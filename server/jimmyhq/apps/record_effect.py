"""The ``record`` effect: a tool that only writes into the app's own records.

A tool declared with ``"effect": "record"`` (such as "submit a draft") promises that
nothing leaves the computer and nothing outside the app changes. So it needs no row on
the ladder and is never held for approval, and every run leaves a receipt on the Tape
(``apps.tool_recorded``: which agent, through which role, whether the app took it;
never what).

**What JimmyHQ enforces** (rule ``record_tool_reaches_nothing_outside``): while a record
tool's delivery is in the app's hands (handed over and not yet answered), every call that
reaches outside the app is refused, from its program and from its page alike: the
network, the models (which may be hosted), the owner's tasks and Bench, handing out work,
the Vault. Reading its own records, storage, settings and the Tape still works.

**A tool called through the door** (rule ``door_tool_reaches_nothing_outside``) is held to
the same, whatever its effect: an outside client chose its arguments, so while its
delivery (``via`` ``"door"``) is in the app's hands, nothing the app asks for may reach
outside it either. A ``read`` tool an agent calls is not held back: an agent's own tools
(Reader's ``search`` and ``read``, say) reach the web on purpose.

"In the app's hands" includes a delivery whose asker gave up waiting (``inbox.expire``):
the app may still be working on it. It stays counted until the app answers it, its
program starts again, or ``inbox.GIVEN_UP_HELD_MS`` has passed (see ``inbox``).

**What it cannot enforce.** A full app runs as the owner on this computer. JimmyHQ cannot
stop its program from opening a network connection itself, or from acting later, after it
has answered, on what an agent recorded: a job that sends a stored draft, say. What stands
between a record and the outside world then is everything else: the permissions the owner
granted the app, the Bench for anything the owner must decide, and the app's own row on
the ladder (``ladder.check`` always asks as ``app:<id>``, never as the agent). A
``record`` tool is a promise by the app's author, which the guard makes hard to break by
accident and impossible to break through JimmyHQ while the call is open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jimmyhq.apps.bridge import BridgeError

if TYPE_CHECKING:
    from jimmyhq.core import Core

RECORD = "record"
DOOR = "door"  # the ``via`` of a delivery an outside client asked for
# Calls that reach outside the app. A call not listed here only reads, or writes into the
# app's own data. Names other packages may add later are listed too, so they are covered
# the day they arrive.
OUTSIDE_CALLS = frozenset({
    "net.fetch", "model.ask", "model.embed", "web.search", "web.read",
    "tasks.create", "tasks.complete", "bench.file", "bench.update", "bench.withdraw",
    "work.give", "work.withdraw", "vault.put", "uploads.put",
})


def held_in_hand(core: Core, app_id: str) -> tuple[str, str] | None:
    """(tool name, why) for a tool delivery this app is handling right now that keeps it
    inside: ``"door"`` for one an outside client called, ``"record"`` for a record tool.
    None when there is none."""
    from jimmyhq.apps import inbox  # noqa: PLC0415 - it imports the bridge

    found = inbox.inbox(core).tools_in_hand(app_id)
    for data in found:
        if data.get("via") == DOOR:
            return str(data.get("name") or "a tool"), DOOR
    for data in found:
        if data.get("effect") == RECORD:
            return str(data.get("name") or "a record tool"), RECORD
    return None


def record_in_hand(core: Core, app_id: str) -> str | None:
    """The name of a record tool, or of a tool called through the door, this app is
    handling right now, or None."""
    held = held_in_hand(core, app_id)
    return None if held is None else held[0]


def check_outside_call(core: Core, app_id: str, call: str) -> None:
    """Rules ``record_tool_reaches_nothing_outside`` and
    ``door_tool_reaches_nothing_outside``: refuse a call that reaches outside the app while
    one of its record tools, or any tool an outside client called through the door, is
    being handled."""
    if call not in OUTSIDE_CALLS:
        return
    held = held_in_hand(core, app_id)
    if held is None:
        return
    busy, why = held
    if why == DOOR:
        raise BridgeError("not_allowed", (
            f"Your app is handling \"{busy}\", which an outside client called through the "
            f"door with arguments it chose, so {call} did nothing: while such a tool is in "
            "your app's hands, nothing your app asks for may reach outside it. Answer the "
            "tool from your app's own records first, and do anything that reaches outside "
            "later, under the permissions the owner granted."))
    raise BridgeError("not_allowed", (
        f"Your app is handling \"{busy}\", a tool declared with the effect \"record\", "
        f"which promises to write only into your app's own records, so {call} did "
        "nothing. Answer the tool first, and do anything that reaches outside later, "
        "under the permissions the owner granted (and ask with ladder.check where it "
        "leaves the building)."))
