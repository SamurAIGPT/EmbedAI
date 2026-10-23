"""Desktop boundaries through real child processes and the fake Docker daemon."""
import asyncio
import dataclasses
import json
import time

import pytest

from app.computers import docker_cli, grants, screen
from app.computers.control import ControlRegistry
from app.computers.mcp_server import Server
from app.computers.policy import automatic
from app.computers.provider import DockerProvider, mcp_launch_for
from tests.computers.harness import IMAGE, INSTANCE, docker  # noqa: F401
from tests.computers.test_service import service, agent, workspace, computer_cleanup  # noqa: F401
from tests.services.thread_harness import sessions, owner, cleanup  # noqa: F401


def test_shell_command_and_fingerprint_stay_compatible(tmp_path):
    spec = docker_cli.Spec(1, INSTANCE, IMAGE, str(tmp_path), user="123:456")
    args = docker_cli.run_args(spec)
    assert args[-3:] == [IMAGE, "sleep", "infinity"]
    assert "--shm-size" not in args and "DISPLAY=:1" not in args
    desktop = dataclasses.replace(spec, kind="desktop", shm_mb=512)
    args = docker_cli.run_args(desktop)
    assert args[-2:] == [IMAGE, "od-desktop"]
    assert args[args.index("--shm-size")+1] == "512m"
    assert args.count("--mount") == 1 and "--publish" not in args and "--cap-add" not in args
    assert "DISPLAY=:1" in args and "HOME=/home/agent" in args
    assert docker_cli.fingerprint(spec) != docker_cli.fingerprint(desktop)


@pytest.mark.parametrize("name,args", [("click", {"x": 1.0, "y": 0}), ("click", {"x": 1280, "y": 1}), ("click", {"x": True, "y": 0}), ("key", {"keys": "ctrl+l; touch x"}), ("type", {"text": "x"*2001}), ("screenshot", {"x": 1200, "y": 0, "width": 100, "height": 1}), ("wait", {"seconds": 11})])
def test_invalid_screen_arguments(name, args):
    assert screen.validate(name, args)


def test_screen_policy_requires_turn_grant_even_in_accept_edits():
    for mode in ("ask", "accept_edits", None):
        assert automatic("mcp__computer__click", mode) is None
        assert automatic("mcp__computer__click", mode, True) == ("allowed", "screen_turn_grant")
    assert automatic("mcp__computer__click", "plan", True) == ("denied", "plan_mode")
    assert automatic("mcp__computer__screenshot", "plan") == ("allowed", "computer_screen_read")


def test_control_lease_does_not_leak_and_recovers_after_disconnect():
    now = [100.0]
    c = ControlRegistry(lambda: now[0])
    snapshot, owned, acquired, lease = c.take(1)
    assert owned and acquired and lease and lease not in str(snapshot)
    assert lease not in repr(c._holds[1])
    assert c.take(1, lease)[1:3] == (True, False)
    assert c.take(1)[1:3] == (False, False)
    assert not c.release(1, "other")
    c.viewer_opened(1, "viewer")
    now[0] += 10
    assert c.heartbeat(1, lease)
    c.viewer_closed(1, "viewer")
    now[0] += 20
    assert c.expire() == [1]
    assert not c.heartbeat(1, lease)
    _, _, _, replacement = c.take(1)
    now[0] += 30
    assert not c.owns(1, replacement)


async def test_desktop_defaults_lifecycle_and_coalesced_picture(service, agent, workspace, docker):  # noqa: F811
    computer, _ = await service.create_for_agent(agent, workspace=workspace, kind="desktop")
    assert computer.kind == "desktop" and computer.cpu_millis == 2000
    assert computer.memory_mb == 2048 and computer.pids_limit == 1024 and computer.network == "none"
    docker.add_image(docker_cli.DESKTOP_IMAGE)
    computer = await service.start(computer.id)
    assert computer.state == "running"
    assert (await service.live_status(computer))[1].desktop_ready
    images = await asyncio.gather(*(service.screenshot(computer) for _ in range(5)))
    assert len(set(images)) == 1
    assert sum("od-shot" in c for c in docker.calls()) == 1
    with pytest.raises(ValueError):
        await service.update_config(computer.id, kind="shell")
    assert (await service.stop(computer.id)).state == "stopped"


def test_docker_context_is_forwarded_to_only_the_mcp_launch(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "unix:///different.sock")
    launch = mcp_launch_for("safe-name", "1000:1000", tmp_path, tmp_path, None, "desktop")
    assert launch.env["DOCKER_HOST"] == "unix:///different.sock"
    assert launch.args[-2:] == ["--kind", "desktop"]


def test_screen_permits_hold_owner_and_input_cap(docker, tmp_path):  # noqa: F811
    workspace = tmp_path / "workspace"; workspace.mkdir()
    spec = docker_cli.Spec(42, INSTANCE, IMAGE, str(workspace), kind="desktop", user="123:456")
    docker.add_image()
    DockerProvider(str(docker.path)).create(spec)
    state = tmp_path / "computer-state"
    one = Server(spec.name, spec.run_as, tmp_path / "one", state, str(docker.path), "desktop", "one", 1)
    two = Server(spec.name, spec.run_as, tmp_path / "two", state, str(docker.path), "desktop", "two")
    args = {"x": 10, "y": 20}
    assert one.call("click", args)["isError"]
    grants.issue(one.grants_dir, "click", args)
    assert not one.call("click", args).get("isError")
    grants.issue(two.grants_dir, "click", args)
    assert "Another conversation" in two.call("click", args)["content"][0]["text"]
    grants.issue(one.grants_dir, "click", args)
    assert "limit" in one.call("click", args)["content"][0]["text"]
    screen.write_state(state / "control.json", {"until": time.time()+35})
    grants.issue(one.grants_dir, "screenshot", {})
    result = one.call("screenshot", {})
    assert result["isError"] and all(b["type"] == "text" for b in result["content"])
    (state / "control.json").unlink()
    grants.issue(one.grants_dir, "screenshot", {})
    result = one.call("screenshot", {})
    assert result["content"][1]["mimeType"] == "image/png"
    assert len((docker.state_dir / "screen-actions.jsonl").read_text().splitlines()) == 1
