"""Updating an app, rolling it back, and erasing what it wrote when it is removed.

**Finding an update.** ``check_for_updates`` runs in the worker every few minutes and, for
each app not looked at for ``apps.update_check_hours``, asks:

- an app installed from a git address with a branch or tag name (``ref``): which commit
  does that name point at now (``git ls-remote``, nothing fetched)?
- an app copied from an example that ships with JimmyHQ: is the copy that ships now a
  newer version?

What it finds is kept in ``apps.db`` (working state, ``found``) and the Apps screen says
"Update available". Nothing is installed unless the owner allows it: ``apps.auto_update``
for every app, ``app.<id>.auto_update`` for one. Even then, a version that asks for a new
permission, drops the ``private`` promise or changes tier waits for the owner.

**Updating is two calls, like installing.** ``plan_update`` fetches the new version into
staging beside the old one, proves its files safe exactly as an install does, and
answers with what the owner must read: only what is new (``permissions.diff``).
``confirm_update`` carries the plan's id and fingerprint back; the staged files are
hashed again and the difference worked out again from what is granted now, so a version
that changed after the owner looked, or that asks for more than it was shown asking, is
refused and nothing changes.

**Switching** (``switch``): stop the program and wait until it has gone, let go of the
app's database, copy ``data/`` to ``backups/<old version>/``, print the event, start the
new version. The last ``apps.keep_versions`` versions are kept with their environments
and backups (``prune``); older ones are deleted.

**Rolling back** (``rollback``) switches to a kept version, the same way, and offers the
backup of the data taken when that version was replaced (``restore_data``). Its
permissions are again what the owner agreed to for it. Only a version this app really ran
and still keeps can be named; nothing the caller sends is joined onto a path before that.

**A version that keeps crashing** is rolled back only when the owner chose that
(``apps.crash_loop`` = ``roll_back``), the crashing began with an update, the program has
not once run steadily since, and the version before is still kept (``crash_rollback``).

**Erasing** (``erase_record``): what an app wrote on the Tape, every event printed as
``app:<id>`` and every event of its own kinds, is erased with ``Tape.erase``: the events
keep their place and lose what they said. The record of its install, updates and removal
stays: the owner did those.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import re
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from jimmyhq import ids, storeversion
from jimmyhq.apps import (
    dbfile,
    environment,
    feeds,
    install,
    kinds,
    quotas,
    runtime,
    storage_ask,
    store,
)
from jimmyhq.apps import manifest as mf
from jimmyhq.apps import permissions as perms
from jimmyhq.apps.store import AppError
from jimmyhq.settings import SettingError
from jimmyhq.tape.store import TapeConflict

if TYPE_CHECKING:
    from fastapi import APIRouter

    from jimmyhq.core import Core

log = logging.getLogger("jimmyhq.apps.update")

BACKUPS = "backups"
JOB_EVERY_S = 600  # how often the worker asks whether any app is due a look
CARD_KIND = "apps.updated"
ERASE_PAGE = 5000
_VERSION_NAME = re.compile(r"^[0-9A-Za-z.+-]{1,80}-[0-9a-f]{8}\Z")
_PRE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+.*)?\Z")


# --- what may be asked for --------------------------------------------------------------------


def version_key(version: str) -> tuple[Any, ...]:
    """Semantic-version order: numbers first, and a pre-release before its release."""
    found = _PRE.match(version or "")
    if not found:
        return (-1,)
    major, minor, patch, pre = found.groups()
    parts: tuple[Any, ...] = ()
    if pre:
        parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (int(major), int(minor), int(patch), 0 if pre else 1, parts)


def check_same_app(row: dict[str, Any], m: mf.Manifest) -> None:
    """An update is the same app: the same id."""
    if m.id != row["app_id"]:
        raise AppError(422, (
            f"That is not a version of {row['name']}: its id is \"{m.id}\", not "
            f"\"{row['app_id']}\". Nothing was changed. Install it as an app of its own "
            "instead."))


def check_newer(row: dict[str, Any], m: mf.Manifest) -> None:
    """An update has a higher version number than the one installed. Going back to an
    older one is Roll back, to a version that was kept."""
    if version_key(m.version) <= version_key(row["version"]):
        raise AppError(409, (
            f"That copy of {row['name']} is version {m.version}, and you have "
            f"{row['version']}, so it is not an update. Nothing was changed. To go back to "
            "an earlier version, use Roll back on the app's page."))


def check_version_name(name: Any) -> str:
    """A kept version is named ``<version>-<hash8>`` and nothing else, so it is never a
    path."""
    if not isinstance(name, str) or not _VERSION_NAME.match(name) or ".." in name:
        raise never_kept()
    return name


def never_kept() -> AppError:
    return AppError(404, (
        "You cannot roll back to that version: this app never ran it here, or it is no "
        "longer kept. Nothing was changed. The app's page lists the versions you can go "
        "back to."))


def not_stopped(name: str, what: str) -> AppError:
    return AppError(409, (
        f"{name}'s program did not stop in time, so it was not {what} and nothing was "
        "changed. Try again in a moment."))


def changed_since() -> AppError:
    return AppError(409, (
        "The app changed after this update was shown to you (it was updated, rolled back "
        "or removed), so nothing was changed. Start the update again."))


class _Granted:
    """What the owner agreed to for the version in use, in the shape ``permissions.diff``
    reads. What was granted, not what the old manifest asked for, is what counts."""

    def __init__(self, row: dict[str, Any], private: bool, owner_hosts: Any = None):
        self.id = row["app_id"]
        self.tier = row["tier"]
        self.permissions = tuple(row["permissions"])
        self.private = private
        # Like ``private``, a promise of the installed version's manifest, which the
        # owner's consent (its fingerprint) covers.
        self.owner_hosts = owner_hosts


def granted_now(core: Core, row: dict[str, Any]) -> _Granted:
    try:
        installed = store.manifest_of(core, row["app_id"])
        private, owner_hosts = installed.private, installed.owner_hosts
    except AppError:
        private, owner_hosts = False, None
    return _Granted(row, private, owner_hosts)


def needs_typed_name(row: dict[str, Any], m: mf.Manifest) -> bool:
    """A sandboxed app that becomes a full one is agreed to as an install of a full app
    is: with its name typed out."""
    return m.tier == perms.FULL and row["tier"] != perms.FULL


# --- updates found ----------------------------------------------------------------------------



class Found:
    """The updates JimmyHQ has found and not yet put in place. Working state only: what
    happens to an app is printed on the Tape when it happens."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None,
                                   timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        storeversion.prepare(self._db, dbfile.STORE)

    def get(self, app_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM app_updates WHERE app_id=?",
                                   (app_id,)).fetchone()
        return None if row is None else dict(row)

    def checked(self, app_id: str, now: int) -> None:
        with self._lock:
            self._db.execute("INSERT INTO app_updates (app_id, checked_at) VALUES (?,?) ON "
                             "CONFLICT (app_id) DO UPDATE SET checked_at=excluded.checked_at",
                             (app_id, now))

    def found(self, app_id: str, now: int, *, source: str, repo: str | None = None,
              ref: str | None = None, commit: str | None = None,
              example: str | None = None) -> None:
        before = self.get(app_id) or {}
        same = (before.get("source"), before.get("commit_"), before.get("example")) == (
            source, commit, example) and before.get("found_at")
        with self._lock:
            self._db.execute(
                "INSERT INTO app_updates (app_id, checked_at, found_at, source, repo, ref, "
                "commit_, example, version, needs_consent, problem) VALUES (?,?,?,?,?,?,?,?,"
                "?,?,?) ON CONFLICT (app_id) DO UPDATE SET checked_at=excluded.checked_at, "
                "found_at=excluded.found_at, source=excluded.source, repo=excluded.repo, "
                "ref=excluded.ref, commit_=excluded.commit_, example=excluded.example, "
                "version=excluded.version, needs_consent=excluded.needs_consent, "
                "problem=excluded.problem",
                (app_id, now, before["found_at"] if same else now, source, repo, ref, commit,
                 example, before.get("version") if same else None,
                 before.get("needs_consent") if same else None,
                 before.get("problem") if same else None))

    def learned(self, app_id: str, *, version: str | None = None,
                needs_consent: bool | None = None, problem: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE app_updates SET version=COALESCE(?, version), "
                "needs_consent=COALESCE(?, needs_consent), problem=? WHERE app_id=?",
                (version, None if needs_consent is None else int(needs_consent), problem,
                 app_id))

    def clear(self, app_id: str) -> None:
        """The update was put in place, or none is waiting any more."""
        with self._lock:
            self._db.execute(
                "UPDATE app_updates SET found_at=NULL, source=NULL, repo=NULL, ref=NULL, "
                "commit_=NULL, example=NULL, version=NULL, needs_consent=NULL, problem=NULL "
                "WHERE app_id=?", (app_id,))

    def forget(self, app_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM app_updates WHERE app_id=?", (app_id,))

    def close(self) -> None:
        with self._lock:
            self._db.close()


def found_store(core: Core) -> Found:
    return core.service("apps.updates", lambda c: Found(quotas.state_db(c)))


def forget(core: Core, app_id: str) -> None:
    found_store(core).forget(app_id)


def waiting(core: Core, app_id: str) -> dict[str, Any] | None:
    """The update found for an app and not yet put in place, for the Apps screen."""
    row = found_store(core).get(app_id)
    if not row or not row.get("source"):
        return None
    consent = row.get("needs_consent")
    return {"source": row["source"], "repo": row.get("repo"), "ref": row.get("ref"),
            "commit": row.get("commit_"), "version": row.get("version"),
            "found_at": row.get("found_at"),
            "needs_consent": None if consent is None else bool(consent),
            "problem": row.get("problem")}


def available_label(update: dict[str, Any] | None) -> str | None:
    """The few words after "Update available": its version, or its commit."""
    if not update:
        return None
    if update.get("version"):
        return str(update["version"])
    commit = update.get("commit")
    return f"commit {commit[:8]}" if commit else "a new version"


# --- the places -----------------------------------------------------------------------------


def backups_dir(core: Core, app_id: str) -> Path:
    return store.app_dir(core, app_id) / BACKUPS


def backup_dir(core: Core, app_id: str, version_name: str) -> Path:
    return backups_dir(core, app_id) / check_version_name(version_name)


def current_name(row: dict[str, Any]) -> str:
    return store.version_name(row["version"], row["tree"])


def history(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Every version the app has run here, each once, the one in use first and then the
    most recently used."""
    seen: dict[str, dict[str, Any]] = {}
    for entry in row.get("versions", []):
        if entry.get("version") and entry.get("tree"):
            name = store.version_name(entry["version"], entry["tree"])
            seen.pop(name, None)
            seen[name] = entry | {"name": name}
    now = current_name(row)
    ordered = list(reversed(seen.values()))
    ordered.sort(key=lambda e: e["name"] != now)  # stable: the one in use first
    return ordered


def keep_count(core: Core) -> int:
    return max(1, int(core.settings.get("apps.keep_versions")))


def kept(core: Core, app_id: str) -> list[dict[str, Any]]:
    """The versions an app can be switched to, the one in use first: within
    ``apps.keep_versions`` and with their code still in place."""
    row = store.need(core, app_id)
    out = []
    for entry in history(row)[:keep_count(core)]:
        name = entry["name"]
        if not store.version_dir(core, app_id, entry["version"], entry["tree"]).is_dir():
            continue
        backup = backups_dir(core, app_id) / name
        out.append({
            "name": name, "version": entry["version"], "tree": entry["tree"],
            "used_at": entry.get("at"), "current": name == current_name(row),
            "has_backup": backup.is_dir(),
            "backup_at": _mtime_ms(backup) if backup.is_dir() else None,
            "permissions": list(entry.get("permissions") or []),
            "tier": entry.get("tier"),
        })
    return out


def local_carried(core: Core, row: dict[str, Any], tier: str) -> dict[str, Any] | None:
    """For the update's consent: the addresses on the owner's own network he let this app
    reach, and whether a version of ``tier`` keeps them (only the same tier does), or None
    when he let it reach none."""
    from jimmyhq.apps import local_reach  # noqa: PLC0415 - it imports this package

    held = list(local_reach.granted(row))
    if not held:
        return None
    return {"addresses": held, "kept": tier == row["tier"]}


def _mtime_ms(path: Path) -> int | None:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return None


# --- data, kept beside each version ---------------------------------------------------------


def _copy_replace(source: Path, dest: Path) -> None:
    """Copy a folder to ``dest``, replacing it, by way of a new folder beside it."""
    temp = dest.with_name(f".{dest.name}.{ids.ulid()}")
    try:
        shutil.copytree(source, temp, symlinks=True)
        if dest.exists() and not store.delete_tree(dest):
            raise AppError(500, (
                f"JimmyHQ could not replace the folder {dest.name}, so nothing was changed. "
                "Close anything that has the app's files open, then try again."))
        store.move_tree(temp, dest)
    finally:
        store.delete_tree(temp)


def backup_data(core: Core, app_id: str, version_name: str) -> bool:
    """Copy the app's data to ``backups/<version_name>/``, replacing an older copy. False
    when it has no data yet (any older copy is then removed: it is not this version's)."""
    source = store.data_dir(core, app_id)
    dest = backup_dir(core, app_id, version_name)
    if not source.is_dir():
        store.delete_tree(dest)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _copy_replace(source, dest)
    except OSError:
        raise AppError(500, (
            "JimmyHQ could not copy the app's data before changing its version, so nothing "
            "was changed. Check there is room on the disk, then try again.")) from None
    return True


def restore_data(core: Core, app_id: str, version_name: str) -> None:
    """Put the backup of ``version_name`` back as the app's data. The backup stays."""
    source = backup_dir(core, app_id, version_name)
    if not source.is_dir():
        raise AppError(404, (
            "There is no copy of the app's data from that version, so nothing was changed. "
            "Roll back without putting its data back."))
    try:
        _copy_replace(source, store.data_dir(core, app_id))
    except OSError:
        raise AppError(500, (
            "JimmyHQ could not put back the app's data from that version, so nothing was "
            "changed. Check there is room on the disk, then try again.")) from None


def prune(core: Core, app_id: str) -> list[str]:
    """Delete the versions beyond ``apps.keep_versions``, with their environments and
    backups. Returns the names deleted. Only versions the app's record lists are touched."""
    row = store.need(core, app_id)
    dropped = []
    for entry in history(row)[keep_count(core):]:
        name = entry["name"]
        home = store.app_dir(core, app_id)
        for folder in (home / "versions" / name, home / "venvs" / name, home / BACKUPS / name):
            store.delete_tree(folder)
        dropped.append(name)
    return dropped


# --- switching ------------------------------------------------------------------------------


def switch(core: Core, app_id: str, print_events: Any, *, restore: str | None = None,
           what: str = "updated") -> None:
    """Stop, back up the data, print, (restore), start. Raises with nothing changed when
    the program does not stop or the data cannot be copied."""
    row = store.need(core, app_id)
    old = current_name(row)
    running = row.get("tier") == perms.FULL
    asked = runtime.pause(core, app_id) if running else 0
    try:
        if running and not runtime.wait_stopped(core, app_id, asked):
            raise not_stopped(row["name"], what)
        install._close(core, app_id)  # noqa: SLF001 - let go of its files first
        backup_data(core, app_id, old)
        if restore is not None:
            restore_data(core, app_id, restore)
        try:
            print_events()
        except BaseException:
            if restore is not None:
                with contextlib.suppress(AppError):
                    restore_data(core, app_id, old)  # as it was a moment ago
            raise
    finally:
        if running:
            runtime.resume(core, app_id)
    install._publish(core)  # noqa: SLF001
    prune(core, app_id)


# --- plans ----------------------------------------------------------------------------------


def _plan_update(core: Core, plan_id: str, folder: Path, source: dict[str, Any], skipped: int,
                 app_id: str, origin: Path | None = None) -> dict[str, Any]:
    row = store.need(core, app_id)
    tree = store.hash_tree(folder / "tree", store.limits(core))
    try:
        m = mf.load(folder / "tree")
        mf.check_alongside(m, [x for x in store.manifests(core) if x.id != app_id])
    except mf.ManifestError as error:
        raise AppError(422, str(error)) from None
    check_same_app(row, m)
    install.check_records(m)
    check_newer(row, m)
    install.check_full_allowed(core, m.tier, source["kind"])
    change = perms.diff(granted_now(core, row), m)
    came_from = install.provenance(core, origin, source["kind"], tree.hash, m.id)
    now = ids.now_ms()
    record = {
        "plan": plan_id, "created_at": now,
        "expires_at": now + int(core.settings.get("apps.plan_minutes")) * 60_000,
        "app": {"id": m.id, "name": m.name, "version": m.version, "author": m.author,
                "summary": m.summary, "licence": m.licence, "tier": m.tier},
        "source": {**source, "tree": tree.hash, "files": tree.files, "bytes": tree.bytes,
                   "left_out": skipped},
        "reviewed": False, "provenance": came_from,
        "review": install.review_sentence(came_from, reviewed=False),
        "warning": install.tier_warning(m.tier) if change.tier else None,
        "needs_typed_name": needs_typed_name(row, m),
        "permissions": feeds.explain(core, [line.as_dict() for line in perms.describe(m)]),
        "adds": store.adds(m), "described": store.no_code(m),
        "fingerprint": perms.fingerprint(m),
        "feeds": feeds.plan_lines(core, m),
        "update": {"app_id": app_id, "from_version": row["version"], "from_tree": row["tree"],
                   "ask": feeds.explain(core, [line.as_dict() for line in change.ask]),
                   "removed": [line.as_dict() for line in change.removed],
                   "tier": list(change.tier) if change.tier else None,
                   "needs_consent": change.needs_consent,
                   "storage": storage_ask.plan_line(core, m),
                   "local_addresses": local_carried(core, row, m.tier)},
    }
    (folder / "plan.json").write_text(json.dumps(record, indent=1), encoding="utf-8",
                                      newline="\n")
    waiting_now = waiting(core, app_id)
    if waiting_now and (waiting_now["commit"] in (None, source.get("commit"))):
        found_store(core).learned(app_id, version=m.version,
                                  needs_consent=change.needs_consent, problem=None)
    return record


def _staged(core: Core, work: Any) -> dict[str, Any]:
    plan_id, staged, empty = install._stage(core)  # noqa: SLF001
    try:
        return work(plan_id, staged, empty)
    except BaseException:
        store.delete_tree(staged)
        raise


def plan_folder(core: Core, app_id: str, folder: str | Path) -> dict[str, Any]:
    """Stage a new version of an installed app from a folder on this computer."""
    store.need(core, app_id)
    if not isinstance(folder, str | Path) or not str(folder).strip():
        raise AppError(422, "Give the folder to update from. Nothing was changed.")
    source = Path(folder).expanduser()

    def work(plan_id: str, staged: Path, _empty: Path) -> dict[str, Any]:
        tree = store.copy_folder(source, staged / "tree", store.limits(core))
        return _plan_update(core, plan_id, staged,
                            {"kind": "folder", "repo": None, "commit": None, "ref": None},
                            tree.left_out, app_id, origin=source)
    return _staged(core, work)


def plan_git(core: Core, app_id: str, url: str | None = None, *, commit: str | None = None,
             ref: str | None = None) -> dict[str, Any]:
    """Stage a new version from a git address (the one it was installed from, unless
    another is given) at one exact commit, or the commit a branch or tag names now."""
    row = store.need(core, app_id)
    url = install.check_git_url(url or row.get("repo") or "")
    if commit:
        commit = install.check_commit(commit)
    else:
        ref = install.check_ref(ref or row.get("ref") or "")

    def work(plan_id: str, staged: Path, empty: Path) -> dict[str, Any]:
        pinned = commit or install.resolve_ref(core, url, ref or "", empty=empty)
        remote, protocol = install._transport(url)  # noqa: SLF001
        repo = staged / "repo.git"
        install.fetch_commit(core, remote, pinned, repo, empty=empty, protocol=protocol)
        skipped = install.extract_commit(core, repo, pinned, staged / "tree",
                                         store.limits(core), empty=empty, protocol=protocol)
        store.delete_tree(repo)
        return _plan_update(core, plan_id, staged, {"kind": "git", "repo": url,
                                                    "commit": pinned, "ref": ref}, skipped,
                            app_id)
    return _staged(core, work)


def plan_example(core: Core, app_id: str) -> dict[str, Any]:
    """Stage the copy of an example that ships with this JimmyHQ as the new version of the
    app of the same id."""
    store.need(core, app_id)
    origin = install.bundled_folder(app_id)
    if origin is None or app_id not in {e["id"] for e in install.examples()}:
        raise AppError(404, (
            "No app that ships with JimmyHQ has this app's id, so there is nothing to "
            "update it from here. Nothing was changed. Update it from a folder or an "
            "address instead."))

    def work(plan_id: str, staged: Path, _empty: Path) -> dict[str, Any]:
        tree = store.copy_folder(origin, staged / "tree", store.limits(core))
        source = {"kind": "folder", "repo": None, "commit": None, "ref": None,
                  "example": app_id}
        return _plan_update(core, plan_id, staged, source, tree.left_out, app_id,
                            origin=origin)
    return _staged(core, work)


def plan_found(core: Core, app_id: str) -> dict[str, Any]:
    """Stage the update JimmyHQ found for this app."""
    found = waiting(core, app_id)
    if found is None:
        raise AppError(404, (
            "JimmyHQ has not found a newer version of this app, so nothing was changed. "
            "Update it from a folder or an address instead."))
    if found["source"] == "example":
        return plan_example(core, app_id)
    return plan_git(core, app_id, found["repo"], commit=found["commit"])


# --- consent and the switch -------------------------------------------------------------------


def confirm_update(core: Core, plan_id: str, fingerprint: str, *, typed_name: str | None = None,
                   actor: str = "owner") -> dict[str, Any]:
    """Put a staged version in place, if this is the consent for exactly what was shown.

    Refused, with nothing changed: a plan that is not an update, ran out, or carries
    another fingerprint; staged files that no longer hash to what was shown; a manifest
    that now asks for something the plan did not list; an app that changed since; and,
    when no person is asking (``actor`` "system"), any update that needs consent."""
    folder = install._plan_dir(core, plan_id)  # noqa: SLF001 - checks the id first
    with store.service(core).lock:
        record = install._read_record(folder)  # noqa: SLF001
        if record is None or "update" not in record:
            raise AppError(404, (
                "That update is no longer waiting: it was cancelled, done, or ran out of "
                "time. Nothing was changed. Start the update again."))
        if record.get("plan") != plan_id or record.get("expires_at", 0) <= ids.now_ms():
            store.delete_tree(folder)
            raise AppError(410, (
                "That update waited too long for an answer and was thrown away. Nothing "
                "was changed. Start the update again."))
        if not isinstance(fingerprint, str) or not hmac.compare_digest(
                fingerprint.encode("utf-8", "replace"), str(record["fingerprint"]).encode()):
            raise AppError(409, (
                "That is not the consent for this update. Nothing was changed. Start the "
                "update again and agree to what it shows."))
        planned = record["update"]
        app_id = planned["app_id"]
        changed = AppError(409, (
            "The version waiting to be put in place is not what you were shown: its files "
            "changed after the update was planned. Nothing was changed, and it was thrown "
            "away. Start the update again."))
        try:
            tree = store.hash_tree(folder / "tree", store.limits(core))
            m = mf.load(folder / "tree")
        except (AppError, mf.ManifestError, OSError):
            store.delete_tree(folder)
            raise changed from None
        source = record["source"]
        if (tree.hash != source["tree"] or perms.fingerprint(m) != record["fingerprint"]
                or m.id != app_id or m.version != record["app"]["version"]):
            store.delete_tree(folder)
            raise changed
        row = store.get(core, app_id)
        if row is None or (row["version"], row["tree"]) != (planned["from_version"],
                                                             planned["from_tree"]):
            store.delete_tree(folder)
            raise changed_since()
        change = perms.diff(granted_now(core, row), m)
        if [x.id for x in change.ask] != [x["id"] for x in planned["ask"]] or \
                change.needs_consent != planned["needs_consent"]:
            store.delete_tree(folder)
            raise changed
        if actor == "system" and change.needs_consent:
            raise AppError(403, (
                f"This version of {m.name} asks for something new, so only you can agree to "
                "it. Nothing was changed. Open the app on the Apps screen and press Update."))
        if needs_typed_name(row, m) and (not isinstance(typed_name, str)
                                         or typed_name.strip() != m.name):
            raise AppError(422, (
                f"Nothing was changed. This version of {m.name} becomes a full app, which "
                f"runs as you: type its name, {m.name}, exactly, to show you mean it."))
        check_newer(row, m)
        install.check_full_allowed(core, m.tier, source["kind"])
        try:
            mf.check_alongside(m, [x for x in store.manifests(core) if x.id != app_id])
        except mf.ManifestError as error:
            raise AppError(422, str(error)) from None

        name = store.version_name(m.version, tree.hash)
        dest = store.version_dir(core, app_id, m.version, tree.hash)
        made = not dest.is_dir()
        if made:
            store.move_tree(folder / "tree", dest)
        store.delete_tree(folder)
        try:
            environment.build(core, app_id, m.name, name, dest)
        except BaseException:
            if made:
                store.delete_tree(dest)
            raise
        subject = kinds.subject(app_id)

        def print_events() -> None:
            seq = core.tape.subject_last_seq(subject)
            if change.needs_consent:
                core.tape.print("apps.permissions_granted", {
                    "app_id": app_id, "version": m.version, "tier": m.tier,
                    "permissions": list(m.permissions), "added": [x.id for x in change.ask],
                    "removed": [x.id for x in change.removed],
                    "fingerprint": record["fingerprint"],
                }, actor=actor, subject=subject, expect_subject_seq=seq)
                seq = core.tape.subject_last_seq(subject)
            core.tape.print("apps.updated", {
                "app_id": app_id, "name": m.name, "version": m.version, "tier": m.tier,
                "source": source["kind"], "repo": source["repo"], "commit": source["commit"],
                "tree": tree.hash, "permissions": list(m.permissions),
                "fingerprint": record["fingerprint"], "reviewed": False,
                "ref": source.get("ref"), "example": source.get("example")
                if record.get("provenance") == install.BUNDLED else None,
                "from_version": row["version"],
            }, actor=actor, subject=subject, expect_subject_seq=seq)

        try:
            switch(core, app_id, print_events)
        except TapeConflict:
            raise AppError(409, (
                "Something else changed this app at the same moment, so nothing was "
                "changed. Look at the Apps screen, then try again.")) from None
        except BaseException:
            if made and current_name(store.need(core, app_id)) != name:
                store.delete_tree(dest)
                store.delete_tree(environment.venv_dir(core, app_id, name))
            raise
        found_store(core).clear(app_id)
        from jimmyhq.apps import owner_hosts  # noqa: PLC0415 - it imports this package

        owner_hosts.drop_undeclared(core, app_id, actor=actor)  # only while still declared
        if actor != "system":  # by itself, a version keeps the storage it had
            storage_ask.grant(core, m, actor=actor)
        return install.detail(core, app_id)


def rollback(core: Core, app_id: str, version: Any, *, restore: bool = False,
             actor: str = "owner", reason: str = "owner") -> dict[str, Any]:
    """Switch back to a kept version, named as ``<version>-<hash8>``. With ``restore``, the
    copy of the data taken when that version was replaced is put back too; the data of
    the version left behind is kept as its own backup."""
    name = check_version_name(version)
    with store.service(core).lock:
        row = store.need(core, app_id)
        target = next((v for v in kept(core, app_id) if v["name"] == name), None)
        if target is None:
            raise never_kept()
        if target["current"]:
            raise AppError(409, (
                f"{row['name']} is already on version {target['version']}. Nothing was "
                "changed."))
        if restore and not target["has_backup"]:
            raise AppError(404, (
                f"There is no copy of {row['name']}'s data from version {target['version']}, "
                "so nothing was changed. Roll back without putting its data back."))
        folder = store.version_dir(core, app_id, target["version"], target["tree"])
        try:
            m = mf.load(folder)
            mf.check_alongside(m, [x for x in store.manifests(core) if x.id != app_id])
        except mf.ManifestError:
            raise AppError(500, (
                f"The files of {row['name']} {target['version']} are missing or damaged, so "
                "it cannot be rolled back to. Nothing was changed.")) from None
        environment.build(core, app_id, m.name, name, folder)
        subject = kinds.subject(app_id)

        def print_events() -> None:
            core.tape.print("apps.rolled_back", {
                "app_id": app_id, "from_version": row["version"], "version": target["version"],
                "tree": target["tree"], "reason": reason, "data_restored": restore,
            }, actor=actor, subject=subject,
                expect_subject_seq=core.tape.subject_last_seq(subject))

        try:
            switch(core, app_id, print_events, restore=name if restore else None,
                   what="rolled back")
        except TapeConflict:
            raise AppError(409, (
                "Something else changed this app at the same moment, so nothing was "
                "changed. Look at the Apps screen, then try again.")) from None
        from jimmyhq.apps import owner_hosts  # noqa: PLC0415 - it imports this package

        owner_hosts.drop_undeclared(core, app_id, actor=actor)
        return install.detail(core, app_id)


def began_after_update(row: dict[str, Any], status: dict[str, Any]) -> bool:
    """Whether a crash loop began with the latest update: the last switch was an update,
    and every start since was followed by a quick exit (the program never once ran
    steadily on the new version)."""
    switched = row.get("switched") or {}
    if switched.get("how") != "updated" or not row.get("previous"):
        return False
    starts = [r for r in status.get("restarts") or [] if isinstance(r, int)
              and r >= int(switched.get("at") or 0)]
    failures = int(status.get("failures") or 0)
    return bool(starts) and failures >= len(starts)


def crash_rollback(core: Core, app_id: str, status: dict[str, Any]) -> str | None:
    """Roll a crashing app back to the version before its update, when the crashing began
    with that update and the version is kept. Returns the version gone back to, or None
    when it was not rolled back."""
    row = store.get(core, app_id)
    if row is None or not began_after_update(row, status):
        return None
    previous = row["previous"]
    name = store.version_name(previous["version"], previous["tree"])
    target = next((v for v in kept(core, app_id) if v["name"] == name and not v["current"]),
                  None)
    if target is None:
        return None
    # The data the old version left goes back with it, when a copy was kept and the owner
    # has not said otherwise: a new version may have changed its shape.
    restore = bool(target["has_backup"]
                   and core.settings.get("apps.crash_rollback_restores_data"))
    try:
        rollback(core, app_id, name, restore=restore, actor="system", reason="crashing")
    except AppError as error:
        log.warning("could not roll %s back after it kept crashing: %s", app_id, error.detail)
        return None
    return previous["version"]


# --- looking for updates --------------------------------------------------------------------


def auto_allowed(core: Core, app_id: str) -> bool:
    """Whether a new version of this app may be put in place without the owner's click
    (when it asks for nothing new): the app's own setting, or the one for every app."""
    try:
        own = core.settings.get(f"app.{app_id}.auto_update")
    except SettingError:
        own = "default"
    if own in ("wait", "auto"):
        return own == "auto"
    return core.settings.get("apps.auto_update") == "auto"


def newer_example(core: Core, row: dict[str, Any]) -> bool:
    """Whether the example this app was copied from ships as a newer version now."""
    example = row.get("example")
    folder = install.bundled_folder(example) if example == row["app_id"] else None
    if folder is None:
        return False
    try:
        m = mf.load(folder)
    except mf.ManifestError:
        return False
    return (install.bundled_tree(core, example) not in (None, row["tree"])
            and version_key(m.version) > version_key(row["version"]))


def look(core: Core, app_id: str, *, now: int | None = None) -> dict[str, Any] | None:
    """Look for a newer version of one app now. Returns what was found, or None."""
    now = ids.now_ms() if now is None else now
    row = store.need(core, app_id)
    found = found_store(core)
    found.checked(app_id, now)
    if newer_example(core, row):
        found.found(app_id, now, source="example", example=row["example"])
    elif row.get("source") == "git" and row.get("repo") and row.get("ref"):
        empty = store.staging_root(core) / ids.ulid()
        empty.mkdir(parents=True)
        try:
            commit = install.resolve_ref(core, row["repo"], row["ref"], empty=empty)
        finally:
            store.delete_tree(empty)
        if commit != row.get("commit"):
            found.found(app_id, now, source="git", repo=row["repo"], ref=row["ref"],
                        commit=commit)
        else:
            found.clear(app_id)
    return waiting(core, app_id)


def due(core: Core, app_id: str, now: int) -> bool:
    hours = int(core.settings.get("apps.update_check_hours"))
    if hours <= 0:
        return False
    last = (found_store(core).get(app_id) or {}).get("checked_at")
    return last is None or now - int(last) >= hours * 3600 * 1000


def check_for_updates(core: Core, *, now: int | None = None) -> list[str]:
    """The worker's pass: look at each app that is due, and put in place by itself what
    the owner allowed. Returns the ids of the apps updated."""
    now = ids.now_ms() if now is None else now
    updated = []
    for app_id in sorted(store.installed(core)):
        if not due(core, app_id, now):
            continue
        try:
            found = look(core, app_id, now=now)
        except AppError as error:
            log.info("could not look for an update of %s: %s", app_id, error.detail)
            continue
        if found and auto_allowed(core, app_id) and apply_by_itself(core, app_id):
            updated.append(app_id)
    return updated


def apply_by_itself(core: Core, app_id: str) -> bool:
    """Put the update found in place without the owner, when it asks for nothing new, and
    tell them on the Bench. One that needs consent waits, and the screen says why."""
    from jimmyhq import bench  # noqa: PLC0415

    try:
        plan = plan_found(core, app_id)
    except AppError as error:
        found_store(core).learned(app_id, problem=error.detail)
        return False
    if plan["update"]["needs_consent"] or plan["needs_typed_name"]:
        install.cancel(core, plan["plan"])
        return False
    try:
        done = confirm_update(core, plan["plan"], plan["fingerprint"], actor="system")
    except AppError as error:
        install.cancel(core, plan["plan"])
        found_store(core).learned(app_id, problem=error.detail)
        return False
    bench.file_card(core, filed_by="system", kind=CARD_KIND, source="Apps", lane="done",
                    subject=kinds.subject(app_id),
                    title=f"{done['name']} was updated to {done['version']}",
                    summary="It asked for nothing new, and you let updates like this happen "
                            "by themselves.",
                    body=(f"{done['name']} {plan['update']['from_version']} was replaced by "
                          f"{done['version']}. Its data was copied first. If something is "
                          "wrong, Roll back on its page on the Apps screen goes back, and "
                          "can put that copy of its data back too."))
    return True


def register(core: Core, scheduler: Any) -> None:
    def look_for_updates() -> None:
        from jimmyhq.apps import sync  # noqa: PLC0415

        sync.keep_in_step(core)  # an app's own update setting, declared elsewhere
        check_for_updates(core)

    scheduler.every("apps.updates", JOB_EVERY_S, look_for_updates)


# --- erasing what it wrote ------------------------------------------------------------------


def written_by(event: Any, app_id: str) -> bool:
    """Whether an event is one the app wrote: printed as ``app:<id>``, or of its own
    kinds. The record of what the owner did with it (installed, updated, removed) is not."""
    return (event.actor == f"app:{app_id}" or event.kind.startswith(f"app.{app_id}.")) \
        and event.kind != "tape.erased"


def erase_record(core: Core, app_id: str) -> int:
    """Erase, for good, what the app wrote on the Tape. Returns how many events lost what
    they said. Files in the Vault only they named go too."""
    found: list[str] = []
    seq = 0
    while True:
        page = core.tape.after(seq, limit=ERASE_PAGE)
        if not page:
            break
        found += [e.id for e in page if not e.erased and written_by(e, app_id)]
        seq = page[-1].seq
    if not found:
        return 0
    done = core.tape.erase(found, actor="owner", reason="apps.removed",
                           subject=kinds.subject(app_id))
    with contextlib.suppress(Exception):
        core.tape.finish(core.vault)
    return done.events


# --- over HTTP ------------------------------------------------------------------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpdatePlanBody(_Body):
    folder: str | None = None
    url: str | None = None
    commit: str | None = None
    ref: str | None = None
    example: bool | None = None


class UpdateConfirmBody(_Body):
    plan: str
    fingerprint: str
    typed_name: str | None = None


class RollbackBody(_Body):
    version: str
    restore_data: bool = False


def update_source(body: UpdatePlanBody) -> str:
    """Which one source an update names: "folder", "url", "example", or "found" for the
    update JimmyHQ found."""
    named = [n for n in ("folder", "url", "example") if getattr(body, n)]
    if len(named) > 1 or (named and named[0] != "url" and (body.commit or body.ref)):
        raise AppError(422, (
            "Give at most one of: a folder on this computer, a git address with its commit, "
            "or the example that ships with JimmyHQ. Nothing was changed."))
    if not named:
        return "url" if body.commit or body.ref else "found"
    return named[0]


def build_router(core: Core) -> APIRouter:
    from fastapi import APIRouter, Depends, HTTPException  # noqa: PLC0415

    from jimmyhq.auth import Principal  # noqa: PLC0415
    from jimmyhq.web.deps import owner  # noqa: PLC0415

    router = APIRouter(prefix="/api/apps", tags=["apps"])

    def answer(work: Any) -> dict[str, Any]:
        try:
            return work()
        except AppError as error:
            raise HTTPException(error.status, error.detail) from None

    @router.post("/{app_id}/update/plan")
    def plan_route(app_id: str, body: UpdatePlanBody | None = None,
                   _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        body = body or UpdatePlanBody()

        def work() -> dict[str, Any]:
            store.app_dir(core, app_id)  # an id, or "no such app"
            source = update_source(body)
            if source == "folder":
                return plan_folder(core, app_id, body.folder or "")
            if source == "example":
                return plan_example(core, app_id)
            if source == "url":
                return plan_git(core, app_id, body.url, commit=body.commit, ref=body.ref)
            return plan_found(core, app_id)
        return answer(work)

    @router.post("/{app_id}/update/confirm")
    def confirm_route(app_id: str, body: UpdateConfirmBody,
                      who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        def work() -> dict[str, Any]:
            record = install._read_record(install._plan_dir(core, body.plan))  # noqa: SLF001
            if record is not None and (record.get("update") or {}).get("app_id") != app_id:
                raise AppError(409, "That update is for another app. Nothing was changed.")
            return confirm_update(core, body.plan, body.fingerprint,
                                  typed_name=body.typed_name, actor=who.actor)
        return answer(work)

    @router.post("/{app_id}/update/check")
    def check_route(app_id: str, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return answer(lambda: {"update": look(core, app_id)})

    @router.post("/{app_id}/rollback")
    def rollback_route(app_id: str, body: RollbackBody,
                       who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return answer(lambda: rollback(core, app_id, body.version, restore=body.restore_data,
                                       actor=who.actor))

    return router
