"""What reaches the village from outside its own process.

The web process cannot touch a body: it prints to the Tape (a chat message, a
priority, a new configuration, a decision on the Bench) or leaves a row in ``commands``
(a birth, a retirement, a rollback). This module reads both, by cursor, and turns them
into things the bodies can feel: a chat line in the village log, an early wake, a
command run between beats.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq.bookmark import within_tape
from jimmyhq.village import roster
from jimmyhq.village.rules import rule

log = logging.getLogger("jimmyhq.village.intake")

CURSOR = "tape"
KINDS = ("village.said", "village.prioritized", "village.configured", "bench.decided",
         "hand.outcome", "hands.rule_set")
BATCH = 200


@rule("wake_early")
def wake(v: Any, agent_id: str, call: Any = None) -> None:
    """End an agent's wait between beats: the owner wrote, a coworker spoke, or a
    decision came back. An urgent message (``call``, its chat event) also wakes a
    sleeper, once that sleep's memory work is done, and keeps it up to answer."""
    if agent_id not in v.world.agents:
        return
    if call is not None:
        v.world.wake_for_call(agent_id, call)
    v.wake(agent_id)


def _said(v: Any, event: Any) -> None:
    """A chat line from the Tape goes into the village log once (``chat.mirror`` knows
    the lines the agent's own ``message_owner`` already logged). The mirror wakes the
    agent; an urgent line also gets it out of bed."""
    if event.payload.get("agent_id") in v.world.agents:
        v.ask("comms.chat", "mirror", event)


def _decided(v: Any, event: Any) -> None:
    """The owner's decision on a village card: applied, logged and the agent woken by
    ``benchlink.apply_decision``, which also checks that the card is really ours."""
    kind = str(event.payload.get("card_kind", ""))
    if kind.startswith("village."):
        v.ask("comms.benchlink", "apply_decision", event)
    elif kind.startswith("browser."):  # a form an agent asked to send from its browser
        v.ask("tools.browser", "apply_decision", event)


def _configured(v: Any, event: Any) -> None:
    agent_id = event.payload.get("agent_id")
    if agent_id:
        v.forget_cfg(agent_id)
        if agent_id in v.world.agents:  # a new capability brings its section of the docs
            v.ask("docs", "capability_sections", agent_id)


def _prioritized(v: Any, event: Any) -> None:
    named = event.payload.get("agents") or list(v.world.agents)
    for agent_id in named:
        wake(v, agent_id)


def _hand_outcome(v: Any, event: Any) -> None:
    """What became of a request to use an integration: the agent's answer."""
    v.ask("comms.benchlink", "apply_outcome", event)


def _hand_rule(v: Any, event: Any) -> None:
    """The owner switched an integration action on: its section of the docs arrives."""
    agent_id = event.payload.get("agent")
    if agent_id in v.world.agents:
        v.ask("docs", "capability_sections", agent_id)


HANDLERS = {"village.said": _said, "bench.decided": _decided,
            "village.configured": _configured, "village.prioritized": _prioritized,
            "hand.outcome": _hand_outcome, "hands.rule_set": _hand_rule}


def read_tape(v: Any) -> int:
    """Take in every Tape event since the cursor. Returns how many were read. One event
    that cannot be handled is logged and passed over, so it cannot block the rest. The
    cursor is kept in the roster's ``cursors`` table.

    A cursor past the Tape's last event means the Tape is older than the Village's own
    file (a restore from a backup that copied them in the other order). The events up to
    the Tape's end were all taken in already, so the cursor goes back to that end: nothing
    is taken in twice, and the next new event, which reuses a number the Village thought
    it had read, is taken in.

    An event the owner had erased (``Tape.erase``) is stepped over: it keeps its place but
    no longer says anything."""
    seq, n = roster.cursor(v.core, CURSOR), 0
    kept = within_tape(v.core.tape, seq, "the Village")  # the end is read after the cursor
    if kept != seq:
        roster.rewind_cursor(v.core, CURSOR, kept)
        seq = kept
    while True:
        events = v.core.tape.after(seq, limit=BATCH, kinds=KINDS)
        for event in events:
            try:
                if event.readable:  # an erased event keeps its place and says nothing
                    HANDLERS[event.kind](v, event)
            except Exception:  # noqa: BLE001 - see the docstring
                log.exception("could not take in %s (%s)", event.id, event.kind)
            seq = event.seq
            roster.set_cursor(v.core, CURSOR, seq)
        n += len(events)
        if len(events) < BATCH:
            return n


def run_commands(v: Any) -> int:
    """Run the commands that are waiting, oldest first, and store each result. A command
    for an agent in the middle of a beat waits for the next pass, so a body never
    changes under a beat."""
    handle = getattr(v.seam("roster"), "handle", None)
    if handle is None:
        return 0  # nothing here can run them yet; they keep
    done = 0
    for command in roster.pending(v.core):
        agent_id = command.get("agent")
        if agent_id and v.state(agent_id).busy:
            continue
        try:
            result = handle(v, command) or {}
        except Exception as e:  # noqa: BLE001 - a failed command is answered, not fatal
            log.exception("command %s (%s) failed", command["id"], command["kind"])
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        roster.finish(v.core, command["id"], result)
        done += 1
    return done


def poll(v: Any, commands: bool = True) -> None:
    read_tape(v)
    if commands:
        run_commands(v)
