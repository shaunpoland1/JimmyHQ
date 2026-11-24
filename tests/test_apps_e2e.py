"""The app platform's first milestone, end to end: a described app and a sandboxed app
install and run.

Everything here goes through the real HTTP routes of a real web app over a real Core in
a temp folder. Nothing of the platform is faked: the install record, the bridge, the
app's database, the files and the quotas are the ones that ship. The only stand-ins are
outside it: a model provider that answers from a script over a made-up network, and a
local git repository standing in for an https address.
"""

from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from apps_install_helpers import (
    EXAMPLES,
    URL,
    commit_all,
    events,
    make_repo,
    staging,
    use_local,
)
from jimmyhq import tasks
from jimmyhq.apps import bridge, files, quotas, store
from jimmyhq.models import Target
from jimmyhq.web.app import create_app
from test_models_fakes import FakeProvider, add, wire

PASSWORD = "correct horse battery"
# What JimmyHQ's own screen sends with a relayed call: a fetch from its own page.
SHELL = {"origin": "http://testserver", "sec-fetch-site": "same-origin",
         "sec-fetch-dest": "empty", "sec-fetch-mode": "cors"}
# The design's policy for every app file, word for word (section 2, "Isolation").
DESIGN_CSP = (
    "sandbox allow-scripts allow-forms; default-src 'none'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; "
    "media-src 'self' blob:; connect-src 'none'; form-action 'none'; frame-src 'none'; "
    "worker-src 'none'; base-uri 'none'; webrtc 'block'")
EVERYTHING = ["tasks.read", "tasks.write", "bench.file", "model.ask", "memory.recall"]


@pytest.fixture
def app(core):
    return create_app(core)


@pytest.fixture
def owner(app):
    with TestClient(app, base_url="http://testserver") as client:
        assert client.post("/api/auth/setup",
                           json={"name": "Sam", "password": PASSWORD}).status_code == 200
        yield client


@pytest.fixture
def token(app, owner, core):
    """A token with every scope a token can have. It is still not the owner."""
    secret = core.auth.create_token("scout", ["read", "write"])[1]
    with TestClient(app, base_url="http://testserver",
                    headers={"authorization": f"Bearer {secret}"}) as client:
        yield client


@pytest.fixture
def nobody(app, owner):
    """A browser with no session at all, as an app's frame is."""
    with TestClient(app, base_url="http://testserver") as client:
        yield client


def install(owner, source: dict, **confirm) -> dict:
    """Install through the two steps the Apps screen takes."""
    plan = owner.post("/api/apps/_install/plan", json=source)
    assert plan.status_code == 200, plan.text
    body = plan.json()
    done = owner.post("/api/apps/_install/confirm", json={
        "plan": body["plan"], "fingerprint": body["fingerprint"], **confirm})
    assert done.status_code == 200, done.text
    return done.json()


def install_example(owner, name: str) -> dict:
    """An example installed from its own folder in the repository."""
    return install(owner, {"folder": str(EXAMPLES / name)})


def variant(tmp_path, app_id: str, **changes) -> str:
    """A copy of the sandboxed example under another id, with its manifest changed."""
    folder = tmp_path / "src" / app_id
    shutil.copytree(EXAMPLES / "water", folder)
    manifest = json.loads((folder / "jimmyhq.app.json").read_text(encoding="utf-8"))
    manifest |= {"id": app_id, "name": app_id.replace("_", " ").title(), **changes}
    (folder / "jimmyhq.app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return str(folder)


_next_id = iter(range(1, 10**9))


def relay(client, app_id: str, call: str, args: dict | None = None) -> dict:
    """One call relayed as the shell relays it. Returns the envelope the frame is given."""
    message_id = next(_next_id)
    answer = client.post(f"/api/apps/{app_id}/bridge/{call}", headers=SHELL,
                         json={"id": message_id, "call": call, "args": args or {}})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["id"] == message_id
    assert set(body) == {"id", "ok", "result" if body["ok"] else "error"}
    return body


def result(client, app_id: str, call: str, args: dict | None = None):
    body = relay(client, app_id, call, args)
    assert body["ok"] is True, body
    return body["result"]


def refusal(client, app_id: str, call: str, args: dict | None = None) -> dict:
    body = relay(client, app_id, call, args)
    assert body["ok"] is False, body
    assert set(body["error"]) <= {"code", "message", "retry_after_s"}
    return body["error"]


def tape(core) -> list[tuple[str, str]]:
    return [(e.kind, e.actor) for e in core.tape.after(0, limit=100000)]


# --- install --------------------------------------------------------------------------------


def test_a_described_app_and_a_sandboxed_app_install_from_their_folders(owner, token, core):
    books = install_example(owner, "books_read")
    water = install_example(owner, "water")
    assert (books["id"], books["tier"], books["described"]) == ("books_read", "sandboxed", True)
    assert (water["id"], water["tier"], water["described"]) == ("water", "sandboxed", False)
    assert water["permissions"] == ["tasks.write"] and books["permissions"] == []
    assert [(e.kind, e.payload["app_id"], e.actor) for e in events(core)] == [
        ("apps.installed", "books_read", "owner"), ("apps.installed", "water", "owner")]
    assert staging(core) == []
    # What anyone who can read is shown: the screens, and nothing of what was consented to.
    for reader in (token, owner):
        listed = {a["id"]: a for a in reader.get("/api/apps").json()["apps"]}
        assert listed["books_read"]["screens"] == [
            {"id": "main", "label": "Books read", "described": True}]
        assert listed["water"]["screens"] == [
            {"id": "main", "label": "Water", "described": False}]
        assert all(a["enabled"] and a["broken"] is None for a in listed.values())
        assert "permissions" not in listed["water"] and "tree" not in listed["water"]


def test_the_plan_shows_what_is_asked_and_installs_nothing_until_it_is_confirmed(owner, core):
    plan = owner.post("/api/apps/_install/plan", json={"folder": str(EXAMPLES / "water")}).json()
    assert plan["app"]["id"] == "water" and plan["needs_typed_name"] is False
    assert [line["sentence"] for line in plan["permissions"]] == [
        "Add tasks, and mark the ones it added as done.", "Keep its own data on this computer."]
    assert plan["adds"]["screens"] == ["Water"] and plan["adds"]["settings"] == ["Glasses a day"]
    assert store.get(core, "water") is None and events(core) == []
    assert owner.get("/api/apps").json() == {"apps": [], "refresh_s": 60}
    wrong = owner.post("/api/apps/_install/confirm",
                       json={"plan": plan["plan"], "fingerprint": "0" * 64})
    assert wrong.status_code == 409 and store.get(core, "water") is None


def test_a_token_without_owner_rights_cannot_install(owner, token, nobody, core):
    for source in ({"folder": str(EXAMPLES / "water")}, {"example": "water"},
                   {"url": URL, "commit": "0" * 40}):
        answer = token.post("/api/apps/_install/plan", json=source)
        assert answer.status_code == 403
        assert answer.json()["detail"] == "Only the signed-in owner can do this."
        assert nobody.post("/api/apps/_install/plan", json=source).status_code == 401
    # Nor finish an install the owner started.
    plan = owner.post("/api/apps/_install/plan", json={"folder": str(EXAMPLES / "water")}).json()
    confirm = {"plan": plan["plan"], "fingerprint": plan["fingerprint"]}
    assert token.post("/api/apps/_install/confirm", json=confirm).status_code == 403
    assert nobody.post("/api/apps/_install/confirm", json=confirm).status_code == 401
    assert store.get(core, "water") is None and events(core) == []
    assert token.get("/api/apps/_installed").status_code == 403
    assert token.get("/api/apps").json() == {"apps": [], "refresh_s": 60}


def test_an_app_installs_from_a_git_repository_at_a_pinned_commit(
        owner, core, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(EXAMPLES / "water", repo)
    first = make_repo(repo)
    # A later commit changes the app. It is not the one that was asked for.
    (repo / "app.js").write_text("// changed after the pin\n", encoding="utf-8")
    later = commit_all(repo, "later")
    assert later != first
    use_local(monkeypatch, repo)

    plan = owner.post("/api/apps/_install/plan", json={"url": URL, "commit": first}).json()
    assert plan["source"]["kind"] == "git" and plan["source"]["commit"] == first
    assert plan["source"]["repo"] == URL
    done = owner.post("/api/apps/_install/confirm",
                      json={"plan": plan["plan"], "fingerprint": plan["fingerprint"]}).json()
    assert (done["source"], done["repo"], done["commit"]) == ("git", URL, first)
    installed = events(core, "apps.installed")[0].payload
    assert installed["commit"] == first and installed["tree"] == plan["source"]["tree"]
    # The files served are the pinned commit's, not the branch's newest.
    mount = owner.post("/api/apps/water/mount", json={"screen": "main"}).json()
    served = owner.get(mount["base"] + "app.js").text
    assert "changed after the pin" not in served and "hq.storage.set" in served
    assert result(owner, "water", "storage.get", {"key": "k"}) is None
    # A branch or tag name over HTTP is resolved to a commit and that commit is pinned.
    owner.post("/api/apps/water/remove")
    by_name = owner.post("/api/apps/_install/plan", json={"url": URL, "ref": "main"}).json()
    assert by_name["source"]["commit"] == later and by_name["source"]["ref"] == "main"


# --- the frame's files ----------------------------------------------------------------------


def test_a_mounted_apps_page_and_bridge_script_carry_exactly_the_designed_headers(
        owner, nobody, core):
    install_example(owner, "water")
    mount = owner.post("/api/apps/water/mount", json={"screen": "main"}, headers=SHELL)
    assert mount.status_code == 200
    made = mount.json()
    assert made["entry"] == "index.html" and made["base"] == f"/appfiles/{made['mount']}/"
    assert made["permissions"] == ["tasks.write"]
    # The two things an app is told about the owner, and nothing else about them.
    assert (made["timezone"], made["week_start"]) == ("", "monday")
    assert set(made) == {"mount", "base", "entry", "app", "name", "version", "tier",
                         "permissions", "local_addresses", "owner_hosts", "development", "api",
                         "timezone", "week_start",
                         "upload_max_mb", "thumbnail_px", "picture_px", "picture_max_mb",
                         "pictures_per_minute", "dictate_max_s", "private",
                         "save_max_mb", "save_parts", "save_per_minute", "save_html"}

    expected = {
        "content-security-policy": DESIGN_CSP,
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
        "x-frame-options": "SAMEORIGIN",
        "access-control-allow-origin": "*",
        "cross-origin-resource-policy": "cross-origin",
        "cache-control": "private, max-age=3600, immutable",
    }
    kinds = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
             "_bridge.js": "text/javascript; charset=utf-8"}
    # A frame holds no session: the mount in the address is the whole key.
    for name, kind in kinds.items():
        answer = nobody.get(made["base"] + name, headers={"sec-fetch-dest": "iframe"})
        assert answer.status_code == 200, name
        got = {k: v for k, v in answer.headers.items() if k in expected}
        assert got == expected, name
        assert answer.headers["content-type"] == kind
        assert "set-cookie" not in answer.headers
    page = nobody.get(made["base"] + "index.html").text
    assert '<script type="module" src="app.js">' in page
    # The bridge script is JimmyHQ's own, served beside the app's files.
    served = nobody.get(made["base"] + "_bridge.js")
    # Byte for byte: the file is served as it is on disk, whatever its line endings.
    assert served.content == store.bridge_script().read_bytes()
    script = served.text
    assert "export const hq" in script and "jimmyhq: 1, hello: true" in script
    # The manifest is the app's file too; nothing outside the app is.
    assert nobody.get(made["base"] + "jimmyhq.app.json").status_code == 200
    for path in ("%2e%2e/%2e%2e/data/app.db", "..%2f..%2ftape.db", "nope.html", "index.htm",
                 "versions/index.html", ""):
        missing = nobody.get(made["base"] + path)
        assert missing.status_code == 404
        assert missing.headers["content-security-policy"] == DESIGN_CSP


def test_a_frame_is_shown_an_apps_files_and_nothing_else_of_jimmyhq(owner, core):
    install_example(owner, "water")
    made = owner.post("/api/apps/water/mount").json()
    framed = {"sec-fetch-dest": "iframe"}
    assert owner.get(made["base"] + "index.html", headers=framed).status_code == 200
    for path in ("/", "/api/apps", "/api/apps/_installed", "/api/settings", "/api/tape"):
        assert owner.get(path, headers=framed).status_code == 403, path
    # The shell's own pages may frame only this server.
    assert owner.get("/api/apps").headers["content-security-policy"] == "frame-src 'self'"
    # A request an app's own page could make (its origin is opaque) relays nothing.
    for headers in ({"origin": "null"}, {"sec-fetch-site": "cross-site"},
                    {"sec-fetch-dest": "iframe"}):
        answer = owner.post("/api/apps/water/bridge/storage.set", headers=headers,
                            json={"id": 1, "call": "storage.set",
                                  "args": {"key": "k", "value": "v"}})
        assert answer.status_code == 403
    assert result(owner, "water", "storage.get", {"key": "k"}) is None


def test_a_described_screen_has_no_file_to_mount_and_the_shell_is_told_so(owner):
    install_example(owner, "books_read")
    made = owner.post("/api/apps/books_read/mount", json={"screen": "main"}).json()
    assert made["entry"] is None
    assert owner.post("/api/apps/books_read/mount", json={"screen": "nope"}).status_code == 404


# --- the bridge -----------------------------------------------------------------------------


@pytest.fixture
def model(core):
    """The owner's default role answered by a scripted provider on a made-up network."""
    provider = FakeProvider("openai_compatible", text="Eight glasses is a fine goal.")
    brain = wire(core, {"127.0.0.1": provider})
    add(brain, "home", host="127.0.0.1:11434")
    brain.set_role("default", Target("home", "model-a"), [], actor="owner")
    return provider


def test_a_sandboxed_app_does_what_it_was_granted_as_itself(owner, core, tmp_path, model):
    install(owner, {"folder": variant(tmp_path, "water_plus", permissions=EVERYTHING)})
    app, actor = "water_plus", "app:water_plus"

    # Its own storage.
    assert result(owner, app, "storage.set", {"key": "2026-11-19", "value": {"n": 3}}) is None
    assert result(owner, app, "storage.get", {"key": "2026-11-19"}) == {"n": 3}
    assert result(owner, app, "storage.keys", {"prefix": "2026"}) == ["2026-11-19"]
    assert (core.config.data / "apps" / app / "data" / "app.db").is_file()

    # A task, filed as the app and for the owner.
    task = result(owner, app, "tasks.create", {"title": "Keep drinking water"})
    stored = owner.get(f"/api/tasks/{task['id']}").json()
    assert (stored["title"], stored["filed_by"], stored["assignee"]) == (
        "Keep drinking water", actor, "owner")
    assert task["id"] in [t["id"] for t in result(owner, app, "tasks.list")["tasks"]]

    # A Bench card whose filer is the app, under the app's own kind and name.
    card_id = result(owner, app, "bench.file", {
        "title": "Raise the goal?", "body": "You have met it seven days running.",
        "subject": "goal"})["card_id"]
    card = owner.get(f"/api/bench/{card_id}").json()
    card = card.get("card", card)
    assert card["filed_by"] == actor
    assert (card["kind"], card["source"], card["subject"]) == (
        "app.water_plus.card", "Water Plus", "app.water_plus:goal")

    # A setting it declared, and only those.
    assert result(owner, app, "settings.get", {"key": "goal"}) == 8
    assert result(owner, app, "settings.get") == {"goal": 8}
    assert refusal(owner, app, "settings.get", {"key": "owner.name"})["code"] == "not_found"

    # A model call through the owner's roles, as the app.
    reply = result(owner, app, "model.ask", {"prompt": "Is eight glasses a good goal?",
                                             "system": "Answer in one sentence."})
    assert reply == {"text": "Eight glasses is a fine goal.", "data": None, "local": True,
                     "model": "model-a"}
    asked = model.bodies[-1]
    assert asked["model"] == "model-a"
    assert asked["messages"][-1] == {"role": "user", "content": "Is eight glasses a good goal?"}
    called = [e for e in core.tape.after(0, limit=1000) if e.kind == "model.called"]
    assert len(called) == 1 and called[0].actor == actor
    assert called[0].payload["caller"] == "apps.water_plus"

    # Everything it did is on the record under its own name, and nothing as the owner.
    did = [(kind, who) for kind, who in tape(core) if kind in (
        "task.filed", "bench.filed", "model.called")]
    assert did == [("task.filed", actor), ("bench.filed", actor), ("model.called", actor)]


def test_each_call_is_refused_without_its_permission_and_nothing_happens(
        owner, core, tmp_path, model):
    install_example(owner, "water")       # granted tasks.write only
    install_example(owner, "books_read")  # granted nothing
    install(owner, {"folder": variant(tmp_path, "water_net",
                                      permissions=["net:api.example.com"])})
    before = tape(core)
    needs = [
        ("water", "tasks.list", {}, "tasks.read"),
        ("water", "bench.file", {"title": "Look"}, "bench.file"),
        ("water", "model.ask", {"prompt": "Hello"}, "model.ask"),
        ("water", "memory.recall", {"query": "tea"}, "memory.recall"),
        ("books_read", "tasks.create", {"title": "Read"}, "tasks.write"),
        ("books_read", "tasks.complete", {"id": "x"}, "tasks.write"),
    ]
    for app_id, call, args, permission in needs:
        error = refusal(owner, app_id, call, args)
        assert error["code"] == "not_allowed", call
        assert f'"{permission}"' in error["message"] and "jimmyhq.app.json" in error["message"]
    # A host is a permission of its own: one that was not granted is never asked.
    for app_id, url in (("water", "https://api.example.com/v1"),
                        ("water_net", "https://other.example.com/v1"),
                        ("water_net", "https://api.example.com.evil.example/v1")):
        error = refusal(owner, app_id, "net.fetch", {"url": url})
        assert error["code"] == "not_allowed" and "Nothing" not in error["message"][:0]
    assert refusal(owner, "water", "app.call", {"name": "x"})["code"] == "not_allowed"
    assert refusal(owner, "water", "ui.toast", {"message": "hi"})["code"] == "invalid"
    assert refusal(owner, "water", "tape.read", {})["code"] == "invalid"
    assert tape(core) == before and model.bodies == []
    assert tasks.service(core).list_open() == []
    # With the permission it has, the same app is served.
    assert result(owner, "water", "tasks.create", {"title": "Drink"})["status"] == "open"


def test_an_app_marks_done_only_the_tasks_it_filed(owner, core, tmp_path):
    install(owner, {"folder": variant(tmp_path, "water_plus", permissions=EVERYTHING)})
    install_example(owner, "water")
    owners = owner.post("/api/tasks", json={"title": "Call the bank"}).json()
    others = result(owner, "water", "tasks.create", {"title": "Drink"})
    mine = result(owner, "water_plus", "tasks.create", {"title": "Refill the jug"})
    for task in (owners, others):
        error = refusal(owner, "water_plus", "tasks.complete", {"id": task["id"]})
        assert error["code"] == "not_allowed" and "added itself" in error["message"]
        assert owner.get(f"/api/tasks/{task['id']}").json()["status"] == "open"
    assert result(owner, "water_plus", "tasks.complete", {"id": mine["id"]})["status"] == "done"


def test_one_app_never_reaches_anothers_data(owner, tmp_path):
    install_example(owner, "water")
    install(owner, {"folder": variant(tmp_path, "other_water")})
    result(owner, "water", "storage.set", {"key": "k", "value": "water's"})
    assert result(owner, "other_water", "storage.get", {"key": "k"}) is None
    assert result(owner, "other_water", "storage.keys") == []
    for args in ({"key": "k", "app": "water"}, {"key": "../water/data/k"}):
        body = relay(owner, "other_water", "storage.get", args)
        assert body["ok"] is False or body["result"] is None
    # The app is the one in the address, whatever the body says.
    spoofed = owner.post("/api/apps/other_water/bridge/storage.get", headers=SHELL,
                         json={"id": 1, "call": "storage.get", "args": {"key": "k"},
                               "app": "water"}).json()
    assert spoofed["ok"] is False and spoofed["error"]["code"] == "invalid"


# --- a described app ------------------------------------------------------------------------


def described(owner, call: str, args: dict):
    """A records call as JimmyHQ's own screen for a described app sends it: the
    arguments alone, to the same bridge route."""
    answer = owner.post(f"/api/apps/books_read/bridge/{call}", headers=SHELL, json={"args": args})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_a_described_apps_records_work_end_to_end_through_the_bridge(owner, core):
    install_example(owner, "books_read")
    description = owner.get("/api/apps/books_read/described").json()
    assert set(description["tables"]) == {"author", "book"}
    # This test asks faster than a person's screen would; the owner may allow that.
    owner.put("/api/settings/apps.quota_overrides",
              json={"value": ["books_read.calls_per_second=1000"]})

    def ok(call, args):
        body = described(owner, call, args)
        assert body["ok"] is True, body
        return body["result"]

    def no(call, args):
        body = described(owner, call, args)
        assert body["ok"] is False, body
        return body["error"]

    author = ok("records.create", {"table": "author",
                                   "values": {"name": "Avery Example", "born": 1929}})
    assert author["record"]["label"] == "Avery Example" and author["record"]["id"] == 1
    dune = ok("records.create", {"table": "book", "values": {
        "title": "Dune", "author": author["record"]["id"], "status": "finished",
        "finished": "2026-01-31", "stars": 5, "pages": 400, "paid": 1200}})
    assert set(dune["record"]) == {"id", "label", "values", "created", "updated"}
    assert dune["record"]["values"]["per_page"] == 3  # computed, never stored
    assert dune["labels"] == {"author": {"1": "Avery Example"}}
    ok("records.create", {"table": "book", "values": {
        "title": "Emma", "status": "reading", "stars": 3, "pages": 250, "paid": 500}})
    ok("records.create", {"table": "book", "values": {"title": "Ulysses", "status": "to read"}})

    # A view: its sort, its totals, and the caller's filter on top.
    listed = ok("records.list", {"table": "book", "view": "all"})
    assert [r["label"] for r in listed["records"]] == ["Dune", "Emma", "Ulysses"]
    assert listed["total"] == 3 and listed["next"] is None
    assert listed["totals"] == {"stars": 4, "pages": 650, "paid": 1700}
    grouped = ok("records.list", {"table": "book", "view": "shelf"})
    assert [(g["value"], g["count"]) for g in grouped["groups"]] == [
        ("to read", 1), ("reading", 1), ("finished", 1)]
    favourites = ok("records.list", {"table": "book", "view": "favourites"})
    assert [r["label"] for r in favourites["records"]] == ["Dune"]
    found = ok("records.list", {"table": "book", "search": "emm"})
    assert [r["label"] for r in found["records"]] == ["Emma"]
    filtered = ok("records.list", {"table": "book", "sort": ["title"], "filter": [
        {"field": "pages", "op": "ge", "value": 300}]})
    assert [r["label"] for r in filtered["records"]] == ["Dune"]

    # A page at a time.
    first = ok("records.list", {"table": "book", "sort": ["title"], "limit": 2})
    assert [r["label"] for r in first["records"]] == ["Dune", "Emma"] and first["next"]
    second = ok("records.list", {"table": "book", "sort": ["title"], "limit": 2,
                                 "after": first["next"]})
    assert [r["label"] for r in second["records"]] == ["Ulysses"] and second["next"] is None

    # Changing and reading one.
    changed = ok("records.update", {"table": "book", "id": dune["record"]["id"],
                                    "values": {"stars": 4, "pages": 600}})
    assert changed["record"]["values"]["stars"] == 4
    assert changed["record"]["values"]["per_page"] == 2
    assert ok("records.get", {"table": "book", "id": dune["record"]["id"]}) == changed

    # Every refusal is a sentence that names the field and says nothing was saved.
    error = no("records.create", {"table": "book", "values": {"title": "X", "stars": 11}})
    assert error["code"] == "invalid" and "Stars" in error["message"]
    assert "Nothing was saved" in error["message"]
    assert no("records.create", {"table": "book", "values": {"stars": 3}})["code"] == "invalid"
    assert no("records.create", {"table": "book", "values": {
        "title": "X", "author": 999}})["code"] == "invalid"
    assert no("records.create", {"table": "book", "values": {
        "title": "X", "per_page": 1}})["code"] == "invalid"
    assert no("records.list", {"table": "kv"})["code"] == "not_found"
    assert no("records.get", {"table": "book", "id": "1 OR 1=1"})["code"] == "not_found"
    assert no("records.list", {"table": "book", "where": "1=1"})["code"] == "invalid"
    assert ok("records.list", {"table": "book"})["total"] == 3

    # A record others link to is not deleted by accident.
    author_id = author["record"]["id"]
    error = no("records.delete", {"table": "author", "id": author_id})
    assert error["code"] == "invalid" and "Nothing was deleted" in error["message"]
    assert ok("records.delete", {"table": "author", "id": author_id, "unlink": True}) == {
        "deleted": author_id, "unlinked": 1}
    assert ok("records.get", {"table": "book", "id": dune["record"]["id"]})[
        "record"]["values"]["author"] is None
    assert ok("records.delete", {"table": "book", "id": dune["record"]["id"]})["deleted"] == 2
    assert ok("records.list", {"table": "book"})["total"] == 2

    # None of it is on the Tape: a described app's records are its own.
    assert not [kind for kind, _ in tape(core) if kind.startswith(("app.", "record"))]
    assert {who for _, who in tape(core)} <= {"owner", "system"}
    assert quotas.usage(core, "books_read")


def test_a_described_app_has_no_code_so_it_can_do_nothing_but_keep_records(owner):
    install_example(owner, "books_read")
    for call, args in (("tasks.create", {"title": "x"}), ("bench.file", {"title": "x"}),
                       ("model.ask", {"prompt": "x"}), ("net.fetch", {"url": "https://a.example"}),
                       ("memory.recall", {"query": "x"})):
        body = described(owner, call, args)
        assert body["ok"] is False and body["error"]["code"] == "not_allowed"


# --- limits ---------------------------------------------------------------------------------


def test_an_app_that_hits_a_quota_is_told_the_number_and_when_to_try_again(owner, core):
    install_example(owner, "water")
    install_example(owner, "books_read")
    put = owner.put("/api/settings/apps.quota_overrides", json={"value": [
        "water.tasks_per_day=2", "water.storage_mb=0", "books_read.records=1"]})
    assert put.status_code == 200, put.text

    result(owner, "water", "tasks.create", {"title": "one"})
    result(owner, "water", "tasks.create", {"title": "two"})
    error = refusal(owner, "water", "tasks.create", {"title": "three"})
    assert error["code"] == "quota" and "all 2 new tasks" in error["message"]
    assert 0 < error["retry_after_s"] <= 86_400
    assert len(tasks.service(core).list_open()) == 2

    error = refusal(owner, "water", "storage.set", {"key": "k", "value": "v"})
    assert error["code"] == "quota" and "Nothing was saved" in error["message"]
    assert result(owner, "water", "storage.get", {"key": "k"}) is None

    described(owner, "records.create", {"table": "author", "values": {"name": "A"}})
    body = described(owner, "records.create", {"table": "author", "values": {"name": "B"}})
    assert body["error"]["code"] == "quota" and "at most 1 records" in body["error"]["message"]

    # The rate of calls itself is a limit, counted on the server.
    owner.put("/api/settings/apps.quota_overrides", json={"value": ["water.calls_per_day=3"]})
    seen = [relay(owner, "water", "storage.get", {"key": "k"}) for _ in range(8)]
    refused = [body["error"] for body in seen if not body["ok"]]
    assert refused and all(e["code"] == "quota" and e["retry_after_s"] > 0 for e in refused)
    message = refused[0]["message"]
    assert "requests to JimmyHQ" in message and "Requests to JimmyHQ a day" in message


# --- switching off, removing, coming back ---------------------------------------------------


def test_a_disabled_app_is_refused_every_call_and_served_nothing(
        owner, nobody, token, core, tmp_path, model):
    install(owner, {"folder": variant(tmp_path, "water_plus", permissions=EVERYTHING)})
    app = "water_plus"
    result(owner, app, "storage.set", {"key": "k", "value": "kept"})
    mount = owner.post(f"/api/apps/{app}/mount").json()
    assert nobody.get(mount["base"] + "index.html").status_code == 200

    off = owner.post(f"/api/apps/{app}/disable")
    assert off.status_code == 200 and off.json()["enabled"] is False
    before = tape(core)
    calls = [("storage.get", {"key": "k"}), ("storage.set", {"key": "k", "value": "x"}),
             ("storage.delete", {"key": "k"}), ("storage.keys", {}),
             ("records.list", {"table": "book"}), ("records.get", {"table": "book", "id": 1}),
             ("records.create", {"table": "book", "values": {}}),
             ("records.update", {"table": "book", "id": 1, "values": {}}),
             ("records.delete", {"table": "book", "id": 1}), ("tasks.list", {}),
             ("tasks.create", {"title": "x"}), ("tasks.complete", {"id": "x"}),
             ("bench.file", {"title": "x"}), ("settings.get", {"key": "goal"}),
             ("model.ask", {"prompt": "x"}), ("memory.recall", {"query": "x"}),
             ("net.fetch", {"url": "https://api.example.com"}), ("app.call", {"name": "x"}),
             ("model.embed", {"texts": ["x"]}), ("web.search", {"query": "x"}),
             ("web.read", {"url": "https://example.com/"}), ("hosts.list", {}),
                 ("ui.limits", {})]
    assert {name for name, _ in calls} == set(bridge.CALLS)
    for name, args in calls:
        error = refusal(owner, app, name, args)
        assert error["code"] == "not_allowed" and "switched off" in error["message"], name
    assert tape(core) == before and model.bodies == []
    # Frames already open are served nothing more, and no new one can be opened.
    assert nobody.get(mount["base"] + "index.html").status_code == 404
    assert nobody.get(mount["base"] + "_bridge.js").status_code == 404
    assert owner.post(f"/api/apps/{app}/mount").status_code == 409
    listed = token.get("/api/apps").json()["apps"]
    assert [(a["id"], a["enabled"]) for a in listed] == [(app, False)]

    on = owner.post(f"/api/apps/{app}/enable")
    assert on.json()["enabled"] is True
    assert result(owner, app, "storage.get", {"key": "k"}) == "kept"
    # The old mount stays dead; a new one works.
    assert nobody.get(mount["base"] + "index.html").status_code == 404
    again = owner.post(f"/api/apps/{app}/mount").json()
    assert nobody.get(again["base"] + "index.html").status_code == 200


def test_removing_keeps_the_data_for_a_reinstall_unless_the_owner_deletes_it(
        owner, nobody, core):
    install_example(owner, "water")
    install_example(owner, "books_read")
    result(owner, "water", "storage.set", {"key": "2026-11-19", "value": 5})
    described(owner, "records.create", {"table": "author", "values": {"name": "Avery Example"}})
    mount = owner.post("/api/apps/water/mount").json()
    data = {name: core.config.data / "apps" / name / "data" / "app.db"
            for name in ("water", "books_read")}
    assert all(path.is_file() for path in data.values())

    # Keeping the data (the default).
    for name in data:
        gone = owner.post(f"/api/apps/{name}/remove", json={"keep_data": True})
        assert gone.status_code == 200 and gone.json()["data"] == "kept"
    assert owner.get("/api/apps").json() == {"apps": [], "refresh_s": 60}
    assert all(path.is_file() for path in data.values())
    assert not (core.config.data / "apps" / "water" / "versions").exists()
    assert nobody.get(mount["base"] + "index.html").status_code == 404
    error = refusal(owner, "water", "storage.get", {"key": "2026-11-19"})
    assert error["code"] == "not_found" and "No app called" in error["message"]
    assert owner.post("/api/apps/water/mount").status_code == 404
    assert owner.get("/api/apps/water").status_code == 404

    install_example(owner, "water")
    install_example(owner, "books_read")
    assert result(owner, "water", "storage.get", {"key": "2026-11-19"}) == 5
    back = described(owner, "records.list", {"table": "author"})["result"]
    assert [r["label"] for r in back["records"]] == ["Avery Example"]

    # Deleting it for good.
    for name in data:
        gone = owner.post(f"/api/apps/{name}/remove", json={"keep_data": False})
        assert gone.status_code == 200 and gone.json()["data"] == "deleted"
    assert not any(path.exists() for path in data.values())
    assert not (core.config.data / "apps" / "water").exists()

    install_example(owner, "water")
    install_example(owner, "books_read")
    assert result(owner, "water", "storage.get", {"key": "2026-11-19"}) is None
    assert result(owner, "water", "storage.keys") == []
    assert described(owner, "records.list", {"table": "author"})["result"]["total"] == 0
    kinds = [(e.kind, e.payload.get("data")) for e in events(core) if e.kind == "apps.removed"]
    assert kinds == [("apps.removed", "kept")] * 2 + [("apps.removed", "deleted")] * 2


def test_the_bundled_examples_install_by_name_and_run(owner, nobody):
    for name in ("books_read", "water", "chores"):
        plan = owner.post("/api/apps/_install/plan", json={"example": name}).json()
        done = owner.post("/api/apps/_install/confirm",
                          json={"plan": plan["plan"], "fingerprint": plan["fingerprint"]})
        assert done.status_code == 200, done.text
    made = owner.post("/api/apps/water/mount", json={"screen": "main"}).json()
    assert nobody.get(made["base"] + made["entry"]).status_code == 200
    chores = owner.get("/api/apps/chores/described").json()
    table = next(iter(chores["tables"]))
    assert described(owner, "records.list", {"table": "author"})["ok"] is True
    answer = owner.post("/api/apps/chores/bridge/records.list", headers=SHELL,
                        json={"args": {"table": table}}).json()
    assert answer["ok"] is True and answer["result"]["total"] == 0
    assert files.resolve_mount(owner.app.state.core, made["mount"]) is not None
