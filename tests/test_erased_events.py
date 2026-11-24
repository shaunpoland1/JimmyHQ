"""An event the owner had erased keeps its place on the Tape but says nothing: its payload
is empty and it reads back unreadable. Every worker and reader that goes along the Tape must
step over it quietly, not fail on a field that is no longer there. The case that showed it:
an app removed with its data deleted erases what it wrote on the Tape, and a card it had
withdrawn was then read by the hands worker with no ``card_id`` in it."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps_install_helpers import example, install_folder
from jimmyhq import bench
from jimmyhq.apps import install
from jimmyhq.apps.plugs import bench as bench_plug
from jimmyhq.apps.plugs import settings as settings_plug
from jimmyhq.apps.plugs import work as work_plug
from jimmyhq.bench import cards as bench_cards
from jimmyhq.hands import execute
from jimmyhq.tape.registry import example_of
from jimmyhq.village import intake, record
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"
APP = "water"
BY = f"app:{APP}"


def card(core, title: str) -> str:
    return bench.file_card(core, filed_by=BY, kind=f"app.{APP}.note", source="Water",
                           title=title)


@pytest.fixture
def cards_then_removed(core, tmp_path):
    """The app ``water`` has three cards on the Bench: one it withdrew, one it changed, one
    the owner closed; then the app is removed with its data deleted, which erases them."""
    install_folder(core, example(tmp_path, APP))
    withdrawn, changed, closed, open_one = (card(core, t) for t in ("A", "B", "C", "D"))
    bench.withdraw_card(core, withdrawn, by=BY)
    bench.update_card(core, changed, by=BY, title="B, again")
    bench_cards.decide(core, closed, action="not_yet")
    done = install.remove(core, APP, keep_data=False)
    assert done["erased_events"] >= 6
    erased = [e for e in core.tape.after(0, limit=1000) if e.erased]
    assert {e.kind for e in erased} >= {"bench.filed", "bench.withdrawn", "bench.updated"}
    return SimpleNamespace(withdrawn=withdrawn, changed=changed, closed=closed,
                           open=open_one, erased=erased)


def errors(caplog) -> list[str]:
    return [f"{r.name}: {r.getMessage()}" for r in caplog.records
            if r.levelno >= logging.WARNING]


def test_the_hands_worker_goes_over_the_cards_of_a_removed_app_without_an_error(
        core, cards_then_removed, caplog):
    caplog.set_level(logging.DEBUG)
    read = asyncio.run(execute.run(core))
    assert read == 2  # the withdrawal that was erased, and the owner's decision
    assert errors(caplog) == []
    assert asyncio.run(execute.run(core)) == 0  # nothing is read twice


def test_the_hands_worker_passes_over_an_erased_withdrawal_whatever_the_card_was(core):
    withdrawn = core.tape.print("bench.withdrawn", example_of("bench.withdrawn"), actor=BY)
    core.tape.erase([withdrawn.id], actor="owner", reason="apps.removed")
    gone = core.tape.get(withdrawn.id)
    assert gone.erased and gone.payload == {}
    asyncio.run(execute.handle(core, gone))  # raised KeyError: 'card_id'
    execute._withdrawn(core, gone)  # noqa: SLF001


def test_the_bench_still_reads_after_an_app_that_filed_cards_is_removed_with_its_data(
        core, cards_then_removed):
    board = bench.board(core)
    ids = {c["id"] for lane in board["lanes"].values() for c in lane}
    assert ids.isdisjoint({cards_then_removed.withdrawn, cards_then_removed.changed,
                           cards_then_removed.open})
    for card_id in (cards_then_removed.withdrawn, cards_then_removed.open,
                    cards_then_removed.closed):
        assert bench.get_card(core, card_id) is None


def test_the_village_passes_over_an_erased_line_and_keeps_reading_after_it(core, monkeypatch,
                                                                         caplog):
    seen = []
    monkeypatch.setitem(intake.HANDLERS, "village.said", lambda _v, e: seen.append(e.id))
    first = core.tape.print("village.said", example_of("village.said"), actor="owner")
    second = core.tape.print("village.said", example_of("village.said"), actor="owner")
    core.tape.erase([first.id], actor="owner", reason="apps.removed")
    caplog.set_level(logging.DEBUG)
    assert intake.read_tape(SimpleNamespace(core=core)) == 2
    assert seen == [second.id] and errors(caplog) == []


def test_the_villages_older_chat_lines_leave_out_an_erased_one(core):
    record.born(core, "agent-a", 7, "researcher")
    first = record.said(core, "agent-a", "owner", "An early line.")
    kept = record.said(core, "agent-a", "owner", "A later line.")
    core.tape.erase([first.id], actor="owner", reason="apps.removed")
    with TestClient(create_app(core), base_url="http://testserver",
                    raise_server_exceptions=False) as web:
        assert web.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}
                        ).status_code == 200
        got = web.get(f"/api/village/agents/agent-a/chat?before={kept.seq}")
    assert got.status_code == 200 and got.json()["messages"] == []


def test_a_delivery_to_an_app_is_never_made_from_an_erased_event(core):
    decided = core.tape.print("bench.decided", example_of("bench.decided"), actor="owner")
    setting = core.tape.print("setting.updated", example_of("setting.updated"), actor="owner")
    core.tape.erase([decided.id, setting.id], actor="owner", reason="apps.removed")
    app = SimpleNamespace(manifest=SimpleNamespace(id=APP, prefix=f"app.{APP}."))
    assert bench_plug.decision_delivery(app, {"decisions": ["note"]},
                                        core.tape.get(decided.id)) is None
    assert settings_plug.settings_delivery(APP, core.tape.get(setting.id)) is None
    found = work_plug.delivery_for(core, core.tape.get(decided.id), {"work": {}}, {})
    assert found is None


def test_an_outcome_that_was_erased_does_not_stop_the_next_one_being_told(core):
    subject = "hand_request:abc"
    first = core.tape.print("hand.outcome", example_of("hand.outcome"), actor="hands",
                            subject=subject)
    core.tape.erase([first.id], actor="owner", reason="apps.removed")
    row = {"agent": "scout", "account": "notes", "action": "write", "tool": "NOTES_WRITE"}
    assert execute.tell(core, row, "declined", subject=subject, same=("declined",)) is True
    said = [e for e in core.tape.by_subject(subject, kind="hand.outcome") if e.readable]
    assert [e.payload["outcome"] for e in said] == ["declined"]
