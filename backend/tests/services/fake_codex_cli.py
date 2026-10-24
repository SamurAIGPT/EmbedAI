"""A stand-in for the `codex` CLI, run as a real child process.

It models the parts of codex-cli 0.162.1 the Codex adapter uses:
`--version`, `login status`, and `app-server` speaking JSON-RPC (one JSON
object per line) with `initialize`, `model/list`, `thread/start`,
`thread/resume`, `turn/start` and `turn/interrupt`, server-to-client approval
requests, item notifications and `turn/completed`.

Configured by the environment (the adapter passes its environment on):
  FAKE_CODEX_DIR   a folder holding `plan.json` and, written by this script,
                   `log.jsonl` (everything received) and `threads/` (one file
                   per Codex thread that exists, so resume can be checked).
plan.json: {"login": "in" | "out" | "hang", "version_hang": bool,
            "policy_mismatch": bool, "steps": [step, ...]}
Turn N plays steps[N] (the last repeats). A step is
  {"type": "chat", "text": "..."}
  {"type": "command", "command": "...", "then": "..."}   asks before running
  {"type": "file", "path": "...", "then": "..."}         asks before writing
  {"type": "mcp"}                                         an MCP approval elicitation
  {"type": "computer", "tool": "exec", "arguments": {}}   a call of the computer MCP server mounted in the
                                                          thread config: asks like Codex does, and on accept
                                                          runs the real server (stdio) and reports its result
plan.json also takes "user_mcp": {name: {...}}, the person's own MCP servers that `config/read` reports.
  {"type": "unknown_request"}                             a request no client knows
  {"type": "slow"}                                        waits for turn/interrupt
  {"type": "fail", "message": "..."}                      turn/completed failed
  {"type": "crash"}                                       exits mid-turn
"""

import json
import os
import pathlib
import sys
import threading
import time
import uuid

base = pathlib.Path(os.environ["FAKE_CODEX_DIR"])
plan = json.loads((base / "plan.json").read_text())
threads_dir = base / "threads"
threads_dir.mkdir(exist_ok=True)


def log(**fields):
    with open(base / "log.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(fields) + "\n")


args = sys.argv[1:]
log(event="argv", argv=args, cwd=os.getcwd())

if args[:1] == ["--version"]:
    if plan.get("version_hang"):
        time.sleep(60)
    print("codex-cli 0.162.1")
    sys.exit(0)

if args[:2] == ["login", "status"]:
    login = plan.get("login", "in")
    if login == "hang":
        time.sleep(60)
    if login == "in":
        print("Logged in using ChatGPT (someone@example.com)")
        sys.exit(0)
    print("Not logged in", file=sys.stderr)
    sys.exit(1)

if args[:1] != ["app-server"]:
    sys.exit(2)

lock = threading.Lock()
responses: dict = {}
response_ready = threading.Condition(lock)
interrupted = threading.Event()


def send(message):
    with lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def notify(method, params):
    send({"jsonrpc": "2.0", "method": method, "params": params})


next_request = [100]


def ask(method, params):
    """A server-to-client request; waits for the answer."""
    with lock:
        rid = next_request[0]
        next_request[0] += 1
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    with response_ready:
        while rid not in responses:
            if not response_ready.wait(timeout=60):
                return None
        return responses.pop(rid)


counter = base / "turns"


thread_configs = {}


def call_mcp(config, server_name, tool, arguments):
    import subprocess

    server = config["mcp_servers"][server_name]
    proc = subprocess.Popen(
        [server["command"], *server["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        env={**os.environ, **server.get("env", {})}, text=True,
    )
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
    ]
    out, _ = proc.communicate("".join(json.dumps(r) + "\n" for r in requests), timeout=60)
    replies = [json.loads(line) for line in out.splitlines() if line.strip()]
    result = next(r for r in replies if r.get("id") == 2)["result"]
    return "".join(c.get("text", "") for c in result["content"]), bool(result.get("isError"))


def next_step():
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))
    steps = plan.get("steps") or [{"type": "chat", "text": "hello"}]
    return steps[min(n, len(steps) - 1)]


def message(thread_id, turn_id, text, phase="final_answer"):
    item_id = "msg_" + uuid.uuid4().hex[:8]
    notify("item/started", {"threadId": thread_id, "turnId": turn_id, "item": {"type": "agentMessage", "id": item_id, "text": "", "phase": phase}})
    for word in text.split(" "):
        notify("item/agentMessage/delta", {"threadId": thread_id, "turnId": turn_id, "itemId": item_id, "delta": word + " "})
    notify("item/completed", {"threadId": thread_id, "turnId": turn_id, "item": {"type": "agentMessage", "id": item_id, "text": text, "phase": phase}})


def complete(thread_id, turn_id, status="completed", error=None):
    notify("thread/tokenUsage/updated", {"threadId": thread_id, "turnId": turn_id, "tokenUsage": {"total": {"inputTokens": 11, "outputTokens": 7, "cachedInputTokens": 3}}})
    notify("turn/completed", {"threadId": thread_id, "turn": {"id": turn_id, "status": status, "error": error, "items": []}})


def play(thread_id, turn_id, step):
    kind = step.get("type")
    if kind == "chat":
        message(thread_id, turn_id, step.get("text", "hello"))
        complete(thread_id, turn_id)
    elif kind == "command":
        command = step.get("command", "echo hi > out.txt")
        item_id = "exec-" + uuid.uuid4().hex[:8]
        full = f"/bin/zsh -lc '{command}'"
        notify("item/started", {"threadId": thread_id, "turnId": turn_id, "item": {"type": "commandExecution", "id": item_id, "command": full, "cwd": os.getcwd(), "status": "inProgress", "commandActions": [{"type": "unknown", "command": command}]}})
        answer = ask("item/commandExecution/requestApproval", {"threadId": thread_id, "turnId": turn_id, "itemId": item_id, "command": full, "cwd": os.getcwd(), "commandActions": [{"type": "unknown", "command": command}]})
        log(event="approval_answer", answer=answer)
        accepted = isinstance(answer, dict) and answer.get("result", {}).get("decision") == "accept"
        if accepted:
            pathlib.Path("out.txt").write_text("hi\n")
        notify("item/completed", {"threadId": thread_id, "turnId": turn_id, "item": {"type": "commandExecution", "id": item_id, "command": full, "status": "completed" if accepted else "declined", "aggregatedOutput": "" if accepted else None, "exitCode": 0 if accepted else None}})
        message(thread_id, turn_id, step.get("then", "done" if accepted else "it was declined"))
        complete(thread_id, turn_id)
    elif kind == "file":
        path = step.get("path", os.path.join(os.getcwd(), "notes.txt"))
        item_id = "patch-" + uuid.uuid4().hex[:8]
        changes = [{"path": path, "kind": {"type": "add"}, "diff": "+hello\n"}]
        notify("item/started", {"threadId": thread_id, "turnId": turn_id, "item": {"type": "fileChange", "id": item_id, "changes": changes, "status": "inProgress"}})
        answer = ask("item/fileChange/requestApproval", {"threadId": thread_id, "turnId": turn_id, "itemId": item_id, "reason": None, "grantRoot": None})
        log(event="approval_answer", answer=answer)
        accepted = isinstance(answer, dict) and answer.get("result", {}).get("decision") == "accept"
        notify("item/completed", {"threadId": thread_id, "turnId": turn_id, "item": {"type": "fileChange", "id": item_id, "changes": changes, "status": "completed" if accepted else "declined"}})
        message(thread_id, turn_id, step.get("then", "written" if accepted else "not written"))
        complete(thread_id, turn_id)
    elif kind == "mcp":
        answer = ask("mcpServer/elicitation/request", {"threadId": thread_id, "serverName": "docs", "message": 'Allow the docs MCP server to run tool "search"?', "_meta": {"codex_approval_kind": "mcp_tool_call"}})
        log(event="approval_answer", answer=answer)
        message(thread_id, turn_id, "mcp done")
        complete(thread_id, turn_id)
    elif kind == "computer":
        tool, arguments = step.get("tool", "exec"), step.get("arguments", {})
        item_id = "mcp-" + uuid.uuid4().hex[:8]
        item = {"type": "mcpToolCall", "id": item_id, "server": "computer", "tool": tool, "status": "inProgress", "arguments": arguments}
        notify("item/started", {"threadId": thread_id, "turnId": turn_id, "item": item})
        answer = ask("mcpServer/elicitation/request", {
            "threadId": thread_id, "turnId": turn_id, "serverName": "computer", "mode": "form",
            "message": f'Allow the computer MCP server to run tool "{tool}"?',
            "_meta": {"codex_approval_kind": "mcp_tool_call", "tool_params": arguments},
            "requestedSchema": {"type": "object", "properties": {}},
        })
        log(event="approval_answer", answer=answer)
        accepted = isinstance(answer, dict) and answer.get("result", {}).get("action") == "accept"
        if accepted:
            text, is_error = call_mcp(thread_configs[thread_id], "computer", tool, arguments)
            log(event="mcp_result", content=text, is_error=is_error)
        notify("item/completed", {"threadId": thread_id, "turnId": turn_id, "item": {**item, "status": "completed" if accepted else "declined"}})
        message(thread_id, turn_id, "ran" if accepted else "declined")
        complete(thread_id, turn_id)
    elif kind == "unknown_request":
        answer = ask("item/tool/call", {"threadId": thread_id, "tool": "x"})
        log(event="approval_answer", answer=answer)
        message(thread_id, turn_id, "ok")
        complete(thread_id, turn_id)
    elif kind == "slow":
        notify("item/agentMessage/delta", {"threadId": thread_id, "turnId": turn_id, "itemId": "m1", "delta": "partial words"})
        interrupted.wait(60)
        complete(thread_id, turn_id, status="interrupted")
    elif kind == "fail":
        complete(thread_id, turn_id, status="failed", error={"message": step.get("message", "model exploded")})
    elif kind == "crash":
        notify("item/agentMessage/delta", {"threadId": thread_id, "turnId": turn_id, "itemId": "m1", "delta": "half"})
        sys.stderr.write("codex crashed\n")
        sys.stderr.flush()
        os._exit(1)


def thread_result(thread_id, params):
    policy = params.get("approvalPolicy")
    sandbox = {"read-only": "readOnly", "workspace-write": "workspaceWrite", "danger-full-access": "dangerFullAccess"}.get(params.get("sandbox"), "readOnly")
    if plan.get("policy_mismatch"):
        policy, sandbox = "never", "dangerFullAccess"
    return {"thread": {"id": thread_id}, "model": params.get("model") or "gpt-fake", "approvalPolicy": policy, "sandbox": {"type": sandbox}}


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    log(event="in", msg=msg)
    if "method" not in msg:
        with response_ready:
            responses[msg.get("id")] = msg
            response_ready.notify_all()
        continue
    method, rid, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if method == "initialize":
        log(event="initialize", params=params)
        send({"id": rid, "result": {"userAgent": "fake"}})
    elif method == "initialized":
        pass
    elif method == "model/list":
        send({"id": rid, "result": {"data": [
            {"id": "gpt-fake", "displayName": "GPT Fake", "hidden": False, "isDefault": True},
            {"id": "gpt-fake-mini", "displayName": "GPT Fake Mini", "hidden": False, "isDefault": False},
            {"id": "gpt-hidden", "displayName": "Hidden", "hidden": True, "isDefault": False},
        ]}})
    elif method == "config/read":
        send({"id": rid, "result": {"config": {"mcp_servers": plan.get("user_mcp", {})}, "origins": {}}})
    elif method == "thread/start":
        thread_id = str(uuid.uuid4())
        thread_configs[thread_id] = params.get("config") or {}
        (threads_dir / thread_id).write_text("x")
        send({"id": rid, "result": thread_result(thread_id, params)})
    elif method == "thread/resume":
        thread_id = params.get("threadId")
        thread_configs[thread_id] = params.get("config") or {}
        if not (threads_dir / str(thread_id)).exists():
            send({"id": rid, "error": {"code": -32600, "message": f"no rollout found for thread id {thread_id}"}})
        else:
            send({"id": rid, "result": thread_result(thread_id, params)})
    elif method == "turn/start":
        turn_id = "turn-" + uuid.uuid4().hex[:8]
        send({"id": rid, "result": {"turn": {"id": turn_id, "status": "inProgress"}}})
        step = next_step()
        threading.Thread(target=play, args=(params.get("threadId"), turn_id, step), daemon=True).start()
    elif method == "turn/interrupt":
        interrupted.set()
        send({"id": rid, "result": {}})
    else:
        send({"id": rid, "error": {"code": -32601, "message": "unknown method"}})
