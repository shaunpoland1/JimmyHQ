"""The manifest and the code agree, every setting has words an owner can read, and the app is
on the lists that offer it."""

from __future__ import annotations

import json
import re
from pathlib import Path

from heard import config, program

from jimmyhq.apps import dev, install

APP = Path(config.MANIFEST).parent
REPO = APP.parents[1]
CODE = APP / "heard"


def manifest() -> dict:
    return json.loads(config.MANIFEST.read_text(encoding="utf-8"))


def test_the_manifest_passes_the_platforms_own_check_with_no_finding():
    checked, findings = dev.check_manifest(APP)
    assert findings == [] and checked is not None
    assert checked.private is True and checked.tier == "full" and checked.version == "0.2.2"
    assert dev.check_python(APP, checked) == []


def test_the_app_asks_for_only_what_it_uses_and_takes_three_names_of_message_and_no_audio():
    m = manifest()
    assert sorted(m["permissions"]) == ["inbound", "jobs", "model.ask", "tasks.write"]
    assert [i["name"] for i in m["inbound"]] == ["conversation", "live", "transcript"]
    assert "net:" not in json.dumps(m["permissions"])


def test_every_setting_the_manifest_declares_is_read_by_the_code_and_every_one_read_is_declared():
    declared = {s["key"] for s in manifest()["settings"]}
    text = "\n".join(p.read_text(encoding="utf-8") for p in CODE.glob("*.py"))
    read = set(re.findall(r"cfg(?:\(\))?\.([a-z_]+)", text))
    assert read == declared, read ^ declared


def test_every_setting_has_words_the_owner_can_read_and_a_default_that_fits():
    for s in manifest()["settings"]:
        assert s["label"].strip() and s["help"].strip() and len(s["label"]) <= 60, s["key"]
        if s["type"] == "int":
            assert s["min"] <= s["default"] <= s["max"], s["key"]
    assert "—" not in config.MANIFEST.read_text(encoding="utf-8")  # no em dashes on screen


def test_the_defaults_are_the_ones_the_owner_was_told():
    values = config.defaults()
    assert values["gap_minutes"] == 5 and values["keep_days"] == 0
    assert values["model_reads"] is True and values["file_tasks"] is True
    assert values["skip_discarded"] is True


def test_the_settings_a_person_would_not_tune_are_behind_advanced_and_none_about_privacy_is():
    advanced = {s["key"] for s in manifest()["settings"] if s.get("advanced")}
    assert {"page_size", "search_limit", "model_chars", "summary_words",
            "quote_min_chars"} <= advanced
    assert not {"model_reads", "file_tasks", "keep_days", "gap_minutes",
                "skip_discarded"} & advanced


def test_no_page_or_program_text_uses_the_browsers_own_dialogs_or_an_em_dash():
    for path in (APP / "web").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(alert|confirm|prompt)\(", text), path.name
        assert "—" not in text, path.name
    for path in CODE.glob("*.py"):
        assert "—" not in path.read_text(encoding="utf-8"), path.name


def test_no_page_script_writes_markup_or_reaches_anywhere_but_the_program():
    for path in (APP / "web").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        for needle in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                       "eval(", "fetch(", "XMLHttpRequest", "localStorage", "sessionStorage",
                       "WebSocket", "window.open"):
            assert needle not in text, f"{path.name}: {needle}"


def test_the_app_is_one_of_the_first_party_apps_the_apps_screen_offers():
    shipped = {e["id"]: e["group"] for e in install.examples()}
    assert shipped["heard"] == "first_party"


def test_the_app_has_its_guide_beside_the_others():
    assert (REPO / "docs" / "heard.md").is_file()


def test_the_program_builds_with_its_inbound_names_its_job_and_its_calls(rig):
    assert program.build.__module__ == "heard.program"
    plugs = rig.app.plugs()
    assert plugs["inbound"] == ["conversation", "live", "transcript"]
    assert {"retention"} == {j["name"] for j in plugs["jobs"]}
    assert not plugs.get("tools") and not plugs.get("sources") and not plugs.get("decisions")


def test_the_docs_name_the_door_the_shapes_and_the_audio_promise():
    text = (REPO / "docs" / "heard.md").read_text(encoding="utf-8")
    for word in ("/api/in/heard/transcript", "/api/in/heard/conversation",
                 "/api/in/heard/live", "transcript_segments", "session_id", "uid",
                 "Audio is never kept", "never fetched", "Export everything",
                 "For a sender that can only be given an address", "Audio Bytes Webhook",
                 "Only accept this recorder id", "https://docs.omi.me/doc/developer/apps/Integrations"):
        assert word in text, word
    assert "—" not in text


def test_the_setup_steps_no_longer_tell_the_owner_to_run_a_relay():
    text = (REPO / "docs" / "heard.md").read_text(encoding="utf-8")
    setup = text.split("## Connect your recorder")[1].split("## The screens")[0]
    assert setup.lower().count("relay") == 1 and "there is no relay to run" in setup
    page = (APP / "web" / "views" / "settings.js").read_text(encoding="utf-8")
    assert "relay" not in page.lower()


def test_the_docs_say_what_omis_pages_confirmed_and_what_stays_unconfirmed():
    text = (REPO / "docs" / "heard.md").read_text(encoding="utf-8")
    assert "What Omi's pages confirm" in text and "Not confirmed" in text


def test_the_page_shows_a_refusal_as_the_program_wrote_it_with_nothing_stripped_or_added():
    for path in (APP / "web").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert "could not do that" not in text.lower(), path.name
        assert "LEAD_IN" not in text, path.name
