"""When a reminder is due next, and the dates in a bank's file."""

from __future__ import annotations

from datetime import date

import pytest
from books.dates import (
    Rule,
    detect_format,
    next_due,
    occurrences,
    parse_day,
    parse_file_date,
    rule_from,
    rule_problem,
)


def walk(rule: Rule, first: date, steps: int) -> list[str]:
    out, cur = [first.isoformat()], first
    for _ in range(steps):
        cur = next_due(rule, cur)
        out.append(cur.isoformat())
    return out


def test_monthly_on_the_31st_lands_on_the_last_day_of_shorter_months_and_returns():
    rule = rule_from("monthly", date(2027, 1, 31))
    assert walk(rule, date(2027, 1, 31), 4) == [
        "2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30", "2027-05-31"]


def test_monthly_on_the_31st_in_a_leap_february_lands_on_the_29th():
    rule = rule_from("monthly", date(2028, 1, 31))
    assert walk(rule, date(2028, 1, 31), 2) == ["2028-01-31", "2028-02-29", "2028-03-31"]


def test_yearly_on_29_february_lands_on_the_28th_and_returns_in_a_leap_year():
    rule = rule_from("yearly", date(2028, 2, 29))
    assert walk(rule, date(2028, 2, 29), 4) == [
        "2028-02-29", "2029-02-28", "2030-02-28", "2031-02-28", "2032-02-29"]


def test_every_two_weeks_keeps_its_weekday():
    first = date(2027, 1, 15)  # a Friday
    days = walk(Rule("biweekly"), first, 30)
    assert all(date.fromisoformat(d).weekday() == first.weekday() for d in days)
    assert days[1] == "2027-01-29"


def test_weekly_steps_seven_days():
    assert walk(Rule("weekly"), date(2027, 12, 29), 1) == ["2027-12-29", "2028-01-05"]


def test_twice_a_month_goes_between_its_two_days_and_holds_to_month_end():
    rule = rule_from("twice_monthly", date(2027, 1, 15), day2=31)
    assert (rule.day, rule.day2) == (15, 31)
    assert walk(rule, date(2027, 1, 15), 5) == [
        "2027-01-15", "2027-01-31", "2027-02-15", "2027-02-28", "2027-03-15", "2027-03-31"]
    early = rule_from("twice_monthly", date(2027, 1, 20), day2=5)
    assert walk(early, date(2027, 1, 20), 2) == ["2027-01-20", "2027-02-05", "2027-02-20"]


def test_every_few_months_keeps_its_day():
    rule = rule_from("months", date(2027, 1, 31), n=3)
    assert walk(rule, date(2027, 1, 31), 3) == [
        "2027-01-31", "2027-04-30", "2027-07-31", "2027-10-31"]


def test_once_has_no_next_date():
    assert next_due(Rule("once"), date(2027, 1, 1)) is None


def test_a_schedule_that_cannot_be_kept_is_refused_with_a_sentence():
    assert rule_problem(Rule("fortnightly")) != ""
    assert rule_problem(Rule("twice_monthly", day=5, day2=5)) != ""
    assert rule_problem(Rule("months", n=0)) != ""
    assert rule_problem(Rule("monthly", day=12)) == ""


def test_occurrences_list_every_date_in_a_window():
    rule = rule_from("monthly", date(2027, 1, 10))
    got = occurrences(rule, date(2027, 1, 10), date(2027, 2, 1), date(2027, 4, 10))
    assert [d.isoformat() for d in got] == ["2027-02-10", "2027-03-10", "2027-04-10"]


def test_a_typed_date_is_a_real_calendar_day():
    assert parse_day("2027-02-28") == date(2027, 2, 28)
    for bad in ("2027-02-30", "28/02/2027", "", None, "2027-2-3"):
        with pytest.raises(ValueError):
            parse_day(bad)


def test_a_files_date_format_is_detected():
    assert detect_format(["2027-01-05", "2027-01-31"]) == "ymd"
    assert detect_format(["1/5/2027", "1/31/2027"]) == "mdy"
    assert detect_format(["5/1/2027", "31/1/2027"]) == "dmy"
    assert detect_format(["3/4/2027", "5/6/2027"]) == "ask"
    assert detect_format(["13/14/2027"]) is None
    assert detect_format(["yesterday"]) is None
    assert detect_format(["2027-01-05", "Total", "2027-01-06"]) == "ymd"
    assert detect_format(["Total", "Note", "2027-01-06"]) is None
    assert detect_format(["2027-01-05 00:00:00"]) == "ymd"


def test_a_files_date_is_read_in_its_format():
    assert parse_file_date("3/4/2027", "mdy") == date(2027, 3, 4)
    assert parse_file_date("3/4/2027", "dmy") == date(2027, 4, 3)
    assert parse_file_date("3.4.27", "dmy") == date(2027, 4, 3)
    assert parse_file_date("2027/04/03", "ymd") == date(2027, 4, 3)
    with pytest.raises(ValueError):
        parse_file_date("2/30/2027", "mdy")
