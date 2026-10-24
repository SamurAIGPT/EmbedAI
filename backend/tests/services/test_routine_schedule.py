"""Schedule kinds, next dates, timezones and clock changes, with fixed clocks."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services import routine_schedule as s

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
BERLIN = ZoneInfo("Europe/Berlin")


def local(moment, tz=BERLIN):
    return moment.astimezone(tz).replace(tzinfo=None)


def test_cron_next_three_dates_in_a_timezone():
    spec = s.normalize("cron", {"expression": "0 9 * * 1-5"}, "Europe/Berlin", now=NOW)
    dates = s.next_dates("cron", spec, "Europe/Berlin", NOW)
    # Sat 17 Oct 2026: next weekdays are Mon 19, Tue 20, Wed 21 at 09:00 Berlin.
    assert [local(d) for d in dates] == [datetime(2026, 10, d, 9, 0) for d in (19, 20, 21)]
    assert all(d.tzinfo is not None for d in dates)


def test_cron_sunday_is_zero_like_standard_cron():
    spec = s.normalize("cron", {"expression": "30 8 * * 0"}, "UTC", now=NOW)
    first = s.next_after("cron", spec, "UTC", NOW)
    assert first == datetime(2026, 10, 18, 8, 30, tzinfo=UTC) and first.weekday() == 6


def test_cron_either_day_field_matches():
    spec = s.normalize("cron", {"expression": "0 9 1 * 1"}, "UTC", now=NOW)
    dates = s.next_dates("cron", spec, "UTC", NOW, count=3)
    assert [d.day for d in dates] == [19, 26, 1]  # Mondays, then the 1st of November


def test_weekdays_kind_matches_its_days_and_time():
    spec = s.normalize("weekdays", {"days": [6, 0], "time": "07:15"}, "America/New_York", now=NOW)
    dates = s.next_dates("weekdays", spec, "America/New_York", NOW)
    ny = ZoneInfo("America/New_York")
    assert [local(d, ny) for d in dates] == [
        datetime(2026, 10, 18, 7, 15), datetime(2026, 10, 24, 7, 15), datetime(2026, 10, 25, 7, 15)
    ]
    assert s.summary("weekdays", spec, "America/New_York") == "Weekends at 07:15 (America/New_York)"


def test_interval_counts_elapsed_minutes_from_its_anchor():
    spec = s.normalize("interval", {"every_minutes": 90, "anchor": "2026-10-17T08:00:00Z"}, "UTC", now=NOW)
    assert s.next_dates("interval", spec, "UTC", NOW) == [
        datetime(2026, 10, 17, 12, 30, tzinfo=UTC),
        datetime(2026, 10, 17, 14, 0, tzinfo=UTC),
        datetime(2026, 10, 17, 15, 30, tzinfo=UTC),
    ]
    assert s.summary("interval", spec, "UTC") == "Every 90 minutes"


def test_interval_without_anchor_starts_from_now():
    spec = s.normalize("interval", {"every_minutes": 60}, "UTC", now=NOW + timedelta(seconds=42))
    assert spec["anchor"] == NOW.isoformat()


def test_once_reads_a_naive_time_in_the_routine_zone():
    spec = s.normalize("once", {"at": "2026-10-18T09:00"}, "Asia/Kolkata", now=NOW)
    assert s.next_after("once", spec, "Asia/Kolkata", NOW) == datetime(2026, 10, 18, 3, 30, tzinfo=UTC)
    assert s.next_after("once", spec, "Asia/Kolkata", datetime(2026, 10, 18, 4, tzinfo=UTC)) is None


def test_manual_never_runs_on_its_own():
    assert s.next_after("manual", {}, "UTC", NOW) is None
    assert s.summary("manual", {}, "UTC").startswith("Only when run")


@pytest.mark.parametrize(
    "kind, spec, message",
    [
        ("cron", {"expression": "0 9 * *"}, "five fields"),
        ("cron", {"expression": "61 9 * * *"}, "Invalid cron"),
        ("weekdays", {"days": [], "time": "09:00"}, "at least one day"),
        ("weekdays", {"days": [7], "time": "09:00"}, "from 0"),
        ("weekdays", {"days": [1], "time": "9am"}, "HH:MM"),
        ("interval", {"every_minutes": 1}, "from 5"),
        ("once", {"at": "tomorrow"}, "ISO"),
        ("hourly", {}, "kind must be"),
    ],
)
def test_invalid_specs_say_what_is_wrong(kind, spec, message):
    with pytest.raises(s.InvalidSchedule, match=message):
        s.normalize(kind, spec, "UTC", now=NOW)


def test_unknown_timezone_is_refused():
    with pytest.raises(s.InvalidSchedule, match="Unknown timezone"):
        s.normalize("cron", {"expression": "0 9 * * *"}, "Mars/Olympus", now=NOW)


def test_spring_forward_gap_runs_after_the_gap():
    spec = s.normalize("cron", {"expression": "30 2 * * *"}, "Europe/Berlin", now=NOW)
    before = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    first, second = s.next_dates("cron", spec, "Europe/Berlin", before, count=2)
    assert local(first) == datetime(2026, 3, 29, 3, 30)  # 02:30 does not exist that night
    assert local(second) == datetime(2026, 3, 30, 2, 30)


def test_repeated_autumn_hour_runs_once_at_its_first_occurrence():
    spec = s.normalize("cron", {"expression": "30 2 * * *"}, "Europe/Berlin", now=NOW)
    before = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    first, second = s.next_dates("cron", spec, "Europe/Berlin", before, count=2)
    assert first == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)  # 02:30 CEST, the first one
    assert local(second) == datetime(2026, 10, 26, 2, 30)  # not 02:30 CET the same night


def test_frequent_cron_skips_the_repeated_wall_clock_times():
    spec = s.normalize("cron", {"expression": "*/30 * * * *"}, "Europe/Berlin", now=NOW)
    dates = s.next_dates("cron", spec, "Europe/Berlin", datetime(2026, 10, 24, 23, 50, tzinfo=UTC), count=4)
    assert [d.strftime("%H:%M") for d in dates] == ["00:00", "00:30", "02:00", "02:30"]  # UTC


def test_interval_ignores_clock_changes():
    spec = s.normalize("interval", {"every_minutes": 60, "anchor": "2026-10-25T00:00:00Z"}, "Europe/Berlin", now=NOW)
    dates = s.next_dates("interval", spec, "Europe/Berlin", datetime(2026, 10, 25, 0, 0, tzinfo=UTC))
    assert [d - dates[0] for d in dates] == [timedelta(0), timedelta(hours=1), timedelta(hours=2)]


def test_latest_due_collapses_a_backlog():
    spec = s.normalize("cron", {"expression": "* * * * *"}, "UTC", now=NOW)
    first = datetime(2026, 10, 17, 11, 50, tzinfo=UTC)
    latest, passed = s.latest_due("cron", spec, "UTC", first, NOW + timedelta(seconds=20))
    assert latest == NOW and passed == 10
    interval = s.normalize("interval", {"every_minutes": 15, "anchor": "2026-10-17T10:00:00Z"}, "UTC", now=NOW)
    latest, passed = s.latest_due("interval", interval, "UTC", datetime(2026, 10, 17, 10, 0, tzinfo=UTC), NOW)
    assert latest == NOW and passed == 8


@pytest.mark.parametrize(
    "expression, text",
    [
        ("* * * * *", "Every minute (UTC)"),
        ("*/15 * * * *", "Every 15 minutes (UTC)"),
        ("5 * * * *", "Every hour at :05 (UTC)"),
        ("0 9 * * *", "Every day at 09:00 (UTC)"),
        ("0 9 * * 1-5", "Mon–Fri at 09:00 (UTC)"),
        ("0 9 * * Mon-Fri", "Mon–Fri at 09:00 (UTC)"),
        ("0 9 * * MON-FRI", "Mon–Fri at 09:00 (UTC)"),
        ("0 18 * * 0,6", "Weekends at 18:00 (UTC)"),
        ("0 9 1 * *", "Monthly on day 1 at 09:00 (UTC)"),
        ("0 9 1 1 *", "Cron 0 9 1 1 * (UTC)"),
    ],
)
def test_cron_summaries(expression, text):
    assert s.summary("cron", {"expression": expression}, "UTC") == text


@pytest.mark.parametrize("zone,expression,after,expected", [
    ("Australia/Lord_Howe", "15 2 * * *", "2026-10-03T15:30:00+00:00", "2026-10-03T15:45:00+00:00"),
    ("Australia/Lord_Howe", "15 2 * * *", "2026-10-03T15:44:59+00:00", "2026-10-03T15:45:00+00:00"),
    ("Australia/Lord_Howe", "15 2 * * *", "2026-10-03T15:45:00+00:00", "2026-10-04T15:15:00+00:00"),
    ("Pacific/Apia", "0 12 * * 5", "2011-12-30T10:00:00+00:00", "2011-12-30T22:00:00+00:00"),
    ("Pacific/Apia", "0 12 * * 5", "2011-12-30T22:00:00+00:00", "2012-01-05T22:00:00+00:00"),
    ("Europe/Berlin", "30 2 * * 1", "2026-03-29T01:00:00+00:00", "2026-03-30T00:30:00+00:00"),
    ("Europe/Berlin", "30 2 * * 0", "2026-03-29T01:00:00+00:00", "2026-03-29T01:30:00+00:00"),
    ("Europe/Berlin", "*/15 1,2 * * *", "2026-03-29T01:05:00+00:00", "2026-03-29T01:15:00+00:00"),
    ("Europe/Berlin", "* * * * *", "2026-03-29T01:05:00+00:00", "2026-03-29T01:06:00+00:00"),
], ids=["lord-howe-transition", "lord-howe-before-carried", "lord-howe-strict", "apia-skipped-friday", "apia-strict", "nonselected-monday", "selected-sunday", "multi-hour-cron", "dense-cron"])
def test_next_after_retains_carried_gap_occurrences(zone, expression, after, expected):
    now = datetime.fromisoformat(after)
    spec = s.normalize("cron", {"expression": expression}, zone, now=now)
    assert s.next_after("cron", spec, zone, now) == datetime.fromisoformat(expected)


@pytest.mark.parametrize("zone,expiry", [
    ("Europe/Berlin", "2026-03-29T02:00:00+00:00"),
    ("Australia/Lord_Howe", "2026-10-03T16:00:00+00:00"),
    ("Pacific/Apia", "2011-12-31T10:00:00+00:00"),
], ids=["berlin", "lord-howe", "apia"])
@pytest.mark.parametrize("side", [-1, 0, 1], ids=["before-expiry", "at-expiry", "after-expiry"])
def test_next_after_dense_gap_expiry_boundaries(zone, expiry, side):
    from datetime import timedelta

    end = datetime.fromisoformat(expiry)
    after = end + timedelta(microseconds=side)
    expected = end if side < 0 else end + timedelta(minutes=1)
    spec = s.normalize("cron", {"expression": "* * * * *"}, zone, now=after)
    assert s.next_after("cron", spec, zone, after) == expected
