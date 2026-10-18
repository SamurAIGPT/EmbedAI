"""The ComputerProvider interface, and the Docker provider.

A provider knows how to make, wake, sleep and remove one isolated machine for
an agent, how to report what it finds, and how the agent's tools reach it (an
MCP server launch). Docker is the only provider today; a remote server over
SSH (`docker -H ssh://alias`) and a desktop computer with a screen fit the
same interface (docs/COMPUTERS.md).

Every method is synchronous and may block on the docker CLI: callers run them
in a worker thread.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.computers import docker_cli
from app.computers.docker_cli import Spec


class ComputerError(Exception):
    """A lifecycle step that cannot be done, with a code a client can act on."""

    def __init__(self, message: str, code: str = "computer_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Health:
    provider: str
    available: bool
    version: str | None = None
    error: str | None = None


@dataclass
class Status:
    """What the provider found for one computer, in check order."""

    # missing | running | stopped | foreign | unsafe | unavailable
    state: str
    container_id: str | None = None
    image: str | None = None
    image_id: str | None = None
    # The container was made from different settings (image, folder, network
    # or user): recreate it for them to apply.
    needs_recreate: bool = False
    problem: str | None = None
    started_at: str | None = None


@dataclass
class McpLaunch:
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)


class ComputerProvider(Protocol):
    key: str

    def health(self) -> Health: ...

    def status(self, spec: Spec) -> Status: ...

    def image_present(self, image: str) -> bool: ...

    def pull(self, image: str) -> None: ...

    def create(self, spec: Spec) -> Status:
        """Make the computer if it does not exist and start it. Idempotent: an
        existing computer of this app with this identity is kept as it is."""

    def start(self, spec: Spec) -> Status: ...

    def stop(self, spec: Spec) -> Status: ...

    def delete(self, spec: Spec) -> None:
        """Remove the machine. Never touches the agent's workspace folder."""

    def update_limits(self, spec: Spec) -> None: ...

    def list_managed(self, instance: str) -> list[tuple[str, int | None]]:
        """(name, computer id) of every machine this installation made."""

    def remove_by_name(self, name: str, instance: str) -> None: ...

    def mcp_launch(self, spec: Spec, grants_dir: Path, state_dir: Path) -> McpLaunch: ...


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def mcp_launch_for(container: str, user: str, grants_dir: Path, state_dir: Path, docker: str | None) -> McpLaunch:
    tail = ["--container", container, "--user", user, "--grants", str(grants_dir), "--state", str(state_dir)]
    if docker:
        tail += ["--docker", docker]
    if getattr(sys, "frozen", False):
        # The packaged desktop backend is one executable: it starts the server
        # when its first argument says so (desktop_main.py).
        return McpLaunch(sys.executable, ["--computer-mcp", *tail])
    return McpLaunch(
        sys.executable,
        ["-m", "app.computers.mcp_server", *tail],
        {"PYTHONPATH": str(_backend_root()), "PYTHONDONTWRITEBYTECODE": "1"},
    )


class DockerProvider:
    key = "docker"

    INFO_TIMEOUT = 8.0
    LIFECYCLE_TIMEOUT = 120.0
    PULL_TIMEOUT = 900.0

    def __init__(self, docker: str | None = None) -> None:
        self._docker = docker

    def _run(self, args: list[str], timeout: float = LIFECYCLE_TIMEOUT, **kw) -> docker_cli.Completed:
        try:
            return docker_cli.run(args, timeout=timeout, docker=self._docker, **kw)
        except docker_cli.DockerMissing as exc:
            raise ComputerError(str(exc), "docker_missing") from None

    def _must(self, args: list[str], what: str, timeout: float = LIFECYCLE_TIMEOUT, **kw) -> docker_cli.Completed:
        done = self._run(args, timeout, **kw)
        if done.timed_out:
            raise ComputerError(f"Docker did not answer in time while trying to {what}.", "docker_timeout")
        if done.code != 0:
            detail = done.stderr.strip()[-500:] or f"exit code {done.code}"
            raise ComputerError(f"Docker could not {what}: {detail}", "docker_failed")
        return done

    # -- health ------------------------------------------------------------------------

    def health(self) -> Health:
        try:
            done = docker_cli.run(
                ["info", "--format", "{{json .ServerVersion}}"], timeout=self.INFO_TIMEOUT, docker=self._docker
            )
        except docker_cli.DockerMissing as exc:
            return Health("docker", False, error=str(exc))
        if done.timed_out:
            return Health("docker", False, error="Docker is not responding. Start Docker (or restart it) and try again.")
        if done.code != 0:
            detail = done.stderr.strip().splitlines()[-1:] or ["unknown error"]
            return Health("docker", False, error=f"Docker is not running: {detail[0][:300]}")
        try:
            version = json.loads(done.stdout.strip() or "null")
        except json.JSONDecodeError:
            version = None
        if not version:
            return Health("docker", False, error="Docker answered without a server version.")
        return Health("docker", True, version=str(version))

    # -- inspection --------------------------------------------------------------------

    def image_present(self, image: str) -> bool:
        done = self._run(["image", "inspect", "--format", "{{.Id}}", docker_cli.check_image(image)], 30)
        return done.code == 0 and not done.timed_out

    def image_id(self, image: str) -> str | None:
        done = self._run(["image", "inspect", "--format", "{{.Id}}", docker_cli.check_image(image)], 30)
        return (done.stdout.strip() or None) if done.code == 0 else None

    def _inspect(self, name: str) -> dict | None:
        done = self._run(["container", "inspect", docker_cli.check_name(name)], 30)
        if done.timed_out:
            raise ComputerError("Docker did not answer in time.", "docker_timeout")
        if done.code != 0:
            if "no such" in done.stderr.lower():
                return None
            raise ComputerError(f"Docker could not inspect the computer: {done.stderr.strip()[-300:]}", "docker_failed")
        try:
            parsed = json.loads(done.stdout)
        except json.JSONDecodeError:
            raise ComputerError("Docker returned unreadable container details.", "docker_failed") from None
        return parsed[0] if isinstance(parsed, list) and parsed else None

    def status(self, spec: Spec) -> Status:
        detail = self._inspect(spec.name)
        if detail is None:
            return Status("missing")
        return _status_of(detail, spec)

    # -- lifecycle ---------------------------------------------------------------------

    def pull(self, image: str) -> None:
        self._must(["pull", docker_cli.check_image(image)], "download the image", self.PULL_TIMEOUT)

    def create(self, spec: Spec) -> Status:
        docker_cli.validate(spec)
        current = self.status(spec)
        if current.state in ("foreign", "unsafe"):
            raise ComputerError(current.problem or "The computer cannot be used.", "computer_" + current.state)
        if current.state == "stopped":
            return self.start(spec)
        if current.state == "running":
            return current
        if not self.image_present(spec.image):
            raise ComputerError(
                f"The image {spec.image} is not on this machine yet. Download it first (Prepare image), "
                "or choose an image that is already here.",
                "image_missing",
            )
        if not Path(spec.workspace).is_dir():
            raise ComputerError("The agent's workspace folder does not exist.", "workspace_missing")
        done = self._run(docker_cli.run_args(spec))
        if done.code != 0 or done.timed_out:
            # A second create racing this one made it first: that one counts.
            again = self.status(spec)
            if again.state in ("running", "stopped"):
                return again if again.state == "running" else self.start(spec)
            detail = "Docker did not answer in time" if done.timed_out else done.stderr.strip()[-500:]
            raise ComputerError(f"Docker could not create the computer: {detail}", "docker_failed")
        return self.status(spec)

    def start(self, spec: Spec) -> Status:
        current = self.status(spec)
        if current.state == "missing":
            raise ComputerError("The computer does not exist yet; create it first.", "computer_missing")
        if current.state in ("foreign", "unsafe"):
            raise ComputerError(current.problem or "The computer cannot be used.", "computer_" + current.state)
        if current.state == "stopped":
            self._must(["start", spec.name], "start the computer")
            current = self.status(spec)
        return current

    def stop(self, spec: Spec) -> Status:
        current = self.status(spec)
        if current.state == "running":
            self._must(["stop", "-t", "10", spec.name], "stop the computer")
            current = self.status(spec)
        return current

    def delete(self, spec: Spec) -> None:
        current = self.status(spec)
        if current.state == "missing":
            return
        if current.state == "foreign":
            raise ComputerError(current.problem or "That container was not made by open-dots.", "computer_foreign")
        self._must(["rm", "-f", spec.name], "remove the computer")

    def update_limits(self, spec: Spec) -> None:
        if self.status(spec).state in ("running", "stopped"):
            self._must(docker_cli.update_args(spec.name, spec.cpus, spec.memory_mb, spec.pids_limit), "change the limits")

    def list_managed(self, instance: str) -> list[tuple[str, int | None]]:
        done = self._must(
            [
                "ps", "-a",
                "--filter", f"label={docker_cli.LABEL_MANAGED}=1",
                "--filter", f"label={docker_cli.LABEL_INSTANCE}={instance}",
                "--format", "{{.Names}}\t{{.Label \"" + docker_cli.LABEL_ID + "\"}}",
            ],
            "list computers",
            30,
        )
        found: list[tuple[str, int | None]] = []
        for line in done.stdout.splitlines():
            name, _, raw_id = line.partition("\t")
            if not name.strip():
                continue
            found.append((name.strip(), int(raw_id) if raw_id.strip().isdigit() else None))
        return found

    def remove_by_name(self, name: str, instance: str) -> None:
        detail = self._inspect(name)
        if detail is None:
            return
        labels = (detail.get("Config") or {}).get("Labels") or {}
        if labels.get(docker_cli.LABEL_MANAGED) != "1" or labels.get(docker_cli.LABEL_INSTANCE) != instance:
            raise ComputerError("That container was not made by this installation.", "computer_foreign")
        self._must(["rm", "-f", name], "remove the computer")

    def mcp_launch(self, spec: Spec, grants_dir: Path, state_dir: Path) -> McpLaunch:
        return mcp_launch_for(spec.name, spec.run_as, grants_dir, state_dir, self._docker or docker_cli.docker_executable())


def _status_of(detail: dict, spec: Spec) -> Status:
    config = detail.get("Config") or {}
    host = detail.get("HostConfig") or {}
    labels = config.get("Labels") or {}
    state = detail.get("State") or {}
    container_id = (detail.get("Id") or "")[:12] or None
    common = {
        "container_id": container_id,
        "image": config.get("Image"),
        "image_id": detail.get("Image"),
        "started_at": state.get("StartedAt") if state.get("Running") else None,
    }
    if (
        labels.get(docker_cli.LABEL_MANAGED) != "1"
        or labels.get(docker_cli.LABEL_ID) != str(spec.computer_id)
        or labels.get(docker_cli.LABEL_INSTANCE) != spec.instance
    ):
        return Status(
            "foreign",
            problem=f"A container named {spec.name} exists but was not made by open-dots for this agent. "
            "Remove or rename it in Docker.",
            **common,
        )
    problem = _hardening_problem(config, host, detail.get("Mounts") or [], spec)
    if problem:
        return Status("unsafe", problem=problem + " Recreate the computer.", needs_recreate=True, **common)
    return Status(
        "running" if state.get("Running") else "stopped",
        needs_recreate=labels.get(docker_cli.LABEL_CONFIG) != docker_cli.fingerprint(spec),
        **common,
    )


def _hardening_problem(config: dict, host: dict, mounts: list, spec: Spec) -> str | None:
    """Checked before every use, not only at creation: a container changed by
    hand afterwards is refused rather than trusted."""
    caps = [c.lower() for c in (host.get("CapDrop") or [])]
    if "all" not in caps and "cap_all" not in caps:
        return "The computer keeps Linux capabilities."
    if host.get("CapAdd"):
        return "The computer was given extra Linux capabilities."
    if host.get("Privileged"):
        return "The computer is privileged."
    if host.get("PublishAllPorts") or any(v for v in (host.get("PortBindings") or {}).values()):
        return "The computer publishes network ports."
    if host.get("PidMode") or (host.get("NetworkMode") or "") == "host":
        return "The computer shares the host's processes or network."
    user = (config.get("User") or "").split(":")[0]
    if user in ("", "0", "root"):
        return "The computer runs as root."
    binds = [m for m in mounts if m.get("Type") != "tmpfs"]
    if len(binds) > 1:
        return "The computer has extra folders mounted."
    for mount in binds:
        if mount.get("Destination") != docker_cli.GUEST_WORKSPACE:
            return "The computer has a folder mounted somewhere unexpected."
    return None


def default_instance_file(state_root: Path) -> Path:
    return state_root / "instance-id"


def load_instance_id(state_root: Path) -> str:
    """A random id for this installation, so two installations that share one
    Docker never touch each other's computers."""
    path = default_instance_file(state_root)
    try:
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 16 and all(c in "0123456789abcdef" for c in value):
            return value
    except OSError:
        pass
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    value = os.urandom(16).hex()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(value, encoding="utf-8")
    os.replace(tmp, path)
    return value
