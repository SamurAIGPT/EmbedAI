"""Turns the Claude CLI's `--output-format stream-json` lines into the few
events this app cares about.

The CLI interleaves many other events with the ones that matter
(`system/init`, `system/status`, `system/thinking_tokens`, `rate_limit_event`,
and whatever a newer version adds). This parser acts on an allowlist and skips
everything else, so an unknown event, a malformed line or a missing field can
never fail a run. Only a `result` event, a timeout or a non-zero exit decides
the outcome.

Nothing here depends on the operating system; it only sees decoded lines.
"""

import json
from dataclasses import dataclass, field


RESULT_PREVIEW_CHARS = 1000
SUMMARY_CHARS = 300


@dataclass
class ToolEvent:
    """Something about tools the stream said: `tool_started`, `tool_completed`,
    `approval_requested`, `approval_cancelled` or `control_unsupported`."""

    kind: str
    data: dict
    # What a durable store needs and the live timeline does not: the full input
    # of a started call (`input`) and the full text of a result (`output`).
    # Kept out of `data` so the existing live payloads do not change, and out of
    # equality so they compare as before.
    extra: dict = field(default_factory=dict, compare=False, repr=False)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def summarize_tool(name: str, tool_input: dict) -> str:
    """One line saying what a tool call does, for the timeline. The approval
    card shows the full input; this is only a label."""
    if name.startswith("mcp__computer__"):
        # A computer tool: say where it runs, then what it does.
        subject = tool_input.get("command") or tool_input.get("path") or "."
        label = name[len("mcp__computer__"):]
        text = subject if label == "exec" else f"{label} {subject}"
        return "computer: " + _clip(text if isinstance(text, str) else label, SUMMARY_CHARS - 10)
    key = {
        "Bash": "command",
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "Glob": "pattern",
        "Grep": "pattern",
    }.get(name)
    value = tool_input.get(key) if key else None
    return _clip(value, SUMMARY_CHARS) if isinstance(value, str) else name


def _usage_of(event: dict) -> dict | None:
    """Token counts and cost from a `result` event, as a small dict, or None."""
    usage: dict = {}
    raw = event.get("usage")
    if isinstance(raw, dict):
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = raw.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                usage[key] = value
    cost = event.get("total_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        usage["cost_usd"] = cost
    return usage or None


@dataclass
class FinalResult:
    """The CLI's own end-of-turn report (the `result` event)."""

    text: str | None
    is_error: bool
    permission_denied: bool


@dataclass
class StreamNormalizer:
    """Feed it one raw line at a time; it returns the text deltas to publish.

    Text comes from stream deltas, or from a full `assistant` snapshot for a
    message that produced no deltas, never both for the same message.
    """

    max_text_bytes: int
    text_bytes: int = 0
    over_limit: bool = False
    skipped_lines: int = 0
    final: FinalResult | None = None
    # True once the CLI has reported `system/init`: the session exists.
    init_seen: bool = False
    # What the CLI reported about itself and the run, for the stored turn.
    model: str | None = None
    usage: dict | None = None
    # A usage or rate limit refusing this account, as the CLI reported it
    # (`rate_limit_event` with status "rejected"): when it resets (epoch
    # seconds) and which window, when said.
    limit_refused: bool = False
    limit_resets_at: float | None = None
    limit_window: str | None = None
    # The text of an API error the CLI reported as a synthetic assistant
    # message (a refused call, never the model's answer).
    api_error: str | None = None
    _text_parts: list[str] = field(default_factory=list)
    # Text streamed since the last completed assistant message: what would be
    # lost if the run ended now.
    _since_message: list[str] = field(default_factory=list)
    _messages: list[str] = field(default_factory=list)
    _message_keys: set = field(default_factory=set)
    _current_message: str | None = None
    _messages_with_deltas: set[str] = field(default_factory=set)
    _snapshot_seen: dict[str, str] = field(default_factory=dict)
    _tool_events: list[ToolEvent] = field(default_factory=list)
    _tools_started: set[str] = field(default_factory=set)

    @property
    def text(self) -> str:
        return "".join(self._text_parts)

    @property
    def unflushed_text(self) -> str:
        """Streamed text that no completed assistant message has covered yet."""
        return "".join(self._since_message)

    def take_messages(self) -> list[str]:
        """Complete assistant messages (their text) seen since the last call, in
        order. A message with no text (a tool call alone) is not listed."""
        messages, self._messages = self._messages, []
        return messages

    def take_tool_events(self) -> list[ToolEvent]:
        """Tool activity and approval requests seen since the last call."""
        events, self._tool_events = self._tool_events, []
        return events

    def feed(self, raw: bytes | str) -> list[str]:
        """Returns the text chunks (possibly none) this line adds."""
        event = self._decode(raw)
        if event is None:
            return []
        kind = event.get("type")
        if kind == "stream_event":
            return self._stream_event(event)
        if kind == "system":
            if event.get("subtype") == "init":
                self.init_seen = True
                model = event.get("model")
                self.model = model if isinstance(model, str) else None
            return []
        if kind == "rate_limit_event":
            self._rate_limit(event)
            return []
        if kind == "assistant" and self._api_error(event):
            return []
        if kind == "assistant":
            self._tool_uses(event)
            chunks = self._assistant(event)
            self._complete_message(event)
            return chunks
        if kind == "user":
            self._tool_results(event)
        elif kind == "control_request":
            self._control_request(event)
        elif kind == "control_cancel_request":
            request_id = event.get("request_id")
            if isinstance(request_id, str):
                self._tool_events.append(ToolEvent("approval_cancelled", {"request_id": request_id}))
        elif kind == "result":
            self._result(event)
        return []

    def _rate_limit(self, event: dict) -> None:
        info = event.get("rate_limit_info")
        if not isinstance(info, dict) or info.get("status") != "rejected" or info.get("isUsingOverage") is True:
            return
        self.limit_refused = True
        resets = info.get("resetsAt")
        if isinstance(resets, (int, float)) and resets > 0:
            self.limit_resets_at = float(resets)
        window = info.get("rateLimitType")
        self.limit_window = window if isinstance(window, str) else None

    def _api_error(self, event: dict) -> bool:
        """A refused API call the CLI reports as an assistant message (with
        `error` or `isApiErrorMessage`): kept as the error, never stored or
        shown as the model's answer."""
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        flagged = (
            isinstance(event.get("error"), str)
            or event.get("isApiErrorMessage") is True
            or event.get("is_api_error_message") is True
            or message.get("isApiErrorMessage") is True
        )
        if not flagged:
            return False
        content = message.get("content")
        text = "".join(
            block.get("text", "") for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)
        ) if isinstance(content, list) else ""
        self.api_error = text.strip() or (event.get("error") if isinstance(event.get("error"), str) else "API error")
        return True

    def _complete_message(self, event: dict) -> None:
        """An `assistant` event carries a finished block of the answer. Its text
        becomes one stored message, once, and whatever was streamed for it is no
        longer at risk."""
        if event.get("parent_tool_use_id"):
            return
        message = event.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            return
        text = "".join(
            block.get("text", "")
            for block in message["content"]
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        if not text.strip():
            return
        key = (message.get("id"), text)
        if key in self._message_keys:
            return
        self._message_keys.add(key)
        self._messages.append(text)
        self._since_message.clear()

    def _tool_uses(self, event: dict) -> None:
        if event.get("parent_tool_use_id"):
            return
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_use_id = block.get("id")
            name = block.get("name")
            if not isinstance(tool_use_id, str) or not isinstance(name, str):
                continue
            if tool_use_id in self._tools_started:
                continue  # the same call can appear in more than one snapshot
            self._tools_started.add(tool_use_id)
            tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
            self._tool_events.append(
                ToolEvent(
                    "tool_started",
                    {
                        "tool_use_id": tool_use_id,
                        "tool": name,
                        "summary": summarize_tool(name, tool_input),
                    },
                    extra={"input": tool_input},
                )
            )

    def _tool_results(self, event: dict) -> None:
        if event.get("parent_tool_use_id"):
            return
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str):
                continue
            self._tool_events.append(
                ToolEvent(
                    "tool_completed",
                    {
                        "tool_use_id": tool_use_id,
                        "is_error": bool(block.get("is_error")),
                        "preview": _clip(_result_text(block.get("content")), RESULT_PREVIEW_CHARS),
                    },
                    extra={"output": _result_text(block.get("content"))},
                )
            )

    def _control_request(self, event: dict) -> None:
        request = event.get("request")
        request_id = event.get("request_id")
        if not isinstance(request, dict) or not isinstance(request_id, str):
            return
        if request.get("subtype") != "can_use_tool":
            # Any other question from the CLI is unknown to us; it is answered
            # with a refusal by the caller rather than silently ignored.
            self._tool_events.append(ToolEvent("control_unsupported", {"request_id": request_id}))
            return
        tool_input = request.get("input")
        name = request.get("tool_name")
        self._tool_events.append(
            ToolEvent(
                "approval_requested",
                {
                    "request_id": request_id,
                    "tool": name if isinstance(name, str) else "unknown",
                    "tool_use_id": request.get("tool_use_id")
                    if isinstance(request.get("tool_use_id"), str)
                    else None,
                    "input": tool_input if isinstance(tool_input, dict) else {},
                },
            )
        )

    def _decode(self, raw: bytes | str) -> dict | None:
        try:
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            text = text.strip()
            if not text:
                return None
            decoded = json.loads(text)
        except (ValueError, TypeError):
            self.skipped_lines += 1
            return None
        if not isinstance(decoded, dict):
            self.skipped_lines += 1
            return None
        return decoded

    def _stream_event(self, event: dict) -> list[str]:
        if event.get("parent_tool_use_id"):
            return []  # a sub-agent's output, not this conversation's answer
        inner = event.get("event")
        if not isinstance(inner, dict):
            return []
        inner_type = inner.get("type")
        if inner_type == "message_start":
            message = inner.get("message")
            message_id = message.get("id") if isinstance(message, dict) else None
            self._current_message = message_id if isinstance(message_id, str) else None
            return []
        if inner_type == "content_block_delta":
            delta = inner.get("delta")
            if not isinstance(delta, dict) or delta.get("type") != "text_delta":
                return []
            chunk = delta.get("text")
            if not isinstance(chunk, str) or not chunk:
                return []
            if self._current_message is not None:
                self._messages_with_deltas.add(self._current_message)
            return self._append(chunk)
        return []

    def _assistant(self, event: dict) -> list[str]:
        if event.get("parent_tool_use_id"):
            return []
        message = event.get("message")
        if not isinstance(message, dict):
            return []
        message_id = message.get("id")
        if isinstance(message_id, str) and message_id in self._messages_with_deltas:
            return []  # already streamed as deltas; the snapshot would repeat it
        content = message.get("content")
        if not isinstance(content, list):
            return []
        snapshot = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        if not snapshot:
            return []
        key = message_id if isinstance(message_id, str) else ""
        previous = self._snapshot_seen.get(key, "")
        # A later snapshot of the same message may repeat what an earlier one
        # already carried; add only what is new.
        new_text = snapshot[len(previous):] if snapshot.startswith(previous) else snapshot
        self._snapshot_seen[key] = snapshot if snapshot.startswith(previous) else previous + snapshot
        return self._append(new_text) if new_text else []

    def _result(self, event: dict) -> None:
        self.usage = _usage_of(event)
        text = event.get("result")
        denials = event.get("permission_denials")
        self.final = FinalResult(
            text=text if isinstance(text, str) else None,
            # `is_error` can be false even when an action was refused, so the
            # denial list counts as a failure too.
            is_error=bool(event.get("is_error")),
            permission_denied=bool(denials) if isinstance(denials, list) else False,
        )

    def _append(self, chunk: str) -> list[str]:
        size = len(chunk.encode("utf-8"))
        if self.text_bytes + size > self.max_text_bytes:
            self.over_limit = True
            return []
        self.text_bytes += size
        self._text_parts.append(chunk)
        self._since_message.append(chunk)
        return [chunk]
