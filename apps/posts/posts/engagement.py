"""How posts did: the figures a scheduler gives for each post that went out, kept per page
as plain numbers.

``collect`` runs with reconcile: for ``figures_days`` after a post goes out it asks the
scheduler again and keeps the latest figure for each account and metric. A scheduler that
gathers figures only now and then (Buffer: once a day) is asked no more often than that,
and not again about a post whose figures stopped changing. The researcher's
evidence tool reads them with ``what_did_well(ctx, page_id)``.
"""

from __future__ import annotations

import logging
from typing import Any

from posts.context import Ctx

log = logging.getLogger("posts.engagement")

DAY_MS = 24 * 3_600_000

# Figures that count people who saw a post, not what they did: kept, never ranked on.
SEEN_ONLY = frozenset({"impressions", "unique_impressions", "views", "reach", "media_view",
                       "video_views", "video_views_paid", "video_views_organic",
                       "impressions_paid", "impressions_organic", "impressions_fan",
                       "impressions_unique", "total_plays"})

NONE_YET = ("There are no figures for this brand's posts yet: none has gone out through a "
            "scheduler that gives them, or it has not given any so far.")


def interactions(figures: dict[str, float]) -> float:
    """What people did with a post (likes, comments, shares and so on), summed; figures
    that only count who saw it are left out. A ``total_interactions`` figure, where the
    service gives one, stands for the rest."""
    if "total_interactions" in figures:
        return float(figures["total_interactions"])
    return float(sum(v for k, v in figures.items() if k not in SEEN_ONLY))


def due_for_figures(post: dict[str, Any], now_ms: int, days: int) -> bool:
    """A post that went out in the last ``days`` days."""
    return (days > 0 and post["state"] == "went_out" and post["slot_ms"] is not None
            and now_ms - days * DAY_MS <= post["slot_ms"] <= now_ms)


def keep(ctx: Ctx, post: dict[str, Any], copies: list[dict[str, Any]]) -> int:
    """Keep the latest figures from each copy. Returns how many figures were kept."""
    count = 0
    for copy in copies:
        for metric, value in (copy.get("figures") or {}).items():
            ctx.store.conn.execute(
                "INSERT INTO figure (post_id, page_id, account, metric, value, seen) VALUES "
                "(?, ?, ?, ?, ?, ?) ON CONFLICT(post_id, account, metric) DO UPDATE SET "
                "value=excluded.value, seen=excluded.seen",
                (post["id"], post["page_id"], copy["account"], metric, float(value),
                 ctx.now()))
            count += 1
    ctx.store.conn.commit()
    return count


def figures_sign(copies: list[dict[str, Any]]) -> str:
    """The figures of a read as one comparable text; '' when there are none."""
    rows = sorted((str(c.get("account")), k, float(v)) for c in copies
                  for k, v in (c.get("figures") or {}).items())
    return repr(rows) if rows else ""


def asks_again(read: dict[str, Any] | None, now_ms: int, every_ms: int | None) -> bool:
    """Rule ``figures_are_asked_for_only_while_they_may_change``: a scheduler that says how
    often it gathers figures (``figures_every_ms``; Buffer: once a day) is asked about a
    post at most that often, and no more once two reads in a row gave the same figures.
    Any other scheduler is asked at every run, as before."""
    if not every_ms or not read:
        return True
    if read.get("steady"):
        return False
    return now_ms - int(read.get("at") or 0) >= every_ms


def collect(ctx: Ctx) -> int:
    """Ask again about each post that went out recently, all at once where the scheduler
    can (``status_many``). A scheduler that cannot tell changes nothing. Returns how many
    figures were kept."""
    days = int(ctx.cfg().figures_days)
    now = ctx.now()
    count = 0
    reads = dict(ctx.store.get_meta("figures.read") or {})
    due: dict[str, list[tuple[dict[str, Any], list[dict[str, Any]]]]] = {}
    for post in ctx.store.all("SELECT * FROM post WHERE state='went_out' ORDER BY id"):
        sched = ctx.schedulers.get(post["scheduler"])
        if sched is None or not sched.sends_out or not due_for_figures(post, now, days):
            continue
        if not asks_again(reads.get(str(post["id"])), now,
                          getattr(sched, "figures_every_ms", None)):
            continue
        copies = [c for c in ctx.store.upstream(post["id"]) if c["state"] == "published"]
        if copies:
            due.setdefault(sched.id, []).append((post, copies))
    for sched_id, wanted in due.items():
        sched = ctx.schedulers[sched_id]
        for post, seen in _ask_each(sched, wanted):
            if seen is None or not seen.known:
                continue
            count += keep(ctx, post, seen.copies)
            if getattr(sched, "figures_every_ms", None):
                sign = figures_sign(seen.copies)
                before = reads.get(str(post["id"])) or {}
                reads[str(post["id"])] = {"at": now, "sign": sign,
                                          "steady": bool(sign) and sign == before.get("sign")}
    keep_reads = {k: v for k, v in reads.items()
                  if ctx.store.one("SELECT 1 FROM post WHERE id=? AND state='went_out'", (int(k),))}
    ctx.store.set_meta("figures.read", keep_reads)
    return count


def _ask_each(sched: Any, wanted: list[tuple[dict[str, Any], list[dict[str, Any]]]]
              ) -> list[tuple[dict[str, Any], Any]]:
    """What the scheduler says of each post: in one go with ``status_many``, else one by
    one. A failure is None for that post."""
    many = getattr(sched, "status_many", None)
    if callable(many):
        try:
            found = many([(post["id"], copies) for post, copies in wanted])
        except Exception as error:  # noqa: BLE001 - a blip changes nothing
            log.info("no figures from %s: %s", sched.id, type(error).__name__)
            return [(post, None) for post, _ in wanted]
        return [(post, found.get(post["id"])) for post, _ in wanted]
    out = []
    for post, copies in wanted:
        try:
            out.append((post, sched.status(post["id"], copies)))
        except Exception as error:  # noqa: BLE001 - a blip changes nothing
            log.info("no figures for post %s: %s", post["id"], type(error).__name__)
            out.append((post, None))
    return out


def what_did_well(ctx: Ctx, page_id: int, *, limit: int = 10) -> dict[str, Any]:
    """The page's posts that went out, best first by what people did with them, each with
    its figures summed over its accounts as plain numbers::

        {"page": 3, "posts": [{"post": 12, "title": ..., "slot_ms": ..., "interactions": 41.0,
                               "figures": {"likes": 30.0, "comments": 11.0}}], "said": ""}

    With none, ``posts`` is empty and ``said`` says so."""
    rows = ctx.store.all("SELECT f.post_id, f.metric, SUM(f.value) AS value, p.title, "
                         "p.slot_ms FROM figure f JOIN post p ON p.id=f.post_id WHERE "
                         "f.page_id=? GROUP BY f.post_id, f.metric", (page_id,))
    posts: dict[int, dict[str, Any]] = {}
    for row in rows:
        entry = posts.setdefault(row["post_id"], {"post": row["post_id"],
                                                  "title": row["title"],
                                                  "slot_ms": row["slot_ms"], "figures": {}})
        entry["figures"][row["metric"]] = float(row["value"])
    ranked = sorted(posts.values(), key=lambda e: (-interactions(e["figures"]), e["post"]))
    for entry in ranked:
        entry["interactions"] = interactions(entry["figures"])
    return {"page": page_id, "posts": ranked[:max(1, int(limit))],
            "said": "" if ranked else NONE_YET}
