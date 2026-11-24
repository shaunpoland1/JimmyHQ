"""The Packages sample content makes a believable set of parcels through the app's calls."""

from __future__ import annotations

import importlib.util
import re
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


sys.path.insert(0, str(ROOT / "apps/packages/tests"))  # the rig imports its stand-in
rig_module = _load("demo_packages_rig", ROOT / "apps/packages/tests/packages_rig.py")
sys.path.insert(0, str(ROOT))
from scripts.demo_seed import packages as sample  # noqa: E402

TODAY, NOW = "2027-01-15", rig_module.START


def _seeded(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    rig = rig_module.Rig(folder, tracker=str(sample.SETTINGS["tracker"]),
                         settings=dict(sample.SETTINGS))
    sample.seed(lambda call, **args: rig.core.call(call, args), TODAY, NOW)
    return rig


def _readback(rig):
    data = rig.core.call("overview", {})
    rows = [(r["label"], r["number"], r["status"], r["status_by"], r["carrier"],
             r["carrier_name"], r["event"], r["tab"]) for r in data["rows"]]
    return rows, data["badge"], data["carriers"], data["tracker"]


@pytest.fixture
def rigs(tmp_path):
    made = [_seeded(tmp_path, "one"), _seeded(tmp_path, "two")]
    yield made
    for one in made:
        one.ctx.close()


def test_the_packages_fill_a_screen_and_mix_every_kind_of_news(rigs):
    data = rigs[0].core.call("overview", {})
    rows = data["rows"]
    assert len(rows) == 12 and data["tracker"]["id"] == "practice"
    assert {r["status"] for r in rows} >= {"delivered", "on_its_way", "out_for_delivery",
                                           "problem", "pickup", "waiting"}
    assert data["badge"] == 3
    assert {r["tab"] for r in rows} == {"way", "delivered"}


def test_nothing_is_looked_up_and_nothing_is_put_on_the_bench(rigs):
    rig = rigs[0]
    assert rig.core.fetched == [] and rig.cards() == []


def test_every_status_is_the_owners_so_a_practice_check_leaves_the_screen_as_it_is(rigs):
    rig = rigs[0]
    before = _readback(rig)
    assert all(r[3] == "hand" for r in before[0])
    rig.clock.advance(rig_module.DAY)
    rig.sync()
    assert _readback(rig) == before


def test_carriers_are_saved_with_their_names(rigs):
    data = rigs[0].core.call("overview", {})
    names = {r["carrier_name"] for r in data["rows"]}
    assert {"Swift Parcel", "Northern Post", "Harbor Express"} <= names
    assert len(data["carriers"]) == 3


def test_seeding_two_fresh_installs_the_same_day_gives_identical_read_backs(rigs):
    assert _readback(rigs[0]) == _readback(rigs[1])


def test_the_sample_names_nothing_but_invented_things():
    text = Path(sample.__file__).read_text(encoding="utf-8")
    assert "@" not in text and "http" not in text
    assert all(re.fullmatch(r"ZX9001\d{5}QQ", n) for _, n, *_ in sample.PACKAGES)
