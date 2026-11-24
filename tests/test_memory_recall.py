"""Recall: keyword and meaning search, the budget, and receipts that tell the truth."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time

import pytest

from jimmyhq import config, memory
from jimmyhq.core import open_core
from jimmyhq.memory.errors import LibraryError
from jimmyhq.models import Target, brain
from jimmyhq.scheduler import Scheduler
from test_memory_fakes import tape_text, with_embedder

WHO = {"actor": "agent:scout", "caller": "chat.reply"}


@pytest.fixture
def lib(core):
    memory.status(core)
    return core.config.data / "memory"


def events(core, kind):
    return [e for e in core.tape.after(0, limit=100_000) if e.kind == kind]


def fill(core):
    """A few facts and some free text, by the owner."""
    for text, kind in (("Sam enjoys a cup of tea every afternoon", "person"),
                       ("The puppy sleeps in the hallway", "about"),
                       ("Invoices are sent on the first of the month", "rule"),
                       ("The spare key is under the blue flowerpot", "about")):
        memory.remember(core, text, kind=kind, actor="owner", confirmed=True)
    memory.save_note(core, "shared/travel.md",
                     "# Travel\n\nThe train to the coast leaves early.\n\n"
                     "## Packing\n\nAlways bring the green raincoat.\n", actor="owner")


def texts(found):
    return [p.text for p in found.passages]


# --- keyword search, and saying when that is all there is ------------------------


def test_without_an_embeddings_model_search_matches_words_and_says_so(core, lib):
    fill(core)
    found = memory.recall(core, "where is the spare key?", **WHO)
    assert texts(found)[0] == "The spare key is under the blue flowerpot"
    assert found.methods == ("keyword",) and found.embedding_model is None
    assert "No embeddings model is chosen" in found.meaning_note
    assert "Settings, Models" in found.meaning_note
    assert found.passages[0].why == "keyword" and found.passages[0].confirmed is True
    status = memory.status(core)["meaning_search"]
    assert status["on"] is False and status["model"] is None
    assert "No embeddings model is chosen" in status["reason"]
    assert events(core, "model.called") == [] and events(core, "model.failed") == []


def test_when_the_embeddings_provider_is_down_search_still_answers_by_keyword(core, lib):
    fill(core)
    fake = with_embedder(core, mode="down")
    found = memory.recall(core, "spare key", **WHO)
    assert texts(found)[0] == "The spare key is under the blue flowerpot"
    assert found.methods == ("keyword",)
    assert "could not be reached" in found.meaning_note
    assert "matched words only" in found.meaning_note
    assert fake.calls == 1 and len(events(core, "model.failed")) == 1


def test_a_slow_embeddings_model_does_not_hold_a_search_up(core, lib, monkeypatch):
    fill(core)
    with_embedder(core)
    core.settings.set("memory.embed_timeout_s", 1, actor="owner")

    async def never(*args, **kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(brain(core), "embed", never)
    found = memory.recall(core, "spare key", **WHO)
    assert found.methods == ("keyword",) and "took too long" in found.meaning_note
    assert texts(found)[0] == "The spare key is under the blue flowerpot"


def test_free_text_is_found_with_its_heading_and_common_words_do_not_pad_results(core, lib):
    fill(core)
    found = memory.recall(core, "what should I bring, the raincoat?", **WHO)
    first = found.passages[0]
    assert (first.text, first.note, first.heading, first.fact_id, first.confirmed) == (
        "Always bring the green raincoat.", "shared/travel.md", "Travel > Packing", None, None)
    assert first.id.startswith("c:")
    # Every passage here contains "the"; only the one that is about the question comes back.
    assert len(found.passages) == 1


# --- meaning search --------------------------------------------------------------


def test_meaning_search_finds_a_passage_that_shares_no_words_with_the_question(core, lib):
    fill(core)
    with_embedder(core)
    waiting = memory.recall(core, "espresso or another hot beverage", **WHO)
    assert waiting.passages == () and waiting.methods == ("keyword",)
    assert "still being prepared" in waiting.meaning_note
    done = memory.embed_pending(core)
    assert done["embedded"] == 6 and done["left"] == 0 and done["model"] == "home/embed-a"
    found = memory.recall(core, "espresso or another hot beverage", **WHO)
    assert texts(found) == ["Sam enjoys a cup of tea every afternoon"]
    assert found.passages[0].why == "meaning"
    assert found.methods == ("keyword", "meaning") and found.meaning_note is None
    assert found.embedding_model == "home/embed-a"
    both = memory.recall(core, "which animal sleeps in the hallway", **WHO)
    assert both.passages[0].text == "The puppy sleeps in the hallway"
    assert both.passages[0].why == "both"
    status = memory.status(core)
    assert status["meaning_search"] == {"on": True, "reason": None, "model": "home/embed-a",
                                        "local_only": True, "ready": 6, "waiting": 0}
    assert status["counts"]["chunks_with_vectors"] == 6


def test_embedding_happens_a_bounded_batch_at_a_time(core, lib):
    fill(core)
    fake = with_embedder(core)
    core.settings.set("memory.embed_batch", 4, actor="owner")
    assert memory.embed_pending(core)["embedded"] == 4
    assert memory.status(core)["meaning_search"]["waiting"] == 2
    assert memory.embed_pending(core) == {"embedded": 2, "left": 0, "model": "home/embed-a",
                                          "reason": None}
    assert memory.embed_pending(core)["embedded"] == 0 and fake.calls == 2
    # Only new or changed passages are embedded afterwards.
    memory.remember(core, "The cat prefers the windowsill", kind="about", actor="owner")
    assert memory.embed_pending(core)["embedded"] == 1
    assert fake.texts[-1].endswith("The cat prefers the windowsill")


def test_vectors_from_another_model_are_ignored_and_made_again(core, lib):
    fill(core)
    fake = with_embedder(core)
    memory.embed_pending(core)
    assert texts(memory.recall(core, "espresso", **WHO)) == [
        "Sam enjoys a cup of tea every afternoon"]
    brain(core).set_role("embeddings", Target("home", "embed-b"), actor="owner")
    changed = memory.recall(core, "espresso", **WHO)
    assert changed.passages == () and "meaning" not in changed.methods
    assert "still being prepared" in changed.meaning_note
    status = memory.status(core)["meaning_search"]
    assert (status["model"], status["ready"], status["waiting"]) == ("home/embed-b", 0, 6)
    assert status["on"] is False
    before = fake.calls
    assert memory.embed_pending(core)["embedded"] == 6 and fake.calls == before + 1
    assert texts(memory.recall(core, "espresso", **WHO)) == [
        "Sam enjoys a cup of tea every afternoon"]
    memory.embed_pending(core)  # nothing left to do: the old model's vectors are dropped
    models = {r["model"] for r in memory.library(core).read("SELECT model FROM vectors")}
    assert models == {"home/embed-b"}


def test_private_memory_is_not_sent_to_a_hosted_embeddings_model(core, lib):
    fill(core)
    fake = with_embedder(core, hosted=True)
    assert memory.embed_pending(core)["embedded"] == 0
    found = memory.recall(core, "spare key", **WHO)
    assert found.methods == ("keyword",) and texts(found)
    assert "stay on your own machines" in found.meaning_note
    assert "Keep meaning search on my own machines" in found.meaning_note
    status = memory.status(core)["meaning_search"]
    assert status["on"] is False and status["model"] is None and status["local_only"] is True
    assert "hosted provider" in status["reason"]
    assert fake.calls == 0 and fake.texts == []
    assert events(core, "model.called") == [] and events(core, "model.failed") == []
    # The owner may decide otherwise.
    core.settings.set("memory.embed_local_only", False, actor="owner")
    assert memory.embed_pending(core)["embedded"] == 6
    assert "meaning" in memory.recall(core, "espresso", **WHO).methods


def test_a_failing_embeddings_model_is_left_alone_for_a_while(core, lib):
    fill(core)
    fake = with_embedder(core, mode="down")
    first = memory.embed_pending(core)
    assert first["embedded"] == 0 and "could not be reached" in first["reason"]
    assert memory.embed_pending(core)["embedded"] == 0 and fake.calls == 1
    status = memory.status(core)["meaning_search"]
    assert status["on"] is False and "could not be reached" in status["reason"]
    fake.mode = "ok"
    lb = memory.library(core)
    lb.set_meta("embed_failed", lb.meta("embed_failed") | {"at_ms": 0})  # the wait is over
    assert memory.embed_pending(core)["embedded"] == 6
    assert memory.status(core)["meaning_search"]["on"] is True


# --- rebuilding ------------------------------------------------------------------


def test_the_index_rebuilt_from_the_files_gives_the_same_results(core, lib):
    fill(core)
    fake = with_embedder(core)
    memory.embed_pending(core)
    questions = ("spare key", "espresso", "raincoat for the coast", "animal in the hallway")

    def snapshot():
        return [[(p.id, p.text, p.note, p.heading, p.why) for p in
                 memory.recall(core, q, **WHO).passages] for q in questions]

    before, calls = snapshot(), fake.calls
    assert all(before)
    lb = memory.library(core)
    with lb._mutex:
        lb._db.execute("DELETE FROM chunks_fts")
        lb._db.execute("DELETE FROM chunks")
    assert memory.recall(core, "spare key", **WHO).passages == ()
    result = memory.reindex(core)
    assert result["reindexed"] == 7
    assert snapshot() == before
    # Passages that did not change keep their vectors: nothing is embedded again.
    assert memory.embed_pending(core)["embedded"] == 0
    assert memory.status(core)["counts"]["chunks_with_vectors"] == 6
    assert fake.calls == calls + 1 + len(questions)  # only the questions themselves


def test_changing_the_passage_size_rebuilds_the_index_at_the_next_scan(core, lib):
    body = " ".join(f"Sentence {i} about the lighthouse." for i in range(60))
    memory.save_note(core, "shared/long.md", body + "\n", actor="owner")
    memory.scan(core)
    assert memory.status(core)["index_version"] == 1
    before = memory.status(core)["counts"]["chunks"]
    core.settings.set("memory.chunk_chars", 200, actor="owner")
    memory.scan(core)
    assert memory.status(core)["counts"]["chunks"] > before * 2


# --- the budget and the receipt --------------------------------------------------


def test_recall_fills_the_budget_with_whole_passages_and_lists_what_it_left_out(core, lib):
    long = "The harbour ferry timetable changes in winter and " + "also " * 60 + "in spring"
    memory.remember(core, "The harbour ferry leaves at nine", kind="about", actor="owner")
    memory.remember(core, long, kind="about", actor="owner")
    memory.remember(core, "Harbour parking is free on Sunday", kind="about", actor="owner")
    core.settings.set("memory.keyword_floor", 0, actor="owner")
    everything = memory.recall(core, "harbour ferry", **WHO)
    assert len(everything.passages) == 3 and everything.trimmed == ()
    tight = memory.recall(core, "harbour ferry", budget_chars=160, **WHO)
    assert long not in tight.render()
    assert [p.text for p in tight.trimmed] == [long]
    assert set(texts(tight)) == {"The harbour ferry leaves at nine",
                                 "Harbour parking is free on Sunday"}
    assert len(tight.render()) <= 160
    for passage in tight.passages:  # never cut: each admitted passage is there whole
        assert passage.text in tight.render()
    assert memory.recall(core, "harbour ferry", budget_chars=10, **WHO).passages == ()
    one = memory.recall(core, "harbour ferry", k=1, **WHO)
    assert len(one.passages) == 1 and one.trimmed == ()


@pytest.mark.parametrize("budget", [None, 400, 120, 60, 1])
def test_the_receipt_describes_exactly_what_render_returns(core, lib, budget):
    fill(core)
    memory.remember(core, "The spare key for the shed is in the kitchen drawer " * 3,
                    kind="about", actor="owner")
    core.settings.set("memory.keyword_floor", 0, actor="owner")
    found = memory.recall(core, "the spare key and the puppy", budget_chars=budget, **WHO)
    block = found.render()
    (receipt,) = events(core, "memory.recalled")
    payload = receipt.payload
    assert receipt.id == found.receipt_id and receipt.actor == "agent:scout"
    assert payload["shown_chars"] == len(block) == found.shown_chars
    assert payload["trimmed"] == len(found.trimmed)
    assert [(p["id"], p["note"], p["chars"], p["why"]) for p in payload["passages"]] == [
        (p.id, p.note, len(p.text), p.why) for p in found.passages]
    # Rebuild the block from the receipt's own numbers: it accounts for every character.
    expected = 0
    for number, passage in enumerate(found.passages, 1):
        header = f"[{number}] ({passage.note}"
        header += f" > {passage.heading})" if passage.heading else ")"
        assert f"{header}\n{passage.text}" in block
        expected += len(header) + 1 + payload["passages"][number - 1]["chars"]
    expected += 2 * max(0, len(found.passages) - 1)
    assert expected == payload["shown_chars"]
    assert payload["budget_chars"] == (budget or 4000) and len(block) <= payload["budget_chars"]
    for passage in found.trimmed:
        assert passage.text not in block
    if budget == 1:
        assert block == "" and payload["passages"] == [] and payload["shown_chars"] == 0


def test_the_receipt_and_the_logs_never_carry_the_question(core, lib, caplog):
    fill(core)
    with_embedder(core)
    memory.embed_pending(core)
    question = "does anyone mention espresso or my secret diagnosis"
    with caplog.at_level("DEBUG"):
        found = memory.recall(core, question, **WHO)
    assert found.passages
    recorded = tape_text(core)
    for word in ("espresso", "diagnosis", "secret", "afternoon", "flowerpot"):
        assert word not in recorded and word not in caplog.text, word
    (receipt,) = events(core, "memory.recalled")
    assert receipt.payload["query_chars"] == len(question)
    # A keyed hash: not the plain SHA-256 a guess could be checked against.
    key = core.secrets.get("memory.receipt_key")
    assert key and key not in recorded and key not in caplog.text
    assert receipt.payload["query_hash"] == hmac.new(
        key.encode(), question.encode(), hashlib.sha256).hexdigest()
    assert receipt.payload["query_hash"] != hashlib.sha256(question.encode()).hexdigest()
    assert receipt.payload["caller"] == "chat.reply"
    assert receipt.payload["methods"] == ["keyword", "meaning"]
    assert receipt.payload["embedding_model"] == "home/embed-a"
    called = events(core, "model.called")[-1].payload
    assert called["embedding"] is True and called["caller"] == "memory.recall"
    assert called["prompt_ref"] is None


def test_a_question_with_no_words_returns_nothing_and_still_leaves_a_receipt(core, lib):
    fill(core)
    with_embedder(core)
    for question in ("", "   ", "?!"):
        found = memory.recall(core, question, **WHO)
        assert found.passages == () and found.render() == "" and found.methods == ()
    assert len(events(core, "memory.recalled")) == 3
    assert events(core, "model.called") == []


# --- ranking ---------------------------------------------------------------------


def test_passages_that_say_the_same_thing_are_returned_once(core, lib):
    memory.save_note(core, "shared/a.md", "The lighthouse keeper visits on Tuesday.\n",
                     actor="owner")
    memory.save_note(core, "shared/b.md", "the lighthouse keeper visits on tuesday\n",
                     actor="owner")
    memory.save_note(core, "shared/c.md", "The lighthouse lamp was replaced.\n", actor="owner")
    core.settings.set("memory.keyword_floor", 0, actor="owner")
    found = memory.recall(core, "lighthouse keeper", **WHO)
    assert len(found.passages) == 2 and found.trimmed == ()
    assert sum("keeper" in t.lower() for t in texts(found)) == 1


def test_a_confirmed_fact_and_newer_text_are_preferred_a_little(core, lib):
    one = memory.remember(core, "The orchard gate code is on the fridge", kind="about",
                          actor="agent:miner")
    two = memory.remember(core, "Orchard gate code changes are sent by post", kind="about",
                          actor="agent:miner")
    core.settings.set("memory.recency_boost", 0, actor="owner")
    core.settings.set("memory.confirmed_boost", 0, actor="owner")
    order = [p.fact_id for p in memory.recall(core, "orchard gate code", **WHO).passages]
    assert set(order) == {one.id, two.id}
    second = order[1]
    core.settings.set("memory.confirmed_boost", 2, actor="owner")
    memory.edit_fact(core, second, actor="owner", confirmed=True)
    promoted = memory.recall(core, "orchard gate code", **WHO).passages
    assert promoted[0].fact_id == second and promoted[0].confirmed is True
    # Now age the confirmed one and prefer newer text strongly instead.
    core.settings.set("memory.confirmed_boost", 0, actor="owner")
    core.settings.set("memory.recency_boost", 2, actor="owner")
    core.settings.set("memory.recency_half_life_days", 1, actor="owner")
    lb = memory.library(core)
    with lb._mutex:
        lb._db.execute("UPDATE facts SET updated_ms = updated_ms - 864000000 WHERE id = ?",
                       (second,))
    assert memory.recall(core, "orchard gate code", **WHO).passages[0].fact_id == order[0]


def test_a_weight_of_zero_leaves_that_kind_of_search_out(core, lib):
    fill(core)
    with_embedder(core)
    memory.embed_pending(core)
    core.settings.set("memory.weight_keyword", 0, actor="owner")
    found = memory.recall(core, "spare key espresso", **WHO)
    assert found.methods == ("meaning",) and texts(found) == [
        "Sam enjoys a cup of tea every afternoon"]
    core.settings.set("memory.weight_keyword", 1, actor="owner")
    core.settings.set("memory.weight_meaning", 0, actor="owner")
    found = memory.recall(core, "spare key espresso", **WHO)
    assert found.methods == ("keyword",) and found.meaning_note is None
    assert texts(found) == ["The spare key is under the blue flowerpot"]


# --- scope -----------------------------------------------------------------------


def test_scope_keeps_an_agents_notes_apart_from_shared_memory(core, lib):
    memory.remember(core, "The owner likes lemon cake", kind="about", actor="owner")
    memory.remember(core, "Scout tried the lemon cake recipe twice", kind="finding",
                    actor="agent:scout", note="agents/scout/notes.md")
    memory.remember(core, "Baker keeps lemon cake notes too", kind="finding",
                    actor="agent:baker", note="agents/baker/notes.md")
    core.settings.set("memory.keyword_floor", 0, actor="owner")

    def notes(**kw):
        return sorted(p.note for p in memory.recall(core, "lemon cake", **WHO, **kw).passages)

    assert notes() == ["shared/about-me.md"]
    assert notes(scope="agents/scout") == ["agents/scout/notes.md"]
    assert notes(scope=("shared", "agents/scout")) == ["agents/scout/notes.md",
                                                       "shared/about-me.md"]
    assert notes(scope="agents") == ["agents/baker/notes.md", "agents/scout/notes.md"]
    assert len(notes(scope="*")) == 3
    assert events(core, "memory.recalled")[1].payload["scope"] == ["agents/scout"]


@pytest.mark.parametrize("bad", [
    {"scope": "../outside"}, {"scope": ()}, {"scope": ["shared"] * 21}, {"scope": 5},
    {"scope": "shared\\x"}, {"k": 0}, {"k": 51}, {"k": "many"}, {"k": True},
    {"budget_chars": 0}, {"budget_chars": -5}, {"budget_chars": 1.5}, {"actor": ""},
    {"caller": "Not A Caller"}, {"caller": None}, {"query": 5}, {"query": "x" * 4001},
    {"query": "bad \ud800 surrogate"},
])
def test_recall_refuses_arguments_it_cannot_honour(core, lib, bad):
    args = {"query": "tea"} | WHO | bad
    with pytest.raises(LibraryError) as e:
        memory.recall(core, args.pop("query"), **args)
    assert e.value.status == 422 and e.value.detail.endswith(".")
    assert events(core, "memory.recalled") == []


def test_recall_works_from_async_code_both_ways(core, lib):
    fill(core)

    async def run():
        awaited = await memory.arecall(core, "spare key", **WHO)
        blocking = memory.recall(core, "spare key", **WHO)  # allowed, though it waits
        return awaited, blocking

    awaited, blocking = asyncio.run(run())
    assert texts(awaited) == texts(blocking) == ["The spare key is under the blue flowerpot"]


# --- the worker ------------------------------------------------------------------


def test_the_worker_scans_when_due_and_embeds_in_the_background(core, lib):
    scheduler = Scheduler(core.metrics)
    memory.jobs(core, scheduler)
    jobs = scheduler._jobs
    assert set(jobs) == {"memory.scan", "memory.embed", "memory.prune", "memory.mine"}
    fake = with_embedder(core)
    (lib / "shared" / "byhand.md").write_text("- The canoe is stored in the barn\n")
    quiet = time.time() - 60  # written a while ago: the background scan leaves fresh files
    os.utime(lib / "shared" / "byhand.md", (quiet, quiet))
    jobs["memory.scan"].fn()  # scanned a moment ago (first use): not due yet
    assert memory.list_facts(core)["facts"] == []
    lb = memory.library(core)
    lb.set_meta("last_scan", lb.meta("last_scan") | {"at_ms": 0})
    core.settings.set("memory.watch_disk", False, actor="owner")
    jobs["memory.scan"].fn()  # switched off: the owner scans by hand
    assert memory.list_facts(core)["facts"] == []
    core.settings.set("memory.watch_disk", True, actor="owner")
    assert scheduler.tick() == ["memory.scan", "memory.embed", "memory.prune", "memory.mine"]
    assert [f["text"] for f in memory.list_facts(core)["facts"]] == [
        "The canoe is stored in the barn"]
    assert fake.calls == 1 and memory.status(core)["meaning_search"]["waiting"] == 0
    assert all(j["ok"] for j in core.metrics.jobs())


def test_the_worker_comes_back_soon_for_a_note_it_left_because_it_was_being_written(core, lib):
    scheduler = Scheduler(core.metrics)
    memory.jobs(core, scheduler)
    scan_job = scheduler._jobs["memory.scan"].fn
    lb = memory.library(core)
    path = lib / "shared" / "fresh.md"
    path.write_text("- Written this very second\n")
    lb.set_meta("last_scan", lb.meta("last_scan") | {"at_ms": 0})
    scan_job()
    last = lb.meta("last_scan")
    assert last["deferred"] == 1 and memory.list_facts(core)["facts"] == []
    assert path.read_text() == "- Written this very second\n"
    scan_job()  # only a moment later: not yet
    assert lb.meta("last_scan")["at_ms"] == last["at_ms"]
    quiet = time.time() - 60
    os.utime(path, (quiet, quiet))
    lb.set_meta("last_scan", last | {"at_ms": last["at_ms"] - 11_000})  # one tick later
    scan_job()  # well inside the usual interval, because something was left waiting
    assert [f["text"] for f in memory.list_facts(core)["facts"]] == ["Written this very second"]


def test_scope_is_exact_about_case_and_holds_for_meaning_search_too(core, lib):
    memory.remember(core, "Scout fed the puppy at noon", kind="finding", actor="agent:scout",
                    note="agents/scout/notes.md")
    memory.remember(core, "Baker walked the dog at dusk", kind="finding", actor="agent:baker",
                    note="agents/baker/notes.md")
    core.settings.set("memory.keyword_floor", 0, actor="owner")

    def notes(scope, question="puppy dog"):
        found = memory.recall(core, question, scope=scope, **WHO)
        return sorted({p.note for p in found.passages})

    assert notes("agents/scout") == ["agents/scout/notes.md"]
    for other in ("agents/SCOUT", "Agents/scout", "agents/sc_ut", "agents/scou%", "agents/sco"):
        assert notes(other) == [], other
    with_embedder(core)
    memory.embed_pending(core)
    # "animal" shares no word with either note: only meaning search can find them.
    assert notes("agents/scout", "which animal") == ["agents/scout/notes.md"]
    assert notes("agents/baker", "which animal") == ["agents/baker/notes.md"]
    assert notes("agents/SCOUT", "which animal") == []
    assert notes("agents", "which animal") == ["agents/baker/notes.md", "agents/scout/notes.md"]


def test_with_local_only_on_a_hosted_embeddings_model_is_never_contacted_even_as_a_fallback(
        core, lib):
    from test_memory_fakes import CLOUD, HOME, Embedder
    from test_models_fakes import Network, add
    fill(core)
    home, cloud = Embedder(), Embedder()
    b = brain(core)
    b.transport = Network({HOME: home, CLOUD: cloud})
    add(b, "home", host=f"{HOME}:11434")
    add(b, "cloud", host=CLOUD, key="sk-test-embed-key-000000")
    # Hosted first, local second: only the local one may ever see memory text.
    b.set_role("embeddings", Target("cloud", "embed-big"), [Target("home", "embed-a")],
               actor="owner")
    assert memory.embed_pending(core)["model"] == "home/embed-a"
    found = memory.recall(core, "espresso or another hot beverage", **WHO)
    assert found.embedding_model == "home/embed-a" and texts(found)
    assert cloud.calls == 0 and home.calls == 2
    # The local one first and failing: the hosted fallback is still not asked, and
    # saving and searching carry on without meaning search.
    b.set_role("embeddings", Target("home", "embed-a"), [Target("cloud", "embed-big")],
               actor="owner")
    home.mode = "down"
    memory.save_note(core, "shared/later.md", "- The spare bulbs are in the cellar\n",
                     actor="owner")
    assert memory.embed_pending(core)["embedded"] == 0
    found = memory.recall(core, "where are the spare bulbs", **WHO)
    assert texts(found)[0] == "The spare bulbs are in the cellar"
    assert found.methods == ("keyword",) and "matched words only" in found.meaning_note
    assert cloud.calls == 0 and cloud.texts == []
    assert all(e.payload["provider_id"] == "home" for e in events(core, "model.failed"))


def test_an_unexpected_error_from_the_embeddings_call_does_not_stop_a_search(
        core, lib, monkeypatch, caplog):
    fill(core)
    with_embedder(core)

    async def broken(*args, **kwargs):
        raise RuntimeError("the spare key question must not be in here")

    monkeypatch.setattr(brain(core), "embed", broken)
    found = memory.recall(core, "spare key", **WHO)
    assert texts(found)[0] == "The spare key is under the blue flowerpot"
    assert found.methods == ("keyword",) and "unexpected error" in found.meaning_note
    assert "RuntimeError" in caplog.text and "spare key question" not in caplog.text



def test_equal_questions_share_a_hash_on_one_install_and_not_across_installs(core, lib, tmp_path):
    fill(core)
    memory.recall(core, "tea or coffee", **WHO)
    memory.recall(core, "tea or coffee", **WHO)
    memory.recall(core, "tea or coffee?", **WHO)
    one, two, three = (e.payload["query_hash"] for e in events(core, "memory.recalled"))
    assert one == two != three and len(one) == 64
    (tmp_path / "other").mkdir()
    elsewhere = open_core(config.Config(data=tmp_path / "other", host="127.0.0.1", port=0,
                                        allowed_hosts=("testserver",), remote_setup=True))
    try:
        memory.recall(elsewhere, "tea or coffee", **WHO)
        (other,) = events(elsewhere, "memory.recalled")
        assert other.payload["query_hash"] != one
        assert elsewhere.secrets.get("memory.receipt_key") != core.secrets.get(
            "memory.receipt_key")
    finally:
        elsewhere.close()


def test_a_private_recall_leaves_a_receipt_that_says_nothing_about_the_question(core, lib):
    fill(core)
    question = "does anyone mention tea in the afternoon"
    usual = memory.recall(core, question, **WHO)
    quiet = memory.recall(core, question, private=True, **WHO)
    assert quiet.render() == usual.render() and quiet.passages
    first, second = events(core, "memory.recalled")
    assert second.payload["query_hash"] == "" and second.payload["query_chars"] == 0
    assert (second.cause, second.root, second.subject) == (None, None, None)
    same = ("caller", "scope", "passages", "trimmed", "methods", "shown_chars", "k",
            "budget_chars", "embedding_model")
    assert {k: first.payload[k] for k in same} == {k: second.payload[k] for k in same}
    assert second.payload["shown_chars"] == len(quiet.render())
    with pytest.raises(memory.LibraryError):
        memory.recall(core, question, private="yes", **WHO)


def test_a_private_recall_works_from_async_code_too(core, lib):
    fill(core)

    async def ask():
        return await memory.arecall(core, "espresso", private=True, **WHO)

    assert asyncio.run(ask()).receipt_id
    assert events(core, "memory.recalled")[-1].payload["query_hash"] == ""
