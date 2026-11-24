"""``jimmyhq backup``: list, check, make and restore backups from the terminal.

``restore`` is here and nowhere else. It refuses while JimmyHQ is running, checks the
backup in full first, refuses one written by a newer JimmyHQ, asks the owner to type a word
(or ``--yes``), and never deletes the data that is there: that folder is moved aside to a
sibling with the time in its name, and the command says where.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from jimmyhq.backup import snapshot

if TYPE_CHECKING:
    from jimmyhq.config import Config

CONFIRM_WORD = "restore"
REFUSED = 1


def add_parser(sub: Any) -> None:
    parser = sub.add_parser("backup", help="list, check, make and restore backups")
    parser.set_defaults(backup_parser=parser)
    commands = parser.add_subparsers(dest="backup_command", metavar="command")
    listing = commands.add_parser("list", help="show the backups in a folder")
    listing.add_argument("folder", nargs="?",
                         help="the folder to look in (default: the backup folders set)")
    verify = commands.add_parser("verify", help="run the restore test on one backup")
    verify.add_argument("snapshot", help="the backup's folder, jimmyhq-backup-...")
    commands.add_parser("now", help="make a backup in each backup folder set, now")
    restore = commands.add_parser(
        "restore", help="put a backup in place of the data folder (JimmyHQ must be stopped)",
        description="Put a backup in place of the data folder. JimmyHQ must be stopped. The "
                    "backup is checked in full first. The data folder as it is now is moved "
                    "aside, never deleted.")
    restore.add_argument("snapshot", help="the backup's folder, jimmyhq-backup-...")
    restore.add_argument("--yes", action="store_true",
                         help=f"restore without asking you to type {CONFIRM_WORD!r}")


def _when(ms: int) -> str:
    """For the terminal: the person's own clock. A time the system cannot place on that
    clock (a damaged manifest; on Windows, anything within a day of 1970) is shown in UTC,
    and one that is no time at all is said to be unknown: a listing never fails on it."""
    try:
        moment = datetime.fromtimestamp(ms / 1000, UTC)
    except (OverflowError, OSError, ValueError, TypeError):
        return "time unknown"
    try:
        return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except (OverflowError, OSError, ValueError):
        return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def _size(count: int) -> str:
    step = 1024.0
    value = float(count)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < step or unit == "GB":
            return f"{int(value)} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= step
    return f"{count} bytes"


def list_main(folders: list[Path], sources: set[str] | None, out: TextIO) -> int:
    any_found = False
    for where in folders:
        found = snapshot.snapshots(where)
        print(f"{where}:", file=out)
        if not found:
            print("  No backups here.", file=out)
            continue
        any_found = True
        for item in found:
            theirs = "" if sources is None or item.source in sources else \
                "  (from another installation)"
            later = "  (dated later than this computer's clock: listed last)" \
                if item.ahead else ""
            print(f"  {item.name}  {_when(item.made_ms)}  JimmyHQ {item.version}  "
                  f"{item.files} files, {_size(item.bytes)}{theirs}{later}", file=out)
    return 0 if any_found or folders else REFUSED


def verify_main(path: Path, out: TextIO, err: TextIO) -> int:
    found = snapshot.verify(path, full=True)
    if found.passed:
        print(f"It opens. {found.files} files ({_size(found.bytes)}) match what was written, "
              "and every database is whole at a version this JimmyHQ knows.", file=out)
        return 0
    print("It does not pass the restore test:", file=err)
    for line in found.lines():
        print(f"  {line}", file=err)
    return REFUSED


def _ask(question: str) -> str | None:
    try:
        return input(question)
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def _stamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def _aside_name(data: Path) -> Path:
    base = data.with_name(f"{data.name}-before-restore-{_stamp()}")
    candidate, n = base, 1
    while os.path.lexists(candidate):
        n += 1
        candidate = base.with_name(f"{base.name}-{n}")
    return candidate


def _copy_tree(source: Path, target: Path) -> None:
    """The snapshot's data, copied (the backup itself is left as it is)."""
    os.mkdir(snapshot.native(target))
    with contextlib.suppress(OSError):
        os.chmod(snapshot.native(target), 0o700)
    pending = [(source, target)]
    while pending:
        here, there = pending.pop()
        for entry in os.scandir(snapshot.native(here)):
            if entry.is_symlink():
                continue  # a snapshot never holds one; one put there later is not followed
            dest = there / entry.name
            if entry.is_dir(follow_symlinks=False):
                os.mkdir(snapshot.native(dest))
                with contextlib.suppress(OSError):
                    os.chmod(snapshot.native(dest), 0o700)
                pending.append((Path(entry.path), dest))
            elif entry.is_file(follow_symlinks=False) and not snapshot.is_clutter(entry.name):
                snapshot.copy_file(Path(entry.path), dest)


def _real_folder(named: Path) -> Path | None:
    """The data folder itself, with any link on the way to it followed, or None when a
    link leads nowhere. A restore moves the real folder, never a link to it: moving the
    link would put the backup where the link was and leave the data where it is."""
    if not os.path.lexists(named):
        return Path(os.path.realpath(named))  # nothing there yet; its parent, followed
    try:
        return Path(os.path.realpath(named, strict=True))
    except (OSError, RuntimeError):
        return None


# Windows cannot rename a folder while a file in it is open, so there the locks are let go
# just before the folders are swapped, and the swap is the two renames back to back.
HOLD_LOCK_THROUGH_SWAP = sys.platform != "win32"


def restore_main(cfg: Config, path: Path, *, yes: bool, out: TextIO, err: TextIO,
                 ask=_ask) -> int:
    from jimmyhq import supervisor  # noqa: PLC0415

    named = Path(os.path.abspath(cfg.data))
    path = Path(os.path.expanduser(str(path)))
    if snapshot.made_at(path.name) is None or snapshot.read_manifest(path) is None:
        print("Nothing was changed. That is not a backup made by JimmyHQ: give the folder "
              "named jimmyhq-backup-... that `jimmyhq backup list` shows.", file=err)
        return REFUSED
    data = _real_folder(named)
    if data is None:
        print(f"Nothing was changed. The data folder {named} is a link that leads to no "
              "folder. Point it at the data folder, or set JIMMYHQ_DATA to the folder "
              "itself, then run this again.", file=err)
        return REFUSED
    if snapshot.same_or_inside(path, data):
        print("Nothing was changed. That backup is inside the data folder it would replace. "
              "Copy it somewhere else first.", file=err)
        return REFUSED
    try:
        lock = supervisor.take_lock(data)
    except supervisor.AlreadyRunning:
        print("Nothing was changed. JimmyHQ is running for this data folder. Stop it first "
              "(Ctrl-C where `jimmyhq run` runs, or switch off the service that starts it at "
              "login), then run this again.", file=err)
        return REFUSED
    locks = [lock]
    try:
        return _restore_locked(cfg, path, data, named, yes=yes, out=out, err=err, ask=ask,
                               locks=locks)
    finally:
        for held in locks:
            with contextlib.suppress(OSError):
                held.close()


def _restore_locked(cfg: Config, path: Path, data: Path, named: Path, *, yes: bool,
                    out: TextIO, err: TextIO, ask, locks: list) -> int:
    from jimmyhq import supervisor  # noqa: PLC0415

    taken = supervisor.port_in_use(cfg.host, cfg.port)
    if taken is not None:
        print(f"Nothing was changed. Something is answering on port {cfg.port}, which may "
              "be JimmyHQ's web process on its own. Stop it, then run this again.",
              file=err)
        return REFUSED
    found = snapshot.verify(path, full=True)
    if found.too_new is not None:
        print(f"Nothing was changed. {found.too_new}", file=err)
        return REFUSED
    if not found.passed:
        print("Nothing was changed. The backup does not pass the restore test:", file=err)
        for line in found.lines():
            print(f"  {line}", file=err)
        return REFUSED
    manifest = found.manifest or {}
    made = int(manifest.get("made_ms") or snapshot.made_at(path.name) or 0)
    aside = _aside_name(data)
    print(f"Backup: {path.name}, made {_when(made)} by JimmyHQ "
          f"{manifest.get('version', '?')}: {found.files} files, {_size(found.bytes)}.",
          file=out)
    through_link = data != Path(os.path.realpath(named.parent)) / named.name
    linked = f" (which {named} links to; the link is kept)" if through_link else ""
    print(f"It will replace the data folder {data}{linked}.", file=out)
    print(f"What is there now will be moved, not deleted, to {aside}.", file=out)
    if not yes:
        answer = ask(f"Type {CONFIRM_WORD} to go ahead: ")
        if answer is None:
            print(f"Nothing was changed. Run this in a terminal and type {CONFIRM_WORD} "
                  "when asked, or add --yes.", file=err)
            return REFUSED
        if answer.strip().lower() != CONFIRM_WORD:
            print("Nothing was changed.", file=out)
            return REFUSED
    incoming = data.with_name(f".{data.name}.restoring-{_stamp()}")
    try:
        _copy_tree(path / snapshot.DATA, incoming)
    except OSError as exc:
        snapshot.remove_tree(incoming)
        print(f"Nothing was changed. The backup could not be copied next to the data "
              f"folder ({snapshot.plain(exc)}). Free some space there, then try "
              "again.", file=err)
        return REFUSED
    copied = _matches(incoming, manifest)
    if copied is not None:
        snapshot.remove_tree(incoming)
        print(f"Nothing was changed. The copy of the backup did not match it ({copied}). "
              "Try again; if it repeats, the disk may be failing.", file=err)
        return REFUSED
    if HOLD_LOCK_THROUGH_SWAP:
        # The copy is locked too, so from the moment it is the data folder no JimmyHQ can
        # start on it until the restore is noted. The lock on the old folder moves aside
        # with it, held, so nothing starts on that one before it is moved either.
        locks.append(supervisor.take_lock(incoming))
    else:
        for held in locks:
            held.close()
        locks.clear()
    snapshot.sync_tree(incoming)  # on the disk before the old folder is moved
    done = _swap(data, aside, incoming, err)
    if done != 0:
        return done
    _record(cfg, path.name, made, str(manifest.get("version", "")), err)
    print(f"Restored {path.name}. The data you had before is in {aside}; nothing in it was "
          "deleted. Start JimmyHQ again with `jimmyhq run`. A full app's own Python "
          "environment is not in a backup: update or install such an app again from the "
          "Apps screen.", file=out)
    return 0


def _stranded(data: Path, aside: Path, incoming: Path, why: str) -> str:
    """What to say when the data folder is not where it belongs: where each folder is,
    and exactly what to do."""
    return (f"The restore stopped part way: {why} Nothing was deleted. Your data as it was "
            f"is in {aside}, and a checked copy of the backup is in {incoming}. Do not start "
            f"JimmyHQ until one of them is back at {data}, or it starts with an empty data "
            f"folder. To go back to how it was, rename {aside} to {data}. To finish the "
            f"restore instead, rename {incoming} to {data}.")


def _swap(data: Path, aside: Path, incoming: Path, err: TextIO) -> int:
    """Move the data folder aside and put the copy in its place. 0 when done."""
    try:
        if os.path.lexists(data):
            snapshot.retry(lambda: os.rename(data, aside))
    except OSError as exc:
        snapshot.remove_tree(incoming)
        print(f"Nothing was changed. The data folder could not be moved aside "
              f"({snapshot.plain(exc)}). Close anything that has a file in it open, then "
              "try again.", file=err)
        return REFUSED
    if os.path.lexists(data):
        # Something made a new data folder in the moment between (JimmyHQ starting makes
        # one when there is none). Putting the copy over it could lose what it holds.
        print(_stranded(data, aside, incoming,
                        f"a new folder appeared at {data} while the restore ran, perhaps "
                        "JimmyHQ starting. Stop JimmyHQ, look inside that new folder, and "
                        "move it out of the way first."), file=err)
        return REFUSED
    try:
        snapshot.retry(lambda: os.rename(incoming, data))
    except OSError as exc:
        reason = snapshot.plain(exc)
        try:
            if os.path.lexists(data):
                raise FileExistsError(data)
            snapshot.retry(lambda: os.rename(aside, data))
            back = os.path.isdir(data) and not os.path.lexists(aside)
        except OSError:
            back = False
        if back:
            snapshot.sync_dir(data.parent)
            print(f"Nothing was changed. The backup could not be put in place ({reason}), "
                  f"so the data folder was put back as it was. A copy of the backup is left "
                  f"at {incoming}.", file=err)
        else:
            print(_stranded(data, aside, incoming,
                            f"the backup could not be put in place ({reason}), and the data "
                            "folder could not be put back either."), file=err)
        return REFUSED
    snapshot.sync_dir(data.parent)
    return 0


def _matches(root: Path, manifest: dict[str, Any]) -> str | None:
    for entry in manifest.get("files", []):
        if snapshot.is_clutter(entry["path"].rsplit("/", 1)[-1]):
            continue  # an older backup may list one; it is never copied back
        target = root.joinpath(*entry["path"].split("/"))
        try:
            size, digest = snapshot.hash_file(target)
        except OSError:
            return f"{entry['path']} is missing"
        if size != entry.get("size") or digest != entry.get("sha256"):
            return f"{entry['path']} differs"
    return None


def _record(cfg: Config, name: str, made: int, version: str, err: TextIO) -> None:
    """Note the restore on the restored Tape. The restore stands if this cannot be done."""
    from jimmyhq.core import open_core  # noqa: PLC0415

    try:
        core = open_core(cfg)
        try:
            core.tape.print("backup.restored", {"name": name, "made_ms": made,
                                                "version": version[:40]}, actor="owner")
        finally:
            core.close()
    except Exception as exc:  # noqa: BLE001 - the restore itself is done
        print(f"The restore is done, but it could not be noted on the Tape ({exc}).", file=err)


def main_for(args: argparse.Namespace, cfg: Config) -> int:
    command = args.backup_command
    if command == "verify":
        return verify_main(Path(os.path.expanduser(args.snapshot)), sys.stdout, sys.stderr)
    if command == "restore":
        return restore_main(cfg, Path(args.snapshot), yes=args.yes, out=sys.stdout,
                            err=sys.stderr)
    from jimmyhq.backup import service  # noqa: PLC0415
    from jimmyhq.core import open_core  # noqa: PLC0415

    if command == "list" and args.folder:
        where = Path(os.path.expanduser(args.folder))
        return list_main([where], {snapshot.installation_id(cfg.data),
                                   snapshot.place_of(cfg.data)}, sys.stdout)
    core = open_core(cfg)
    try:
        folders = [f for w in service.WHICH if (f := service.folder(core, w)) is not None]
        if command == "list":
            if not folders:
                print("No backup folder is set. Name one: `jimmyhq backup list <folder>`, or "
                      "choose one under Settings, Backups.", file=sys.stderr)
                return REFUSED
            return list_main(folders, {service.source(core), service.older_source(core)},
                             sys.stdout)
        if command == "now":
            if not folders:
                print(service.OFF, file=sys.stderr)
                return REFUSED
            try:
                outcomes = service.run_now(core, actor="owner")
            except service.Busy as exc:
                print(exc, file=sys.stderr)
                return REFUSED
            for outcome in outcomes:
                print(outcome.sentence, file=sys.stdout if outcome.ok else sys.stderr)
            return 0 if all(o.ok for o in outcomes) else REFUSED
    finally:
        core.close()
    args.backup_parser.print_help()
    return 2
