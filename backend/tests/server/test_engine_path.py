"""Engines installed in the service account's home are found under a minimal PATH
(app/server/engine_path.py)."""

import os
import stat
import sys
from pathlib import Path

import pytest

from app.core.config import settings
from app.server.engine_path import EXTRA_SETTING, augment_path, candidate_dirs


@pytest.fixture
def home(tmp_path) -> Path:
    folder = tmp_path / "home"
    (folder / ".local" / "bin").mkdir(parents=True)
    (folder / ".npm-global" / "bin").mkdir(parents=True)
    return folder


def test_the_account_s_own_folders_are_added_to_the_end_in_order(home):
    env = {"PATH": os.pathsep.join(["/usr/bin", "/bin"])}
    added = augment_path(env, home)
    assert added[:2] == [str(home / ".local" / "bin"), str(home / ".npm-global" / "bin")]
    assert env["PATH"].split(os.pathsep)[:2] == ["/usr/bin", "/bin"]  # nothing already there moved or was overridden
    assert env["PATH"].split(os.pathsep)[2:4] == added[:2]


def test_a_folder_already_on_the_path_is_not_added_again(home):
    local = str(home / ".local" / "bin")
    env = {"PATH": os.pathsep.join(["/usr/bin", local])}
    added = augment_path(env, home)
    assert local not in added
    assert env["PATH"].split(os.pathsep).count(local) == 1


def test_folders_that_do_not_exist_are_skipped(tmp_path):
    bare = tmp_path / "bare-home"
    bare.mkdir()
    env = {"PATH": "/usr/bin"}
    assert augment_path(env, bare) == [d for d in ("/usr/local/bin",) if os.path.isdir(d)]
    if not os.path.isdir("/usr/local/bin"):
        assert env["PATH"] == "/usr/bin"  # untouched when there is nothing to add


def test_nothing_to_add_leaves_the_environment_alone(tmp_path):
    bare = tmp_path / "bare-home"
    bare.mkdir()
    env = {"PATH": os.pathsep.join(["/usr/bin", "/usr/local/bin"])}
    before = dict(env)
    assert augment_path(env, bare) == []
    assert env == before


def test_extra_folders_can_be_named_and_a_missing_one_is_skipped(home, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    env = {"PATH": "/usr/bin", EXTRA_SETTING: os.pathsep.join([str(tools), str(tmp_path / "nowhere")])}
    added = augment_path(env, home)
    assert str(tools) in added and str(tmp_path / "nowhere") not in added
    assert env["PATH"].endswith(str(tools))


def test_a_missing_path_becomes_the_added_folders(home):
    env: dict[str, str] = {}
    added = augment_path(env, home)
    assert env["PATH"] == os.pathsep.join(added) and added


def test_candidates_name_the_usual_places(home):
    names = candidate_dirs(home)
    assert str(home / ".local" / "bin") in names and str(home / ".npm-global" / "bin") in names


def _install_fake(folder: Path, name: str) -> Path:
    program = folder / (f"{name}.exe" if sys.platform == "win32" else name)
    program.write_text("#!/bin/sh\nexit 0\n")
    program.chmod(program.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return program


def test_an_engine_in_the_account_s_home_is_found_only_after_the_path_is_extended(home, monkeypatch):
    from app.services import local_claude_runner

    fake = _install_fake(home / ".local" / "bin", "claude")
    monkeypatch.setattr(settings, "LOCAL_CLAUDE_CLI_PATH", "")
    monkeypatch.setenv("PATH", os.pathsep.join(p for p in [os.path.dirname(sys.executable)] if p))
    monkeypatch.delenv(EXTRA_SETTING, raising=False)
    assert local_claude_runner.resolve_claude_executable() is None  # a service's minimal PATH

    augment_path(os.environ, home)
    found = local_claude_runner.resolve_claude_executable()
    assert found is not None and found.resolve() == fake.resolve()
