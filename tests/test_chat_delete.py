"""Deleting a conversation for good: what goes, what stays, who may ask, and
the setting that deletes archived conversations after a number of days."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from jimmyhq import bench, chat, lookup, memory
from jimmyhq.bench.cards import decide
from jimmyhq.chat import deletion, store
from jimmyhq.core import open_core
from jimmyhq.ids import now_ms
from jimmyhq.memory import mining
from jimmyhq.scheduler import Scheduler
from jimmyhq.web.app import create_app
from test_chat_fakes import SlowProvider, models, of_kind
from test_chat_record import BASE, PASSWORD, again, answers, ask, events_of, flip
from test_chat_web import ANSWER, PAGE, Scripted, fetch, search
from test_chat_web import run as web_reply
from test_chat_web import setup as web_setup
from test_memory_fakes import fact, with_miner
from test_memory_mining import TEA, cands, cards, from_owner, talk

DAY = 86_400_000
HOUR = 3_600_000
TITLE = "Gooseberry ledger"
Q, A = "Where is the gooseberry fool recipe?", "Under the rhubarb crumble card."
WORDS = ("ooseberry", "hubarb", "ulberry", "loganberry", "edlar")


@pytest.fixture
def owner(core):
    with TestClient(create_app(core), base_url="http://testserver") as client:
        assert client.post("/api/auth/setup",
                           json={"name": "Sam", "password": PASSWORD}).status_code == 200
        yield client


@pytest.fixture
def token(owner, core):
    secret = core.auth.create_token("scout", ["read", "write"])[1]
    with TestClient(owner.app, base_url="http://testserver",
                    headers={"authorization": f"Bearer {secret}"}) as client:
        yield client


@pytest.fixture(autouse=True)
def vault_files_go_at_once(monkeypatch):
    """A Vault file stored a moment ago normally waits a minute before it is
    removed (see the tests of that below). These tests do not wait."""
    monkeypatch.setattr("jimmyhq.tape.store.ORPHAN_QUIET_MS", 0)


def held(cfg) -> bytes:
    """Every byte of every file in the data folder."""
    return b"".join(p.read_bytes() for p in sorted(cfg.data.rglob("*")) if p.is_file())


def spoken(core, title=TITLE, on_record=True):
    """A conversation with a title, an exchange, a regenerated reply and a
    message long enough to live in the Vault."""
    conv_id = chat.start_conversation(core, title=title, on_record=on_record)["id"]
    ask(core, conv_id, Q, reply=A)
    again(core, conv_id, "Behind the mulberry jam.")
    ask(core, conv_id, "loganberry " * 1000, reply="Medlar jelly, then.")
    return conv_id


def titles(core, **how):
    return [c["title"] for c in chat.list_conversations(core, **how)["conversations"]]


def archive_at(core, conv_id, days_ago, monkeypatch):
    """Archive a conversation as if it had been done ``days_ago`` days ago."""
    then = now_ms() - days_ago * DAY
    with monkeypatch.context() as patch:
        for module in ("jimmyhq.tape.registry", "jimmyhq.chat.offrecord"):
            patch.setattr(f"{module}.now_ms", lambda: then)
        chat.archive_conversation(core, conv_id)


def keep_for(core, days, grace_hours=24):
    core.settings.set("chat.delete_archived_after_days", days, actor="owner")
    core.settings.set("chat.delete_grace_hours", grace_hours, actor="owner")


def run_twice(core, later=25 * HOUR):
    """One run that marks, and one after the grace period that deletes."""
    assert chat.delete_expired(core, now=now_ms() + 1) == 0
    return chat.delete_expired(core, now=now_ms() + later)


DONE = {"evidence": {"quotes": 0, "cards": 0, "withdrawn": 0}, "finished": True,
        "settled": True}


# --- what goes ---------------------------------------------------------------------


def test_a_deleted_conversation_is_gone_from_every_list_search_and_read(core):
    conv_id = spoken(core)
    other = chat.start_conversation(core, title="Kept plans")["id"]
    chat.archive_conversation(core, conv_id)
    assert titles(core, archived=True) == [TITLE]
    done = chat.delete_conversation(core, conv_id)
    assert done["deleted"] is True and done["messages"] == 4
    assert titles(core) == ["Kept plans"] and titles(core, archived=True) == []
    assert titles(core, q="gooseberry") == [] == titles(core, archived=True, q="gooseberry")
    for read in (chat.get_conversation, chat.archive_conversation, chat.restore_conversation):
        with pytest.raises(chat.ChatError) as gone:
            read(core, conv_id)
        assert gone.value.status == 404
    assert chat.get_conversation(core, other)["conversation"]["title"] == "Kept plans"
    assert conv_id not in store.listed(core) and conv_id not in store.dropped(core)


def test_nothing_it_said_is_left_in_the_tape_the_vault_the_views_or_any_file(core, cfg):
    core.settings.set("chat.model_titles", True, actor="owner")
    conv_id = spoken(core)
    chat.change_conversation(core, conv_id, title="Mulberry matters")
    chat.archive_conversation(core, conv_id)
    chat.restore_conversation(core, conv_id)
    refs = [e.payload["text_ref"] for e in of_kind(core, "chat.message.sent")
            if e.payload["text_ref"]]
    assert refs and all(core.vault.has(ref) for ref in refs)
    core.tape.settle()
    assert b"ooseberry" in held(cfg)
    chat.delete_conversation(core, conv_id)
    everything = held(cfg)
    for word in WORDS:
        assert word.encode() not in everything, word
    assert not any(core.vault.has(ref) for ref in refs)
    dumped = json.dumps([e.model_dump() for e in core.tape.after(0, limit=100_000)])
    assert not any(word in dumped for word in WORDS)
    assert not any(word in json.dumps(store.listed(core)) for word in WORDS)
    # And after more is written and the log is folded in again.
    chat.start_conversation(core, title="Kept plans")
    core.tape.settle()
    assert b"ooseberry" not in held(cfg) and b"loganberry" not in held(cfg)


def test_the_tape_keeps_the_order_the_counts_and_two_small_events(core):
    conv_id = spoken(core)
    before = [(e.seq, e.id, e.ts, e.kind, e.actor, e.subject)
              for e in core.tape.after(0, limit=100_000)]
    receipts = [e.model_dump() for e in of_kind(core, "model.called", "model.failed")]
    chat.delete_conversation(core, conv_id)
    after = core.tape.after(0, limit=100_000)
    assert [(e.seq, e.id, e.ts, e.kind, e.actor, e.subject)
            for e in after[:len(before)]] == before
    erased, deleted = after[len(before):]
    assert deleted.payload == {"conversation_id": conv_id, "messages": 4, "settled": True}
    assert erased.kind == "tape.erased" and erased.payload == {
        "reason": "chat.conversation.deleted", "events": 6,
        "kinds": {"chat.conversation.started": 1, "chat.message.answered": 3,
                  "chat.message.sent": 2}}
    assert (deleted.kind, deleted.actor, deleted.subject) == (
        "chat.conversation.deleted", "owner", f"chat:{conv_id}")
    # Every chat event of it now reads back empty; the receipts are untouched,
    # so usage totals are what they were.
    assert all(e.erased and e.payload == {} for e in after[:len(before)]
               if e.subject == f"chat:{conv_id}" and e.kind.startswith("chat."))
    assert [e.model_dump() for e in of_kind(core, "model.called", "model.failed")] == receipts
    assert len(receipts) == 3


def test_a_receipt_that_kept_its_text_loses_it_and_one_that_did_not_stays(core, cfg):
    conv_id = spoken(core)
    root = of_kind(core, "chat.conversation.started")[0].id
    plain = of_kind(core, "model.called")[0]
    prompt, answer = core.vault.put_text("gooseberry prompt"), core.vault.put_text("fool")
    kept = core.tape.print("model.called", plain.payload | {
        "prompt_ref": prompt, "completion_ref": answer}, actor="owner", root=root)
    chat.delete_conversation(core, conv_id)
    assert core.tape.get(kept.id).erased is True
    assert core.tape.get(plain.id).payload == plain.payload
    assert not core.vault.has(prompt) and not core.vault.has(answer)
    assert b"gooseberry prompt" not in held(cfg)


def test_a_long_message_also_said_elsewhere_keeps_its_vault_file_for_the_other(core):
    conv_id = spoken(core)
    other = chat.start_conversation(core, title="Kept plans")["id"]
    ask(core, other, "loganberry " * 1000, reply="Noted.")
    chat.delete_conversation(core, conv_id)
    assert chat.get_conversation(core, other)["messages"][0]["text"] == "loganberry " * 1000


def test_the_part_off_the_record_goes_with_it(core, cfg):
    conv_id = spoken(core)
    flip(core, conv_id, False)
    ask(core, conv_id, "And the damson cheese?", reply="Next to the quince paste.")
    assert chat.delete_conversation(core, conv_id)["messages"] == 6
    with pytest.raises(chat.ChatError):
        chat.get_conversation(core, conv_id)
    everything = held(cfg)
    assert b"amson" not in everything and b"uince" not in everything


def test_one_that_was_never_on_the_record_is_deleted_and_the_tape_hears_nothing(core, cfg):
    answers(core, A)
    conv_id = spoken(core, on_record=False)
    end = [e.kind for e in core.tape.after(0, limit=100_000)]
    assert chat.delete_conversation(core, conv_id) == {
        "deleted": True, "messages": 4, "evidence": None, "finished": True, "settled": True}
    assert [e.kind for e in core.tape.after(0, limit=100_000)] == end
    assert conv_id.encode() not in held(cfg)
    with pytest.raises(chat.ChatError) as gone:
        chat.delete_conversation(core, conv_id)
    assert gone.value.status == 404  # nothing anywhere says it ever existed


def test_deleting_it_a_second_time_changes_nothing_and_says_so(core):
    conv_id = spoken(core)
    assert chat.delete_conversation(core, conv_id)["deleted"] is True
    end = core.tape.last_seq()
    assert chat.delete_conversation(core, conv_id) == {"deleted": False, "messages": 0} | DONE
    assert core.tape.last_seq() == end
    with pytest.raises(chat.ChatError) as unknown:
        chat.delete_conversation(core, "01JABCDEFGHJKMNPQRSTVWXYZ0")
    assert unknown.value.status == 404


def test_another_process_stops_showing_it_at_once(core, cfg):
    conv_id = spoken(core)
    second = open_core(cfg)
    try:
        assert titles(second) == [TITLE]
        chat.delete_conversation(core, conv_id)
        assert titles(second) == []
        with pytest.raises(chat.ChatError):
            chat.get_conversation(second, conv_id)
    finally:
        second.close()


# --- who may ask, and when ------------------------------------------------------------


def test_only_the_signed_in_owner_can_delete_a_conversation(owner, token, core):
    conv_id = spoken(core)
    r = token.delete(f"{BASE}/{conv_id}")
    assert r.status_code == 403 and "owner" in r.json()["detail"]
    for actor in ("token:scout", "agent:scout"):
        with pytest.raises(chat.ChatError) as refused:
            chat.delete_conversation(core, conv_id, actor=actor)
        assert refused.value.status == 403
    assert owner.get(f"{BASE}/{conv_id}").json()["conversation"]["title"] == TITLE
    r = owner.delete(f"{BASE}/{conv_id}")
    assert r.status_code == 200 and r.json()["deleted"] is True and r.json()["messages"] == 4
    assert owner.get(f"{BASE}/{conv_id}").status_code == 404
    assert owner.delete(f"{BASE}/{conv_id}").json()["deleted"] is False
    assert owner.delete(f"{BASE}/01JABCDEFGHJKMNPQRSTVWXYZ0").status_code == 404
    assert token.get(f"{BASE}/{conv_id}").status_code == 404
    assert token.get(BASE).json()["conversations"] == []


def test_a_conversation_cannot_be_deleted_while_a_reply_is_being_written(owner, core):
    home = SlowProvider()
    models(core, home=home)
    conv_id = owner.post(BASE, json={"title": TITLE}).json()["id"]
    result = {}

    def first_tab():
        result["events"] = events_of(owner.post(f"{BASE}/{conv_id}/messages", json={"text": Q}))

    tab = threading.Thread(target=first_tab)
    tab.start()
    try:
        assert home.streaming.wait(10)
        r = owner.delete(f"{BASE}/{conv_id}")
        assert r.status_code == 409 and "reply" in r.json()["detail"]
        assert "Stop it or wait" in r.json()["detail"]
    finally:
        home.release.set()
        tab.join(10)
    assert result["events"][-1][0] == "done"
    assert owner.delete(f"{BASE}/{conv_id}").json() == {"deleted": True, "messages": 2} | DONE


# --- memory ----------------------------------------------------------------------------


def test_a_fact_memory_saved_from_it_stays_and_the_quoted_words_go(core, cfg):
    memory.status(core)
    with_miner(core, from_owner(("tea", TEA), confidence=0.62))
    conv = talk(core, "I prefer tea to coffee, said over gooseberry tart.", title=TITLE)
    memory.mine(core, trigger="owner")
    (card,) = cards(core)
    decide(core, card["id"], action="approve")
    (cand,) = cands(core)
    assert "gooseberry" in cand["quotes"][0]["quote"] and cand["state"] == "saved"
    assert "gooseberry" in bench.get_card(core, card["id"])["body"]
    done = chat.delete_conversation(core, conv["id"])
    assert done["evidence"] == {"quotes": 1, "cards": 1, "withdrawn": 0}
    fact = memory.get_fact(core, cand["fact_id"])
    assert fact.text == TEA and fact.confirmed is True
    assert [f["id"] for f in memory.list_facts(core)["facts"]] == [cand["fact_id"]]
    (cand,) = cands(core)
    assert cand["quotes"] == [] and cand["state"] == "saved" and cand["statement"] == TEA
    # The fact still says where it came from, and that place now holds no words.
    (ref,) = fact.evidence
    assert core.tape.get(ref.removeprefix("tape:")).erased is True
    assert bench.get_card(core, card["id"]) is None
    assert b"ooseberry" not in held(cfg)


def test_a_proposal_still_waiting_on_the_bench_that_rested_only_on_it_is_withdrawn(core, cfg):
    memory.status(core)
    with_miner(core, from_owner(("tea", TEA), confidence=0.62))
    conv = talk(core, "I prefer tea to coffee, said over gooseberry tart.", title=TITLE)
    memory.mine(core, trigger="owner")
    assert len(cards(core)) == 1
    done = chat.delete_conversation(core, conv["id"])
    assert done["evidence"] == {"quotes": 1, "cards": 1, "withdrawn": 1}
    (cand,) = cands(core)
    assert (cand["state"], cand["quotes"], cand["reason"]) == ("withdrawn", [], mining.GONE)
    assert cards(core) == [] and memory.list_facts(core)["facts"] == []
    assert b"ooseberry" not in held(cfg)


# --- deleting archived conversations after a number of days ---------------------------


def test_archived_conversations_are_kept_for_ever_by_default(core, monkeypatch):
    conv_id = spoken(core)
    archive_at(core, conv_id, 4000, monkeypatch)
    assert core.settings.get("chat.delete_archived_after_days") == 0
    assert chat.delete_expired(core) == 0
    shown = chat.get_conversation(core, conv_id)["conversation"]
    assert shown["archived"] is True and shown["deletes_at"] is None
    described = {s["key"]: s for s in core.settings.describe()["settings"]}
    setting = described["chat.delete_archived_after_days"]
    assert (setting["min"], setting["max"]) == (0, 3650) and "for good" in setting["help"]


def test_the_job_deletes_only_what_has_been_archived_longer_than_the_setting(
        core, cfg, monkeypatch):
    old, recent, open_one = spoken(core), spoken(core, "Recent"), spoken(core, "Still open")
    quiet = spoken(core, "Quiet and old", on_record=False)
    archive_at(core, old, 31, monkeypatch)
    archive_at(core, quiet, 31, monkeypatch)
    archive_at(core, recent, 29, monkeypatch)
    keep_for(core, 30)
    assert run_twice(core) == 2
    assert titles(core, archived=True) == ["Recent"] and titles(core) == ["Still open"]
    deleted = of_kind(core, "chat.conversation.deleted")
    assert [(e.actor, e.payload["conversation_id"]) for e in deleted] == [("system", old)]
    assert [e.actor for e in of_kind(core, "tape.erased")] == ["system"]
    assert quiet.encode() not in held(cfg)  # the one off the record left no trace at all
    assert chat.delete_expired(core, now=now_ms() + 26 * HOUR) == 0 and open_one
    # The recent one becomes due two days on, is marked then, and goes a day after that.
    assert chat.delete_expired(core, now=now_ms() + 2 * DAY) == 0
    assert chat.delete_expired(core, now=now_ms() + 3 * DAY + HOUR) == 1
    assert titles(core, archived=True) == [] and titles(core) == ["Still open"]


def test_nothing_is_deleted_on_the_run_that_first_finds_it_due_however_old_it_is(
        core, monkeypatch):
    conv_id, quiet = spoken(core), spoken(core, "Quiet", on_record=False)
    for each in (conv_id, quiet):
        archive_at(core, each, 4000, monkeypatch)
    keep_for(core, 1)  # typed by mistake
    for _ in range(3):  # however often the job runs inside the grace period
        assert chat.delete_expired(core) == 0
    assert chat.delete_expired(core, now=now_ms() + 23 * HOUR) == 0
    marked = of_kind(core, "chat.conversation.delete_marked")
    assert [(e.actor, e.payload["conversation_id"]) for e in marked] == [("system", conv_id)]
    assert sorted(titles(core, archived=True)) == [TITLE, "Quiet"]
    # The archived list says when: a day from the mark, not "overdue".
    listed = chat.list_conversations(core, archived=True)["conversations"]
    assert all(now_ms() + 23 * HOUR < c["deletes_at"] <= now_ms() + 24 * HOUR for c in listed)
    assert chat.delete_expired(core, now=now_ms() + 24 * HOUR + 1) == 2


def test_changing_the_setting_takes_the_marks_away_and_the_grace_period_starts_again(
        core, monkeypatch):
    conv_id, quiet = spoken(core), spoken(core, "Quiet", on_record=False)
    for each in (conv_id, quiet):
        archive_at(core, each, 40, monkeypatch)
    keep_for(core, 1)
    assert chat.delete_expired(core) == 0  # both marked
    assert conv_id in store.marks(core)
    keep_for(core, 0)  # noticed the mistake
    assert chat.delete_expired(core, now=now_ms() + 30 * HOUR) == 0
    assert store.marks(core) == {}
    assert all(c["deletes_at"] is None
               for c in chat.list_conversations(core, archived=True)["conversations"])
    keep_for(core, 60)  # raised above their age: still not due
    assert chat.delete_expired(core, now=now_ms() + 60 * HOUR) == 0 and store.marks(core) == {}
    # Lowered again without the job having run in between: the old marks, had
    # any been left, would not count. The grace period starts from this run.
    keep_for(core, 30)
    core.tape.print("chat.conversation.delete_marked",
                    {"conversation_id": conv_id, "at": now_ms() - 30 * HOUR}, actor="system",
                    subject=store.subject(conv_id))
    keep_for(core, 30, grace_hours=23)
    assert chat.delete_expired(core, now=now_ms() + 1) == 0
    assert chat.delete_expired(core, now=now_ms() + 22 * HOUR) == 0
    assert sorted(titles(core, archived=True)) == [TITLE, "Quiet"]
    assert chat.delete_expired(core, now=now_ms() + 24 * HOUR) == 2


def test_a_clock_that_jumped_forward_and_came_back_deletes_nothing(core, monkeypatch):
    conv_id = spoken(core)
    chat.archive_conversation(core, conv_id)
    keep_for(core, 30)
    assert chat.delete_expired(core, now=now_ms() + 400 * DAY) == 0  # the clock is a year out
    assert store.marks(core)[conv_id] > now_ms() + 399 * DAY
    assert chat.delete_expired(core) == 0  # and right again: no longer due, so unmarked
    assert store.marks(core) == {}
    # Had it still been due, a mark dated in the future would not have counted either.
    archive_at(core, spoken(core, "Second"), 40, monkeypatch)
    second = [c["id"] for c in chat.list_conversations(core, archived=True)["conversations"]
              if c["title"] == "Second"][0]
    core.tape.print("chat.conversation.delete_marked",
                    {"conversation_id": second, "at": now_ms() + 400 * DAY}, actor="system",
                    subject=store.subject(second))
    assert chat.delete_expired(core) == 0
    assert store.marks(core)[second] <= now_ms()
    assert sorted(titles(core, archived=True)) == [TITLE, "Second"]


def test_restoring_a_conversation_stops_the_clock_and_archiving_again_restarts_it(
        core, monkeypatch):
    conv_id, quiet = spoken(core), spoken(core, "Quiet", on_record=False)
    for each in (conv_id, quiet):
        archive_at(core, each, 40, monkeypatch)
    keep_for(core, 30)
    assert chat.delete_expired(core) == 0 and conv_id in store.marks(core)  # marked
    for each in (conv_id, quiet):
        chat.restore_conversation(core, each)
    assert store.marks(core) == {}
    assert chat.delete_expired(core, now=now_ms() + 25 * HOUR) == 0
    for each in (conv_id, quiet):
        shown = chat.archive_conversation(core, each)
        # Thirty days from now, then the day of grace.
        assert shown["deletes_at"] == shown["archived_at"] + 30 * DAY + 24 * HOUR
    assert chat.delete_expired(core, now=now_ms() + 25 * HOUR) == 0
    assert chat.delete_expired(core, now=now_ms() + 31 * DAY) == 0  # marked on this run
    assert chat.delete_expired(core, now=now_ms() + 32 * DAY + HOUR) == 2


def test_one_restored_while_the_job_was_looking_is_left_alone(core, monkeypatch):
    conv_id = spoken(core)
    archive_at(core, conv_id, 40, monkeypatch)
    keep_for(core, 30)
    assert chat.delete_expired(core, now=now_ms() + 1) == 0
    real = deletion._receipts

    def restored_meanwhile(inner, events):
        chat_tape = inner.tape  # another process changes it at the last moment
        chat_tape.print("chat.conversation.mode", {"conversation_id": conv_id, "on_record": True},
                        actor="owner", subject=store.subject(conv_id))
        return real(inner, events)

    monkeypatch.setattr(deletion, "_receipts", restored_meanwhile)
    assert chat.delete_expired(core, now=now_ms() + 25 * HOUR) == 0
    assert chat.get_conversation(core, conv_id)["conversation"]["title"] == TITLE


def test_an_archived_conversation_the_list_has_let_go_is_still_deleted(core, monkeypatch):
    monkeypatch.setattr(store, "ARCHIVED_CAP", 1)
    first, second = spoken(core), spoken(core, "Second")
    archive_at(core, first, 40, monkeypatch)
    archive_at(core, second, 39, monkeypatch)
    assert titles(core, archived=True) == ["Second"] and first in store.dropped(core)
    keep_for(core, 30)
    assert run_twice(core) == 2
    assert store.dropped(core) == {} and titles(core, archived=True) == []
    assert store.marks(core) == {}


def test_the_worker_has_a_job_that_marks_them_and_later_deletes_them(core, monkeypatch):
    conv_id = spoken(core)
    archive_at(core, conv_id, 40, monkeypatch)
    keep_for(core, 30, grace_hours=1)
    scheduler = Scheduler(core.metrics)
    chat.jobs(core, scheduler)
    assert "chat.delete_archived" in scheduler.tick()
    assert titles(core, archived=True) == [TITLE] and conv_id in store.marks(core)
    then = now_ms() + 2 * HOUR
    monkeypatch.setattr(deletion, "now_ms", lambda: then)
    core.metrics.job_ran("chat.delete_archived", ok=True)
    chat.delete_expired(core)
    assert titles(core, archived=True) == []


# --- a deletion that was interrupted is finished --------------------------------------


def test_a_deletion_cut_short_after_the_erase_is_finished_by_asking_again(core, cfg, monkeypatch):
    conv_id = spoken(core)

    def stops(vault):
        raise OSError("the disk went away")

    with monkeypatch.context() as patch:
        patch.setattr(type(core.tape), "finish", lambda self, vault: stops(vault))
        patch.setattr(type(core.tape), "release_orphans", lambda self, vault, **how: stops(vault))
        first = chat.delete_conversation(core, conv_id)
    assert (first["deleted"], first["settled"]) == (True, False)
    assert core.tape.pending()["vault"] == 1 and b"loganberry" in held(cfg)
    again_ = chat.delete_conversation(core, conv_id)
    assert again_ == {"deleted": False, "messages": 0} | DONE
    assert b"loganberry" not in held(cfg) and core.tape.pending() == {"settle": False, "vault": 0}
    assert len(of_kind(core, "chat.conversation.deleted")) == 1


def test_a_process_that_died_right_after_the_erase_leaves_a_deletion_the_next_one_finishes(
        core, cfg, monkeypatch):
    conv_id = spoken(core)
    events = [e for e in core.tape.by_subject(store.subject(conv_id))
              if e.kind in ("chat.conversation.started", "chat.message.sent",
                            "chat.message.answered")]
    # All the process got done before it died: the erase itself, without even
    # emptying the log.
    with monkeypatch.context() as patch:
        patch.setattr(type(core.tape), "settle", lambda self: False)
        core.tape.erase([e.id for e in events], actor="owner",
                        reason="chat.conversation.deleted", subject=store.subject(conv_id))
    assert core.tape.pending() == {"settle": True, "vault": 1}
    assert of_kind(core, "chat.conversation.deleted") == [] and b"loganberry" in held(cfg)
    after = open_core(cfg)
    try:
        assert list(store.unfinished(after)) == [conv_id]
        # Asking again is not a 404: it finishes the job and says so.
        done = chat.delete_conversation(after, conv_id)
        assert (done["deleted"], done["finished"], done["settled"]) == (True, True, True)
        assert done["messages"] == 5  # one per message event, the replaced reply included
        (deleted,) = of_kind(after, "chat.conversation.deleted")
        assert deleted.payload == {"conversation_id": conv_id, "messages": 5, "settled": True}
        assert store.unfinished(after) == {} and after.tape.pending()["vault"] == 0
    finally:
        after.close()
    assert b"loganberry" not in held(cfg)


def test_the_hourly_job_finishes_an_interrupted_deletion_without_being_asked(core, cfg):
    conv_id = spoken(core)
    events = [e for e in core.tape.by_subject(store.subject(conv_id)) if e.kind != "tape.erased"]
    core.tape.erase([e.id for e in events if e.kind.startswith("chat.")], actor="owner",
                    reason="chat.conversation.deleted", subject=store.subject(conv_id))
    assert core.settings.get("chat.delete_archived_after_days") == 0
    assert chat.delete_expired(core) == 0  # nothing new deleted; the old one finished
    (deleted,) = of_kind(core, "chat.conversation.deleted")
    assert (deleted.actor, deleted.payload["conversation_id"]) == ("system", conv_id)
    assert b"loganberry" not in held(cfg) and store.unfinished(core) == {}


def test_memorys_clean_up_is_tried_again_until_it_succeeds(core, cfg, monkeypatch):
    memory.status(core)
    with_miner(core, from_owner(("tea", TEA), confidence=0.62))
    conv = talk(core, "I prefer tea to coffee, said over gooseberry tart.", title=TITLE)
    memory.mine(core, trigger="owner")
    (card,) = cards(core)

    def locked(*args, **how):
        raise RuntimeError("the Library is held")

    with monkeypatch.context() as patch:
        patch.setattr(memory, "evidence_erased", locked)
        first = chat.delete_conversation(core, conv["id"])
        assert (first["deleted"], first["finished"], first["evidence"]) == (True, False, None)
        # Not recorded as deleted while memory still quotes it, so it is not forgotten.
        assert of_kind(core, "chat.conversation.deleted") == []
        assert list(store.unfinished(core)) == [conv["id"]]
        assert chat.delete_expired(core) == 0  # the job tries, and memory is still held
        assert "gooseberry" in cands(core)[0]["quotes"][0]["quote"]
    assert chat.delete_expired(core) == 0  # the next hour it is not
    (cand,) = cands(core)
    assert (cand["state"], cand["quotes"]) == ("withdrawn", [])
    assert bench.get_card(core, card["id"]) is None and b"ooseberry" not in held(cfg)
    assert len(of_kind(core, "chat.conversation.deleted")) == 1 and store.unfinished(core) == {}


def test_asking_again_after_a_finished_deletion_still_clears_what_memory_quotes(core, cfg):
    memory.status(core)
    with_miner(core, from_owner(("tea", TEA), confidence=0.62))
    conv = talk(core, "I prefer tea to coffee, said over gooseberry tart.", title=TITLE)
    memory.mine(core, trigger="owner")
    (cand,) = cands(core)
    quotes = json.dumps(cand["quotes"])
    assert chat.delete_conversation(core, conv["id"])["finished"] is True
    # Something put the quote back (a restore of memory's store from a copy).
    with mining._mem(core).store.tx() as db:
        db.execute("UPDATE candidates SET quotes = ? WHERE id = ?", (quotes, cand["id"]))
    second = chat.delete_conversation(core, conv["id"])
    assert second["deleted"] is False and second["evidence"]["quotes"] == 1
    assert cands(core)[0]["quotes"] == [] and b"ooseberry" not in held(cfg)
    assert chat.delete_conversation(core, conv["id"])["evidence"]["quotes"] == 0


def test_a_conversation_deleted_while_the_miner_was_reading_it_leaves_no_quote_behind(
        core, cfg, monkeypatch):
    memory.status(core)
    with_miner(core, from_owner(("tea", TEA), confidence=0.62))
    conv = talk(core, "I prefer tea to coffee, said over gooseberry tart.", title=TITLE)
    real = mining._ask

    def deleted_meanwhile(inner, *args, **how):
        answer = real(inner, *args, **how)  # the model has the text and is answering
        assert chat.delete_conversation(core, conv["id"])["finished"] is True
        return answer

    monkeypatch.setattr(mining, "_ask", deleted_meanwhile)
    counts = memory.mine(core, source="chat", trigger="owner")["sources"]["chat"]
    assert counts["proposed"] == 1 and counts["dropped"]["no_evidence"] == 1
    assert cands(core) == [] and cards(core) == []
    assert b"ooseberry" not in held(cfg)


def test_a_proposal_resting_on_two_things_keeps_only_the_one_that_is_still_there(
        core, monkeypatch):
    memory.status(core)

    def both(items):
        return [fact(TEA, *[i["n"] for i in items if "tea" in i["text"].lower()], confidence=0.62)]

    with_miner(core, both)
    gone = talk(core, "I prefer tea, said over gooseberry tart.", title=TITLE)
    talk(core, "Tea again, please.", title="Kept")
    real = mining._ask

    def deleted_meanwhile(inner, *args, **how):
        answer = real(inner, *args, **how)
        chat.delete_conversation(core, gone["id"])
        return answer

    monkeypatch.setattr(mining, "_ask", deleted_meanwhile)
    memory.mine(core, source="chat", trigger="owner")
    (cand,) = cands(core)
    assert [q["quote"] for q in cand["quotes"]] == ["Tea again, please."]
    assert len(cand["evidence"]) == 1 and "ooseberry" not in json.dumps(cand)


# --- when the bytes are really gone ----------------------------------------------------


def test_the_answer_and_the_event_say_so_when_a_reader_kept_the_log_from_being_emptied(
        core, cfg, monkeypatch):
    conv_id = spoken(core)
    with monkeypatch.context() as patch:
        patch.setattr(type(core.tape), "settle", lambda self: False)  # a reader holds the log
        done = chat.delete_conversation(core, conv_id)
    assert (done["deleted"], done["finished"], done["settled"]) == (True, True, False)
    (deleted,) = of_kind(core, "chat.conversation.deleted")
    assert deleted.payload["settled"] is False
    after = open_core(cfg)  # the debt is in the database, not in the process that ran up
    try:
        assert after.tape.pending()["settle"] is True
        assert chat.delete_expired(after) == 0  # the hourly job pays it
        assert after.tape.pending() == {"settle": False, "vault": 0}
    finally:
        after.close()
    assert b"ooseberry" not in held(cfg)


def test_a_long_message_stored_a_moment_ago_waits_and_the_answer_says_so(core, cfg, monkeypatch):
    monkeypatch.setattr("jimmyhq.tape.store.ORPHAN_QUIET_MS", 60_000)
    conv_id = spoken(core)
    (ref,) = [e.payload["text_ref"] for e in of_kind(core, "chat.message.sent")
              if e.payload["text_ref"]]
    done = chat.delete_conversation(core, conv_id)
    assert (done["deleted"], done["finished"], done["settled"]) == (True, True, False)
    assert core.vault.has(ref) and core.tape.pending()["vault"] == 1
    path = next(p for p in (cfg.data / "vault").rglob("*") if p.is_file())
    os.utime(path, (time.time() - 120, time.time() - 120))  # two minutes pass
    assert chat.finish_pending(core) == {"settle": False, "vault": 0, "unfinished": 0}
    assert not core.vault.has(ref) and b"loganberry" not in held(cfg)


def test_the_same_long_text_stored_again_before_the_file_is_removed_keeps_its_file(
        core, cfg, monkeypatch):
    monkeypatch.setattr("jimmyhq.tape.store.ORPHAN_QUIET_MS", 60_000)
    long = "loganberry " * 1000
    conv_id = spoken(core)
    other = chat.start_conversation(core, title="Kept plans")["id"]
    path = next(p for p in (cfg.data / "vault").rglob("*") if p.is_file())
    os.utime(path, (time.time() - 120, time.time() - 120))
    real = type(core.tape).release_orphans

    def another_process_first(tape, vault, **how):
        # Between the erase and the removal, another process stores the same
        # text. Its ``put`` finds the file there; its event is not printed yet.
        ref = vault.put_text(long)
        left = real(tape, vault, **how)
        tape.print("chat.message.sent", {
            "conversation_id": other, "message_id": "01JABCDEFGHJKMNPQRSTVWXYZ9", "text": "",
            "text_ref": ref, "chars": len(long), "title": None,
        }, actor="owner", subject=store.subject(other))
        return left

    with monkeypatch.context() as patch:
        patch.setattr(type(core.tape), "release_orphans", another_process_first)
        assert chat.delete_conversation(core, conv_id)["settled"] is False
    assert chat.get_conversation(core, other)["messages"][0]["text"] == long
    os.utime(path, (time.time() - 120, time.time() - 120))
    # Now an event names it, so it is no orphan and is never removed.
    assert chat.finish_pending(core)["vault"] == 0
    assert chat.get_conversation(core, other)["messages"][0]["text"] == long


# --- web lookups made for it ---------------------------------------------------------

QUINCE = "https://example.com/quince-notes"
SEARCHED = "medlar pectin setting point"
LOOKED_UP = (b"quince-notes", b"pectin setting")


def looked_up(core, **how) -> str:
    """A conversation whose one reply searched the web and read a page."""
    web_setup(core, Scripted([search(SEARCHED)], [fetch(QUINCE)], ANSWER),
              {("example.com", "/quince-notes"): PAGE})
    conv_id = chat.start_conversation(core, **how)["id"]
    _, reply = web_reply(core, conv_id, text=f"What does {QUINCE} say?", web=True)
    assert reply["web"]["searches"] == 1 and reply["web"]["pages"] == 1
    return conv_id


def lookups(core):
    return of_kind(core, "lookup.searched", "lookup.fetched")


def test_what_a_deleted_conversation_searched_for_and_read_is_in_no_file(core, cfg):
    conv_id = looked_up(core)
    searched, fetched = lookups(core)
    assert searched.payload["query"] == SEARCHED and fetched.payload["url"] == QUINCE
    core.tape.settle()
    assert all(word in held(cfg) for word in LOOKED_UP)
    calls = [e.model_dump() for e in of_kind(core, "model.called", "model.failed")]
    done = chat.delete_conversation(core, conv_id)
    assert done["deleted"] is True and done["settled"] is True
    everything = held(cfg)
    for word in LOOKED_UP:
        assert word not in everything, word
    # The receipts keep their place and say nothing; the model's stay whole.
    assert [(e.seq, e.kind, e.erased, e.payload) for e in lookups(core)] == [
        (searched.seq, "lookup.searched", True, {}), (fetched.seq, "lookup.fetched", True, {})]
    assert [e.model_dump() for e in of_kind(core, "model.called", "model.failed")] == calls
    (erased,) = of_kind(core, "tape.erased")
    assert erased.payload["kinds"] == {
        "chat.conversation.started": 1, "chat.message.answered": 1, "chat.message.sent": 1,
        "lookup.fetched": 1, "lookup.searched": 1}
    assert store.unfinished(core) == {}


def test_deleting_one_conversation_leaves_another_ones_lookups_and_the_owners_own(core):
    gone = looked_up(core)
    looked_up(core)
    asyncio.run(lookup.search(core, "greengage chutney", actor="owner", caller="lookup.try"))
    chat.delete_conversation(core, gone)
    left = [e for e in lookups(core) if not e.erased]
    assert [e.payload.get("query") or e.payload.get("url") for e in left] == [
        SEARCHED, QUINCE, "greengage chutney"]
    (sent,) = [e for e in of_kind(core, "chat.message.sent") if not e.erased]
    assert {e.cause for e in left[:2]} == {sent.id}  # the kept conversation's own


def test_a_lookup_made_for_words_kept_off_the_record_never_named_them_and_stays(core, cfg):
    conv_id = looked_up(core, on_record=False)
    assert all(e.payload["private"] and e.cause is None and e.root is None
               for e in lookups(core))
    core.tape.settle()
    assert not any(word in held(cfg).replace(b"lookup.", b"") for word in LOOKED_UP[1:])
    chat.delete_conversation(core, conv_id)
    assert [e.erased for e in lookups(core)] == [False, False]


def test_asking_again_erases_lookups_left_by_a_deletion_made_before_they_went_too(
        core, cfg, monkeypatch):
    conv_id = looked_up(core)
    with monkeypatch.context() as earlier:
        earlier.setattr(deletion, "_receipts", lambda core, events: [])
        assert chat.delete_conversation(core, conv_id)["deleted"] is True
    assert [e.erased for e in lookups(core)] == [False, False]
    again_ = chat.delete_conversation(core, conv_id)
    assert again_["deleted"] is False and again_["finished"] is True
    assert [e.erased for e in lookups(core)] == [True, True]
    everything = held(cfg)
    assert not any(word in everything for word in LOOKED_UP)
    # The deletion is still recorded once, and is not reopened.
    assert len(of_kind(core, "chat.conversation.deleted")) == 1
    assert store.unfinished(core) == {}
    end = core.tape.last_seq()
    chat.delete_conversation(core, conv_id)
    assert core.tape.last_seq() == end
