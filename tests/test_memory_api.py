"""Memory over HTTP: who may do what, the shapes, and what odd input gets back."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from jimmyhq.web.app import create_app
from test_memory_fakes import with_embedder

PASSWORD = "correct horse battery"
MISSING = "01JABCDEFGHJKMNPQRSTVWXYZ0"
API = "/api/memory"


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


def test_memory_needs_someone_signed_in(client):
    for path in ("tree", "status", "facts", "search?q=x", "notes/shared/people.md"):
        assert client.get(f"{API}/{path}").status_code == 401, path
    assert client.post(f"{API}/scan").status_code == 401
    assert client.put(f"{API}/notes/shared/x.md", json={"text": "x"}).status_code == 401


def test_a_read_token_can_read_and_search_but_only_the_owner_changes_memory(owner, core):
    owner.post(f"{API}/facts", json={"text": "Likes lemon cake", "kind": "preference"})
    fact_id = owner.get(f"{API}/facts").json()["facts"][0]["id"]
    for scopes in (("read",), ("read", "write")):
        with token_client(owner, core, *scopes) as helper:
            for path in ("tree", "status", "facts", f"facts/{fact_id}", "search?q=lemon",
                         "notes/shared/preferences.md", "notes/shared/preferences.md/history"):
                assert helper.get(f"{API}/{path}").status_code == 200, path
            assert helper.post(f"{API}/search", json={"q": "lemon"}).status_code == 200
            refused = [
                helper.put(f"{API}/notes/shared/x.md", json={"text": "x"}),
                helper.delete(f"{API}/notes/shared/preferences.md"),
                helper.post(f"{API}/notes/move", json={"from": "shared/people.md",
                                                       "to": "shared/p.md"}),
                helper.post(f"{API}/facts", json={"text": "x y", "kind": "about"}),
                helper.patch(f"{API}/facts/{fact_id}", json={"confirmed": False}),
                helper.delete(f"{API}/facts/{fact_id}"),
                helper.post(f"{API}/scan"), helper.post(f"{API}/reindex"),
            ]
            assert [r.status_code for r in refused] == [403] * 8
            assert all("owner" in r.json()["detail"] for r in refused)
    with token_client(owner, core, "write") as writer:
        assert writer.get(f"{API}/tree").status_code == 403
    searches = [e for e in core.tape.after(0) if e.kind == "memory.recalled"]
    assert [e.actor for e in searches] == ["token:helper"] * 4


def test_the_tree_lists_folders_and_notes_with_sizes_times_and_fact_counts(owner):
    owner.put(f"{API}/notes/agents/scout/log.md", json={"text": "- one\n- two\n"})
    tree = owner.get(f"{API}/tree").json()
    assert tree["folders"] == ["agents", "agents/scout", "shared"]
    assert [n["path"] for n in tree["notes"]][:2] == ["agents/scout/log.md",
                                                     "shared/about-me.md"]
    note = tree["notes"][0]
    assert note["facts"] == 2 and note["size"] > 0 and note["modified_ms"] > 1_600_000_000_000
    assert set(note) == {"path", "size", "modified_ms", "facts"}


def test_a_note_can_be_created_read_saved_moved_and_deleted(owner):
    made = owner.put(f"{API}/notes/shared/ideas.md", json={"text": "# Ideas\n\n- Build a shed\n"})
    assert made.status_code == 200
    note = made.json()
    assert set(note) == {"path", "text", "revision", "size", "modified_ms", "facts"}
    assert re.search(r"- Build a shed <!-- m:\w{26} -->\n", note["text"])
    assert note["facts"][0]["text"] == "Build a shed" and note["facts"][0]["confirmed"] is True
    assert owner.get(f"{API}/notes/shared/ideas.md").json() == note
    saved = owner.put(f"{API}/notes/shared/ideas.md",
                      json={"text": note["text"] + "More.\n", "revision": note["revision"]})
    assert saved.status_code == 200 and saved.json()["revision"] != note["revision"]
    stale = owner.put(f"{API}/notes/shared/ideas.md",
                      json={"text": "clobber", "revision": note["revision"]})
    assert stale.status_code == 409 and "Reload it" in stale.json()["detail"]
    assert owner.get(f"{API}/notes/shared/ideas.md").json()["text"].endswith("More.\n")
    moved = owner.post(f"{API}/notes/move", json={"from": "shared/ideas.md",
                                                  "to": "shared/plans/ideas.md"})
    assert moved.status_code == 200 and moved.json()["path"] == "shared/plans/ideas.md"
    assert moved.json()["facts"][0]["id"] == note["facts"][0]["id"]
    assert owner.get(f"{API}/notes/shared/ideas.md").status_code == 404
    clash = owner.post(f"{API}/notes/move", json={"from": "shared/plans/ideas.md",
                                                  "to": "shared/people.md"})
    assert clash.status_code == 409
    assert owner.delete(f"{API}/notes/shared/plans/ideas.md").json() == {"ok": True}
    gone = owner.delete(f"{API}/notes/shared/plans/ideas.md")
    assert gone.status_code == 404 and "Reload" in gone.json()["detail"]
    assert owner.get(f"{API}/facts/{note['facts'][0]['id']}").json()["fact"]["retired_ms"]


def test_history_lists_versions_and_serves_each_ones_text(owner):
    owner.put(f"{API}/notes/shared/log.md", json={"text": "first\n"})
    owner.put(f"{API}/notes/shared/log.md", json={"text": "second\n"})
    history = owner.get(f"{API}/notes/shared/log.md/history").json()
    assert history["path"] == "shared/log.md"
    versions = history["versions"]
    assert [v["actor"] for v in versions] == ["owner", "owner"]
    assert set(versions[0]) == {"version", "ts", "actor", "size", "facts", "note", "available"}
    old = owner.get(f"{API}/notes/shared/log.md/history/{versions[1]['version']}")
    assert old.status_code == 200
    assert old.json()["text"] == "first\n" and old.json()["actor"] == "owner"
    assert owner.get(f"{API}/notes/shared/log.md/history/{MISSING}").status_code == 404
    assert owner.get(f"{API}/notes/shared/log.md/history/nope").status_code == 404
    assert owner.get(f"{API}/notes/shared/never.md/history").json()["versions"] == []
    # A note that lives in a folder called "history" is still a note.
    owner.put(f"{API}/notes/shared/history/old.md", json={"text": "kept\n"})
    assert owner.get(f"{API}/notes/shared/history/old.md").json()["text"] == "kept\n"


def test_facts_can_be_added_listed_changed_and_forgotten(owner, core):
    made = owner.post(f"{API}/facts", json={"text": "Prefers tea to coffee", "kind": "preference"})
    assert made.status_code == 201 and made.json()["created"] is True
    fact = made.json()["fact"]
    assert set(fact) == {"id", "text", "note", "line", "kind", "confidence", "confirmed",
                         "saved_by", "evidence", "created_ms", "updated_ms", "retired_ms",
                         "retired_reason"}
    assert (fact["note"], fact["confirmed"], fact["saved_by"], fact["confidence"]) == (
        "shared/preferences.md", True, "owner", 1.0)
    again = owner.post(f"{API}/facts", json={"text": "prefers tea to coffee!", "kind": "about"})
    assert again.status_code == 201 and again.json()["created"] is False
    assert again.json()["fact"]["id"] == fact["id"]
    owner.post(f"{API}/facts", json={"text": "Works from the garden room", "kind": "about",
                                     "note": "shared/places.md"})
    listed = owner.get(f"{API}/facts").json()
    assert [f["text"] for f in listed["facts"]] == ["Works from the garden room",
                                                    "Prefers tea to coffee"]
    assert listed["next_before"] is None
    assert len(owner.get(f"{API}/facts?kind=preference").json()["facts"]) == 1
    assert len(owner.get(f"{API}/facts?q=garden&confirmed=true").json()["facts"]) == 1
    assert owner.get(f"{API}/facts?confirmed=false").json()["facts"] == []
    assert len(owner.get(f"{API}/facts?note=shared/places.md").json()["facts"]) == 1
    page = owner.get(f"{API}/facts?limit=1").json()
    assert len(page["facts"]) == 1 and page["next_before"]
    rest = owner.get(f"{API}/facts", params={"limit": 1, "before": page["next_before"]}).json()
    assert rest["facts"][0]["id"] == fact["id"]
    changed = owner.patch(f"{API}/facts/{fact['id']}",
                          json={"text": "Prefers green tea", "kind": "taste", "confirmed": False})
    assert changed.status_code == 200
    assert (changed.json()["fact"]["text"], changed.json()["fact"]["kind"],
            changed.json()["fact"]["confirmed"]) == ("Prefers green tea", "taste", False)
    assert "Prefers green tea <!--" in owner.get(
        f"{API}/notes/shared/preferences.md").json()["text"]
    forgotten = owner.request("DELETE", f"{API}/facts/{fact['id']}", json={"reason": "Not true."})
    assert forgotten.status_code == 200
    assert forgotten.json()["fact"]["retired_reason"] == "Not true."
    assert "green tea" not in owner.get(f"{API}/notes/shared/preferences.md").json()["text"]
    assert owner.delete(f"{API}/facts/{fact['id']}").status_code == 404
    assert owner.patch(f"{API}/facts/{fact['id']}", json={"kind": "x"}).status_code == 404
    assert owner.get(f"{API}/facts/{fact['id']}").json()["fact"]["retired_ms"]
    assert owner.get(f"{API}/facts/{MISSING}").status_code == 404


def test_search_returns_what_recall_would_and_says_how_it_searched(owner, core):
    owner.post(f"{API}/facts", json={"text": "Sam enjoys a cup of tea", "kind": "person"})
    owner.put(f"{API}/notes/agents/scout/log.md", json={"text": "- Scout saw a dog\n"})
    plain = owner.get(f"{API}/search", params={"q": "cup of tea"}).json()
    assert set(plain) == {"passages", "trimmed", "methods", "meaning", "scope", "k",
                          "budget_chars", "shown_chars", "receipt_id"}
    assert plain["methods"] == ["keyword"] and plain["meaning"]["used"] is False
    assert "No embeddings model is chosen" in plain["meaning"]["reason"]
    (passage,) = plain["passages"]
    assert set(passage) == {"id", "text", "note", "heading", "fact_id", "score", "why",
                            "confirmed", "chars"}
    assert (passage["text"], passage["note"], passage["why"]) == (
        "Sam enjoys a cup of tea", "shared/people.md", "keyword")
    assert plain["scope"] == ["shared"] and plain["k"] == 8 and plain["budget_chars"] == 4000
    receipt = core.tape.get(plain["receipt_id"])
    assert receipt.kind == "memory.recalled" and receipt.actor == "owner"
    assert receipt.payload["shown_chars"] == plain["shown_chars"] > 0
    assert owner.get(f"{API}/search", params={"q": "dog"}).json()["passages"] == []
    scoped = owner.get(f"{API}/search", params={"q": "dog", "scope": "agents/scout, shared"})
    assert [p["note"] for p in scoped.json()["passages"]] == ["agents/scout/log.md"]
    assert scoped.json()["scope"] == ["agents/scout", "shared"]
    everywhere = owner.get(f"{API}/search", params={"q": "dog tea", "scope": "*", "k": 1})
    assert len(everywhere.json()["passages"]) == 1 and everywhere.json()["scope"] == ["*"]
    posted = owner.post(f"{API}/search", json={"q": "dog", "scope": ["agents/scout", "shared"]})
    assert posted.status_code == 200
    assert posted.json()["passages"] == scoped.json()["passages"]
    assert owner.post(f"{API}/search", json={"q": "dog", "scope": []}).status_code == 422
    assert owner.post(f"{API}/search", json={"q": "dog", "k": "3"}).status_code == 422
    with_embedder(core)
    owner.post(f"{API}/scan")
    from jimmyhq import memory
    memory.embed_pending(core)
    by_meaning = owner.get(f"{API}/search", params={"q": "espresso"}).json()
    assert by_meaning["methods"] == ["keyword", "meaning"]
    assert by_meaning["meaning"] == {"used": True, "reason": None, "model": "home/embed-a"}
    assert by_meaning["passages"][0]["why"] == "meaning"


def test_status_says_what_the_library_holds_and_what_the_last_scan_did(owner, core):
    status = owner.get(f"{API}/status").json()
    assert set(status) == {"library_path", "counts", "index_version", "last_scan", "skipped",
                           "meaning_search", "watch_disk"}
    assert status["library_path"] == str(core.config.data / "memory")
    assert status["counts"] == {"notes": 6, "facts": 0, "chunks": 0, "chunks_with_vectors": 0}
    assert status["last_scan"]["added"] == 6 and status["watch_disk"] is True
    (core.config.data / "memory" / "shared" / "byhand.md").write_text("- By hand\n")
    (core.config.data / "memory" / "shared" / "bad.md").write_bytes(b"\xff\xfe")
    scanned = owner.post(f"{API}/scan").json()["scan"]
    assert (scanned["added"], scanned["facts_added"], scanned["ids_written"],
            scanned["skipped"]) == (1, 1, 1, 1)
    assert set(scanned) == {"added", "changed", "removed", "moved", "facts_added",
                            "facts_edited", "facts_retired", "at_ms", "notes", "ids_written",
                            "deferred", "skipped", "took_ms"}
    status = owner.get(f"{API}/status").json()
    assert status["index_version"] == 1 and status["last_scan"]["at_ms"] == scanned["at_ms"]
    assert status["skipped"] == [{"path": "shared/bad.md",
                                  "reason": "It is not UTF-8 text. Save it as UTF-8 to "
                                            "include it."}]
    rebuilt = owner.post(f"{API}/reindex").json()["scan"]
    assert rebuilt["reindexed"] == 7 and rebuilt["changed"] == 0


ODD = [
    ("GET", "notes/shared/nope.md", None, 404),
    ("GET", "notes/shared/x.txt", None, 422),
    ("GET", "notes/..%2F..%2Fetc%2Fpasswd", None, 422),
    ("GET", "notes/%2Fetc%2Fpasswd.md", None, 422),
    ("GET", "notes/shared%5Cx.md", None, 422),
    ("GET", "notes/C:%5Cx.md", None, 422),
    ("GET", "notes/shared/%00.md", None, 422),
    ("GET", "notes/shared/con.md", None, 422),
    ("GET", "notes/shared/.hidden.md", None, 422),
    ("GET", "notes/shared/x.md%20", None, 422),
    ("GET", "notes/" + "a" * 600 + ".md", None, 422),
    ("GET", "notes/shared", None, 422),
    ("GET", "notes/shared/x.md/history/..%2F..", None, 422),
    ("PUT", "notes/shared/x.md", None, 422),
    ("PUT", "notes/shared/x.md", {}, 422),
    ("PUT", "notes/shared/x.md", {"text": 5}, 422),
    ("PUT", "notes/shared/x.md", {"text": "x", "extra": 1}, 422),
    ("PUT", "notes/shared/x.md", {"text": "x", "revision": "short"}, 422),
    ("PUT", "notes/shared/x.md", {"text": "x", "revision": "Z" * 64}, 422),
    ("PUT", "notes/shared/x.md", {"text": "x", "revision": "0" * 64}, 409),
    ("PUT", "notes/shared/x.md", {"text": "nul \x00 byte"}, 422),
    ("PUT", "notes/shared/x.md", [1, 2], 422),
    ("PUT", "notes/shared", {"text": "x"}, 422),
    ("PUT", "notes/shared/people.md/x.md", {"text": "x"}, 409),
    ("PUT", "notes/..%2Fx.md", {"text": "x"}, 422),
    ("DELETE", "notes/shared", None, 422),
    ("DELETE", "notes/shared/nope.md", None, 404),
    ("POST", "notes/move", {"from": "shared/people.md"}, 422),
    ("POST", "notes/move", {"from": "shared/people.md", "to": "../x.md"}, 422),
    ("POST", "notes/move", {"from": "shared/people.md", "to": "shared/people.md"}, 422),
    ("POST", "notes/move", {"from": "shared/nope.md", "to": "shared/y.md"}, 404),
    ("POST", "notes/move", {"from": 5, "to": []}, 422),
    ("GET", "facts?limit=0", None, 422),
    ("GET", "facts?limit=abc", None, 422),
    ("GET", "facts?confirmed=maybe", None, 422),
    ("GET", "facts?before=garbage", None, 422),
    ("GET", "facts?kind=BAD%20KIND", None, 422),
    ("GET", "facts?q=%25%27%22%5C_", None, 200),
    ("GET", "facts/not-an-id", None, 404),
    ("POST", "facts", {}, 422),
    ("POST", "facts", {"text": "", "kind": "about"}, 422),
    ("POST", "facts", {"text": "two\nlines", "kind": "about"}, 422),
    ("POST", "facts", {"text": "fine words", "kind": "Bad Kind"}, 422),
    ("POST", "facts", {"text": "fine words", "kind": "about", "note": "/abs.md"}, 422),
    ("POST", "facts", {"text": "fine words", "kind": "about", "confidence": 2}, 422),
    ("POST", "facts", {"text": "x" * 5000, "kind": "about"}, 422),
    ("PATCH", f"facts/{MISSING}", {"text": "x y"}, 404),
    ("PATCH", "facts/nope", {"confirmed": True}, 404),
    ("PATCH", f"facts/{MISSING}", {"confirmed": "yes"}, 422),
    ("PATCH", f"facts/{MISSING}", {"bogus": 1}, 422),
    ("DELETE", f"facts/{MISSING}", {"reason": "x" * 301}, 422),
    ("DELETE", f"facts/{MISSING}", {"reason": 5}, 422),
    ("DELETE", "facts/nope", None, 404),
    ("GET", "search", None, 422),
    ("GET", "search?q=" + "x" * 4001, None, 422),
    ("GET", "search?q=tea&k=0", None, 422),
    ("GET", "search?q=tea&k=many", None, 422),
    ("GET", "search?q=tea&scope=..", None, 422),
    ("GET", "search?q=tea&scope=%2Fetc", None, 422),
    ("GET", "search?q=tea&scope=shared%5Cx", None, 422),
    ("GET", "search?q=%22%27%29%20OR%20NEAR%28%2A", None, 200),
    ("GET", "search?q=tea%00", None, 422),
    ("GET", "search?q=AND%20OR%20NOT%20%22", None, 200),
    ("GET", "search?q=", None, 200),
    ("GET", "nothing-here", None, 404),
]


@pytest.mark.parametrize(("method", "path", "body", "status"), ODD)
def test_odd_input_gets_a_readable_answer_and_never_a_500(owner, method, path, body, status):
    response = owner.request(method, f"{API}/{path}", json=body)
    assert response.status_code == status, response.text
    if status >= 400:
        detail = response.json()["detail"]
        assert isinstance(detail, str) and 10 < len(detail) < 400
        assert "Traceback" not in detail and "\x00" not in detail


def test_a_request_that_is_not_json_is_refused_plainly(owner):
    for data in (b"{not json", b"\xff\xfe", b"", b'{"text": "half a pair \\ud800"}'):
        response = owner.put(f"{API}/notes/shared/x.md", content=data,
                             headers={"content-type": "application/json"})
        assert response.status_code == 422 and isinstance(response.json()["detail"], str)


# --- mining over HTTP ----------------------------------------------------------------


def _mined(owner, core, *facts):
    """One task read by a fake model that proposes ``facts`` from it."""
    from jimmyhq.tasks import service as tasks
    from test_memory_fakes import with_miner

    with_miner(core, lambda items: list(facts))
    tasks(core).create("Order tea, shortbread and oat biscuits, which the owner likes",
                       actor="owner")
    return owner.post(f"{API}/mine", json={"source": "tasks"})


def _fact(statement, confidence):
    return {"statement": statement, "kind": "preference", "confidence": confidence,
            "evidence": [1]}


def test_the_mining_routes_need_the_owner_to_change_anything_and_a_reader_to_look(owner, core):
    assert _mined(owner, core, _fact("The owner likes oat biscuits.", 0.6)).status_code == 200
    cand = owner.get(f"{API}/candidates").json()["candidates"][0]
    for scopes in (("read",), ("read", "write")):
        with token_client(owner, core, *scopes) as helper:
            for path in ("sources", "candidates", "candidates?state=bench", "activity"):
                assert helper.get(f"{API}/{path}").status_code == 200, path
            assert helper.post(f"{API}/mine").status_code == 403
            assert helper.post(f"{API}/candidates/{cand['id']}/keep").status_code == 403
            assert helper.post(f"{API}/candidates/{cand['id']}/decline",
                               json={"reason": "No."}).status_code == 403
    with TestClient(owner.app, base_url="http://testserver") as stranger:
        for path in ("sources", "candidates", "activity"):
            assert stranger.get(f"{API}/{path}").status_code == 401, path
        assert stranger.post(f"{API}/mine").status_code == 401
    assert owner.get(f"{API}/candidates").json()["candidates"][0]["state"] == "bench"


def test_mine_now_returns_its_counts_and_says_why_when_it_cannot_run(owner, core):
    nothing = owner.post(f"{API}/mine")
    assert nothing.status_code == 200
    body = nothing.json()
    assert body["ran"] is False and "No model is chosen" in body["reason"]
    assert body["sources"] == {} and body["filed"] == 0 and body["totals"]["read"] == 0
    done = _mined(owner, core, _fact("The owner likes oat biscuits.", 0.95),
                  _fact("The owner drinks tea.", 0.6), _fact("The owner is hungry.", 0.1))
    body = done.json()
    assert body["ran"] is True and body["reason"] is None and body["filed"] == 1
    assert body["sources"]["tasks"] == {
        "read": 1, "skipped": {}, "proposed": 3, "saved": 1, "sent": 1, "ignored": 1,
        "dropped": {"duplicate": 0, "declined": 0, "no_evidence": 0, "not_from_owner": 0,
                    "secret": 0, "too_long": 0, "invalid": 0, "too_many": 0}, "error": None}
    assert body["totals"]["proposed"] == 3 and body["unfinished"] == []
    assert owner.post(f"{API}/mine", json={}).json()["sources"] == {}  # nothing new
    assert owner.post(f"{API}/mine", json={"source": "nonsense"}).status_code == 404
    assert owner.post(f"{API}/mine", json={"source": 7}).status_code == 422
    assert owner.post(f"{API}/mine", json={"sauce": "tasks"}).status_code == 422
    assert owner.post(f"{API}/mine", json={"source": "x" * 41}).status_code == 422
    owner.put("/api/settings/memory.source.tasks", json={"value": False})
    off = owner.post(f"{API}/mine", json={"source": "tasks"})
    assert off.status_code == 409 and "switched off" in off.json()["detail"]


def test_the_sources_route_says_what_is_and_is_not_being_mined(owner, core):
    _mined(owner, core, _fact("The owner likes oat biscuits.", 0.95))
    body = owner.get(f"{API}/sources").json()
    assert set(body) == {"sources", "mining"}
    by_id = {s["id"]: s for s in body["sources"]}
    assert set(by_id) >= {"chat", "tasks", "bench"}
    done = by_id["tasks"]
    assert set(done) == {
        "id", "label", "description", "on", "setting", "last_read_ms", "items_read",
        "items_waiting", "items_waiting_more", "items_waiting_as_of_ms", "last_run",
        "all_time", "last_error"}
    assert done["on"] is True and done["items_read"] == 1 and done["items_waiting"] == 0
    assert done["setting"] == "memory.source.tasks" and done["last_error"] is None
    assert set(done["last_run"]) == {"read", "skipped", "skipped_total", "proposed", "saved",
                                     "sent", "ignored", "dropped", "dropped_total"}
    assert done["last_run"]["saved"] == 1 and done["all_time"]["proposed"] == 1
    assert by_id["chat"]["items_read"] == 0 and by_id["chat"]["last_read_ms"] is None
    assert set(body["mining"]) == {
        "on", "working", "reason", "last_run_ms", "next_run_ms", "interval_s", "model",
        "auto_save_above", "ignore_below", "thresholds_note", "pass_seconds", "unfinished",
        "open_cards", "max_open_cards", "waiting_for_bench"}
    assert body["mining"]["thresholds_note"] is None and body["mining"]["unfinished"] == []
    assert body["mining"]["model"] == {"role": "cheap", "name": "home/small",
                                       "is_local": True, "local_only": True}
    assert (body["mining"]["auto_save_above"], body["mining"]["ignore_below"]) == (0.85, 0.4)


def test_candidates_are_listed_filtered_paged_kept_and_declined(owner, core):
    statements = [f"The owner likes {word} biscuits." for word in
                  ("oat", "rye", "nut", "fig", "bran")]
    _mined(owner, core, *(_fact(s, 0.6) for s in statements),
           _fact("The owner likes shortbread.", 0.95), _fact("The owner is peckish.", 0.2))
    everything = owner.get(f"{API}/candidates").json()
    assert everything["counts"] == {"waiting": 0, "bench": 5, "saved": 1, "declined": 0,
                                    "ignored": 1, "withdrawn": 0, "review": 1}
    assert len(everything["candidates"]) == 7 and everything["next_before"] is None
    one = everything["candidates"][0]
    assert set(one) == {"id", "source", "statement", "kind", "confidence", "evidence", "quotes",
                        "proposed_ms", "state", "state_ms", "reason", "fact_id", "card_id",
                        "saved_automatically", "needs_review"}
    assert set(one["quotes"][0]) == {"ref", "who", "when_ms", "where", "quote"}
    for state, count in (("bench", 5), ("saved", 1), ("ignored", 1), ("review", 1),
                         ("waiting", 0), ("declined", 0), ("withdrawn", 0)):
        got = owner.get(f"{API}/candidates?state={state}").json()["candidates"]
        assert len(got) == count, state
    assert len(owner.get(f"{API}/candidates?source=tasks").json()["candidates"]) == 7
    assert owner.get(f"{API}/candidates?source=chat").json()["candidates"] == []
    seen, marker = [], None
    while True:
        page = owner.get(f"{API}/candidates", params={"limit": 3} | (
            {"before": marker} if marker else {})).json()
        seen += [c["id"] for c in page["candidates"]]
        marker = page["next_before"]
        if marker is None:
            break
    assert seen == [c["id"] for c in everything["candidates"]]
    for bad in ("state=nonsense", "limit=0", "limit=201", "before=nonsense", "before=1-2",
                f"state={'x' * 21}"):
        assert owner.get(f"{API}/candidates?{bad}").status_code == 422, bad
    # Keep one from the Bench, decline another: the Bench and the Memory screen agree.
    on_bench = owner.get(f"{API}/candidates?state=bench").json()["candidates"]
    kept = owner.post(f"{API}/candidates/{on_bench[0]['id']}/keep").json()["candidate"]
    assert kept["state"] == "saved" and kept["saved_automatically"] is False
    assert owner.get(f"{API}/facts/{kept['fact_id']}").json()["fact"]["confirmed"] is True
    card = owner.get(f"/api/bench/{on_bench[0]['card_id']}").json()
    assert card["status"] == "decided" and card["decision"]["action"] == "approve"
    target = f"{API}/candidates/{on_bench[1]['id']}/decline"
    for bad in ({}, {"reason": ""}, {"reason": "   "}, {"reason": 5}, {"reason": "x" * 301},
                {"reason": "ok", "extra": 1}):
        assert owner.post(target, json=bad).status_code == 422, bad
    assert owner.post(target).status_code == 422
    gone = owner.post(target, json={"reason": "Never liked them."}).json()["candidate"]
    assert gone["state"] == "declined" and gone["reason"] == "Never liked them."
    assert owner.get(f"/api/bench/{on_bench[1]['card_id']}").json()["decision"]["reason"] == (
        "Never liked them.")
    assert owner.post(target, json={"reason": "Again."}).status_code == 409
    assert owner.get("/api/bench").json()["counts"]["needs"] == 3
    for bad_id in ("nonsense", MISSING, "x" * 300):
        assert owner.post(f"{API}/candidates/{bad_id}/keep").status_code == 404, bad_id
        assert owner.post(f"{API}/candidates/{bad_id}/decline",
                          json={"reason": "No."}).status_code == 404, bad_id
    # Keeping the one saved automatically confirms it; declining it forgets it.
    auto = owner.get(f"{API}/candidates?state=review").json()["candidates"][0]
    confirmed = owner.post(f"{API}/candidates/{auto['id']}/keep").json()["candidate"]
    assert confirmed["needs_review"] is False and confirmed["saved_automatically"] is True
    owner.post(f"{API}/candidates/{auto['id']}/decline", json={"reason": "Too sweet."})
    assert owner.get(f"{API}/facts/{auto['fact_id']}").json()["fact"]["retired_reason"] == (
        "Too sweet.")
    assert owner.get(f"{API}/candidates?state=review").json()["candidates"] == []


def test_deciding_the_card_on_the_bench_does_the_same_as_the_memory_routes(owner, core):
    _mined(owner, core, _fact("The owner likes oat biscuits.", 0.6),
           _fact("The owner drinks tea.", 0.6))
    first, second = owner.get(f"{API}/candidates?state=bench").json()["candidates"]
    approve = owner.post(f"/api/bench/{first['card_id']}/decide",
                         json={"action": "approve", "seen_updates": 0})
    assert approve.status_code == 200
    refuse = owner.post(f"/api/bench/{second['card_id']}/decide",
                        json={"action": "no", "seen_updates": 0})
    assert refuse.status_code == 422  # a No needs a reason, as on every card
    owner.post(f"/api/bench/{second['card_id']}/decide",
               json={"action": "no", "seen_updates": 0, "reason": "Coffee, in fact."})
    states = {c["statement"]: c for c in owner.get(f"{API}/candidates").json()["candidates"]}
    assert states[first["statement"]]["state"] == "saved"
    assert states[second["statement"]]["state"] == "declined"
    assert states[second["statement"]]["reason"] == "Coffee, in fact."
    facts = owner.get(f"{API}/facts").json()["facts"]
    assert [(f["text"], f["confirmed"]) for f in facts] == [(first["statement"], True)]
    assert first["statement"] in owner.get(f"{API}/notes/shared/preferences.md").json()["text"]


def test_the_activity_route_pages_and_refuses_odd_input(owner, core):
    owner.post(f"{API}/facts", json={"text": "Likes lemon cake", "kind": "preference"})
    _mined(owner, core, _fact("The owner likes oat biscuits.", 0.95))
    body = owner.get(f"{API}/activity").json()
    assert set(body) == {"activity", "next_before"}
    assert {a["type"] for a in body["activity"]} >= {"fact_saved", "mined"}
    assert set(body["activity"][0]) == {"id", "ts", "type", "actor", "note", "detail", "fact"}
    first = owner.get(f"{API}/activity?limit=1").json()
    assert len(first["activity"]) == 1 and first["next_before"]
    rest = owner.get(f"{API}/activity?before={first['next_before']}").json()
    assert first["activity"][0]["id"] not in [a["id"] for a in rest["activity"]]
    for bad in ("limit=0", "limit=999", "before=nonsense", f"before={'A' * 27}"):
        assert owner.get(f"{API}/activity?{bad}").status_code == 422, bad


def test_the_memory_settings_include_mining_and_a_switch_per_source(owner):
    described = {s["key"]: s for s in owner.get("/api/settings").json()["settings"]}
    for key in ("memory.mining", "memory.mine_interval_s", "memory.mine_role",
                "memory.mine_local_only", "memory.auto_save_above", "memory.ignore_below",
                "memory.mine_batch", "memory.mine_max_open_cards", "memory.source.chat",
                "memory.source.tasks", "memory.source.bench", "memory.history_keep_versions",
                "memory.history_keep_days"):
        assert described[key]["section"].startswith("memory_") and described[key]["help"], key
    assert described["memory.mine_local_only"]["default"] is True
    assert described["memory.mine_role"]["default"] == "cheap"
    # Each threshold takes any value in its own range; a pair that crosses is
    # explained on the Memory screen, not refused one setting at a time.
    assert owner.put("/api/settings/memory.ignore_below", json={"value": 0.9}).status_code == 200
    note = owner.get(f"{API}/sources").json()["mining"]["thresholds_note"]
    assert "set higher" in note
    assert owner.put("/api/settings/memory.ignore_below",
                     json={"value": 1.5}).status_code in (400, 422)


def test_the_owner_erases_a_proposed_fact_over_http_and_a_token_cannot(owner, core):
    _mined(owner, core, _fact("The owner likes rye biscuits.", 0.6))
    (cand,) = owner.get(f"{API}/candidates").json()["candidates"]
    for scopes in (("read",), ("read", "write")):
        assert token_client(owner, core, *scopes).delete(
            f"{API}/candidates/{cand['id']}").status_code == 403
    done = owner.delete(f"{API}/candidates/{cand['id']}")
    assert done.status_code == 200
    erased = done.json()["candidate"]
    assert (erased["statement"], erased["quotes"], erased["state"]) == ("", [], "withdrawn")
    assert owner.delete(f"{API}/candidates/{'0' * 26}").status_code == 404
