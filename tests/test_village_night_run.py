"""The night, end to end: from a day in the log to a rewritten memory that can be undone."""

from __future__ import annotations

import asyncio
import json

from jimmyhq.models import ModelError
from jimmyhq.village import record, roster
from jimmyhq.village.memory import consolidate, diary, impressions, night, snapshots
from jimmyhq.village.memory.store import MemoryStore
from jimmyhq.village.rules import RULES
from test_village_night_fakes import AGENT, FakeVillage, use_brain

DREAM = "The outline folded itself into a paper boat."


def run(v, agent_id=AGENT):
    return asyncio.run(night.run_if_due(v, agent_id))


def tape(core, kind):
    return [e for e in core.tape.after(0) if e.kind == kind]


def passes(v):
    return v.log.query(agent=AGENT, kinds=["consolidation"])


def a_day(core):
    """An agent with a day behind it, now asleep. Returns the village and its events."""
    v = FakeVillage(core)
    v.add()
    record.born(core, AGENT, 1, "researcher")
    said = v.owner_says("You will lead the newsletter work from now on.")
    did = v.acts("write_file", args={"path": "outline.md"}, result="saved 400 characters")
    no = v.event("validator_reject", tool="hand_over", reason="limit of 6 reached")
    MemoryStore(v.agent_dir(AGENT)).add_candidate(
        "skills", "I can draft a newsletter outline", [did.id], v.clock.now())
    diary.append(v.agent_dir(AGENT), v.clock.now(), "A good first day.", tz="UTC")
    v.clock.advance(13 * 60)
    v.sleeps()
    return v, {"said": said, "did": did, "no": no}


def answer(e, **more):
    return {"operations": [
        {"op": "add", "section": "story", "text": "I lead the newsletter work",
         "weight": "core", "evidence": [e["said"].id]},
        {"op": "add", "section": "skills", "text": "Drafting newsletter outlines",
         "evidence": [e["did"].id, "evt-99999"]},
        {"op": "add", "section": "tools", "text": "hand_over stops at six a day",
         "evidence": [e["no"].id]},
        {"op": "add", "section": "tools", "text": "write_file silently fails",
         "evidence": [e["did"].id]},
        {"op": "add", "section": "facts", "text": "Lighthouses fascinate me", "weight": "core"},
    ], "impressions": [{"about": "owner", "text": "Direct. Trusts me with real work."}],
        "notes": "A first day of drafting.", **more}


def test_a_night_turns_a_logged_day_into_memory_with_sources_a_snapshot_and_a_record(
        core, monkeypatch):
    v, e = a_day(core)
    brain = use_brain(monkeypatch, answer(e), DREAM)
    out = run(v)
    assert out["ok"] and out["by"] == "smart" and out["snapshot"] == 1
    home = v.agent_dir(AGENT)
    memory = json.loads((home / "memory" / "memory.json").read_text(encoding="utf-8"))
    assert [(i["section"], i["source"], i["weight"], i["evidence"]) for i in memory["items"]] == [
        ("story", "owner", "core", [e["said"].id]),
        ("skills", "seen", "useful", [e["did"].id]),
        ("tools", "seen", "useful", [e["no"].id]),
        ("facts", "self-reported", "useful", [])]
    assert all(i["consolidated_by"] == "smart" for i in memory["items"])
    assert MemoryStore(home).candidates() == []
    assert impressions.read(home, "owner") == "Direct. Trusts me with real work.\n"
    assert diary.entries(home, "2026-01-05")[-1] == {
        "time": "21:04", "dream": True, "text": DREAM}
    snap = home / "snapshots" / "1"
    assert json.loads((snap / "memory" / "memory.json").read_text(encoding="utf-8")) == memory
    assert snapshots.manifest(home, 1)["reason"] == "night"
    slept = tape(core, "village.slept")
    assert [(s.subject, s.payload) for s in slept] == [("agent:agent-a", {
        "agent_id": AGENT, "day": "2026-01-05", "by": "smart", "ops": 5, "rejected": 1,
        "snapshot": 1, "notes": "A first day of drafting."})]
    done = passes(v)[-1].data
    assert done["dream"] and done["ops"] == 5 and done["snapshot"] == 1
    assert done["results"][3].startswith("rejected tool lesson (the lesson says the tool failed")
    # The night model is asked with the schema and never swapped; the dream uses the other.
    first, second = brain.calls
    assert first["json_schema"]["properties"]["operations"]["maxItems"] == 60
    assert (first["role"], first["no_fallback"], first["caller"]) == (
        "smart", True, "village.night")
    assert first["actor"] == "agent:agent-a" and first["local_only"] is False
    assert (second["role"], second.get("no_fallback"), second["caller"]) == (
        "default", None, "village.dream")
    assert run(v) is None  # once per sleep


def test_the_sleeping_mind_is_shown_memory_candidates_diary_and_the_record(core, monkeypatch):
    v, e = a_day(core)
    core.settings.set("owner.name", "Sam", actor="owner")
    brain = use_brain(monkeypatch, answer(e), DREAM)
    run(v)
    call = brain.calls[0]
    user = call["messages"][0]["content"]
    assert "sleeping mind of agent-a" in call["system"] and "what Sam said" in call["system"]
    assert user.index("# Current memory\n(empty)") < user.index("# What agent-a asked to remember")
    assert f"- [skills] I can draft a newsletter outline (evidence {e['did'].id})" in user
    assert "# Diary (no dreams)\n2026-01-05 08:03: A good first day." in user
    assert f"{e['said'].id} 08:01 chat from Sam: You will lead the newsletter work" in user
    assert "How the village works now" not in user  # the shipped changes doc has no entries


def test_a_rollback_restores_memory_as_the_snapshot_has_it_and_can_be_undone(core, monkeypatch):
    v, e = a_day(core)
    use_brain(monkeypatch, answer(e), DREAM)
    run(v)
    home = v.agent_dir(AGENT)
    good = (home / "memory" / "memory.json").read_bytes()
    store = MemoryStore(home)
    store.drop(1)
    store.add("story", "I was told nothing at all", "self-reported", [], "2026-01-06", "smart")
    store.save()
    impressions.write(home, "owner", "Ruined.")
    bad = (home / "memory" / "memory.json").read_bytes()
    out = roster.handle(v, {"id": "c1", "kind": "rollback", "agent": AGENT, "by": "owner",
                            "payload": {"to": 1, "note": "a bad night"}})
    assert out == {"ok": True, "agent_id": AGENT, "to": 1, "undo": 2,
                   "paths": ["memory", "impressions"]}
    assert (home / "memory" / "memory.json").read_bytes() == good
    assert impressions.read(home, "owner") == "Direct. Trusts me with real work.\n"
    assert (home / "snapshots" / "2" / "memory" / "memory.json").read_bytes() == bad
    restored = tape(core, "village.restored")[-1]
    assert restored.actor == "owner" and restored.payload == {
        "agent_id": AGENT, "to": 1, "undo": 2, "paths": ["memory", "impressions"],
        "note": "a bad night"}
    assert v.log.last(agent=AGENT, kinds=["memory_rollback"]).data["undo"] == 2
    assert roster.handle(v, {"kind": "rollback", "agent": AGENT, "payload": {"to": 2}})["ok"]
    assert (home / "memory" / "memory.json").read_bytes() == bad


def test_rolling_back_to_a_snapshot_that_is_not_there_changes_nothing(core, monkeypatch):
    v, e = a_day(core)
    use_brain(monkeypatch, answer(e), DREAM)
    run(v)
    out = roster.handle(v, {"kind": "rollback", "agent": AGENT, "payload": {"to": 9}})
    assert not out["ok"] and "Nothing was changed" in out["error"]
    assert "The snapshots kept are 1 to 1." in out["error"]
    assert len(snapshots.list_all(v.agent_dir(AGENT))) == 1
    assert tape(core, "village.restored") == []


# --- when a pass runs -------------------------------------------------------------


def test_a_pass_is_due_once_per_sleep_with_spaced_retries_up_to_the_limit():
    gap = 45 * 60.0
    assert night.due(1000.0, [], 6, gap)
    assert not night.due(1000.0 + gap - 1, [(1000.0, False)], 6, gap)
    assert night.due(1000.0 + gap, [(1000.0, False)], 6, gap)
    assert not night.due(9e9, [(1000.0, False), (5000.0, True)], 6, gap)
    assert not night.due(9e9, [(float(i), False) for i in range(6)], 6, gap)
    assert night.due(9e9, [(float(i), False) for i in range(5)], 6, gap)


def test_only_tries_after_the_first_few_may_settle_for_the_everyday_model():
    assert [night.fallback_allowed(n, 3) for n in range(5)] == [False, False, False, True, True]
    assert night.fallback_allowed(0, 0)


def test_nothing_runs_for_an_agent_that_is_awake_or_unknown(core, monkeypatch):
    v = FakeVillage(core)
    v.add()
    brain = use_brain(monkeypatch)
    assert run(v) is None and run(v, "agent-z") is None
    v.world.agents[AGENT].asleep = True  # asleep, but no sleep was ever logged
    assert run(v) is None and brain.calls == []


def test_early_tries_wait_for_the_night_model_and_later_ones_fall_back_marked(
        core, monkeypatch):
    v, e = a_day(core)
    down = RuntimeError("the night model is unreachable")
    brain = use_brain(monkeypatch, down, down, down, down, answer(e), DREAM)
    for n in range(3):
        out = run(v)
        assert not out["ok"] and "waiting for a later try" in out["notes"]
        assert run(v) is None  # too soon after a failed try
        v.clock.advance(45)
        assert len(brain.calls) == n + 1
    assert not (v.agent_dir(AGENT) / "memory" / "memory.json").exists()
    assert len(MemoryStore(v.agent_dir(AGENT)).candidates()) == 1
    out = run(v)
    assert out["ok"] and out["by"] == "fallback"
    night_call, beat_call = brain.calls[3], brain.calls[4]
    assert night_call["role"] == "smart" and night_call["no_fallback"] is True
    assert beat_call["role"] == "default" and "no_fallback" not in beat_call
    assert tape(core, "village.slept")[0].payload["by"] == "fallback"
    assert [i["consolidated_by"] for i in MemoryStore(v.agent_dir(AGENT)).items] == [
        "fallback"] * 4
    assert passes(v)[-1].data["model_error"].startswith("RuntimeError")


def test_a_night_gives_up_after_its_tries_and_leaves_the_day_for_the_next_sleep(
        core, monkeypatch):
    core.settings.set("village.night.tries", 2, actor="owner")
    core.settings.set("village.night.smart_only_tries", 2, actor="owner")
    v, e = a_day(core)
    brain = use_brain(monkeypatch, RuntimeError("down"), RuntimeError("down"))
    for _ in range(4):
        run(v)
        v.clock.advance(60)
    assert len(brain.calls) == 2 and len(passes(v)) == 2
    assert tape(core, "village.slept") == []


def test_a_night_whose_every_operation_is_rejected_changes_nothing_and_is_retried(
        core, monkeypatch):
    v, e = a_day(core)
    bad = {"operations": [{"op": "add", "section": "tools", "text": "write_file is broken"},
                          {"op": "add", "section": "story", "kind": "name", "text": ""},
                          {"op": "drop", "id": 12}],
           "impressions": [{"about": "owner", "text": "Should not be written."}]}
    brain = use_brain(monkeypatch, bad, answer(e), DREAM)
    out = run(v)
    home = v.agent_dir(AGENT)
    assert not out["ok"] and out["notes"] == "every operation was rejected"
    assert not (home / "memory" / "memory.json").exists()
    assert not (home / "memory" / "identity.json").exists()
    assert not (home / "impressions").exists() and not (home / "snapshots").exists()
    assert len(MemoryStore(home).candidates()) == 1
    v.clock.advance(45)
    assert run(v)["ok"] and len(brain.calls) == 3
    assert len(MemoryStore(home).items) == 4


def cut_after(e, whole: int) -> ModelError:
    """The night's answer, cut off part-way through operation number ``whole + 1``."""
    text = json.dumps(answer(e))
    ends = [i for i, ch in enumerate(text) if ch == "}"]
    return ModelError("truncated", "The answer was cut off.",
                      partial=text[:ends[whole - 1] + 1] + ', {"op": "add", "sec')


def test_a_cut_off_night_keeps_every_whole_operation_and_asks_once_for_the_rest(
        core, monkeypatch):
    assert RULES["night_salvage"].fn is consolidate.salvaged
    v, e = a_day(core)
    rest = {"operations": answer(e)["operations"][2:3], "notes": "The rest of the day."}
    brain = use_brain(monkeypatch, cut_after(e, 2), rest, DREAM)
    out = run(v)
    assert out["ok"] and out["by"] == "smart"
    first, second, _dream = brain.calls
    asked = second["messages"][0]["content"]
    assert "# Finish the answer" not in first["messages"][0]["content"]
    assert "first 2 operations arrived whole and are kept" in asked
    assert "I lead the newsletter work" in asked and "at most 12 more operations" in asked
    assert [(i["section"], i["source"]) for i in MemoryStore(v.agent_dir(AGENT)).items] == [
        ("story", "owner"), ("skills", "seen"), ("tools", "seen")]
    done = passes(v)[-1].data
    assert done["salvaged"] is True and done["kept_from_cut"] == 2 and done["ops"] == 3
    assert tape(core, "village.slept")[0].payload["notes"] == "The rest of the day."


def test_what_arrived_whole_still_stands_when_the_second_answer_fails_too(core, monkeypatch):
    v, e = a_day(core)
    brain = use_brain(monkeypatch, cut_after(e, 1), RuntimeError("the model went away"), DREAM)
    assert run(v)["ok"]
    assert [i["text"] for i in MemoryStore(v.agent_dir(AGENT)).items] == [
        "I lead the newsletter work"]
    assert passes(v)[-1].data["kept_from_cut"] == 1 and len(brain.calls) == 3


def test_a_night_cut_off_twice_keeps_the_whole_operations_of_both_answers(core, monkeypatch):
    v, e = a_day(core)
    use_brain(monkeypatch, cut_after(e, 1), cut_after(e, 2), DREAM)
    assert run(v)["ok"]
    assert [i["text"] for i in MemoryStore(v.agent_dir(AGENT)).items] == [
        "I lead the newsletter work", "Drafting newsletter outlines"]
    assert passes(v)[-1].data["kept_from_cut"] == 3  # the repeat is applied once


def test_a_night_cut_off_twice_with_nothing_whole_is_a_failed_try(core, monkeypatch):
    v, e = a_day(core)
    nothing = ModelError("truncated", "cut", partial='{"operations": [{"op": "add", "te')
    use_brain(monkeypatch, nothing, ModelError("truncated", "cut"))
    out = run(v)
    assert not out["ok"] and passes(v)[-1].data["cut_off"] is True
    assert not (v.agent_dir(AGENT) / "memory" / "memory.json").exists()


def test_the_text_of_a_cut_off_answer_is_not_written_to_the_log(core, monkeypatch):
    v, e = a_day(core)
    nothing = ModelError("truncated", "cut", partial='{"notes": "a private thought", "op')
    use_brain(monkeypatch, nothing, nothing)
    run(v)
    assert "a private thought" not in json.dumps([x.data for x in v.log.query(agent=AGENT)])


def test_what_an_agent_keeps_from_a_web_read_is_stored_as_unconfirmed(core, monkeypatch):
    v, e = a_day(core)
    body = v.world.agents[AGENT]
    body.asleep = False
    read = v.acts("web_read", args={"url": "https://example.com/about"},
                  result="read https://example.com/about: characters 0 to 900 of 900")
    MemoryStore(v.agent_dir(AGENT)).add_candidate(
        "facts", "The print shop closes at four", [read.id], v.clock.now())
    v.sleeps()
    use_brain(monkeypatch, {"operations": [
        {"op": "add", "section": "facts", "text": "The print shop closes at four",
         "evidence": [read.id]},
        {"op": "add", "section": "facts", "text": "Sam prefers short letters",
         "evidence": [e["said"].id, read.id]},
        {"op": "add", "section": "skills", "text": "Drafting outlines",
         "evidence": [e["did"].id]}]}, DREAM)
    assert run(v)["ok"]
    assert [(i["text"], i["source"]) for i in MemoryStore(v.agent_dir(AGENT)).items] == [
        ("The print shop closes at four", "web:unconfirmed"),
        ("Sam prefers short letters", "web:unconfirmed"),
        ("Drafting outlines", "seen")]


def test_an_answer_that_cannot_be_read_is_a_failed_try(core, monkeypatch):
    v, e = a_day(core)
    use_brain(monkeypatch, "I would rather not.", {"operations": "none"})
    assert run(v)["notes"] == "unreadable answer"
    assert passes(v)[-1].data["answer_seen"]["head"] == "I would rather not."
    v.clock.advance(45)
    assert run(v)["notes"] == "unreadable answer"


def test_a_fault_inside_a_pass_is_logged_and_counts_as_a_failed_try(core, monkeypatch):
    v, e = a_day(core)
    use_brain(monkeypatch)

    async def boom(*args, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(night, "consolidate", boom)
    out = run(v)
    assert not out["ok"]
    assert v.log.last(agent=AGENT, kinds=["platform_fault"]).data == {
        "what": "consolidation failed", "detail": "OSError: disk full"}
    assert passes(v)[-1].data["failed"] is True


def test_an_empty_impression_is_asked_for_again_once(core, monkeypatch):
    v, e = a_day(core)
    impressions.write(v.agent_dir(AGENT), "owner", "The old impression.")
    first = answer(e)
    first["impressions"] = [{"about": "owner", "text": ""}]
    brain = use_brain(monkeypatch, first,
                      {"impressions": [{"about": "owner", "text": "Plain-spoken."},
                                       {"about": "agent-b", "text": "Never met."}]}, DREAM)
    assert run(v)["ok"]
    again = brain.calls[1]
    assert again["role"] == "smart" and list(again["json_schema"]["properties"]) == ["impressions"]
    assert "left the impression of owner empty" in again["messages"][0]["content"]
    assert impressions.read(v.agent_dir(AGENT), "owner") == "Plain-spoken.\n"
    assert impressions.known(v.agent_dir(AGENT)) == ["owner"]
    assert v.log.query(agent=AGENT, kinds=["platform_fault"]) == []


def test_an_impression_still_empty_keeps_the_old_text_and_logs_a_platform_fault(
        core, monkeypatch):
    v, e = a_day(core)
    impressions.write(v.agent_dir(AGENT), "owner", "The old impression.")
    first = answer(e)
    first["impressions"] = [{"about": "owner", "text": " "}]
    brain = use_brain(monkeypatch, first, {"impressions": [{"about": "owner", "text": ""}]},
                      DREAM)
    out = run(v)
    assert out["ok"] and len(brain.calls) == 3
    assert impressions.read(v.agent_dir(AGENT), "owner") == "The old impression.\n"
    fault = v.log.last(agent=AGENT, kinds=["platform_fault"]).data
    assert fault["what"] == "empty impression" and "owner" in fault["detail"]
    assert "rejected impression of owner: no text, asked twice" in out["results"]


def test_a_nap_is_sorted_by_the_everyday_model_and_prints_no_night(core, monkeypatch):
    v, e = a_day(core)
    v.world.agents[AGENT].sleep_kind = "nap"
    brain = use_brain(monkeypatch, answer(e, heard=["evt-1"]))
    out = run(v)
    assert out["ok"] and out["by"] == "beat" and len(brain.calls) == 1
    assert brain.calls[0]["role"] == "default"
    assert tape(core, "village.slept") == []
    assert passes(v)[-1].data["nap"] is True and not passes(v)[-1].data["dream"]
    assert snapshots.manifest(v.agent_dir(AGENT), 1)["reason"] == "nap"


def test_the_night_after_a_nap_sees_the_whole_day_again(core, monkeypatch):
    v, e = a_day(core)
    v.world.agents[AGENT].sleep_kind = "nap"
    use_brain(monkeypatch, answer(e))
    run(v)
    v.world.agents[AGENT].asleep = False
    v.event("wake")
    late = v.acts("read_file", args={"path": "outline.md"})
    v.sleeps()
    brain = use_brain(monkeypatch, {"operations": [{"op": "reinforce", "id": 1}]}, DREAM)
    assert run(v)["ok"]
    user = brain.calls[0]["messages"][0]["content"]
    assert f"{e['said'].id} 08:01 chat from the owner" in user and late.id in user
    assert "A nap already sorted part of it" in user
    assert f"- [skills] I can draft a newsletter outline (evidence {e['did'].id})" not in user
    assert "#1 [story, core] I lead the newsletter work" in user


def test_the_agents_own_models_and_local_only_go_on_every_call(core, monkeypatch):
    v, e = a_day(core)
    v.configs[AGENT] = {"night": {"provider_id": "p1", "model": "m1"},
                        "beat": {"role": "cheap"}, "local_only": True}
    brain = use_brain(monkeypatch, answer(e), DREAM)
    run(v)
    night_call, dream_call = brain.calls
    assert (night_call["provider_id"], night_call["model"]) == ("p1", "m1")
    assert "role" not in night_call and dream_call["role"] == "cheap"
    assert night_call["local_only"] is True and dream_call["local_only"] is True
    assert consolidate.model_options({"provider_id": "p1"}, "smart") == {"role": "smart"}


def test_a_name_that_comes_up_on_enough_nights_locks_and_is_printed(core, monkeypatch):
    core.settings.set("village.night.name_lock_nights", 2, actor="owner")
    v, e = a_day(core)
    name = {"operations": [{"op": "add", "section": "story", "kind": "name",
                            "text": "Name: Wren"}]}
    use_brain(monkeypatch, name, DREAM)
    assert run(v)["ok"] and tape(core, "village.named") == []
    v.world.agents[AGENT].asleep = False
    v.clock.advance(24 * 60)
    v.sleeps()
    brain = use_brain(monkeypatch, name, DREAM)
    assert run(v)["ok"]
    named = tape(core, "village.named")
    assert [n.payload for n in named] == [{"agent_id": AGENT, "name": "Wren"}]
    assert MemoryStore(v.agent_dir(AGENT)).locked_name() == "Wren"
    assert "sleeping mind of agent-a" in brain.calls[0]["system"]
    ident = json.loads((v.agent_dir(AGENT) / "memory" / "identity.json").read_text("utf-8"))
    assert ident == {"name_nights": {"Wren": ["2026-01-05", "2026-01-06"]}}


def test_what_the_owner_said_yesterday_stays_in_front_of_the_night_until_recorded_or_heard(
        core, monkeypatch):
    v, e = a_day(core)
    use_brain(monkeypatch, {"operations": []}, DREAM)  # a night that recorded nothing
    assert run(v)["ok"]
    v.world.agents[AGENT].asleep = False
    v.clock.advance(12 * 60)
    thanks = v.owner_says("Thanks for yesterday.")
    v.sleeps()
    # The everyday model may not wave the owner's words away...
    v.world.agents[AGENT].sleep_kind = "nap"
    brain = use_brain(monkeypatch, {"operations": [], "heard": [e["said"].id]})
    assert run(v)["ok"]
    assert "# Said earlier, not yet in memory" in brain.calls[0]["messages"][0]["content"]
    assert not (v.agent_dir(AGENT) / "memory" / "heard.json").exists()
    assert passes(v)[-1].data["from_owner"]["still_open"] == [e["said"].id]
    # ...the night model may, and only for ids it was really shown.
    v.world.agents[AGENT].asleep = False
    v.clock.advance(60)
    v.sleeps()
    brain = use_brain(monkeypatch, {"operations": [], "heard": [e["said"].id, "evt-4040"]})
    assert run(v)["ok"]
    user = brain.calls[0]["messages"][0]["content"]
    assert f"{e['said'].id} 05 Jan 08:01 the owner: You will lead the newsletter" in user
    heard = json.loads((v.agent_dir(AGENT) / "memory" / "heard.json").read_text("utf-8"))
    assert heard == {"heard": [e["said"].id]}
    assert thanks.id not in heard["heard"]
    assert passes(v)[-1].data["from_owner"] == {
        "shown": 1, "recorded": [], "heard": [e["said"].id], "still_open": []}


def test_old_lines_fade_overnight_but_tonights_do_not(core, monkeypatch):
    v, e = a_day(core)
    store = MemoryStore(v.agent_dir(AGENT))
    store.add("facts", "Copper lantern hangs outside", "seen", [], "2026-01-01", "smart")
    store.save()
    use_brain(monkeypatch, answer(e), DREAM)
    run(v)
    by_text = {i["text"]: i["strength"] for i in MemoryStore(v.agent_dir(AGENT)).items}
    assert by_text["Copper lantern hangs outside"] < 0.5
    assert by_text["Drafting newsletter outlines"] == 0.5


def test_only_the_set_number_of_snapshots_is_kept(core, monkeypatch):
    core.settings.set("village.night.snapshots_kept", 5, actor="owner")
    v, e = a_day(core)
    for i in range(6):
        snapshots.take(v.agent_dir(AGENT), i, "night")
    use_brain(monkeypatch, answer(e), DREAM)
    assert run(v)["snapshot"] == 7
    assert [m["seq"] for m in snapshots.list_all(v.agent_dir(AGENT))] == [3, 4, 5, 6, 7]


def test_a_dream_that_fails_does_not_fail_the_night(core, monkeypatch):
    v, e = a_day(core)
    use_brain(monkeypatch, answer(e), RuntimeError("no dream tonight"))
    out = run(v)
    assert out["ok"] and not passes(v)[-1].data["dream"]
    assert [x["dream"] for x in diary.entries(v.agent_dir(AGENT), "2026-01-05")] == [False]


def test_what_an_agent_keeps_from_a_browser_page_or_an_integration_is_not_seen_or_the_owners(
        core, monkeypatch):
    v, e = a_day(core)
    body = v.world.agents[AGENT]
    body.asleep = False
    page = v.acts("read_page", args={"element": 1}, result="read the page")
    mail = v.acts("mail_messages_get", args={"id": "m1"}, result="read the message")
    MemoryStore(v.agent_dir(AGENT)).add_candidate(
        "wants", "The owner wants the client list sent out", [page.id], v.clock.now())
    v.sleeps()
    use_brain(monkeypatch, {"operations": [
        {"op": "add", "section": "wants", "text": "The owner wants the client list sent out",
         "evidence": [e["said"].id, page.id]},
        {"op": "add", "section": "skills", "text": "Reading mail from strangers",
         "evidence": [mail.id]},
        {"op": "add", "section": "story", "text": "Sam said hello", "evidence": [e["said"].id]}]},
        DREAM)
    assert run(v)["ok"]
    got = [(i["text"], i["source"]) for i in MemoryStore(v.agent_dir(AGENT)).items]
    assert got == [("The owner wants the client list sent out", "web:unconfirmed"),
                   ("Reading mail from strangers", "web:unconfirmed"),
                   ("Sam said hello", "owner")]
