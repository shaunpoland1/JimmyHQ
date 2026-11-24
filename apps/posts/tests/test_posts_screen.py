"""The Posts screen: the one call it draws from, and the calls its page makes."""

from __future__ import annotations

import re

from posts.program import calls
from posts_rig import APP_ROOT

WEB = APP_ROOT / "web"


def test_a_first_run_with_no_brands_says_how_to_add_one(rig):
    seen = rig.core.call("overview", {})
    assert seen["pages"] == [] and "Add one under Brands" in seen["hint"]
    assert seen["posts"] == [] and seen["pieces"] == []


def test_the_overview_says_that_practice_books_nothing(rig):
    seen = rig.core.call("overview", {})
    assert seen["scheduler"] == {"id": "practice", "label": "Practice", "sends_out": False,
                                 "known": True}


def test_the_overview_counts_a_drafts_open_questions_and_warnings(rig):
    page = rig.page()
    rig.through(page, "Solar roofs: 1,240 homes, 38% more, and 99 installers we made up.")
    post = next(p for p in rig.core.call("overview", {})["posts"])
    assert post["state"] == "draft" and post["piece_state"] == "ready"
    assert post["questions"] >= 1  # "99" is in no sentence of the source
    checks = rig.store.checks(post["id"])
    assert post["questions"] == sum(1 for c in checks if c["level"] == "ask"
                                    and not c["answer"])
    assert post["warnings"] == sum(1 for c in checks if c["level"] == "warn")


def test_an_answered_question_is_no_longer_counted_as_open(rig):
    page = rig.page()
    rig.through(page, "Solar roofs: 99 installers we made up.")
    post = rig.core.call("overview", {})["posts"][0]
    ask = next(c for c in rig.store.checks(post["id"]) if c["level"] == "ask")
    rig.core.call("check.answer", {"id": ask["id"], "answer": "yes"})
    assert rig.core.call("overview", {})["posts"][0]["questions"] == post["questions"] - 1


def test_a_post_that_went_out_is_listed_with_its_opening_only(make_rig):
    rig = make_rig(settings={"screen_snippet_chars": 40})
    page = rig.page()
    rig.through(page, "Solar roofs spread across the valley. " * 10)
    post = rig.core.call("overview", {})["posts"][0]
    assert len(post["body"]) > 40  # a draft is shown whole
    rig.core.call("post.approve", {"id": post["id"]})
    assert rig.store.post(post["id"])["state"] == "booked"  # Practice books at approval
    rig.store.move_post(post["id"], "went_out")  # as reconcile would
    listed = rig.core.call("overview", {})["posts"][0]
    assert listed["state"] == "went_out" and listed["cut"] is True
    assert len(listed["body"]) <= 41


def test_the_overview_gives_the_decline_reasons_from_the_settings(make_rig):
    rig = make_rig(settings={"decline_reasons": ["Off topic", " ", "Other"]})
    assert rig.core.call("overview", {})["decline_reasons"] == ["Off topic", "Other"]


def test_text_from_an_article_comes_back_as_it_was_never_changed_into_markup(rig):
    page = rig.page()
    hostile = '<img src=x onerror="alert(1)"> Solar roofs & "quotes".'
    rig.through(page, hostile)
    assert rig.core.call("overview", {})["posts"][0]["body"] == hostile


def test_every_call_the_page_makes_is_one_the_program_answers(rig):
    asked = set()
    for path in WEB.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        asked |= set(re.findall(r"""call\(\s*["']([a-z_.]+)["']""", text))
    assert asked, "the page makes no calls at all"
    known = set(calls(rig.ctx))
    assert asked - known == set()


def test_the_page_never_writes_html_from_text_and_never_uses_the_browsers_dialogs():
    for path in WEB.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                       "alert(", "confirm(", "prompt(", "eval(", "new Function"):
            assert banned not in text, f"{path.name} uses {banned}"


def test_the_page_finds_numbers_and_quotations_under_the_keys_the_checks_keep(rig):
    # The page's own test reads the same fixture and must find these same marks.
    import json  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    from posts import checks  # noqa: PLC0415
    from posts.checks import numbers, quotes  # noqa: PLC0415

    fixture = json.loads((APP_ROOT / "tests" / "fixtures" / "evidence.json")
                         .read_text(encoding="utf-8"))
    cfg = SimpleNamespace(number_tolerance_pct=1.0, many_numbers=12)
    given = checks.Material(sentences=fixture["sentences"])
    # Each number must sit in a sentence a claim cites, so the draft cites every sentence.
    cites = [{"text": "the article", "sentences": list(range(1, len(fixture["sentences"]) + 1))}]
    draft = checks.Draft(title="", body=fixture["body"], claims=cites)
    found = {(r.name, r.key): r for r in [*numbers.numbers(draft, given, cfg),
                                          *quotes.exact_quotes(draft, given, cfg)]}
    for mark in fixture["marks"]:
        name = "numbers" if mark["kind"] == "number" else "quotes"
        result = found[(name, mark["key"])]
        assert [e for e in result.evidence if isinstance(e, int)] == mark["sentences"], mark
        assert (result.level == "ok") == bool(mark["sentences"]), mark


def test_the_overview_tells_the_page_how_long_a_step_may_take_before_it_is_called_stuck(rig):
    seen = rig.core.call("overview", {})
    assert seen["slow_minutes"] == 30 and seen["now_ms"] > 0
