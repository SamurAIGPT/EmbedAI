"""A fake `docker` executable for the computer tests (fake_docker.py), and
helpers to read and change what it holds."""

import json
import stat
import sys
from pathlib import Path

import pytest

from app.computers import docker_cli

FAKE_DOCKER = Path(__file__).with_name("fake_docker.py")
IMAGE = docker_cli.DEFAULT_IMAGE
INSTANCE = "0123456789abcdef0123456789abcdef"


class FakeDocker:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.state_dir = folder / "state"
        self.state_dir.mkdir(parents=True)
        self.path = folder / "docker"
        # The state folder is written into the wrapper, so it reaches every
        # child (the MCP server the Claude CLI starts) whatever its environment.
        self.path.write_text(
            f"#!/bin/sh\nFAKE_DOCKER_STATE='{self.state_dir}' exec '{sys.executable}' '{FAKE_DOCKER}' \"$@\"\n"
        )
        self.path.chmod(self.path.stat().st_mode | stat.S_IXUSR)

    def state(self) -> dict:
        path = self.state_dir / "state.json"
        return json.loads(path.read_text()) if path.exists() else {"images": {}, "containers": {}}

    def save(self, state: dict) -> None:
        (self.state_dir / "state.json").write_text(json.dumps(state))

    def add_image(self, image: str = IMAGE) -> None:
        state = self.state()
        state["images"][image] = "sha256:" + "ab" * 32
        self.save(state)

    def mode(self, value: str) -> None:
        (self.state_dir / "mode").write_text(value)

    def calls(self) -> list[list[str]]:
        path = self.state_dir / "log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line)["args"] for line in path.read_text().splitlines()]

    def commands(self) -> list[str]:
        return [args[0] if args else "" for args in self.calls()]

    def container(self, name: str) -> dict | None:
        return self.state()["containers"].get(name)

    def edit_container(self, name: str, change) -> None:
        state = self.state()
        change(state["containers"][name]["inspect"])
        self.save(state)


@pytest.fixture
def docker(tmp_path):
    if sys.platform == "win32":
        pytest.skip("the fake docker is a POSIX shell script")
    return FakeDocker(tmp_path / "fake-docker")
