"""An address on the owner's own network that he lets one app reach: from the owner's side,
and from the side of an app, or a name, that wants more than that one address. No network:
names resolve from a table and answers come from a made-up network."""

from __future__ import annotations

import asyncio
import dataclasses
import json

import httpx
import pytest

from apps_full_helpers import AppClient, client_for, full_app, install_full
from apps_install_helpers import install_folder
from jimmyhq import lookup
from jimmyhq.apps import bridge, install, local_reach, store, update
from jimmyhq.apps.local_reach import AddressRefused
from jimmyhq.lookup import safety
from jimmyhq.lookup.errors import WebError
from test_lookup_fakes import PUBLIC, Names, Web, private

PASSWORD = "correct horse battery"
SERVER = "192.0.2.10"  # a documentation address, as an image server at home would be
ORIGIN = f"http://{SERVER}:8000"
# Built at run time, so the repo's public-safety check sees no real-looking local name.
HOME = private(192, 168, 1, 20)
TAILNET = "example-machine.example-tailnet" + ".ts.net"
METADATA = "169.254.169.254"
ALIBABA = private(100, 100, 100, 200)  # a cloud's metadata address in the tailnet range


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
def network(core):
    """A home network: the image server answers on its port; names resolve from a table."""
    service = lookup.service(core)
    service.resolver = Names({TAILNET: [HOME], "localhost": ["127.0.0.1"],
                              "images.example.com": [PUBLIC],
                              "sneaky.example.com": [HOME]})
    service.transport = Web({
        (f"{SERVER}:8000", "/v1/images"): httpx.Response(200, json={"data": [1]}),
        (f"{TAILNET}:8000", "/v1/images"): httpx.Response(200, json={"data": [2]}),
        (f"{SERVER}:8000", "/go"): httpx.Response(302, headers={
            "location": f"http://{SERVER}:22/"}),
    })
    return service


@pytest.fixture
def imagery(core, tmp_path):
    install_full(core, tmp_path, "imagery")
    return "imagery"


def program(bare, core, app_id: str = "imagery") -> AppClient:
    return AppClient(bare, core.auth.mint_app_token(app_id))


def fetch(bare, core, url, app_id="imagery"):
    return program(bare, core, app_id).call("net.fetch", {"url": url})


def check(web, address, app_id="imagery"):
    return web.post(f"/api/apps/{app_id}/local-addresses/check", json={"address": address})


def add(web, address, app_id="imagery"):
    """As the owner's page does it: see where it leads, then agree to exactly that."""
    seen = check(web, address, app_id)
    if seen.status_code != 200:
        return seen
    return web.post(f"/api/apps/{app_id}/local-addresses",
                    json={"address": address, "resolved": seen.json()["resolved"]})


def confirm(web, key, resolved, app_id="imagery"):
    return web.post(f"/api/apps/{app_id}/local-addresses/{key}/confirm",
                    json={"resolved": resolved})


def tape(core, kind):
    return [e for e in core.tape.after(0, limit=100000) if e.kind == kind]


# --- reading what the owner typed --------------------------------------------------------------


@pytest.mark.parametrize("typed, origin", [
    (ORIGIN, ORIGIN), (f"{ORIGIN}/", ORIGIN), (f"HTTP://{SERVER}:8000", ORIGIN),
    (f"http://{SERVER}", f"http://{SERVER}:80"), (f"https://{SERVER}", f"https://{SERVER}:443"),
    ("http://localhost:8000", "http://localhost:8000"),
    ("http://[::1]:8000", "http://[::1]:8000"),
    (f"https://{TAILNET}:8443", f"https://{TAILNET}:8443"),
])
def test_the_owner_types_one_exact_scheme_host_and_port(typed, origin):
    assert local_reach.read_address(typed).origin == origin


@pytest.mark.parametrize("typed", [
    "", "   ", None, 5, SERVER, f"{SERVER}:8000", f"ftp://{SERVER}:21",
    f"{ORIGIN}/v1/images", f"{ORIGIN}/?x=1", f"{ORIGIN}#top", f"http://user:pw@{SERVER}:8000",
    "http://*.example.com:8000", "http://2130706433:8000", "http://0x7f.1:8000",
    f"http://{SERVER}:99999", "http://" + "a" * 400, f"http://{SERVER}:8000\\x",
])
def test_anything_but_one_exact_origin_is_refused_with_a_sentence(typed):
    with pytest.raises(AddressRefused) as caught:
        local_reach.read_address(typed)
    assert caught.value.status == 422 and "Nothing was added" in caught.value.detail


@pytest.mark.parametrize("address", [
    METADATA, "169.254.170.2", "fe80::1", "fd00:ec2::254", ALIBABA,
    "::ffff:169.254.169.254", "::169.254.169.254", "64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::1",
    "0.0.0.0", "::", "224.0.0.1",
])
def test_a_cloud_computers_metadata_address_is_never_one(address):
    assert local_reach.is_metadata(local_reach.ipaddress.ip_address(address))


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", SERVER, "fd12:3456::1",
                                     private(10, 0, 0, 5), private(100, 101, 102, 103)])
def test_an_ordinary_address_on_the_owners_network_is_not_metadata(address):
    assert not local_reach.is_metadata(local_reach.ipaddress.ip_address(address))


# --- the owner adds one, and the app reaches exactly it ------------------------------------------


def test_before_the_owner_adds_it_the_app_reaches_nothing_on_his_network(
        bare, core, imagery, network):
    error = fetch(bare, core, f"{ORIGIN}/v1/images")["error"]
    assert error["code"] == "not_allowed" and "Addresses on your own network" in error["message"]
    assert network.transport.seen == []


def test_the_owner_adds_an_address_and_the_app_reaches_it(web, bare, core, imagery, network):
    answer = add(web, ORIGIN)
    assert answer.status_code == 200, answer.text
    listed = answer.json()
    assert [a["address"] for a in listed["addresses"]] == [ORIGIN] and listed["max"] == 3
    assert "will be able to send to and read from" in listed["warning"]
    [added] = tape(core, "apps.local_address_added")
    assert added.payload == {"app_id": "imagery", "address": ORIGIN, "resolved": [SERVER]}
    assert added.subject == "app:imagery" and added.actor != "app:imagery"
    got = program(bare, core).ok("net.fetch", {"url": f"{ORIGIN}/v1/images", "method": "POST",
                                               "json": {"prompt": "a pear"}})
    assert got["status"] == 200 and network.transport.seen == [
        (SERVER, f"{SERVER}:8000", "/v1/images")]
    [receipt] = tape(core, "apps.fetched")
    assert receipt.payload["host"] == SERVER and receipt.payload["address"] == SERVER
    assert web.get("/api/apps/imagery/local-addresses").json()["addresses"][0]["address"] == ORIGIN


def test_an_added_name_is_resolved_again_and_the_checked_address_is_the_one_connected_to(
        web, bare, core, imagery, network):
    assert add(web, f"http://{TAILNET}:8000").status_code == 200
    got = program(bare, core).ok("net.fetch", {"url": f"http://{TAILNET}:8000/v1/images"})
    assert got["status"] == 200
    assert network.transport.seen == [(HOME, f"{TAILNET}:8000", "/v1/images")]
    [receipt] = tape(core, "apps.fetched")
    assert receipt.payload["host"] == TAILNET and receipt.payload["address"] == HOME


def test_only_that_exact_scheme_host_and_port_and_never_a_neighbour(
        web, bare, core, imagery, network):
    add(web, ORIGIN)
    for url in (f"http://{SERVER}:8001/", f"https://{SERVER}:8000/", f"http://{SERVER}/",
                f"http://{SERVER}:22/", "http://192.0.2.11:8000/", f"http://{HOME}:8000/",
                "http://localhost:8000/", f"http://{TAILNET}:8000/"):
        error = fetch(bare, core, url)["error"]
        assert error["code"] in ("not_allowed", "invalid"), url
        assert "othing was sent" in error["message"], url
    assert network.transport.seen == []


def test_a_public_looking_name_cannot_be_used_to_reach_an_added_private_address(
        web, bare, core, imagery, network):
    add(web, f"http://{HOME}:8000")
    # The name points at the very address the owner added, on the same port: still refused,
    # because only the exact name (here, the address) he typed is let through.
    error = fetch(bare, core, "http://sneaky.example.com:8000/v1/images")["error"]
    assert error["code"] == "not_allowed"
    assert network.transport.seen == []


def test_an_added_name_that_later_leads_to_metadata_or_the_internet_reaches_nothing(
        web, bare, core, imagery, network):
    assert add(web, f"http://{TAILNET}:8000").status_code == 200
    for answer in ([METADATA], ["fd00:ec2::254"], [HOME, METADATA], [PUBLIC], [HOME, PUBLIC]):
        network.resolver.table[TAILNET] = answer
        error = fetch(bare, core, f"http://{TAILNET}:8000/v1/images")["error"]
        assert error["code"] == "not_allowed" and "Nothing was sent" in error["message"]
    network.resolver.table[TAILNET] = []
    assert fetch(bare, core, f"http://{TAILNET}:8000/v1/images")["error"]["code"] == \
        "unavailable"
    assert network.transport.seen == []


def test_a_redirect_from_an_added_address_is_handed_back_and_never_followed(
        web, bare, core, imagery, network):
    add(web, ORIGIN)
    got = program(bare, core).ok("net.fetch", {"url": f"{ORIGIN}/go"})
    assert got["status"] == 302 and got["headers"]["location"] == f"http://{SERVER}:22/"
    assert network.transport.seen == [(SERVER, f"{SERVER}:8000", "/go")]
    assert fetch(bare, core, f"http://{SERVER}:22/")["error"]["code"] == "not_allowed"


# --- what is never added -----------------------------------------------------------------------


@pytest.mark.parametrize("typed", [
    f"http://{METADATA}", f"http://{METADATA}:8000", "http://[fd00:ec2::254]:80",
    "http://[::ffff:169.254.169.254]:80", f"http://{ALIBABA}:80",
    "http://metadata.google.internal:80", "http://metadata:80",
])
def test_a_cloud_metadata_address_is_never_added(web, core, imagery, network, typed):
    answer = add(web, typed)
    assert answer.status_code == 422 and "Nothing was added" in answer.json()["detail"]
    assert tape(core, "apps.local_address_added") == []


def test_a_name_that_leads_to_metadata_is_never_added(web, core, imagery, network):
    network.resolver.table["cloudy.example.com"] = [METADATA]
    answer = add(web, "http://cloudy.example.com:8000")
    assert answer.status_code == 422 and "keys and settings" in answer.json()["detail"]
    assert tape(core, "apps.local_address_added") == []


def test_jimmyhqs_own_port_is_never_added_on_any_host(web, core, imagery, network, monkeypatch):
    monkeypatch.setattr(core, "config", dataclasses.replace(core.config, port=8440))
    for typed in ("http://127.0.0.1:8440", "http://localhost:8440", f"http://{SERVER}:8440",
                  "http://[::1]:8440"):
        answer = add(web, typed)
        assert answer.status_code == 422 and "JimmyHQ's own" in answer.json()["detail"]
    assert tape(core, "apps.local_address_added") == []


def test_jimmyhqs_own_port_is_refused_at_the_connection_too(core, monkeypatch):
    monkeypatch.setattr(core, "config", dataclasses.replace(core.config, port=8440))
    parsed = safety.parse("http://127.0.0.1:8440/api/auth/setup")
    with pytest.raises(WebError) as caught:
        asyncio.run(local_reach.check_at_connect(core, parsed, Names()))
    assert caught.value.kind == "refused"


def test_a_public_address_is_not_one_on_the_owners_network(web, core, imagery, network):
    for typed in (f"http://{PUBLIC}:8000", "https://images.example.com:8443"):
        answer = add(web, typed)
        assert answer.status_code == 422 and "public internet" in answer.json()["detail"]
    assert tape(core, "apps.local_address_added") == []


def test_a_name_that_cannot_be_found_is_refused_and_says_to_check_it(web, imagery, network):
    answer = add(web, "http://nowhere.example.com:8000")
    assert answer.status_code == 422 and "spelling" in answer.json()["detail"]


def test_more_addresses_than_the_owner_allows_are_refused_and_the_same_one_twice(
        web, core, imagery, network):
    core.settings.set(local_reach.MAX, 2, actor="owner")
    assert add(web, ORIGIN).status_code == 200
    again = add(web, f"{ORIGIN}/")
    assert again.status_code == 409 and "already" in again.json()["detail"]
    assert add(web, "http://localhost:8000").status_code == 200
    third = add(web, f"http://{TAILNET}:8000")
    assert third.status_code == 409 and "at most 2" in third.json()["detail"]
    assert len(tape(core, "apps.local_address_added")) == 2


# --- only the owner, and taking it back ----------------------------------------------------------


def test_an_app_cannot_add_an_address_itself_by_any_call_or_route(bare, core, imagery, network):
    me = program(bare, core)
    for name in ("hosts.add", "local.add", "net.allow"):
        assert me.call(name, {"address": ORIGIN})["ok"] is False
    with client_for(core) as other:
        assert other.post("/api/apps/imagery/local-addresses", json={"address": ORIGIN},
                          headers={"authorization": f"Bearer {me.token}"}).status_code in (
            401, 403)
        assert other.post("/api/apps/imagery/local-addresses",
                          json={"address": ORIGIN}).status_code in (401, 403)
    assert tape(core, "apps.local_address_added") == []
    assert fetch(bare, core, f"{ORIGIN}/v1/images")["error"]["code"] == "not_allowed"


def test_a_page_from_a_frame_or_another_site_cannot_add_or_list_one(web, core, imagery, network):
    cross = {"sec-fetch-site": "cross-site"}
    assert web.post("/api/apps/imagery/local-addresses", json={"address": ORIGIN},
                    headers=cross).status_code == 403
    assert web.get("/api/apps/imagery/local-addresses", headers=cross).status_code == 403
    assert tape(core, "apps.local_address_added") == []


def test_a_body_of_another_shape_adds_nothing(web, core, imagery, network):
    for body in ({"host": ORIGIN}, {"address": ORIGIN, "app": "other"}, [ORIGIN], "x",
                 {"address": ORIGIN}, {"address": ORIGIN, "resolved": "192.0.2.10"},
                 {"address": ORIGIN, "resolved": []}, {"address": ORIGIN, "resolved": ["x"]}):
        answer = web.post("/api/apps/imagery/local-addresses", json=body)
        assert answer.status_code in (409, 422), body
    assert web.post("/api/apps/nobody/local-addresses",
                    json={"address": ORIGIN, "resolved": [SERVER]}).status_code == 404
    assert check(web, ORIGIN, "nobody").status_code == 404
    assert tape(core, "apps.local_address_added") == []


def test_removing_an_address_stops_the_app_reaching_it_and_is_recorded(
        web, bare, core, imagery, network):
    key = add(web, ORIGIN).json()["addresses"][0]["key"]
    assert web.delete("/api/apps/imagery/local-addresses/0000000000000000").status_code == 404
    gone = web.delete(f"/api/apps/imagery/local-addresses/{key}")
    assert gone.status_code == 200 and gone.json()["addresses"] == []
    [removed] = tape(core, "apps.local_address_removed")
    assert removed.payload == {"app_id": "imagery", "address": ORIGIN, "reason": "owner"}
    assert fetch(bare, core, f"{ORIGIN}/v1/images")["error"]["code"] == "not_allowed"
    assert network.transport.seen == []


def test_removing_the_app_drops_its_addresses_and_a_reinstall_starts_with_none(
        web, core, tmp_path, imagery, network):
    add(web, ORIGIN)
    install.remove(core, "imagery", keep_data=True)
    install_folder(core, full_app(tmp_path / "again", "imagery"))
    assert local_reach.granted(store.get(core, "imagery")) == ()
    assert bridge.find_app(core, "imagery").local_addresses == ()


def test_one_apps_address_is_never_another_apps(web, bare, core, tmp_path, imagery, network):
    install_full(core, tmp_path / "other", "posty")
    add(web, ORIGIN)
    assert fetch(bare, core, f"{ORIGIN}/v1/images", "posty")["error"]["code"] == "not_allowed"
    assert network.transport.seen == []


def test_a_private_app_is_held_to_the_same_rule_and_gains_nothing_more(
        web, bare, core, tmp_path, network):
    install_full(core, tmp_path, "secretive", private=True)
    assert add(web, ORIGIN, "secretive").status_code == 200
    assert fetch(bare, core, f"{ORIGIN}/v1/images", "secretive")["ok"] is True
    assert fetch(bare, core, f"http://{SERVER}:8001/", "secretive")["ok"] is False


def test_each_app_holds_only_the_addresses_the_owner_added_for_it(
        web, core, tmp_path, imagery, network):
    install_full(core, tmp_path / "other", "posty")
    add(web, ORIGIN)
    assert bridge.find_app(core, "imagery").local_addresses == (ORIGIN,)
    assert bridge.find_app(core, "posty").local_addresses == ()


# --- a name stays where the owner saw it -------------------------------------------------------

ROUTER = private(192, 168, 1, 1)
NEIGHBOUR = private(10, 0, 0, 1)


def test_the_owner_is_shown_where_a_name_leads_and_that_is_stored_with_the_address(
        web, core, imagery, network):
    seen = check(web, f"http://{TAILNET}:8000")
    assert seen.status_code == 200 and seen.json()["resolved"] == [HOME]
    assert HOME in seen.json()["warning"] and seen.json()["added"] is False
    assert tape(core, "apps.local_address_added") == []  # looking adds nothing
    add(web, f"http://{TAILNET}:8000")
    [added] = tape(core, "apps.local_address_added")
    assert added.payload["resolved"] == [HOME]
    listed = web.get("/api/apps/imagery/local-addresses").json()["addresses"]
    assert listed[0]["resolved"] == [HOME]
    assert bridge.find_app(core, "imagery").local_resolved == (
        (f"http://{TAILNET}:8000", (HOME,)),)


@pytest.mark.parametrize("target", [ROUTER, "127.0.0.1", "::1", NEIGHBOUR])
def test_a_name_pointed_anywhere_else_after_it_was_added_reaches_nothing(
        web, bare, core, imagery, network, target):
    assert add(web, f"http://{TAILNET}:8000").status_code == 200
    network.resolver.table[TAILNET] = [target]
    error = fetch(bare, core, f"http://{TAILNET}:8000/v1/images")["error"]
    assert error["code"] == "not_allowed", target
    assert "Nothing was sent" in error["message"]
    assert "somewhere else" in error["message"] or "this computer itself" in error["message"]
    network.resolver.table[TAILNET] = [HOME, target]  # one known among them is not enough
    assert fetch(bare, core, f"http://{TAILNET}:8000/v1/images")["error"]["code"] == \
        "not_allowed"
    assert network.transport.seen == [] and tape(core, "apps.fetched") == []


def test_the_owner_confirms_where_a_name_leads_now_and_only_then_it_is_reached(
        web, bare, core, imagery, network):
    key = add(web, f"http://{TAILNET}:8000").json()["addresses"][0]["key"]
    network.resolver.table[TAILNET] = [NEIGHBOUR]
    network.transport.pages[(f"{TAILNET}:8000", "/v1/images")] = httpx.Response(200, json={})
    seen = check(web, f"http://{TAILNET}:8000").json()
    assert seen["added"] is True and seen["stored"] == [HOME]
    assert seen["resolved"] == [NEIGHBOUR] and seen["key"] == key
    # What he agrees to must be where it leads when he agrees.
    assert confirm(web, key, [HOME]).status_code == 409
    assert confirm(web, "0000000000000000", [NEIGHBOUR]).status_code == 404
    done = confirm(web, key, [NEIGHBOUR])
    assert done.status_code == 200 and done.json()["addresses"][0]["resolved"] == [NEIGHBOUR]
    [confirmed] = tape(core, "apps.local_address_confirmed")
    assert confirmed.payload == {"app_id": "imagery", "address": f"http://{TAILNET}:8000",
                                 "resolved": [NEIGHBOUR]}
    assert confirmed.actor != "app:imagery"
    assert program(bare, core).ok("net.fetch", {
        "url": f"http://{TAILNET}:8000/v1/images"})["status"] == 200
    assert network.transport.seen == [(NEIGHBOUR, f"{TAILNET}:8000", "/v1/images")]


def test_a_confirm_is_held_to_every_rule_and_comes_only_from_the_owners_page(
        web, bare, core, imagery, network):
    key = add(web, f"http://{TAILNET}:8000").json()["addresses"][0]["key"]
    network.resolver.table[TAILNET] = ["127.0.0.1"]
    assert check(web, f"http://{TAILNET}:8000").status_code == 422
    answer = confirm(web, key, ["127.0.0.1"])
    assert answer.status_code == 422 and "this computer itself" in answer.json()["detail"]
    network.resolver.table[TAILNET] = [NEIGHBOUR]
    cross = {"sec-fetch-site": "cross-site"}
    assert web.post(f"/api/apps/imagery/local-addresses/{key}/confirm",
                    json={"resolved": [NEIGHBOUR]}, headers=cross).status_code == 403
    me = program(bare, core)
    with client_for(core) as other:
        assert other.post(f"/api/apps/imagery/local-addresses/{key}/confirm",
                          json={"resolved": [NEIGHBOUR]},
                          headers={"authorization": f"Bearer {me.token}"}).status_code in (
            401, 403)
    for body in ({}, {"resolved": NEIGHBOUR}, {"resolved": [NEIGHBOUR], "address": "x"}):
        assert web.post(f"/api/apps/imagery/local-addresses/{key}/confirm",
                        json=body).status_code == 422
    assert tape(core, "apps.local_address_confirmed") == []


def test_an_add_is_refused_when_the_name_leads_elsewhere_than_the_owner_was_shown(
        web, core, imagery, network):
    shown = check(web, f"http://{TAILNET}:8000").json()["resolved"]
    network.resolver.table[TAILNET] = [ROUTER]
    answer = web.post("/api/apps/imagery/local-addresses",
                      json={"address": f"http://{TAILNET}:8000", "resolved": shown})
    assert answer.status_code == 409 and "not what you were shown" in answer.json()["detail"]
    assert tape(core, "apps.local_address_added") == []


def test_an_address_put_on_the_record_before_its_addresses_were_kept_waits_for_the_owner(
        web, bare, core, imagery, network):
    core.tape.print("apps.local_address_added", {"app_id": "imagery",
                                                 "address": f"http://{TAILNET}:8000"},
                    actor="owner", subject="app:imagery")
    error = fetch(bare, core, f"http://{TAILNET}:8000/v1/images")["error"]
    assert error["code"] == "not_allowed" and "confirm the new address" in error["message"]
    assert network.transport.seen == []


@pytest.mark.parametrize("typed", ["http://localhost:8000", "http://127.0.0.1:8000",
                                   "http://[::1]:8000"])
def test_this_computer_is_added_when_the_owner_types_it_as_this_computer(
        web, core, imagery, network, typed):
    assert add(web, typed).status_code == 200


def test_a_name_that_leads_to_this_computer_is_refused_unless_the_owner_typed_localhost(
        web, core, imagery, network):
    for answer in (["127.0.0.1"], ["::1"], [HOME, "127.0.0.1"], ["::ffff:127.0.0.1"]):
        network.resolver.table["loopy.example.net"] = answer
        refused = add(web, "http://loopy.example.net:8000")
        assert refused.status_code == 422 and "localhost" in refused.json()["detail"]
    assert tape(core, "apps.local_address_added") == []


# --- never JimmyHQ itself ------------------------------------------------------------------------


def test_the_doors_own_port_is_never_added_nor_reached_while_the_door_listens_there(
        web, bare, core, imagery, network):
    core.settings.set("door.listen", "own_port", actor="owner")
    core.settings.set("door.port", 8441, actor="owner")
    for typed in ("http://127.0.0.1:8441", "http://localhost:8441", f"http://{SERVER}:8441"):
        answer = add(web, typed)
        assert answer.status_code == 422 and "JimmyHQ's own" in answer.json()["detail"]
    with pytest.raises(WebError) as caught:
        asyncio.run(local_reach.check_at_connect(
            core, safety.parse("http://127.0.0.1:8441/mcp"), Names()))
    assert caught.value.kind == "refused"
    # On the main address instead, that port is no longer JimmyHQ's.
    core.settings.set("door.listen", "main_port", actor="owner")
    assert add(web, "http://127.0.0.1:8441").status_code == 200
    core.settings.set("door.listen", "own_port", actor="owner")  # and then it is again
    assert fetch(bare, core, "http://127.0.0.1:8441/mcp")["error"]["code"] == "not_allowed"
    assert network.transport.seen == []


@pytest.mark.parametrize("typed", ["https://testserver:443", "https://testserver:8443",
                                   "http://testserver:80"])
def test_a_name_jimmyhq_is_reached_by_is_never_added_on_any_port(web, core, imagery, network,
                                                                  typed):
    network.resolver.table["testserver"] = [HOME]
    answer = add(web, typed)
    assert answer.status_code == 422 and "JimmyHQ itself is reached by" in \
        answer.json()["detail"]
    assert tape(core, "apps.local_address_added") == []


def test_the_names_the_owner_and_the_door_reach_jimmyhq_by_are_never_added(
        web, core, imagery, network):
    core.settings.set("access.allowed_hosts", [TAILNET], actor="owner")
    core.settings.set("door.allowed_hosts", ["door.example.net"], actor="owner")
    core.settings.set("door.public_url", "https://tunnel.example.net/mcp", actor="owner")
    network.resolver.table |= {"door.example.net": [HOME], "tunnel.example.net": [HOME]}
    for typed in (f"https://{TAILNET}:443", f"http://{TAILNET}:8000",
                  "https://door.example.net:443", "http://tunnel.example.net:9000"):
        answer = add(web, typed)
        assert answer.status_code == 422 and "reached by" in answer.json()["detail"], typed


def test_a_name_that_becomes_one_jimmyhq_is_reached_by_is_no_longer_reached(
        web, bare, core, imagery, network):
    assert add(web, f"http://{TAILNET}:8000").status_code == 200
    core.settings.set("access.allowed_hosts", [TAILNET], actor="owner")
    error = fetch(bare, core, f"http://{TAILNET}:8000/v1/images")["error"]
    assert error["code"] == "not_allowed" and network.transport.seen == []


def test_a_port_jimmyhq_listens_on_here_is_never_reached_on_this_computer(
        web, bare, core, imagery, network):
    assert add(web, "http://localhost:47001").status_code == 200
    safety.listening_here(47001)
    try:
        assert fetch(bare, core, "http://localhost:47001/")["error"]["code"] == "not_allowed"
        answer = add(web, "http://127.0.0.1:47001")
        assert answer.status_code == 422 and "listening" in answer.json()["detail"]
        # Another computer's port of that number is not JimmyHQ's.
        assert add(web, f"http://{SERVER}:47001").status_code == 200
    finally:
        safety.stopped_listening_here(47001)
    assert network.transport.seen == []


# --- every form of a metadata address ------------------------------------------------------------


@pytest.mark.parametrize("address", [
    "64:ff9b:1::a9fe:a9fe",  # a network's own NAT64, last 32 bits
    "64:ff9b:1:a9fe:fe:a9fe::",  # the same prefix, the address after 48 bits (RFC 6052)
    "64:ff9b:1:ff:a9:fea9:fe00:0",  # and after 56
    "fd20:ce::254",  # Google Cloud over IPv6
    "fd00:c1::a9fe:a9fe",  # Oracle Cloud over IPv6
    "fd00::5efe:a9fe:a9fe", "2001:db8::200:5efe:a9fe:a9fe",  # ISATAP
    "::ffff:0:a9fe:a9fe",  # "IPv4-translated" (SIIT)
    "fd12:3456::a9fe:1", "fd00::6464:64c8",  # a unique-local address carrying one
    "fec0::a9fe:a9fe", "100::a9fe:a9fe",  # site-local and discard-only, all the same
])
def test_every_form_that_carries_a_metadata_address_is_one(address):
    assert local_reach.is_metadata(local_reach.ipaddress.ip_address(address))


def test_a_network_s_own_nat64_form_of_metadata_is_never_added_or_reached(
        web, core, imagery, network):
    answer = add(web, "http://[64:ff9b:1::a9fe:a9fe]:80")
    assert answer.status_code == 422 and "keys and settings" in answer.json()["detail"]
    network.resolver.table["cloudy.example.com"] = ["fd20:ce::254"]
    assert add(web, "http://cloudy.example.com:80").status_code == 422
    assert tape(core, "apps.local_address_added") == []


# --- an allowance is for the kind of app it was given to -----------------------------------------


def _sandboxed(tmp_path, version):
    from apps_install_helpers import write_manifest  # noqa: PLC0415

    folder = full_app(tmp_path / version, "imagery", version=version)
    data = write_manifest(folder, tier="sandboxed")
    del data["server"]
    data |= {"screens": [{"id": "main", "label": "Imagery", "entry": "described"}],
             "tables": {"note": {"fields": {"title": {"type": "text"}}}},
             "views": [{"id": "all", "label": "All", "table": "note"}]}
    (folder / "jimmyhq.app.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def _update(core, folder):
    plan = update.plan_folder(core, "imagery", folder)
    update.confirm_update(core, plan["plan"], plan["fingerprint"],
                          typed_name=plan["app"]["name"])
    return plan


def test_an_update_of_the_same_tier_keeps_the_addresses_and_its_consent_names_them(
        web, bare, core, tmp_path, imagery, network):
    add(web, ORIGIN)
    plan = update.plan_folder(core, "imagery", full_app(tmp_path / "b", "imagery",
                                                        version="1.1.0"))
    assert plan["update"]["local_addresses"] == {"addresses": [ORIGIN], "kept": True}
    update.confirm_update(core, plan["plan"], plan["fingerprint"], typed_name="Imagery")
    assert bridge.find_app(core, "imagery").local_addresses == (ORIGIN,)
    assert fetch(bare, core, f"{ORIGIN}/v1/images")["ok"] is True


def test_an_update_that_changes_the_tier_takes_the_addresses_back_and_says_so(
        web, bare, core, tmp_path, imagery, network):
    add(web, ORIGIN)
    plan = _update(core, _sandboxed(tmp_path, "1.1.0"))
    assert plan["update"]["tier"] == ["full", "sandboxed"]
    assert plan["update"]["local_addresses"] == {"addresses": [ORIGIN], "kept": False}
    assert bridge.find_app(core, "imagery").local_addresses == ()
    assert web.get("/api/apps/imagery/local-addresses").json()["addresses"] == []
    assert fetch(bare, core, f"{ORIGIN}/v1/images")["ok"] is False
    # Back on the first tier, nothing comes back by itself: the owner adds it again.
    _update(core, full_app(tmp_path / "c", "imagery", version="1.2.0"))
    assert bridge.find_app(core, "imagery").local_addresses == ()


def test_a_rollback_keeps_the_addresses_only_to_a_version_of_the_same_tier(
        web, core, tmp_path, imagery, network):
    _update(core, full_app(tmp_path / "b", "imagery", version="1.1.0"))
    add(web, ORIGIN)
    versions = {v["version"]: v for v in install.detail(core, "imagery")["versions"]}
    assert versions["1.0.0"]["tier"] == "full"
    assert install.detail(core, "imagery")["local_addresses"] == [ORIGIN]
    update.rollback(core, "imagery", versions["1.0.0"]["name"])
    assert bridge.find_app(core, "imagery").local_addresses == (ORIGIN,)
    _update(core, _sandboxed(tmp_path, "1.2.0"))
    add(web, ORIGIN)
    full = next(v for v in install.detail(core, "imagery")["versions"]
                if v["version"] == "1.0.0")
    update.rollback(core, "imagery", full["name"])
    assert store.get(core, "imagery")["tier"] == "full"
    assert bridge.find_app(core, "imagery").local_addresses == ()


# --- what an app may send, and how much at once --------------------------------------------------


@pytest.mark.parametrize("name", ["X-Forwarded-For", "x-forwarded-host", "X-Forwarded-Proto",
                                  "Forwarded", "X-Real-IP"])
def test_an_app_never_sends_headers_that_say_where_a_request_came_from(
        web, bare, core, tmp_path, imagery, network, name):
    add(web, ORIGIN)
    install_full(core, tmp_path / "webby", "webby", permissions=["net:images.example.com"])
    with pytest.raises(bridge.BridgeError) as caught:
        bridge.fetch_headers({name: "192.0.2.99"})
    assert caught.value.code == "invalid" and name in caught.value.message
    for app_id, url in (("imagery", f"{ORIGIN}/v1/images"),
                        ("webby", "https://images.example.com/")):
        error = program(bare, core, app_id).call("net.fetch", {
            "url": url, "headers": {name: "x"}})["error"]
        assert error["code"] == "invalid" and "Nothing was sent" in error["message"]
    assert network.transport.seen == []


def test_an_app_has_only_so_many_fetches_open_at_once_and_a_finished_one_frees_its_place(
        web, bare, core, tmp_path, imagery, network):
    add(web, ORIGIN)
    assert core.settings.get("apps.net_fetch_at_once") == 3
    core.settings.set("apps.net_fetch_at_once", 2, actor="owner")
    for _ in range(2):
        bridge.fetch_begins(core, "imagery")
    try:
        error = fetch(bare, core, f"{ORIGIN}/v1/images")["error"]
        assert error["code"] == "quota" and "apps.net_fetch_at_once" in error["message"]
        assert network.transport.seen == []
        install_full(core, tmp_path / "other", "posty")
        add(web, ORIGIN, "posty")
        assert fetch(bare, core, f"{ORIGIN}/v1/images", "posty")["ok"] is True  # its own
    finally:
        for _ in range(2):
            bridge.fetch_ends(core, "imagery")
    assert fetch(bare, core, f"{ORIGIN}/v1/images")["ok"] is True
    assert fetch(bare, core, f"http://{SERVER}:22/")["ok"] is False
    assert fetch(bare, core, f"{ORIGIN}/missing")["ok"] is True  # a 404 is an answer
    assert bridge._FETCHING == {}  # noqa: SLF001 - every one, done or refused, let go
