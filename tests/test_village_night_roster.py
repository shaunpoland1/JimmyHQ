"""Birth, retirement and the commands table the web process leaves requests in."""

from __future__ import annotations

from jimmyhq.tasks import service as tasks
from jimmyhq.village import lens, roster
from jimmyhq.village.memory import snapshots
from jimmyhq.village.world.state import load_agent, load_json
from test_village_night_fakes import FakeVillage


def tape(core, kind):
    return [e for e in core.tape.after(0) if e.kind == kind]


def birth(v, **payload):
    return roster.handle(v, {"id": "c", "kind": "birth", "agent": None, "by": "owner",
                             "payload": payload})


def test_a_command_waits_in_the_table_until_the_village_runs_it(core):
    cid = roster.submit(core, roster.BIRTH, None, {"role": "researcher"}, by="owner")
    other = roster.submit(core, roster.RETIRE, "agent-a", {"note": "done"}, by="owner")
    waiting = roster.pending(core)
    assert [c["id"] for c in waiting] == [cid, other]
    assert waiting[0]["payload"] == {"role": "researcher"} and waiting[0]["by"] == "owner"
    assert waiting[0]["ts"] > 1_600_000_000_000 and waiting[0]["done_ts"] is None
    roster.finish(core, cid, {"ok": True, "agent_id": "agent-a"})
    roster.finish(core, cid, {"ok": False})  # a command is finished once
    done = roster.get(core, cid)
    assert done["result"] == {"ok": True, "agent_id": "agent-a"} and done["done_ts"]
    assert [c["id"] for c in roster.pending(core)] == [other]
    assert roster.get(core, "nope") is None


def test_the_village_process_runs_waiting_commands_in_order(core):
    v = FakeVillage(core)
    first = roster.submit(core, roster.BIRTH, None, {"seed": 3}, by="owner")
    second = roster.submit(core, roster.RETIRE, "agent-q", {}, by="owner")
    ran = roster.run_pending(v)
    assert [c["id"] for c in ran] == [first, second]
    assert roster.get(core, first)["result"]["agent_id"] == "agent-a"
    assert roster.get(core, second)["result"]["ok"] is False
    assert roster.pending(core) == [] and roster.run_pending(v) == []


def test_a_tape_cursor_only_moves_forward(core):
    assert roster.cursor(core, "intake") == 0
    roster.set_cursor(core, "intake", 12)
    roster.set_cursor(core, "intake", 7)
    roster.set_cursor(core, "other", 3)
    assert (roster.cursor(core, "intake"), roster.cursor(core, "other")) == (12, 3)


def test_only_a_plain_lower_case_name_is_an_agent_id():
    assert all(roster.valid_id(x) for x in ("agent-a", "a", "agent-27", "7up"))
    assert not any(roster.valid_id(x) for x in (
        "", "Agent-a", "-a", "a/b", "..", "a.b", "a b", "a" * 41, None, 3, "agent-a\n"))


def test_new_ids_run_through_the_alphabet_and_are_never_reused():
    assert roster.next_id(set()) == "agent-a"
    assert roster.next_id({"agent-a", "agent-c"}) == "agent-b"
    letters = {f"agent-{c}" for c in "abcdefghijklmnopqrstuvwxyz"}
    assert roster.next_id(letters) == "agent-27"
    assert roster.next_id(letters | {"agent-27"}) == "agent-28"


def test_a_birth_makes_the_body_the_folders_the_docs_the_first_tasks_and_the_record(core):
    v = FakeVillage(core)
    out = birth(v, seed=7, role="researcher", local_only=True,
                models={"night": {"provider_id": "p1", "model": "m1"}})
    assert out == {"ok": True, "agent_id": "agent-a", "seed": 7, "tasks": 5}
    home = v.agent_dir("agent-a")
    for folder in ("home/files/village", "diary", "memory", "impressions", "deliverables",
                   "snapshots"):
        assert (home / folder).is_dir(), folder
    assert (v.shared_dir / "agent-a").is_dir()
    assert sorted(p.name for p in (home / "home" / "files" / "village").iterdir()) == [
        "README.md", "changes.md", "how-things-work.md", "reviewing.md"]
    body = load_agent(load_json(home / "state.json"))
    assert body.id == "agent-a" and body.temperament == v.world.agents["agent-a"].temperament
    born, configured = tape(core, "village.born")[0], tape(core, "village.configured")[0]
    assert born.payload == {"agent_id": "agent-a", "seed": 7, "role": "researcher"}
    assert born.actor == "owner" and born.subject == "agent:agent-a"
    assert configured.cause == born.id
    assert lens.config(core, "agent-a") == {
        "role": "researcher", "beat": {"role": "default"},
        "night": {"provider_id": "p1", "model": "m1"}, "local_only": True, "capabilities": []}
    mine = tasks(core).list_open(assignee="agent:agent-a")
    assert [t["title"] for t in mine][:2] == [
        "Look around your files folder and try each kind of file action once.",
        "Write your first diary entry about your first day."]
    assert not any("coworker" in t["title"] for t in mine)


def test_the_same_seed_rolls_the_same_temperament(core):
    v = FakeVillage(core)
    a, b = birth(v, seed=11)["agent_id"], birth(v, seed=11)["agent_id"]
    assert (a, b) == ("agent-a", "agent-b")
    assert v.world.agents[a].temperament == v.world.agents[b].temperament
    assert isinstance(birth(v)["seed"], int)
    # The second arrival has someone to meet, so it is given that task too.
    assert any("coworker" in t["title"]
               for t in tasks(core).list_open(assignee="agent:agent-b"))


def test_a_birth_past_the_headcount_is_refused_with_the_number(core):
    core.settings.set("village.headcount_cap", 1, actor="owner")
    v = FakeVillage(core)
    assert birth(v)["ok"]
    out = birth(v)
    assert out["ok"] is False
    assert "holds 1 agents and the most allowed is 1" in out["error"]
    assert "Nobody was born" in out["error"] and "Retire an agent" in out["error"]
    assert len(tape(core, "village.born")) == 1 and list(v.world.agents) == ["agent-a"]


def test_retiring_keeps_the_work_readable_and_archives_the_private_folder(core):
    v = FakeVillage(core)
    birth(v)
    home = v.agent_dir("agent-a")
    (home / "home" / "files" / "notes.md").write_text("my notes", encoding="utf-8")
    (home / "diary" / "2026-01-05.md").write_text("## 08:00\nprivate\n", encoding="utf-8")
    (v.shared_dir / "agent-a" / "draft.md").write_text("a draft", encoding="utf-8")
    out = roster.handle(v, {"kind": "retire", "agent": "agent-a", "by": "owner",
                            "payload": {"note": "The project is finished."}})
    assert out == {"ok": True, "agent_id": "agent-a", "left": ["files", "shared"]}
    left = v.shared_dir / "left" / "agent-a"
    assert (left / "files" / "notes.md").read_text(encoding="utf-8") == "my notes"
    assert (left / "shared" / "draft.md").read_text(encoding="utf-8") == "a draft"
    assert not list(left.rglob("2026-01-05.md"))
    assert not home.exists() and not (v.shared_dir / "agent-a").exists()
    archived = home.parent.parent / "retired" / "agent-a"
    assert (archived / "diary" / "2026-01-05.md").is_file()
    assert "agent-a" not in v.world.agents
    assert tape(core, "village.retired")[0].payload == {
        "agent_id": "agent-a", "note": "The project is finished."}
    assert lens.living(lens.read(core)) == []
    # Its id is not given to the next arrival, and it cannot be retired twice.
    assert birth(v)["agent_id"] == "agent-b"
    again = roster.handle(v, {"kind": "retire", "agent": "agent-a", "payload": {}})
    assert again["ok"] is False and "Nothing was changed" in again["error"]


def test_a_command_for_nobody_or_of_an_unknown_kind_is_refused_plainly(core):
    v = FakeVillage(core)
    birth(v)
    for agent in ("agent-z", "../agent-a", None):
        out = roster.handle(v, {"kind": "rollback", "agent": agent, "payload": {"to": 1}})
        assert out["ok"] is False and "no living agent" in out["error"]
    out = roster.handle(v, {"kind": "promote", "agent": "agent-a", "payload": {}})
    assert out["ok"] is False and "does not know the command" in out["error"]
    out = roster.handle(v, {"kind": "rollback", "agent": "agent-a", "payload": {"to": True}})
    assert "This agent has no snapshots yet." in out["error"]


def test_a_command_that_breaks_is_logged_as_a_platform_fault_and_never_raises(
        core, monkeypatch):
    v = FakeVillage(core)
    birth(v)
    snapshots.take(v.agent_dir("agent-a"), 1, "night")

    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(snapshots, "restore", boom)
    out = roster.handle(v, {"kind": "rollback", "agent": "agent-a", "payload": {"to": 1}})
    assert out["ok"] is False and "platform fault" in out["error"]
    fault = v.log.last(agent="agent-a", kinds=["platform_fault"]).data
    assert fault == {"what": "command rollback failed", "detail": "OSError: disk full"}
