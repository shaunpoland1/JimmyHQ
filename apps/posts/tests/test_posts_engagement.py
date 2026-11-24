"""What did well: figures SocialBu gives for each post that went out, kept per page as
plain numbers, ranked by what people did, and saying so when there are none."""

from __future__ import annotations

import pytest
from posts import engagement
from posts_service import DAY, KEY, Service, booked, upstream_post


@pytest.fixture
def sb(make_rig):
    rig = make_rig(settings={"scheduler": "socialbu"}, secrets={"socialbu_key": KEY})
    rig.service = Service(rig.core)
    return rig


def went_out(rig, likes=(3, 4), *, url=None):
    more = {"url": url} if url else {}
    post = booked(rig, **more)
    for (pid, aid), n in zip(((500, 1001), (501, 1002)), likes, strict=True):
        rig.service.on("GET", f"/posts/{pid}", upstream_post(
            pid, aid, published=True, insights=[{"type": "likes", "value": n},
                                                {"type": "comments", "value": 1},
                                                {"type": "impressions", "value": 900}]))
    rig.clock.advance(DAY + 3_600_000)  # past its time
    rig.core.run_job("reconcile")
    return rig.store.post(post["id"])


def test_the_figures_of_a_post_that_went_out_are_kept_per_page_as_plain_numbers(sb):
    post = went_out(sb)
    assert post["state"] == "went_out"
    found = engagement.what_did_well(sb.ctx, post["page_id"])
    assert found["said"] == ""
    [entry] = found["posts"]
    assert entry["post"] == post["id"]
    assert entry["figures"] == {"likes": 7.0, "comments": 2.0, "impressions": 1800.0}
    assert entry["interactions"] == 9.0  # who only saw it is not counted


def test_with_no_figures_what_did_well_says_so(sb):
    page = sb.page()
    found = engagement.what_did_well(sb.ctx, page["id"])
    assert found["posts"] == [] and found["said"] == engagement.NONE_YET


def test_figures_are_not_asked_for_past_the_days_in_the_setting(sb):
    went_out(sb)
    sb.service.calls.clear()
    sb.clock.advance(15 * DAY)
    sb.core.run_job("reconcile")
    assert sb.service.calls == []


def test_a_network_blip_while_collecting_figures_keeps_the_ones_there_were(sb):
    post = went_out(sb)
    sb.service.script.clear()
    sb.clock.advance(DAY)
    sb.core.run_job("reconcile")
    [entry] = engagement.what_did_well(sb.ctx, post["page_id"])["posts"]
    assert entry["figures"]["likes"] == 7.0


def test_what_people_did_is_ranked_and_a_total_where_given_stands_for_the_rest():
    assert engagement.interactions({"likes": 2, "shares": 1, "views": 50}) == 3.0
    assert engagement.interactions({"likes": 2, "total_interactions": 10}) == 10.0


def test_only_a_post_that_went_out_in_the_last_days_is_due_for_figures():
    now = 100 * DAY
    out = {"state": "went_out", "slot_ms": now - 2 * DAY}
    assert engagement.due_for_figures(out, now, 14)
    assert not engagement.due_for_figures(out, now, 1)
    assert not engagement.due_for_figures(out, now, 0)
    assert not engagement.due_for_figures({**out, "state": "booked"}, now, 14)
