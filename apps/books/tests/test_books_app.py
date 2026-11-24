"""The app as a whole: its manifest, its settings, its store, and the platform's checks."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from books.config import MANIFEST, defaults
from books.store import SCHEMA_VERSION

from jimmyhq.apps import dev, install

APP = Path(MANIFEST).parent
CODE = APP / "books"
REPO = APP.parents[1]


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_the_manifest_passes_the_platforms_own_check_with_no_finding():
    found, findings = dev.check_manifest(APP)
    assert findings == [] and found is not None
    assert found.private is True and found.tier == "full"
    assert dev.check_python(APP, found) == []


def test_the_app_asks_only_for_its_job_and_its_card_and_promises_to_stay_off_the_record():
    m = manifest()
    assert m["id"] == "books" and m["version"] == "0.3.4" and m["private"] is True
    assert sorted(m["permissions"]) == ["bench.file", "jobs"]
    assert [c["kind"] for c in m["cards"]] == ["due"]
    for key in ("feeds", "tools", "memory_sources", "secrets", "roles", "owner_hosts"):
        assert key not in m


def test_the_app_is_one_of_the_first_party_apps_the_apps_screen_offers():
    shipped = {e["id"]: e["group"] for e in install.examples()}
    assert shipped["books"] == "first_party"


def test_every_setting_the_manifest_declares_is_read_by_the_code_and_every_one_read_is_declared():
    declared = {s["key"] for s in manifest()["settings"]}
    text = "\n".join(p.read_text(encoding="utf-8") for p in CODE.rglob("*.py"))
    read = set(re.findall(r"cfg(?:\(\))?\.([a-z_]+)", text))
    read |= set(re.findall(r'every_setting="([a-z_]+)"', text))
    assert read == declared, (read ^ declared)


def test_every_setting_has_words_the_owner_can_read_and_a_default_that_fits():
    for s in manifest()["settings"]:
        assert s["label"].strip() and s["help"].strip() and len(s["label"]) <= 60
        if s["type"] == "int":
            assert s["min"] <= s["default"] <= s["max"], s["key"]
    values = defaults()
    assert (values["due_days"], values["ahead_days"], values["tell_bills"]) == (14, 45, True)


def test_no_setting_about_money_or_privacy_is_kept_behind_advanced():
    advanced = {s["key"] for s in manifest()["settings"] if s.get("advanced")}
    assert advanced == {"register_rows", "check_minutes", "import_mb"}


def test_the_code_asks_no_model_and_uses_no_network_and_writes_nothing_on_the_record():
    text = "\n".join(p.read_text(encoding="utf-8") for p in CODE.rglob("*.py"))
    for call in ("app.model", "app.net", "app.web", "app.tape", "app.memory", "app.feeds",
                 "app.tasks", "app.vault", "app.work"):
        assert call not in text, call


def test_no_page_code_uses_the_browsers_own_dialogs_or_writes_html():
    for path in (APP / "web").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(alert|confirm|prompt)\s*\(", text), path.name
        assert "innerHTML" not in text and "insertAdjacentHTML" not in text, path.name


def test_every_call_the_page_makes_is_one_the_program_answers(rig):
    offered = set(rig.core.plugs["calls"])
    used = set()
    for path in (APP / "web").rglob("*.js"):
        used |= set(re.findall(r'call\("([a-z.]+)"', path.read_text(encoding="utf-8")))
    assert used and used <= offered, used - offered


def test_the_store_says_which_schema_it_has_and_refuses_one_from_the_future(rig):
    assert rig.store.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    path = rig.ctx.app.data_dir / "books.db"
    rig.ctx.close()
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="newer Books"):
        rig.ctx.store  # noqa: B018 - opening it is the test


def test_what_was_entered_is_kept_between_runs(rig):
    bank = rig.account()
    rig.add(bank, "2027-01-03", payment="12.50")
    rig.ctx.close()
    assert rig.rows(bank)[0]["payment"] == "12.50"


def test_a_failed_write_leaves_nothing_half_done(rig):
    bank = rig.account()
    save = rig.account("Example Savings", "savings", opening="0")
    real = rig.store.update

    def broken(table, row_id, **values):
        if "transfer_id" in values:
            raise RuntimeError("disk trouble")
        return real(table, row_id, **values)
    rig.store.update = broken
    with pytest.raises(Exception, match="disk trouble"):
        rig.call("transfer.add", {"from": bank, "to": save, "date": "2027-01-05",
                                  "amount": "10"})
    rig.store.update = real
    assert rig.store.all("SELECT * FROM txn") == []


def test_the_screen_is_nudged_when_something_changes_and_not_when_it_only_reads(rig):
    bank = rig.account()
    before = len(rig.core.emitted)
    rig.call("register", {"account": bank})
    assert len(rig.core.emitted) == before
    rig.add(bank, "2027-01-03", payment="1")
    assert len(rig.core.emitted) == before + 1
    assert rig.core.emitted[-1] == {"event": "books.changed", "data": None}
