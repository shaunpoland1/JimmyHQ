"""The named rules and readers behind the agent tools, each on its own: how claims, links
and points are read from what an agent wrote, who may call what, and the small pieces of
text the tools are built from."""

from __future__ import annotations

import pytest
from posts import credits, lessons, tools
from posts.checks.review import usable_point, usable_points


def test_claims_are_read_one_a_line_with_their_sentence_numbers_in_brackets():
    found = tools.parse_claims("1,240 homes added panels [2]\n- 38% more [3, 4]\nno cite")
    assert found == [{"text": "1,240 homes added panels", "sentences": [2]},
                     {"text": "38% more", "sentences": [3, 4]},
                     {"text": "no cite", "sentences": []}]


def test_claims_may_come_as_a_json_list_and_a_broken_one_is_refused_with_an_example():
    assert tools.parse_claims('[{"text": "a", "sentences": [1, "x", true]}]') == [
        {"text": "a", "sentences": [1]}]
    with pytest.raises(tools.Refused, match="one claim per line"):
        tools.parse_claims("[not json")


def test_too_many_claims_are_refused():
    with pytest.raises(tools.Refused, match="keep it to"):
        tools.parse_claims("\n".join(f"claim [{n}]" for n in range(tools.MAX_CLAIMS + 1)))


@pytest.mark.parametrize("given, read", [
    ("1", [1]), ("1, 2", [1, 2]), ("[1, 3]", [1, 3]), (2, [2]), ([1, True, "x"], [1]),
    ("", []), (None, []), (True, []),
])
def test_article_and_sentence_numbers_are_read_from_text_numbers_or_lists(given, read):
    assert tools.parse_numbers(given) == read


def test_points_are_read_as_quote_sentences_and_problem_and_none_means_unsupported():
    found = tools.parse_points("\"38% more\" | 3 | It was homes.\n“1,240 homes” | none | No "
                               "sentence says it.\nnot a point")
    assert found[0] == {"quote": "38% more", "sentences": [3], "problem": "It was homes.",
                        "none": False}
    assert found[1]["none"] is True and found[1]["sentences"] == []
    assert found[2]["problem"] == ""
    assert tools.parse_points('[{"quote": "a b", "sentences": "none", "problem": "p"}]')[0][
        "none"] is True


def test_a_point_must_quote_the_draft_say_what_is_wrong_and_cite_or_say_none():
    body = "Some 1,240 homes went solar."
    assert usable_point({"quote": "1,240 homes", "sentences": [2], "problem": "x"}, body, 10)
    assert usable_point({"quote": "1,240 homes", "sentences": [], "none": True,
                         "problem": "x"}, body, 10)["none"] is True
    assert usable_point({"quote": "1,240 homes", "sentences": [], "problem": "x"}, body,
                        10) is None
    assert usable_point({"quote": "not there", "sentences": [2], "problem": "x"}, body,
                        10) is None
    assert usable_point({"quote": "1,240 homes", "sentences": [2], "problem": " "}, body,
                        10) is None
    assert usable_point({"quote": "1,240 homes", "sentences": [99], "problem": "x"}, body,
                        10) is None
    assert usable_points([{"quote": "nope", "sentences": [1], "problem": "x"}], body, 10) \
        == ([], 1)


def test_a_tool_called_through_another_role_is_refused_and_says_whose_it_is():
    assert tools.role_may(None, "writer", "brief") is None
    assert tools.role_may("writer", "writer", "brief") is None
    said = tools.role_may("researcher", "writer", "submit_draft")
    assert "belongs to the writer" in said and "Nothing was done" in said


def test_a_whole_number_is_asked_for_by_name_when_it_is_missing_or_not_a_number():
    with pytest.raises(tools.Refused, match="which piece"):
        tools.whole({}, "piece")
    with pytest.raises(tools.Refused, match="whole number"):
        tools.whole({"piece": "seven"}, "piece")
    assert tools.whole({"piece": "7"}, "piece") == 7
    assert tools.whole({}, "start", required=False) is None


@pytest.mark.parametrize("given, read", [(None, True), ("", True), (False, False),
                                          ("false", False), ("no", False), ("yes", True)])
def test_ready_is_read_from_a_boolean_or_its_words(given, read):
    assert tools.flag(given) is read


def test_long_material_is_cut_to_what_one_answer_may_hold_and_says_so():
    cut = tools.fit("x" * 5000, 1000)
    assert len(cut) <= 1000 and cut.endswith("fit what one answer may hold.)")
    assert tools.fit("short", 1000) == "short"


def test_sentences_are_paged_by_number_and_say_where_the_next_page_starts():
    piece = {"sentences": [f"Sentence {n} here." for n in range(1, 11)]}
    text, after = tools.sentences_page(piece, 1, 60)
    assert text == "[1] Sentence 1 here.\n[2] Sentence 2 here." and after == 3
    rest, end = tools.sentences_page(piece, after, 10_000)
    assert rest.startswith("[3] ") and end is None


def test_the_owners_edit_is_shown_as_what_they_changed():
    said = lessons.changes("Solar roofs are booming in the valley.",
                           "Solar roofs are spreading in the valley today.")
    assert said == ["changed “booming” to “spreading”", "changed “valley.” to “valley today.”"]
    assert lessons.changes("Same text.", "Same text.") == []


def test_the_shape_of_a_draft_names_its_opening_ending_and_length():
    said = lessons.shape("Why now?\n\n- one\n- two\n\nThat is all.")
    assert said.startswith("3 paragraphs, opens with a question, ends with a statement")
    assert "a list of 2" in said and said.endswith("words")
    assert lessons.opens_with("1,240 homes went solar.") == "a number"
    assert lessons.opens_with("“Never,” she said.") == "a quotation"
    assert lessons.opens_with("We went solar.") == "the first person"


def test_the_who_worked_line_counts_a_step_done_more_than_once(make_rig):
    rig = make_rig()
    page = rig.page()
    piece = rig.core.call("new.topic", {"page_id": page["id"], "topic": "Roofs"})
    for _ in range(2):
        credits.add(rig.store, piece["id"], "writing", "agent", role="writer", label="Wren",
                    agent="agent:wren")
    credits.add(rig.store, piece["id"], "checking", "plain")
    assert credits.line(rig.store, piece["id"]) == (
        "Found by you. Written by the writer (Wren), twice. Checked the plain way.")
    assert credits.agents_on(rig.store, piece["id"], "writing") == {"agent:wren"}
