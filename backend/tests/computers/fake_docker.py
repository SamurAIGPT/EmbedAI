#!/usr/bin/env python3
"""A stand-in for the `docker` CLI, run as a real child process.

State lives in $FAKE_DOCKER_STATE/state.json and every call is appended to
$FAKE_DOCKER_STATE/log.jsonl. `docker exec` really runs the command on this
machine, in the folder mounted at /workspace (with `timeout -k 5 N` dropped,
since not every host has it), so the computer tools can be tested end to end.

$FAKE_DOCKER_STATE/mode may hold `down` (the daemon is not running) or `hang`
(every call sleeps).
"""

import json
import os
import pathlib
import subprocess
import sys
import time

base = pathlib.Path(os.environ["FAKE_DOCKER_STATE"])
base.mkdir(parents=True, exist_ok=True)
state_file = base / "state.json"
args = sys.argv[1:]


def load():
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {"images": {}, "containers": {}}


def save(state):
    state_file.write_text(json.dumps(state))


def log(**fields):
    with open(base / "log.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"args": args, **fields}) + "\n")


def fail(message, code=1):
    sys.stderr.write(message + "\n")
    sys.exit(code)


log(event="call")
mode = (base / "mode").read_text().strip() if (base / "mode").exists() else ""
if mode == "hang":
    time.sleep(60)
if mode == "down":
    fail("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?")

state = load()
command = args[0] if args else ""

if command == "info":
    print(json.dumps("27.0.0-fake"))

elif command == "image" and args[1] == "inspect":
    image = args[-1]
    if image not in state["images"]:
        fail(f"Error response from daemon: No such image: {image}")
    print(state["images"][image])

elif command == "pull":
    image = args[-1]
    state["images"][image] = "sha256:" + format(abs(hash(image)), "x").rjust(64, "0")[:64]
    save(state)
    print(f"Pulled {image}")

elif command == "container" and args[1] == "inspect":
    name = args[-1]
    container = state["containers"].get(name)
    if container is None:
        fail(f"Error response from daemon: No such container: {name}")
    print(json.dumps([container["inspect"]]))

elif command == "run":
    rest = args[1:]
    labels, envs, name, user, network, mounts, caps, extra = {}, [], None, "", "default", [], [], {}
    i = 0
    flags_with_value = {
        "--name", "--hostname", "--label", "--user", "--cap-drop", "--security-opt", "--cpus", "--memory",
        "--memory-swap", "--pids-limit", "--ipc", "--restart", "--network", "--tmpfs", "-e", "--mount", "-w",
    }
    while i < len(rest):
        flag = rest[i]
        if flag in ("-d", "--init"):
            i += 1
            continue
        if flag in flags_with_value:
            value = rest[i + 1]
            if flag == "--name":
                name = value
            elif flag == "--label":
                key, _, val = value.partition("=")
                labels[key] = val
            elif flag == "--user":
                user = value
            elif flag == "--network":
                network = value
            elif flag == "--cap-drop":
                caps.append(value)
            elif flag == "--mount":
                parts = dict(p.split("=", 1) for p in value.split(","))
                mounts.append({"Type": parts["type"], "Source": parts["source"], "Destination": parts["target"], "RW": True})
            else:
                extra[flag] = value
            i += 2
            continue
        break
    image = rest[i]
    if image not in state["images"]:
        fail(f"Unable to find image '{image}' locally", 125)
    if name in state["containers"]:
        fail(f'docker: Error response from daemon: Conflict. The container name "/{name}" is already in use.', 125)
    cid = format(abs(hash(name + str(time.time()))), "x").rjust(64, "0")[:64]
    state["containers"][name] = {
        "inspect": {
            "Id": cid,
            "Image": state["images"][image],
            "Config": {"Image": image, "Labels": labels, "User": user, "Cmd": rest[i + 1:]},
            "HostConfig": {"CapDrop": caps, "CapAdd": None, "Privileged": False, "PortBindings": {},
                           "PublishAllPorts": False, "NetworkMode": network, "PidMode": "", **{"Flags": extra}},
            "Mounts": mounts,
            "State": {"Running": True, "StartedAt": "2026-10-17T00:00:00Z"},
        }
    }
    save(state)
    print(cid)

elif command in ("start", "stop"):
    name = args[-1]
    container = state["containers"].get(name)
    if container is None:
        fail(f"Error response from daemon: No such container: {name}")
    container["inspect"]["State"]["Running"] = command == "start"
    save(state)
    print(name)

elif command == "rm":
    name = args[-1]
    if name not in state["containers"]:
        fail(f"Error: No such container: {name}")
    del state["containers"][name]
    save(state)
    print(name)

elif command == "update":
    name = args[-1]
    container = state["containers"].get(name)
    if container is None:
        fail(f"Error response from daemon: No such container: {name}")
    container["inspect"]["HostConfig"]["Updated"] = args[1:-1]
    save(state)
    print(name)

elif command == "ps":
    filters = [args[i + 1] for i, a in enumerate(args) if a == "--filter"]
    wanted = [f.removeprefix("label=").split("=", 1) for f in filters]
    for name, container in state["containers"].items():
        labels = container["inspect"]["Config"]["Labels"]
        if all(labels.get(k) == v for k, v in wanted):
            print(f"{name}\t{labels.get('dev.open-dots.computer.id', '')}")

elif command == "exec":
    rest = args[1:]
    interactive = False
    i = 0
    while rest[i].startswith("-"):
        if rest[i] == "-i":
            interactive = True
            i += 1
        else:
            i += 2
    name, cmd = rest[i], rest[i + 1:]
    container = state["containers"].get(name)
    if container is None:
        fail(f"Error response from daemon: No such container: {name}")
    if not container["inspect"]["State"]["Running"]:
        fail(f"Error response from daemon: container {name} is not running")
    if cmd[:2] == ["timeout", "-k"]:
        cmd = cmd[4:]
    mount = next((m for m in container["inspect"]["Mounts"] if m["Destination"] == "/workspace"), None)
    cwd = mount["Source"] if mount else str(base)
    env = dict(os.environ, HOME=str(base / "home"))
    done = subprocess.run(cmd, cwd=cwd, env=env, input=sys.stdin.buffer.read() if interactive else None,
                          capture_output=True)
    sys.stdout.buffer.write(done.stdout)
    sys.stderr.buffer.write(done.stderr)
    sys.exit(done.returncode)

else:
    fail(f"fake docker: unsupported command {args}", 2)
