"""The sample conversations for Heard go in through the app's own message handler."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rig_module = _load("heard_rig_for_seed", REPO / "apps" / "heard" / "tests" / "heard_rig.py")
seed_module = _load("demo_seed_heard", REPO / "scripts" / "demo_seed" / "heard.py")

TODAY = "2027-01-15"


def _seeded():
    rig = rig_module.Rig(Path(tempfile.mkdtemp()), settings=seed_module.SETTINGS)
    sent: list[tuple[str, dict]] = []

    def post(name, payload):
        sent.append((name, payload))
        rig.core.inbound(name, payload, key_name="Pendant", received_ms=rig.clock.now)

    seed_module.seed(lambda name, **args: None, TODAY, rig.clock.now, post=post)
    return rig, sent


def _readback(rig) -> list:
    days = rig.call("days")
    return [(c["heading"], c["lines"], c["words"], c["started_ms"], c["speakers"])
            for c in days["conversations"]]


def test_every_sample_conversation_is_kept_and_none_is_refused():
    rig, sent = _seeded()
    try:
        assert {name for name, _ in sent} == {"conversation"}
        kept = rig.call("days")["conversations"]
        assert len(kept) == len(seed_module.CONVERSATIONS) >= 5
        titles = {c["heading"] for c in kept}
        assert "Garden plan for spring" in titles
    finally:
        rig.ctx.close()


def test_a_conversation_shows_who_spoke_and_can_be_searched():
    rig, _ = _seeded()
    try:
        found = rig.call("search", query="tulip")["results"]
        assert found and "tulip" in repr(found)
        oldest = rig.call("days")["conversations"][-1]
        one = rig.call("conversation", id=oldest["id"])
        assert {s["speaker"] for s in one["segments"]} == {"You", "Alex"}
        assert one["title"] == "Garden plan for spring"
    finally:
        rig.ctx.close()


def test_seeding_twice_into_fresh_rigs_reads_back_the_same():
    first, _ = _seeded()
    second, _ = _seeded()
    try:
        assert _readback(first) == _readback(second)
        assert repr(first.call("days")) == repr(second.call("days"))
    finally:
        first.ctx.close()
        second.ctx.close()


def test_the_seed_asks_for_no_model_and_needs_post():
    rig, _ = _seeded()
    try:
        assert not [c for c in rig.calls if "model" in c or c.startswith("summarise")]
    finally:
        rig.ctx.close()
    try:
        seed_module.seed(lambda name, **args: None, TODAY, 0)
    except ValueError as said:
        assert "post" in str(said)
    else:
        raise AssertionError("seeding without post must say so")
