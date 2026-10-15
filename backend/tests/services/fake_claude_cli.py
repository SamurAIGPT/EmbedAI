"""A stand-in for the `claude` CLI, run as a real child process.

It models what was verified against Claude Code 2.1.232 (F04 Step 0):
- a session exists once the CLI has read the prompt and reported `system/init`;
- `--session-id <id>` for an id that exists exits 1 with "already in use";
- `--resume <id>` for an id that does not exist exits 1 with "No conversation found";
- in tools mode stdin is stream-json and stays open until the app closes it after
  the final result; in chat-only mode stdin is the prompt text until EOF.

Invoked as: python fake_claude_cli.py PLAN MODE SESSION_ID TOOLS
  PLAN   a JSON file {"steps": [{"type": ...}, ...]}; invocation N plays steps[N]
         (the last step repeats). Next to it the script keeps `count`, `sessions/`
         and `log.jsonl` (one line per thing that happened) for the test to read.
  MODE   "new" or "resume"        TOOLS  "1" or "0"
"""

import json
import pathlib
import sys
import time

plan_path, mode, sid, tools = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
base = pathlib.Path(plan_path).parent
plan = json.loads(pathlib.Path(plan_path).read_text())
counter = base / "count"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
step = plan["steps"][min(n, len(plan["steps"]) - 1)]
session_file = base / "sessions" / sid
session_file.parent.mkdir(exist_ok=True)


def log(**fields):
    with open(base / "log.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"n": n, "mode": mode, "sid": sid, **fields}) + "\n")


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


log(event="start", tools=tools)

if mode == "new" and session_file.exists():
    sys.stderr.write(f"Error: Session ID {sid} is already in use.\n")
    log(event="already_in_use")
    sys.exit(1)
if mode == "resume" and not session_file.exists():
    message = f"No conversation found with session ID: {sid}"
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "",
          "errors": [message]})
    sys.stderr.write(message + "\n")
    log(event="no_conversation")
    sys.exit(1)

if tools:
    prompt = json.loads(sys.stdin.readline())["message"]["content"]
else:
    prompt = sys.stdin.read()
log(event="prompt", prompt=prompt)

session_file.write_text("session")
log(event="session_created")
emit({"type": "system", "subtype": "init", "session_id": sid, "model": "fake-model"})

kind = step.get("type", "chat")


def say(text, message_id="m1"):
    """Stream `text` as deltas, then the complete assistant message."""
    emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": message_id}}})
    words = text.split(" ")
    for index, word in enumerate(words):
        piece = word + (" " if index < len(words) - 1 else "")
        emit({"type": "stream_event", "event": {"type": "content_block_delta",
                                                 "delta": {"type": "text_delta", "text": piece}}})
    emit({"type": "assistant", "message": {"id": message_id, "content": [{"type": "text", "text": text}]}})


def finish(result, **more):
    emit({"type": "result", "is_error": False, "result": result,
          "usage": {"input_tokens": 3, "output_tokens": 2}, "total_cost_usd": 0.001, **more})
    if tools:
        sys.stdin.read()  # the real CLI waits for the app to close stdin
    log(event="exit_clean")


if kind == "chat":
    text = step.get("text", "Hello world")
    say(text)
    finish(text)

elif kind == "two_messages":
    say("Let me look.", "m1")
    say("All done.", "m2")
    finish("All done.")

elif kind in ("tool", "tool_big"):
    tool_input = {"file_path": "notes.txt", "content": "hello"}
    if kind == "tool_big":
        tool_input["content"] = "x" * 60000
    emit({"type": "assistant", "message": {"id": "m1", "content": [
        {"type": "tool_use", "id": "tu1", "name": "Write", "input": tool_input}]}})
    emit({"type": "control_request", "request_id": "req-1", "request": {
        "subtype": "can_use_tool", "tool_name": "Write", "input": tool_input, "tool_use_id": "tu1"}})
    reply = sys.stdin.readline()
    log(event="reply", reply=json.loads(reply) if reply.strip() else None)
    behavior = json.loads(reply)["response"]["response"]["behavior"] if reply.strip() else "none"
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu1", "is_error": behavior != "allow",
         "content": "File created" if behavior == "allow" else "denied"}]}})
    finish("finished: " + behavior)

elif kind == "hang":
    emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}})
    emit({"type": "stream_event", "event": {"type": "content_block_delta",
                                             "delta": {"type": "text_delta", "text": "typing"}}})
    log(event="hanging")
    time.sleep(120)

elif kind == "partial_fail":
    emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}})
    emit({"type": "stream_event", "event": {"type": "content_block_delta",
                                             "delta": {"type": "text_delta", "text": "partial text"}}})
    sys.stderr.write("the CLI crashed\n")
    sys.exit(1)

elif kind == "error_result":
    emit({"type": "result", "is_error": True, "result": "model overloaded"})
    sys.exit(0)

else:
    raise SystemExit(f"unknown step {kind!r}")
