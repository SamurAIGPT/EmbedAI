"""The self-hosting guide and the example files in deploy/ must stay true to the code: every
setting they name exists, the ports agree, the links resolve, the commands exist, the host proxy
examples keep what the server relies on, and the blocks scripts/verify-guide.py runs are still
there. (The commands themselves are run by that script, not here.)"""

import importlib.util
import re
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.engine_env import EXTRA_NAMES_VARIABLE
from app.server import entry, proxies
from tests.conftest import BACKEND_DIR

ROOT = BACKEND_DIR.parent
DOC = ROOT / "docs" / "SELF-HOSTING.md"
DEPLOY = ROOT / "deploy"
pytestmark = pytest.mark.skipif(not DOC.exists(), reason="the repository's docs are not available")
FILES = [DOC, *sorted(path for path in DEPLOY.rglob("*") if path.is_file())]

# Read by the server entry point, not by the Settings class.
ENTRY_SETTINGS = {"OPEN_DOTS_HOST", "OPEN_DOTS_PORT", "OPEN_DOTS_UI_DIR", "OPEN_DOTS_ALLOW_PUBLIC_BIND",
                  "OPEN_DOTS_EXTRA_PATH", proxies.ENV_NAME}
SERVER_COMMANDS = {"migrate", "doctor", "backup", "restore", "service"}
AUTH_COMMANDS = {"set-passphrase", "pairing-code", "devices", "revoke"}


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compose_variables() -> set[str]:
    names: set[str] = set()
    for path in [ROOT / "compose.yaml", *(DEPLOY / "proxy").glob("compose.*.yaml"), DEPLOY / "proxy" / "Caddyfile"]:
        names |= set(re.findall(r"\bOPEN_DOTS_[A-Z_]+\b", text(path)))
    return names


def test_the_files_exist():
    assert {"Caddyfile", "nginx.conf.example", "open-dots.env.example"} <= {path.name for path in DEPLOY.iterdir()}
    assert not (DEPLOY / "open-dots.service.example").exists(), "the unit comes from `python -m app.server service`"
    assert DOC.is_file()


def test_every_setting_in_the_env_example_exists():
    known = set(Settings.model_fields) | ENTRY_SETTINGS | {"OPEN_DOTS_PUBLIC_URL", EXTRA_NAMES_VARIABLE}
    named = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text(DEPLOY / "open-dots.env.example"), re.MULTILINE))
    assert named, "the example names no settings?"
    assert named <= known, f"not settings: {sorted(named - known)}"


def test_every_open_dots_name_the_docs_use_is_a_setting_or_a_compose_variable():
    known = set(Settings.model_fields) | ENTRY_SETTINGS | compose_variables() | {"OPEN_DOTS_PUBLIC_URL", EXTRA_NAMES_VARIABLE}
    for path in FILES:
        named = set(re.findall(r"\bOPEN_DOTS_[A-Z_]+\b", text(path)))
        assert named <= known, f"{path.name} names things that do not exist: {sorted(named - known)}"


def test_the_required_settings_are_in_the_example_uncommented():
    lines = text(DEPLOY / "open-dots.env.example").splitlines()
    active = {line.split("=", 1)[0] for line in lines if re.match(r"^[A-Z]", line)}
    assert {"OPEN_DOTS_PUBLIC_URL", "TOKEN_ENCRYPTION_KEYS", "OPEN_DOTS_UI_DIR", "OPEN_DOTS_HOST", "OPEN_DOTS_PORT",
            "LOCAL_EXECUTION_ENABLED"} <= active
    assert "LOCAL_EXECUTION_ENABLED=true" in lines  # agents do not run without it, and the server does not turn it on


def test_the_host_proxy_examples_point_at_the_servers_default_port(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    options = entry.read_options([], {"OPEN_DOTS_UI_DIR": str(tmp_path)})
    for name in ("Caddyfile", "nginx.conf.example"):
        assert f"127.0.0.1:{options.port}" in text(DEPLOY / name), name
    assert f"OPEN_DOTS_PORT={options.port}" in text(DEPLOY / "open-dots.env.example")
    assert f"OPEN_DOTS_HOST={options.host}" in text(DEPLOY / "open-dots.env.example")


def uncommented(path: Path) -> str:
    return "\n".join(line for line in text(path).splitlines() if not line.strip().startswith("#"))


def test_the_host_nginx_example_keeps_what_the_tested_one_has_with_only_the_upstream_changed():
    host, tested = uncommented(DEPLOY / "nginx.conf.example"), uncommented(DEPLOY / "proxy" / "nginx.conf")
    for required in ("proxy_set_header Host              $host;", "proxy_set_header X-Forwarded-For   $remote_addr;",
                     "proxy_set_header X-Forwarded-Proto $scheme;", "proxy_buffering off;", "proxy_read_timeout 3600s;",
                     "client_max_body_size 20m;", "return 301 https://$host$request_uri;"):
        assert required in host and required in tested, required
    assert "proxy_add_x_forwarded_for" not in host and "gzip" not in host
    assert "location ~ ^/api/v1/threads/[0-9]+/events/stream$ {" in host
    assert host.count("proxy_pass http://127.0.0.1:8000;") == 2 and "open-dots:8000" not in host


def test_the_host_caddy_example_keeps_what_the_tested_one_has_with_only_the_upstream_changed():
    host = uncommented(DEPLOY / "Caddyfile")
    assert "flush_interval -1" in host and "max_size 20MB" in host
    assert "reverse_proxy 127.0.0.1:8000" in host and "open-dots:8000" not in host
    assert "trusted_proxies" not in host and "encode" not in host  # both would change what the server is told


def test_relative_links_resolve():
    for path in (DOC, *(p for p in FILES if p.suffix in (".md",))):
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text(path)):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            assert (path.parent / target).resolve().exists(), f"{path.name} links to {target}, which does not exist"


def test_the_guide_links_to_its_example_files_and_neighbours():
    guide = text(DOC)
    for target in ("../deploy/Caddyfile", "../deploy/nginx.conf.example", "../deploy/open-dots.env.example",
                   "../deploy/proxy/", "AUTH.md", "ENGINES.md", "ROUTINES.md", "COMPUTERS.md", "ROADMAP.md"):
        assert f"]({target})" in guide, target


def test_the_guide_says_what_was_not_run():
    guide = text(DOC)
    assert "experimental" in guide.lower() and "## What has been checked" in guide
    for open_question in ("Real public certificates", "The systemd unit actually starting", "not run on a real Linux host"):
        assert open_question in guide, open_question
    assert "**Not run**" in guide


def test_public_docs_and_examples_never_name_the_reference_product():
    for path in [*FILES, ROOT / "README.md", ROOT / "CHANGELOG.md", ROOT / "docs" / "ROADMAP.md", ROOT / "docs" / "AUTH.md"]:
        assert "maus" not in text(path).lower(), path.name


def test_the_commands_in_the_guide_exist():
    guide = text(DOC)
    assert set(re.findall(r"python -m app\.server (\w+)", guide)) <= SERVER_COMMANDS
    assert set(re.findall(r"python -m app\.auth\.cli ([a-z-]+)", guide)) <= AUTH_COMMANDS
    assert {"migrate", "doctor", "backup", "restore", "service"} <= set(re.findall(r"python -m app\.server (\w+)", guide))
    assert (ROOT / "scripts" / "build-ui.js").is_file()
    assert (ROOT / "scripts" / "probe-docker-trust.sh").is_file() and "scripts/probe-docker-trust.sh" in guide


def test_the_guide_never_says_the_server_turns_execution_on_or_names_a_network_as_trusted():
    guide = text(DOC)
    assert "on by default" not in guide
    assert not re.search(r"TRUSTED_PROXIES=\S*/\d+", guide + text(DEPLOY / "open-dots.env.example"))
    assert "172.18.0.0/16" not in guide


def test_the_blocks_the_verification_script_runs_are_still_in_the_guide():
    spec = importlib.util.spec_from_file_location("verify_guide", ROOT / "scripts" / "verify-guide.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found = module.blocks()
    roles = {block.role for block in found}
    assert roles <= {"root", "account", "docker", "skip"}, roles
    assert {"root", "account", "docker"} <= roles
    serve = [block for block in found if block.extra == "serve"]
    check = [block for block in found if block.extra == "check"]
    assert len(serve) == len(check) == 1 and found.index(check[0]) == found.index(serve[0]) + 1
    assert len([b for b in found if b.role != "skip"]) >= 10
    skipped = " ".join(block.body for block in found if block.role == "skip")
    assert "git clone" in skipped and "systemctl" in skipped  # what cannot run in a container is said to be skipped


def test_the_direct_route_settings_choose_sqlite_because_the_default_is_postgresql():
    assert Settings(DATABASE_BACKEND="", TOKEN_ENCRYPTION_KEYS="x", _env_file=None).DATABASE_KIND == "postgres"
    assert "   DATABASE_BACKEND=sqlite\n" in text(DOC)  # the settings the guide writes for the direct route
    assert "DATABASE_BACKEND=sqlite" in text(DEPLOY / "open-dots.env.example")
