import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.computers import native, policy
from app.computers.service import ComputerService, ComputerUnavailable
from app.desktop import profile


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    value = profile.DesktopProfile(tmp_path, "http://127.0.0.1:1234", "not-used", native_control={"port": 12345, "token": "a" * 64})
    monkeypatch.setattr(profile, "_current", value)
    return value


async def test_web_installations_never_query_or_attach_native_control(monkeypatch):
    monkeypatch.setattr(profile, "_current", None)
    monkeypatch.setattr(native, "request", lambda *_: pytest.fail("a web backend queried native control"))
    assert not await native.selected(1)
    assert not await native.selected(None)


async def test_disconnected_local_service_cannot_silently_fall_back(desktop, monkeypatch):
    def disconnected(*_):
        raise OSError("disconnected")
    monkeypatch.setattr(native, "request", disconnected)
    with pytest.raises(ComputerUnavailable, match="disconnected"):
        await ComputerService().attach_for_turn(1, 2, None)


async def test_native_binding_is_turn_scoped_private_and_revoked_on_release(desktop, monkeypatch):
    calls = []
    def respond(connection, body):
        calls.append(body)
        return {"selected": True} if body["method"] == "selected" else {"capability": "turn-only"}
    monkeypatch.setattr(native, "request", respond)
    service = ComputerService(root=desktop.data_dir / "computers")
    binding = await service.attach_for_turn(7, 13, None)
    assert binding.provider == "native"
    assert binding.computer_id == -7
    assert "turn-only" not in repr(binding.launch)
    connection = binding.grants_dir / "connection.json"
    assert json.loads(connection.read_text()) == {"port": 12345, "token": "turn-only"}
    assert connection.stat().st_mode & 0o777 == 0o600
    assert binding.grants_dir.stat().st_mode & 0o777 == 0o700
    assert "native desktop control" in binding.prompt_note()
    await service.release_turn(binding, 13)
    assert not binding.grants_dir.exists()
    assert calls[-1] == {"method": "release", "agent": 7, "turn": 13}


async def test_failed_acquire_releases_possible_lease_and_deletes_connection(desktop, monkeypatch):
    calls = []
    def respond(connection, body):
        calls.append(body)
        if body["method"] == "acquire":
            raise OSError("connection lost after acquire")
        return {}
    monkeypatch.setattr(native, "request", respond)
    with pytest.raises(ComputerUnavailable):
        await native.attach(7, 13, desktop.data_dir / "computers")
    assert calls[-1] == {"method": "release", "agent": 7, "turn": 13}
    assert list((desktop.data_dir / "computers/grants").iterdir()) == []


@pytest.mark.parametrize("name", ["click", "type_text", "press_key", "scroll"])
def test_native_input_never_inherits_whole_turn_grants_or_accept_edits(name):
    tool = "mcp__computer__native_" + name
    assert tool in policy.SCREEN_INPUT
    for mode in ("ask", "accept_edits"):
        assert policy.automatic(tool, mode, screen_granted=True) is None
    assert policy.automatic(tool, "plan", screen_granted=True) == ("denied", "plan_mode")


def test_native_permit_keeps_exact_arguments_and_original_epoch(tmp_path):
    from app.services.claude_adapter import _issue_permit
    ctx = SimpleNamespace(computer=SimpleNamespace(provider="native", grants_dir=tmp_path), thread_id=1)
    handle = SimpleNamespace(tool="mcp__computer__native_type_text")
    assert not _issue_permit(ctx, handle, {"text": "secret"})
    assert list(tmp_path.iterdir()) == []
    assert _issue_permit(ctx, handle, {"text": "secret"}, 4)
    record = json.loads(next(tmp_path.iterdir()).read_text())
    assert record == {"generation": 4, "tool": "native_type_text", "arguments": {"text": "secret"}}


def test_tool_history_redacts_typing_keys_and_element_tokens():
    value = {"pid": 99, "text": "private", "key": "password", "element_token": "sensitive-label"}
    recorded = native.durable_input("mcp__computer__native_type_text", value)
    assert recorded == {"pid": 99, "text": "[redacted]", "key": "[redacted]", "element_token": "[redacted]"}
    assert value["text"] == "private"  # the live approval retains exact arguments
    assert native.durable_input("mcp__computer__write_file", value) is value


def test_mcp_proxy_does_not_expose_launch_token_or_driver_errors(monkeypatch, tmp_path, capsys):
    connection = tmp_path / "connection.json"
    connection.write_text('{"port":12345,"token":"turn-secret"}')
    def rejected(*_):
        raise OSError("private driver error containing turn-secret")
    monkeypatch.setattr(native, "request", rejected)
    monkeypatch.setattr(native.sys, "stdin", io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "native_type_text", "arguments": {"text": "secret"}}}) + "\n"))
    assert native.main(["--connection", str(connection)]) == 0
    output = capsys.readouterr().out
    assert "turn-secret" not in output and "private driver" not in output
    assert json.loads(output)["result"]["isError"] is True
