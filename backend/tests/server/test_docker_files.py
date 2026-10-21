"""Guards on the Docker and proxy files that need no Docker.

scripts/smoke-docker.py and scripts/smoke-proxy.py build and run the image and the proxy overlays
for real; these checks only keep the properties that must not drift quietly: no secrets in the
build context, an image that carries no engine and no setting by default, a published port that
stays on the loopback, no network ever named as a trusted proxy, and versions that agree with the
rest of the repository.
"""

import ipaddress
import re

import pytest

from app.server import proxies
from tests.conftest import BACKEND_DIR

REPO = BACKEND_DIR.parent
pytestmark = pytest.mark.skipif(not (REPO / "Dockerfile").exists(), reason="the repository root is not available")


def text(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def uncommented(name: str) -> str:
    return "\n".join(line for line in text(name).splitlines() if not line.strip().startswith("#"))


def instructions(dockerfile: str) -> list[str]:
    """Dockerfile instructions with continuation lines joined and comments dropped."""
    joined = re.sub(r"\\\n\s*", " ", dockerfile)
    return [line.strip() for line in joined.splitlines() if line.strip() and not line.strip().startswith("#")]


def compose_default(name: str, variable: str) -> str:
    return re.search(variable + r":-([0-9./]+)\}", uncommented(name)).group(1)


# -- the image ------------------------------------------------------------------------------------

def test_the_build_context_never_includes_settings_or_private_notes():
    ignored = text(".dockerignore").splitlines()
    for pattern in (".env", ".env.*", "**/.env", ".internal", "**/open-dots.env", ".git", "**/node_modules", "**/.venv", "backend/tests"):
        assert pattern in ignored, pattern


def test_the_image_carries_no_setting_secret_or_engine_by_default():
    steps = instructions(text("Dockerfile"))
    assert 'ARG ENGINES=""' in steps  # no engine unless one is named at build time
    envs = " ".join(step for step in steps if step.startswith("ENV "))
    for forbidden in ("TOKEN_ENCRYPTION_KEYS", "LOCAL_EXECUTION_ENABLED", "LOCAL_TOOLS_ENABLED", "LOOPBACK_TRUST", "TRUSTED_PROXIES", "PASSPHRASE"):
        assert forbidden not in envs, f"the image sets {forbidden}; that belongs to the owner's settings file"
    assert not [step for step in steps if re.match(r"(COPY|ADD) \.\s", step)], "copy named folders, never the whole context"
    assert not [step for step in steps if "claude" in step.lower() and "install" in step.lower() and "$ENGINES" not in step]


def test_the_image_runs_as_a_non_root_user_with_its_home_on_the_volume():
    steps = instructions(text("Dockerfile"))
    users = [step for step in steps if step.startswith("USER ")]
    assert users and users[-1] != "USER root" and users[-1].startswith("USER opendots")
    assert any("useradd" in step and "--home-dir /data" in step for step in steps)
    assert 'VOLUME ["/data"]' in steps
    assert any(step.startswith("HEALTHCHECK") and "/api/v1/health" in step for step in steps)
    assert any(step.startswith("ENTRYPOINT") and "tini" in step for step in steps)
    command = next(step for step in steps if step.startswith("CMD"))
    assert "app.server migrate" in command and "exec python -m app.server" in command  # prepare the database, then serve


def test_the_image_listens_on_the_container_address_only_with_the_explicit_opt_in():
    steps = " ".join(instructions(text("Dockerfile")))
    assert "OPEN_DOTS_HOST=0.0.0.0" in steps and "OPEN_DOTS_ALLOW_PUBLIC_BIND=1" in steps


def test_the_image_versions_agree_with_the_rest_of_the_repository():
    dockerfile = text("Dockerfile")
    python = re.search(r"ARG PYTHON_IMAGE=python:(\d+\.\d+)", dockerfile).group(1)
    assert f">={python}" in text("backend/pyproject.toml").replace('"', "")  # the language the project requires
    assert text("backend/.python-version").strip() == python
    node = re.search(r"ARG NODE_IMAGE=node:(\d+)", dockerfile).group(1)
    assert f"node-version: {node}" in text(".github/workflows/ci.yml")  # the Node that CI builds the pages with


# -- compose: nothing is believed unless it is named ------------------------------------------------

def test_compose_publishes_on_the_loopback_and_believes_no_proxy_unless_one_is_named():
    compose = uncommented("compose.yaml")
    assert re.search(r'"\$\{OPEN_DOTS_BIND:-127\.0\.0\.1\}:\$\{OPEN_DOTS_PORT:-8000\}:8000"', compose)
    assert "TRUSTED_PROXIES: ${OPEN_DOTS_TRUSTED_PROXIES:-}" in compose  # empty by default: loopback only, no network named
    for forbidden in ("network_mode", "docker.sock", "privileged", "TOKEN_ENCRYPTION_KEYS", "LOCAL_EXECUTION_ENABLED", "TRUSTED_PROXIES: '*'", 'TRUSTED_PROXIES: "*"'):
        assert forbidden not in compose, forbidden
    assert "env_file:" in compose and "OPEN_DOTS_ENV_FILE" in compose
    assert "ENGINES: ${OPEN_DOTS_ENGINES:-}" in compose  # empty by default


def test_the_network_reserves_the_proxy_address_so_a_stray_container_cannot_land_on_it():
    subnet = ipaddress.ip_network(compose_default("compose.yaml", "OPEN_DOTS_SUBNET"))
    gateway = ipaddress.ip_address(compose_default("compose.yaml", "OPEN_DOTS_GATEWAY"))
    dynamic = ipaddress.ip_network(compose_default("compose.yaml", "OPEN_DOTS_DYNAMIC_RANGE"))
    proxy = ipaddress.ip_address(compose_default("deploy/proxy/compose.nginx.yaml", "OPEN_DOTS_PROXY_IP"))
    assert subnet.is_private and gateway in subnet and dynamic.subnet_of(subnet)
    assert proxy in subnet and proxy != gateway
    assert proxy not in dynamic and gateway not in dynamic  # unassigned containers get addresses from the dynamic range only


@pytest.mark.parametrize("overlay", ["compose.nginx.yaml", "compose.caddy.yaml"])
def test_a_proxy_overlay_trusts_exactly_the_proxy_address_and_never_a_network(overlay):
    body = uncommented(f"deploy/proxy/{overlay}")
    default = "172.31.250.10"
    assert "TRUSTED_PROXIES: ${OPEN_DOTS_TRUSTED_PROXIES:-${OPEN_DOTS_PROXY_IP:-" + default + "}}" in body
    assert "ipv4_address: ${OPEN_DOTS_PROXY_IP:-" + default + "}" in body  # the proxy keeps the address the app trusts
    assert compose_default(f"deploy/proxy/{overlay}", "OPEN_DOTS_PROXY_IP") == default
    assert proxies.parse(default) == (*proxies.LOOPBACK, default)  # one address, not a range
    for forbidden in ("network_mode", "docker.sock", "privileged", "TOKEN_ENCRYPTION_KEYS"):
        assert forbidden not in body, forbidden
    assert "${OPEN_DOTS_DOMAIN:?" in body  # no default name: the owner must say which one


# -- the proxy configurations -------------------------------------------------------------------------

def test_the_nginx_config_provides_what_the_server_relies_on():
    conf = uncommented("deploy/proxy/nginx.conf")
    assert re.search(r"proxy_set_header X-Forwarded-For\s+\$remote_addr;", conf)  # set, never appended to
    assert "proxy_add_x_forwarded_for" not in conf
    assert re.search(r"proxy_set_header X-Forwarded-Proto\s+\$scheme;", conf)
    assert re.search(r"proxy_set_header Host\s+\$host;", conf)
    stream = re.search(r"location ~ \^/api/v1/threads/\[0-9\]\+/events/stream\$ \{(.*?)\n    \}", conf, re.S).group(1)
    assert "proxy_buffering off;" in stream
    assert "gzip" not in conf and "proxy_ignore_headers" not in conf  # a compressed or buffered stream is held back
    assert int(re.search(r"client_max_body_size (\d+)m;", conf).group(1)) >= 12  # the server's 10 MB image limit plus form overhead
    assert "return 301 https://$host$request_uri;" in conf
    assert "resolver 127.0.0.11" in conf  # the app is found by name at request time, so recreating it needs no reload


def test_the_caddy_config_provides_what_the_server_relies_on():
    conf = uncommented("deploy/proxy/Caddyfile")
    assert "flush_interval -1" in conf
    assert int(re.search(r"max_size (\d+)MB", conf).group(1)) >= 12
    assert "reverse_proxy open-dots:8000" in conf
    assert "{$OPEN_DOTS_DOMAIN}" in conf and "{$OPEN_DOTS_CADDY_TLS}" in conf
    # Caddy replaces a client-supplied X-Forwarded-For unless told to trust upstream proxies: never tell it to.
    assert "trusted_proxies" not in conf and "encode" not in conf
