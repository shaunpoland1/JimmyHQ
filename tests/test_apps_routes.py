"""Apps over HTTP: who may do what, and the two-step install."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps_install_helpers import URL, events, example, make_repo, staging, use_local
from jimmyhq.apps import manifest as mf
from jimmyhq.apps import routes, store
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"
OWNER_ONLY = [
    ("GET", "/api/apps/_installed", None),
    ("GET", "/api/apps/water", None),
    ("POST", "/api/apps/_install/plan", {"folder": "x"}),
    ("POST", "/api/apps/_install/confirm", {"plan": "0" * 26, "fingerprint": "0" * 64}),
    ("POST", "/api/apps/_install/cancel", {"plan": "0" * 26}),
    ("POST", "/api/apps/water/enable", None),
    ("POST", "/api/apps/water/disable", None),
    ("POST", "/api/apps/water/remove", {"keep_data": False}),
    ("GET", "/api/apps/_examples", None),
    ("POST", "/api/apps/_install/plan", {"example": "water"}),
    ("GET", "/api/apps/water/described", None),
    ("POST", "/api/apps/water/mount", None),
    ("POST", "/api/apps/water/bridge/storage.set", {"args": {"key": "k", "value": 1}}),
]


@pytest.fixture
def app(core):
    return create_app(core)


@pytest.fixture
def owner(app):
    with TestClient(app) as client:
        made = client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD})
        assert made.status_code == 200
        yield client


@pytest.fixture
def token(app, owner, core):
    secret = core.auth.create_token("scout", ["read", "write"])[1]
    with TestClient(app, headers={"authorization": f"Bearer {secret}"}) as client:
        yield client


@pytest.fixture
def stranger(app, owner):
    with TestClient(app) as client:
        yield client


def put_in(owner, folder, **confirm):
    plan = owner.post("/api/apps/_install/plan", json={"folder": str(folder)})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    done = owner.post("/api/apps/_install/confirm", json={
        "plan": body["plan"], "fingerprint": body["fingerprint"], **confirm})
    assert done.status_code == 200, done.text
    return done.json()


# --- who may do what ------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), OWNER_ONLY)
def test_a_token_with_every_scope_cannot_install_remove_switch_or_read_consents(
        owner, token, core, tmp_path, method, path, body):
    put_in(owner, example(tmp_path, "water"))
    before = len(events(core))
    answer = token.request(method, path, json=body)
    assert answer.status_code == 403
    assert answer.json()["detail"] == "Only the signed-in owner can do this."
    assert len(events(core)) == before and store.is_enabled(core, "water")


@pytest.mark.parametrize(("method", "path", "body"),
                         [("GET", "/api/apps", None), *OWNER_ONLY])
def test_nobody_signed_out_reaches_any_apps_route(stranger, method, path, body):
    assert stranger.request(method, path, json=body).status_code == 401


def test_a_reader_gets_only_what_the_shell_needs(owner, token, tmp_path):
    put_in(owner, example(tmp_path, "water"))
    put_in(owner, example(tmp_path, "books_read"))
    owner.post("/api/apps/water/disable")
    answer = token.get("/api/apps")
    assert answer.status_code == 200
    assert answer.json() == {"apps": [
        {"id": "books_read", "name": "Books read", "version": "1.0.0", "tier": "sandboxed",
         "enabled": True, "icon": "", "dock": [], "wall": [], "broken": None, "count": None,
         "screens": [{"id": "main", "label": "Books read", "described": True}]},
        {"id": "water", "name": "Water", "version": "1.0.0", "tier": "sandboxed",
         "enabled": False, "icon": "", "dock": [], "wall": [], "broken": None, "count": None,
         "screens": [{"id": "main", "label": "Water", "described": False}]},
    ], "refresh_s": 60}
    assert owner.get("/api/apps").json() == answer.json()


# --- the two-step install -------------------------------------------------------------------


def test_the_owner_installs_in_two_steps_and_nothing_is_installed_by_the_first(
        owner, core, tmp_path):
    plan = owner.post("/api/apps/_install/plan", json={"folder": str(example(tmp_path, "water"))})
    assert plan.status_code == 200
    body = plan.json()
    assert body["app"]["name"] == "Water" and body["reviewed"] is False
    assert [line["sentence"] for line in body["permissions"]] == [
        "Add tasks, and mark the ones it added as done.", "Keep its own data on this computer."]
    assert owner.get("/api/apps/_installed").json() == {"apps": []} and events(core) == []
    done = owner.post("/api/apps/_install/confirm", json={
        "plan": body["plan"], "fingerprint": body["fingerprint"]})
    assert done.status_code == 200 and done.json()["enabled"] is True
    [event] = events(core, "apps.installed")
    assert event.actor == "owner"
    [row] = owner.get("/api/apps/_installed").json()["apps"]
    assert row["id"] == "water" and row["permissions"] == ["tasks.write"]
    assert row["summary"] == "Count glasses of water."


def test_the_owner_installs_from_a_git_address_over_http(owner, core, tmp_path, monkeypatch):
    repo = example(tmp_path, "water")
    commit = make_repo(repo)
    use_local(monkeypatch, repo)
    body = owner.post("/api/apps/_install/plan", json={"url": URL, "commit": commit}).json()
    assert body["source"]["commit"] == commit and body["source"]["repo"] == URL
    done = owner.post("/api/apps/_install/confirm", json={
        "plan": body["plan"], "fingerprint": body["fingerprint"]})
    assert done.status_code == 200 and done.json()["commit"] == commit


def test_a_confirm_without_the_plans_fingerprint_installs_nothing(owner, core, tmp_path):
    water = owner.post("/api/apps/_install/plan",
                       json={"folder": str(example(tmp_path, "water"))}).json()
    books = owner.post("/api/apps/_install/plan",
                       json={"folder": str(example(tmp_path, "books_read"))}).json()
    assert owner.post("/api/apps/_install/confirm", json={"plan": water["plan"]}).status_code == 422
    for mark in (books["fingerprint"], "0" * 64, ""):
        answer = owner.post("/api/apps/_install/confirm",
                            json={"plan": water["plan"], "fingerprint": mark})
        assert answer.status_code == 409
        assert "Nothing was installed" in answer.json()["detail"]
    answer = owner.post("/api/apps/_install/confirm",
                        json={"plan": "../" + water["plan"], "fingerprint": water["fingerprint"]})
    assert answer.status_code == 404
    assert events(core) == [] and store.installed(core) == {}


def test_a_full_app_over_http_needs_its_name_typed(owner, core, tmp_path):
    body = owner.post("/api/apps/_install/plan",
                      json={"folder": str(example(tmp_path, "drafts"))}).json()
    assert body["needs_typed_name"] is True and "Runs as you" in body["warning"]
    send = {"plan": body["plan"], "fingerprint": body["fingerprint"]}
    assert owner.post("/api/apps/_install/confirm", json=send).status_code == 422
    assert owner.post("/api/apps/_install/confirm",
                      json=send | {"typed_name": "Drafts"}).status_code == 200
    detail = owner.get("/api/apps/drafts").json()
    assert detail["lines"][0]["strong"] is True and detail["tier"] == "full"


@pytest.mark.parametrize("body", [
    {}, {"folder": "x", "url": URL}, {"folder": ""}, {"url": URL, "commit": "main"},
    {"url": "ext::sh -c id", "commit": "0" * 40}, {"url": "--upload-pack=x", "ref": "main"},
    {"url": "http://git.example.com/a.git", "commit": "0" * 40}, {"folder": "x", "shell": 1},
    {"url": URL},
])
def test_a_plan_request_that_is_not_one_clear_source_is_refused(owner, core, body):
    answer = owner.post("/api/apps/_install/plan", json=body)
    assert answer.status_code == 422 and staging(core) == []


def test_a_refusal_carries_its_sentence(owner, tmp_path):
    answer = owner.post("/api/apps/_install/plan", json={"folder": str(tmp_path / "nowhere")})
    assert answer.status_code == 422 and "nothing was installed" in answer.json()["detail"]


def test_cancelling_over_http_throws_the_plan_away(owner, core, tmp_path):
    body = owner.post("/api/apps/_install/plan",
                      json={"folder": str(example(tmp_path, "water"))}).json()
    assert owner.post("/api/apps/_install/cancel",
                      json={"plan": body["plan"]}).json() == {"cancelled": True}
    assert staging(core) == []
    answer = owner.post("/api/apps/_install/confirm", json={
        "plan": body["plan"], "fingerprint": body["fingerprint"]})
    assert answer.status_code == 404


# --- after install --------------------------------------------------------------------------


def test_the_detail_shows_the_consented_permissions_and_where_it_came_from(owner, tmp_path):
    put_in(owner, example(tmp_path, "water"))
    detail = owner.get("/api/apps/water").json()
    assert detail["source"] == "folder" and len(detail["tree"]) == 64
    assert [line["id"] for line in detail["lines"]] == ["tasks.write", "always.data"]
    assert detail["consents"][0]["by"] == "owner" and detail["consented_at"] > 0
    assert detail["adds"]["screens"] == ["Water"]


def test_the_owner_switches_an_app_off_and_on(owner, core, tmp_path):
    put_in(owner, example(tmp_path, "water"))
    assert owner.post("/api/apps/water/disable").json()["enabled"] is False
    assert not store.is_enabled(core, "water")
    assert owner.post("/api/apps/water/enable").json()["enabled"] is True
    assert [e.kind for e in events(core)][1:] == ["apps.disabled", "apps.enabled"]


def test_removing_over_http_keeps_data_unless_told_otherwise(owner, core, tmp_path):
    for body, kept in ((None, "kept"), ({}, "kept"), ({"keep_data": False}, "deleted")):
        put_in(owner, example(tmp_path, "water", f"w_{kept}_{body is None}"))
        data = store.data_dir(core, "water")
        data.mkdir(parents=True, exist_ok=True)
        (data / "app.db").write_bytes(b"x")
        answer = owner.post("/api/apps/water/remove", json=body)
        assert answer.status_code == 200 and answer.json()["data"] == kept
        assert (data / "app.db").exists() is (kept == "kept")
    assert owner.get("/api/apps").json() == {"apps": [], "refresh_s": 60}


@pytest.mark.parametrize("app_id", ["ghost", "install", "installed", "_staging", "..%2Fx"])
def test_an_unknown_app_is_a_404_with_a_sentence(owner, app_id):
    for method, path in (("GET", f"/api/apps/{app_id}"),
                         ("POST", f"/api/apps/{app_id}/enable"),
                         ("POST", f"/api/apps/{app_id}/disable"),
                         ("POST", f"/api/apps/{app_id}/remove")):
        answer = owner.request(method, path)
        assert answer.status_code == 404


# --- one table ------------------------------------------------------------------------------


def test_no_app_can_be_named_like_a_word_the_routes_use(app):
    """Every fixed first step under /api/apps is a word the id rule refuses, so an app
    called "install", "installed" or anything else can never take a route's place."""
    def paths(routes_):
        for route in routes_:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from paths(inner.routes)
            elif isinstance(getattr(route, "path", None), str):
                yield route.path

    firsts = {path.removeprefix("/api/apps/").split("/")[0]
              for path in paths(app.routes) if path.startswith("/api/apps/")}
    fixed = {step for step in firsts if not step.startswith("{")}
    assert fixed == set(routes.COLLECTION_WORDS) and "{app_id}" in firsts
    for word in fixed:
        assert not mf._ID.match(word)  # noqa: SLF001
        with pytest.raises(mf.ManifestError):
            mf.parse({"manifest": 1, "id": word, "name": "X", "summary": "x", "licence": "MIT",
                      "version": "1.0.0", "tier": "sandboxed", "api": "1.0", "permissions": [],
                      "screens": [{"id": "main", "label": "X", "entry": "described"}]})


@pytest.mark.parametrize("app_id", ["install", "installed", "plan", "confirm", "remove"])
def test_an_app_whose_id_is_an_ordinary_word_installs_and_is_reached(owner, tmp_path, app_id):
    folder = example(tmp_path, "water", app_id)
    data = mf.parse((folder / "jimmyhq.app.json").read_bytes()).to_dict() | {"id": app_id}
    (folder / "jimmyhq.app.json").write_text(__import__("json").dumps(data), encoding="utf-8")
    assert put_in(owner, folder)["id"] == app_id
    assert owner.get(f"/api/apps/{app_id}").json()["id"] == app_id
    assert owner.post(f"/api/apps/{app_id}/mount").json()["app"] == app_id
    assert [a["id"] for a in owner.get("/api/apps/_installed").json()["apps"]] == [app_id]
    assert owner.post(f"/api/apps/{app_id}/remove").json()["id"] == app_id


def test_the_examples_that_ship_are_listed_and_install_by_name(owner, core):
    listed = owner.get("/api/apps/_examples").json()["examples"]
    assert {e["id"] for e in listed} >= {"books_read", "water", "chores", "drafts"}
    assert all(set(e) == {"id", "name", "summary", "tier", "described", "group",
                          "installed"} for e in listed)
    groups = {e["id"]: e["group"] for e in listed}
    assert groups["water"] == "example" and groups["posts"] == "first_party"
    assert not any(e["installed"] for e in listed)
    plan = owner.post("/api/apps/_install/plan", json={"example": "books_read"})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["source"]["kind"] == "folder" and body["source"]["example"] == "books_read"
    assert body["described"] is True and store.get(core, "books_read") is None
    done = owner.post("/api/apps/_install/confirm", json={
        "plan": body["plan"], "fingerprint": body["fingerprint"]})
    assert done.status_code == 200 and done.json()["id"] == "books_read"
    now = {e["id"]: e["installed"] for e in owner.get("/api/apps/_examples").json()["examples"]}
    assert now["books_read"] is True and now["water"] is False


@pytest.mark.parametrize("name", [
    "../examples/water", "..", ".", "water/", "/etc", "water/../water", "Water", "nope", "",
    "water\x00", "C:\\apps", "~", "apps/examples/water", "_staging", 7, ["water"], None,
])
def test_an_example_is_named_by_its_id_and_never_by_a_path(owner, core, name):
    answer = owner.post("/api/apps/_install/plan", json={"example": name})
    assert answer.status_code in (404, 422), answer.text
    assert "nothing was installed" in answer.json()["detail"].lower() or \
        answer.status_code == 422
    assert staging(core) == [] and events(core) == []


def test_an_example_and_another_source_together_are_refused(owner, core):
    for body in ({"example": "water", "folder": "x"}, {"example": "water", "url": URL},
                 {"example": "water", "commit": "0" * 40}, {"folder": "x", "ref": "main"}):
        assert owner.post("/api/apps/_install/plan", json=body).status_code == 422
    assert staging(core) == []


def test_the_owner_reads_a_described_apps_tables_and_views(owner, tmp_path):
    put_in(owner, example(tmp_path, "books_read"))
    got = owner.get("/api/apps/books_read/described").json()
    assert set(got) == {"id", "name", "tables", "views"}
    assert set(got["tables"]) == {"author", "book"}
    assert got["tables"]["book"]["fields"]["per_page"]["formula"] == "paid / pages"
    assert [v["id"] for v in got["views"]] == ["all", "shelf", "favourites", "authors"]
    assert owner.get("/api/apps/ghost/described").status_code == 404
