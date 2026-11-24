"""Each check on its own, then the checks together: they annotate and never block, run
again on every revision and edit, and a question can be answered."""

from __future__ import annotations

import pytest
from jimmyhq_app.testing import NUMBER_CASES, QUOTE_CASES
from posts import checks, config, sources
from posts.booking import booked_text
from posts.checks import Draft, Material, Result, numbers, owner, quotes, review, source, text
from posts_rig import ARTICLE, ARTICLE_TEXT

CFG = config.Config(config.defaults())
SENTENCES = sources.split_sentences(sources.clean(ARTICLE_TEXT))


def material(**more) -> Material:
    return Material(**{"sentences": SENTENCES, "source_url": ARTICLE, **more})


def levels(results: list[Result]) -> list[str]:
    return [r.level for r in results]


# --- numbers -----------------------------------------------------------------------------------


CITES_2 = [{"text": "1,240 homes", "sentences": [2]}]


def test_a_number_found_in_a_cited_sentence_names_the_sentence_it_is_in():
    [found] = numbers.numbers(Draft("", "Some 1,240 homes went solar.", claims=CITES_2),
                              material(), CFG)
    assert found.level == "ok" and found.evidence == [2]
    assert "sentence 2" in found.detail


def test_a_number_that_is_not_in_the_source_becomes_a_question_and_names_no_sentence():
    [found] = numbers.numbers(Draft("", "Some 1,300 homes went solar."), material(), CFG)
    assert found.level == "ask" and found.evidence == []
    assert "1,300" in found.detail and "sentence" not in found.detail.replace("sentences", "")


@pytest.mark.parametrize("written, source_says", [
    ("3.4", "3.42"), ("40%", "39.6%"), ("1.2 million", "1,234,567"), ("8,000", "7,950"),
])
def test_a_number_rounded_to_the_places_the_draft_writes_still_matches(written, source_says):
    draft = numbers.find_numbers(written)[0]
    said = numbers.find_numbers(source_says)[0]
    assert numbers.matches(draft, said, tolerance_pct=1.0)


def test_a_number_further_off_than_the_tolerance_does_not_match():
    assert not numbers.matches(numbers.find_numbers("45%")[0], numbers.find_numbers("38%")[0],
                               tolerance_pct=1.0)
    assert not numbers.matches(numbers.find_numbers("8,000")[0],
                               numbers.find_numbers("7,900")[0], tolerance_pct=1.0)


def test_every_number_is_checked_however_many_there_are():
    body = " ".join(str(1240) for _ in range(30)) + " and 999 more."
    found = numbers.numbers(Draft("", body, claims=CITES_2), material(), CFG)
    assert levels(found).count("ok") == 30
    assert any(r.level == "ask" and "999" in r.detail for r in found)
    assert any(r.key == "many" and r.level == "warn" for r in found)


def test_a_number_in_the_owners_notes_is_found_there():
    [found] = numbers.numbers(Draft("", "We planted 12 trees."),
                              material(notes="I planted 12 trees in March."), CFG)
    assert found.level == "ok" and found.evidence == ["notes"]


def test_a_number_in_a_draft_that_is_not_in_a_cited_sentence_becomes_a_question():
    draft = Draft("", "Waiting lists reach 14 weeks.", claims=[{"text": "x", "sentences": [2]}])
    [found] = numbers.numbers(draft, material(), CFG)
    assert found.level == "ask" and found.evidence == [6]
    assert "no claim in the draft cites" in found.detail


def test_a_number_in_the_source_with_no_claims_at_all_is_a_question_too():
    [found] = numbers.numbers(Draft("", "Waiting lists reach 14 weeks."), material(), CFG)
    assert found.level == "ask"


@pytest.mark.parametrize("written, said, tolerance, expected", NUMBER_CASES)
def test_posts_reads_numbers_by_the_cases_every_app_shares(written, said, tolerance, expected):
    cfg = config.Config({**config.defaults(), "number_tolerance_pct": tolerance})
    given = Material(sentences=[f"The figure was {said}."], source_url=ARTICLE)
    draft = Draft("", f"It was {written}.", claims=[{"text": "x", "sentences": [1]}])
    found = [r for r in numbers.numbers(draft, given, cfg) if r.key != "none"]
    assert [r.level for r in found] == (["ok"] if expected else ["ask"])


@pytest.mark.parametrize("sentence, span, expected", QUOTE_CASES)
def test_posts_reads_quotations_by_the_cases_every_app_shares(sentence, span, expected):
    given = Material(sentences=[sentence], source_url=ARTICLE)
    [found] = quotes.exact_quotes(Draft("", f"She said \"{span}\" then."), given, CFG)
    assert found.level == ("ok" if expected else "ask")


# --- quotes ------------------------------------------------------------------------------------


def test_a_quote_word_for_word_in_the_source_passes_whatever_its_quotation_marks():
    body = "The officer said “we have never seen demand like this.”"
    [found] = quotes.exact_quotes(Draft("", body), material(), CFG)
    assert found.level == "ok" and found.evidence == [5]


def test_an_attributed_quote_that_is_not_in_the_source_is_a_question():
    body = "\"Solar is the future of every home,\" said the officer."
    [found] = quotes.exact_quotes(Draft("", body), material(), CFG)
    assert found.level == "ask" and found.evidence == []


def test_quoted_words_with_no_attribution_that_are_not_in_the_source_are_a_warning():
    body = "Some call it \"the quiet revolution of our roofs\" these days."
    [found] = quotes.exact_quotes(Draft("", body), material(), CFG)
    assert found.level == "warn"


def test_a_word_or_two_in_quotation_marks_is_not_taken_as_a_quote():
    assert quotes.exact_quotes(Draft("", "A \"green\" choice."), material(), CFG) == []


def test_a_quote_from_the_owners_notes_passes():
    body = "As a neighbour told me: \"it paid for itself in a winter.\""
    [found] = quotes.exact_quotes(Draft("", body), material(
        notes="My neighbour: it paid for itself in a winter."), CFG)
    assert found.level == "ok" and found.evidence == ["notes"]


# --- the source --------------------------------------------------------------------------------


def test_a_source_on_a_weak_host_or_its_sub_hosts_is_a_warning():
    cfg = config.Config({**config.defaults(), "weak_hosts": ["example.com"]})
    found = source.source_strength(Draft("", "x"), material(), cfg)
    assert any(r.key == "weak" for r in found)


def test_a_short_source_is_a_warning_and_no_article_says_so():
    found = source.source_strength(Draft("", "x"), material(sentences=["Short."]), CFG)
    assert [r.key for r in found] == ["short"]
    [none] = source.source_strength(Draft("", "x"), material(source_url=None), CFG)
    assert none.level == "warn" and "only on your notes" in none.detail


@pytest.mark.parametrize("tier, level", [("strong", "ask"), ("close", "warn"), ("none", "ok")])
def test_a_near_duplicate_is_asked_when_strong_and_warned_when_close(tier, level):
    overlap = {"tier": tier, "method": "embeddings", "note": "", "best": {"what": "post",
                                                                          "id": 3}}
    [found] = source.near_duplicate(Draft("", "x"), material(overlap=overlap), CFG)
    assert found.level == level and found.evidence == ["post:3"]


def test_a_draft_whose_overlap_was_never_measured_says_so():
    [found] = source.near_duplicate(Draft("", "x"), material(), CFG)
    assert found.level == "warn" and found.key == "missing"


# --- the text ----------------------------------------------------------------------------------


def test_the_booked_text_is_plain_and_the_check_shows_what_was_stripped():
    body = "## News\n**Big** day. See [the story](https://news.example.com/a).\n- one"
    plain, removed = text.strip_markdown(body)
    assert plain == "News\nBig day. See the story.\none"
    [found] = text.markdown(Draft("", body), material(), CFG)
    assert found.level == "warn" and "It will read: News" in found.detail
    assert "a heading mark" in found.detail and "a list mark" in found.detail


def test_an_address_posts_did_not_write_is_pointed_out():
    draft = Draft("", "More at https://elsewhere.example.net/x", links=[ARTICLE])
    [found] = text.links(draft, material(), CFG)
    assert found.level == "warn" and "elsewhere.example.net" in found.detail


def test_runs_of_eight_words_shared_with_a_recent_draft_are_a_warning():
    old = "the panels paid for themselves in under nine years said most of the owners here"
    draft = Draft("", "Good news: the panels paid for themselves in under nine years said "
                      "most of the owners here today.")
    [found] = text.recycled(draft, material(recent=[(7, old)]), CFG)
    assert found.level == "warn" and found.evidence == ["post:7"]


# --- the owner's own ---------------------------------------------------------------------------


def test_a_sponsored_draft_without_its_tag_gets_it_when_booked():
    [found] = owner.sponsor(Draft("", "Thanks to our partner.", sponsored=True), material(),
                            CFG)
    assert found.level == "warn" and "#ad" in found.detail
    assert booked_text({"body": "Thanks to our partner."}, {"sponsored": 1}, "#ad").endswith(
        "\n\n#ad")


def test_a_draft_that_reads_sponsored_but_is_not_marked_is_a_question():
    [found] = owner.sponsor(Draft("", "Use promo code GROW for a discount."), material(), CFG)
    assert found.level == "ask"


def test_the_owners_phrases_tags_and_questions_are_kept():
    rules = [{"id": 1, "kind": "phrase", "text": "game changer", "says": "Too much."},
             {"id": 2, "kind": "tag", "text": "#garden", "says": ""},
             {"id": 3, "kind": "question", "text": "Is the date still right?", "says": ""}]
    found = owner.owner_rules(Draft("", "A game changer for roofs."),
                              material(rules=rules, page_tags=["#valley"]), CFG)
    assert sorted((r.key, r.level) for r in found) == [
        ("rule:1", "warn"), ("rule:3", "ask"), ("tag:#garden", "warn"), ("tag:#valley", "warn")]


def test_a_passage_the_owner_quoted_when_sending_back_is_asked_about_if_it_returns():
    [found] = owner.send_back_quotes(Draft("", "Truly a Game Changer."),
                                     material(send_back_quotes=["game changer"]), CFG)
    assert found.level == "ask"


# --- the checker -------------------------------------------------------------------------------


def test_a_checker_point_must_quote_the_draft_and_name_a_real_sentence():
    points = [{"quote": "38% more", "sentences": [3], "problem": "Fine as it is."},
              {"quote": "not in the draft", "sentences": [3], "problem": "Made up."},
              {"quote": "38% more", "sentences": [99], "problem": "No such sentence."}]
    found = review.checker_points(Draft("", "That is 38% more."),
                                  material(checker_points=points), CFG)
    assert [(r.level, r.evidence) for r in found] == [("warn", [3]), ("ok", [])]
    assert "2 of the checker's points were set aside" in found[1].detail


def test_checking_on_the_same_model_role_as_the_writer_is_a_warning():
    [found] = review.same_role(Draft("", "x"), material(roles=("smart", "smart")), CFG)
    assert found.level == "warn"
    assert review.same_role(Draft("", "x"), material(roles=("smart", "default")), CFG) == []


# --- together ----------------------------------------------------------------------------------


def test_every_check_answers_only_ok_warn_or_ask():
    with pytest.raises(ValueError):
        Result("x", "block", "No check may block.")
    found = checks.run_all(Draft("A", "Some 1,300 homes."), material(), CFG)
    assert set(levels(found)) <= {"ok", "warn", "ask"}


def draft_on_bench(rig, body):
    page = rig.page()
    piece = rig.through(page, body, claims=[{"text": "homes", "sentences": [2]}])
    return rig.store.post_of(piece["id"])


def test_a_question_can_be_answered_and_the_answer_is_kept(rig):
    post = draft_on_bench(rig, "Some 1,300 homes went solar.")
    [ask] = [c for c in rig.store.checks(post["id"]) if c["level"] == "ask"]
    rig.core.call("check.answer", {"id": ask["id"], "answer": "yes"})
    kept = rig.store.one("SELECT * FROM check_result WHERE id=?", (ask["id"],))
    assert kept["answer"] == "yes" and kept["answered"] == rig.clock.now
    [card] = rig.cards_of("draft")
    assert "you said yes" in card["body"] and "Every check passed." not in card["body"]


def test_an_answer_that_is_not_yes_no_or_fixed_is_refused(rig):
    post = draft_on_bench(rig, "Some 1,300 homes went solar.")
    [ask] = [c for c in rig.store.checks(post["id"]) if c["level"] == "ask"]
    with pytest.raises(Exception, match="yes, no or fixed"):
        rig.core.call("check.answer", {"id": ask["id"], "answer": "maybe"})


def test_the_checks_run_again_on_every_edit_and_the_card_is_filed_again(rig):
    post = draft_on_bench(rig, "Some 1,240 homes went solar.")
    [first] = rig.cards_of("draft")
    assert "question" not in first["body"].split("Source:")[1].lower()
    rig.core.call("post.edit", {"id": post["id"], "body": "Some 1,240 homes, and 77 schools."})
    edited = rig.store.post(post["id"])
    assert edited["revision"] == post["revision"] + 1
    assert any(c["level"] == "ask" and "77" in c["detail"] for c in rig.store.checks(post["id"]))
    [second] = rig.cards_of("draft")
    assert second["card_id"] != first["card_id"]
    assert rig.core.cards[first["card_id"]]["withdrawn"]


def test_a_yes_stays_with_its_question_on_the_next_revision_and_fixed_does_not(rig):
    post = draft_on_bench(rig, "Some 1,300 homes, and 77 schools.")
    asks = {c["key"]: c for c in rig.store.checks(post["id"]) if c["level"] == "ask"}
    rig.core.call("check.answer", {"id": asks["1,300"]["id"], "answer": "yes"})
    rig.core.call("check.answer", {"id": asks["77"]["id"], "answer": "fixed"})
    rig.core.call("post.edit", {"id": post["id"], "body": "Some 1,300 homes, and 77 schools!"})
    now = {c["key"]: c for c in rig.store.checks(post["id"]) if c["level"] == "ask"}
    assert now["1,300"]["answer"] == "yes" and now["77"]["answer"] is None


def test_approving_with_questions_open_is_allowed_and_the_card_says_how_many(rig):
    draft_on_bench(rig, "Some 1,300 homes and 77 schools went solar.")
    [card] = rig.cards_of("draft")
    assert "2 questions open" in card["body"] and card["summary"].startswith("2 questions open")
    rig.core.decide(card["card_id"], "approve")
    assert rig.store.all("SELECT state FROM post") == [{"state": "booked"}]
