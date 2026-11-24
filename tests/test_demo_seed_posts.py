"""The Posts sample content fills a fresh Posts through the app's own calls, the same way
twice, with nothing written outside them. Built on the app's own test rig and the SDK's
FakeCore: no server and no network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RIG_DIR = REPO / "apps" / "posts" / "tests"
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


rigs = _load("posts_rig", RIG_DIR / "posts_rig.py")
demo = _load("demo_seed_posts", REPO / "scripts" / "demo_seed" / "posts.py")


def seeded(folder: Path, start_pieces: bool = False):
    folder.mkdir()
    rig = rigs.Rig(folder, settings=dict(demo.SETTINGS))
    demo.seed(lambda name, /, **args: rig.core.call(name, args), TODAY, NOW_MS,
              start_pieces)
    return rig



def _readback(rig) -> dict:
    call = rig.core.call
    return {"pages": call("pages.list", {}), "rules": [call("rules.list", {"page_id": p["id"]})
                  for p in call("pages.list", {})["pages"]] + [call("rules.list", {})],
            "pieces": call("pieces.list", {})}


def test_posts_sample_fills_brands_and_rules(tmp_path):
    rig = seeded(tmp_path / "a")
    try:
        shown = _readback(rig)
        assert sorted(p["name"] for p in shown["pages"]["pages"]) == [
            "Corner Oven Bakery", "Hillside Community Garden", "Ridgeway Hiking Club"]
        assert all(p["samples"] and p["register"] for p in shown["pages"]["pages"])
        assert len(shown["rules"][-1]) == 1  # the one rule for every brand
        assert sum(len(r) for r in shown["rules"]) >= 7
        assert shown["pieces"] == []
    finally:
        rig.ctx._store.conn.close()  # noqa: SLF001


def test_posts_sample_asks_for_practice_and_sends_nothing(tmp_path):
    assert demo.SETTINGS == {"scheduler": "practice"}
    rig = seeded(tmp_path / "a")
    try:
        assert rig.core.fetched == []
        assert rig.core.replies == []
    finally:
        rig.ctx._store.conn.close()  # noqa: SLF001


def test_posts_sample_gives_the_same_read_back_twice(tmp_path):
    one, two = seeded(tmp_path / "a"), seeded(tmp_path / "b")
    try:
        assert _readback(one) == _readback(two)
    finally:
        one.ctx._store.conn.close()  # noqa: SLF001
        two.ctx._store.conn.close()  # noqa: SLF001


def test_posts_sample_can_start_three_pieces_when_asked(tmp_path):
    rig = seeded(tmp_path / "a", start_pieces=True)
    try:
        assert len(_readback(rig)["pieces"]) == 3
    finally:
        rig.ctx._store.conn.close()  # noqa: SLF001
