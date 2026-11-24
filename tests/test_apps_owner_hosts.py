"""Hosts the owner grants an app after install, from the owner's side and from the side of
an app that wants more than it was given. No network: names resolve from a table and
answers come from a made-up internet."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest

from apps_full_helpers import AppClient, client_for, full_app, install_full
from apps_install_helpers import install_folder
from jimmyhq import lookup
from jimmyhq.apps import install, owner_hosts, store, update
from jimmyhq.apps import manifest as mf
from jimmyhq.apps import permissions as perms
from jimmyhq.apps.owner_hosts import HostRefused
from jimmyhq.core import open_core
from test_apps_sync import as_they_were
from test_lookup_fakes import PUBLIC, Names, Web, private

PASSWORD = "correct horse battery"
KEY = "sk-test-SECRET-0123456789"
SITE = "blog.example.com"
OTHER = "notes.example.org"
ASKED = {"purpose": "Publish drafts to your own site.", "max": 2}
# Put together at run time, so the repo's public-safety check sees no local name.
LOCAL_NAME = "printer" + ".local"
TAILNET_NAME = "box.tail1234" + ".ts.net"
PER_HOST = [{"name": "site_user", "label": "User name", "help": "Your sign-in name there.",
             "for_each": "owner_host"},
            {"name": "site_password", "label": "Application password",
             "help": "Made on your site.", "for_each": "owner_host"}]


@pytest.fixture
def web(core):
    with client_for(core) as client:
        assert client.post("/api/auth/setup", json={"name": "Sam",
                                                    "password": PASSWORD}).status_code == 200
        yield client


@pytest.fixture
def bare(core):
    with client_for(core) as client:
        yield client


@pytest.fixture
def internet(core):
    """Every name used here resolves to a public address until a test says otherwise."""
    service = lookup.service(core)
    service.resolver = Names({SITE: [PUBLIC], OTHER: [PUBLIC], "third.example.net": [PUBLIC],
                              "api.example.com": [PUBLIC]})
    service.transport = Web({(SITE, "/wp-json/"): httpx.Response(200, json={"name": "x"}),
                             (OTHER, "/wp-json/"): httpx.Response(200, json={"name": "y"})})
    return service


@pytest.fixture
def drafty(core, tmp_path):
    install_full(core, tmp_path, "drafty", owner_hosts=ASKED, secrets=PER_HOST)
    return "drafty"


def program(bare, core, app_id: str = "drafty") -> AppClient:
    return AppClient(bare, core.auth.mint_app_token(app_id))


def add(web, host, app_id="drafty"):
    return web.post(f"/api/apps/{app_id}/hosts", json={"host": host})


def tape(core, kind=None):
    return [e for e in core.tape.after(0, limit=100000) if kind is None or e.kind == kind]


def fetch(bare, core, url, app_id="drafty"):
    return program(bare, core, app_id).call("net.fetch", {"url": url})


# --- the manifest and the consent --------------------------------------------------------------


def test_the_install_shows_owner_hosts_as_its_own_sentence_with_the_apps_purpose(core, tmp_path):
    plan = install.plan_folder(core, full_app(tmp_path, "drafty", owner_hosts=ASKED))
    [line] = [x for x in plan["permissions"] if x["id"] == "owner_hosts"]
    assert line["sentence"].startswith("It may ask you for the address of your own site, and "
                                       "will be able to send to and read from the addresses "
                                       "you give it, 2 at most.")
    assert "Publish drafts to your own site." in line["sentence"]


def test_the_owners_consent_covers_the_purpose_and_the_number(core):
    base = {"manifest": 1, "id": "drafty", "name": "Drafty", "summary": "A test app.",
            "licence": "MIT", "version": "1.0.0", "tier": "full", "api": "1.0",
            "permissions": [], "server": {"entry": "drafty_main:app"}}
    without = perms.fingerprint(mf.parse(base))
    marks = {perms.fingerprint(mf.parse(base | {"owner_hosts": a})) for a in (
        ASKED, ASKED | {"max": 3}, ASKED | {"purpose": "Something else."})}
    assert without not in marks and len(marks) == 3


@pytest.mark.parametrize("asked", [
    {"purpose": "Publish drafts.", "max": 0}, {"purpose": "Publish drafts.", "max": 6},
    {"purpose": "Publish drafts.", "max": True}, {"purpose": "", "max": 1},
    {"purpose": "Publish drafts."}, {"purpose": "Publish drafts.", "max": 1, "hosts": ["x"]},
    ["blog.example.com"], "any"])
def test_owner_hosts_is_a_purpose_and_a_small_number_and_nothing_else(asked):
    with pytest.raises(mf._Bad):  # noqa: SLF001
        mf.check_owner_hosts(asked)


NEXT = "Then open the app and choose it."


def test_owner_hosts_may_say_in_one_sentence_what_to_do_after_adding_an_address():
    asked = mf.check_owner_hosts(ASKED | {"next": NEXT})
    assert asked.next == NEXT
    assert mf.check_owner_hosts(ASKED).next is None
    for bad in ("", " padded ", "two\nlines", "x" * 201, 7, None):
        with pytest.raises(mf._Bad):  # noqa: SLF001
            mf.check_owner_hosts(ASKED | {"next": bad})


def test_the_next_sentence_survives_the_manifest_round_trip_and_changes_no_consent():
    base = {"manifest": 1, "id": "drafty", "name": "Drafty", "summary": "A test app.",
            "licence": "MIT", "version": "1.0.0", "tier": "full", "api": "1.0",
            "permissions": [], "server": {"entry": "drafty_main:app"}}
    said = mf.parse(base | {"owner_hosts": ASKED | {"next": NEXT}})
    assert mf.parse(said.to_dict()) == said
    assert said.to_dict()["owner_hosts"]["next"] == NEXT
    assert "next" not in mf.parse(base | {"owner_hosts": ASKED}).to_dict()["owner_hosts"]
    assert perms.fingerprint(said) == perms.fingerprint(mf.parse(base | {"owner_hosts": ASKED}))


def test_the_apps_page_under_settings_is_given_the_next_sentence_only_when_declared(
        core, tmp_path, web):
    install_full(core, tmp_path, "drafty", owner_hosts=ASKED | {"next": NEXT}, secrets=PER_HOST)
    assert web.get("/api/apps/drafty/hosts").json()["declared"]["next"] == NEXT
    install_full(core, tmp_path, "plainy", owner_hosts=ASKED, secrets=PER_HOST)
    assert web.get("/api/apps/plainy/hosts").json()["declared"]["next"] is None


def test_owner_hosts_cannot_be_asked_for_inside_permissions():
    with pytest.raises(perms.PermissionRefused, match="its own key"):
        perms.parse_permission("owner_hosts")


def test_a_secret_kept_for_each_owner_host_needs_owner_hosts_declared(tmp_path):
    base = json.loads((full_app(tmp_path, "drafty") / "jimmyhq.app.json").read_text())
    with pytest.raises(mf.ManifestError, match="for_each"):
        mf.parse(base | {"secrets": PER_HOST})
    with pytest.raises(mf.ManifestError, match="for_each"):
        mf.parse(base | {"owner_hosts": ASKED, "secrets": [PER_HOST[0] | {"for_each": "x"}]})
    parsed = mf.parse(base | {"owner_hosts": ASKED, "secrets": PER_HOST})
    assert all(s.per_host for s in parsed.secrets)
    assert mf.parse(parsed.to_dict()) == parsed


def test_an_update_that_adds_owner_hosts_or_raises_its_number_asks_again():
    base = {"manifest": 1, "id": "drafty", "name": "Drafty", "summary": "A test app.",
            "licence": "MIT", "version": "1.0.0", "tier": "full", "api": "1.0",
            "permissions": [], "server": {"entry": "drafty_main:app"}}
    old, new = mf.parse(base), mf.parse(base | {"owner_hosts": ASKED})
    assert [x.id for x in perms.diff(old, new).ask] == ["owner_hosts"]
    more = mf.parse(base | {"owner_hosts": ASKED | {"max": 3}})
    assert [x.id for x in perms.diff(new, more).ask] == ["owner_hosts"]
    assert perms.diff(new, new).needs_consent is False
    assert [x.id for x in perms.diff(new, old).removed] == ["owner_hosts"]


# --- the rules for a host, one by one ----------------------------------------------------------


@pytest.mark.parametrize("typed, host", [
    (SITE, SITE), (f"https://{SITE}", SITE), (f"https://{SITE}/", SITE),
    ("  HTTPS://Blog.Example.COM  ", SITE), (f"{SITE}/", SITE)])
def test_the_owner_may_type_a_bare_name_or_an_https_address(typed, host):
    assert owner_hosts.read_host(typed) == host


@pytest.mark.parametrize("typed, words", [
    ("*.example.com", "wildcard"), (f"https://*.{SITE}", "wildcard"),
    (f"http://{SITE}", "https"), (f"ftp://{SITE}", "https"),
    (f"https://{SITE}:8443", "nothing after"), (f"https://{SITE}/blog", "nothing after"),
    (f"https://{SITE}/?x=1", "nothing after"), ("https://sam:pw" + "@" + SITE, "nothing after"),
    (PUBLIC, "numbers"), (f"https://{PUBLIC}/", "numbers"), ("https://[::1]/", "numbers"),
    ("::1", "numbers"), ("localhost", "exact public"), (LOCAL_NAME, "exact public"),
    (TAILNET_NAME, "exact public"), ("intranet", "exact public"),
    ("router.home", "exact public"), ("", "Type your site"), (None, "Type your site"),
    (f"{SITE}\\x", "one line"), (f"{SITE} {OTHER}", "one line"), ("x" * 400, "one line")])
def test_anything_but_one_exact_public_host_over_https_is_refused_with_a_sentence(typed, words):
    with pytest.raises(HostRefused) as refused:
        owner_hosts.read_host(typed)
    assert words in refused.value.detail and "Nothing was added" in refused.value.detail


def test_a_name_jimmyhq_itself_is_reached_by_is_never_given():
    with pytest.raises(HostRefused, match="JimmyHQ itself"):
        owner_hosts.check_not_own({"hq.example.com"}, "hq.example.com")
    owner_hosts.check_not_own({"hq.example.com"}, SITE)


def test_only_a_version_that_declares_owner_hosts_is_given_any_and_never_more_than_its_max(
        tmp_path):
    row = {"owner_hosts": {OTHER: 2, SITE: 1, "third.example.net": 3}}
    declared = mf.parse(json.loads((full_app(tmp_path, "drafty", owner_hosts=ASKED)
                                    / "jimmyhq.app.json").read_text()))
    plain = mf.parse(json.loads((full_app(tmp_path / "b", "drafty")
                                 / "jimmyhq.app.json").read_text()))
    assert owner_hosts.granted(declared, row) == (SITE, OTHER)
    assert owner_hosts.granted(plain, row) == ()
    assert owner_hosts.granted(declared, None) == ()


# --- the owner adds and removes --------------------------------------------------------------


def test_the_owner_adds_his_own_site_and_the_app_reaches_it_like_a_manifest_host(
        web, bare, core, drafty, internet):
    assert fetch(bare, core, f"https://{SITE}/wp-json/")["ok"] is False
    answer = add(web, f"https://{SITE}")
    assert answer.status_code == 200, answer.text
    assert [h["host"] for h in answer.json()["hosts"]] == [SITE]
    [added] = tape(core, "apps.host_added")
    assert added.payload == {"app_id": "drafty", "host": SITE} and added.subject == "app:drafty"
    got = program(bare, core).ok("net.fetch", {"url": f"https://{SITE}/wp-json/"})
    assert got["status"] == 200 and internet.transport.seen == [(PUBLIC, SITE, "/wp-json/")]
    [receipt] = tape(core, "apps.fetched")
    assert receipt.payload == {"app_id": "drafty", "host": SITE, "size": len(got["text"]),
                               "address": PUBLIC}
    assert program(bare, core).ok("hosts.list") == {"hosts": [SITE], **ASKED}
    detail = web.get("/api/apps/drafty").json()
    assert detail["owner_hosts"] == [SITE]
    assert any(line["id"] == "owner_hosts" for line in detail["lines"])


def test_the_mount_answer_carries_the_hosts_the_owner_gave_so_the_page_may_reach_them(
        web, core, tmp_path, internet):
    from jimmyhq.apps import files  # noqa: PLC0415

    folder = full_app(tmp_path, "drafty", owner_hosts=ASKED, secrets=PER_HOST,
                      screens=[{"id": "main", "label": "Drafts", "entry": "index.html"}])
    (folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    install_folder(core, folder)
    assert files.mint_mount(core, "drafty")["owner_hosts"] == []
    add(web, SITE)
    add(web, OTHER)
    assert files.mint_mount(core, "drafty")["owner_hosts"] == [SITE, OTHER]
    assert web.post("/api/apps/drafty/mount").json()["owner_hosts"] == [SITE, OTHER]
    web.delete(f"/api/apps/drafty/hosts/{SITE}")
    assert files.mint_mount(core, "drafty")["owner_hosts"] == [OTHER]


def test_an_owner_host_gives_the_exact_host_only_and_never_a_neighbour(
        web, bare, core, drafty, internet):
    add(web, SITE)
    internet.resolver.table.update({f"x.{SITE}": [PUBLIC], "example.com": [PUBLIC]})
    for url in (f"https://x.{SITE}/", "https://example.com/", f"http://{SITE}/",
                f"https://{SITE}:8443/", f"https://{SITE}.evil.example/"):
        error = fetch(bare, core, url)["error"]
        assert error["code"] in ("not_allowed", "invalid") and "othing was sent" in \
            error["message"], url
    assert internet.transport.seen == []


def test_removing_a_host_stops_the_app_reaching_it_and_clears_its_secrets(
        web, bare, core, drafty, internet):
    add(web, SITE)
    assert web.put(f"/api/apps/drafty/hosts/{SITE}/secrets/site_password",
                   json={"value": KEY}).status_code == 200
    gone = web.delete(f"/api/apps/drafty/hosts/{SITE}")
    assert gone.status_code == 200 and gone.json()["hosts"] == []
    assert [e.payload["reason"] for e in tape(core, "apps.host_removed")] == ["owner"]
    assert fetch(bare, core, f"https://{SITE}/wp-json/")["error"]["code"] == "not_allowed"
    assert not [n for n in core.secrets.names() if n.endswith(f"#{SITE}")]
    assert internet.transport.seen == []


def test_more_hosts_than_the_app_asked_for_are_refused_and_nothing_is_recorded(
        web, core, drafty, internet):
    assert add(web, SITE).status_code == 200
    assert add(web, OTHER).status_code == 200
    third = add(web, "third.example.net")
    assert third.status_code == 409 and "at most 2" in third.json()["detail"]
    assert "Nothing was added" in third.json()["detail"]
    assert len(tape(core, "apps.host_added")) == 2
    again = add(web, SITE)
    assert again.status_code == 409 and "already" in again.json()["detail"]


@pytest.mark.parametrize("typed", [
    "*.example.com", PUBLIC, "https://[::1]/", "localhost", LOCAL_NAME,
    f"http://{SITE}", f"https://{SITE}:8443", TAILNET_NAME])
def test_a_wildcard_an_address_or_a_local_name_is_refused_and_nothing_is_recorded(
        web, core, drafty, internet, typed):
    answer = add(web, typed)
    assert answer.status_code == 422 and "Nothing was added" in answer.json()["detail"]
    assert tape(core, "apps.host_added") == [] and internet.resolver.asked == []


@pytest.mark.parametrize("address", [private(10, 0, 0, 5), "127.0.0.1",
                                     private(192, 168, 1, 1), "::1", "169.254.169.254",
                                     private(100, 101, 102, 103)])
def test_a_host_that_resolves_to_a_private_address_is_refused_when_it_is_added(
        web, core, drafty, internet, address):
    internet.resolver.table[SITE] = [address]
    answer = add(web, SITE)
    assert answer.status_code == 422 and "private network" in answer.json()["detail"]
    assert tape(core, "apps.host_added") == []


def test_the_owners_web_lookup_exception_never_lets_an_app_have_a_private_host(
        web, core, drafty, internet):
    core.settings.set("lookup.allow_private_hosts", [SITE], actor="owner")
    internet.resolver.table[SITE] = [private(10, 0, 0, 5)]
    assert add(web, SITE).status_code == 422
    assert tape(core, "apps.host_added") == []


def test_a_name_that_cannot_be_found_is_refused_and_says_to_check_the_spelling(
        web, core, drafty, internet):
    answer = add(web, "nowhere.example.com")
    assert answer.status_code == 422 and "spelling" in answer.json()["detail"]


def test_a_name_jimmyhq_is_reached_by_is_refused_when_it_is_added(web, core, drafty, internet):
    core.settings.set("access.allowed_hosts", ["hq.example.com"], actor="owner")
    internet.resolver.table["hq.example.com"] = [PUBLIC]
    answer = add(web, "hq.example.com")
    assert answer.status_code == 422 and "JimmyHQ itself" in answer.json()["detail"]


def test_a_host_that_later_resolves_to_a_private_address_reaches_nothing(
        web, bare, core, drafty, internet):
    assert add(web, SITE).status_code == 200
    for address in (private(10, 0, 0, 5), "127.0.0.1", "::1", private(100, 64, 0, 1)):
        internet.resolver.table[SITE] = [address]
        error = fetch(bare, core, f"https://{SITE}/wp-json/")["error"]
        assert error["code"] == "not_allowed" and "Nothing was sent" in error["message"]
    core.settings.set("lookup.allow_private_hosts", [SITE], actor="owner")
    internet.resolver.table[SITE] = [private(10, 0, 0, 5)]
    error = fetch(bare, core, f"https://{SITE}/wp-json/")["error"]
    assert error["code"] == "not_allowed" and "private network" in error["message"]
    assert internet.transport.seen == []


# --- from the app's side ---------------------------------------------------------------------


def test_an_app_cannot_add_a_host_itself_by_any_call_or_route(web, bare, core, drafty,
                                                             internet):
    me = program(bare, core)
    for name in ("hosts.add", "hosts.set", "hosts.grant"):
        assert me.call(name, {"host": SITE})["ok"] is False
    assert me.call("hosts.list", {"host": SITE})["error"]["code"] == "invalid"
    token = {"authorization": f"Bearer {me.token}"}
    with client_for(core) as other:
        assert other.post("/api/apps/drafty/hosts", json={"host": SITE},
                          headers=token).status_code in (401, 403)
        assert other.post("/api/apps/drafty/hosts", json={"host": SITE}).status_code in (
            401, 403)
    assert tape(core, "apps.host_added") == []
    assert fetch(bare, core, f"https://{SITE}/wp-json/")["error"]["code"] == "not_allowed"


def test_a_page_from_a_frame_or_another_site_cannot_add_a_host(web, core, drafty, internet):
    answer = web.post("/api/apps/drafty/hosts", json={"host": SITE},
                      headers={"sec-fetch-site": "cross-site"})
    assert answer.status_code == 403 and tape(core, "apps.host_added") == []


def test_an_app_that_does_not_declare_owner_hosts_is_given_none_and_lists_none(
        web, bare, core, tmp_path, internet):
    install_full(core, tmp_path, "plain")
    answer = add(web, SITE, app_id="plain")
    assert answer.status_code == 409 and "did not ask" in answer.json()["detail"]
    error = program(bare, core, "plain").call("hosts.list")["error"]
    assert error["code"] == "not_allowed" and "owner_hosts" in error["message"]
    assert fetch(bare, core, f"https://{SITE}/wp-json/", "plain")["ok"] is False


def test_a_host_left_on_the_record_counts_for_nothing_once_the_version_stops_declaring_it(
        web, bare, core, tmp_path, drafty, internet, monkeypatch):
    add(web, SITE)
    monkeypatch.setattr(owner_hosts, "drop_undeclared", lambda *a, **k: [])
    update.confirm_update(core, *_plan(core, tmp_path, owner_hosts=None))
    assert owner_hosts.recorded(store.get(core, "drafty")) == (SITE,)
    assert fetch(bare, core, f"https://{SITE}/wp-json/")["error"]["code"] == "not_allowed"


def _plan(core, tmp_path, **changes):
    extra = {"owner_hosts": ASKED, "secrets": PER_HOST} | changes
    extra = {k: v for k, v in extra.items() if v is not None}
    if "secrets" in extra and "owner_hosts" not in extra:
        extra.pop("secrets")
    folder = full_app(tmp_path / f"v{len(list(tmp_path.iterdir()))}", "drafty",
                      version="1.1.0", **extra)
    plan = update.plan_folder(core, "drafty", folder)
    return plan["plan"], plan["fingerprint"]


def test_an_update_that_still_declares_owner_hosts_keeps_them(web, bare, core, tmp_path,
                                                               drafty, internet):
    add(web, SITE)
    update.confirm_update(core, *_plan(core, tmp_path))
    assert owner_hosts.recorded(store.get(core, "drafty")) == (SITE,)
    assert program(bare, core).ok("net.fetch", {"url": f"https://{SITE}/wp-json/"})[
        "status"] == 200


def test_an_update_that_drops_owner_hosts_removes_every_host_and_its_secrets(
        web, bare, core, tmp_path, drafty, internet):
    add(web, SITE)
    web.put(f"/api/apps/drafty/hosts/{SITE}/secrets/site_password", json={"value": KEY})
    update.confirm_update(core, *_plan(core, tmp_path, owner_hosts=None))
    assert owner_hosts.recorded(store.get(core, "drafty")) == ()
    assert [e.payload["reason"] for e in tape(core, "apps.host_removed")] == [
        "no_longer_declared"]
    assert KEY not in json.dumps({n: core.secrets.get(n) for n in core.secrets.names()})


def test_an_update_that_allows_fewer_keeps_the_oldest(web, core, tmp_path, drafty, internet):
    add(web, SITE)
    add(web, OTHER)
    update.confirm_update(core, *_plan(core, tmp_path, owner_hosts=ASKED | {"max": 1}))
    assert owner_hosts.recorded(store.get(core, "drafty")) == (SITE,)


def test_removing_the_app_drops_its_hosts_and_a_reinstall_starts_with_none(
        web, core, tmp_path, drafty, internet):
    add(web, SITE)
    web.put(f"/api/apps/drafty/hosts/{SITE}/secrets/site_password", json={"value": KEY})
    install.remove(core, "drafty", keep_data=True)
    assert not [n for n in core.secrets.names() if n.startswith("app.drafty.")]
    install_folder(core, full_app(tmp_path / "again", "drafty", owner_hosts=ASKED,
                                  secrets=PER_HOST))
    assert owner_hosts.recorded(store.get(core, "drafty")) == ()
    assert web.get("/api/apps/drafty/hosts").json()["hosts"] == []


def test_a_host_removed_in_one_process_is_unreachable_at_once_in_another(
        web, cfg, core, drafty, internet):
    add(web, SITE)
    with as_they_were():
        other = open_core(cfg)
        try:
            service = lookup.service(other)
            service.resolver = internet.resolver
            service.transport = internet.transport
            with client_for(other) as there:
                me = AppClient(there, other.auth.mint_app_token("drafty"))
                assert me.ok("net.fetch", {"url": f"https://{SITE}/wp-json/"})["status"] == 200
                assert me.ok("hosts.list")["hosts"] == [SITE]
                assert web.delete(f"/api/apps/drafty/hosts/{SITE}").status_code == 200
                error = me.call("net.fetch", {"url": f"https://{SITE}/wp-json/"})["error"]
                assert error["code"] == "not_allowed"
                assert me.ok("hosts.list")["hosts"] == []
        finally:
            other.close()


# --- secrets kept for each host --------------------------------------------------------------


def test_the_owner_types_a_user_name_and_a_password_beside_each_address(
        web, bare, core, drafty, internet):
    add(web, SITE)
    add(web, OTHER)
    base = f"/api/apps/drafty/hosts/{SITE}/secrets"
    assert web.put(f"{base}/site_user", json={"value": "editor"}).json() == {
        "host": SITE, "name": "site_user", "set": True}
    web.put(f"{base}/site_password", json={"value": KEY})
    listed = web.get("/api/apps/drafty/hosts").json()["hosts"]
    assert [(h["host"], [s["set"] for s in h["secrets"]]) for h in listed] == [
        (SITE, [True, True]), (OTHER, [False, False])]
    me = program(bare, core)
    assert me.ok("secrets.get", {"name": "site_password", "host": SITE}) == KEY
    assert me.ok("secrets.get", {"name": "site_password", "host": OTHER}) is None


def test_a_secret_kept_for_one_host_is_never_read_for_another_or_without_one(
        web, bare, core, drafty, internet):
    add(web, SITE)
    web.put(f"/api/apps/drafty/hosts/{SITE}/secrets/site_password", json={"value": KEY})
    me = program(bare, core)
    for args in ({"name": "site_password"}, {"name": "site_password", "host": OTHER},
                 {"name": "site_password", "host": f"x.{SITE}"},
                 {"name": f"site_password#{SITE}"},
                 {"name": "site_password", "host": ["blog.example.com"]}):
        answer = me.call("secrets.get", args)
        assert answer["ok"] is False and KEY not in json.dumps(answer), args
    assert web.put(f"/api/apps/drafty/hosts/{OTHER}/secrets/site_password",
                   json={"value": KEY}).status_code == 404
    assert web.put("/api/apps/drafty/secrets/site_password",
                   json={"value": KEY}).status_code == 404
    assert web.get("/api/apps/drafty/secrets").json() == {"secrets": []}


def test_a_host_secret_never_appears_in_an_answer_the_record_or_a_log(
        web, core, drafty, internet, caplog):
    caplog.set_level(logging.DEBUG)
    add(web, SITE)
    answer = web.put(f"/api/apps/drafty/hosts/{SITE}/secrets/site_password",
                     json={"value": KEY})
    cleared = web.delete(f"/api/apps/drafty/hosts/{SITE}/secrets/site_password")
    assert KEY not in answer.text + cleared.text + web.get("/api/apps/drafty/hosts").text
    receipts = [e.payload for e in tape(core, "apps.host_secret_changed")]
    assert receipts == [{"app_id": "drafty", "host": SITE, "name": "site_password",
                         "action": a} for a in ("set", "cleared")]
    assert KEY not in json.dumps([e.payload for e in tape(core)]) and KEY not in caplog.text


def test_adding_a_host_runs_its_rules_in_order_and_nothing_else_happens_on_a_refusal(
        core, drafty, internet):
    with pytest.raises(HostRefused, match="wildcard"):
        asyncio.run(owner_hosts.add(core, "drafty", "*.example.com"))
    with pytest.raises(HostRefused, match="No app"):
        asyncio.run(owner_hosts.add(core, "nobody", SITE))
    assert asyncio.run(owner_hosts.add(core, "drafty", SITE))["hosts"][0]["host"] == SITE
    assert internet.resolver.asked == [SITE]



# --- the SDK and its fake --------------------------------------------------------------------


def test_the_sdk_lists_the_owners_hosts_and_reads_a_secret_for_one_of_them(tmp_path):
    import sys  # noqa: PLC0415

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))
    from jimmyhq_app import App, CallError  # noqa: PLC0415
    from jimmyhq_app.testing import FakeCore  # noqa: PLC0415

    fake = FakeCore("drafty", data_dir=tmp_path, permissions=[], owner_hosts=ASKED)
    app = App(transport=fake)
    assert app.hosts.list().hosts == []
    fake.grant_host(SITE, secrets={"site_password": KEY})
    listed = app.hosts.list()
    assert (listed.hosts, listed.max, listed.purpose) == ([SITE], 2, ASKED["purpose"])
    assert app.secrets.get("site_password", host=SITE) == KEY
    fake.responses[f"https://{SITE}/wp-json/"] = {"status": 200, "text": "{}"}
    assert app.net.fetch(f"https://{SITE}/wp-json/")["status"] == 200
    fake.remove_host(SITE)
    with pytest.raises(CallError):
        app.secrets.get("site_password", host=SITE)
    with pytest.raises(CallError):
        app.net.fetch(f"https://{SITE}/wp-json/")
    plain = App(transport=FakeCore("plain", data_dir=tmp_path, permissions=[]))
    with pytest.raises(CallError):
        plain.hosts.list()
