"""The tool catalog: what a model is shown, and how what comes back is read."""

from __future__ import annotations

import pytest

from jimmyhq.village.catalog import (
    Catalog,
    Done,
    ToolError,
    as_list,
    mode_names,
    repair,
    salvage,
    unwrap,
    with_thought,
)
from jimmyhq.village.rules import RULES, Refusal, rule
from test_village_beat_fakes import some_tools


@pytest.fixture
def catalog():
    return Catalog(some_tools())


def test_modes_can_be_written_as_letters_or_as_names():
    assert mode_names("WEO") == {"work", "explore", "outlet"}
    assert mode_names("S") == {"social"}
    assert mode_names(("work", "social")) == {"work", "social"}
    assert mode_names(["W", "E"]) == {"work", "explore"}


def test_a_tool_is_offered_only_in_its_modes(catalog):
    assert "focus_on" in catalog.offered("work") and "focus_on" not in catalog.offered("explore")
    assert catalog.offered("social") == ["say"]
    assert catalog.offered("rest") == []


def test_a_tool_with_a_capability_is_offered_only_to_an_agent_that_has_it(catalog):
    assert "open_page" not in catalog.offered("work")
    assert "open_page" in catalog.offered("work", capabilities=["desktop"])
    assert "open_page" not in catalog.offered("social", capabilities=["desktop"])


def test_the_situation_can_offer_a_tool_outside_its_mode(catalog):
    assert "say" not in catalog.offered("work") and "say" in catalog.offered("work", extra=("say",))
    assert catalog.validate("say", {"text": "hi"}, "work", extra=("say",)) == {"text": "hi"}
    with pytest.raises(ToolError, match=r"say is not available right now \(work\)"):
        catalog.validate("say", {"text": "hi"}, "work")


def test_the_thought_is_the_first_required_argument_of_every_tool(catalog):
    for schema in catalog.schemas("work", capabilities=["desktop"], extra=("say",)):
        params = schema["parameters"]
        assert params["type"] == "object" and params["required"][0] == "thought"
        assert list(params["properties"])[0] == "thought", schema["name"]
    schema = with_thought(catalog.tools["write_file"])
    assert schema["name"] == "write_file" and schema["description"]
    assert schema["parameters"]["required"] == ["thought", "path", "content"]
    assert set(schema["parameters"]["properties"]) == {"thought", "path", "content", "replace"}


def test_an_unknown_tool_is_refused_by_name(catalog):
    with pytest.raises(ToolError, match="there is no tool called fly"):
        catalog.validate("fly", {}, "work")


def test_a_missing_or_empty_required_argument_is_named(catalog):
    with pytest.raises(ToolError, match="write_file needs content"):
        catalog.validate("write_file", {"path": "a.md"}, "work")
    with pytest.raises(ToolError, match="write_file needs content"):
        catalog.validate("write_file", {"path": "a.md", "content": ""}, "work")
    with pytest.raises(ToolError, match="the arguments were not readable"):
        catalog.validate("write_file", "path=a.md", "work")


def test_arguments_that_belong_to_another_tool_on_offer_name_that_tool(catalog):
    with pytest.raises(ToolError) as e:
        catalog.validate("write_diary", {"path": "a.md", "content": "x"}, "work")
    assert str(e.value) == ("write_diary needs text. Those arguments fit write_file: "
                            "use that instead")
    # A tool that is not on offer is never suggested.
    with pytest.raises(ToolError) as e:
        catalog.validate("write_diary", {"task": "T1"}, "explore")
    assert str(e.value) == "write_diary needs text"


def test_a_wrong_type_or_a_value_outside_the_allowed_ones_is_named(catalog):
    with pytest.raises(ToolError, match="read_file: start should be a integer"):
        catalog.validate("read_file", {"path": "a.md", "start": "later"}, "work")
    with pytest.raises(ToolError, match="kind must be one of need_help, good_at"):
        catalog.validate("post_to_board", {"kind": "gossip", "text": "x"}, "work")


def test_values_are_brought_to_the_type_the_tool_declares(catalog):
    clean = catalog.validate("read_file", {"path": 12, "start": "[30000]"}, "work")
    assert clean == {"path": "12", "start": 30000}
    clean = catalog.validate("write_file", {"path": "a", "content": "b", "replace": "yes"}, "work")
    assert clean["replace"] is True
    assert catalog.validate("write_file", {"path": "a", "content": "b", "extra": 1}, "work") == {
        "path": "a", "content": "b"}


def test_other_names_for_an_argument_are_understood(catalog):
    clean = catalog.validate("write_file", {"filename": "a.md", "body": "text"}, "work")
    assert clean == {"path": "a.md", "content": "text"}
    # The real name wins over another name for it.
    clean = catalog.validate("write_file", {"path": "a.md", "file": "b.md", "content": "x"},
                             "work")
    assert clean["path"] == "a.md"


def test_an_empty_optional_argument_counts_as_not_given(catalog):
    assert catalog.validate("read_file", {"path": "a.md", "start": ""}, "work") == {"path": "a.md"}
    assert catalog.validate("read_file", {"path": "a.md", "start": None}, "work") == {
        "path": "a.md"}


def test_a_list_sent_as_text_is_read_as_a_list(catalog):
    assert as_list("['a.md', 'b.md']") == ["a.md", "b.md"]
    assert as_list('["a.md"]') == ["a.md"] and as_list("a.md") == ["a.md"]
    clean = catalog.validate("post_to_board", {"kind": "good_at", "text": "x",
                                               "tags": "['maps', 'tea']"}, "work")
    assert clean["tags"] == ["maps", "tea"]


def test_arguments_sent_inside_a_wrapper_are_unwrapped(catalog):
    params = catalog.tools["write_diary"].params
    assert unwrap({"arguments": '{"text": "hi"}'}, params) == {"text": "hi"}
    assert unwrap({"args": {"text": "hi"}}, params) == {"text": "hi"}
    assert unwrap({"name": "write_diary", "arguments": '{"text": "hi"}'}, params) == {
        "text": "hi"}
    assert unwrap({"input": "not json"}, params) == {"input": "not json"}
    assert unwrap({"text": "hi"}, params) == {"text": "hi"}
    assert catalog.validate("write_diary", {"parameters": '{"text": "hi"}'}, "work") == {
        "text": "hi"}


def test_arguments_written_as_markup_in_the_thought_are_recovered(catalog):
    tool = catalog.tools["write_file"]
    thought = ('I will save it.</thinking><parameter name="path">notes/a.md</parameter>'
               '<parameter name="content">line one\nline two</parameter>'
               '<parameter name="colour">blue</parameter>')
    assert salvage(thought, tool.params) == {"path": "notes/a.md",
                                             "content": "line one\nline two"}
    args, cleaned = repair(tool, {}, thought)
    assert args["path"] == "notes/a.md" and cleaned == "I will save it."
    whole = 'Saving.<parameter name="args">{"path": "b.md", "content": "x", "odd": 1}'
    assert salvage(whole, tool.params) == {"path": "b.md", "content": "x"}
    # Arguments that did arrive are left alone, and so is the thought.
    assert repair(tool, {"path": "c.md"}, thought) == ({"path": "c.md"}, thought)


def test_a_rule_is_registered_by_name_with_the_setting_that_decides_it():
    @rule("test_only_rule", "village.test.number")
    def judged() -> bool:
        return True

    assert RULES["test_only_rule"].fn is judged
    assert RULES["test_only_rule"].setting == "village.test.number"
    with pytest.raises(ValueError, match="already"):
        @rule("test_only_rule")
        def another() -> bool:
            return False
    del RULES["test_only_rule"]


def test_a_refusal_carries_its_rule_its_sentence_and_whether_it_frustrates():
    r = Refusal("day_limit", "You have sent 10 messages today, so nothing was sent.",
                frustrate=False, args_logged={"length": 12})
    assert (r.rule, r.frustrate, r.args_logged) == ("day_limit", False, {"length": 12})
    assert str(r) == r.sentence
    assert Refusal("x", "y").frustrate is True and Refusal("x", "y").args_logged is None


def test_what_a_tool_did_defaults_to_a_successful_action_of_the_agents_own():
    done = Done("noted")
    assert (done.result, done.args_logged, done.category, done.ok) == ("noted", None, "self",
                                                                       True)
