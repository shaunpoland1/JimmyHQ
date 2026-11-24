"""What the owner does to a package: add, rename, change carrier, register again, remove,
mark by hand.

Each is a named rule that refuses in a sentence that says nothing was changed and what to
do instead. A tracking number is kept only here and in the store: it is cleaned of spaces,
and never written anywhere else.
"""

from __future__ import annotations

import re
from typing import Any

from packages import moves, sync, tell
from packages.context import Ctx
from packages.status import STATUSES, words
from packages.trackers import NO, NONE, YES, Item

MOST_LABEL = 80
MOST_CARRIER_NAME = 40
_NUMBER = re.compile(r"[A-Za-z0-9._/-]{4,60}\Z")


class Refused(ValueError):
    """The sentence the owner reads: what was refused, that nothing changed, what to do."""


def clean_label(value: Any, nothing: str) -> str:
    """Rule ``a_package_has_a_name``: one line of up to 80 characters."""
    label = " ".join(str(value or "").split())
    if not label:
        raise Refused(f"Give the package a short name, such as \"Garden hose\", so you can "
                      f"tell it apart. {nothing}")
    if len(label) > MOST_LABEL:
        raise Refused(f"A package's name is at most {MOST_LABEL} characters. {nothing} "
                      "Shorten it.")
    return label


def clean_number(value: Any) -> str:
    """Rule ``a_number_is_one_word``: letters, digits and . _ / - only, 4 to 60 of them,
    spaces taken out."""
    number = re.sub(r"\s+", "", str(value or ""))
    if not number:
        raise Refused("Type the tracking number. Nothing was added.")
    if not _NUMBER.match(number):
        raise Refused("A tracking number is 4 to 60 letters and digits (a dash or dot is "
                      "fine). Nothing was added. Check what you typed.")
    return number


def clean_carrier(ctx: Ctx, args: dict[str, Any], nothing: str) -> tuple[str, str]:
    """``(code, name)``: the carrier the owner chose, "" for "detect it". The tracker says
    whether the code is one it can use."""
    code = "".join(str(args.get("carrier") or "").split())
    name = " ".join(str(args.get("carrier_name") or "").split())
    if len(name) > MOST_CARRIER_NAME:
        raise Refused(f"A carrier's name is at most {MOST_CARRIER_NAME} characters. {nothing}")
    said = ctx.tracker().check_carrier(code) if code else None
    if said:
        raise Refused(said)
    return code, name


def one(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    try:
        found = ctx.store.package(int(args["id"]))
    except (KeyError, TypeError, ValueError):
        raise Refused("Say which package. Nothing was changed.") from None
    if found is None:
        raise Refused("There is no such package: it may have been removed. Nothing was "
                      "changed.")
    return found


def add(ctx: Ctx, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Add a package. Returns it and a sentence for the owner. A new number is registered
    with the tracker at once (once, ever); if that cannot be done now it is tried again at
    the next check, and the package is kept."""
    nothing = "Nothing was added."
    label = clean_label(args.get("label"), nothing)
    number = clean_number(args.get("number"))
    code, name = clean_carrier(ctx, args, nothing)
    twin = ctx.store.by_number(number)
    if twin is not None:
        raise Refused(f"\"{twin['label']}\" already has that number. Nothing was added.")
    now = ctx.now()
    new = ctx.store.insert("package", {"label": label, "number": number, "carrier": code,
                                       "created": now, "updated": now})
    if code and name:
        ctx.store.save_carrier(code, name)
    said = f"Added {label}."
    tracker = ctx.tracker()
    if tracker.outside:
        done = sync.run(ctx, by="add", only=new)
        if not done.ok:
            said += (f" It could not be looked up just now: {done.said} It is tried again "
                     "at the next check.")
        elif (ctx.store.package(new) or {}).get("refused"):
            said += f" {tracker.label} did not take it: {ctx.store.package(new)['refused']}"
    ctx.emit()
    return ctx.store.package(new) or {}, said


def rename(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    pkg = one(ctx, args)
    label = clean_label(args.get("label"), "Nothing was changed.")
    return ctx.store.set(pkg["id"], label=label)


def change_carrier(ctx: Ctx, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Change the carrier of a package. When the tracker already knows the number, it is
    asked first (with the carrier it has now), and the change is kept only if it says
    yes. When it gives no clear answer, the tracker may have made the change or not: the
    package is marked "carrier change not confirmed" and read without a carrier until a
    read says which carrier the tracker has (``sync``), so a read under the wrong carrier
    never makes it look gone."""
    nothing = "Nothing was changed."
    pkg = one(ctx, args)
    code, name = clean_carrier(ctx, args, nothing)
    tracker = ctx.tracker()
    said = f"The carrier of {pkg['label']} is changed."
    if tracker.outside and tracker.registers and sync.is_registered(pkg, tracker):
        problem = tracker.key_problem()
        if problem:
            raise Refused(f"{problem} {nothing}")
        now_on = pkg["carrier"] or pkg["carrier_found"]
        reply = tracker.change_carrier(Item(pkg["id"], pkg["number"], now_on), code)
        if reply.kind == NONE or (reply.kind == YES and reply.items[0].kind == NONE):
            note = (f"Carrier change not confirmed: {tracker.label} gave no clear answer "
                    f"about the change to carrier {code} ({reply.said or reply.items[0].said})."
                    " Packages reads this number without a carrier until a read says which "
                    "carrier it has, and keeps that one. Look again after the next check.")
            sync.mark_carrier_unconfirmed(ctx, pkg["id"], code)
            if code and name:
                ctx.store.save_carrier(code, name)
            updated = ctx.store.set(pkg["id"], note=note)
            ctx.emit()
            return updated, note
        if reply.kind == NO or reply.items[0].kind != YES:
            raise Refused(f"{reply.said or reply.items[0].said} {nothing}")
    if code and name:
        ctx.store.save_carrier(code, name)
    sync.mark_carrier_unconfirmed(ctx, pkg["id"], None)
    updated = ctx.store.set(pkg["id"], carrier=code, refused="", note="")
    ctx.emit()
    return updated, said


def register_again(ctx: Ctx, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Register again a number the tracker no longer knows, which uses another place of
    quota: only when the owner presses it on that package, never by a check."""
    pkg = one(ctx, args)
    tracker = ctx.tracker()
    if not sync.is_held(ctx, pkg["id"]):
        raise Refused("This package is not waiting to be registered again. Nothing was "
                      "changed.")
    sync.hold(ctx, pkg["id"], False)
    ctx.store.set(pkg["id"], refused="", note="")
    said = f"{pkg['label']} is registered with {tracker.label} again."
    done = sync.run(ctx, by="add", only=pkg["id"])
    after = ctx.store.package(pkg["id"]) or {}
    if not done.ok:
        said = (f"{pkg['label']} could not be registered just now: {done.said} It is tried "
                "again at the next check.")
    elif after.get("refused"):
        said = f"{tracker.label} did not take it: {after['refused']}"
    ctx.emit()
    return after, said


def remove(ctx: Ctx, args: dict[str, Any]) -> str:
    """Remove a package for good. The tracker is asked, once, to stop tracking it; if it
    cannot be reached the package is removed here all the same, and the sentence says
    so."""
    pkg = one(ctx, args)
    tracker, said = ctx.tracker(), f"{pkg['label']} is removed."
    if tracker.outside and tracker.registers and sync.is_registered(pkg, tracker):
        if tracker.key_problem():
            said += f" {tracker.label} was not asked to stop tracking it: it has no key."
        else:
            reply = tracker.forget(Item(pkg["id"], pkg["number"], pkg["carrier"]))
            if reply.kind != YES or reply.items[0].kind != YES:
                said += (f" {tracker.label} could not be asked to stop tracking it "
                         f"({reply.said or reply.items[0].said}), so it still holds the "
                         "number.")
    tell.withdraw(ctx, pkg["card_id"], "The package was removed.")
    ctx.store.remove(pkg["id"])
    ctx.emit()
    return said


def mark(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """The owner says what a package's status is. A tracker that looks parcels up takes
    only "delivered" by hand; Practice takes any status."""
    pkg = one(ctx, args)
    status = str(args.get("status") or "")
    allowed = ctx.tracker().hand_statuses
    if status not in STATUSES or status not in allowed:
        raise Refused(f"{ctx.tracker().label} lets you mark a package only as: "
                      f"{', '.join(words(s).lower() for s in allowed)}. Nothing was changed.")
    moves.by_hand(ctx, pkg, status)
    ctx.emit()
    return ctx.store.package(pkg["id"]) or {}


def unmark(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Undo a status the owner set by hand: the package goes back to waiting, and is
    checked again."""
    pkg = one(ctx, args)
    if pkg["status_by"] != "hand":
        raise Refused("That status did not come from you, so there is nothing to undo. "
                      "Nothing was changed.")
    moves.apply(ctx, pkg, "waiting", by="hand", event="You took back your mark.",
                event_ms=ctx.now())
    ctx.store.set(pkg["id"], status_by="tracker", archived_ms=None)
    ctx.emit()
    return ctx.store.package(pkg["id"]) or {}
