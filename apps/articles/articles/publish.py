"""The one publishing path. Every publication, whoever asks (approval, the page's button,
later an agent's ladder row), goes through ``publish``:

1. the pre-checks, each a named function: the article is approved; no earlier attempt is
   still waiting or unsure; the site has what its publisher needs;
2. the attempt is recorded, and ``publish.attempted`` written, before anything is sent;
3. the publisher is asked once. A definite no puts the article back to approved with the
   reason. No answer makes it ``unsure``, which refuses another try until the owner says
   they looked and it is not there. Nothing ever retries by itself.

A publisher that sends nothing out (Practice, By hand) publishes as soon as the owner
approves. One that does (WordPress) waits for the owner's Publish click, unless the owner
lets Articles publish alone on the ladder.

An edit after sending (``edit_sent``) is kept as a new version here, then put into what was
sent by the publisher's ``update`` when it has one (WordPress updates its draft while it is
still a draft), recorded the same way: attempt first, one try, a definite answer or
unsure. When it cannot be sent, the owner is told plainly that the site was not changed.
"""

from __future__ import annotations

from typing import Any

from articles import render, states
from articles.context import Ctx
from articles.publishers import Outcome, Publication


class PublishError(ValueError):
    """Nothing was sent. The message says why and what to do instead."""


# --- the pre-checks, one function each; None means it passes --------------------------------


def must_be_approved(piece: dict[str, Any]) -> str | None:
    if piece["state"] == "approved":
        return None
    return (f"This article is {states.WORDS.get(piece['state'], piece['state'])}, and only an "
            "approved article can be published. Nothing was sent.")


def no_open_attempt(attempts: list[dict[str, Any]]) -> str | None:
    """Rule ``one attempt at a time``: an attempt that got no answer may have reached the
    site, so another is refused until the owner has looked."""
    for attempt in attempts:
        if attempt["outcome"] == "unsure":
            return ("An earlier attempt to publish this article got no answer, so it may be "
                    "on the site already. Nothing was sent. Look on the site, then press "
                    "\"I checked, it is not there\" before trying again.")
        if attempt["outcome"] == "pending":
            return ("An attempt to publish this article is still waiting for its answer. "
                    "Nothing was sent. Wait for it to finish.")
    return None


def site_is_ready(publisher: Any, site: dict[str, Any]) -> str | None:
    lacks = publisher.needs(site)
    return f"{lacks} Nothing was sent." if lacks else None


def prechecks(ctx: Ctx, piece: dict[str, Any], publisher: Any,
              site: dict[str, Any]) -> list[str]:
    found = [must_be_approved(piece), no_open_attempt(ctx.store.attempts(piece["id"])),
             site_is_ready(publisher, site)]
    return [f for f in found if f]


# --- what is sent ---------------------------------------------------------------------------


def publication(ctx: Ctx, piece: dict[str, Any], site: dict[str, Any]) -> Publication:
    revision = ctx.store.revision(piece["id"]) or {}
    sources = {s["n"]: s for s in ctx.store.sources(piece["id"])}
    style = str(ctx.cfg().cite_style)
    body = render.final_html(revision["title"], revision["body"], sources, cite_style=style,
                             with_title=False)
    page = render.final_html(revision["title"], revision["body"], sources, cite_style=style)
    from articles import header  # noqa: PLC0415 - header imports render, as this does

    image = header.for_publication(ctx, piece["id"])
    return Publication(piece_id=piece["id"], title=revision["title"], html=body,
                       markdown=render.to_markdown(page), site=site,
                       header=image[0] if image else None,
                       header_type=image[1] if image else "image/png")


def export(ctx: Ctx, piece_id: int, form: str) -> dict[str, Any]:
    """The article as clean HTML or Markdown, to copy or download."""
    piece = ctx.store.piece(piece_id)
    if piece is None or not piece["revision"]:
        raise PublishError("This article has no text yet. Nothing was made.")
    site = ctx.store.site(piece["site_id"]) or {}
    revision = ctx.store.revision(piece_id) or {}
    sources = {s["n"]: s for s in ctx.store.sources(piece_id)}
    page = render.final_html(revision["title"], revision["body"], sources,
                             cite_style=str(ctx.cfg().cite_style))
    if form == "html":
        return {"text": page, "type": "text/html", "name": f"article-{piece_id}.html",
                "site": site.get("name", "")}
    if form == "markdown":
        return {"text": render.to_markdown(page), "type": "text/markdown",
                "name": f"article-{piece_id}.md", "site": site.get("name", "")}
    raise PublishError("Choose HTML or Markdown. Nothing was made.")


# --- the path -------------------------------------------------------------------------------


def _ask(publisher: Any, item: Publication, external: dict[str, Any] | None = None) -> Outcome:
    """One call to the publisher (an update of what was sent, with ``external``). Anything
    but a definite answer is unsure."""
    try:
        outcome = (publisher.publish(item) if external is None
                   else publisher.update(item, external))
    except Exception as error:  # noqa: BLE001 - no answer is an answer: unsure
        message = getattr(error, "message", None) or str(error)
        return Outcome("unsure", f"The site gave no answer ({message}). Look on the site "
                       "before trying again.")
    if not isinstance(outcome, Outcome) or outcome.kind not in ("sent", "refused", "unsure"):
        return Outcome("unsure", "The site's answer could not be read.")
    return outcome


def publish(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    """Publish an approved article through its site's publisher, once."""
    store = ctx.store
    piece = store.piece(piece_id)
    if piece is None:
        raise PublishError("There is no such article. Nothing was sent.")
    site = store.site(piece["site_id"]) or {}
    publisher = ctx.publishers.get(site.get("publisher") or "practice")
    if publisher is None:
        raise PublishError("This site's publisher is not known. Nothing was sent. Choose "
                           "Practice, By hand or WordPress for it under Sites.")
    refused = prechecks(ctx, piece, publisher, site)
    if refused:
        raise PublishError(" ".join(dict.fromkeys(refused)))
    item = publication(ctx, piece, site)
    attempt_id = store.insert("attempt", {"piece_id": piece_id, "publisher": publisher.id,
                                          "started": ctx.now(), "outcome": "pending"})
    ctx.tape("publish.attempted", {"piece": piece_id, "attempt": attempt_id,
                                   "publisher": publisher.id}, subject=f"piece:{piece_id}")
    store.move(piece_id, "publishing")
    outcome = _ask(publisher, item)
    store.update("attempt", attempt_id, {"outcome": outcome.kind, "finished": ctx.now(),
                                         "detail": outcome.detail[:500],
                                         "external": outcome.external})
    ctx.tape("publish.result", {"piece": piece_id, "attempt": attempt_id,
                                "outcome": outcome.kind}, subject=f"piece:{piece_id}")
    new = {"sent": "sent", "refused": "approved", "unsure": "unsure"}[outcome.kind]
    moved = store.move(piece_id, new, error="" if outcome.kind == "sent" else outcome.detail)
    store.add_turn(piece_id, "publishing", f"{publisher.label}: {outcome.detail}")
    ctx.emit()
    return {**moved, "said": outcome.detail}


def after_approval(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    """Approval publishes at once when nothing leaves this computer, or when the owner
    lets Articles publish alone; otherwise the article waits for the Publish click."""
    piece = ctx.store.piece(piece_id) or {}
    site = ctx.store.site(piece.get("site_id")) or {}
    publisher = ctx.publishers.get(site.get("publisher") or "practice")
    if publisher is not None and publisher.sends_out:
        try:
            level = ctx.app.ladder.check("publish", "send", "publishing")
        except Exception:  # noqa: BLE001 - when it cannot be asked, wait for the owner
            level = "approve"
        if level != "alone":
            return {**piece, "said": f"Approved. Press Publish to send it to {publisher.label} "
                    "as a draft."}
    try:
        return publish(ctx, piece_id)
    except PublishError as error:
        ctx.store.update("piece", piece_id, {"error": str(error)})
        return {**(ctx.store.piece(piece_id) or {}), "said": f"Approved, but not published: "
                f"{error}"}


def sent_external(attempts: list[dict[str, Any]]) -> tuple[int, dict[str, Any]] | None:
    """Rule ``an_edit_goes_where_the_article_went``: the last attempt that was sent, with
    what the site said it made, or None."""
    for attempt in reversed(attempts):
        if attempt["outcome"] == "sent":
            return attempt["id"], dict(attempt["external"] or {})
    return None


def edit_sent(ctx: Ctx, piece_id: int, *, title: Any, body: Any) -> dict[str, Any]:
    """The owner's edit of an article already sent to its site: kept here as a new
    version, checked, and put into what was sent when its publisher can; otherwise the
    answer says plainly that the site was not changed."""
    from articles import checked  # noqa: PLC0415 - checked imports this package's parts

    store = ctx.store
    piece = store.piece(piece_id)
    if piece is None or piece["state"] != "sent":
        raise PublishError("Only an article that was sent to its site, and is not yet marked "
                           "published, can be edited and sent again. Nothing was changed.")
    if not str(body or "").strip():
        raise PublishError("You left the article empty. Nothing was changed.")
    waiting = no_open_attempt(store.attempts(piece_id))
    if waiting:
        raise PublishError(waiting.replace("Nothing was sent.", "Nothing was changed."))
    before = store.revision(piece_id) or {}
    new = checked.new_revision(ctx, piece, title=title or before.get("title"), body=body,
                               kind="edit")
    checked.run(ctx, new["id"])
    site = store.site(piece["site_id"]) or {}
    publisher = ctx.publishers.get(site.get("publisher") or "practice")
    earlier = sent_external(store.attempts(piece_id))
    if publisher is None or not hasattr(publisher, "update") or earlier is None:
        label = publisher.label if publisher is not None else "the site"
        said = (f"Your edit is kept here as a new version. It was not sent to {label}: "
                "nothing on your site was changed. Copy it there by hand if you want it.")
        store.add_turn(piece_id, "sent", said, role="owner")
        return {**(store.piece(piece_id) or {}), "said": said}
    item = publication(ctx, store.piece(piece_id) or piece, site)
    attempt_id = store.insert("attempt", {"piece_id": piece_id, "publisher": publisher.id,
                                          "started": ctx.now(), "outcome": "pending",
                                          "external": {"updates": earlier[0]}})
    ctx.tape("publish.attempted", {"piece": piece_id, "attempt": attempt_id,
                                   "publisher": publisher.id}, subject=f"piece:{piece_id}")
    outcome = _ask(publisher, item, earlier[1])
    store.update("attempt", attempt_id, {
        "outcome": outcome.kind, "finished": ctx.now(), "detail": outcome.detail[:500],
        "external": {**outcome.external, "updates": earlier[0]}})
    ctx.tape("publish.result", {"piece": piece_id, "attempt": attempt_id,
                                "outcome": outcome.kind}, subject=f"piece:{piece_id}")
    store.add_turn(piece_id, "sent", f"{publisher.label}: {outcome.detail}", role="owner")
    ctx.emit()
    return {**(store.piece(piece_id) or {}), "said": outcome.detail}


def clear_unsure(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    """The owner looked on the site and the article is not there: it may be tried again.
    For an edit sent after the article (the article is still sent), the edit may be sent
    again."""
    piece = ctx.store.piece(piece_id)
    if piece is not None and piece["state"] == "sent" and any(
            a["outcome"] == "unsure" for a in ctx.store.attempts(piece_id)):
        for attempt in ctx.store.attempts(piece_id):
            if attempt["outcome"] == "unsure":
                ctx.store.update("attempt", attempt["id"], {"outcome": "cleared"})
                ctx.tape("publish.result", {"piece": piece_id, "attempt": attempt["id"],
                                            "outcome": "cleared"}, subject=f"piece:{piece_id}")
        ctx.store.add_turn(piece_id, "sent", "You looked on the site: the edit was not there.",
                           role="owner")
        return ctx.store.piece(piece_id) or {}
    if piece is None or piece["state"] != "unsure":
        raise PublishError("Only an article whose publishing got no answer can be cleared. "
                           "Nothing was changed.")
    for attempt in ctx.store.attempts(piece_id):
        if attempt["outcome"] == "unsure":
            ctx.store.update("attempt", attempt["id"], {"outcome": "cleared"})
            ctx.tape("publish.result", {"piece": piece_id, "attempt": attempt["id"],
                                        "outcome": "cleared"}, subject=f"piece:{piece_id}")
    ctx.store.add_turn(piece_id, "unsure", "You looked on the site and it was not there.",
                       role="owner")
    return ctx.store.move(piece_id, "approved", error="")


def mark_published(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    """The owner pressed Published: it is live on the site."""
    piece = ctx.store.piece(piece_id)
    if piece is None or piece["state"] not in ("sent", "unsure"):
        raise PublishError("Only an article that was sent to its site can be marked "
                           "published. Nothing was changed.")
    if piece["state"] == "unsure":
        for attempt in ctx.store.attempts(piece_id):
            if attempt["outcome"] == "unsure":
                ctx.store.update("attempt", attempt["id"], {"outcome": "sent"})
    moved = ctx.store.move(piece_id, "published")
    ctx.store.add_turn(piece_id, "sent", "You marked it published.", role="owner")
    ctx.tape("piece.published", {"piece": piece_id}, subject=f"piece:{piece_id}")
    return moved
