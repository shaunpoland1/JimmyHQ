"""On and off the record: what reaches the Tape, what never does, how the two
parts read back as one conversation, and what deleting removes."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from jimmyhq import chat
from jimmyhq.chat import offrecord, replies, store
from jimmyhq.core import open_core
from jimmyhq.web.app import create_app
from test_chat_fakes import CLOUD, HOME, SlowProvider, models, of_kind, sent_to
from test_models_fakes import TOKENS_IN, TOKENS_OUT, FakeProvider

BASE = "/api/chat/conversations"
PASSWORD = "correct horse battery"
OPEN_Q, OPEN_A = "Which beans climb best?", "Runner beans, on tall canes."
QUIET_Q, QUIET_A = "Where did I hide the walnut cake?", "Behind the flour, top shelf."
QUIET_TITLE = "Walnut cake whereabouts"


async def collect(stream):
    return [item async for item in stream]


def answers(core, text, **more):
    """From now on the local model answers with ``text``."""
    return models(core, home=FakeProvider("openai_compatible", text=text), **more)


def ask(core, conv_id, text, reply=None):
    if reply is not None:
        answers(core, reply)
    return asyncio.run(collect(chat.send(core, conv_id, text)))[-1]


def again(core, conv_id, reply):
    answers(core, reply)
    return asyncio.run(collect(chat.regenerate(core, conv_id)))[-1]


def flip(core, conv_id, on_record):
    return chat.change_conversation(core, conv_id, on_record=on_record)


def shown(core, conv_id, **page):
    """The thread as (text, on_record) pairs."""
    return [(m["text"], m["on_record"])
            for m in chat.get_conversation(core, conv_id, **page)["messages"]]


def off_file(cfg):
    return cfg.data / offrecord.FILE


def everything_recorded(core, cfg, caplog) -> str:
    """Every Tape event as JSON, every Vault file, every byte of every other
    file in the data folder except the off-the-record store, and the logs."""
    parts = [json.dumps(e.model_dump()) for e in core.tape.after(0, limit=100000)]
    for path in sorted(cfg.data.rglob("*")):
        if path.is_file() and not path.name.startswith(offrecord.FILE):
            parts.append(path.read_bytes().decode("utf-8", "ignore"))
    parts.append(caplog.text)
    return "\n".join(parts)


def rows(cfg, table):
    conn = sqlite3.connect(off_file(cfg))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    finally:
        conn.close()


@pytest.fixture
def owner(core):
    with TestClient(create_app(core), base_url="http://testserver") as client:
        assert client.post("/api/auth/setup",
                           json={"name": "Sam", "password": PASSWORD}).status_code == 200
        yield client


# --- nothing off the record reaches the Tape ------------------------------------------


def test_a_conversation_kept_off_the_record_leaves_nothing_on_the_tape_the_vault_or_the_logs(
        core, cfg, caplog):
    caplog.set_level(logging.DEBUG)
    core.settings.set("chat.model_titles", True, actor="owner")
    answers(core, QUIET_A, cheap=("home", "model-a"))
    conv = chat.start_conversation(core, title="Hazelnut errands", on_record=False)
    conv_id = conv["id"]
    assert conv["on_record"] is False and conv["recorded"] is False
    reply = ask(core, conv_id, QUIET_Q)
    assert (reply["state"], reply["text"], reply["on_record"]) == ("complete", QUIET_A, False)
    long = "quince " * 15000  # well past what would go to the Vault on the record
    ask(core, conv_id, long, reply="Pear and almond.")
    assert again(core, conv_id, "Plum and marzipan.")["text"] == "Plum and marzipan."
    chat.change_conversation(core, conv_id, title=QUIET_TITLE)
    chat.archive_conversation(core, conv_id)
    assert chat.list_conversations(core, archived=True)["conversations"][0]["id"] == conv_id
    chat.restore_conversation(core, conv_id)
    assert shown(core, conv_id) == [(QUIET_Q, False), (QUIET_A, False), (long, False),
                                    ("Plum and marzipan.", False)]
    listed = chat.list_conversations(core, q="walnut")["conversations"]
    assert [(c["id"], c["title"], c["on_record"]) for c in listed] == [
        (conv_id, QUIET_TITLE, False)]

    found = everything_recorded(core, cfg, caplog)
    for words in (conv_id, "Hazelnut", QUIET_TITLE, "walnut", QUIET_A, "quince", "almond",
                  "marzipan", reply["id"]):
        assert words not in found, words
    assert not [e for e in core.tape.after(0, limit=100000) if e.kind.startswith("chat.")]
    assert not [p for p in cfg.vault_dir.rglob("*") if p.is_file()]

    assert chat.delete_off_record(core, conv_id) == {"deleted": 4, "conversation": None}
    with pytest.raises(chat.ChatError) as gone:
        chat.get_conversation(core, conv_id)
    assert gone.value.status == 404
    assert chat.list_conversations(core)["conversations"] == []
    assert conv_id not in everything_recorded(core, cfg, caplog)


def test_an_off_the_record_reply_still_leaves_a_receipt_with_counts_and_no_words(core):
    answers(core, QUIET_A)
    conv_id = chat.start_conversation(core, on_record=False)["id"]
    ask(core, conv_id, QUIET_Q)
    (receipt,) = of_kind(core, "model.called")
    p = receipt.payload
    assert (p["caller"], p["provider_id"], p["model"]) == ("chat.reply", "home", "model-a")
    assert (p["input_tokens"], p["output_tokens"]) == (TOKENS_IN, TOKENS_OUT)
    assert p["prompt_ref"] is None and p["completion_ref"] is None
    # Tied to nothing: not the conversation, and not a message.
    assert (receipt.cause, receipt.root, receipt.subject) == (None, None, None)
    stored = json.dumps(receipt.model_dump())
    for words in (conv_id, "walnut", "flour"):
        assert words not in stored


def test_flipped_off_on_and_off_again_only_what_was_said_on_the_record_is_recorded(
        core, cfg, caplog):
    caplog.set_level(logging.DEBUG)
    conv_id = chat.start_conversation(core)["id"]
    ask(core, conv_id, OPEN_Q, reply=OPEN_A)
    assert flip(core, conv_id, False)["on_record"] is False
    ask(core, conv_id, QUIET_Q, reply=QUIET_A)
    chat.change_conversation(core, conv_id, title=QUIET_TITLE)
    flip(core, conv_id, True)
    ask(core, conv_id, "And which peas?", reply="Sugar snaps.")
    flip(core, conv_id, False)
    ask(core, conv_id, "Is the marzipan safe too?", reply="Yes, in the blue tin.")

    assert shown(core, conv_id) == [
        (OPEN_Q, True), (OPEN_A, True), (QUIET_Q, False), (QUIET_A, False),
        ("And which peas?", True), ("Sugar snaps.", True),
        ("Is the marzipan safe too?", False), ("Yes, in the blue tin.", False)]
    conv = chat.get_conversation(core, conv_id)["conversation"]
    assert (conv["title"], conv["on_record"], conv["recorded"]) == (QUIET_TITLE, False, True)
    assert (conv["messages"], conv["off_record_messages"]) == (8, 4)

    found = everything_recorded(core, cfg, caplog)
    for words in (OPEN_Q, OPEN_A, "And which peas?", "Sugar snaps."):
        assert words in found, words
    for words in ("walnut", "Walnut", "flour", "marzipan", "blue tin"):
        assert words not in found, words
    # The Tape says when the switch was flipped, and nothing about what followed.
    assert [e.payload["on_record"] for e in of_kind(core, "chat.conversation.mode")] == [
        False, True, False]
    assert len(of_kind(core, "chat.message.sent")) == 2
    for receipt in of_kind(core, "model.called"):
        assert (receipt.cause is None) == (receipt.root is None)
    assert [r.root is None for r in of_kind(core, "model.called")] == [False, True, False, True]


def test_a_title_a_model_writes_off_the_record_is_shown_and_never_recorded(core, cfg, caplog):
    core.settings.set("chat.model_titles", True, actor="owner")
    title = FakeProvider("openai_compatible", text="Cake Hiding Places")
    fakes = models(core, home=title, cheap=("home", "model-a"))
    conv_id = chat.start_conversation(core)["id"]
    flip(core, conv_id, False)
    ask(core, conv_id, QUIET_Q)
    assert len(fakes[HOME].bodies) == 2  # the reply, then the title
    conv = chat.get_conversation(core, conv_id)["conversation"]
    assert (conv["title"], conv["title_source"]) == ("Cake Hiding Places", "model")
    assert conv["off_record_title"] is True
    found = everything_recorded(core, cfg, caplog)
    assert "Cake Hiding" not in found and "walnut" not in found
    assert not of_kind(core, "chat.conversation.changed")


def test_a_failed_reply_off_the_record_still_says_which_model_was_asked(core):
    models(core, home=FakeProvider("openai_compatible", "timeout"),
           cloud=FakeProvider("openai_compatible", "timeout"))
    conv_id = chat.start_conversation(core, on_record=False)["id"]
    reply = ask(core, conv_id, QUIET_Q)
    assert (reply["state"], reply["on_record"]) == ("failed", False)
    assert reply["provider_id"] == "cloud" and reply["fallback_from"] == "home/model-a"
    assert chat.get_conversation(core, conv_id)["messages"][-1] == reply
    assert not [e for e in core.tape.after(0, limit=100000) if e.kind.startswith("chat.")]


# --- one thread from two places -------------------------------------------------------


def test_the_model_is_sent_both_kinds_of_message_in_the_order_they_were_said(core):
    conv_id = chat.start_conversation(core)["id"]
    ask(core, conv_id, "One", reply="First")
    flip(core, conv_id, False)
    ask(core, conv_id, "Two", reply="Second")
    flip(core, conv_id, True)
    core.settings.set("chat.system_prompt", "", actor="owner")
    fakes = answers(core, "Third")
    ask(core, conv_id, "Three")
    assert [(m["role"], m["content"]) for m in sent_to(fakes[HOME])] == [
        ("user", "One"), ("assistant", "First"), ("user", "Two"), ("assistant", "Second"),
        ("user", "Three")]


def test_a_mixed_conversation_pages_back_through_both_places_without_a_gap(core):
    conv_id = chat.start_conversation(core, on_record=False)["id"]
    said = []
    for n in range(6):
        flip(core, conv_id, n % 3 == 1)  # off, on, off, off, on, off
        ask(core, conv_id, f"Question {n}", reply=f"Answer {n}")
        said += [(f"Question {n}", n % 3 == 1), (f"Answer {n}", n % 3 == 1)]
    assert shown(core, conv_id) == said
    for limit in (1, 2, 3, 5, 12, 50):
        pages, before = [], None
        while True:
            page = chat.get_conversation(core, conv_id, limit=limit, before=before)
            assert page["conversation"]["messages"] == 12
            pages.insert(0, [(m["text"], m["on_record"]) for m in page["messages"]])
            before = page["older"]
            if before is None:
                break
        assert [m for page in pages for m in page] == said, limit
        assert all(len(page) == limit for page in pages[1:])


def test_a_regenerated_reply_is_kept_where_the_switch_says_at_that_moment(core, cfg):
    # Asked and answered on the record, then regenerated off it.
    conv_id = chat.start_conversation(core)["id"]
    ask(core, conv_id, OPEN_Q, reply=OPEN_A)
    flip(core, conv_id, False)
    reply = again(core, conv_id, QUIET_A)
    assert (reply["text"], reply["on_record"]) == (QUIET_A, False)
    assert shown(core, conv_id) == [(OPEN_Q, True), (QUIET_A, False)]
    assert len(of_kind(core, "chat.message.answered")) == 1  # the first, still on the Tape
    # Deleting the newer reply leaves what the Tape holds: the first one.
    assert chat.delete_off_record(core, conv_id)["deleted"] == 1
    assert shown(core, conv_id) == [(OPEN_Q, True), (OPEN_A, True)]

    # Asked and answered off the record, then regenerated on it.
    other = chat.start_conversation(core, on_record=False)["id"]
    ask(core, other, QUIET_Q, reply=QUIET_A)
    flip(core, other, True)
    reply = again(core, other, "Ask me off the record.")
    assert reply["on_record"] is True
    assert shown(core, other) == [(QUIET_Q, False), ("Ask me off the record.", True)]
    assert rows(cfg, "messages") == 1  # the old reply was removed, not hidden
    again(core, other, "Still not saying.")
    assert shown(core, other) == [(QUIET_Q, False), ("Still not saying.", True)]
    # And in the mode it was asked in, as before.
    flip(core, other, False)
    ask(core, other, "Two", reply="Second")
    again(core, other, "Second, again")
    assert shown(core, other)[-2:] == [("Two", False), ("Second, again", False)]
    recorded = json.dumps([e.model_dump() for e in core.tape.after(0, limit=100000)])
    assert "walnut" not in recorded and "flour" not in recorded and "Second" not in recorded


def test_a_restart_and_a_second_process_see_the_same_thread(core, cfg):
    conv_id = chat.start_conversation(core)["id"]
    ask(core, conv_id, OPEN_Q, reply=OPEN_A)
    flip(core, conv_id, False)
    ask(core, conv_id, QUIET_Q, reply=QUIET_A)
    quiet = chat.start_conversation(core, title="Hazelnut errands", on_record=False)["id"]
    second = open_core(cfg)  # the worker, say: another process on the same folder
    try:
        for each in (core, second):
            assert shown(each, conv_id) == [(OPEN_Q, True), (OPEN_A, True), (QUIET_Q, False),
                                            (QUIET_A, False)]
            assert {c["id"]: c["on_record"] for c in
                    chat.list_conversations(each)["conversations"]} == {conv_id: False,
                                                                        quiet: False}
        # Each sees what the other writes, off the record included.
        ask(second, quiet, "Is it Tuesday?", reply="It is.")
        chat.change_conversation(second, quiet, title="Errands")
        assert shown(core, quiet) == [("Is it Tuesday?", False), ("It is.", False)]
        assert chat.get_conversation(core, quiet)["conversation"]["title"] == "Errands"
        ask(core, quiet, "And tomorrow?", reply="Wednesday.")
        assert len(shown(second, quiet)) == 4
        assert chat.delete_off_record(second, quiet)["conversation"] is None
        assert [c["id"] for c in chat.list_conversations(core)["conversations"]] == [conv_id]
    finally:
        second.close()
    core.close()
    after = open_core(cfg)  # and after a restart
    try:
        assert shown(after, conv_id) == [(OPEN_Q, True), (OPEN_A, True), (QUIET_Q, False),
                                         (QUIET_A, False)]
        assert chat.get_conversation(after, conv_id)["conversation"]["on_record"] is False
    finally:
        after.close()


def test_two_processes_starting_off_the_record_on_a_fresh_folder_do_not_trip(core, cfg):
    second = open_core(cfg)
    try:
        made, errors = [], []

        def start(each):
            try:
                for _ in range(10):
                    made.append(chat.start_conversation(each, on_record=False)["id"])
            except Exception as exc:  # noqa: BLE001 - the test reports it
                errors.append(exc)

        threads = [threading.Thread(target=start, args=(each,)) for each in (core, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert not errors and len(set(made)) == 20
        assert len(chat.list_conversations(core, limit=200)["conversations"]) == 20
    finally:
        second.close()


def test_a_reply_cut_short_off_the_record_leaves_the_conversation_ready_not_stuck(core, cfg):
    models(core, home=SlowProvider())
    conv_id = chat.start_conversation(core, on_record=False)["id"]

    async def dies_mid_reply():
        run = await replies.begin(core, conv_id, text=QUIET_Q)
        run.finished = True  # the process is gone: nothing more is ever stored
        run.task.cancel()

    asyncio.run(dies_mid_reply())
    after = open_core(cfg)
    try:
        detail = chat.get_conversation(after, conv_id)
        assert [m["role"] for m in detail["messages"]] == ["user"]
        assert detail["unanswered"] is True and detail["can_regenerate"] is True
        assert again(after, conv_id, QUIET_A)["reply_to"] == detail["messages"][0]["id"]
    finally:
        after.close()


def test_stopping_the_server_keeps_an_off_the_record_reply_in_flight_off_the_record(core):
    models(core, home=SlowProvider())
    conv_id = chat.start_conversation(core, on_record=False)["id"]

    async def scenario():
        run = await replies.begin(core, conv_id, text=QUIET_Q)
        while run.queue.empty():
            await asyncio.sleep(0.005)
        await chat.shutdown(core)
        return run.message

    reply = asyncio.run(scenario())
    assert (reply["state"], reply["text"], reply["on_record"]) == ("stopped", "Hello", False)
    assert shown(core, conv_id) == [(QUIET_Q, False), ("Hello", False)]
    assert not of_kind(core, "chat.message.answered", "chat.message.sent")
    (receipt,) = of_kind(core, "model.failed")
    assert receipt.payload["caller"] == "chat.reply" and receipt.cause is None
    assert conv_id not in store.svc(core).runs


# --- the first time on the record -----------------------------------------------------


def test_a_conversation_begun_off_the_record_reaches_the_tape_untitled_when_flipped_on(
        core, cfg, caplog):
    conv_id = chat.start_conversation(core, on_record=False)["id"]
    ask(core, conv_id, QUIET_Q, reply=QUIET_A)  # names it, off the record
    chat.change_conversation(core, conv_id, title=QUIET_TITLE)
    assert not of_kind(core, "chat.conversation.started")
    conv = flip(core, conv_id, True)
    assert (conv["on_record"], conv["recorded"], conv["title"]) == (True, True, QUIET_TITLE)
    (started,) = of_kind(core, "chat.conversation.started")
    assert started.subject == store.subject(conv_id)
    assert (started.payload["title"], started.payload["title_source"]) == ("", "none")
    ask(core, conv_id, OPEN_Q, reply=OPEN_A)
    # The owner still sees the name they gave it; the Tape has only its own.
    assert chat.get_conversation(core, conv_id)["conversation"]["title"] == QUIET_TITLE
    assert core.lenses.read(store.svc(core).lens)["conversations"][conv_id]["title"] == OPEN_Q
    found = everything_recorded(core, cfg, caplog)
    assert "alnut" not in found and "flour" not in found
    # A rename while on the record is recorded, and is the name from then on.
    conv = chat.change_conversation(core, conv_id, title="Beans")
    assert (conv["title"], conv["off_record_title"]) == ("Beans", False)
    assert of_kind(core, "chat.conversation.changed")[-1].payload["title"] == "Beans"
    assert shown(core, conv_id) == [(QUIET_Q, False), (QUIET_A, False), (OPEN_Q, True),
                                    (OPEN_A, True)]


def test_a_title_sent_together_with_a_flip_stays_off_the_record(core):
    conv_id = chat.start_conversation(core)["id"]
    conv = chat.change_conversation(core, conv_id, title=QUIET_TITLE, on_record=False)
    assert (conv["title"], conv["on_record"]) == (QUIET_TITLE, False)
    conv = chat.change_conversation(core, conv_id, title="Walnut plans", on_record=True)
    assert (conv["title"], conv["on_record"]) == ("Walnut plans", True)
    assert "alnut" not in json.dumps([e.model_dump() for e in core.tape.after(0, limit=1000)])


def test_new_conversations_start_as_the_setting_says_and_old_ones_keep_their_own(core):
    first = chat.start_conversation(core)
    assert first["on_record"] is True
    core.settings.set("chat.on_record", False, actor="owner")
    second = chat.start_conversation(core)
    assert second["on_record"] is False and second["recorded"] is False
    assert chat.start_conversation(core, on_record=True)["on_record"] is True
    assert chat.get_conversation(core, first["id"])["conversation"]["on_record"] is True
    core.settings.reset("chat.on_record", actor="owner")
    assert chat.get_conversation(core, second["id"])["conversation"]["on_record"] is False
    with pytest.raises(chat.ChatError) as odd:
        chat.start_conversation(core, on_record="no")
    assert odd.value.status == 422


def test_a_tape_from_before_the_switch_existed_lists_and_opens_as_it_always_did(core, cfg):
    conv_id, user, reply = (f"01JABCDEFGHJKMNPQRSTVWXYZ{n}" for n in range(3))
    role = {"kind": "role", "role": "default"}

    def put(kind, payload, **lineage):
        return core.tape.print(kind, {"conversation_id": conv_id, **payload}, actor="owner",
                               subject=store.subject(conv_id), **lineage)

    started = put("chat.conversation.started", {"title": "", "title_source": "none",
                                                "target": role, "effort": None,
                                                "local_only": False})
    put("chat.message.sent", {"message_id": user, "text": OPEN_Q, "text_ref": None,
                              "chars": len(OPEN_Q), "title": OPEN_Q}, root=started.id)
    put("chat.message.answered", {
        "message_id": reply, "reply_to": user, "replaces": None, "state": "complete",
        "text": OPEN_A, "text_ref": None, "chars": len(OPEN_A), "error": None,
        "error_kind": None, "provider_id": "home", "provider_type": "openai_compatible",
        "model": "model-a", "local": True, "fallback_from": None, "fallback_reason": None,
        "input_tokens": 3, "output_tokens": 2, "duration_ms": 5, "omitted": 0}, root=started.id)
    before = core.tape.last_seq()
    (listed,) = chat.list_conversations(core)["conversations"]
    assert (listed["id"], listed["title"], listed["on_record"], listed["recorded"]) == (
        conv_id, OPEN_Q, True, True)
    assert (listed["messages"], listed["off_record_messages"]) == (2, 0)
    assert shown(core, conv_id) == [(OPEN_Q, True), (OPEN_A, True)]
    # Reading changes nothing, and does not so much as create the other store.
    assert core.tape.last_seq() == before and not off_file(cfg).exists()
    ask(core, conv_id, "And which peas?", reply="Sugar snaps.")
    assert len(shown(core, conv_id)) == 4 and not off_file(cfg).exists()


# --- deleting -------------------------------------------------------------------------


def test_deleting_what_is_off_the_record_leaves_what_is_on_the_record_intact(core, cfg):
    conv_id = chat.start_conversation(core)["id"]
    ask(core, conv_id, OPEN_Q, reply=OPEN_A)
    flip(core, conv_id, False)
    ask(core, conv_id, QUIET_Q, reply=QUIET_A)
    chat.change_conversation(core, conv_id, title=QUIET_TITLE)
    before = core.tape.last_seq()
    result = chat.delete_off_record(core, conv_id)
    assert result["deleted"] == 2
    left = result["conversation"]
    assert (left["title"], left["on_record"], left["off_record_messages"]) == (OPEN_Q, False, 0)
    assert core.tape.last_seq() == before  # the deletion itself is not recorded
    assert shown(core, conv_id) == [(OPEN_Q, True), (OPEN_A, True)]
    assert (rows(cfg, "messages"), rows(cfg, "conversations")) == (0, 0)
    # The model is sent only what is still stored.
    core.settings.set("chat.system_prompt", "", actor="owner")
    fakes = answers(core, "Sugar snaps.")
    ask(core, conv_id, "And which peas?")
    assert [m["content"] for m in sent_to(fakes[HOME])] == [OPEN_Q, OPEN_A, "And which peas?"]
    # It is still an ordinary conversation: it archives, and there is nothing left to delete.
    assert chat.archive_conversation(core, conv_id)["archived"] is True
    chat.restore_conversation(core, conv_id)
    chat.delete_off_record(core, conv_id)  # the message just sent, off the record
    with pytest.raises(chat.ChatError) as nothing:
        chat.delete_off_record(core, conv_id)
    assert nothing.value.status == 409 and "whole conversation" in nothing.value.detail


def test_deleted_words_are_gone_from_the_file_and_the_file_does_not_keep_growing(core, cfg):
    answers(core, QUIET_A)
    sizes = []
    for round_ in range(12):
        conv_id = chat.start_conversation(core, on_record=False)["id"]
        ask(core, conv_id, QUIET_Q + " pistachio" * 2000)
        chat.delete_off_record(core, conv_id)
        sizes.append(sum(p.stat().st_size for p in cfg.data.glob(offrecord.FILE + "*")))
        if round_ == 0:
            assert (rows(cfg, "messages"), rows(cfg, "conversations")) == (0, 0)
            held = b"".join(p.read_bytes() for p in cfg.data.glob(offrecord.FILE + "*"))
            assert b"pistachio" not in held and b"walnut" not in held and b"flour" not in held
    assert sizes[-1] <= sizes[1]


def test_an_archived_conversation_restored_after_leaving_the_list_keeps_its_switch(
        core, monkeypatch):
    monkeypatch.setattr(store, "ARCHIVED_CAP", 1)
    conv_id = chat.start_conversation(core)["id"]
    flip(core, conv_id, False)
    ask(core, conv_id, QUIET_Q, reply=QUIET_A)
    chat.archive_conversation(core, conv_id)
    for _ in range(2):  # push it out of the list
        chat.archive_conversation(core, chat.start_conversation(core)["id"])
    assert conv_id not in {c["id"] for c in
                           chat.list_conversations(core, archived=True)["conversations"]}
    assert chat.get_conversation(core, conv_id)["conversation"]["on_record"] is False
    chat.restore_conversation(core, conv_id)
    (listed,) = chat.list_conversations(core)["conversations"]
    assert (listed["id"], listed["on_record"]) == (conv_id, False)
    assert shown(core, conv_id) == [(QUIET_Q, False), (QUIET_A, False)]


# --- over HTTP ------------------------------------------------------------------------


def events_of(response) -> list[tuple[str, dict]]:
    assert response.status_code == 200, response.text
    out = []
    for block in response.text.split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        if lines:
            out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


def test_the_switch_the_stream_and_the_delete_work_over_http(owner, core, cfg, caplog):
    caplog.set_level(logging.DEBUG)
    assert owner.get("/api/chat/options").json()["defaults"]["on_record"] is True
    core.settings.set("chat.on_record", False, actor="owner")
    assert owner.get("/api/chat/options").json()["defaults"]["on_record"] is False
    answers(core, QUIET_A)
    made = owner.post(BASE, json={}).json()
    conv_id = made["id"]
    assert (made["on_record"], made["recorded"]) == (False, False)
    names = events_of(owner.post(f"{BASE}/{conv_id}/messages", json={"text": QUIET_Q}))
    assert [n for n, _ in names][0] == "start" and names[-1][0] == "done"
    assert names[-1][1]["message"]["on_record"] is False
    assert names[0][1]["user_message"]["on_record"] is False

    r = owner.patch(f"{BASE}/{conv_id}", json={"on_record": True})
    assert r.status_code == 200 and r.json()["on_record"] is True
    answers(core, OPEN_A)
    events_of(owner.post(f"{BASE}/{conv_id}/messages", json={"text": OPEN_Q}))
    page = owner.get(f"{BASE}/{conv_id}").json()
    assert [(m["text"], m["on_record"]) for m in page["messages"]] == [
        (QUIET_Q, False), (QUIET_A, False), (OPEN_Q, True), (OPEN_A, True)]
    assert page["conversation"]["off_record_messages"] == 2
    (listed,) = owner.get(BASE).json()["conversations"]
    assert (listed["id"], listed["on_record"], listed["off_record_messages"]) == (
        conv_id, True, 2)

    found = everything_recorded(core, cfg, caplog)
    assert OPEN_Q in found and "walnut" not in found and "flour" not in found

    r = owner.delete(f"{BASE}/{conv_id}/off-record")
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert r.json()["conversation"]["off_record_messages"] == 0
    assert [m["text"] for m in owner.get(f"{BASE}/{conv_id}").json()["messages"]] == [
        OPEN_Q, OPEN_A]
    r = owner.delete(f"{BASE}/{conv_id}/off-record")
    assert r.status_code == 409 and "whole conversation" in r.json()["detail"]

    quiet = owner.post(BASE, json={"on_record": False, "title": "Hazelnut errands"}).json()["id"]
    assert owner.delete(f"{BASE}/{quiet}/off-record").json() == {"deleted": 0,
                                                                 "conversation": None}
    assert owner.get(f"{BASE}/{quiet}").status_code == 404
    assert owner.delete(f"{BASE}/{quiet}/off-record").status_code == 404


def test_only_the_owner_may_flip_the_switch_or_delete_and_odd_input_gets_a_sentence(
        owner, core):
    conv_id = owner.post(BASE, json={"on_record": False}).json()["id"]
    secret = core.auth.create_token("scout", ["read", "write"])[1]
    with TestClient(owner.app, base_url="http://testserver",
                    headers={"authorization": f"Bearer {secret}"}) as token:
        assert token.get(f"{BASE}/{conv_id}").status_code == 404  # not there, for a token
        assert token.patch(f"{BASE}/{conv_id}", json={"on_record": True}).status_code == 403
        assert token.delete(f"{BASE}/{conv_id}/off-record").status_code == 403
    for body in ({"on_record": "yes"}, {"on_record": 1}, {"on_record": None}):
        r = owner.patch(f"{BASE}/{conv_id}", json=body)
        assert r.status_code == 422 and r.json()["detail"], body
        assert owner.post(BASE, json=body).status_code in (201, 422)
    assert owner.post(BASE, json={"on_record": "yes"}).status_code == 422
    for odd in ("nope", "01JABCDEFGHJKMNPQRSTVWXYZ0", "%00"):
        r = owner.delete(f"{BASE}/{odd}/off-record")
        assert r.status_code == 404 and isinstance(r.json()["detail"], str)
    # Sending the switch as it already stands changes nothing and prints nothing.
    before = core.tape.last_seq()
    assert owner.patch(f"{BASE}/{conv_id}", json={"on_record": False}).status_code == 200
    assert core.tape.last_seq() == before
    owner.post(f"{BASE}/{conv_id}/archive")
    r = owner.patch(f"{BASE}/{conv_id}", json={"on_record": True})
    assert r.status_code == 409 and "Restore" in r.json()["detail"]


def test_the_switch_cannot_be_flipped_nor_anything_deleted_while_a_reply_is_being_written(
        owner, core):
    home = SlowProvider()
    models(core, home=home)
    conv_id = owner.post(BASE, json={"on_record": False}).json()["id"]
    result = {}

    def first_tab():
        result["events"] = events_of(
            owner.post(f"{BASE}/{conv_id}/messages", json={"text": QUIET_Q}))

    tab = threading.Thread(target=first_tab)
    tab.start()
    try:
        assert home.streaming.wait(10)
        r = owner.patch(f"{BASE}/{conv_id}", json={"on_record": True})
        assert r.status_code == 409 and "reply" in r.json()["detail"]
        r = owner.delete(f"{BASE}/{conv_id}/off-record")
        assert r.status_code == 409 and "reply" in r.json()["detail"]
    finally:
        home.release.set()
        tab.join(10)
    assert result["events"][-1][1]["message"]["on_record"] is False
    assert owner.patch(f"{BASE}/{conv_id}", json={"on_record": True}).status_code == 200
    assert CLOUD  # the hosted fake is wired but never asked


def test_the_access_log_names_no_conversation_and_no_title_search(owner, caplog):
    caplog.set_level(logging.INFO)
    access = logging.getLogger("uvicorn.access")
    conv_id = owner.post(BASE, json={"on_record": False}).json()["id"]
    line = '%s - "%s %s HTTP/%s" %d'  # how the server writes each request down
    for path in (f"{BASE}/{conv_id}", f"{BASE}/{conv_id}/messages", f"{BASE}/{conv_id}/off-record",
                 f"{BASE}?archived=false&q=walnut+cake", f"{BASE}/{conv_id}?limit=50"):
        access.info(line, "127.0.0.1:5000", "GET", path, "1.1", 200)
    access.info(line, "127.0.0.1:5000", "GET", "/api/tape?limit=5", "1.1", 200)
    assert conv_id not in caplog.text and "walnut" not in caplog.text
    assert f'"GET {BASE}/-/messages HTTP/1.1" 200' in caplog.text
    assert f'"GET {BASE} HTTP/1.1" 200' in caplog.text
    assert "/api/tape?limit=5" in caplog.text  # other routes are written down as before
