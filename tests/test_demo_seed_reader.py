"""The Reader sample content fills a fresh Reader through the app's own calls, the same way
twice, with nothing written outside them. Built on the app's own test rig and the SDK's
FakeCore: no server and no network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RIG_DIR = REPO / "apps" / "reader" / "tests"
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


rigs = _load("reader_rig", RIG_DIR / "reader_rig.py")
demo = _load("demo_seed_reader", REPO / "scripts" / "demo_seed" / "reader.py")


def seeded(folder: Path):
    folder.mkdir()
    rig = rigs.Rig(folder, settings=dict(demo.SETTINGS))
    demo.seed(lambda name, /, **args: rig.core.call(name, args), TODAY, NOW_MS)
    return rig



def _readback(rig) -> dict:
    return {"overview": rig.core.call("overview", {}),
            "articles": rig.core.call("articles.list", {})}


def test_reader_sample_fills_topics_and_pauses_one(tmp_path):
    rig = seeded(tmp_path / "a")
    try:
        topics = rig.store.topics()
        assert len(topics) == 5
        assert [t["paused"] for t in topics].count(1) == 1
        assert all(t["phrasings"] for t in topics)
    finally:
        rig.ctx.close()


def test_reader_sample_asks_nothing_of_the_web_or_a_model(tmp_path):
    rig = seeded(tmp_path / "a")
    try:
        assert rig.core.fetched == []
        assert rig.core.replies == []
        assert demo.SETTINGS == {}
    finally:
        rig.ctx.close()


def test_reader_sample_gives_the_same_read_back_twice(tmp_path):
    one, two = seeded(tmp_path / "a"), seeded(tmp_path / "b")
    try:
        assert _readback(one) == _readback(two)
        assert one.store.topics() == two.store.topics()
    finally:
        one.ctx.close()
        two.ctx.close()
