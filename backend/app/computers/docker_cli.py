"""The Docker command lines an agent computer is made of.

Standard library only, so the MCP server process can use it without the rest
of the app. Every value that reaches a command line is an argument, never
shell text, and is checked before it gets there.
"""

import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Small, pinned by digest: the Debian 12 "slim" Python 3.12 image (bash,
# coreutils, python3, pip; about 45 MB compressed). A tag can be moved to other
# content; a digest cannot. docs/COMPUTERS.md says how to choose another image.
DEFAULT_IMAGE = (
    "docker.io/library/python:3.12-slim-bookworm"
    "@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258"
)

GUEST_WORKSPACE = "/workspace"
GUEST_HOME = "/tmp/home"

# Every container this app creates carries these labels. Ownership is decided
# by them, never by a name alone: a container someone else made under the same
# name is refused, not reused or removed.
LABEL_MANAGED = "dev.open-dots.computer"
LABEL_ID = "dev.open-dots.computer.id"
LABEL_INSTANCE = "dev.open-dots.instance"
LABEL_CONFIG = "dev.open-dots.config"

NETWORK_MODES = ("none", "default")

# Commands: default and maximum time, and how much output is kept.
EXEC_DEFAULT_SECONDS = 120
EXEC_MAX_SECONDS = 1800
EXEC_OUTPUT_LIMIT = 30_000
MAX_COMMAND_CHARS = 20_000
MAX_FILE_BYTES = 2_000_000

_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_INSTANCE = re.compile(r"^[a-f0-9]{8,64}$")
_USER = re.compile(r"^\d{1,10}:\d{1,10}$")


class InvalidSpec(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    """Everything a computer's container is created from."""

    computer_id: int
    instance: str
    image: str
    workspace: str
    cpus: float = 1.0
    memory_mb: int = 1024
    pids_limit: int = 256
    network: str = "none"
    # uid:gid the agent's commands run as; None: this process's own ids.
    user: str | None = None

    @property
    def name(self) -> str:
        return container_name(self.instance, self.computer_id)

    @property
    def run_as(self) -> str:
        return self.user or default_user()


def default_user() -> str:
    """The host user's ids, so files the agent writes in the mounted folder are
    the person's own. Never root."""
    if hasattr(os, "getuid"):
        uid, gid = os.getuid(), os.getgid()
        if uid != 0:
            return f"{uid}:{gid}"
    return "1000:1000"


def container_name(instance: str, computer_id: int) -> str:
    if not _INSTANCE.match(instance) or not isinstance(computer_id, int) or computer_id <= 0:
        raise InvalidSpec("invalid computer identity")
    return f"open-dots-computer-{instance[:8]}-{computer_id}"


def check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.match(name):
        raise InvalidSpec("invalid container name")
    return name


def check_image(image: str) -> str:
    image = (image or "").strip()
    if not image or len(image) > 300 or image.startswith("-") or any(c.isspace() for c in image):
        raise InvalidSpec("invalid image reference")
    return image


def image_is_pinned(image: str) -> bool:
    return "@sha256:" in image or bool(re.match(r"^sha256:[a-f0-9]{64}$", image))


def fingerprint(spec: Spec) -> str:
    """What cannot change on a running container. A container whose label
    differs must be recreated for a change to apply."""
    text = "\n".join([spec.image, spec.workspace, spec.network, spec.run_as])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def validate(spec: Spec) -> Spec:
    check_image(spec.image)
    container_name(spec.instance, spec.computer_id)
    if not Path(spec.workspace).is_absolute():
        raise InvalidSpec("the workspace must be an absolute path")
    if "," in spec.workspace or "\n" in spec.workspace:
        # --mount splits on commas; such a folder cannot be described safely.
        raise InvalidSpec("the workspace path cannot contain a comma or a newline")
    if not (0.1 <= spec.cpus <= 64):
        raise InvalidSpec("cpus must be between 0.1 and 64")
    if not (128 <= spec.memory_mb <= 262_144):
        raise InvalidSpec("memory must be between 128 MB and 256 GB")
    if not (32 <= spec.pids_limit <= 65_536):
        raise InvalidSpec("the process limit must be between 32 and 65536")
    if spec.network not in NETWORK_MODES:
        raise InvalidSpec(f"network must be one of: {', '.join(NETWORK_MODES)}")
    if not _USER.match(spec.run_as) or spec.run_as.split(":")[0] == "0":
        raise InvalidSpec("the computer user must be a non-root uid:gid")
    return spec


def run_args(spec: Spec) -> list[str]:
    """`docker run` for a new computer: no capabilities, no new privileges, no
    published ports, bounded CPU, memory and processes, one bind mount (the
    agent's workspace), and a non-root user. It sleeps until told otherwise;
    every command is a `docker exec`."""
    validate(spec)
    return [
        "run", "-d",
        "--name", spec.name,
        "--hostname", "computer",
        "--label", f"{LABEL_MANAGED}=1",
        "--label", f"{LABEL_ID}={spec.computer_id}",
        "--label", f"{LABEL_INSTANCE}={spec.instance}",
        "--label", f"{LABEL_CONFIG}={fingerprint(spec)}",
        "--user", spec.run_as,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--cpus", f"{spec.cpus:g}",
        "--memory", f"{spec.memory_mb}m",
        "--memory-swap", f"{spec.memory_mb}m",
        "--pids-limit", str(spec.pids_limit),
        "--ipc", "private",
        "--init",
        "--restart", "no",
        "--network", "none" if spec.network == "none" else "bridge",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
        "-e", f"HOME={GUEST_HOME}",
        "--mount", f"type=bind,source={spec.workspace},target={GUEST_WORKSPACE}",
        "-w", GUEST_WORKSPACE,
        spec.image,
        "sleep", "infinity",
    ]


def update_args(name: str, cpus: float, memory_mb: int, pids_limit: int) -> list[str]:
    """Limits Docker can change on a live container."""
    return [
        "update",
        "--cpus", f"{cpus:g}",
        "--memory", f"{memory_mb}m",
        "--memory-swap", f"{memory_mb}m",
        "--pids-limit", str(pids_limit),
        check_name(name),
    ]


# Runs the command with bash when the image has it, sh otherwise. The command
# is a positional parameter, never spliced into this text.
_SHELL = 'mkdir -p "$HOME" 2>/dev/null; if command -v bash >/dev/null 2>&1; then exec bash -c "$1"; else exec sh -c "$1"; fi'


def exec_seconds(timeout_seconds: object) -> int:
    if timeout_seconds is None:
        return EXEC_DEFAULT_SECONDS
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise InvalidSpec("timeout_seconds must be a number")
    if timeout_seconds != timeout_seconds or timeout_seconds in (float("inf"), float("-inf")):
        raise InvalidSpec("timeout_seconds must be finite")
    return int(min(max(int(timeout_seconds), 1), EXEC_MAX_SECONDS))


def exec_args(name: str, command: str, seconds: int, user: str, *, cwd: str | None = None) -> list[str]:
    """`docker exec` of one shell command. The time limit is enforced inside
    the container (`timeout`), so a runaway process really stops."""
    if not isinstance(command, str) or not command.strip():
        raise InvalidSpec("command is required")
    if len(command) > MAX_COMMAND_CHARS:
        raise InvalidSpec("command is too long")
    return [
        "exec", "-u", user, "-w", cwd or GUEST_WORKSPACE, "-e", f"HOME={GUEST_HOME}",
        check_name(name),
        "timeout", "-k", "5", str(seconds),
        "sh", "-c", _SHELL, "sh", command,
    ]


def check_guest_path(path: object) -> str:
    if not isinstance(path, str) or not path.strip():
        raise InvalidSpec("path is required")
    if "\x00" in path or len(path) > 4096:
        raise InvalidSpec("invalid path")
    return path


def read_args(name: str, path: str, user: str) -> list[str]:
    return [
        "exec", "-u", user, "-w", GUEST_WORKSPACE, check_name(name),
        "head", "-c", str(MAX_FILE_BYTES + 1), "--", check_guest_path(path),
    ]


def write_args(name: str, path: str, user: str) -> list[str]:
    script = 'd=$(dirname -- "$1") && mkdir -p -- "$d" && cat > "$1"'
    return [
        "exec", "-i", "-u", user, "-w", GUEST_WORKSPACE, check_name(name),
        "sh", "-c", script, "sh", check_guest_path(path),
    ]


def list_args(name: str, path: str, user: str) -> list[str]:
    return [
        "exec", "-u", user, "-w", GUEST_WORKSPACE, check_name(name),
        "ls", "-lA", "--", check_guest_path(path),
    ]


def clip_output(text: str, limit: int = EXEC_OUTPUT_LIMIT) -> str:
    """The start and the end of a long output, where errors usually are."""
    if len(text) <= limit:
        return text
    head = limit // 5
    omitted = len(text) - limit
    return f"{text[:head]}\n… {omitted} characters omitted …\n{text[len(text) - (limit - head):]}"


# -- running the docker CLI --------------------------------------------------------

# Where Docker installs its CLI when the app was started from a desktop
# launcher, which does not inherit a terminal's PATH.
_EXTRA_PATHS = (
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/Applications/Docker.app/Contents/Resources/bin",
    str(Path.home() / ".docker" / "bin"),
)


def docker_executable() -> str | None:
    configured = os.environ.get("OPEN_DOTS_DOCKER", "").strip()
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which("docker")
    if found:
        return found
    for folder in _EXTRA_PATHS:
        candidate = Path(folder) / ("docker.exe" if sys.platform == "win32" else "docker")
        if candidate.is_file():
            return str(candidate)
    return None


@dataclass
class Completed:
    code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    raw: bytes = b""


class DockerMissing(RuntimeError):
    pass


def _kill(proc: subprocess.Popen) -> None:
    try:
        if sys.platform != "win32":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run(args: list[str], *, timeout: float, input_bytes: bytes | None = None,
        docker: str | None = None, max_bytes: int = 16_000_000) -> Completed:
    """One docker CLI call. Never raises for a failing command (the exit code
    says it); raises DockerMissing when there is no docker CLI. On a timeout
    the CLI process is killed and `timed_out` is set."""
    executable = docker or docker_executable()
    if not executable:
        raise DockerMissing("Docker is not installed (no docker command was found).")
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), *_EXTRA_PATHS])
    try:
        proc = subprocess.Popen(
            [executable, *args],
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            **kwargs,
        )
    except OSError as exc:
        raise DockerMissing(f"The docker command could not be started: {exc}") from None
    try:
        out, err = proc.communicate(input=input_bytes, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill(proc)
        out, err = proc.communicate()
        timed_out = True
    return Completed(
        code=None if timed_out else proc.returncode,
        stdout=out[:max_bytes].decode("utf-8", errors="replace"),
        stderr=err[:max_bytes].decode("utf-8", errors="replace"),
        timed_out=timed_out,
        raw=out[:max_bytes],
    )
