"""What the owner decided about one agent's browser: where it may go, and how far it may
go by itself when a form would change something.

The allow-list is on the Tape (``browser.configured``) and read through a lens. The
level is a row on the Bench's permission ladder, ``agent:<id>`` / ``browser`` /
``submit_form``, so the same table that holds every other "may it act alone" holds this
one. A row the owner never set starts at ``browser.default_submit_level``.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.lens import Lens
from jimmyhq.tape.registry import Event

TOOL, ACTION, CATEGORY = "browser", "submit_form", "browser_submit"
LEVELS = ("read", "approve", "alone")


class BrowserLens(Lens):
    name = "browser"
    version = 1
    kinds = ("browser.configured",)

    def initial(self) -> dict[str, Any]:
        return {"agents": {}}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        if event.actor == "owner":  # nobody else decides where a browser may go
            state["agents"][event.payload["agent_id"]] = {
                "allow_hosts": list(event.payload.get("allow_hosts") or [])}
        return state


class _Service:
    def __init__(self) -> None:
        self.lens = BrowserLens()


def allow_hosts(core: Any, agent_id: str) -> list[str]:
    """The sites this agent's browser is held to, or [] for no allow-list."""
    lens = core.service("browser.config", lambda _: _Service()).lens
    return list((core.lenses.read(lens)["agents"].get(agent_id) or {}).get("allow_hosts") or [])


def set_allow_hosts(core: Any, agent_id: str, hosts: list[str], *, actor: str) -> list[str]:
    from jimmyhq.browser.settings import host_patterns

    clean = host_patterns(hosts)
    core.tape.print("browser.configured", {"agent_id": agent_id, "allow_hosts": clean},
                    actor=actor, subject=f"agent:{agent_id}")
    return clean


def who(agent_id: str) -> str:
    return f"agent:{agent_id}"


def submit_level(core: Any, agent_id: str) -> str:
    """Rule ``browser_submit_level``: read, approve or alone for sending a form that
    changes something. The owner's row if there is one, else the default for a new
    agent."""
    from jimmyhq.bench import ladder

    row = ladder.row_of(core, who(agent_id), TOOL, ACTION)
    if row.get("set_at") is None and row.get("set_by") is None:
        return str(core.settings.get("browser.default_submit_level"))
    return ladder.check(core, who(agent_id), TOOL, ACTION, CATEGORY)


def set_submit_level(core: Any, agent_id: str, level: str, *, by: str) -> str:
    from jimmyhq.bench import ladder

    row = ladder.row_of(core, who(agent_id), TOOL, ACTION)
    if level == row["level"] and row.get("set_at") is None:
        # The ladder records a change, and an unset row already reads "approve". Step
        # down first so that the owner's choice is on the record and no longer the default.
        ladder.set_level(core, who(agent_id), TOOL, ACTION, "read", by=by, category=CATEGORY)
    ladder.set_level(core, who(agent_id), TOOL, ACTION, level, by=by, category=CATEGORY)
    return submit_level(core, agent_id)
