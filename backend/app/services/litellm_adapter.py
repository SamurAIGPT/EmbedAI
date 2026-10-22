"""LiteLLM (https://docs.litellm.ai) as a thread engine: one engine that calls
any provider the LiteLLM SDK supports (Anthropic, Gemini, Bedrock, Vertex AI,
Azure, Mistral, Groq, Ollama, ...) directly, by a model id such as
`anthropic/claude-sonnet-4-6` or `bedrock/us.anthropic.claude-sonnet-4-6-v1:0`.

Chat only, like the OpenAI-compatible engine it mirrors: no tools, approvals
or computer; every turn replays the stored history (chat_messages) with the
agent's instructions as the system message, streamed back as typing deltas.

The SDK reads each provider's usual credentials from the backend's
environment (ANTHROPIC_API_KEY, AWS credentials, GOOGLE_APPLICATION_CREDENTIALS
...), so a key is optional. A key saved in Settings → Engines is kept in the
encrypted engine configuration (app/services/engine_config.py) and handled
like the OpenAI-compatible engine's: passed only to the SDK call, never
logged, returned, stored in an event or the decision log, and removed from
error messages. `litellm` is an optional dependency (`uv sync --extra
litellm`); without it the engine is unavailable and says how to install it.
"""

import asyncio
import importlib.util

from app.core import app_settings
from app.core.logging import get_logger
from app.services.openai_chat_adapter import InvalidConfig, chat_messages, check_base_url, redact
from app.services.provider_adapter import (
    Capabilities,
    EngineProbe,
    ModelOption,
    ProviderStatus,
    TurnContext,
    TurnOutcome,
)

logger = get_logger(__name__)

CONNECT_TIMEOUT_SECONDS = 15.0
MAX_ERROR_CHARS = 500
MAX_MODELS = 200

NOT_INSTALLED = "LiteLLM is not installed in the backend. Run `uv sync --extra litellm` in backend/ and restart."
NOT_CONFIGURED = "Save a default LiteLLM model (and a key, if its provider needs one) in Settings → Engines."

# LiteLLM exception class -> this app's error code. Matched by name so this
# module imports without the optional SDK.
_ERROR_CODES = {
    "AuthenticationError": "endpoint_refused",
    "PermissionDeniedError": "endpoint_refused",
    "Timeout": "timeout",
    "APIConnectionError": "endpoint_unreachable",
}


def litellm_installed() -> bool:
    return importlib.util.find_spec("litellm") is not None


def _import_litellm():
    """The SDK, with its own console logging and feedback banner turned off."""
    import litellm

    litellm.suppress_debug_info = True
    return litellm


def provider_of(model: str | None) -> str | None:
    if model and "/" in model:
        return model.split("/", 1)[0]
    return None


def error_code_of(exc: Exception) -> str:
    for cls in type(exc).__mro__:
        code = _ERROR_CODES.get(cls.__name__)
        if code:
            return code
    return "endpoint_error"


def error_text(exc: Exception, secret: str | None) -> str:
    """What the provider said, one line, without the key. The status code is
    kept (`429`, `402`) so the runtime can recognise a limit."""
    message = " ".join(str(getattr(exc, "message", None) or exc).split())
    status = getattr(exc, "status_code", None)
    message = redact(message, secret)[:MAX_ERROR_CHARS]
    if isinstance(status, int):
        return f"The provider answered {status}: {message}" if message else f"The provider answered {status}."
    return message or f"The call failed ({exc.__class__.__name__})."


class LiteLLMAdapter:
    key = "litellm"
    label = "LiteLLM"
    kind = "api"
    capabilities = Capabilities(
        tools=False, approvals=False, mcp=False, images=False, resume=True, computers=False
    )
    models: list[ModelOption] = []
    custom_models = True
    needs_config = True
    replays_history = True
    compact_at_tokens = 24_000
    not_configured = NOT_CONFIGURED

    def __init__(self) -> None:
        self.config: dict | None = None

    def new_session_id(self) -> None:
        return None

    # -- configuration --------------------------------------------------------------

    def validate_config(self, payload: dict, current: dict | None) -> dict:
        current = current or {}
        model = payload.get("model")
        if not isinstance(model, str) or not model.strip():
            raise InvalidConfig("Give a default LiteLLM model id with its provider, for example anthropic/claude-sonnet-4-6.")
        model = model.strip()
        from app.services.engine_registry import InvalidModel, check_model_id

        try:
            check_model_id(model)
        except InvalidModel as exc:
            raise InvalidConfig(str(exc)) from None
        if not provider_of(model):
            raise InvalidConfig("Start the model id with its provider, for example anthropic/claude-sonnet-4-6 or gemini/gemini-2.5-pro.")
        config: dict = {"model": model}
        # The settings form's address field is the provider's API base: an Azure
        # deployment, a self-hosted vLLM or Ollama server. Optional.
        base = payload.get("base_url")
        if isinstance(base, str) and base.strip():
            config["base_url"] = check_base_url(base)
        window = payload.get("context_tokens")
        if window is not None:
            if isinstance(window, bool) or not isinstance(window, int) or not 1_024 <= window <= 10_000_000:
                raise InvalidConfig("The context window must be a whole number of tokens from 1024 to 10000000.")
            config["context_tokens"] = window
        key = payload.get("api_key")
        if payload.get("clear_api_key"):
            pass
        elif isinstance(key, str) and key.strip():
            if len(key) > 4096 or any(ord(c) < 33 for c in key.strip()):
                raise InvalidConfig("The key must be one line without spaces.")
            config["api_key"] = key.strip()
        elif (
            current.get("api_key")
            and provider_of(current.get("model")) == provider_of(model)
            and current.get("base_url") == config.get("base_url")
        ):
            # Kept only for the same provider and address: a key is never sent
            # to another provider without being given again.
            config["api_key"] = current["api_key"]
        return config

    def public_config(self, config: dict | None) -> dict | None:
        if not config:
            return None
        public = {
            "base_url": config.get("base_url"),
            "model": config.get("model"),
            "has_api_key": bool(config.get("api_key")),
        }
        if config.get("context_tokens"):
            public["context_tokens"] = config["context_tokens"]
        return public

    def _credentials(self, config: dict) -> dict:
        """Only what was saved; blank values are left out so the SDK reads the
        provider's own environment variables."""
        kwargs = {}
        if config.get("api_key"):
            kwargs["api_key"] = config["api_key"]
        if config.get("base_url"):
            kwargs["api_base"] = config["base_url"]
        return kwargs

    # -- status --------------------------------------------------------------------------

    async def status(self) -> ProviderStatus:
        if not litellm_installed():
            return ProviderStatus(ready=False, error=NOT_INSTALLED, installed=False)
        if not (self.config or {}).get("model"):
            return ProviderStatus(ready=False, error=self.not_configured, installed=True)
        return ProviderStatus(ready=True, installed=True)

    async def probe(self) -> EngineProbe:
        if not litellm_installed():
            return EngineProbe(installed=False, ready=False, reason=NOT_INSTALLED)
        config = self.config or {}
        default = config.get("model")
        if not default:
            return EngineProbe(installed=True, ready=False, reason=self.not_configured)
        litellm = _import_litellm()
        version = getattr(litellm, "__version__", None)
        if version is None:
            from importlib.metadata import version as package_version

            version = package_version("litellm")
        models = [ModelOption(default, default, True)]
        # Suggestions from LiteLLM's catalog for the default model's provider.
        known = (getattr(litellm, "models_by_provider", None) or {}).get(provider_of(default)) or []
        for name in sorted(known):
            model_id = name if name.startswith(f"{provider_of(default)}/") else f"{provider_of(default)}/{name}"
            if model_id != default and len(models) < MAX_MODELS:
                models.append(ModelOption(model_id, model_id, False))
        if not config.get("api_key") and not config.get("base_url"):
            try:
                env = litellm.validate_environment(model=default)
            except Exception:  # noqa: BLE001  (an unknown provider is checked by the first turn)
                env = {}
            missing = env.get("missing_keys") or []
            if missing:
                return EngineProbe(
                    installed=True, ready=False, version=version, models=models,
                    reason=f"Set {', '.join(missing)} in the backend's environment, or save a key for {default}.",
                )
        return EngineProbe(
            installed=True, ready=True, version=version, models=models,
            signed_in=True if config.get("api_key") else None,
        )

    # -- a turn ----------------------------------------------------------------------------

    async def run_turn(self, ctx: TurnContext, sink, gate) -> TurnOutcome:
        config = ctx.engine_config or self.config or {}
        if not litellm_installed():
            return TurnOutcome("failed", error=NOT_INSTALLED, error_code="engine_unavailable")
        if ctx.computer is not None:
            return TurnOutcome(
                "failed", error="This engine cannot use an agent computer.", error_code="engine_without_computers"
            )
        model = ctx.model or config.get("model")
        if not model:
            return TurnOutcome("failed", error=self.not_configured, error_code="no_model")
        secret = config.get("api_key")
        messages = chat_messages(await ctx.history(), ctx.instructions)
        if not messages or messages[-1].get("role") != "user":
            messages.append({"role": "user", "content": ctx.prompt})
        else:
            messages[-1] = {"role": "user", "content": ctx.prompt}
        litellm = _import_litellm()
        loop = asyncio.get_running_loop()
        state = {"text": "", "usage": None, "finish": None, "error": None, "code": None}
        max_text = app_settings.get("LOCAL_MAX_OUTPUT_BYTES")
        timeout = app_settings.get("LOCAL_TASK_TIMEOUT_SECONDS")

        async def stream() -> None:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                # Drop sampling or streaming options a provider does not take
                # instead of failing the turn.
                drop_params=True,
                timeout=timeout,
                **self._credentials(config),
            )
            async for chunk in response:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    counts = {
                        ours: getattr(usage, theirs)
                        for theirs, ours in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"))
                        if isinstance(getattr(usage, theirs, None), int)
                    }
                    state["usage"] = counts or state["usage"]
                for choice in getattr(chunk, "choices", None) or []:
                    piece = getattr(getattr(choice, "delta", None), "content", None)
                    if isinstance(piece, str) and piece:
                        state["text"] += piece
                        if len(state["text"].encode("utf-8")) > max_text:
                            state["error"] = "The provider produced more output than allowed"
                            state["code"] = "output_too_large"
                            return
                        if not sink.lost:
                            sink.delta(piece)
                    if getattr(choice, "finish_reason", None):
                        state["finish"] = choice.finish_reason

        task = asyncio.create_task(stream())
        ctx.on_abort(lambda: loop.call_soon_threadsafe(task.cancel))
        try:
            await task
        except asyncio.CancelledError:
            if not ctx.aborted:
                raise
        except Exception as exc:  # noqa: BLE001  (every provider failure is reported, never raised)
            state["code"] = error_code_of(exc)
            if state["code"] == "timeout":
                state["error"] = f"Timed out after {timeout} seconds"
            elif state["code"] == "endpoint_unreachable":
                state["error"] = redact(f"The provider could not be reached ({exc.__class__.__name__}).", secret)
            else:
                state["error"] = error_text(exc, secret)
            logger.warning("litellm turn failed", model=model, error=exc.__class__.__name__)

        partial = state["text"] or None
        if ctx.aborted:
            return TurnOutcome(
                "failed", error="The run was stopped because it no longer owns its turn.", error_code="lost",
                model=model, usage=state["usage"], partial_text=partial,
            )
        if state["error"]:
            return TurnOutcome(
                "failed", error=state["error"], error_code=state["code"], model=model,
                usage=state["usage"], partial_text=partial,
            )
        text = state["text"]
        if text.strip():
            await sink.emit("assistant.message", {"text": text})
        if state["finish"] == "length":
            return TurnOutcome(
                "failed", error="The answer was cut off at the model's length limit.", error_code="length",
                model=model, usage=state["usage"],
            )
        return TurnOutcome("succeeded", result=text, model=model, usage=state["usage"])
