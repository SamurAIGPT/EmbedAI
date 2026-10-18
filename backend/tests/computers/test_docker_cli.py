"""The Docker command lines a computer is made of, and the one-use permits."""

import time

import pytest

from app.computers import docker_cli, grants
from app.computers.docker_cli import InvalidSpec, Spec
from tests.computers.harness import INSTANCE


def spec(tmp_path, **more) -> Spec:
    fields = {"computer_id": 7, "instance": INSTANCE, "image": docker_cli.DEFAULT_IMAGE,
              "workspace": str(tmp_path), "user": "501:20", **more}
    return Spec(**fields)


def flag_values(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def test_the_default_image_is_pinned_by_digest():
    assert docker_cli.image_is_pinned(docker_cli.DEFAULT_IMAGE)
    assert not docker_cli.image_is_pinned("python:3.12-slim")


def test_a_new_computer_is_hardened_limited_and_mounts_only_the_workspace(tmp_path):
    args = docker_cli.run_args(spec(tmp_path, cpus=1.5, memory_mb=2048, pids_limit=300))
    assert args[:2] == ["run", "-d"]
    assert flag_values(args, "--cap-drop") == ["ALL"]
    assert flag_values(args, "--security-opt") == ["no-new-privileges"]
    assert flag_values(args, "--user") == ["501:20"]
    assert flag_values(args, "--cpus") == ["1.5"]
    assert flag_values(args, "--memory") == ["2048m"]
    assert flag_values(args, "--memory-swap") == ["2048m"]
    assert flag_values(args, "--pids-limit") == ["300"]
    assert flag_values(args, "--network") == ["none"]
    assert flag_values(args, "--mount") == [f"type=bind,source={tmp_path},target=/workspace"]
    # Nothing is published, nothing privileged, no host namespaces.
    for forbidden in ("-p", "--publish", "-P", "--privileged", "--pid", "--cap-add", "-v", "--volume"):
        assert forbidden not in args
    labels = dict(v.split("=", 1) for v in flag_values(args, "--label"))
    assert labels[docker_cli.LABEL_MANAGED] == "1"
    assert labels[docker_cli.LABEL_ID] == "7"
    assert labels[docker_cli.LABEL_INSTANCE] == INSTANCE
    assert args[-3:] == [docker_cli.DEFAULT_IMAGE, "sleep", "infinity"]


def test_the_default_network_mode_is_dockers_bridge(tmp_path):
    assert flag_values(docker_cli.run_args(spec(tmp_path, network="default")), "--network") == ["bridge"]


@pytest.mark.parametrize(
    "change",
    [
        {"network": "host"},
        {"user": "0:0"},
        {"user": "root"},
        {"cpus": 0},
        {"memory_mb": 16},
        {"pids_limit": 1},
        {"image": "--privileged"},
        {"image": "a b"},
    ],
)
def test_unsafe_or_impossible_settings_are_refused(tmp_path, change):
    with pytest.raises(InvalidSpec):
        docker_cli.run_args(spec(tmp_path, **change))


def test_a_folder_that_cannot_be_described_to_mount_is_refused(tmp_path):
    with pytest.raises(InvalidSpec):
        docker_cli.run_args(Spec(7, INSTANCE, docker_cli.DEFAULT_IMAGE, str(tmp_path / "a,b"), user="501:20"))
    with pytest.raises(InvalidSpec):
        docker_cli.run_args(Spec(7, INSTANCE, docker_cli.DEFAULT_IMAGE, "relative/path", user="501:20"))


def test_settings_that_need_a_new_container_change_the_fingerprint(tmp_path):
    base = docker_cli.fingerprint(spec(tmp_path))
    assert docker_cli.fingerprint(spec(tmp_path, cpus=4)) == base  # docker update can change limits
    assert docker_cli.fingerprint(spec(tmp_path, network="default")) != base
    assert docker_cli.fingerprint(Spec(7, INSTANCE, "busybox@sha256:" + "0" * 64, str(tmp_path), user="501:20")) != base


def test_a_command_runs_as_the_user_in_the_workspace_with_the_limit_inside(tmp_path):
    args = docker_cli.exec_args("open-dots-computer-01234567-7", "echo hi; rm -rf x", 30, "501:20")
    assert args[:7] == ["exec", "-u", "501:20", "-w", "/workspace", "-e", "HOME=/tmp/home"]
    assert args[7] == "open-dots-computer-01234567-7"
    assert args[8:12] == ["timeout", "-k", "5", "30"]
    # The command is a positional argument, never spliced into the script.
    assert args[-1] == "echo hi; rm -rf x"
    assert "echo hi" not in args[-3]


def test_time_limits_are_bounded_and_bad_values_refused():
    assert docker_cli.exec_seconds(None) == docker_cli.EXEC_DEFAULT_SECONDS
    assert docker_cli.exec_seconds(10**9) == docker_cli.EXEC_MAX_SECONDS
    assert docker_cli.exec_seconds(-4) == 1
    for bad in (float("nan"), float("inf"), "10", True):
        with pytest.raises(InvalidSpec):
            docker_cli.exec_seconds(bad)
    with pytest.raises(InvalidSpec):
        docker_cli.exec_args("open-dots-computer-01234567-7", "   ", 5, "501:20")
    with pytest.raises(InvalidSpec):
        docker_cli.exec_args("open-dots-computer-01234567-7", "x" * (docker_cli.MAX_COMMAND_CHARS + 1), 5, "501:20")
    with pytest.raises(InvalidSpec):
        docker_cli.exec_args("--rm", "true", 5, "501:20")


def test_long_output_keeps_its_start_and_end():
    text = "START" + "x" * 100_000 + "END"
    clipped = docker_cli.clip_output(text)
    assert clipped.startswith("START") and clipped.endswith("END")
    assert "characters omitted" in clipped
    assert len(clipped) < docker_cli.EXEC_OUTPUT_LIMIT + 100
    assert docker_cli.clip_output("short") == "short"


def test_a_missing_docker_cli_is_reported(monkeypatch):
    monkeypatch.setenv("OPEN_DOTS_DOCKER", "/nonexistent/docker")
    with pytest.raises(docker_cli.DockerMissing):
        docker_cli.run(["info"], timeout=1)


# -- permits --------------------------------------------------------------------------


def test_a_permit_is_spent_once_and_only_on_the_exact_call(tmp_path):
    folder = tmp_path / "grants"
    grants.issue(folder, "exec", {"command": "ls", "timeout_seconds": 5})
    assert not grants.consume(folder, "exec", {"command": "ls -la", "timeout_seconds": 5})
    assert not grants.consume(folder, "write_file", {"command": "ls", "timeout_seconds": 5})
    # Key order does not matter; the content does.
    assert grants.consume(folder, "exec", {"timeout_seconds": 5, "command": "ls"})
    assert not grants.consume(folder, "exec", {"command": "ls", "timeout_seconds": 5})


def test_two_approvals_of_the_same_call_allow_it_twice(tmp_path):
    folder = tmp_path / "grants"
    grants.issue(folder, "exec", {"command": "make"})
    grants.issue(folder, "exec", {"command": "make"})
    assert grants.consume(folder, "exec", {"command": "make"})
    assert grants.consume(folder, "exec", {"command": "make"})
    assert not grants.consume(folder, "exec", {"command": "make"})


def test_an_old_permit_is_not_honoured(tmp_path):
    folder = tmp_path / "grants"
    grants.issue(folder, "exec", {"command": "ls"})
    assert not grants.consume(folder, "exec", {"command": "ls"}, now=time.time() + grants.TTL_SECONDS + 5)
    assert not grants.consume(folder, "exec", {"command": "ls"})  # and it was removed


def test_the_permit_folder_is_private(tmp_path):
    folder = tmp_path / "grants"
    path = grants.issue(folder, "exec", {"command": "ls"})
    assert folder.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert not grants.consume(tmp_path / "missing", "exec", {"command": "ls"})
