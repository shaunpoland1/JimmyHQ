"""``app.emit``: a full app's program tells its own open pages that something changed.

    app.emit("board.changed", {"post": 12})       # in the program
    hq.on("board.changed", ({ post }) => ...)     # in its page

The program calls ``app.emit {event, data}``. JimmyHQ keeps the last few for a short while
(``state.EMITS_KEPT``) and the shell, while one of the app's frames is open, asks for new
ones (``GET /api/apps/<id>/emitted?after=<seq>``, owner only) and posts each to that app's
frames, and only that app's. It is a nudge, not the record: nothing is printed on the
Tape, and a page that was closed misses it.

An event's name is the app's own: a short dotted lower-case name that is not one of the
events JimmyHQ itself sends (``ready``, ``theme``, ``visible``, ``reload`` and the rest),
so a program can never make its page believe JimmyHQ said something.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from jimmyhq.apps import api
from jimmyhq.apps.bridge import BridgeError, Call, Ctx, only
from jimmyhq.apps.permissions import FULL
from jimmyhq.apps.plugs.state import state
from jimmyhq.ids import now_ms

if TYPE_CHECKING:
    from fastapi import APIRouter

    from jimmyhq.core import Core

# What JimmyHQ's shell sends a frame itself (web/src/modules/apps/lib/bridge.ts).
CORE_EVENTS = frozenset({"ready", "theme", "settings.changed", "records.changed",
                         "bench.decided", "visible", "reload"})
MAX_DATA = 16 * 1024
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}(\.[a-z][a-z0-9_]{0,30}){0,2}\Z")


def check_event_name(name: Any) -> str:
    """An app's own event: a short dotted lower-case name JimmyHQ does not use itself."""
    if not isinstance(name, str) or not _NAME.match(name):
        raise BridgeError("invalid", "\"event\" for app.emit is a short lower-case name such "
                                     "as \"board.changed\". Nothing was sent.")
    if name in CORE_EVENTS:
        raise BridgeError("invalid", f"\"{name}\" is an event JimmyHQ sends itself, so your "
                                     "app cannot send it. Nothing was sent. Choose a name of "
                                     "your own, such as \"board.changed\".")
    return name


def check_event_data(data: Any) -> Any:
    try:
        size = len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise BridgeError("invalid", "\"data\" for app.emit must be plain JSON. Nothing was "
                                     "sent.") from None
    if size > MAX_DATA:
        raise BridgeError("too_large", f"\"data\" for app.emit can be at most "
                                       f"{MAX_DATA // 1024} KB. Nothing was sent. Send an id "
                                       "and let the page ask for the rest.")
    return data


def app_emit(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    only("app.emit", args, ("event", "data"), ("event",))
    name = check_event_name(args["event"])
    data = check_event_data(args.get("data"))
    return {"seq": state(ctx.core).emit(ctx.id, name, data, now_ms())}


CALL = Call("app.emit", app_emit, tiers=(FULL,))


def add_call() -> None:
    if CALL.name not in api.API_CALLS:
        api.add_call(CALL)


def emitted(core: Core, app_id: str, after: int) -> dict[str, Any]:
    """What the app emitted after ``after``: ``{events: [{seq, event, data, at}], next}``.
    ``after`` of -1 asks only where the list ends now, for a page that has just opened."""
    if after < 0:
        return {"events": [], "next": state(core).last_emit(app_id)}
    found = state(core).emitted(app_id, after)
    return {"events": [{"seq": e["seq"], "event": e["name"], "data": e["data"],
                        "at": e["at"]} for e in found],
            "next": found[-1]["seq"] if found else after}


def build_router(core: Core) -> APIRouter:
    from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: PLC0415

    from jimmyhq.apps import store  # noqa: PLC0415
    from jimmyhq.auth import Principal  # noqa: PLC0415
    from jimmyhq.web.deps import owner  # noqa: PLC0415

    router = APIRouter(prefix="/api/apps", tags=["apps"])

    @router.get("/{app_id}/emitted")
    def emitted_route(app_id: str, after: int = Query(-1, ge=-1),  # noqa: B008
                      _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            store.need(core, app_id)
        except store.AppError as error:
            raise HTTPException(error.status, error.detail) from None
        return emitted(core, app_id, after)

    return router
