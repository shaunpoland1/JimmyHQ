"""Off the record, looked at by someone who wants to find out: what a read
token is shown, what the Tape and the logs can tell about when and where
something was said, and what is left after a delete."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from jimmyhq import chat
from jimmyhq.chat import replies, store
from jimmyhq.ids import now_ms
from jimmyhq.models import Target, brain
from jimmyhq.web.app import create_app
from test_chat_fakes import HOME, SlowProvider, models, of_kind, sent_to
from test_chat_record import (
    BASE,
    OPEN_A,
    OPEN_Q,
    PASSWORD,
    QUIET_A,
    QUIET_Q,
    QUIET_TITLE,
    again,
    answers,
    ask,
    collect,
    flip,
    shown,
)
from test_models_fakes import FakeProvider, add

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
DAY_MS = 86_400_000


def made_at(ulid: str) -> int:
    """The time an id says it was made: its first ten characters."""
    value = 0
    for ch in ulid[:10]:
        value = value * 32 + _CROCKFORD.index(ch)
    return value


def tape_json(core) -> str:
    return json.dumps([e.model_dump() for e in core.tape.after(0, limit=100000)])


@pytest.fixture
def owner(core):
    with TestClient(create_app(core), base_url="http://testserver") as client:
        assert client.post("/api/auth/setup",
                           json={"name": "Sam", "password": PASSWORD}).status_code == 200
        yield client


@pytest.fixture
def token(owner, core):
    secret = core.auth.create_token("scout", ["read"])[1]
    with TestClient(owner.app, base_url="http://testserver",
                    headers={"authorization": f"Bearer {secret}"}) as client:
        yield client


# --- who may see what is off the record ---------------------------------------------------


def test_a_read_token_is_shown_only_what_is_on_the_tape(owner, token, core):
    answers(core, QUIET_A)
    quiet = chat.start_conversation(core, title="Hazelnut errands", on_record=False)["id"]
    ask(core, quiet, QUIET_Q)
    mixed = chat.start_conversation(core)["id"]
    ask(core, mixed, OPEN_Q, reply=OPEN_A)
    flip(core, mixed, False)
    ask(core, mixed, QUIET_Q, reply=QUIET_A)
    chat.change_conversation(core, mixed, title=QUIET_TITLE)

    # The owner sees all of it.
    assert {c["id"] for c in owner.get(BASE).json()["conversations"]} == {quiet, mixed}
    assert len(owner.get(f"{BASE}/{mixed}").json()["messages"]) == 4

    # A token: the one never on the record is not there at all.
    listed = token.get(BASE).json()["conversations"]
    assert [c["id"] for c in listed] == [mixed]
    assert token.get(f"{BASE}/{quiet}").status_code == 404
    assert token.get(f"{BASE}/{quiet}?before={'0' * 26}").status_code == 404
    for words in ("walnut", "hazelnut", "cake"):
        assert token.get(BASE, params={"q": words}).json()["conversations"] == []
    assert token.get(BASE, params={"archived": True}).json()["conversations"] == []
    # And of the other, only the Tape's part: its messages, its title, its count.
    page = token.get(f"{BASE}/{mixed}").json()
    assert [(m["text"], m["on_record"]) for m in page["messages"]] == [(OPEN_Q, True),
                                                                      (OPEN_A, True)]
    conv = page["conversation"]
    assert (conv["title"], conv["messages"]) == (OPEN_Q, 2)
    assert (conv["off_record_messages"], conv["off_record_title"]) == (0, False)
    assert (page["unanswered"], listed[0]["title"], listed[0]["messages"]) == (False, OPEN_Q, 2)
    older = token.get(f"{BASE}/{mixed}", params={"limit": 1}).json()
    assert [m["text"] for m in older["messages"]] == [OPEN_A]
    older = token.get(f"{BASE}/{mixed}", params={"limit": 1, "before": older["older"]}).json()
    assert [m["text"] for m in older["messages"]] == [OPEN_Q]
    seen = token.get(BASE).text + token.get(f"{BASE}/{mixed}").text
    for words in ("walnut", "Walnut", "flour", quiet):
        assert words not in seen, words
    # From Python, the same: asking without what is off the record.
    assert chat.get_conversation(core, mixed, off_record=False)["conversation"]["title"] == OPEN_Q
    with pytest.raises(chat.ChatError) as gone:
        chat.get_conversation(core, quiet, off_record=False)
    assert gone.value.status == 404


def test_a_read_token_is_not_told_that_a_reply_is_being_written_off_the_record(
        owner, token, core):
    home = SlowProvider()
    models(core, home=home)
    conv_id = chat.start_conversation(core)["id"]
    flip(core, conv_id, False)

    async def scenario():
        run = await replies.begin(core, conv_id, text=QUIET_Q)
        try:
            assert owner.get(f"{BASE}/{conv_id}").json()["conversation"]["streaming"] is True
            page = token.get(f"{BASE}/{conv_id}").json()
            assert page["conversation"]["streaming"] is False and page["messages"] == []
            assert token.get(BASE).json()["conversations"][0]["streaming"] is False
        finally:
            home.release.set()
            await asyncio.wait({run.task})

    asyncio.run(scenario())


# --- what the Tape can tell about when and where ----------------------------------------------


def test_an_id_made_off_the_record_does_not_say_when_it_was_made(core, cfg):
    before = now_ms()
    answers(core, QUIET_A)
    conv_id = chat.start_conversation(core, on_record=False)["id"]
    reply = ask(core, conv_id, QUIET_Q)
    question = chat.get_conversation(core, conv_id)["messages"][0]["id"]
    for made in (conv_id, question, reply["id"]):
        assert store.CONV_ID.fullmatch(made)
        assert abs(made_at(made) - before) > DAY_MS, made
    # On the record an id is still an ordinary one, in the order it was made.
    open_id = chat.start_conversation(core)["id"]
    assert abs(made_at(open_id) - before) < DAY_MS
    # Put on the record and answered again there, the ids the Tape now holds
    # (the conversation's, and the message the new reply answers) tell no time.
    flip(core, conv_id, True)
    again(core, conv_id, "Ask me off the record.")
    (answered,) = of_kind(core, "chat.message.answered")
    assert answered.payload["reply_to"] == question
    assert shown(core, conv_id) == [(QUIET_Q, False), ("Ask me off the record.", True)]


def test_the_tape_is_not_told_which_off_the_record_reply_a_recorded_one_replaced(core):
    # Asked off the record and answered there; answered again on the record.
    conv_id = chat.start_conversation(core, on_record=False)["id"]
    old = ask(core, conv_id, QUIET_Q, reply=QUIET_A)
    flip(core, conv_id, True)
    again(core, conv_id, "Ask me off the record.")
    (answered,) = of_kind(core, "chat.message.answered")
    assert answered.payload["replaces"] is None
    assert old["id"] not in tape_json(core)

    # Asked and answered on the record, answered again off it, then again on it:
    # what the Tape's new reply replaces is the Tape's own earlier one.
    other = chat.start_conversation(core)["id"]
    first = ask(core, other, OPEN_Q, reply=OPEN_A)
    flip(core, other, False)
    hidden = again(core, other, QUIET_A)
    flip(core, other, True)
    again(core, other, "Borlotti, then.")
    assert of_kind(core, "chat.message.answered")[-1].payload["replaces"] == first["id"]
    assert hidden["id"] not in tape_json(core)
    assert shown(core, other) == [(OPEN_Q, True), ("Borlotti, then.", True)]
    listed = {c["id"]: c for c in chat.list_conversations(core)["conversations"]}
    assert listed[other]["messages"] == 2  # counted once, not once per reply


def test_a_change_of_model_made_off_the_record_reaches_the_tape_only_when_back_on_it(core):
    models(core)
    brain(core).set_role("smart", Target("home", "model-b"), actor="owner")
    conv_id = chat.start_conversation(core)["id"]
    ask(core, conv_id, OPEN_Q)
    flip(core, conv_id, False)
    before = core.tape.last_seq()
    smart = {"kind": "role", "role": "smart"}
    conv = chat.change_conversation(core, conv_id, target=smart, effort="high", local_only=True)
    assert (conv["target"], conv["effort"], conv["local_only"]) == (smart, "high", True)
    fakes = models(core)
    brain(core).set_role("smart", Target("home", "model-b"), actor="owner")
    asyncio.run(collect(chat.send(core, conv_id, QUIET_Q, effort="low")))
    assert fakes[HOME].bodies[-1]["model"] == "model-b"  # the choice is the one used
    conv = chat.get_conversation(core, conv_id)["conversation"]
    assert (conv["target"], conv["effort"], conv["local_only"]) == (smart, "low", True)
    during = [e.kind for e in core.tape.after(before, limit=1000) if e.kind.startswith("chat.")]
    assert during == []  # nothing on the Tape says the owner was here just then
    assert chat.list_conversations(core)["conversations"][0]["target"] == smart

    # Back on the record the choice carries on, and the Tape learns it at the flip.
    conv = flip(core, conv_id, True)
    assert (conv["target"], conv["effort"], conv["local_only"]) == (smart, "low", True)
    kinds = [e.kind for e in core.tape.after(before, limit=1000) if e.kind.startswith("chat.")]
    assert kinds == ["chat.conversation.mode", "chat.conversation.changed"]
    changed = of_kind(core, "chat.conversation.changed")[-1].payload
    assert (changed["target"]["role"], changed["effort"], changed["local_only"]) == (
        "smart", "low", True)
    # A change made on the record is recorded as before, and going off again starts from it.
    default = {"kind": "role", "role": "default"}
    chat.change_conversation(core, conv_id, target=default, effort=None)
    assert flip(core, conv_id, False)["target"] == default
    assert chat.get_conversation(core, conv_id)["conversation"]["effort"] is None


def test_two_off_the_record_replies_failing_together_each_name_their_own_model(core):
    slow = SlowProvider(then="fail")
    fakes = {HOME: FakeProvider("openai_compatible", "timeout"), "slow.example.com": slow}
    b = models(core, home=fakes[HOME]) and brain(core)
    from test_models_fakes import wire  # noqa: PLC0415

    wire(core, {HOME: fakes[HOME], "cloud.example.com": FakeProvider("openai_compatible"),
                "slow.example.com": slow})
    add(b, "slow", host="slow.example.com", key="sk-test-slow-key-0000000")
    b.set_role("default", Target("home", "model-a"), actor="owner")
    first = chat.start_conversation(
        core, on_record=False,
        target={"kind": "model", "provider_id": "slow", "model": "model-s"})["id"]
    second = chat.start_conversation(core, on_record=False)["id"]

    async def scenario():
        one = await replies.begin(core, first, text=QUIET_Q)  # still being written when...
        while not slow.streaming.is_set():
            await asyncio.sleep(0.005)
        two = await replies.begin(core, second, text="And the marzipan?")  # ...this one fails
        await asyncio.wait({two.task})
        slow.release.set()
        await asyncio.wait({one.task})
        return one.message, two.message

    one, two = asyncio.run(scenario())
    assert (one["state"], two["state"]) == ("failed", "failed")
    assert (two["provider_id"], two["model"], two["fallback_from"]) == ("home", "model-a", None)
    assert (one["provider_id"], one["model"], one["fallback_from"]) == ("slow", "model-s", None)


# --- the logs -----------------------------------------------------------------------------


def test_an_unexpected_failure_off_the_record_writes_no_words_to_the_log(
        core, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    models(core)

    async def broken(messages, **options):
        raise RuntimeError(f"could not handle {messages[-1]['content']!r}")
        yield  # pragma: no cover - makes this a generator

    monkeypatch.setattr(brain(core), "stream", broken)
    conv_id = chat.start_conversation(core, on_record=False)["id"]
    reply = asyncio.run(collect(chat.send(core, conv_id, QUIET_Q)))[-1]
    assert reply["state"] == "failed"
    assert "walnut" not in caplog.text and "RuntimeError" in caplog.text
    # On the record the whole error is written down, as it always was.
    open_id = chat.start_conversation(core)["id"]
    asyncio.run(collect(chat.send(core, open_id, OPEN_Q)))
    assert "beans climb" in caplog.text


def test_deleting_what_is_off_the_record_is_not_written_to_the_access_log(owner, caplog):
    caplog.set_level(logging.INFO)
    access = logging.getLogger("uvicorn.access")
    conv_id = owner.post(BASE, json={"on_record": False}).json()["id"]
    line = '%s - "%s %s HTTP/%s" %d'  # how the server writes each request down
    access.info(line, "127.0.0.1:5000", "DELETE", f"{BASE}/{conv_id}/off-record", "1.1", 200)
    access.info(line, "127.0.0.1:5000", "DELETE", f"{BASE}/{conv_id}/off-record", "1.1", 409)
    access.info(line, "127.0.0.1:5000", "POST", f"{BASE}/{conv_id}/archive", "1.1", 200)
    assert "off-record" not in caplog.text and conv_id not in caplog.text
    assert f'"POST {BASE}/-/archive HTTP/1.1" 200' in caplog.text


# --- deleting -----------------------------------------------------------------------------


def test_a_title_still_being_written_when_its_words_are_deleted_is_not_kept(
        core, monkeypatch):
    core.settings.set("chat.model_titles", True, actor="owner")
    models(core, home=FakeProvider("openai_compatible", text=QUIET_A), cheap=("home", "model-a"))
    plans = []

    async def later(core_, plan, answer, reply, reply_id):
        plans.append((plan, reply_id))  # the title model has not answered yet

    monkeypatch.setattr(replies, "_write_title", later)
    conv_id = chat.start_conversation(core)["id"]
    flip(core, conv_id, False)
    asyncio.run(collect(chat.send(core, conv_id, QUIET_Q)))
    (plan, reply_id), = plans
    assert chat.delete_off_record(core, conv_id)["deleted"] == 2
    replies._save_title(core, plan, "Cake Hiding Places", reply_id)  # and now it answers
    conv = chat.get_conversation(core, conv_id)["conversation"]
    assert (conv["title"], conv["off_record_title"], conv["off_record_messages"]) == ("", False, 0)

    # Not deleted, it is kept (off the record) as before.
    asyncio.run(collect(chat.send(core, conv_id, QUIET_Q)))
    plan, reply_id = plans[-1]
    replies._save_title(core, plan, "Cake Hiding Places", reply_id)
    assert chat.get_conversation(core, conv_id)["conversation"]["title"] == "Cake Hiding Places"


def test_nothing_of_a_deleted_conversation_is_left_in_any_file(core, cfg, caplog):
    """Every byte of the data folder, the off-the-record store and its log
    included, after everything that can be done to a conversation was done."""
    caplog.set_level(logging.DEBUG)
    core.settings.set("chat.model_titles", True, actor="owner")
    answers(core, "Marzipan Matters", cheap=("home", "model-a"))
    conv_id = chat.start_conversation(core, title="Hazelnut errands", on_record=False)["id"]
    ask(core, conv_id, QUIET_Q, reply=QUIET_A)
    again(core, conv_id, "Under the pistachio jar.")
    chat.change_conversation(core, conv_id, title=QUIET_TITLE)
    chat.change_conversation(core, conv_id, title="Quince plans")
    chat.archive_conversation(core, conv_id)
    chat.restore_conversation(core, conv_id)
    ask(core, conv_id, "apricot " * 3000, reply="Damson jam.")
    assert chat.delete_off_record(core, conv_id)["conversation"] is None
    time.sleep(0.01)
    held = b"".join(p.read_bytes() for p in sorted(cfg.data.rglob("*")) if p.is_file())
    held += caplog.text.encode()
    for words in (conv_id, "azelnut", "alnut", "flour", "istachio", "uince", "apricot",
                  "amson", "arzipan"):
        assert words.encode() not in held, words


def test_what_is_deleted_is_no_longer_sent_to_the_model(core):
    core.settings.set("chat.system_prompt", "", actor="owner")
    conv_id = chat.start_conversation(core, on_record=False)["id"]
    ask(core, conv_id, QUIET_Q, reply=QUIET_A)
    flip(core, conv_id, True)
    again(core, conv_id, "Ask me off the record.")  # on the Tape, answering what is not
    ask(core, conv_id, OPEN_Q, reply=OPEN_A)
    assert chat.delete_off_record(core, conv_id)["deleted"] == 1
    # The recorded reply to the deleted message stays, as everything on the Tape does.
    assert shown(core, conv_id) == [("Ask me off the record.", True), (OPEN_Q, True),
                                    (OPEN_A, True)]
    fakes = answers(core, "Sugar snaps.")
    ask(core, conv_id, "And which peas?")
    assert [(m["role"], m["content"]) for m in sent_to(fakes[HOME])] == [
        ("user", OPEN_Q), ("assistant", OPEN_A), ("user", "And which peas?")]
    assert "walnut" not in json.dumps(fakes[HOME].bodies)


def test_a_regenerate_replaces_the_reply_to_its_own_message_not_one_left_without_a_question(
        core):
    conv_id = chat.start_conversation(core)["id"]
    first = ask(core, conv_id, OPEN_Q, reply=OPEN_A)
    flip(core, conv_id, False)
    ask(core, conv_id, QUIET_Q, reply=QUIET_A)
    flip(core, conv_id, True)
    again(core, conv_id, "Ask me off the record.")
    chat.delete_off_record(core, conv_id)  # the recorded reply now answers nothing shown
    assert shown(core, conv_id) == [(OPEN_Q, True), (OPEN_A, True),
                                    ("Ask me off the record.", True)]
    models(core, home=FakeProvider("openai_compatible", text="Borlotti, then."))

    async def regenerate():
        run = await replies.begin(core, conv_id, regenerate=True)
        await asyncio.wait({run.task})
        return run

    run = asyncio.run(regenerate())
    assert run.start["replaces"] == first["id"] and run.message["reply_to"] == first["reply_to"]
    assert of_kind(core, "chat.message.answered")[-1].payload["replaces"] == first["id"]
    assert sorted(shown(core, conv_id)) == sorted([
        (OPEN_Q, True), ("Borlotti, then.", True), ("Ask me off the record.", True)])
