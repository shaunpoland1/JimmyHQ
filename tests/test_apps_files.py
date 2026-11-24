"""Serving an app's files: the exact headers, and every way a path might try to leave."""

from __future__ import annotations

import os

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

import jimmyhq.apps
from jimmyhq.apps import bridge, files, quotas
from jimmyhq.web.app import create_app
from test_apps_bridge_fakes import BOOKS, WATER, installs

PASSWORD = "correct horse battery"
SECRET = "OUTSIDE-THE-APP"

CSP = ("sandbox allow-scripts allow-forms; default-src 'none'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; "
       "media-src 'self' blob:; connect-src 'none'; form-action 'none'; frame-src 'none'; "
       "worker-src 'none'; base-uri 'none'; webrtc 'block'")


@pytest.fixture
def apps(monkeypatch, tmp_path):
    return installs(monkeypatch, tmp_path)


@pytest.fixture
def client(core, monkeypatch):
    def build(core_):
        router = APIRouter()
        router.include_router(bridge.build_router(core_))
        router.include_router(files.build_router(core_))
        return router

    monkeypatch.setattr(jimmyhq.apps, "build", build, raising=False)
    monkeypatch.setattr(files, "_PROVIDED", {})
    with TestClient(create_app(core), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def owner(client):
    assert client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
    return client


def write(folder, name, content="x"):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def water(apps, tmp_path):
    app = apps.add(WATER)
    write(app.folder, "index.html", "<!doctype html><title>Water</title>")
    write(app.folder, "app.js", "export const n = 1;")
    write(app.folder, "css/site.css", "body { margin: 0 }")
    # Things that must never be served, inside and outside the app.
    write(app.folder.parent.parent, "data/app.db", SECRET)
    write(app.folder.parent, "other-version/index.html", SECRET)
    write(tmp_path, "secret.html", SECRET)
    return app


def mount(owner, app_id="water"):
    answer = owner.post(f"/api/apps/{app_id}/mount")
    assert answer.status_code == 200, answer.text
    return answer.json()


# --- mounting --------------------------------------------------------------------------------


def test_the_owner_mounts_an_app_and_is_told_what_the_page_needs(owner, water):
    made = mount(owner)
    assert set(made) == {"mount", "base", "entry", "app", "name", "version", "tier",
                         "permissions", "local_addresses", "owner_hosts", "development", "api",
                         "timezone", "week_start",
                         "upload_max_mb", "thumbnail_px", "picture_px", "picture_max_mb",
                         "pictures_per_minute", "dictate_max_s", "private",
                         "save_max_mb", "save_parts", "save_per_minute", "save_html"}
    assert made["entry"] == "index.html"
    assert made["base"] == f"/appfiles/{made['mount']}/"
    assert (made["app"], made["version"], made["tier"]) == ("water", "1.0.0", "sandboxed")
    assert made["permissions"] == ["tasks.write"] and made["api"] == "1.0"
    assert len(made["mount"]) >= 40 and mount(owner)["mount"] != made["mount"]


def test_only_the_signed_in_owner_can_mount(client, core, water):
    assert client.post("/api/apps/water/mount").status_code == 401
    secret = core.auth.create_token("automation", ["read", "write"])[1]
    answer = client.post("/api/apps/water/mount",
                         headers={"authorization": f"Bearer {secret}"})
    assert answer.status_code == 403


def test_an_app_that_is_missing_or_switched_off_cannot_be_mounted(owner, apps):
    apps.add(WATER, enabled=False)
    assert owner.post("/api/apps/water/mount").status_code == 409
    for app_id in ("nobody", "..", "Water", "water%2F.."):
        assert owner.post(f"/api/apps/{app_id}/mount").status_code == 404


def test_the_token_itself_is_not_kept_only_its_hash(owner, core, water):
    token = mount(owner)["mount"]
    files.mounts(core).close()
    on_disk = b"".join(p.read_bytes() for p in quotas.state_db(core).parent.iterdir()
                       if p.is_file())
    assert token.encode() not in on_disk


# --- the headers -----------------------------------------------------------------------------


@pytest.mark.parametrize("name, kind", [
    ("index.html", "text/html; charset=utf-8"),
    ("app.js", "text/javascript; charset=utf-8"),
    ("css/site.css", "text/css; charset=utf-8"),
])
def test_every_app_file_carries_exactly_the_designs_headers(owner, water, name, kind):
    base = mount(owner)["base"]
    owner.cookies.clear()  # the frame holds no credential, and needs none
    answer = owner.get(base + name)
    assert answer.status_code == 200
    got = answer.headers
    assert got["content-security-policy"] == CSP == files.CSP
    assert got["content-type"] == kind
    assert got["x-content-type-options"] == "nosniff"
    assert got["referrer-policy"] == "no-referrer"
    assert got["x-frame-options"] == "SAMEORIGIN"
    assert got["cache-control"] == "private, max-age=3600, immutable"
    assert got["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in got
    assert "set-cookie" not in got


def test_the_policy_gives_the_page_no_way_out_but_the_bridge():
    parts = dict(part.strip().split(" ", 1) for part in files.CSP.split(";"))
    assert parts["sandbox"] == "allow-scripts allow-forms"  # never allow-same-origin
    assert parts["default-src"] == "'none'" and parts["connect-src"] == "'none'"
    assert parts["form-action"] == "'none'" and parts["frame-src"] == "'none'"
    assert parts["worker-src"] == "'none'" and parts["base-uri"] == "'none'"
    assert parts["script-src"] == "'self'" and parts["webrtc"] == "'block'"
    for word in ("allow-same-origin", "allow-top-navigation", "allow-popups", "unsafe-eval",
                 "http:", "https:", "*"):
        assert word not in files.CSP
    assert "'unsafe-inline'" not in parts["script-src"]


def test_a_refusal_carries_the_same_policy_and_is_never_cached(owner, water):
    base = mount(owner)["base"]
    for url in (base + "missing.html", "/appfiles/not-a-token/index.html", "/appfiles/",
                "/appfiles"):
        answer = owner.get(url)
        if url == "/appfiles":
            continue  # not under the prefix: the web app's own page or a 404, not a file
        assert answer.status_code == 404
        assert answer.headers["content-security-policy"] == CSP
        assert answer.headers["x-content-type-options"] == "nosniff"
        assert answer.headers["cache-control"] == "no-store"
        assert answer.json() == {"detail": "There is nothing at this address."}


def test_a_development_apps_files_are_never_cached(owner, apps):
    app = apps.add(WATER, development=True)
    write(app.folder, "index.html")
    made = mount(owner)
    assert made["development"] is True
    assert owner.get(made["base"] + "index.html").headers["cache-control"] == "no-store"


def test_a_head_request_gets_the_headers_without_the_body(owner, water):
    answer = owner.head(mount(owner)["base"] + "index.html")
    assert answer.status_code == 200 and answer.content == b""
    assert answer.headers["content-security-policy"] == CSP


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch", "options"])
def test_app_files_can_only_be_read(owner, water, method):
    answer = getattr(owner, method)(mount(owner)["base"] + "index.html")
    assert answer.status_code in (404, 405)
    assert (water.folder / "index.html").read_text(encoding="utf-8").startswith("<!doctype")


# --- content types ---------------------------------------------------------------------------


def test_each_kind_of_file_has_one_fixed_type(owner, water):
    base = mount(owner)["base"]
    for ending, kind in files.TYPES.items():
        write(water.folder, f"file{ending}", b"data")
        answer = owner.get(f"{base}file{ending}")
        assert answer.status_code == 200 and answer.headers["content-type"] == kind, ending
    upper = write(water.folder, "LOGO.PNG", b"data")
    assert owner.get(base + upper.name).headers["content-type"] == "image/png"


def test_a_file_is_typed_by_its_name_never_by_what_is_in_it(owner, water):
    write(water.folder, "pic.png", "<script>alert(1)</script>")
    write(water.folder, "notes.txt", "<!doctype html><script>x</script>")
    base = mount(owner)["base"]
    assert owner.get(base + "pic.png").headers["content-type"] == "image/png"
    assert owner.get(base + "notes.txt").headers["content-type"] == "text/plain; charset=utf-8"


@pytest.mark.parametrize("name", ["main.py", "uv.lock", "pyproject.toml", "app.db", "run.sh",
                                  "page.htm", "page.xhtml", "page.php", "data.xml", "a.wasm",
                                  "README", "Makefile", "archive.zip", "x.html.bak",
                                  "app.js.map", "page.html.py"])
def test_a_kind_of_file_not_on_the_list_is_not_served(owner, water, name):
    write(water.folder, name, SECRET)
    answer = owner.get(mount(owner)["base"] + name)
    assert answer.status_code == 404 and SECRET not in answer.text


# --- confinement -----------------------------------------------------------------------------

TRICKS = [
    "../secret.html", "../../secret.html", "../../../../../secret.html",
    "css/../../other-version/index.html", "../other-version/index.html",
    "../../data/app.db", "./index.html", "css/./site.css", "css//site.css",
    "/index.html", "//index.html", "/etc/passwd", "//server/share/index.html",
    "\\\\server\\share\\index.html", "..\\secret.html", "css\\site.css",
    "..\\..\\data\\app.db", "C:/index.html", "C:\\index.html", "C:index.html",
    "c:/Windows/win.ini", "file:///index.html", "index.html::$DATA", "index.html:stream",
    "index.html.", "index.html ", " index.html", "index.html/", "index.html/.",
    "index.html%00.png", "index.html\x00.png", "con.html", "nul.js", "aux.css",
    "COM1.html", ".env", ".git/config", ".hidden.html", "css/.secret.css", "...html",
    "%2e%2e/secret.html", "%2e%2e%2fsecret.html", "..%2fsecret.html", "..%5csecret.html",
    "%252e%252e%252fsecret.html", "..%252fsecret.html", "%c0%ae%c0%ae/secret.html",
    "\uff0e\uff0e/secret.html", "..\u2215secret.html", "~/secret.html", "*", "*.html",
    "index.html?x=../secret.html", "", "css", "css/",
]


@pytest.mark.parametrize("path", TRICKS)
def test_no_path_trick_reaches_anything_but_an_ordinary_file_of_the_app(
        owner, core, water, path):
    token = mount(owner)["mount"]
    direct = files.serve(core, token, path)  # the path as it arrives after decoding
    assert direct.status_code == 404
    assert files.locate(water.folder, path) is None
    try:
        answer = owner.get(f"/appfiles/{token}/{path}")
    except Exception:  # noqa: BLE001 - an address the client itself will not send
        return
    if answer.status_code == 200:
        # The client or the server may fold an address into a plain one before routing;
        # then it must be one of the app's own files and nothing else.
        assert SECRET not in answer.text
        assert answer.text in ("<!doctype html><title>Water</title>", "body { margin: 0 }")
    else:
        # 404 from here, or the web app's own answer when the client folded ".." and
        # the address no longer points under /appfiles/ at all.
        assert answer.status_code in (404, 503) and SECRET not in answer.text


@pytest.mark.parametrize("raw", [
    b"/..%2f..%2f..%2fsecret.html", b"/%2e%2e/%2e%2e/%2e%2e/secret.html",
    b"/..%5c..%5csecret.html", b"/css%2f..%2f..%2fother-version%2findex.html",
    b"/%2e%2e%2f%2e%2e%2fdata%2fapp.db", b"/..;/secret.html", b"/.%2e/secret.html",
])
def test_an_encoded_separator_or_dot_does_not_get_past_the_check(owner, core, water, raw):
    token = mount(owner)["mount"]
    answer = owner.get(f"/appfiles/{token}" + raw.decode("ascii"))
    assert answer.status_code == 404 and SECRET not in answer.text


def _link(link, target, *, folder=False):
    try:
        os.symlink(target, link, target_is_directory=folder)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let the test make a link")


def test_a_link_to_a_file_outside_the_app_is_not_followed(owner, core, water, tmp_path):
    _link(water.folder / "leak.html", tmp_path / "secret.html")
    answer = owner.get(mount(owner)["base"] + "leak.html")
    assert answer.status_code == 404 and SECRET not in answer.text
    assert files.locate(water.folder, "leak.html") is None


def test_a_linked_folder_is_not_followed(owner, core, water, tmp_path):
    _link(water.folder / "up", tmp_path, folder=True)
    _link(water.folder / "sibling", water.folder.parent / "other-version", folder=True)
    base = mount(owner)["base"]
    for path in ("up/secret.html", "sibling/index.html"):
        answer = owner.get(base + path)
        assert answer.status_code == 404 and SECRET not in answer.text


def test_even_a_link_that_stays_inside_the_app_is_refused(owner, water):
    _link(water.folder / "alias.html", water.folder / "index.html")
    assert owner.get(mount(owner)["base"] + "alias.html").status_code == 404


def test_a_hard_path_inside_the_app_is_still_served(owner, water):
    write(water.folder, "a/b/c/deep_file-1.v2.js", "deep")
    assert owner.get(mount(owner)["base"] + "a/b/c/deep_file-1.v2.js").text == "deep"


def test_one_apps_mount_never_serves_another_apps_files(owner, apps, water):
    books = apps.add(BOOKS)
    write(books.folder, "private.html", SECRET)
    water_base, books_base = mount(owner)["base"], mount(owner, "books_read")["base"]
    assert owner.get(books_base + "private.html").text == SECRET
    for path in ("private.html", "../../../books_read/versions/1.0.0-0a1b2c3d/private.html",
                 "..%2f..%2f..%2fbooks_read%2fversions%2f1.0.0-0a1b2c3d%2fprivate.html"):
        answer = owner.get(water_base + path)
        assert answer.status_code in (404, 503) and SECRET not in answer.text
    assert owner.get(books_base + "index.html").status_code == 404  # water's file


# --- the life of a mount ---------------------------------------------------------------------


@pytest.mark.parametrize("token", ["", "x", "water", "..", "0" * 43, "a" * 200, "%2e%2e",
                                   "null", "undefined", "*"])
def test_a_made_up_token_opens_nothing(owner, water, token):
    answer = owner.get(f"/appfiles/{token}/index.html")
    # ".." never reaches /appfiles/ at all: the address is /index.html, the shell's own
    # page when the web app has been built, which carries the shell's policy, not an app's.
    shell = answer.headers.get("content-security-policy") == "frame-src 'self'"
    assert answer.status_code in (404, 503) or (token == ".." and shell)
    assert "Water" not in answer.text


def test_a_mount_stops_working_when_the_app_is_switched_off_or_removed(owner, apps, water):
    base = mount(owner)["base"]
    assert owner.get(base + "index.html").status_code == 200
    apps.apps["water"] = bridge.Installed(water.manifest, False, water.granted, water.folder)
    assert owner.get(base + "index.html").status_code == 404
    apps.apps["water"] = water
    assert owner.get(base + "index.html").status_code == 200
    del apps.apps["water"]
    assert owner.get(base + "index.html").status_code == 404


def test_a_mount_is_for_one_version_and_does_not_follow_an_update(owner, apps, water):
    old = mount(owner)["base"]
    updated = apps.add(WATER, version="1.1.0", hash8="ffff0000")
    write(updated.folder, "index.html", "new")
    assert owner.get(old + "index.html").status_code == 404
    new = mount(owner)["base"]
    assert owner.get(new + "index.html").text == "new"
    apps.apps["water"] = water  # rolled back: the newer mount must not show the old code
    assert owner.get(new + "index.html").status_code == 404


def test_forgetting_an_apps_mounts_closes_them_all(owner, core, water):
    bases = [mount(owner)["base"] for _ in range(3)]
    files.forget_mounts(core, "water")
    assert [owner.get(b + "index.html").status_code for b in bases] == [404, 404, 404]


def test_only_the_newest_mounts_of_an_app_are_kept(owner, core, water):
    first = files.mint_mount(core, "water")["base"]
    for _ in range(60):
        last = files.mint_mount(core, "water")["base"]
    assert owner.get(first + "index.html").status_code == 404
    assert owner.get(last + "index.html").status_code == 200


def test_a_mount_survives_a_restart_of_the_service(owner, core, water):
    base = mount(owner)["base"]
    files.mounts(core).close()
    core._services.pop("apps.mounts")
    assert owner.get(base + "index.html").status_code == 200


# --- files the core supplies -----------------------------------------------------------------


def test_the_core_supplies_the_bridge_script_under_every_mount(owner, water):
    files.provide("_bridge.js", b"export const hq = {};")
    write(water.folder, "_bridge.js", "export const hq = 'the app made its own';")
    answer = owner.get(mount(owner)["base"] + "_bridge.js")
    assert answer.text == "export const hq = {};"
    assert answer.headers["content-type"] == "text/javascript; charset=utf-8"
    assert answer.headers["content-security-policy"] == CSP


def test_a_page_in_a_folder_of_the_app_gets_the_bridge_from_beside_itself(owner, water):
    files.provide("_bridge.js", b"export const hq = {};")
    write(water.folder, "web/_bridge.js", "export const hq = 'the app made its own';")
    for where in ("web/_bridge.js", "web/views/_bridge.js"):
        answer = owner.get(mount(owner)["base"] + where)
        assert answer.status_code == 200 and answer.text == "export const hq = {};", where


def test_the_core_supplies_the_page_helper_beside_every_page_from_the_sdk(owner, water):
    from jimmyhq.apps import store

    write(water.folder, "web/_ui.js", "export const h = 'the app made its own';")
    for where in ("_ui.js", "web/_ui.js", "web/views/_ui.js"):
        answer = owner.get(mount(owner)["base"] + where)
        assert answer.status_code == 200, where
        assert answer.content == store.ui_script().read_bytes(), where
        assert answer.headers["content-security-policy"] == CSP


def test_a_supplied_file_can_come_from_disk_and_needs_a_mount(owner, water, tmp_path):
    files.provide("_described/index.html", write(tmp_path, "renderer.html", "<p>drawn</p>"))
    assert owner.get(mount(owner)["base"] + "_described/index.html").text == "<p>drawn</p>"
    assert owner.get("/appfiles/nope/_described/index.html").status_code == 404


@pytest.mark.parametrize("name", ["bridge.js", "_../x.js", "_x//y.js", "_secret.py", "_",
                                  "/_abs.js", "_x\\y.js"])
def test_the_core_only_supplies_names_that_are_its_own(name):
    with pytest.raises(ValueError):
        files.provide(name, b"x")


# --- nothing but app files in a frame --------------------------------------------------------


@pytest.mark.parametrize("path, dest, ok", [
    ("/appfiles/tok/index.html", "iframe", True),
    ("/", "iframe", False), ("/index.html", "iframe", False),
    ("/api/auth/state", "iframe", False), ("/appfilesx/tok/index.html", "iframe", False),
    ("/appfiles", "iframe", False), ("/", "frame", False), ("/", "embed", False),
    ("/", "object", False), ("/", "IFrame", False), ("/", "fencedframe", False),
    ("/", "document", True), ("/api/tasks", "empty", True), ("/", None, True),
    ("/assets/app.js", "script", True),
])
def test_a_frame_is_shown_nothing_but_app_files(path, dest, ok):
    sent = {} if dest is None else {"sec-fetch-dest": dest}
    assert files.frames_only_get_app_files(path, sent) is ok


def test_the_frame_guard_refuses_before_the_page_is_built():
    reached = []
    app = FastAPI()

    @app.get("/{path:path}")
    def page(path: str):
        reached.append(path)
        return PlainTextResponse("the sign-in page")

    app.add_middleware(files.FrameGuard)
    with TestClient(app) as c:
        framed = c.get("/", headers={"sec-fetch-dest": "iframe"})
        assert framed.status_code == 403 and "sign-in" not in framed.text
        assert "only an app's own files" in framed.json()["detail"]
        assert c.get("/login", headers={"sec-fetch-dest": "iframe"}).status_code == 403
        assert reached == []
        assert c.get("/").text == "the sign-in page"
        assert c.get("/appfiles/t/x.html", headers={"sec-fetch-dest": "iframe"}).is_success
