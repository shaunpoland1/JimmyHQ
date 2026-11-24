"""Backups with a running JimmyHQ: making them on a schedule and on a click, testing them,
the stored view the Settings screen reads, and the card on the Bench when they need a look.

- One backup or test at a time per data folder, across processes: ``backup.lock`` in the
  data folder (the worker's schedule and the owner's button never write at once).
- The two folders are independent: each is tried in its own step, so one failing never
  stops or undoes the other.
- Everything is recorded on the Tape (``backup.*``) and read back through ``BackupLens``.
- A standing problem (a folder's newest good backup older than
  ``backup.warn_after_days``, a failed restore test, a date ahead of the clock, or a run
  going on for longer than ``backup.stuck_after_hours``) is one card on the Bench, filed
  by ``system`` and withdrawn by itself once the problem is gone. That is the existing
  way other modules (integrations, app updates) bring something to the owner.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from jimmyhq import __version__
from jimmyhq.backup import snapshot
from jimmyhq.core import Core
from jimmyhq.ids import now_ms
from jimmyhq.lens import Lens
from jimmyhq.tape.registry import Event

log = logging.getLogger("jimmyhq.backup")

WHICH = ("first", "second")
KEYS = {"first": "backup.destination", "second": "backup.second_destination"}
NAMES = {"first": "backup folder", "second": "second backup folder"}
LOCK_FILE = "backup.lock"
CARD_KIND = "backup.problem"
CARD_SOURCE = "Backups"
ACTOR = "system"
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
JOB_EVERY_S = 300
_MAX_REASON = 1000
_MAX_PROBLEM = 500

BUSY = ("A backup or a test is already running. Nothing new was started; the status "
        "updates when it is done.")
OFF = ("Backups are off: no backup folder is set. Choose one under Settings, Backups, "
       "then try again.")


# --- one at a time -------------------------------------------------------------------

_in_process = threading.Lock()
# Where in ``backup.lock`` the holder writes when its run began. Past the one byte that is
# locked, because a Windows lock also stops others reading the bytes it covers.
_STAMP_AT = 16


@dataclass
class Held:
    """The backup lock, held. ``release`` lets it go; safe to call twice."""

    handle: IO[bytes] | None

    def release(self) -> None:
        if self.handle is not None:
            with contextlib.suppress(OSError):
                self.handle.seek(_STAMP_AT)
                self.handle.truncate()  # the run is over: no start time left behind
            with contextlib.suppress(OSError):
                self.handle.close()
            self.handle = None
            _in_process.release()


def try_lock(data: Path, *, stamp: bool = True) -> Held | None:
    """Take the backup lock for this data folder, or None if a backup or test holds it.
    On a file system that cannot lock, the lock within this process still holds. With
    ``stamp``, the time the run begins is written in the lock file, for ``running_since``."""
    if not _in_process.acquire(blocking=False):
        return None
    try:
        fd = os.open(Path(data) / LOCK_FILE, os.O_RDWR | os.O_CREAT
                     | getattr(os, "O_BINARY", 0), 0o600)
        handle = os.fdopen(fd, "r+b")
    except OSError:
        _in_process.release()
        raise
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES, errno.EDEADLK):
            handle.close()
            _in_process.release()
            return None
        log.warning("could not lock %s (%s); one backup at a time is kept within this "
                    "process only", LOCK_FILE, exc)
    if stamp:
        with contextlib.suppress(OSError):
            handle.seek(_STAMP_AT)
            handle.truncate()
            handle.write(f"{now_ms()}\n".encode("ascii"))
            handle.flush()
    return Held(handle)


def running_since(data: Path) -> int | None:
    """When the backup or test running now began (unix ms), or None when none is running
    or its start is not known."""
    if not busy(data):
        return None
    try:
        with open(Path(data) / LOCK_FILE, "rb") as handle:
            handle.seek(_STAMP_AT)
            text = handle.read(32).decode("ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() else None


def busy(data: Path) -> bool:
    """Whether a backup or a test is running for this data folder right now."""
    held = try_lock(data, stamp=False)
    if held is None:
        return True
    held.release()
    return False


@contextlib.contextmanager
def locked(data: Path) -> Iterator[bool]:
    held = try_lock(data)
    try:
        yield held is not None
    finally:
        if held is not None:
            held.release()


# --- where ---------------------------------------------------------------------------


def folder(core: Core, which: str) -> Path | None:
    value = str(core.settings.get(KEYS[which])).strip()
    return Path(os.path.expanduser(value)) if value else None


def source(core: Core) -> str:
    """Which installation a snapshot came from: its random id, kept in the data folder.
    Pruning and the restore test only ever look at this installation's snapshots, never
    another's sharing the folder, even one whose data folder is at the same path."""
    return snapshot.installation_id(core.config.data)


def older_source(core: Core) -> str:
    """What snapshots made before installation ids carry instead: a fingerprint of the
    data folder's path. They are still this installation's to list, test and prune."""
    return snapshot.place_of(core.config.data)


def mine(core: Core, where: Path) -> list[snapshot.Listed]:
    """This installation's snapshots in a folder, newest first."""
    sources = {source(core), older_source(core)}
    return [s for s in snapshot.snapshots(where) if s.source in sources]


def is_on(core: Core) -> bool:
    return any(folder(core, which) is not None for which in WHICH)


# --- the record ----------------------------------------------------------------------


def _empty_place() -> dict[str, Any]:
    return {"made": None, "failed": None, "tested": None}


class BackupLens(Lens):
    """For each folder (by its fingerprint): the last backup made, the last failure and the
    last restore test. And the last card this module filed on the Bench."""

    name = "backup"
    version = 2  # 2: each entry keeps its event's seq, which orders them whatever the clock
    kinds = ("backup.made", "backup.failed", "backup.test_passed", "backup.test_failed",
             "bench.filed")

    def initial(self) -> dict[str, Any]:
        return {"places": {}, "card": None}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        p = event.payload
        if event.kind == "bench.filed":
            if p.get("kind") == CARD_KIND:
                state["card"] = {"id": p["card_id"], "ts": event.ts, "summary": p["summary"]}
            return state
        place = state["places"].setdefault(p["place"], _empty_place())
        if event.kind == "backup.made":
            place["made"] = {"ts": event.ts, "seq": event.seq, "name": p["name"],
                             "files": p["files"], "bytes": p["bytes"],
                             "duration_ms": p["duration_ms"], "pruned": p["pruned"]}
        elif event.kind == "backup.failed":
            place["failed"] = {"ts": event.ts, "seq": event.seq, "reason": p["reason"]}
        else:
            passed = event.kind == "backup.test_passed"
            place["tested"] = {"ts": event.ts, "seq": event.seq, "name": p.get("name"),
                               "passed": passed, "problems": [] if passed else p["problems"],
                               "duration_ms": p["duration_ms"]}
        return state


_LENS = BackupLens()


def record(core: Core) -> dict[str, Any]:
    return core.lenses.read(_LENS)


def _place_state(state: dict[str, Any], where: Path) -> dict[str, Any]:
    return state["places"].get(snapshot.place_of(where)) or _empty_place()


# --- making and testing ----------------------------------------------------------------


@dataclass
class Outcome:
    which: str
    ok: bool
    sentence: str  # what to tell the owner in the terminal or on the screen


def _clip(text: str, most: int) -> str:
    return text if len(text) <= most else text[: most - 1] + "…"


def _same_folder(first: Path | None, second: Path) -> bool:
    if first is None:
        return False
    return snapshot.place_of(first) == snapshot.place_of(second) or \
        snapshot.same_or_inside(second, first) and snapshot.same_or_inside(first, second)


def back_up_one(core: Core, which: str, where: Path, *, actor: str,
                now: int | None = None) -> Outcome:
    """Make one backup in one folder and record it. Never raises for anything the folder
    or the data does: that is a ``backup.failed`` with a sentence."""
    started = time.monotonic()
    place = snapshot.place_of(where)
    try:
        if which == "second" and _same_folder(folder(core, "first"), where):
            raise snapshot.BackupError("The second backup folder is the same as the first, so "
                                       "it would be no second copy. Nothing was written there. "
                                       "Choose a different folder, ideally on another drive.")
        made = snapshot.make(core.config.data, where, now_ms=now or now_ms(),
                             version=__version__, source=source(core),
                             include_rebuildable=bool(core.settings.get(
                                 "backup.include_rebuildable")))
    except snapshot.BackupError as exc:
        reason = str(exc)
    except Exception as exc:  # noqa: BLE001 - one folder's trouble must not stop the other
        log.exception("a backup in the %s failed", NAMES[which])
        reason = (f"Something unexpected stopped the backup ({type(exc).__name__}). Nothing "
                  "older was removed. The details are in JimmyHQ's log.")
    else:
        pruned = snapshot.prune(where, keep=int(core.settings.get("backup.keep")),
                                source=source(core), also=[older_source(core)],
                                protect=made.name)
        core.tape.print("backup.made", {
            "destination": which, "place": place, "name": made.name, "files": made.files,
            "bytes": made.bytes, "stores": made.stores,
            "left_out": sum(made.left_out.values()), "pruned": len(pruned),
            "duration_ms": made.duration_ms}, actor=actor)
        return Outcome(which, True, f"Made {made.name} in the {NAMES[which]}: {made.files} "
                                    f"files, {made.bytes} bytes."
                                    + (f" Removed {len(pruned)} older." if pruned else ""))
    core.tape.print("backup.failed", {
        "destination": which, "place": place, "reason": _clip(reason, _MAX_REASON),
        "duration_ms": int((time.monotonic() - started) * 1000)}, actor=actor)
    return Outcome(which, False, f"The {NAMES[which]} got no backup: {reason}")


def test_one(core: Core, which: str, where: Path, *, actor: str,
             asked: bool = True) -> Outcome | None:
    """The restore test on the newest backup of this data folder in one folder. When there
    is none, an owner's request is answered with a failed test; the schedule skips."""
    started = time.monotonic()
    place = snapshot.place_of(where)
    found_mine = mine(core, where)
    if not found_mine:
        if not asked:
            return None
        sentence = ("There is no backup of this data folder in the folder to test. Make one "
                    "with Back up now first.")
        core.tape.print("backup.test_failed", {
            "destination": which, "place": place, "name": None, "problems": [sentence],
            "duration_ms": int((time.monotonic() - started) * 1000)}, actor=actor)
        return Outcome(which, False, f"The {NAMES[which]}: {sentence}")
    newest = found_mine[0]
    try:
        found = snapshot.verify(newest.path, full=True)
        problems = found.short()
    except Exception as exc:  # noqa: BLE001 - recorded as a failed test, never a crash
        log.exception("the restore test in the %s could not run", NAMES[which])
        found, problems = None, [f"The test could not run ({type(exc).__name__}). The "
                                 "details are in JimmyHQ's log."]
    took = int((time.monotonic() - started) * 1000)
    if found is not None and found.passed:
        core.tape.print("backup.test_passed", {
            "destination": which, "place": place, "name": newest.name, "files": found.files,
            "bytes": found.bytes, "duration_ms": took}, actor=actor)
        return Outcome(which, True, f"{newest.name} in the {NAMES[which]} opens: "
                                    f"{found.files} files checked.")
    core.tape.print("backup.test_failed", {
        "destination": which, "place": place, "name": newest.name,
        "problems": [_clip(p, _MAX_PROBLEM) for p in problems[:10]],
        "duration_ms": took}, actor=actor)
    return Outcome(which, False, f"{newest.name} in the {NAMES[which]} did not pass: "
                                 + " ".join(problems))


def work(core: Core, *, backups: list[str], tests: list[str], actor: str,
         asked: bool = True, now: int | None = None) -> list[Outcome]:
    """Make the backups and run the tests named, each folder in its own step. The caller
    holds the lock."""
    out: list[Outcome] = []
    for which in backups:
        where = folder(core, which)
        if where is not None:
            out.append(back_up_one(core, which, where, actor=actor, now=now))
    for which in tests:
        where = folder(core, which)
        if where is not None:
            found = test_one(core, which, where, actor=actor, asked=asked)
            if found is not None:
                out.append(found)
    keep_card(core, now=now, running=False)  # this run is the one running: it is done
    return out


class Busy(Exception):
    pass


def run_now(core: Core, *, actor: str, test: bool = False) -> list[Outcome]:
    """``Back up now`` (or ``Test the newest backup``) in this thread. Raises ``Busy``."""
    with locked(core.config.data) as got:
        if not got:
            raise Busy(BUSY)
        targets = [w for w in WHICH if folder(core, w) is not None]
        if test:
            return work(core, backups=[], tests=targets, actor=actor)
        return work(core, backups=targets, tests=[], actor=actor)


def start(core: Core, *, actor: str, test: bool = False,
          done: Callable[[list[Outcome]], None] | None = None) -> threading.Thread:
    """The same in a thread of its own, so a large backup never holds up a request or the
    worker's other jobs. Raises ``Busy`` at once if one is running."""
    held = try_lock(core.config.data)
    if held is None:
        raise Busy(BUSY)

    def run() -> None:
        try:
            targets = [w for w in WHICH if folder(core, w) is not None]
            outcomes = work(core, backups=[] if test else targets,
                            tests=targets if test else [], actor=actor)
            if done is not None:
                done(outcomes)
        except Exception:  # noqa: BLE001 - a thread's failure is logged, never lost
            log.exception("a backup run failed")
        finally:
            held.release()

    thread = threading.Thread(target=run, name="backup", daemon=True)
    thread.start()
    return thread


# --- the schedule ----------------------------------------------------------------------


def ahead(entry: dict[str, Any] | None, now: int) -> bool:
    """Whether a recorded backup, failure or test is dated later than the clock says it
    is now, by more than clocks drift: the clock was wrong then, or is wrong now. Such a
    date cannot say how long ago something was, so it makes the next one due at once."""
    return entry is not None and entry["ts"] - now > snapshot.AHEAD_MS


def _later(one: dict[str, Any] | None, other: dict[str, Any] | None) -> bool:
    """Whether ``one`` was recorded after ``other``: by the order on the Tape, which a
    wrong clock cannot change."""
    return one is not None and (other is None or one["seq"] > other["seq"])


def due(core: Core, now: int | None = None) -> tuple[list[str], list[str]]:
    """Which folders want a backup now, and which want their restore test."""
    now = now_ms() if now is None else now
    state = record(core)
    every = int(core.settings.get("backup.every_hours")) * HOUR_MS
    again = int(core.settings.get("backup.retry_hours")) * HOUR_MS
    test_every = int(core.settings.get("backup.test_every_days")) * DAY_MS
    backups, tests = [], []
    for which in WHICH:
        where = folder(core, which)
        if where is None:
            continue
        st = _place_state(state, where)
        made, failed, tested = st["made"], st["failed"], st["tested"]
        last = failed if _later(failed, made) else made
        wait = min(again, every) if last is failed else every
        if last is None or ahead(last, now) or now - last["ts"] >= wait:
            backups.append(which)
        if made is not None:
            since = tested if tested else made
            if ahead(since, now) or now - since["ts"] >= test_every:
                tests.append(which)
    return backups, tests


def job(core: Core) -> threading.Thread | None:
    """The worker's pass: start what is due in a thread of its own, and keep the Bench card
    true. Returns the thread, or None when nothing was started."""
    backups, tests = due(core)
    if not backups and not tests:
        keep_card(core)
        return None
    held = try_lock(core.config.data)
    if held is None:
        # The owner's button is running one, or one is stuck: the card says so if it has
        # run for too long, and the next pass looks again.
        keep_card(core)
        return None

    def run() -> None:
        try:
            work(core, backups=backups, tests=tests, actor=ACTOR, asked=False)
        except Exception:  # noqa: BLE001
            log.exception("a scheduled backup failed")
        finally:
            held.release()

    thread = threading.Thread(target=run, name="backup", daemon=True)
    thread.start()
    return thread


# --- what the owner sees ---------------------------------------------------------------


def problem_of(st: dict[str, Any], which: str, *, now: int, warn_days: int) -> str | None:
    """The standing problem with one folder, as a sentence, or None."""
    made, failed, tested = st["made"], st["failed"], st["tested"]
    name = NAMES[which]
    if tested and not tested["passed"] and not _later(made, tested):
        first = tested["problems"][0] if tested["problems"] else ""
        return f"The newest backup in the {name} did not pass its restore test. {first}".strip()
    newest = failed if _later(failed, made) else made
    if ahead(newest, now) or ahead(tested, now):
        return (f"The last backup or test in the {name} is dated later than this computer's "
                "clock says it is now, so the clock was wrong then or is wrong now. Check "
                "the computer's date and time. A new backup and test are made at the next "
                "check, and this goes away once they are recorded.")
    if made is None:
        if failed is not None:
            return f"No backup has been made in the {name} yet. The last try said: " \
                   f"{failed['reason']}"
        return None
    age = now - made["ts"]
    if age > warn_days * DAY_MS:
        days = age // DAY_MS
        line = f"The newest backup in the {name} is {days} day{'s' if days != 1 else ''} old."
        if _later(failed, made):
            line += f" The last try said: {failed['reason']}"
        return line
    return None


def destinations(core: Core, now: int | None = None) -> list[dict[str, Any]]:
    """Each folder's last backup, failure and test, and its standing problem. A read of
    settings and of the stored view only."""
    now = now_ms() if now is None else now
    state = record(core)
    warn_days = int(core.settings.get("backup.warn_after_days"))
    every = int(core.settings.get("backup.every_hours")) * HOUR_MS
    out = []
    for which in WHICH:
        where = folder(core, which)
        st = _place_state(state, where) if where is not None else _empty_place()
        out.append({
            "which": which,
            "set": where is not None,
            "last_backup": st["made"],
            "last_failure": st["failed"],
            "last_test": st["tested"],
            "problem": problem_of(st, which, now=now, warn_days=warn_days)
                       if where is not None else None,
            "next_ms": (now if ahead(st["made"], now) else st["made"]["ts"] + every)
                       if where is not None and st["made"] else None,
        })
    return out


def stuck(core: Core, now: int) -> str | None:
    """The problem with a backup or test that has been running for too long, or None."""
    since = running_since(core.config.data)
    limit = int(core.settings.get("backup.stuck_after_hours"))
    if since is None or now - since <= limit * HOUR_MS:
        return None
    hours = (now - since) // HOUR_MS
    return (f"A backup or test has been running for {hours} hours, longer than the "
            f"{limit} hours set under Settings, Backups. It may be stuck, for example on a "
            "drive that stopped answering. Restarting JimmyHQ stops it. Nothing is lost: "
            "a backup that was not finished is never listed, and is cleared later.")


def status(core: Core, now: int | None = None) -> dict[str, Any]:
    """What the Settings screen shows. Nothing on disk is looked at except whether a
    backup is running, and since when."""
    now = now_ms() if now is None else now
    out = destinations(core, now)
    problems = [p for p in [stuck(core, now)] if p] + [d["problem"] for d in out if d["problem"]]
    since = running_since(core.config.data)
    return {
        "on": is_on(core),
        "running": busy(core.config.data),
        "running_since": since,
        "destinations": out,
        "problem": problems[0] if problems else None,
        "warn_after_days": int(core.settings.get("backup.warn_after_days")),
        "now": now,
    }


def problems_now(core: Core, now: int | None = None, *, running: bool = True) -> list[str]:
    """Every standing problem, as sentences. ``running`` includes a run that has gone on
    for too long; the run itself leaves that out when it is done."""
    now = now_ms() if now is None else now
    found = [stuck(core, now)] if running and is_on(core) else []
    return [p for p in found if p] + [d["problem"] for d in destinations(core, now)
                                      if d["problem"]]


def _open_cards(core: Core) -> list[dict[str, Any]]:
    from jimmyhq import bench  # noqa: PLC0415 - only needed here

    lanes = bench.board(core)["lanes"]
    return [c for cards in lanes.values() for c in cards
            if c["kind"] == CARD_KIND and c["filed_by"] == ACTOR]


def keep_card(core: Core, now: int | None = None, *, running: bool = True) -> str | None:
    """File, revise or withdraw the one Bench card about backups, so it says what is true
    now. Returns the open card's id, or None."""
    from jimmyhq import bench  # noqa: PLC0415
    from jimmyhq.bench.cards import RESERVED_ACTIONS  # noqa: PLC0415

    now = now_ms() if now is None else now
    problems = problems_now(core, now, running=running)
    if not problems and record(core)["card"] is None:
        return None  # never filed one, so there is none to take back
    try:
        cards = _open_cards(core)
        if not problems:
            for card in cards:
                bench.withdraw_card(
                    core, card["id"], by=ACTOR,
                    note=("Backups are working again." if is_on(core)
                          else "Backups are switched off, so there is nothing to look at."))
            return None
        summary = _clip(problems[0], 300)
        body = ("\n\n".join(problems) + "\n\nOpen Settings, Backups to see each folder's last "
                "backup and test. Once the cause is fixed, press Back up now there; this card "
                "goes away by itself when backups work again.")
        if cards:
            card = cards[0]
            if card["summary"] != summary:
                bench.update_card(core, card["id"], by=ACTOR, summary=summary, body=body)
            return card["id"]
        last = record(core)["card"]
        warn_ms = int(core.settings.get("backup.warn_after_days")) * DAY_MS
        if last is not None and last["summary"] == summary and 0 <= now - last["ts"] < warn_ms:
            return None  # the owner already saw this one and closed it; ask again later
        return bench.file_card(
            core, filed_by=ACTOR, kind=CARD_KIND, source=CARD_SOURCE,
            title="Backups need a look", summary=summary, body=body,
            actions=[dict(RESERVED_ACTIONS["ok"])])
    except bench.BenchError:
        log.exception("the card about backups could not be kept up to date")
        return None
