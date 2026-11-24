"""Each rule the agents' side keeps, on its own: how a tool reads what an agent gave, and
what the code decides from it. Sample text is invented."""

from __future__ import annotations

import pytest
from articles import checks, lessons, tools, work
from articles.checks import Draft, Material
from articles.checks.text import owner_flagged
from articles_rig import SURVEY

SENTENCES = {(1, 1): SURVEY[0], (1, 4): SURVEY[3], (2, 2): "Yields rose by 9%."}


# --- reading what an agent gave -------------------------------------------------------------


@pytest.mark.parametrize(("given", "cites"), [
    ("S2:5", [(2, 5)]), ("S2:5,7", [(2, 5), (2, 7)]), ("S1:3; S4:2", [(1, 3), (4, 2)]),
    ("[s1:2-4]", [(1, 2), (1, 3), (1, 4)]), ([{"source": 1, "sentence": 2}], [(1, 2)]),
    (["S3:1"], [(3, 1)]), ("sentence five", []), ([{"source": True, "sentence": 1}], [])])
def test_citations_are_read_in_every_form_an_agent_writes_them(given, cites):
    assert tools.parse_cites(given) == cites


def test_statements_come_one_a_line_or_as_json_and_a_line_with_no_brackets_cites_nothing():
    lines = tools.parse_cited_lines("- Plots save 18% [S1:1].\nGardens are good.", "x")
    assert lines == [{"text": "Plots save 18%", "cites": [(1, 1)]},
                     {"text": "Gardens are good.", "cites": []}]
    as_json = tools.parse_cited_lines('[{"text": "Plots save 18%", "cites": ["S1:1"]}]', "x")
    assert as_json == [{"text": "Plots save 18%", "cites": [(1, 1)]}]
    with pytest.raises(tools.Refused, match="starts like a JSON list but is not one"):
        tools.parse_cited_lines("[not json", "statements")


def test_a_summary_is_refused_for_each_statement_that_cites_nothing_or_what_is_not_there():
    given = [{"text": "Plots save 18%", "cites": [(1, 1)]},
             {"text": "Gardens are good", "cites": []},
             {"text": "Waiting lists grew", "cites": [(1, 6)]}, {"text": "", "cites": [(1, 1)]}]
    problems = tools.check_statements(given, SENTENCES, 10)
    assert problems == ["statement 2 (“Gardens are good”) cites nothing",
                        "statement 3 cites S1:6, which no source has",
                        "statement 4 has no text"]
    assert tools.check_statements(given[:1], SENTENCES, 10) == []
    assert "at most 0 are kept" in tools.check_statements(given[:1], SENTENCES, 0)[0]
    assert tools.check_statements([], SENTENCES, 10) == ["there are no statements"]


def test_findings_quote_name_sentences_or_say_none_and_take_a_kind():
    found = tools.parse_findings('"spent 30% less" | S1:1 | It says 18%.\n'
                                 'left_out | "grew 41 kilograms" | S1:8 | Unused plots.\n'
                                 '"everyone saves" | none | Nothing says so.\njust a remark')
    assert [f["kind"] for f in found] == ["other", "left_out", "unsupported", "unsupported"]
    assert found[0]["cites"] == [{"source": 1, "sentence": 1}] and found[2]["cites"] == []
    assert found[3]["problem"] == ""  # set aside later: it says nothing is wrong


def test_a_claim_given_apart_is_marked_after_its_words_in_the_body():
    body = "<p>Families with a garden plot spent 18% less on vegetables. More.</p>"
    marked, notes = tools.mark_claims(body, [
        {"text": "Families with a garden plot spent 18% less on vegetables.",
         "cites": [(1, 1), (9, 9)]},
        {"text": "Not in the body", "cites": [(1, 4)]},
        {"text": "More", "cites": [(5, 5)]}], SENTENCES)
    assert "spent 18% less on vegetables [S1:1]." in marked
    assert notes == ["claim 1 cites S9:9, which no source has, so that citation was left out",
                     "claim 2 (“Not in the body”) is not in the body word for word, so no "
                     "mark was added",
                     "claim 3 cites S5:5, which no source has, so that citation was left out",
                     "claim 3 cites no sentence a source has, so no mark was added"]


def test_pages_hold_whole_lines_and_say_where_the_next_starts():
    lines = [f"line {n} " + "x" * 40 for n in range(1, 11)]
    first, after = tools.paged(lines, 1, 200)
    assert first.count("\n") == 3 and after == 5
    rest, end = tools.paged(lines, 9, 200)
    assert rest.startswith("line 9") and end is None
    huge, _ = tools.paged(["y" * 500], 1, 100)
    assert len(huge) == 100


def test_an_outline_comes_as_lines_or_json_and_needs_a_title_and_a_heading():
    found = tools.parse_outline("Plots and prices\n## The savings\n- 18% less\nmore words\n"
                                "## The catch\n")
    assert found == {"title": "Plots and prices", "sections": [
        {"heading": "The savings", "points": ["18% less", "more words"]},
        {"heading": "The catch", "points": []}]}
    assert tools.parse_outline('{"title": "T", "sections": [{"heading": "H", "points": []}]}')
    with pytest.raises(tools.Refused, match="needs a title and at least one section"):
        tools.parse_outline("Only a title")


def test_a_tool_called_through_another_role_is_refused_and_the_whole_app_grant_is_not():
    assert tools.role_may(None, "writer", "brief") is None
    assert tools.role_may("writer", "writer", "brief") is None
    said = tools.role_may("checker", "writer", "brief")
    assert said.startswith("You called app_articles_brief as the checker") and \
        "Nothing was done" in said


def test_a_whole_number_is_read_with_or_without_its_s_and_anything_else_is_refused():
    assert tools.whole({"source": "S2"}, "source") == 2
    assert tools.whole({"piece": 3}, "piece") == 3
    with pytest.raises(tools.Refused, match="must be a whole number"):
        tools.whole({"piece": "1; DROP TABLE piece"}, "piece")
    with pytest.raises(tools.Refused, match="You did not say which piece"):
        tools.whole({}, "piece")
    assert tools.flag("publish") is True and tools.flag("false") is False


# --- lessons --------------------------------------------------------------------------------


def test_a_quoted_span_in_the_owners_note_becomes_a_phrase_to_flag():
    note = 'Cut “a real game changer” and "at the end of the day"; keep ‘ok’ as is.'
    assert lessons.quoted_spans(note) == ["a real game changer", "at the end of the day"]


def test_a_flagged_phrase_that_comes_back_is_a_question():
    draft = Draft("Plots", "<p>It is a real  Game changer for families [S1:1].</p>")
    found = owner_flagged(draft, Material(flagged=("a real game changer", "not here")), None)
    assert [(r.level, r.key) for r in found] == [("ask", "flagged:a real game changer")]
    assert owner_flagged(draft, Material(), None) == []
    assert owner_flagged in checks.all_checks()


def test_the_shape_of_an_article_says_how_it_opens_runs_and_ends():
    body = ("<p>Families saved 18% [S1:1].</p><h2>Why</h2><p>Plots grow food.</p>"
            "<ul><li>One</li></ul><p>Would you try one?</p>")
    assert lessons.shape(body) == ("3 paragraphs under 1 heading, 1 list, opens with a "
                                   "figure, ends with a question, 12 words")
    assert lessons.opens_with("<p>Why grow food?</p>") == "a question"


# --- budgets, stalls and the owner's choices ------------------------------------------------


def test_a_step_is_late_once_its_hours_have_passed(rig):
    row = {"step": "reviewing", "given": rig.clock.now}
    assert not work.stalled_for(rig.ctx, row)
    rig.clock.advance(12 * 3_600_000)
    assert work.stalled_for(rig.ctx, row)  # stall_hours_check
    assert not work.stalled_for(rig.ctx, {**row, "step": "writing"})  # stall_hours_write


def test_waiting_is_not_offered_once_the_budget_is_spent_or_the_agent_ended_it():
    ids = [[a["id"] for a in work.choices({"state": s})] for s in ("open", "budget", "done")]
    assert ids == [["wait", "finish_plain", "drop"], ["finish_plain", "drop"],
                   ["finish_plain", "drop"]]


def test_research_finished_the_plain_way_starts_again_at_the_search():
    assert work.plain_state("researching") == "searching"
    assert work.plain_state("reviewing") == "reviewing"


def test_a_submission_past_the_budget_is_taken_and_not_counted(make_rig):
    rig = make_rig(settings={"agent_steps_per_piece": 1})
    piece_id = 7
    assert work.spend(rig.ctx, piece_id, "agent-a", "writer", "brief") is None
    said = work.spend(rig.ctx, piece_id, "agent-a", "writer", "brief")
    assert said.startswith("Article 7's budget of 1 agent steps is spent") and \
        "app_articles_submit_article" in said
    assert work.spend(rig.ctx, piece_id, "agent-a", "writer", "submit_article",
                      submit=True) is None
    assert work.used(rig.ctx, piece_id) == 1
