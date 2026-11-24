"""Pausing and removing a brand, what approving a draft would do before it is done, and
the switch for the single-letter keys."""

from __future__ import annotations

import sqlite3

import pytest
from posts import pages
from posts.program import READS, calls
from posts.store import Store
from posts_rig import APP_ROOT, ARTICLE, ARTICLE_TEXT, Clock

ROLES = {"researcher": "Scout", "writer": "Wren", "checker": "Kit"}
SCOUT = "agent:scout"
BODY = "1,240 homes in the valley added solar panels last year."


def open_work(rig, role="researcher"):
    return [w for w in rig.core.work.values() if w["role"] == role and w["state"] == "open"]


def pause(rig, page, paused=True):
    return rig.core.call("pages.pause", {"id": page["id"], "paused": paused})


def offered(n: int = 1, url: str = ARTICLE) -> dict:
    return {"offer": n, "article": 10 + n, "url": url, "title": "Solar roofs spread",
            "topic": "Solar", "why": "Worth a look.", "text_ref": f"reader:article:{10 + n}",
            "when_ms": 0}


# --- pausing ----------------------------------------------------------------------------------


def test_a_paused_page_is_still_listed_and_says_it_is_paused(rig):
    page = rig.page()
    assert not page["paused"]
    assert pause(rig, page)["paused"]
    [seen] = rig.core.call("overview", {})["pages"]
    assert seen["id"] == page["id"] and seen["paused"]
    [listed] = rig.core.call("pages.list")["pages"]
    assert listed["paused"]


def test_a_paused_page_can_be_resumed_and_takes_new_work_again(rig):
    page = rig.page()
    pause(rig, page)
    assert not pause(rig, page, paused=False)["paused"]
    rig.core.pages[ARTICLE] = ARTICLE_TEXT
    assert rig.core.call("new.link", {"page_id": page["id"], "url": ARTICLE})["state"] == "reading"


def test_new_work_for_a_paused_page_is_refused_with_what_to_do_instead(rig):
    page = rig.page()
    pause(rig, page)
    for call, more in (("new.link", {"url": ARTICLE}), ("new.topic", {"topic": "Solar roofs"})):
        with pytest.raises(Exception, match="is paused, so nothing was started. Resume it"):
            rig.core.call(call, {"page_id": page["id"], **more})
    assert rig.store.all("SELECT id FROM piece") == []


def test_editing_a_page_does_not_resume_it(rig):
    page = rig.page()
    pause(rig, page)
    saved = rig.core.call("pages.save", {**{k: page[k] for k in ("id", "name", "hours")},
                                         "register": "Dry and brief."})
    assert saved["register"] == "Dry and brief." and saved["paused"]


def test_the_daily_researcher_ask_skips_a_paused_page(make_rig):
    rig = make_rig(roles=dict(ROLES))
    page = rig.page()
    other = rig.page(name="Kitchen notes")
    pause(rig, other)
    rig.core.run_job("watch")
    assert {w["subject"] for w in open_work(rig)} == {f"page:{page['id']}"}


def test_pausing_a_page_takes_back_what_the_researcher_was_asked_for_it(make_rig):
    rig = make_rig(roles=dict(ROLES))
    page = rig.page()
    rig.core.run_job("watch")
    assert len(open_work(rig)) == 1
    pause(rig, page)
    assert open_work(rig) == []
    rig.clock.advance(48 * 3_600_000)
    rig.core.run_job("watch")
    assert open_work(rig) == []


def test_the_researcher_is_not_shown_a_paused_page_and_cannot_propose_for_it(make_rig):
    rig = make_rig(roles=dict(ROLES))
    rig.core.pages[ARTICLE] = ARTICLE_TEXT
    page = rig.page()
    other = rig.page(name="Kitchen notes")
    pause(rig, other)
    shown = rig.core.tool("recent", {}, agent=SCOUT, role="researcher")["shown"]
    assert page["name"] in shown and other["name"] not in shown
    said = rig.core.tool("propose", {"page": other["id"], "url": ARTICLE, "angle": "An angle.",
                                     "why": "A reason."}, agent=SCOUT, role="researcher")
    assert not said["ok"] and "paused" in said["shown"]
    assert rig.store.all("SELECT id FROM piece") == []


def test_an_idea_from_reader_cannot_be_started_for_a_paused_page(make_rig):
    rig = make_rig(reads_reader=True)
    page = rig.page()
    pause(rig, page)
    rig.core.offer("reader.articles", offered())
    rig.core.run_job("intake")
    [idea] = rig.core.call("ideas.list")
    with pytest.raises(Exception, match="is paused, so nothing was started"):
        rig.core.call("idea.start", {"id": idea["id"], "page_id": page["id"]})
    assert len(rig.core.call("ideas.list")) == 1  # the idea still waits


def test_ideas_that_start_by_themselves_go_to_the_one_page_that_is_not_paused(make_rig):
    rig = make_rig(reads_reader=True, settings={"from_reader": "start"})
    page = rig.page()
    other = rig.page(name="Kitchen notes")
    pause(rig, other)
    rig.core.offer("reader.articles", offered())
    assert "Started 1 pieces" in rig.core.run_job("intake")
    [piece] = rig.store.all("SELECT page_id FROM piece")
    assert piece["page_id"] == page["id"]


def test_ideas_never_start_by_themselves_when_every_page_is_paused(make_rig):
    rig = make_rig(reads_reader=True, settings={"from_reader": "start"})
    pause(rig, rig.page())
    rig.core.offer("reader.articles", offered())
    rig.core.run_job("intake")
    assert rig.store.all("SELECT id FROM piece") == []
    assert len(rig.core.call("ideas.list")) == 1


def test_pausing_a_page_leaves_its_draft_and_its_booked_post_alone(rig):
    page = rig.page(hours=["08:00", "17:30"])
    rig.through(page, BODY)
    booked = rig.store.all("SELECT * FROM post")[0]
    rig.core.call("post.approve", {"id": booked["id"]})
    rig.through(page, "Installers report waiting lists of up to 14 weeks.",
                url="https://news.example.com/2027/waiting")
    pause(rig, page)
    states = {p["state"] for p in rig.store.all("SELECT state FROM post")}
    assert states == {"booked", "draft"}
    assert len(rig.cards_of("draft")) == 1


def test_a_database_made_before_pages_could_be_paused_gets_the_column(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE page (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
                 "register TEXT NOT NULL DEFAULT '', samples TEXT NOT NULL DEFAULT '', "
                 "timezone TEXT NOT NULL DEFAULT '', hours TEXT NOT NULL DEFAULT '[\"08:00\"]', "
                 "accounts TEXT NOT NULL DEFAULT '[]', tags TEXT NOT NULL DEFAULT '[]', "
                 "created INTEGER NOT NULL, updated INTEGER NOT NULL)")
    conn.execute("INSERT INTO page (name, created, updated) VALUES ('Garden notes', 1, 1)")
    conn.commit()
    store = Store(conn, Clock())
    try:
        assert [p["paused"] for p in store.pages()] == [0]
        assert len(store.active_pages()) == 1
    finally:
        conn.close()


# --- removing ---------------------------------------------------------------------------------


def test_a_page_with_a_post_queued_or_booked_is_not_removed_and_says_what_to_do(rig):
    page = rig.page()
    rig.through(page, BODY)
    post = rig.store.all("SELECT * FROM post")[0]
    rig.core.call("post.approve", {"id": post["id"]})
    assert rig.store.post(post["id"])["state"] == "booked"
    found = rig.core.call("pages.removal", {"id": page["id"]})
    assert found["on_the_way"] == 1
    assert "has 1 post queued or booked, so it was not removed" in found["refused"]
    assert "pause the brand instead" in found["refused"]
    with pytest.raises(Exception, match="so it was not removed"):
        rig.core.call("pages.remove", {"id": page["id"]})
    assert rig.store.page(page["id"]) is not None


@pytest.mark.parametrize("state", pages.ON_THE_WAY)
def test_every_state_a_post_has_on_its_way_out_keeps_its_page(rig, state):
    page = rig.page()
    rig.through(page, BODY)
    post = rig.store.all("SELECT * FROM post")[0]
    rig.store.update("post", post["id"], {"state": state})  # as booking would leave it
    assert rig.core.call("pages.removal", {"id": page["id"]})["refused"]


def test_asking_what_removing_would_do_changes_nothing(rig):
    page = rig.page()
    rig.through(page, BODY)
    before = rig.store.conn.total_changes
    found = rig.core.call("pages.removal", {"id": page["id"]})
    assert rig.store.conn.total_changes == before
    assert "pages.removal" in READS
    assert found["refused"] == "" and found["working"] == 1 and found["drafts"] == 1


def test_removing_a_page_drops_its_open_pieces_and_declines_their_drafts(rig):
    page = rig.page()
    rig.through(page, BODY)
    reading = rig.link(page, "https://news.example.com/2027/waiting")
    post = rig.store.all("SELECT * FROM post")[0]
    assert len(rig.cards_of("draft")) == 1
    done = rig.core.call("pages.remove", {"id": page["id"]})
    assert done["removed"] and done["working"] == 2 and done["drafts"] == 1
    assert rig.store.page(page["id"]) is None
    assert rig.store.post(post["id"])["state"] == "declined"
    assert rig.store.piece(reading["id"])["state"] == "dropped"
    assert rig.cards_of("draft") == []
    assert rig.core.call("overview", {})["pages"] == []


def test_removing_a_page_deletes_its_own_rules_and_lessons_and_keeps_the_rules_for_every_page(
        rig):
    page = rig.page()
    other = rig.page(name="Kitchen notes")
    rig.core.call("rules.save", {"page_id": page["id"], "kind": "phrase", "text": "game changer"})
    rig.core.call("rules.save", {"page_id": other["id"], "kind": "phrase", "text": "must-have"})
    rig.core.call("rules.save", {"page_id": None, "kind": "tag", "text": "#local"})
    rig.through(page, BODY)
    post = rig.store.all("SELECT * FROM post")[0]
    rig.core.call("post.decline", {"id": post["id"], "reason": "Wrong angle"})
    assert rig.store.all("SELECT id FROM lesson WHERE page_id=?", (page["id"],))
    found = rig.core.call("pages.removal", {"id": page["id"]})
    assert found["rules"] == 1 and found["kept"] == 1
    rig.core.call("pages.remove", {"id": page["id"]})
    assert [r["text"] for r in rig.store.all("SELECT text FROM rule ORDER BY id")] == [
        "must-have", "#local"]
    assert rig.store.all("SELECT id FROM lesson WHERE page_id=?", (page["id"],)) == []


def test_the_posts_of_a_removed_page_stay_in_their_lists(rig):
    page = rig.page()
    rig.through(page, BODY)
    post = rig.store.all("SELECT * FROM post")[0]
    rig.core.call("post.approve", {"id": post["id"]})
    rig.store.move_post(post["id"], "went_out")  # as reconcile would
    rig.core.call("pages.remove", {"id": page["id"]})
    seen = rig.core.call("overview", {})
    assert [p["state"] for p in seen["posts"]] == ["went_out"]
    assert seen["posts"][0]["page_id"] == page["id"] and seen["pages"] == []


def test_removing_a_page_takes_back_what_the_researcher_was_asked_for_it(make_rig):
    rig = make_rig(roles=dict(ROLES))
    page = rig.page()
    rig.core.run_job("watch")
    assert len(open_work(rig)) == 1
    rig.core.call("pages.remove", {"id": page["id"]})
    assert open_work(rig) == []


def test_a_removed_pages_name_can_be_used_again(rig):
    page = rig.page()
    rig.core.call("pages.remove", {"id": page["id"]})
    assert rig.page()["name"] == page["name"]


def test_removing_or_pausing_a_page_that_is_not_there_says_nothing_happened(rig):
    with pytest.raises(Exception, match="no such brand. Nothing was removed"):
        rig.core.call("pages.remove", {"id": 99})
    with pytest.raises(Exception, match="no such brand. Nothing was changed"):
        rig.core.call("pages.pause", {"id": 99})


def test_pausing_resuming_and_removing_a_page_tell_the_open_pages_to_draw_again(rig):
    page = rig.page()
    for call, args in (("pages.pause", {"paused": True}), ("pages.pause", {"paused": False}),
                       ("pages.remove", {})):
        before = rig.ctx.emits
        rig.core.call(call, {"id": page["id"], **args})
        assert rig.ctx.emits > before, call


def test_the_page_has_the_calls_to_pause_remove_and_ask_first(rig):
    assert {"pages.pause", "pages.removal", "pages.remove", "post.approval"} <= set(calls(rig.ctx))


# --- what approving would do ------------------------------------------------------------------


def test_asking_what_approving_would_do_names_the_time_and_changes_nothing(rig):
    page = rig.page()
    rig.through(page, BODY)
    post = rig.store.all("SELECT * FROM post")[0]
    before = rig.store.conn.total_changes
    seen = rig.core.call("post.approval", {"id": post["id"]})
    assert rig.store.conn.total_changes == before and "post.approval" in READS
    assert seen["books"] is True and seen["scheduler"] == "Practice"  # Practice sends nothing
    assert rig.store.post(post["id"])["state"] == "draft"
    done = rig.core.call("post.approve", {"id": post["id"]})
    assert done["state"] == "booked" and done["slot_ms"] == seen["slot_ms"]


def test_a_scheduler_that_sends_out_is_not_booked_at_approval_and_the_answer_says_so(make_rig):
    rig = make_rig(settings={"scheduler": "socialbu"})
    page = rig.page()
    rig.through(page, BODY)
    post = rig.store.all("SELECT * FROM post")[0]
    seen = rig.core.call("post.approval", {"id": post["id"]})
    assert seen["books"] is False and seen["slot_ms"]


def test_what_approving_would_do_is_only_said_for_a_draft(rig):
    page = rig.page()
    rig.through(page, BODY)
    post = rig.store.all("SELECT * FROM post")[0]
    rig.core.call("post.approve", {"id": post["id"]})
    with pytest.raises(Exception, match="so that does not apply"):
        rig.core.call("post.approval", {"id": post["id"]})


# --- the single-letter keys -------------------------------------------------------------------


def test_the_page_is_told_whether_single_letter_keys_are_on_and_they_are_by_default(make_rig):
    assert make_rig().core.call("overview", {})["letter_keys"] is True
    off = make_rig(settings={"letter_keys": False})
    assert off.core.call("overview", {})["letter_keys"] is False


def test_single_letter_keys_is_a_setting_in_plain_sight(rig):
    from jimmyhq.apps import manifest as mf  # noqa: PLC0415

    [found] = [s for s in mf.load(APP_ROOT).settings if s.key == "letter_keys"]
    assert not found.advanced and found.default is True


# --- what the owner reads says "brand", not "page" -----------------------------------------


def test_everything_the_manifest_tells_the_owner_says_brand_where_it_means_one():
    import json  # noqa: PLC0415
    import re  # noqa: PLC0415

    manifest = json.loads((APP_ROOT / "jimmyhq.app.json").read_text(encoding="utf-8"))
    said: list[str] = []

    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("label", "help", "description", "purpose", "summary") \
                        and isinstance(value, str):
                    said.append(value)
                else:
                    collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(manifest)
    assert len(said) > 50
    allowed = ("Settings page", "settings page", "Leave out page", "comes in pages",
               "in pages. Call it again")
    for text in said:
        rest = text
        for ok in allowed:
            rest = rest.replace(ok, "")
        assert not re.search(r"\bpages?\b", rest, re.IGNORECASE), text


def test_the_first_run_sentence_and_the_refusals_say_brand():
    assert "no brands yet" in pages.NO_PAGES and "under Brands" in pages.NO_PAGES
    for text in (pages.NO_PAGES,):
        assert "page" not in text.lower()


def test_the_agents_charters_say_brand_for_what_the_owner_set_up():
    for path in (APP_ROOT / "roles").glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "page's followers" not in text and "for one page" not in text, path.name
