"""Where apps live on this computer, and the proof that a folder of files is safe to keep.

Layout, under the data directory::

    apps/_staging/<plan>/tree/           fetched, waiting for the owner's answer
    apps/<id>/versions/<version>-<hash8>/   the code, exactly as it was consented to
    apps/<id>/data/                      the app's own data (kept when it is removed)

Two halves:

- **Trees.** ``copy_folder`` and ``hash_tree`` walk a folder without following anything.
  A link, a junction, a device, a name that is not safe on every system, two names that
  differ only by case, or a tree over the size limits is refused with a sentence.
  ``tree_hash`` is the mark recorded on the Tape: the sha256 of one line per file,
  ``"<sha256 of the file>  <path>\\n"``, sorted by path, paths with forward slashes.
- **What is installed.** ``installed``, ``get``, ``granted``, ``code_dir``, ``manifest_of``
  read the ``apps`` lens and the installed manifest. Other packages ask here.

Nothing in an app's folder is ever run or imported by this module.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jimmyhq.apps import manifest as mf
from jimmyhq.apps.lens import AppsLens

if TYPE_CHECKING:
    from jimmyhq.core import Core

APPS_DIR = "apps"
STAGING = "_staging"  # an app id starts with a letter, so this can never be one
LEFT_OUT_DIRS = frozenset({"__pycache__"})
MB = 1024 * 1024
MAX_DEPTH = 16
_RETRIES = 6


class AppError(Exception):
    """Something about apps was refused. ``detail`` is a sentence for the owner: what
    did not happen and what to do instead."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --- limits ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    max_files: int = 2000
    max_file_bytes: int = 10 * MB
    max_tree_bytes: int = 50 * MB


def limits(core: Core) -> Limits:
    """The size limits the owner has set."""
    get = core.settings.get
    return Limits(max_files=int(get("apps.max_files")),
                  max_file_bytes=int(get("apps.max_file_mb")) * MB,
                  max_tree_bytes=int(get("apps.max_tree_mb")) * MB)


def _mb(size: int) -> str:
    return f"{size / MB:.1f} MB"


def check_file_size(path: str, size: int, lim: Limits) -> None:
    """Refuse one file that is larger than the owner allows."""
    if size > lim.max_file_bytes:
        raise AppError(413, (
            f"You cannot install this app: its file \"{path}\" is {_mb(size)}, and the "
            f"largest file allowed is {_mb(lim.max_file_bytes)}. Nothing was installed. If "
            "you trust the app, raise the limit in Settings, Apps."))


def check_tree_size(files: int, total: int, lim: Limits) -> None:
    """Refuse a tree with too many files or too many bytes."""
    if files > lim.max_files:
        raise AppError(413, (
            f"You cannot install this app: it has more than {lim.max_files} files. Nothing "
            "was installed. If you trust the app, raise the limit in Settings, Apps."))
    if total > lim.max_tree_bytes:
        raise AppError(413, (
            f"You cannot install this app: its files add up to more than "
            f"{_mb(lim.max_tree_bytes)}. Nothing was installed. If you trust the app, raise "
            "the limit in Settings, Apps."))


# --- names ----------------------------------------------------------------------------------


def left_out(name: str) -> bool:
    """Whether a file or folder name is never part of an app: anything starting with a
    dot (``.git``, editor and system files) and Python's cache folders. ``.`` and ``..``
    are not names: they are left for ``check_tree_path`` to refuse."""
    return (name.startswith(".") and name not in (".", "..")) or name in LEFT_OUT_DIRS


def path_left_out(path: str) -> bool:
    return any(left_out(part) for part in path.split("/"))


def check_tree_path(path: str) -> str:
    """A file's path inside an app, proven to stay inside it on every system, or
    ``AppError``. Forward slashes; no ``..``, no absolute path, no drive, no device name."""
    try:
        mf.check_path(path, "file")
    except mf.ManifestError as error:
        raise AppError(422, (
            "You cannot install this app, and nothing was installed: it has a file whose "
            f"name is not safe on every system. {error.problems[0].sentence}")) from None
    if path.count("/") >= MAX_DEPTH:
        raise AppError(422, (
            f"You cannot install this app, and nothing was installed: \"{path}\" is more "
            f"than {MAX_DEPTH} folders deep. Flatten the app's folders."))
    return path


def check_case(seen: dict[str, str], path: str) -> None:
    """Refuse two names that differ only by case: they are one file on Windows and macOS."""
    parts = path.split("/")
    for n in range(1, len(parts) + 1):
        prefix = "/".join(parts[:n])
        known = seen.setdefault(prefix.lower(), prefix)
        if known != prefix:
            raise AppError(422, (
                f"You cannot install this app, and nothing was installed: \"{known}\" and "
                f"\"{prefix}\" differ only by capital letters, which is one name on some "
                "systems. Rename one of them."))


def _link_refusal(path: str) -> AppError:
    return AppError(422, (
        f"You cannot install this app, and nothing was installed: \"{path}\" is a link. "
        "Links are refused because they can point outside the app. Put the real file in "
        "the app's folder."))


def _special_refusal(path: str) -> AppError:
    return AppError(422, (
        f"You cannot install this app, and nothing was installed: \"{path}\" is not an "
        "ordinary file or folder. Remove it from the app."))


# --- trees ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tree:
    hash: str
    files: int
    bytes: int
    left_out: int = 0  # top-level names that were not copied (dot files, caches)
    paths: tuple[str, ...] = field(default=(), repr=False)


def tree_hash(entries: Iterable[tuple[str, str]]) -> str:
    """The mark of a tree: sha256 over ``"<file sha256>  <path>\\n"`` for each file,
    sorted by path."""
    whole = hashlib.sha256()
    for path, digest in sorted(entries):
        whole.update(f"{digest}  {path}\n".encode())
    return whole.hexdigest()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _walk(folder: Path, *, skip: bool) -> Iterator[tuple[str, Path] | None]:
    """Every ordinary file under ``folder`` as (forward-slash path, real path), never
    following a link. Yields None once for each name left out. With ``skip`` off, a name
    that would be left out is refused instead: a staged tree must not hold one."""
    seen: dict[str, str] = {}

    def visit(here: Path, prefix: str) -> Iterator[tuple[str, Path] | None]:
        try:
            names = sorted(os.listdir(here))
        except OSError:
            raise AppError(422, (
                f"JimmyHQ could not read the folder \"{prefix or '.'}\" of this app, so "
                "nothing was installed. Check that you may read it.")) from None
        for name in names:
            rel = f"{prefix}{name}"
            path = here / name
            if left_out(name):
                if skip:
                    yield None
                    continue
                raise AppError(409, (
                    f"The app waiting to be installed has a file it should not, \"{rel}\". "
                    "Nothing was installed. Start the install again."))
            if _is_link(path):
                raise _link_refusal(rel)
            check_tree_path(rel)
            check_case(seen, rel)
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                yield from visit(path, rel + "/")
            elif stat.S_ISREG(mode):
                yield rel, path
            else:
                raise _special_refusal(rel)

    yield from visit(folder, "")


def _read(path: Path, rel: str, lim: Limits) -> bytes:
    """One file's bytes, opened so that a link swapped in after the walk is refused."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise _link_refusal(rel) if _is_link(path) else AppError(422, (
            f"JimmyHQ could not read \"{rel}\" in this app, so nothing was installed. "
            "Check that you may read it.")) from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise _special_refusal(rel)
        check_file_size(rel, info.st_size, lim)
        data = handle.read(lim.max_file_bytes + 1)
    check_file_size(rel, len(data), lim)
    return data


def write_file(root: Path, rel: str, data: bytes) -> None:
    """Write one checked path under ``root``. The path is checked again here, so nothing
    can be written outside ``root`` whoever calls this."""
    check_tree_path(rel)
    target = root.joinpath(*rel.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "xb") as handle:  # never over something already there
        handle.write(data)


def _gather(folder: Path, lim: Limits, *, skip: bool,
            into: Path | None = None) -> Tree:
    entries: list[tuple[str, str]] = []
    total = files = skipped = 0
    for found in _walk(folder, skip=skip):
        if found is None:
            skipped += 1
            continue
        rel, path = found
        files += 1
        check_tree_size(files, total, lim)
        data = _read(path, rel, lim)
        total += len(data)
        check_tree_size(files, total, lim)
        entries.append((rel, hashlib.sha256(data).hexdigest()))
        if into is not None:
            write_file(into, rel, data)
    return Tree(hash=tree_hash(entries), files=files, bytes=total, left_out=skipped,
                paths=tuple(sorted(p for p, _ in entries)))


def copy_folder(source: Path, dest: Path, lim: Limits) -> Tree:
    """Copy an app folder into ``dest`` (which must not exist), leaving out dot files and
    caches, refusing anything unsafe, and hashing exactly the bytes that were copied."""
    source = Path(source)
    if _is_link(source) or not source.is_dir():
        raise AppError(422, (
            "That is not a folder JimmyHQ can install from, and nothing was installed. "
            f"Give the full path of the folder that holds {mf.MANIFEST_FILE}."))
    dest.mkdir(parents=True)
    return _gather(source, lim, skip=True, into=dest)


def hash_folder(folder: Path, lim: Limits) -> Tree:
    """What ``copy_folder`` would copy from ``folder``, hashed, with nothing written."""
    folder = Path(folder)
    if _is_link(folder) or not folder.is_dir():
        raise AppError(422, f"\"{folder.name}\" is not a folder JimmyHQ can read.")
    return _gather(folder, lim, skip=True)


def hash_tree(folder: Path, lim: Limits) -> Tree:
    """Hash a staged or installed tree strictly: the same refusals, and nothing left out."""
    return _gather(Path(folder), lim, skip=False)


def _retry(action: Any, *args: Any) -> None:
    """Windows virus scanners hold new files open for a moment; try a few times."""
    for attempt in range(_RETRIES):
        try:
            action(*args)
            return
        except PermissionError:
            if attempt == _RETRIES - 1:
                raise
            time.sleep(0.1 * (attempt + 1))


def _unlock(function: Any, path: str, _exc: Any) -> None:
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)  # git marks its objects read-only
    function(path)


def delete_tree(path: Path) -> bool:
    """Delete a folder for good. True when it is gone."""
    path = Path(path)
    if _is_link(path):
        with contextlib.suppress(OSError):
            path.unlink()
        return not path.exists()
    if not path.exists():
        return True
    with contextlib.suppress(OSError):
        _retry(lambda: shutil.rmtree(path, onexc=_unlock))
    return not path.exists()


def move_tree(source: Path, dest: Path) -> None:
    """Put a staged tree in its final place. ``dest`` is replaced if it is there."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not delete_tree(dest):
        raise AppError(500, (
            f"JimmyHQ could not clear the folder {dest.name} to install into, so nothing "
            "was installed. Close anything that has it open, then try again."))
    try:
        _retry(os.rename, source, dest)
    except OSError:
        shutil.copytree(source, dest, symlinks=True)
        delete_tree(source)


# --- places ---------------------------------------------------------------------------------


def root(core: Core) -> Path:
    return core.config.data / APPS_DIR


def staging_root(core: Core) -> Path:
    return root(core) / STAGING


def app_dir(core: Core, app_id: str) -> Path:
    if not mf._ID.match(app_id):  # noqa: SLF001 - one rule for what an id is
        raise AppError(404, f"There is no app called \"{app_id[:40]}\". Nothing was changed.")
    return root(core) / app_id


def version_name(version: str, tree: str) -> str:
    return f"{version}-{tree[:8]}"


def version_dir(core: Core, app_id: str, version: str, tree: str) -> Path:
    return app_dir(core, app_id) / "versions" / version_name(version, tree)


def data_dir(core: Core, app_id: str) -> Path:
    """Where the app's own data lives. It outlives a removal unless the owner says not."""
    return app_dir(core, app_id) / "data"


# --- what ships with JimmyHQ ----------------------------------------------------------------

_PACKAGE = Path(__file__).resolve().parent
_CHECKOUT = _PACKAGE.parents[2]


def bundled(packed: str, checkout: str) -> Path | None:
    """A file or folder that ships with JimmyHQ: beside this package in an installed
    copy (``pyproject.toml`` packs it there), or at its place in a source checkout."""
    for candidate in (_PACKAGE / packed, _CHECKOUT.joinpath(*checkout.split("/"))):
        if candidate.exists():
            return candidate
    return None


def examples_dir() -> Path | None:
    """The folder of example apps, one folder for each, named by its id."""
    return bundled("_apps/examples", "apps/examples")


EXAMPLES_FOLDER = "examples"  # inside the first-party folder of a checkout; never an app


def first_party_dir() -> Path | None:
    """The folder of first-party apps, made by the JimmyHQ project and shipped with it,
    one folder for each, named by its id. In a checkout this is the repository's ``apps/``
    folder, which also holds ``examples/``: that folder is never an app."""
    return bundled("_apps", "apps")


def bridge_script() -> Path | None:
    """The JavaScript bridge every app's page is served as ``_bridge.js``."""
    return bundled("_sdk/bridge.js", "sdk/js/bridge.js")


def ui_script() -> Path | None:
    """The page helper every app's page is served as ``_ui.js``: building the page from
    text nodes only, and dialogs of the page's own (``sdk/js/ui.js``)."""
    return bundled("_sdk/ui.js", "sdk/js/ui.js")


# --- what is installed ----------------------------------------------------------------------


class _Service:
    def __init__(self, _core: Core):
        self.lens = AppsLens()
        self.lock = threading.RLock()  # one install, removal or switch at a time
        self.manifests: dict[tuple[str, str, str], mf.Manifest] = {}


def service(core: Core) -> _Service:
    return core.service("apps.store", _Service)


def state(core: Core) -> dict[str, Any]:
    """The ``apps`` lens, caught up."""
    return core.lenses.read(service(core).lens)


def installed(core: Core) -> dict[str, dict[str, Any]]:
    """Every installed app's row, by id."""
    return state(core)["apps"]


def get(core: Core, app_id: str) -> dict[str, Any] | None:
    return installed(core).get(app_id)


def need(core: Core, app_id: str) -> dict[str, Any]:
    row = get(core, app_id)
    if row is None:
        raise AppError(404, (
            f"There is no app called \"{app_id[:40]}\" installed. Nothing was changed. "
            "The Apps screen lists what is installed."))
    return row


def granted(core: Core, app_id: str) -> tuple[str, ...]:
    """What the owner has agreed this app may do, as the manifest writes permissions.
    Empty for an app that is not installed or is switched off."""
    row = get(core, app_id)
    return tuple(row["permissions"]) if row and row["enabled"] else ()


def is_enabled(core: Core, app_id: str) -> bool:
    row = get(core, app_id)
    return bool(row and row["enabled"])


def code_dir(core: Core, app_id: str) -> Path | None:
    """The folder holding the installed version's files, or None if it is not installed."""
    row = get(core, app_id)
    return version_dir(core, app_id, row["version"], row["tree"]) if row else None


def manifest_of(core: Core, app_id: str) -> mf.Manifest:
    """The installed version's manifest. ``AppError`` if the app is not installed or its
    files are no longer readable."""
    row = need(core, app_id)
    key = (app_id, row["version"], row["tree"])
    svc = service(core)
    found = svc.manifests.get(key)
    if found is None:
        path = version_dir(core, app_id, row["version"], row["tree"]) / mf.MANIFEST_FILE
        try:
            found = mf.parse(path.read_bytes())
        except (OSError, mf.ManifestError):
            raise AppError(500, (
                f"The files of the app \"{row['name']}\" are missing or damaged, so it "
                "cannot be used. Remove it on the Apps screen, then install it again; its "
                "data is kept.")) from None
        svc.manifests[key] = found
    return found


def manifests(core: Core) -> list[mf.Manifest]:
    """The manifest of every installed app whose files can be read."""
    out = []
    for app_id in installed(core):
        with contextlib.suppress(AppError):
            out.append(manifest_of(core, app_id))
    return out


def _screens(items: Iterable[mf.Screen]) -> list[dict[str, Any]]:
    return [{"id": s.id, "label": s.label, "described": s.described} for s in items]


def places(m: mf.Manifest) -> dict[str, list[dict[str, Any]]]:
    """Where an app shows itself: its screens, side-panel tabs and Wall tiles. A file's
    path is not part of it; the shell is told that when it mounts the screen."""
    return {"screens": _screens(m.screens), "dock": _screens(m.dock),
            "wall": _screens(m.wall)}


def shell_list(core: Core) -> list[dict[str, Any]]:
    """What the shell needs to build its sidebar, dock and wall: no more than that."""
    from jimmyhq.apps import counts  # noqa: PLC0415 - it imports the bridge, which imports this

    out = []
    rows = installed(core)
    said = counts.shown(core, rows)
    for app_id, row in sorted(rows.items(), key=lambda kv: kv[1]["name"].lower()):
        item: dict[str, Any] = {
            "id": app_id, "name": row["name"], "version": row["version"], "tier": row["tier"],
            "enabled": row["enabled"], "icon": "", "screens": [], "dock": [], "wall": [],
            "broken": None,
            # What a full app's program last said waits on the owner, or None for unknown
            # (never said, or switched off): the sidebar's number when its page is closed.
            "count": said.get(app_id),
        }
        try:
            found = manifest_of(core, app_id)
            item |= places(found)
            item["icon"] = found.icon
        except AppError as error:
            item["broken"] = error.detail
        out.append(item)
    return out


def no_code(m: mf.Manifest) -> bool:
    """True for an app that is a description and nothing else: sandboxed, and JimmyHQ
    itself draws every screen, tab and tile it has. None of its own files is ever shown."""
    shown = m.screens + m.dock + m.wall
    return m.tier == "sandboxed" and bool(shown) and all(s.described for s in shown)


def adds(m: mf.Manifest) -> dict[str, Any]:
    """What an app adds to JimmyHQ, for the install dialog and the detail screen."""
    return {
        "screens": [s.label for s in m.screens], "dock": [s.label for s in m.dock],
        "wall": [s.label for s in m.wall], "cards": [c.label for c in m.cards],
        "memory_sources": [s.label for s in m.memory_sources],
        "tools": [t.name for t in m.tools], "schedule": m.allows("jobs"),
        "settings": [s.label for s in m.settings], "secrets": [s.label for s in m.secrets],
    }
