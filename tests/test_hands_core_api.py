"""The owner's API for integrations: what each route does and who may use it."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from jimmyhq import hands
from jimmyhq.hands import fake
from jimmyhq.web.app import create_app
from test_hands_core_fakes import ADDRESS, KEY, LEAKED, Clock, events, tape_text

PASSWORD = "correct horse battery"
BODY = {"id": "demo", "type": "fake", "label": "Demo"}
RULE = {"agent": "ada", "account": "notes", "action": "notes.list"}
CARD = "01JABCDEFGHJKMNPQRSTVWXYZ0"  # no request is tied to it: the owner gets a 404

# Every route, with a request that would be valid from the owner.
READS = [
    ("GET", "/api/hands/types", None),
    ("GET", "/api/hands/providers", None),
    ("GET", "/api/hands/accounts", None),
    ("GET", "/api/hands/accounts/notes/actions", None),
    ("GET", "/api/hands/accounts/notes/dependents", None),
    ("GET", "/api/hands/rules", None),
    ("GET", "/api/hands/runs", None),
]
# Owner only. A waiting request's values change nothing, but only the owner may read them.
WRITES = [
    ("GET", f"/api/hands/requests/by-card/{CARD}", None),
    ("POST", "/api/hands/providers", {"type": "fake", "label": "Second"}),
    ("PUT", "/api/hands/providers/demo", {"label": "Renamed"}),
    ("PUT", "/api/hands/providers/demo/key", {"key": KEY}),
    ("PUT", "/api/hands/providers/demo/secrets/header", {"key": "value-of-a-header"}),
    ("DELETE", "/api/hands/providers/demo/secrets/header", None),
    ("POST", "/api/hands/providers/demo/check", None),
    ("POST", "/api/hands/accounts", {"provider_id": "demo", "account": "other"}),
    ("POST", "/api/hands/accounts/other/finish", None),
    ("DELETE", "/api/hands/accounts/other/connect", None),
    ("POST", "/api/hands/accounts/notes/try", {"action": "notes.list"}),
    ("POST", "/api/hands/rules/preflight", RULE),
    ("PUT", "/api/hands/rules", RULE),
    ("DELETE", "/api/hands/rules?agent=ada&account=notes&action=notes.list", None),
    ("DELETE", "/api/hands/accounts/notes", None),
    ("DELETE", "/api/hands/providers/demo/key", None),
    ("DELETE", "/api/hands/providers/demo", None),
]


@pytest.fixture
def owner(core):
    fake.register()
    try:
        with TestClient(create_app(core), base_url="http://testserver") as client:
            assert client.post("/api/auth/setup",
                               json={"name": "Sam", "password": PASSWORD}).status_code == 200
            hands.hands(core).now = Clock()
            yield client
    finally:
        fake.unregister()


@pytest.fixture
def ready(owner):
    """A provider with a key and one connected account called ``notes``."""
    assert owner.post("/api/hands/providers", json=BODY).status_code == 201
    assert owner.put("/api/hands/providers/demo/key", json={"key": KEY}).status_code == 200
    r = owner.post("/api/hands/accounts", json={"provider_id": "demo", "account": "notes",
                                                "label": "Notes"})
    assert r.json()["status"] == "ready"
    return owner


def token(client, core, scopes):
    _, value = core.auth.create_token("wall", scopes)
    client.cookies.clear()
    return {"authorization": f"Bearer {value}"}


# --- access -----------------------------------------------------------------------------------


def test_every_route_under_hands_is_listed_in_these_tests(core, owner):
    listed = {(m, p.split("?")[0]) for m, p, _ in READS + WRITES}
    real = {(method.upper(), path) for path, item in owner.app.openapi()["paths"].items()
            if path.startswith("/api/hands") for method in item}
    assert len(real) == 24
    shaped = {(m, p.replace("/demo", "/{provider_id}").replace("/notes", "/{account}")
               .replace("/other", "/{account}").replace("/header", "/{name}")
               .replace(CARD, "{card_id}"))
              for m, p in listed}
    assert shaped == real


def test_nothing_under_hands_answers_without_signing_in(core, ready):
    ready.cookies.clear()
    for method, path, body in READS + WRITES:
        assert ready.request(method, path, json=body).status_code == 401, (method, path)


def test_a_read_token_can_list_and_can_change_nothing(core, ready):
    headers = token(ready, core, ["read"])
    for method, path, _body in READS:
        assert ready.request(method, path, headers=headers).status_code == 200, path
    for method, path, body in WRITES:
        r = ready.request(method, path, json=body, headers=headers)
        assert r.status_code == 403, (method, path)
    assert fake.world("demo").calls == []


def test_a_write_token_cannot_change_integrations_either(core, ready):
    headers = token(ready, core, ["read", "write"])
    for method, path, body in WRITES:
        r = ready.request(method, path, json=body, headers=headers)
        assert r.status_code == 403, (method, path)
    assert [p["label"] for p in ready.get("/api/hands/providers",
                                          headers=headers).json()["providers"]] == ["Demo"]


def test_the_owner_can_use_every_route(core, ready):
    for method, path, body in READS + WRITES:
        if (method, path) == ("DELETE", "/api/hands/providers/demo"):
            assert ready.delete("/api/hands/accounts/other").status_code == 200
        r = ready.request(method, path, json=body)
        # "other" connected at once, so there is nothing left to finish: a plain answer.
        fine = ((422,) if path.endswith("/finish")
                else (404,) if path.endswith(("/connect", CARD)) else (200, 201))
        assert r.status_code in fine, (method, path, r.text)


def test_a_read_token_cannot_force_a_fresh_list_of_actions(core, ready):
    ready.get("/api/hands/accounts/notes/actions")
    fake.world("demo").set_version("notes.list", "2")
    headers = token(ready, core, ["read"])
    seen = ready.get("/api/hands/accounts/notes/actions?refresh=true", headers=headers).json()
    assert {a["version"] for a in seen["actions"]} == {"1"}


# --- providers and keys -----------------------------------------------------------------------


def test_a_provider_is_saved_listed_changed_and_removed(core, owner):
    assert [t["type"] for t in owner.get("/api/hands/types").json()["types"]] == [
        "agentmail", "composio", "fake", "mcp"]
    made = owner.post("/api/hands/providers", json={"type": "fake", "label": "My Demo"})
    assert made.status_code == 201
    assert made.json()["provider"] == {
        "id": "my-demo", "type": "fake", "label": "My Demo", "base_url": None,
        "enabled": True, "options": {}, "has_key": False, "needs_key": True, "secrets": [],
        "type_known": True, "secrets_wanted": [],
        "health": {"ok": None, "checked_ms": None, "error": None}}
    assert owner.post("/api/hands/providers",
                      json={"id": "my-demo", "type": "fake", "label": "x"}).status_code == 409
    changed = owner.put("/api/hands/providers/my-demo", json={"enabled": False})
    assert changed.json()["provider"]["enabled"] is False
    assert changed.json()["provider"]["label"] == "My Demo"
    assert owner.delete("/api/hands/providers/my-demo").json() == {"ok": True}
    assert owner.get("/api/hands/providers").json()["providers"] == []
    assert [e.kind for e in events(core, "hands.provider_saved")
            + events(core, "hands.provider_removed")] == [
        "hands.provider_saved", "hands.provider_saved", "hands.provider_removed"]


def test_a_provider_of_an_unknown_type_or_with_a_secret_in_its_options_is_refused(owner):
    r = owner.post("/api/hands/providers", json={"type": "nope", "label": "x"})
    assert r.status_code == 422
    assert "The types are: agentmail, composio, fake, mcp" in r.json()["detail"]
    r = owner.post("/api/hands/providers",
                   json=BODY | {"options": {"header": f"Bearer {LEAKED}"}})
    assert r.status_code == 422 and "looks like a key" in r.json()["detail"]
    r = owner.post("/api/hands/providers", json=BODY | {"base_url": "ftp://files.example.com"})
    assert r.status_code == 422
    assert owner.put("/api/hands/providers/gone", json={"label": "x"}).status_code == 404


def test_a_key_never_comes_back_out_in_any_response_event_or_log(core, owner, caplog):
    with caplog.at_level(logging.DEBUG):
        owner.post("/api/hands/providers", json=BODY)
        said = [owner.put("/api/hands/providers/demo/key", json={"key": KEY}),
                owner.put("/api/hands/providers/demo/secrets/header", json={"key": LEAKED}),
                owner.get("/api/hands/providers"),
                owner.post("/api/hands/providers/demo/check"),
                owner.post("/api/hands/accounts", json={"provider_id": "demo",
                                                        "account": "notes"}),
                owner.get("/api/hands/accounts"),
                owner.get("/api/hands/accounts/notes/actions"),
                owner.put("/api/hands/rules", json=RULE),
                owner.get("/api/hands/rules"), owner.get("/api/hands/runs")]
    listed = owner.get("/api/hands/providers").json()["providers"][0]
    assert listed["has_key"] is True and listed["secrets"] == ["header"]
    assert fake.world("demo").keys_seen[-1] == KEY  # the adapter is handed it
    for r in said:
        assert r.status_code in (200, 201) and KEY not in r.text and LEAKED not in r.text
    assert KEY not in tape_text(core) and LEAKED not in tape_text(core)
    assert KEY not in caplog.text and LEAKED not in caplog.text
    assert owner.delete("/api/hands/providers/demo/key").json()["has_key"] is False
    assert core.secrets.get("hands.demo.key") is None
    assert core.secrets.get("hands.demo.header") == LEAKED


def test_a_key_with_spaces_or_a_strange_secret_name_is_refused(owner):
    owner.post("/api/hands/providers", json=BODY)
    assert owner.put("/api/hands/providers/demo/key", json={"key": "a b"}).status_code == 422
    assert owner.put("/api/hands/providers/demo/secrets/key",
                     json={"key": "x"}).status_code == 422
    assert owner.put("/api/hands/providers/demo/secrets/Bad.Name",
                     json={"key": "x"}).status_code == 404


def test_a_health_check_says_whether_the_provider_answers(owner):
    owner.post("/api/hands/providers", json=BODY)
    no_key = owner.post("/api/hands/providers/demo/check").json()
    assert no_key["ok"] is False and "no key" in no_key["error"]
    owner.put("/api/hands/providers/demo/key", json={"key": KEY})
    assert owner.post("/api/hands/providers/demo/check").json()["ok"] is True
    fake.world("demo").healthy = False
    assert owner.post("/api/hands/providers/demo/check").json()["ok"] is False
    health = owner.get("/api/hands/providers").json()["providers"][0]["health"]
    assert health["ok"] is False and health["error"] == "The fake is unwell."


# --- accounts ---------------------------------------------------------------------------------


def test_connecting_answers_needs_key_until_the_provider_has_one(owner):
    owner.post("/api/hands/providers", json=BODY)
    r = owner.post("/api/hands/accounts", json={"provider_id": "demo", "account": "notes"})
    assert r.json() == {"status": "needs_key", "url": None, "account": None}
    assert owner.get("/api/hands/accounts").json()["accounts"] == []


def test_connecting_with_a_key_is_ready_and_the_tape_holds_ids_and_labels_only(core, ready):
    [account] = ready.get("/api/hands/accounts").json()["accounts"]
    assert account["account"] == "notes" and account["label"] == "Notes"
    assert account["provider_label"] == "Demo" and account["connected"] is True
    assert account["receives"] is True
    [event] = events(core, "hands.account_connected")
    assert event.payload == {"account": "notes", "provider_id": "demo", "label": "Notes",
                             "app": None, "own_inbox": False}
    assert "acct-1" not in tape_text(core)  # the provider's reference may be an address


def test_connecting_by_redirect_gives_a_page_to_open_then_waits_for_the_account(core, ready):
    world = fake.world("demo")
    world.connect = "redirect"
    r = ready.post("/api/hands/accounts", json={"provider_id": "demo", "account": "cal",
                                                "label": "Calendar", "app": "calendar"})
    assert r.json() == {"status": "redirect", "url": fake.CONSENT_URL, "account": None}
    assert [c["account"] for c in ready.get("/api/hands/accounts").json()["connecting"]] == [
        "cal"]
    assert ready.post("/api/hands/accounts/cal/finish").json()["status"] == "waiting"
    world.new_account("calendar")  # the owner approved it on the provider's page
    done = ready.post("/api/hands/accounts/cal/finish").json()
    assert done["status"] == "ready" and done["account"]["account"] == "cal"
    assert hands.hands(core).store.ref("cal") == "acct-2"  # not the one already connected
    assert ready.get("/api/hands/accounts").json()["connecting"] == []
    assert ready.post("/api/hands/accounts/cal/finish").status_code == 422


def test_an_account_name_must_be_a_new_plain_slug(ready):
    for bad in ("Notes", "9lives", "has-dash", "notes"):
        r = ready.post("/api/hands/accounts", json={"provider_id": "demo", "account": bad})
        assert r.status_code == 422, bad
    r = ready.post("/api/hands/accounts", json={"provider_id": "gone", "account": "x"})
    assert r.status_code == 404


def test_a_provider_with_an_account_cannot_be_removed_until_the_account_is_forgotten(ready):
    r = ready.delete("/api/hands/providers/demo")
    assert r.status_code == 409 and "Forget the account first" in r.json()["detail"]
    assert ready.delete("/api/hands/accounts/notes").json() == {"ok": True}
    assert ready.delete("/api/hands/accounts/notes").status_code == 404
    assert ready.delete("/api/hands/providers/demo").status_code == 200


def test_actions_are_listed_with_effect_where_it_came_from_and_whether_offerable(ready):
    actions = {a["name"]: a for a in
               ready.get("/api/hands/accounts/notes/actions").json()["actions"]}
    send = actions["messages.send"]
    assert (send["effect"], send["effect_from"], send["can_offer"]) == ("send", "adapter", True)
    assert send["tool"] == "notes_messages_send" and send["default_level"] == "ask"
    assert send["default_arguments"]["to"]["mode"] == "open"
    assert send["default_arguments"]["subject"]["mode"] == "hidden"
    assert send["limits_offered"] == {"per_day": True, "recipients": True, "max_amount": False}
    assert actions["things.poke"]["effect_from"] == "guess"
    assert actions["records.import"]["can_offer"] is False
    assert actions["notes.list"]["default_level"] == "alone"
    assert ready.get("/api/hands/accounts/gone/actions").status_code == 404


# --- rules ------------------------------------------------------------------------------------


def test_a_rule_is_set_listed_changed_and_removed(core, ready):
    made = ready.put("/api/hands/rules", json=RULE)
    assert made.status_code == 200
    rule = made.json()["rule"]
    assert (rule["tool"], rule["level"], rule["version"]) == ("notes_notes_list", "alone", "1")
    ready.put("/api/hands/rules", json=RULE | {"limits": {"per_day": 50},
                                               "arguments": {"query": {"mode": "open"}}})
    listed = ready.get("/api/hands/rules?agent=ada").json()
    assert listed["max_tools_per_agent"] == 12
    [shown] = listed["rules"]
    assert shown["limits"]["per_day"] == 50 and shown["arguments"]["query"]["mode"] == "open"
    assert (shown["state"], shown["reason"], shown["today"]) == ("on", None, 0)
    assert ready.get("/api/hands/rules?agent=bob").json()["rules"] == []
    gone = ready.delete("/api/hands/rules", params=RULE)
    assert gone.json() == {"ok": True}
    assert ready.delete("/api/hands/rules", params=RULE).status_code == 404
    assert [len(events(core, k)) for k in ("hands.rule_set", "hands.rule_removed")] == [2, 1]
    assert all(e.actor == "owner" for e in events(core, "hands.rule_set"))


def test_a_rule_the_core_will_not_allow_is_refused_with_a_sentence(ready):
    for change, words in (
        ({"action": "nope"}, "no such action"),
        ({"account": "gone"}, "no account called"),
        ({"agent": "Not An Id"}, "not an agent's id"),
        ({"level": "sometimes"}, "The level must be one of"),
        ({"arguments": {"nope": {"mode": "open"}}}, "no argument called"),
        ({"limits": {"per_day": 0}}, "daily limit"),
        ({"effect": "explode"}, "The effect must be one of"),
    ):
        r = ready.put("/api/hands/rules", json=RULE | change)
        assert r.status_code == 422 and words in r.json()["detail"], change
    assert ready.get("/api/hands/rules").json()["rules"] == []


# --- try it -----------------------------------------------------------------------------------


def test_try_it_runs_a_read_action_once_and_leaves_a_receipt(core, ready):
    fake.world("demo").data["notes.list"] = {"notes": [{"title": "Beans", "from": ADDRESS}]}
    r = ready.post("/api/hands/accounts/notes/try",
                   json={"action": "notes.list", "arguments": {"limit": 3}})
    tried = r.json()
    assert tried["ok"] is True and "Beans" in tried["text"] and ADDRESS not in tried["text"]
    assert fake.world("demo").calls[0]["arguments"] == {"limit": 3}
    [receipt] = events(core, "hand.called")
    assert receipt.actor == "owner"
    assert (receipt.payload["agent"], receipt.payload["level"]) == ("owner", "try")
    [run] = ready.get("/api/hands/runs").json()["runs"]
    assert run["id"] == tried["run_id"] and run["status"] == "done"
    assert "receipt" not in run and "ref" not in run and "key" not in run


def test_try_it_refuses_anything_that_is_not_known_to_only_read(ready):
    for action in ("messages.send", "payments.make", "things.poke", "status.peek"):
        r = ready.post("/api/hands/accounts/notes/try",
                       json={"action": action, "arguments": {"to": "x", "text": "y"}})
        assert r.status_code == 422 and "Nothing was tried" in r.json()["detail"], action
    r = ready.post("/api/hands/accounts/notes/try", json={"action": "notes.get"})
    assert r.status_code == 422 and "needs id" in r.json()["detail"]
    r = ready.post("/api/hands/accounts/notes/try",
                   json={"action": "notes.list", "arguments": {"nope": 1}})
    assert r.status_code == 422
    assert fake.world("demo").calls == []


def test_a_provider_that_breaks_during_a_request_is_a_502_with_no_detail_of_the_fault(ready):
    world = fake.world("demo")
    world.connect = "redirect"

    async def broken():
        raise RuntimeError(f"cannot reach {ADDRESS} with {LEAKED}")

    fake.FakeProvider.accounts, kept = (lambda self: broken()), fake.FakeProvider.accounts
    try:
        r = ready.post("/api/hands/accounts", json={"provider_id": "demo", "account": "cal"})
    finally:
        fake.FakeProvider.accounts = kept
    assert r.status_code == 502 and ADDRESS not in r.text and LEAKED not in r.text
