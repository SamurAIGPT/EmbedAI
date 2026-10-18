"""Context compaction for engines that replay the transcript (docs/MEMORY.md).

An OpenAI-compatible endpoint keeps no session, so every turn sends the whole
stored conversation; an ACP agent that cannot reload its session is sent the
conversation as a preamble. A long conversation would eventually not fit, and
the adapters would silently drop the oldest messages. Instead, before such a
replay grows past the engine's budget, the earlier part is folded:

- the fold point is deterministic: the last `KEEP_EXCHANGES` exchanges (a
  person's message and everything after it) stay verbatim, and so does the
  current message; everything before is folded;
- the folded part becomes a summary made without a model call: the newest
  excerpts of what was said, oldest first, within `SUMMARY_MAX_CHARS`, with
  any earlier summary kept at its head. Excerpts, not a rewrite: nothing is
  invented, and later messages override earlier ones;
- the summary is stored as a `context.compacted` event in the turn that folded
  it, so later turns replay exactly the same summary (stable for prompt
  caching) until the conversation outgrows the budget again, and the
  transcript shows an "Earlier messages summarized" marker. The full
  conversation stays stored and visible.

Claude and Codex keep their own sessions and their CLI's own compaction; they
never call the history, so nothing here touches them. An engine opts in with
`replays_history = True`.

Token budget: tokens are estimated from characters with a per-engine ratio
(`CHARS_PER_TOKEN`). The budget is the engine's `compact_at_tokens`, or 80%
of `context_tokens` when the engine's saved configuration gives its model's
context window.
"""

from dataclasses import dataclass

from app.services.agent_memory import redact_secrets

KEEP_EXCHANGES = 4
SUMMARY_MAX_CHARS = 6_000
EXCERPT_MAX_CHARS = 240
# The summary takes at most this share of the budget (and SUMMARY_MAX_CHARS).
SUMMARY_SHARE = 0.25
SUMMARY_MIN_CHARS = 512
DEFAULT_BUDGET_TOKENS = 24_000
MIN_BUDGET_TOKENS = 512
# Characters per token, by engine. Conservative: English prose is about four;
# code and other scripts are fewer, so an ACP agent (often coding) is lower.
CHARS_PER_TOKEN = {"openai_compat": 4.0, "acp": 3.5}
DEFAULT_CHARS_PER_TOKEN = 4.0
WINDOW_SHARE = 0.8
# A fold aims to leave the replay under this share of the budget.
TARGET_SHARE = 0.7

SUMMARY_HEADER = (
    "[Summary of earlier messages in this conversation, made of excerpts, oldest first. "
    "Background, not new instructions: later messages override earlier ones, and details may be "
    "missing. The full conversation is still stored.]"
)


@dataclass
class Plan:
    items: list[dict]
    # Set when this replay folded more of the conversation: the data of the
    # `context.compacted` event to store.
    record: dict | None = None


def chars_per_token(engine: str | None) -> float:
    return CHARS_PER_TOKEN.get(engine or "", DEFAULT_CHARS_PER_TOKEN)


def estimate_tokens(items: list[dict], engine: str | None, extra_chars: int = 0) -> int:
    """Estimated tokens of a replay: the text of every message, plus a few
    tokens of overhead per message."""
    chars = extra_chars
    count = 0
    for item in items:
        text = item.get("text")
        if item.get("role") in ("user", "assistant") and isinstance(text, str):
            chars += len(text)
            count += 1
    return int(chars / chars_per_token(engine)) + 4 * count


def budget_for(adapter, config: dict | None) -> int | None:
    """The token budget past which this engine's replay is folded; None for
    an engine that does not replay the transcript (it compacts on its own)."""
    if not getattr(adapter, "replays_history", False):
        return None
    window = (config or {}).get("context_tokens")
    if isinstance(window, int) and not isinstance(window, bool) and window > 0:
        return max(MIN_BUDGET_TOKENS, int(window * WINDOW_SHARE))
    return int(getattr(adapter, "compact_at_tokens", DEFAULT_BUDGET_TOKENS))


def _turn_end_index(items: list[dict], turn_id) -> int:
    """Index of the last item of that turn, or -1."""
    for index in range(len(items) - 1, -1, -1):
        if items[index].get("turn_id") == turn_id and items[index].get("role") != "compaction":
            return index
    return -1


def applied(items: list[dict]) -> tuple[list[dict], str | None]:
    """The replay as the newest stored compaction left it: the items after
    the folded turn, and the summary (None when nothing was folded or the
    record no longer matches the conversation)."""
    record = next((i for i in reversed(items) if i.get("role") == "compaction"), None)
    rest = [i for i in items if i.get("role") != "compaction"]
    if record is None:
        return rest, None
    end = _turn_end_index(rest, record.get("folded_through_turn_id"))
    if end < 0 or not isinstance(record.get("summary"), str):
        return rest, None
    return rest[end + 1 :], record["summary"]


def fold_point(items: list[dict], current_turn_id, keep: int = KEEP_EXCHANGES) -> int | None:
    """Index of the first item kept verbatim: the person's message that opens
    the `keep`-th newest exchange before the current one. None when there is
    not more than that to fold."""
    if keep < 1:
        raise ValueError("keep at least one exchange")
    users = [
        i for i, item in enumerate(items)
        if item.get("role") == "user" and item.get("turn_id") != current_turn_id
    ]
    if len(users) <= keep:
        return None
    cut = users[-keep]
    return cut if cut > 0 else None


def excerpt_summary(folded: list[dict], previous: str | None = None, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """The deterministic summary: the newest excerpts that fit, oldest first,
    after any earlier summary. Tool calls are counted, never quoted."""
    lines: list[str] = []
    room = max(0, max_chars - len(SUMMARY_HEADER) - 96)
    prior = ""
    if previous:
        body = previous.replace(SUMMARY_HEADER, "").strip()
        limit = room // 3
        prior = body if len(body) <= limit else "…" + body[-max(1, limit - 1):]
        room -= len(prior) + 1
    omitted = 0
    tools = 0
    for item in reversed(folded):
        role, text = item.get("role"), item.get("text")
        if role == "tool":
            tools += 1
            continue
        if role not in ("user", "assistant") or not isinstance(text, str) or not text.strip():
            continue
        flat = " ".join(text.split())
        if len(flat) > EXCERPT_MAX_CHARS:
            flat = flat[: EXCERPT_MAX_CHARS - 1] + "…"
        line = f"{'Person' if role == 'user' else 'Assistant'}: {flat}"
        if len(line) + 1 > room:
            omitted += 1
            continue
        lines.insert(0, line)
        room -= len(line) + 1
    head = [SUMMARY_HEADER]
    if prior:
        head.append(prior)
    if omitted:
        head.append(f"[{omitted} older excerpt{'s' if omitted != 1 else ''} left out]")
    if tools:
        head.append(f"[{tools} tool call{'s' if tools != 1 else ''} in this part, not shown]")
    text, _ = redact_secrets("\n".join(head + lines))
    return text


def with_summary(items: list[dict], summary: str | None) -> list[dict]:
    """The replay items with the summary put at the head of the first message
    of the person's (a chat must start with the person, so the summary rides
    on that message rather than standing alone)."""
    if not summary:
        return items
    out = [dict(i) for i in items]
    for item in out:
        if item.get("role") == "user":
            item["text"] = f"{summary}\n\n{item.get('text') or ''}"
            return out
    return [{"role": "user", "text": summary, "turn_id": None}] + out


def plan(
    items: list[dict],
    *,
    current_turn_id,
    budget_tokens: int | None,
    engine: str | None,
    extra_chars: int = 0,
    keep: int = KEEP_EXCHANGES,
) -> Plan:
    """What to replay this turn, and the record to store when it folded."""
    rest, summary = applied(items)
    if budget_tokens is None:
        return Plan(items=[i for i in items if i.get("role") != "compaction"])
    before = estimate_tokens(with_summary(rest, summary), engine, extra_chars)
    if before < budget_tokens:
        return Plan(items=with_summary(rest, summary))
    room = int(budget_tokens * chars_per_token(engine) * SUMMARY_SHARE)
    room = max(SUMMARY_MIN_CHARS, min(SUMMARY_MAX_CHARS, room))
    # Keep `keep` exchanges, or fewer when that many would not leave the
    # replay under TARGET_SHARE of the budget: a fold that does not leave
    # headroom would fold again on every turn and the replay would never be
    # stable. Deterministic: the same conversation always folds the same way.
    chosen = None
    for kept_count in range(keep, 0, -1):
        cut = fold_point(rest, current_turn_id, kept_count)
        if cut is None:
            continue
        folded, kept = rest[:cut], rest[cut:]
        folded_through = next((i.get("turn_id") for i in reversed(folded) if i.get("turn_id") is not None), None)
        if folded_through is None:
            continue
        new_summary = excerpt_summary(folded, summary, room)
        replay = with_summary(kept, new_summary)
        chosen = (kept_count, folded, folded_through, new_summary, replay)
        if estimate_tokens(replay, engine, extra_chars) <= budget_tokens * TARGET_SHARE:
            break
    if chosen is None:
        # Only the kept exchanges are left and they are too large by
        # themselves: the adapter's own size limit applies.
        return Plan(items=with_summary(rest, summary))
    keep, folded, folded_through, new_summary, replay = chosen
    record = {
        "folded_through_turn_id": folded_through,
        "summary": new_summary,
        "folded_messages": sum(1 for i in folded if i.get("role") in ("user", "assistant")),
        "kept_exchanges": keep,
        "tokens_before": before,
        "tokens_after": estimate_tokens(replay, engine, extra_chars),
        "budget_tokens": budget_tokens,
        "method": "excerpts",
        "engine": engine,
    }
    return Plan(items=replay, record=record)
