"""Practice, moving along by itself: the way to try Packages out with nothing looked up.

A real tracker reads a package's news and ``moves.apply`` acts on it, so a delivery files
its card. Practice reads nothing, so without this a package would stay where it was put until
the owner set it by hand. With it, each package Practice holds moves one stage at a time,
waiting, on its way, out for delivery, delivered, taking about ``practice_minutes`` in all,
and goes through ``moves.apply`` exactly as a tracker's reading would: the same rules, the
same card on the Bench. A package whose status the owner set by hand, or that has stopped
being checked, is left as it is.
"""

from __future__ import annotations

from packages import moves
from packages.context import Ctx
from packages.schedule import MINUTE
from packages.status import words

PATH = ("waiting", "on_its_way", "out_for_delivery", "delivered")
LAST_STEP = len(PATH) - 1


def happens(ctx: Ctx) -> bool:
    """Whether the tracking service the owner chose moves packages along by itself."""
    return bool(getattr(ctx.tracker(), "moves_by_itself", False))


def step_ms(ctx: Ctx) -> int:
    """How long a package stays at one stage: a third of ``practice_minutes``."""
    return max(1, int(ctx.cfg().practice_minutes)) * MINUTE // LAST_STEP


def event_of(status: str) -> str:
    return f"Practice: pretending it is {words(status).lower()}. Nothing was looked up."


def progress(ctx: Ctx) -> int:
    """Move every package that has stayed at its stage long enough to the next. Returns how
    many moved. A package is due a little before its time (half a minute), so a job that
    looks every few minutes does not make every stage take one look longer."""
    if not happens(ctx):
        return 0
    now, step, moved = ctx.now(), step_ms(ctx), 0
    for pkg in ctx.store.all("SELECT * FROM package WHERE archived_ms IS NULL AND "
                             "status_by='tracker' ORDER BY id"):
        if pkg["status"] not in PATH[:-1]:
            continue
        since = max(pkg["event_ms"] or 0, pkg["created"])
        if now - since < step - MINUTE // 2:
            continue
        status = PATH[PATH.index(pkg["status"]) + 1]
        moves.apply(ctx, pkg, status, event=event_of(status), event_ms=now)
        ctx.store.set(pkg["id"], checked_ms=now)
        moved += 1
    if moved:
        ctx.emit()
    return moved
