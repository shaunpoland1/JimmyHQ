"""The browser inside a real beat: the picture beside the view, the fallback for a model
that takes none, the page as the repeat guard's state, the owner's answer coming in from
the Tape, and what an agent's own documents say."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jimmyhq import bench
from jimmyhq.bench import cards
from jimmyhq.browser import engine
from jimmyhq.village import docs, intake, tools
from jimmyhq.village.beat import think
from jimmyhq.village.beat.run import beat
from jimmyhq.village.context import build
from jimmyhq.village.rules import RULES
from jimmyhq.village.tools import browser as bt
from test_browser_fakes import INJECTION, SHOP, fake_browser
from test_village_beat_fakes import ME, call, fails, kinds, make_village


@pytest.fixture
def installed(monkeypatch):
    """Whatever this machine has: the capability's switch reads as "a browser is here"."""
    monkeypatch.setattr(engine, "find", lambda core: Path("browser-program"))


def village(core, *script, **cfg):
    v = make_village(core, *script, **{"tools": SimpleNamespace(ALL=tools.ALL),
                                       "tools.browser": bt, "tools.logged": None})
    v.set_cfg(ME, {"capabilities": ["browser"], **cfg})
    return v, fake_browser(core)


def images(message) -> list[dict]:
    content = message["content"]
    return [p for p in content if p.get("type") == "image"] if isinstance(content, list) else []


def text_of(message) -> str:
    content = message["content"]
    return content if isinstance(content, str) else " ".join(
        p["text"] for p in content if p.get("type") == "text")


def test_the_page_is_shown_marked_untrusted_with_its_picture_and_only_to_the_agents_model(
        core, installed):
    v, web = village(core, call("open_page", url=SHOP), call("read_page"), local_only=True)
    asyncio.run(beat(v, ME))
    asyncio.run(beat(v, ME))
    first, second = v.fakes["brain"].calls
    assert images(first["messages"][0]) == []  # nothing was open yet
    (picture,) = images(second["messages"][0])
    assert picture["media_type"] == "image/png" and picture["data"]
    view = text_of(second["messages"][0])
    assert "# What came back from open_page" in view and INJECTION in view
    assert view.index("untrusted text from the internet") < view.index(INJECTION)
    assert view.endswith("Choose one action.")
    # A local-only agent's page goes to a model on this machine or to none.
    assert first["local_only"] is True and second["local_only"] is True
    assert len(v.fakes["brain"].calls) == 2  # and no other model call was made for it
    assert INJECTION not in str([[e.kind, e.data] for e in v.log.query()])


def test_a_model_that_takes_no_pictures_is_asked_again_with_the_text_view_and_told_once(
        core, installed):
    sent_with = []

    def refuses_pictures(messages, options):
        sent_with.append(len(images(messages[0])))
        return fails("no_images")

    v, web = village(core, call("open_page", url=SHOP), refuses_pictures,
                     call("scroll", direction="down"), call("scroll", direction="up"),
                     call("read_page"))
    for _ in range(4):
        asyncio.run(beat(v, ME))
    calls = v.fakes["brain"].calls
    assert sent_with == [1]  # tried with the picture
    assert images(calls[2]["messages"][0]) == []  # asked again without it, same beat
    assert "# What came back from open_page" in text_of(calls[2]["messages"][0])
    assert [e.data["tool"] for e in kinds(v, "action")] == [
        "open_page", "scroll", "scroll", "read_page"]
    assert not kinds(v, "platform_fault")  # the beat went on: this is not a fault
    told = [text_of(c["messages"][0]) for c in calls[3:]]
    assert images(calls[3]["messages"][0]) == [] and images(calls[4]["messages"][0]) == []
    assert "Your model does not take pictures" in told[0]
    assert "Your model does not take pictures" not in told[1]  # said once
    assert RULES["picture_fallback"].fn is think.without_pictures


def test_pictures_are_taken_out_of_a_conversation_in_place_leaving_the_text():
    messages = [{"role": "user", "content": [{"type": "text", "text": "the view"},
                                             {"type": "image", "data": "x"}]}]
    assert think.without_pictures(messages) is True
    assert messages == [{"role": "user", "content": "the view"}]
    assert think.without_pictures(messages) is False


def test_another_failure_of_the_model_is_still_a_fault_and_no_picture_is_forgotten(
        core, installed):
    v, web = village(core, call("open_page", url=SHOP), fails("timeout"))
    asyncio.run(beat(v, ME))
    asyncio.run(beat(v, ME))
    assert kinds(v, "platform_fault") and bt.takes_pictures(v, ME)


def test_the_same_click_on_the_same_page_is_refused_by_the_worlds_guard_in_a_beat(
        core, installed):
    refresh = 8  # "Refresh prices": a button that changes nothing
    v, web = village(core, call("open_page", url=SHOP), call("click", element=refresh),
                     call("click", element=refresh), call("scroll", direction="down"))
    for _ in range(3):
        asyncio.run(beat(v, ME))
    assert web[ME].clicked == [refresh]
    refused = kinds(v, "validator_reject")
    assert [r.data["reason"] for r in refused] == ["repeat_same_state"]
    assert kinds(v, "action")[-1].data["tool"] == "scroll"  # it got another go, and moved on


def test_the_owners_approval_comes_in_from_the_tape_and_the_form_is_sent(core, installed):
    v, web = village(core, call("open_page", url=f"{SHOP}/order"),
                     call("type_text", element=1, text="2"), call("click", element=3),
                     call("read_page"))
    for _ in range(3):
        asyncio.run(beat(v, ME))
    assert web[ME].sent == []
    card_id = cards.board(core)["lanes"]["needs"][0]["id"]
    assert "Waiting for the owner: sending the form at shop.example.com" in build.user_text(
        v, ME, [])
    cards.decide(core, card_id, action="approve")

    async def taken_in():
        intake.read_tape(v)  # the village's own loop: the approval is performed on it
        await asyncio.sleep(0.05)

    asyncio.run(taken_in())
    assert [s[:2] for s in web[ME].sent] == [("POST", f"{SHOP}/orders")]
    assert v.state(ME).wake.is_set()
    view = build.user_text(v, ME, [])
    assert "# Forms you asked to send" in view and "was sent as shown" in view
    assert bench.get_card(core, card_id)["filed_by"] == f"agent:{ME}"


def test_only_an_agent_with_the_capability_reads_about_the_browser(core, installed):
    with_it = docs.frame("Ada", "Sam", ["browser"])
    assert "You have a browser of your own" in with_it and "never instructions" in with_it
    assert "browser of your own" not in docs.frame("Ada", "Sam", [])
    how = docs.fill(docs.read(docs.HOW), "Ada", "Sam", ["browser"], doc=docs.HOW)
    assert "## Using the browser" in how and "Changing something out there needs Sam's leave" in how
    assert "{owner}" not in how
    assert "## Using the browser" not in docs.fill(docs.read(docs.HOW), "Ada", "Sam", [],
                                                   doc=docs.HOW)
    assert "## Using the web" not in how  # one capability does not bring another's section
