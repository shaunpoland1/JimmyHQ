"""Relevance: what a search or a chat recall presents as found, and what it leaves out.

Meaning search ranks every passage, so in a small Library every fact is the
"nearest" to something. These tests use a fake embeddings model built like a
real one: every sentence about the owner shares a common direction, so even
unrelated facts are moderately similar to any question (0.5 here, well above
the absolute floor). Only how much closer the right fact is tells them apart.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import test_memory_fakes
from jimmyhq import memory
from jimmyhq.web.app import create_app
from test_memory_fakes import with_embedder

WHO = {"actor": "owner", "caller": "chat.reply"}
SEARCH = {"actor": "owner", "caller": "memory.search"}

TOPICS = {
    "age": ("age", "old", "years", "birthday"),
    "colour": ("colour", "green", "shade"),
    "food": ("food", "spicy", "eating"),
    "work": ("work", "carpenter", "job"),
    "pet": ("cat", "dog", "puppy", "goldfish", "pets"),
}

FIVE = ("My age is 41",
        "My favourite colour is green",
        "I work as a carpenter",
        "I love spicy food",
        "The cat is called Pebble")


def personal_vector(text: str) -> list[float]:
    words = "".join(ch if ch.isalnum() else " " for ch in text.lower()).split()
    axes = [float(sum(w in topic for w in words)) for topic in TOPICS.values()]
    return [*axes, 1.0]  # what every sentence about a person has in common


@pytest.fixture
def embedder(core, monkeypatch):
    memory.status(core)
    monkeypatch.setattr(test_memory_fakes, "vector", personal_vector)
    return with_embedder(core)


def keep(core, *facts):
    for text in facts:
        memory.remember(core, text, kind="about", actor="owner", confirmed=True)
    memory.embed_pending(core)


def found(recall):
    return [(p.text, p.why) for p in recall.passages]


def test_searching_age_among_five_facts_returns_the_age_fact_and_not_the_other_four(
        core, embedder):
    keep(core, *FIVE)
    result = memory.recall(core, "age", **SEARCH, related_max=3)
    assert found(result) == [("My age is 41", "both")]
    assert result.methods == ("keyword", "meaning")


def test_the_age_fact_is_found_by_meaning_even_when_it_does_not_use_the_word(core, embedder):
    keep(core, "I am 41 years old", *FIVE[1:])
    result = memory.recall(core, "age", **SEARCH, related_max=3)
    assert found(result) == [("I am 41 years old", "meaning")]


def test_a_word_is_never_matched_inside_a_longer_word(core):
    memory.status(core)
    keep(core, "I speak two languages", "I manage a small team", "The garage is full")
    assert memory.recall(core, "age", **SEARCH).passages == ()


def test_chat_recall_does_not_pass_along_facts_that_are_merely_the_nearest_of_a_few(
        core, embedder):
    keep(core, *FIVE)
    result = memory.recall(core, "how old am I?", **WHO)
    assert found(result) == [("My age is 41", "meaning")]


def test_chat_recall_keeps_every_fact_a_question_is_about(core, embedder):
    keep(core, *FIVE)
    result = memory.recall(core, "what am I eating tonight, and which shade suits me?", **WHO)
    assert sorted(found(result)) == [("I love spicy food", "meaning"),
                                     ("My favourite colour is green", "meaning")]


def test_on_the_search_screen_matches_come_first_and_only_a_few_related_follow(
        core, embedder):
    keep(core, "The cat is called Pebble", "Our dog barks at night",
         "A puppy is coming in spring", "We keep two goldfish", "I work as a carpenter")
    result = memory.recall(core, "cat", **SEARCH, related_max=2)
    assert result.passages[0].text == "The cat is called Pebble"
    assert result.passages[0].why == "both"
    assert [p.why for p in result.passages[1:]] == ["meaning", "meaning"]
    only_words = memory.recall(core, "cat", **SEARCH, related_max=0)
    assert found(only_words) == [("The cat is called Pebble", "both")]
    # A prompt is not limited by the screen's setting, only by k.
    assert len(memory.recall(core, "cat", **WHO).passages) == 4


def test_the_margin_is_a_setting_and_at_one_every_nearest_fact_comes_back(core, embedder):
    keep(core, *FIVE)
    core.settings.set("memory.related_margin", 1.0, actor="owner")
    result = memory.recall(core, "age", **SEARCH, related_max=10)
    assert result.passages[0].text == "My age is 41"
    assert len(result.passages) == 5
    assert all(p.why == "meaning" for p in result.passages[1:])


def test_the_search_screen_uses_the_related_passages_setting(core, embedder):
    keep(core, "The cat is called Pebble", "Our dog barks at night",
         "A puppy is coming in spring", "We keep two goldfish")
    with TestClient(create_app(core), base_url="http://testserver") as client:
        assert client.post("/api/auth/setup", json={
            "name": "Sam", "password": "correct horse battery"}).status_code == 200
        core.settings.set("memory.related_max", 1, actor="owner")
        body = client.post("/api/memory/search", json={"q": "cat"}).json()
    assert [p["why"] for p in body["passages"]] == ["both", "meaning"]


@pytest.mark.parametrize("bad", [-1, 51, True, 1.5])
def test_recall_refuses_a_related_limit_it_cannot_honour(core, bad):
    memory.status(core)
    with pytest.raises(memory.LibraryError):
        memory.recall(core, "age", **SEARCH, related_max=bad)
