"""The whole way through with no agents: a pasted link becomes a checked draft on the
Bench, Approve queues it, and Practice books it without sending anything."""

from __future__ import annotations

from posts_rig import ARTICLE, ARTICLE_TEXT, draft_reply, ship

GOOD = ("1,240 homes in the valley added solar panels last year, 38% more than the year "
        "before. Installers have waiting lists of up to 14 weeks.")


def test_a_pasted_link_becomes_a_checked_draft_that_approve_queues_and_practice_books(rig):
    page = rig.page()
    rig.core.pages[ARTICLE] = ARTICLE_TEXT
    piece = rig.core.call("new.link", {"page_id": page["id"], "url": ARTICLE})
    assert piece["state"] == "reading"
    rig.core.replies += [draft_reply(GOOD, claims=[{"text": "homes", "sentences": [1, 2]}]),
                         ship()]
    rig.advance(4)  # read, overlap, draft, check
    [card] = rig.cards_of("draft")
    assert "Every check passed." in card["body"] or "warning" in card["body"]
    assert ARTICLE in card["body"]
    rig.core.decide(card["card_id"], "approve")
    post = rig.store.post_of(piece["id"])
    assert post["state"] == "booked" and post["scheduler"] == "practice"
    assert post["slot_ms"] > rig.clock.now
    [logged] = rig.store.all("SELECT * FROM practice_log")
    assert logged["would_send"]["text"].startswith("1,240 homes")
    assert not rig.core.fetched  # nothing left the building
    kinds = [e["kind"] for e in rig.core.events]
    assert kinds == ["app.posts.piece.started", "app.posts.post.decided",
                     "app.posts.booking.attempted", "app.posts.booking.result"]


def test_every_step_writes_one_human_line(rig):
    page = rig.page()
    rig.core.pages[ARTICLE] = ARTICLE_TEXT
    piece = rig.core.call("new.link", {"page_id": page["id"], "url": ARTICLE})
    rig.core.replies += [draft_reply(GOOD), ship()]
    rig.advance(4)
    steps = [t["step"] for t in rig.store.turns(piece["id"])]
    assert steps == ["proposed", "reading", "overlap_check", "writing", "in_check"]
    assert all(t["gloss"] for t in rig.store.turns(piece["id"]))
