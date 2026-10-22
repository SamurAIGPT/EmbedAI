"""The environment an engine process gets: an allow-list, never the server's
whole environment.

An engine turn, and every shell command or tool server it starts, can be
steered by text from outside (a routine reading a web page, a webhook body).
Whatever is in its environment is readable by that text, so only names an
engine or a normal shell needs are passed. A name the operator needs on top
(a token for `gh`, a proxy login) is listed in `OPEN_DOTS_ENGINE_ENV`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

EXTRA_NAMES_VARIABLE = "OPEN_DOTS_ENGINE_ENV"

# What a shell, a runtime and the OS's own tools expect, on every platform.
_NAMES = frozenset(
    {
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "HOSTNAME", "PWD", "TERM", "COLORTERM",
        "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP", "NO_COLOR", "FORCE_COLOR", "CI", "DEBUG",
        "NODE_ENV", "NODE_OPTIONS", "NODE_EXTRA_CA_CERTS", "NODE_USE_ENV_PROXY",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "no_proxy", "all_proxy",
        "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "__CF_USER_TEXT_ENCODING", "GOOGLE_API_KEY",
        "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING", "PYTHONUTF8",
        "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
        # Windows: without these a child cannot find the system, the profile or the shell.
        "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERNAME", "USERDOMAIN",
        "COMPUTERNAME", "OS", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
        "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES",
        "ALLUSERSPROFILE", "PUBLIC", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    }
)

# The engines' own settings and provider keys. Cloud-account routes (AWS for
# Bedrock, Google Cloud for Vertex, Azure) and `SSH_AUTH_SOCK` carry more than
# a model key, so they are opt-in through `OPEN_DOTS_ENGINE_ENV`.
_PREFIXES = (
    "LC_", "XDG_", "NPM_CONFIG_", "npm_config_",
    "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_", "GEMINI_",
)

# Never passed, whatever the lists say: this app's own secrets and database settings.
_NEVER = frozenset(
    {
        "BOAT_API_KEY", "TOKEN_ENCRYPTION_KEYS", "HOOK_TOKEN", "TELEGRAM_BOT_TOKEN",
        "POSTGRES_PASSWORD", "DATABASE_URL", "SQLITE_PATH", "SECRET_KEY", EXTRA_NAMES_VARIABLE,
    }
)
_NEVER_PREFIXES = ("OPEN_DOTS_", "POSTGRES_", "TOKEN_ENCRYPTION")


def _never(name: str) -> bool:
    return name.upper() in _NEVER or name.upper().startswith(_NEVER_PREFIXES)


def _extra(environ: Mapping[str, str]) -> tuple[frozenset[str], tuple[str, ...]]:
    """Names and `PREFIX*` patterns the operator added; entries for a never-passed name are ignored."""
    names: set[str] = set()
    prefixes: list[str] = []
    for item in environ.get(EXTRA_NAMES_VARIABLE, "").split(","):
        item = item.strip()
        if not item:
            continue
        if item.endswith("*"):
            if len(item) > 1 and not _never(item[:-1]):
                prefixes.append(item[:-1])
        elif not _never(item):
            names.add(item)
    return frozenset(names), tuple(prefixes)


def allows(name: str, environ: Mapping[str, str] | None = None) -> bool:
    if _never(name):
        return False
    if name in _NAMES or name.startswith(_PREFIXES):
        return True
    extra_names, extra_prefixes = _extra(os.environ if environ is None else environ)
    return name in extra_names or name.startswith(extra_prefixes)


def engine_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The allowed names of `environ` (default: this process's), in a fresh dict."""
    source = os.environ if environ is None else environ
    extra_names, extra_prefixes = _extra(source)
    kept: dict[str, str] = {}
    for name, value in source.items():
        if _never(name):
            continue
        if name in _NAMES or name.startswith(_PREFIXES) or name in extra_names or name.startswith(extra_prefixes):
            kept[name] = value
    return kept
