"""Event kinds for the task list.

A task's whole life is these events. Filing and re-filing carry the full values,
so any one event says what the task was at that moment without reading back.
Dates are "YYYY-MM-DD" in the owner's calendar; the event's own ``ts`` is the
only clock time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind

_ID = "01JABCDEFGHJKMNPQRSTVWXYZ0"
_OTHER = "01JABCDEFGHJKMNPQRSTVWXYZ1"
_FILED = {"task_id": _ID, "title": "Put the bins out", "notes": "", "area": "home",
          "due": "2026-01-08", "due_time": "07:30", "repeat": "FREQ=WEEKLY;BYDAY=TH",
          "assignee": "owner"}


class _Values(Payload):
    task_id: str
    title: str
    notes: str = ""
    area: str = ""
    due: str | None = None
    due_time: str | None = None
    repeat: str | None = None
    assignee: str = "owner"


@kind("task.filed", example=_FILED)
class TaskFiled(_Values):
    """A new task joined the end of the list."""


@kind("task.refiled", example={**_FILED, "was": "2026-01-01"})
class TaskRefiled(_Values):
    """A task was edited. Carries every value, changed or not."""

    was: str | None  # the due date the edit was made against


class Round(Payload):
    """One kept round of a repeating task, with what is needed to undo it."""

    kept_for: str  # the due date this round was for
    kept_on: str  # the owner's calendar date when it was kept
    prev_due: str  # the due date to put back if the round is undone
    pushes: int = 0  # the push count to put back
    pushed_from: list[str | None] = Field(default_factory=list)  # and where from
    at: int  # when it was kept, unix ms


class Snapshot(Payload):
    """A finished task as it stood, for bringing back one the lens has let go of."""

    id: str
    title: str
    notes: str = ""
    area: str = ""
    due: str | None = None
    due_time: str | None = None
    repeat: str | None = None
    anchor: str | None = None
    assignee: str = "owner"
    status: Literal["done", "dropped"]
    created: int
    updated: int
    done_at: int | None = None
    dropped_at: int | None = None
    filed_by: str
    pushes: int = 0
    # The due date each push since the task was last done moved it from, oldest first.
    pushed_from: list[str | None] = Field(default_factory=list)
    kept: int = 0
    rounds: list[Round] = Field(default_factory=list)
    # Who last changed the title, notes and assignee: the actor of the event that did.
    edited_by: dict[str, str] = Field(default_factory=dict)


@kind("task.completed", example={"task_id": _ID})
class TaskCompleted(Payload):
    """A one-off task was done."""

    task_id: str


@kind("task.reopened", example={"task_id": _ID, "snapshot": None})
class TaskReopened(Payload):
    """A done one-off task is open again."""

    task_id: str
    snapshot: Snapshot | None = None


@kind("task.kept", example={"task_id": _ID, "kept_for": "2026-01-08", "kept_on": "2026-01-08",
                            "next_due": "2026-01-15"})
class TaskKept(Payload):
    """A round of a repeating task was kept. ``next_due`` is always later than both
    dates; when it is null the rule has run out and the task is done for good."""

    task_id: str
    kept_for: str
    kept_on: str
    next_due: str | None = None


@kind("task.unkept", example={"task_id": _ID, "kept_for": "2026-01-08", "snapshot": None})
class TaskUnkept(Payload):
    """The latest kept round was undone."""

    task_id: str
    kept_for: str
    snapshot: Snapshot | None = None


@kind("task.pushed", example={"task_id": _ID, "was": "2026-01-08", "to": "2026-01-10"})
class TaskPushed(Payload):
    """The task was postponed. This claims no work."""

    task_id: str
    was: str | None = None  # the due date it was pushed from
    to: str


@kind("task.unpushed", example={"task_id": _ID, "was": "2026-01-10", "to": "2026-01-08"})
class TaskUnpushed(Payload):
    """The latest push was taken back: the due date it moved the task from is put
    back and the push is no longer counted. The repeat rule keeps its anchor."""

    task_id: str
    was: str  # the due date the push left it on
    to: str | None = None  # the due date before the push; null if it had none


@kind("task.dropped", example={"task_id": _ID})
class TaskDropped(Payload):
    """The task left the open list without being done."""

    task_id: str


@kind("task.restored", example={"task_id": _ID, "snapshot": None})
class TaskRestored(Payload):
    """A dropped task is open again."""

    task_id: str
    snapshot: Snapshot | None = None


@kind("task.ranked", example={"task_id": _ID, "before": _OTHER, "after": None})
class TaskRanked(Payload):
    """The task moved in the one manual order, to sit just before or after another."""

    task_id: str
    before: str | None = None
    after: str | None = None
