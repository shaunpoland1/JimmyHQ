"""Each claims check on its own, the sanitiser, and the checks together: they annotate and
never block, and every number is checked however many there are."""

from __future__ import annotations

import pytest
from articles import checks, config, markup, render
from articles.checks import Draft, Material, claims, text
from articles.config import Config
from articles_rig import COOK, FORUM, SOIL, SURVEY, TEST_SETTINGS, text_of
from jimmyhq_app.testing import NUMBER_CASES, QUOTE_CASES

CFG = Config({**config.defaults(), **TEST_SETTINGS})


def source(n: int, sentences: list[str], host: str = "survey.example.com",
           weak: bool = False) -> dict:
    return {"n": n, "url": f"https://{host}/{n}", "final_url": f"https://{host}/{n}",
            "host": host, "title": f"Source {n}", "text": text_of(sentences),
            "sentences": sentences, "weak": int(weak)}


MATERIAL = Material(sources={1: source(1, SURVEY), 2: source(2, SOIL, "soil.example.com"),
                             3: source(3, COOK, "kitchen.example.com"),
                             4: source(4, FORUM, "forum.example.com", weak=True)})


def draft(body: str, title: str = "Plots") -> Draft:
    clean = markup.sanitise(body, set(MATERIAL.sources))
    return Draft(title, clean.html, clean.removed)


def levels(results) -> list[str]:
    return [r.level for r in results]


# --- numbers --------------------------------------------------------------------------------


def test_a_number_in_a_cited_sentence_names_where_it_is():
    [found] = claims.numbers_in_cited_sentences(draft("<p>They spent 18% less [S1:1].</p>"),
                                                MATERIAL, CFG)
    assert found.level == "ok" and found.evidence == ["S1:1"]


def test_a_number_that_is_in_no_cited_sentence_becomes_a_question():
    [found] = claims.numbers_in_cited_sentences(
        draft("<p>Waiting lists grew by 25% [S1:1].</p>"), MATERIAL, CFG)
    assert found.level == "ask" and found.evidence == ["S1:6"]
    assert "which this sentence does not cite" in found.detail


def test_a_number_that_is_in_no_source_at_all_is_a_question_naming_none():
    [found] = claims.numbers_in_cited_sentences(
        draft("<p>They spent 47% less [S1:1].</p>"), MATERIAL, CFG)
    assert found.level == "ask" and found.evidence == []
    assert "in no sentence of any source" in found.detail


def test_the_ninth_number_is_checked_like_the_first():
    good = [("1,200", "S1:1"), ("18%", "S1:1"), ("2026", "S1:2"), ("41", "S1:4"),
            ("340", "S1:5"), ("25%", "S1:6"), ("30", "S1:7"), ("12%", "S1:8")]
    body = "".join(f"<p>A figure: {number} [{cite}].</p>" for number, cite in good)
    body += "<p>A figure: 99 [S1:1].</p><p>A figure: 9% [S2:2].</p>"
    found = claims.numbers_in_cited_sentences(draft(body), MATERIAL, CFG)
    assert len(found) == 10
    assert levels(found) == ["ok"] * 8 + ["ask", "ok"]
    assert "“99”" in found[8].detail


def test_a_number_in_the_title_or_a_heading_must_be_in_a_sentence_the_article_cites():
    found = claims.numbers_in_cited_sentences(
        draft("<h2>Why 18% matters</h2><p>They spent less [S1:1].</p>",
              title="Plots save 60%"), MATERIAL, CFG)
    by_text = {r.detail.split("”")[0].split("“")[-1]: r.level for r in found}
    assert by_text == {"60%": "ask", "18%": "ok"}


@pytest.mark.parametrize("written, said, tolerance, expected", NUMBER_CASES)
def test_articles_reads_numbers_by_the_cases_every_app_shares(written, said, tolerance, expected):
    given = Material(sources={1: source(1, [f"The figure was {said}."])})
    cfg = Config({**CFG.values, "number_tolerance_pct": tolerance})
    found = claims.numbers_in_cited_sentences(Draft("T", f"<p>It was {written} [S1:1].</p>"),
                                              given, cfg)
    assert levels(found) == (["ok"] if expected else ["ask"])


# --- quotations -------------------------------------------------------------------------------


def test_a_quotation_word_for_word_in_a_source_passes():
    [found] = claims.exact_quotes(draft("<p>Dr. Rivera said: “Most plots paid for their seeds "
                                        "within six weeks.” [S1:3]</p>"), MATERIAL, CFG)
    assert found.level == "ok" and found.evidence == ["S1:3"]


def test_a_quotation_that_is_not_word_for_word_in_a_source_becomes_a_question():
    [found] = claims.exact_quotes(draft("<p>Dr. Rivera said: “Most plots paid for "
                                        "themselves in weeks.” [S1:3]</p>"), MATERIAL, CFG)
    assert found.level == "ask" and "Did someone say" in found.detail


def test_quoted_words_with_no_attribution_that_are_not_in_a_source_are_a_warning():
    [found] = claims.exact_quotes(draft("<p>Some call it “the quiet green revolution” [S1:1].</p>"),
                                  MATERIAL, CFG)
    assert found.level == "warn"


@pytest.mark.parametrize("sentence, span, expected", QUOTE_CASES)
def test_articles_reads_quotations_by_the_cases_every_app_shares(sentence, span, expected):
    given = Material(sources={1: source(1, [sentence])})
    [found] = claims.exact_quotes(Draft("T", f"<p>She said \"{span}\" then [S1:1].</p>"),
                                  given, CFG)
    assert found.level == ("ok" if expected else "ask")


# --- names, uncited claims, advice, citations -----------------------------------------------


def test_someone_said_to_have_said_something_must_be_named_in_a_cited_sentence():
    good, bad = claims.named_in_cited_sentences(draft(
        "<p>Dr. Rivera said most plots paid for their seeds [S1:3]. "
        "The Valley Growers Network found plots save money [S1:1].</p>"), MATERIAL, CFG)
    assert good.level == "ok" and "Rivera" in good.detail
    assert bad.level == "ask" and "Valley Growers Network" in bad.detail
    assert bad.evidence == ["S1:1"]


def test_names_are_read_without_the_words_that_open_a_sentence():
    assert claims.names("The Valley Growers Network found more.") == ["Valley Growers Network"]
    assert claims.names("In March the council said so.") == []
    assert claims.names("According to Dr. Rivera, plots pay.") == ["Rivera"]


def test_a_claim_with_no_citation_at_all_is_a_question():
    found = claims.uncited_claims(draft("<p>Plots save families 18% on vegetables. Gardens "
                                        "are lovely.</p><h2>Plots in 2026</h2>"), MATERIAL, CFG)
    assert [r.level for r in found] == ["ask"]
    assert "cites nothing" in found[0].detail


def test_a_citation_of_a_sentence_no_source_has_counts_for_nothing():
    found = claims.cites_are_real(draft("<p>They spent less [S1:99; S7:1].</p>"), MATERIAL, CFG)
    assert [r.level for r in found] == ["warn"] and "S1:99, S7:1" in found[0].detail
    [number] = claims.uncited_claims(draft("<p>They spent 18% less [S7:1].</p>"), MATERIAL, CFG)
    assert number.level == "ask"


@pytest.mark.parametrize("sentence, topic", [
    ("You should stop taking your medication before planting.", "health"),
    ("Always read the contract before you rent a plot.", "the law"),
    ("Never put your savings into seeds.", "money"),
])
def test_advice_presented_as_fact_about_health_law_or_money_is_a_question(sentence, topic):
    [found] = claims.advice_as_fact(draft(f"<p>{sentence}</p>"), MATERIAL, CFG)
    assert found.level == "ask" and f"about {topic}" in found.detail


def test_a_plain_statement_with_a_topic_word_is_not_advice():
    assert claims.advice_as_fact(draft("<p>The survey asked about money [S1:1].</p>"),
                                 MATERIAL, CFG) == []


def test_citing_a_weak_source_is_a_warning():
    [found] = claims.weak_sources_cited(draft("<p>Some saved hundreds [S4:3].</p>"),
                                        MATERIAL, CFG)
    assert found.level == "warn" and "forum.example.com" in found.detail


def test_a_mark_after_the_full_stop_belongs_to_its_own_sentence():
    found = markup.claims("<p>They spent 18% less. [S1:1] Plots cost 30 dollars. [S1:7]</p>")
    assert [(c.text, c.cites) for c in found] == [("They spent 18% less.", ((1, 1),)),
                                                  ("Plots cost 30 dollars.", ((1, 7),))]
    assert markup.parse_mark("S1:3-5, 8; S2:1") == [(1, 3), (1, 4), (1, 5), (1, 8), (2, 1)]


# --- markup and links -------------------------------------------------------------------------


def test_markup_outside_the_allowed_set_is_removed():
    clean = markup.sanitise('<div class="x"><p style="color:red" onclick="go()">Hi '
                            '<span>there</span></p><script>steal()</script>'
                            '<img src="https://cdn.example.net/x.png"><table><tr><td>t</td>'
                            '</tr></table></div>', {1})
    assert "<div" not in clean.html and "<script" not in clean.html and "steal" not in clean.html
    assert "style=" not in clean.html and "onclick" not in clean.html and "<img" not in clean.html
    assert "<span" not in clean.html and "Hi there" in clean.html
    assert any("<script>" in r for r in clean.removed)
    [found] = text.markup_allowed(Draft("T", clean.html, clean.removed), MATERIAL, CFG)
    assert found.level == "warn" and "<div> tags" in found.detail


def test_a_link_that_is_not_a_source_cannot_reach_the_article():
    clean = markup.sanitise('<p>See <a href="https://made-up.example.net/x">this</a>, '
                            '<a href="S9">that</a>, <a href="S2" title="t">the trial</a> and '
                            'www.made-up.example.net/y or javascript:alert(1).</p>', {1, 2})
    assert "made-up" not in clean.html and 'href="S9"' not in clean.html
    assert '<a href="S2">the trial</a>' in clean.html
    assert clean.html.count("<a ") == 1
    found = text.links_are_sources(Draft("T", clean.html, clean.removed), MATERIAL, CFG)
    assert {r.level for r in found} == {"warn"} and len(found) == 3


def test_the_published_article_has_real_addresses_and_lists_only_what_it_cited():
    body = markup.sanitise('<p>They spent 18% less [S1:1]. <a href="S3">Cooks</a> agree '
                           '[S3:1].</p>', {1, 2, 3}).html
    out = render.final_html("Plots", body, MATERIAL.sources)
    assert "[S1:1]" not in out and 'href="https://kitchen.example.com/3"' in out
    listed = out[out.index("<h2>Sources"):]
    assert listed.index("survey.example.com/1") < listed.index("kitchen.example.com/3")
    assert "soil.example.com" not in out
    numbered = render.final_html("Plots", body, MATERIAL.sources, cite_style="numbers")
    assert "They spent 18% less [1]." in numbered
    md = render.to_markdown(out)
    assert md.startswith("# Plots") and "[Cooks](https://kitchen.example.com/3)" in md
    assert "1. [Source 1](https://survey.example.com/1)" in md


# --- together -------------------------------------------------------------------------------


def test_the_checks_annotate_and_never_block():
    results = checks.run_all(draft("<p>They spent 47% less, a survey said.</p>"), MATERIAL, CFG)
    assert checks.open_count(results) >= 2
    assert all(r.level in checks.LEVELS for r in results)
