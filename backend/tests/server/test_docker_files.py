"""Guards on the Docker files (Dockerfile, compose.yaml, .dockerignore) that need no Docker.

scripts/smoke-docker.py builds and runs the image for real; these checks only keep the properties
that must not drift quietly: no secrets in the build context, an image that carries no engine and
no setting by default, a published port that stays on the loopback, and versions that agree with
the rest of the repository.
"""

import re

import pytest

from tests.conftest import BACKEND_DIR

REPO = BACKEND_DIR.parent
pytestmark = pytest.mark.skipif(not (REPO / "Dockerfile").exists(), reason="the repository root is not available")


def text(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def instructions(dockerfile: str) -> list[str]:
    """Dockerfile instructions with continuation lines joined and comments dropped."""
    joined = re.sub(r"\\\n\s*", " ", dockerfile)
    return [line.strip() for line in joined.splitlines() if line.strip() and not line.strip().startswith("#")]


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


def test_compose_publishes_on_the_loopback_and_trusts_only_its_own_network():
    compose = "\n".join(line for line in text("compose.yaml").splitlines() if not line.strip().startswith("#"))
    assert re.search(r'"\$\{OPEN_DOTS_BIND:-127\.0\.0\.1\}:\$\{OPEN_DOTS_PORT:-8000\}:8000"', compose)
    assert "TRUSTED_PROXIES: ${OPEN_DOTS_SUBNET:-172.31.250.0/24}" in compose
    assert "subnet: ${OPEN_DOTS_SUBNET:-172.31.250.0/24}" in compose
    for forbidden in ("network_mode", "docker.sock", "privileged", "TOKEN_ENCRYPTION_KEYS", "LOCAL_EXECUTION_ENABLED", "TRUSTED_PROXIES: '*'", 'TRUSTED_PROXIES: "*"'):
        assert forbidden not in compose, forbidden
    assert "env_file:" in compose and "OPEN_DOTS_ENV_FILE" in compose
    assert "ENGINES: ${OPEN_DOTS_ENGINES:-}" in compose  # empty by default


def test_the_compose_default_range_is_a_valid_private_network_that_the_server_accepts():
    import ipaddress

    from app.server import proxies

    network = ipaddress.ip_network(re.search(r"OPEN_DOTS_SUBNET:-([\d./]+)\}", text("compose.yaml")).group(1))
    assert network.is_private and network.prefixlen >= 16
    assert str(network) in proxies.parse(str(network))


def test_the_image_versions_agree_with_the_rest_of_the_repository():
    dockerfile = text("Dockerfile")
    python = re.search(r"ARG PYTHON_IMAGE=python:(\d+\.\d+)", dockerfile).group(1)
    assert f">={python}" in text("backend/pyproject.toml").replace('"', "")  # the language the project requires
    assert text("backend/.python-version").strip() == python
    node = re.search(r"ARG NODE_IMAGE=node:(\d+)", dockerfile).group(1)
    assert f"node-version: {node}" in text(".github/workflows/ci.yml")  # the Node that CI builds the pages with
