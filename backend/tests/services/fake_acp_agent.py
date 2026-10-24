"""A stand-in ACP agent (Agent Client Protocol over stdio), run as a real child
process.

Configured by FAKE_ACP_DIR, a folder holding `plan.json`; the script writes
`log.jsonl` (everything received) and `sessions/` (one file per session, so
`session/load` can be checked) there.
plan.json: {"load": bool (agentCapabilities.loadSession), "hang_init": bool,
            "steps": [step, ...]}   prompt N plays steps[N], the last repeats:
  {"type": "chat", "text": "..."}
  {"type": "permission", "kind": "execute", "command": "ls", "locations": [...], "title": "...", "raw": {...}}
  {"type": "only_always"}              offers allow_always and reject_once only
  {"type": "fs"}                       asks the client to read a file
  {"type": "slow"}                     streams, then waits for session/cancel
  {"type": "refusal"}                  stopReason "refusal"
  {"type": "error"}                    session/prompt answers an error
"""

import json
import os
import pathlib
import sys
import threading
import time
import uuid

base = pathlib.Path(os.environ["FAKE_ACP_DIR"])
plan = json.loads((base / "plan.json").read_text())
sessions = base / "sessions"
sessions.mkdir(exist_ok=True)
lock = threading.Lock()
cond = threading.Condition(lock)
responses: dict = {}
cancelled = threading.Event()
next_id = [500]


def log(**fields):
    with open(base / "log.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(fields) + "\n")


log(event="argv", argv=sys.argv[1:], cwd=os.getcwd(), env_names=sorted(os.environ))


def send(message):
    with lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def update(session_id, payload):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": payload}})


def ask(method, params):
    with lock:
        rid = next_id[0]
        next_id[0] += 1
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    with cond:
        while rid not in responses:
            if not cond.wait(timeout=60):
                return None
        return responses.pop(rid)


def chunk(session_id, text):
    update(session_id, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})


counter = base / "prompts"


def next_step():
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))
    steps = plan.get("steps") or [{"type": "chat", "text": "hi from acp"}]
    return steps[min(n, len(steps) - 1)]


def prompt(rid, params):
    session_id = params.get("sessionId")
    text = "".join(block.get("text", "") for block in params.get("prompt") or [])
    log(event="prompt_text", text=text)
    step = next_step()
    kind = step.get("type")
    if kind == "chat":
        for word in step.get("text", "hi from acp").split(" "):
            chunk(session_id, word + " ")
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn", "usage": {"inputTokens": 5, "outputTokens": 3}}})
    elif kind in ("permission", "only_always"):
        call_id = "call_" + uuid.uuid4().hex[:6]
        tool_kind = step.get("kind", "execute")
        raw = step.get("raw") or ({"command": step.get("command", "ls -la")} if tool_kind == "execute" else {"path": "x"})
        chunk(session_id, "Let me look.")
        update(session_id, {"sessionUpdate": "tool_call", "toolCallId": call_id, "title": step.get("title", "Run ls"), "kind": tool_kind, "status": "pending", "rawInput": raw})
        options = [
            {"optionId": "always", "name": "Always", "kind": "allow_always"},
            {"optionId": "once", "name": "Allow", "kind": "allow_once"},
            {"optionId": "no", "name": "Reject", "kind": "reject_once"},
        ]
        if kind == "only_always":
            options = [options[0], options[2]]
        locations = [{"path": p} for p in step.get("locations", [])]
        answer = ask("session/request_permission", {"sessionId": session_id, "toolCall": {"toolCallId": call_id, "title": step.get("title", "Run ls"), "kind": tool_kind, "rawInput": raw, "locations": locations}, "options": options})
        log(event="permission_answer", answer=answer)
        outcome = (answer or {}).get("result", {}).get("outcome", {})
        allowed = outcome.get("outcome") == "selected" and outcome.get("optionId") in ("once", "always")
        update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "completed" if allowed else "failed", "content": [{"type": "content", "content": {"type": "text", "text": "file-a\nfile-b" if allowed else "rejected"}}]})
        chunk(session_id, "Done." if allowed else "Not allowed.")
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
    elif kind == "fs":
        answer = ask("fs/read_text_file", {"sessionId": session_id, "path": "/etc/hosts"})
        log(event="fs_answer", answer=answer)
        chunk(session_id, "no fs")
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
    elif kind == "slow":
        chunk(session_id, "thinking out loud")
        cancelled.wait(60)
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
    elif kind == "refusal":
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "refusal"}})
    elif kind == "error":
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": step.get("message", "agent fell over")}})


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    log(event="in", msg=msg)
    if "method" not in msg:
        with cond:
            responses[msg.get("id")] = msg
            cond.notify_all()
        continue
    method, rid, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if method == "initialize":
        if plan.get("hang_init"):
            time.sleep(60)
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": bool(plan.get("load"))}, "agentInfo": plan.get("agent_info", {"name": "fake-agent", "version": "1.2"}), "authMethods": plan.get("auth_methods", [])}})
    elif method == "authenticate":
        if plan.get("auth_write_token"):
            token = pathlib.Path(os.environ["GEMINI_HOME"]) / "antigravity-acp" / "acp_token.json"
            token.write_text(json.dumps({"client_id": "fixture", "client_secret": "fixture", "refresh_token": "fixture"}))
        if plan.get("auth_error"):
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": plan["auth_error"]}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif method in ("session/set_model", "session/set_config_option"):
        if plan.get("model_error"):
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": plan["model_error"], "message": "Model refused"}})
        else:
            result = {"configOptions": [{"id": params.get("configId"), "currentValue": plan.get("mode_result", params.get("value")) if params.get("configId") == "mode" else params.get("value")}]} if method == "session/set_config_option" else {}
            send({"jsonrpc": "2.0", "id": rid, "result": result})
    elif method == "session/new":
        session_id = "sess_" + uuid.uuid4().hex[:8]
        (sessions / session_id).write_text("x")
        send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": session_id, **plan.get("session", {})}})
    elif method in ("session/load", "session/resume"):
        session_id = params.get("sessionId")
        if not (sessions / str(session_id)).exists():
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32002, "message": "session not found"}})
        else:
            chunk(session_id, "REPLAYED OLD TEXT")
            send({"jsonrpc": "2.0", "id": rid, "result": plan.get("session") if method == "session/resume" else None})
    elif method == "session/prompt":
        threading.Thread(target=prompt, args=(rid, params), daemon=True).start()
    elif method == "session/cancel":
        cancelled.set()
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown"}})
