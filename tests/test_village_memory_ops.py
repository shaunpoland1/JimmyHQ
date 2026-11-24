"""The sleeping mind's answer: read leniently, applied strictly."""

from __future__ import annotations

import json

import pytest

from jimmyhq.village.memory import ops
from jimmyhq.village.memory.store import MemoryStore
from jimmyhq.village.world import MemoryLog

ME = "agent-a"
DAY = "2026-01-05"


@pytest.fixture
def log():
    return MemoryLog()


def run(log, store, todo, **kw):
    return ops.apply_ops(log, ME, store, todo, kw.pop("day", DAY), "smart", **kw)


def test_a_memory_id_is_read_as_a_number_however_the_model_writes_it():
    assert [ops.mem_id(x) for x in (6, "6", "#6", " # 6 ")] == [6, 6, 6, 6]
    with pytest.raises(ValueError):
        ops.mem_id("six")


def test_an_answer_wrapped_in_a_fence_or_stray_words_is_still_read():
    answer = {"operations": [{"op": "drop", "id": 1}]}
    assert ops.parse_answer("```json\n" + json.dumps(answer) + "\n```") == answer
    assert ops.parse_answer("Here it is: " + json.dumps(answer) + " Done.") == answer
    with pytest.raises(ValueError):
        ops.parse_answer("nothing useful")


def test_an_answer_cut_off_part_way_keeps_every_whole_operation():
    cut = ('{"operations": [{"op": "add", "section": "facts", "text": "one"}, '
           '{"op": "add", "section": "facts", "text": "two"}, {"op": "add", "sec')
    got = ops.parse_answer(cut)
    assert [o["text"] for o in got["operations"]] == ["one", "two"] and got["salvaged"]
    assert ops.salvage_operations('{"notes": "no ops here"') is None


def test_salvage_reads_whole_operations_wherever_the_answer_was_cut():
    one = {"op": "add", "section": "facts", "text": "braces {like these} and a ] too"}
    two = {"op": "drop", "id": 4}
    whole = json.dumps({"notes": "first", "operations": [one, two],
                        "impressions": [{"about": "owner", "text": "Kind."}]})
    for cut in (whole[:whole.index('"impressions"') + 30],  # inside the impressions
                whole[:whole.index('"id": 4}') + 8] + ', {"op": "ad',  # inside a third op
                "Here you go:\n```json\n" + whole[:-3]):  # stray words, the end missing
        assert ops.salvage_operations(cut)["operations"] == [one, two], cut
    assert ops.salvage_operations(whole[:-2])["impressions"] == [
        {"about": "owner", "text": "Kind."}]
    assert ops.salvage_operations(whole[:whole.index('"text"')]) is None  # not one whole op
    assert ops.salvage_operations('{"notes": "the \\"operations\\": [{}] were long"') is None
    assert ops.salvage_operations("") is None


def test_an_operation_the_model_repeats_is_applied_once():
    a = {"op": "add", "section": "facts", "text": "one"}
    assert ops.unique_ops([a, dict(a), {"text": "one", "section": "facts", "op": "add"},
                           {"op": "drop", "id": 2}]) == [a, {"op": "drop", "id": 2}]


def test_the_schema_caps_the_lists_and_requires_impression_text():
    props = ops.SCHEMA["properties"]
    assert props["operations"]["maxItems"] == 60
    assert props["operations"]["items"]["properties"]["evidence"]["maxItems"] == 6
    imp = props["impressions"]["items"]
    assert imp["required"] == ["about", "text"] and imp["properties"]["text"]["minLength"] == 1


def test_each_line_gets_its_source_from_what_it_really_cites(tmp_path, log):
    said = log.append("chat", ME, 1, sender="owner", text="You lead the newsletter.")
    did = log.append("action", ME, 2, tool="write_file", ok=True)
    s = MemoryStore(tmp_path)
    out = run(log, s, [
        {"op": "add", "section": "story", "text": "I lead the newsletter", "weight": "core",
         "evidence": [said.id, "evt-404"]},
        {"op": "add", "section": "skills", "text": "Drafting outlines quickly",
         "evidence": [did.id]},
        {"op": "add", "section": "facts", "text": "Maple harbour opens at dawn",
         "weight": "core", "evidence": ["evt-404"]},
        {"op": "add", "section": "facts", "text": "Queued work goes out next morning",
         "evidence": ["changes"]},
        {"op": "add", "section": "facts", "text": "Unconfirmed (web): owner ran a bakery"},
    ], news_shown=True)
    assert out.rejected == 0
    assert [(i["source"], i["weight"], i["evidence"]) for i in s.items] == [
        ("owner", "core", [said.id]), ("seen", "useful", [did.id]),
        ("self-reported", "useful", []), ("village", "useful", []),
        ("web:unconfirmed", "useful", [])]


def test_citing_the_villages_notes_counts_only_when_they_were_shown(tmp_path, log):
    s = MemoryStore(tmp_path)
    run(log, s, [{"op": "add", "section": "facts", "text": "Queued work goes out",
                  "evidence": ["changes"]}], news_shown=False)
    assert s.items[0]["source"] == "self-reported"


def test_a_tools_line_without_tool_actions_is_rejected(tmp_path, log):
    said = log.append("chat", ME, 1, sender="owner", text="The editor is fine.")
    s = MemoryStore(tmp_path)
    out = run(log, s, [{"op": "add", "section": "tools", "text": "edit_file needs exact text",
                        "evidence": [said.id]}])
    assert out.results[0].startswith("rejected tool lesson (a tool lesson must cite")
    assert s.items == [] and out.all_rejected(1)


def test_rewriting_a_tools_line_is_checked_even_when_the_op_names_no_section(tmp_path, log):
    did = log.append("action", ME, 1, tool="edit_file", ok=True)
    s = MemoryStore(tmp_path)
    run(log, s, [{"op": "add", "section": "tools", "text": "edit_file needs exact text",
                  "evidence": [did.id]}])
    out = run(log, s, [{"op": "replace", "id": "#1", "text": "edit_file silently fails",
                        "evidence": [did.id]},
                       {"op": "replace", "id": 1, "section": "facts",
                        "text": "edit_file is broken"}])
    assert all(r.startswith("rejected tool lesson") for r in out.results)
    assert s.items[0]["text"] == "edit_file needs exact text"


def test_a_pass_may_not_drop_a_line_it_has_just_rewritten_or_reinforced(tmp_path, log):
    s = MemoryStore(tmp_path)
    run(log, s, [{"op": "add", "section": "wants", "text": "Plan tomorrow's first steps"},
                 {"op": "add", "section": "wants", "text": "Learn about lighthouses"},
                 {"op": "add", "section": "wants", "text": "Tidy the notes folder"}])
    out = run(log, s, [{"op": "replace", "id": 1, "text": "Start with the outline tomorrow"},
                       {"op": "drop", "id": 1}, {"op": "reinforce", "id": 2},
                       {"op": "drop", "id": "2"}, {"op": "drop", "id": 3}])
    assert out.results == [
        "replaced #1", "rejected drop #1: this pass already rewrote or reinforced it",
        "reinforced #2", "rejected drop #2: this pass already rewrote or reinforced it",
        "dropped #3"]
    assert [i["id"] for i in s.items] == [1, 2]


def test_a_malformed_operation_is_rejected_and_the_rest_still_apply(tmp_path, log):
    s = MemoryStore(tmp_path)
    out = run(log, s, ["nonsense", {"op": "REPLACE"}, {"op": "replace", "id": "six", "text": "x"},
                       {"op": "merge", "id": 1}, {"op": "drop", "id": 41},
                       {"op": " Add ", "section": " Facts ", "text": "Maple harbour opens late",
                        "evidence": "evt-1"}])
    assert [r.split(":")[0] for r in out.results[:5]] == ["rejected"] * 5
    assert out.results[5] == "added #1" and not out.all_rejected(6)
    assert out.rejected == 5


def test_reinforcing_with_real_evidence_promotes_a_self_reported_line(tmp_path, log):
    did = log.append("action", ME, 1, tool="write_file", ok=True)
    s = MemoryStore(tmp_path)
    run(log, s, [{"op": "add", "section": "skills", "text": "Drafting outlines quickly"}])
    assert s.items[0]["source"] == "self-reported"
    run(log, s, [{"op": "reinforce", "id": 1, "evidence": ["evt-77"]}])
    assert s.items[0]["source"] == "self-reported"
    run(log, s, [{"op": "reinforce", "id": 1, "evidence": [did.id]}])
    assert s.items[0]["source"] == "seen" and s.items[0]["evidence"] == [did.id]


def test_what_the_owner_said_is_not_pushed_out_to_make_room(tmp_path, log):
    said = log.append("chat", ME, 1, sender="owner", text="I started the village in spring.")
    s = MemoryStore(tmp_path, cap=2)
    run(log, s, [{"op": "add", "section": "facts", "text": "The village began in spring",
                  "evidence": [said.id]},
                 {"op": "add", "section": "facts", "text": "Copper lantern hangs outside"}])
    s.items[0]["strength"] = 0.05
    out = run(log, s, [{"op": "add", "section": "facts", "text": "Velvet thunder rolled past",
                        "evidence": [said.id], "weight": "core"}])
    assert out.results == ["added #3"]
    assert [i["id"] for i in s.items] == [1, 3]
    assert s.keep == set() and s.evict_budget is None


def test_a_nap_is_given_only_three_evictions(tmp_path, log):
    s = MemoryStore(tmp_path, cap=4)
    old = ["maple harbour", "copper lantern", "velvet thunder", "granite orchard"]
    new = ["silver meadow", "amber compass", "willow engine", "cobalt feather"]
    run(log, s, [{"op": "add", "section": "moments", "text": w} for w in old])
    for item in s.items:
        item["strength"] = 0.1
    out = run(log, s, [{"op": "add", "section": "moments", "text": w} for w in new], nap=True)
    assert [r.split(" ")[0] for r in out.results] == ["added"] * 3 + ["rejected:"]
    assert "the night decides" in out.results[3]
    night = run(log, s, [{"op": "add", "section": "moments", "text": new[3]}])
    assert night.results == ["added #8"]


def test_a_name_settles_only_after_coming_up_on_enough_different_nights(tmp_path, log):
    s = MemoryStore(tmp_path)
    op = [{"op": "add", "section": "story", "kind": "name", "text": "Name: Wren"}]
    nights: dict = {}
    for day in ("2026-01-05", "2026-01-05", "2026-01-06"):
        out = run(log, s, op, day=day, name_nights=nights, lock_nights=3)
        nights = out.name_nights
        assert out.locked_names == [] and s.locked_name() is None
    out = run(log, s, op, day="2026-01-07", name_nights=nights, lock_nights=3)
    assert out.name_nights == {"Wren": ["2026-01-05", "2026-01-06", "2026-01-07"]}
    assert out.locked_names == ["Wren"] and out.results[-1] == "locked #1"
    assert s.locked_name() == "Wren"
    assert run(log, s, op, day="2026-01-08", name_nights=out.name_nights).locked_names == []


def test_no_more_than_sixty_operations_and_six_citations_are_applied(tmp_path, log):
    acts = [log.append("action", ME, i, tool="write_file", ok=True).id for i in range(8)]
    s = MemoryStore(tmp_path)
    out = run(log, s, [{"op": "add", "section": "skills", "text": "Drafting outlines quickly",
                        "evidence": acts}] + [{"op": "drop", "id": 900}] * 70)
    assert len(out.results) == 60 and s.items[0]["evidence"] == acts[:6]
