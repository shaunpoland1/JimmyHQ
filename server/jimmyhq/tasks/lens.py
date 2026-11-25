"""The task lens: the open list in its one manual order, and a tail of finished tasks.

Open tasks are held in full. Finished ones (done or dropped) pile up for ever on
the Tape, so the lens keeps only the newest ``FINISHED_CAP`` of them; an older
one can still be brought back because the event that reopens it carries a
snapshot rebuilt from the Tape.

The fold is pure and forgiving. Two processes can each check a task and then
print, so an event may arrive that no longer fits (a round kept twice, a rank
against a task that has just been dropped). Such an event changes nothing.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.lens import Lens
from jimmyhq.tape.registry import Event

FINISHED_CAP = 500  # finished tasks kept in the lens, newest last
ROUNDS_CAP = 10  # kept rounds remembered per task, which is how far undo reaches
PUSHES_CAP = 10  # pushes remembered per task, which is how far taking one back reaches
AREAS_CAP = 100

KINDS = ("task.filed", "task.refiled", "task.completed", "task.reopened", "task.kept",
         "task.unkept", "task.pushed", "task.unpushed", "task.dropped", "task.restored",
         "task.ranked")
_VALUES = ("title", "notes", "area", "due", "due_time", "repeat", "assignee")
# The fields an agent reads as its work: whoever last changed one of these wrote its words.
AGENT_READS = ("title", "notes", "assignee")


def initial() -> dict[str, Any]:
    return {"open": {}, "order": [], "finished": [], "areas": []}


def _finished_index(state: dict, task_id: str) -> int | None:
    for i in range(len(state["finished"]) - 1, -1, -1):
        if state["finished"][i]["id"] == task_id:
            return i
    return None


def find(state: dict, task_id: str) -> dict | None:
    """The task as the lens holds it, open or finished, or None."""
    task = state["open"].get(task_id)
    if task is None:
        i = _finished_index(state, task_id)
        task = state["finished"][i] if i is not None else None
    return task


def _finish(state: dict, task: dict) -> None:
    state["open"].pop(task["id"], None)
    if task["id"] in state["order"]:
        state["order"].remove(task["id"])
    state["finished"].append(task)
    del state["finished"][:-FINISHED_CAP]


def _revive(state: dict, task_id: str, status: str) -> dict | None:
    """Take a finished task with this status back onto the end of the open list."""
    i = _finished_index(state, task_id)
    if i is None or state["finished"][i]["status"] != status:
        return None
    task = state["finished"].pop(i)
    task.update(status="open", done_at=None, dropped_at=None)
    state["open"][task_id] = task
    state["order"].append(task_id)
    return task


def fold(state: dict[str, Any], event: Event) -> dict[str, Any]:
    p, kind, ts = event.payload, event.kind, event.ts
    task_id = p["task_id"]
    known = find(state, task_id)
    if known is None and p.get("snapshot") is not None:
        # A finished task the lens had let go of comes back with its event.
        known = dict(p["snapshot"])
        state["finished"].append(known)
        del state["finished"][:-FINISHED_CAP]

    if kind == "task.filed":
        if known is None:
            state["open"][task_id] = {
                "id": task_id, **{k: p[k] for k in _VALUES},
                "anchor": p["due"] if p["repeat"] else None,
                "status": "open", "created": ts, "updated": ts, "done_at": None,
                "dropped_at": None, "filed_by": event.actor, "pushes": 0, "pushed_from": [],
                "kept": 0, "rounds": [],
                "edited_by": {k: event.actor for k in AGENT_READS},
            }
            state["order"].append(task_id)
    elif known is None:
        return state
    elif kind == "task.refiled":
        values = {k: p[k] for k in _VALUES}
        if p["was"] != known["due"]:
            # The edit was made against an older due date: a round was kept or the
            # task pushed in between. The schedule as it now stands wins, or a kept
            # round would come due again.
            values.update(due=known["due"], repeat=known["repeat"])
            if known["due"] is None:
                values["due_time"] = None
        elif (p["due"], p["repeat"]) != (known["due"], known["repeat"]):
            # Rounds are counted from the due date given with the rule.
            known["anchor"] = p["due"] if p["repeat"] else None
            known["pushed_from"] = []  # a push from the old schedule cannot be taken back
        # Who last changed each field an agent reads. A task from before this was kept
        # has none: its fields are all the filer's.
        by = known.setdefault("edited_by", {k: known["filed_by"] for k in AGENT_READS})
        by.update({k: event.actor for k in AGENT_READS if values[k] != known[k]})
        known.update(values, updated=ts)
    elif kind == "task.completed":
        if known["status"] == "open":
            known.update(status="done", done_at=ts, updated=ts, pushes=0, pushed_from=[])
            _finish(state, known)
    elif kind == "task.kept":
        nxt = p["next_due"]
        fits = known["status"] == "open" and known["repeat"] and known["due"] == p["kept_for"]
        # The next round must be later than the day it was kept on and the day it
        # was kept for. A round that came due again the same day was the old bug.
        if fits and (nxt is None or nxt > max(p["kept_for"], p["kept_on"])):
            known["rounds"].append({"kept_for": p["kept_for"], "kept_on": p["kept_on"],
                                    "prev_due": known["due"], "pushes": known["pushes"],
                                    "pushed_from": known.get("pushed_from") or [],
                                    "at": ts})
            del known["rounds"][:-ROUNDS_CAP]
            known.update(kept=known["kept"] + 1, pushes=0, pushed_from=[], updated=ts)
            if nxt is None:
                known.update(status="done", done_at=ts)
                _finish(state, known)
            else:
                known["due"] = nxt
    elif kind == "task.unkept":
        rounds = known["rounds"]
        # A done task is only unkept if a round is what ended it (it still repeats).
        can_unkeep = known["status"] == "open" or (known["status"] == "done"
                                                   and known["repeat"])
        if can_unkeep and rounds and rounds[-1]["kept_for"] == p["kept_for"]:
            if known["status"] == "done":
                known = _revive(state, task_id, "done")
            last = known["rounds"].pop()
            known.update(due=last["prev_due"], pushes=last["pushes"],
                         pushed_from=last.get("pushed_from") or [],
                         kept=max(0, known["kept"] - 1), updated=ts)
    elif kind == "task.reopened":
        if _revive(state, task_id, "done") is not None:
            known.update(updated=ts, pushes=0, pushed_from=[])
    elif kind == "task.restored":
        if _revive(state, task_id, "dropped") is not None:
            known["updated"] = ts
    elif kind == "task.pushed":
        if known["status"] == "open" and known["due"] == p["was"]:
            origins = [*(known.get("pushed_from") or []), p["was"]][-PUSHES_CAP:]
            known.update(due=p["to"], pushes=known["pushes"] + 1, pushed_from=origins,
                         updated=ts)
    elif kind == "task.unpushed":
        origins = known.get("pushed_from") or []
        # Only the push that left the task where it now stands is taken back. The
        # anchor is not touched, so a repeating task's rounds are counted as before.
        if known["status"] == "open" and known["due"] == p["was"] and origins and \
                origins[-1] == p["to"]:
            known.update(due=p["to"], pushes=max(0, known["pushes"] - 1),
                         pushed_from=origins[:-1], updated=ts)
            if p["to"] is None:
                known["due_time"] = None
    elif kind == "task.dropped":
        if known["status"] == "open":
            known.update(status="dropped", dropped_at=ts, updated=ts)
            _finish(state, known)
    elif kind == "task.ranked":
        other = p.get("before") or p.get("after")
        order = state["order"]
        if known["status"] == "open" and other != task_id and other in state["open"]:
            order.remove(task_id)
            order.insert(order.index(other) + (0 if p.get("before") else 1), task_id)

    if kind in ("task.filed", "task.refiled"):
        area = p["area"]
        if area and area not in state["areas"] and len(state["areas"]) < AREAS_CAP:
            state["areas"].append(area)
    return state


class TaskLens(Lens):
    name = "tasks"
    version = 3  # 3: tasks remember who last changed the title, notes and assignee
    kinds = KINDS

    def initial(self) -> dict[str, Any]:
        return initial()

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        return fold(state, event)


LENS = TaskLens()
