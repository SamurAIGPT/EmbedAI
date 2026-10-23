"""Browser MCP isolation, input validation and approval boundaries."""
import json
import time
from types import SimpleNamespace

import pytest

from app.computers import browser, grants, screen
from app.computers.mcp_server import Server
from app.computers.policy import automatic
from tests.computers.harness import docker  # noqa: F401
from tests.computers.test_service import service, agent, workspace, computer_cleanup  # noqa: F401
from tests.services.thread_harness import sessions, owner, cleanup  # noqa: F401


@pytest.mark.parametrize("name,args", [
    ("browser_open", {"url": "x", "endpoint": "http://host:9222"}),
    ("browser_fill", {"selector": "input", "text": "x" * 10001}),
    ("browser_click", {}),
    ("browser_click", {"ref": "e1", "selector": "button"}),
    ("browser_read", {"offset": True}),
    ("browser_read", {"offset": -1}),
    ("browser_tabs", {"action": "switch"}),
    ("browser_dialog", {"action": "yes"}),
])
def test_invalid_arguments(name, args):
    assert browser.validate(name, args)


def test_browser_tools_only_exist_on_an_assigned_desktop(tmp_path):
    shell = Server("safe-name", "123:456", tmp_path / "grants", tmp_path / "state", None)
    desktop = Server("safe-name", "123:456", tmp_path / "grants", tmp_path / "state", None, "desktop", browser_enabled=True)
    disabled = Server("safe-name", "123:456", tmp_path / "grants", tmp_path / "state", None, "desktop")
    query = {"id": 1, "method": "tools/list"}
    assert not any(t["name"].startswith("browser_") for t in shell.handle(query)["result"]["tools"])
    assert {t["name"] for t in browser.TOOLS} <= {t["name"] for t in desktop.handle(query)["result"]["tools"]}
    assert shell.call("browser_snapshot", {})["isError"]
    assert disabled.call("browser_snapshot", {})["isError"]
    assert not any(t["name"].startswith("browser_") for t in disabled.handle(query)["result"]["tools"])


def test_all_browser_inputs_need_permission_and_plan_refuses_them():
    for name in browser.INPUT_NAMES:
        tool = "mcp__computer__" + name
        for mode in ("ask", "accept_edits", None):
            assert automatic(tool, mode) is None
            assert automatic(tool, mode, True) == ("allowed", "screen_turn_grant")
        assert automatic(tool, "plan", True) == ("denied", "plan_mode")
    for name in browser.READ_NAMES:
        assert automatic("mcp__computer__" + name, "plan") == ("allowed", "computer_browser_read")


async def test_browser_opt_in_survives_reload_and_is_bound_to_the_next_turn(service, agent, workspace, docker):  # noqa: F811
    from app.computers import docker_cli
    computer, _ = await service.create_for_agent(agent, workspace=workspace, kind="desktop")
    assert computer.browser_enabled is False
    docker.add_image(docker_cli.DESKTOP_IMAGE)
    await service.start(computer.id)
    binding = await service.attach_for_turn(agent.id, 8001, workspace)
    try:
        assert "--browser" not in binding.launch.args and "browser_open" not in binding.prompt_note()
    finally:
        await service.release_turn(binding, 8001)
    await service.update_config(computer.id, browser_enabled=True)
    assert (await service.get(computer.id)).browser_enabled is True
    binding = await service.attach_for_turn(agent.id, 8002, workspace)
    try:
        assert "--browser" in binding.launch.args and "browser_open" in binding.prompt_note()
        from app.computers.provider import ComputerError
        with pytest.raises(ComputerError, match="Stop the active turn"):
            await service.update_config(computer.id, browser_enabled=False)
    finally:
        await service.release_turn(binding, 8002)
    await service.update_config(computer.id, browser_enabled=False)
    binding = await service.attach_for_turn(agent.id, 8003, workspace)
    try:
        assert "--browser" not in binding.launch.args
    finally:
        await service.release_turn(binding, 8003)


def test_permits_are_exact_and_one_use_and_takeover_is_enforced(monkeypatch, tmp_path):
    requests = []
    def run(command, **kwargs):
        requests.append((command, json.loads(kwargs["input_bytes"])))
        return SimpleNamespace(code=0, timed_out=False, stdout=json.dumps({"content": [{"type": "text", "text": "ok"}]}))
    monkeypatch.setattr(browser.docker_cli, "run", run)
    server = Server("safe-name", "123:456", tmp_path / "grants", tmp_path / "state", None, "desktop", "turn-1", 2, True)
    assert server.call("browser_open", {"url": "https://example.com"})["isError"]
    assert not requests
    args = {"url": "https://example.com"}
    grants.issue(server.grants_dir, "browser_open", args)
    assert server.call("browser_open", {"url": "https://other.test"})["isError"]
    assert not server.call("browser_open", args).get("isError")
    assert server.call("browser_open", args)["isError"]
    command, request = requests[0]
    assert command == ["exec", "-i", "-u", "123:456", "-e", "DISPLAY=:1", "-e", "HOME=/home/agent", "safe-name", "od-browser"]
    assert request["owner"] == "turn-1"
    screen.write_state(server.state_dir / "control.json", {"until": time.time()+35})
    grants.issue(server.grants_dir, "browser_open", args)
    assert "taken control" in server.call("browser_open", args)["content"][0]["text"]
    assert len(requests) == 1
    (server.state_dir / "control.json").unlink()
    screen.write_state(server.state_dir / "browser-epoch.json", {"at": 123})
    grants.issue(server.grants_dir, "browser_snapshot", {})
    assert not server.call("browser_snapshot", {}).get("isError")
    assert requests[-1][1]["epoch"] == 123
    two = Server("safe-name", "123:456", tmp_path / "two", server.state_dir, None, "desktop", "turn-2", browser_enabled=True)
    grants.issue(two.grants_dir, "browser_open", args)
    assert "Another conversation" in two.call("browser_open", args)["content"][0]["text"]
    grants.issue(server.grants_dir, "browser_open", args)
    assert not server.call("browser_open", args).get("isError")
    grants.issue(server.grants_dir, "browser_open", args)
    assert "limit" in server.call("browser_open", args)["content"][0]["text"]
