"""The task service: every rule about tasks, for the routes, the voice and the agents.

Each change is checked against the lens, printed to the Tape as one event, and
answered by folding that same event into the copy just read. Nothing is kept in
memory between calls, so the web and worker processes can both use it.

"Today" is always the owner's calendar date (the ``owner.timezone`` setting),
never the server's and never UTC.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from jimmyhq.core import Core
from jimmyhq.ids import now_ms, ulid
from jimmyhq.tape.registry import Event
from jimmyhq.tasks import recur
from jimmyhq.tasks.lens import AGENT_READS, KINDS, LENS, find, fold, initial

MAX_TITLE = 300
MAX_NOTES = 10_000
MAX_AREA = 40
_ID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")
_ASSIGNEE = re.compile(r"owner|agent:[a-z0-9][a-z0-9_.-]{0,63}")
_TIME = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_EDITABLE = ("title", "notes", "area", "due", "due_time", "repeat", "assignee")
_DAY_MS = 86_400_000


class TaskError(Exception):
    """A task request that cannot be carried out. ``detail`` says what to do."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --- checking what was given ----------------------------------------------------


def _text(value: Any, label: str, limit: int, *, single_line: bool = False) -> str:
    if not isinstance(value, str):
        raise TaskError(422, f"{label} must be text.")
    value = value.strip()
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise TaskError(422, f"{label} contains characters that cannot be saved.") from None
    if "\x00" in value:
        raise TaskError(422, f"{label} contains characters that cannot be saved.")
    if single_line:
        value = " ".join(value.split())
    if len(value) > limit:
        raise TaskError(422, f"{label} cannot be longer than {limit} characters.")
    return value


def _day(value: Any, label: str) -> str:
    if isinstance(value, datetime):
        raise TaskError(422, f"{label} must be a date written as YYYY-MM-DD.")
    if isinstance(value, date):
        value = value.isoformat()
    try:
        # The shape is checked first: fromisoformat also reads week dates ("2026-W01-1").
        ok = isinstance(value, str) and _DATE.fullmatch(value) is not None and \
            1900 <= date.fromisoformat(value).year <= 2999
    except ValueError:
        ok = False
    if not ok:
        raise TaskError(422, f"{label} must be a real date written as YYYY-MM-DD.")
    return value


def _clean(values: dict[str, Any], default_area: str, today: str,
           keep: tuple[str | None, str | None] | None = None) -> dict[str, Any]:
    """Check a full set of task values and return them in their stored form.

    ``keep`` is the (due, repeat) a task already has. While an edit leaves both
    alone the due date is not moved, so a pushed round stays where it was pushed.
    """
    title = _text(values["title"], "The title", MAX_TITLE, single_line=True)
    if not title:
        raise TaskError(422, "Give the task a title.")
    notes = _text(values["notes"] or "", "The notes", MAX_NOTES)
    area = values["area"]
    area = default_area if area is None else _text(area, "The area", MAX_AREA,
                                                   single_line=True).lower()
    assignee = values["assignee"]
    if not isinstance(assignee, str) or not _ASSIGNEE.fullmatch(assignee):
        raise TaskError(422, "The assignee must be owner or agent: followed by the agent's id, "
                             "such as agent:scout.")
    due = None if values["due"] in (None, "") else _day(values["due"], "The due date")
    due_time = values["due_time"] or None
    if due_time is not None and (not isinstance(due_time, str) or not _TIME.fullmatch(due_time)):
        raise TaskError(422, "The due time must be written as HH:MM, such as 07:30.")
    repeat = values["repeat"] or None
    if repeat is not None:
        if not isinstance(repeat, str):
            raise TaskError(422, "The repeat rule must be text, such as FREQ=WEEKLY;BYDAY=TH.")
        try:
            repeat = recur.canonical(repeat)
            if keep is None or (due, repeat) != keep:
                start = date.fromisoformat(due or today)
                first = recur.on_or_after(repeat, start, start)
                if first is None or first.year > 2999:
                    raise TaskError(422, "The repeat rule has no round on or after "
                                         f"{due or today}. Check its days, COUNT and UNTIL.")
                due = first.isoformat()  # a repeating task is always due on a round
        except recur.RecurError as exc:
            raise TaskError(422, str(exc)) from None
    if due_time is not None and due is None:
        raise TaskError(422, "A due time needs a due date. Add a date or remove the time.")
    return {"title": title, "notes": notes, "area": area, "due": due, "due_time": due_time,
            "repeat": repeat, "assignee": assignee}


# --- what a screen is given -----------------------------------------------------


def _local(ms: int, zone: tzinfo | None) -> date:
    """The calendar date of a moment in a zone; None is this computer's zone."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(zone).date()


def _view(task: dict, today: str, stalled_after: int, zone: tzinfo | None) -> dict[str, Any]:
    is_open = task["status"] == "open"
    due = task["due"]
    lapsed = 0
    if is_open and due and due < today:
        lapsed = (date.fromisoformat(today) - date.fromisoformat(due)).days
    last = task["rounds"][-1] if task["rounds"] else None
    return {
        "id": task["id"],
        "title": task["title"],
        "notes": task["notes"],
        "area": task["area"],
        "due": due,
        "due_time": task["due_time"],
        "repeat": task["repeat"],
        "repeat_text": recur.describe(task["repeat"]) if task["repeat"] else None,
        "assignee": task["assignee"],
        "status": task["status"],
        "created": task["created"],
        "updated": task["updated"],
        "done_at": task["done_at"],
        # The owner's calendar day it was done on. Screens compare this with the
        # board's date and never work a day out from done_at themselves.
        "done_on": _local(task["done_at"], zone).isoformat()
        if task["done_at"] is not None else None,
        "dropped_at": task["dropped_at"],
        "filed_by": task["filed_by"],
        # Who last changed each of the title, notes and assignee. Anyone but the owner
        # means those words are not the owner's, whoever filed the task.
        "edited_by": {k: (task.get("edited_by") or {}).get(k, task["filed_by"])
                      for k in AGENT_READS},
        "next_due": due if is_open else None,
        "due_today": bool(is_open and due == today),
        "lapsed_days": lapsed,
        "pushes": task["pushes"],
        "can_unpush": bool(is_open and task.get("pushed_from")),
        "stalled": bool(is_open and task["pushes"] >= stalled_after),
        "last_kept": last["kept_for"] if last else None,
        "last_kept_on": last["kept_on"] if last else None,
        "kept_count": task["kept"],
        # A done one-off was not ended by a round, so it is reopened, not unkept.
        "can_undo_round": bool(last) and (is_open or bool(
            task["status"] == "done" and task["repeat"])),
    }


class TaskService:
    def __init__(self, core: Core, clock: Callable[[], int] = now_ms):
        self.core = core
        self.clock = clock  # tests move the clock; nothing else should

    # --- the owner's calendar ---------------------------------------------------

    def _zone(self) -> tzinfo | None:
        name = self.core.settings.get("owner.timezone").strip()
        if name:
            try:
                return ZoneInfo(name)
            except (KeyError, ValueError, OSError):
                pass  # an unknown name falls back to this computer's zone
        return None

    def today(self) -> str:
        """Today's date where the owner lives."""
        return _local(self.clock(), self._zone()).isoformat()

    def _show(self, task: dict, today: str) -> dict[str, Any]:
        return _view(task, today, self._stalled_after(), self._zone())

    # --- reading ----------------------------------------------------------------

    def _state(self) -> dict[str, Any]:
        return self.core.lenses.read(LENS)

    def _from_tape(self, task_id: str) -> dict | None:
        """Rebuild one task the lens has let go of, from its own events."""
        mini = initial()
        for event in self.core.tape.by_subject(f"task:{task_id}", limit=100_000):
            if event.readable and event.kind in KINDS:
                fold(mini, event)
        return find(mini, task_id)

    def _task(self, state: dict, task_id: str) -> dict:
        task = None
        if isinstance(task_id, str) and _ID.fullmatch(task_id):
            task = find(state, task_id) or self._from_tape(task_id)
        if task is None:
            raise TaskError(404, "There is no task with this id. It may never have existed; "
                                 "read the task list for the current ids.")
        return task

    def _open(self, state: dict, task_id: str, verb: str) -> dict:
        task = self._task(state, task_id)
        if task["status"] == "done":
            raise TaskError(409, f"This task is already done. Reopen it before you {verb} it.")
        if task["status"] == "dropped":
            raise TaskError(409, f"This task was dropped. Restore it before you {verb} it.")
        return task

    def _stalled_after(self) -> int:
        return self.core.settings.get("tasks.stalled_after")

    def get(self, task_id: str) -> dict[str, Any]:
        return self._show(self._task(self._state(), task_id), self.today())

    def board(self, *, assignee: str | None = None, area: str | None = None) -> dict[str, Any]:
        """Everything the task screen shows, from one read of the lens."""
        state = self._state()
        settings = self.core.settings
        zone, now = self._zone(), self.clock()
        today = _local(now, zone).isoformat()
        stalled_after = self._stalled_after()
        area = area.strip().lower() if area is not None else None

        def wanted(task: dict) -> bool:
            return (assignee is None or task["assignee"] == assignee) and \
                (area is None or task["area"] == area)

        tasks = [state["open"][i] for i in state["order"] if i in state["open"]]
        open_ = [_view(t, today, stalled_after, zone) for t in tasks if wanted(t)]
        sheet = [t for t in open_ if t["due"] and t["due"] <= today]
        kept_today = [t for t in open_ if t["last_kept_on"] == today]

        done_days = settings.get("tasks.done_days")
        since = now - done_days * _DAY_MS
        done, dropped, done_today = [], [], 0
        for task in reversed(state["finished"]):
            when = task["done_at"] if task["status"] == "done" else task["dropped_at"]
            if not wanted(task) or when is None or when < since:
                continue
            view = _view(task, today, stalled_after, zone)
            (done if task["status"] == "done" else dropped).append(view)
            # A task that ended on its last round is counted with the rounds below.
            if task["status"] == "done" and not task["repeat"] and view["done_on"] == today:
                done_today += 1
        done.sort(key=lambda t: t["done_at"], reverse=True)
        dropped.sort(key=lambda t: t["dropped_at"], reverse=True)
        for task in [*tasks, *state["finished"]]:
            if wanted(task) and task["status"] != "dropped":
                done_today += sum(1 for r in task["rounds"] if r["kept_on"] == today)

        areas = [a.strip().lower() for a in settings.get("tasks.areas") if a.strip()]
        default_area = settings.get("tasks.default_area").strip().lower()
        seen = [t["area"] for t in tasks]
        areas = list(dict.fromkeys(a for a in [*areas, default_area, *state["areas"], *seen] if a))
        return {
            "date": today,
            "timezone": getattr(zone, "key", None) or "local",
            "stalled_after": stalled_after,
            "done_days": done_days,
            "open": open_,
            "today": sheet,
            "kept_today": kept_today,
            "done": done,
            "dropped": dropped,
            "areas": areas,
            "default_area": default_area,
            "counts": {
                "open": len(open_),
                "due_today": sum(1 for t in sheet if t["due_today"]),
                "lapsed": sum(1 for t in sheet if t["lapsed_days"]),
                "stalled": sum(1 for t in open_ if t["stalled"]),
                "done_today": done_today,
            },
        }

    def list_open(self, *, assignee: str | None = None, area: str | None = None) -> list[dict]:
        """Open tasks in the manual order."""
        return self.board(assignee=assignee, area=area)["open"]

    def list_today(self, *, assignee: str | None = None, area: str | None = None) -> list[dict]:
        """Open tasks due today or lapsed, in the manual order."""
        return self.board(assignee=assignee, area=area)["today"]

    # --- changing ---------------------------------------------------------------

    def _apply(self, state: dict, kind: str, task_id: str, payload: dict, actor: str,
               today: str) -> dict[str, Any]:
        """Print one event and answer with the task as the lens then holds it."""
        event: Event = self.core.tape.print(kind, {"task_id": task_id, **payload}, actor=actor,
                                            subject=f"task:{task_id}")
        if kind == "task.filed":
            fold(state, event)
            return self._show(find(state, task_id), today)
        # Read again rather than trust the copy the check was made on: another
        # process may have printed first, and then this event changed nothing.
        task = find(self._state(), task_id) or self._from_tape(task_id)
        return self._show(task, today)

    def create(self, title: str, *, actor: str, notes: str = "", area: str | None = None,
               due: str | date | None = None, due_time: str | None = None,
               repeat: str | None = None, assignee: str = "owner") -> dict[str, Any]:
        """File a new task at the end of the list."""
        today = self.today()
        default_area = self.core.settings.get("tasks.default_area").strip().lower()[:MAX_AREA]
        values = _clean({"title": title, "notes": notes, "area": area, "due": due,
                         "due_time": due_time, "repeat": repeat, "assignee": assignee},
                        default_area, today)
        # A new id cannot clash with anything, so there is no need to read the list.
        return self._apply(initial(), "task.filed", ulid(self.clock()), values, actor, today)

    def edit(self, task_id: str, *, actor: str, **changes: Any) -> dict[str, Any]:
        """Change some of a task's values. The event carries all of them (a re-filing)."""
        unknown = sorted(set(changes) - set(_EDITABLE))
        if unknown:
            raise TaskError(422, f"A task has no field called {unknown[0]}. The fields are: "
                                 f"{', '.join(_EDITABLE)}.")
        state, today = self._state(), self.today()
        task = self._open(state, task_id, "change")
        values = {k: task[k] for k in _EDITABLE} | changes
        if "area" in changes and changes["area"] is None:
            values["area"] = ""
        if values["due"] in (None, "") and "due_time" not in changes and not values["repeat"]:
            values["due_time"] = None  # a time cannot outlive its date
        values = _clean(values, "", today, keep=(task["due"], task["repeat"]))
        if all(values[k] == task[k] for k in _EDITABLE):
            return self._show(task, today)
        # ``was`` is the due date this edit was made against. If a round is kept or
        # the task pushed in between, the fold keeps that newer date.
        return self._apply(state, "task.refiled", task_id, values | {"was": task["due"]},
                           actor, today)

    def complete(self, task_id: str, *, actor: str) -> dict[str, Any]:
        """Mark a one-off task done, or keep the current round of a repeating one."""
        state, today = self._state(), self.today()
        task = self._open(state, task_id, "complete")
        if not task["repeat"]:
            return self._apply(state, "task.completed", task_id, {}, actor, today)
        last = task["rounds"][-1] if task["rounds"] else None
        if last and last["kept_on"] == today and task["due"] > today:
            # A second press of the same button must not eat tomorrow's round.
            raise TaskError(409, "A round of this task was already kept today, and the next "
                                 f"is not due until {task['due']}. Undo the round if that "
                                 "was a mistake.")
        # The next round is strictly after both the round kept and today, so a
        # round kept today can never come due again today.
        after = date.fromisoformat(max(task["due"], today))
        anchor = date.fromisoformat(task["anchor"] or task["due"])
        try:
            nxt = recur.next_after(task["repeat"], anchor, after)
        except recur.RecurError:
            nxt = None  # a stored rule this version can no longer read ends here
        if nxt is not None and nxt.year > 2999:
            nxt = None
        return self._apply(state, "task.kept", task_id, {
            "kept_for": task["due"], "kept_on": today,
            "next_due": nxt.isoformat() if nxt else None,
        }, actor, today)

    def undo_round(self, task_id: str, *, actor: str) -> dict[str, Any]:
        """Take back the latest kept round and put the due date back."""
        state, today = self._state(), self.today()
        task = self._task(state, task_id)
        if task["status"] == "dropped":
            raise TaskError(409, "This task was dropped. Restore it before you undo a round.")
        if not task["rounds"]:
            raise TaskError(409, "This task has no kept round to undo.")
        if task["status"] == "done" and not task["repeat"]:
            raise TaskError(409, "This task was done as a one-off, not by keeping a round. "
                                 "Reopen it instead.")
        return self._apply(state, "task.unkept", task_id, {
            "kept_for": task["rounds"][-1]["kept_for"],
            "snapshot": self._snapshot(state, task),
        }, actor, today)

    def reopen(self, task_id: str, *, actor: str) -> dict[str, Any]:
        """Open a done task again. For a repeating task that ran out, undo its last round."""
        state, today = self._state(), self.today()
        task = self._task(state, task_id)
        if task["status"] == "open":
            raise TaskError(409, "This task is already open.")
        if task["status"] == "dropped":
            raise TaskError(409, "This task was dropped, not done. Restore it instead.")
        if task["repeat"] and task["rounds"]:
            return self.undo_round(task_id, actor=actor)
        return self._apply(state, "task.reopened", task_id,
                           {"snapshot": self._snapshot(state, task)}, actor, today)

    def push(self, task_id: str, to: str | date, *, actor: str) -> dict[str, Any]:
        """Postpone a task to a later date. It is counted, and claims no work."""
        state, today = self._state(), self.today()
        task = self._open(state, task_id, "push")
        to = _day(to, "The date to push to")
        if to < today:
            raise TaskError(422, f"A task cannot be pushed into the past. Choose {today} "
                                 "or later.")
        if task["due"] and to <= task["due"]:
            raise TaskError(422, f"This task is due {task['due']}. Push it to a later date, "
                                 "or edit the task to bring it forward.")
        return self._apply(state, "task.pushed", task_id, {"was": task["due"], "to": to},
                           actor, today)

    def unpush(self, task_id: str, *, actor: str) -> dict[str, Any]:
        """Take back the latest push: the due date it moved the task from is put back
        and the push is no longer counted. A repeating task keeps its rounds."""
        state, today = self._state(), self.today()
        task = self._open(state, task_id, "take a push back on")
        origins = task.get("pushed_from") or []
        if not origins:
            if task["pushes"]:
                raise TaskError(409, "This task's due date or repeat rule was changed after "
                                     "it was pushed, or the push is too old to take back. "
                                     "Edit the task to set its due date instead.")
            raise TaskError(409, "This task has not been pushed since it was last done, so "
                                 "there is nothing to take back.")
        return self._apply(state, "task.unpushed", task_id,
                           {"was": task["due"], "to": origins[-1]}, actor, today)

    def drop(self, task_id: str, *, actor: str) -> dict[str, Any]:
        """Take a task off the open list without calling it done."""
        state, today = self._state(), self.today()
        task = self._task(state, task_id)
        if task["status"] == "dropped":
            raise TaskError(409, "This task is already dropped.")
        if task["status"] == "done":
            raise TaskError(409, "This task is already done, so there is nothing to drop.")
        return self._apply(state, "task.dropped", task_id, {}, actor, today)

    def restore(self, task_id: str, *, actor: str) -> dict[str, Any]:
        """Put a dropped task back on the end of the open list."""
        state, today = self._state(), self.today()
        task = self._task(state, task_id)
        if task["status"] != "dropped":
            raise TaskError(409, "Only a dropped task can be restored. This one is "
                                 f"{task['status']}.")
        return self._apply(state, "task.restored", task_id,
                           {"snapshot": self._snapshot(state, task)}, actor, today)

    def rank(self, task_id: str, *, actor: str, before: str | None = None,
             after: str | None = None) -> dict[str, Any]:
        """Move a task to sit just before or just after another open task."""
        if (before is None) == (after is None):
            raise TaskError(422, "Say where the task goes with either before or after, "
                                 "and the id of another open task.")
        other = before or after
        state, today = self._state(), self.today()
        task = self._open(state, task_id, "move")
        if other == task_id:
            raise TaskError(422, "A task cannot be placed before or after itself.")
        if not isinstance(other, str) or other not in state["open"]:
            raise TaskError(409, "The task to place it next to is no longer on the open "
                                 "list. Read the list again and choose another.")
        if state["order"].index(task_id) + (1 if before else -1) == state["order"].index(other):
            return self._show(task, today)  # already there
        return self._apply(state, "task.ranked", task_id, {"before": before, "after": after},
                           actor, today)

    @staticmethod
    def _snapshot(state: dict, task: dict) -> dict | None:
        """Only a task the lens no longer holds needs to travel with its event."""
        return None if find(state, task["id"]) is not None else task


def service(core: Core) -> TaskService:
    """This process's task service."""
    return core.service("tasks", TaskService)
