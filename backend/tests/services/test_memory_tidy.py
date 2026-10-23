"""The pure parts of organizing and tidying (docs/MEMORY.md)."""

import json

from app.services import memory_entries, memory_tidy

TODAY = "2026-10-23"


def e(day, text, extra=""):
    return f'- {day} · from chat "A" · {text}{extra}'


def test_organize_never_offers_core_judged_or_health_lines():
    text = "\n".join([
        e("2026-01-01", "Asha's school is Greenfield"),
        e("2026-01-02", "The person is allergic to peanuts"),
        e("2026-01-03", "Old and expired", " · until 2026-02-01"),
        e("2026-01-04", "~~struck~~", " · superseded 2026-02-01").replace("· ~~", "· ~~"),
        e("2026-01-05", "The person lives in Pune"),
        "hand written line",
    ])
    cands = memory_tidy.organize_candidates(text, TODAY, {"The person lives in Pune"})
    assert [c.body for c in cands] == ["Asha's school is Greenfield"]


def test_moves_parse_strictly_and_apply_unchanged():
    cands = memory_entries.parse(e("2026-01-01", "Asha's school") + "\n" + e("2026-01-02", "Asha's birthday") + "\n")
    answer = '```json\n{"moves": [{"i": 0, "topic": "Asha", "aliases": ["sister", "a,b"]}, {"i": 0, "topic": "x"}]}\n```'
    (move,) = memory_tidy.parse_moves(answer, cands)
    assert move.topic == "asha.md" and move.aliases == ("sister", "a b")
    assert memory_tidy.parse_moves('{"moves": [{"i": 5, "topic": "x"}]}', cands) is None  # a line that is not there
    assert memory_tidy.parse_moves('{"moves": [{"i": 0, "topic": "archive"}]}', cands) is None  # reserved
    assert memory_tidy.parse_moves("no", cands) is None
    assert memory_tidy.parse_moves('{"moves": []}', cands) == []
    text = "\n".join(c.raw for c in cands)
    assert memory_tidy.apply_moves(text, [move]) == cands[1].raw


def test_plan_expires_dedupes_and_strikes_within_the_budget():
    lines = [
        e("2026-01-01", "Lives in Pune"),
        e("2026-02-01", "Lives in Mumbai"),
        e("2026-01-05", "Likes tea"),
        e("2026-03-01", "Likes tea."),  # same fact: the newer stays
        e("2026-01-06", "Meeting", " · until 2026-02-01"),
        e("2026-04-01", "Plain one"),
        e("2026-04-02", "Plain two"),
        e("2026-04-03", "Plain three"),
        e("2026-04-04", "Plain four"),
        e("2026-04-05", "Plain five"),
    ]
    text = "\n".join(lines) + "\n"
    cands = memory_tidy.contradiction_candidates(text, TODAY)
    assert [c.body for c in cands][:3] == ["Lives in Pune", "Lives in Mumbai", "Likes tea."]
    pair = memory_tidy.Pair(0, 1, "b", "The person prefers short replies")
    plan = memory_tidy.plan(text, TODAY, [pair, memory_tidy.Pair(0, 2, "a")])
    assert [x.body for x in plan.expired] == ["Meeting"]
    assert [x.body for x in plan.duplicates] == ["Likes tea"]
    assert [(l.body, w.body) for l, w, _ in plan.superseded] == [("Lives in Pune", "Lives in Mumbai")]  # 2nd pair touches a used line
    new, archived = memory_tidy.apply(text, plan, TODAY)
    assert archived == [lines[4] + f" · expired {TODAY}"]
    assert f'- 2026-01-01 · from chat "A" · ~~Lives in Pune~~ · superseded {TODAY}' in new
    assert f"- {TODAY} · from tidy-up · The person prefers short replies" in new
    assert "Meeting" not in new and new.count("Likes tea") == 1
    # superseded entries are skipped by every later reading
    assert "Lives in Pune" not in memory_entries.without_superseded(new)


def test_the_strike_budget_is_a_fifth_and_deferred_otherwise():
    text = "\n".join(e(f"2026-01-{n + 1:02d}", f"fact {n}") for n in range(10)) + "\n"
    pairs = [memory_tidy.Pair(0, 1, "b"), memory_tidy.Pair(2, 3, "b"), memory_tidy.Pair(4, 5, "b")]
    plan = memory_tidy.plan(text, TODAY, pairs)
    assert len(plan.superseded) == 2 and plan.deferred == 1
    assert len(memory_tidy.plan(text, TODAY, pairs, minimum_budget=3).superseded) == 3


def test_pairs_parse_defensively():
    answer = json.dumps({"pairs": [
        {"a": 0, "b": 1, "keep": "a", "remainder": "x ~~y~~"},
        {"a": 1, "b": 0, "keep": "b"},  # the same pair
        {"a": 0, "b": 0, "keep": "a"}, {"a": 0, "b": 9, "keep": "a"}, {"a": 0, "b": 1, "keep": "c"}, "junk",
    ]})
    assert memory_tidy.parse_pairs(answer, 3) == [memory_tidy.Pair(0, 1, "a", "x y")]
    assert memory_tidy.parse_pairs("nonsense", 3) == []
