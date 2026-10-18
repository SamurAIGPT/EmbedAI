"""The Docker provider against a real Docker daemon.

Skipped unless `docker info` answers within 10 seconds and the computer image
is already present (these tests never download one). Set
OPEN_DOTS_TEST_IMAGE to use another local image that has sh, coreutils and
`sleep infinity` (for example a slim Debian or Ubuntu image)."""

import json
import os
import subprocess
import sys

import pytest

from app.computers import docker_cli, grants
from app.computers.docker_cli import Spec
from app.computers.provider import DockerProvider

IMAGE = os.environ.get("OPEN_DOTS_TEST_IMAGE", docker_cli.DEFAULT_IMAGE)
INSTANCE = "feedfacefeedfacefeedfacefeedface"


def _docker_ready() -> str | None:
    docker = docker_cli.docker_executable()
    if docker is None:
        return "no docker CLI"
    try:
        done = docker_cli.run(["info", "--format", "{{.ServerVersion}}"], timeout=10)
    except docker_cli.DockerMissing as exc:
        return str(exc)
    if done.timed_out or done.code != 0:
        return "the Docker daemon is not answering"
    if docker_cli.run(["image", "inspect", IMAGE], timeout=20).code != 0:
        return f"{IMAGE} is not present (these tests never download images)"
    return None


_why = _docker_ready() if sys.platform != "win32" else "POSIX only"
pytestmark = pytest.mark.skipif(_why is not None, reason=f"real Docker: {_why}")


@pytest.fixture
def machine(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spec = Spec(computer_id=424242, instance=INSTANCE, image=IMAGE, workspace=str(workspace), memory_mb=256, pids_limit=64)
    provider = DockerProvider()
    try:
        yield provider, spec, workspace
    finally:
        provider.delete(spec)


def test_a_real_computer_is_hardened_and_runs_commands_in_the_workspace(machine, tmp_path):
    provider, spec, workspace = machine
    status = provider.create(spec)
    assert status.state == "running" and not status.needs_recreate
    assert provider.create(spec).container_id == status.container_id  # idempotent

    inspect = json.loads(subprocess.run(["docker", "container", "inspect", spec.name], capture_output=True, text=True).stdout)[0]
    host = inspect["HostConfig"]
    assert host["CapDrop"] == ["ALL"] and not host["Privileged"] and host["PidsLimit"] == 64
    assert host["Memory"] == 256 * 1024 * 1024 and host["NetworkMode"] == "none"
    assert inspect["Config"]["User"] == spec.run_as

    launch = provider.mcp_launch(spec, tmp_path / "grants", tmp_path / "state")
    arguments = {"command": "id -u; echo made > made.txt; ls /workspace; cat /proc/self/status | grep CapEff"}
    grants.issue(tmp_path / "grants", "exec", arguments)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "exec", "arguments": arguments}},
    ]
    out = subprocess.run(
        [launch.command, *launch.args], input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True, text=True, timeout=120, env={**os.environ, **launch.env},
    ).stdout
    text = [json.loads(line) for line in out.splitlines()][-1]["result"]["content"][0]["text"]
    assert "exit code: 0" in text
    assert spec.run_as.split(":")[0] in text and "made.txt" in text
    assert "0000000000000000" in text  # no effective capabilities
    assert (workspace / "made.txt").read_text() == "made\n"

    assert provider.stop(spec).state == "stopped"
    assert provider.start(spec).state == "running"
    provider.delete(spec)
    assert provider.status(spec).state == "missing"
