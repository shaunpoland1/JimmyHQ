"""The Articles sample content fills a fresh Articles through the app's own calls, the same way
twice, with nothing written outside them. Built on the app's own test rig and the SDK's
FakeCore: no server and no network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RIG_DIR = REPO / "apps" / "articles" / "tests"
TODAY = "2027-01-15"
NOW_MS = 1_800_000_000_000


def _load(name: str, path: Path):
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rigs = _load("articles_rig", RIG_DIR / "articles_rig.py")
demo = _load("demo_seed_articles", REPO / "scripts" / "demo_seed" / "articles.py")


def seeded(folder: Path, start_pieces: bool = False):
    folder.mkdir()
    rig = rigs.Rig(folder, settings=dict(demo.SETTINGS))
    demo.seed(lambda name, /, **args: rig.core.call(name, args), TODAY, NOW_MS,
              start_pieces)
    return rig



def _readback(rig) -> dict:
    call = rig.core.call
    return {"sites": call("sites.list", {}), "pieces": call("pieces.list", {}),
            "replies": call("replies.status", {})}


def test_articles_sample_fills_sites_and_replies(tmp_path):
    rig = seeded(tmp_path / "a")
    try:
        shown = _readback(rig)
        assert sorted(s["name"] for s in shown["sites"]["sites"]) == [
            "Crumb and Crust Journal", "The Garden Gate Blog"]
        assert shown["pieces"] == []
        assert shown["replies"]["replies"] == 5
    finally:
        rig.close()


def test_articles_sample_publishes_nothing_and_asks_nothing_outside(tmp_path):
    rig = seeded(tmp_path / "a")
    try:
        publishers = {s["publisher"] for s in rig.core.call("sites.list", {})["sites"]}
        assert publishers <= {"practice", "by_hand"}
        assert rig.core.fetched == []
        assert rig.core.replies == []
    finally:
        rig.close()


def test_articles_sample_gives_the_same_read_back_twice(tmp_path):
    one, two = seeded(tmp_path / "a"), seeded(tmp_path / "b")
    try:
        assert _readback(one) == _readback(two)
    finally:
        one.close()
        two.close()


def test_articles_sample_can_start_three_pieces_when_asked(tmp_path):
    rig = seeded(tmp_path / "a", start_pieces=True)
    try:
        assert len(_readback(rig)["pieces"]) == 3
    finally:
        rig.close()
