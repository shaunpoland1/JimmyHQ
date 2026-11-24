"""The tools Posts offers anyone who holds no role: an agent with no work from Posts and an
outside client the owner let through the door. They only read, are bounded, never name an
account or a key, and are never counted against anyone's work. Sample data is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from posts import outside

MANIFEST = Path(__file__).resolve().parents[1] / "jimmyhq.app.json"
HIDDEN = "sk-test-0000-secret-value"
ACCOUNT = "acct-9876543210"
CLIENT = "client:test"
LONELY = "agent:nobody"  # a real agent that holds no role of Posts
START = 1_800_000_000_000
HOUR = 3_600_000


@pytest.fixture
def posts(make_rig):
    rig = make_rig(settings={"scheduler": "by_hand"}, secrets={"socialbu_key": HIDDEN})
    rig.garden = rig.page(register="Warm, plain and practical.\nNever shouty.",
                          accounts=[{"scheduler": "socialbu", "account_id": ACCOUNT,
                                     "label": "Main"}], hours=["08:00", "17:30"],
                          timezone="UTC")
    rig.shop = rig.page(name="Shop talk", register="Brisk.", hours=["09:00"])
    return rig


def put(rig, page, state, body, *, slot=None, **more):
    piece = rig.store.insert("piece", {"page_id": page["id"], "way": "link", "state": "closed",
                                       "created": START, "updated": START})
    return rig.store.insert("post", {"piece_id": piece, "page_id": page["id"],
                                     "body": body, "state": state, "slot_ms": slot,
                                     "created": START, "updated": START + 5, **more})


def call(rig, name, args=None, agent=CLIENT, role=None):
    return rig.core.tool(name, args or {}, agent=agent, role=role)


def dump(said) -> str:
    return json.dumps(said, ensure_ascii=False)


# --- shape -----------------------------------------------------------------------------------


def test_brands_gives_each_brands_name_voice_times_booking_tool_and_paused_state(posts):
    posts.core.call("pages.pause", {"id": posts.shop["id"], "paused": True})
    said = call(posts, "brands")
    assert said["ok"] and said["result"] == "2 brands."
    shown = said["shown"]
    assert "Garden notes" in shown and "Warm, plain and practical. Never shouty." in shown
    assert "Posts at: 08:00, 17:30 (UTC)" in shown and "Booked through: By hand" in shown
    assert "Shop talk (paused: no new work)" in shown
    assert "Garden notes (paused" not in shown


def test_the_queue_lists_approved_posts_that_have_not_gone_out_with_state_time_and_text(posts):
    put(posts, posts.garden, "booked", "Plant the bulbs now.", slot=START + HOUR)
    put(posts, posts.shop, "queued", "Sale on spades.")
    put(posts, posts.garden, "unsure", "Mulch matters.", slot=START + 2 * HOUR)
    said = call(posts, "queue")
    shown = said["shown"]
    assert said["ok"] and said["result"] == "3 waiting or booked."
    assert shown.index("Mulch matters.") < shown.index("Plant the bulbs now.")
    assert "booked, for 2027-01-15 09:00 UTC" in shown
    assert "unsure, for 2027-01-15 10:00 UTC" in shown
    assert "queued, for no time yet" in shown and "Shop talk" in shown


def test_the_queue_leaves_out_drafts_that_wait_for_approval_and_what_went_out(posts):
    put(posts, posts.garden, "draft", "A DRAFT NOBODY APPROVED")
    put(posts, posts.garden, "revising", "A REVISED DRAFT")
    put(posts, posts.garden, "declined", "A DECLINED ONE")
    put(posts, posts.garden, "went_out", "ALREADY OUT", slot=START)
    shown = call(posts, "queue")["shown"]
    assert "Nothing is waiting or booked." in shown
    for left_out in ("NOBODY APPROVED", "REVISED DRAFT", "DECLINED", "ALREADY OUT"):
        assert left_out not in shown


def test_went_out_lists_recent_posts_newest_first_with_figures_summed_over_accounts(posts):
    older = put(posts, posts.garden, "went_out", "Older post.", slot=START)
    newer = put(posts, posts.shop, "went_out", "Newer post.", slot=START + HOUR)
    for account, likes in (("a1", 3), ("a2", 4)):
        posts.store.conn.execute(
            "INSERT INTO figure (post_id, page_id, account, metric, value, seen) VALUES "
            "(?, ?, ?, 'likes', ?, ?)", (newer, posts.shop["id"], account, likes, START))
    posts.store.conn.commit()
    shown = call(posts, "went_out")["shown"]
    assert shown.index("Newer post.") < shown.index("Older post.")
    assert "Figures: likes 7" in shown and "Figures: none reported" in shown
    assert f"Post {older}, Garden notes, 2027-01-15 08:00 UTC" in shown
    assert "a1" not in shown and "a2" not in shown


def test_a_brand_number_narrows_the_queue_and_what_went_out_to_that_brand(posts):
    put(posts, posts.garden, "queued", "Garden one.")
    put(posts, posts.shop, "queued", "Shop one.")
    put(posts, posts.shop, "went_out", "Shop out.", slot=START)
    only = call(posts, "queue", {"brand": posts.shop["id"]})
    assert "Shop one." in only["shown"] and "Garden one." not in only["shown"]
    assert only["result"] == "1 waiting or booked."
    out = call(posts, "went_out", {"brand": posts.garden["id"]})
    assert "Shop out." not in out["shown"] and "Nothing has gone out" in out["shown"]


def test_with_nothing_yet_each_tool_says_so_in_words(posts):
    assert "Nothing is waiting" in call(posts, "queue")["shown"]
    assert "Nothing has gone out" in call(posts, "went_out")["shown"]


def test_with_no_brands_at_all_brands_says_the_owner_has_added_none(make_rig):
    rig = make_rig()
    said = call(rig, "brands")
    assert said["ok"] and "no brands yet" in said["shown"]


# --- bounds ----------------------------------------------------------------------------------


def test_a_list_holds_at_most_the_limit_and_says_how_many_were_left_out(posts):
    for n in range(8):
        put(posts, posts.garden, "queued", f"Post number {n}.", slot=START + n * HOUR)
    said = call(posts, "queue", {"limit": 3})
    assert said["shown"].count("- Post ") == 3
    assert "5 more not shown here: 3 of 8" in said["shown"]
    assert said["result"] == "8 waiting or booked."


def test_without_a_limit_ten_rows_come_and_never_more_than_the_largest_limit(posts):
    for n in range(40):
        put(posts, posts.garden, "went_out", f"Post {n}.", slot=START + n)
    assert call(posts, "went_out")["shown"].count("- Post ") == outside.DEFAULT
    biggest = call(posts, "went_out", {"limit": outside.MOST})["shown"]
    assert biggest.count("- Post ") <= outside.MOST


def test_one_posts_words_and_the_whole_answer_are_cut_and_the_cut_is_said(posts):
    for n in range(outside.MOST):
        put(posts, posts.garden, "went_out", "word " * 400, slot=START + n)
    said = call(posts, "went_out", {"limit": outside.MOST})
    assert len(said["shown"]) <= int(posts.ctx.cfg().tool_page_chars)
    assert "(cut)" in said["shown"] and "more not shown here" in said["shown"]


@pytest.mark.parametrize("bad", [0, 26, -1, "ten", True, 2.5, [3]])
def test_a_limit_that_is_not_a_whole_number_from_one_to_the_largest_is_refused_plainly(
        posts, bad):
    said = call(posts, "brands", {"limit": bad})
    assert said["ok"] is False
    assert "Nothing was read" in said["result"] and "from 1 to 25" in said["result"]


@pytest.mark.parametrize("bad", ["x", 999, True])
def test_a_brand_that_is_not_there_is_refused_plainly_and_nothing_is_read(posts, bad):
    said = call(posts, "queue", {"brand": bad})
    assert said["ok"] is False and "Nothing was read" in said["result"]


# --- what is never given ---------------------------------------------------------------------


def test_no_account_key_or_scheduler_id_appears_in_anything_the_tools_say(posts):
    put(posts, posts.garden, "booked", "Booked text.", slot=START, scheduler="socialbu",
        card_id="card-secret-id", note="owner's private note")
    out = put(posts, posts.garden, "went_out", "Out text.", slot=START, scheduler="socialbu",
              note="owner's private note")
    posts.store.insert("upstream", {"post_id": out, "account": "socialbu:" + ACCOUNT,
                                    "external_id": "ext-7777", "state": "published"})
    posts.store.conn.execute(
        "INSERT INTO figure (post_id, page_id, account, metric, value, seen) VALUES "
        f"({out}, {posts.garden['id']}, '{ACCOUNT}', 'likes', 2, {START})")
    posts.store.conn.commit()
    text = " ".join(dump(call(posts, name, args)) for name in outside.READING
                    for args in ({}, {"limit": 25}))
    for forbidden in (HIDDEN, ACCOUNT, "ext-7777", "card-secret-id", "private note",
                      "account_id", "external", "socialbu", "secret"):
        assert forbidden not in text, forbidden


def test_the_tools_are_the_same_for_a_client_and_an_agent_with_no_role(posts):
    put(posts, posts.garden, "queued", "Same for all.", slot=START)
    for name in outside.READING:
        assert call(posts, name, agent=CLIENT) == call(posts, name, agent=LONELY)


# --- no work, no steps, nothing written -------------------------------------------------------


def test_calling_them_counts_no_step_credits_nobody_and_writes_nothing(posts):
    put(posts, posts.garden, "queued", "Text.", slot=START)
    before = {t: posts.store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
              for t in ("agent_step", "agent_work", "credit", "turn", "piece", "post", "meta")}
    changes = posts.store.conn.total_changes
    for agent in (CLIENT, LONELY):
        for name in outside.READING:
            assert call(posts, name, agent=agent)["ok"]
    after = {t: posts.store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in before}
    assert after == before and posts.store.conn.total_changes == changes
    assert posts.core.work == {}


def test_they_reach_nothing_outside_posts_no_model_no_network_no_task(posts):
    put(posts, posts.garden, "queued", "Text.", slot=START)
    posts.core.replies.clear()
    posts.core.pages.clear()
    for name in outside.READING:
        assert call(posts, name)["ok"]
    assert posts.core.replies == [] and posts.core.pages == {}


def test_none_of_them_belongs_to_a_role_and_a_role_tool_still_asks_for_its_role(posts):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    in_roles = {t for r in manifest["roles"] for t in r["tools"]}
    declared = {t["name"]: t["effect"] for t in manifest["tools"]}
    for name in outside.NAMES:
        assert name not in in_roles and declared[name] == "read"
    said = call(posts, "submit_draft", {"piece": 1, "body": "x"}, agent="agent:wren",
                role="researcher")
    assert said["ok"] is False and "belongs to the writer" in said["shown"]
    said = call(posts, "recent", {}, agent="agent:wren", role="writer")
    assert said["ok"] is False and "belongs to the researcher" in said["shown"]
