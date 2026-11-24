"""The Library and its record: files on disk, a small database beside them.

The files are the truth. ``memory.db`` holds what can be worked out from them
(which notes exist, their passages, the search index) and the few things that
do not belong in a file a person reads (who saved a fact, how sure they were,
what it came from). Every change is also printed to the Tape.

One engine, ``_sync``, brings the record up to date with what is on disk. A
scan runs it over the whole Library; saving, moving or deleting a note in the
app changes the file and then runs it over that one path. So a change made in
the app and the same change made by hand leave the same record, apart from
who is named as the actor.

Nothing here is kept in memory between calls except caches that can be thrown
away: the web and worker processes share the data folder, and a cross-process
file lock (``memory.lock``) makes every change to the Library one at a time.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import re
import stat
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jimmyhq.core import Core
from jimmyhq.ids import now_ms, ulid
from jimmyhq.memory import notes as md
from jimmyhq.memory.errors import LibraryError
from jimmyhq.memory.paths import clean_path, ignorable, inside, is_link, is_note_name
from jimmyhq.memory.settings import KIND, kind_notes
from jimmyhq.memory.store import Store, scrub, secure
from jimmyhq.tape.registry import Event
from jimmyhq.tape.store import TapeConflict, connect

log = logging.getLogger("jimmyhq.memory")

INDEX_VERSION = 1  # bump when chunking or the index layout changes: forces a rebuild
MAX_FACT = 2000  # characters in one fact
MAX_REASON = 300
MAX_EVIDENCE = 20
DISK = "owner:disk"  # the actor for a change found on disk
REF = "sha256:"  # a note version's body is named by its hash, as the Vault names blobs
DAY_MS = 86_400_000
PRUNE_EVERY_MS = 3_600_000  # how often the worker applies the history limits
LINE_REMOVED = "Its line was removed from the note."
NOTE_GONE = "Its note was removed or can no longer be read."
NOTE_DELETED = "Its note was deleted."
_RACY_NS = 2_000_000_000  # a file changed this recently is read again next scan
# The temp file a write goes through: short, so the longest note name still fits
# on disk, and recognisable, so one left behind by a crash can be cleared away.
_TEMP = re.compile(r"\.[0-9A-HJKMNP-TV-Z]{26}\.tmp")
_TEMP_STALE_NS = 3600 * 1_000_000_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes (
  path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
  hash TEXT NOT NULL, modified_ms INTEGER NOT NULL, facts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_hash ON notes(hash);
CREATE TABLE IF NOT EXISTS skipped (
  path TEXT PRIMARY KEY, reason TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY, path TEXT NOT NULL, line INTEGER NOT NULL, text TEXT NOT NULL,
  norm TEXT NOT NULL, kind TEXT NOT NULL, confidence REAL NOT NULL,
  confirmed INTEGER NOT NULL, actor TEXT NOT NULL, evidence TEXT NOT NULL,
  created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
  retired_ms INTEGER, retired_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_path ON facts(path);
CREATE INDEX IF NOT EXISTS idx_facts_norm ON facts(norm);
CREATE INDEX IF NOT EXISTS idx_facts_updated ON facts(updated_ms, id);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY, path TEXT NOT NULL, ord INTEGER NOT NULL, fact_id TEXT,
  heading TEXT NOT NULL, text TEXT NOT NULL, hash TEXT NOT NULL, chars INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(hash);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  heading, text, tokenize = 'porter unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS vectors (
  hash TEXT NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL,
  PRIMARY KEY (hash, model)
);
"""

STARTER = {
    "shared/about-me.md": ("About me", "Who you are and how you live and work: the basics "
                                       "anything helping you should know."),
    "shared/preferences.md": ("Preferences", "How you like things done: tone, timing, tools, "
                                             "what to avoid."),
    "shared/people.md": ("People", "The people in your life and work, and what is worth "
                                   "remembering about each."),
    "shared/projects.md": ("Projects", "What you are working on, where each thing stands and "
                                       "what matters about it."),
    "shared/decisions.md": ("Decisions", "Choices you have made and why, so they are not "
                                         "argued again."),
    "shared/rules.md": ("Rules", "Standing instructions: things that must always or never "
                                 "happen."),
}
_STARTER_TAIL = ("Each line that starts with a dash is one fact. Add, change or remove "
                 "lines here or in the app.")


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    note: str
    line: int  # 1-based line in the note
    kind: str
    confidence: float
    confirmed: bool
    saved_by: str
    evidence: tuple[str, ...]
    created_ms: int
    updated_ms: int
    retired_ms: int | None = None
    retired_reason: str | None = None
    # From ``remember``: False when an existing fact said the same thing and
    # was returned instead of saving a new one.
    created: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "note": self.note, "line": self.line,
            "kind": self.kind, "confidence": self.confidence, "confirmed": self.confirmed,
            "saved_by": self.saved_by, "evidence": list(self.evidence),
            "created_ms": self.created_ms, "updated_ms": self.updated_ms,
            "retired_ms": self.retired_ms, "retired_reason": self.retired_reason,
        }


@dataclass
class _Loaded:
    rel: str
    raw: bytes
    digest: str
    st: os.stat_result
    parsed: md.Parsed


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def storable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return "\x00" not in text


def clean_fact_text(value: Any) -> str:
    if not isinstance(value, str):
        raise LibraryError(422, "A fact must be text.")
    text = value.strip()
    if any(c in text for c in "\r\n"):
        raise LibraryError(422, "A fact is one line. Put each statement in its own fact.")
    if not storable(text) or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise LibraryError(422, "The fact contains characters that cannot be saved.")
    if not any(c.isalnum() for c in text):
        raise LibraryError(422, "A fact needs some words in it.")
    if len(text) > MAX_FACT:
        raise LibraryError(422, f"A fact cannot be longer than {MAX_FACT} characters. "
                                "Put the detail in the note as ordinary text.")
    if "<!--" in text or "-->" in text:
        raise LibraryError(422, "A fact cannot contain <!-- or -->. Remove them.")
    return text


def clean_kind(value: Any) -> str:
    if not isinstance(value, str) or not KIND.match(value):
        raise LibraryError(422, "A fact's kind is a short lower-case word such as "
                                "preference, person or project.")
    return value


def atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Replace a file's content in one step: readers see the old or the new, never half."""
    tmp = path.with_name(f".{ulid()}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            with contextlib.suppress(OSError):
                os.fsync(handle.fileno())
        if mode is not None:
            with contextlib.suppress(OSError):
                os.chmod(tmp, stat.S_IMODE(mode))
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                # Windows refuses while another program has the file open.
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


class Memory:
    """This process's handle on the Library. Get it with ``memory(core)``."""

    def __init__(self, core: Core):
        self.core = core
        self.root = core.config.data / "memory"
        self._lock_path = core.config.data / "memory.lock"
        self._mutex = threading.RLock()
        self._depth = 0
        self._guard_file: Any = None
        # Deleting from the index overwrites what is removed, like the store.
        self._db = secure(connect(core.config.data / "memory.db"))
        with self._mutex:
            self._db.executescript(_SCHEMA)
            with contextlib.suppress(Exception):  # SQLite older than 3.42
                self._db.execute(
                    "INSERT INTO chunks_fts (chunks_fts, rank) VALUES ('secure-delete', 1)")
        # What cannot be rebuilt (versions of notes, candidates, cursors) is
        # kept apart from the index, which can.
        self.store = Store(core.config.data / "memory_store.db")
        # A second connection for reading, so a search never waits for a scan.
        self._rdb = connect(core.config.data / "memory.db")
        self._rlock = threading.Lock()
        self._ready = False
        self.vector_cache: tuple[Any, ...] | None = None

    def close(self) -> None:
        with self._mutex, self._rlock:
            self._db.close()
            self._rdb.close()
        self.store.close()

    # --- locking --------------------------------------------------------------

    @contextlib.contextmanager
    def exclusive(self):
        """Hold the Library, across threads and processes, for one change."""
        with self._mutex:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            with open(self._lock_path, "a+b") as guard:
                locked_windows = False
                if sys.platform == "win32":
                    import msvcrt

                    while True:  # LK_LOCK gives up after ten seconds; a long scan may not
                        try:
                            guard.seek(0)
                            msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK, 1)
                            locked_windows = True
                            break
                        except OSError:
                            time.sleep(0.1)
                else:
                    import fcntl

                    try:
                        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
                    except OSError:
                        pass  # a file system that cannot lock: the in-process lock remains
                self._depth = 1
                try:
                    yield
                finally:
                    self._depth = 0
                    if locked_windows:
                        import msvcrt

                        guard.seek(0)
                        msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)

    # --- small helpers --------------------------------------------------------

    def read(self, sql: str, params: tuple = ()) -> list[Any]:
        with self._rlock:
            return self._rdb.execute(sql, params).fetchall()

    def meta(self, key: str, default: Any = None) -> Any:
        rows = self.read("SELECT value FROM meta WHERE key = ?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_meta(self, key: str, value: Any) -> None:
        with self._mutex:
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)))

    def _bump(self) -> None:
        self._db.execute(
            "INSERT INTO meta (key, value) VALUES ('gen', '1')"
            " ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")

    def _setting(self, key: str) -> Any:
        return self.core.settings.get(f"memory.{key}")

    def _max_bytes(self) -> int:
        return self._setting("max_note_kb") * 1024

    def ensure(self) -> None:
        """Create the Library with its starter notes the first time it is used."""
        if self._ready and self.root.is_dir():
            return
        with self.exclusive():
            if not self.root.exists():
                (self.root / "agents").mkdir(parents=True)
                (self.root / "shared").mkdir()
                for rel, (title, about) in STARTER.items():
                    text = f"# {title}\n\n<!-- {about} {_STARTER_TAIL} -->\n"
                    atomic_write(self.root / rel, text.encode("utf-8"))
                self._sync(actor="system")
            self._ready = True

    def _fact(self, row: Any, created: bool = True) -> Fact:
        return Fact(
            id=row["id"], text=row["text"], note=row["path"], line=row["line"],
            kind=row["kind"], confidence=row["confidence"], confirmed=bool(row["confirmed"]),
            saved_by=row["actor"], evidence=tuple(json.loads(row["evidence"])),
            created_ms=row["created_ms"], updated_ms=row["updated_ms"],
            retired_ms=row["retired_ms"], retired_reason=row["retired_reason"], created=created)

    # --- walking the folder ---------------------------------------------------

    def _walk(self):
        """Every note file under the Library: ``(rel, stat, why_skipped)``.

        Hidden names and editor leftovers are passed over silently. A link is
        never followed, wherever it points: a linked folder is not entered and
        a linked note is listed as skipped.
        """
        stack = [("", self.root)]
        while stack:
            prefix, folder = stack.pop()
            try:
                entries = sorted(os.scandir(folder), key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                name = entry.name
                if ignorable(name):
                    if _TEMP.fullmatch(name):
                        # Ours, from a write that a crash cut short. Never a note.
                        with contextlib.suppress(OSError):
                            st = entry.stat(follow_symlinks=False)
                            if stat.S_ISREG(st.st_mode) \
                                    and time.time_ns() - st.st_mtime_ns > _TEMP_STALE_NS:
                                os.unlink(entry.path)
                    continue
                rel = f"{prefix}{name}"
                try:
                    if entry.is_symlink() or entry.is_junction():
                        if is_note_name(name):
                            yield rel, None, "It is a link, and links are never followed."
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((f"{rel}/", Path(entry.path)))
                        continue
                    if not is_note_name(name) or not entry.is_file(follow_symlinks=False):
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                try:
                    clean_path(rel)
                except LibraryError:
                    yield rel, st, ("Its name or a folder's name cannot be used in the "
                                    "Library. Rename it with plain letters, digits, spaces "
                                    "and dashes.")
                    continue
                yield rel, st, None

    def _stat_one(self, rel: str):
        path = self.root / rel
        try:
            here = self.root
            for part in rel.split("/"):
                here = here / part
                if is_link(here):
                    return None, "It is a link, and links are never followed."
            st = os.lstat(path)
        except OSError:
            return None, None
        return (st, None) if stat.S_ISREG(st.st_mode) else (None, None)

    # --- the engine -----------------------------------------------------------

    def _sync(self, *, actor: str, only: set[str] | None = None,
              new_meta: dict[str, dict] | None = None,
              retire_reasons: dict[str, str] | None = None,
              guard: dict[str, int] | None = None,
              settle: bool = False, by_hand: bool = False) -> dict[str, Any]:
        """Bring the record up to date with the files. Call inside ``exclusive``.

        ``only`` limits it to those paths. ``new_meta`` carries what the caller
        knows about facts it has just written (kind, confidence, evidence).
        ``guard`` maps a note to the Tape seq its subject had when the caller
        began: if someone else has written about that note since, nothing is
        recorded and LibraryError(409) is raised. ``settle`` is for a scan nobody
        asked for by hand: a note changed in the last two seconds is not given
        ids yet, because a program may still be writing it, and replacing the
        file under that program would lose what it writes next.
        """
        started = time.monotonic()
        now = now_ms()
        max_bytes = self._max_bytes()
        db = self._db
        if only is None:
            rows = {r["path"]: r for r in db.execute("SELECT * FROM notes")}
            old_skips = {r["path"]: r for r in db.execute("SELECT * FROM skipped")}
        else:
            marks = ",".join("?" * len(only))
            rows = {r["path"]: r for r in db.execute(
                f"SELECT * FROM notes WHERE path IN ({marks})", tuple(only))}  # noqa: S608
            old_skips = {r["path"]: r for r in db.execute(
                f"SELECT * FROM skipped WHERE path IN ({marks})", tuple(only))}  # noqa: S608

        disk: dict[str, os.stat_result] = {}
        skips: dict[str, tuple[str, int, int]] = {}
        if only is None:
            self.root.mkdir(parents=True, exist_ok=True)
            for rel, st, why in self._walk():
                if why:
                    skips[rel] = (why, st.st_size if st else 0, st.st_mtime_ns if st else 0)
                else:
                    disk[rel] = st
        else:
            for rel in only:
                st, why = self._stat_one(rel)
                if why:
                    skips[rel] = (why, 0, 0)
                elif st is not None:
                    disk[rel] = st

        loaded: dict[str, _Loaded] = {}
        touched: list[tuple[int, int, str]] = []
        for rel in sorted(disk):
            st = disk[rel]
            row = rows.get(rel)
            sig = (st.st_size, st.st_mtime_ns)
            if row is not None and (row["size"], row["mtime_ns"]) == sig:
                continue  # unchanged: not read again
            if st.st_size > max_bytes:
                skips[rel] = (f"It is larger than the {max_bytes // 1024} KB limit for a note "
                              "(Settings, Memory).", *sig)
                continue
            before = old_skips.get(rel)
            if before is not None and (before["size"], before["mtime_ns"]) == sig \
                    and "limit" not in before["reason"]:
                skips[rel] = (before["reason"], *sig)
                continue
            try:
                raw = (self.root / rel).read_bytes()
            except OSError:
                skips[rel] = ("It could not be read. Check the file's permissions.", *sig)
                continue
            if len(raw) > max_bytes:
                skips[rel] = (f"It is larger than the {max_bytes // 1024} KB limit for a note "
                              "(Settings, Memory).", *sig)
                continue
            digest = _sha(raw)
            if row is not None and row["hash"] == digest:
                touched.append((st.st_size, self._mtime_to_store(st), rel))
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                skips[rel] = ("It is not UTF-8 text. Save it as UTF-8 to include it.", *sig)
                continue
            if "\x00" in text:
                skips[rel] = ("It contains a NUL character, so it does not look like text.",
                              *sig)
                continue
            loaded[rel] = _Loaded(rel, raw, digest, st, md.parse(text))

        vanished = sorted(rel for rel in rows if rel not in disk or rel in skips)

        # Renames: a note that is gone and a new one with the same bytes.
        gone_by_hash: dict[str, list[str]] = defaultdict(list)
        for rel in vanished:
            if rel not in skips:
                gone_by_hash[rows[rel]["hash"]].append(rel)
        moves: dict[str, str] = {}
        for rel in sorted(loaded):
            if rel not in rows and gone_by_hash.get(loaded[rel].digest):
                moves[rel] = gone_by_hash[loaded[rel].digest].pop(0)

        # Which facts were in the notes that changed or went.
        affected = set(loaded) | set(vanished)
        live: dict[str, Any] = {}
        if affected:
            if len(affected) > 400:
                found = db.execute("SELECT * FROM facts WHERE retired_ms IS NULL")
                live = {r["id"]: r for r in found if r["path"] in affected}
            else:
                marks = ",".join("?" * len(affected))
                live = {r["id"]: r for r in db.execute(
                    f"SELECT * FROM facts WHERE retired_ms IS NULL AND path IN ({marks})",  # noqa: S608
                    tuple(affected))}

        # One id, one line. A copied line keeps its id only where it was.
        seen: dict[str, list[tuple[str, md.FactLine]]] = defaultdict(list)
        for rel in sorted(loaded):
            for fact in loaded[rel].parsed.facts:
                if fact.id:
                    seen[fact.id].append((rel, fact))
        dirty: set[str] = set()
        fresh: set[str] = set()
        for fact_id, places in seen.items():
            old = live.get(fact_id)
            keeper: md.FactLine | None = None
            if old is not None:
                keeper = next((f for rel, f in places
                               if rel == old["path"] or moves.get(rel) == old["path"]), None)
                keeper = keeper or places[0][1]
            elif not db.execute("SELECT 1 FROM facts WHERE id = ? AND retired_ms IS NULL",
                                (fact_id,)).fetchone():
                keeper = places[0][1]
            for rel, fact in places:
                if fact is not keeper:
                    fact.id = None
                    dirty.add(rel)
        for rel, item in loaded.items():
            for fact in item.parsed.facts:
                if fact.id is None:
                    fact.id = ulid()
                    fresh.add(fact.id)
                    item.parsed.write_fact(fact)
                    dirty.add(rel)

        # Write the ids into the files, touching nothing else.
        deferred = 0
        rewritten = 0
        held: set[str] = set()  # notes left exactly as recorded until the next scan
        for rel in sorted(dirty):
            item = loaded[rel]
            path = self.root / rel
            data = item.parsed.render().encode("utf-8")
            try:
                current = os.lstat(path)
                if (current.st_size, current.st_mtime_ns) != (item.st.st_size,
                                                              item.st.st_mtime_ns):
                    raise OSError("changed while it was being read")
                if settle and time.time_ns() - current.st_mtime_ns < _RACY_NS:
                    raise OSError("changed a moment ago: it may still be being written")
                try:
                    atomic_write(path, data, current.st_mode)
                except OSError as exc:
                    skips[rel] = (
                        "Its facts could not be given ids, because the file or its folder "
                        f"cannot be written ({exc.strerror or type(exc).__name__}). Check "
                        "its permissions. Until then its latest changes are not part of memory.",
                        0, 0)
                    raise
                item.raw, item.digest, item.st = data, _sha(data), os.lstat(path)
                rewritten += 1
            except OSError:
                # Being edited right now, or not writable: leave it for the next
                # scan, and leave what is recorded about it alone until then. Its
                # facts are not gone.
                del loaded[rel]
                held.add(rel)
                moves.pop(rel, None)
                deferred += 1

        counts = {"added": 0, "changed": 0, "removed": 0, "moved": 0, "facts_added": 0,
                  "facts_edited": 0, "facts_retired": 0}
        reverse_kinds = {path: kind for kind, path in
                         kind_notes(self._setting("kind_notes")).items()}
        chunk_chars = self._setting("chunk_chars")
        moved_to = {old: new for new, old in moves.items()}
        present: set[str] = set()
        tape = self.core.tape

        # What happened, for the timeline and for mining's record of what the
        # owner took back. Written after the index is committed.
        happened: list[tuple[str, int, str, str, str | None, str | None, dict]] = []
        forgotten: list[tuple[str, str, str]] = []  # (fact id, its words, the reason)
        revived: list[str] = []
        hand = by_hand or actor == DISK

        def print_note(kind: str, payload: dict, rel: str) -> Event:
            expect = guard.get(rel) if guard else None
            try:
                event = tape.print(kind, payload, actor=actor, subject=f"memory_note:{rel}",
                                   expect_subject_seq=expect)
            except TapeConflict:
                raise LibraryError(
                    409, "This note was changed by someone else at the same moment. Reload "
                         "it and check that your change is there.") from None
            if guard and rel in guard:
                guard[rel] = tape.subject_last_seq(f"memory_note:{rel}")
            return event

        db.execute("BEGIN IMMEDIATE")
        try:
            for rel in vanished:
                if rel in loaded or rel in held:
                    continue
                reason = skips[rel][0] if rel in skips else None
                print_note("memory.note.removed",
                           {"note": rel, "moved_to": moved_to.get(rel), "reason": reason}, rel)
                db.execute("DELETE FROM notes WHERE path = ?", (rel,))
                self._drop_chunks(rel)
                counts["moved" if rel in moved_to else "removed"] += 1
                if hand and rel not in moved_to:
                    happened.append((ulid(), now, "note_removed", actor, None, rel, {}))
            for size, mtime, rel in touched:
                db.execute("UPDATE notes SET size = ?, mtime_ns = ? WHERE path = ?",
                           (size, mtime, rel))
            for rel in sorted(loaded):
                item = loaded[rel]
                ref = REF + item.digest
                row = rows.get(rel)
                facts = item.parsed.facts
                saved = print_note("memory.note.saved", {
                    "note": rel, "ref": ref, "size": len(item.raw), "facts": len(facts),
                    "created": row is None and rel not in moves,
                    "moved_from": moves.get(rel)}, rel)
                # The text of this version, in memory's own store, which can delete.
                self.store.keep(saved.id, rel, item.raw, saved.ts)
                if hand:
                    happened.append((ulid(), now, "note_saved", actor, None, rel, {
                        "created": row is None and rel not in moves,
                        "moved_from": moves.get(rel)}))
                if row is None and rel not in moves:
                    counts["added"] += 1
                elif row is not None:
                    counts["changed"] += 1
                modified = item.st.st_mtime_ns // 1_000_000
                db.execute(
                    "INSERT INTO notes (path, size, mtime_ns, hash, modified_ms, facts)"
                    " VALUES (?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET size = excluded.size,"
                    " mtime_ns = excluded.mtime_ns, hash = excluded.hash,"
                    " modified_ms = excluded.modified_ms, facts = excluded.facts",
                    (rel, len(item.raw), self._mtime_to_store(item.st), item.digest, modified,
                     len(facts)))
                for fact in facts:
                    present.add(fact.id)
                    line = fact.index + 1
                    old = live.get(fact.id)
                    if old is not None:
                        changed = [name for name, differs in (
                            ("text", old["text"] != fact.text), ("note", old["path"] != rel),
                        ) if differs]
                        if changed:
                            tape.print("memory.fact.edited", {
                                "fact_id": fact.id, "note": rel, "note_ref": ref,
                                "changed": changed,
                                "from_note": old["path"] if "note" in changed else None,
                                "kind": old["kind"], "confirmed": bool(old["confirmed"]),
                                "chars": len(fact.text)},
                                actor=actor, subject=f"memory_fact:{fact.id}")
                            counts["facts_edited"] += 1
                            happened.append((ulid(), now, "fact_edited", actor, fact.id, rel,
                                             {"changed": changed}))
                            db.execute(
                                "UPDATE facts SET path = ?, line = ?, text = ?, norm = ?,"
                                " updated_ms = ? WHERE id = ?",
                                (rel, line, fact.text, md.normalise(fact.text), now, fact.id))
                        elif old["line"] != line:
                            db.execute("UPDATE facts SET line = ? WHERE id = ?", (line, fact.id))
                        continue
                    known = (new_meta or {}).get(fact.id)
                    prior = None
                    if known is None and fact.id not in fresh:
                        before = db.execute("SELECT * FROM facts WHERE id = ?",
                                            (fact.id,)).fetchone()
                        prior = ({"kind": before["kind"], "confidence": before["confidence"],
                                  "confirmed": bool(before["confirmed"]),
                                  "evidence": json.loads(before["evidence"]),
                                  "actor": before["actor"], "created_ms": before["created_ms"]}
                                 if before is not None else self._recover(fact.id))
                    meta = known or prior or {}
                    record = {
                        "kind": meta.get("kind") or reverse_kinds.get(rel, "note"),
                        "confidence": meta.get("confidence", 1.0),
                        "confirmed": meta.get("confirmed", actor.startswith("owner")),
                        "evidence": list(meta.get("evidence", ())),
                    }
                    tape.print("memory.fact.saved", {
                        "fact_id": fact.id, "note": rel, "note_ref": ref, **record,
                        "chars": len(fact.text), "revived": prior is not None},
                        actor=actor, subject=f"memory_fact:{fact.id}")
                    counts["facts_added"] += 1
                    happened.append((ulid(), now, "fact_saved", meta.get("actor", actor),
                                     fact.id, rel, {
                                         "kind": record["kind"], "confirmed": record["confirmed"],
                                         "confidence": record["confidence"],
                                         "revived": prior is not None}))
                    if prior is not None:
                        revived.append(fact.id)
                    db.execute(
                        "INSERT OR REPLACE INTO facts (id, path, line, text, norm, kind,"
                        " confidence, confirmed, actor, evidence, created_ms, updated_ms,"
                        " retired_ms, retired_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
                        (fact.id, rel, line, fact.text, md.normalise(fact.text), record["kind"],
                         record["confidence"], int(record["confirmed"]),
                         meta.get("actor", actor), json.dumps(record["evidence"]),
                         meta.get("created_ms", now), now))
                self._rechunk(rel, item.parsed, chunk_chars)
            for fact_id, old in live.items():
                if fact_id in present or old["path"] in held:
                    continue
                reason = (retire_reasons.get(fact_id) if retire_reasons is not None
                          else None) or (
                    LINE_REMOVED if old["path"] in loaded
                    else NOTE_GONE)
                tape.print("memory.fact.retired",
                           {"fact_id": fact_id, "note": old["path"], "reason": reason},
                           actor=actor, subject=f"memory_fact:{fact_id}")
                counts["facts_retired"] += 1
                happened.append((ulid(), now, "fact_forgotten", actor, fact_id, old["path"],
                                 {"reason": reason}))
                if old["path"] in loaded:
                    # Taken out on purpose, not lost with its note: remembered as
                    # something not to propose again.
                    forgotten.append((fact_id, old["text"], reason))
                db.execute("UPDATE facts SET retired_ms = ?, retired_reason = ?, updated_ms = ?"
                           " WHERE id = ?", (now, reason, now, fact_id))
            if only is None:
                db.execute("DELETE FROM skipped")
            else:
                db.execute(f"DELETE FROM skipped WHERE path IN ({','.join('?' * len(only))})",  # noqa: S608
                           tuple(only))
            db.executemany("INSERT OR REPLACE INTO skipped (path, reason, size, mtime_ns)"
                           " VALUES (?,?,?,?)", [(rel, *skip) for rel, skip in skips.items()])
            if loaded or vanished:
                self._bump()
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        self.store.log(happened)
        if forgotten or revived:
            from jimmyhq.memory import mining  # noqa: PLC0415 - mining imports this module

            mining.facts_changed(self, forgotten=forgotten, revived=revived, actor=actor)
        result = counts | {
            "at_ms": now, "notes": len(disk), "ids_written": rewritten, "deferred": deferred,
            "skipped": len(skips), "took_ms": int((time.monotonic() - started) * 1000)}
        if only is None:
            self.set_meta("last_scan", result)
            log.info("memory scan: %s", {k: v for k, v in result.items() if v})
        return result

    @staticmethod
    def _mtime_to_store(st: os.stat_result) -> int:
        """A file changed in the last moments is stored as "look again": on a disk
        with coarse timestamps a second edit could otherwise go unseen."""
        return 0 if time.time_ns() - st.st_mtime_ns < _RACY_NS else st.st_mtime_ns

    def _recover(self, fact_id: str) -> dict | None:
        """What the Tape remembers about a fact this database has never seen
        (the database was rebuilt, or the note came back from a backup)."""
        if not md.FACT_ID.fullmatch(fact_id):
            return None
        events = self.core.tape.by_subject(f"memory_fact:{fact_id}", limit=200)
        meta: dict | None = None
        for event in events:
            if not event.readable:
                continue
            payload = event.payload
            if event.kind == "memory.fact.saved":
                if meta is None:
                    meta = {"actor": event.actor, "created_ms": event.ts}
                meta.update(kind=payload["kind"], confidence=payload["confidence"],
                            confirmed=payload["confirmed"], evidence=payload["evidence"])
            elif event.kind == "memory.fact.edited" and meta is not None:
                meta.update(kind=payload["kind"], confirmed=payload["confirmed"])
        return meta

    def _drop_chunks(self, rel: str) -> None:
        self._db.execute("DELETE FROM chunks_fts WHERE rowid IN"
                         " (SELECT id FROM chunks WHERE path = ?)", (rel,))
        self._db.execute("DELETE FROM chunks WHERE path = ?", (rel,))

    def _rechunk(self, rel: str, parsed: md.Parsed, size: int) -> None:
        self._drop_chunks(rel)
        for order, chunk in enumerate(md.chunks(parsed, size)):
            cur = self._db.execute(
                "INSERT INTO chunks (path, ord, fact_id, heading, text, hash, chars)"
                " VALUES (?,?,?,?,?,?,?)",
                (rel, order, chunk.fact_id, chunk.heading, chunk.text, chunk.hash,
                 len(chunk.text)))
            self._db.execute("INSERT INTO chunks_fts (rowid, heading, text) VALUES (?,?,?)",
                             (cur.lastrowid, chunk.heading, chunk.text))

    # --- scanning and rebuilding ----------------------------------------------

    def _index_stamp(self) -> dict[str, int]:
        return {"version": INDEX_VERSION, "chunk_chars": self._setting("chunk_chars")}

    def scan(self, *, settle: bool = False) -> dict[str, Any]:
        """Notice everything that changed on disk. Returns what it did.

        ``settle`` is for the background scan: a note changed in the last two
        seconds waits for the next one before ids are written into it.
        """
        self.ensure()
        with self.exclusive():
            if self.meta("index") != self._index_stamp():
                self._rebuild()
            return self._sync(actor=DISK, settle=settle)

    def reindex(self) -> dict[str, Any]:
        """Throw the search index away and build it again from the files."""
        self.ensure()
        with self.exclusive():
            rebuilt = self._rebuild()
            return self._sync(actor=DISK) | {"reindexed": rebuilt}

    def _rebuild(self) -> int:
        db = self._db
        size = self._setting("chunk_chars")
        done = 0
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("DELETE FROM chunks_fts")
            db.execute("DELETE FROM chunks")
            for row in db.execute("SELECT path, hash FROM notes ORDER BY path").fetchall():
                try:
                    raw = (self.root / row["path"]).read_bytes()
                except OSError:
                    continue
                if _sha(raw) != row["hash"]:
                    # Changed on disk since it was last recorded: forget its stamp so
                    # the scan that follows reads it and indexes it.
                    db.execute("UPDATE notes SET mtime_ns = 0, hash = '' WHERE path = ?",
                               (row["path"],))
                    continue
                self._rechunk(row["path"], md.parse(raw.decode("utf-8")), size)
                done += 1
            # Vectors are kept by passage text, so unchanged passages keep theirs.
            db.execute("DELETE FROM vectors WHERE hash NOT IN (SELECT hash FROM chunks)")
            self._bump()
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        self.set_meta("index", self._index_stamp())
        return done

    def refresh(self, rel: str) -> None:
        """Bring one note's record up to date if its file changed on disk."""
        st, _ = self._stat_one(rel)
        rows = self.read("SELECT size, mtime_ns FROM notes WHERE path = ?", (rel,))
        if st is None and not rows:
            return
        if st is not None and rows and (rows[0]["size"], rows[0]["mtime_ns"]) == (
                st.st_size, st.st_mtime_ns):
            return
        with self.exclusive():
            self._sync(actor=DISK, only={rel}, settle=True)

    # --- notes ----------------------------------------------------------------

    def _existing(self, rel: str) -> Path:
        target = inside(self.root, rel)
        if not target.is_file():
            raise LibraryError(404, "There is no note at this path. Reload the list of notes.")
        return target

    def _check_text(self, text: Any) -> bytes:
        if not isinstance(text, str):
            raise LibraryError(422, "A note's text must be text.")
        if not storable(text):
            raise LibraryError(422, "The note contains characters that cannot be saved.")
        data = text.encode("utf-8")
        if len(data) > self._max_bytes():
            raise LibraryError(
                413, f"The note is larger than the {self._max_bytes() // 1024} KB limit. "
                     "Split it into smaller notes, or raise the limit under Settings, Memory.")
        return data

    @staticmethod
    def _read(target: Path, rel: str) -> bytes:
        """A note's bytes, for a change about to be made to it. Another program
        may move or replace the file at any moment; that is a 409, not a crash."""
        try:
            return target.read_bytes()
        except OSError as exc:
            raise LibraryError(
                409, f"{rel} was moved or replaced on disk at the same moment "
                     f"({exc.strerror or type(exc).__name__}). Reload it and try "
                     "again.") from None

    def _write(self, target: Path, data: bytes) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = os.lstat(target).st_mode if target.exists() else None
            atomic_write(target, data, mode)
        except OSError as exc:
            raise LibraryError(
                409, "The note could not be written to the Library folder "
                     f"({exc.strerror or type(exc).__name__}). Check that a file is not in "
                     "the way of a folder and that the folder can be written.") from None

    def read_note(self, path: Any) -> dict[str, Any]:
        self.ensure()
        rel = clean_path(path)
        target = self._existing(rel)
        if self._setting("watch_disk"):
            self.refresh(rel)
        try:
            raw = target.read_bytes()
        except OSError:
            raise LibraryError(404, "There is no note at this path. Reload the list of "
                                    "notes.") from None
        skipped = self.read("SELECT reason FROM skipped WHERE path = ?", (rel,))
        try:
            if len(raw) > self._max_bytes():
                raise ValueError
            text = raw.decode("utf-8")
        except ValueError:
            why = skipped[0]["reason"] if skipped else "It is too large or is not UTF-8 text."
            raise LibraryError(422, f"This note is not part of memory. {why}") from None
        facts = self.read("SELECT * FROM facts WHERE path = ? AND retired_ms IS NULL"
                          " ORDER BY line", (rel,))
        try:
            modified = os.lstat(target).st_mtime_ns // 1_000_000
        except OSError:  # deleted by hand since it was read a moment ago
            modified = now_ms()
        return {
            "path": rel, "text": text, "revision": _sha(raw), "size": len(raw),
            "modified_ms": modified,
            "facts": [self._fact(r).as_dict() for r in facts],
        }

    def save_note(self, path: Any, text: Any, *, actor: str,
                  revision: str | None = None) -> dict[str, Any]:
        """Create or replace a note. Facts typed without an id are given one, so
        the saved text can differ from what was sent: use what comes back."""
        self.ensure()
        rel = clean_path(path)
        self._check_text(text)
        with self.exclusive():
            target = inside(self.root, rel)
            subject = f"memory_note:{rel}"
            seq = self.core.tape.subject_last_seq(subject)
            if target.exists() and not target.is_file():
                raise LibraryError(409, "A folder has this name. Choose another name.")
            current = self._read(target, rel) if target.is_file() else None
            if revision is not None and (current is None or _sha(current) != revision):
                raise LibraryError(
                    409, "This note changed since you opened it, in another window or on "
                         "disk. Reload it, then make your change again.")
            if current is not None:
                # A hand edit nobody has recorded yet is recorded first, so
                # history keeps what the note said before this save.
                self._sync(actor=DISK, only={rel})
                seq = self.core.tape.subject_last_seq(subject)
                current = self._read(target, rel) if target.is_file() else None
            parsed = md.parse(text)
            for fact in parsed.facts:
                if fact.id is None:
                    fact.id = ulid()
                    parsed.write_fact(fact)
            data = self._check_text(parsed.render())
            if data != current:
                self._write(target, data)
                self._sync(actor=actor, only={rel}, guard={rel: seq}, by_hand=True)
        return self.read_note(rel)

    def delete_note(self, path: Any, *, actor: str, history: bool = False
                    ) -> dict[str, int] | None:
        """Delete a note. With ``history``, every stored version of it goes too
        (and the words of its facts leave the index), which cannot be undone;
        that also works for a note that has already been deleted."""
        self.ensure()
        rel = clean_path(path)
        with self.exclusive():
            try:
                target = self._existing(rel)
            except LibraryError:
                if not history or not self.store.one(
                        "SELECT 1 FROM versions WHERE path = ?", (rel,)):
                    raise
                target = None
            if target is not None:
                self._sync(actor=DISK, only={rel})
                seq = self.core.tape.subject_last_seq(f"memory_note:{rel}")
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise LibraryError(
                        409, "The note could not be deleted "
                             f"({exc.strerror or type(exc).__name__}).") from None
                self._sync(actor=actor, only={rel}, guard={rel: seq},
                           retire_reasons=_AllSay(NOTE_DELETED), by_hand=True)
            if not history:
                return None
            versions = [v["version"] for v in self._lineage(rel, limit=100_000)]
            counts = self.store.purge(versions, [rel])
            from jimmyhq.memory import mining  # noqa: PLC0415 - mining imports this module

            with self._mutex:
                # Its facts are retired; their words must not stay in the index,
                # or in what mining remembers of them.
                retired = self._db.execute(
                    "SELECT id, text FROM facts WHERE path = ? AND retired_ms IS NOT NULL",
                    (rel,)).fetchall()
                for row in retired:
                    mining.erase_fact(self, row["id"], row["text"])
                self._db.execute("UPDATE facts SET text = '', norm = '' WHERE path = ?"
                                 " AND retired_ms IS NOT NULL", (rel,))
            self._purged("note_deleted", counts, actor=actor, subject=f"memory_note:{rel}",
                         note=rel)
            return counts

    # --- deleting for good ----------------------------------------------------

    def _purged(self, why: str, counts: dict[str, Any], *, actor: str, subject: str | None,
                note: str | None = None, fact_id: str | None = None) -> None:
        """Finish a purge: leave nothing in the search index or the write-ahead
        logs, and print how much went (never what)."""
        with self._mutex:
            with contextlib.suppress(Exception):
                # Merging the keyword index drops the words of deleted passages.
                self._db.execute("INSERT INTO chunks_fts (chunks_fts) VALUES ('optimize')")
            self._db.execute("DELETE FROM vectors WHERE hash NOT IN (SELECT hash FROM chunks)")
            self._bump()
        clean = scrub(self._db, self._mutex)
        clean = self.store.scrub() and clean
        if not clean:
            # Something was reading at that moment. The worker finishes the job.
            self.store.set_meta("scrub_pending", True)
        notes = counts.get("notes")
        self.core.tape.print("memory.history.purged", {
            "why": why, "versions": counts["versions"], "bodies": counts["bodies"],
            "notes": len(notes) if isinstance(notes, list) else (1 if note else 0),
        }, actor=actor, subject=subject)
        self.store.log([(ulid(), now_ms(), "history_purged", actor, fact_id, note, {
            "why": why, "versions": counts["versions"]})])

    def forget_everywhere(self, fact_id: Any, *, actor: str, reason: Any = None) -> dict[str, Any]:
        """Forget a fact and remove its words from everything memory stores:
        every kept version of any note that contains its line, the index, and
        what mining kept about it. Returns the retired fact and the counts.

        It cannot reach a Bench card that once asked about the fact, the source
        the fact was read from, or a copy of the Library made outside the app.
        """
        self.ensure()
        reason = _clean_reason(reason)
        with self.exclusive():
            fact = self.get_fact(fact_id, live_only=False)
            text = fact.text
            if fact.retired_ms is None:
                fact = self.forget(fact.id, actor=actor, reason=reason)
            needles = [f"m:{fact.id}".encode()]
            if text.strip():
                needles.append(text.encode("utf-8"))
            counts = self.store.purge_containing(needles)
            from jimmyhq.memory import mining  # noqa: PLC0415 - mining imports this module

            mining.erase_fact(self, fact.id, text)
            with self._mutex:
                self._db.execute("UPDATE facts SET text = '', norm = '' WHERE id = ?",
                                 (fact.id,))
            self._purged("forgotten_everywhere", counts, actor=actor,
                         subject=f"memory_fact:{fact.id}", fact_id=fact.id)
            return {"fact": self.get_fact(fact.id, live_only=False),
                    "purged": {"versions": counts["versions"], "bodies": counts["bodies"],
                               "notes": len(counts["notes"])}}

    def prune_history(self, *, force: bool = False) -> dict[str, int] | None:
        """Apply the owner's limits on kept versions. The worker calls this
        often; it does the work at most once an hour unless ``force``."""
        now = now_ms()
        if self.store.meta("scrub_pending"):
            if scrub(self._db, self._mutex) and self.store.scrub():
                self.store.set_meta("scrub_pending", False)
        if not force and now - (self.store.meta("last_prune") or 0) < PRUNE_EVERY_MS:
            return None
        keep = self._setting("history_keep_versions")
        days = self._setting("history_keep_days")
        with self.exclusive():
            counts = self.store.prune(keep, now - days * DAY_MS if days else None)
            self.store.set_meta("last_prune", now)
            if counts["versions"] or counts["bodies"]:
                # The index's log can still hold passages of the versions that
                # went, from when the note was changed: empty that too.
                clean = scrub(self._db, self._mutex)
                if not (self.store.scrub() and clean):
                    self.store.set_meta("scrub_pending", True)
                self.core.tape.print("memory.history.purged", {
                    "why": "retention", "versions": counts["versions"],
                    "bodies": counts["bodies"], "notes": 0}, actor="system")
        return counts

    def move_note(self, src: Any, dst: Any, *, actor: str) -> dict[str, Any]:
        self.ensure()
        old, new = clean_path(src), clean_path(dst)
        if old == new:
            raise LibraryError(422, "The note is already at that path. Choose a different one.")
        with self.exclusive():
            source = self._existing(old)
            target = inside(self.root, new)
            if target.exists():
                raise LibraryError(409, "There is already something at that path. Choose "
                                        "another name, or move that one first.")
            self._sync(actor=DISK, only={old})
            seqs = {rel: self.core.tape.subject_last_seq(f"memory_note:{rel}")
                    for rel in (old, new)}
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
            except OSError as exc:
                raise LibraryError(409, "The note could not be moved "
                                        f"({exc.strerror or type(exc).__name__}).") from None
            self._sync(actor=actor, only={old, new}, guard=seqs, by_hand=True)
        return self.read_note(new)

    def tree(self) -> dict[str, Any]:
        self.ensure()
        rows = self.read("SELECT path, size, modified_ms, facts FROM notes ORDER BY path")
        folders = {"shared", "agents"}
        for row in rows:
            parts = row["path"].split("/")[:-1]
            folders.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        try:
            agents = sorted(e.name for e in os.scandir(self.root / "agents")
                            if e.is_dir(follow_symlinks=False) and not ignorable(e.name))
            folders.update(f"agents/{name}" for name in agents)
        except OSError:
            pass
        return {
            "folders": sorted(folders),
            "notes": [{"path": r["path"], "size": r["size"], "modified_ms": r["modified_ms"],
                       "facts": r["facts"]} for r in rows],
        }

    def _lineage(self, rel: str, *, limit: int) -> list[dict[str, Any]]:
        """A note's saved versions, newest first, followed back through renames.
        Under an earlier name only what was saved before the rename counts:
        anything saved there later belongs to a different note."""
        out: list[dict[str, Any]] = []
        hops = 0
        current: str | None = rel
        before: int | None = None
        while current and len(out) < limit and hops < 50:
            events = self.core.tape.by_subject(
                f"memory_note:{current}", kind="memory.note.saved", newest_first=True,
                limit=limit - len(out), before=before)
            came_from = None
            for event in events:
                if not event.readable:
                    continue
                out.append({"version": event.id, "ts": event.ts, "actor": event.actor,
                            "size": event.payload["size"], "facts": event.payload["facts"],
                            "note": event.payload["note"]})
                if event.payload.get("moved_from"):
                    came_from, before = event.payload["moved_from"], event.seq
                    break  # anything older under this name was a different note
            current, hops = came_from, hops + 1
        return out

    def history(self, path: Any, *, limit: int = 100) -> list[dict[str, Any]]:
        """A note's saved versions, newest first, following it back through
        renames. ``available`` says whether a version's text is still stored."""
        rel = clean_path(path)
        out = self._lineage(rel, limit=limit)
        stored = self.store.stored([v["version"] for v in out])
        for item in out:
            item["available"] = item["version"] in stored
        return out

    def version(self, path: Any, version: Any) -> dict[str, Any]:
        rel = clean_path(path)
        gone = LibraryError(404, "There is no such version of this note. Reload its history.")
        if not isinstance(version, str) or not md.FACT_ID.fullmatch(version):
            raise gone
        event = self.core.tape.get(version)
        if event is None or event.kind != "memory.note.saved" or not event.readable:
            raise gone
        if event.payload["note"] != rel and not any(
                v["version"] == version for v in self._lineage(rel, limit=500)):
            raise gone
        body = self.store.body(version)
        try:
            if body is None:
                raise ValueError
            text = body.decode("utf-8")
        except ValueError:
            raise LibraryError(410, "The text of this version is no longer stored.") from None
        return {"version": event.id, "ts": event.ts, "actor": event.actor,
                "note": event.payload["note"], "size": event.payload["size"], "text": text}

    # --- facts ----------------------------------------------------------------

    def get_fact(self, fact_id: Any, *, live_only: bool = True) -> Fact:
        rows = []
        if isinstance(fact_id, str) and md.FACT_ID.fullmatch(fact_id):
            rows = self.read("SELECT * FROM facts WHERE id = ?", (fact_id,))
        if not rows or (live_only and rows[0]["retired_ms"] is not None):
            raise LibraryError(404, "There is no fact with this id. Reload the list of facts.")
        return self._fact(rows[0])

    def list_facts(self, *, kind: str | None = None, q: str | None = None,
                   confirmed: bool | None = None, note: str | None = None, limit: int = 50,
                   before: str | None = None) -> dict[str, Any]:
        """Live facts, most recently changed first, a page at a time."""
        self.ensure()
        where, params = ["retired_ms IS NULL"], []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if confirmed is not None:
            where.append("confirmed = ?")
            params.append(int(bool(confirmed)))
        if note:
            where.append("path = ?")
            params.append(note)
        if q and q.strip():
            escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("text LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        if before:
            stamp, _, last = before.partition("-")
            if not stamp.isdigit() or len(stamp) > 16 or not md.FACT_ID.fullmatch(last):
                raise LibraryError(422, "before is not a page marker this list gave out. "
                                        "Start again from the first page.")
            where.append("(updated_ms < ? OR (updated_ms = ? AND id < ?))")
            params += [int(stamp), int(stamp), last]
        limit = max(1, min(int(limit), 200))
        rows = self.read(
            f"SELECT * FROM facts WHERE {' AND '.join(where)}"  # noqa: S608
            " ORDER BY updated_ms DESC, id DESC LIMIT ?", (*params, limit + 1))
        page = rows[:limit]
        more = len(rows) > limit
        return {
            "facts": [self._fact(r).as_dict() for r in page],
            "next_before": f"{page[-1]['updated_ms']}-{page[-1]['id']}" if more else None,
        }

    def _locate(self, fact_id: str) -> tuple[Fact, Path, md.Parsed, md.FactLine]:
        """A live fact and its line, after catching its note up with the disk."""
        fact = self.get_fact(fact_id)
        self._sync(actor=DISK, only={fact.note})
        fact = self.get_fact(fact_id)
        target = self._existing(fact.note)
        try:
            parsed = md.parse(self._read(target, fact.note).decode("utf-8"))
        except UnicodeDecodeError:
            raise LibraryError(
                409, f"{fact.note} is no longer UTF-8 text, so the fact in it cannot be "
                     "changed. Save it as UTF-8 and try again.") from None
        line = next((f for f in parsed.facts if f.id == fact_id), None)
        if line is None:
            raise LibraryError(404, "There is no fact with this id. Reload the list of facts.")
        return fact, target, parsed, line

    def edit_fact(self, fact_id: Any, *, actor: str, text: Any = None, kind: Any = None,
                  confirmed: Any = None) -> Fact:
        self.ensure()
        if text is not None:
            text = clean_fact_text(text)
        if kind is not None:
            kind = clean_kind(kind)
        if confirmed is not None and not isinstance(confirmed, bool):
            raise LibraryError(422, "confirmed must be true or false.")
        with self.exclusive():
            fact = self.get_fact(fact_id)
            if text is not None and text != fact.text:
                fact, target, parsed, line = self._locate(fact.id)
                seq = self.core.tape.subject_last_seq(f"memory_note:{fact.note}")
                line.text = text
                parsed.write_fact(line)
                self._write(target, self._check_text(parsed.render()))
                self._sync(actor=actor, only={fact.note}, guard={fact.note: seq})
                fact = self.get_fact(fact.id)
            changed = [name for name, new, old in (
                ("kind", kind, fact.kind), ("confirmed", confirmed, fact.confirmed),
            ) if new is not None and new != old]
            if changed:
                new_kind = kind if kind is not None else fact.kind
                new_confirmed = confirmed if confirmed is not None else fact.confirmed
                self.core.tape.print("memory.fact.edited", {
                    "fact_id": fact.id, "note": fact.note, "changed": changed,
                    "kind": new_kind, "confirmed": new_confirmed, "chars": len(fact.text)},
                    actor=actor, subject=f"memory_fact:{fact.id}")
                with self._mutex:
                    self._db.execute(
                        "UPDATE facts SET kind = ?, confirmed = ?, updated_ms = ? WHERE id = ?",
                        (new_kind, int(new_confirmed), now_ms(), fact.id))
                    self._bump()
                kind_of = ("fact_confirmed" if "confirmed" in changed and new_confirmed
                           else "fact_edited")
                self.store.log([(ulid(), now_ms(), kind_of, actor, fact.id, fact.note,
                                 {"changed": changed})])
                if "confirmed" in changed:
                    from jimmyhq.memory import mining  # noqa: PLC0415 - it imports this module

                    mining.fact_confirmed(self, fact.id, new_confirmed)
            return self.get_fact(fact.id)

    def forget(self, fact_id: Any, *, actor: str, reason: Any = None) -> Fact:
        """Remove a fact's line (and anything nested under it) from its note."""
        self.ensure()
        reason = _clean_reason(reason)
        with self.exclusive():
            fact, target, parsed, line = self._locate(self.get_fact(fact_id).id)
            seq = self.core.tape.subject_last_seq(f"memory_note:{fact.note}")
            del parsed.lines[line.index:line.end]
            self._write(target, parsed.render().encode("utf-8"))
            self._sync(actor=actor, only={fact.note}, guard={fact.note: seq},
                       retire_reasons={fact.id: reason})
            return self.get_fact(fact.id, live_only=False)

    def find_duplicate(self, text: str) -> Fact | None:
        """A live fact that already says this, exactly or nearly."""
        rows = self.read("SELECT * FROM facts WHERE norm = ? AND retired_ms IS NULL LIMIT 1",
                         (md.normalise(text),))
        if rows:
            return self._fact(rows[0], created=False)
        terms = list(dict.fromkeys(md.words(text)))[:40]
        if not terms:
            return None
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = self.read(
            "SELECT f.* FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
            " JOIN facts f ON f.id = c.fact_id AND f.retired_ms IS NULL"
            " WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT 40",
            (f"text : ({match})",))
        for row in rows:
            if md.near_duplicate(text, row["text"]):
                return self._fact(row, created=False)
        return None

    def remember(self, text: Any, *, kind: Any, note: Any = None, actor: str,
                 confidence: Any = 1.0, evidence: Any = (), confirmed: Any = False) -> Fact:
        self.ensure()
        text = clean_fact_text(text)
        kind = clean_kind(kind)
        if not isinstance(actor, str) or not actor or len(actor) > 80:
            raise LibraryError(422, "remember needs an actor, such as owner or agent:scout.")
        if isinstance(confidence, bool) or not isinstance(confidence, int | float) \
                or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise LibraryError(422, "confidence must be a number from 0 to 1.")
        if not isinstance(confirmed, bool):
            raise LibraryError(422, "confirmed must be true or false.")
        if isinstance(evidence, str) or not isinstance(evidence, list | tuple) \
                or len(evidence) > MAX_EVIDENCE or not all(
                    isinstance(e, str) and 0 < len(e) <= 200 and storable(e)
                    and not any(ch.isspace() for ch in e) for e in evidence):
            raise LibraryError(
                422, f"evidence must be a list of at most {MAX_EVIDENCE} short references "
                     "without spaces, such as tape:<event id> or chat:<message id>.")
        if note is None:
            rel = kind_notes(self._setting("kind_notes")).get(kind) or self._setting(
                "default_note")
        else:
            rel = clean_path(note)
        with self.exclusive():
            target = inside(self.root, rel)
            if target.exists() and not target.is_file():
                raise LibraryError(409, "A folder has this note's name. Choose another note.")
            if target.is_file():
                self._sync(actor=DISK, only={rel})
            existing = self.find_duplicate(text)
            if existing is not None:
                return existing
            seq = self.core.tape.subject_last_seq(f"memory_note:{rel}")
            if target.is_file():
                try:
                    body = self._read(target, rel).decode("utf-8")
                except UnicodeDecodeError:
                    raise LibraryError(
                        409, f"{rel} is not UTF-8 text, so nothing can be added to it. Save "
                             "it as UTF-8, or name another note.") from None
            else:
                body = f"# {md.title_for(rel)}\n\n"
            before = md.parse(body)
            eol = before.eol
            fact_id = ulid()
            if body and not body.endswith(("\n", "\r")):
                body += eol
            last = before.lines[-1][0] if before.lines else ""
            in_list = any(f.end == len(before.lines) for f in before.facts)
            if last.strip() and not in_list:
                body += eol  # a blank line between a paragraph and the list it starts
            body += f"- {text} <!-- m:{fact_id} -->{eol}"
            if not any(f.id == fact_id for f in md.parse(body).facts):
                raise LibraryError(
                    409, f"{rel} ends inside an unfinished code block, so a fact added to "
                         "it would not be read as one. Close the block, or name another note.")
            self._write(target, self._check_text(body))
            self._sync(actor=actor, only={rel}, guard={rel: seq}, new_meta={fact_id: {
                "kind": kind, "confidence": float(confidence), "confirmed": confirmed,
                "evidence": list(evidence)}})
            return self.get_fact(fact_id)

    # --- status ---------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        one = lambda sql, *p: self.read(sql, p)[0][0]  # noqa: E731
        return {
            "notes": one("SELECT COUNT(*) FROM notes"),
            "facts": one("SELECT COUNT(*) FROM facts WHERE retired_ms IS NULL"),
            "chunks": one("SELECT COUNT(*) FROM chunks"),
        }

    def skipped(self, limit: int = 200) -> list[dict[str, str]]:
        rows = self.read("SELECT path, reason FROM skipped ORDER BY path LIMIT ?", (limit,))
        return [{"path": r["path"], "reason": r["reason"]} for r in rows]


class _AllSay(dict):
    """A reason given for every fact that is retired in one step."""

    def __init__(self, reason: str):
        super().__init__()
        self._reason = reason

    def get(self, key, default=None):  # noqa: ARG002
        return self._reason


def _clean_reason(value: Any) -> str:
    if value is None:
        return "Forgotten."
    if not isinstance(value, str) or not storable(value):
        raise LibraryError(422, "The reason must be text.")
    reason = " ".join(value.split())
    if len(reason) > MAX_REASON:
        raise LibraryError(422, f"The reason cannot be longer than {MAX_REASON} characters.")
    return reason or "Forgotten."
