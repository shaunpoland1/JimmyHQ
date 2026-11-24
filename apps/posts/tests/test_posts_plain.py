"""Plain mode: one step a run, each with a deadline and a human line; a separate check
call on another model role; revisions stopped by the code; the owner's send-backs and
declines kept as lessons."""

from __future__ import annotations

import pytest
from posts_rig import ARTICLE, draft_reply, ship

BODY = "Some 1,240 homes in the valley added solar panels last year."


def revise(points=None):
    return {"verdict": "revise", "points": points or [
        {"quote": "1,240 homes", "sentences": [2], "problem": "Say it was last year."}],
        "gloss": "One thing to fix."}


def test_the_check_call_is_separate_and_on_the_check_role(rig):
    rig.through(rig.page(), BODY)
    roles = [a["role"] for a in rig.core.asked]
    assert roles == ["smart", "default"]
    assert all(a["json_schema"] for a in rig.core.asked)


def test_revisions_stop_where_the_code_says_however_often_the_checker_asks(rig):
    page = rig.page()
    piece = rig.link(page)
    rig.core.replies += [draft_reply(BODY), revise(), draft_reply(BODY), revise(),
                         draft_reply(BODY), revise(), draft_reply(BODY), revise()]
    rig.advance(12)
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "ready" and piece["round"] == 2
    assert len(rig.core.asked) == 6  # draft, check, revise, check, revise, check
    assert len(rig.core.replies) == 2  # never asked again
    assert "stopped the revisions" in rig.store.turns(piece["id"])[-1]["gloss"]
    assert len(rig.cards_of("draft")) == 1


def test_with_no_revisions_allowed_the_first_check_goes_straight_to_the_owner(make_rig):
    rig = make_rig(settings={"max_rounds": 0})
    page = rig.page()
    piece = rig.link(page)
    rig.core.replies += [draft_reply(BODY), revise()]
    rig.advance(4)
    assert rig.store.piece(piece["id"])["state"] == "ready"


def test_a_checker_point_reaches_the_revision_as_data_not_as_instructions(rig):
    page = rig.page()
    rig.link(page)
    rig.core.replies += [draft_reply(BODY), revise(), draft_reply(BODY), ship()]
    rig.advance(6)
    revision = rig.core.asked[2]
    assert "Say it was last year." not in revision["prompt"]
    assert any("Say it was last year." in block for block in revision["untrusted"])


def test_a_reply_that_is_not_the_shape_asked_for_leaves_the_piece_stuck_with_a_sentence(rig):
    page = rig.page()
    piece = rig.link(page)
    rig.core.replies.append("Sorry, I cannot help with that.")
    rig.advance(3)
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "stalled" and "could not be read" in piece["error"]
    assert "Retry" in piece["error"]
    rig.advance(3)
    assert len(rig.core.asked) == 1  # nothing tried again by itself
    rig.core.call("piece.retry", {"id": piece["id"]})
    rig.core.replies += [draft_reply(BODY), ship()]
    rig.advance(2)
    assert rig.store.piece(piece["id"])["state"] == "ready"


def test_a_model_that_refuses_leaves_the_piece_stuck_and_says_why(rig):
    page = rig.page()
    piece = rig.link(page)
    rig.advance(3)  # no reply waiting: the fake model refuses
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "stalled" and "no model reply waiting" in piece["error"]


def test_a_step_still_running_after_its_deadline_is_marked_stuck(rig):
    page = rig.page()
    piece = rig.link(page)
    rig.advance(2)
    rig.store.update("piece", piece["id"], {"step_started": rig.clock.now,
                                            "deadline_ms": rig.clock.now + 60_000})
    rig.clock.advance(31 * 60_000)  # the run that held it stopped without finishing
    rig.advance()
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "stalled" and "within 30 minutes" in piece["error"]
    assert rig.core.asked == []
    assert rig.core.call("piece.retry", {"id": piece["id"]})["state"] == "writing"


def test_only_the_pieces_a_day_setting_may_start_drafting_each_day(make_rig):
    rig = make_rig(settings={"pieces_per_day": 1})
    page = rig.page()
    first = rig.link(page)
    second = rig.link(page, url="https://bread.example.com/b", text=open_text())
    rig.core.replies += [draft_reply(BODY), ship()]
    rig.advance(8)
    assert rig.store.piece(first["id"])["state"] == "ready"
    assert rig.store.piece(second["id"])["state"] == "writing"
    assert any("waits for tomorrow" in t["gloss"] for t in rig.store.turns(second["id"]))
    rig.clock.advance(86_400_000)
    rig.core.replies += [draft_reply("Another post entirely about bread."), ship()]
    rig.advance(2)
    assert rig.store.piece(second["id"])["state"] == "ready"


def test_a_typed_topic_is_drafted_from_the_owners_notes_with_no_links(rig):
    page = rig.page()
    piece = rig.core.call("new.topic", {"page_id": page["id"], "topic": "Saving seeds",
                                        "notes": "I saved 40 jars of seed this autumn."})
    rig.core.replies += [draft_reply("I saved 40 jars of seed.", links=[1]), ship()]
    rig.advance(4)
    post = rig.store.post_of(piece["id"])
    assert post["links"] == []
    assert rig.core.asked[0].get("untrusted") is None
    assert "Saving seeds" in rig.core.asked[0]["prompt"]
    [found] = [c for c in rig.store.checks(post["id"]) if c["name"] == "numbers"]
    assert found["level"] == "ok" and found["evidence"] == ["notes"]


def test_sending_back_needs_a_note_that_becomes_a_lesson_and_a_new_revision(rig):
    piece = rig.through(rig.page(), "A real game changer: " + BODY)
    [card] = rig.cards_of("draft")
    rig.core.decide(card["card_id"], "send_back", reason="Drop “game changer”.")
    assert rig.store.piece(piece["id"])["state"] == "revising"
    [lesson] = rig.store.all("SELECT * FROM lesson")
    assert lesson["kind"] == "send_back" and lesson["quotes"] == ["game changer"]
    rig.core.replies += [draft_reply("Still a game changer: " + BODY), ship()]
    rig.advance(2)
    revision = rig.core.asked[2]
    assert "Drop “game changer”." in revision["prompt"]  # the owner's own words
    assert "game changer" in revision["system"]
    post = rig.store.post_of(piece["id"])
    assert post["state"] == "draft" and post["revision"] == 2
    assert any(c["name"] == "send_back" and c["level"] == "ask"
               for c in rig.store.checks(post["id"]))
    assert len(rig.cards_of("draft")) == 1


def test_sending_back_from_the_page_without_a_note_is_refused(rig):
    piece = rig.through(rig.page(), BODY)
    post = rig.store.post_of(piece["id"])
    with pytest.raises(Exception, match="Say what to change"):
        rig.core.call("post.send_back", {"id": post["id"], "note": "  "})


def test_a_decline_is_kept_under_the_listed_reason_it_starts_with(rig):
    piece = rig.through(rig.page(), BODY)
    [card] = rig.cards_of("draft")
    rig.core.decide(card["card_id"], "decline", reason="already covered last month")
    post = rig.store.post_of(piece["id"])
    assert post["state"] == "declined" and post["note"] == "Declined: Already covered."
    [lesson] = rig.store.all("SELECT * FROM lesson")
    assert lesson["reason"] == "Already covered"
    assert lesson["note"] == "already covered last month"


def test_a_decline_reason_from_outside_the_list_is_kept_as_other():
    from posts.actions import reason_from_list  # noqa: PLC0415

    listed = ["Wrong angle", "Voice off", "Other"]
    assert reason_from_list("too long", listed) == "Other"
    assert reason_from_list("voice off, too stiff", listed) == "Voice off"


def test_a_lesson_reaches_the_next_draft_of_the_page(rig):
    page = rig.page()
    rig.through(page, BODY)
    [card] = rig.cards_of("draft")
    rig.core.decide(card["card_id"], "decline", reason="Voice off: too formal for us")
    rig.through(page, "A bread post.", url="https://bread.example.com/a",
                text=open_text())
    system = rig.core.asked[-2]["system"]
    assert "Voice off: Voice off: too formal for us" in system


def open_text():
    return ("Sourdough needs time more than skill. " * 20).strip()


def test_a_decision_on_a_card_that_was_replaced_does_nothing(rig):
    piece = rig.through(rig.page(), BODY)
    [old] = rig.cards_of("draft")
    post = rig.store.post_of(piece["id"])
    rig.core.call("post.edit", {"id": post["id"], "body": BODY + " More soon."})
    rig.core.decide(old["card_id"], "approve")
    assert rig.store.post(post["id"])["state"] == "draft"


def test_the_source_address_comes_from_the_code_not_the_model(rig):
    piece = rig.through(rig.page(), BODY)
    post = rig.store.post_of(piece["id"])
    assert post["links"] == [ARTICLE]
