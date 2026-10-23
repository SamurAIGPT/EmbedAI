"""Vendor presets sharing the generic ACP transport and approval gate.

CLI contracts are covered with synthetic agents; see docs/ENGINES.md for the
separate record of real CLI verification.
"""
import json
import re
import sys
import time
from pathlib import Path

from app.core import app_settings
from app.services import cli_probe, local_claude_runner
from app.services.acp_adapter import AcpAdapter, InvalidConfig, resolve_command, split_command
from app.services.engine_registry import check_model_id
from app.services.provider_adapter import EngineProbe, ModelOption

FIELDS = [
    {"name": "api_key", "label": "API key", "kind": "secret"},
    {"name": "model", "label": "Default model", "advanced": True},
    {"name": "command", "label": "Program path", "advanced": True, "help": "Program only; the ACP arguments are added automatically."},
]


class PresetAdapter(AcpAdapter):
    preset = True
    custom_models = True
    config_fields = FIELDS
    credential_names = ()
    default_program = ""
    state_dirs = ()

    def __init__(self):
        super().__init__()
        self._model_cache = (0.0, [])

    def program(self, config):
        return resolve_command(config.get("command") or self.default_program)

    def validate_config(self, payload, current):
        current = current or {}
        command = payload.get("command") or ""
        if not isinstance(command, str) or len(command) > 1000 or any(ord(c) < 32 for c in command):
            raise InvalidConfig("The program must be one line of at most 1000 characters.")
        if command.strip():
            split_command(command)
        model = payload.get("model") or None
        if model is not None:
            check_model_id(model)
        key = payload.get("api_key")
        if key is not None and (not isinstance(key, str) or any(ord(c) < 32 for c in key) or len(key) > 16_000):
            raise InvalidConfig("The API key must be one line of text.")
        return {"command": command.strip() or None, "model": model, "api_key": None if payload.get("clear_api_key") else (key.strip() if key else current.get("api_key"))}

    def public_config(self, config):
        config = config or {}
        return {"command": config.get("command"), "model": config.get("model"), "has_api_key": bool(config.get("api_key"))}

    def child_env(self, config, ctx=None):
        extra = {self.credential_names[0]: config["api_key"]} if config.get("api_key") else {}
        return local_claude_runner.child_env(extra_names=self.credential_names, extra_env=extra)

    async def signed_in(self, env, config):
        return True if any(env.get(name) for name in self.credential_names) else None

    async def auth_failure_allowed(self, env, config):
        return await self.signed_in(env, config) is True

    async def live_models(self, program, env):
        return self.models

    async def probe(self, *, handshake=True):
        if sys.platform == "win32":
            return EngineProbe(installed=False, ready=False, reason="Preset CLIs are not supported on Windows yet. Use a custom ACP command or an API endpoint.")
        config = self.config or {}
        try:
            program = self.program(config)
        except InvalidConfig:
            return EngineProbe(installed=False, ready=False, reason="The saved program could not be read.")
        if not program:
            return EngineProbe(installed=False, ready=False, reason=f"Install {self.label}, or set its program path in Settings → Engines.")
        env = self.child_env(config)
        signed = await self.signed_in(env, config)
        version = await cli_probe.run([*program, "--version"], timeout=2, env=env)
        # Version output is untrusted and may contain account details. Keep only
        # a conventional numeric version token, never the command's raw output.
        number = re.search(r"\b\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?\b", version.stdout) if version.ok else None
        models = await self.live_models(program, env)
        ready = signed is not False
        if self.key == "opencode":
            ready = bool(models)
        reason = None if ready else ("OpenCode lists no models; run `opencode auth login` or save a key." if self.key == "opencode" else self.signed_out_message())
        return EngineProbe(installed=True, ready=ready, signed_in=signed, version=number.group() if number else None, reason=reason, models=models)


class GeminiAdapter(PresetAdapter):
    key = "gemini"
    label = "Gemini CLI"
    default_program = "gemini"
    credential_names = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    state_dirs = ("~/.gemini",)
    models = [ModelOption("gemini-2.5-pro", "Gemini 2.5 Pro"), ModelOption("gemini-2.5-flash", "Gemini 2.5 Flash")]
    setup = {"install": "npm install -g @google/gemini-cli", "sign_in": "gemini", "docs_url": "https://geminicli.com/docs/", "credential": "either", "notes": ["Use an API key, opt-in Vertex configuration, or an existing CLI login. Consumer login availability depends on Google's current policy.", "Windows presets are not supported yet."]}

    def mode_args(self, ctx, config):
        model = (ctx.model if ctx else None) or config.get("model")
        return ["--acp", "--approval-mode", "default", *(["-m", model] if model else [])]

    async def signed_in(self, env, config):
        if await super().signed_in(env, config):
            return True
        if env.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1") and env.get("GOOGLE_CLOUD_PROJECT"):
            return True
        try:
            data = json.loads((Path(env.get("HOME", str(Path.home()))) / ".gemini/oauth_creds.json").read_text())
            return bool(data.get("refresh_token") or (data.get("access_token") and isinstance(data.get("expiry_date"), (int, float)) and data["expiry_date"] > time.time() * 1000))
        except (OSError, ValueError, TypeError):
            return False

    def authenticate(self, methods, env):
        ids = {m.get("id") for m in methods if isinstance(m, dict)}
        preferred = ["gemini-api-key"] if any(env.get(n) for n in self.credential_names) else (["vertex-ai"] if env.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1") else ["oauth-personal"])
        return next((m for m in preferred if m in ids), None)

    async def select_model(self, call, session_id, session, model):
        # Gemini selects via argv. An explicit contradictory session report is
        # a refusal; old CLIs omit model metadata, so argv is the only contract.
        reported = session.get("models", {}).get("currentModelId") if isinstance(session.get("models"), dict) else None
        if model and reported and model != reported:
            raise InvalidConfig("The CLI selected a different model.")
        return model or reported


def session_models(session):
    data = session.get("models")
    return data if isinstance(data, dict) else {}


def cursor_model_id(model, available):
    ids = [m["modelId"] for m in available if isinstance(m, dict) and isinstance(m.get("modelId"), str)]
    if model in ids:
        return model
    base = "default" if model in ("auto", "default") else model
    matches = [m for m in ids if m.split("[", 1)[0] == base and "fast=true" not in m and "fast=1" not in m]
    if len(matches) == 1:
        return matches[0]
    return None


class CursorAdapter(PresetAdapter):
    key = "cursor"
    label = "Cursor"
    default_program = "cursor-agent"
    credential_names = ("CURSOR_API_KEY", "CURSOR_AUTH_TOKEN")
    state_dirs = ("~/.cursor", "~/.config/cursor")
    models = [ModelOption("auto", "Auto")]
    setup = {"install": "curl https://cursor.com/install -fsS | bash", "sign_in": "cursor-agent login", "docs_url": "https://cursor.com/docs/cli", "credential": "either", "notes": ["Cursor's own allow rules can skip approval requests. Review its CLI configuration.", "Interactive Cursor question cards and Windows presets are not supported yet."]}

    def mode_args(self, ctx, config):
        return ["acp"]

    async def signed_in(self, env, config):
        if await super().signed_in(env, config):
            return True
        program = self.program(config)
        result = await cli_probe.run([*program, "status"], timeout=3, env=env) if program else None
        if result and not result.timed_out:
            text = (result.stdout + result.stderr).lower()
            if "not logged in" in text or "not authenticated" in text:
                return False
            if "logged in" in text or "authenticated as" in text:
                return True
        return None

    def authenticate(self, methods, env):
        if any(env.get(n) for n in self.credential_names):
            return None
        return "cursor_login" if any(isinstance(m, dict) and m.get("id") == "cursor_login" for m in methods) else None

    async def live_models(self, program, env):
        now, cached = self._model_cache
        if cached and time.monotonic() - now < 600:
            return cached
        result = await cli_probe.run([*program, "models"], timeout=3, env=env)
        models = []
        if result.ok:
            for line in result.stdout.splitlines():
                match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._:/@+\-\[\]]*)\s+-\s+(.+)$", line)
                if match:
                    slug, label = match.groups()
                    models.append(ModelOption(slug, re.sub(r"\s*\((?:default|current)\)", "", label), "(default)" in label))
        if models:
            self._model_cache = (time.monotonic(), models[:600])
        return models[:600] or cached or self.models

    async def select_model(self, call, session_id, session, model):
        data = session_models(session)
        current = data.get("currentModelId")
        if not model:
            return current
        mapped = cursor_model_id(model, data.get("availableModels") or [])
        if not mapped:
            raise InvalidConfig("The session did not offer this model.")
        if mapped != current:
            await call("session/set_model", {"sessionId": session_id, "modelId": mapped}, handle_incoming=True)
        return mapped


class OpenCodeAdapter(PresetAdapter):
    key = "opencode"
    label = "OpenCode (experimental)"
    default_program = "opencode"
    credential_names = ("OPENCODE_API_KEY",)
    state_dirs = ("~/.local/share/opencode", "~/.config/opencode", "~/.cache/opencode")
    setup = {"install": "npm install -g opencode-ai", "sign_in": "opencode auth login", "docs_url": "https://opencode.ai/docs/", "credential": "either", "notes": ["Experimental: enable with OPEN_DOTS_PRESET_ENGINES=opencode. Permission overrides require a real CLI adversarial configuration check before default enablement.", "Windows presets are not supported yet."]}

    def mode_args(self, ctx, config):
        return ["acp"]

    def child_env(self, config, ctx=None):
        env = super().child_env(config, ctx)
        # Ambient provider keys must not silently select a billed provider.
        for name in list(env):
            if name.endswith(("_API_KEY", "_AUTH_TOKEN")) and name not in self.credential_names:
                env.pop(name)
        for name in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT"):
            env.pop(name, None)
        deny = ctx is not None and (ctx.approval_mode == "plan" or not app_settings.get("LOCAL_TOOLS_ENABLED"))
        rules = {"*": "ask", "edit": "deny" if deny else "ask", "bash": "deny" if deny else "ask", "webfetch": "ask", "external_directory": "ask"}
        if ctx is not None and not app_settings.get("LOCAL_TOOLS_ENABLED"):
            rules = {"*": "deny"}
        env.update(OPENCODE_PERMISSION=json.dumps(rules), OPENCODE_DISABLE_PROJECT_CONFIG="1")
        return env

    async def signed_in(self, env, config):
        return None

    async def live_models(self, program, env):
        now, cached = self._model_cache
        if cached and time.monotonic() - now < 600:
            return cached
        result = await cli_probe.run([*program, "models"], timeout=3, env=env)
        models = []
        if result.ok:
            for line in result.stdout.splitlines():
                slug = line.strip()
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}", slug) and "/" in slug:
                    models.append(ModelOption(slug, slug))
        if models:
            self._model_cache = (time.monotonic(), models[:600])
        return models[:600] or cached

    async def select_model(self, call, session_id, session, model):
        option = next((o for o in session.get("configOptions", []) if isinstance(o, dict) and o.get("id") == "model"), None)
        if not option:
            if model:
                raise InvalidConfig("The CLI did not offer model configuration.")
            return session_models(session).get("currentModelId")
        if not model:
            return option.get("currentValue")
        values = []
        for item in option.get("options", []):
            values.extend(item.get("options", []) if isinstance(item, dict) and "options" in item else [item])
        if model not in {v.get("value") for v in values if isinstance(v, dict)}:
            raise InvalidConfig("The session did not offer this model.")
        result = await call("session/set_config_option", {"sessionId": session_id, "configId": "model", "value": model}, handle_incoming=True)
        confirmed = next((o.get("currentValue") for o in (result or {}).get("configOptions", []) if isinstance(o, dict) and o.get("id") == "model"), None)
        if confirmed != model:
            raise InvalidConfig("The engine did not confirm this model.")
        return confirmed
