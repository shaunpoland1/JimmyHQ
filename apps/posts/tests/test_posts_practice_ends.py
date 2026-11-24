"""A post booked on Practice ends by itself once its time has passed: it moves to went out
with a note that nothing was sent, and is treated as real only where trying things out
needs it (overlap, the daily limit); never as evidence, a figure or a post for the feed."""

from __future__ import annotations

from posts import engagement, reconcile
from posts_rig import ARTICLE_TEXT
from test_posts_booking import BODY
from test_posts_join import ROLES, well

HOUR = 3_600_000
DAY = 24 * HOUR


def practice_post(rig, page=None):
    piece = rig.through(page or rig.page(), BODY)
    [card] = rig.cards_of("draft")
    rig.core.decide(card["card_id"], "approve")
    return rig.store.post_of(piece["id"])


def after_its_time(rig, post):
    rig.clock.now = post["slot_ms"] + HOUR
    return rig.core.run_job("reconcile")


def test_a_practice_post_stays_booked_until_its_time_has_passed(make_rig):
    rig = make_rig()
    post = practice_post(rig)
    rig.clock.now = post["slot_ms"] - HOUR
    rig.core.run_job("reconcile")
    assert rig.store.post(post["id"])["state"] == "booked"


def test_a_practice_post_moves_to_went_out_with_a_note_that_nothing_was_sent(make_rig):
    rig = make_rig()
    post = practice_post(rig)
    after_its_time(rig, post)
    done = rig.store.post(post["id"])
    assert done["state"] == "went_out" and done["scheduler"] == "practice"
    assert done["note"] == reconcile.PRACTICE_NOTE
    assert "Nothing was sent" in done["note"]


def test_a_practice_post_that_ended_is_never_told_to_the_posted_feed(make_rig):
    rig = make_rig()
    post = practice_post(rig)
    after_its_time(rig, post)
    assert "app.posts.posted" not in [e["kind"] for e in rig.core.events]


def test_a_post_booked_by_hand_waits_for_the_owner_however_late_it_is(make_rig):
    rig = make_rig(settings={"scheduler": "by_hand"})
    post = practice_post(rig)
    after_its_time(rig, post)
    assert rig.store.post(post["id"])["state"] == "booked"


def test_a_practice_post_that_ended_gets_no_figures_asked_for(make_rig):
    rig = make_rig()
    post = practice_post(rig)
    after_its_time(rig, post)
    assert engagement.collect(rig.ctx) == 0
    assert rig.store.all("SELECT * FROM figure") == []


def test_the_went_out_tool_leaves_out_posts_that_only_went_out_on_practice(make_rig):
    rig = make_rig()
    post = practice_post(rig)
    after_its_time(rig, post)
    said = rig.core.tool("went_out", {}, agent="client:test", role=None)
    assert said["result"] == "0 went out." and BODY not in said["shown"]


def test_a_practice_approval_still_counts_as_an_approval_in_what_did_well_and_has_no_figures(
        make_rig):
    rig = make_rig(roles=ROLES)
    post = practice_post(rig)
    after_its_time(rig, post)
    shown = well(rig, post["page_id"])
    assert "approved 1" in shown and "approved: Some 1,240 homes" in shown
    assert "How posts did" not in shown and "no figures" in shown


def test_a_practice_post_that_ended_still_counts_for_overlap_while_trying_things_out(make_rig):
    rig = make_rig()
    page = rig.page()
    post = practice_post(rig, page)
    after_its_time(rig, post)
    rig.store.update("post", post["id"], {"body": ARTICLE_TEXT})
    rig.link(page)
    rig.advance(3)
    assert rig.cards_of("overlap")
