from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jimmyhq.core import open_core
from jimmyhq.ids import now_ms
from jimmyhq.tasks import TaskError, service
from jimmyhq.tasks.lens import FINISHED_CAP, LENS


def at(svc, when: str, zone: str = "UTC") -> None:
    """Set the service's clock to a wall-clock time in a zone."""
    moment = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(zone))
    svc.clock = lambda: int(moment.timestamp() * 1000)


@pytest.fixture
def tasks(core):
    core.settings.set("owner.timezone", "UTC", actor="owner")
    svc = service(core)
    at(svc, "2026-03-10 09:00")  # a Tuesday
    return svc


def titles(items) -> list[str]:
    return [t["title"] for t in items]


# --- filing and reading -----------------------------------------------------------


def test_a_new_task_goes_to_the_end_of_the_open_list_with_everything_the_screen_needs(tasks):
    tasks.create("First", actor="owner")
    made = tasks.create("  Second   thing ", actor="token:phone", notes="n", area="Home",
                        due="2026-03-12", due_time="07:30", assignee="agent:scout")
    assert made["title"] == "Second thing" and made["area"] == "home"
    assert (made["status"], made["filed_by"], made["assignee"]) == ("open", "token:phone",
                                                                   "agent:scout")
    assert (made["due"], made["next_due"], made["due_time"]) == ("2026-03-12", "2026-03-12",
                                                                 "07:30")
    assert (made["pushes"], made["stalled"], made["lapsed_days"], made["repeat_text"]) == (
        0, False, 0, None)
    assert titles(tasks.list_open()) == ["First", "Second thing"]
    assert tasks.get(made["id"]) == made


def test_a_task_due_today_appears_on_the_today_sheet_and_an_undated_one_does_not(tasks):
    tasks.create("Undated", actor="owner")
    tasks.create("Later", actor="owner", due="2026-03-11")
    tasks.create("Today", actor="owner", due="2026-03-10")
    tasks.create("Old", actor="owner", due="2026-03-07")
    board = tasks.board()
    assert titles(board["today"]) == ["Today", "Old"]  # manual order, not date order
    assert [t["lapsed_days"] for t in board["today"]] == [0, 3]
    assert board["counts"] == {"open": 4, "due_today": 1, "lapsed": 1, "stalled": 0,
                               "done_today": 0}
    assert board["date"] == "2026-03-10" and board["timezone"] == "UTC"


def test_the_board_filters_by_assignee_and_area_and_lists_areas_in_use(tasks, core):
    tasks.create("Mine", actor="owner", area="home")
    tasks.create("Theirs", actor="owner", area="garden", assignee="agent:scout")
    assert titles(tasks.board(assignee="agent:scout")["open"]) == ["Theirs"]
    assert titles(tasks.board(area="HOME")["open"]) == ["Mine"]
    assert tasks.board(area="home", assignee="agent:scout")["counts"]["open"] == 0
    # An unknown area is accepted and remembered after the settings' own list.
    assert tasks.board()["areas"] == ["home", "work", "garden"]
    core.settings.set("tasks.default_area", "inbox", actor="owner")
    assert tasks.create("No area given", actor="owner")["area"] == "inbox"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"title": "   "}, "Give the task a title"),
        ({"title": "x" * 301}, "longer than 300"),
        ({"title": "bad \ud800 text"}, "cannot be saved"),
        ({"title": "t", "notes": "n" * 10_001}, "longer than 10000"),
        ({"title": "t", "due": "2026-02-30"}, "real date"),
        ({"title": "t", "due": "tomorrow"}, "real date"),
        ({"title": "t", "due_time": "07:30"}, "needs a due date"),
        ({"title": "t", "due": "2026-03-12", "due_time": "25:00"}, "HH:MM"),
        ({"title": "t", "assignee": "somebody"}, "owner or agent:"),
        ({"title": "t", "repeat": "every thursday"}, "could not be read"),
        ({"title": "t", "repeat": "FREQ=HOURLY"}, "at most once a day"),
        ({"title": "t", "repeat": "FREQ=DAILY;BYHOUR=9,17"}, "at most once a day"),
        ({"title": "t", "repeat": "FREQ=WEEKLY;BYDAY=XX"}, "could not be read"),
        ({"title": "t", "repeat": "FREQ=WEEKLY;INTERVAL=0"}, "from 1 to 1000"),
        ({"title": "t", "repeat": "FREQ=DAILY;UNTIL=20200101"}, "no round on or after"),
        ({"title": "t", "repeat": "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30"}, "no round on or"),
    ],
)
def test_a_task_that_cannot_be_filed_is_refused_with_a_message_that_says_why(
        tasks, core, fields, message):
    title = fields.pop("title")
    with pytest.raises(TaskError, match=message) as err:
        tasks.create(title, actor="owner", **fields)
    assert err.value.status == 422
    assert core.tape.after(0, kind="task.filed") == []


# --- one-off tasks ----------------------------------------------------------------


def test_a_one_off_task_can_be_done_and_reopened(tasks):
    tasks.clock = now_ms  # done_at comes from the Tape's own clock
    one = tasks.create("One", actor="owner")
    tasks.create("Two", actor="owner")
    done = tasks.complete(one["id"], actor="owner")
    assert done["status"] == "done" and done["done_at"] and done["next_due"] is None
    board = tasks.board()
    assert titles(board["open"]) == ["Two"] and titles(board["done"]) == ["One"]
    assert board["counts"]["done_today"] == 1
    with pytest.raises(TaskError, match="already done"):
        tasks.complete(one["id"], actor="owner")
    again = tasks.reopen(one["id"], actor="owner")
    assert again["status"] == "open" and again["done_at"] is None
    assert titles(tasks.list_open()) == ["Two", "One"]
    with pytest.raises(TaskError, match="already open"):
        tasks.reopen(one["id"], actor="owner")


def test_dropping_takes_a_task_off_the_list_without_calling_it_done_and_can_be_undone(tasks):
    one = tasks.create("One", actor="owner")
    dropped = tasks.drop(one["id"], actor="owner")
    assert dropped["status"] == "dropped" and dropped["done_at"] is None
    board = tasks.board()
    assert board["open"] == [] and board["done"] == [] and titles(board["dropped"]) == ["One"]
    assert board["counts"]["done_today"] == 0
    for change in (tasks.complete, tasks.reopen, tasks.drop):
        with pytest.raises(TaskError) as err:
            change(one["id"], actor="owner")
        assert err.value.status == 409
    assert tasks.restore(one["id"], actor="owner")["status"] == "open"
    with pytest.raises(TaskError, match="Only a dropped task"):
        tasks.restore(one["id"], actor="owner")


def test_finished_tasks_leave_the_board_after_the_number_of_days_in_settings(tasks, core):
    one = tasks.create("One", actor="owner")
    tasks.complete(one["id"], actor="owner")
    real = time.time()
    tasks.clock = lambda: int((real + 3 * 86400) * 1000)
    assert titles(tasks.board()["done"]) == ["One"]
    core.settings.set("tasks.done_days", 2, actor="owner")
    assert tasks.board()["done"] == []
    assert tasks.get(one["id"])["status"] == "done"


def test_an_unknown_or_malformed_id_is_a_404_not_a_crash(tasks):
    for bad in ("nope", "01JABCDEFGHJKMNPQRSTVWXYZ0", "", "x" * 5000):
        with pytest.raises(TaskError) as err:
            tasks.get(bad)
        assert err.value.status == 404


# --- editing ----------------------------------------------------------------------


def test_editing_refiles_the_task_with_every_value_and_an_unchanged_edit_prints_nothing(
        tasks, core):
    one = tasks.create("One", actor="owner", due="2026-03-12", due_time="08:00", area="home")
    edited = tasks.edit(one["id"], actor="token:phone", title="One, revised", notes="more")
    assert (edited["title"], edited["notes"], edited["due"], edited["area"]) == (
        "One, revised", "more", "2026-03-12", "home")
    assert edited["filed_by"] == "owner" and edited["updated"] >= edited["created"]
    tasks.edit(one["id"], actor="owner", title="One, revised")
    events = core.tape.after(0, kind="task.refiled")
    assert len(events) == 1 and events[0].actor == "token:phone"
    assert events[0].payload["due_time"] == "08:00" and events[0].subject == f"task:{one['id']}"
    # Clearing the date takes the time with it.
    cleared = tasks.edit(one["id"], actor="owner", due=None)
    assert (cleared["due"], cleared["due_time"]) == (None, None)
    with pytest.raises(TaskError, match="no field called status"):
        tasks.edit(one["id"], actor="owner", status="done")
    tasks.complete(one["id"], actor="owner")
    with pytest.raises(TaskError, match="Reopen it before you change it"):
        tasks.edit(one["id"], actor="owner", title="Too late")


# --- pushing ----------------------------------------------------------------------


def test_pushing_moves_the_date_claims_no_work_and_stalls_the_task_after_the_setting(
        tasks, core):
    one = tasks.create("One", actor="owner", due="2026-03-10")
    assert tasks.push(one["id"], "2026-03-11", actor="owner")["pushes"] == 1
    assert tasks.push(one["id"], "2026-03-12", actor="owner")["stalled"] is False
    third = tasks.push(one["id"], "2026-03-13", actor="owner")
    assert (third["due"], third["pushes"], third["stalled"], third["status"]) == (
        "2026-03-13", 3, True, "open")
    board = tasks.board()
    assert board["counts"]["stalled"] == 1 and board["counts"]["done_today"] == 0
    core.settings.set("tasks.stalled_after", 5, actor="owner")
    assert tasks.get(one["id"])["stalled"] is False
    # Doing it clears the count.
    tasks.complete(one["id"], actor="owner")
    assert tasks.reopen(one["id"], actor="owner")["pushes"] == 0


def test_taking_a_push_back_restores_the_due_date_and_the_count_so_undone_pushes_never_stall(
        tasks):
    one = tasks.create("One", actor="owner", due="2026-03-10")
    assert one["can_unpush"] is False
    for _ in range(3):  # three slips of the finger, each undone
        pushed = tasks.push(one["id"], "2026-03-11", actor="owner")
        assert (pushed["pushes"], pushed["can_unpush"]) == (1, True)
        back = tasks.unpush(one["id"], actor="owner")
        assert (back["due"], back["pushes"], back["stalled"], back["can_unpush"]) == (
            "2026-03-10", 0, False, False)
    # Pushes are taken back one at a time, newest first.
    tasks.push(one["id"], "2026-03-12", actor="owner")
    tasks.push(one["id"], "2026-03-15", actor="owner")
    assert tasks.unpush(one["id"], actor="owner")["due"] == "2026-03-12"
    last = tasks.unpush(one["id"], actor="owner")
    assert (last["due"], last["pushes"]) == ("2026-03-10", 0)


def test_taking_back_the_push_of_an_undated_task_leaves_it_undated_again(tasks):
    loose = tasks.create("Loose", actor="owner")
    assert tasks.push(loose["id"], "2026-03-12", actor="owner")["due"] == "2026-03-12"
    back = tasks.unpush(loose["id"], actor="owner")
    assert (back["due"], back["due_time"], back["pushes"]) == (None, None, 0)
    assert tasks.board()["today"] == []


def test_there_is_nothing_to_take_back_on_a_task_that_was_not_pushed_or_is_not_open(tasks):
    one = tasks.create("One", actor="owner", due="2026-03-10")
    with pytest.raises(TaskError, match="has not been pushed since it was last done") as err:
        tasks.unpush(one["id"], actor="owner")
    assert err.value.status == 409
    # Changing the date by hand makes the earlier push no longer the thing to undo.
    tasks.push(one["id"], "2026-03-11", actor="owner")
    tasks.edit(one["id"], actor="owner", due="2026-03-20")
    with pytest.raises(TaskError, match="Edit the task") as err:
        tasks.unpush(one["id"], actor="owner")
    assert err.value.status == 409
    assert tasks.get(one["id"])["due"] == "2026-03-20"
    # Doing the task clears its pushes, so reopening it leaves nothing to take back.
    tasks.push(one["id"], "2026-03-21", actor="owner")
    tasks.complete(one["id"], actor="owner")
    with pytest.raises(TaskError, match="Reopen it") as err:
        tasks.unpush(one["id"], actor="owner")
    assert err.value.status == 409
    assert tasks.reopen(one["id"], actor="owner")["can_unpush"] is False


def test_a_push_taken_back_twice_at_once_by_two_processes_only_goes_back_one_push(tasks, core):
    one = tasks.create("One", actor="owner", due="2026-03-10")
    tasks.push(one["id"], "2026-03-11", actor="owner")
    tasks.push(one["id"], "2026-03-12", actor="owner")
    unpushed = {"task_id": one["id"], "was": "2026-03-12", "to": "2026-03-11"}
    for _ in range(2):  # both read the same state, then both print
        core.tape.print("task.unpushed", unpushed, actor="owner", subject=f"task:{one['id']}")
    after = tasks.get(one["id"])
    assert (after["due"], after["pushes"], after["can_unpush"]) == ("2026-03-11", 1, True)


def test_the_board_says_how_many_days_of_finished_tasks_it_covers(tasks, core):
    assert tasks.board()["done_days"] == 7
    core.settings.set("tasks.done_days", 30, actor="owner")
    assert tasks.board()["done_days"] == 30


def test_a_finished_task_carries_the_owners_calendar_day_it_was_finished_on(
        tasks, core, monkeypatch):
    core.settings.set("owner.timezone", "Pacific/Kiritimati", actor="owner")  # UTC+14
    one = tasks.create("One", actor="owner")
    assert one["done_on"] is None
    done = tasks.complete(one["id"], actor="owner")
    finished = datetime.fromtimestamp(done["done_at"] / 1000, tz=ZoneInfo("Pacific/Kiritimati"))
    assert done["done_on"] == finished.date().isoformat()
    assert tasks.board()["done"][0]["done_on"] == done["done_on"]
    # With no zone chosen the day is this computer's, and it is still the server
    # that says so: the screen is never left to work it out in the browser's zone.
    core.settings.set("owner.timezone", "", actor="owner")
    if not hasattr(time, "tzset"):
        # Windows cannot change the process's zone, so check against whatever it is.
        board = tasks.board()
        here = datetime.fromtimestamp(done["done_at"] / 1000).astimezone()
        assert board["timezone"] == "local"
        assert board["done"][0]["done_on"] == here.date().isoformat()
        return
    monkeypatch.setenv("TZ", "Pacific/Pago_Pago")  # UTC-11: never Kiritimati's day
    time.tzset()
    try:
        board = tasks.board()
        local = datetime.fromtimestamp(done["done_at"] / 1000, tz=ZoneInfo("Pacific/Pago_Pago"))
        assert board["timezone"] == "local"
        assert board["done"][0]["done_on"] == local.date().isoformat() != done["done_on"]
    finally:
        monkeypatch.undo()
        time.tzset()


def test_a_push_must_go_to_a_later_date_that_is_not_in_the_past(tasks):
    one = tasks.create("One", actor="owner", due="2026-03-12")
    old = tasks.create("Old", actor="owner", due="2026-03-01")
    for task, to, message in ((one, "2026-03-12", "later date"), (one, "2026-03-11", "later"),
                              (old, "2026-03-05", "into the past"), (one, "soon", "real date")):
        with pytest.raises(TaskError, match=message) as err:
            tasks.push(task["id"], to, actor="owner")
        assert err.value.status == 422
    assert tasks.get(one["id"])["pushes"] == 0


# --- the one manual order ---------------------------------------------------------


def test_rank_moves_a_task_before_or_after_another_in_the_one_order(tasks):
    a, b, c = (tasks.create(t, actor="owner", due="2026-03-10") for t in "ABC")
    tasks.rank(c["id"], before=a["id"], actor="owner")
    assert titles(tasks.list_open()) == ["C", "A", "B"]
    tasks.rank(c["id"], after=b["id"], actor="owner")
    assert titles(tasks.list_open()) == ["A", "B", "C"]
    tasks.rank(a["id"], after=b["id"], actor="owner")
    assert titles(tasks.list_today()) == ["B", "A", "C"]


def test_ranking_against_a_task_that_is_gone_is_refused_and_changes_nothing(tasks, core):
    a, b = (tasks.create(t, actor="owner") for t in "AB")
    tasks.drop(b["id"], actor="owner")
    with pytest.raises(TaskError, match="no longer on the open list") as err:
        tasks.rank(a["id"], before=b["id"], actor="owner")
    assert err.value.status == 409
    for kwargs in ({}, {"before": a["id"]}, {"before": b["id"], "after": b["id"]}):
        with pytest.raises(TaskError) as err:
            tasks.rank(a["id"], actor="owner", **kwargs)
        assert err.value.status == 422
    with pytest.raises(TaskError, match="no longer on the open list"):
        tasks.rank(a["id"], after="01JABCDEFGHJKMNPQRSTVWXYZ0", actor="owner")
    # Even an event that slips past the checks cannot break the list.
    core.tape.print("task.ranked", {"task_id": a["id"], "before": b["id"]}, actor="owner")
    core.tape.print("task.ranked", {"task_id": b["id"], "after": a["id"]}, actor="owner")
    assert titles(tasks.list_open()) == ["A"]
    assert core.tape.after(0, kind="task.ranked")[2:] == []


# --- two processes, and size ------------------------------------------------------


def test_two_processes_on_one_data_folder_see_each_others_changes(cfg, core, tasks):
    other_core = open_core(cfg)
    try:
        other = service(other_core)
        other.clock = tasks.clock
        a = tasks.create("From web", actor="owner", due="2026-03-10")
        b = other.create("From worker", actor="agent:scout")
        assert titles(tasks.list_open()) == titles(other.list_open()) == ["From web",
                                                                         "From worker"]
        other.edit(a["id"], actor="agent:scout", title="Edited by worker")
        tasks.edit(a["id"], actor="owner", notes="Edited by web")
        seen = other.get(a["id"])
        assert (seen["title"], seen["notes"]) == ("Edited by worker", "Edited by web")
        other.complete(b["id"], actor="agent:scout")
        with pytest.raises(TaskError, match="already done"):
            tasks.complete(b["id"], actor="owner")
        assert tasks.board() == other.board()
    finally:
        other_core.close()


def test_a_change_that_lost_a_race_between_processes_does_not_count_twice(tasks, core):
    habit = tasks.create("Habit", actor="owner", due="2026-03-10", repeat="FREQ=DAILY")
    one = tasks.create("One", actor="owner", due="2026-03-10")
    # Both processes read the same state, then both print.
    kept = {"task_id": habit["id"], "kept_for": "2026-03-10", "kept_on": "2026-03-10",
            "next_due": "2026-03-11"}
    pushed = {"task_id": one["id"], "was": "2026-03-10", "to": "2026-03-11"}
    for _ in range(2):
        core.tape.print("task.kept", kept, actor="owner")
        core.tape.print("task.pushed", pushed, actor="owner")
        core.tape.print("task.completed", {"task_id": one["id"]}, actor="owner")
    after = tasks.get(habit["id"])
    assert (after["due"], after["kept_count"]) == ("2026-03-11", 1)
    assert tasks.board()["counts"]["done_today"] == 1  # the one round, once
    done = tasks.get(one["id"])
    assert (done["status"], done["due"]) == ("done", "2026-03-11")  # pushed once, not twice


def test_several_thousand_tasks_stay_fast_to_read_and_change(tasks, core):
    for i in range(3000):
        core.tape.print("task.filed", {
            "task_id": f"01JABCDEFGHJKMNPQRSTV{i:05d}", "title": f"Task {i}",
            "notes": "some notes " * 5, "area": "home", "due": f"2026-03-{1 + i % 28:02d}",
            "repeat": "FREQ=WEEKLY" if i % 3 == 0 else None,
        }, actor="owner")
    tasks.board()  # the first read folds everything
    started = time.perf_counter()
    board = tasks.board()
    tasks.complete(board["open"][1500]["id"], actor="owner")
    tasks.rank(board["open"][2999]["id"], before=board["open"][0]["id"], actor="owner")
    board = tasks.board()
    assert time.perf_counter() - started < 3
    assert board["counts"]["open"] == 3000 and board["open"][0]["title"] == "Task 2999"


def test_the_lens_keeps_only_a_capped_tail_of_finished_tasks_and_can_still_bring_one_back(
        tasks, core):
    first = tasks.create("The first", actor="owner", due="2026-03-10")
    tasks.complete(first["id"], actor="owner")
    gone = tasks.create("Dropped long ago", actor="owner")
    tasks.drop(gone["id"], actor="owner")
    for i in range(FINISHED_CAP):
        task_id = f"01JABCDEFGHJKMNPQRSTV{i:05d}"
        core.tape.print("task.filed", {"task_id": task_id, "title": f"Task {i}"}, actor="owner")
        core.tape.print("task.completed", {"task_id": task_id}, actor="owner")
    state = core.lenses.read(LENS)
    assert len(state["finished"]) == FINISHED_CAP
    assert first["id"] not in [t["id"] for t in state["finished"]]
    # Still readable from its own events, and reopening carries it back into the lens.
    assert tasks.get(first["id"])["status"] == "done"
    back = tasks.reopen(first["id"], actor="owner")
    assert (back["status"], back["title"], back["due"]) == ("open", "The first", "2026-03-10")
    assert tasks.restore(gone["id"], actor="owner")["status"] == "open"
    assert titles(tasks.list_open()) == ["The first", "Dropped long ago"]
    assert tasks.edit(first["id"], actor="owner", notes="alive")["notes"] == "alive"


# --- found on review --------------------------------------------------------------


@pytest.mark.parametrize("field, value", [
    ("due", "2026-W11-2"),  # an ISO week date is ten characters and Python reads it
    ("due", "2026-03-1\n"),
    ("due_time", "07:30\n"),
    ("assignee", "owner\n"),
    ("assignee", "agent:scout\n"),
])
def test_a_value_that_only_looks_right_is_refused_and_never_stored(tasks, core, field, value):
    values = {"due": "2026-03-12"} | {field: value}
    with pytest.raises(TaskError) as err:
        tasks.create("Odd", actor="owner", **values)
    assert err.value.status == 422
    assert core.tape.after(0, kind="task.filed") == []


def test_an_id_with_a_trailing_line_break_is_not_taken_for_a_task(tasks):
    one = tasks.create("One", actor="owner")
    with pytest.raises(TaskError) as err:
        tasks.get(one["id"] + "\n")
    assert err.value.status == 404
