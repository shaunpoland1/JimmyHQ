"""Plug points: where a full app's program joins the rest of JimmyHQ.

Each is allowed only by the matching permission the owner granted, read afresh every
time, and each reaches the program only through its inbox (``inbox.deliver``) and the app
API (``api``):

- ``jobs``: work on a schedule (``jobs``).
- ``bench``: cards of its own types, updated and withdrawn by it, and the owner's
  decisions handed back (``bench.file``); ``ladder.check``.
- ``memory``: sources Memory may learn from, each off until the owner switches it on
  (``memory.source``).
- ``tools``: tools for the owner's agents, off for every agent until given
  (``agents.tools``).
- ``settings``: its settings on the Settings screen, and a word when one changes.
- ``emit``: a nudge from the program to its own open pages.
- ``crashes``: a program held back after crashing is recorded and the owner told.
- ``work``: an app giving work to whoever holds one of its roles, and hearing back
  (``roles.get``, ``work.give``, ``work.withdraw``; see ``apps.roles``).

``setup(core)`` makes the calls known and declares what each process must know (the
settings, the memory sources); ``tick(core)`` is the worker's: every few seconds it
queues due jobs, follows the Tape for decisions and settings, and notices crash loops.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from jimmyhq.apps import bridge, inbox, kinds, store
from jimmyhq.apps.permissions import FULL
from jimmyhq.apps.plugs import bench, crashes, emit, jobs, settings, work
from jimmyhq.apps.plugs.state import state
from jimmyhq.bookmark import within_tape

if TYPE_CHECKING:
    from jimmyhq.core import Core
    from jimmyhq.scheduler import Scheduler

log = logging.getLogger("jimmyhq.apps.plugs")

TICK_S = 5
FOLLOWED = ("bench.decided", *settings.KINDS)
CURSOR = "tape"
PAGE = 200
PAGES = 10  # at most this many pages for one app in one tick


def add_calls() -> None:
    """The calls a program may make that the plug points add to the app API."""
    bench.add_calls()
    emit.add_call()
    work.add_calls()  # roles: roles.get, work.give, work.withdraw
    from jimmyhq.apps import feeds  # noqa: PLC0415 - feeds between apps: feed.read

    feeds.add_call()
    from jimmyhq.apps import counts  # noqa: PLC0415 - ui.count, for the sidebar

    counts.add_call()


add_calls()


def setup(core: Core) -> None:
    """What every process must know about the installed apps: their settings on the
    Settings screen, their sources in Memory, their kinds (``apps.sync``). Safe to call
    again whenever what is installed changes."""
    from jimmyhq.apps import sync  # noqa: PLC0415 - it imports these plug points

    sync.sync(core)


def start_cursor(core: Core, app_id: str) -> int:
    """Where an app starts reading the Tape: where it left off, and never before it was
    last installed, so an app installed again under the same id is not handed what was
    decided for the one before."""
    installed = core.tape.by_subject(kinds.subject(app_id), kind="apps.installed",
                                     newest_first=True, limit=1)
    floor = installed[0].seq if installed else 0
    return max(floor, settle_cursor(core, app_id))


def settle_cursor(core: Core, app_id: str) -> int:
    """An app's stored place on the Tape. A place past the Tape's end (the Tape was put
    back from an older backup than this file) goes back to that end, and is stored at
    once, so events printed while the app is switched off are not skipped later.
    Everything up to the end was handed over already."""
    kept = state(core).cursor(app_id, CURSOR) or 0
    ok = within_tape(core.tape, kept, f"app {app_id}")
    if ok != kept:
        state(core).set_cursor(app_id, CURSOR, ok)
    return ok


def follow(core: Core, app_id: str) -> int:
    """Hand an app what the Tape says it should hear: the owner's decisions on its cards
    and changes to its settings. Returns how many deliveries were queued. Waits, without
    moving on, while the app is switched off or has never said what it handles."""
    settle_cursor(core, app_id)  # before any wait: the Tape may be behind the place kept
    app = bridge.find_app(core, app_id)
    if app is None or not app.enabled or app.manifest.tier != FULL:
        return 0
    plugs = inbox.plugs(core, app_id)
    if not plugs:
        return 0
    cursor = start_cursor(core, app_id)
    queued = 0
    for _ in range(PAGES):
        page = core.tape.after(cursor, limit=PAGE, kinds=FOLLOWED)
        for event in page:
            for kind, data in (("decision", bench.decision_delivery(app, plugs, event)),
                               ("settings", settings.settings_delivery(app_id, event))):
                if data is not None:
                    inbox.deliver(core, app_id, kind, data, key=f"{kind}:{event.id}")
                    queued += 1
            cursor = event.seq
            state(core).set_cursor(app_id, CURSOR, cursor)
        if len(page) < PAGE:
            break
    state(core).set_cursor(app_id, CURSOR, cursor)
    return queued


def tick(core: Core) -> None:
    """The worker's one pass over every installed full app."""
    from jimmyhq.apps import sync  # noqa: PLC0415

    sync.keep_in_step(core)  # installed, updated or removed elsewhere since the last pass
    for app_id, row in sorted(store.installed(core).items()):
        if row.get("tier") != FULL:
            continue
        for part in (jobs.tick_app, follow):
            try:
                part(core, app_id)
            except Exception:  # noqa: BLE001 - one app must not stop the others
                log.exception("apps: %s for %s failed", part.__name__, app_id)
    try:
        crashes.notice(core)
    except Exception:  # noqa: BLE001
        log.exception("apps: noticing crash loops failed")
    # --- roles: tidy what a change elsewhere left behind, then tell apps about their work ---
    from jimmyhq.apps import roles  # noqa: PLC0415

    for step in (roles.reconcile, work.follow):
        try:
            step(core)
        except Exception:  # noqa: BLE001
            log.exception("apps: %s failed", step.__name__)


def register(core: Core, scheduler: Scheduler) -> None:
    setup(core)
    scheduler.every("apps.plugs", TICK_S, lambda: tick(core))
