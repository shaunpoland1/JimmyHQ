"""Keeping what apps declare in step with what is installed, in every process.

JimmyHQ runs as several processes (web, worker, village, and a command such as ``jimmyhq
apps install`` run beside them), and an installed app makes each of them hold things in
its own memory:

- its settings, on a page of their own (``plugs.settings``), and the switch of each of its
  Memory sources (``plugs.memory``), in the settings registry;
- its Memory sources, in Memory's registry;
- its own event kinds (``schemas``), in the Tape's registry;
- its open database (``appdata``) and its parsed manifest (``store``).

Whoever changes what is installed (the Apps screen, the command line, the worker's update
job, a rollback after a crash loop) does it by printing on the Tape, and the ``apps`` lens
folds it. That record is the source of truth. A process does not follow the events itself:
it compares a cheap stamp before use (``keep_in_step``), at most every ``STEP_S``:

1. the Tape's last seq and how many kinds are kept: two indexed counts, no scan;
2. only when either moved, the ``apps`` lens (caught up incrementally, as every screen
   reads it) and, for each app, what decides what it declares (``entry``);
3. only when that changed, ``sync``: what is new is declared, what is gone withdrawn,
   what changed replaced.

The web process checks before answering any ``/api/`` request, so a request sees a change
made elsewhere at most ``STEP_S`` after it was printed. The worker checks at the start of
its apps tick and its update job, which is where it uses them; the village once a second;
a ``jimmyhq settings`` command syncs when it starts. The process that made the change
syncs at once.

``sync`` is safe to run any number of times: a page, source or kind already in step is
left alone. An app whose files no longer read is skipped with a logged sentence (the Apps
screen shows it as broken) and has nothing registered, and one whose settings or sources
cannot be declared has none of them: an app is never half registered, and the others are
unaffected. Kinds are only ever added: a kept kind stays readable after its app is removed,
so that what it wrote on the Tape can still be read.

What is not here is read afresh from the lens and ``apps.db`` on every use, in every
process: tools offered to agents, roles, card kinds, jobs, the inbox, the grants, the
hosts the owner gave an app himself (``owner_hosts``: ``bridge.find_app`` reads them from
the lens on every call, so a host removed elsewhere is unreachable at the next call), and
the feeds an app offers and reads (``feeds``: each ``feed.read`` reads the producer's manifest
and the reader's grant then, and registers kinds declared elsewhere first).
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jimmyhq.apps import schemas, store
from jimmyhq.apps.store import AppError

if TYPE_CHECKING:
    from jimmyhq.apps.manifest import Manifest
    from jimmyhq.core import Core

log = logging.getLogger("jimmyhq.apps.sync")

STEP_S = 0.5  # the longest a request waits to see a change another process made
_ENTRY = ("version", "tree", "enabled", "fingerprint", "installed_at", "updated_at")
_HELD = ("version", "tree", "enabled", "installed_at")  # what an open database is tied to

# The registries are this process's, whichever core asks: one sync at a time.
_LOCK = threading.RLock()


@dataclass
class Synced:
    """What one ``sync`` changed in this process."""

    settings: list[str] = field(default_factory=list)  # keys declared or replaced
    pages_gone: list[str] = field(default_factory=list)  # settings pages taken away
    sources: list[str] = field(default_factory=list)  # Memory sources registered
    kinds: int = 0  # kinds registered that were new here
    let_go: list[str] = field(default_factory=list)  # apps whose open files were let go
    broken: list[str] = field(default_factory=list)  # apps skipped: their files do not read

    def changed(self) -> bool:
        return bool(self.settings or self.pages_gone or self.sources or self.kinds
                    or self.let_go)


class _Step:
    """Where this core's process last stood."""

    def __init__(self, _core: Core):
        self.lock = threading.Lock()
        self.checked = float("-inf")  # time.monotonic() of the last look
        self.marks: tuple[int, int] | None = None  # (Tape's last seq, kinds kept)
        self.apps: dict[str, tuple[Any, ...]] | None = None  # as of the last sync
        self.kinds = -1  # kinds kept, as of the last sync


def _step(core: Core) -> _Step:
    return core.service("apps.sync", _Step)


# --- the rules ------------------------------------------------------------------------------


def due(last_s: float, now_s: float, every_s: float | None = None) -> bool:
    """Whether it is time to look again: at most once every ``every_s`` seconds
    (``STEP_S``), so a change made elsewhere is seen no later than that."""
    return now_s - last_s >= (STEP_S if every_s is None else every_s)


def entry(row: dict[str, Any]) -> tuple[Any, ...]:
    """What decides what an installed app declares here: which version, whether it is
    on, what it was granted, and when it was installed and last switched."""
    return tuple(row.get(k) for k in _ENTRY)


def installed_now(core: Core) -> dict[str, tuple[Any, ...]]:
    """Every installed app's ``entry``, read from the ``apps`` lens."""
    return {app_id: entry(row) for app_id, row in store.installed(core).items()}


def to_let_go(before: dict[str, tuple[Any, ...]],
              now: dict[str, tuple[Any, ...]]) -> list[str]:
    """The apps whose open files this process must let go of: removed, switched off, or
    on another version since it last looked. A new grant alone keeps them open."""
    held = [_ENTRY.index(k) for k in _HELD]
    out = []
    for app_id, was in sorted(before.items()):
        is_now = now.get(app_id)
        if is_now is None or any(was[i] != is_now[i] for i in held):
            out.append(app_id)
    return out


def readable(core: Core) -> tuple[list[Manifest], list[str]]:
    """The manifest of every installed app whose files read, and the ids of those whose
    files do not: each of those is logged and skipped."""
    found, broken = [], []
    for app_id in sorted(store.installed(core)):
        try:
            found.append(store.manifest_of(core, app_id))
        except AppError as error:
            log.warning("The app %s is skipped, so nothing of it is registered: %s",
                        app_id, error.detail)
            broken.append(app_id)
    return found, broken


# --- doing it -------------------------------------------------------------------------------


def let_go(core: Core, app_ids: list[str]) -> None:
    """Close this process's open database of each app, so it is opened again at its new
    place and shape, and forget manifests read for versions no longer installed."""
    from jimmyhq.apps import appdata  # noqa: PLC0415 - it imports this package

    for app_id in app_ids:
        with contextlib.suppress(Exception):
            appdata.stores(core).drop(app_id)
    rows = store.installed(core)
    cache = store.service(core).manifests
    for key in list(cache):
        row = rows.get(key[0])
        if row is None or (row["version"], row["tree"]) != key[1:]:
            cache.pop(key, None)


def _part(what: str, fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - one registry's trouble must not stop the others
        log.exception("apps: keeping %s in step failed", what)
        return None


def sync(core: Core) -> Synced:
    """Put everything apps declare in this process in step with what is installed now."""
    from jimmyhq.apps.plugs import memory as app_memory  # noqa: PLC0415 - imports this
    from jimmyhq.apps.plugs import settings as app_settings  # noqa: PLC0415

    with _LOCK:
        step = _step(core)
        kept = schemas.kind_store(core).total()
        now = installed_now(core)
        manifests, broken = readable(core)
        done = Synced(broken=broken)
        pages = _part("settings", app_settings.sync_pages, manifests)
        if pages is not None:
            done.settings, done.pages_gone = pages
        done.sources = _part("memory sources", app_memory.register_all, core, manifests) or []
        done.kinds = _part("kinds", schemas.load_all, core) or 0
        done.let_go = to_let_go(step.apps or {}, now)
        _part("open files", let_go, core, done.let_go)
        step.apps, step.kinds = now, kept
    if done.changed():
        log.info("apps: in step (%d settings, %d pages gone, %d sources, %d kinds, %d let go)",
                 len(done.settings), len(done.pages_gone), len(done.sources), done.kinds,
                 len(done.let_go))
    return done


def keep_in_step(core: Core, *, now_s: float | None = None) -> Synced | None:
    """Sync when what is installed changed since this process last looked, looking at
    most every ``STEP_S``. Returns what the sync changed, or None when none was needed."""
    step = _step(core)
    now_s = time.monotonic() if now_s is None else now_s
    if not due(step.checked, now_s):
        return None
    with step.lock:
        if not due(step.checked, now_s):
            return None  # another thread looked meanwhile
        step.checked = now_s
        # The marks are read before the lens, so a change printed in between is seen
        # at the next look.
        marks = (core.tape.last_seq(), schemas.kind_store(core).total())
        if marks == step.marks:
            return None
        step.marks = marks
        if step.apps == installed_now(core) and step.kinds == marks[1]:
            return None
        return sync(core)


def due_now(core: Core) -> bool:
    """Whether this process is due for a look: cheap enough for every request."""
    return due(_step(core).checked, time.monotonic())
