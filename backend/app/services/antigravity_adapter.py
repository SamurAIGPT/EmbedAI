"""Google account engine using the official Antigravity ACP runtime."""

import asyncio
import http.client
import json
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from app.services import antigravity_runtime as runtime
from app.services import local_claude_runner
from app.services.acp_adapter import AcpAdapter, InvalidConfig, _initialize_params
from app.services.engine_registry import check_model_id
from app.services.provider_adapter import EngineProbe, ModelOption, TurnOutcome
from app.services.stdio_rpc import StdioRpc

AUTH_PREFIX = "Open the following link to authenticate the ACP server: "
BROWSER_MARKER = "__OPEN_DOTS_GOOGLE_AUTH__"
AUTH_SECONDS = 300
STARTUP_SECONDS = 90


def profile_dir(config):
    profile = config.get("profile")
    if not isinstance(profile, str) or not re.fullmatch(r"[a-f0-9]{32}", profile):
        raise InvalidConfig("Sign in with Google in Settings → Engines.")
    return runtime.data_root() / "profiles" / profile


def authenticated(config):
    try:
        token = profile_dir(config) / "antigravity-acp" / "acp_token.json"
        if token.is_symlink() or token.stat().st_size > 1024 * 1024:
            return False
        data = json.loads(token.read_text())
        return isinstance(data, dict) and all(isinstance(data.get(k), str) and data[k] for k in ("client_id", "client_secret", "refresh_token"))
    except (OSError, ValueError, TypeError):
        return False


def profile_env(directory: Path, binary_dir: Path):
    acp = directory / "antigravity-acp"
    acp.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    acp.chmod(0o700)
    settings_file = acp / "settings.json"
    settings_file.write_text(json.dumps({"auth": {"type": "oauth-personal"}}))
    settings_file.chmod(0o600)
    env = local_claude_runner.child_env()
    for name in list(env):
        upper = name.upper()
        if upper.startswith(("GOOGLE_", "GEMINI_", "GCLOUD_", "CLOUDSDK_", "AGY_", "ANTIGRAVITY_")) or upper in {"BROWSER", "ELECTRON_RUN_AS_NODE"}:
            env.pop(name)
    helper = f"import sys;sys.stderr.write({BROWSER_MARKER!r}+sys.argv[-1]+'\\n')"
    if any(c in sys.executable for c in ("\n", "\r", "%s", ":")):
        raise InvalidConfig("The Python runtime path cannot safely launch Google sign-in.")
    env.update(GEMINI_HOME=str(directory), AGY_ACP_FORCE_FILE_STORAGE="1",
               ANTIGRAVITY_HARNESS_PATH=str(binary_dir / "localharness_external"),
               BROWSER=shlex.join([sys.executable, "-c", helper, "--", "%s"]), PYTHONUNBUFFERED="1")
    return env


def authorization(raw):
    try:
        url = urlsplit(raw)
        query = parse_qs(url.query, keep_blank_values=True)
        state, redirect, response = (query.get(k, []) for k in ("state", "redirect_uri", "response_type"))
        if len(raw) > 16384 or re.search(r"\s", raw) or url.scheme != "https" or url.netloc != "accounts.google.com" or url.path != "/o/oauth2/v2/auth" or url.fragment:
            raise ValueError
        if len(state) != 1 or not state[0] or len(state[0]) > 512 or re.search(r"\s", state[0]) or len(redirect) != 1 or response != ["code"]:
            raise ValueError
        target = urlsplit(redirect[0])
        if not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}/", redirect[0]) or not 1024 <= target.port <= 65535:
            raise ValueError
        return raw, redirect[0], state[0]
    except (ValueError, TypeError):
        raise InvalidConfig("Antigravity returned an invalid Google sign-in URL.") from None


def callback_url(raw, redirect, state):
    try:
        url, expected = urlsplit(raw), urlsplit(redirect)
        query = parse_qs(url.query, keep_blank_values=True)
        codes, errors = query.get("code", []), query.get("error", [])
        if len(raw) > 16384 or re.search(r"\s", raw) or url.scheme != expected.scheme or url.netloc != expected.netloc or url.path != expected.path or url.fragment:
            raise ValueError
        if query.get("state") != [state] or query.get("iss", ["https://accounts.google.com"]) != ["https://accounts.google.com"]:
            raise ValueError
        if not ((len(codes) == 1 and codes[0] and not errors) or (len(errors) == 1 and errors[0] and not codes)):
            raise ValueError
        return raw
    except (ValueError, TypeError):
        raise InvalidConfig("This redirect URL does not belong to the current sign-in.") from None


def option(session, name):
    options = session.get("configOptions")
    return next((o for o in options if isinstance(o, dict) and o.get("id") == name), {}) if isinstance(options, list) else {}


def catalog(session):
    model = option(session, "model")
    values = []
    def visit(items, depth=0):
        if not isinstance(items, list) or depth > 8:
            return
        for item in items[:600]:
            if not isinstance(item, dict):
                continue
            if "options" in item:
                visit(item["options"], depth + 1)
                continue
            identifier = item.get("value")
            if not isinstance(identifier, str):
                continue
            try:
                check_model_id(identifier)
            except ValueError:
                continue
            if identifier not in {m.id for m in values} and len(values) < 600:
                label = item.get("name")
                values.append(ModelOption(identifier, label[:200] if isinstance(label, str) and label else identifier, identifier == model.get("currentValue")))
    visit(model.get("options"))
    return values


class AntigravityAdapter(AcpAdapter):
    key = "antigravity"
    label = "Google Gemini (Antigravity)"
    preset = True
    custom_models = False
    resume_method = "session/resume"
    redact_diagnostics = True
    exit_grace_seconds = 5
    config_fields = [{"name": "model", "label": "Default model", "advanced": True}]
    setup = {"credential": "google", "managed_install": True, "docs_url": "https://www.antigravity.google/docs/install?tab=cli",
             "notes": ["Sign in with an eligible personal Google account. Models depend on your plan.", "One connected account at a time. Removing it deletes this app's isolated credentials.", "Enterprise authentication is not supported by this integration."]}

    def __init__(self):
        super().__init__()
        self.lock = asyncio.Lock()
        self.operation = None
        self.task = None
        self.auth_request = None
        self.active_turns = 0

    @property
    def models(self):
        try:
            data = json.loads((profile_dir(self.config or {}) / "models.json").read_text())
            return [ModelOption(**m) for m in data]
        except (OSError, ValueError, TypeError):
            return []

    def validate_config(self, payload, current):
        model = payload.get("model") or None
        if model:
            check_model_id(model)
            self.config = current
            if model not in {m.id for m in self.models}:
                raise InvalidConfig("Choose a model offered to your Google account.")
        return {**(current or {}), "model": model}

    def public_config(self, config):
        return {"model": (config or {}).get("model"), "connected": authenticated(config or {})}

    def program(self, config):
        return [str(runtime.runtime_dir() / "agy_acp_server.par")] if runtime.installed() else None

    def mode_args(self, ctx, config):
        return ["--uid="] if sys.platform == "linux" else []

    def child_env(self, config, ctx=None):
        env = profile_env(profile_dir(config), runtime.runtime_dir())
        model = (ctx.model if ctx else None) or config.get("model")
        if model:
            env["AGY_ACP_DEFAULT_MODEL"] = model
        return env

    def validate_initialize(self, initialized):
        info = initialized.get("agentInfo", {})
        caps = initialized.get("agentCapabilities", {})
        methods = initialized.get("authMethods", [])
        if initialized.get("protocolVersion") != 1 or info.get("name") not in ("antigravity-acp", "Google Antigravity") or info.get("version") != runtime.VERSION or caps.get("loadSession") is not True or not any(isinstance(m, dict) and m.get("id") == "oauth-personal" for m in methods):
            raise InvalidConfig("The runtime is not the pinned Google Antigravity ACP release.")

    def authenticate(self, methods, env):
        if not any(isinstance(m, dict) and m.get("id") == "oauth-personal" for m in methods):
            raise InvalidConfig("The runtime does not support personal Google sign-in.")
        return "oauth-personal"

    async def configure_session(self, call, session_id, session, ctx):
        result = await call("session/set_config_option", {"sessionId": session_id, "configId": "mode", "value": "default"}, handle_incoming=True)
        if option(result or {}, "mode").get("currentValue") != "default":
            raise InvalidConfig("Antigravity did not confirm tool approval mode.")

    async def select_model(self, call, session_id, session, model):
        offered = catalog(session)
        current = option(session, "model").get("currentValue")
        if not model:
            if current not in {m.id for m in offered}:
                raise InvalidConfig("Antigravity did not return its current model.")
            return current
        if model not in {m.id for m in offered}:
            raise InvalidConfig("The account did not offer this model.")
        if model != current:
            result = await call("session/set_config_option", {"sessionId": session_id, "configId": "model", "value": model}, handle_incoming=True)
            if option(result or {}, "model").get("currentValue") != model:
                raise InvalidConfig("The engine did not confirm this model.")
        return model

    async def probe(self, *, handshake=True):
        try:
            runtime.asset()
        except ValueError as exc:
            return EngineProbe(installed=False, ready=False, reason=str(exc))
        installed = runtime.installed()
        signed = authenticated(self.config or {})
        models = self.models if signed else []
        return EngineProbe(installed=installed, ready=installed and signed and bool(models), signed_in=signed,
                           version=runtime.VERSION if installed else None, models=models,
                           reason="Install Antigravity in Settings → Engines." if not installed else (self.signed_out_message() if not signed else "Refresh the account's models in Settings → Engines."))

    async def run_turn(self, ctx, sink, gate):
        if self.task and not self.task.done():
            return TurnOutcome("failed", error="Wait for Antigravity setup to finish.", error_code="engine_busy")
        config = ctx.engine_config or self.config or {}
        if not authenticated(config):
            return TurnOutcome("failed", error=self.signed_out_message(), error_code="engine_signed_out")
        state = ctx.provider_state or {}
        if ctx.resume and state.get("antigravity_profile") != config.get("profile"):
            return TurnOutcome("failed", error="The connected Google account changed. Start a new session.", error_code="session_resume_failed")
        async with self.lock:
            if not authenticated(config):
                return TurnOutcome("failed", error=self.signed_out_message(), error_code="engine_signed_out")
            self.active_turns += 1
            try:
                result = await super().run_turn(ctx, sink, gate)
                if result.provider_state:
                    result.provider_state["antigravity_profile"] = config["profile"]
                return result
            finally:
                self.active_turns -= 1

    async def client(self, config, binary_dir=None):
        directory = profile_dir(config)
        binary = binary_dir or runtime.runtime_dir()
        env = profile_env(directory, binary)
        rpc = await StdioRpc.spawn([str(binary / "agy_acp_server.par"), *self.mode_args(None, {})],
                                   cwd=str(directory), env=env, max_bytes=2_000_000, diagnostic=self._diagnostic)
        try:
            initialized = await rpc.request("initialize", _initialize_params(), timeout=STARTUP_SECONDS)
            self.validate_initialize(initialized)
            return rpc
        except BaseException:
            await rpc.close(grace_seconds=self.exit_grace_seconds)
            raise

    def _diagnostic(self, line):
        if self.operation is None or self.operation["kind"] != "sign_in":
            return
        raw = line[len(AUTH_PREFIX):].strip() if line.startswith(AUTH_PREFIX) else (line[len(BROWSER_MARKER):] if line.startswith(BROWSER_MARKER) else None)
        if raw is not None:
            try:
                self.auth_request = authorization(raw)
                self.operation["authorization_url"] = self.auth_request[0]
            except InvalidConfig:
                pass

    async def discover(self, config):
        rpc = await self.client(config)
        try:
            await rpc.request("authenticate", {"methodId": "oauth-personal"}, timeout=30)
            session = await rpc.request("session/new", {"cwd": str(profile_dir(config)), "mcpServers": []}, timeout=STARTUP_SECONDS)
            models = catalog(session)
            if not models:
                raise InvalidConfig("Antigravity did not offer any models to this account.")
            path = profile_dir(config) / "models.json"
            path.write_text(json.dumps([{"id": m.id, "label": m.label, "default": m.default} for m in models]))
            path.chmod(0o600)
        finally:
            await rpc.close(grace_seconds=self.exit_grace_seconds)

    async def validate_runtime(self, binary):
        with tempfile.TemporaryDirectory(prefix=".verify-", dir=runtime.data_root()) as temporary:
            # The validator must never authenticate or touch a connected profile.
            rpc = await StdioRpc.spawn([str(binary / "agy_acp_server.par"), *self.mode_args(None, {})],
                cwd=temporary, env=profile_env(Path(temporary), binary), max_bytes=2_000_000, diagnostic=lambda line: None)
            try:
                initialized = await rpc.request("initialize", _initialize_params(), timeout=STARTUP_SECONDS)
                self.validate_initialize(initialized)
            finally:
                await rpc.close(grace_seconds=self.exit_grace_seconds)

    def start_operation(self, kind, work):
        if self.lock.locked() or (self.task and not self.task.done()):
            raise InvalidConfig("Antigravity is busy. Stop the conversation or wait for setup to finish.")
        self.operation = {"id": uuid4().hex, "kind": kind, "phase": "running", "authorization_url": None, "error": None}
        self.auth_request = None
        async def run():
            async with self.lock:
                try:
                    await work()
                    self.operation["phase"] = "succeeded"
                except asyncio.CancelledError:
                    self.operation["phase"] = "cancelled"
                    raise
                except (InvalidConfig, runtime.InstallError) as exc:
                    self.operation.update(phase="failed", error=str(exc))
                except Exception:
                    # Native diagnostics and RPC errors can contain OAuth codes.
                    self.operation.update(phase="failed", error="Antigravity setup failed. Retry setup and check your account eligibility and network connection.")
                finally:
                    self.operation["authorization_url"] = None
                    self.auth_request = None
        self.task = asyncio.create_task(run())
        return dict(self.operation)

    async def sign_in(self, save):
        previous = self.config or {}
        config = {"profile": uuid4().hex}
        succeeded = False
        rpc = None
        try:
            rpc = await self.client(config)
            await rpc.request("authenticate", {"methodId": "oauth-personal"}, timeout=AUTH_SECONDS)
            if not authenticated(config):
                raise InvalidConfig("Google did not save credentials for this profile.")
            (profile_dir(config) / "antigravity-acp/acp_token.json").chmod(0o600)
            await rpc.close(grace_seconds=self.exit_grace_seconds)
            rpc = None
            await self.discover(config)
            await save(config)
            self.config = config
            succeeded = True
            if previous.get("profile") and profile_dir(previous).exists():
                shutil.rmtree(profile_dir(previous))
        finally:
            if rpc:
                await rpc.close(grace_seconds=self.exit_grace_seconds)
            if not succeeded and profile_dir(config).exists():
                shutil.rmtree(profile_dir(config))

    async def complete_callback(self, operation_id, raw):
        if not self.operation or self.operation["id"] != operation_id or self.operation["phase"] != "running" or not self.auth_request:
            raise InvalidConfig("This Google sign-in is no longer active. Start again.")
        _, redirect, state = self.auth_request
        url = callback_url(raw, redirect, state)
        # OAuth codes must not enter HTTP client request logs or proxy settings.
        def forward():
            parsed = urlsplit(url)
            connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=10)
            try:
                connection.request("GET", parsed.path + "?" + parsed.query)
                response = connection.getresponse()
                if not 200 <= response.status < 300:
                    raise InvalidConfig("Could not deliver the Google sign-in response.")
            finally:
                connection.close()
        await asyncio.to_thread(forward)

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.operation.update(phase="cancelled", authorization_url=None)
            self.auth_request = None
