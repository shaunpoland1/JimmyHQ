"""The Imagery sample content fills a fresh Imagery through the app's own calls, the same way
twice, with nothing written outside them. Built on the app's own test rig and the SDK's
FakeCore: no server and no network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RIG_DIR = REPO / "apps" / "imagery" / "tests"
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


rigs = _load("imagery_rig", RIG_DIR / "imagery_rig.py")
demo = _load("demo_seed_imagery", REPO / "scripts" / "demo_seed" / "imagery.py")


def seeded(folder: Path):
    folder.mkdir()
    rig = rigs.Rig(folder, settings=dict(demo.SETTINGS))
    demo.seed(lambda name, /, **args: rig.core.call(name, args), TODAY, NOW_MS)
    return rig



def seeded(folder: Path):  # noqa: F811 - Imagery's worker is run here, as its own tests do
    folder.mkdir()
    rig = rigs.Rig(folder, settings=dict(demo.SETTINGS))

    def call(name, /, **args):
        answer = rig.core.call(name, args)
        if name == "make":
            rig.ctx.maker.drain()  # the test rig has no worker thread
        return answer

    demo.seed(call, TODAY, NOW_MS)
    return rig


def _readback(rig) -> dict:
    rows = [{k: v for k, v in r.items() if k != "ref"}  # the file's name is random
            for r in rig.core.call("pictures.list", {})["rows"]]
    return {"rows": rows, "overview": rig.core.call("overview", {})}


def test_imagery_sample_makes_a_starred_practice_gallery(tmp_path):
    rig = seeded(tmp_path / "a")
    try:
        shown = _readback(rig)
        assert len(shown["rows"]) == sum(r[3] for r in demo.REQUESTS)
        assert {r["provider"] for r in shown["rows"]} == {"practice"}
        assert sum(1 for r in shown["rows"] if r["starred"]) == 3
        assert shown["overview"]["today"]["used"] == 0
    finally:
        rig.ctx.close()


def test_imagery_sample_sends_nothing_to_an_outside_service(tmp_path):
    assert demo.SETTINGS == {"provider": "practice"}
    rig = seeded(tmp_path / "a")
    try:
        assert rig.core.fetched == []
        assert {r["state"] for r in rig.store.all("SELECT state FROM run")} == {"done"}
    finally:
        rig.ctx.close()


def test_imagery_sample_gives_the_same_read_back_twice(tmp_path):
    one, two = seeded(tmp_path / "a"), seeded(tmp_path / "b")
    try:
        assert _readback(one) == _readback(two)
    finally:
        one.ctx.close()
        two.ctx.close()
