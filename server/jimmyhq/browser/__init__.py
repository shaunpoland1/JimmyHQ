"""A headless browser an agent can drive: the smallest useful version of a computer of
its own.

    engine.py    which browser program, and exactly how it is started
    cdp.py       the DevTools protocol over a pipe
    fence.py     the one place every request must pass, and the proxy that enforces it
    page.py      the page driver: numbered elements, text, a picture, the actions
    session.py   one profile and one running browser per agent, stopped when idle
    fields.py    which boxes are never typed into, and which actions change something
    config.py    the owner's per-agent choices
    routes.py    what the owner's Browser tab reads

The agent's tools, and every rule about what it may do with them, are in
``jimmyhq.village.tools.browser``. See ``docs/browser.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import APIRouter

    from jimmyhq.browser.session import Manager
    from jimmyhq.core import Core

CAPABILITY = "browser"


def manager(core: Core) -> Manager:
    """This process's keeper of running browsers. Only the village process starts any."""
    from jimmyhq.browser.session import Manager

    return core.service("browser", Manager)


def build(core: Core) -> APIRouter:
    from jimmyhq.browser.routes import build_router

    return build_router(core)
