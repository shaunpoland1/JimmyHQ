"""The calls a page can reach are the same on the server, in the shell and in the SDK.

The shell's list (``web/src/modules/apps/lib/bridge.ts``) and the SDK's
(``sdk/js/bridge.js``) are TypeScript and JavaScript, so these tests read them as text.
A call the server answers but the shell does not list never leaves the browser, and a
permission the owner granted for it can then never be used."""

from __future__ import annotations

import re
from pathlib import Path

from jimmyhq.apps import bridge
from jimmyhq.apps.permissions import FULL

ROOT = Path(__file__).resolve().parents[1]
SHELL = (ROOT / "web" / "src" / "modules" / "apps" / "lib" / "bridge.ts").read_text("utf-8")
SDK = (ROOT / "sdk" / "js" / "bridge.js").read_text("utf-8")


def shell_rules() -> dict[str, dict[str, object]]:
    """Each call of the shell's ``CALLS`` with its rule: ``needs``, ``full``, ``shell``."""
    body = SHELL.split("export const CALLS", 1)[1].split("});", 1)[0]
    rules: dict[str, dict[str, object]] = {}
    for name, rule in re.findall(r'^\s*"([a-z]+\.[a-z_]+)":\s*\{(.*?)\},?\s*$', body, re.M):
        found: dict[str, object] = dict(re.findall(r'(\w+):\s*"([^"]+)"', rule))
        for flag in re.findall(r"(\w+):\s*true", rule):
            found[flag] = True
        rules[name] = found
    return rules


def sdk_calls() -> set[str]:
    """Every ``group.name`` of the SDK's ``CALLS``."""
    body = SDK.split("const CALLS = {", 1)[1].split("};", 1)[0]
    return {f"{group}.{name}"
            for group, names in re.findall(r"^\s*([a-z]+):\s*\[(.*?)\]", body, re.M)
            for name in re.findall(r'"([a-z_]+)"', names)}


def test_every_call_a_page_can_make_on_the_server_is_in_the_shells_list():
    rules = shell_rules()
    assert len(rules) > 20, "the shell's list was not read"
    missing = sorted(set(bridge.CALLS) - set(rules))
    assert missing == [], f"the shell never sends these to the server: {missing}"


def test_every_call_the_server_leaves_to_the_shell_is_in_the_shells_list_as_the_shells_own():
    rules = shell_rules()
    for name in bridge.SHELL_CALLS:
        assert rules.get(name, {}).get("shell") is True, name


def test_every_call_the_shell_sends_on_is_one_the_server_answers():
    rules = shell_rules()
    sent_on = {name for name, rule in rules.items() if not rule.get("shell")}
    assert sorted(sent_on - set(bridge.CALLS)) == []


def test_the_shell_asks_for_the_permission_and_tier_the_server_asks_for():
    rules = shell_rules()
    for name, call in bridge.CALLS.items():
        assert rules[name].get("needs") == call.permission, name
        assert bool(rules[name].get("full")) == (call.tiers == (FULL,)), name


def test_every_call_a_page_can_make_on_the_server_is_in_the_sdks_list():
    offered = sdk_calls()
    assert len(offered) > 20, "the SDK's list was not read"
    assert sorted(set(bridge.CALLS) - offered) == []


def test_the_sdk_offers_the_web_and_similarity_calls_and_the_hosts_list_a_page_may_use():
    assert {"web.search", "web.read", "model.embed", "hosts.list"} <= sdk_calls()
