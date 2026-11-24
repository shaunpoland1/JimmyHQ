"""The MCP door: how other systems talk to JimmyHQ.

An outside assistant, another agent platform or an automation service connects here as a
Model Context Protocol client and can use a small, fixed set of tools: see who is in the
Village and leave an agent a message, see what is on the Bench and file a card for the
owner, list and add tasks, search shared memory and propose a fact. The owner may also give a
client some of the tools his installed apps offer: only those that read or write only
into the app's own records, and no role's (``apptools.py``).

It is a way **in** to a private system, so it is built as a boundary:

- Off until the owner switches it on, and it listens on this computer only. Putting it
  on the internet is a step the owner takes with their own tunnel; nothing here does it.
- Its own port answers ``/mcp`` and nothing else (``listener.py``).
- Every client is named by the owner and has its own token (``jimmyhq.auth``), its own
  tools (none to begin with), its own daily limits and its own pace (``service.py``).
- What a client sends is data. A message to an agent is shown to it as untrusted text
  and starts nothing (``messages.py``). A proposed fact is a card for the owner.
- What a client is shown is shaped: no secrets, no addresses unless allowed, sizes
  capped and said aloud (``shape.py``, ``tools.py``).
- Every call is a ``door.called`` receipt on the Tape, with no argument text in it.

Other code uses::

    from jimmyhq import door
    door.door(core)                  # the service: clients, limits, calls
    door.start_listener(core)        # the web process: follow door.enabled and door.port

See ``docs/door.md``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from jimmyhq.door.service import Door, DoorError

if TYPE_CHECKING:
    from fastapi import APIRouter

    from jimmyhq.core import Core
    from jimmyhq.door.listener import Listener

__all__ = ["Door", "DoorError", "build", "door", "start_listener"]

log = logging.getLogger("jimmyhq.door")


def door(core: Core) -> Door:
    """This process's door. Limits that are counted in memory (requests a minute, wrong
    tokens) live in it, so only the web process should serve clients from it."""
    return core.service("door", Door)


def _on_decision(core: Core, decision: Any) -> None:
    from jimmyhq.door import tools

    for what, act in (("fact", tools.keep_fact), ("message", tools.deliver_approved)):
        try:
            act(core, decision)
        except Exception:  # noqa: BLE001 - the owner's click is recorded either way
            log.exception("an approved %s from an outside client could not be carried out",
                          what)


def _register() -> None:
    from jimmyhq import bench

    bench.on_decision("door.", _on_decision)


# The owner's approval of a proposed fact reaches the door wherever the click lands.
_register()


def build(core: Core) -> APIRouter:
    """The owner's API under ``/api/door``, and ``/mcp`` on the main address for an
    owner who chose to mount it there."""
    from jimmyhq.door.routes import build_router

    return build_router(core)


def start_listener(core: Core) -> Listener:
    """Start following the door's settings in the web process. The port opens only
    while ``door.enabled`` is on and the door is set to its own port. Call ``stop()`` on
    the result when the process ends."""
    from jimmyhq.door.listener import Listener

    listener = Listener(door(core), tuple(core.config.trusted_proxies))
    listener.start()
    return listener
