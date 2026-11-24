"""\"Have we covered this lately?\": before any drafting, on every way in, by meaning when
an embeddings model is set up and by shared words when it is not, never skipped."""

from __future__ import annotations

from jimmyhq_app import CallError
from posts import similar
from posts_rig import ARTICLE_TEXT, draft_reply, ship

DAY = 86_400_000
EARLIER = ("1,240 homes in the valley added solar panels last year, 38% more than the year "
           "before. Installers report waiting lists of up to 14 weeks for solar panels.")


def sent_post(rig, page, body=EARLIER, *, days_ago=21, state="went_out"):
    """A post the page sent some days ago, as the record would hold it."""
    when = rig.clock.now - days_ago * DAY
    piece = rig.store.insert("piece", {"page_id": page["id"], "way": "link",
                                       "source_url": "https://old.example.com/a",
                                       "state": "closed", "created": when, "updated": when})
    return rig.store.insert("post", {"piece_id": piece, "page_id": page["id"],
                                     "title": "Solar roofs", "body": body, "state": state,
                                     "slot_ms": when, "created": when, "updated": when})


def no_embeddings(rig):
    def refuse(args):
        raise CallError("unavailable", "The owner has not chosen a model for the "
                                       "'embeddings' role. Nothing was sent anywhere.")
    rig.core._model_embed = refuse  # noqa: SLF001


def test_an_overlap_is_found_before_any_model_call_is_made(rig):
    page = rig.page()
    sent_post(rig, page, body=ARTICLE_TEXT)
    piece = rig.link(page)
    rig.advance(3)
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "held_overlap"
    assert rig.core.asked == []  # no drafting model was asked
    [card] = rig.cards_of("overlap")
    assert "like the post of 3 weeks ago" in card["title"]
    assert [a["id"] for a in card["actions"]] == ["draft_anyway", "drop"]
    assert "Nothing has been spent" in card["body"]


def test_drafting_anyway_carries_the_piece_on_and_dropping_ends_it(make_rig):
    for action, then in (("draft_anyway", "writing"), ("drop", "dropped")):
        rig = make_rig()
        page = rig.page()
        sent_post(rig, page, body=ARTICLE_TEXT)
        piece = rig.link(page)
        rig.advance(2)
        [card] = rig.cards_of("overlap")
        rig.core.decide(card["card_id"], action)
        assert rig.store.piece(piece["id"])["state"] == then


def test_a_typed_topic_is_compared_the_same_way(rig):
    page = rig.page()
    sent_post(rig, page)
    piece = rig.core.call("new.topic", {"page_id": page["id"], "topic": "Solar panels on "
                                        "valley homes", "notes": EARLIER})
    rig.advance()
    assert rig.store.piece(piece["id"])["state"] == "held_overlap"
    assert rig.core.asked == []


def test_open_pieces_are_compared_too(rig):
    page = rig.page()
    first = rig.link(page)
    second = rig.link(page, url="https://other.example.com/solar")
    rig.advance(4)  # read both, then check both
    for piece, other in ((first, second), (second, first)):
        found = rig.store.piece(piece["id"])
        assert found["state"] == "held_overlap"
        assert found["overlap"]["best"]["what"] == "piece"
        assert found["overlap"]["best"]["id"] == other["id"]


def test_posts_older_than_the_window_or_of_another_page_are_not_compared(rig):
    page = rig.page()
    other = rig.page(name="Workshop")
    sent_post(rig, page, body=ARTICLE_TEXT, days_ago=40)
    sent_post(rig, other, body=ARTICLE_TEXT, days_ago=2)
    piece = rig.link(page)
    rig.advance(2)
    assert rig.store.piece(piece["id"])["state"] == "writing"


def test_without_an_embeddings_model_shared_words_are_used_and_the_result_says_so(rig):
    page = rig.page()
    sent_post(rig, page, body=ARTICLE_TEXT)
    no_embeddings(rig)
    piece = rig.link(page)
    rig.advance(2)
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "held_overlap"
    assert piece["overlap"]["method"] == "words"
    assert "Compared by shared words" in piece["overlap"]["note"]
    [card] = rig.cards_of("overlap")
    assert "by shared words" in card["title"]


def test_the_word_comparison_is_the_same_every_time_and_ignores_case():
    a = "The valley added solar panels last year"
    assert similar.word_overlap(a, a.upper()) == 1.0
    assert similar.word_overlap(a, "A recipe for bread with seeds") == 0.0
    assert similar.word_overlap(a, a + " and more") == similar.word_overlap(a, a + " and more")


def test_the_overlap_runs_again_on_every_draft_and_rides_along_on_the_card(rig):
    page = rig.page()
    sent_post(rig, page, body=EARLIER)
    rig.core.change_setting("dup_warn", 0.99)
    rig.core.change_setting("dup_strong", 0.995)  # let the piece through to drafting
    piece = rig.link(page)
    rig.advance(2)
    rig.core.change_setting("dup_warn", 0.80)
    rig.core.change_setting("dup_strong", 0.88)
    rig.core.replies += [draft_reply(EARLIER, claims=[{"text": "homes", "sentences": [2, 3, 6]}]),
                         ship()]
    rig.advance(2)
    post = rig.store.post_of(piece["id"])
    [dup] = [c for c in rig.store.checks(post["id"]) if c["name"] == "near_duplicate"]
    assert dup["level"] == "ask" and "Post it anyway?" in dup["detail"]
    assert dup["evidence"] and dup["evidence"][0].startswith("post:")


def test_a_near_duplicate_is_never_withheld_after_approval(rig):
    test_the_overlap_runs_again_on_every_draft_and_rides_along_on_the_card(rig)
    [card] = rig.cards_of("draft")
    assert "1 question open" in card["body"]
    rig.core.decide(card["card_id"], "approve")
    [post] = rig.store.all("SELECT * FROM post WHERE state='booked'")
    assert post["body"] == EARLIER


def test_an_overlap_is_described_in_plain_words():
    best = similar.Match("post", 4, 0.8234, 0, "Solar roofs")
    overlap = similar.Overlap("embeddings", "close", 0.8234, 0.8, 0.88, best)
    assert similar.describe(overlap, 21 * DAY) == ("82% like the post of 3 weeks ago "
                                                   "(\"Solar roofs\"), by meaning.")
