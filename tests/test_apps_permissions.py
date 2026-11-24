"""Permissions are data: one table gives each its tiers and its sentence, and the consent
dialog, the update check and the record of consent all read it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jimmyhq import modules
from jimmyhq.apps import kinds as app_kinds
from jimmyhq.apps import manifest as mf
from jimmyhq.apps import permissions as pm
from jimmyhq.apps.manifest import ManifestError, load, parse
from jimmyhq.apps.permissions import PermissionRefused, describe, diff, fingerprint
from jimmyhq.tape import registry

EXAMPLES = Path(__file__).resolve().parents[1] / "apps" / "examples"
HOST = "api.booking.example"


def water(**changes) -> dict:
    data = json.loads((EXAMPLES / "water" / mf.MANIFEST_FILE).read_text(encoding="utf-8"))
    return {**data, **changes}


def drafts(**changes) -> dict:
    data = json.loads((EXAMPLES / "drafts" / mf.MANIFEST_FILE).read_text(encoding="utf-8"))
    return {**data, **changes}


def dotted(*labels: str) -> str:
    """A host name built at run time, so no private-looking name sits in the repo."""
    return ".".join(labels)


# --- the table ------------------------------------------------------------------------------


def test_the_table_holds_exactly_the_designs_permissions_and_words():
    words = {p.id: p.sentence for p in pm.TABLE}
    assert words == {
        "always.data": "Keep its own data on this computer.",
        "tasks.read": "See your tasks.",
        "tasks.write": "Add tasks, and mark the ones it added as done.",
        "bench.file": "Put cards on your Bench for you to decide. It cannot decide for you.",
        "model.ask": "Ask your AI models, within your roles and limits, and show them "
                     "pictures from its own data. If a role uses a hosted model, what it "
                     "asks leaves this computer.",
        "web": "Search the web and read public pages, under your web lookup settings. What "
               "it searches for, and the addresses it reads, leave this computer.",
        "memory.recall": "Search what JimmyHQ remembers about you.",
        "speech.dictate": "Ask you to dictate: you see when it is recording, and it gets "
                          "only the words.",
        "net": "Send data to and read data from {host}.",
        "jobs": "Work in the background on a schedule.",
        "inbound": "Take messages from devices and services on your network, such as your "
                   "home's hub. Each one needs a key you make for it on the app's page.",
        "tape.read": "Read your whole record, not only its own events.",
        "feed": "Read the feed \"{feed}\" that the app \"{app}\" offers, and nothing else of "
                "its or on your record.",
        "memory.source": "Offer what it collects to Memory. You switch each source on yourself.",
        "agents.tools": "Offer tools to your agents. You choose which agents.",
        "always.full": "Runs as you on this computer. It can read and change your files and "
                       "use the network. JimmyHQ cannot limit this.",
    }


def test_seven_lines_are_for_full_apps_only_and_the_rest_are_for_both_tiers():
    full_only = {p.id for p in pm.TABLE if p.tiers == ("full",)}
    assert full_only == {"jobs", "tape.read", "feed", "memory.source", "agents.tools",
                         "inbound", "always.full"}
    assert all(p.tiers == pm.TIERS for p in pm.TABLE if p.id not in full_only)


def test_only_the_network_and_feed_permissions_name_something_and_only_the_warning_is_apart():
    assert [(p.id, p.param) for p in pm.TABLE if p.param] == [("feed", "feed"), ("net", "host")]
    assert [p.id for p in pm.TABLE if p.strong] == ["always.full"]
    assert [p.id for p in pm.TABLE if p.always] == ["always.full", "always.data"]
    assert set(pm.ASKABLE) == set(pm.BY_ID) - {"always.full", "always.data"}


# --- reading one permission -----------------------------------------------------------------


def test_a_permission_is_read_as_its_table_row_and_its_host():
    assert pm.parse_permission("tasks.read") == pm.Grant("tasks.read")
    grant = pm.parse_permission(f"net:{HOST}")
    assert (grant.permission, grant.host, grant.text) == ("net", HOST, f"net:{HOST}")


@pytest.mark.parametrize("text", ["", "root", "tasks", "TASKS.READ", " tasks.read", "tasks.read ",
                                  "tasks.*", "*", "net.fetch", "tasks.read,tasks.write"])
def test_an_unknown_permission_is_refused_with_the_ones_to_choose_from(text):
    with pytest.raises(PermissionRefused) as caught:
        pm.parse_permission(text)
    assert "tasks.read" in str(caught.value) and "net:<host>" in str(caught.value)


@pytest.mark.parametrize("text", ["always.full", "always.data"])
def test_an_app_cannot_ask_for_a_line_jimmyhq_adds_itself(text):
    with pytest.raises(PermissionRefused, match="Remove it"):
        pm.parse_permission(text)


def test_a_permission_that_takes_nothing_is_refused_with_something_after_a_colon():
    with pytest.raises(PermissionRefused, match='Write it as "tasks.read"'):
        pm.parse_permission("tasks.read:everything")


@pytest.mark.parametrize("value", [None, 5, True, ["tasks.read"], {"id": "tasks.read"}])
def test_a_permission_that_is_not_text_is_refused(value):
    with pytest.raises(PermissionRefused, match="must be text"):
        pm.parse_permission(value)


# --- hosts, from the attacker's side --------------------------------------------------------

GOOD_HOSTS = [HOST, "example.com", "a.b.c.example.org", "xn--bcher-kva.example",
              "api-2.example.net", "0.example.com", "example.xn--p1ai"]

BAD_HOSTS = [
    # wildcards
    "*", "*.example.com", "api.*.com", "example.*",
    # addresses and schemes
    "https://example.com", "example.com/path", "example.com?x=1", "user@example.com",
    "example.com#x", "//example.com",
    # ports and numeric addresses in every spelling
    "example.com:8443", "127.0.0.1", "192.0.2.7", "0x7f.0.0.1", "0x7f.0x0.0x0.0x1", "2130706433",
    "017700000001", "127.1", "[::1]", "::1", "[2001:db8::1]", "0.0.0.0", "1.2.3.4.5",
    # local and private names
    "localhost", "intranet", "printer", "router.", "example.com.",
    # shape
    "", " example.com", "example.com ", "EXAMPLE.com", "Example.Com", "-a.example.com",
    "a-.example.com", "a..example.com", ".example.com", "a_b.example.com", "exa mple.com",
    "b\u00fccher.example", "example.c", "example.123", "a" * 64 + ".example.com",
    ".".join(["a" * 60] * 5) + ".com", "example.com\n", "example.com\x00",
    "example.com,evil.example.com", "example.com evil.example.com",
]
LOCAL_SUFFIXES = ["localhost", "local", "localdomain", "lan", "home", "internal", "intranet",
                  "corp", "private", "arpa", "onion", "test", "invalid"]


@pytest.mark.parametrize("host", GOOD_HOSTS)
def test_one_exact_public_host_name_is_accepted(host):
    assert pm.check_host(host) == host
    assert pm.parse_permission(f"net:{host}").host == host


@pytest.mark.parametrize("host", BAD_HOSTS)
def test_a_host_that_is_not_one_exact_public_name_is_refused(host):
    with pytest.raises(PermissionRefused) as caught:
        pm.parse_permission(f"net:{host}")
    assert "one exact public host name" in str(caught.value)
    with pytest.raises(ManifestError):
        parse(water(permissions=[f"net:{host}"]))


@pytest.mark.parametrize("suffix", LOCAL_SUFFIXES)
def test_a_name_that_only_means_something_on_a_local_network_is_refused(suffix):
    for host in (dotted("printer", suffix), dotted("a", "printer", suffix)):
        with pytest.raises(PermissionRefused, match="local or private"):
            pm.check_host(host)


def test_private_overlay_and_reverse_lookup_names_are_refused():
    for host in (dotted("example-machine", "example-tailnet", "ts", "net"), dotted("ts", "net"),
                 dotted("1", "2", "0", "192", "in-addr", "arpa"),
                 dotted("gateway", "home", "arpa")):
        with pytest.raises(PermissionRefused, match="local or private"):
            pm.check_host(host)


def test_a_host_grant_covers_that_host_and_no_other():
    granted = [f"net:{HOST}", "tasks.read"]
    assert pm.allows(granted, "net", host=HOST)
    for other in ("booking.example", f"x.{HOST}", f"{HOST}.evil.example", HOST.upper(), ""):
        assert not pm.allows(granted, "net", host=other)
    assert not pm.allows(granted, "net") and not pm.allows(["tasks.read"], "net", host=HOST)
    assert pm.allows(granted, "tasks.read") and not pm.allows(granted, "tasks.write")
    assert pm.hosts([*granted, "net:a.example.com"]) == ("a.example.com", HOST)


# --- tiers ----------------------------------------------------------------------------------


@pytest.mark.parametrize("permission", ["jobs", "tape.read", "memory.source", "agents.tools"])
def test_a_sandboxed_app_is_refused_a_full_only_permission(permission):
    with pytest.raises(PermissionRefused, match="only for a full app"):
        pm.check_for_tier(pm.parse_permission(permission), "sandboxed")
    pm.check_for_tier(pm.parse_permission(permission), "full")


def test_checking_a_whole_list_refuses_repeats_and_too_many():
    assert pm.check_all(["tasks.read", f"net:{HOST}"], "sandboxed") == ("tasks.read", f"net:{HOST}")
    with pytest.raises(PermissionRefused, match="twice"):
        pm.check_all(["tasks.read", "tasks.read"], "sandboxed")
    with pytest.raises(PermissionRefused, match="at most 16 hosts"):
        pm.check_all([f"net:h{n}.example.com" for n in range(17)], "sandboxed")
    with pytest.raises(PermissionRefused, match="only for a full app"):
        pm.check_all(["jobs"], "sandboxed")


# --- the dialog -----------------------------------------------------------------------------


def test_a_sandboxed_app_that_asks_for_nothing_still_shows_that_it_keeps_its_own_data():
    lines = describe(load(EXAMPLES / "books_read"))
    assert [(x.id, x.sentence, x.always, x.strong) for x in lines] == [
        ("always.data", "Keep its own data on this computer.", True, False)]


def test_a_full_app_always_shows_the_warning_first_and_set_apart():
    lines = describe(load(EXAMPLES / "drafts"))
    assert lines[0].id == "always.full" and lines[0].strong and lines[0].always
    assert lines[0].sentence.startswith("Runs as you on this computer.")
    assert [x.id for x in lines] == [
        "always.full", "model.ask", "jobs", "bench.file", "always.data"]
    assert not any(x.strong for x in lines[1:])


def test_lines_run_from_most_to_least_sensitive_whatever_order_the_manifest_used():
    everything = ["tasks.read", "tasks.write", "bench.file", "jobs", "memory.source",
                  "agents.tools", "memory.recall", "model.ask", "net:b.example.com",
                  "net:a.example.com", "tape.read", "inbound"]
    expected = ["always.full", "tape.read", "net:a.example.com", "net:b.example.com", "model.ask",
                "memory.recall", "agents.tools", "memory.source", "inbound", "jobs",
                "bench.file",
                "tasks.write", "tasks.read", "always.data"]
    assert [x.id for x in pm.describe_permissions("full", everything)] == expected
    assert [x.id for x in pm.describe_permissions("full", reversed(everything))] == expected


def test_a_network_line_names_its_host_in_the_sentence():
    line = describe(parse(water(permissions=[f"net:{HOST}"])))[0]
    assert line.sentence == f"Send data to and read data from {HOST}."
    assert (line.id, line.permission, line.host) == (f"net:{HOST}", "net", HOST)
    assert line.as_dict() == {"id": f"net:{HOST}", "permission": "net", "host": HOST,
                              "sentence": line.sentence, "strong": False, "always": False}


def test_no_sentence_is_left_with_an_unfilled_gap():
    named = {"net": f"net:{HOST}", "feed": "feed:reader.articles"}
    lines = pm.describe_permissions("full", [named.get(p, p) for p in pm.ASKABLE])
    assert len(lines) == len(pm.TABLE)
    assert all("{" not in x.sentence and x.sentence.endswith(".") for x in lines)


def test_describing_refuses_a_permission_or_tier_that_was_never_checked():
    with pytest.raises(PermissionRefused):
        pm.describe_permissions("sandboxed", ["net:*"])
    with pytest.raises(ValueError):
        pm.describe_permissions("root", [])


# --- updates --------------------------------------------------------------------------------


def test_an_update_that_asks_for_nothing_new_needs_no_consent():
    old = parse(water())
    new = parse(water(version="1.1.0", name="Water two", summary="Count more."))
    change = diff(old, new)
    assert not change.needs_consent and change.ask == () and change.tier is None
    assert change.added == change.widened == change.removed == ()


def test_an_update_lists_exactly_the_permissions_it_adds():
    old = parse(water(permissions=["tasks.write"]))
    new = parse(water(permissions=["tasks.read", "tasks.write", f"net:{HOST}", "model.ask"]))
    change = diff(old, new)
    assert [x.id for x in change.added] == [f"net:{HOST}", "model.ask", "tasks.read"]
    assert change.ask == change.added and change.needs_consent and not change.removed


def test_naming_one_more_host_is_an_added_permission_and_swapping_a_host_is_too():
    old = parse(water(permissions=["net:a.example.com"]))
    more = diff(old, parse(water(permissions=["net:a.example.com", "net:b.example.com"])))
    assert [x.id for x in more.added] == ["net:b.example.com"] and not more.removed
    swap = diff(old, parse(water(permissions=["net:b.example.com"])))
    assert [x.id for x in swap.added] == ["net:b.example.com"]
    assert [x.id for x in swap.removed] == ["net:a.example.com"] and swap.needs_consent


def test_an_update_that_only_drops_permissions_needs_no_consent():
    old = parse(water(permissions=["tasks.read", "tasks.write"]))
    change = diff(old, parse(water(permissions=["tasks.read"])))
    assert [x.id for x in change.removed] == ["tasks.write"] and not change.needs_consent


def test_becoming_a_full_app_asks_again_and_shows_the_warning():
    old = parse(water(id="drafts"))
    change = diff(old, parse(drafts()))
    assert change.tier == ("sandboxed", "full") and change.needs_consent
    assert change.ask[0].id == "always.full" and change.ask[0].strong
    assert {x.id for x in change.added} == {"always.full", "model.ask", "jobs", "bench.file"}


def test_any_change_of_tier_asks_again_even_a_step_down():
    change = diff(parse(drafts()), parse(water(id="drafts")))
    assert change.tier == ("full", "sandboxed") and change.needs_consent
    assert "always.full" in {x.id for x in change.removed}


def test_dropping_the_private_promise_is_a_widening_the_owner_is_asked_about():
    private = parse(drafts(private=True))
    public = parse(drafts())
    change = diff(private, public)
    assert [x.id for x in change.widened] == ["private.off"] and change.needs_consent
    assert "private" in change.widened[0].sentence and change.ask == change.widened
    assert not diff(public, private).needs_consent


def test_the_change_is_ready_to_send_as_json():
    change = diff(parse(water()), parse(water(permissions=["tasks.write", "model.ask"])))
    out = json.loads(json.dumps(change.as_dict()))
    assert out["needs_consent"] is True and out["tier"] is None
    assert [x["id"] for x in out["ask"]] == ["model.ask"] and out["removed"] == []


def test_two_different_apps_are_never_compared():
    with pytest.raises(ValueError):
        diff(parse(water()), parse(drafts()))


# --- the record of consent ------------------------------------------------------------------


def test_the_fingerprint_is_stable_across_releases_of_jimmyhq():
    assert fingerprint(load(EXAMPLES / "water")) == (
        "e7376326ade48ee56479ecbf9f4e590d9f3a9273dc176c663cd0c03078c4ba77")
    assert fingerprint(load(EXAMPLES / "drafts")) == (
        "c47b06002d323c9d13e0e64674c898829fc888d46d34982964c8e85c83feb0ea")


def test_the_fingerprint_ignores_everything_the_owner_did_not_consent_to():
    base = fingerprint(parse(water(permissions=["tasks.read", "tasks.write"])))
    same = water(permissions=["tasks.write", "tasks.read"], version="2.0.0", name="Other",
                 summary="Other.", settings=[], author="Someone")
    assert fingerprint(parse(same)) == base


def test_the_fingerprint_changes_with_the_app_the_tier_a_permission_a_host_or_privacy():
    marks = {
        fingerprint(parse(water())),
        fingerprint(parse(water(id="water_two"))),
        fingerprint(parse(water(permissions=[]))),
        fingerprint(parse(water(permissions=["tasks.write", "net:a.example.com"]))),
        fingerprint(parse(water(permissions=["tasks.write", "net:b.example.com"]))),
        fingerprint(parse(drafts())),
        fingerprint(parse(drafts(private=True))),
        fingerprint(parse(drafts(id="water", permissions=["tasks.write"], cards=[]))),
    }
    assert len(marks) == 8 and all(len(m) == 64 for m in marks)


def test_a_diff_needs_consent_exactly_when_permissions_were_added_or_the_fingerprint_says_so():
    versions = [parse(water(permissions=p)) for p in (
        [], ["tasks.read"], ["tasks.read", "tasks.write"], [f"net:{HOST}"], ["tasks.write"])]
    for old in versions:
        for new in versions:
            change = diff(old, new)
            if fingerprint(old) == fingerprint(new):
                assert not change.needs_consent and not change.removed
            assert change.needs_consent == bool(set(new.permissions) - set(old.permissions))


# --- the Tape kinds -------------------------------------------------------------------------


def test_every_lifecycle_kind_is_registered_as_a_core_kind_with_an_example():
    modules.load()
    assert "apps" in modules.MODULES
    for name in (*app_kinds.LIFECYCLE, "apps.fetched"):
        assert registry.owner_of(name) == "core"
        example = registry.example_of(name)
        assert registry.validate_payload(name, 1, example)["app_id"] == "water"
    assert set(app_kinds.LIFECYCLE) == {
        "apps.installed", "apps.updated", "apps.permissions_granted", "apps.enabled",
        "apps.disabled", "apps.rolled_back", "apps.removed", "apps.host_added",
        "apps.host_removed", "apps.local_address_added", "apps.local_address_confirmed",
        "apps.local_address_removed"}


def test_the_installed_record_carries_what_the_design_lists():
    fields = set(app_kinds.AppInstalled.model_fields)
    assert {"app_id", "version", "tier", "source", "commit", "tree", "permissions",
            "fingerprint"} <= fields
    assert app_kinds.subject("water") == "app:water"


def test_an_installed_record_can_be_filled_from_a_manifest_and_its_fingerprint():
    manifest = load(EXAMPLES / "water")
    payload = registry.validate_payload("apps.installed", 1, {
        "app_id": manifest.id, "name": manifest.name, "version": manifest.version,
        "tier": manifest.tier, "source": "folder", "tree": "0" * 64,
        "permissions": list(manifest.permissions), "fingerprint": fingerprint(manifest)})
    assert payload["repo"] is None and payload["commit"] is None and payload["reviewed"] is False


def test_a_lifecycle_record_refuses_a_tier_or_field_it_does_not_know():
    example = registry.example_of("apps.installed")
    for bad in ({**example, "tier": "root"}, {**example, "password": "x"}):
        with pytest.raises(ValueError):
            registry.validate_payload("apps.installed", 1, bad)


def test_the_apps_module_adds_kinds_routes_and_a_worker_hook():
    import jimmyhq.apps as apps
    assert callable(apps.build) and callable(apps.jobs)


def test_a_private_app_is_not_said_to_send_what_it_asks_to_a_hosted_model():
    from types import SimpleNamespace

    from jimmyhq.apps import permissions

    def asks(private):
        return SimpleNamespace(id="notes", tier="full", permissions=["model.ask"],
                               private=private, owner_hosts=None)

    private = [x.sentence for x in permissions.describe(asks(True)) if x.permission == "model.ask"]
    public = [x.sentence for x in permissions.describe(asks(False)) if x.permission == "model.ask"]
    assert private == [permissions.PRIVATE_MODEL_SENTENCE]
    assert "hosted" not in private[0] and "own machines" in private[0]
    assert "hosted model" in public[0]
