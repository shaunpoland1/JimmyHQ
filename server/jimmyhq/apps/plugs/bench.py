"""The Bench for a full app: cards of its own types, with its own buttons, and the owner's
decision handed back to it.

**Filing** is the bridge's ``bench.file``: the kind is forced to ``app.<id>.<kind>`` (one
the manifest declares), the card is filed by ``app:<id>`` with the app's name as its
source, and its buttons are checked by the Bench like anyone's. A card's words are data:
nothing in them is read as an instruction, and only the owner decides.

**Changing its own cards.** Two calls a program may make, with ``bench.file`` granted:

- ``bench.update {card_id, note?, title?, summary?, body?}``
- ``bench.withdraw {card_id, note?}``

Each only on a card **this app filed**; any other card, whoever filed it, is answered as
if there were no such card, so an app cannot learn what else is on the Bench. A private
app's card keeps a title only, so its update may change the title alone.

``ladder.check {tool, action, category}`` answers how far the owner lets this app go with
something that leaves the building: ``read``, ``approve`` or ``alone``. Who is asking is
always ``app:<id>``; the call cannot name anyone else. Nothing can raise a level here.

**Decisions.** There is no call that decides a card. The worker follows ``bench.decided``
on the Tape (``follow``, with a cursor for each app) and queues a ``decision`` delivery
for each decision on a card this app filed, of a type it said it handles. "This app
filed" is read from who printed the card (``filed_by``), never from its kind: anyone may
file a card under any kind, so a card named like the app's is not the app's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jimmyhq.apps import api, bridge
from jimmyhq.apps.bridge import BridgeError, Call, Ctx, only, text_arg
from jimmyhq.apps.permissions import FULL

if TYPE_CHECKING:
    from jimmyhq.tape.registry import Event

_NOT_YOURS = ("There is no card with that id that your app filed, so nothing was changed. "
              "Use the card id bench.file gave you.")


def own_card(ctx: Ctx, card_id: Any) -> dict[str, Any]:
    """The card, if this app filed it. Every other card, missing or someone else's, is
    refused with the same words."""
    from jimmyhq import bench  # noqa: PLC0415

    if not isinstance(card_id, str) or not 0 < len(card_id) <= 40:
        raise BridgeError("not_found", _NOT_YOURS)
    card = bench.get_card(ctx.core, card_id)
    if card is None or card.get("filed_by") != ctx.actor:
        raise BridgeError("not_found", _NOT_YOURS)
    return card


def check_private_update(ctx: Ctx, args: dict[str, Any]) -> None:
    """A private app's cards carry a title only, so an update may change the title and
    nothing else."""
    if ctx.app.manifest.private and set(args) - {"card_id", "title"}:
        raise BridgeError("invalid", "Your app is marked private, so its cards carry a title "
                                     "only. Nothing was changed. Send only \"card_id\" and "
                                     "\"title\".")


def bench_update(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    from jimmyhq import bench  # noqa: PLC0415

    only("bench.update", args, ("card_id", "note", "title", "summary", "body"),
         ("card_id",), note="Who changed it is set by JimmyHQ. ")
    check_private_update(ctx, args)
    own_card(ctx, args["card_id"])
    bench.update_card(ctx.core, args["card_id"], by=ctx.actor,
                      note=text_arg("bench.update", args, "note", 4000),
                      title=args.get("title"), summary=args.get("summary"),
                      body=args.get("body"))
    return {"card_id": args["card_id"]}


def bench_withdraw(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    from jimmyhq import bench  # noqa: PLC0415

    only("bench.withdraw", args, ("card_id", "note"), ("card_id",))
    if ctx.app.manifest.private and args.get("note") is not None:
        raise BridgeError("invalid", "Your app is marked private, so it withdraws a card "
                                     "without a note. Nothing was changed. Leave out "
                                     "\"note\".")
    own_card(ctx, args["card_id"])
    bench.withdraw_card(ctx.core, args["card_id"], by=ctx.actor,
                        note=text_arg("bench.withdraw", args, "note", 4000))
    return {"card_id": args["card_id"]}


def ladder_check(ctx: Ctx, args: dict[str, Any]) -> str:
    from jimmyhq import bench  # noqa: PLC0415

    only("ladder.check", args, ("tool", "action", "category"), ("tool", "action", "category"),
         note="Who is asking is always your app. ")
    return bench.check(ctx.core, ctx.actor, args["tool"], args["action"], args["category"])


CALLS = (
    Call("bench.update", bench_update, "bench.file", tiers=(FULL,)),
    Call("bench.withdraw", bench_withdraw, "bench.file", tiers=(FULL,)),
    Call("ladder.check", ladder_check, tiers=(FULL,)),
)


def add_calls() -> None:
    """Make the calls known to the app API, once."""
    for call in CALLS:
        if call.name not in api.API_CALLS:
            api.add_call(call)


# --- decisions -------------------------------------------------------------------------------


def filed_by_app(app_id: str, event: Event) -> bool:
    """A decision is this app's when the card was filed by ``app:<id>`` and is of one of
    its own kinds. Both, because a write token may file any kind, and the kind alone says
    nothing about who filed it."""
    p = event.payload
    return (p.get("filed_by") == f"app:{app_id}"
            and str(p.get("card_kind") or "").startswith(f"app.{app_id}."))


def decision_delivery(app: bridge.Installed, plugs: dict[str, Any],
                      event: Event) -> dict[str, Any] | None:
    """What a ``decision`` delivery carries for this event, or None when it is not this
    app's or of a card type it said it handles."""
    if (event.kind != "bench.decided" or not event.readable
            or not filed_by_app(app.manifest.id, event)):
        return None  # (an event that was erased says nothing to hand over)
    p = event.payload
    short = p["card_kind"].removeprefix(app.manifest.prefix)
    if short not in (plugs.get("decisions") or []):
        return None
    return {"card_id": p["card_id"], "card_kind": p["card_kind"], "action": p["action"],
            "note": p.get("note"), "reason": p.get("reason"), "subject": p.get("subject"),
            "event_id": event.id, "ts": event.ts}
