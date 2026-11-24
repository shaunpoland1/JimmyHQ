"""``build()``: the Posts app with every job, card, call and kind wired in.

``posts.main`` makes the one JimmyHQ runs. A test makes its own with
``build(transport=FakeCore(...), clock=...)``; nothing is shared between two.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jimmyhq_app import App, Decision, Work

from posts import (
    actions,
    booking,
    cards,
    config,
    credits,
    from_reader,
    kinds,
    lookup,
    outside,
    pages,
    pictures,
    plain,
    reconcile,
    scheduled,
    screen,
    states,
    tools,
    voice,
    waiting,
    work,
)
from posts.context import Ctx
from posts.intake import WAYS

ADVANCE_EVERY_S = 60


def build(*, transport: Any = None, clock: Callable[[], int] | None = None,
          schedulers: dict[str, Any] | None = None) -> App:
    app = App(transport=transport)
    ctx = Ctx(app, clock=clock, schedulers=schedulers)
    app.posts = ctx  # type: ignore[attr-defined]  # for tests and later parts
    kinds.declare(app)

    @app.job("advance", every=ADVANCE_EVERY_S)
    def advance() -> str:
        return plain.advance(ctx)

    @app.job("reconcile", every_setting="reconcile_minutes")
    def reconcile_job() -> str:
        return reconcile.run(ctx)

    @app.on_decision("draft")
    def draft_decided(decision: Decision) -> str:
        return cards.on_draft(ctx, decision)

    @app.on_decision("overlap")
    def overlap_decided(decision: Decision) -> str:
        return cards.on_overlap(ctx, decision)

    @app.on_decision("notice")
    def notice_seen(decision: Decision) -> str | None:
        if (decision.subject or "").startswith("work:"):
            return work.on_card(ctx, decision)  # Wait, Finish without agents, Drop
        return None

    @app.job("intake", every_setting="intake_minutes")
    def intake_job() -> str:
        return from_reader.intake_job(ctx)

    @app.job("watch", every_setting="watch_minutes")
    def watch() -> str:
        return work.watch(ctx)

    @app.on_work
    def work_came_back(given: Work) -> str:
        return work.on_work(ctx, given)

    tools.register(app, ctx)
    outside.register(app, ctx)  # for anyone who holds no role: read only

    @app.ui.counter
    def waiting_on_owner() -> int:
        return waiting.count(ctx)  # the sidebar's number, said after everything that changes it

    @app.on_settings
    def settings_changed(key: str) -> None:
        ctx.forget_settings()
        if key == "letter_keys":
            ctx.emit("posts.changed")  # an open page follows the switch at once
        for kind, pair in config.PAIRS.items():
            if key in pair:
                limits = config.thresholds(ctx.cfg(), ctx.store, kind)
                if limits.refused:
                    cards.notice(ctx, "Posts did not take that change", limits.refused)
                    ctx.emit("posts.changed")

    for name, fn in calls(ctx).items():
        app.call(name)(fn if name in READS else announcing(ctx, fn))
    return app


# The page's calls that only read. Every other call is wrapped by ``announcing``.
READS = frozenset({"overview", "pages.list", "rules.list", "pieces.list", "piece.get",
                   "posts.list", "ideas.list", "pages.removal", "post.approval",
                   "accounts.list", "voice.fill", "scheduled.kept", "scheduled.refresh"})


def announcing(ctx: Ctx, fn: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
    """``fn``, telling the open pages ``posts.changed`` whenever it changed a record and
    did not say so itself, so every view of every open page can draw itself again."""

    def run(args: dict[str, Any]) -> Any:
        changes, emits = ctx.store.conn.total_changes, ctx.emits
        try:
            return fn(args)
        finally:
            if ctx.store.conn.total_changes != changes and ctx.emits == emits:
                ctx.emit("posts.changed")

    run.__wrapped__ = fn  # type: ignore[attr-defined]
    return run


def _int(args: dict[str, Any], key: str = "id") -> int:
    try:
        return int(args[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Say which one with \"{key}\". Nothing was changed.") from None


def calls(ctx: Ctx) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """What the app's page may ask. Each returns plain JSON."""

    def page_list(args: dict[str, Any]) -> dict[str, Any]:
        found = ctx.store.pages()
        return {"pages": found, "hint": "" if found else pages.NO_PAGES}

    def piece_view(args: dict[str, Any]) -> dict[str, Any]:
        piece = ctx.store.piece(_int(args))
        if piece is None:
            raise ValueError("There is no such piece.")
        post = ctx.store.post_of(piece["id"])
        return {"piece": {**piece, "words": states.WORDS.get(piece["state"], piece["state"])},
                "turns": ctx.store.turns(piece["id"]), "post": post,
                "worked": credits.line(ctx.store, piece["id"]),
                "checks": ctx.store.checks(post["id"]) if post else [],
                "attempts": ctx.store.attempts(post["id"]) if post else [],
                "pictures": pictures.view(ctx, piece)}

    def piece_list(args: dict[str, Any]) -> list[dict[str, Any]]:
        rows = ctx.store.all("SELECT id, page_id, way, source_url, topic, state, error, "
                             "updated FROM piece ORDER BY id DESC LIMIT 100")
        return [{**r, "words": states.WORDS.get(r["state"], r["state"])} for r in rows]

    def post_list(args: dict[str, Any]) -> list[dict[str, Any]]:
        rows = ctx.store.all("SELECT * FROM post ORDER BY COALESCE(slot_ms, updated) DESC, "
                             "id DESC LIMIT 200")
        return [{**r, "words": states.WORDS.get(r["state"], r["state"])} for r in rows]

    def from_page_approve(args: dict[str, Any]) -> dict[str, Any]:
        post = ctx.store.post(_int(args)) or {}
        cards._withdraw(ctx, post.get("card_id"))  # noqa: SLF001
        return actions.approve(ctx, _int(args))

    def from_page_send_back(args: dict[str, Any]) -> dict[str, Any]:
        post = ctx.store.post(_int(args)) or {}
        cards._withdraw(ctx, post.get("card_id"))  # noqa: SLF001
        return actions.send_back(ctx, _int(args), str(args.get("note") or ""))

    def from_page_decline(args: dict[str, Any]) -> dict[str, Any]:
        post = ctx.store.post(_int(args)) or {}
        cards._withdraw(ctx, post.get("card_id"))  # noqa: SLF001
        return actions.decline(ctx, _int(args), str(args.get("reason") or ""))

    def rule_delete(args: dict[str, Any]) -> bool:
        ctx.store.conn.execute("DELETE FROM rule WHERE id=?", (_int(args),))
        ctx.store.conn.commit()
        return True

    out: dict[str, Callable[[dict[str, Any]], Any]] = {
        "pages.list": page_list,
        "pages.save": lambda a: pages.save_page(ctx.store, a, known=ctx.schedulers),
        "accounts.list": lambda a: lookup.accounts_list(ctx, a),
        "voice.fill": lambda a: voice.fill(ctx, a),
        "pages.pause": lambda a: actions.pause_page(ctx, a.get("id"), a.get("paused", True)),
        "pages.removal": lambda a: pages.removal(ctx.store, a.get("id")),
        "pages.remove": lambda a: actions.remove_page(ctx, a.get("id")),
        "post.approval": lambda a: actions.approval(ctx, _int(a)),
        "rules.list": lambda a: ctx.store.rules(a.get("page_id")),
        "rules.save": lambda a: pages.save_rule(ctx.store, a),
        "rules.delete": rule_delete,
        "pieces.list": piece_list,
        "piece.get": piece_view,
        "piece.retry": lambda a: plain.retry(ctx, _int(a)),
        "piece.drop": lambda a: actions.drop(ctx, _int(a)),
        "piece.draft_anyway": lambda a: actions.draft_anyway(ctx, _int(a)),
        "posts.list": post_list,
        "post.edit": lambda a: actions.edit(ctx, _int(a), title=a.get("title"),
                                            body=str(a.get("body") or "")),
        "post.approve": from_page_approve,
        "post.send_back": from_page_send_back,
        "post.decline": from_page_decline,
        "check.answer": lambda a: actions.answer(ctx, _int(a), str(a.get("answer") or "")),
        "post.book": lambda a: booking.book(ctx, _int(a), slot_ms=a.get("slot_ms")),
        "post.move": lambda a: booking.move(ctx, _int(a), _int(a, "slot_ms")),
        "post.cancel": lambda a: booking.cancel(ctx, _int(a)),
        "post.clear": lambda a: booking.clear_unsure(ctx, _int(a)),
        "post.book_again": lambda a: booking.book_again(ctx, _int(a)),
        "work.choose": lambda a: work.from_page(ctx, _int(a), str(a.get("action") or "")),
        "post.posted": lambda a: booking.posted_by_hand(ctx, _int(a)),
        "ideas.list": lambda a: from_reader.ideas(ctx),
        "idea.start": lambda a: from_reader.start(ctx, _int(a), a.get("page_id")),
        "idea.dismiss": lambda a: from_reader.dismiss(ctx, _int(a)),
    }
    for way, start in WAYS.items():
        out[f"new.{way}"] = (lambda fn: lambda a: fn(ctx, a))(start)
    out.update(screen.calls(ctx))
    out.update(scheduled.calls(ctx))
    return out
