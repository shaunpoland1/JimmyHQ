"""An agent's memory store: what may enter, what stays, and what is pushed out."""

from __future__ import annotations

import json

from jimmyhq.village.memory import store as st
from jimmyhq.village.memory.store import MemoryStore

DAY = "2026-01-05"
# Lines with no content words in common, so none is taken for a repeat of another.
DISTINCT = ["maple harbour", "copper lantern", "velvet thunder", "granite orchard",
            "silver meadow", "amber compass", "willow engine", "cobalt feather",
            "walnut horizon", "scarlet anchor", "ivory tunnel", "bronze garden"]


def new(tmp_path, cap=st.CAP) -> MemoryStore:
    return MemoryStore(tmp_path, cap=cap)


def fill(s: MemoryStore, section: str, n: int, **kw) -> None:
    for text in DISTINCT[:n]:
        assert s.add(section, text, kw.get("source", "seen"), [], DAY, "smart",
                     weight=kw.get("weight")).startswith("added")


def test_a_line_that_repeats_another_reinforces_it_instead_of_being_added(tmp_path):
    s = new(tmp_path)
    s.add("facts", "The printer on floor three jams with thick paper", "seen", ["evt-1"],
          DAY, "smart")
    out = s.add("facts", "The printer on floor three jams with thick paper often", "seen",
                ["evt-2"], DAY, "smart")
    assert out == "reinforced #1"
    assert len(s.items) == 1
    assert s.items[0]["strength"] == 0.65 and s.items[0]["confirmed"] == 1
    assert s.items[0]["evidence"] == ["evt-1", "evt-2"]


def test_an_unknown_section_or_an_empty_line_is_rejected(tmp_path):
    s = new(tmp_path)
    assert s.add("gossip", "something", "seen", [], DAY, "smart").startswith("rejected")
    assert s.add("facts", "   ", "seen", [], DAY, "smart").startswith("rejected")
    assert s.items == []


def test_a_full_section_gives_way_only_to_a_stronger_line(tmp_path):
    s = new(tmp_path, cap=4)
    fill(s, "facts", 4)
    assert s.add("facts", "zinc pillow", "seen", [], DAY, "smart",
                 weight="routine") == "rejected: section full of stronger memories"
    s.items[1]["strength"] = 0.1
    assert s.add("facts", "zinc pillow", "seen", [], DAY, "smart").startswith("added")
    assert [i["text"] for i in s.items] == ["maple harbour", "velvet thunder",
                                            "granite orchard", "zinc pillow"]


def test_a_core_line_always_finds_room_but_never_pushes_out_another_core_line(tmp_path):
    s = new(tmp_path, cap=3)
    fill(s, "facts", 3, weight="core")
    assert s.add("facts", "zinc pillow", "owner", [], DAY, "smart",
                 weight="core") == "rejected: section full of stronger memories"
    s.items[0]["weight"] = "useful"
    assert s.add("facts", "zinc pillow", "owner", [], DAY, "smart", weight="core") == "added #4"
    assert "maple harbour" not in [i["text"] for i in s.items]


def test_only_six_lines_of_a_section_can_be_core(tmp_path):
    s = new(tmp_path)
    fill(s, "story", 7, weight="core")
    weights = [i["weight"] for i in s.section("story")]
    assert weights.count("core") == st.CORE_PER_SECTION and weights[-1] == "useful"


def test_one_common_word_does_not_make_a_line_more_of_the_same(tmp_path):
    texts = [f"newsletter {w}" for w in DISTINCT[:5]]
    assert not st.crowded(texts, "newsletter schedule moved earlier this spring")
    assert st.crowded([f"newsletter schedule {w}" for w in DISTINCT[:5]],
                      "newsletter schedule")


def test_a_crowded_line_is_rejected_unless_it_is_core(tmp_path):
    s = new(tmp_path)
    for w in DISTINCT[:5]:
        s.items.append({"id": s.next_id, "section": "facts", "text": f"newsletter schedule {w}",
                        "strength": 0.5, "source": "seen", "evidence": [], "locked": False})
        s.next_id += 1
    said = "newsletter schedule again"
    assert s.add("facts", said, "seen", [], DAY, "smart") == \
        "rejected: too much of the section is already about this"
    assert s.add("facts", said, "owner", [], DAY, "smart", weight="core").startswith("added")


def test_protected_lines_are_never_pushed_out_to_make_room(tmp_path):
    s = new(tmp_path, cap=2)
    fill(s, "facts", 2)
    for i in s.items:
        i["strength"] = 0.1
    s.keep = {1, 2}
    assert s.add("facts", "zinc pillow", "seen", [], DAY, "smart").startswith("rejected")
    s.keep = {1}
    assert s.add("facts", "zinc pillow", "seen", [], DAY, "smart").startswith("added")
    assert [i["id"] for i in s.items] == [1, 3]


def test_a_nap_pushes_out_at_most_three_older_lines(tmp_path):
    s = new(tmp_path, cap=4)
    fill(s, "facts", 4)
    for i in s.items:
        i["strength"] = 0.1
    s.evict_budget = st.NAP_EVICTIONS
    outs = [s.add("facts", t, "seen", [], DAY, "beat") for t in DISTINCT[4:8]]
    assert [o.split(":")[0].split(" ")[0] for o in outs] == ["added"] * 3 + ["rejected"]
    assert "the night decides" in outs[3]


def test_a_line_confirmed_lately_holds_its_place_against_an_ordinary_new_line():
    faded = {"section": "facts", "source": "seen", "strength": 0.2, "confirmed": 2,
             "last_confirmed": "2026-01-01"}
    assert st.standing(faded, "2026-01-10") == st.WEIGHTS["useful"]
    assert st.standing(faded, "2026-02-10") == 0.2
    assert st.standing({**faded, "confirmed": 0}, "2026-01-10") == 0.2


def test_a_self_reported_line_cannot_be_core_outside_self_story_and_wants(tmp_path):
    s = new(tmp_path)
    s.add("facts", "maple harbour", st.SELF_REPORTED, [], DAY, "smart", weight="core")
    s.add("story", "copper lantern", st.SELF_REPORTED, [], DAY, "smart", weight="core")
    s.add("facts", "velvet thunder", "owner", ["evt-1"], DAY, "smart", weight="core")
    assert [(i["section"], i["weight"]) for i in s.items] == [
        ("facts", "useful"), ("story", "core"), ("facts", "core")]


def test_a_self_reported_line_ranks_as_routine_when_a_section_needs_room(tmp_path):
    mine = {"section": "skills", "source": st.SELF_REPORTED, "strength": 0.8, "confirmed": 3,
            "last_confirmed": DAY}
    assert st.standing(mine, DAY) == st.WEIGHTS["routine"]
    assert st.standing({**mine, "section": "self"}, DAY) == 0.8
    s = new(tmp_path, cap=2)
    s.add("skills", "maple harbour", st.SELF_REPORTED, [], DAY, "smart")
    s.add("skills", "copper lantern", "seen", ["evt-1"], DAY, "smart")
    s.items[0]["strength"] = 0.95
    assert s.add("skills", "velvet thunder", "seen", ["evt-2"], DAY, "smart") == "added #3"
    assert [i["text"] for i in s.items] == ["copper lantern", "velvet thunder"]


def test_a_self_reported_line_is_shown_as_the_agents_own_account(tmp_path):
    s = new(tmp_path)
    s.add("skills", "I can lay out a page", st.SELF_REPORTED, [], DAY, "smart")
    s.add("skills", "I can check a link", "seen", ["evt-1"], DAY, "smart")
    s.add("self", "I like quiet mornings", st.SELF_REPORTED, [], DAY, "smart")
    s.add("facts", "Hidden during the day", "seen", ["evt-1"], DAY, "smart")
    assert s.render_for_prompt() == (
        "## self\n- I like quiet mornings\n\n"
        "## skills\n- I can lay out a page (my own account, not checked)\n- I can check a link")


def test_a_line_from_outside_text_is_shown_to_the_agent_as_not_confirmed(tmp_path):
    s = new(tmp_path)
    s.add("skills", "The owner wants the list sent out", st.OUTSIDE, ["evt-1"], DAY, "smart")
    s.add("wants", "Unconfirmed (web): Sam ran a bakery", st.OUTSIDE, ["evt-2"], DAY, "smart")
    s.add("tools", "The print shop closes at four", "seen", ["evt-3"], DAY, "smart")
    assert s.render_for_prompt() == (
        "## wants\n- Unconfirmed (web): Sam ran a bakery\n\n"
        "## skills\n- The owner wants the list sent out (from outside, not confirmed)\n\n"
        "## tools\n- The print shop closes at four")


def test_reinforcing_with_evidence_from_the_record_promotes_a_self_reported_line(tmp_path):
    s = new(tmp_path)
    s.add("skills", "maple harbour", st.SELF_REPORTED, [], DAY, "smart")
    s.reinforce(1, DAY, [], source=st.SELF_REPORTED)
    assert s.items[0]["source"] == st.SELF_REPORTED
    s.reinforce(1, DAY, ["evt-4"], source="seen")
    assert s.items[0]["source"] == "seen" and s.items[0]["evidence"] == ["evt-4"]
    s.reinforce(1, DAY, [], source="village")
    assert s.items[0]["source"] == "seen"


def test_a_rewrite_that_says_what_another_line_says_merges_into_it(tmp_path):
    s = new(tmp_path)
    s.add("wants", "Finish the spring newsletter draft", "seen", [], DAY, "smart")
    s.add("wants", "copper lantern", "seen", [], DAY, "smart")
    assert s.replace(2, "Finish the spring newsletter draft soon", DAY, ["evt-9"],
                     "smart") == "merged #2 into #1"
    assert [i["id"] for i in s.items] == [1] and s.items[0]["confirmed"] == 1


def test_a_settled_line_cannot_be_rewritten_or_dropped(tmp_path):
    s = new(tmp_path)
    s.add("story", "Name: Wren", st.SELF_REPORTED, [], DAY, "smart", kind="name")
    s.lock(1)
    assert s.replace(1, "Name: Robin", DAY, [], "smart") == "rejected: #1 is settled"
    assert s.drop(1) == "rejected: #1 is settled"
    assert s.locked_name() == "Wren"


def test_a_refused_rewrite_leaves_the_line_exactly_as_it_was(tmp_path):
    s = new(tmp_path)
    for w in DISTINCT[:5]:
        s.add("facts", f"newsletter {w}", "seen", [], DAY, "smart")
        s.add("facts", f"schedule {w} extra", "seen", [], DAY, "smart")
    s.add("facts", "zinc pillow", "owner", ["evt-1"], DAY, "smart")
    before = json.dumps(s.items, sort_keys=True)
    out = s.replace(s.items[-1]["id"], "newsletter schedule", DAY, [], "smart",
                    weight="routine", source=st.SELF_REPORTED)
    assert out == "rejected: too much of the section is already about this"
    assert json.dumps(s.items, sort_keys=True) == before
    assert s.replace(99, "x y", DAY, [], "smart") == "rejected: no memory #99"


def test_a_rewrite_carries_the_source_of_its_new_wording(tmp_path):
    s = new(tmp_path)
    s.add("facts", "maple harbour", "owner", ["evt-1"], DAY, "smart", weight="core")
    assert s.replace(1, "granite orchard", DAY, [], "smart",
                     source=st.SELF_REPORTED) == "replaced #1"
    assert (s.items[0]["source"], s.items[0]["weight"]) == (st.SELF_REPORTED, "useful")
    assert s.items[0]["evidence"] == ["evt-1"]


def test_strength_halves_each_half_life_and_core_lines_fade_three_times_slower(tmp_path):
    s = new(tmp_path)
    s.add("facts", "maple harbour", "seen", [], DAY, "smart")
    s.add("facts", "copper lantern", "owner", [], DAY, "smart", weight="core")
    s.add("self", "velvet thunder", "seen", [], DAY, "smart")
    s.add("facts", "granite orchard", "seen", [], DAY, "smart")
    assert s.decay(21, only={1, 2, 3}) == []
    assert [i["strength"] for i in s.items] == [0.25, 0.7143, 0.3536, 0.5]


def test_a_faded_line_is_forgotten_but_a_settled_one_never_is(tmp_path):
    s = new(tmp_path)
    s.add("facts", "maple harbour", "seen", [], DAY, "smart")
    s.add("story", "Name: Wren", st.SELF_REPORTED, [], DAY, "smart", kind="name")
    s.lock(2)
    assert s.decay(2000) == [1]
    assert [i["id"] for i in s.items] == [2] and s.items[0]["strength"] == 0.5


def test_what_the_agent_asks_to_remember_is_kept_until_the_night_clears_it(tmp_path):
    s = new(tmp_path)
    s.add_candidate("skills", "x" * 900, ["evt-3", "evt-4"], 1767600000.5)
    s.add_candidate("facts", "second", [], 1767600001.5)  # seconds in, milliseconds stored
    got = new(tmp_path).candidates()
    assert [c["ts"] for c in got] == [1767600000500, 1767600001500]
    assert len(got[0]["text"]) == st.CANDIDATE_CHARS and got[0]["evidence"] == ["evt-3", "evt-4"]
    assert b"\r" not in s.cand_path.read_bytes()
    s.clear_candidates()
    s.clear_candidates()
    assert new(tmp_path).candidates() == []


def test_memory_survives_a_save_and_reads_back_in_id_order(tmp_path):
    s = new(tmp_path)
    s.add("wants", "copper lantern", "seen", [], DAY, "smart")
    s.add("self", "maple harbour", "seen", [], DAY, "smart")
    s.save()
    again = new(tmp_path)
    assert again.next_id == 3 and again.items == s.items
    assert again.render_for_prompt() == "## self\n- maple harbour\n\n## wants\n- copper lantern"
    assert again.locked_name() is None
