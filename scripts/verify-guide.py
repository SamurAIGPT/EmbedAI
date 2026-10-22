#!/usr/bin/env python3
"""Run the commands in docs/SELF-HOSTING.md, from the guide's own text.

Every fenced `sh` block in the guide that is preceded by a marker comment is run:

    <!-- verify:root -->                 as root, in a clean Ubuntu container (stands for your own sudo account)
    <!-- verify:account -->              as the service account `opendots`, with a login shell (sudo -iu opendots)
    <!-- verify:account doctor -->       exit 0 or 1 accepted: 1 means `doctor` found a problem (no engine in a clean container)
    <!-- verify:account serve -->        started in the background, stopped after the next `check` block
    <!-- verify:account check -->        run while the `serve` block runs
    <!-- verify:account HOME=/tmp/x -->  as the account, with another HOME (a restore into an empty data folder)
    <!-- verify:docker -->               in a scratch copy of the repository, with Docker Compose
    <!-- verify:skip -->                 not run (needs the public repository, systemd or a real machine)

A block with no marker is not run. A few lines cannot run unchanged and are replaced, and the
replacements are the only departures from the guide's text:
  - `git clone` (needs the public repository) is replaced by copying this working tree;
  - the passphrase prompt reads a line from standard input instead of a terminal (`--stdin`);
  - `docker compose exec` gets `-T` (no terminal); the proxy step is replaced by a plain `docker compose up -d`
    (the proxies are checked by scripts/smoke-proxy.py);
  - the restore's archive name is the backup made a moment before.
After the blocks, the generated systemd unit is checked with `systemd-analyze verify`.

    python scripts/verify-guide.py [--route direct|docker|both] [--keep]

Needs Docker, and `bash` on the PATH for the Docker route. The direct route installs nothing on your machine:
it builds a throwaway image with Node.js 22 and uv, copies this working tree in, and removes both afterwards.
Exit code 0 when every block exited 0.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUIDE = REPO / "docs" / "SELF-HOSTING.md"
IMAGE = "open-dots-verify-guide:local"
CONTAINER = "open-dots-verify-guide"
PASSPHRASE = "a long passphrase for the guide check"


def find_bash() -> str:
    """Git Bash on Windows (the `bash` on the PATH there is often the WSL launcher, which mangles quoting)."""
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(candidate).exists():
            return candidate
    return "bash"


BASH = find_bash()

DOCKERFILE = """\
FROM node:22-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS uv
FROM ubuntu:24.04
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends sudo git curl ca-certificates procps systemd \\
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/ /usr/local/
COPY --from=uv /usr/local/bin/uv /usr/local/bin/uv
"""

BLOCK = re.compile(r"<!-- verify:(?P<role>\w+)(?P<extra>[^>]*?)-->\s*\n\s*```sh\n(?P<body>.*?)```", re.S)


class Block:
    def __init__(self, role: str, extra: str, body: str, line: int):
        self.role, self.extra, self.body, self.line = role, extra.strip(), textwrap.dedent(body), line

    def __repr__(self) -> str:
        return f"{self.role} {self.extra}".strip() + f" (guide line {self.line})"


def blocks() -> list[Block]:
    text = GUIDE.read_text(encoding="utf-8")
    return [Block(m["role"], m["extra"], m["body"], text[: m.start()].count("\n") + 1) for m in BLOCK.finditer(text)]


def run(args: list[str], *, input: str | None = None, cwd: Path | None = None, env: dict | None = None,
        timeout: int = 1800) -> subprocess.CompletedProcess:
    import os
    return subprocess.run(args, input=input, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "MSYS_NO_PATHCONV": "1", **(env or {})}, encoding="utf-8", errors="replace")


def show(label: str, block: Block, result: subprocess.CompletedProcess) -> bool:
    ok = result.returncode == 0
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {block}")
    if not ok:
        print(textwrap.indent((result.stdout + result.stderr)[-3000:], "      "))
    return ok


# -- the direct route, in a clean container -----------------------------------------------------------

def container_exec(script: str, *, user: str | None = None, env: dict | None = None, stdin: str | None = None):
    args = ["docker", "exec", "-i"]
    if user:
        args += ["-u", user, "-w", f"/home/{user}"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    args += [CONTAINER, "bash", "-lec" if user else "-ec", script]
    return run(args, input=stdin)


def copy_tree_in() -> None:
    listing = run(["git", "ls-files", "-z"], cwd=REPO).stdout
    names = [name for name in listing.split("\0") if name and (REPO / name).exists()]
    with tempfile.TemporaryDirectory() as folder:
        archive = Path(folder) / "tree.tar"
        subprocess.run(["tar", "--null", "-cf", str(archive), "-T", "-"], cwd=REPO, input="\0".join(names).encode(), check=True)
        subprocess.run(["docker", "cp", str(archive), f"{CONTAINER}:/tmp/tree.tar"], check=True, capture_output=True)
    result = container_exec("tar -xf /tmp/tree.tar -C /opt/open-dots && chown -R opendots:opendots /opt/open-dots && rm /tmp/tree.tar")
    assert result.returncode == 0, result.stderr


def direct_route(steps: list[Block], keep: bool) -> bool:
    print("-- direct route, in a clean container --")
    build = run(["docker", "build", "-q", "-t", IMAGE, "-"], input=DOCKERFILE)
    assert build.returncode == 0, build.stdout + build.stderr
    run(["docker", "rm", "-f", CONTAINER])
    started = run(["docker", "run", "-d", "--name", CONTAINER, IMAGE, "sleep", "infinity"])
    assert started.returncode == 0, started.stderr
    good = True
    try:
        index = 0
        while index < len(steps):
            step = steps[index]
            index += 1
            if step.role == "skip":
                if "git clone" in step.body:
                    copy_tree_in()
                    print(f"----  the working tree is copied in place of: {step}")
                else:
                    print(f"SKIP  {step}")
                continue
            if step.role == "root":
                good &= show("run", step, container_exec(step.body))
            elif step.role == "account" and step.extra == "check":
                continue  # run by the serve block
            elif step.role == "account" and step.extra == "serve":
                good &= serve(step, steps[index])
            elif step.role == "account":
                good &= account(step)
        unit = container_exec("systemd-analyze verify /etc/systemd/system/open-dots.service 2>&1; echo exit=$?; "
                              "grep -E '^(User|ExecStart|EnvironmentFile)=' /etc/systemd/system/open-dots.service")
        print("----  generated unit:\n" + textwrap.indent(unit.stdout, "      "))
        bad = [line for line in unit.stdout.splitlines() if "open-dots.service" in line and "Failed" in line]
        good &= not bad
    finally:
        if not keep:
            run(["docker", "rm", "-f", CONTAINER])
            run(["docker", "rmi", IMAGE])
    return good


def account(step: Block) -> bool:
    body = step.body
    stdin = None
    if "set-passphrase" in body:
        body = body.replace("set-passphrase", "set-passphrase --stdin")
        stdin = PASSPHRASE + "\n"
    env = {}
    for word in step.extra.split():
        if "=" in word:
            key, value = word.split("=", 1)
            env[key] = value
    if env.get("HOME"):
        container_exec(f"mkdir -p {env['HOME']} && chown opendots:opendots {env['HOME']}")
    if "restore" in body:
        archive = container_exec("ls -t /home/opendots/.open-dots/backups/open-dots-backup-*.tar.gz | head -1").stdout.strip()
        body = re.sub(r"/path/to/open-dots-backup-\S*", archive, body)
    result = container_exec(body, user="opendots", env=env, stdin=stdin)
    if "doctor" in step.extra.split():
        # `doctor` exits 1 when it found a problem; a clean container has no engine. Anything else is a failure.
        if result.returncode == 1 and "Open Duet server check" in result.stdout:
            result.returncode = 0
    return show("run", step, result)


def serve(step: Block, check: Block) -> bool:
    assert check.role == "account" and check.extra == "check", "a serve block is followed by a check block"
    import os
    process = subprocess.Popen(["docker", "exec", "-u", "opendots", "-w", "/home/opendots", CONTAINER, "bash", "-lec", step.body],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if container_exec("curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1").returncode == 0:
                break
            time.sleep(1)
        result = container_exec(check.body, user="opendots")
    finally:
        container_exec("pkill -TERM -f 'python -m app.server$' || true")
        try:
            output, _ = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
    ok = show("serve and check", check, result)
    if not ok:
        print(textwrap.indent((output or "")[-2000:], "      server: "))
    return ok


# -- the Docker route, in a scratch copy --------------------------------------------------------------

def docker_route(steps: list[Block], keep: bool) -> bool:
    print("-- Docker route, in a scratch copy of the repository --")
    scratch = Path(tempfile.mkdtemp(prefix="open-dots-guide-")) / "open-dots"
    listing = run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=REPO).stdout
    for name in filter(None, listing.split("\0")):
        source = REPO / name
        if source.is_file() and not name.endswith(".db") and name != "open-dots.env":
            (scratch / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, scratch / name)
    env = {"COMPOSE_PROJECT_NAME": "open-dots-guide", "OPEN_DOTS_PORT": "18321", "OPEN_DOTS_IMAGE": "open-dots:guide"}
    good = True
    try:
        for step in steps:
            body = step.body
            if "git clone" in body:
                body = "\n".join(line for line in body.splitlines() if not line.startswith(("git clone", "cd open-duet")))
            if "docker compose exec" in body:
                body = body.replace("docker compose exec ", "docker compose exec -T ")
            stdin = None
            if "set-passphrase" in body:
                body = body.replace("set-passphrase", "set-passphrase --stdin")
                stdin = PASSPHRASE + "\n"
            if "KEY=$(" not in body and "docker compose exec" in body:
                up = run([BASH, "-ec", "docker compose up -d"], cwd=scratch, env=env)
                print(f"----  docker compose up -d, in place of the proxy step (exit {up.returncode})")
                good &= up.returncode == 0
                time.sleep(8)
            result = run([BASH, "-ec", body], cwd=scratch, env=env, input=stdin)
            if "doctor" in step.extra.split() and result.returncode == 1 and "Open Duet server check" in result.stdout:
                result.returncode = 0  # `doctor` found a problem (no engine); see the account route
            good &= show("run", step, result)
    finally:
        if not keep:
            run([BASH, "-ec", "docker compose down -v --remove-orphans"], cwd=scratch, env=env)
            run(["docker", "rmi", "open-dots:guide"])
            shutil.rmtree(scratch.parent, ignore_errors=True)
    return good


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # the console of a Windows machine is not UTF-8
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--route", choices=("direct", "docker", "both"), default="both")
    parser.add_argument("--keep", action="store_true", help="leave the container, image and scratch copy in place")
    args = parser.parse_args()
    found = blocks()
    print(f"{len(found)} marked blocks in {GUIDE.name}: {sum(1 for b in found if b.role != 'skip')} run, {sum(1 for b in found if b.role == 'skip')} skipped")
    good = True
    if args.route in ("direct", "both"):
        good &= direct_route([b for b in found if b.role != "docker"], args.keep)
    if args.route in ("docker", "both"):
        good &= docker_route([b for b in found if b.role == "docker"], args.keep)
    print("\nall blocks ran" if good else "\nSOME BLOCKS FAILED")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
