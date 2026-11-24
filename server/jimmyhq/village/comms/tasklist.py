"""What an agent has to do: the owner's standing priorities, then its tasks.

- A **priority** is standing. It never finishes: each piece of work under it is handed
  over on its own. Priorities are read from the village lens (``village.prioritized``).
- A **task** is one-off work in JimmyHQ's task list, assigned to ``agent:<id>``: something
  the owner asked for in chat (``take_on``), something the owner filed for the agent, or
  work of the agent's own (``start_own_task``).

An agent names an item by a short ref, ``P1`` or ``T4``, never by free text. A ref is
given once and never reused (table ``task_refs``), so a ref in an old diary entry still
means what it meant. The body knows items by the same refs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any

from jimmyhq import tasks
from jimmyhq.village import lens, record
from jimmyhq.village.comms import Refused, actor, limit, ms, owner

OWN_DONE_SHOWN_S = 86400  # a finished task stays on the list this long
OWN_OPEN_KEPT_S = 3 * 86400  # a task of its own left open this long drops off the list
ASKED_WITHIN_S = 2 * 86400  # a request is taken on within this long of being asked
ASKED_WORDS = 3  # consecutive words of the owner's that tie a task to their message
TITLE_CHARS = 300
PRIORITY = "priority:"  # how a priority's id is stored in task_refs
STANDING_NOTE = " (Standing: it doesn't finish. Hand over each piece of work with hand_over.)"


@dataclass(frozen=True)
class Item:
    ref: str  # "P1", "T4"
    title: str
    status: str  # ongoing | current | open | set aside | done
    source: str  # priority | owner | own | task
    task_id: str = ""  # the priority id or the task id behind the ref
    plain: str = ""  # the title without the notes added for the agent

    @property
    def standing(self) -> bool:
        return self.source == "priority"


# --- priorities, from the Tape -----------------------------------------------------


def priorities(v, agent_id: str | None = None) -> list[dict[str, Any]]:
    """Active priorities, oldest first; with ``agent_id``, those that are this agent's
    (a priority with no agents named is everyone's). Read from the village lens."""
    return lens.priorities(lens.read(v.core), agent_id, active_only=True)


def add_priority(v, title: str, why: str, details: str, *, suggested_by: str | None,
                 cause: str | None = None) -> str:
    """Record a new standing priority the owner approved. Returns its id."""
    pid = lens.priority_id(title, set(lens.read(v.core)["priorities"]))
    record.prioritized(v.core, {
        "priority_id": pid, "title": title, "why": why, "details": details, "agents": [],
        "active": True, "suggested_by": suggested_by,
    }, actor=record.SYSTEM, cause=cause)
    return pid


# --- refs ----------------------------------------------------------------------------


def clean_ref(text: object) -> str:
    """A ref as an agent typed it: ``t4``, ``[T4]`` and ``T4.`` all mean ``T4``."""
    return str(text or "").strip().strip("[]()\"'`.:").upper()


def _refs(v, agent_id: str) -> dict[str, str]:
    """task id -> ref, for one agent."""
    from jimmyhq.village.comms.benchlink import db

    conn, lock = db(v)
    with lock:
        rows = conn.execute("SELECT ref, task_id FROM task_refs WHERE agent = ?",
                            (agent_id,)).fetchall()
    return {r["task_id"]: r["ref"] for r in rows}


def ref_for(v, agent_id: str, task_id: str, letter: str,
            known: dict[str, str] | None = None) -> str:
    """The ref this agent knows an item by, given on first sight and never reused."""
    from jimmyhq.village.comms.benchlink import db

    known = _refs(v, agent_id) if known is None else known
    if task_id in known:
        return known[task_id]
    conn, lock = db(v)
    with lock:
        rows = conn.execute("SELECT ref, task_id FROM task_refs WHERE agent = ?",
                            (agent_id,)).fetchall()
        for r in rows:
            if r["task_id"] == task_id:
                return r["ref"]
        used = [int(r["ref"][1:]) for r in rows
                if r["ref"][:1] == letter and r["ref"][1:].isdigit()]
        ref = f"{letter}{max(used, default=0) + 1}"
        conn.execute("INSERT INTO task_refs (agent, ref, task_id) VALUES (?, ?, ?)",
                     (agent_id, ref, task_id))
    known[task_id] = ref
    return ref


def task_id_of(v, agent_id: str, ref: str) -> str | None:
    ref = clean_ref(ref)
    return next((tid for tid, r in _refs(v, agent_id).items() if r == ref), None)


# --- the list ------------------------------------------------------------------------


def _sources(v, agent_id: str) -> dict[str, str]:
    """ref -> "owner" or "own", from how each task came onto the list."""
    out = {}
    for e in v.log.query(agent=agent_id, kinds=["task_taken", "own_task"]):
        out[e.data["task"]] = "owner" if e.kind == "task_taken" else "own"
    return out


def items(v, agent_id: str) -> list[Item]:
    """The agent's list: priorities first, then its tasks."""
    a = v.world.agents.get(agent_id)
    current = a.current_task if a else None
    parked = set(a.parked) if a else set()
    known = _refs(v, agent_id)
    out: list[Item] = []
    for p in priorities(v, agent_id):
        ref = ref_for(v, agent_id, PRIORITY + p["id"], "P", known)
        title = p["title"] + (f" Why it matters: {p['why']}" if p["why"] else "") \
            + (f" Details: {p['details']}" if p["details"] else "") + STANDING_NOTE
        # A standing priority is never done or set aside on the list: it is always there.
        out.append(Item(ref, title, "current" if ref == current else "ongoing", "priority",
                        p["id"], p["title"]))
    sources = _sources(v, agent_id)
    board = tasks.service(v.core).board(assignee=actor(agent_id))
    now_ms = ms(v.clock.now())
    name = owner(v)
    # Open tasks in the list's own order; those just finished after them.
    listed = [*board["open"], *sorted(board["done"], key=lambda t: t["done_at"] or 0)]
    for t in listed:
        ref = ref_for(v, agent_id, t["id"], "T", known)
        source = sources.get(ref, "task")
        done = t["status"] == "done"
        if done and now_ms - (t["done_at"] or now_ms) > OWN_DONE_SHOWN_S * 1000:
            continue
        if source == "own" and not done and ref != current \
                and now_ms - t["created"] > OWN_OPEN_KEPT_S * 1000:
            continue  # its own task, left open for days: off the list, so the list stays short
        status = "done" if done else "set aside" if ref in parked else \
            "current" if ref == current else "open"
        note = {"owner": f" ({name} asked in chat)", "own": " (your own task)"}.get(source, "")
        filed_by = str(t.get("filed_by") or "")
        title = t["title"]
        if filed_by.startswith("app:"):
            title = app_title(filed_by.removeprefix("app:"), title)
            if t.get("notes") and not done:
                note += f" ({t['notes']})"  # work an app gave out: who gave it, and its details
        out.append(Item(ref, title + note, status, source, t["id"], title))
    return out


def app_title(app_id: str, title: str) -> str:
    """Rule ``app_titles_are_marked``: a title an app wrote is the app's words, wherever an
    agent or a coworker reads it. Who gave it comes first, then the title in quotes it
    cannot close, on one line, so it cannot pass as the owner's request or a new line."""
    said = " ".join(str(title).split()).replace('"', "'")
    return f'work for the app {app_id}: "{said}"'


def find(v, agent_id: str, ref: object) -> Item | None:
    ref = clean_ref(ref)
    return next((i for i in items(v, agent_id) if i.ref == ref), None)


def standing(v, agent_id: str) -> set[str]:
    """The refs of this agent's standing priorities. The body asks for these: being on
    one is not "having a task" at bedtime."""
    return {i.ref for i in items(v, agent_id) if i.standing}


def title_of(v, agent_id: str, ref: str | None) -> str:
    """A task's plain title, for telling someone else what an agent is on."""
    item = find(v, agent_id, ref) if ref else None
    return item.plain if item else (ref or "")


def task_known(v, agent_id: str, ref: object, verb: str = "happened") -> Item:
    """Rule ``task_known``: a task is named by a ref from the list. A near miss is
    offered, and the refs that exist are listed."""
    ref = clean_ref(ref)
    listed = items(v, agent_id)
    item = next((i for i in listed if i.ref == ref), None)
    if item is not None:
        return item
    refs = [i.ref for i in listed]
    near = get_close_matches(ref, refs, n=1, cutoff=0.8)
    by_title = [i.ref for i in listed if ref and ref.casefold() in i.plain.casefold()]
    hint = f" Did you mean {near[0]}?" if near else \
        f" Did you mean {by_title[0]}?" if len(by_title) == 1 else ""
    raise Refused("task_known",
                  f"Nothing {verb}: {ref[:40] or 'that'} is not on your list.{hint} Your list "
                  f"has {len(refs)} items" + (": " + ", ".join(refs) if refs else "")
                  + ". Name a task by its ref, like P1 or T4, never by its title.")


# --- taking on, starting -------------------------------------------------------------


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def owner_asked(v, agent_id: str, owner_said: object) -> dict[str, Any]:
    """Rule ``owner_asked``: a request is tied to the owner's message whose words it
    quotes. Without that an agent's own idea goes onto the list under the owner's name,
    and past the limits that only the owner's requests may pass."""
    from jimmyhq.village.comms import chat

    name = owner(v)
    asked = [m for m in chat.history(v, agent_id, since=v.clock.now() - ASKED_WITHIN_S)
             if m["sender"] == chat.OWNER]
    if not asked:
        raise Refused("owner_asked",
                      f"Nothing was added: take_on is for things {name} asked you in chat, "
                      f"and they have sent 0 messages in the last 2 days. For a piece of "
                      "work of your own, use start_own_task.")
    said = _words(owner_said)
    if len(said) >= ASKED_WORDS:
        for m in reversed(asked):
            mw = _words(m["text"])
            if any(mw[i:i + len(said)] == said for i in range(len(mw) - len(said) + 1)):
                return m
    raise Refused("owner_asked",
                  f"Nothing was added: take_on needs owner_said, at least {ASKED_WORDS} words "
                  f"in a row copied from the chat message where {name} asked for it"
                  + (f", and those {len(said)} words are not in anything they wrote in the "
                     "last 2 days" if said else "")
                  + ". For a piece of work of your own, use start_own_task; a quick check "
                  "needs no task: just do it.")


def requests_taken_per_day(v, agent_id: str) -> None:
    """Rule ``requests_taken_per_day``."""
    cap = limit(v, "requests_taken_per_day")
    n = v.log.count(agent=agent_id, kinds=["task_taken"], since=v.clock.now() - 86400)
    if n >= cap:
        raise Refused("requests_taken_per_day",
                      f"Nothing was added: you have taken on {n} requests in the last 24 "
                      f"hours and the limit is {cap}. You can still do the work: answer in "
                      "chat, and take it on tomorrow if it still needs a task.",
                      frustrate=False)


def _create(v, agent_id: str, title: str, notes: str) -> tuple[str, str]:
    try:
        task = tasks.service(v.core).create(title, actor=actor(agent_id), notes=notes,
                                            assignee=actor(agent_id))
    except tasks.TaskError as e:
        raise Refused("task_refused", f"Nothing was added: the task list would not take "
                                      f"that. {e.detail} Fix the title and try again.") from None
    return ref_for(v, agent_id, task["id"], "T"), task["id"]


def _title(title: object) -> str:
    return " ".join(str(title or "").split())[:TITLE_CHARS]


def take_on(v, agent_id: str, title: str, owner_said: object) -> str:
    """Turn something the owner asked in chat into a task. Returns its ref."""
    source = owner_asked(v, agent_id, owner_said)
    requests_taken_per_day(v, agent_id)
    title = _title(title)
    ref, task_id = _create(v, agent_id, title, f"Asked for in chat: \"{source['text'][:400]}\"")
    v.log.append("task_taken", agent_id, v.clock.now(), task=ref, task_id=task_id, title=title,
                 asked_in=source["id"])
    v.world.choose_task(agent_id, ref)
    return ref


def asked_by_owner(v, agent_id: str, ref: str | None) -> bool:
    """Whether this task is one the owner asked for in chat. The day's limits protect
    the owner from work they did not ask for, not from what they asked for."""
    if not ref:
        return False
    return any(e.data.get("task") == ref for e in v.log.query(agent=agent_id, kinds=["task_taken"]))


def own_duplicate(mine: list[Item], title: str) -> Item | None:
    """Rule ``own_duplicate``: the same title as an open task of its own."""
    key = title.casefold()
    return next((i for i in mine if i.status != "done" and i.plain.casefold() == key), None)


def start_own(v, agent_id: str, title: str) -> str:
    """Put a piece of the agent's own work on its list, marked as its own. Returns its
    ref. Its own work gets its own kind: no way past the day's hand-over limit, and it does
    not carry the owner's name."""
    title = _title(title)
    mine = [i for i in items(v, agent_id) if i.source == "own"]
    same = own_duplicate(mine, title)
    if same:
        raise Refused("own_duplicate",
                      f"Nothing was added: that is already on your list as {same.ref} (1 "
                      f"task with this title). Use focus_on {same.ref}.", frustrate=False)
    still = [i for i in mine if i.status != "done"]
    most = limit(v, "own_tasks_open")
    if len(still) >= most:
        raise Refused("own_tasks_open",
                      f"Nothing was added: you have {len(still)} tasks of your own open ("
                      + ", ".join(i.ref for i in still) + f") and the limit is {most}. Finish "
                      "one with finish_task first, or just do this without a task.")
    cap = limit(v, "own_tasks_per_day")
    n = v.log.count(agent=agent_id, kinds=["own_task"], since=v.clock.now() - 86400)
    if n >= cap:
        # A productive agent reaches this by being productive. It is the village's
        # limit, not a mistake.
        raise Refused("own_tasks_per_day",
                      f"Nothing was added: you have started {n} tasks of your own in the "
                      f"last 24 hours and the limit is {cap}. You can still do the work: it "
                      "just doesn't go on the list.", frustrate=False)
    ref, task_id = _create(v, agent_id, title, "Started by the agent as its own work.")
    v.log.append("own_task", agent_id, v.clock.now(), task=ref, task_id=task_id, title=title)
    v.world.choose_task(agent_id, ref)
    return ref


# --- focusing --------------------------------------------------------------------------


def resume_wait(v, agent_id: str, ref: str) -> None:
    """Rule ``resume_wait``: a task set aside can be picked back up by choice once it
    has been down a while, not at once."""
    a = v.world.agents[agent_id]
    parked = a.parked.get(ref)
    if parked is None:
        return
    wait = float(v.world.S("frustration.resume_by_choice_min")) * 60
    down = v.clock.now() - parked["since"]
    if down < wait:
        raise Refused("resume_wait",
                      f"Nothing changed: you set {ref} aside {down / 60:.0f} minutes ago and "
                      f"can pick it back up in {max(1, round((wait - down) / 60))} more "
                      "minutes. Do something else until then.")
    v.world.resume_by_choice(agent_id, ref)


def focus(v, agent_id: str, ref: object) -> tuple[Item, bool]:
    """Start or switch to a task. Returns the item and whether it was already in hand
    (then nothing changes: refocusing is not a fruitless repeat)."""
    item = task_known(v, agent_id, ref, "changed")
    a = v.world.agents[agent_id]
    already = a.current_task == item.ref
    resume_wait(v, agent_id, item.ref)
    if item.status == "done":
        raise Refused("already_done",
                      f"Nothing changed: {item.ref} is already done (1 finished task). Pick "
                      "an item on your list that is open.")
    if already:
        return item, True
    v.world.choose_task(agent_id, item.ref)
    return item, False


# --- finishing -------------------------------------------------------------------------


def evidence_own_ok_actions(v, agent_id: str, evidence: object) -> list[str]:
    """Rule ``evidence_own_ok_actions``: the cited events that are this agent's own
    successful actions. A completion counts only if it cites some."""
    cited = evidence if isinstance(evidence, list) else [evidence] if evidence else []
    out = []
    for eid in cited:
        e = v.log.get(str(eid).strip())
        if e and e.agent == agent_id and e.kind == "action" and e.data.get("ok") \
                and e.id not in out:
            out.append(e.id)
    return out


def files_touched(v, evidence: list[str]) -> list[str]:
    out: list[str] = []
    for eid in evidence:
        e = v.log.get(eid)
        p = (e.data.get("args") or {}).get("path") if e else None
        if p and str(p) not in out:
            out.append(str(p))
    return out


def finish(v, agent_id: str, ref: object, evidence: object) -> dict[str, Any]:
    """Mark a task done. Returns ``{item, verified, card}``; ``card`` is the done card on
    the owner's Bench, for a task that was not the agent's own."""
    from jimmyhq.village.comms import benchlink

    item = task_known(v, agent_id, ref, "was marked done")
    if item.standing:
        raise Refused("priority_never_finishes",
                      f"Nothing was marked done: {item.ref} is a standing priority, and a "
                      "standing priority does not finish (0 of them ever do). Hand over each "
                      "piece of work under it with hand_over instead.")
    if item.status == "done":
        raise Refused("already_done",
                      f"Nothing was marked done: {item.ref} is already done (1 finished "
                      "task). Pick an item on your list that is open.")
    cited = evidence if isinstance(evidence, list) else [evidence] if evidence else []
    verified = evidence_own_ok_actions(v, agent_id, cited)
    if not verified:
        v.world.complete(agent_id, item.ref, None)  # logged as a claim, earns nothing
        raise Refused("evidence_own_ok_actions",
                      f"Nothing was marked done: of the {len(cited)} event ids you cited, 0 "
                      "are successful actions of your own. Cite the ids of your own actions "
                      f"that show {item.ref} is done, like evt-123, from your recent actions.",
                      frustrate=False)
    try:
        tasks.service(v.core).complete(item.task_id, actor=actor(agent_id))
    except tasks.TaskError as e:
        raise Refused("task_refused", f"Nothing was marked done: the task list would not "
                                      f"close {item.ref}. {e.detail}") from None
    v.world.complete(agent_id, item.ref, verified)
    card = None
    if item.source != "own" and not given_by_app(v, item.task_id):
        touched = files_touched(v, verified)
        try:
            card = benchlink.file(
                v, agent_id, benchlink.DONE, f"Done: {item.plain}",
                summary=f"Finished, citing {len(verified)} of its own actions as evidence.",
                body=f"The agent finished this task and cited {len(verified)} of its own "
                     "successful actions as evidence. Reopen it if it is not done.",
                details=[("Evidence", ", ".join(verified)),
                         ("Files touched", ", ".join(touched))],
                lane="done", subject=f"task:{item.task_id}", task=item.ref,
                extra={"task_id": item.task_id},
                actions=[
                    {"id": "ok", "label": "Got it", "style": "primary", "needs_reason": False,
                     "closes": True, "moves_to": None},
                    {"id": benchlink.REOPEN, "label": "Reopen", "style": "default",
                     "needs_reason": True, "closes": True, "moves_to": None},
                ])
        except Refused:
            card = None  # the task is done either way; the card is a courtesy
    return {"item": item, "verified": verified, "card": card}


def reopen(v, agent_id: str, task_id: str, ref: str | None, note: str, *,
           evidence: list[str], cause: str | None = None) -> None:
    """The owner said a finished task is not done: back on the list, with their note."""
    try:
        tasks.service(v.core).reopen(task_id, actor="owner")
    except tasks.TaskError:
        pass  # already open again: the note below still reaches the agent
    v.log.append("task_reopened", agent_id, v.clock.now(), task=ref, task_id=task_id,
                 why=note or f"{owner(v)} reopened it", evidence=evidence)


def set_aside(v, agent_id: str, ref: object, note: str) -> Item:
    item = task_known(v, agent_id, ref, "was set aside")
    v.world.set_aside(agent_id, item.ref, note)
    tell_app_set_aside(v, agent_id, item, note)
    return item


# --- work an app gave out (apps.roles): the app reports the outcome its own way ---------


def given_by_app(v, task_id: str) -> str | None:
    """Rule ``app_work_files_no_done_card``: the app id, for a task an app gave out
    through one of its roles. Such a task files no "done" card for the owner."""
    if not task_id:
        return None
    try:
        filed_by = str(tasks.service(v.core).get(task_id).get("filed_by") or "")
    except tasks.TaskError:
        return None
    return filed_by.removeprefix("app:") if filed_by.startswith("app:") else None


def tell_app_set_aside(v, agent_id: str, item: Item, note: str) -> None:
    """An app that gave this task out hears that it was set aside, with the agent's note."""
    app_id = None if item.standing else given_by_app(v, item.task_id)
    if app_id is None:
        return
    from jimmyhq.apps import roles as app_roles

    work = app_roles.read(v.core)["work"].get(item.task_id)
    if work is None or work["state"] != app_roles.OPEN:
        return
    v.core.tape.print("apps.work_reported", {
        "app_id": app_id, "task_id": item.task_id, "state": "set_aside",
        "agent_id": agent_id, "note": (note or "")[:1000] or None},
        actor=actor(agent_id), subject=f"app:{app_id}")


# --- suggesting a priority -----------------------------------------------------------


def suggest_priority(v, agent_id: str, title: str, why: str, details: str = "") -> dict[str, Any]:
    """Put a suggested standing priority on the owner's Bench. Returns the card's row.

    Rules ``priority_suggestions_per_day`` and ``priority_duplicate``."""
    from jimmyhq.village.comms import benchlink

    name = owner(v)
    cap = limit(v, "priority_suggestions_per_day")
    n = benchlink.filed_today(v, agent_id, benchlink.PRIORITY)
    if n >= cap:
        raise Refused("priority_suggestions_per_day",
                      f"Nothing was suggested: you have suggested {n} new priorities today "
                      f"and the day's limit is {cap}. Keep this one in your notes and "
                      "suggest it tomorrow, or raise it with message_owner.", frustrate=False)
    title = _title(title)
    same = benchlink.duplicate_open_title(v, agent_id, benchlink.PRIORITY, title)
    if same:
        raise Refused("priority_duplicate",
                      f"Nothing was suggested: you have already suggested that (1 card, "
                      f"{same['card_id']}), and it is waiting for {name}. You will see their "
                      "answer in your bench view.")
    if any(p["title"].casefold() == title.casefold() for p in priorities(v)):
        raise Refused("priority_duplicate",
                      "Nothing was suggested: that is already 1 of the standing priorities. "
                      "Look at your list and work under it.")
    return benchlink.file(v, agent_id, benchlink.PRIORITY, title, why=why,
                          body=details, extra={"why": why.strip(), "details": details.strip()})
