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
    _text_parts: list[str] = field(default_factory=list)
    _current_message: str | None = None
    _messages_with_deltas: set[str] = field(default_factory=set)
    _snapshot_seen: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "".join(self._text_parts)

    def feed(self, raw: bytes | str) -> list[str]:
        """Returns the text chunks (possibly none) this line adds."""
        event = self._decode(raw)
        if event is None:
            return []
        kind = event.get("type")
        if kind == "stream_event":
            return self._stream_event(event)
        if kind == "assistant":
            return self._assistant(event)
        if kind == "result":
            self._result(event)
        return []

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
        return [chunk]
