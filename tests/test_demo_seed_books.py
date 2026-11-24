"""The Books sample content builds a believable household through the app's own calls."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rig_module = _load("demo_books_rig", ROOT / "apps/books/tests/books_rig.py")
sys.path.insert(0, str(ROOT))
from scripts.demo_seed import books as sample  # noqa: E402

TODAY, NOW = rig_module.TODAY, rig_module.START


def _seeded(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    rig = rig_module.Rig(folder, settings={k: v for k, v in sample.SETTINGS.items()})
    sample.seed(lambda call, **args: rig.call(call, args), TODAY, NOW)
    return rig


def _readback(rig):
    over = rig.call("overview")
    reg = {a["name"]: [(r["date"], r["payee"], r["payment"], r["deposit"], r["category"])
                       for r in rig.rows(a["id"])] for a in over["accounts"]}
    return (over["accounts"], over["total"], reg, rig.call("reminders"), rig.call("debts"))


@pytest.fixture
def rigs(tmp_path):
    made = [_seeded(tmp_path, "one"), _seeded(tmp_path, "two")]
    yield made
    for one in made:
        one.ctx.close()


def test_the_household_has_accounts_and_enough_register_rows_to_fill_a_screen(rigs):
    rig = rigs[0]
    accounts = rig.call("overview")["accounts"]
    assert {a["kind"] for a in accounts} >= {"checking", "savings", "credit_card", "loan"}
    checking = next(a for a in accounts if a["name"] == "Everyday Checking")
    assert len(rig.rows(checking["id"])) >= 25


def test_some_bills_are_overdue_some_due_soon_and_some_later(rigs):
    listing = rigs[0].call("reminders")
    assert len(listing["items"]) >= 10
    standing = {r["standing"] for r in listing["items"]}
    assert {"overdue", "later"} <= standing and len(standing) >= 3, standing
    assert listing["due_now"] >= 2
    assert {r["kind"] for r in listing["items"]} == {"bill", "income", "transfer"}
    assert any(r["paid_other"] for r in listing["items"])
    assert not any(r["auto"] for r in listing["items"])


def test_the_debts_are_listed_with_balances_and_a_payoff_plan(rigs):
    debts = rigs[0].call("debts")
    assert len(debts["items"]) == 3 and all(d["known"] for d in debts["items"])
    assert len(debts["steps"]) == 3 and debts["problem"] == ""
    assert any(len(d["history"]) >= 3 for d in debts["items"])


def test_nothing_is_dated_after_the_next_few_weeks_or_before_the_accounts_open(rigs):
    rig = rigs[0]
    for acct in rig.call("overview")["accounts"]:
        assert all(r["date"] <= TODAY for r in rig.rows(acct["id"]))


def test_seeding_two_fresh_installs_the_same_day_gives_identical_read_backs(rigs):
    assert _readback(rigs[0]) == _readback(rigs[1])


def test_the_sample_names_nothing_but_invented_things():
    text = Path(sample.__file__).read_text(encoding="utf-8")
    assert "@" not in text and "http" not in text
