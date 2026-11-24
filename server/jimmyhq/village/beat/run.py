"""One beat, stage by stage.

    asleep -> intake -> flush -> pick -> rest -> resume_note -> feel -> context
           -> think -> validate -> act -> pace -> save

``beat`` returns how many seconds to wait before the next one. ``safe_beat`` is what
the loop calls: whatever goes wrong inside a beat is logged and waited out.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from jimmyhq.village import intake
from jimmyhq.village.beat import act, asleep, pace, rest, resume_note, room, think, validate
from jimmyhq.village.capabilities import held as held_capabilities
from jimmyhq.village.catalog import Catalog
from jimmyhq.village.context import build
from jimmyhq.village.context.situation import reason_words
from jimmyhq.village.rules import rule

log = logging.getLogger("jimmyhq.village.beat")

FAULT_WAIT_FALLBACK_S = 120.0  # only if the setting itself cannot be read

STAGES = ("asleep", "intake", "flush", "pick", "rest", "resume_note", "feel", "context",
          "think", "validate", "act", "pace", "save")


def idle_wait(v: Any) -> float:
    """Seconds between looks at an agent that is asleep, resting or has nothing on offer."""
    return float(v.beat_s("idle_wait_s"))


def fault_wait(v: Any) -> float:
    """Seconds an agent waits after a beat that failed."""
    return float(v.beat_s("fault_wait_s"))


async def beat(v: Any, agent_id: str) -> float:
    st = v.state(agent_id)
    st.trace, st.busy = [], True
    try:
        st.heartbeat = await _beat(v, st, v.world.agents[agent_id])
    finally:
        st.busy = False
    return st.heartbeat


def _end(v: Any, st: Any, agent_id: str, wait: float) -> float:
    st.trace.append("save")
    v.save(agent_id)
    return wait


async def _beat(v: Any, st: Any, a: Any) -> float:
    agent_id, stage = a.id, st.trace.append

    stage("asleep")
    wait = asleep.asleep_wait(a, idle_wait(v))
    if wait is not None:
        await asleep.night_check(v, agent_id)
        return _end(v, st, agent_id, wait)

    stage("intake")
    intake.read_tape(v)

    stage("flush")  # held hand-overs and findings go out when there is room
    v.ask("comms.work", "flush", agent_id)
    v.ask("comms.findings", "flush", agent_id)

    stage("pick")
    mode = v.world.pick_mode(a)
    if mode == "sleep":
        return _end(v, st, agent_id, idle_wait(v))

    stage("rest")
    if mode == "rest":
        message = rest.owner_waiting(v, st, agent_id)
        wait = rest.rest_wait(mode, message is not None, idle_wait(v))
        if wait is not None:
            return _end(v, st, agent_id, wait)
        rest.rouse(v, st, a, message)

    stage("resume_note")
    await resume_note.write(v, st, agent_id)

    stage("feel")
    feelings = v.world.feelings(agent_id)
    room.check_empty_room(v, a)
    mode = a.mode
    extra = room.extra_tools(v, agent_id, mode)

    stage("context")
    capabilities = held_capabilities(v, agent_id)
    catalog = Catalog([*v.tools(), *await hand_tools(v, agent_id, capabilities),
                       *await app_tools(v, agent_id, capabilities)])
    schemas = catalog.schemas(mode, capabilities, extra, owner=v.owner_name())
    if not schemas:
        v.log.append("platform_fault", agent_id, v.clock.now(), what="no tools on offer",
                     detail=f"mode {mode}")
        return _end(v, st, agent_id, idle_wait(v))
    system = build.system_text(v, agent_id)
    view: Any = build.user_text(v, agent_id, feelings, st.last_result)
    # The page as it looks, beside the text view of it, for a model that takes pictures.
    picture = v.ask("tools.browser", "picture", agent_id, st.last_result)
    if picture is not None:
        view = [{"type": "text", "text": view}, picture]
    messages = [{"role": "user", "content": view}]

    ok = False
    for attempt in range(retries_after_refusal(v) + 1):
        seen = a.last_refusal
        stage("think")
        thought = await think.think(v, agent_id, messages, system, schemas, mode,
                                    retry=attempt > 0)
        if thought.fault:
            return _end(v, st, agent_id, fault_wait(v))
        stage("validate")
        if validate.dropped_asleep(a):
            return _end(v, st, agent_id, idle_wait(v))
        choice = validate.judge(v, agent_id, thought, catalog, mode, capabilities, extra,
                                retry=attempt > 0)
        if choice is not None:
            stage("act")
            # What the page looked like before, so the same push on the same page is
            # caught by the world's repeat guard.
            before = v.ask("tools.browser", "before", agent_id, choice.tool)
            ok = await act.act(v, st, a, catalog.tools[choice.tool], choice.args, mode,
                               before)
        refusal = a.last_refusal
        if ok or refusal is None or refusal is seen:
            break  # done, or a real failure the agent should see in its view first
        messages = think.with_refusal(messages, thought, reason_words(refusal.data["reason"]))

    stage("pace")
    wait = pace.heartbeat(v, a, mode, ok)
    return _end(v, st, agent_id, wait)


async def hand_tools(v: Any, agent_id: str, capabilities: list[str]) -> list[Any]:
    """The tools the owner's integration rules give this agent right now: one for each
    action switched on for it. They differ from agent to agent, so they join the catalog
    for this beat only. A fault in an integration must not cost the agent its beat."""
    offered = getattr(v.seam("tools.hands"), "offered", None)
    if offered is None or "hands" not in capabilities:
        return []
    try:
        return await offered(v, agent_id, taken={t.name for t in v.tools()})
    except Exception:  # noqa: BLE001 - see the docstring
        log.exception("could not list the integration tools of %s", agent_id)
        return []


async def app_tools(v: Any, agent_id: str, capabilities: list[str]) -> list[Any]:
    """The tools full apps offer, for an agent the owner gave that app's capability
    (``jimmyhq.apps.plugs.tools``). Like the integration tools, they join the catalog for
    this beat only, and a fault in an app must not cost the agent its beat."""
    if not any(c.startswith("app.") for c in capabilities):
        return []
    try:
        from jimmyhq.apps.plugs import tools as app_plugs

        return await app_plugs.offered(v, agent_id, capabilities,
                                       taken={t.name for t in v.tools()})
    except Exception:  # noqa: BLE001 - see the docstring
        log.exception("could not list the app tools of %s", agent_id)
        return []


@rule("refusal_retry", "village.beat.retries_after_refusal")
def retries_after_refusal(v: Any) -> int:
    """A refused action gets another go in the same beat, with the refusal in front of
    the agent, instead of after a whole heartbeat of nothing."""
    return int(v.beat_s("retries_after_refusal"))


@rule("beat_fault", "village.beat.fault_wait_s")
async def safe_beat(v: Any, agent_id: str) -> float:
    """The loop must never die: whatever a beat raises is logged, and the agent waits
    out the fault wait before its next one."""
    try:
        return await beat(v, agent_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - see the docstring
        log.exception("beat failed for %s", agent_id)
        with contextlib.suppress(Exception):
            v.log.append("brain_error", agent_id, v.clock.now(),
                         error=f"{type(e).__name__}: {e}"[:300])
        wait = FAULT_WAIT_FALLBACK_S
        with contextlib.suppress(Exception):
            wait = fault_wait(v)
        v.state(agent_id).heartbeat = wait
        return wait
