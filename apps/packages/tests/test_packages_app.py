"""The app as a whole: its manifest and its settings agree with its code, its store keeps
what it should and deletes for good, and what its screen is told is complete."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from packages import status
from packages.config import MANIFEST, TELL_MODES, defaults
from packages.store import SCHEMA_VERSION
from packages_rig import HOSE, RAKE, START
from packages_service import ok, quota, registered, tracking

CODE = Path(MANIFEST).parent / "packages"


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


# --- the manifest ----------------------------------------------------------------------------


def test_the_app_asks_only_for_what_it_uses_and_promises_to_stay_off_the_record():
    m = manifest()
    assert m["id"] == "packages" and m["tier"] == "full" and m["version"] == "0.1.5"
    assert m["private"] is True
    assert sorted(m["permissions"]) == ["bench.file", "jobs", "net:api.17track.net"]
    assert [c["kind"] for c in m["cards"]] == ["update"]
    assert [s["name"] for s in m["secrets"]] == ["seventeentrack_key"]
    assert "feeds" not in m and "tools" not in m and "memory_sources" not in m


def test_every_setting_the_manifest_declares_is_read_by_the_code_and_every_one_read_is_declared():
    declared = {s["key"] for s in manifest()["settings"]}
    text = "\n".join(p.read_text(encoding="utf-8") for p in CODE.rglob("*.py"))
    read = set(re.findall(r"cfg(?:\(\))?\.([a-z_]+)", text))
    read |= set(re.findall(r'every_setting="([a-z_]+)"', text))  # a job's own schedule
    assert read == declared, (read ^ declared)


def test_every_setting_has_words_the_owner_can_read_and_a_default_that_fits():
    for s in manifest()["settings"]:
        assert s["label"].strip() and s["help"].strip() and len(s["label"]) <= 60
        if s["type"] == "int":
            assert s["min"] <= s["default"] <= s["max"], s["key"]
    values = defaults()
    assert values["tracker"] == "practice"
    assert (values["interval_hours"], values["daily_time"], values["retry_minutes"]) == (
        4, "06:00", 15)
    assert values["batch_size"] == 40 and values["tell_when"] == "delivered_or_problem"
    assert [o["value"] for o in next(
        s for s in manifest()["settings"] if s["key"] == "tell_when")["options"]] == list(
        TELL_MODES)


def test_the_settings_a_person_would_not_tune_are_kept_behind_advanced_and_none_about_money_is():
    advanced = {s["key"] for s in manifest()["settings"] if s.get("advanced")}
    assert {"batch_size", "timeout_s", "check_minutes", "pause_ms"} <= advanced
    assert not {"refresh_gap_minutes", "tell_when", "tracker", "delivered_days"} & advanced


def test_no_code_uses_the_browsers_own_dialogs():
    web = Path(MANIFEST).parent / "web"
    for path in web.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(alert|confirm|prompt)\s*\(", text), path.name


def test_no_other_file_than_the_one_tracker_knows_the_services_host_or_header_names():
    for path in CODE.rglob("*.py"):
        if path.name == "seventeentrack.py":
            continue
        text = path.read_text(encoding="utf-8")
        for word in ("17token", "api.17track.net", "gettrackinfo", "registerlist", "InTransit"):
            assert word not in text, f"{path.name} mentions {word}"


# --- the store -------------------------------------------------------------------------------


def test_the_store_says_which_schema_it_has_and_refuses_one_from_the_future(rig, tmp_path):
    assert rig.store.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    path = rig.ctx.app.data_dir / "packages.db"
    rig.ctx.close()
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="newer Packages"):
        rig.ctx.store  # noqa: B018 - opening it is the test


def test_packages_are_kept_between_runs_with_their_status_and_last_event(make_rig, tmp_path):
    rig = make_rig()
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "OutForDelivery")]))
    rig.add()
    rig.ctx.close()
    again = rig.store.package(1)
    assert (again["label"], again["number"], again["status"]) == (
        "Garden hose", HOSE, "out_for_delivery")


def test_a_removed_number_is_overwritten_in_the_file_not_only_unlinked(rig):
    rig.service.on("register", ok([registered(RAKE)]))
    rig.service.on("gettrackinfo", ok([tracking(RAKE, "InTransit")]))
    rig.add("Rake", RAKE)
    rig.ctx.store.conn.commit()
    raw = b"".join(p.read_bytes() for p in rig.ctx.app.data_dir.glob("packages.db*"))
    assert RAKE.encode() in raw
    rig.service.on("deletetrack", ok([{"number": RAKE, "carrier": 3011}]))
    rig.core.call("package.remove", {"id": 1})
    raw = b"".join(p.read_bytes() for p in rig.ctx.app.data_dir.glob("packages.db*"))
    assert RAKE.encode() not in raw


def test_the_vocabulary_has_words_and_a_card_sentence_for_every_status():
    assert set(status.WORDS) == set(status.STATUSES) == set(status.SENTENCES)
    assert status.LOUD == ("out_for_delivery", "problem")
    assert status.is_status("delivered") and not status.is_status("lost")


# --- what the screen is told ------------------------------------------------------------------


def test_the_overview_has_the_rows_the_tracker_the_schedule_and_the_sidebar_count(rig):
    rig.service.on("register", ok([registered(HOSE), registered(RAKE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "OutForDelivery"),
                                       tracking(RAKE, "Exception")]))
    rig.service.on("getquota", quota(100, 2))
    rig.add()
    rig.add("Rake", RAKE)
    rig.clock.advance(11 * 60_000)
    rig.core.call("refresh")
    seen = rig.core.call("overview")
    assert [r["words"] for r in seen["rows"]] == ["Out for delivery", "Problem"]
    assert seen["badge"] == 2 and {r["tab"] for r in seen["rows"]} == {"way"}
    assert seen["tracker"]["id"] == "seventeentrack" and seen["tracker"]["outside"] is True
    assert seen["schedule"]["interval_hours"] == 4 and seen["schedule"]["daily"] == "06:00"
    assert seen["schedule"]["next_ms"] > START
    assert seen["quota"]["left"] == 98
    assert [s["id"] for s in seen["statuses"]] == list(status.STATUSES)


def test_the_sidebar_counts_only_packages_out_for_delivery_or_with_a_problem(rig):
    rig.service.on("register", ok([registered(HOSE), registered(RAKE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit"),
                                       tracking(RAKE, "Delivered")]))
    rig.add()
    rig.add("Rake", RAKE)
    assert rig.core.call("overview")["badge"] == 0


def test_the_screen_is_nudged_when_something_changes(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    before = len(rig.core.emitted)
    rig.add()
    assert len(rig.core.emitted) > before
    assert {e["event"] for e in rig.core.emitted} == {"packages.changed"}
    assert HOSE not in json.dumps(rig.core.emitted)


def test_a_changed_setting_is_read_at_once(make_rig):
    rig = make_rig(settings={"tell_when": "off"})
    assert str(rig.ctx.cfg().tell_when) == "off"
    rig.core.change_setting("tell_when", "any_change")
    assert str(rig.ctx.cfg().tell_when) == "any_change"
