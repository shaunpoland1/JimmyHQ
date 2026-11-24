from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from jimmyhq.settings import SettingError
from jimmyhq.tasks import TaskError, recur, service
from jimmyhq.tasks.lens import LENS


def at(svc, when: str, zone: str = "UTC") -> None:
    """Set the service's clock to a wall-clock time in a zone."""
    moment = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(zone))
    svc.clock = lambda: int(moment.timestamp() * 1000)


def titles(items) -> list[str]:
    return [t["title"] for t in items]


@pytest.fixture
def tasks(core):
    core.settings.set("owner.timezone", "UTC", actor="owner")
    svc = service(core)
    at(svc, "2026-03-10 09:00")  # a Tuesday
    return svc


def keep(tasks, task) -> dict:
    return tasks.complete(task["id"], actor="owner")


# --- keeping a round --------------------------------------------------------------


def test_keeping_a_round_moves_a_repeating_task_to_its_next_round_and_keeps_it_open(tasks):
    bins = tasks.create("Bins", actor="owner", due="2026-03-10", repeat="FREQ=WEEKLY;BYDAY=TH")
    assert bins["due"] == "2026-03-12"  # the first Thursday on or after the date given
    assert bins["repeat_text"] == "every Thursday"
    kept = keep(tasks, bins)
    assert (kept["status"], kept["due"], kept["last_kept"], kept["last_kept_on"]) == (
        "open", "2026-03-19", "2026-03-12", "2026-03-10")
    assert (kept["kept_count"], kept["can_undo_round"]) == (1, True)
    board = tasks.board()
    assert titles(board["open"]) == ["Bins"] and board["done"] == []
    assert titles(board["kept_today"]) == ["Bins"] and board["counts"]["done_today"] == 1


def test_a_round_kept_today_never_leaves_the_next_round_due_today(tasks, core):
    daily = tasks.create("Stretch", actor="owner", due="2026-03-10", repeat="FREQ=DAILY")
    assert keep(tasks, daily)["due"] == "2026-03-11"
    assert tasks.board()["today"] == []
    # Kept late: the rounds missed in between are skipped, not queued up for today.
    lapsed = tasks.create("Water plants", actor="owner", due="2026-03-03", repeat="FREQ=DAILY")
    at(tasks, "2026-03-10 23:59")
    kept = keep(tasks, lapsed)
    assert (kept["due"], kept["last_kept"], kept["lapsed_days"]) == ("2026-03-11",
                                                                    "2026-03-03", 0)
    # An event that claims otherwise is ignored by the lens.
    core.tape.print("task.kept", {"task_id": daily["id"], "kept_for": "2026-03-11",
                                  "kept_on": "2026-03-11", "next_due": "2026-03-11"},
                    actor="owner")
    assert tasks.get(daily["id"])["kept_count"] == 1


def test_a_round_kept_early_moves_past_the_round_it_was_kept_for(tasks):
    bins = tasks.create("Bins", actor="owner", due="2026-03-12", repeat="FREQ=WEEKLY")
    assert keep(tasks, bins)["due"] == "2026-03-19"


def test_pressing_done_twice_does_not_also_keep_the_round_that_is_not_due_yet(tasks, core):
    habit = tasks.create("Stretch", actor="owner", due="2026-03-10", repeat="FREQ=DAILY")
    keep(tasks, habit)
    with pytest.raises(TaskError, match="already kept today") as err:
        keep(tasks, habit)
    assert err.value.status == 409
    assert tasks.get(habit["id"])["due"] == "2026-03-11"
    assert len(core.tape.after(0, kind="task.kept")) == 1


def test_keeping_a_round_can_be_undone_which_puts_back_the_due_date_and_the_pushes(tasks):
    habit = tasks.create("Habit", actor="owner", due="2026-03-10", repeat="FREQ=DAILY")
    tasks.push(habit["id"], "2026-03-11", actor="owner")
    at(tasks, "2026-03-11 08:00")
    assert keep(tasks, habit)["pushes"] == 0
    at(tasks, "2026-03-12 08:00")
    assert keep(tasks, habit)["due"] == "2026-03-13"
    undone = tasks.undo_round(habit["id"], actor="owner")
    assert (undone["due"], undone["kept_count"], undone["last_kept"]) == (
        "2026-03-12", 1, "2026-03-11")
    undone = tasks.undo_round(habit["id"], actor="owner")
    assert (undone["due"], undone["pushes"], undone["kept_count"], undone["last_kept"]) == (
        "2026-03-11", 1, 0, None)
    assert undone["can_undo_round"] is False
    with pytest.raises(TaskError, match="no kept round to undo") as err:
        tasks.undo_round(habit["id"], actor="owner")
    assert err.value.status == 409
    one = tasks.create("One-off", actor="owner")
    tasks.complete(one["id"], actor="owner")
    with pytest.raises(TaskError, match="no kept round to undo"):
        tasks.undo_round(one["id"], actor="owner")


@pytest.mark.parametrize("rule", ["FREQ=DAILY;COUNT=2", "FREQ=DAILY;UNTIL=20260311T120000Z"])
def test_a_rule_with_count_or_until_runs_out_and_the_task_is_then_done_for_good(tasks, rule):
    task = tasks.create("Course of tablets", actor="owner", due="2026-03-10", repeat=rule)
    assert keep(tasks, task)["due"] == "2026-03-11"
    at(tasks, "2026-03-11 09:00")
    last = keep(tasks, task)
    assert (last["status"], last["next_due"], last["kept_count"]) == ("done", None, 2)
    board = tasks.board()
    assert board["open"] == [] and titles(board["done"]) == ["Course of tablets"]
    assert board["counts"]["done_today"] == 1
    with pytest.raises(TaskError, match="already done"):
        keep(tasks, task)
    # Reopening it is the same as undoing the last round.
    back = tasks.reopen(task["id"], actor="owner")
    assert (back["status"], back["due"], back["kept_count"]) == ("open", "2026-03-11", 1)


def test_taking_a_push_back_does_not_start_a_repeating_tasks_count_again(tasks):
    pills = tasks.create("Pills", actor="owner", due="2026-03-10", repeat="FREQ=DAILY;COUNT=3")
    assert keep(tasks, pills)["due"] == "2026-03-11"  # round one of three
    assert tasks.push(pills["id"], "2026-03-13", actor="owner")["pushes"] == 1
    back = tasks.unpush(pills["id"], actor="owner")
    assert (back["due"], back["pushes"], back["kept_count"]) == ("2026-03-11", 0, 1)
    # Two rounds are left, not three: the rule is still counted from the 10th.
    at(tasks, "2026-03-11 09:00")
    assert keep(tasks, back)["due"] == "2026-03-12"
    at(tasks, "2026-03-12 09:00")
    assert keep(tasks, back)["status"] == "done"


def test_undoing_a_kept_round_brings_back_the_push_that_can_be_taken_back(tasks):
    habit = tasks.create("Habit", actor="owner", due="2026-03-10", repeat="FREQ=DAILY")
    tasks.push(habit["id"], "2026-03-12", actor="owner")
    at(tasks, "2026-03-12 09:00")
    assert keep(tasks, habit)["can_unpush"] is False
    undone = tasks.undo_round(habit["id"], actor="owner")
    assert (undone["due"], undone["pushes"], undone["can_unpush"]) == ("2026-03-12", 1, True)
    back = tasks.unpush(habit["id"], actor="owner")
    assert (back["due"], back["pushes"]) == ("2026-03-10", 0)


def test_pushing_a_round_does_not_shift_the_rule(tasks):
    bins = tasks.create("Bins", actor="owner", due="2026-03-12", repeat="FREQ=WEEKLY;BYDAY=TH")
    pushed = tasks.push(bins["id"], "2026-03-14", actor="owner")
    assert pushed["due"] == "2026-03-14"
    # Editing something else leaves the pushed date alone.
    assert tasks.edit(bins["id"], actor="owner", notes="blue bin")["due"] == "2026-03-14"
    at(tasks, "2026-03-14 10:00")
    assert keep(tasks, bins)["due"] == "2026-03-19"


def test_every_other_week_keeps_its_phase_from_the_first_due_date(tasks):
    task = tasks.create("Recycling", actor="owner", due="2026-03-12",
                        repeat="FREQ=WEEKLY;INTERVAL=2;BYDAY=TH")
    at(tasks, "2026-03-20 10:00")  # kept eight days late, in the off week
    assert keep(tasks, task)["due"] == "2026-03-26"
    at(tasks, "2026-03-26 10:00")
    assert keep(tasks, task)["due"] == "2026-04-09"


def test_editing_the_rule_of_a_task_with_history_keeps_the_history_and_follows_the_new_rule(
        tasks):
    task = tasks.create("Review", actor="owner", due="2026-03-12", repeat="FREQ=WEEKLY;BYDAY=TH")
    at(tasks, "2026-03-12 09:00")
    keep(tasks, task)
    edited = tasks.edit(task["id"], actor="owner", repeat="rrule:freq=monthly;bymonthday=1")
    assert (edited["repeat"], edited["repeat_text"]) == ("FREQ=MONTHLY;BYMONTHDAY=1",
                                                         "monthly on the 1st")
    assert (edited["due"], edited["kept_count"], edited["last_kept"]) == (
        "2026-04-01", 1, "2026-03-12")
    at(tasks, "2026-04-01 09:00")
    assert keep(tasks, task)["due"] == "2026-05-01"
    # The older round can still be undone after the newer one.
    tasks.undo_round(task["id"], actor="owner")
    assert tasks.undo_round(task["id"], actor="owner")["due"] == "2026-03-12"
    # Taking the rule away makes it a one-off that can simply be done.
    plain = tasks.edit(task["id"], actor="owner", repeat=None)
    assert plain["repeat"] is None and plain["repeat_text"] is None
    assert tasks.complete(task["id"], actor="owner")["status"] == "done"


# --- the calendar -----------------------------------------------------------------


def test_month_end_repeats_land_on_the_last_day_of_each_month(tasks):
    at(tasks, "2027-01-31 09:00")
    rent = tasks.create("Rent", actor="owner", due="2027-01-31",
                        repeat="FREQ=MONTHLY;BYMONTHDAY=-1")
    seen = []
    for today in ("2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30"):
        at(tasks, f"{today} 09:00")
        seen.append(keep(tasks, rent)["due"])
    assert seen == ["2027-02-28", "2027-03-31", "2027-04-30", "2027-05-31"]
    # A rule fixed on the 31st skips the months that have none, as the standard says.
    strict = tasks.create("Report", actor="owner", due="2027-01-31",
                          repeat="FREQ=MONTHLY;BYMONTHDAY=31")
    at(tasks, "2027-01-31 09:00")
    assert keep(tasks, strict)["due"] == "2027-03-31"


def test_a_leap_day_repeat_waits_for_the_next_leap_year(tasks):
    at(tasks, "2028-02-29 09:00")
    task = tasks.create("Leap day", actor="owner", due="2028-02-29", repeat="FREQ=YEARLY")
    assert keep(tasks, task)["due"] == "2032-02-29"
    feb = tasks.create("Month end", actor="owner", due="2028-02-01",
                       repeat="FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=-1")
    assert feb["due"] == "2028-02-29"
    assert keep(tasks, feb)["due"] == "2029-02-28"


def test_today_follows_the_owners_time_zone_either_side_of_midnight(tasks, core):
    task = tasks.create("Due on the tenth", actor="owner", due="2026-03-10")
    core.settings.set("owner.timezone", "Pacific/Auckland", actor="owner")
    at(tasks, "2026-03-09 23:59", "Pacific/Auckland")  # 10:59 UTC on the 9th
    assert tasks.board()["date"] == "2026-03-09" and tasks.list_today() == []
    at(tasks, "2026-03-10 00:00", "Pacific/Auckland")  # still the 9th in UTC
    board = tasks.board()
    assert (board["date"], board["timezone"]) == ("2026-03-10", "Pacific/Auckland")
    assert titles(board["today"]) == ["Due on the tenth"]
    at(tasks, "2026-03-11 00:01", "Pacific/Auckland")
    assert tasks.get(task["id"])["lapsed_days"] == 1
    # West of UTC the date is still the day before when UTC has moved on.
    core.settings.set("owner.timezone", "America/Los_Angeles", actor="owner")
    at(tasks, "2026-03-10 02:00", "UTC")
    assert tasks.board()["date"] == "2026-03-09" and tasks.list_today() == []
    # A name saved before the setting was checked: it is no longer accepted,
    # and one already on the Tape reads as "use this computer's zone".
    with pytest.raises(SettingError, match="not a name this computer knows"):
        core.settings.set("owner.timezone", "Not/AZone", actor="owner")
    core.tape.print("setting.updated", {"key": "owner.timezone", "value": "Not/AZone"},
                    actor="owner")
    assert tasks.board()["timezone"] == "local"  # falls back rather than failing


def test_a_habit_kept_late_at_night_is_kept_for_the_owners_day_not_the_utc_day(tasks, core):
    core.settings.set("owner.timezone", "America/Los_Angeles", actor="owner")
    at(tasks, "2026-03-10 21:30", "America/Los_Angeles")  # already the 11th in UTC
    habit = tasks.create("Journal", actor="owner", repeat="FREQ=DAILY")
    assert habit["due"] == "2026-03-10"
    kept = keep(tasks, habit)
    assert (kept["last_kept_on"], kept["due"]) == ("2026-03-10", "2026-03-11")
    assert tasks.board()["counts"]["done_today"] == 1
    at(tasks, "2026-03-11 00:05", "America/Los_Angeles")
    board = tasks.board()
    assert titles(board["today"]) == ["Journal"] and board["counts"]["done_today"] == 0


@pytest.mark.parametrize(("zone", "days"), [
    ("Europe/Paris", ["2026-03-28", "2026-03-29", "2026-03-30"]),  # clocks go forward
    ("Europe/Paris", ["2026-10-24", "2026-10-25", "2026-10-26"]),  # clocks go back
    ("America/New_York", ["2026-03-07", "2026-03-08", "2026-03-09"]),
])
def test_a_daily_habit_neither_skips_nor_repeats_a_day_when_the_clocks_change(
        tasks, core, zone, days):
    core.settings.set("owner.timezone", zone, actor="owner")
    at(tasks, f"{days[0]} 23:30", zone)
    habit = tasks.create("Walk", actor="owner", repeat="FREQ=DAILY")
    for today, tomorrow in zip(days, days[1:], strict=False):
        for clock in ("00:30", "23:30"):
            at(tasks, f"{today} {clock}", zone)
            assert tasks.board()["date"] == today
        assert titles(tasks.list_today()) == ["Walk"]
        assert keep(tasks, habit)["due"] == tomorrow
        assert tasks.list_today() == []


# --- reading a rule ---------------------------------------------------------------


@pytest.mark.parametrize(("rule", "text"), [
    ("FREQ=DAILY", "every day"),
    ("FREQ=DAILY;INTERVAL=2", "every other day"),
    ("FREQ=DAILY;INTERVAL=3", "every 3 days"),
    ("FREQ=WEEKLY", "every week"),
    ("FREQ=WEEKLY;INTERVAL=2", "every other week"),
    ("FREQ=WEEKLY;BYDAY=TH", "every Thursday"),
    ("FREQ=WEEKLY;BYDAY=MO,WE,FR", "every Monday, Wednesday and Friday"),
    ("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "every weekday"),
    ("FREQ=WEEKLY;INTERVAL=2;BYDAY=TH", "every other Thursday"),
    ("FREQ=WEEKLY;INTERVAL=3;BYDAY=MO,TH", "every 3 weeks on Monday and Thursday"),
    ("FREQ=MONTHLY", "every month"),
    ("FREQ=MONTHLY;BYMONTHDAY=1", "monthly on the 1st"),
    ("FREQ=MONTHLY;BYMONTHDAY=22", "monthly on the 22nd"),
    ("FREQ=MONTHLY;BYMONTHDAY=-1", "monthly on the last day"),
    ("FREQ=MONTHLY;BYDAY=1MO", "monthly on the first Monday"),
    ("FREQ=MONTHLY;BYDAY=-1FR", "monthly on the last Friday"),
    ("FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=15", "every 3 months on the 15th"),
    ("FREQ=YEARLY", "every year"),
    ("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29", "every year on 29 February"),
    ("FREQ=DAILY;COUNT=5", "every day, 5 times"),
    ("FREQ=WEEKLY;BYDAY=TH;UNTIL=20261231", "every Thursday, until 2026-12-31"),
    ("FREQ=MONTHLY;BYDAY=MO;BYSETPOS=2", "FREQ=MONTHLY;BYDAY=MO;BYSETPOS=2"),
    ("not a rule", "not a rule"),
])
def test_a_rule_reads_as_a_short_phrase_or_falls_back_to_the_rule_itself(rule, text):
    assert recur.describe(rule) == text


def test_the_next_round_is_always_strictly_after_the_day_asked_about():
    start = date(2026, 1, 1)
    for rule in ("FREQ=DAILY", "FREQ=WEEKLY;BYDAY=MO,TH", "FREQ=MONTHLY;BYMONTHDAY=-1",
                 "FREQ=YEARLY", "FREQ=DAILY;INTERVAL=3"):
        day = start
        for _ in range(40):
            nxt = recur.next_after(rule, start, day)
            assert nxt is not None and nxt > day, rule
            day = nxt


# --- found on review --------------------------------------------------------------


def test_an_edit_made_against_an_older_due_date_does_not_bring_a_kept_round_back(tasks, core):
    habit = tasks.create("Habit", actor="owner", due="2026-03-10", repeat="FREQ=DAILY")
    pushed = tasks.create("Pushed", actor="owner", due="2026-03-10", due_time="08:00")
    # Another process read both tasks, and prints its edits only after these changes.
    keep(tasks, habit)
    tasks.push(pushed["id"], "2026-03-12", actor="owner")
    for task, repeat in ((habit, "FREQ=DAILY"), (pushed, None)):
        core.tape.print("task.refiled", {
            "task_id": task["id"], "title": task["title"] + ", renamed", "notes": "",
            "area": "", "due": "2026-03-10", "due_time": task["due_time"], "repeat": repeat,
            "assignee": "owner", "was": "2026-03-10"}, actor="agent:scout")
    after = tasks.get(habit["id"])
    assert (after["title"], after["due"], after["kept_count"]) == ("Habit, renamed",
                                                                   "2026-03-11", 1)
    after = tasks.get(pushed["id"])
    assert (after["title"], after["due"], after["due_time"], after["pushes"]) == (
        "Pushed, renamed", "2026-03-12", "08:00", 1)
    assert tasks.board()["today"] == []
    # An edit made against the current date still moves it.
    assert tasks.edit(pushed["id"], actor="owner", due="2026-03-20")["due"] == "2026-03-20"
    assert core.tape.after(0, kind="task.refiled")[-1].payload["was"] == "2026-03-12"


def test_a_change_that_lost_a_race_is_answered_with_the_task_as_it_really_stands(tasks, core):
    habit = tasks.create("Habit", actor="owner", due="2026-03-10", repeat="FREQ=DAILY")
    real_read = core.lenses.read
    before = real_read(LENS)  # what the losing process read, before the round was kept
    keep(tasks, habit)

    def stale_once(lens):
        core.lenses.read = real_read
        return before
    core.lenses.read = stale_once
    try:
        answer = tasks.push(habit["id"], "2026-03-15", actor="owner")
    finally:
        core.lenses.read = real_read
    # The push expected the task to be due on the 10th. It no longer is, so nothing moved.
    assert (answer["due"], answer["pushes"], answer["kept_count"]) == ("2026-03-11", 0, 1)
    assert tasks.get(habit["id"]) == answer


def test_a_done_one_off_that_once_repeated_is_reopened_not_unkept(tasks):
    task = tasks.create("Was a habit", actor="owner", due="2026-03-10", repeat="FREQ=DAILY")
    keep(tasks, task)
    tasks.edit(task["id"], actor="owner", repeat=None, due="2026-03-20")
    done = tasks.complete(task["id"], actor="owner")
    assert (done["status"], done["can_undo_round"]) == ("done", False)
    with pytest.raises(TaskError, match="Reopen it instead") as err:
        tasks.undo_round(task["id"], actor="owner")
    assert err.value.status == 409
    back = tasks.reopen(task["id"], actor="owner")
    assert (back["status"], back["due"], back["kept_count"]) == ("open", "2026-03-20", 1)


def test_clearing_the_date_of_a_repeating_task_keeps_its_time(tasks):
    task = tasks.create("Bins", actor="owner", due="2026-03-19", due_time="07:30",
                        repeat="FREQ=WEEKLY;BYDAY=TH")
    cleared = tasks.edit(task["id"], actor="owner", due=None)
    assert (cleared["due"], cleared["due_time"]) == ("2026-03-12", "07:30")


def test_a_rule_with_both_count_and_until_is_refused_with_a_message_that_says_so(tasks):
    with pytest.raises(TaskError, match="both COUNT and UNTIL") as err:
        tasks.create("Both", actor="owner", repeat="FREQ=DAILY;COUNT=3;UNTIL=20270101")
    assert err.value.status == 422


def test_repeated_days_and_fortnightly_weekdays_read_naturally():
    assert recur.describe("FREQ=WEEKLY;BYDAY=MO,MO") == "every Monday"
    assert recur.describe("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TU,WE,TH,FR") == \
        "every other week on weekdays"
    assert recur.describe("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR") == "every weekday"
