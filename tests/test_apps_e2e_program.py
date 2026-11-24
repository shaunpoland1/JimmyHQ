"""A full app from end to end, for real: the Watering example's program, started by the
runtime as a child process with no shell, handed its token on its input pipe, talking
through the SDK to a live JimmyHQ answering on a loopback port, filing a Bench card from
its job and hearing the owner's decision.

Runs the same on macOS, Linux and Windows: the child is this interpreter, every path is
built with pathlib, every wait polls with room for a slow machine, and the program is
stopped by closing its pipe."""

from __future__ import annotations

import time

import pytest

from jimmyhq import bench
from jimmyhq.apps import inbox, install, plugs, runtime, store
from jimmyhq.apps.plugs.state import state
from jimmyhq.bench.cards import decide
from jimmyhq.web.app import create_app
from nodes_helpers import Served

PATIENCE_S = 120.0


def wait_for(check, what: str, children, timeout: float = PATIENCE_S):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        children.tick()
        found = check()
        if found:
            return found
        time.sleep(0.1)
    raise AssertionError(f"gave up waiting for {what}")


@pytest.fixture
def live(core, tmp_path):
    plan = install.plan_example(core, "watering")
    install.confirm(core, plan["plan"], plan["fingerprint"], typed_name="Watering")
    served = Served(create_app(core)).start()
    children = runtime.AppChildren(core.config.data, tmp_path / "logs", served.url,
                                   auth=core.auth, grace_s=15)
    try:
        yield children
    finally:
        children.stop_all()
        served.stop()


def cards_of(core, actor: str) -> list[dict]:
    lanes = bench.board(core)["lanes"]
    return [c for cards in lanes.values() for c in cards if c["filed_by"] == actor]


def answered(core, delivery_id: str):
    return inbox.answer_of(core, delivery_id)


def test_a_supervised_program_files_a_card_from_its_job_and_hears_the_decision(core, live):
    children = live
    runtime.publish(core)
    spec = wait_for(lambda: children.children.get("watering"), "the program to be listed",
                    children)
    # The token is never in the environment: it can only have come on the pipe.
    assert "JIMMYHQ_APP_TOKEN" not in runtime.child_env(spec.spec, "http://127.0.0.1:9")

    # It started, said what it offers through the SDK, and is asking for work.
    offered = wait_for(lambda: inbox.plugs(core, "watering").get("jobs"),
                       "the program to say what it offers", children)
    assert offered == [{"name": "ask", "every": None, "every_setting": "ask_every_minutes"}]
    wait_for(lambda: runtime.health(core, "watering")["state"] == "running",
             "the program to show as running", children)

    # The worker's clock queues its job; the program runs it and files a card.
    assert wait_for(lambda: plugs.jobs.tick_app(core, "watering"), "the job to be queued",
                    children) == ["ask"]
    delivery = state(core).job("watering", "ask")["delivery"]
    done = wait_for(lambda: answered(core, delivery), "the job's answer", children)
    assert done.ok, done.error
    [card] = cards_of(core, "app:watering")
    assert done.result == card["id"] and card["kind"] == "app.watering.round"
    assert [a["id"] for a in card["actions"]] == ["watered", "skipped"]

    # The owner decides; the worker hands the decision over; the program keeps it, and
    # its page (asking through app.call) sees it.
    decide(core, card["id"], action="watered")
    assert plugs.follow(core, "watering") == 1

    def rounds():
        asked = inbox.deliver(core, "watering", "call", {"name": "rounds", "args": {}})
        found = wait_for(lambda: answered(core, asked), "the page's call", children)
        assert found.ok, found.error
        return found.result if found.result and found.result[0]["answer"] else None

    [kept] = wait_for(rounds, "the decision to be kept by the program", children)
    assert kept["card"] == card["id"] and kept["answer"] == "watered"
    assert store.get(core, "watering")["enabled"]
