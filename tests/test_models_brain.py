from __future__ import annotations

import asyncio
import json
import logging

import pytest

from jimmyhq.ids import now_ms
from jimmyhq.models import ModelError, Reply, Target, brain
from jimmyhq.models.lenses import DAYS_KEPT, HOUR_MS, UsageLens
from jimmyhq.models.validate import Invalid, looks_local
from test_models_fakes import TEXT, TOKENS_IN, TOKENS_OUT, FakeBedrock, FakeProvider, add, wire

HOME, CLOUD = "127.0.0.1", "cloud.example.com"
HELLO = [{"role": "user", "content": "A private question"}]
WHO = {"actor": "owner", "caller": "chat.reply"}


@pytest.fixture
def setup(core):
    """A local provider as the default role's first choice, a hosted one as its fallback."""

    def make(home: str = "ok", cloud: str = "ok", role: bool = True):
        fakes = {HOME: FakeProvider("openai_compatible", home),
                 CLOUD: FakeProvider("openai_compatible", cloud, text="From the cloud")}
        b = wire(core, fakes)
        add(b, "home", host=f"{HOME}:11434")
        add(b, "cloud", host=CLOUD, key="sk-test-cloud-key-000000")
        if role:
            b.set_role("default", Target("home", "model-a"), [Target("cloud", "model-b")],
                       actor="owner")
        return b, fakes

    return make


def kinds(core, *wanted):
    return [e for e in core.tape.after(0) if e.kind in wanted]


async def gather(stream):
    return [item async for item in stream]


def test_a_role_answers_from_its_first_choice_and_prints_a_receipt(setup, core):
    b, fakes = setup()
    reply = asyncio.run(b.chat(HELLO, system="Be kind.", **WHO))
    assert (reply.text, reply.provider_id, reply.provider_type, reply.model) == (
        TEXT, "home", "openai_compatible", "model-a")
    assert (reply.input_tokens, reply.output_tokens) == (TOKENS_IN, TOKENS_OUT)
    assert reply.is_local and reply.fallback_from is None and reply.fallback_reason is None
    assert not fakes[CLOUD].requests
    (event,) = kinds(core, "model.called")
    assert event.actor == "owner"
    payload = event.payload
    assert (payload["role"], payload["caller"], payload["local"], payload["streamed"]) == (
        "default", "chat.reply", True, False)
    assert json.loads(core.vault.get_text(payload["prompt_ref"])) == {
        "system": "Be kind.", "messages": HELLO}
    assert core.vault.get_text(payload["completion_ref"]) == TEXT


@pytest.mark.parametrize("mode", ["down", "timeout", "auth", "rate", "server", "garbage"])
def test_a_failed_first_choice_falls_back_and_the_reply_says_so(setup, core, mode):
    b, _ = setup(home=mode)
    reply = asyncio.run(b.chat(HELLO, **WHO))
    assert (reply.text, reply.provider_id, reply.is_local) == ("From the cloud", "cloud", False)
    assert reply.fallback_from == "home/model-a"
    assert reply.fallback_reason.startswith("Home ")
    (failed,) = kinds(core, "model.failed")
    assert failed.payload["provider_id"] == "home" and failed.payload["fallback_ok"] is True
    (called,) = kinds(core, "model.called")
    assert called.payload["fallback_from"] == "home/model-a"
    assert called.payload["fallback_reason"] == reply.fallback_reason


def test_a_request_the_provider_rejects_or_cuts_off_is_not_sent_to_another_model(setup, core):
    for mode, reason in (("rejected", "bad_request"), ("truncated", "truncated")):
        b, fakes = setup(home=mode)
        with pytest.raises(ModelError) as caught:
            asyncio.run(b.chat(HELLO, **WHO))
        assert caught.value.kind == reason and not fakes[CLOUD].requests


def test_a_cut_off_answer_reaches_the_caller_and_never_a_receipt_or_a_log_line(
        setup, core, caplog):
    caplog.set_level(logging.DEBUG)
    for ask in (lambda b: b.chat(HELLO, **WHO), lambda b: gather(b.stream(HELLO, **WHO))):
        b, _ = setup(home="truncated")
        with pytest.raises(ModelError) as caught:
            asyncio.run(ask(b))
        assert caught.value.kind == "truncated" and caught.value.partial == TEXT
    failed = kinds(core, "model.failed")
    assert len(failed) == 2 and not kinds(core, "model.called")
    for event in failed:
        assert TEXT not in json.dumps(event.payload) and "partial" not in event.payload
    assert TEXT not in caplog.text


def test_a_local_only_request_never_reaches_a_hosted_provider(setup, core):
    b, fakes = setup(home="down")
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat(HELLO, local_only=True, **WHO))
    assert caught.value.kind == "unreachable"
    assert not fakes[CLOUD].requests
    assert kinds(core, "model.called") == []


def test_a_local_only_request_with_no_local_model_fails_with_a_clear_message(setup, core):
    b, fakes = setup(role=False)
    b.set_role("default", Target("cloud", "model-b"), actor="owner")
    with pytest.raises(ModelError, match="must stay on your own machines") as caught:
        asyncio.run(b.chat(HELLO, local_only=True, **WHO))
    assert caught.value.kind == "local_only"
    with pytest.raises(ModelError, match="must stay on your own machines"):
        asyncio.run(b.chat(HELLO, provider_id="cloud", model="model-b", local_only=True, **WHO))
    assert not fakes[CLOUD].requests and not fakes[HOME].requests


def test_a_local_only_request_skips_a_hosted_first_choice_for_a_local_fallback(setup, core):
    b, fakes = setup(role=False)
    b.set_role("default", Target("cloud", "model-b"), [Target("home", "model-a")], actor="owner")
    reply = asyncio.run(b.chat(HELLO, local_only=True, **WHO))
    assert reply.provider_id == "home" and reply.fallback_from == "cloud/model-b"
    assert "must stay local" in reply.fallback_reason
    assert not fakes[CLOUD].requests


def test_a_no_fallback_request_fails_rather_than_falls_back(setup, core):
    b, fakes = setup(home="down")
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat(HELLO, no_fallback=True, **WHO))
    assert caught.value.kind == "unreachable" and not fakes[CLOUD].requests
    assert [e.payload["reason"] for e in kinds(core, "model.failed")] == ["unreachable"]


def test_when_every_choice_fails_each_leg_is_recorded_and_the_last_error_is_raised(setup, core):
    b, _ = setup(home="down", cloud="rate")
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat(HELLO, **WHO))
    assert caught.value.kind == "rate_limited"
    assert [(e.payload["provider_id"], e.payload["reason"])
            for e in kinds(core, "model.failed")] == [("home", "unreachable"),
                                                       ("cloud", "rate_limited")]


def test_a_switched_off_or_removed_provider_is_passed_over(setup, core):
    b, fakes = setup()
    b.save_provider(b.provider("home").as_dict() | {"enabled": False}, actor="owner")
    reply = asyncio.run(b.chat(HELLO, **WHO))
    assert reply.provider_id == "cloud" and "switched off" in reply.fallback_reason
    b.remove_provider("home", actor="owner")
    assert "no longer exists" in asyncio.run(b.chat(HELLO, **WHO)).fallback_reason
    assert not fakes[HOME].requests


def test_a_private_caller_leaves_counts_on_the_tape_and_no_text_anywhere(setup, core):
    b, _ = setup()
    before = {p.name for p in core.config.vault_dir.rglob("*") if p.is_file()}
    asyncio.run(b.chat(HELLO, system="Secret instructions", keep_text=False, **WHO))
    (event,) = kinds(core, "model.called")
    assert event.payload["prompt_ref"] is None and event.payload["completion_ref"] is None
    assert (event.payload["input_tokens"], event.payload["output_tokens"]) == (
        TOKENS_IN, TOKENS_OUT)
    assert {p.name for p in core.config.vault_dir.rglob("*") if p.is_file()} == before
    dumped = json.dumps([e.model_dump() for e in core.tape.after(0)])
    assert "private question" not in dumped and "Secret instructions" not in dumped
    assert TEXT not in dumped


def test_a_failure_receipt_never_carries_the_prompt_or_what_the_provider_said(setup, core):
    b, fakes = setup(home="rejected", cloud="auth")
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat(HELLO, **WHO))
    assert "malformed" in caught.value.message  # the caller is told what the provider said
    (failed,) = kinds(core, "model.failed")
    assert failed.payload["message"] == "Home rejected the request."
    assert failed.payload["status"] == 400
    assert "private question" not in json.dumps(failed.model_dump())


def test_a_stream_yields_text_as_it_arrives_then_the_reply_and_one_receipt(setup, core):
    b, _ = setup()
    *pieces, reply = asyncio.run(gather(b.stream(HELLO, **WHO)))
    assert pieces == ["Hello", " there"]
    assert isinstance(reply, Reply) and reply.text == TEXT
    assert (reply.input_tokens, reply.output_tokens) == (TOKENS_IN, TOKENS_OUT)
    (event,) = kinds(core, "model.called")
    assert event.payload["streamed"] is True


def test_a_stream_falls_back_before_any_text_but_not_after_it_has_started(setup, core):
    b, _ = setup(home="down")
    *pieces, reply = asyncio.run(gather(b.stream(HELLO, **WHO)))
    assert "".join(pieces) == "From the cloud" and reply.fallback_from == "home/model-a"

    def broken(request):  # one piece of text, then an unreadable event
        import httpx
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
                                        "data: {not json\n\n")

    b.transport.hosts[HOME] = broken
    seen = []

    async def run():
        async for item in b.stream(HELLO, **WHO):
            seen.append(item)

    with pytest.raises(ModelError) as caught:
        asyncio.run(run())
    assert seen == ["Hel"] and caught.value.kind == "bad_reply"


def test_a_model_can_be_named_directly_instead_of_a_role(setup, core):
    b, fakes = setup(role=False)
    reply = asyncio.run(b.chat(HELLO, provider_id="cloud", model="model-z", **WHO))
    assert reply.model == "model-z" and fakes[CLOUD].bodies[0]["model"] == "model-z"
    assert kinds(core, "model.called")[0].payload["role"] is None
    with pytest.raises(ModelError, match="no provider called"):
        asyncio.run(b.chat(HELLO, provider_id="nowhere", model="m", **WHO))


def test_an_unset_role_uses_the_default_role_except_for_embeddings(setup, core):
    b, _ = setup()
    assert asyncio.run(b.chat(HELLO, role="cheap", **WHO)).provider_id == "home"
    with pytest.raises(ModelError, match="No model is chosen for the 'embeddings' role"):
        asyncio.run(b.chat(HELLO, role="embeddings", **WHO))


def test_with_no_role_set_the_error_says_where_to_choose_a_model(setup):
    b, _ = setup(role=False)
    with pytest.raises(ModelError, match="Choose one under Settings, Models") as caught:
        asyncio.run(b.chat(HELLO, **WHO))
    assert caught.value.kind == "not_configured"


def test_request_options_come_from_the_call_then_the_role_then_the_provider(setup, core):
    b, fakes = setup(role=False)
    add(b, "home", host=f"{HOME}:11434", options={"max_tokens": 111, "temperature": 0.5,
                                                    "effort": "low"})
    b.set_role("default", Target("home", "model-a", "high"), actor="owner")
    asyncio.run(b.chat(HELLO, **WHO))
    asyncio.run(b.chat(HELLO, max_tokens=7, temperature=0, effort="minimal", **WHO))
    first, second = fakes[HOME].bodies
    assert (first["max_tokens"], first["temperature"], first["reasoning_effort"]) == (
        111, 0.5, "high")
    assert (second["max_tokens"], second["temperature"], second["reasoning_effort"]) == (
        7, 0, "minimal")
    core.settings.set("models.default_max_tokens", 99, actor="owner")
    asyncio.run(b.chat(HELLO, provider_id="cloud", model="m", **WHO))
    assert fakes[CLOUD].bodies[0]["max_tokens"] == 99


def test_a_json_reply_is_parsed_and_one_that_does_not_parse_raises(setup, core):
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    b, fakes = setup(role=False)
    fakes[HOME].text = '```json\n{"n": 3}\n```'
    reply = asyncio.run(b.chat(HELLO, provider_id="home", model="m", json_schema=schema,
                               system="Count.", **WHO))
    assert reply.data == {"n": 3}
    system = fakes[HOME].bodies[0]["messages"][0]["content"]
    assert system.startswith("Count.") and "only a JSON value" in system
    assert json.dumps(schema) in system
    fakes[HOME].text = "Sure! The answer is three."
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat(HELLO, provider_id="home", model="m", json_schema=schema, **WHO))
    assert caught.value.kind == "bad_reply"
    assert kinds(core, "model.failed")[-1].payload["reason"] == "bad_reply"


@pytest.mark.parametrize(
    ("messages", "options", "message"),
    [
        ([], {}, "at least one message"),
        ("hello", {}, "at least one message"),
        ([{"role": "system", "content": "x"}], {}, "role of 'user' or 'assistant'"),
        ([{"role": "assistant", "content": "x"}], {}, "first message must be from the user"),
        ([{"role": "user", "content": 5}], {}, "must be text"),
        ([{"role": "user", "content": "\ud800"}], {}, "cannot be sent"),
        ([{"role": "user", "content": [{"type": "image", "data": "x"}]}], {}, "media_type"),
        (HELLO, {"role": "wizard"}, "no role called"),
        (HELLO, {"effort": "extreme"}, "Effort must be one of"),
        (HELLO, {"max_tokens": 0}, "max_tokens"),
        (HELLO, {"temperature": 9}, "temperature"),
        (HELLO, {"caller": "Not A Caller"}, "needs a caller"),
        (HELLO, {"provider_id": "home"}, "both a provider and a model"),
        (HELLO, {"json_schema": "object"}, "JSON Schema object"),
        (HELLO, {"colour": "blue"}, "not made correctly"),
    ],
)
def test_an_odd_request_is_refused_before_anything_is_sent(setup, core, messages, options,
                                                           message):
    b, fakes = setup()
    with pytest.raises(ModelError, match=message) as caught:
        asyncio.run(b.chat(messages, **(WHO | options)))
    assert caught.value.kind == "bad_request"
    assert not fakes[HOME].requests and kinds(core, "model.failed", "model.called") == []


def test_a_message_may_be_a_list_of_text_parts(setup):
    b, fakes = setup()
    parts = [{"type": "text", "text": "One. "}, {"type": "text", "text": "Two."}]
    asyncio.run(b.chat([{"role": "user", "content": parts}], **WHO))
    assert fakes[HOME].bodies[0]["messages"][-1]["content"] == "One. Two."


def test_the_sync_wrapper_works_outside_async_code_and_refuses_inside_it(setup):
    b, _ = setup()
    assert b.chat_sync(HELLO, **WHO).text == TEXT

    async def inside():
        b.chat_sync(HELLO, **WHO)

    with pytest.raises(RuntimeError, match="await brain.chat"):
        asyncio.run(inside())


def test_bedrock_is_reached_through_the_brain_with_its_region_and_profile(core):
    b = brain(core)
    b.bedrock_clients = FakeBedrock()
    b.save_provider({"id": "aws", "type": "bedrock", "label": "Cloud", "region": "us-east-1",
                     "profile": "work"}, actor="owner")
    reply = asyncio.run(b.chat(HELLO, provider_id="aws", model="model-a", **WHO))
    assert (reply.text, reply.provider_type, reply.is_local) == (TEXT, "bedrock", False)
    assert b.bedrock_clients.asked == ("bedrock-runtime", "us-east-1", "work")
    saved = kinds(core, "model.provider.saved")[0].payload
    assert (saved["region"], saved["profile"], saved["base_url"]) == ("us-east-1", "work", None)


# --- configuration ------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://127.0.0.1:11434/v1", True),
        ("http://localhost:1234/v1", True),
        ("http://[::1]:8000/v1", True),
        ("http://192.0.2.10:8000/v1", True),  # a non-public address
        ("http://studio:1234/v1", True),
        ("http://models.internal/v1", True),
        ("http://box.home.arpa/v1", True),
        ("https://api.example.com/v1", False),
        ("https://203.0.114.9/v1", False),
        ("https://internal.example.com/v1", False),
    ],
)
def test_a_compatible_provider_is_local_only_when_its_address_is_on_your_own_network(url,
                                                                                    local):
    assert looks_local(url) is local


def test_local_defaults_from_the_address_and_the_owner_can_override_it(core):
    b = brain(core)
    assert add(b, "home").is_local is True
    assert add(b, "hosted", host="api.example.com").is_local is False
    assert add(b, "proxy", host="api.example.com", local=True).is_local is True
    assert add(b, "shared", local=False).is_local is False
    # A hosted type is hosted wherever it is pointed, unless the owner says otherwise.
    assert add(b, "vendor", "anthropic", host="127.0.0.1:9000").is_local is False


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"id": "Bad Id"}, "provider id must start with a letter"),
        ({"type": "magic"}, "provider type must be one of"),
        ({"label": " "}, "label cannot be empty"),
        ({"label": "\ud800"}, "cannot be saved"),
        ({"base_url": None}, "needs an address"),
        ({"base_url": "ftp://example.com"}, "must start with http"),
        ({"base_url": "http://"}, "must start with http"),
        ({"base_url": "http://example.com:port/v1"}, "not a valid URL"),
        ({"base_url": "https://user:secret@example.com/v1"}, "Do not put a name or key"),
        ({"base_url": "https://example.com/v1?key=abc"}, "cannot contain"),
        ({"options": {"max_tokens": 0}}, "answer limit"),
        ({"options": {"temperature": "hot"}}, "Temperature"),
        ({"options": {"effort": "extreme"}}, "Effort must be one of"),
        ({"options": {"colour": 1}}, "Unknown option"),
        ({"local": "yes"}, "true or false"),
        ({"type": "bedrock", "base_url": None}, "needs an AWS region"),
    ],
)
def test_a_bad_provider_is_refused_with_what_to_fix_and_nothing_is_recorded(core, record,
                                                                           message):
    b = brain(core)
    good = {"id": "p", "type": "openai_compatible", "label": "P",
            "base_url": "http://127.0.0.1:1/v1"}
    with pytest.raises(Invalid, match=message):
        b.save_provider(good | record, actor="owner")
    assert kinds(core, "model.provider.saved") == []


def test_a_role_can_only_point_at_a_provider_that_exists_and_is_switched_on(setup, core):
    b, _ = setup(role=False)
    with pytest.raises(Invalid, match="no provider called 'nowhere'"):
        b.set_role("default", Target("nowhere", "m"), actor="owner")
    add(b, "off", enabled=False)
    with pytest.raises(Invalid, match="switched off"):
        b.set_role("default", Target("home", "m"), [Target("off", "m")], actor="owner")
    with pytest.raises(Invalid, match="no role called"):
        b.set_role("wizard", Target("home", "m"), actor="owner")
    assert kinds(core, "model.role.set") == []
    b.set_role("embeddings", Target("home", "embed-model"), actor="owner")
    assert b.config().roles["embeddings"] == [Target("home", "embed-model")]
    b.set_role("embeddings", None, actor="owner")
    assert "embeddings" not in b.config().roles


def test_configuration_is_read_from_the_tape_so_another_process_sees_it(setup, core):
    b, _ = setup()
    from jimmyhq.models.brain import Brain

    other = Brain(core)  # as the worker process would build its own
    assert [p.id for p in other.providers()] == ["cloud", "home"]
    assert other.config().roles["default"][0] == Target("home", "model-a")


# --- discovery and health -----------------------------------------------------


def test_a_model_list_is_cached_briefly_and_can_be_refreshed(setup, core):
    b, fakes = setup()
    first = asyncio.run(b.list_models("home"))
    again = asyncio.run(b.list_models("home"))
    assert first["models"] == ["model-a", "model-b"] and first["cached"] is False
    assert again["cached"] is True and again["fetched_ms"] == first["fetched_ms"]
    assert len(fakes[HOME].requests) == 1
    assert asyncio.run(b.list_models("home", refresh=True))["cached"] is False
    core.settings.set("models.models_cache_s", 0, actor="owner")
    asyncio.run(b.list_models("home"))
    assert len(fakes[HOME].requests) == 3


def test_health_lists_models_never_chats_and_is_cached_for_the_interval(setup, core):
    b, fakes = setup(cloud="down")
    add(b, "off", host="off.example.com", enabled=False)
    report = asyncio.run(b.health())
    by_id = {p["id"]: p for p in report["providers"]}
    assert set(by_id) == {"home", "cloud"} and report["interval_s"] == 30
    assert by_id["home"]["ok"] is True and by_id["home"]["error"] is None
    assert by_id["cloud"]["ok"] is False and "could not be reached" in by_id["cloud"]["error"]
    assert by_id["home"]["checked_ms"] <= now_ms()
    asyncio.run(b.health())
    for fake in fakes.values():
        assert [r.method for r in fake.requests] == ["GET"]  # one check each, no chat


def test_a_call_that_works_or_cannot_connect_updates_health_without_a_check(setup, core):
    b, fakes = setup(cloud="down")
    asyncio.run(b.chat(HELLO, **WHO))
    with pytest.raises(ModelError):
        asyncio.run(b.chat(HELLO, provider_id="cloud", model="m", **WHO))
    described = {p.id: b.describe(p)["health"] for p in b.providers()}
    assert described["home"]["ok"] is True and described["cloud"]["ok"] is False


def test_testing_a_provider_that_cannot_list_models_falls_back_to_a_one_token_chat(setup, core):
    b, fakes = setup()

    class NoList(FakeProvider):
        def __call__(self, request):
            if request.method == "GET":
                self.mode = "missing"
            else:
                self.mode = "truncated"  # one token is always cut off
            return super().__call__(request)

    b.transport.hosts[HOME] = quiet = NoList("openai_compatible")
    assert asyncio.run(b.test("home")) == {"ok": True, "error": None, "models": None,
                                            "checked_ms": pytest.approx(now_ms(), abs=5000)}
    assert quiet.bodies[0]["max_tokens"] == 1 and quiet.bodies[0]["model"] == "model-a"
    add(b, "norole", host="norole.example.com")
    b.transport.hosts["norole.example.com"] = NoList("openai_compatible")
    assert "Give it a role" in asyncio.run(b.test("norole"))["error"]
    # The one-token chat is a real call, so it leaves a receipt like any other.
    (receipt,) = kinds(core, "model.called", "model.failed")
    assert receipt.payload["caller"] == "models.test" and receipt.actor == "owner"
    assert (receipt.payload["provider_id"], receipt.payload["model"]) == ("home", "model-a")
    assert receipt.payload["role"] is None
    assert not fakes[CLOUD].requests  # and it is never sent on to a fallback


# --- usage and cost -----------------------------------------------------------


def called(core, provider_id="cloud", model="model-b", tokens=(1_000_000, 500_000),
           local=False, fallback=None, ptype="openai_compatible"):
    core.tape.print("model.called", {
        "provider_id": provider_id, "provider_type": ptype, "model": model, "role": "default",
        "caller": "chat.reply", "duration_ms": 10, "input_tokens": tokens[0],
        "output_tokens": tokens[1], "local": local, "fallback_from": fallback,
    }, actor="owner")


PRICES = [
    {"id": "cloud-b", "provider": "cloud", "model": "model-b", "input_per_mtok": 3,
     "output_per_mtok": 15},
    {"id": "any-big", "provider": "*", "model": "big-*", "input_per_mtok": 10,
     "output_per_mtok": 30},
]


def test_usage_adds_up_calls_and_tokens_and_prices_them_at_read_time(core):
    b = brain(core)
    b.set_prices(PRICES, actor="owner")
    called(core)                                            # 3.00 + 7.50
    called(core, tokens=(200_000, 100_000))                 # 0.60 + 1.50
    called(core, model="big-one", tokens=(100_000, 10_000))  # 1.00 + 0.30
    called(core, model="unknown", tokens=(5, 5), fallback="home/model-a")
    called(core, "home", "model-a", (400_000, 300_000), local=True)
    usage = b.usage()
    total = usage["all_time"]
    assert (total["calls"], total["input_tokens"], total["output_tokens"]) == (
        5, 1_700_005, 910_005)
    assert (total["local_calls"], total["local_input_tokens"],
            total["local_output_tokens"]) == (1, 400_000, 300_000)
    assert total["estimated_cost"] == pytest.approx(13.9)
    assert (total["unpriced_calls"], total["fallback_calls"]) == (1, 1)
    assert total["saved_by_local"] is None and usage["savings_reference"] is None
    top = total["models"][0]
    assert (top["provider_id"], top["model"], top["calls"], top["estimated_cost"]) == (
        "cloud", "model-b", 2, pytest.approx(12.6))
    by_model = {m["model"]: m for m in total["models"]}
    assert by_model["unknown"]["estimated_cost"] is None
    assert by_model["model-a"]["estimated_cost"] == 0
    assert usage["last_24h"] == total
    assert len(usage["days"]) == 1 and usage["days"][0]["calls"] == 5
    assert usage["currency"] == "USD" and usage["prices_are_default"] is False
    # Prices are applied when read, so changing them reprices history.
    b.set_prices([PRICES[0] | {"input_per_mtok": 0, "output_per_mtok": 0}], actor="owner")
    assert b.usage()["all_time"]["estimated_cost"] == 0


def test_saved_by_running_locally_prices_local_tokens_at_the_chosen_reference(core):
    b = brain(core)
    b.set_prices(PRICES, actor="owner")
    called(core, "home", "model-a", (400_000, 300_000), local=True)
    called(core)
    core.settings.set("models.savings_reference", "cloud-b", actor="owner")
    usage = b.usage()
    assert usage["all_time"]["saved_by_local"] == pytest.approx(0.4 * 3 + 0.3 * 15)
    assert usage["savings_reference"]["id"] == "cloud-b"
    core.settings.set("models.savings_reference", "no-such-line", actor="owner")
    assert b.usage()["all_time"]["saved_by_local"] is None


def test_the_shipped_example_prices_apply_until_the_owner_saves_a_table(core):
    b = brain(core)
    config = b.config()
    assert config.prices_are_default and len(config.prices) >= 3
    called(core, "vendor", "claude-sonnet-example", (1_000_000, 1_000_000), ptype="anthropic")
    assert b.usage()["all_time"]["estimated_cost"] == pytest.approx(12.0)
    b.set_prices([], actor="owner")
    assert b.config().prices == [] and b.usage()["all_time"]["unpriced_calls"] == 1


def test_failed_legs_are_counted_and_the_recent_window_leaves_out_older_work(core):
    lens, state = UsageLens(), UsageLens().initial()
    from jimmyhq.tape.registry import make_event

    def event(kind, ts, **payload):
        base = {"provider_id": "home", "provider_type": "openai_compatible", "model": "m",
                "caller": "chat.reply", "duration_ms": 1}
        return make_event(kind, base | payload, actor="owner").model_copy(update={"ts": ts})

    now = now_ms()
    ok = {"input_tokens": 10, "output_tokens": 1, "local": True}
    for ts in (now - 30 * HOUR_MS, now - 2 * HOUR_MS, now):
        state = lens.fold(state, event("model.called", ts, **ok))
    state = lens.fold(state, event("model.failed", now, reason="timeout", message="x",
                                   fallback_ok=True))
    from jimmyhq.models.prices import usage_report

    report = usage_report(state, [], None, now)
    assert (report["all_time"]["calls"], report["last_24h"]["calls"]) == (3, 2)
    assert report["all_time"]["failed_calls"] == report["last_24h"]["failed_calls"] == 1


def test_the_usage_view_keeps_a_bounded_number_of_days_and_hours(core):
    lens, state = UsageLens(), UsageLens().initial()
    from jimmyhq.tape.registry import make_event

    day_ms = 24 * HOUR_MS
    for day in range(DAYS_KEPT + 30):
        event = make_event("model.called", {
            "provider_id": "home", "provider_type": "openai_compatible", "model": "m",
            "caller": "chat.reply", "duration_ms": 1, "input_tokens": 1, "output_tokens": 1,
            "local": True}, actor="owner").model_copy(update={"ts": 1_700_000_000_000
                                                                + day * day_ms})
        state = lens.fold(state, event)
    assert len(state["days"]) == DAYS_KEPT and len(state["hours"]) <= 2
    assert state["all"]["m"]["home\nopenai_compatible\nm"][0] == DAYS_KEPT + 30


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ("free", "must be a list"),
        ([{"id": "a", "provider": "*", "model": "*", "input_per_mtok": -1,
           "output_per_mtok": 0}], "must be a number from 0"),
        ([{"id": "Bad Id", "provider": "*", "model": "*", "input_per_mtok": 1,
           "output_per_mtok": 1}], "needs an id"),
        ([{"id": "a", "provider": "*", "model": "", "input_per_mtok": 1,
           "output_per_mtok": 1}], "cannot be empty"),
        ([{"id": "a", "provider": "*", "model": "*", "input_per_mtok": 1,
           "output_per_mtok": 1}] * 2, "share the id"),
    ],
)
def test_a_bad_price_table_is_refused_with_what_to_fix(core, entries, message):
    with pytest.raises(Invalid, match=message):
        brain(core).set_prices(entries, actor="owner")
    assert kinds(core, "model.prices.set") == []


# --- keys ---------------------------------------------------------------------


def test_a_key_is_sent_to_its_provider_and_never_reaches_the_tape_vault_or_logs(setup, core,
                                                                               caplog):
    key = "sk-test-cloud-key-000000"
    b, fakes = setup(home="down")
    with caplog.at_level(logging.DEBUG):
        asyncio.run(b.chat(HELLO, **WHO))
        asyncio.run(b.health())
        fakes[CLOUD].mode = "auth"
        with pytest.raises(ModelError):
            asyncio.run(b.chat(HELLO, provider_id="cloud", model="m", **WHO))
    assert fakes[CLOUD].requests[0].headers["authorization"] == f"Bearer {key}"
    assert all("authorization" not in r.headers for r in fakes[HOME].requests)
    assert key not in json.dumps([e.model_dump() for e in core.tape.after(0)])
    assert key not in caplog.text and "private question" not in caplog.text
    for path in core.config.data.rglob("*"):
        if path.is_file() and path != core.config.secrets_file:
            assert key.encode() not in path.read_bytes(), path.name
    assert key in core.config.secrets_file.read_text()
    b.remove_provider("cloud", actor="owner")
    assert core.secrets.names() == []


# --- found on review ----------------------------------------------------------


@pytest.mark.parametrize("url", ["http://134744072/v1", "http://0x8080808/v1", "http://8.8/v1"])
def test_a_bare_number_for_a_host_is_never_taken_for_the_owners_own_network(url):
    assert looks_local(url) is False


@pytest.mark.parametrize("model", [5, "", "   ", "m" * 201, ["m"]])
def test_a_model_named_directly_must_be_a_short_name_before_anything_is_sent(setup, core, model):
    b, fakes = setup()
    with pytest.raises(ModelError) as caught:
        asyncio.run(b.chat(HELLO, provider_id="home", model=model, **WHO))
    assert caught.value.kind == "bad_request"
    assert not fakes[HOME].requests
    assert kinds(core, "model.called", "model.failed") == []


def test_the_usage_view_keeps_a_bounded_number_of_models_and_adds_the_rest_together(core):
    from jimmyhq.models.lenses import MODELS_KEPT, OTHER
    from jimmyhq.models.prices import usage_report
    from jimmyhq.tape.registry import make_event

    lens, state = UsageLens(), UsageLens().initial()
    extra = 25
    for n in range(MODELS_KEPT + extra):
        state = lens.fold(state, make_event("model.called", {
            "provider_id": "cloud", "provider_type": "openai", "model": f"model-{n}",
            "caller": "chat.reply", "duration_ms": 1, "input_tokens": 10, "output_tokens": 1,
            "local": False}, actor="owner"))
    for bucket in (state["all"], *state["days"].values(), *state["hours"].values()):
        assert len(bucket["m"]) <= MODELS_KEPT + 1
    assert state["all"]["m"][OTHER][:3] == [extra, extra * 10, extra]
    catch_all = [{"id": "any", "provider": "*", "model": "*", "input_per_mtok": 1.0,
                  "output_per_mtok": 1.0}]
    total = usage_report(state, catch_all, None, now_ms())["all_time"]
    assert total["calls"] == MODELS_KEPT + extra  # nothing is lost from the totals
    assert total["unpriced_calls"] == extra  # and the gathered row is never priced by guess


def test_a_provider_with_no_model_list_is_reachable_not_down(setup, core):
    b, fakes = setup()
    fakes[HOME].mode = "missing"  # every address answers 404
    report = asyncio.run(b.health())
    home = next(p for p in report["providers"] if p["id"] == "home")
    assert home["ok"] is True and home["error"] is None
    assert [r.method for r in fakes[HOME].requests] == ["GET"]
    fakes[CLOUD].mode = "auth"
    core.settings.set("models.health_interval_s", 10, actor="owner")
    b._state.set_health("cloud", True, 0)  # stale, so it is checked again
    cloud = next(p for p in asyncio.run(b.health())["providers"] if p["id"] == "cloud")
    assert cloud["ok"] is False and "refused the key" in cloud["error"]


def test_a_stream_the_caller_walks_away_from_still_leaves_a_receipt(setup, core):
    b, _ = setup()

    async def run():
        stream = b.stream(HELLO, **WHO)
        assert await stream.__anext__() == "Hello"
        await stream.aclose()

    asyncio.run(run())
    assert kinds(core, "model.called") == []
    (failed,) = kinds(core, "model.failed")
    assert (failed.payload["reason"], failed.payload["provider_id"]) == ("abandoned", "home")
    assert failed.payload["fallback_ok"] is False
    assert "private question" not in json.dumps(failed.model_dump())


def test_a_cancelled_call_leaves_a_receipt_and_is_not_sent_to_a_fallback(setup, core):
    b, fakes = setup()

    async def run():
        task = asyncio.create_task(b.chat(HELLO, **WHO))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    import httpx

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(30)

    b.transport = Slow()
    asyncio.run(run())
    assert [e.payload["reason"] for e in kinds(core, "model.failed")] == ["abandoned"]
    assert not fakes[CLOUD].requests and kinds(core, "model.called") == []


def test_an_absurd_token_count_from_a_provider_cannot_swamp_the_totals(setup, core):
    import httpx

    from jimmyhq.models.adapters.base import MAX_TOKEN_COUNT

    b, _ = setup()
    b.transport.hosts[HOME] = lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1e300, "completion_tokens": True}})
    reply = asyncio.run(b.chat(HELLO, **WHO))
    assert (reply.input_tokens, reply.output_tokens) == (MAX_TOKEN_COUNT, 0)


# --- found on the phase 1 review ----------------------------------------------


def keyless(core):
    """A hosted provider that needs a key and has none, first in the default role."""
    fakes = {HOME: FakeProvider("openai_compatible"),
             "anthropic.example.com": FakeProvider("anthropic")}
    b = wire(core, fakes)
    add(b, "home", host=f"{HOME}:11434")
    add(b, "hosted", "anthropic")
    b.set_role("default", Target("hosted", "model-a"), [Target("home", "model-a")],
               actor="owner")
    return b, fakes["anthropic.example.com"], fakes[HOME]


def test_a_hosted_provider_with_no_key_is_never_contacted_for_health_models_or_a_test(core):
    b, hosted, _ = keyless(core)
    report = {p["id"]: p for p in asyncio.run(b.health())["providers"]}
    assert report["hosted"] | {"error": ""} == {
        "id": "hosted", "label": "Hosted", "type": "anthropic", "local": False,
        "ok": None, "checked_ms": None, "error": ""}
    assert report["hosted"]["error"] == "Hosted has no key yet. Add one under Settings, Models."
    for refresh in (False, True):
        with pytest.raises(ModelError, match="has no key yet") as caught:
            asyncio.run(b.list_models("hosted", refresh=refresh))
        assert caught.value.kind == "not_configured"
    tested = asyncio.run(b.test("hosted"))
    assert tested["ok"] is False and "has no key yet" in tested["error"]
    described = b.describe(b.provider("hosted"))
    assert (described["needs_key"], described["has_key"]) == (True, False)
    assert described["health"] == {"ok": None, "checked_ms": None,
                                   "error": report["hosted"]["error"]}
    assert b.describe(b.provider("home"))["needs_key"] is False
    assert hosted.requests == []
    # Once a key is saved it is checked like any other provider.
    b.set_key("hosted", "sk-test-hosted-key-000000")
    assert {p["id"]: p["ok"] for p in asyncio.run(b.health())["providers"]}["hosted"] is True
    assert len(hosted.requests) == 1


def test_a_prompt_is_never_sent_to_a_hosted_provider_with_no_key(core):
    b, hosted, home = keyless(core)
    reply = asyncio.run(b.chat(HELLO, **WHO))
    assert (reply.provider_id, reply.fallback_from) == ("home", "hosted/model-a")
    assert "has no key yet" in reply.fallback_reason
    assert [item for item in asyncio.run(gather(b.stream(HELLO, **WHO)))
            if isinstance(item, Reply)][0].provider_id == "home"
    with pytest.raises(ModelError, match="has no key yet") as caught:
        asyncio.run(b.chat(HELLO, provider_id="hosted", model="model-a", **WHO))
    assert caught.value.kind == "not_configured"
    assert hosted.requests == [] and len(home.requests) == 2


def test_changing_where_a_provider_lives_forgets_its_health_and_model_list(setup, core):
    b, fakes = setup()
    assert asyncio.run(b.list_models("home"))["models"] == ["model-a", "model-b"]
    record = b.provider("home").as_dict()
    b.save_provider(record | {"label": "Renamed"}, actor="owner")
    assert b.describe(b.provider("home"))["health"]["ok"] is True  # a new name moves nothing
    assert asyncio.run(b.list_models("home"))["cached"] is True
    moved = FakeProvider("openai_compatible")
    b.transport.hosts["moved.example.com"] = moved
    b.save_provider(record | {"base_url": "http://moved.example.com/v1"}, actor="owner")
    assert b.describe(b.provider("home"))["health"] == {
        "ok": None, "checked_ms": None, "error": None}
    assert asyncio.run(b.list_models("home"))["cached"] is False
    assert len(moved.requests) == 1 and len(fakes[HOME].requests) == 1


def test_changing_a_bedrock_region_or_profile_forgets_its_health_and_model_list(core):
    b = brain(core)
    b.bedrock_clients = FakeBedrock()
    record = {"id": "aws", "type": "bedrock", "label": "AWS", "region": "us-east-1"}
    b.save_provider(record, actor="owner")
    for change in ({"region": "eu-west-1"}, {"profile": "work"}):
        asyncio.run(b.list_models("aws"))
        assert b.last_health()["aws"]["ok"] is True
        record = record | change
        b.save_provider(record, actor="owner")
        assert "aws" not in b.last_health()
        assert asyncio.run(b.list_models("aws"))["cached"] is False


def test_saving_or_removing_a_key_forgets_the_health_and_model_list_it_was_checked_with(
        setup, core):
    b, fakes = setup(cloud="auth")
    asyncio.run(b.health())
    assert "refused the key" in b.last_health()["cloud"]["error"]
    fakes[CLOUD].mode = "ok"
    b.set_key("cloud", "sk-test-corrected-key-0000")
    assert "cloud" not in b.last_health()
    assert asyncio.run(b.list_models("cloud"))["cached"] is False
    assert b.last_health()["cloud"]["ok"] is True
    b.clear_key("cloud")
    assert "cloud" not in b.last_health()
    assert asyncio.run(b.list_models("cloud"))["cached"] is False


def test_someone_who_is_not_the_owner_gets_the_list_already_held_from_a_provider_just_checked(
        setup, core):
    b, fakes = setup()
    asyncio.run(b.health())  # checked a moment ago
    asked = len(fakes[CLOUD].requests)
    b._state.set_models("cloud", ["held"], 1)  # a list long past its cache time
    assert asyncio.run(b.list_models("cloud", recheck=False)) == {
        "provider_id": "cloud", "models": ["held"], "cached": True, "fetched_ms": 1}
    assert len(fakes[CLOUD].requests) == asked
    assert asyncio.run(b.list_models("cloud"))["cached"] is False  # the owner still renews it
    assert len(fakes[CLOUD].requests) == asked + 1


@pytest.mark.parametrize("change", ["address", "key"])
def test_a_check_still_on_its_way_when_the_address_or_key_changes_is_not_remembered(
        setup, core, change):
    b, fakes = setup()

    class ChangedMeanwhile(FakeProvider):
        def __call__(self, request):
            if change == "address":
                b.save_provider(b.provider("cloud").as_dict()
                                | {"base_url": "https://moved.example.com/v1"}, actor="owner")
            else:
                b.set_key("cloud", "sk-test-another-key-000000")
            return super().__call__(request)

    b.transport.hosts[CLOUD] = ChangedMeanwhile("openai_compatible")
    # The caller who asked before the change still gets the answer to what they asked.
    assert asyncio.run(b.list_models("cloud"))["models"] == ["model-a", "model-b"]
    assert "cloud" not in b.last_health() and b._state.models("cloud") is None
    assert b.describe(b.provider("cloud"))["health"] == {
        "ok": None, "checked_ms": None, "error": None}


def test_a_call_still_on_its_way_when_the_address_changes_does_not_mark_the_new_one_reachable(
        setup, core):
    b, fakes = setup()

    class MovedMeanwhile(FakeProvider):
        def __call__(self, request):
            b.save_provider(b.provider("cloud").as_dict()
                            | {"base_url": "https://moved.example.com/v1"}, actor="owner")
            return super().__call__(request)

    b.transport.hosts[CLOUD] = MovedMeanwhile("openai_compatible")
    reply = asyncio.run(b.chat(HELLO, provider_id="cloud", model="model-b", **WHO))
    assert reply.provider_id == "cloud" and len(kinds(core, "model.called")) == 1
    assert "cloud" not in b.last_health()
