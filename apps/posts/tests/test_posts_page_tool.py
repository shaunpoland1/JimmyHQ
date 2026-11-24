"""Each page may choose its own tool to book through; a page that chose none follows Posts'
setting. A post booked on a tool stays with it, whatever its page chooses afterwards."""

from __future__ import annotations

import sqlite3

import pytest
from posts import booking
from posts.schedulers import Outcome
from posts.store import Store
from posts_rig import Clock

BODY = "Some 1,240 homes in the valley added solar panels last year."
FAKE = [{"scheduler": "fake", "account_id": "a1", "label": "Main"}]


class Fake:
    """A scheduler that sends out, whose answers the test chooses."""

    id = "fake"
    label = "Fake scheduler"
    sends_out = True
    can_edit = True

    def __init__(self):
        self.calls: list = []

    def needs(self, page):
        return None

    def book(self, b):
        self.calls.append("book")
        return Outcome("sent", [{"account": a["account"], "external_id": "up-1"}
                                for a in b.accounts])

    def status(self, post_id, upstream):
        return None

    def move(self, post_id, upstream, slot_ms):
        self.calls.append("move")
        return Outcome("sent", upstream)

    def edit_text(self, post_id, upstream, text):
        self.calls.append("edit")
        return Outcome("sent", upstream)

    def cancel(self, post_id, upstream):
        self.calls.append("cancel")
        return Outcome("sent", upstream)

    def scheduled(self):
        return []


def draft_of(rig, page, url):
    rig.through(page, BODY, url=url)
    return next(p for p in rig.store.all("SELECT * FROM post WHERE page_id=? ORDER BY id DESC",
                                         (page["id"],)))


def approved(rig, page, url="https://news.example.com/a"):
    post = draft_of(rig, page, url)
    return rig.core.call("post.approve", {"id": post["id"]})


def with_fake(rig):
    """The rig, also offering a scheduler that sends out (so a page may choose it)."""
    rig.fake = Fake()
    rig.ctx.schedulers["fake"] = rig.fake
    return rig


@pytest.fixture
def two_tools(make_rig):
    """Posts' setting is Practice; the rig offers the real Practice and By hand."""
    return make_rig(settings={"scheduler": "practice"})


# --- which tool books a page -----------------------------------------------------------------


def test_a_page_that_chose_nothing_books_through_the_setting(make_rig):
    rig = make_rig(settings={"scheduler": "by_hand"})
    page = rig.page()
    assert page["book_through"] == ""
    assert approved(rig, page)["scheduler"] == "by_hand"


def test_a_page_that_chose_a_tool_books_through_it_while_another_page_follows_the_setting(
        two_tools):
    own = two_tools.page(name="Garden notes", book_through="by_hand")
    plain = two_tools.page(name="Kitchen notes")
    assert approved(two_tools, own)["scheduler"] == "by_hand"
    assert approved(two_tools, plain, "https://news.example.com/b")["scheduler"] == "practice"


def test_approving_a_draft_says_which_tool_its_page_books_through(two_tools):
    own = two_tools.page(name="Garden notes", book_through="by_hand")
    plain = two_tools.page(name="Kitchen notes")
    first = draft_of(two_tools, own, "https://news.example.com/a")
    second = draft_of(two_tools, plain, "https://news.example.com/b")
    assert two_tools.core.call("post.approval", {"id": first["id"]})["scheduler"] == "By hand"
    assert two_tools.core.call("post.approval", {"id": second["id"]})["scheduler"] == "Practice"


def test_a_page_may_choose_a_tool_that_sends_out_and_then_waits_for_the_owners_ladder(
        make_rig):
    rig = with_fake(make_rig(settings={"scheduler": "by_hand"}))
    fake = rig.fake
    page = rig.page(accounts=FAKE, book_through="fake")
    post = approved(rig, page)
    assert post["state"] == "queued" and fake.calls == []
    assert rig.core.call("post.book", {"id": post["id"]})["scheduler"] == "fake"
    assert fake.calls == ["book"]


def test_the_choice_is_the_pages_ids_only_and_empty_means_the_setting(rig):
    page = rig.page(book_through="by_hand")
    assert rig.core.call("pages.save", {"id": page["id"], "name": page["name"],
                                        "book_through": ""})["book_through"] == ""


def test_a_save_that_does_not_mention_the_choice_leaves_it_alone(rig):
    page = rig.page(book_through="by_hand")
    again = rig.core.call("pages.save", {"id": page["id"], "name": page["name"],
                                         "hours": ["09:00"]})
    assert again["book_through"] == "by_hand" and again["hours"] == ["09:00"]


def test_a_tool_posts_does_not_offer_is_refused_with_what_to_do_instead(rig):
    with pytest.raises(Exception, match=r'book through "elsewhere", which Posts does not '
                                        r"offer\. Choose one from the list.*Nothing was saved\."):
        rig.page(book_through="elsewhere")
    assert rig.store.pages() == []


def test_a_choice_that_is_too_long_is_refused(rig):
    with pytest.raises(Exception, match="keep it to 40. Nothing was saved."):
        rig.page(book_through="x" * 41)


# --- a choice that is no longer offered ------------------------------------------------------


def test_a_choice_posts_no_longer_offers_is_booked_through_the_setting_and_the_screen_says_so(
        make_rig):
    rig = make_rig(settings={"scheduler": "by_hand"})
    page = rig.page()
    rig.store.update("page", page["id"], {"book_through": "retired"})
    [seen] = rig.core.call("overview", {})["pages"]
    assert seen["books_through"] == {"id": "by_hand", "label": "By hand", "sends_out": False,
                                     "known": True, "own": False, "lost": True}
    assert approved(rig, rig.store.page(page["id"]))["scheduler"] == "by_hand"


def test_the_overview_says_for_each_page_which_tool_books_it_and_whether_it_chose_it(
        two_tools):
    two_tools.page(name="Garden notes", book_through="by_hand")
    two_tools.page(name="Kitchen notes")
    seen = {p["name"]: p["books_through"] for p in two_tools.core.call("overview", {})["pages"]}
    assert seen["Garden notes"] == {"id": "by_hand", "label": "By hand", "sends_out": False,
                                    "known": True, "own": True, "lost": False}
    assert seen["Kitchen notes"] == {"id": "practice", "label": "Practice", "sends_out": False,
                                     "known": True, "own": False, "lost": False}


def test_a_queued_post_that_was_refused_follows_its_pages_choice_not_the_one_it_was_refused_on(
        make_rig):
    rig = with_fake(make_rig(settings={"scheduler": "by_hand"}))
    page = rig.page(accounts=FAKE, book_through="fake")
    post = approved(rig, page)
    rig.store.update("post", post["id"], {"scheduler": "fake"})  # left by a refused attempt
    rig.core.call("pages.save", {"id": page["id"], "name": page["name"], "book_through": ""})
    assert booking.scheduler_id_of(rig.ctx, rig.store.post(post["id"])) == "by_hand"


# --- a booked post keeps the tool it was booked with -----------------------------------------


def test_a_booked_post_is_moved_edited_and_cancelled_through_the_tool_it_was_booked_with(
        make_rig):
    rig = with_fake(make_rig(settings={"scheduler": "by_hand"}))
    fake = rig.fake
    page = rig.page(accounts=FAKE, book_through="fake")
    post = rig.core.call("post.book", {"id": approved(rig, page)["id"]})
    assert post["state"] == "booked" and post["scheduler"] == "fake"
    # the page now chooses to follow the setting; the post stays where it was booked
    rig.core.call("pages.save", {"id": page["id"], "name": page["name"], "book_through": ""})
    assert booking.scheduler_id_of(rig.ctx, rig.store.post(post["id"])) == "fake"
    rig.core.call("post.move", {"id": post["id"], "slot_ms": post["slot_ms"] + 86_400_000})
    rig.core.call("post.edit", {"id": post["id"], "body": "New words for the post."})
    rig.core.call("post.cancel", {"id": post["id"]})
    assert fake.calls == ["book", "move", "edit", "cancel"]


def test_the_overview_names_the_tool_a_posts_page_would_book_it_with_and_the_one_it_has(
        make_rig):
    rig = with_fake(make_rig(settings={"scheduler": "by_hand"}))
    page = rig.page(accounts=FAKE, book_through="fake")
    post = rig.core.call("post.book", {"id": approved(rig, page)["id"]})
    rig.core.call("pages.save", {"id": page["id"], "name": page["name"], "book_through": ""})
    [seen] = rig.core.call("overview", {})["posts"]
    assert seen["id"] == post["id"] and seen["tool"] == {"id": "fake", "label": "Fake scheduler"}


# --- the stored page ------------------------------------------------------------------------


def test_a_database_made_before_pages_chose_a_tool_gets_the_column_and_keeps_its_data(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE page (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
                 "register TEXT NOT NULL DEFAULT '', samples TEXT NOT NULL DEFAULT '', "
                 "timezone TEXT NOT NULL DEFAULT '', hours TEXT NOT NULL DEFAULT '[\"08:00\"]', "
                 "accounts TEXT NOT NULL DEFAULT '[]', tags TEXT NOT NULL DEFAULT '[]', "
                 "created INTEGER NOT NULL, updated INTEGER NOT NULL)")
    conn.execute("INSERT INTO page (name, accounts, created, updated) VALUES "
                 "('Garden notes', '[{\"scheduler\": \"socialbu\", \"account_id\": \"7\", "
                 "\"label\": \"\"}]', 1, 1)")
    conn.commit()
    store = Store(conn, Clock())
    try:
        [page] = store.pages()
        assert page["book_through"] == "" and page["name"] == "Garden notes"
        assert page["accounts"][0]["account_id"] == "7"
    finally:
        conn.close()
