"""An OpenAI-compatible chat endpoint (`POST {base_url}/chat/completions`,
streamed) as a thread engine: a local Ollama, LM Studio or vLLM server, or a
hosted API that speaks the same protocol.

Chat only. No tools, so no approvals and no computer; the engine keeps no
session, so each turn sends the conversation's stored history (user and
assistant messages, newest kept within a size limit) with the agent's
instructions as the system message.

The endpoint's address and key are saved through the encrypted engine
configuration (app/services/engine_config.py). The key goes only into the
`Authorization` header of requests to that address: it is never logged,
returned by the API, written to an event or put in the decision log, and an
error message from the endpoint has it removed before it is shown.

`OpenRouterAdapter` at the end is a preset of the same engine: OpenRouter's
address filled in, its key required, and OpenRouter's optional app-attribution
headers (sent to openrouter.ai only).
"""

import asyncio
import json
from urllib.parse import urlsplit

import httpx

from app.core import app_settings
from app.core.logging import get_logger
from app.services.provider_adapter import (
    Capabilities,
    EngineProbe,
    ModelOption,
    ProviderStatus,
    TurnContext,
    TurnOutcome,
)

logger = get_logger(__name__)

PROBE_TIMEOUT_SECONDS = 5.0
CONNECT_TIMEOUT_SECONDS = 15.0
HISTORY_CHARS = 120_000
MAX_ERROR_CHARS = 500
MAX_MODELS = 200

NOT_CONFIGURED = "Set the endpoint's address (and key, if it needs one) in Settings → Engines."

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_HOST = "openrouter.ai"
# OpenRouter's optional app attribution (its public app rankings).
OPENROUTER_ATTRIBUTION = {"HTTP-Referer": "https://github.com/Anil-matcha/open-duet", "X-Title": "Open Duet"}


class InvalidConfig(ValueError):
    pass


def check_base_url(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidConfig("Give the endpoint's address, for example http://127.0.0.1:11434/v1.")
    url = value.strip().rstrip("/")
    if len(url) > 500 or any(ord(c) < 33 for c in url):
        raise InvalidConfig("The address must be one URL without spaces.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InvalidConfig("The address must start with http:// or https://.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise InvalidConfig("Put the key in the key field, not in the address; no query or fragment.")
    return url


def redact(text: str, secret: str | None) -> str:
    if secret and secret in text:
        text = text.replace(secret, "[redacted]")
    return text


def chat_messages(items: list[dict], instructions: str | None) -> list[dict]:
    """The stored history as chat messages, oldest dropped past the limit."""
    messages = []
    for item in items:
        role, text = item.get("role"), item.get("text")
        if role in ("user", "assistant") and isinstance(text, str) and text.strip():
            messages.append({"role": role, "content": text})
    total = 0
    kept: list[dict] = []
    for message in reversed(messages):
        total += len(message["content"])
        if kept and total > HISTORY_CHARS:
            break
        kept.append(message)
    kept.reverse()
    # A chat starts with the person: an orphaned assistant reply goes.
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    if instructions:
        kept.insert(0, {"role": "system", "content": instructions})
    return kept


class OpenAIChatAdapter:
    key = "openai_compat"
    label = "OpenAI-compatible"
    kind = "api"
    capabilities = Capabilities(
        tools=False, approvals=False, mcp=False, images=False, resume=True, computers=False
    )
    models: list[ModelOption] = []
    custom_models = True
    needs_config = True
    # No session: every turn replays the stored conversation, folded when it
    # outgrows the budget (context_compaction.py). `context_tokens` in the
    # saved configuration (the model's context window) sets the budget.
    replays_history = True
    compact_at_tokens = 24_000
    # What status, probe and a turn say while nothing usable is saved.
    not_configured = NOT_CONFIGURED

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config: dict | None = None
        # Tests pass a mock transport; real runs use the network.
        self._transport = transport

    def new_session_id(self) -> None:
        return None

    def _client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport, follow_redirects=False)

    # -- configuration --------------------------------------------------------------

    def validate_config(self, payload: dict, current: dict | None) -> dict:
        current = current or {}
        config = {"base_url": check_base_url(payload.get("base_url"))}
        model = payload.get("model")
        if model is not None and not isinstance(model, str):
            raise InvalidConfig("The default model must be text.")
        if model and model.strip():
            from app.services.engine_registry import InvalidModel, check_model_id

            try:
                check_model_id(model.strip())
            except InvalidModel as exc:
                raise InvalidConfig(str(exc)) from None
            config["model"] = model.strip()
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
        elif current.get("api_key") and current.get("base_url") == config["base_url"]:
            # Kept only for the same address: a key is never sent somewhere new
            # without being given again.
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

    def _headers(self, config: dict) -> dict:
        headers = {"Accept": "application/json"}
        if config.get("api_key"):
            headers["Authorization"] = f"Bearer {config['api_key']}"
        return headers

    # -- status --------------------------------------------------------------------------

    async def status(self) -> ProviderStatus:
        config = self.config or {}
        if not config.get("base_url"):
            return ProviderStatus(ready=False, error=self.not_configured, installed=False)
        return ProviderStatus(ready=True, installed=True)

    async def probe(self) -> EngineProbe:
        config = self.config or {}
        base = config.get("base_url")
        if not base:
            return EngineProbe(installed=False, ready=False, reason=self.not_configured)
        default = config.get("model")
        try:
            async with self._client(httpx.Timeout(PROBE_TIMEOUT_SECONDS)) as client:
                response = await client.get(f"{base}/models", headers=self._headers(config))
        except httpx.HTTPError as exc:
            return EngineProbe(
                installed=True, ready=False,
                reason=f"The endpoint did not answer ({exc.__class__.__name__}).",
                models=[ModelOption(default, default, True)] if default else None,
            )
        if response.status_code in (401, 403):
            return EngineProbe(installed=True, ready=False, signed_in=False, reason="The endpoint refused the key.")
        models: list[ModelOption] = []
        if response.status_code == 200:
            try:
                data = response.json().get("data")
            except (ValueError, AttributeError):
                data = None
            for item in (data or [])[:MAX_MODELS]:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    models.append(ModelOption(item["id"], item["id"], item["id"] == default))
        if default and default not in {m.id for m in models}:
            models.insert(0, ModelOption(default, default, True))
        return EngineProbe(
            installed=True, ready=True, signed_in=True if config.get("api_key") else None, models=models or None
        )

    # -- a turn ----------------------------------------------------------------------------

    async def run_turn(self, ctx: TurnContext, sink, gate) -> TurnOutcome:
        config = ctx.engine_config or self.config or {}
        base = config.get("base_url")
        if not base:
            return TurnOutcome("failed", error=self.not_configured, error_code="engine_not_configured")
        if ctx.computer is not None:
            return TurnOutcome(
                "failed", error="This engine cannot use an agent computer.", error_code="engine_without_computers"
            )
        model = ctx.model or config.get("model")
        if not model:
            return TurnOutcome(
                "failed", error="Choose a model for this endpoint (on the agent, the conversation, or the engine).",
                error_code="no_model",
            )
        secret = config.get("api_key")
        messages = chat_messages(await ctx.history(), ctx.instructions)
        if not messages or messages[-1].get("role") != "user":
            messages.append({"role": "user", "content": ctx.prompt})
        else:
            # The stored message is what the person typed; the engine gets the
            # turn's prompt (attached text files, recalled passages).
            messages[-1] = {"role": "user", "content": ctx.prompt}
        loop = asyncio.get_running_loop()
        state = {"text": "", "usage": None, "finish": None, "error": None, "code": None}
        max_text = app_settings.get("LOCAL_MAX_OUTPUT_BYTES")

        async def stream() -> None:
            body = {"model": model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
            timeout = httpx.Timeout(app_settings.get("LOCAL_TASK_TIMEOUT_SECONDS"), connect=CONNECT_TIMEOUT_SECONDS)
            async with self._client(timeout) as client:
                async with client.stream(
                    "POST", f"{base}/chat/completions", headers=self._headers(config), json=body
                ) as response:
                    if response.status_code != 200:
                        raw = (await response.aread())[:4000].decode("utf-8", errors="replace")
                        state["error"] = _error_text(response.status_code, raw, secret)
                        state["code"] = "endpoint_refused" if response.status_code in (401, 403) else "endpoint_error"
                        return
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue
                        if isinstance(chunk.get("error"), (dict, str)):
                            err = chunk["error"]
                            message = err.get("message") if isinstance(err, dict) else err
                            state["error"] = redact(str(message), secret)[:MAX_ERROR_CHARS]
                            state["code"] = "endpoint_error"
                            return
                        usage = chunk.get("usage")
                        if isinstance(usage, dict):
                            state["usage"] = {
                                ours: usage[theirs]
                                for theirs, ours in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"))
                                if isinstance(usage.get(theirs), int)
                            } or None
                        for choice in chunk.get("choices") or []:
                            if not isinstance(choice, dict):
                                continue
                            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                            piece = delta.get("content")
                            if isinstance(piece, str) and piece:
                                state["text"] += piece
                                if len(state["text"].encode("utf-8")) > max_text:
                                    state["error"] = "The endpoint produced more output than allowed"
                                    state["code"] = "output_too_large"
                                    return
                                if not sink.lost:
                                    sink.delta(piece)
                            if choice.get("finish_reason"):
                                state["finish"] = choice["finish_reason"]

        task = asyncio.create_task(stream())
        ctx.on_abort(lambda: loop.call_soon_threadsafe(task.cancel))
        try:
            await task
        except asyncio.CancelledError:
            if not ctx.aborted:
                raise
        except httpx.TimeoutException:
            state["error"], state["code"] = (
                f"Timed out after {app_settings.get('LOCAL_TASK_TIMEOUT_SECONDS')} seconds", "timeout"
            )
        except httpx.HTTPError as exc:
            state["error"] = f"The endpoint could not be reached ({exc.__class__.__name__})."
            state["code"] = "endpoint_unreachable"

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


def _error_text(status: int, body: str, secret: str | None) -> str:
    message = body
    try:
        parsed = json.loads(body)
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            message = err["message"]
        elif isinstance(err, str):
            message = err
    except ValueError:
        pass
    message = redact(" ".join(message.split()), secret)[:MAX_ERROR_CHARS]
    return f"The endpoint answered {status}: {message}" if message else f"The endpoint answered {status}."


class OpenRouterAdapter(OpenAIChatAdapter):
    """OpenRouter (https://openrouter.ai) as a preset of the OpenAI-compatible
    engine: its own saved configuration (`engine:openrouter`), so it can sit
    beside a local endpoint. The address defaults to OpenRouter's, a key is
    required, and models are OpenRouter ids such as `openai/gpt-4o-mini` or
    `meta-llama/llama-3.3-70b-instruct:free`. Requests to openrouter.ai
    itself also carry OpenRouter's optional app-attribution headers, which tell
    OpenRouter this account's traffic comes from Open Duet; another address (a
    proxy) never gets them."""

    key = "openrouter"
    label = "OpenRouter"
    not_configured = "Add your OpenRouter API key in Settings → Engines."

    def validate_config(self, payload: dict, current: dict | None) -> dict:
        base = payload.get("base_url")
        if not isinstance(base, str) or not base.strip():
            payload = {**payload, "base_url": OPENROUTER_BASE_URL}
        config = super().validate_config(payload, current)
        if not config.get("api_key"):
            raise InvalidConfig("Give your OpenRouter API key (openrouter.ai → Keys).")
        return config

    def _headers(self, config: dict) -> dict:
        headers = super()._headers(config)
        if urlsplit(config.get("base_url") or "").hostname == OPENROUTER_HOST:
            headers.update(OPENROUTER_ATTRIBUTION)
        return headers

    async def status(self) -> ProviderStatus:
        if not (self.config or {}).get("api_key"):
            return ProviderStatus(ready=False, error=self.not_configured, installed=False)
        return await super().status()

    async def probe(self) -> EngineProbe:
        if not (self.config or {}).get("api_key"):
            return EngineProbe(installed=False, ready=False, reason=self.not_configured)
        return await super().probe()
