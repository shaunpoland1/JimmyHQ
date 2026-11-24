"""The spend guard: at most ``daily_limit`` requests to outside services in the owner's day.

Every request counts when it is about to be sent, whether or not the service then makes a
picture, since a refused or failed request may still be charged. A run asks for its whole
count first (``check``) so it is refused before anything is sent, and each request is then
counted again as it goes (``take``) in case another run used the room meanwhile. Practice
sends nothing and is never counted. A day runs from midnight in the owner's time zone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from imagery.context import Ctx
from imagery.rules import Refused

KEEP_DAYS = 7  # how long a request stays on the books


def day_start_ms(ctx: Ctx, now_ms: int) -> int:
    """Midnight at the start of the owner's day that holds ``now_ms``, in unix ms UTC."""
    local = datetime.fromtimestamp(now_ms / 1000, ctx.zone())
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp() * 1000)


def used_today(ctx: Ctx) -> int:
    return ctx.store.number("SELECT COUNT(*) FROM attempt WHERE ts>=?",
                            (day_start_ms(ctx, ctx.now()),))


def status(ctx: Ctx) -> dict[str, int]:
    limit = int(ctx.cfg().daily_limit)
    used = used_today(ctx)
    return {"used": used, "limit": limit, "left": max(0, limit - used)}


def over_sentence(used: int, limit: int, wanted: int) -> str:
    left = max(0, limit - used)
    if left == 0:
        return (f"You have already asked outside image services for {used} pictures today, "
                f"which is your limit of {limit}. Nothing was made. Try again tomorrow, use "
                "Practice, or raise \"Pictures a day with an outside service\" in Imagery's "
                "settings.")
    return (f"You have asked for {used} of today's {limit} pictures, so {wanted} more would "
            f"go over your limit. Nothing was made. Ask for {left} or fewer, or raise "
            "\"Pictures a day with an outside service\" in Imagery's settings.")


def check(ctx: Ctx, provider: Any, wanted: int) -> None:
    """Refuse a request for ``wanted`` pictures that would go over the day's limit."""
    if not provider.outside:
        return
    now = status(ctx)
    if now["used"] + wanted > now["limit"]:
        raise Refused(over_sentence(now["used"], now["limit"], wanted))


def take(ctx: Ctx, provider: Any, run_id: int | None) -> int | None:
    """Count one request about to be sent, or refuse when the day's limit is used up.
    Returns the count's number, for ``give_back``; None when nothing was counted."""
    if not provider.outside:
        return None
    with ctx.spend_lock:
        now = status(ctx)
        if now["used"] + 1 > now["limit"]:
            raise Refused(over_sentence(now["used"], now["limit"], 1))
        taken = ctx.store.insert("attempt", {"ts": ctx.now(), "provider": provider.id,
                                             "run_id": run_id})
        ctx.store.forget_attempts_before(ctx.now() - KEEP_DAYS * 86_400_000)
        return taken


def give_back(ctx: Ctx, taken: int | None) -> None:
    """Take a count back when nothing left this computer (JimmyHQ refused the request, or
    the address was not allowed yet), so a request never sent is not charged to the day."""
    if taken is not None:
        with ctx.spend_lock:
            ctx.store.forget_attempt(taken)
