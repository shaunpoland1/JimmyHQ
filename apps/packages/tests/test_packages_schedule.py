"""When a check is due and when Refresh now may go: the pure rules, with no clock and no
store. Every time is unix milliseconds in UTC; the owner's time zone only says when "every
day at 06:00" falls."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from packages import schedule
from packages.schedule import DAY, HOUR, MINUTE

UTC_ZONE = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


def due(**more):
    given = {"last_ok": ms("2027-01-15T08:00:00"), "last_try": ms("2027-01-15T08:00:00"),
             "fails": 0, "interval_hours": 4, "clock": (6, 0), "retry_minutes": 15,
             "longest_minutes": 240, "zone": UTC_ZONE}
    given.update(more)
    return schedule.due_ms(**given)


def test_a_time_such_as_06_00_is_read_and_nothing_means_no_daily_check():
    assert schedule.read_clock("06:00") == ((6, 0), "")
    assert schedule.read_clock("6:30") == ((6, 30), "")
    assert schedule.read_clock("") == (None, "")
    assert schedule.read_clock("  off ") == (None, "")


def test_a_daily_time_that_cannot_be_read_falls_back_to_the_default_and_says_so():
    clock, said = schedule.read_clock("25:99")
    assert clock == (6, 0)
    assert "25:99" in said and "06:00" in said
    assert schedule.read_clock("soon")[0] == (6, 0)


def test_the_next_daily_time_is_the_first_one_after_the_moment_given():
    after = ms("2027-01-15T08:00:00")
    assert schedule.next_daily(after, (6, 0), UTC_ZONE) == ms("2027-01-16T06:00:00")
    assert schedule.next_daily(ms("2027-01-15T05:00:00"), (6, 0), UTC_ZONE) == ms(
        "2027-01-15T06:00:00")
    assert schedule.next_daily(ms("2027-01-15T06:00:00"), (6, 0), UTC_ZONE) == ms(
        "2027-01-16T06:00:00")  # strictly after


def test_the_daily_time_is_read_in_the_owners_time_zone():
    after = ms("2027-01-15T08:00:00")  # 03:00 in New York
    assert schedule.next_daily(after, (6, 0), NEW_YORK) == ms("2027-01-15T11:00:00")


def test_the_daily_time_follows_the_clock_across_a_summer_time_change():
    after = ms("2027-03-13T12:00:00")  # the day before the clocks go forward in New York
    first = schedule.next_daily(after, (6, 0), NEW_YORK)
    second = schedule.next_daily(first, (6, 0), NEW_YORK)
    assert first == ms("2027-03-14T10:00:00")  # 06:00 is already summer time that day
    assert second - first == DAY


def test_a_first_check_is_due_at_once():
    assert due(last_ok=None, last_try=None) == 0


def test_after_a_good_check_the_next_is_due_one_interval_later():
    assert due(clock=None) == ms("2027-01-15T08:00:00") + 4 * HOUR


def test_the_daily_time_can_come_before_the_interval_is_up():
    assert due(interval_hours=48) == ms("2027-01-16T06:00:00")
    assert due(interval_hours=2) == ms("2027-01-15T10:00:00")  # the interval is sooner


def test_after_a_failed_check_the_next_try_waits_the_retry_time_not_the_interval():
    tried = ms("2027-01-15T08:00:00")
    assert due(fails=1, last_try=tried) == tried + 15 * MINUTE


def test_each_failure_in_a_row_doubles_the_wait_up_to_the_longest():
    waits = [schedule.retry_wait_ms(n, 15, 240) // MINUTE for n in range(1, 8)]
    assert waits == [15, 30, 60, 120, 240, 240, 240]


def test_the_longest_wait_is_never_shorter_than_the_first_retry():
    assert schedule.retry_wait_ms(1, 30, 10) == 30 * MINUTE


def test_refresh_now_waits_out_the_gap_since_the_last_request():
    now = ms("2027-01-15T08:00:00")
    assert schedule.refresh_wait_ms(now, now - 3 * MINUTE, 10) == 7 * MINUTE
    assert schedule.refresh_wait_ms(now, now - 10 * MINUTE, 10) == 0
    assert schedule.refresh_wait_ms(now, None, 10) == 0
    assert schedule.refresh_wait_ms(now, now - 1, 0) == 0


def test_a_wait_is_said_in_minutes_and_hours_rounded_up():
    assert schedule.span(30_000) == "less than a minute"
    assert schedule.span(61_000) == "2 minutes"
    assert schedule.span(7 * MINUTE) == "7 minutes"
    assert schedule.span(3 * HOUR) == "3 hours"
    assert schedule.span(5 * DAY) == "5 days"
