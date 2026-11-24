"""The review held to its shape, and repair passes the code decides on and keeps only when
they leave fewer open findings."""

from __future__ import annotations

from articles import review, writing
from articles_rig import GOOD_BODY, article, finding, no_findings

QUOTE = "Compost helped too"


def test_a_review_answer_that_is_not_in_the_agreed_shape_fails_the_step_and_is_not_read_as_no_findings(rig):  # noqa: E501
    piece = rig.to_card(review={"findings": "none found"})
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "stalled" and piece["came_from"] == "reviewing"
    assert "not in the shape Articles asked for" in piece["error"]
    assert "not read as finding nothing" in piece["error"]
    assert rig.cards_of("article") == []
    assert rig.store.revision(piece["id"])["review"] is None
    checks = rig.store.checks(rig.store.revision(piece["id"])["id"])
    assert any(c["name"] == "review" and "not been reviewed" in c["detail"] for c in checks)


def test_a_review_that_is_not_json_at_all_fails_the_step_too(rig):
    piece = rig.to_card(review="Looks fine to me!")
    assert rig.store.piece(piece["id"])["state"] == "stalled"


def test_the_shape_rule_names_what_was_wrong():
    for data, said in [([], "not a JSON object"), ({}, "no list of findings"),
                       ({"findings": [{"kind": "other"}]}, "had no quote, cites, problem"),
                       ({"findings": [{**finding("x"), "kind": "praise"}]}, "of a kind"),
                       ({"findings": [{**finding("x"), "cites": [{"source": "one"}]}]},
                        "citations")]:
        try:
            review.check_shape(data)
        except review.ReviewShapeError as error:
            assert said in str(error)
        else:
            raise AssertionError(f"{data!r} was taken")


def test_a_finding_that_quotes_nothing_in_the_article_is_set_aside_and_counted(rig):
    piece = rig.to_card(review={"findings": [finding("words the article never had")]})
    revision = rig.store.revision(piece["id"])
    assert revision["review"] == [] and revision["review_aside"] == 1
    checks = rig.store.checks(revision["id"])
    assert any("1 of the review's findings were set aside" in c["detail"] for c in checks)
    assert rig.store.piece(piece["id"])["state"] == "ready"


def test_a_review_finding_is_a_question_and_a_repair_that_answers_it_is_kept(rig):
    piece = rig.to_card(review={"findings": [finding(QUOTE)]})
    assert rig.store.piece(piece["id"])["state"] == "repairing"
    fixed = GOOD_BODY.replace("Compost helped too: a", "In a separate")
    rig.core.replies += [article(fixed), no_findings()]
    rig.advance(2)
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "ready" and piece["revision"] == 2
    kept = rig.store.revision(piece["id"])
    assert kept["kind"] == "repair" and kept["kept"] == 1 and kept["open_count"] == 0
    assert any("Compost helped too" in change for change in kept["changes"])
    [line] = [t for t in rig.store.turns(piece["id"]) if t["step"] == "rechecking"]
    assert "open findings went from 1 to 0" in line["gloss"]
    repair_call = [a for a in rig.core.asked if a["prompt"].startswith("Repair")][0]
    assert any(block.startswith("Findings:") and QUOTE in block
               for block in repair_call["untrusted"])


def test_a_repair_pass_that_does_not_reduce_the_open_findings_is_thrown_away(rig):
    piece = rig.to_card(review={"findings": [finding(QUOTE)]})
    worse = GOOD_BODY.replace("18% less", "28% less")
    rig.core.replies += [article(worse), {"findings": [finding(QUOTE)]}]
    rig.advance(2)
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "ready" and piece["revision"] == 1
    thrown = rig.store.revisions(piece["id"])[1]
    assert thrown["kind"] == "repair" and thrown["kept"] == 0
    [line] = [t for t in rig.store.turns(piece["id"]) if t["step"] == "rechecking"]
    assert "thrown away" in line["gloss"] and "The earlier version stands" in line["gloss"]
    [card] = rig.cards_of("article")
    assert "28%" not in card["body"]


def test_repair_passes_stop_where_the_code_says(make_rig):
    rig = make_rig(settings={"repair_passes": 1})
    two = [finding(QUOTE), finding("12% were left empty by August")]
    piece = rig.to_card(review={"findings": two})
    rig.core.replies += [article(GOOD_BODY.replace("Compost helped too: a", "In a separate")),
                         {"findings": [finding("12% were left empty by August")]}]
    rig.advance(2)
    piece = rig.store.piece(piece["id"])
    assert piece["state"] == "ready" and piece["passes"] == 1
    assert rig.store.revision(piece["id"])["open_count"] == 1


def test_with_no_repair_passes_the_findings_go_straight_to_the_owner(make_rig):
    rig = make_rig(settings={"repair_passes": 0})
    piece = rig.to_card(review={"findings": [finding(QUOTE)]})
    assert rig.store.piece(piece["id"])["state"] == "ready"
    [line] = [t for t in rig.store.turns(piece["id"]) if t["step"] == "reviewing"]
    assert "The code stopped the repairs here" in line["gloss"]
    [card] = rig.cards_of("article")
    assert "1 question open" in card["body"]


def test_the_review_runs_on_its_own_role_and_warns_when_it_is_the_writers(make_rig):
    rig = make_rig(settings={"role_review": "smart"})
    piece = rig.to_card()
    review_call = [a for a in rig.core.asked if a["prompt"].startswith("Review")][0]
    assert review_call["role"] == "smart"
    checks = rig.store.checks(rig.store.revision(piece["id"])["id"])
    assert any(c["name"] == "review" and "same model role" in c["detail"] for c in checks)


def test_the_repair_rule_needs_strictly_fewer_open_findings():
    assert writing.strictly_fewer(1, 2)
    assert not writing.strictly_fewer(2, 2)
    assert not writing.strictly_fewer(3, 2)
