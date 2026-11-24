"""Lenses: every screen reads a stored view that is a fold over the Tape.

A lens is ``fold(state, event) -> state`` plus a version. The runner keeps the
folded state in ``views.db`` and catches it up as events arrive. Bumping the
version throws the state away and replays from the first event, which replaces
schema migrations for all derived data.

Folds must be pure: no clock, no I/O, no randomness. State must be JSON.

An erased event (``Tape.erase``) folds as nothing. An erase prints a
``tape.erased`` event naming the kinds it touched; a lens that folds one of
those kinds throws its state away when it meets that event and replays, so no
view keeps what was erased. A lens that knows exactly what an erasure took
from its state may say so instead (``Lens.erased``) and skip the replay. Either
way the old state is overwritten in ``views.db`` (``secure_delete``) and the
write-ahead log emptied.

Screens read through a lens. Reading the Tape directly on each request is the
habit this module exists to prevent.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from jimmyhq.tape.registry import Event
from jimmyhq.tape.store import ERASED_KIND, Tape, connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lens_state (
  name     TEXT PRIMARY KEY,
  version  INTEGER NOT NULL,
  last_seq INTEGER NOT NULL,
  state    TEXT NOT NULL
);
"""

_BATCH = 500
log = logging.getLogger("jimmyhq.lens")


class Lens(ABC):
    name: str
    version: int
    kinds: tuple[str, ...] | None = None  # only fold these kinds; None means all

    @abstractmethod
    def initial(self) -> dict[str, Any]: ...

    @abstractmethod
    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]: ...

    def erased(self, state: dict[str, Any], event: Event) -> dict[str, Any] | None:
        """Events this lens folds were erased; ``event`` is the ``tape.erased``
        record. Return the state with everything they contributed taken out,
        exactly as a replay would leave it, or None (the default) to be
        replayed from the first event. Pure, like ``fold``."""
        return None


class LensRunner:
    def __init__(self, tape: Tape, path: Path | str):
        self._tape = tape
        self._lock = threading.Lock()
        self._conn = connect(path)
        # A state that is replaced after an erase is overwritten, not unlinked.
        self._conn.execute("PRAGMA secure_delete=ON")
        self._conn.executescript(_SCHEMA)
        self._cache: dict[str, tuple[int, int, dict]] = {}  # name -> (version, last_seq, state)
        self._warned: set[tuple[str, int]] = set()

    def _load(self, lens: Lens) -> tuple[int, dict]:
        row = self._conn.execute(
            "SELECT version, last_seq, state FROM lens_state WHERE name = ?", (lens.name,)
        ).fetchone()
        if row is None or row["version"] != lens.version:
            return 0, lens.initial()
        return row["last_seq"], json.loads(row["state"])

    def _erased_since(self, lens: Lens, last_seq: int, end: int) -> list[Event]:
        """The erasures after ``last_seq`` that touched a kind this lens folds."""
        found: list[Event] = []
        cursor = last_seq
        while True:
            batch = self._tape.after(cursor, limit=_BATCH, kind=ERASED_KIND, upto=end)
            found += [e for e in batch if e.readable and (
                lens.kinds is None or not set(lens.kinds).isdisjoint(e.payload["kinds"]))]
            if len(batch) < _BATCH:
                return found
            cursor = batch[-1].seq or end

    def read(self, lens: Lens) -> dict[str, Any]:
        """Catch the lens up to the end of the Tape and return a copy of its state."""
        with self._lock:
            cached = self._cache.get(lens.name)
            if cached and cached[0] == lens.version:
                last_seq, state = cached[1], cached[2]
            else:
                last_seq, state = self._load(lens)
            end = self._tape.last_seq()
            rebuilt = False
            if last_seq > end:
                # The view has read further than the Tape goes: the Tape was put back
                # from a backup, or replaced. Its numbers will be used again by new
                # events, which would be taken as already folded, and the state holds
                # events this Tape does not have. Replay from the first event.
                log.warning("lens %s had read to event %s but the Tape ends at %s; "
                            "replaying it", lens.name, last_seq, end)
                last_seq, state, rebuilt = 0, lens.initial(), True
                self._conn.execute("DELETE FROM lens_state WHERE name = ?", (lens.name,))
            if end > last_seq:
                erasures = self._erased_since(lens, last_seq, end) if last_seq else []
                # Fold on a private copy. If a fold raises partway, the cached
                # state is untouched and the next read starts again cleanly.
                state = copy.deepcopy(state)
                for erasure in erasures:
                    # The state may hold what was erased. The lens takes it
                    # out itself, or the state is thrown away and replayed:
                    # the erased events now fold as nothing.
                    rebuilt = True
                    cleaned = lens.erased(state, erasure)
                    if cleaned is None:
                        last_seq, state = 0, lens.initial()
                        break
                    state = cleaned
                cursor = last_seq
                while True:
                    batch = self._tape.after(cursor, limit=_BATCH, kinds=lens.kinds, upto=end)
                    if not batch:
                        break
                    for event in batch:
                        # A payload this process could not check has a shape
                        # the fold was not written for; folding it could raise
                        # on every read from then on.
                        if event.readable:
                            state = lens.fold(state, event)
                        elif event.erased:
                            pass  # erased by the owner: it folds as nothing
                        elif (event.kind, event.v) not in self._warned:
                            self._warned.add((event.kind, event.v))
                            log.warning("lens %s skipped unreadable %s v%s events",
                                        lens.name, event.kind, event.v)
                    cursor = batch[-1].seq or cursor
                last_seq = end
                self._conn.execute(
                    "INSERT INTO lens_state (name, version, last_seq, state) VALUES (?,?,?,?)"
                    " ON CONFLICT(name) DO UPDATE SET version = excluded.version,"
                    " last_seq = excluded.last_seq, state = excluded.state"
                    " WHERE excluded.last_seq >= lens_state.last_seq"
                    " OR excluded.version != lens_state.version",
                    (lens.name, lens.version, last_seq, json.dumps(state, ensure_ascii=False)),
                )
                if rebuilt:
                    # The row was overwritten in place; empty the log too, so
                    # no earlier image of it stays there. A reader in another
                    # process only delays this until the next rebuild.
                    with contextlib.suppress(sqlite3.Error):
                        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._cache[lens.name] = (lens.version, last_seq, state)
            # A copy, so a caller cannot change what everyone else reads.
            return copy.deepcopy(state)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
