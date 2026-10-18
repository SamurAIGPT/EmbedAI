"""The computer tools' MCP server, run as a real child process over stdio, with
the fake docker (which really runs commands in the mounted folder)."""

import json
import subprocess
import sys

import pytest

from app.computers import grants
from app.computers.docker_cli import Spec
from app.computers.mcp_server import LAST_COMMAND_FILE, NOT_APPROVED
from app.computers.provider import DockerProvider
from tests.computers.harness import IMAGE, INSTANCE, docker  # noqa: F401


@pytest.fixture
def machine(docker, tmp_path):  # noqa: F811
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spec = Spec(computer_id=5, instance=INSTANCE, image=IMAGE, workspace=str(workspace), user="501:20")
    docker.add_image()
    provider = DockerProvider(docker=str(docker.path))
    provider.create(spec)
    launch = provider.mcp_launch(spec, tmp_path / "grants", tmp_path / "state")
    return provider, spec, launch, workspace


def talk(launch, *requests):
    import os

    proc = subprocess.run(
        [launch.command, *launch.args],
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True, text=True, timeout=60, env={**os.environ, **launch.env},
    )
    assert proc.returncode == 0, proc.stderr
    return {r["id"]: r for r in map(json.loads, proc.stdout.splitlines()) if "id" in r}


def call(launch, name, arguments):
    replies = talk(
        launch,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    )
    result = replies[2]["result"]
    return result["content"][0]["text"], bool(result.get("isError"))


def grants_dir(launch):
    from pathlib import Path

    return Path(launch.args[launch.args.index("--grants") + 1])


def test_it_lists_four_tools_and_answers_ping(machine):
    _, _, launch, _ = machine
    replies = talk(
        launch,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
    )
    assert replies[1]["result"]["serverInfo"]["name"] == "open-dots-computer"
    assert [t["name"] for t in replies[2]["result"]["tools"]] == ["exec", "read_file", "write_file", "list_files"]
    assert replies[3]["result"] == {}
    assert replies[4]["error"]["code"] == -32601


def test_a_call_without_a_permit_is_refused_and_nothing_runs(machine, docker):  # noqa: F811
    _, _, launch, workspace = machine
    before = docker.commands().count("exec")
    text, is_error = call(launch, "exec", {"command": "touch pwned"})
    assert is_error and text == NOT_APPROVED
    assert not (workspace / "pwned").exists()
    assert docker.commands().count("exec") == before


def test_an_approved_command_runs_in_the_container_once(machine, docker):  # noqa: F811
    _, spec, launch, workspace = machine
    arguments = {"command": "echo hello > note.txt && cat note.txt"}
    grants.issue(grants_dir(launch), "exec", arguments)
    text, is_error = call(launch, "exec", arguments)
    assert not is_error
    assert "exit code: 0" in text and "hello" in text
    assert (workspace / "note.txt").read_text() == "hello\n"
    exec_call = [c for c in docker.calls() if c[0] == "exec"][-1]
    assert exec_call[:3] == ["exec", "-u", "501:20"] and spec.name in exec_call
    # The permit was spent: the same call again is refused.
    assert call(launch, "exec", arguments) == (NOT_APPROVED, True)


def test_a_failing_command_reports_its_exit_code_and_stderr(machine):
    _, _, launch, _ = machine
    arguments = {"command": "echo oops >&2; exit 3"}
    grants.issue(grants_dir(launch), "exec", arguments)
    text, is_error = call(launch, "exec", arguments)
    assert is_error and "exit code: 3" in text and "oops" in text


def test_files_are_written_read_and_listed_in_the_container(machine, tmp_path):
    _, _, launch, workspace = machine
    write = {"path": "src/app.py", "content": "print('hi')\n"}
    grants.issue(grants_dir(launch), "write_file", write)
    assert call(launch, "write_file", write) == ("Wrote 12 bytes to src/app.py.", False)
    assert (workspace / "src" / "app.py").read_text() == "print('hi')\n"
    grants.issue(grants_dir(launch), "read_file", {"path": "src/app.py"})
    assert call(launch, "read_file", {"path": "src/app.py"}) == ("print('hi')\n", False)
    grants.issue(grants_dir(launch), "list_files", {"path": "src"})
    text, is_error = call(launch, "list_files", {"path": "src"})
    assert not is_error and "app.py" in text
    last = json.loads((tmp_path / "state" / LAST_COMMAND_FILE).read_text())
    assert last["tool"] == "list_files" and last["subject"] == "src" and last["exit_code"] == 0


def test_bad_arguments_are_refused_before_any_permit_is_spent(machine):
    _, _, launch, _ = machine
    grants.issue(grants_dir(launch), "exec", {"command": "ls", "extra": 1})
    text, is_error = call(launch, "exec", {"command": "ls", "extra": 1})
    assert is_error and "Unknown argument" in text
    assert call(launch, "exec", {"command": 5}) [1]
    assert call(launch, "nope", {})[0] == "Unknown computer tool."


def test_a_stopped_computer_says_so(machine):
    provider, spec, launch, _ = machine
    provider.stop(spec)
    grants.issue(grants_dir(launch), "exec", {"command": "true"})
    text, is_error = call(launch, "exec", {"command": "true"})
    assert is_error and "not running" in text


def test_an_invalid_connection_exits_without_serving(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "app.computers.mcp_server", "--container", "--bad", "--user", "1:1",
         "--grants", str(tmp_path), "--state", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2
