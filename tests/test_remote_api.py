"""Remote access over HTTP: the status, the Tailscale mapping on and off, and allowed hosts."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from jimmyhq.web.app import create_app
from test_remote_fakes import CLI, NAME, PORT, FakeTailscale, Ran, install, ready_status, served_doc

PASSWORD = "correct horse battery"
ON = [CLI, "serve", "--bg", f"--https={PORT}", f"http://127.0.0.1:{PORT}"]
OFF = [CLI, "serve", f"--https={PORT}", "off"]


@pytest.fixture
def cfg(cfg):
    return dataclasses.replace(cfg, port=PORT)


@pytest.fixture
def app(core):
    return create_app(core)


def signed_in(app, **options):
    client = TestClient(app, **options)
    if client.get("/api/auth/state").json()["setup_needed"]:
        made = client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD})
        assert made.status_code == 200
    else:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def here(app):
    """The owner at this computer, as a browser at http://127.0.0.1 would be."""
    with signed_in(app, base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 5000)) as c:
        yield c


@pytest.fixture
def away(app, here):
    """The owner on another device, reaching JimmyHQ by a name it already answers to."""
    with signed_in(app, base_url="http://testserver", client=("192.0.2.9", 5000)) as c:
        yield c


@pytest.fixture
def token(app, here, core):
    secret = core.auth.create_token("scout", ["read", "write"])[1]
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 5000),
                    headers={"authorization": f"Bearer {secret}"}) as c:
        yield c


def hosts(core):
    return core.settings.get("access.allowed_hosts")


def serve_events(core):
    return [e for e in core.tape.latest(limit=50) if e.kind == "remote.serve_changed"]


# --- reading ------------------------------------------------------------------------

def test_the_status_needs_the_owner_and_a_token_cannot_read_it(core, here, token, app):
    install(core, FakeTailscale())
    with TestClient(app, base_url="http://testserver") as stranger:
        assert stranger.get("/api/remote/status").status_code == 401
    assert token.get("/api/remote/status").status_code == 403
    assert here.get("/api/remote/status").status_code == 200


def test_the_status_of_a_ready_tailscale_has_the_name_the_commands_and_nothing_mapped(core, here):
    install(core, FakeTailscale())
    body = here.get("/api/remote/status").json()
    assert (body["state"], body["problem"], body["name"]) == ("ready", None, NAME)
    assert body["port"] == PORT and body["mapped"] is False and body["address"] is None
    assert body["allowed"] is False and body["from_this_computer"] is True
    assert body["https_enabled"] is True
    assert body["commands"]["on"] == f"tailscale serve --bg --https={PORT} http://127.0.0.1:{PORT}"
    assert body["commands"]["off"] == f"tailscale serve --https={PORT} off"
    assert body["commands"]["allowed_hosts_value"] == NAME


def test_the_status_says_when_the_request_is_not_from_this_computer(core, away):
    install(core, FakeTailscale())
    assert away.get("/api/remote/status").json()["from_this_computer"] is False


def test_a_request_through_a_proxy_on_this_computer_does_not_count_as_it(core, app, here):
    install(core, FakeTailscale())
    with signed_in(app, base_url="http://testserver", client=("127.0.0.1", 5000),
                   headers={"x-forwarded-for": "192.0.2.9"}) as proxied:
        assert proxied.get("/api/remote/status").json()["from_this_computer"] is False
        assert proxied.post("/api/remote/tailscale/serve").status_code == 403
        # Writing the machine's own name through the proxy does not get it any further:
        # a relayed request is not answered for a local name at all.
        local = {"host": f"127.0.0.1:{PORT}"}
        assert proxied.post("/api/remote/tailscale/serve", headers=local).status_code == 421


@pytest.mark.parametrize(("fake", "state"), [
    (FakeTailscale(status=Ran(1, "", "failed to connect to local tailscaled")), "not_running"),
    (FakeTailscale(status=ready_status(BackendState="NeedsLogin")), "needs_login"),
    (FakeTailscale(status=ready_status(BackendState="Stopped")), "stopped"),
    (FakeTailscale(status={"Nothing": 1}), "unknown"),
])
def test_each_state_that_cannot_turn_anything_on_says_what_to_do(core, here, fake, state):
    install(core, fake)
    body = here.get("/api/remote/status").json()
    assert body["state"] == state and body["name"] is None
    assert body["problem"] and body["problem"][0].isupper() and body["problem"].endswith(".")


def test_the_status_without_tailscale_says_so_and_runs_nothing(core, here):
    fake = install(core, FakeTailscale(), found=False)
    body = here.get("/api/remote/status").json()
    assert body["state"] == "not_installed" and "tailscale.com/download" in body["problem"]
    assert fake.calls == []


def test_the_status_finds_an_existing_mapping_and_its_address(core, here):
    install(core, FakeTailscale(served=served_doc((443, PORT))))
    body = here.get("/api/remote/status").json()
    assert body["mapped"] is True and body["address"] == f"https://{NAME}"
    assert body["public"] is False
    assert body["commands"]["off"] == "tailscale serve --https=443 off"


def test_the_status_warns_when_the_mapping_is_open_to_the_internet(core, here):
    install(core, FakeTailscale(served=served_doc((PORT, PORT), public=[PORT])))
    body = here.get("/api/remote/status").json()
    assert body["mapped"] is True and body["public"] is True
    assert body["address"] == f"https://{NAME}:{PORT}"


def test_the_status_knows_when_the_name_is_already_allowed(core, here):
    install(core, FakeTailscale())
    core.settings.set("access.allowed_hosts", [NAME.upper()], actor="owner")
    assert here.get("/api/remote/status").json()["allowed"] is True


def test_a_name_allowed_in_the_config_file_counts_as_allowed(core, app, here):
    install(core, FakeTailscale())
    core.config = dataclasses.replace(core.config, allowed_hosts=("testserver", NAME))
    assert here.get("/api/remote/status").json()["allowed"] is True
    assert here.post("/api/remote/allow-host").json()["added"] is False
    assert hosts(core) == []


# --- turning it on ------------------------------------------------------------------

def test_turning_on_runs_exactly_one_serve_command_and_allows_the_name(core, here):
    fake = install(core, FakeTailscale())
    reply = here.post("/api/remote/tailscale/serve")
    assert reply.status_code == 200
    assert fake.changes == [ON]
    body = reply.json()
    assert body["ran"] == f"tailscale serve --bg --https={PORT} http://127.0.0.1:{PORT}"
    assert body["allowed_added"] is True and hosts(core) == [NAME]
    assert body["status"]["mapped"] is True and body["status"]["address"] == f"https://{NAME}:{PORT}"
    assert body["status"]["allowed"] is True
    assert max(fake.timeouts) <= 30


def test_turning_on_leaves_one_event_with_the_port_and_no_name(core, here):
    install(core, FakeTailscale())
    here.post("/api/remote/tailscale/serve")
    [event] = serve_events(core)
    assert event.payload == {"on": True, "port": PORT} and event.actor == "owner"
    ours = [e.payload for e in core.tape.latest(limit=50) if e.kind.startswith("remote.")]
    assert NAME not in repr(ours)


def test_turning_on_twice_runs_nothing_the_second_time_and_does_not_repeat_the_name(core, here):
    fake = install(core, FakeTailscale())
    here.post("/api/remote/tailscale/serve")
    again = here.post("/api/remote/tailscale/serve")
    assert again.status_code == 200 and again.json()["ran"] is None
    assert again.json()["allowed_added"] is False
    assert fake.changes == [ON] and hosts(core) == [NAME] and len(serve_events(core)) == 1


def test_a_name_that_was_already_allowed_is_not_added_again_when_turning_on(core, here):
    install(core, FakeTailscale())
    core.settings.set("access.allowed_hosts", ["other.example.com", NAME], actor="owner")
    assert here.post("/api/remote/tailscale/serve").json()["allowed_added"] is False
    assert hosts(core) == ["other.example.com", NAME]


def test_turning_on_keeps_the_names_the_owner_already_had(core, here):
    install(core, FakeTailscale())
    core.settings.set("access.allowed_hosts", ["other.example.com"], actor="owner")
    here.post("/api/remote/tailscale/serve")
    assert hosts(core) == ["other.example.com", NAME]


def test_turning_on_from_another_device_is_refused_with_a_sentence_and_runs_nothing(core, away):
    fake = install(core, FakeTailscale())
    reply = away.post("/api/remote/tailscale/serve")
    assert reply.status_code == 403
    assert "this computer itself" in reply.json()["detail"]
    assert fake.calls == [] and hosts(core) == [] and serve_events(core) == []


def test_turning_on_with_a_token_is_refused_even_from_this_computer(core, token):
    fake = install(core, FakeTailscale())
    assert token.post("/api/remote/tailscale/serve").status_code == 403
    assert token.delete("/api/remote/tailscale/serve").status_code == 403
    assert fake.calls == []


def test_turning_on_without_signing_in_is_refused(core, app):
    fake = install(core, FakeTailscale())
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}",
                    client=("127.0.0.1", 5000)) as stranger:
        assert stranger.post("/api/remote/tailscale/serve").status_code == 401
    assert fake.calls == []


@pytest.mark.parametrize("fake", [
    FakeTailscale(status=Ran(1, "", "failed to connect to local tailscaled")),
    FakeTailscale(status=ready_status(BackendState="NeedsLogin")),
    FakeTailscale(status=ready_status(Self={})),
])
def test_turning_on_when_tailscale_is_not_ready_is_refused_and_changes_nothing(core, here, fake):
    install(core, fake)
    reply = here.post("/api/remote/tailscale/serve")
    assert reply.status_code == 409 and reply.json()["detail"]
    assert fake.changes == [] and hosts(core) == []


def test_turning_on_without_tailscale_installed_says_where_to_get_it(core, here):
    install(core, FakeTailscale(), found=False)
    reply = here.post("/api/remote/tailscale/serve")
    assert reply.status_code == 409 and "tailscale.com/download" in reply.json()["detail"]


def test_turning_on_will_not_replace_something_else_served_on_that_port(core, here):
    fake = install(core, FakeTailscale(served=served_doc((PORT, 3000))))
    reply = here.post("/api/remote/tailscale/serve")
    assert reply.status_code == 409 and "already serves something else" in reply.json()["detail"]
    assert fake.changes == [] and hosts(core) == []


def test_a_serve_command_that_fails_says_what_tailscale_said_and_changes_nothing_else(core, here):
    fake = install(core, FakeTailscale())
    fake.fail_change = Ran(1, "", "Serve is not enabled on your tailnet.")
    reply = here.post("/api/remote/tailscale/serve")
    assert reply.status_code == 502
    assert "Serve is not enabled on your tailnet." in reply.json()["detail"]
    assert hosts(core) == [] and serve_events(core) == []


def test_a_serve_command_that_waits_for_the_owner_to_consent_is_cut_off_with_advice(core, here):
    fake = install(core, FakeTailscale())
    fake.fail_change = Ran(None, "", "", timed_out=True)
    detail = here.post("/api/remote/tailscale/serve").json()["detail"]
    assert "run the command yourself in a terminal" in detail


def test_a_permission_failure_on_linux_says_to_set_the_operator(core, here):
    fake = install(core, FakeTailscale())
    fake.fail_change = Ran(1, "", "Access denied: serve config denied")
    assert "set --operator" in here.post("/api/remote/tailscale/serve").json()["detail"]


def test_turning_on_without_a_known_port_is_refused(core, here):
    install(core, FakeTailscale())
    core.config = dataclasses.replace(core.config, port=0)
    assert here.post("/api/remote/tailscale/serve").status_code == 409


# --- turning it off -----------------------------------------------------------------

def test_turning_off_runs_one_off_command_records_it_and_keeps_the_allowed_name(core, here):
    fake = install(core, FakeTailscale())
    here.post("/api/remote/tailscale/serve")
    reply = here.delete("/api/remote/tailscale/serve")
    assert reply.status_code == 200
    assert fake.changes == [ON, OFF]
    assert reply.json()["ran"] == f"tailscale serve --https={PORT} off"
    assert reply.json()["host_removed"] is False and hosts(core) == [NAME]
    assert reply.json()["status"]["mapped"] is False
    assert [e.payload for e in serve_events(core)] == [
        {"on": False, "port": PORT}, {"on": True, "port": PORT}]


def test_turning_off_can_also_take_the_name_out_when_asked(core, here):
    install(core, FakeTailscale())
    core.settings.set("access.allowed_hosts", ["other.example.com"], actor="owner")
    here.post("/api/remote/tailscale/serve")
    reply = here.delete("/api/remote/tailscale/serve?remove_host=true")
    assert reply.json()["host_removed"] is True and hosts(core) == ["other.example.com"]


def test_turning_off_uses_the_https_port_the_mapping_really_has(core, here):
    fake = install(core, FakeTailscale(served=served_doc((443, PORT))))
    here.delete("/api/remote/tailscale/serve")
    assert fake.changes == [[CLI, "serve", "--https=443", "off"]]


def test_turning_off_when_nothing_is_mapped_runs_nothing(core, here):
    fake = install(core, FakeTailscale())
    reply = here.delete("/api/remote/tailscale/serve")
    assert reply.status_code == 200 and reply.json()["ran"] is None
    assert fake.changes == [] and serve_events(core) == []


def test_turning_off_from_another_device_is_refused(core, away):
    fake = install(core, FakeTailscale(served=served_doc((PORT, PORT))))
    assert away.delete("/api/remote/tailscale/serve").status_code == 403
    assert fake.changes == []


def test_a_failed_turn_off_says_so_and_records_nothing(core, here):
    fake = install(core, FakeTailscale(served=served_doc((PORT, PORT))))
    fake.fail_change = Ran(1, "", "boom")
    reply = here.delete("/api/remote/tailscale/serve")
    assert reply.status_code == 502 and "turn it off" in reply.json()["detail"]
    assert serve_events(core) == []


# --- allowed hosts only -------------------------------------------------------------

def test_adding_the_name_touches_only_the_setting_and_works_from_anywhere(core, away):
    fake = install(core, FakeTailscale())
    reply = away.post("/api/remote/allow-host")
    assert reply.status_code == 200 and reply.json()["added"] is True
    assert hosts(core) == [NAME]
    assert fake.changes == [] and serve_events(core) == []


def test_adding_the_name_twice_leaves_it_in_the_list_once(core, here):
    install(core, FakeTailscale())
    here.post("/api/remote/allow-host")
    assert here.post("/api/remote/allow-host").json()["added"] is False
    assert hosts(core) == [NAME]


def test_adding_the_name_with_a_token_is_refused(core, token):
    install(core, FakeTailscale())
    assert token.post("/api/remote/allow-host").status_code == 403
    assert hosts(core) == []


def test_adding_the_name_when_tailscale_is_not_ready_says_what_to_do(core, here):
    install(core, FakeTailscale(status=ready_status(BackendState="Stopped")))
    reply = here.post("/api/remote/allow-host")
    assert reply.status_code == 409 and "turned off" in reply.json()["detail"]


def test_removing_the_name_takes_only_that_name_out(core, here):
    install(core, FakeTailscale())
    core.settings.set("access.allowed_hosts", ["other.example.com", NAME], actor="owner")
    assert here.delete("/api/remote/allow-host").json()["removed"] is True
    assert hosts(core) == ["other.example.com"]
    assert here.delete("/api/remote/allow-host").json()["removed"] is False


def test_the_allowed_name_is_then_answered_to_by_the_server(core, app, here):
    install(core, FakeTailscale())
    here.post("/api/remote/allow-host")
    with TestClient(app, base_url=f"https://{NAME}") as other:
        assert other.get("/api/health").status_code == 200
    with TestClient(app, base_url="https://not-allowed.example.com") as other:
        assert other.get("/api/health").status_code == 421


def test_adding_the_name_while_funnel_makes_the_address_public_is_refused(core, app, here):
    fake = install(core, FakeTailscale(served=served_doc((PORT, PORT), public=[PORT])))
    reply = here.post("/api/remote/allow-host")
    assert reply.status_code == 409
    detail = reply.json()["detail"]
    assert f"tailscale funnel --https={PORT} off" in detail and "Nothing was changed" in detail
    assert hosts(core) == [] and fake.changes == []
    with TestClient(app, base_url=f"https://{NAME}:{PORT}") as stranger:
        assert stranger.get("/api/health").status_code == 421


def test_turning_on_over_a_public_mapping_is_refused_and_adds_no_name(core, here):
    fake = install(core, FakeTailscale(served=served_doc((PORT, PORT), public=[PORT])))
    reply = here.post("/api/remote/tailscale/serve")
    assert reply.status_code == 409 and "tailscale funnel" in reply.json()["detail"]
    assert hosts(core) == [] and fake.changes == [] and serve_events(core) == []


def test_one_public_address_among_several_for_jimmyhqs_port_is_enough_to_refuse(core, app,
                                                                               here):
    # The first address for the port is private; a second one Funnel opens to everyone.
    served = served_doc((443, PORT), (PORT, PORT), public=[PORT])
    fake = install(core, FakeTailscale(served=served))
    body = here.get("/api/remote/status").json()
    assert body["mapped"] is True and body["public"] is True
    reply = here.post("/api/remote/allow-host")
    assert reply.status_code == 409
    assert f"tailscale funnel --https={PORT} off" in reply.json()["detail"]
    assert here.post("/api/remote/tailscale/serve").status_code == 409
    assert hosts(core) == [] and fake.changes == []


def test_a_mapping_that_is_not_public_can_still_have_its_name_added(core, here):
    install(core, FakeTailscale(served=served_doc((PORT, PORT))))
    assert here.post("/api/remote/allow-host").json()["added"] is True
    assert hosts(core) == [NAME]


# --- the line never crossed ---------------------------------------------------------

def test_nothing_the_module_runs_ever_opens_the_machine_to_the_public_internet(core, here, away):
    fake = install(core, FakeTailscale())
    here.post("/api/remote/tailscale/serve")
    here.get("/api/remote/status")
    here.post("/api/remote/allow-host")
    here.delete("/api/remote/tailscale/serve?remove_host=true")
    away.post("/api/remote/tailscale/serve")
    assert fake.calls
    for argv in fake.calls:
        assert "funnel" not in argv and not any("funnel" in part for part in argv[1:])
        assert argv[0] == CLI and all(isinstance(part, str) for part in argv)
