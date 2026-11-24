"""Changing a package's status: the one place it is done, whoever says so.

The tracker's reading and the owner's hand both come here, so the rules are the same:
the time a package was delivered is kept, a delivered package marked by hand stops being
checked at once, a new package a carrier does not know yet reads as waiting rather than
"not found", and a change the owner wants told is set down for ``tell``. What the tracker
reads counts by ``tell_when``; what the owner sets by hand counts only when it is
"delivered" (so a package marked delivered has its card, as one a tracker read would).
"""

from __future__ import annotations

from typing import Any

from packages import tell
from packages.context import Ctx
from packages.schedule import HOUR
from packages.status import words


def apply(ctx: Ctx, pkg: dict[str, Any], status: str, *, event: str = "",
          event_ms: int | None = None, by: str = "tracker") -> bool:
    """Set a package's status. Returns True when it changed. A change by the tracker that
    ``tell_when`` counts is filed on the Bench (or kept to be filed); so is a package the
    owner marks delivered by hand, when ``tell_when`` counts a delivery."""
    now, cfg = ctx.now(), ctx.cfg()
    if by == "tracker" and status == "not_found":
        since = pkg["registered_ms"] or pkg["created"]
        if now - since < int(cfg.not_found_hours) * HOUR:
            status = "waiting"  # the carrier often does not know a new number yet
    old = pkg["status"]
    changed = status != old
    values: dict[str, Any] = {"status": status, "status_by": by}
    if event:
        values.update(event=event[:300], event_ms=event_ms)
    if status == "delivered" and old != "delivered":
        values["delivered_ms"] = now
        if by == "hand":
            values["archived_ms"] = now  # the owner said so: no need to check it again
    elif status != "delivered" and old == "delivered":
        values.update(delivered_ms=None, archived_ms=None)
    told = by == "tracker" or (by == "hand" and status == "delivered")
    if told and tell.should_tell(str(cfg.tell_when), old, status):
        values["tell_status"] = status
    ctx.store.set(pkg["id"], **values)
    if "tell_status" in values:
        tell.deliver(ctx, pkg["id"])
    return changed


def by_hand(ctx: Ctx, pkg: dict[str, Any], status: str) -> bool:
    """The owner set the status himself."""
    return apply(ctx, pkg, status, by="hand", event=f"You marked it {words(status).lower()}.",
                 event_ms=ctx.now())
