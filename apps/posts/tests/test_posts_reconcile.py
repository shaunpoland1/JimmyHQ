"""Reconcile, against canned SocialBu answers: posts marked as sent, times changed there
picked up, failed and vanished posts told to the owner with what the service said, unsure
bookings looked for, a network blip changing nothing, and one card for a stuck post."""

from __future__ import annotations

import json

import pytest
from posts import reconcile
from posts.reconcile import Verdict, judge
from posts_rig import START
from posts_service import (
    DAY,
    HOUR,
    KEY,
    SLOT,
    Service,
    booked,
    created,
    no_answer,
    queued,
    reply,
    upstream_post,
)

MINUTE = 60_000


@pytest.fixture
def sb(make_rig):
    rig = make_rig(settings={"scheduler": "socialbu"}, secrets={"socialbu_key": KEY})
    rig.service = Service(rig.core)
    return rig


def run(rig, after_ms: int = 31 * MINUTE) -> str:
    rig.clock.advance(after_ms)
    return rig.core.run_job("reconcile")


def both(rig, **more):
    for pid, aid in ((500, 1001), (501, 1002)):
        rig.service.on("GET", f"/posts/{pid}", upstream_post(pid, aid, **more))


def notices(rig):
    return [c for c in rig.cards_of("notice")]


def everything(rig) -> str:
    """Every row Posts keeps and every line it wrote, as one text, to compare."""
    tables = ["post", "attempt", "upstream", "figure", "piece"]
    rows = {t: rig.store.all(f"SELECT * FROM {t} ORDER BY id") for t in tables}  # noqa: S608
    return json.dumps([rows, rig.core.events, sorted(rig.core.cards)], sort_keys=True,
                      default=str)


def test_a_post_is_marked_as_sent_once_every_copy_went_out(sb):
    post = booked(sb)
    both(sb, published=True)
    run(sb)
    after = sb.store.post(post["id"])
    assert after["state"] == "went_out" and after["note"] == "It went out on SocialBu."
    kinds = [e["kind"].rsplit(".", 2)[-2:] for e in sb.core.events][-2:]
    assert kinds == [["post", "went_out"], ["posts", "posted"]]


def test_a_post_out_on_one_account_and_waiting_on_another_is_left_as_it_is(sb):
    post = booked(sb)
    sb.service.on("GET", "/posts/500", upstream_post(500, 1001, published=True))
    sb.service.on("GET", "/posts/501", upstream_post(501, 1002))
    run(sb)
    assert sb.store.post(post["id"])["state"] == "booked"


def test_a_time_changed_on_socialbu_is_picked_up(sb):
    post = booked(sb)
    both(sb, publish_at="2027-01-16 10:30:00")
    run(sb)
    after = sb.store.post(post["id"])
    assert after["slot_ms"] == SLOT + 2 * HOUR + 30 * MINUTE and after["state"] == "booked"
    assert "changed on SocialBu" in after["note"]


def test_copies_holding_different_times_there_are_called_out_of_step(sb):
    post = booked(sb)
    sb.service.on("GET", "/posts/500", upstream_post(500, 1001))
    sb.service.on("GET", "/posts/501", upstream_post(501, 1002, publish_at="2027-01-16 09:00:00"))
    run(sb)
    after = sb.store.post(post["id"])
    assert after["out_of_step"] == 1 and "different times" in after["note"]


def test_a_post_socialbu_says_failed_is_failed_and_shown_with_what_it_said(sb):
    post = booked(sb)
    sb.service.on("GET", "/posts/500", upstream_post(500, 1001, error="Token expired for "
                                                                       "this page."))
    sb.service.on("GET", "/posts/501", upstream_post(501, 1002))
    run(sb)
    after = sb.store.post(post["id"])
    assert after["state"] == "failed" and "Token expired for this page." in after["note"]
    [card] = notices(sb)
    assert card["title"] == "A post did not go out" and "Token expired" in card["body"]
    assert any(e["kind"].endswith("post.failed") for e in sb.core.events)


def test_a_post_that_vanished_before_its_time_is_missing_and_the_owner_is_told(sb):
    post = booked(sb)
    sb.service.on("GET", "/posts/500", reply(404, {"message": "Not found."}))
    sb.service.on("GET", "/posts/501", upstream_post(501, 1002))
    run(sb)
    after = sb.store.post(post["id"])
    assert after["state"] == "missing" and "no longer on SocialBu" in after["note"]
    assert [c["title"] for c in notices(sb)] == ["A booked post vanished"]


def test_a_network_blip_during_reconcile_changes_nothing(sb):
    post = booked(sb)
    unsure = queued(sb, page=sb.store.pages()[0], url="https://news.example.com/2027/kitchens")
    sb.service.on("POST", "/posts", no_answer())
    sb.core.call("post.book", {"id": unsure["id"], "slot_ms": SLOT + DAY})
    sb.service.on("GET", "/posts/500", upstream_post(500, 1001, published=True))
    sb.service.on("GET", "/posts/501", no_answer())
    sb.service.on("GET", "/posts?perPage=100&page=1", reply(502, text="Bad gateway"))
    before = everything(sb)
    run(sb)
    assert everything(sb) == before
    assert sb.store.post(post["id"])["state"] == "booked"
    assert sb.store.post(unsure["id"])["state"] == "unsure"
    assert ("POST", "/posts") not in sb.service.asked()[-3:]  # nothing booked again


def test_an_unsure_booking_found_on_socialbu_for_every_account_is_booked(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", no_answer())
    sb.core.call("post.book", {"id": post["id"]})
    sb.service.on("GET", "/posts?perPage=100&page=1", reply(200, {
        "items": [{"id": 901, "account_id": 1002, "publish_at": "2027-01-16 08:00:00"},
                  {"id": 900, "account_id": 1001, "publish_at": "2027-01-16 08:00:00"},
                  {"id": 950, "account_id": 1001, "publish_at": "2027-01-17 08:00:00"}],
        "nextPage": None}))
    run(sb)
    after = sb.store.post(post["id"])
    assert after["state"] == "booked" and "found it on SocialBu" in after["note"]
    held = {u["account"]: u["external_id"] for u in sb.store.upstream(post["id"], live=True)}
    assert held == {"socialbu:1001": "900", "socialbu:1002": "901"}
    assert sb.store.attempts(post["id"])[-1]["outcome"] == "sent"
    assert sb.service.asked().count(("POST", "/posts")) == 1


def test_an_unsure_booking_not_on_socialbu_while_its_time_is_ahead_may_be_booked_again(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", no_answer())
    sb.core.call("post.book", {"id": post["id"]})
    sb.service.on("GET", "/posts?perPage=100&page=1", reply(200, {"items": [], "nextPage": 2}))
    sb.service.on("GET", "/posts?perPage=100&page=2", reply(200, {"items": [],
                                                                  "nextPage": None}))
    run(sb)
    after = sb.store.post(post["id"])
    assert after["state"] == "queued" and "it is not there" in after["note"]
    assert sb.service.asked().count(("POST", "/posts")) == 1  # never booked again by itself


def test_an_unsure_booking_found_on_only_some_accounts_is_booked_there_and_lacks_the_rest(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", created((900, 1001)))
    sb.core.call("post.book", {"id": post["id"]})
    sb.service.on("GET", "/posts?perPage=100&page=1", reply(200, {
        "items": [{"id": 900, "account_id": 1001, "publish_at": "2027-01-16 08:00:00"}],
        "nextPage": None}))
    run(sb)
    after = sb.store.post(post["id"])
    assert after["state"] == "booked" and "but not for Second" in after["note"]
    held = {u["account"]: u["external_id"] for u in sb.store.upstream(post["id"], live=True)}
    assert held == {"socialbu:1001": "900"}  # the copy it named, now confirmed by the list
    assert sb.service.asked().count(("POST", "/posts")) == 1


def test_an_unsure_booking_found_on_only_some_accounts_after_its_time_stays_unsure(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", created((900, 1001)))
    sb.core.call("post.book", {"id": post["id"]})
    sb.service.on("GET", "/posts?perPage=100&page=1", reply(200, {
        "items": [{"id": 900, "account_id": 1001, "publish_at": "2027-01-16 08:00:00"}],
        "nextPage": None}))
    run(sb, 2 * DAY)
    after = sb.store.post(post["id"])
    assert after["state"] == "unsure" and "for 1 of 2 accounts" in after["note"]
    assert "may have gone out there" in after["note"]


def test_a_list_that_fails_part_way_resolves_nothing(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", no_answer())
    sb.core.call("post.book", {"id": post["id"]})
    sb.service.on("GET", "/posts?perPage=100&page=1", reply(200, {"items": [], "nextPage": 2}))
    sb.service.on("GET", "/posts?perPage=100&page=2", reply(500, text="oops"))
    run(sb)
    assert sb.store.post(post["id"])["state"] == "unsure"


def test_a_post_stuck_past_the_setting_gets_one_card_and_only_one(make_rig):
    rig = make_rig(settings={"scheduler": "socialbu", "stuck_hours": 2},
                   secrets={"socialbu_key": KEY})
    rig.service = Service(rig.core)
    post = queued(rig)
    rig.service.on("POST", "/posts", no_answer())
    rig.core.call("post.book", {"id": post["id"]})
    rig.service.on("GET", "/posts?perPage=100&page=1", reply(503, text=""))
    run(rig)
    assert notices(rig) == []
    run(rig, 3 * HOUR)
    [card] = notices(rig)
    assert card["title"] == "1 post has been stuck for over 2 hours"
    assert f"Post #{post['id']}" in card["body"]
    run(rig, HOUR)
    assert len(notices(rig)) == 1


def test_reconcile_does_nothing_when_it_is_asked_again_sooner_than_its_setting(sb):
    booked(sb)
    both(sb)
    run(sb)
    asked = len(sb.service.calls)
    assert run(sb, MINUTE) == "Not due yet." and len(sb.service.calls) == asked


def test_practice_and_by_hand_posts_are_never_asked_about(make_rig):
    rig = make_rig()
    rig.service = Service(rig.core)
    rig.core.call("pages.save", {"name": "Garden notes", "hours": ["08:00"]})
    run(rig)
    assert rig.service.calls == []


# --- the rules ----------------------------------------------------------------------------


def copy(state="scheduled", slot=SLOT, said=""):
    return {"account": "socialbu:1001", "external_id": "500", "state": state, "slot_ms": slot,
            "said": said, "figures": None}


@pytest.mark.parametrize("copies, kind", [
    ([copy(), copy()], "same"),
    ([copy("published"), copy("published")], "went_out"),
    ([copy("published"), copy()], "going"),
    ([copy("failed", said="No."), copy("published")], "failed"),
    ([copy("gone"), copy()], "missing"),
    ([copy(slot=SLOT + HOUR), copy(slot=SLOT + HOUR)], "moved"),
    ([copy(), copy(slot=SLOT + HOUR)], "out_of_step"),
    ([], "same"),
])
def test_what_the_copies_say_is_judged_one_way(copies, kind):
    assert judge({"slot_ms": SLOT}, copies).kind == kind


def test_a_moved_verdict_carries_the_new_time():
    assert judge({"slot_ms": SLOT}, [copy(slot=SLOT + HOUR)]) == Verdict("moved",
                                                                       slot_ms=SLOT + HOUR)


def test_an_unsure_booking_is_found_by_account_and_minute():
    listing = [{"account": "socialbu:1001", "external_id": "9", "slot_ms": SLOT + 20_000},
               {"account": "socialbu:1002", "external_id": "8", "slot_ms": SLOT + MINUTE},
               {"account": "socialbu:4444", "external_id": "7", "slot_ms": SLOT}]
    assert reconcile.found_upstream(listing, ["socialbu:1001", "socialbu:1002"], SLOT) == {
        "socialbu:1001": "9"}


def test_a_post_is_stuck_only_when_troubled_for_longer_than_the_setting():
    old = START - 3 * HOUR
    assert reconcile.is_stuck({"state": "unsure", "out_of_step": 0, "updated": old}, START, 2)
    assert reconcile.is_stuck({"state": "booked", "out_of_step": 1, "updated": old}, START, 2)
    assert not reconcile.is_stuck({"state": "booked", "out_of_step": 0, "updated": old},
                                  START, 2)
    assert not reconcile.is_stuck({"state": "unsure", "out_of_step": 0, "updated": START},
                                  START, 2)


def test_reconcile_is_due_after_its_interval_or_on_its_first_run():
    assert reconcile.is_due(None, START, 30)
    assert reconcile.is_due(START - 30 * MINUTE, START, 30)
    assert not reconcile.is_due(START - 5 * MINUTE, START, 30)

