"""The Village over HTTP: who may do what, and that no path leaves its folder."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from jimmyhq import village
from jimmyhq.village import lens, record, roster, routes
from jimmyhq.village.memory import diary, impressions, snapshots
from jimmyhq.village.memory.store import MemoryStore
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"
API = "/api/village"
A = "agent-a"
T0 = 1767600000.0

READER_GETS = ("agents", f"agents/{A}", "priorities", "breakroom", "board")
OWNER_GETS = (f"agents/{A}/chat", f"agents/{A}/diary", f"agents/{A}/memory",
              f"agents/{A}/files", f"agents/{A}/files/read?path=notes.md", "shared",
              f"agents/{A}/events", f"agents/{A}/snapshots", "commands/abc")
OWNER_WRITES = (
    ("post", "agents", {"role": "researcher"}),
    ("put", f"agents/{A}/config", {"role": "editor"}),
    ("post", f"agents/{A}/retire", {"note": "done"}),
    ("post", "priorities", {"title": "Draft newsletters"}),
    ("put", "priorities/p-x", {"active": False}),
    ("post", f"agents/{A}/chat", {"text": "hello"}),
    ("post", f"agents/{A}/rollback", {"to": 1}),
)


@pytest.fixture
def client(core):
    with TestClient(create_app(core), base_url="http://testserver",
                    raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def owner(client):
    assert client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}
                       ).status_code == 200
    return client


def token_client(owner, core, *scopes):
    headers = {"authorization": f"Bearer {core.auth.create_token('helper', list(scopes))[1]}"}
    return TestClient(owner.app, base_url="http://testserver", headers=headers,
                      raise_server_exceptions=False)


@pytest.fixture
def home(core):
    """One living agent with a body, files, memory, a diary and a snapshot."""
    record.born(core, A, 7, "researcher")
    folder = roster.agent_dir(core, A)
    files = folder / "home" / "files"
    (files / "drafts").mkdir(parents=True)
    (files / "notes.md").write_text("my notes", encoding="utf-8")
    (files / "drafts" / "one.md").write_text("draft one", encoding="utf-8")
    (files / ".hidden").write_text("dotfile", encoding="utf-8")
    (folder / "state.json").write_text(json.dumps({
        "id": A, "mode": "work", "asleep": False, "sleep_kind": None, "current_task": "T1",
        "needs": {"energy": 71.26, "frustration": 12.0, "social": 55.0, "curiosity": 40.0,
                  "purpose": 50.0},
        "waiting": {"c1": {}}, "parked": {"T2": {}},
        "temperament": {"mood_weights": {"energy": 0.2, "frustration": 0.2, "social": 0.2,
                                         "curiosity": 0.2, "purpose": 0.2}}}),
        encoding="utf-8")
    (folder / "secret.txt").write_text("outside the files folder", encoding="utf-8")
    store = MemoryStore(folder)
    store.add("story", "I lead the newsletter", "owner", ["evt-1"], "2026-01-05", "smart")
    store.add("facts", "Lighthouses fascinate me", "self-reported", [], "2026-01-05", "smart")
    store.save()
    store.add_candidate("skills", "I can outline", ["evt-2"], T0)
    impressions.write(folder, "owner", "Direct.")
    impressions.write(folder, "agent-b", "   ")
    diary.append(folder, T0, "First day.", tz="UTC")
    diary.append(folder, T0 + 86400, "Second day.", tz="UTC")
    snapshots.take(folder, 1767600000000, "night", day="2026-01-05", by="smart")
    shared = roster.shared_dir(core)
    (shared / A).mkdir(parents=True)
    (shared / A / "draft.md").write_text("shared draft", encoding="utf-8")
    return folder


# --- who may do what ------------------------------------------------------------------


def test_the_village_needs_someone_signed_in(client, home):
    for path in READER_GETS + OWNER_GETS:
        assert client.get(f"{API}/{path}").status_code == 401, path
    for method, path, body in OWNER_WRITES:
        assert getattr(client, method)(f"{API}/{path}", json=body).status_code == 401, path


def test_a_read_token_sees_the_roster_and_the_talk_but_nothing_private(owner, core, home):
    for scopes in (("read",), ("read", "write")):
        with token_client(owner, core, *scopes) as helper:
            for path in READER_GETS:
                assert helper.get(f"{API}/{path}").status_code == 200, path
            for path in OWNER_GETS:
                r = helper.get(f"{API}/{path}")
                assert r.status_code == 403 and "owner" in r.json()["detail"], path
            for method, path, body in OWNER_WRITES:
                r = getattr(helper, method)(f"{API}/{path}", json=body)
                assert r.status_code == 403, path
    with token_client(owner, core, "write") as writer:
        assert writer.get(f"{API}/agents").status_code == 403
    assert roster.pending(core) == []
    assert [e.kind for e in core.tape.after(0) if e.kind.startswith("village.")] == [
        "village.born"]


def test_the_owner_can_reach_every_route(owner, core, home):
    for path in READER_GETS + OWNER_GETS[:-1]:
        assert owner.get(f"{API}/{path}").status_code == 200, path
    assert owner.get(f"{API}/commands/abc").status_code == 404


def test_an_agent_id_is_checked_before_it_touches_a_path(owner, core, home):
    outside = roster.village_dir(core) / "agents" / "Agent_B"
    (outside / "home" / "files").mkdir(parents=True)
    (outside / "home" / "files" / "x.md").write_text("never on the roster", encoding="utf-8")
    for bad in ("nobody", "Agent_B", "..", "%2e%2e", "agent-a%2F..", "a" * 41, "agent-a%00"):
        for path in ("", "/chat", "/diary", "/memory", "/files", "/files/read?path=x.md",
                     "/events", "/snapshots"):
            r = owner.get(f"{API}/agents/{bad}{path}")
            assert r.status_code == 404, (bad, path, r.status_code)
        assert owner.post(f"{API}/agents/{bad}/chat", json={"text": "hi"}).status_code == 404
        assert owner.post(f"{API}/agents/{bad}/retire", json={}).status_code == 404
    assert roster.pending(core) == []


# --- reading an agent -----------------------------------------------------------------


def test_the_roster_lists_each_living_agent_with_its_vitals(owner, core, home):
    record.said(core, A, "owner", "How goes it?")
    record.said(core, A, A, "Well.")
    record.said(core, A, A, "One question though.")
    record.born(core, "agent-b", 8)
    record.retired(core, "agent-b", "left")
    assert owner.get(f"{API}/agents").json() == [{
        "id": A, "name": None, "role": "researcher", "retired": False,
        "status_line": "Working on T1", "mode": "work", "asleep": False,
        "needs": {"energy": 71.3, "frustration": 12.0, "social": 55.0, "curiosity": 40.0,
                  "purpose": 50.0},
        "mood": {"valence": 0.297, "arousal": -0.0554},
        "sleep_kind": None, "wake_at": None,
        "current_task": "T1", "waiting": 1, "unanswered": 2}]
    everyone = owner.get(f"{API}/agents?retired=true").json()
    assert [(a["id"], a["status_line"]) for a in everyone] == [
        (A, "Working on T1"), ("agent-b", "Retired")]


def test_what_an_agent_is_doing_is_said_in_one_line():
    assert routes.status_line(None) == "Not started yet"
    assert routes.status_line({"asleep": True, "sleep_kind": "night"}) == "Asleep"
    assert routes.status_line({"asleep": True, "sleep_kind": "nap"}) == "Taking a nap"
    assert routes.status_line({"mode": "rest", "current_task": "T1"}) == "Resting"
    assert routes.status_line({"mode": "social"}) == "In the break room"
    assert routes.status_line({"mode": "work"}) == "Working"


def test_an_agents_card_has_its_mood_config_temperament_last_night_and_counts(
        owner, core, home):
    record.slept(core, A, day="2026-01-05", by="smart", ops=3, rejected=0, snapshot=1)
    village.log(core).append("action", A, T0 + 20 * 365 * 86400, tool="write_file", ok=True)
    card = owner.get(f"{API}/agents/{A}").json()
    assert card["config"] == lens.clean_config({"role": "researcher"})
    assert set(card["mood"]) == {"valence", "arousal"} and -1 <= card["mood"]["valence"] <= 1
    assert card["temperament"]["mood_weights"]["energy"] == 0.2
    assert card["last_night"]["day"] == "2026-01-05" and card["seed"] == 7
    assert card["vitals"] == {"sleep_kind": None, "parked": ["T2"], "rest_until": None}
    assert card["counts"] == {"memory": 2, "candidates": 1, "diary_days": 2, "snapshots": 1,
                              "events_today": {"action": 1}}


def test_an_agent_with_no_saved_body_yet_still_has_a_card(owner, core):
    record.born(core, "agent-c", 1)
    card = owner.get(f"{API}/agents/agent-c").json()
    assert (card["status_line"], card["mood"], card["needs"]) == ("Not started yet", None, {})
    assert card["counts"]["memory"] == 0
    assert owner.get(f"{API}/agents/agent-c/files").json() == {"path": "", "items": []}
    assert owner.get(f"{API}/agents/agent-c/memory").json()["impressions"] == []


def test_memory_is_shown_by_section_with_where_each_line_came_from(owner, home):
    memory = owner.get(f"{API}/agents/{A}/memory").json()
    assert list(memory["sections"]) == ["self", "skills", "tools", "wants", "moments", "facts",
                                        "story"]
    assert [(i["text"], i["source"], i["evidence"]) for i in memory["sections"]["story"]] == [
        ("I lead the newsletter", "owner", ["evt-1"])]
    assert memory["sections"]["facts"][0]["source"] == "self-reported"
    assert [c["text"] for c in memory["candidates"]] == ["I can outline"]
    assert memory["impressions"] == [{"about": "owner", "text": "Direct."}]


def test_the_diary_opens_on_its_newest_day_and_refuses_a_day_that_is_not_a_date(owner, home):
    newest = owner.get(f"{API}/agents/{A}/diary").json()
    assert newest == {"days": ["2026-01-05", "2026-01-06"], "day": "2026-01-06",
                      "entries": [{"time": "08:00", "dream": False, "text": "Second day."}]}
    first = owner.get(f"{API}/agents/{A}/diary?day=2026-01-05").json()
    assert first["entries"][0]["text"] == "First day."
    assert owner.get(f"{API}/agents/{A}/diary?day=2026-02-01").json()["entries"] == []
    for bad in ("../secret", "..%2F..%2Fstate", "2026-1-5"):
        assert owner.get(f"{API}/agents/{A}/diary?day={bad}").status_code == 422


def test_a_retired_agents_memory_and_diary_can_still_be_read_but_not_written(
        owner, core, home):
    archive = roster.agent_dir(core, A, retired=True)
    archive.parent.mkdir(parents=True)
    home.rename(archive)
    record.retired(core, A, "done")
    assert owner.get(f"{API}/agents/{A}/diary").json()["day"] == "2026-01-06"
    assert len(owner.get(f"{API}/agents/{A}/memory").json()["sections"]["story"]) == 1
    assert owner.get(f"{API}/agents/{A}/files/read?path=notes.md").json()["text"] == "my notes"
    for method, path, body in (("post", "chat", {"text": "hi"}), ("post", "retire", {}),
                               ("post", "rollback", {"to": 1}), ("put", "config", {})):
        r = getattr(owner, method)(f"{API}/agents/{A}/{path}", json=body)
        assert r.status_code == 409 and "retired" in r.json()["detail"], path


# --- files ----------------------------------------------------------------------------


def test_an_agents_files_are_listed_folders_first_without_hidden_names(owner, home):
    top = owner.get(f"{API}/agents/{A}/files").json()
    assert [(i["name"], i["path"], i["dir"], i["size"]) for i in top["items"]] == [
        ("drafts", "drafts", True, None), ("notes.md", "notes.md", False, 8)]
    assert top["path"] == "" and top["items"][1]["modified"] > 1_600_000_000_000
    inner = owner.get(f"{API}/agents/{A}/files?path=drafts/").json()
    assert inner == {"path": "drafts", "items": [inner["items"][0]]}
    assert inner["items"][0]["path"] == "drafts/one.md"
    assert owner.get(f"{API}/agents/{A}/files?path=nowhere").status_code == 404
    assert owner.get(f"{API}/agents/{A}/files?path=notes.md").status_code == 404


def test_a_file_is_read_as_text_and_a_long_one_is_cut(owner, home):
    got = owner.get(f"{API}/agents/{A}/files/read?path=drafts\\one.md").json()
    assert (got["path"], got["text"], got["size"], got["truncated"]) == (
        "drafts/one.md", "draft one", 9, False)
    (home / "home" / "files" / "big.bin").write_bytes(b"\xff" + b"a" * routes.READ_BYTES)
    big = owner.get(f"{API}/agents/{A}/files/read?path=big.bin").json()
    assert big["truncated"] and big["size"] == routes.READ_BYTES + 1
    assert len(big["text"]) == routes.READ_BYTES and big["text"][0] == "\ufffd"
    assert owner.get(f"{API}/agents/{A}/files/read?path=drafts").status_code == 404
    assert owner.get(f"{API}/agents/{A}/files/read").status_code == 422


@pytest.mark.parametrize("path", [
    "../secret.txt", "../../../village.db", "drafts/../../secret.txt", "/etc/hosts",
    "..\\secret.txt", "C:\\Windows\\win.ini", "c:secret.txt", ".hidden", "drafts/.git/config",
    "notes.md\x00.txt", "..", "//server/share/x",
])
def test_no_path_leaves_the_agents_files_folder(owner, home, path):
    for route in ("files", "files/read"):
        r = owner.get(f"{API}/agents/{A}/{route}", params={"path": path})
        assert r.status_code == 400, (route, path, r.status_code)
        assert "outside" not in r.text and "secret" not in r.json()["detail"]
    assert owner.get(f"{API}/shared", params={"path": path}).status_code == 400


def test_a_link_that_leads_outside_is_neither_listed_nor_followed(owner, core, home):
    files = home / "home" / "files"
    try:
        os.symlink(home / "secret.txt", files / "link.txt")
        os.symlink(home, files / "up", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow making links")
    names = [i["name"] for i in owner.get(f"{API}/agents/{A}/files").json()["items"]]
    assert names == ["drafts", "notes.md"]
    for path in ("link.txt", "up/secret.txt", "up/memory/memory.json"):
        assert owner.get(f"{API}/agents/{A}/files/read", params={"path": path}
                         ).status_code == 400
    assert owner.get(f"{API}/agents/{A}/files", params={"path": "up"}).status_code == 400


def test_the_path_check_itself_refuses_what_resolves_outside(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    assert routes.inside(root, "") == root.resolve()
    assert routes.inside(root, "./a//") == (root / "a").resolve()
    for bad in ("..", "a/../..", "/abs", "a/../../root2", "~/.ssh", ".env"):
        with pytest.raises(HTTPException) as refused:
            routes.inside(root, bad)
        assert refused.value.status_code == 400


def test_the_shared_place_is_listed_and_read_through_one_route(owner, core, home):
    left = roster.shared_dir(core) / "left" / "agent-z" / "files"
    left.mkdir(parents=True)
    (left / "old.md").write_text("left behind", encoding="utf-8")
    top = owner.get(f"{API}/shared").json()
    assert [i["name"] for i in top["items"]] == [A, "left"]
    assert owner.get(f"{API}/shared?path={A}/draft.md").json()["text"] == "shared draft"
    assert owner.get(f"{API}/shared?path=left/agent-z/files/old.md").json()["text"] == \
        "left behind"
    assert owner.get(f"{API}/shared?path=nowhere").status_code == 404


def test_before_anything_is_shared_the_shared_place_is_empty(owner, core):
    assert owner.get(f"{API}/shared").json() == {"path": "", "items": []}
    assert owner.get(f"{API}/breakroom").json() == {"lines": []}
    assert owner.get(f"{API}/board").json() == {"posts": []}
    assert owner.get(f"{API}/agents").json() == []
    assert owner.get(f"{API}/priorities").json() == []


# --- what the owner does --------------------------------------------------------------


def test_a_birth_is_left_as_a_command_for_the_village_to_run(owner, core):
    r = owner.post(f"{API}/agents", json={
        "role": " researcher ", "seed": 7, "local_only": True,
        "models": {"night": {"provider_id": "p1", "model": "m1"}, "beat": {"role": "cheap"}}})
    cid = r.json()["command_id"]
    assert r.status_code == 200 and list(r.json()) == ["command_id"]
    waiting = roster.pending(core)
    assert [(c["id"], c["kind"], c["agent"], c["by"]) for c in waiting] == [
        (cid, "birth", None, "owner")]
    assert waiting[0]["payload"] == {
        "role": "researcher", "seed": 7, "local_only": True,
        "models": {"beat": {"role": "cheap"}, "night": {"provider_id": "p1", "model": "m1"}}}
    assert owner.get(f"{API}/commands/{cid}").json() == {
        "done": False, "result": None, "kind": "birth", "agent": None}
    roster.finish(core, cid, {"ok": True, "agent_id": A})
    assert owner.get(f"{API}/commands/{cid}").json()["result"] == {"ok": True, "agent_id": A}
    assert owner.get(f"{API}/commands/{cid}").json()["done"] is True
    # The web process made nobody: no folder, no Tape event.
    assert not roster.village_dir(core).exists()
    assert not [e for e in core.tape.after(0) if e.kind == "village.born"]


def test_a_birth_with_a_half_chosen_model_or_an_unknown_field_is_refused(owner, core):
    for body in ({"models": {"night": {"provider_id": "p1"}}},
                 {"models": {"beat": {"role": "embeddings"}}},
                 {"models": {"beat": {}}}, {"seed": -1}, {"role": "x" * 81},
                 {"agent_id": "agent-q"}):
        assert owner.post(f"{API}/agents", json=body).status_code == 422, body
    assert roster.pending(core) == []


def test_configuring_an_agent_prints_its_whole_setup(owner, core, home):
    r = owner.put(f"{API}/agents/{A}/config", json={
        "role": "editor", "night": {"role": "smart"}, "beat": {"provider_id": "p1", "model": "m1"},
        "local_only": True, "capabilities": ["desktop", "desktop", "hands.mail"]})
    want = {"role": "editor", "beat": {"provider_id": "p1", "model": "m1"},
            "night": {"role": "smart"}, "local_only": True,
            "capabilities": ["desktop", "hands.mail"]}
    assert r.status_code == 200 and r.json() == want
    printed = [e for e in core.tape.after(0) if e.kind == "village.configured"][-1]
    assert printed.actor == "owner" and printed.subject == f"agent:{A}"
    assert lens.config(core, A) == want
    assert owner.get(f"{API}/agents/{A}").json()["role"] == "editor"
    bad = owner.put(f"{API}/agents/{A}/config", json={"capabilities": ["Not Valid!"]})
    assert bad.status_code == 422 and "Nothing was changed" in bad.json()["detail"]
    assert lens.config(core, A) == want


def test_retiring_and_rolling_back_are_left_as_commands(owner, core, home):
    retire = owner.post(f"{API}/agents/{A}/retire", json={"note": " Finished. "}).json()
    back = owner.post(f"{API}/agents/{A}/rollback",
                      json={"to": 1, "diary": True, "note": "bad night"}).json()
    assert [(c["id"], c["kind"], c["agent"], c["payload"]) for c in roster.pending(core)] == [
        (retire["command_id"], "retire", A, {"note": "Finished."}),
        (back["command_id"], "rollback", A, {"to": 1, "diary": True, "note": "bad night"})]
    assert home.is_dir() and MemoryStore(home).items  # nothing was touched here
    missing = owner.post(f"{API}/agents/{A}/rollback", json={"to": 9})
    assert missing.status_code == 404 and "Nothing was changed" in missing.json()["detail"]
    assert owner.post(f"{API}/agents/{A}/rollback", json={"to": 0}).status_code == 422
    assert len(roster.pending(core)) == 2


def test_snapshots_are_listed_newest_first(owner, home):
    snapshots.take(home, 1767686400000, "before rollback", to=1)
    got = owner.get(f"{API}/agents/{A}/snapshots").json()["snapshots"]
    assert [(s["seq"], s["reason"]) for s in got] == [(2, "before rollback"), (1, "night")]
    assert got[1]["day"] == "2026-01-05" and got[1]["paths"] == ["memory", "impressions",
                                                                 "diary"]


def test_the_owners_chat_line_goes_on_the_tape_and_reads_back(owner, core, home):
    sent = owner.post(f"{API}/agents/{A}/chat", json={"text": "  How goes it?  "}).json()
    assert (sent["sender"], sent["text"], sent["urgent"], sent["delivered"]) == (
        "owner", "How goes it?", False, "now")
    printed = core.tape.get(sent["id"])
    assert printed.kind == "village.said" and printed.actor == "owner"
    assert printed.payload == {"agent_id": A, "sender": "owner", "text": "How goes it?",
                               "urgent": False}
    record.said(core, A, A, "Well, thank you.")
    chat = owner.get(f"{API}/agents/{A}/chat").json()
    assert [(m["sender"], m["text"]) for m in chat["messages"]] == [
        ("owner", "How goes it?"), ("agent", "Well, thank you.")]
    assert chat["asleep"] is False and chat["more"] is False
    for bad in ({"text": "   "}, {"text": "x" * (routes.CHAT_CHARS + 1)}, {}):
        r = owner.post(f"{API}/agents/{A}/chat", json=bad)
        assert r.status_code == 422, bad
    assert len(owner.get(f"{API}/agents/{A}/chat").json()["messages"]) == 2


def test_a_line_to_a_sleeping_agent_waits_unless_it_is_a_call(owner, home):
    state = json.loads((home / "state.json").read_text(encoding="utf-8"))
    (home / "state.json").write_text(json.dumps({**state, "asleep": True}), encoding="utf-8")
    quiet = owner.post(f"{API}/agents/{A}/chat", json={"text": "No rush."}).json()
    call = owner.post(f"{API}/agents/{A}/chat", json={"text": "Wake up.", "urgent": True}).json()
    assert (quiet["delivered"], call["delivered"]) == ("when it wakes", "now")
    assert owner.get(f"{API}/agents/{A}/chat").json()["asleep"] is True


def test_older_chat_than_the_lens_keeps_is_paged_by_sequence(owner, core, home):
    for i in range(230):
        record.said(core, A, "owner" if i % 2 else A, f"line {i}")
    newest = owner.get(f"{API}/agents/{A}/chat").json()
    assert len(newest["messages"]) == 200 and newest["more"] is True
    assert newest["messages"][0]["text"] == "line 30"
    older = owner.get(f"{API}/agents/{A}/chat?before={newest['messages'][0]['seq']}").json()
    assert [m["text"] for m in older["messages"]] == [f"line {i}" for i in range(30)]
    middle = owner.get(f"{API}/agents/{A}/chat?before={newest['messages'][10]['seq']}").json()
    assert [m["text"] for m in middle["messages"]] == [f"line {i}" for i in range(30, 40)]


def test_priorities_are_set_changed_and_switched_off_by_the_owner(owner, core, home):
    made = owner.post(f"{API}/priorities", json={
        "title": "  Draft   newsletters ", "why": "Monthly.", "agents": [A, A]}).json()
    assert (made["id"], made["title"], made["agents"], made["active"]) == (
        "p-draft-newsletters", "Draft newsletters", [A], True)
    twin = owner.post(f"{API}/priorities", json={"title": "Draft newsletters"}).json()
    assert twin["id"] == "p-draft-newsletters-2"
    changed = owner.put(f"{API}/priorities/{made['id']}",
                        json={"active": False, "details": "One a week."}).json()
    assert (changed["title"], changed["why"], changed["details"], changed["active"]) == (
        "Draft newsletters", "Monthly.", "One a week.", False)
    assert [p["id"] for p in owner.get(f"{API}/priorities").json()] == [
        "p-draft-newsletters", "p-draft-newsletters-2"]
    printed = [e for e in core.tape.after(0) if e.kind == "village.prioritized"]
    assert len(printed) == 3 and printed[-1].payload["suggested_by"] is None
    assert printed[-1].subject == "priority:p-draft-newsletters"
    for r in (owner.post(f"{API}/priorities", json={"title": "  "}),
              owner.post(f"{API}/priorities", json={"title": "x", "agents": ["nobody"]}),
              owner.put(f"{API}/priorities/{made['id']}", json={"title": ""})):
        assert r.status_code == 422 and "Nothing was changed" in r.json()["detail"]
    assert owner.put(f"{API}/priorities/p-none", json={"active": False}).status_code == 404
    assert len([e for e in core.tape.after(0) if e.kind == "village.prioritized"]) == 3


def test_a_priority_id_is_made_from_its_title_and_never_clashes():
    assert routes.priority_id("Draft the Newsletters!", set()) == "p-draft-the-newsletters"
    assert routes.priority_id("???", {"p-priority"}) == "p-priority-2"


# --- talk and events ------------------------------------------------------------------


def test_the_break_room_and_the_board_come_from_the_village_log(owner, core, home):
    log = village.log(core)
    hi = log.append("breakroom", A, T0, text="Morning.")
    log.append("social_unanswered", A, T0 + 60, evidence=[hi.id])
    ask = log.append("board_post", A, T0 + 120, post_kind="help", text="Who knows fonts?")
    offer = log.append("board_post", "agent-b", T0 + 180, post_kind="offer", text="I do.")
    log.append("board_resolved", "agent-b", T0 + 240, post=ask.id, note="Sorted.")
    room = owner.get(f"{API}/breakroom").json()["lines"]
    assert [(m["agent"], m["kind"], m["text"]) for m in room] == [
        (A, "breakroom", "Morning."),
        (A, "social_unanswered", "(nobody answered; went back to work)")]
    assert room[0]["ts"] == 1767600000000 and room[0]["id"] == hi.id
    posts = owner.get(f"{API}/board").json()["posts"]
    assert [(p["id"], p["kind"], p["open"]) for p in posts] == [
        (ask.id, "help", False), (offer.id, "offer", True)]
    assert posts[0]["resolved"] == {"by": "agent-b", "ts": 1767600240000, "note": "Sorted."}


def test_an_agents_events_can_be_filtered_by_kind_and_paged_backwards(owner, core, home):
    log = village.log(core)
    for i in range(6):
        log.append("action" if i % 2 else "thought", A, T0, tool="t", n=i)
    log.append("action", "agent-b", T0, tool="other")
    everything = owner.get(f"{API}/agents/{A}/events").json()["events"]
    assert [e["data"]["n"] for e in everything] == [0, 1, 2, 3, 4, 5]
    assert everything[0]["ts"] == 1767600000000 and everything[0]["agent"] == A
    actions = owner.get(f"{API}/agents/{A}/events?kinds=action&limit=2").json()["events"]
    assert [e["data"]["n"] for e in actions] == [3, 5]
    # All six share one millisecond; paging goes by id, so none is skipped or repeated.
    older = owner.get(f"{API}/agents/{A}/events?before={everything[4]['id']}&limit=3").json()
    assert [e["data"]["n"] for e in older["events"]] == [1, 2, 3]
    for bad in ("kinds=Action", "kinds=a;drop", "before=12", "before=evt-99999"):
        assert owner.get(f"{API}/agents/{A}/events?{bad}").status_code == 422, bad


# --- vitals, wake times and capabilities ------------------------------------------------


def set_body(home, **fields):
    path = home / "state.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body.update(fields)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_a_resting_or_napping_agent_says_when_it_will_be_up_in_milliseconds(owner, home):
    set_body(home, mode="rest", rest_until=1767600600.5)
    row = owner.get(f"{API}/agents").json()[0]
    assert row["wake_at"] == 1767600600500
    assert owner.get(f"{API}/agents/{A}").json()["vitals"]["rest_until"] == 1767600600500
    set_body(home, mode="sleep", asleep=True, sleep_kind="nap", nap_until=1767601000.0)
    row = owner.get(f"{API}/agents").json()[0]
    assert (row["sleep_kind"], row["wake_at"]) == ("nap", 1767601000000)


def test_a_night_sleeper_wakes_at_the_usual_hour_on_its_own_clock(owner, core, home):
    core.settings.set("owner.timezone", "UTC", actor="owner")
    core.settings.set("village.sleep.wake_hour", 7, actor="owner")
    set_body(home, mode="sleep", asleep=True, sleep_kind="night",
             temperament={"sleep_offset_minutes": 90})
    row = owner.get(f"{API}/agents").json()[0]
    now = time.time() * 1000
    assert row["sleep_kind"] == "night"
    here = datetime.fromtimestamp(now / 1000, UTC)
    if 8.5 <= here.hour + here.minute / 60 < 13.5:
        # Its own clock already reads past seven: it wakes when rested, so no hour is given.
        assert row["wake_at"] is None
    else:
        assert now < row["wake_at"] <= now + 24 * 3600 * 1000
        at = datetime.fromtimestamp(row["wake_at"] / 1000, UTC)
        assert (at.hour, at.minute) == (8, 30)
    set_body(home, sleep_kind="forced")
    assert owner.get(f"{API}/agents").json()[0]["wake_at"] is None


def test_the_wake_hour_is_worked_out_the_same_at_any_time_of_day():
    from jimmyhq.village.routes import night_wake_ms
    body = {"temperament": {"sleep_offset_minutes": 90}}
    day = datetime(2027, 3, 10, tzinfo=UTC)
    at = lambda h, m=0: day.replace(hour=h, minute=m).timestamp()  # noqa: E731
    up = lambda ms: datetime.fromtimestamp(ms / 1000, UTC)  # noqa: E731
    assert up(night_wake_ms(body, 7, "UTC", at(2))) == day.replace(hour=8, minute=30)
    assert up(night_wake_ms(body, 7, "UTC", at(22))) == day.replace(day=11, hour=8, minute=30)
    assert night_wake_ms(body, 7, "UTC", at(8, 30)) is None
    assert night_wake_ms(body, 7, "UTC", at(13, 29)) is None
    assert up(night_wake_ms(body, 7, "UTC", at(13, 30))) == day.replace(day=11, hour=8, minute=30)


def test_the_roster_rows_carry_mood_and_none_without_a_body(owner, core, home):
    record.born(core, "agent-c", 1)
    rows = {r["id"]: r for r in owner.get(f"{API}/agents").json()}
    assert set(rows[A]["mood"]) == {"valence", "arousal"}
    assert rows["agent-c"]["mood"] is None and rows["agent-c"]["wake_at"] is None


def test_the_vitals_history_is_the_five_needs_over_time_in_milliseconds(owner, core, home):
    log = village.log(core)
    t = time.time()
    log.append("need_change", A, t - 7200, need="energy", delta=-1, value=60.0)
    log.append("need_change", A, t - 3600, need="energy", delta=-1, value=59.0)
    log.append("need_change", A, t - 1800, need="purpose", delta=5, value=55.0)
    log.append("need_change", "agent-b", t - 1800, need="energy", delta=5, value=1.0)
    rows = owner.get(f"{API}/agents/{A}/vitals?hours=1").json()
    assert len(rows) == 1 and rows[0]["t"] == round((t - 1800) * 1000)
    assert rows[0]["energy"] == 59.0 and rows[0]["purpose"] == 55.0
    wide = owner.get(f"{API}/agents/{A}/vitals?hours=3").json()
    assert [r["energy"] for r in wide] == [60.0, 59.0, 59.0]
    assert set(wide[0]) == {"t", "energy", "frustration", "social", "curiosity", "purpose"}
    assert owner.get(f"{API}/agents/{A}/vitals").status_code == 200


def test_a_long_vitals_history_is_cut_to_about_two_hundred_points(owner, core, home):
    log = village.log(core)
    t = time.time() - 3000
    for i in range(1000):
        log.append("need_change", A, t + i * 2, need="social", delta=0.1, value=float(i % 100))
    rows = owner.get(f"{API}/agents/{A}/vitals").json()
    assert 100 <= len(rows) <= routes.VITALS_POINTS
    assert rows == sorted(rows, key=lambda r: r["t"])


def test_vitals_checks_the_agent_id_the_hours_and_who_is_asking(owner, core, home):
    assert owner.get(f"{API}/agents/nobody/vitals").status_code == 404
    assert owner.get(f"{API}/agents/{A}/vitals?hours=0").status_code == 422
    assert owner.get(f"{API}/agents/{A}/vitals?hours=9999").status_code == 422
    reader = token_client(owner, core, "read")
    assert reader.get(f"{API}/agents/{A}/vitals").status_code == 200
    assert reader.get(f"{API}/capabilities").status_code == 200


def test_the_capabilities_list_holds_only_what_the_tool_catalog_recognises(
        owner, core, monkeypatch):
    assert owner.get(f"{API}/capabilities").json() == routes.capability_list(core)
    from jimmyhq.village import tools
    monkeypatch.setattr(tools, "specs", lambda: [
        {"name": "send_post", "capability": "social_posting"}, {"name": "read_file"}])
    assert owner.get(f"{API}/capabilities").json() == [{
        "id": "social_posting", "label": "Social posting",
        "description": "Lets the agent use send_post.", "tools": ["send_post"],
        "ready": True, "problem": None, "settings": None}]


def test_the_web_capability_says_what_leaves_the_machine_and_what_is_not_set_up_yet(
        owner, core):
    [web] = [c for c in owner.get(f"{API}/capabilities").json() if c["id"] == "web"]
    assert (web["id"], web["label"], web["tools"]) == ("web", "Web lookup",
                                                       ["web_search", "web_read"])
    for said in ("by itself", "only read", "search provider", "fetched from this machine",
                 "sent to the agent's model", "hosted", "Local only", "Off for every agent"):
        assert said in web["description"], said
    assert web["ready"] is False and web["settings"] == "lookup"
    assert web["problem"].startswith("Web lookup is switched off")
    core.settings.set("lookup.enabled", True, actor="owner")
    web = next(c for c in owner.get(f"{API}/capabilities").json() if c["id"] == "web")
    assert web["ready"] is False and "No search provider is ready" in web["problem"]
    core.settings.set("lookup.search_provider", "searxng", actor="owner")
    core.settings.set("lookup.searxng_url", "https://search.example.com", actor="owner")
    web = next(c for c in owner.get(f"{API}/capabilities").json() if c["id"] == "web")
    assert web["ready"] is True and web["problem"] is None


def test_showing_needs_and_mood_on_cards_is_a_setting_that_starts_on(core):
    assert core.settings.get("village.show_vitals") is True
