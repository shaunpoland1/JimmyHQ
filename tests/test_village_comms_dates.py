"""The calendar check on day counts, and the comms settings."""

from __future__ import annotations

from datetime import date

from jimmyhq import settings as registry
from jimmyhq.village.comms import dates, limit
from jimmyhq.village.settings_comms import TABLE
from test_village_tools_fakes import v  # noqa: F401

SAT = date(2027, 3, 6)


def test_a_count_that_fits_none_of_the_dates_is_flagged_with_the_real_numbers():
    why = dates.slip("The Apr 1 email is three days out and needs a fix.", SAT, "Sam")
    assert why == ("Nothing was sent yet: before this goes to Sam, check the days. Today is Sat "
                   "6 Mar. By the calendar: Thu 1 Apr is 26 days from today. Your text says "
                   "\"three days out\". Fix that line and send it again (if it is right as it "
                   "stands, send it again unchanged and it goes).")


def test_a_count_within_a_day_of_a_named_date_passes():
    assert dates.slip("The 1 April email is 26 days away.", SAT) is None
    assert dates.slip("It goes out on Apr 1st, in 25 days.", SAT) is None
    assert dates.slip("March 7 is tomorrow, 1 day away.", SAT) is None


def test_text_with_no_count_or_no_date_is_left_alone():
    assert dates.slip("It goes out on 1 April.", SAT) is None
    assert dates.slip("It goes out in three days.", SAT) is None
    assert dates.slip("Done in 3 days of work, for the 1 Apr send.", SAT) is None
    assert dates.slip("", SAT) is None and dates.slip(None, SAT) is None


def test_a_date_without_a_year_is_the_nearest_one():
    assert dates.named("see you on Jan 2", SAT) == [(date(2028, 1, 2), 302)]
    assert dates.named("it was Feb 20", SAT) == [(date(2027, 2, 20), -14)]
    assert dates.named("on 3rd March, 2028 and Feb 31", SAT) == [(date(2028, 3, 3), 363)]


def test_the_same_title_is_questioned_once_in_half_an_hour(v):  # noqa: F811
    text = "The send on 1 January 2031 is two days away."
    assert dates.check(v, "ada", "hand_over", "Title", text).startswith("Nothing was sent yet")
    assert dates.check(v, "ada", "hand_over", "Title", text) is None
    assert dates.check(v, "ada", "report_finding", "Title", text) is not None
    assert dates.check(v, "ada", "hand_over", "Another", text) is not None
    v.later(31)
    assert dates.check(v, "ada", "hand_over", "Title", text) is not None


def test_the_comms_settings_are_declared_with_the_contracts_numbers(v):  # noqa: F811
    declared = {s.key: s for s in registry.declared()}
    want = {"chat_messages_per_day": (10, 0, 100), "requests_taken_per_day": (5, 0, 30),
            "own_tasks_per_day": (5, 0, 30), "own_tasks_open": (3, 1, 10),
            "handovers_per_day": (6, 0, 30), "priority_suggestions_per_day": (3, 0, 20),
            "findings_per_day": (3, 0, 20), "findings_max_per_day": (6, 1, 20),
            "findings_open_hold": (5, 1, 30), "urgent_findings_per_day": (1, 0, 5),
            "breakroom_per_hour": (30, 0, 600), "board_post_chars": (1500, 200, 8000)}
    assert set(TABLE) == set(want)
    for name, (default, low, high) in want.items():
        s = declared["village.comms." + name]
        assert (s.type, s.default, s.min, s.max) == ("int", default, low, high), name
        assert s.label and s.help and limit(v, name) == default
    s = declared["village.review.min_distance_min"]
    assert (s.type, s.default, s.min, s.max) == ("int", 30, 0, 1440)
