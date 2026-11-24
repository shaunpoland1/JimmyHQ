"""Checking the packages: the ``sync`` job, and Refresh now.

One check does, in this order, for the packages still being checked:

1. **Register** each number the tracker has not registered yet, in batches, once. A number
   is registered with one tracker one time (``registered_with``), and the quota that cost
   is written down (``charged_ms``); it is never registered again unless the owner
   changes tracker. A number the service refused is not offered again by a scheduled
   check, only by Refresh now or after the owner changes it. A number the service no
   longer knows is held (``hold``): registering it again uses quota again, so only the
   owner's "Register again" on that package does it.
2. **Read** each registered package, in batches, and apply what comes back (``moves``):
   with the owner's carrier, or none for "detect it" (``read_item``). A package with no
   news for ``quiet_days`` stops being checked (``stop_quiet``).
3. **Read the quota** after registering something, or when the owner pressed Refresh now.

Every request is answered once and sorted into yes, no or no clear answer (the tracker's
job); nothing here retries inside a check. A request with no clear answer ends the check
with nothing changed for what it held; the next try is due sooner than the usual
interval (``schedule.retry_wait_ms``). The schedule is in ``schedule``: ``tick`` is the
job, run every few minutes, and does nothing but look at the clock until a check is due.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from packages import moves, pretend, schedule, tell
from packages.context import Ctx
from packages.trackers import NO, YES, Item, Reply

log = logging.getLogger("packages")

MAX_PAUSE_S = 5.0


@dataclass
class Outcome:
    ok: bool
    said: str
    checked: int = 0
    changed: int = 0
    registered: int = 0


# --- the rules, one function each --------------------------------------------------------


def is_registered(pkg: dict[str, Any], tracker: Any) -> bool:
    """Whether the tracker already knows this number. A tracker that registers nothing
    knows them all."""
    return (not tracker.registers) or (
        pkg["registered_ms"] is not None and pkg["registered_with"] == tracker.id)


def active(ctx: Ctx, only: int | None = None) -> list[dict[str, Any]]:
    """The packages still being checked: not archived."""
    rows = ctx.store.all("SELECT * FROM package WHERE archived_ms IS NULL ORDER BY id")
    return [r for r in rows if only is None or r["id"] == only]


def archive_old(ctx: Ctx) -> int:
    """Stop checking a package delivered more than ``delivered_days`` ago. It stays in
    Delivered. Returns how many stopped."""
    cutoff = ctx.now() - int(ctx.cfg().delivered_days) * schedule.DAY
    old = ctx.store.all("SELECT id FROM package WHERE delivered_ms IS NOT NULL AND "
                        "archived_ms IS NULL AND delivered_ms <= ?", (cutoff,))
    for row in old:
        ctx.store.set(row["id"], archived_ms=ctx.now())
    return len(old)


def stop_quiet(ctx: Ctx) -> int:
    """Rule ``a_package_with_no_news_for_quiet_days_is_not_checked``: 17TRACK stops tracking
    a number its carrier has said nothing new about for 30 days, so a registered package
    not yet delivered with no news for ``quiet_days`` (counted from its latest event, or
    from when it was registered) stops being checked, and its row says so. 0 means
    never. Returns how many stopped."""
    days = int(ctx.cfg().quiet_days)
    if days <= 0:
        return 0
    cutoff = ctx.now() - days * schedule.DAY
    count = 0
    for row in ctx.store.all("SELECT * FROM package WHERE archived_ms IS NULL AND "
                             "registered_ms IS NOT NULL AND status != 'delivered'"):
        since = max(row["event_ms"] or 0, row["registered_ms"] or row["created"])
        if since <= cutoff:
            ctx.store.set(row["id"], archived_ms=ctx.now(), note=(
                f"Packages stopped checking it: there has been no news of it for {days} "
                "days, and 17TRACK stops tracking such a number itself. If it has come, "
                "mark it delivered; if not, ask the sender, or remove it."))
            count += 1
    return count


# --- what Packages holds back -------------------------------------------------------------


def is_held(ctx: Ctx, package_id: int) -> bool:
    """A number the tracker no longer knows, kept from being registered again: that would
    use another place of quota, so only the owner's own press on it does."""
    return package_id in set(ctx.store.get_meta("register_held", []) or [])


def hold(ctx: Ctx, package_id: int, held: bool) -> None:
    ids = set(ctx.store.get_meta("register_held", []) or [])
    ids = ids | {package_id} if held else ids - {package_id}
    ctx.store.set_meta(register_held=sorted(ids))


def carrier_unconfirmed(ctx: Ctx) -> dict[int, str]:
    """Packages whose carrier change got no clear answer: id -> the carrier asked for."""
    return {int(k): str(v) for k, v in (ctx.store.get_meta("carrier_unconfirmed", {})
                                        or {}).items()}


def mark_carrier_unconfirmed(ctx: Ctx, package_id: int, code: str | None) -> None:
    """Note a carrier change that got no clear answer (``code``), or forget it (None)."""
    found = {str(k): v for k, v in carrier_unconfirmed(ctx).items()}
    if code is None:
        found.pop(str(package_id), None)
    else:
        found[str(package_id)] = code
    ctx.store.set_meta(carrier_unconfirmed=found)


def read_item(ctx: Ctx, pkg: dict[str, Any], unsure: dict[int, str]) -> Item:
    """Rule ``a_number_is_read_with_the_owners_carrier_only``: the carrier the owner chose
    is sent; with "detect it", or while a carrier change is not confirmed, none is, so a
    carrier 17TRACK detected again, or changed, never makes a read fail. The carrier it
    detected before is read first when the answer holds several."""
    carrier = "" if pkg["id"] in unsure else pkg["carrier"]
    return Item(pkg["id"], pkg["number"], carrier, prefer=pkg["carrier_found"])


def batches(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[i:i + size] for i in range(0, len(rows), size)] or []


def batch_size(ctx: Ctx, tracker: Any) -> int:
    """Rule ``batch_size``: the owner's number, never past what the tracker takes."""
    return max(1, min(int(ctx.cfg().batch_size), int(tracker.batch_most)))


def schedule_state(ctx: Ctx) -> dict[str, Any]:
    """What the schedule knows now: when the last good check was, how many failed since,
    and when the next is due (0 means as soon as the job looks)."""
    store, cfg = ctx.store, ctx.cfg()
    clock, note = schedule.read_clock(cfg.daily_time)
    last_ok, last_try = store.get_meta("last_ok"), store.get_meta("last_try")
    fails = int(store.get_meta("fails", 0))
    due = schedule.due_ms(
        last_ok=last_ok, last_try=last_try, fails=fails, interval_hours=int(cfg.interval_hours),
        clock=clock, retry_minutes=int(cfg.retry_minutes),
        longest_minutes=int(cfg.retry_longest_minutes), zone=ctx.zone())
    return {"last_ok_ms": last_ok, "last_try_ms": last_try, "fails": fails, "next_ms": due,
            "said": store.get_meta("said", ""), "clock_note": note,
            "daily": f"{clock[0]:02d}:{clock[1]:02d}" if clock else ""}


def refresh_gate(ctx: Ctx) -> str | None:
    """Rule ``refresh_gap``: None when Refresh now may go; else the sentence that says why
    not and for how long. Only a tracker that asks an outside service keeps the gap."""
    tracker = ctx.tracker()
    if not tracker.outside:
        return None
    sent = ctx.store.get_meta("last_sent")
    gap = int(ctx.cfg().refresh_gap_minutes)
    wait = schedule.refresh_wait_ms(ctx.now(), sent, gap)
    if wait <= 0:
        return None
    since = ctx.now() - sent
    ago = "just now" if since < schedule.MINUTE else f"{schedule.span(since)} ago"
    return (f"Packages asked {tracker.label} {ago}, and Refresh now waits at least {gap} "
            f"minutes between requests so a button cannot use up your quota. Try again in "
            f"{schedule.span(wait)}. Nothing was checked.")


# --- one check ---------------------------------------------------------------------------


class Run:
    """One check in progress: the requests it made."""

    def __init__(self, ctx: Ctx, tracker: Any):
        self.ctx, self.tracker, self.requests = ctx, tracker, 0
        self.out = Outcome(True, "")

    def ask(self, fn: Any, *args: Any) -> Reply:
        """One request. A pause comes before every request but the first, for the
        service's rate limit."""
        if self.requests:
            self.ctx.sleep(min(MAX_PAUSE_S, int(self.ctx.cfg().pause_ms) / 1000))
        self.requests += 1
        return fn(*args)

    def failed(self, said: str) -> Outcome:
        self.out.ok = False
        self.out.said = said
        return self.out


def _registered(ctx: Ctx, pkg: dict[str, Any], found: Any, tracker: Any) -> bool:
    store, now = ctx.store, ctx.now()
    if found.kind == YES:
        store.set(pkg["id"], registered_with=tracker.id, registered_ms=now,
                  charged_ms=now if found.charged else pkg["charged_ms"],
                  carrier_found=found.carrier or pkg["carrier_found"],
                  carrier_guessed=int(found.guessed), refused="", note="")
        return True
    if found.kind == NO:
        store.set(pkg["id"], refused=found.said, note=found.said)
    else:
        store.set(pkg["id"], note=found.said)
    return False


def _read(ctx: Ctx, pkg: dict[str, Any], found: Any, run: Run) -> None:
    store, now = ctx.store, ctx.now()
    if found.kind == YES:
        note = ""
        asked = carrier_unconfirmed(ctx).get(pkg["id"])
        if asked is not None:  # a read without a carrier says which one 17TRACK has
            mark_carrier_unconfirmed(ctx, pkg["id"], None)
            if found.carrier == asked:
                store.set(pkg["id"], carrier=asked)
            elif found.carrier:
                note = (f"The change to carrier {asked} did not happen: 17TRACK tracks it as "
                        f"carrier {found.carrier}. Change it again if you still want to.")
        store.set(pkg["id"], checked_ms=now, note=note,
                  carrier_found=found.carrier or pkg["carrier_found"])
        run.out.checked += 1
        if found.reading is not None:
            fresh = store.package(pkg["id"]) or pkg
            if moves.apply(ctx, fresh, found.reading.status, event=found.reading.event,
                           event_ms=found.reading.event_ms):
                run.out.changed += 1
    elif found.gone:
        hold(ctx, pkg["id"], True)
        store.set(pkg["id"], registered_ms=None, registered_with="", refused=found.said,
                  note=f"{found.said} Registering it again uses another place in your "
                       "quota, so Packages does not do it by itself, not even on Refresh "
                       "now. Press \"Register again (uses quota)\" on it if you want that.")
    else:
        store.set(pkg["id"], note=found.said)


def _quota(ctx: Ctx, run: Run) -> None:
    found = run.ask(run.tracker.quota)
    if found.kind == YES and found.quota is not None:
        ctx.store.set_meta(quota={"total": found.quota.total, "used": found.quota.used,
                                  "left": found.quota.left, "at": ctx.now()})
    else:
        log.info("could not read the quota: %s", found.said)


def _check(ctx: Ctx, by: str, only: int | None, run: Run) -> Outcome:
    tracker, size = run.tracker, batch_size(ctx, run.tracker)
    rows = active(ctx, only)
    if not rows:
        run.out.said = "There is nothing to check yet."
        return run.out
    again = by in ("owner", "add")  # a refused number is offered again only by the owner
    if tracker.registers:
        held = set(ctx.store.get_meta("register_held", []) or [])
        todo = [r for r in rows if not is_registered(r, tracker) and r["id"] not in held
                and (again or not r["refused"])]
        for group in batches(todo, size):
            reply = run.ask(tracker.register,
                            [Item(r["id"], r["number"], r["carrier"]) for r in group])
            if reply.kind != YES:
                return run.failed(f"{reply.said} The rest was not checked.")
            by_id = {r["id"]: r for r in group}
            for found in reply.items:
                run.out.registered += _registered(ctx, by_id[found.id], found, tracker)
    rows = [r for r in active(ctx, only) if is_registered(r, tracker)]
    unsure = carrier_unconfirmed(ctx)
    for group in batches(rows, size):
        reply = run.ask(tracker.read, [read_item(ctx, r, unsure) for r in group])
        if reply.kind != YES:
            return run.failed(f"{reply.said} The rest was not checked.")
        by_id = {r["id"]: r for r in group}
        for found in reply.items:
            _read(ctx, by_id[found.id], found, run)
    if by != "add" and (run.out.registered or by == "owner"):
        _quota(ctx, run)
    n, c = run.out.checked, run.out.changed
    run.out.said = (f"Checked {n} package{'s' if n != 1 else ''}; "
                    + (f"{c} changed." if c else "nothing changed."))
    held = ctx.store.all("SELECT 1 FROM package WHERE archived_ms IS NULL AND refused != ''")
    run.out.said = (f"Checked {n} package{'s' if n != 1 else ''}; "
                    f"{c} changed." if c else
                    f"Checked {n} package{'s' if n != 1 else ''}; nothing changed.")
    if held:
        run.out.said += (f" {len(held)} could not be registered with {tracker.label}: "
                         "the reason is on each one.")
    return run.out


def finish(ctx: Ctx, by: str, run: Run, *, sent: bool) -> Outcome:
    """Write down how a check went, so the schedule knows when to look again."""
    out, store, now = run.out, ctx.store, ctx.now()
    if by in ("schedule", "owner"):
        if sent:
            store.set_meta(last_sent=now)
        cfg = ctx.cfg()
        if out.ok:
            store.set_meta(last_ok=now, last_try=now, fails=0, said=out.said)
        else:
            fails = int(store.get_meta("fails", 0)) + 1
            wait = schedule.retry_wait_ms(fails, int(cfg.retry_minutes),
                                          int(cfg.retry_longest_minutes))
            lead = "" if out.changed or out.registered else "Nothing was changed. "
            out.said = (f"{lead}{out.said} Packages will try again in "
                        f"{schedule.span(wait)}.").replace("  ", " ")
            store.set_meta(last_try=now, fails=fails, said=out.said)
    tell.retry(ctx)
    ctx.emit()
    return out


def run(ctx: Ctx, *, by: str, only: int | None = None) -> Outcome:
    """One check. ``by`` is ``schedule``, ``owner`` (Refresh now) or ``add`` (one new
    package, which leaves the schedule alone)."""
    if not ctx.busy.acquire(blocking=False):
        return Outcome(False, "A check is already running, so this one did not start. "
                              "Nothing was changed.")
    try:
        tracker = ctx.tracker()
        current = Run(ctx, tracker)
        if not tracker.outside:
            current.out.said = (f"{tracker.label.split(' (')[0]} looks nothing up, so there "
                                "was nothing to check. Set a package's status with the "
                                "buttons on it.")
            if pretend.happens(ctx):
                moved = pretend.progress(ctx)
                current.out.said += (f" Practice moved {moved} package{'s' if moved != 1 else ''}"
                                     " a step along by itself." if moved else
                                     " No package was due to move along yet.")
            return finish(ctx, "none", current, sent=False)
        problem = tracker.key_problem()
        if problem:
            current.failed(problem)
            return finish(ctx, by, current, sent=False)
        _check(ctx, by, only, current)
        return finish(ctx, by, current, sent=current.requests > 0)
    finally:
        ctx.busy.release()


def refresh(ctx: Ctx) -> Outcome:
    """Refresh now: a check at once, unless the minimum gap has not passed."""
    gate = refresh_gate(ctx)
    if gate:
        raise ValueError(gate)
    return run(ctx, by="owner")


def tick(ctx: Ctx) -> str:
    """The ``sync`` job: stop checking old deliveries, file cards that were waiting, and
    make a check when one is due. Returns a line for the log."""
    archived = archive_old(ctx)
    tell.retry(ctx)
    if not ctx.tracker().outside:
        said = "The tracking service looks nothing up, so there is nothing to check."
        moved = pretend.progress(ctx) if pretend.happens(ctx) else 0
        return said + (f" Practice moved {moved} package{'s' if moved != 1 else ''} along."
                       if moved else "")
    archived += stop_quiet(ctx)
    state = schedule_state(ctx)
    now = ctx.now()
    if now < state["next_ms"]:
        return f"Next check in {schedule.span(state['next_ms'] - now)}."
    out = run(ctx, by="schedule")
    return out.said + (f" Stopped checking {archived} delivered." if archived else "")
