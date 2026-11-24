"""Snapshots of the data folder: making one, listing them, checking one, pruning old ones.

Nothing here needs a running JimmyHQ, so the terminal commands and the worker share it.

A snapshot is a folder ``jimmyhq-backup-<UTC time>Z`` in a destination the owner chose:

    jimmyhq-backup-20261122T164800123Z/
        manifest.json   what is inside: each file's path, size and sha256, each store's version
        data/           the data folder, as it was

- **Databases** are copied with SQLite's own backup API, never byte by byte: a store in
  write-ahead mode keeps its newest writes in the ``-wal`` file beside it, and a copy of
  the bytes could catch the two at different moments. The copy is a consistent picture of
  each database at one moment, written as one file (journal mode DELETE), so it needs no
  side files and its hash is stable. A store opened from it goes back to write-ahead mode
  by itself. The Vault is copied as bytes: its files are named by their hash.
- **Everything else** is copied as files, with its permission bits where the platform has
  them, so ``secrets.json`` stays owner-only.
- **Links are never followed.** A link or a junction inside the data folder is left out
  and counted, so nothing outside the data folder is ever read into a backup.
- **A half-made snapshot is never mistaken for a whole one.** It is written into a hidden
  sibling (``.jimmyhq-backup-...partial``), checked, and renamed at the end. The listing
  only ever shows names of the finished shape that hold a manifest.

What is left out, and why, is ``left_out``. See "Back up and move your data" in
docs/architecture.md.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import importlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from jimmyhq import storeversion

FORMAT = 1  # the manifest's own layout; a newer one is refused with a sentence
FORMAT_KEY = "jimmyhq_backup"
PREFIX = "jimmyhq-backup-"
NAME = re.compile(r"^jimmyhq-backup-(\d{8}T\d{9})Z$")
PARTIAL = ".partial"
PRUNING = ".pruning"
MANIFEST = "manifest.json"
DATA = "data"
SQLITE_MAGIC = b"SQLite format 3\x00"
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
AHEAD_MS = 5 * 60_000  # how far a date may be ahead of the clock before it is not trusted
_CHUNK = 1024 * 1024
_RETRIES = 8
_LONG = 240  # Windows' old limit is 260 characters; past this, use the long form

# Why each thing left out is left out. Each is either made again by JimmyHQ, or belongs
# to the computer it was made on. The keys are what a manifest counts.
LEFT_OUT = {
    "logs": "the log files: they describe this computer's runs, not your data",
    "locks": "JimmyHQ's own lock files at the top of the data folder: they belong to the "
             "processes running now",
    "side_files": "SQLite's -wal and -shm files: the database copies already hold them",
    "app_environments": "a full app's Python environment (apps/<id>/venvs/): it is built "
                        "for this computer, and updating or installing the app builds it again",
    "unfinished_installs": "an app install that was not finished (apps/_staging/)",
    "browser_profiles": "an agent's browser profile: its cookies are locked to this "
                        "computer, so an agent signs in again after a restore",
    "rebuildable": "the stores JimmyHQ makes again from the others (views.db, memory.db, "
                   "metrics.db, models.db), unless you choose to include them",
    "links": "links and junctions: a backup never follows one out of the data folder",
    "clutter": "files the operating system adds to folders by itself (.DS_Store, ._*, "
               "Thumbs.db, desktop.ini)",
}

# The lock files JimmyHQ itself makes at the top of the data folder, and only those: a
# file named ``*.lock`` anywhere else (an app's ``uv.lock``, say) is data and goes in.
OWN_LOCKS = frozenset({
    "supervisor.lock",  # supervisor.take_lock: one JimmyHQ per data folder
    "backup.lock",  # backup.service: one backup or test at a time
    "memory.lock",  # memory.library
    "memory_mine.lock",  # memory.mining
    "secrets.json.lock",  # secretstore: writers of secrets.json
})

# Files an operating system drops into folders by itself. They are never copied either
# way, and finding one in a backup is not a sign that it was changed.
CLUTTER = frozenset({".ds_store", "thumbs.db", "desktop.ini"})


def is_clutter(name: str) -> bool:
    """Whether a file name is one the operating system made by itself (any case)."""
    return name.lower() in CLUTTER or name.startswith("._")


class BackupError(Exception):
    """A backup could not be made or checked. The message is a sentence for the owner,
    with no full path in it, so it can go on the record as it is."""


# --- names ----------------------------------------------------------------------------


def snapshot_name(now_ms: int) -> str:
    """The folder name for a snapshot made at ``now_ms``: safe on every platform (no
    colon), sorted by time when sorted by name, and to the millisecond."""
    moment = datetime.fromtimestamp(now_ms // 1000, UTC)
    return f"{PREFIX}{moment:%Y%m%dT%H%M%S}{now_ms % 1000:03d}Z"


def made_at(name: str) -> int | None:
    """When a snapshot of this name was made (unix ms, UTC), or None for any other name."""
    found = NAME.match(name)
    if found is None:
        return None
    digits = found.group(1)
    try:
        moment = datetime.strptime(digits[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    return int(moment.timestamp()) * 1000 + int(digits[15:])


INSTALLATION_FILE = "installation_id"
_INSTALLATION = re.compile(r"^[0-9a-f]{32}$")


def _read_installation(path: Path) -> str | None:
    try:
        with open(native(path), encoding="ascii") as handle:
            text = handle.read(100).strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if _INSTALLATION.match(text) else None


def installation_id(data: Path) -> str:
    """This installation's own id: random, made once and kept in the data folder
    (``installation_id``, owner-only where the platform has permission bits). A backup
    names it as its source, so two computers sharing a backup folder never prune or test
    each other's backups, even with their data folders at the same path. It is in every
    backup, so a restore keeps it and the restored installation goes on with the backups
    it made before."""
    path = Path(data) / INSTALLATION_FILE
    found = _read_installation(path)
    if found is not None:
        return found
    new = secrets.token_hex(16)
    # Written whole under another name first, then linked into place, which fails if
    # another process got there first: every process ends up with the one on disk.
    temp = path.with_name(f".{INSTALLATION_FILE}-{new}.tmp")
    fd = os.open(native(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(f"{new}\n".encode("ascii"))
            handle.flush()
            fsync(handle.fileno())
        if os.path.lexists(native(path)):  # there, but not an id: made again
            retry(lambda: os.replace(native(temp), native(path)))
        else:
            try:
                os.link(native(temp), native(path))
            except FileExistsError:
                pass
            except OSError:  # a file system without links
                if not os.path.lexists(native(path)):
                    retry(lambda: os.replace(native(temp), native(path)))
    finally:
        with contextlib.suppress(OSError):
            os.unlink(native(temp))
    sync_dir(Path(data))
    found = _read_installation(path)
    if found is None:
        raise BackupError("JimmyHQ could not keep its installation id in the data folder. "
                          "Nothing was written. Check that the data folder can be written to.")
    return found


def tag_of(source: str) -> str:
    """A short, file-name-safe mark of a source, for the hidden folders a backup or a
    prune leaves while it works."""
    return hashlib.sha256(source.encode("utf-8", "surrogatepass")).hexdigest()[:12]


def place_of(folder: str | Path) -> str:
    """A short fingerprint of a folder, for the record: which folder, without saying where.
    The same folder gives the same fingerprint whether or not it is there right now."""
    text = os.path.normcase(os.path.abspath(os.path.expanduser(str(folder))))
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:12]


# --- small cross-platform helpers -----------------------------------------------------


def native(path: Path | str) -> str:
    """The path as the operating system should be given it. On Windows a long path gets
    the ``\\\\?\\`` form, which lifts the old 260-character limit."""
    text = os.path.abspath(path)
    if sys.platform != "win32" or len(text) < _LONG or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def retry(action: Callable[[], Any]) -> Any:
    """Windows virus scanners and indexers hold a new file open for a moment, which makes
    a rename or a delete fail. Try a few times before giving up."""
    for attempt in range(_RETRIES):
        try:
            return action()
        except PermissionError:
            if attempt == _RETRIES - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
    return None


def _writable_again(function: Any, path: str, _exc: Any) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)  # a read-only file on Windows
    function(path)


def remove_tree(path: Path) -> bool:
    """Delete a folder JimmyHQ made. True when it is gone."""
    if not os.path.lexists(native(path)):
        return True
    with contextlib.suppress(OSError):
        retry(lambda: shutil.rmtree(native(path), onexc=_writable_again))
    return not os.path.lexists(native(path))


def _owner_only(path: Path) -> None:
    # Where the platform has permission bits. A folder on a drive that has none (FAT,
    # most network shares) is left as the drive makes it: the docs say to choose a place
    # only you can read.
    with contextlib.suppress(OSError, NotImplementedError):
        os.chmod(native(path), 0o700)


# A file system that cannot do it at all says so with one of these; anything else (a
# disk error) is an error.
_NO_SYNC = {errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL),
            getattr(errno, "EOPNOTSUPP", errno.EINVAL)}


def fsync(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno not in _NO_SYNC:
            raise


def sync_file(path: Path) -> None:
    """Ask the system to put a file's bytes on the disk now, not when it gets round to it."""
    fd = os.open(native(path), os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        fsync(fd)
    finally:
        os.close(fd)


def sync_dir(path: Path) -> None:
    """The same for a folder's list of names, so a file made or renamed in it is there
    after a power cut. Windows cannot open a folder for this (and NTFS keeps its own
    journal of names), so there it does nothing; nor where the file system refuses."""
    if sys.platform == "win32":
        return
    with contextlib.suppress(OSError):
        fd = os.open(native(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def sync_tree(root: Path) -> None:
    """Every folder under ``root`` and ``root`` itself, deepest first. The files in them
    were each put on the disk as they were written."""
    if sys.platform == "win32":
        return
    folders = [Path(here) for here, _dirs, _files in os.walk(native(root))]
    for folder in reversed(folders):
        sync_dir(folder)


def same_or_inside(child: Path, parent: Path) -> bool:
    """Whether ``child`` is ``parent`` or somewhere inside it, by what the file system says
    (so a different spelling, a link or a case difference is caught too)."""
    child = Path(os.path.abspath(os.path.expanduser(str(child))))
    parent = Path(parent)
    for candidate in (child, *child.parents):
        try:
            if candidate.exists() and parent.exists() and os.path.samefile(candidate, parent):
                return True
        except OSError:
            continue
    try:
        child.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


# --- what goes in ---------------------------------------------------------------------


def _declared() -> list[storeversion.Store]:
    for module in storeversion.DECLARED_IN:
        importlib.import_module(module)
    return list(storeversion.STORES.values())


def _matches(rel: str, pattern: str) -> bool:
    path, wanted = PurePosixPath(rel), PurePosixPath(pattern)
    return len(path.parts) == len(wanted.parts) and path.match(pattern)


def declared_store(rel: str) -> storeversion.Store | None:
    """The store declared for this path inside the data folder, if any."""
    return next((s for s in _declared() if any(_matches(rel, p) for p in s.files)), None)


def left_out_dir(rel: str) -> str | None:
    """Why a folder inside the data folder is left out, or None to go in."""
    parts = PurePosixPath(rel).parts
    if parts[:1] == ("logs",):
        return "logs"
    if len(parts) >= 3 and parts[0] == "apps" and parts[2] == "venvs":
        return "app_environments"
    if parts[:2] == ("apps", "_staging"):
        return "unfinished_installs"
    if (len(parts) >= 5 and parts[:2] == ("village", "agents") and parts[3] == "browser"
            and parts[4] == "profile"):
        return "browser_profiles"
    return None


def is_sqlite(path: Path, rel: str) -> bool:
    """A database to copy with SQLite's backup API: a SQLite file by its first bytes, with
    a database's name, outside the Vault (whose files are named by their bytes)."""
    if rel.startswith("vault/") or not rel.lower().endswith(SQLITE_SUFFIXES):
        return False
    try:
        with open(native(path), "rb") as handle:
            return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


@dataclass
class Item:
    rel: str  # forward slashes, relative to the data folder
    path: Path
    sqlite: bool


@dataclass
class Plan:
    items: list[Item] = field(default_factory=list)
    left_out: dict[str, int] = field(default_factory=dict)

    def leave_out(self, reason: str) -> None:
        self.left_out[reason] = self.left_out.get(reason, 0) + 1


def _walk(root: Path, rel: str = "") -> Iterator[tuple[os.DirEntry, str]]:
    try:
        entries = sorted(os.scandir(native(root)), key=lambda e: e.name)
    except FileNotFoundError:
        return
    for entry in entries:
        yield entry, f"{rel}{entry.name}"


def plan(data: Path, *, include_rebuildable: bool = False) -> Plan:
    """Everything to copy from the data folder, and a count of what is left out by why."""
    out = Plan()
    data = Path(data)
    sqlite_files: set[str] = set()
    pending: list[tuple[Path, str]] = [(data, "")]
    side: list[tuple[os.DirEntry, str]] = []
    while pending:
        folder, rel = pending.pop()
        for entry, name in _walk(folder, rel):
            link = entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction())
            if link:
                out.leave_out("links")
                continue
            if entry.is_dir(follow_symlinks=False):
                reason = left_out_dir(name)
                if reason is not None:
                    out.leave_out(reason)
                    continue
                pending.append((Path(entry.path), f"{name}/"))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue  # a socket, a pipe: nothing to copy
            if is_clutter(entry.name):
                out.leave_out("clutter")
                continue
            if name in OWN_LOCKS:  # ``name`` has no slash only at the top
                out.leave_out("locks")
                continue
            if entry.name.endswith(SIDE_SUFFIXES):
                side.append((entry, name))
                continue
            path = Path(entry.path)
            sqlite = is_sqlite(path, name)
            if sqlite and "/" not in name and declared_store(name) is None:
                # A database at the top of the data folder that is not declared is one
                # JimmyHQ can make again (CONTRIBUTING: one that cannot is declared).
                if not include_rebuildable:
                    out.leave_out("rebuildable")
                    sqlite_files.add(name)
                    continue
            if sqlite:
                sqlite_files.add(name)
            out.items.append(Item(name, path, sqlite))
    for entry, name in side:
        base = next((name[: -len(s)] for s in SIDE_SUFFIXES if name.endswith(s)), name)
        if base in sqlite_files:
            out.leave_out("side_files")
        else:
            out.items.append(Item(name, Path(entry.path), False))
    out.items.sort(key=copy_order)
    return out


TAPE = "tape.db"


def copy_order(item: Item) -> tuple[int, str]:
    """The order things are copied in: every other database, then the Tape, then the
    Vault and the plain files.

    Other stores hold bookmarks on the Tape ("read up to event N": the stored views, the
    Village, the hands, the apps' plugs). Copied before the Tape, each bookmark in a
    backup is at or behind the Tape it comes with, so after a restore nothing new is
    taken as already read. The Vault and the plain files come after the Tape, because an
    event names a Vault file only once that file is written: every file an event in the
    copied Tape names is there in the copied Vault (unless the owner erased that event
    while the backup ran, which lets its file go)."""
    if item.sqlite:
        return (1 if item.rel == TAPE else 0), item.rel
    return 2, item.rel


# --- copying --------------------------------------------------------------------------


def hash_file(path: Path) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    with open(native(path), "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def copy_file(source: Path, target: Path) -> tuple[int, str]:
    """Copy bytes, hashing what is written, so the manifest describes the copy exactly
    even if the source changed while it was read."""
    digest, size = hashlib.sha256(), 0
    os.makedirs(native(target.parent), exist_ok=True)
    with open(native(source), "rb") as src:
        fd = os.open(native(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                     | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as dst:
            while chunk := src.read(_CHUNK):
                dst.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            dst.flush()
            fsync(dst.fileno())  # on the disk before anything older is let go
    with contextlib.suppress(OSError):
        shutil.copystat(native(source), native(target), follow_symlinks=False)
    return size, digest.hexdigest()


def _copy_sqlite(source: Path, target: Path) -> tuple[int, str, int]:
    """A consistent copy of a live database through SQLite's backup API, written as one
    file. Returns its size, its sha256 and its schema version."""
    os.makedirs(native(target.parent), exist_ok=True)
    # Opened read-write, because a read-only connection to a database in write-ahead
    # mode can fail to open when its side files are not there; but never created: a
    # database deleted while the backup runs is gone, not made again, empty, in the live
    # data folder. Nothing is written through it.
    uri = Path(os.path.abspath(source)).as_uri() + "?mode=rw"
    try:
        src = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
    except sqlite3.OperationalError:
        if not os.path.lexists(native(source)):
            raise FileNotFoundError(source) from None
        raise
    try:
        src.execute("PRAGMA busy_timeout=30000")
        dst = sqlite3.connect(str(target), isolation_level=None)
        try:
            src.backup(dst)  # every page, in one step: one moment's picture
            dst.execute("PRAGMA journal_mode=DELETE")
            version = int(dst.execute("PRAGMA user_version").fetchone()[0])
        finally:
            dst.close()
    finally:
        src.close()
    sync_file(target)  # before its mode is copied, which may make it read-only
    with contextlib.suppress(OSError):
        shutil.copymode(native(source), native(target))
    size, digest = hash_file(target)
    return size, digest, version


@dataclass
class Made:
    name: str
    path: Path
    files: int
    bytes: int
    stores: int  # databases copied with the backup API
    left_out: dict[str, int]
    duration_ms: int


def plain(exc: OSError) -> str:
    """An OS error in words, without the path it names."""
    return (exc.strerror or type(exc).__name__).rstrip(".")


def check_destination(data: Path, dest: Path) -> None:
    """Raise ``BackupError`` with the sentence to show when this folder cannot take a
    backup of this data folder."""
    if same_or_inside(dest, data):
        raise BackupError(
            "The backup folder is inside JimmyHQ's data folder, so each backup would hold "
            "the ones before it and be lost with it. Nothing was written. Choose a folder "
            "outside the data folder, ideally on another drive.")
    if not os.path.isdir(native(dest)):
        if os.path.lexists(native(dest)):
            raise BackupError("The backup folder is a file, not a folder. Nothing was "
                              "written. Choose a folder under Settings, Backups.")
        raise BackupError(
            "The backup folder is not there. If it is on a drive that is not plugged in, "
            "plug it in; otherwise choose a folder that exists under Settings, Backups. "
            "Nothing was written.")


def clear_leftovers(dest: Path, *, source: str, now_ms: int | None = None) -> int:
    """Remove what an earlier backup or prune of this installation left half-done, and
    anyone's left for more than a day. Another computer's backup may be being written
    into the same folder right now, so its folder is left alone until then. Call it
    holding the backup lock, so no backup of this data folder is being written."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    mine = f"-{tag_of(source)}"
    removed = 0
    with contextlib.suppress(OSError):
        for entry in os.scandir(native(dest)):
            if not (entry.name.startswith("." + PREFIX)
                    and entry.name.endswith((PARTIAL, PRUNING))
                    and entry.is_dir(follow_symlinks=False)):
                continue
            stem = entry.name.removesuffix(PARTIAL).removesuffix(PRUNING)
            if (stem.endswith(mine) or now_ms - _touched_ms(Path(entry.path)) > LEFTOVER_MS) \
                    and remove_tree(Path(entry.path)):
                removed += 1
    return removed


LEFTOVER_MS = 86_400_000  # a day


def _touched_ms(folder: Path) -> int:
    """When a hidden folder was last written to, as far as its top shows."""
    newest = 0.0
    for path in (folder, folder / DATA, folder / MANIFEST):
        with contextlib.suppress(OSError):
            newest = max(newest, os.stat(native(path), follow_symlinks=False).st_mtime)
    return int(newest * 1000)


def make(data: Path, dest: Path, *, now_ms: int, version: str, source: str,
         include_rebuildable: bool = False) -> Made:
    """Make one snapshot of ``data`` in ``dest`` and return what it holds. Raises
    ``BackupError`` with a sentence; a half-made snapshot stays hidden and is cleared at
    the next backup."""
    started = time.monotonic()
    data, dest = Path(data), Path(os.path.expanduser(str(dest)))
    check_destination(data, dest)
    clear_leftovers(dest, source=source)
    name = snapshot_name(now_ms)
    final = dest / name
    partial = dest / f".{name}-{tag_of(source)}{PARTIAL}"
    if os.path.lexists(native(final)):
        raise BackupError("A backup made at this very moment is already there. Nothing was "
                          "written. Try again in a moment.")
    try:
        os.mkdir(native(partial))
    except PermissionError:
        raise BackupError("JimmyHQ is not allowed to write in the backup folder. Nothing was "
                          "written. Choose a folder you can write to.") from None
    except OSError as exc:
        raise BackupError(f"JimmyHQ could not write in the backup folder ({plain(exc)}). "
                          "Nothing was written.") from None
    _owner_only(partial)
    root = partial / DATA
    files: list[dict[str, Any]] = []
    stores: dict[str, dict[str, Any]] = {}
    total = 0
    try:
        os.mkdir(native(root))
        _owner_only(root)
        todo = plan(data, include_rebuildable=include_rebuildable)
        gone = 0
        for item in _databases_then_files(todo, data, include_rebuildable):
            target = root.joinpath(*item.rel.split("/"))
            try:
                if item.sqlite:
                    size, digest, store_version = _copy_sqlite(item.path, target)
                    declared = declared_store(item.rel)
                    stores[item.rel] = {"store": declared.name if declared else None,
                                        "version": store_version}
                else:
                    size, digest = copy_file(item.path, target)
            except FileNotFoundError:
                gone += 1  # removed while the backup ran: nothing to keep
                continue
            except sqlite3.Error as exc:
                raise BackupError(f"The database {_area(item.rel)} could not be copied "
                                  f"({exc}). Nothing was kept from this try.") from None
            files.append({"path": item.rel, "size": size, "sha256": digest,
                          "sqlite": item.sqlite})
            total += size
        if gone:
            todo.left_out["gone"] = todo.left_out.get("gone", 0) + gone
        files.sort(key=lambda f: f["path"])
        manifest = {
            FORMAT_KEY: FORMAT,
            "version": version,
            "made_ms": now_ms,
            "source": source,
            "files": files,
            "stores": stores,
            "left_out": dict(sorted(todo.left_out.items())),
        }
        _write_json(partial / MANIFEST, manifest)
    except PermissionError:
        raise BackupError("A file in the data folder could not be read, or the copy could not "
                          "be written: permission was refused. Nothing was kept from this "
                          "try. Close anything that holds a file open, then try again.") from None
    except OSError as exc:
        raise BackupError(f"The backup could not be finished ({plain(exc)}). If the drive "
                          "is full, free some space or keep fewer backups. Nothing was kept "
                          "from this try.") from None
    found = verify(partial, full=False)
    if not found.passed:
        raise BackupError("The backup was made but did not pass its check: "
                          f"{found.short()[0]} Nothing older was removed.")
    sync_tree(partial)  # every file is on the disk already; now every folder's names
    try:
        retry(lambda: os.rename(native(partial), native(final)))
    except OSError as exc:
        raise BackupError(f"The finished backup could not be put in place ({plain(exc)}). "
                          "Nothing older was removed.") from None
    sync_dir(dest)  # the new name is on the disk before any older backup is pruned
    return Made(name, final, len(files), total, len(stores), dict(todo.left_out),
                int((time.monotonic() - started) * 1000))


def _databases_then_files(todo: Plan, data: Path, include_rebuildable: bool) -> Iterator[Item]:
    """The databases of ``todo`` in ``copy_order``, then the other files as they are once
    the Tape is copied: the folder is looked at again, so a Vault file written while the
    databases were being copied is there for the event that names it. ``todo`` ends up
    holding what was left out on that second look."""
    yield from (item for item in todo.items if item.sqlite)
    later = plan(data, include_rebuildable=include_rebuildable)
    todo.left_out = later.left_out
    yield from (item for item in later.items if not item.sqlite)


def _write_json(path: Path, value: Any) -> None:
    text = json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    with open(native(path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        with contextlib.suppress(OSError):
            os.fsync(handle.fileno())


def _area(rel: str) -> str:
    """How a file is named on the record: a file at the top by its name, anything deeper by
    the folder it is in, so a note's own name never goes on the Tape."""
    head, _, rest = rel.partition("/")
    return head if not rest else f"in {head}/"


# --- listing --------------------------------------------------------------------------


@dataclass
class Listed:
    name: str
    path: Path
    made_ms: int
    version: str
    files: int
    bytes: int
    source: str
    ahead: bool = False  # dated later than the clock says it is: see ``snapshots``


def read_manifest(snapshot: Path) -> dict[str, Any] | None:
    try:
        with open(native(Path(snapshot) / MANIFEST), encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return found if isinstance(found, dict) and FORMAT_KEY in found else None


def snapshots(folder: Path, *, now_ms: int | None = None) -> list[Listed]:
    """The finished snapshots in a folder, newest first by when each says it was made. A
    half-made one, a pruned one on its way out and any folder without a readable manifest
    are not listed.

    One dated more than ``AHEAD_MS`` after ``now_ms`` (the clock was wrong when it was
    made, or is wrong now) is listed last, marked ``ahead``: otherwise it would stay the
    newest, the one tested, and hold a kept place for as long as its date is to come.
    Last, it is the first pruned once newer ones are made, never before (pruning never
    removes the backup just made)."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    out = []
    try:
        entries = list(os.scandir(native(Path(os.path.expanduser(str(folder))))))
    except OSError:
        return []
    for entry in entries:
        if made_at(entry.name) is None or not entry.is_dir(follow_symlinks=False):
            continue
        manifest = read_manifest(Path(entry.path))
        if manifest is None:
            continue
        files = manifest.get("files")
        files = files if isinstance(files, list) else []
        made = manifest.get("made_ms")
        made = made if isinstance(made, int) and not isinstance(made, bool) else None
        made = made if made is not None else (made_at(entry.name) or 0)
        out.append(Listed(
            name=entry.name, path=Path(entry.path), made_ms=made,
            version=str(manifest.get("version", "")), files=len(files),
            bytes=sum(int(f.get("size", 0)) for f in files if isinstance(f, dict)),
            source=str(manifest.get("source", "")), ahead=made - now_ms > AHEAD_MS))
    out.sort(key=lambda s: (not s.ahead, s.made_ms, s.name), reverse=True)
    return out


def prune(folder: Path, *, keep: int, source: str, protect: str,
          also: Iterable[str] = ()) -> list[str]:
    """Remove the oldest snapshots of this installation past the newest ``keep``. Called
    only after a new snapshot (``protect``) was made and passed its check, so the newest
    good one is never removed. ``source`` is this installation's, and ``also`` any other
    name its older snapshots carry; snapshots made by another are left alone. Returns
    the names removed."""
    sources = {source, *also}
    mine = [s for s in snapshots(folder) if s.source in sources]
    removed = []
    for old in mine[max(1, keep):]:
        if old.name == protect:
            continue
        # Renamed first, so a snapshot half-deleted is never listed as a whole one.
        leaving = old.path.with_name(f".{old.name}-{tag_of(source)}{PRUNING}")
        try:
            retry(lambda old=old, leaving=leaving: os.rename(native(old.path), native(leaving)))
        except OSError:
            continue
        remove_tree(leaving)
        removed.append(old.name)
    return removed


# --- checking -------------------------------------------------------------------------


@dataclass
class Check:
    """What checking a snapshot found. ``problems`` are (file, sentence) pairs; the file
    is relative to the snapshot's data folder, or "" for the snapshot itself."""

    passed: bool
    full: bool
    files: int = 0
    bytes: int = 0
    problems: list[tuple[str, str]] = field(default_factory=list)
    manifest: dict[str, Any] | None = None
    too_new: storeversion.StoreTooNew | None = None

    def lines(self) -> list[str]:
        """Each problem for the terminal, with the file it is about."""
        return [f"{rel}: {text}" if rel else text for rel, text in self.problems]

    def short(self, most: int = 5) -> list[str]:
        """Each problem for the record: a file deeper than the top is named by its folder."""
        out = [f"A file {_area(rel)}: {text}" if "/" in rel else
               (f"{rel}: {text}" if rel else text) for rel, text in self.problems[:most]]
        if len(self.problems) > most:
            out.append(f"And {len(self.problems) - most} more problems.")
        return out


def _safe_rel(rel: Any) -> bool:
    if not isinstance(rel, str) or not rel or "\\" in rel or "\x00" in rel:
        return False
    path = PurePosixPath(rel)
    return not path.is_absolute() and ".." not in path.parts and ":" not in path.parts[0]


def _open_snapshot_db(path: Path, *, in_place: bool) -> sqlite3.Connection:
    if in_place:
        # Read-only and immutable: nothing, not even a side file, is written next to it.
        uri = Path(os.path.abspath(path)).as_uri() + "?mode=ro&immutable=1"
        return sqlite3.connect(uri, uri=True, isolation_level=None)
    return sqlite3.connect(str(path), isolation_level=None)


def _check_db(conn: sqlite3.Connection, *, full: bool) -> tuple[str | None, int]:
    pragma = "integrity_check" if full else "quick_check"
    rows = conn.execute(f"PRAGMA {pragma}").fetchall()
    found = None if [r[0] for r in rows] == ["ok"] else str(rows[0][0])[:200]
    return found, int(conn.execute("PRAGMA user_version").fetchone()[0])


def verify(snapshot: Path, *, full: bool = True) -> Check:
    """Check that a snapshot is whole and opens. It never touches the live data folder.

    Both forms check the manifest, that every file listed is there at its size and that
    nothing else is, and that each database opens at a version this JimmyHQ knows. The
    full form (the restore test) also checks every file's sha256 and runs SQLite's
    ``integrity_check`` on a copy of each database in a temporary folder; the cheap form,
    run straight after a backup is made, runs ``quick_check`` on each in place, read-only.
    """
    snapshot = Path(os.path.expanduser(str(snapshot)))
    result = Check(passed=False, full=full)
    manifest = read_manifest(snapshot)
    if manifest is None:
        result.problems.append(("", "It has no readable manifest.json, so it is not a whole "
                                    "backup made by JimmyHQ."))
        return result
    result.manifest = manifest
    layout = manifest.get(FORMAT_KEY)
    if not isinstance(layout, int) or isinstance(layout, bool) or layout > FORMAT:
        result.problems.append(("", "It was made by a newer JimmyHQ, whose backups this one "
                                    "cannot read. Update JimmyHQ, then try again."))
        return result
    files = manifest.get("files")
    if not isinstance(files, list):
        result.problems.append(("", "Its manifest lists no files."))
        return result
    root = snapshot / DATA
    listed: dict[str, dict[str, Any]] = {}
    for entry in files:
        rel = entry.get("path") if isinstance(entry, dict) else None
        if not _safe_rel(rel) or rel in listed:
            result.problems.append(("", "Its manifest names a file outside the backup, or one "
                                        "twice, so it cannot be trusted."))
            return result
        listed[rel] = entry
    for rel, entry in listed.items():
        path = root.joinpath(*rel.split("/"))
        try:
            size = os.stat(native(path), follow_symlinks=False).st_size
        except OSError:
            result.problems.append((rel, "It is missing from the backup."))
            continue
        if size != entry.get("size"):
            result.problems.append((rel, "Its size is not the size the manifest gives."))
            continue
        if full:
            try:
                _, digest = hash_file(path)
            except OSError as exc:
                result.problems.append((rel, f"It could not be read ({plain(exc)})."))
                continue
            if digest != entry.get("sha256"):
                result.problems.append((rel, "Its contents have changed since the backup was "
                                             "made."))
                continue
        result.files += 1
        result.bytes += size
    pending = [(root, "")]
    while pending:
        folder, rel = pending.pop()
        for entry, name in _walk(folder, rel):
            if entry.is_dir(follow_symlinks=False):
                pending.append((Path(entry.path), f"{name}/"))
            elif name not in listed and not is_clutter(entry.name):
                result.problems.append((name, "It is in the backup but not in its manifest."))
    stores = manifest.get("stores") if isinstance(manifest.get("stores"), dict) else {}
    with tempfile.TemporaryDirectory(prefix="jimmyhq-restore-test-") as scratch:
        for at, (rel, entry) in enumerate(sorted(listed.items())):
            if not entry.get("sqlite"):
                continue
            path = root.joinpath(*rel.split("/"))
            try:
                if full:
                    copy = Path(scratch) / f"{at}.db"
                    shutil.copyfile(native(path), native(copy))
                conn = _open_snapshot_db(copy if full else path, in_place=not full)
                try:
                    damaged, found = _check_db(conn, full=full)
                finally:
                    conn.close()
            except OSError as exc:
                result.problems.append((rel, f"It could not be read ({plain(exc)})."))
                continue
            except sqlite3.Error as exc:
                # SQLite's own words, which never name the file.
                result.problems.append((rel, f"It does not open as a database ({exc})."))
                continue
            if damaged is not None:
                result.problems.append((rel, f"SQLite finds it damaged: {damaged}"))
            declared = declared_store(rel)
            recorded = stores.get(rel, {}).get("version") if isinstance(stores.get(rel), dict) \
                else None
            if recorded is not None and recorded != found:
                result.problems.append((rel, "Its schema version is not the one the manifest "
                                             "gives."))
            if declared is not None and found > declared.latest:
                # Named as it is inside the backup: this sentence goes on the record.
                too_new = storeversion.StoreTooNew(rel, found, declared.latest)
                result.too_new = result.too_new or too_new
                result.problems.append((rel, str(too_new)))
    result.passed = not result.problems
    return result
