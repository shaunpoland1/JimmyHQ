"""Embeddings through the Brain: routing, batching, receipts and usage."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jimmyhq.models import EmbedReply, ModelError, Target, brain
from jimmyhq.models.brain import EMBED_BATCH
from test_memory_fakes import CLOUD, HOME, Embedder, tape_text, vector, with_embedder
from test_models_fakes import FakeProvider, Network, add

WHO = {"actor": "owner", "caller": "memory.index"}
SECRET = "the quarterly budget is private"


def embed(core, texts, **options):
    return asyncio.run(brain(core).embed(texts, **WHO, **options))


def test_embed_returns_one_vector_per_text_in_the_order_asked(core):
    fake = with_embedder(core)
    reply = embed(core, ["tea", "a dog and a cat", "nothing here"])
    assert isinstance(reply, EmbedReply)
    assert reply.vectors == [vector("tea"), vector("a dog and a cat"), vector("nothing here")]
    assert (reply.provider_id, reply.model, reply.is_local) == ("home", "embed-a", True)
    assert reply.name == "home/embed-a" and reply.input_tokens == 8
    assert fake.calls == 1


def test_embed_sends_the_documented_request_shape(core):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content),
                     request.headers.get("authorization")))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}],
                                         "usage": {"prompt_tokens": 1, "total_tokens": 1}})

    b = brain(core)
    b.transport = httpx.MockTransport(handler)
    add(b, "cloud", "openai", key="sk-test-embed-key-000000")
    b.set_role("embeddings", Target("cloud", "embed-a"), actor="owner")
    embed(core, ["hello"])
    assert seen == [("POST", "/v1/embeddings", {"model": "embed-a", "input": ["hello"]},
                     "Bearer sk-test-embed-key-000000")]


def test_many_texts_are_sent_in_batches_and_still_get_one_receipt(core):
    fake = with_embedder(core)
    texts = [f"text number {i}" for i in range(EMBED_BATCH * 2 + 3)]
    reply = embed(core, texts)
    assert len(reply.vectors) == len(texts) and fake.calls == 3
    receipts = [e for e in core.tape.after(0) if e.kind == "model.called"]
    assert len(receipts) == 1
    payload = receipts[0].payload
    assert payload["embedding"] is True and payload["inputs"] == len(texts)
    assert payload["output_tokens"] == 0 and payload["role"] == "embeddings"
    assert payload["prompt_ref"] is None and payload["completion_ref"] is None


def test_the_texts_are_never_stored_or_logged(core, caplog):
    with_embedder(core)
    with caplog.at_level("DEBUG"):
        embed(core, [SECRET])
    assert SECRET not in tape_text(core) and SECRET not in caplog.text
    assert not list(core.config.vault_dir.rglob("*")) or all(
        SECRET.encode() not in p.read_bytes()
        for p in core.config.vault_dir.rglob("*") if p.is_file())


def test_embed_without_an_embeddings_model_says_what_to_set_up(core):
    b = brain(core)
    add(b, "home", host=f"{HOME}:11434")
    b.set_role("default", Target("home", "model-a"), actor="owner")  # never borrowed
    with pytest.raises(ModelError, match="No model is chosen for the 'embeddings' role") as e:
        embed(core, ["tea"])
    assert e.value.kind == "not_configured"


def test_local_only_embedding_never_reaches_a_hosted_provider(core):
    fake = with_embedder(core, hosted=True)
    with pytest.raises(ModelError) as e:
        embed(core, ["tea"], local_only=True)
    assert e.value.kind == "local_only" and fake.calls == 0
    assert not [e for e in core.tape.after(0) if e.kind.startswith("model.called")]
    assert embed(core, ["tea"]).is_local is False  # allowed when the caller does not insist


def test_a_failed_embeddings_provider_falls_back_and_leaves_a_failure_receipt(core):
    down, up = Embedder("down"), Embedder()
    b = brain(core)
    b.transport = Network({HOME: down, CLOUD: up})
    add(b, "home", host=f"{HOME}:11434")
    add(b, "cloud", host=CLOUD, key="sk-test-embed-key-000000")
    b.set_role("embeddings", Target("home", "embed-a"), [Target("cloud", "embed-b")],
               actor="owner")
    reply = embed(core, ["tea"])
    assert reply.name == "cloud/embed-b"
    failed = [e for e in core.tape.after(0) if e.kind == "model.failed"]
    assert len(failed) == 1 and failed[0].payload["embedding"] is True
    assert failed[0].payload["reason"] == "unreachable"
    with pytest.raises(ModelError):  # a caller that must not mix models can say so
        embed(core, ["tea"], no_fallback=True)


@pytest.mark.parametrize("mode", ["garbage", "short"])
def test_an_unusable_embeddings_answer_is_a_readable_error(core, mode):
    with_embedder(core, mode=mode)
    with pytest.raises(ModelError) as e:
        embed(core, ["tea", "dog"])
    assert e.value.kind == "bad_reply"


@pytest.mark.parametrize("kind", ["anthropic", "google"])
def test_a_provider_type_without_embeddings_says_so(core, kind):
    b = brain(core)
    b.transport = Network({"other.example.com": FakeProvider(kind)})
    add(b, "other", kind, host="other.example.com", key="sk-test-other-key-000000")
    b.set_role("embeddings", Target("other", "embed-a"), actor="owner")
    with pytest.raises(ModelError, match="cannot make embeddings yet") as e:
        embed(core, ["tea"])
    assert e.value.kind == "no_embeddings"


@pytest.mark.parametrize("texts", [[], "one string", [""], ["ok", 5], [" "]])
def test_embed_refuses_input_that_is_not_a_list_of_texts(core, texts):
    with_embedder(core)
    with pytest.raises(ModelError) as e:
        embed(core, texts)
    assert e.value.kind == "bad_request"


def test_embedding_tokens_are_counted_apart_from_chat_calls(core):
    b = brain(core)
    with_embedder(core, hosted=True)
    b.set_prices([{"id": "e", "provider": "cloud", "model": "embed-*", "input_per_mtok": 2,
                   "output_per_mtok": 0}], actor="owner")
    embed(core, ["one two three", "four five"])
    embed(core, ["six"])
    total = b.usage()["all_time"]
    assert (total["calls"], total["input_tokens"], total["output_tokens"]) == (0, 0, 0)
    assert total["models"] == []
    assert total["embeddings"] == {
        "calls": 2, "input_tokens": 6, "local_calls": 0, "local_input_tokens": 0,
        "estimated_cost": pytest.approx(6 * 2 / 1e6), "unpriced_calls": 0}
    assert total["estimated_cost"] == pytest.approx(6 * 2 / 1e6)


def test_embed_sync_is_for_code_that_is_not_async(core):
    with_embedder(core)
    assert len(brain(core).embed_sync(["tea"], **WHO).vectors) == 1

    async def inside():
        return brain(core).embed_sync(["tea"], **WHO)

    with pytest.raises(RuntimeError, match="await brain.embed"):
        asyncio.run(inside())
