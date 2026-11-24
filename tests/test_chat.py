"""Chat from Python: sending, stopping, regenerating, and what ends up stored."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading

import httpx
import pytest

from jimmyhq import chat
from jimmyhq.chat import replies, store
from jimmyhq.core import open_core
from jimmyhq.models import Target, brain
from test_chat_fakes import CLOUD, HOME, SlowProvider, models, of_kind, sent_to
from test_models_fakes import PIECES, TEXT, TOKENS_IN, TOKENS_OUT, FakeProvider

QUESTION = "What grows well in a shady corner?"


async def collect(stream):
    return [item async for item in stream]


def ask(core, conv_id, text=QUESTION, **how):
    return asyncio.run(collect(chat.send(core, conv_id, text, **how)))


def new(core, **fields):
    return chat.start_conversation(core, **fields)["id"]


def messages(core, conv_id, **page):
    return chat.get_conversation(core, conv_id, **page)["messages"]


async def until(condition, seconds=5.0):
    for _ in range(int(seconds / 0.005)):
        if condition():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the condition never became true")


def test_a_send_yields_the_pieces_then_the_stored_reply(core):
    models(core)
    conv_id = new(core)
    *pieces, reply = ask(core, conv_id)
    assert tuple(pieces) == PIECES
    assert (reply["role"], reply["state"], reply["text"]) == ("assistant", "complete", TEXT)
    assert (reply["provider_id"], reply["provider_type"], reply["model"]) == (
        "home", "openai_compatible", "model-a")
    assert reply["local"] is True and reply["fallback_from"] is None
    assert (reply["input_tokens"], reply["output_tokens"]) == (TOKENS_IN, TOKENS_OUT)
    assert reply["error"] is None and reply["duration_ms"] >= 0 and reply["omitted"] == 0
    user, stored = messages(core, conv_id)
    assert (user["role"], user["text"]) == ("user", QUESTION)
    assert stored == reply and reply["reply_to"] == user["id"]
    conv = chat.get_conversation(core, conv_id)["conversation"]
    assert conv["messages"] == 2 and conv["streaming"] is False
    assert conv["title"] == QUESTION and conv["title_source"] == "cut"


def test_a_reply_is_printed_once_and_not_per_piece(core):
    models(core)
    ask(core, new(core))
    assert len(of_kind(core, "chat.message.sent")) == 1
    (answered,) = of_kind(core, "chat.message.answered")
    assert answered.actor == "system" and answered.payload["text"] == TEXT
    assert answered.cause == of_kind(core, "chat.message.sent")[0].id


def test_no_message_text_appears_in_any_model_receipt(core):
    home = SlowProvider(then="fail")
    home.release.set()
    models(core, home=home, cheap=("home", "model-a"))
    core.settings.set("chat.model_titles", True, actor="owner")
    conv_id = new(core)
    ask(core, conv_id, "A private question about tomatoes")  # fails part-way
    models(core, cheap=("home", "model-a"))
    asyncio.run(collect(chat.regenerate(core, conv_id)))
    receipts = of_kind(core, "model.called", "model.failed")
    assert {e.kind for e in receipts} == {"model.called", "model.failed"}
    assert {e.payload["caller"] for e in receipts} == {"chat.reply", "chat.title"}
    for event in receipts:
        stored = json.dumps(event.model_dump())
        for words in ("tomatoes", "private question", "Hello", "From the cloud"):
            assert words not in stored
        if event.kind == "model.called":
            assert event.payload["prompt_ref"] is None
            assert event.payload["completion_ref"] is None


def test_the_model_is_sent_the_instructions_and_who_it_is_talking_to(core):
    fakes = models(core)
    core.settings.set("owner.name", "Sam", actor="owner")
    conv_id = new(core)
    ask(core, conv_id)
    system, user = sent_to(fakes[HOME])
    assert system["role"] == "system" and system["content"].endswith("You are talking with Sam.")
    assert system["content"].startswith(core.settings.get("chat.system_prompt"))
    assert user == {"role": "user", "content": QUESTION}
    core.settings.set("chat.system_prompt", "", actor="owner")
    core.settings.set("owner.name", "", actor="owner")
    ask(core, conv_id, "And in full sun?")
    assert [m["role"] for m in sent_to(fakes[HOME])] == ["user", "assistant", "user"]


def test_a_failure_part_way_is_stored_as_failed_and_never_sent_back_as_an_answer(core):
    home = SlowProvider(then="fail")
    home.release.set()
    models(core, home=home)
    conv_id = new(core)
    *pieces, reply = ask(core, conv_id)
    assert pieces == ["Hello"]
    assert reply["state"] == "failed" and reply["text"] == "Hello"
    assert "could not be reached" in reply["error"]
    assert (reply["provider_id"], reply["model"], reply["local"]) == ("home", "model-a", True)
    assert reply["input_tokens"] is None
    detail = chat.get_conversation(core, conv_id)
    assert detail["messages"][-1] == reply and detail["can_regenerate"] is True
    fakes = models(core)
    ask(core, conv_id, "Try again please")
    assert sent_to(fakes[HOME])[1:] == [
        {"role": "user", "content": f"{QUESTION}\n\nTry again please"}]


def test_a_fallback_is_passed_through_so_the_screen_can_show_it(core):
    models(core, home=FakeProvider("openai_compatible", "down"))
    *_, reply = ask(core, new(core))
    assert (reply["text"], reply["provider_id"], reply["local"]) == (
        "From the cloud", "cloud", False)
    assert reply["fallback_from"] == "home/model-a"
    assert reply["fallback_reason"].startswith("Home ")


def test_local_only_keeps_a_conversation_off_hosted_providers(core):
    fakes = models(core, home=FakeProvider("openai_compatible", "down"))
    conv_id = new(core, local_only=True)
    *_, reply = ask(core, conv_id)
    assert reply["state"] == "failed" and not fakes[CLOUD].requests
    b = brain(core)
    b.set_role("default", Target("cloud", "model-b"), actor="owner")
    with pytest.raises(chat.ChatError) as refused:
        ask(core, conv_id)
    assert refused.value.status == 409 and "local" in refused.value.detail
    assert len(messages(core, conv_id)) == 2  # the refused send stored nothing
    core.settings.set("chat.local_only", True, actor="owner")
    assert chat.start_conversation(core)["local_only"] is True


def test_stopping_keeps_what_had_arrived_and_marks_it(core):
    home = SlowProvider()
    models(core, home=home)
    conv_id = new(core)

    async def scenario():
        stream = chat.send(core, conv_id, QUESTION)
        got = [await anext(stream)]
        assert chat.get_conversation(core, conv_id)["conversation"]["streaming"] is True
        assert (await chat.stop(core, conv_id)) == {"stopped": True}
        got += [item async for item in stream]
        return got

    piece, reply = asyncio.run(scenario())
    assert piece == "Hello"
    assert (reply["state"], reply["text"], reply["error"]) == ("stopped", "Hello", None)
    assert (reply["provider_id"], reply["local"]) == ("home", True)
    detail = chat.get_conversation(core, conv_id)
    assert detail["messages"][-1] == reply and detail["conversation"]["streaming"] is False
    (failed,) = of_kind(core, "model.failed")
    assert failed.payload["reason"] == "abandoned"
    assert asyncio.run(chat.stop(core, conv_id)) == {"stopped": False}


def test_a_listener_that_goes_away_stops_the_model_call_and_the_part_is_kept(core):
    home = SlowProvider()
    models(core, home=home)
    conv_id = new(core)

    async def scenario():
        stream = chat.send(core, conv_id, QUESTION)
        assert await anext(stream) == "Hello"
        await stream.aclose()  # the caller walked away
        await until(lambda: conv_id not in store.svc(core).runs)

    asyncio.run(scenario())
    (reply,) = [m for m in messages(core, conv_id) if m["role"] == "assistant"]
    assert (reply["state"], reply["text"]) == ("stopped", "Hello")
    assert not home.release.is_set()  # nobody let the model finish


def test_a_dropped_connection_is_noticed_even_while_the_model_is_quiet(core):
    models(core, home=SlowProvider())
    conv_id = new(core)

    async def scenario():
        gone = False

        async def disconnected():
            return gone

        run = await replies.begin(core, conv_id, text=QUESTION)
        names = []
        async with contextlib.aclosing(
                replies.listen(run, disconnected=disconnected, poll_s=0.01)) as events:
            async for name, _ in events:
                names.append(name)
                gone = name == "delta"
        await until(lambda: run.message is not None)
        return names, run.message

    names, reply = asyncio.run(scenario())
    assert names == ["start", "delta"]
    assert (reply["state"], reply["text"]) == ("stopped", "Hello")


def test_a_quiet_model_gets_keep_alives_and_the_stream_still_ends_once(core):
    home = SlowProvider()
    models(core, home=home)
    conv_id = new(core)

    async def scenario():
        run = await replies.begin(core, conv_id, text=QUESTION)
        names = []
        async for name, _ in replies.listen(run, poll_s=0.01, keepalive_s=0.02):
            names.append(name)
            if name == "ping":
                home.release.set()
        return names

    names = asyncio.run(scenario())
    assert names[0] == "start" and names[-1] == "done" and "ping" in names
    assert names.count("done") == 1 and names.count("delta") == 3


def test_only_one_reply_at_a_time_is_written_in_a_conversation(core):
    home = SlowProvider()
    models(core, home=home)
    conv_id, other = new(core), new(core)

    async def scenario():
        stream = chat.send(core, conv_id, QUESTION)
        await anext(stream)
        for attempt in (chat.send(core, conv_id, "And another thing"),
                        chat.regenerate(core, conv_id)):
            with pytest.raises(chat.ChatError) as refused:
                await anext(attempt)
            assert refused.value.status == 409 and "already" in refused.value.detail
        with pytest.raises(chat.ChatError) as busy:
            chat.archive_conversation(core, conv_id)
        assert busy.value.status == 409
        home.release.set()
        rest = [item async for item in stream]
        elsewhere = [item async for item in chat.send(core, other, QUESTION)]
        return rest[-1], elsewhere[-1]

    reply, elsewhere = asyncio.run(scenario())
    assert reply["state"] == "complete" and reply["text"] == "Hello there friend"
    assert elsewhere["state"] == "complete"
    assert len(messages(core, conv_id)) == 2  # the refused send stored nothing


def test_regenerate_shows_the_newest_reply_and_keeps_the_old_one_on_the_tape(core):
    models(core)
    conv_id = new(core)
    *_, first = ask(core, conv_id)
    fakes = models(core, home=FakeProvider("openai_compatible", text="A second opinion"))

    async def again():
        run = await replies.begin(core, conv_id, regenerate=True)
        return run.start, [e async for e in replies.listen(run)]

    start, events = asyncio.run(again())
    second = events[-1][1]["message"]
    assert start["regenerate"] is True and start["replaces"] == first["id"]
    assert start["user_message"]["text"] == QUESTION
    assert sent_to(fakes[HOME])[1:] == [{"role": "user", "content": QUESTION}]
    assert second["text"] == "A second opinion" and second["id"] != first["id"]
    shown = messages(core, conv_id)
    assert [m["id"] for m in shown] == [second["reply_to"], second["id"]]
    assert chat.get_conversation(core, conv_id)["conversation"]["messages"] == 2
    assert chat.list_conversations(core)["conversations"][0]["messages"] == 2
    answered = of_kind(core, "chat.message.answered")
    assert [e.payload["text"] for e in answered] == [TEXT, "A second opinion"]
    assert answered[1].payload["replaces"] == first["id"]


def test_only_the_last_exchange_can_be_regenerated(core):
    fakes = models(core)
    conv_id = new(core)
    with pytest.raises(chat.ChatError) as nothing:
        asyncio.run(collect(chat.regenerate(core, conv_id)))
    assert nothing.value.status == 409
    ask(core, conv_id, "First question")
    ask(core, conv_id, "Second question")
    *_, reply = asyncio.run(collect(chat.regenerate(core, conv_id)))
    shown = messages(core, conv_id)
    assert [m["role"] for m in shown] == ["user", "assistant", "user", "assistant"]
    assert shown[-1]["id"] == reply["id"] and shown[-1]["reply_to"] == shown[2]["id"]
    assert sent_to(fakes[HOME])[-1] == {"role": "user", "content": "Second question"}


def test_the_context_budget_drops_the_oldest_and_always_keeps_the_newest_message(core):
    fakes = models(core)
    core.settings.set("chat.system_prompt", "", actor="owner")
    conv_id = new(core)
    for n in range(3):
        ask(core, conv_id, f"Question {n}")
    core.settings.set("chat.context_messages", 3, actor="owner")

    async def fourth():
        run = await replies.begin(core, conv_id, text="Question 3")
        return run.start, [e async for e in replies.listen(run)][-1][1]["message"]

    start, reply = asyncio.run(fourth())
    assert [m["content"] for m in sent_to(fakes[HOME])] == ["Question 2", TEXT, "Question 3"]
    assert start["omitted"] == 4 and reply["omitted"] == 4
    core.settings.set("chat.context_messages", 40, actor="owner")
    core.settings.set("chat.context_chars", 1000, actor="owner")
    long = "word " * 400  # longer than the whole budget on its own
    ask(core, conv_id, long)
    assert sent_to(fakes[HOME]) == [{"role": "user", "content": long}]
    ask(core, conv_id, "Short again")
    # The long message does not fit, so nothing before the newest one is sent.
    assert sent_to(fakes[HOME]) == [{"role": "user", "content": "Short again"}]


def test_a_100_kb_message_goes_through_the_vault_and_comes_back_whole(core):
    big_reply = "An answer that goes on. " * 5000
    fakes = models(core, home=FakeProvider("openai_compatible", text=big_reply))
    conv_id = new(core, title="Notes")
    big = "".join(f"Line {n} of a long note with an accent: é\n" for n in range(2600))
    assert len(big.encode()) > 100_000
    *_, reply = ask(core, conv_id, big)
    assert reply["text"] == big_reply
    user, stored = messages(core, conv_id)
    assert user["text"] == big and stored["text"] == big_reply
    assert sent_to(fakes[HOME])[-1]["content"] == big
    for event in of_kind(core, "chat.message.sent", "chat.message.answered"):
        assert event.payload["text"] == "" and core.vault.has(event.payload["text_ref"])
    lens = json.dumps(core.lenses.read(store.svc(core).lens))
    assert "long note" not in lens and "goes on" not in lens and len(lens) < 1000
    with pytest.raises(chat.ChatError) as too_long:
        ask(core, conv_id, "x" * (core.settings.get("chat.max_message_chars") + 1))
    assert too_long.value.status == 422 and "Settings, Chat" in too_long.value.detail


def test_messages_are_paged_from_the_newest_backwards(core):
    models(core)
    conv_id = new(core)
    for n in range(3):
        ask(core, conv_id, f"Question {n}")
    newest = chat.get_conversation(core, conv_id, limit=4)
    assert [m["text"] for m in newest["messages"]] == ["Question 1", TEXT, "Question 2", TEXT]
    older = chat.get_conversation(core, conv_id, limit=4, before=newest["older"])
    assert [m["text"] for m in older["messages"]] == ["Question 0", TEXT]
    assert older["older"] is None and older["conversation"]["messages"] == 6
    for bad in ("nonsense", "01JABCDEFGHJKMNPQRSTVWXYZ0", ""):
        with pytest.raises(chat.ChatError) as refused:
            chat.get_conversation(core, conv_id, before=bad)
        assert refused.value.status == 422


def test_opening_a_long_conversation_reads_only_its_newest_events(core, monkeypatch):
    models(core)
    conv_id = new(core)
    for n in range(12):
        ask(core, conv_id, f"Question {n}")
    monkeypatch.setattr(store, "READ_EVENTS", 4)
    read = []
    by_subject = core.tape.by_subject

    def counted(*args, **kwargs):
        events = by_subject(*args, **kwargs)
        read.append(len(events))
        return events

    monkeypatch.setattr(core.tape, "by_subject", counted)
    newest = chat.get_conversation(core, conv_id, limit=4)
    assert [m["text"] for m in newest["messages"]] == ["Question 10", TEXT, "Question 11", TEXT]
    assert newest["older"] == newest["messages"][0]["id"]
    assert newest["conversation"]["messages"] == 24 and newest["unanswered"] is False
    assert sum(read) <= 8  # not the 25 events of its whole story


def test_the_newest_page_is_the_same_however_it_is_read(core, monkeypatch):
    models(core, home=FakeProvider("openai_compatible", "timeout"),
           cloud=FakeProvider("openai_compatible", "timeout"))
    conv_id = new(core)
    ask(core, conv_id, "Question 0")  # a failed reply
    models(core)
    asyncio.run(collect(chat.regenerate(core, conv_id)))  # takes the failed one's place
    asyncio.run(collect(chat.regenerate(core, conv_id)))
    ask(core, conv_id, "Question 1")
    chat.change_conversation(core, conv_id, title="Shade")
    first, last = [m for m in messages(core, conv_id) if m["role"] == "user"]
    answered = of_kind(core, "chat.message.answered")[-1]
    # A second process finishing late: a reply to the first message lands after the
    # second was sent, and one arrives for a message that was never sent.
    for reply_to in (first["id"], "01JABCDEFGHJKMNPQRSTVWXYZ0"):
        core.tape.print("chat.message.answered", answered.payload | {
            "message_id": f"01JABCDEFGHJKMNPQRSTVWX{reply_to[-3:]}", "reply_to": reply_to,
            "replaces": None, "text": f"Late {reply_to[-3:]}"},
            actor="system", subject=store.subject(conv_id))
    ask(core, conv_id, "Question 2")
    conv, whole = store.thread(store.events_of(core, conv_id))
    assert [m["text"] for m in whole] == [
        "Question 0", f"Late {first['id'][-3:]}", "Question 1", TEXT, "Question 2", TEXT]
    for batch in (1, 2, 3, 200):
        monkeypatch.setattr(store, "READ_EVENTS", batch)
        for limit in range(1, 8):
            page = chat.get_conversation(core, conv_id, limit=limit)
            assert [m["id"] for m in page["messages"]] == [m["id"] for m in whole[-limit:]]
            assert page["older"] == (whole[-limit]["id"] if limit < len(whole) else None)
            assert page["conversation"]["title"] == "Shade"
            if page["older"]:
                older = chat.get_conversation(core, conv_id, limit=limit, before=page["older"])
                assert [m["id"] for m in older["messages"]] == [
                    m["id"] for m in whole[:-limit][-limit:]]
    assert chat.get_conversation(core, conv_id)["conversation"]["messages"] == len(whole)


def test_a_reply_cut_short_by_a_restart_leaves_the_conversation_ready_not_stuck(core, cfg):
    models(core, home=SlowProvider())
    conv_id = new(core)

    async def dies_mid_reply():
        run = await replies.begin(core, conv_id, text=QUESTION)
        run.finished = True  # the process is gone: nothing more is ever stored
        run.task.cancel()

    asyncio.run(dies_mid_reply())
    assert not of_kind(core, "chat.message.answered")
    after = open_core(cfg)  # the server comes back up on the same data
    try:
        fakes = models(after)
        detail = chat.get_conversation(after, conv_id)
        assert [m["role"] for m in detail["messages"]] == ["user"]
        assert detail["unanswered"] is True and detail["can_regenerate"] is True
        assert detail["conversation"]["streaming"] is False
        *_, reply = asyncio.run(collect(chat.regenerate(after, conv_id)))
        assert reply["state"] == "complete" and reply["reply_to"] == detail["messages"][0]["id"]
        asyncio.run(collect(chat.send(after, conv_id, "And then?")))
        assert [m["role"] for m in messages(after, conv_id)] == [
            "user", "assistant", "user", "assistant"]
        assert sent_to(fakes[HOME])[-1]["content"] == "And then?"
    finally:
        after.close()


def test_the_default_title_is_the_first_message_cut_at_a_word(core):
    models(core)
    conv_id = new(core)
    assert chat.get_conversation(core, conv_id)["conversation"]["title"] == ""
    ask(core, conv_id, "Could you help me plan\n a vegetable garden for a small north-facing "
                       "yard with heavy clay soil?")
    conv = chat.get_conversation(core, conv_id)["conversation"]
    assert conv["title"] == "Could you help me plan a vegetable garden for a small…"
    ask(core, conv_id, "A second message does not rename it")
    assert chat.get_conversation(core, conv_id)["conversation"]["title"] == conv["title"]
    named = new(core, title="  Garden   plans ")
    ask(core, named)
    conv = chat.get_conversation(core, named)["conversation"]
    assert (conv["title"], conv["title_source"]) == ("Garden plans", "owner")
    assert not [e for e in of_kind(core, "model.called") if e.payload["caller"] == "chat.title"]


HOSTED = {"kind": "model", "provider_id": "cloud", "model": "model-b"}
PINNED_HOME = {"kind": "model", "provider_id": "home", "model": "model-a"}


class NoTitles(FakeProvider):
    """Answers a streamed reply, and fails any other call (the title)."""

    def __call__(self, request):
        if request.method == "POST" and not json.loads(request.content).get("stream"):
            self.bodies.append(json.loads(request.content))
            return httpx.Response(500, json={"error": {"message": "Boom."}})
        return super().__call__(request)


class SlowTitles(FakeProvider):
    """Answers a streamed reply at once, and holds any other call (the title)
    until it is released."""

    def __init__(self):
        super().__init__("openai_compatible")
        self.asked, self.release = threading.Event(), threading.Event()

    async def __call__(self, request):
        if request.method == "POST" and not json.loads(request.content).get("stream"):
            self.asked.set()
            while not self.release.is_set():
                await asyncio.sleep(0.005)
        return super().__call__(request)


def test_a_model_can_write_the_title_and_a_failure_keeps_the_cut_one_silently(core):
    core.settings.set("chat.model_titles", True, actor="owner")
    title = FakeProvider("openai_compatible", text='"Shade Garden Ideas."\nSecond line')
    fakes = models(core, cloud=title, cheap=("cloud", "model-b"))
    conv_id = new(core, target=HOSTED)  # a hosted model answers, so one may name it too
    *_, reply = ask(core, conv_id)
    assert reply["local"] is False
    conv = chat.get_conversation(core, conv_id)["conversation"]
    assert (conv["title"], conv["title_source"]) == ("Shade Garden Ideas", "model")
    assert len(fakes[CLOUD].bodies) == 2  # the reply, then the title
    asked = fakes[CLOUD].bodies[-1]["messages"][-1]["content"]
    assert QUESTION in asked and reply["text"] in asked
    ask(core, conv_id, "A later message")
    assert len(fakes[CLOUD].bodies) == 3  # only after the first reply

    fakes = models(core, cloud=NoTitles("openai_compatible"), cheap=("cloud", "model-b"))
    other = new(core, target=HOSTED)
    *_, reply = ask(core, other)
    assert reply["state"] == "complete" and len(fakes[CLOUD].bodies) == 2
    conv = chat.get_conversation(core, other)["conversation"]
    assert (conv["title"], conv["title_source"]) == (QUESTION, "cut")


def test_a_title_never_goes_to_a_hosted_model_when_the_reply_was_written_locally(core):
    core.settings.set("chat.model_titles", True, actor="owner")
    # The "cheap" role is hosted, and the conversation is not marked local only.
    fakes = models(core, cheap=("cloud", "model-b"))
    for target in (None, PINNED_HOME):
        conv_id = new(core, target=target)
        *_, reply = ask(core, conv_id, "A private thought about the doctor")
        assert reply["state"] == "complete" and reply["local"] is True
        conv = chat.get_conversation(core, conv_id)["conversation"]
        assert conv["local_only"] is False and conv["title_source"] == "cut"
    assert fakes[CLOUD].bodies == []  # nothing left the building
    assert not [e for e in of_kind(core, "model.called") if e.payload["local"] is False]

    # A local "cheap" role may still name a locally answered conversation.
    fakes = models(core, cheap=("home", "model-a"))
    conv_id = new(core)
    ask(core, conv_id)
    assert chat.get_conversation(core, conv_id)["conversation"]["title_source"] == "model"
    assert fakes[CLOUD].bodies == []


def test_a_second_send_is_accepted_while_the_title_is_still_being_written(core):
    core.settings.set("chat.model_titles", True, actor="owner")
    home = SlowTitles()
    models(core, home=home, cheap=("home", "model-a"))
    conv_id = new(core)

    async def scenario():
        first = await replies.begin(core, conv_id, text=QUESTION)
        events = [name async for name, _ in replies.listen(first)]
        assert events[-1] == "done"
        await until(home.asked.is_set)  # the title is being written now
        assert chat.get_conversation(core, conv_id)["conversation"]["streaming"] is False
        second = await replies.begin(core, conv_id, text="And in full sun?")
        events = [name async for name, _ in replies.listen(second)]
        home.release.set()
        await asyncio.wait({first.task, second.task})
        return events[-1]

    assert asyncio.run(scenario()) == "done"
    assert [m["state"] for m in messages(core, conv_id) if m["role"] == "assistant"] == [
        "complete", "complete"]


def test_stopping_the_server_stores_a_reply_in_flight_as_stopped_with_its_receipt(core):
    models(core, home=SlowProvider())
    conv_id = new(core)

    async def scenario():
        run = await replies.begin(core, conv_id, text=QUESTION)
        await until(lambda: not run.queue.empty())
        await chat.shutdown(core)  # what the web process awaits when asked to stop
        # Nothing more is awaited: the process ends straight after.
        return run.message

    reply = asyncio.run(scenario())
    assert (reply["state"], reply["text"]) == ("stopped", "Hello")
    (answered,) = of_kind(core, "chat.message.answered")
    assert (answered.payload["state"], answered.payload["text"]) == ("stopped", "Hello")
    (receipt,) = of_kind(core, "model.failed")
    assert receipt.payload["caller"] == "chat.reply" and receipt.seq < answered.seq
    assert conv_id not in store.svc(core).runs
    assert chat.get_conversation(core, conv_id)["unanswered"] is False


def test_stopping_the_server_with_no_reply_in_flight_does_nothing(core):
    models(core)
    conv_id = new(core)
    ask(core, conv_id)
    before = core.tape.last_seq()
    asyncio.run(chat.shutdown(core))
    assert core.tape.last_seq() == before


def test_the_list_can_be_searched_by_title_beyond_the_first_page(core):
    old = new(core, title="Straße repairs, Spring PLAN")
    for n in range(6):  # newer conversations push the old one off the first page
        new(core, title=f"Notes {n}")
    more = [new(core, title=f"Later {n}") for n in range(4)]
    assert old not in [c["id"] for c in chat.list_conversations(core, limit=5)["conversations"]]

    def found(q, **how):
        return [c["id"] for c in chat.list_conversations(core, q=q, **how)["conversations"]]

    assert found("spring", limit=5) == [old]
    assert found("  PLAN   strasse ", limit=5) == [old]  # every word, any case, any order
    assert found("plan autumn") == []
    assert found("") == found(None) and len(found(None)) == 11
    chat.archive_conversation(core, old)
    assert found("spring") == [] and found("spring", archived=True) == [old]

    first = chat.list_conversations(core, q="later", limit=3)
    assert first["next"] is not None
    rest = chat.list_conversations(core, q="later", limit=3, before=first["next"])
    assert sorted([c["id"] for c in first["conversations"] + rest["conversations"]]) == sorted(
        more) and rest["next"] is None
    with pytest.raises(chat.ChatError) as refused:
        chat.list_conversations(core, q="x" * 121)
    assert refused.value.status == 422 and "120" in refused.value.detail


def test_an_override_on_a_send_becomes_the_conversations_new_default(core):
    fakes = models(core)
    conv_id = new(core)
    *_, reply = ask(core, conv_id, target={"kind": "model", "provider_id": "cloud",
                                           "model": "model-z"}, effort="high")
    assert (reply["provider_id"], reply["model"], reply["local"]) == ("cloud", "model-z", False)
    assert fakes[CLOUD].bodies[-1]["reasoning_effort"] == "high"
    conv = chat.get_conversation(core, conv_id)["conversation"]
    assert conv["target"] == {"kind": "model", "provider_id": "cloud", "model": "model-z"}
    assert conv["effort"] == "high"
    ask(core, conv_id, "Again", effort=None)
    assert "reasoning_effort" not in fakes[CLOUD].bodies[-1]
    assert chat.get_conversation(core, conv_id)["conversation"]["effort"] is None
    assert len(of_kind(core, "chat.conversation.changed")) == 2


def test_a_send_is_refused_plainly_when_nothing_is_set_up_to_answer(core):
    models(core, role=False)
    conv_id = new(core)
    with pytest.raises(chat.ChatError) as refused:
        ask(core, conv_id)
    assert refused.value.status == 409
    assert "Settings, Models and roles" in refused.value.detail
    gone = new(core, target={"kind": "model", "provider_id": "nowhere", "model": "m"})
    with pytest.raises(chat.ChatError) as refused:
        ask(core, gone)
    assert refused.value.status == 409
    assert not of_kind(core, "chat.message.sent")
    with pytest.raises(chat.ChatError) as agents:
        chat.start_conversation(core, target={"kind": "agent", "agent_id": "scout"})
    assert agents.value.status == 422 and "later release" in agents.value.detail


def test_archiving_hides_a_conversation_and_restoring_brings_it_back(core):
    models(core)
    first, second = new(core, title="First"), new(core, title="Second")
    ask(core, first)
    assert [c["id"] for c in chat.list_conversations(core)["conversations"]] == [first, second]
    assert chat.archive_conversation(core, first)["archived"] is True
    assert [c["id"] for c in chat.list_conversations(core)["conversations"]] == [second]
    assert [c["id"] for c in chat.list_conversations(core, archived=True)["conversations"]] == [
        first]
    for attempt in (lambda: ask(core, first),
                    lambda: chat.change_conversation(core, first, title="Renamed")):
        with pytest.raises(chat.ChatError) as refused:
            attempt()
        assert refused.value.status == 409 and "Restore" in refused.value.detail
    assert len(messages(core, first)) == 2  # still readable, nothing was deleted
    assert chat.restore_conversation(core, first)["archived"] is False
    assert chat.change_conversation(core, first, title="Renamed")["title"] == "Renamed"
    assert len(of_kind(core, "chat.conversation.archived")) == 1


def test_the_list_keeps_a_capped_number_of_archived_conversations(core, monkeypatch):
    monkeypatch.setattr(store, "ARCHIVED_CAP", 2)
    ids = [new(core, title=f"Conversation {n}") for n in range(4)]
    for conv_id in ids:
        chat.archive_conversation(core, conv_id)
    listed = chat.list_conversations(core, archived=True)["conversations"]
    assert len(listed) == 2
    dropped = next(i for i in ids if i not in {c["id"] for c in listed})
    assert chat.get_conversation(core, dropped)["conversation"]["archived"] is True
    restored = chat.restore_conversation(core, dropped)
    assert restored["archived"] is False
    assert chat.list_conversations(core)["conversations"][0]["title"] == restored["title"]


def test_the_list_is_paged_newest_activity_first(core):
    models(core)
    ids = [new(core) for _ in range(5)]
    ask(core, ids[1])
    first = chat.list_conversations(core, limit=2)
    assert first["conversations"][0]["id"] == ids[1] and first["next"]
    seen = [c["id"] for c in first["conversations"]]
    cursor = first["next"]
    while cursor:
        page = chat.list_conversations(core, limit=2, before=cursor)
        seen += [c["id"] for c in page["conversations"]]
        cursor = page["next"]
    assert sorted(seen) == sorted(ids) and len(seen) == 5
    with pytest.raises(chat.ChatError):
        chat.list_conversations(core, before="yesterday")


def test_a_second_process_sees_the_same_conversations(core, cfg):
    models(core)
    conv_id = new(core)
    other = open_core(cfg)
    try:
        assert chat.list_conversations(other)["conversations"][0]["id"] == conv_id
        ask(core, conv_id)
        assert chat.list_conversations(other)["conversations"][0]["messages"] == 2
        assert [m["text"] for m in chat.get_conversation(other, conv_id)["messages"]] == [
            QUESTION, TEXT]
    finally:
        other.close()


def test_a_character_split_across_pieces_is_joined_and_a_stray_half_never_loses_the_reply(core):
    home = SlowProvider(pieces=("Well done \ud83d", "\ude00 and", " a stray \udc00 half"))
    home.release.set()
    models(core, home=home)
    conv_id = new(core)
    *pieces, reply = ask(core, conv_id)
    assert pieces == ["Well done ", "\U0001f600 and", " a stray \ufffd half"]
    assert reply["state"] == "complete"
    assert reply["text"] == "Well done \U0001f600 and a stray \ufffd half"
    assert messages(core, conv_id)[-1] == reply
    for piece in pieces:
        piece.encode("utf-8")  # every piece can go down the wire


def test_a_first_message_with_nothing_printable_leaves_the_next_one_to_name_the_conversation(
        core):
    models(core)
    conv_id = new(core)
    ask(core, conv_id, "\u200b\u200b")
    conv = chat.get_conversation(core, conv_id)["conversation"]
    assert (conv["title"], conv["title_source"]) == ("", "none")
    ask(core, conv_id, "Which bulbs flower first?")
    conv = chat.get_conversation(core, conv_id)["conversation"]
    assert (conv["title"], conv["title_source"]) == ("Which bulbs flower first?", "cut")
    assert chat.list_conversations(core)["conversations"][0]["title"] == conv["title"]


def test_a_server_shutting_down_mid_reply_keeps_what_had_arrived_as_stopped(core):
    models(core, home=SlowProvider())
    conv_id = new(core)

    async def scenario():
        run = await replies.begin(core, conv_id, text=QUESTION)
        await until(lambda: not run.queue.empty())
        run.task.cancel()  # the loop is closing: nobody asked for a stop
        with pytest.raises(asyncio.CancelledError):
            await run.task
        return run.message

    reply = asyncio.run(scenario())
    assert (reply["state"], reply["text"]) == ("stopped", "Hello")
    assert messages(core, conv_id)[-1] == reply
    assert conv_id not in store.svc(core).runs
