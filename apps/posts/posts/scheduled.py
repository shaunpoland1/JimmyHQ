"""The Scheduled view: everything SocialBu has scheduled on the owner's account, whether or
not Posts booked it.

Read only. Nothing here changes, cancels or creates anything on SocialBu, and it asks only
when the owner opens the view or presses Refresh (``refresh``): never on a timer, never from
an agent. ``kept`` answers from what was read last, with no request at all.

The last good read is kept in Posts' own store with the time it was read. A failed read
changes none of it: the view then shows the kept list and says plainly that SocialBu could
not be reached. A read is trusted only whole: any page that fails, or answers in a shape
Posts does not know, makes the whole read a failure (``SocialBu.listing`` raises).

The text of a scheduled post is outside text. It is kept and returned as it came, for the page
to show as text; it is never sent to a model, never put on the owner's record and never read
as an instruction. Which rows Posts booked itself is worked out each time the list is shown,
from the upstream ids Posts stored, so it is never out of date. Account names come from the
accounts list SocialBu gives (``accounts()``), else the label the owner gave that account
on a brand, else the page shows the network and the id.
"""

from __future__ import annotations

from typing import Any

from posts.context import Ctx
from posts.schedulers import Unavailable
from posts.store import LIVE_UPSTREAM

TOOL = "socialbu"
KEPT = "scheduled_list"  # the last good read: {read_ms, posts, pages, cut, skipped, names}
FAILED = "scheduled_list_failed"  # the last refused read: {at_ms, said}
IT_IS_FOR_SOCIALBU = "This list is for SocialBu."
SET_UP = ("Type the key in on Posts' Settings page under Apps, in Keys and tokens, then open "
          "this view again.")


def _tool(ctx: Ctx) -> Any:
    sched = ctx.schedulers.get(TOOL)
    return sched if callable(getattr(sched, "listing", None)) else None


def _uses(ctx: Ctx) -> bool:
    """Whether Posts books anything through SocialBu: by default, or for one brand."""
    if str(ctx.cfg().scheduler) == TOOL:
        return True
    return any(page.get("book_through") == TOOL
               or any(a.get("scheduler") == TOOL for a in page.get("accounts") or [])
               for page in ctx.store.pages())


def _cannot(ctx: Ctx) -> str:
    """Why the list cannot be shown now, as a sentence for the owner; '' when it can."""
    sched = _tool(ctx)
    if sched is None:
        return f"{IT_IS_FOR_SOCIALBU} This copy of Posts has no way to read it."
    if sched.has_key():
        return ""
    chosen = ctx.schedulers.get(str(ctx.cfg().scheduler))
    if chosen is not None and chosen.sends_out and chosen.id != TOOL and not _uses(ctx):
        return (f"{IT_IS_FOR_SOCIALBU} Posts books through {chosen.label}, which has no list "
                f"here. To see a SocialBu schedule, add its key. {SET_UP}")
    return f"Posts has no SocialBu key. {SET_UP}"


def _own(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """The posts Posts booked on SocialBu that still stand there, by the id SocialBu gave."""
    marks = ", ".join("?" * len(LIVE_UPSTREAM))
    rows = ctx.store.all(
        "SELECT u.external_id, o.id AS post_id, o.piece_id, o.state, o.page_id, "
        "g.name AS page_name "
        "FROM upstream u JOIN post o ON o.id = u.post_id LEFT JOIN page g ON g.id = o.page_id "
        f"WHERE u.account LIKE 'socialbu:%' AND u.external_id IS NOT NULL AND u.state IN ({marks}) "  # noqa: S608
        "ORDER BY u.id", LIVE_UPSTREAM)
    return {str(r["external_id"]): {"post_id": r["post_id"], "piece_id": r["piece_id"],
                                    "state": r["state"], "page_id": r["page_id"],
                                    "page_name": r["page_name"] or ""} for r in rows}


def _names(ctx: Ctx, kept: dict[str, Any]) -> dict[str, dict[str, str]]:
    """``{account id: {label, network}}``: the owner's own label for an account on a brand
    first as a fallback under SocialBu's own name for it."""
    out: dict[str, dict[str, str]] = {}
    for page in ctx.store.pages():
        for account in page.get("accounts") or []:
            if account.get("scheduler") == TOOL and account.get("account_id") \
                    and account.get("label"):
                out.setdefault(str(account["account_id"]), {"label": str(account["label"]),
                                                            "network": ""})
    for number, said in (kept.get("names") or {}).items():
        out[str(number)] = {"label": str(said.get("label") or out.get(str(number), {}).get(
            "label", "")), "network": str(said.get("network") or "")}
    return out


def _shape(ctx: Ctx, extra: dict[str, Any]) -> dict[str, Any]:
    """What the page draws: the kept list with the owner's marks, and why not when it
    cannot be shown."""
    sched = _tool(ctx)
    base: dict[str, Any] = {"scheduler": {"id": TOOL, "label": getattr(sched, "label", "SocialBu")},
                            "now_ms": ctx.now(), "usable": False, "said": "", "read_ms": None,
                            "rows": [], "cut": False, "pages_read": 0, "page_cap": 0,
                            "skipped": 0, "failed": None, **extra}
    base["said"] = _cannot(ctx)
    if base["said"]:
        return base
    base["usable"] = True
    kept = ctx.store.get_meta(KEPT)
    failed = ctx.store.get_meta(FAILED)
    if isinstance(kept, dict):
        names, own = _names(ctx, kept), _own(ctx)
        rows = []
        for post in kept.get("posts") or []:
            who = names.get(post["account_id"])
            rows.append({**post, "account": who or {"label": "", "network": ""},
                         "own": own.get(post["id"])})
        rows.sort(key=lambda r: (r["when_ms"] is None, r["when_ms"] or 0, r["id"]))
        base.update(read_ms=kept.get("read_ms"), rows=rows, cut=bool(kept.get("cut")),
                    pages_read=int(kept.get("pages") or 0), page_cap=int(kept.get("cap") or 0),
                    skipped=int(kept.get("skipped") or 0))
    if isinstance(failed, dict):
        after = ("This is the list kept from the last time it was read." if isinstance(kept, dict)
                 else "Nothing is kept yet, so there is no list to show.")
        why = str(failed.get("said") or "")
        base["failed"] = {"at_ms": failed.get("at_ms"),
                          "said": " ".join(
                              f"SocialBu could not be reached just now. {why} {after}".split())}
    return base


def kept(ctx: Ctx, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """The list as it was last read. Asks nobody."""
    return _shape(ctx, {})


def refresh(ctx: Ctx, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read SocialBu's schedule now, keep it if the whole read worked, and answer as
    ``kept`` does. A failed read keeps the last list and says so (``failed``)."""
    if _cannot(ctx):
        return _shape(ctx, {})
    sched = _tool(ctx)
    cap = int(ctx.cfg().scheduled_batches)
    try:
        found = sched.listing(cap)
    except Unavailable as error:
        ctx.store.set_meta(FAILED, {"at_ms": ctx.now(), "said": str(error)})
        return _shape(ctx, {})
    old = ctx.store.get_meta(KEPT)
    names = (old or {}).get("names") if isinstance(old, dict) else None
    try:  # the account names; a failure here only leaves the earlier names in place
        names = {a["account_id"]: {"label": a["label"], "network": a["network"]}
                 for a in sched.accounts()}
    except Unavailable:
        pass
    ctx.store.set_meta(KEPT, {"read_ms": ctx.now(), "posts": found["posts"],
                              "pages": found["pages"], "cut": found["cut"], "cap": cap,
                              "skipped": found["skipped"], "names": names or {}})
    ctx.store.conn.execute("DELETE FROM meta WHERE key=?", (FAILED,))
    ctx.store.conn.commit()
    return _shape(ctx, {})


def calls(ctx: Ctx) -> dict[str, Any]:
    return {"scheduled.kept": lambda a: kept(ctx, a),
            "scheduled.refresh": lambda a: refresh(ctx, a)}

