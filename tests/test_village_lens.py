"""The Village's record on the Tape and the lens the Village screen reads."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jimmyhq.tape import registry
from jimmyhq.village import kinds, lens, record

KINDS = ("village.born", "village.retired", "village.named", "village.configured",
         "village.prioritized", "village.said", "village.slept", "village.restored")
PRIORITY = {"priority_id": "p-draft-newsletters", "title": "Draft newsletters", "why": "",
            "details": "", "agents": [], "active": True, "suggested_by": None}


def test_every_village_kind_is_registered_with_an_example_and_folded_by_the_lens():
    assert lens.KINDS == KINDS and kinds.VillageBorn
    assert {k for k in registry.registered_kinds() if k.startswith("village.")} == set(KINDS)
    for kind in KINDS:
        example = registry.example_of(kind)
        assert registry.validate_payload(kind, 1, example), kind
        assert ("agent_id" in example) != (kind == "village.prioritized"), kind


def test_everything_about_an_agent_is_printed_under_its_own_subject(core):
    events = [
        record.born(core, "agent-a", 7, "researcher"),
        record.configured(core, "agent-a", lens.clean_config({})),
        record.said(core, "agent-a", "owner", "Hello"),
        record.said(core, "agent-a", "agent-a", "Hello back", urgent=True),
        record.slept(core, "agent-a", day="2026-01-05", by="smart", ops=3, rejected=1,
                     snapshot=1, notes="n" * 400),
        record.restored(core, "agent-a", to=1, undo=2, paths=["memory"], note="why"),
        record.named(core, "agent-a", "Wren"),
        record.retired(core, "agent-a", "done"),
    ]
    assert {e.subject for e in events} == {"agent:agent-a"}
    assert [e.actor for e in events] == ["system", "owner", "owner", "agent:agent-a", "system",
                                         "system", "agent:agent-a", "system"]
    assert events[3].payload["sender"] == "agent" and events[3].payload["urgent"] is True
    assert len(events[4].payload["notes"]) == 300
    assert record.prioritized(core, PRIORITY).subject == "priority:p-draft-newsletters"


def test_a_payload_with_an_unknown_field_or_sender_is_refused(core):
    with pytest.raises(ValidationError):
        core.tape.print("village.said", {"agent_id": "agent-a", "sender": "stranger",
                                         "text": "x"}, actor="owner")
    with pytest.raises(ValidationError):
        core.tape.print("village.born", {"agent_id": "agent-a", "seed": 1, "mood": "bright"},
                        actor="system")
    with pytest.raises(ValidationError):
        record.slept(core, "agent-a", day="2026-01-05", by="beat", ops=1, rejected=0,
                     snapshot=1)


def test_the_roster_holds_each_agents_name_role_config_and_last_night(core):
    record.born(core, "agent-a", 7, "researcher")
    record.born(core, "agent-b", 8)
    assert lens.config(core, "agent-a") == {
        "role": "researcher", "beat": {"role": "default"}, "night": {"role": "smart"},
        "local_only": False, "capabilities": []}
    record.configured(core, "agent-a", {
        "role": "editor", "beat": {"role": "cheap"},
        "night": {"provider_id": "p1", "model": "m1"}, "local_only": True,
        "capabilities": ["desktop"]})
    record.named(core, "agent-a", "Wren")
    slept = record.slept(core, "agent-a", day="2026-01-05", by="fallback", ops=4, rejected=2,
                         snapshot=3, notes="A long day.")
    restored = record.restored(core, "agent-a", to=2, undo=4, paths=["memory"], note="oops")
    state = lens.read(core)
    a = state["agents"]["agent-a"]
    assert state["order"] == ["agent-a", "agent-b"]
    assert (a["name"], a["role"], a["seed"], a["retired_ts"]) == ("Wren", "editor", 7, None)
    assert a["config"] == {"role": "editor", "beat": {"role": "cheap"},
                           "night": {"provider_id": "p1", "model": "m1"}, "local_only": True,
                           "capabilities": ["desktop"]}
    assert a["last_night"] == {"ts": slept.ts, "day": "2026-01-05", "by": "fallback", "ops": 4,
                               "rejected": 2, "snapshot": 3, "notes": "A long day."}
    assert a["last_restore"] == {"ts": restored.ts, "to": 2, "undo": 4, "paths": ["memory"],
                                 "note": "oops"}
    assert state["agents"]["agent-b"]["name"] is None
    assert lens.config(core, "nobody")["beat"] == {"role": "default"}


def test_a_retired_agent_stays_on_the_roster_but_is_not_among_the_living(core):
    record.born(core, "agent-a", 1)
    record.born(core, "agent-b", 2)
    gone = record.retired(core, "agent-a", "Finished.")
    state = lens.read(core)
    assert [a["id"] for a in lens.living(state)] == ["agent-b"]
    assert state["agents"]["agent-a"]["retired_ts"] == gone.ts
    assert state["agents"]["agent-a"]["retire_note"] == "Finished."


def test_the_lens_keeps_the_newest_two_hundred_chat_lines_per_agent(core):
    record.born(core, "agent-a", 1)
    for i in range(205):
        record.said(core, "agent-a", "owner" if i % 2 else "agent-a", f"line {i}")
    record.said(core, "agent-b", "owner", "to someone else")
    chat = lens.read(core)["chat"]
    assert len(chat["agent-a"]) == lens.CHAT_LINES == 200
    assert chat["agent-a"][0]["text"] == "line 5" and chat["agent-a"][-1]["text"] == "line 204"
    assert set(chat["agent-a"][0]) == {"seq", "id", "ts", "sender", "text", "urgent"}
    assert [m["text"] for m in chat["agent-b"]] == ["to someone else"]
    assert "agent-b" in lens.read(core)["agents"]  # a line for an unmet agent is not lost


def test_unanswered_counts_what_the_agent_wrote_since_the_owner_last_did():
    def line(sender):
        return {"sender": sender}
    assert lens.unanswered([]) == 0
    assert lens.unanswered([line("agent"), line("owner")]) == 0
    assert lens.unanswered([line("owner"), line("agent"), line("agent")]) == 2
    assert lens.unanswered([line("agent")]) == 1


def test_priorities_keep_their_order_and_show_each_agent_only_its_own(core):
    record.prioritized(core, PRIORITY)
    record.prioritized(core, {**PRIORITY, "priority_id": "p-two", "title": "Two",
                              "agents": ["agent-b"], "suggested_by": "agent:agent-b"})
    changed = record.prioritized(core, {**PRIORITY, "title": "Draft the newsletters",
                                        "active": False})
    state = lens.read(core)
    assert [p["id"] for p in lens.priorities(state)] == ["p-draft-newsletters", "p-two"]
    first = state["priorities"]["p-draft-newsletters"]
    assert (first["title"], first["active"]) == ("Draft the newsletters", False)
    assert first["updated_ts"] == changed.ts and first["created_ts"] <= changed.ts
    assert [p["id"] for p in lens.priorities(state, active_only=True)] == ["p-two"]
    assert lens.priorities(state, agent_id="agent-a", active_only=True) == []
    assert [p["id"] for p in lens.priorities(state, agent_id="agent-b")] == [
        "p-draft-newsletters", "p-two"]
    assert state["priorities"]["p-two"]["suggested_by"] == "agent:agent-b"


def test_a_model_choice_is_a_role_or_a_whole_provider_and_model():
    assert lens.model_choice({"provider_id": "p1", "model": "m1", "role": "smart"}, "x") == {
        "provider_id": "p1", "model": "m1"}
    assert lens.model_choice({"provider_id": "p1"}, "smart") == {"role": "smart"}
    assert lens.model_choice(None, "default") == {"role": "default"}
    assert lens.model_choice({"role": "cheap", "provider_id": None, "model": None}, "x") == {
        "role": "cheap"}
