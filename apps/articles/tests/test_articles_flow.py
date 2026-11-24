"""One article from its topic to the owner's decision, in plain mode, with no agents."""

from __future__ import annotations

import pytest
from articles_rig import GOOD_BODY, SURVEY_URL, article, no_findings


def test_a_first_run_has_no_sites_and_says_how_to_add_one(rig):
    seen = rig.core.call("overview")
    assert seen["sites"] == [] and "Add one under Sites" in seen["hint"]
    with pytest.raises(Exception, match="There are no sites yet"):
        rig.core.call("new.topic", {"site_id": 1, "topic": "Anything"})


def test_a_topic_reaches_a_card_with_its_sources_checks_and_outline(rig):
    piece = rig.to_card()
    assert piece["state"] == "ready"
    [card] = rig.cards_of("article")
    assert card["subject"] == f"app.articles:piece:{piece['id']}"
    assert [a["id"] for a in card["actions"]] == ["approve", "send_back", "decline"]
    assert "Every check passed." in card["body"] and "18% less" in card["body"]
    assert piece["outline"]["title"] == "What a garden plot really saves"
    seen = rig.core.call("piece.get", {"id": piece["id"]})
    assert [s["host"] for s in seen["sources"]] == ["survey.example.com", "soil.example.com",
                                                    "kitchen.example.com", "forum.example.com"]
    assert all(r["level"] == "ok" or r["name"] == "sources" for r in seen["checks"])


def test_each_step_says_what_it_did_in_a_line(rig):
    piece = rig.to_card()
    steps = [t["step"] for t in rig.store.turns(piece["id"])]
    assert steps == ["started", "coverage_check", "searching", "reading", "summarising",
                     "outlining", "drafting", "reviewing"]


def test_approving_publishes_through_practice_and_sends_nothing(rig):
    piece = rig.to_card()
    [card] = rig.cards_of("article")
    rig.core.decide(card["card_id"], "approve")
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "sent"
    [logged] = rig.store.all("SELECT * FROM practice_log")
    assert logged["would_send"]["status"] == "draft"
    assert SURVEY_URL in logged["would_send"]["html"] and "[S1:1]" not in \
        logged["would_send"]["html"]
    assert rig.core.fetched == []
    rig.core.call("article.published", {"id": piece["id"]})
    assert rig.store.piece(piece["id"])["state"] == "published"
    kinds = [e["kind"] for e in rig.core.events]
    assert kinds[-1] == "app.articles.piece.published"


def test_send_back_needs_a_note_and_writes_a_new_version_from_it(rig):
    piece = rig.to_card()
    with pytest.raises(Exception, match="You need to say what to change"):
        rig.core.call("article.send_back", {"id": piece["id"], "note": "  "})
    [card] = rig.cards_of("article")
    rig.core.decide(card["card_id"], "send_back", reason="Open with the cost of a plot.")
    assert rig.store.piece(piece["id"])["state"] == "revising"
    rig.core.replies += [article("<p>A plot costs 30 dollars a season [S1:7]. " +
                                 GOOD_BODY[3:]), no_findings()]
    rig.advance(2)
    revise = rig.core.asked[-2]
    assert "Open with the cost of a plot." in revise["prompt"]
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "ready" and piece["revision"] == 2
    assert rig.store.revision(piece["id"])["kind"] == "owner_note"


def test_decline_needs_a_reason_and_becomes_a_lesson_for_the_next_article(rig):
    piece = rig.to_card()
    with pytest.raises(Exception, match="You need to give a reason"):
        rig.core.call("article.decline", {"id": piece["id"], "reason": ""})
    [card] = rig.cards_of("article")
    rig.core.decide(card["card_id"], "decline", reason="Voice off: too breezy for us.")
    assert rig.store.piece(piece["id"])["state"] == "declined"
    [lesson] = rig.store.lessons(piece["site_id"])
    assert lesson["reason"] == "Voice off" and "too breezy" in lesson["note"]
    rig.to_card(rig.store.site(piece["site_id"]), topic="Compost for a first plot")
    outline_call = [a for a in rig.core.asked if "Outline an article" in a["prompt"]][-1]
    assert "too breezy" in outline_call["system"]


def test_an_edit_is_sanitised_and_checked_again(rig):
    piece = rig.to_card()
    rig.core.call("article.edit", {"id": piece["id"], "title": "Plots and prices",
                                   "body": "<p>Plots cut vegetable bills by 30% [S1:1].</p>"
                                           "<script>alert(1)</script>"})
    revision = rig.store.revision(piece["id"])
    assert revision["kind"] == "edit" and "<script" not in revision["body"]
    checks = rig.store.checks(revision["id"])
    assert any(c["name"] == "numbers" and c["level"] == "ask" and "30%" in c["detail"]
               for c in checks)
    assert "1 question open" in rig.cards_of("article")[0]["summary"]
    [lesson] = rig.store.lessons(piece["site_id"])
    assert lesson["kind"] == "edit"


def test_the_owner_can_answer_a_question_and_the_answer_stays_with_it(make_rig):
    rig = make_rig(settings={"repair_passes": 0})
    piece = rig.to_card(body="<p>Plots cut vegetable bills by 30% [S1:1].</p>")
    revision = rig.store.revision(piece["id"])
    [question] = [c for c in rig.store.checks(revision["id"]) if c["level"] == "ask"]
    rig.core.call("check.answer", {"id": question["id"], "answer": "yes"})
    with pytest.raises(Exception, match="yes, no or fixed"):
        rig.core.call("check.answer", {"id": question["id"], "answer": "maybe"})
    rig.core.call("article.edit", {"id": piece["id"], "body": revision["body"] + "<p>More.</p>"})
    [again] = [c for c in rig.store.checks(rig.store.revision(piece["id"])["id"])
               if c["level"] == "ask"]
    assert again["answer"] == "yes"


def test_a_stuck_step_says_so_and_waits_for_retry(rig):
    site = rig.site()
    rig.web()
    piece = rig.topic(site)
    rig.advance(2)  # coverage, then searching with no model answer: searched as written
    assert rig.store.piece(piece["id"])["state"] == "reading"
    rig.advance(2)  # reading, then the summary has no model answer
    stuck = rig.store.piece(piece["id"])
    assert stuck["state"] == "stalled" and "Press Retry" in stuck["error"]
    rig.advance(1)
    assert rig.store.piece(piece["id"])["state"] == "stalled"  # nothing retries by itself
    rig.core.call("piece.retry", {"id": piece["id"]})
    assert rig.store.piece(piece["id"])["state"] == "summarising"


def test_the_articles_started_a_day_are_counted_at_research(make_rig):
    rig = make_rig(settings={"pieces_per_day": 1})
    site = rig.site()
    rig.web()
    first = rig.topic(site, topic="Garden plots and money")
    second = rig.topic(site, topic="Compost for clay soil beds")
    rig.core.replies += [{"phrasings": ["a"]}]
    rig.advance(4)
    states = {rig.store.piece(first["id"])["state"], rig.store.piece(second["id"])["state"]}
    assert "searching" in states
    waiting = [t for t in rig.store.all("SELECT * FROM turn") if "waits for tomorrow" in
               t["gloss"]]
    assert len(waiting) == 1
