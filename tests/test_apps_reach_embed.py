"""Similarity for apps (``model.embed``), from the side of an app that floods it, sends too
much, or has no right to it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jimmyhq.apps import bridge, quotas, reach
from jimmyhq.models import brain
from test_apps_bridge_fakes import WATER, call, events, installs, refused
from test_memory_fakes import vector, with_embedder

DRAFTS = json.loads((Path(__file__).resolve().parents[1] / "apps" / "examples" / "drafts"
                     / "jimmyhq.app.json").read_text(encoding="utf-8"))


@pytest.fixture
def apps(monkeypatch, tmp_path):
    return installs(monkeypatch, tmp_path)


def test_similarity_goes_to_the_embeddings_role_as_the_app_and_counts_its_tokens(core, apps):
    apps.add(WATER, granted=["model.ask"])
    fake = with_embedder(core)
    got = call(core, "water", "model.embed", {"texts": ["a dog", "a train trip"]})
    assert got == {"vectors": [vector("a dog"), vector("a train trip")],
                   "model": "home/embed-a", "local": True}
    assert fake.texts == ["a dog", "a train trip"]
    [receipt] = events(core, "model.called")
    assert receipt.actor == "app:water" and receipt.payload["caller"] == "apps.water"
    assert receipt.payload["embedding"] is True
    assert "dog" not in json.dumps(receipt.payload)
    assert quotas.used(core, "water", "model_tokens_per_day") == 5
    assert quotas.used(core, "water", "embed_calls_per_hour") == 1


def test_similarity_without_model_ask_is_refused_and_nothing_is_sent(core, apps):
    apps.add(WATER, granted=["tasks.write"])
    fake = with_embedder(core)
    error = refused(core, "water", "model.embed", {"texts": ["a dog"]})
    assert error.code == "not_allowed" and "\"model.ask\"" in error.message
    assert fake.calls == 0


def test_a_flood_of_similarity_calls_stops_at_the_hourly_limit(core, apps):
    apps.add(WATER, granted=["model.ask"])
    fake = with_embedder(core)
    core.settings.set("apps.quota.embed_calls_per_hour", 3, actor="owner")
    for _ in range(3):
        call(core, "water", "model.embed", {"texts": ["a dog"]})
    for _ in range(5):
        error = refused(core, "water", "model.embed", {"texts": ["a dog"]})
        assert error.code == "quota" and "all 3 similarity calls" in error.message
        assert error.retry_after_s is not None and error.retry_after_s > 0
    assert fake.calls == 3


def test_similarity_stops_once_the_days_model_tokens_are_spent(core, apps):
    apps.add(WATER, granted=["model.ask"])
    fake = with_embedder(core)
    core.settings.set("apps.quota.model_tokens_per_day", 2, actor="owner")
    call(core, "water", "model.embed", {"texts": ["a dog and a cat"]})
    assert refused(core, "water", "model.embed", {"texts": ["a dog"]}).code == "quota"
    assert fake.calls == 1


def test_more_texts_than_the_apps_limit_are_refused_before_anything_is_sent(core, apps):
    apps.add(WATER, granted=["model.ask"])
    fake = with_embedder(core)
    core.settings.set("apps.quota.embed_texts", 2, actor="owner")
    error = refused(core, "water", "model.embed", {"texts": ["a", "b", "c"]})
    assert error.code == "too_large" and "at most 2 texts" in error.message
    assert fake.calls == 0 and quotas.used(core, "water", "embed_calls_per_hour") == 0


def test_a_text_longer_than_the_apps_limit_is_refused(core, apps):
    apps.add(WATER, granted=["model.ask"])
    fake = with_embedder(core)
    core.settings.set("apps.quota.embed_text_chars", 10, actor="owner")
    error = refused(core, "water", "model.embed", {"texts": ["x" * 11]})
    assert error.code == "too_large" and "at most 10 characters" in error.message
    assert fake.calls == 0


@pytest.mark.parametrize("texts", ["one text", [], [""], ["  "], [5], None, [["a"]],
                                   {"a": "b"}])
def test_texts_given_wrongly_are_refused(core, apps, texts):
    apps.add(WATER, granted=["model.ask"])
    fake = with_embedder(core)
    error = refused(core, "water", "model.embed", {"texts": texts})
    assert error.code == "invalid" and fake.calls == 0


@pytest.mark.parametrize("extra", [{"model": "other"}, {"local_only": False},
                                   {"actor": "owner"}, {"role": "default"}])
def test_a_similarity_call_cannot_choose_its_model_or_who_asks(core, apps, extra):
    apps.add(WATER, granted=["model.ask"])
    fake = with_embedder(core)
    error = refused(core, "water", "model.embed", {"texts": ["a"], **extra})
    assert error.code == "invalid" and fake.calls == 0


def test_with_no_embeddings_model_the_app_is_told_in_a_sentence(core, apps):
    apps.add(WATER, granted=["model.ask"])
    brain(core)  # nothing chosen for any role
    error = refused(core, "water", "model.embed", {"texts": ["a"]})
    assert error.code == "unavailable" and "'embeddings' role" in error.message
    assert "nothing was sent" in error.message


def test_a_private_apps_texts_go_only_to_a_local_embeddings_model(core, apps):
    apps.add(DRAFTS, private=True)
    fake = with_embedder(core, hosted=True)
    error = refused(core, "drafts", "model.embed", {"texts": ["a private note"]})
    assert error.code == "unavailable" and "Nothing was sent anywhere" in error.message
    assert fake.calls == 0


def test_under_local_only_a_sandboxed_apps_texts_never_reach_a_hosted_model(core, apps):
    apps.add(WATER, granted=["model.ask"])
    core.settings.set(bridge.LOCAL_ONLY, True, actor="owner")
    fake = with_embedder(core, hosted=True)
    assert refused(core, "water", "model.embed", {"texts": ["a"]}).code == "unavailable"
    assert fake.calls == 0


def test_check_embed_texts_holds_each_limit(core):
    assert reach.check_embed_texts(core, "water", ["a", "b"]) == ["a", "b"]
    core.settings.set("apps.quota_overrides", ["water.embed_texts=1"], actor="owner")
    with pytest.raises(bridge.BridgeError):
        reach.check_embed_texts(core, "water", ["a", "b"])
    assert reach.check_embed_texts(core, "other", ["a", "b"]) == ["a", "b"]
