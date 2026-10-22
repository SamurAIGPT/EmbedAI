"""Context compaction for engines that replay the transcript (docs/MEMORY.md)."""

from types import SimpleNamespace

import pytest

from app.services import context_compaction as cc
from app.services.agent_memory import redact_secrets


def conversation(exchanges: int, size: int = 100, current: int | None = None) -> list[dict]:
    items = []
    for turn in range(1, exchanges + 1):
        items.append({"role": "user", "text": f"question {turn} " + "q" * size, "turn_id": turn})
        items.append({"role": "tool", "name": "Read", "turn_id": turn})
        items.append({"role": "assistant", "text": f"answer {turn} " + "a" * size, "turn_id": turn})
    if current is not None:
        items.append({"role": "user", "text": "the new message", "turn_id": current})
    return items


def test_tokens_are_estimated_per_engine():
    items = [{"role": "user", "text": "x" * 400}, {"role": "tool", "output": "y" * 9000}]
    assert cc.estimate_tokens(items, "openai_compat") == 100 + 4
    assert cc.estimate_tokens(items, "acp") == int(400 / 3.5) + 4
    assert cc.estimate_tokens(items, "openai_compat", extra_chars=400) == 204


def test_only_engines_that_replay_get_a_budget():
    assert cc.budget_for(SimpleNamespace(), None) is None  # Claude, Codex: their own compaction
    replaying = SimpleNamespace(replays_history=True, compact_at_tokens=1000)
    assert cc.budget_for(replaying, None) == 1000
    assert cc.budget_for(replaying, {"context_tokens": 8192}) == int(8192 * 0.8)
    assert cc.budget_for(replaying, {"context_tokens": True}) == 1000


def test_the_fold_point_keeps_the_last_exchanges_and_the_current_message():
    items = conversation(6, current=7)
    cut = cc.fold_point(items, 7, keep=2)
    assert items[cut] == items[12]  # the user message of turn 5
    assert cc.fold_point(conversation(2, current=3), 3, keep=2) is None
    with pytest.raises(ValueError):
        cc.fold_point(items, 7, keep=0)


def test_under_the_budget_nothing_is_folded():
    items = conversation(3, current=4)
    plan = cc.plan(items, current_turn_id=4, budget_tokens=10_000, engine="openai_compat")
    assert plan.record is None and plan.items == items


def test_without_a_budget_the_replay_is_untouched_but_records_are_dropped():
    items = conversation(2) + [{"role": "compaction", "summary": "s", "folded_through_turn_id": 1, "turn_id": 2}]
    assert cc.plan(items, current_turn_id=3, budget_tokens=None, engine="claude").items == conversation(2)


def test_over_the_budget_the_earlier_part_becomes_an_excerpt_summary():
    items = conversation(8, size=400, current=9)
    plan = cc.plan(items, current_turn_id=9, budget_tokens=1200, engine="openai_compat", keep=2)
    record = plan.record
    assert record["folded_through_turn_id"] == 6 and record["kept_exchanges"] == 2
    assert record["folded_messages"] == 12 and record["method"] == "excerpts"
    assert record["tokens_after"] < record["tokens_before"]
    # The replay: the summary on the first kept message, then turns 7, 8 and the new one verbatim.
    first = plan.items[0]
    assert first["role"] == "user" and first["text"].startswith(cc.SUMMARY_HEADER)
    assert first["text"].endswith(items[18]["text"])
    assert plan.items[-1]["text"] == "the new message"
    assert "[6 tool calls in this part, not shown]" in record["summary"]
    assert "Person: question 6" in record["summary"] and "Assistant: answer 6" in record["summary"]


def test_a_stored_record_is_replayed_unchanged_until_the_budget_is_reached_again():
    items = conversation(8, size=400, current=9)
    first = cc.plan(items, current_turn_id=9, budget_tokens=1200, engine="openai_compat", keep=2)
    stored = items + [{"role": "compaction", "turn_id": 9, **first.record}]
    stored.append({"role": "assistant", "text": "short", "turn_id": 9})
    stored.append({"role": "user", "text": "next", "turn_id": 10})
    again = cc.plan(stored, current_turn_id=10, budget_tokens=100_000, engine="openai_compat", keep=2)
    assert again.record is None
    assert again.items[0]["text"].startswith(first.record["summary"])
    # Folding again keeps the earlier summary at the head of the new one.
    tight = cc.plan(stored, current_turn_id=10, budget_tokens=700, engine="openai_compat", keep=1)
    assert tight.record["folded_through_turn_id"] == 8
    summary = tight.record["summary"]
    assert summary.startswith(cc.SUMMARY_HEADER) and summary.count(cc.SUMMARY_HEADER) == 1
    assert "Assistant: answer 8" in summary
    assert len(summary) <= 700


def test_a_record_that_no_longer_matches_is_ignored():
    items = conversation(2) + [{"role": "compaction", "summary": "s", "folded_through_turn_id": 99, "turn_id": 2}]
    rest, summary = cc.applied(items)
    assert summary is None and rest == conversation(2)


def test_the_summary_is_bounded_and_redacted():
    folded = [{"role": "user", "text": f"note {i} token=abcdef123456 " + "z" * 500, "turn_id": i} for i in range(100)]
    summary = cc.excerpt_summary(folded, max_chars=2_000)
    assert len(summary) <= 2_000 and "abcdef123456" not in summary
    assert "older excerpts left out]" in summary


def test_a_secret_cut_by_the_excerpt_limit_is_still_redacted():
    # A private key is longer than one excerpt: cut first, its END line is gone
    # and the block no longer looks like a key, so it must be redacted before.
    body = "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ" * 8
    key = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{body}\n-----END OPENSSH PRIVATE KEY-----"
    summary = cc.excerpt_summary([{"role": "user", "text": f"here is the key\n{key}", "turn_id": 1}])
    assert "BEGIN OPENSSH PRIVATE KEY" not in summary
    assert "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU" not in summary


def test_a_private_key_longer_than_the_scan_bound_is_still_redacted():
    body = "private-key-material-" * cc.REDACT_SCAN_CHARS
    key = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{body}\n-----END OPENSSH PRIVATE KEY-----"
    summary = cc.excerpt_summary([{"role": "user", "text": key, "turn_id": 1}])
    assert "BEGIN OPENSSH PRIVATE KEY" not in summary
    assert "private-key-material" not in summary


def test_a_long_message_is_redacted_only_up_to_the_bound(monkeypatch):
    # Redaction is slow on long unbroken text, so only the start of a message
    # is scanned; a key that starts in the excerpt is still caught.
    body = "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ" * 8
    key = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{body}\n-----END OPENSSH PRIVATE KEY-----"
    text = f"key {key} " + "a-b.c" * 200_000
    seen = []

    def recording(value):
        seen.append(len(value))
        return redact_secrets(value)

    monkeypatch.setattr(cc, "redact_secrets", recording)
    summary = cc.excerpt_summary([{"role": "user", "text": text, "turn_id": 1}])
    assert "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU" not in summary
    assert max(seen) <= max(cc.REDACT_SCAN_CHARS, cc.SUMMARY_MAX_CHARS)


def test_only_the_kept_exchanges_left_is_no_fold():
    items = [{"role": "user", "text": "x" * 10_000, "turn_id": 1}, {"role": "user", "text": "y", "turn_id": 2}]
    assert cc.plan(items, current_turn_id=2, budget_tokens=10, engine="openai_compat", keep=1).record is None


def test_fewer_exchanges_are_kept_when_four_would_leave_no_headroom():
    items = conversation(10, size=400, current=11)
    plan = cc.plan(items, current_turn_id=11, budget_tokens=900, engine="openai_compat")
    assert plan.record["kept_exchanges"] < cc.KEEP_EXCHANGES
    assert plan.record["tokens_after"] <= 900 * cc.TARGET_SHARE
    # The next turn, a little longer, replays the stored summary without folding again.
    stored = items + [{"role": "compaction", "turn_id": 11, **plan.record}, {"role": "assistant", "text": "ok", "turn_id": 11}]
    stored.append({"role": "user", "text": "short", "turn_id": 12})
    assert cc.plan(stored, current_turn_id=12, budget_tokens=900, engine="openai_compat").record is None
