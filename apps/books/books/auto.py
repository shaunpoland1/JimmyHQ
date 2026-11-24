"""Reminders that happen by themselves: a direct debit, a salary.

A reminder marked ``auto`` is posted by the scheduled job, never by a page, and only for a
due date that has come: on or after it, never before. It is posted the way pressing Post
would post it (``reminders.post``: a register row, the linked pair of a transfer, or just
moving on for one paid another way), uncleared, for the due date and the amount it holds.
Each due date is posted once, because posting moves ``next_due`` on in the same
transaction and a register row for a due date is unique.

Rule ``an_about_this_much_reminder_is_never_posted_by_itself``: with an amount that is
only about right there is nothing sure to post, so such a reminder (and a file that asks
for both) is refused, and the job leaves a leftover one alone; it raises its card.

Rule ``missed_dates_are_caught_up_to_a_cap_then_stopped``: if Books was not looking for a
while, a reminder's missed dates are posted one by one, at most ``auto_catch_up`` of them
in one go. Past that it is *held*: it stops, says so (``auto_said``), and raises its card
like any bill, until no date on or before today is left for it. A date that cannot be
posted (the account is closed, the date is before its opening) stops it the same way, and
Books tries again on the next look.

What was posted is noted in ``auto_post`` for ``due_days`` days (kept for at most
``LOG_DAYS``) so the Bills and Ahead screens can say "posted by itself".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from books import reminders, tell
from books.context import Ctx

DAY_MS = 24 * 60 * 60 * 1000
LOG_DAYS = 90  # the most "Show bills and income due within (days)" can be set to


def release(ctx: Ctx) -> None:
    """A stopped reminder with no date left on or before today is no longer stopped (the
    owner posted or skipped the rest, or moved it on)."""
    store = ctx.store
    with store.tx():
        store.run("UPDATE reminder SET auto_held=0, auto_said='' WHERE auto_said != '' "
                  "AND (next_due IS NULL OR next_due > ?)", (ctx.today().isoformat(),))


def _post_next(ctx: Ctx, rem_id: int, today: str) -> tuple[str, str]:
    """Post the reminder's next date if it is due. Returns ``("posted", due)``,
    ``("none", "")`` when nothing is due, or ``("failed", sentence)``."""
    store = ctx.store
    due = ""
    try:
        with store.tx():
            rem = store.reminder(rem_id)
            if (rem is None or not rem["auto"] or rem["about"] or rem["auto_held"]
                    or not rem["next_due"] or rem["next_due"] > today):
                return "none", ""
            due = rem["next_due"]
            result = reminders.post(ctx, {"id": rem_id, "due": due})
            store.insert("auto_post", {
                "reminder_id": rem_id, "name": rem["name"], "due": due, "kind": rem["kind"],
                "account_id": rem["account_id"], "to_account_id": rem["to_account_id"],
                "paid_other": int(bool(rem["paid_other"])), "created": ctx.now()})
            store.update("reminder", rem_id, auto_said="")
    except ValueError as error:
        return "failed", f"Books could not post it by itself for {due}: {error}"
    tell.settled(ctx, result, "Posted by itself.")
    return "posted", due


def post_due(ctx: Ctx) -> int:
    """The job's part: post what has come due by itself. Returns how many dates it posted."""
    store, cfg = ctx.store, ctx.cfg()
    if not bool(cfg.auto_post):
        return 0
    cap = max(1, int(cfg.auto_catch_up))
    today = ctx.today().isoformat()
    release(ctx)
    with store.tx():
        store.run("DELETE FROM auto_post WHERE created < ?", (ctx.now() - LOG_DAYS * DAY_MS,))
    ids = [r["id"] for r in store.all(
        "SELECT id FROM reminder WHERE auto=1 AND about=0 AND auto_held=0 AND next_due IS NOT "
        "NULL AND next_due <= ? ORDER BY next_due, id", (today,))]
    total = 0
    for rem_id in ids:
        posted, failed = 0, ""
        while posted < cap:
            outcome, said = _post_next(ctx, rem_id, today)
            if outcome == "posted":
                posted += 1
            else:
                failed = said if outcome == "failed" else ""
                break
        total += posted
        rem = store.reminder(rem_id)
        if rem is None:
            continue
        if failed:
            with store.tx():
                store.update("reminder", rem_id, auto_said=failed)
        elif posted >= cap and rem["next_due"] and rem["next_due"] <= today:
            many = "1 missed date" if posted == 1 else f"{posted} missed dates"
            with store.tx():
                store.update("reminder", rem_id, auto_held=1, auto_said=(
                    f"Books posted {many} by itself and stopped, because the most it posts "
                    f"at once is {cap} (a setting). The dates still overdue are yours to "
                    f"post or skip."))
    return total


def recent(ctx: Ctx) -> list[dict[str, Any]]:
    """What Books posted by itself within ``due_days`` days, newest first."""
    store, cfg = ctx.store, ctx.cfg()
    since = ctx.now() - int(cfg.due_days) * DAY_MS
    zone = ctx.zone()
    return [{"id": r["id"], "name": r["name"], "due": r["due"], "kind": r["kind"],
             "account": r["account_id"], "to_account": r["to_account_id"],
             "paid_other": bool(r["paid_other"]),
             "on": datetime.fromtimestamp(r["created"] / 1000, zone).date().isoformat()}
            for r in store.all("SELECT * FROM auto_post WHERE created >= ? ORDER BY id DESC",
                               (since,))]
