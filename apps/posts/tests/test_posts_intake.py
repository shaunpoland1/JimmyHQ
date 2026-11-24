"""The ways in for this part, a pasted link and a typed topic, and what waits on the
owner."""

from __future__ import annotations

import pytest
from posts import intake
from posts_rig import ARTICLE


def test_a_pasted_link_starts_a_piece_that_reads_first(rig):
    page = rig.page()
    piece = rig.core.call("new.link", {"page_id": page["id"], "url": f"  {ARTICLE}  ",
                                       "notes": "Ask about the grant.", "sponsored": True})
    assert piece["state"] == "reading" and piece["source_url"] == ARTICLE
    assert piece["sponsored"] == 1 and piece["notes"] == "Ask about the grant."
    assert rig.core.events[-1]["payload"] == {"piece": piece["id"], "way": "link"}


def test_a_typed_topic_starts_at_the_overlap_check(rig):
    page = rig.page()
    piece = rig.core.call("new.topic", {"page_id": page["id"], "topic": "Saving seeds"})
    assert piece["state"] == "overlap_check" and piece["source_url"] is None


@pytest.mark.parametrize("call, args, said", [
    ("new.link", {"url": "not a link"}, "Paste the whole link"),
    ("new.topic", {"topic": "   "}, "Type a topic"),
    ("new.topic", {"topic": "x" * 301}, "keep it to 300"),
    ("new.link", {"url": ARTICLE, "page_id": 99}, "Choose one of your brands"),
])
def test_a_way_in_that_cannot_start_says_what_to_change(rig, call, args, said):
    page = rig.page()
    with pytest.raises(Exception, match=said):
        rig.core.call(call, {"page_id": page["id"], **args})
    assert rig.store.all("SELECT id FROM piece") == []


def test_every_way_in_is_a_page_call():
    assert set(intake.WAYS) == {"link", "topic", "pictures"}


def test_a_draft_waiting_past_the_reminder_time_is_reminded_once(rig):
    rig.through(rig.page(), "Some 1,240 homes went solar.")
    [card] = rig.cards_of("draft")
    rig.clock.advance(97 * 3_600_000)
    rig.advance(2)
    assert card["note"] == "This draft is still waiting for you."
    card["note"] = None
    rig.advance()
    assert card["note"] is None


def test_a_piece_can_be_dropped_from_the_page_and_its_card_goes(rig):
    piece = rig.through(rig.page(), "Some 1,240 homes went solar.")
    [card] = rig.cards_of("draft")
    assert rig.core.call("piece.drop", {"id": piece["id"]})["state"] == "dropped"
    assert rig.core.cards[card["card_id"]]["withdrawn"]
    assert rig.store.post_of(piece["id"])["state"] == "declined"


def test_a_notice_card_is_acknowledged_and_changes_nothing(rig):
    rig.core.change_setting("dup_strong", 0.5)
    [card] = rig.cards_of("notice")
    assert rig.core.decide(card["card_id"], "ok") is None
