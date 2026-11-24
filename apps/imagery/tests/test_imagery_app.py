"""The app as a whole: its manifest agrees with its code, the hosts it asks for are exactly
the ones its services need, the one tool only reads, and nothing it writes holds a key."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from imagery import config, providers
from imagery.config import MANIFEST
from imagery.providers import gemini
from imagery_service import GEMINI_HOST, KEY, OWN_HOST
from jimmyhq_app import CallError

from jimmyhq.apps import dev, install
from jimmyhq.apps import permissions as perms

APP = Path(MANIFEST).parent
CODE = APP / "imagery"
REPO = APP.parents[1]


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


# --- the manifest ----------------------------------------------------------------------


def test_the_manifest_passes_the_platforms_own_check_with_no_finding():
    parsed, findings = dev.check_manifest(APP)
    assert findings == [] and parsed is not None
    assert parsed.tier == "full" and parsed.private is False
    assert dev.check_python(APP, parsed) == []


def test_the_app_is_one_of_the_first_party_apps_the_apps_screen_offers():
    shipped = {e["id"]: e["group"] for e in install.examples()}
    assert shipped["imagery"] == "first_party"


def test_the_app_is_at_version_0_2_0_and_asks_for_agent_tools_and_one_outside_host():
    m = manifest()
    assert m["id"] == "imagery" and m["version"] == "0.2.3" and m["tier"] == "full"
    assert sorted(m["permissions"]) == ["agents.tools", f"net:{GEMINI_HOST}"]
    assert m["owner_hosts"]["max"] >= 1
    assert [t["name"] for t in m["tools"]] == ["recent"]
    assert [t["effect"] for t in m["tools"]] == ["read"]
    assert "feeds" not in m and "memory_sources" not in m and "cards" not in m


def test_the_hosts_the_manifest_asks_for_are_exactly_the_fixed_ones_the_services_declare():
    asked = {p.removeprefix("net:") for p in manifest()["permissions"] if p.startswith("net:")}

    class Cfg:
        openai_base = ""
        gemini_model = "m"

    made = providers.made(object())
    fixed = {h for p in made.values() if p.id != "openai_compatible" for h in p.hosts(Cfg())}
    assert asked == fixed == {GEMINI_HOST}
    assert made["practice"].hosts(Cfg()) == () and made["practice"].outside is False
    assert made["gemini"].outside and made["openai_compatible"].outside


def test_your_own_server_asks_for_whatever_host_the_owner_typed_and_nothing_fixed():
    class Cfg:
        openai_base = f"https://{OWN_HOST}/v1"

    own = providers.made(object())["openai_compatible"]
    assert own.hosts(Cfg()) == (OWN_HOST,)
    Cfg.openai_base = "not an address"
    assert own.hosts(Cfg()) == ()


def test_the_manifest_tells_the_owner_at_install_what_gemini_may_send_and_how_long_it_may_take():
    expects = manifest()["net_expects"]
    assert list(expects) == [GEMINI_HOST]
    assert expects[GEMINI_HOST] == {"max_kb": gemini.EXPECT_KB, "timeout_s": gemini.EXPECT_S}
    assert (gemini.EXPECT_KB, gemini.EXPECT_S) == (20 * 1024, 180)
    parsed, _ = dev.check_manifest(APP)
    lines = {line.id: line.sentence for line in perms.describe(parsed)}
    assert "answers of up to 20 MB and to wait up to 3 minutes" in lines[f"net:{GEMINI_HOST}"]


def test_the_three_secrets_are_gemini_one_per_public_host_and_one_for_the_own_network():
    secrets = {s["name"]: s for s in manifest()["secrets"]}
    assert set(secrets) == {config.GEMINI_SECRET, config.COMPATIBLE_SECRET,
                            config.OWN_NETWORK_SECRET}
    assert secrets[config.COMPATIBLE_SECRET]["for_each"] == "owner_host"
    assert "for_each" not in secrets[config.GEMINI_SECRET]
    assert "for_each" not in secrets[config.OWN_NETWORK_SECRET]
    assert gemini.SECRET == config.GEMINI_SECRET


def test_every_setting_the_manifest_declares_is_read_by_the_code_and_every_one_read_is_declared():
    declared = {s["key"] for s in manifest()["settings"]}
    text = "\n".join(p.read_text(encoding="utf-8") for p in CODE.rglob("*.py"))
    read = set(re.findall(r"cfg(?:\(\))?\.([a-z0-9_]+)", text))
    assert read == declared, (read ^ declared)


def test_every_setting_has_words_the_owner_can_read_and_a_default_that_fits():
    for s in manifest()["settings"]:
        assert s["label"].strip() and s["help"].strip() and len(s["label"]) <= 60, s["key"]
        if s["type"] == "int":
            assert s["min"] <= s["default"] <= s["max"], s["key"]
    values = config.defaults()
    assert values["provider"] == "practice"
    assert (values["max_count"], values["daily_limit"]) == (4, 20)
    assert values["max_pictures"] == 300 and values["max_total_mb"] == 400
    assert values["max_total_mb"] <= manifest()["storage_mb"]


def test_the_settings_a_person_would_not_tune_are_kept_behind_advanced_and_none_about_money_is():
    advanced = {s["key"] for s in manifest()["settings"] if s.get("advanced")}
    assert {"prompt_max_chars", "timeout_s", "reference_max_mb", "page_size",
            "openai_ask_base64"} <= advanced
    assert not {"provider", "daily_limit", "max_count", "max_pictures", "max_total_mb",
                "gemini_model"} & advanced


def test_only_gemini_knows_googles_address_and_only_the_server_one_knows_its_endpoint():
    for path in CODE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name != "gemini.py":
            assert "generativelanguage" not in text.replace(
                "generativelanguage.googleapis.com\"", ""), path.name
        if path.name != "openai_compatible.py":
            assert "images/generations" not in text, path.name


def test_no_code_uses_the_browsers_own_dialogs():
    for path in (APP / "web").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(alert|confirm|prompt)\s*\(", text), path.name


def test_the_page_never_builds_html_from_text():
    for path in (APP / "web").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert "innerHTML" not in text and "insertAdjacentHTML" not in text, path.name


# --- the tool --------------------------------------------------------------------------


def test_the_one_tool_lists_recent_pictures_newest_first_in_the_shown_form(rig):
    rig.make("A red kite over a hill", style="Photo", aspect="3:2")
    rig.make("A blue boat")
    answer = rig.core.tool("recent", {})
    assert answer["ok"] is True and answer["result"] == "2 pictures."
    shown = answer["shown"]
    assert shown.index("A blue boat") < shown.index("A red kite over a hill")
    assert "practice" in shown and "shape 3:2" in shown and "style Photo" in shown
    assert "UTC" in shown


def test_the_tool_gives_no_file_name_key_or_address_and_leaves_out_the_owners_own_pictures(rig):
    rig.make("A red kite")
    given = rig.core.upload(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, "mine-secret-name.png")
    rig.core.call("reference.add", {"ref": given["ref"], "name": "mine-secret-name.png"})
    shown = rig.core.tool("recent", {})["shown"]
    assert "uploads/" not in shown and "mine-secret-name" not in shown and "http" not in shown
    assert rig.core.tool("recent", {})["result"] == "1 pictures."


def test_the_tool_cuts_a_long_list_and_says_how_many_were_left_out(rig):
    for n in range(4):
        rig.make(f"Picture {n}")
    shown = rig.core.tool("recent", {"limit": 2})["shown"]
    assert "(2 older not shown." in shown


def test_the_tool_refuses_a_limit_that_makes_no_sense_in_a_sentence(rig):
    for bad in (0, 26, "many"):
        answer = rig.core.tool("recent", {"limit": bad})
        assert answer["ok"] is False and "Nothing was read" in answer["shown"]


def test_the_tool_says_so_when_nothing_has_been_made(rig):
    assert "Nothing has been made yet." in rig.core.tool("recent", {})["shown"]


def test_no_tool_makes_a_picture_or_reaches_outside(rig):
    assert set(rig.app._tools) == {"recent"}
    rig.core.tool("recent", {})
    assert rig.core.fetched == []


# --- keys and the record ---------------------------------------------------------------


def test_a_key_never_reaches_the_record_the_page_the_logs_or_the_overview(make_rig, caplog):
    from imagery_rig import GEMINI_URL
    from imagery_service import gemini_error, gemini_image

    rig = make_rig(provider="gemini")
    rig.service.on(GEMINI_URL, gemini_image(), gemini_error(400, "INVALID_ARGUMENT", KEY))
    with caplog.at_level("DEBUG"):
        rig.make(count=2)
    assert KEY not in rig.wherever_it_could_be_written()
    assert KEY not in json.dumps(rig.overview())
    assert KEY not in json.dumps(rig.core.call("status", {}))
    assert KEY not in caplog.text
    assert KEY not in rig.core.tool("recent", {})["shown"]


def test_nothing_is_written_to_the_record_by_making_or_deleting_pictures(rig):
    rig.make("A kite", count=2)
    rig.core.call("picture.delete", {"id": rig.pictures()[0]["id"]})
    assert rig.core.events == [] and rig.core.cards == {} and rig.core.tasks == []


def test_a_call_that_is_not_known_is_refused(rig):
    with pytest.raises(CallError):
        rig.core.call("picture.print_money", {})


def test_the_settings_page_changes_take_effect_without_a_restart(rig):
    assert rig.overview()["provider"]["id"] == "practice"
    rig.core.change_setting("provider", "gemini")
    rig.core.change_setting("gemini_model", "gemini-3-pro-image")
    shown = rig.overview()["provider"]
    assert shown["id"] == "gemini" and shown["model"] == "gemini-3-pro-image"
    assert shown["supports_reference"] is True and shown["outside"] is True
