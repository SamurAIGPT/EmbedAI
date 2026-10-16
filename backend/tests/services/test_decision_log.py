import json
import os
import stat
import sys

import pytest

from app.core.config import settings
from app.services import decision_log as log


def files(folder):
    return sorted(p.name for p in folder.iterdir())


def test_a_record_is_one_json_line_in_a_private_monthly_file(decision_log_dir):
    log.record("shown", thread_id=1, turn_id=2, approval_id="a1", tool="Bash", kind="command", summary="ls -la")
    [name] = files(decision_log_dir)
    assert name.startswith("decisions-") and name.endswith(".ndjson")
    path = decision_log_dir / name
    [line] = path.read_text().splitlines()
    entry = json.loads(line)
    assert (entry["event"], entry["thread_id"], entry["tool"], entry["summary"]) == ("shown", 1, "Bash", "ls -la")
    assert entry["id"] and entry["at"]
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(decision_log_dir).st_mode) == 0o700


def test_the_file_is_append_only(decision_log_dir):
    for index in range(3):
        log.record("denied", approval_id=f"a{index}", source="user")
    [name] = files(decision_log_dir)
    assert len((decision_log_dir / name).read_text().splitlines()) == 3


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "AKIAABCDEFGHIJKLMNOP",
        "xoxb-1234567890-abcdefghij",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
        "Bearer abcdefghijklmnop1234",
        "0123456789abcdef0123456789abcdef01234567",
    ],
)
def test_secrets_are_removed_from_what_is_written(decision_log_dir, secret):
    log.record("shown", tool="Bash", summary=f"curl -H 'x: {secret}' https://example.com", rule=secret)
    text = next(decision_log_dir.iterdir()).read_text()
    assert secret not in text and "[redacted]" in text


def test_assignments_with_secret_looking_names_are_redacted_but_the_name_stays(decision_log_dir):
    log.record("shown", summary="API_KEY=hunter2 npm run build && export password: 'p w'")
    entry = json.loads(next(decision_log_dir.iterdir()).read_text())
    assert "hunter2" not in entry["summary"] and "p w" not in entry["summary"]
    assert "API_KEY=" in entry["summary"] and "npm run build" in entry["summary"]


def test_a_summary_is_cut_short(decision_log_dir):
    log.record("shown", summary="a " * 1000)
    entry = json.loads(next(decision_log_dir.iterdir()).read_text())
    assert len(entry["summary"]) <= log.MAX_SUMMARY_CHARS


def test_an_unknown_event_is_a_programming_error():
    with pytest.raises(ValueError):
        log.record("approved")


def test_a_failing_write_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    monkeypatch.setattr(settings, "DECISION_LOG_DIR", str(blocker / "inside"))
    log.record("shown", summary="x")  # must not raise


def test_pages_come_newest_first_and_the_cursor_walks_back(decision_log_dir):
    for index in range(5):
        log.record("shown", approval_id=f"a{index}")
    first, cursor = log.read_page(limit=2)
    assert [e["approval_id"] for e in first] == ["a4", "a3"] and cursor == first[-1]["id"]
    second, cursor = log.read_page(limit=2, before=cursor)
    assert [e["approval_id"] for e in second] == ["a2", "a1"]
    last, cursor = log.read_page(limit=2, before=second[-1]["id"])
    assert [e["approval_id"] for e in last] == ["a0"] and cursor is None


def test_a_cursor_stays_valid_while_new_entries_arrive(decision_log_dir):
    for index in range(3):
        log.record("shown", approval_id=f"a{index}")
    first, cursor = log.read_page(limit=2)
    log.record("shown", approval_id="new")
    second, _ = log.read_page(limit=2, before=cursor)
    assert [e["approval_id"] for e in second] == ["a0"]


def test_filters_and_junk_lines(decision_log_dir):
    log.record("shown", thread_id=1)
    log.record("denied", thread_id=2)
    log.record("shown", thread_id=2)
    path = next(decision_log_dir.iterdir())
    with open(path, "a") as handle:
        handle.write("not json\n[1,2]\n{}\n")
    assert [e["event"] for e in log.read_page(event="denied")[0]] == ["denied"]
    assert len(log.read_page(thread_id=2)[0]) == 2
    assert len(log.read_page()[0]) == 3  # junk skipped


def test_reading_with_no_log_yet_is_empty(decision_log_dir):
    assert log.read_page() == ([], None)


def test_a_record_carries_its_source_rule_and_remembered_flag(decision_log_dir):
    log.record("allowed", source="user", rule=None, remembered=True)
    log.record("auto_allowed", source="saved_rule", rule="exact: ls")
    newest, older = log.read_page()[0]
    assert (newest["source"], newest["rule"], newest["remembered"]) == ("saved_rule", "exact: ls", None)
    assert (older["source"], older["remembered"]) == ("user", True)


def test_months_past_the_retention_window_are_deleted(decision_log_dir):
    from datetime import UTC, datetime

    decision_log_dir.mkdir(parents=True)
    for name in ("2026-01", "2026-04", "2026-07", "2026-09"):
        (decision_log_dir / f"decisions-{name}.ndjson").write_text("{}\n")
    (decision_log_dir / "notes.txt").write_text("keep")
    removed = log.prune(decision_log_dir, 180, now=datetime(2026, 10, 17, tzinfo=UTC))
    # January ended 1 Feb (258 days ago); April ended 1 May (169 days ago).
    assert removed == ["decisions-2026-01.ndjson"]
    assert files(decision_log_dir) == [
        "decisions-2026-04.ndjson", "decisions-2026-07.ndjson", "decisions-2026-09.ndjson", "notes.txt"
    ]
    assert log.prune(decision_log_dir, 100, now=datetime(2026, 10, 17, tzinfo=UTC)) == [
        "decisions-2026-04.ndjson"
    ]


def test_a_month_inside_the_window_is_kept_even_if_it_started_outside(decision_log_dir):
    from datetime import UTC, datetime

    decision_log_dir.mkdir(parents=True)
    (decision_log_dir / "decisions-2026-04.ndjson").write_text("{}\n")
    # April ended 30 April: on 20 Oct that is 173 days ago, inside 180.
    assert log.prune(decision_log_dir, 180, now=datetime(2026, 10, 20, tzinfo=UTC)) == []


def test_a_nonsense_retention_setting_falls_back(monkeypatch):
    for bad in (0, -5, 99999):
        monkeypatch.setattr(settings, "DECISION_LOG_RETENTION_DAYS", bad)
        assert log.retention_days() == 180
    monkeypatch.setattr(settings, "DECISION_LOG_RETENTION_DAYS", 30)
    assert log.retention_days() == 30
