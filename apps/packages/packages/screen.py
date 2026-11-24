"""What Packages' own page may ask (``hq.app.call``). Each call returns plain JSON.

The page shows every string as text, never as HTML. A refusal is a sentence the page puts
on its status line; a number is sent to the page only here, to the owner's own screen.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from packages import ops, pretend, schedule, sync
from packages.context import Ctx
from packages.status import LOUD, STATUSES, WORDS, words

ROW_FIELDS = ("id", "label", "number", "carrier", "carrier_found", "carrier_guessed", "status",
              "status_by", "event", "event_ms", "checked_ms", "note", "refused",
              "delivered_ms", "created")


def row_of(ctx: Ctx, pkg: dict[str, Any]) -> dict[str, Any]:
    out = {k: pkg[k] for k in ROW_FIELDS}
    out["carrier_guessed"] = bool(pkg["carrier_guessed"])
    out["words"] = words(pkg["status"])
    out["carrier_name"] = ctx.store.carrier_name(pkg["carrier"] or pkg["carrier_found"])
    out["archived"] = pkg["archived_ms"] is not None
    out["registered"] = sync.is_registered(pkg, ctx.tracker())
    out["tab"] = "delivered" if pkg["status"] == "delivered" else "way"
    out["waiting_to_tell"] = bool(pkg["tell_status"])
    out["held"] = sync.is_held(ctx, pkg["id"])  # "Register again (uses quota)"
    out["carrier_unconfirmed"] = sync.carrier_unconfirmed(ctx).get(pkg["id"])
    return out


def overview(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store, cfg, tracker = ctx.store, ctx.cfg(), ctx.tracker()
    rows = [row_of(ctx, p) for p in store.packages(int(cfg.screen_rows))]
    state = sync.schedule_state(ctx)
    outside = bool(tracker.outside)
    return {
        "rows": rows,
        "tracker": {"id": tracker.id, "label": tracker.label, "outside": outside,
                    "registers": bool(tracker.registers),
                    "key_problem": tracker.key_problem(),
                    "hand_statuses": list(tracker.hand_statuses),
                    "carrier_help": tracker.carrier_help,
                    "practice": pretend.happens(ctx)},
        "statuses": [{"id": s, "words": WORDS[s]} for s in STATUSES],
        "carriers": store.carriers(),
        "quota": store.get_meta("quota") if outside and tracker.registers else None,
        "schedule": {**state, "outside": outside, "practice": pretend.happens(ctx),
                     "practice_minutes": int(cfg.practice_minutes),
                     "interval_hours": int(cfg.interval_hours),
                     "gap_minutes": int(cfg.refresh_gap_minutes),
                     "waits_ms": schedule.refresh_wait_ms(
                         ctx.now(), store.get_meta("last_sent"),
                         int(cfg.refresh_gap_minutes)) if outside else 0},
        "badge": sum(1 for r in rows if r["status"] in LOUD),
        "tell_when": str(cfg.tell_when),
    }


def added(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    pkg, said = ops.add(ctx, args)
    return {**row_of(ctx, pkg), "said": said}


def carrier_changed(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    pkg, said = ops.change_carrier(ctx, args)
    return {**row_of(ctx, pkg), "said": said}


def registered_again(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    pkg, said = ops.register_again(ctx, args)
    return {**row_of(ctx, pkg), "said": said}


def refreshed(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    out = sync.refresh(ctx)
    return {"ok": out.ok, "said": out.said, "checked": out.checked, "changed": out.changed}


def carrier_forgotten(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    code = str(args.get("carrier") or "")
    if not ctx.store.forget_carrier(code):
        raise ValueError("That carrier is not in your saved list. Nothing was changed.")
    return {"said": "Taken out of your saved carriers. Packages that use it keep it."}


def calls(ctx: Ctx) -> dict[str, Callable[[dict[str, Any]], Any]]:
    return {
        "overview": lambda a: overview(ctx, a),
        "package.add": lambda a: added(ctx, a),
        "package.rename": lambda a: row_of(ctx, ops.rename(ctx, a)),
        "package.carrier": lambda a: carrier_changed(ctx, a),
        "package.register": lambda a: registered_again(ctx, a),
        "package.remove": lambda a: {"said": ops.remove(ctx, a)},
        "package.mark": lambda a: row_of(ctx, ops.mark(ctx, a)),
        "package.unmark": lambda a: row_of(ctx, ops.unmark(ctx, a)),
        "refresh": lambda a: refreshed(ctx, a),
        "carrier.forget": lambda a: carrier_forgotten(ctx, a),
    }
