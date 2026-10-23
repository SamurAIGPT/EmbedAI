"""The pure parts of the nightly tidy-up and the organizing pass (docs/MEMORY.md).

*Organizing* moves lines of MEMORY.md that are detail about one subject (a
person, a client, a trip, likes in one domain) into that subject's topic note,
unchanged; core facts about the person always stay, and a line about diet,
allergies or health is never even offered (a fixed rule, not the model's
choice). At most `MAX_MOVES` move in a pass.

*Tidying* needs no model for two steps: expired entries move to
`memory/archive.md`, and of two live entries with exactly the same fact the
newer stays. A model call then looks for pairs that cannot both be true and the
older is struck through (`~~old~~ · superseded <day>`), never deleted; a part of
that line that is still true is kept as its own entry `from tidy-up`. At most a
fifth of the live entries can be struck in one pass in MEMORY.md, and none in a
file of fewer than `MIN_ENTRIES` (topic notes may reconcile one pair).

Everything here takes text and returns text; the writes, the journal and the
model calls are in memory_capture.MemoryCapture.
"""

import json
import re
from dataclasses import dataclass, field

from app.services import memory_entries
from app.services.agent_memory import redact_secrets
from app.services.memory_entries import Entry

MAX_MOVES = 20
ORGANIZE_ENTRIES = 60
TIDY_ENTRIES = 200
CONTRADICTION_SHARE = 0.2
MIN_ENTRIES = 5
RESERVED = {"archive.md", "log.md", "memory.md"}


def _json(text: str, opener: str, closer: str):
    body = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", body)
    body = (fenced.group(1) if fenced else body).strip()
    start, end = body.find(opener), body.rfind(closer)
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(body[start : end + 1])
    except ValueError:
        return None


# --- organizing -------------------------------------------------------------------------


@dataclass(frozen=True)
class Move:
    entry: Entry
    topic: str
    aliases: tuple[str, ...]


def organize_candidates(text: str, today: str, core: set[str]) -> list[Entry]:
    """Live entries not judged core before and not health or safety facts."""
    return [
        e for e in memory_entries.parse(text)
        if not e.struck and not memory_entries.is_expired(e, today)
        and memory_entries.identity(e.body) not in core and not memory_entries.always_core(e.body)
    ][:ORGANIZE_ENTRIES]


def organize_prompt(candidates: list[Entry], topics: str) -> str:
    listed = "\n".join(f"[{i}] {redact_secrets(e.body)[0]}" for i, e in enumerate(candidates)) or "(nothing)"
    return "\n".join([
        "You tidy an assistant's core notebook. You have no tools: read only what is below.",
        "",
        "The core notebook, loaded into every conversation, holds these numbered lines:",
        listed,
        "",
        f"Topic notes it already keeps:\n{topics.strip() or '(none yet)'}",
        "",
        "Core, never moved: facts about the person themselves that matter in any conversation (their name, "
        "where they live now, their company and role, their diet, allergies and health needs, how they like "
        "to be spoken to) and standing decisions.",
        "Move a line only when it is detail about one particular subject: another person, a project or client, "
        "a trip or place, likes and dislikes in one domain. Reuse an existing topic name when one fits; otherwise "
        "name a new one in one or two words. When in doubt, keep the line in the notebook.",
        "For each line to move give its number, the topic, and aliases (two to five other words someone might use "
        "when asking about that subject). Answer with one JSON object and nothing else:",
        '{"moves": [{"i": <number>, "topic": "name", "aliases": ["..."]}]}',
        'When every line is core, answer {"moves": []}.',
    ])


def topic_file(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    base = re.sub(r"[^a-z0-9]+", "-", re.sub(r"\.md$", "", raw.strip(), flags=re.I).lower()).strip("-")[:40].strip("-")
    name = f"{base}.md" if base else None
    return None if name is None or name in RESERVED else name


def parse_moves(answer: str, candidates: list[Entry]) -> list[Move] | None:
    """The moves the answer asks for, or None when it is not an answer to
    trust (malformed, or naming a line that does not exist)."""
    parsed = _json(answer, "{", "}")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("moves"), list):
        return None
    from app.services import memory_topics

    out: list[Move] = []
    seen: set[int] = set()
    for item in parsed["moves"]:
        if not isinstance(item, dict):
            return None
        i, name = item.get("i"), topic_file(item.get("topic"))
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(candidates) or name is None:
            return None
        if i in seen:
            continue
        seen.add(i)
        aliases = item.get("aliases")
        out.append(Move(candidates[i], name, tuple(memory_topics.clean_aliases(aliases if isinstance(aliases, list) else []))))
    return out


def apply_moves(text: str, moves: list[Move]) -> str:
    gone = {i for m in moves for i in range(m.entry.start, m.entry.end)}
    return "\n".join(line for i, line in enumerate(text.split("\n")) if i not in gone)


# --- tidying ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Pair:
    a: int
    b: int
    keep: str
    remainder: str | None = None


@dataclass
class Plan:
    expired: list[Entry] = field(default_factory=list)
    duplicates: list[Entry] = field(default_factory=list)
    superseded: list[tuple[Entry, Entry, str | None]] = field(default_factory=list)  # loser, winner, remainder
    deferred: int = 0

    @property
    def changes(self) -> int:
        return len(self.expired) + len(self.duplicates) + len(self.superseded)


def _duplicates(live: list[Entry]) -> list[Entry]:
    groups: dict[str, list[Entry]] = {}
    for e in live:
        key = memory_entries.identity(e.body)
        if key:
            groups.setdefault(key, []).append(e)
    older: list[Entry] = []
    for group in groups.values():
        if len(group) > 1:
            newest_first = sorted(group, key=lambda e: (e.day, e.start), reverse=True)
            older.extend(newest_first[1:])
    return older


def contradiction_candidates(text: str, today: str) -> list[Entry]:
    live = [e for e in memory_entries.parse(text) if not e.struck and not memory_entries.is_expired(e, today)]
    dupes = {e.start for e in _duplicates(live)}
    return [e for e in live if e.start not in dupes]


def contradiction_prompt(candidates: list[Entry]) -> str:
    listed = "\n".join(f"[{i}] ({e.day}) {redact_secrets(e.body)[0]}" for i, e in enumerate(candidates[:TIDY_ENTRIES])) or "(nothing)"
    return "\n".join([
        "You tidy an assistant's notebook. You have no tools: read only the numbered lines below, each with the "
        "date it was written.",
        "",
        listed,
        "",
        "Find pairs of lines that cannot both be true now: a direct contradiction about the same thing (a changed "
        "number, a new city, a reversed preference), not a refinement, not two different topics, not two facts that "
        "can both hold. Numbers, signs and symbols matter: \"balance is -10\" and \"balance is 10\" contradict; "
        "\"uses C\" and \"uses C++\" may both be true.",
        "For each pair say which to keep: the later line unless the earlier one is clearly the correction. When the "
        "line that loses also states other facts that are still true, give them as remainder, one sentence, worded "
        "as in the line; otherwise omit remainder. Answer with one JSON object and nothing else:",
        '{"pairs": [{"a": <index>, "b": <index>, "keep": "a"|"b", "remainder": "optional"}]}',
        'An empty list {"pairs": []} is the normal answer. Never invent a contradiction.',
    ])


def parse_pairs(answer: str, count: int) -> list[Pair]:
    parsed = _json(answer, "{", "}")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("pairs"), list):
        return []
    out: list[Pair] = []
    seen: set[tuple[int, int]] = set()
    for item in parsed["pairs"]:
        if not isinstance(item, dict):
            continue
        a, b, keep = item.get("a"), item.get("b"), item.get("keep")
        if any(isinstance(x, bool) or not isinstance(x, int) for x in (a, b)) or a == b:
            continue
        if not (0 <= a < count and 0 <= b < count) or keep not in ("a", "b"):
            continue
        key = (min(a, b), max(a, b))
        if key in seen:
            continue
        seen.add(key)
        rest = item.get("remainder")
        rest = " ".join(rest.replace("~~", "").split())[:300] if isinstance(rest, str) else ""
        out.append(Pair(a, b, keep, rest or None))
    return out


def plan(text: str, today: str, pairs: list[Pair] | None = None, *, minimum_budget: int = 0) -> Plan:
    entries = memory_entries.parse(text)
    # A health or safety fact is never archived, whatever its `until`.
    expired = [e for e in entries if memory_entries.is_expired(e, today) and not memory_entries.always_core(e.body)]
    live = [e for e in entries if not e.struck and not memory_entries.is_expired(e, today)]
    result = Plan(expired=expired, duplicates=_duplicates(live))
    candidates = contradiction_candidates(text, today)
    budget = max(minimum_budget, int(len(live) * CONTRADICTION_SHARE))
    touched: set[int] = set()
    for pair in pairs or []:
        a, b = candidates[pair.a], candidates[pair.b]
        winner, loser = (a, b) if pair.keep == "a" else (b, a)
        if memory_entries.always_core(loser.body):
            continue  # a health or safety fact is for the person to settle, never the model
        if winner.start in touched or loser.start in touched:
            continue
        if len(result.superseded) >= budget:
            result.deferred += 1
            continue
        result.superseded.append((loser, winner, pair.remainder))
        touched.update((loser.start, winner.start))
    return result


def apply(text: str, plan_: Plan, today: str) -> tuple[str, list[str]]:
    """The tidied text and the archive lines (expired entries, each marked)."""
    lines = text.split("\n")
    drop = {i for e in [*plan_.expired, *plan_.duplicates] for i in range(e.start, e.end)}
    for loser, _winner, remainder in plan_.superseded:
        lines[loser.start] = f"{loser.prefix}~~{loser.body}~~ · superseded {today}"
        if remainder:  # after the struck entry's own continuation lines
            lines[loser.end - 1] += f"\n- {today} · from tidy-up · {remainder}"
    # the first line of an archived entry carries the mark
    archived = [_mark_first(text, e, today) for e in plan_.expired]
    return "\n".join(line for i, line in enumerate(lines) if i not in drop), archived


def lines_of(text: str, e: Entry) -> list[str]:
    return text.split("\n")[e.start : e.end]


def _mark_first(text: str, e: Entry, today: str) -> str:
    block = lines_of(text, e)
    block[0] = f"{block[0]} · expired {today}"
    return "\n".join(block)
