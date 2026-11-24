"""The village's cards and the owner's decisions: what is trusted, and what it does."""

from __future__ import annotations

from types import SimpleNamespace

from jimmyhq import bench
from jimmyhq.bench import cards as bench_cards
from jimmyhq.village.comms import benchlink
from jimmyhq.village.rules import RULES
from test_village_tools_fakes import call, decide, v  # noqa: F401

WHAT = "The donate button on https://example.com/give opens a page that says not found."


def finding(v, title="Donate button leads to a missing page", agent="ada"):  # noqa: F811
    before = {c["card_id"] for c in benchlink.cards(v, agent)}
    call(v, agent, "report_finding", {"title": title, "what": WHAT, "why": "People give up."})
    # The new one, whatever its id: two cards filed in one millisecond sort by chance.
    [new] = [c["card_id"] for c in benchlink.cards(v, agent) if c["card_id"] not in before]
    return new


def test_the_tables_are_made_once_and_survive_a_second_opening(v):  # noqa: F811
    cid = finding(v)
    benchlink.close(v)
    conn, _ = benchlink.db(v)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"cards", "task_refs", "events"} <= names
    columns = [r[1] for r in conn.execute("PRAGMA table_info(cards)")]
    assert columns[:11] == ["card_id", "agent", "kind", "title", "first_title", "paths",
                            "status", "note", "urgent", "filed_ts", "decided_ts"]
    assert [r[1] for r in conn.execute("PRAGMA table_info(task_refs)")] == [
        "agent", "ref", "task_id"]
    assert benchlink.get(v, cid)["agent"] == "ada"


def test_a_decision_is_applied_once_however_often_the_event_is_read(v):  # noqa: F811
    cid = finding(v)
    result, event = decide(v, cid, "approve")
    assert result["decision"] == "approved"
    assert benchlink.apply_decision(v, event) is None
    assert len(v.log.query(agent="ada", kinds=["bench_decided"])) == 1
    assert v.woken == ["ada"]


def test_a_decision_on_a_card_the_village_did_not_file_is_ignored(v):  # noqa: F811
    other = bench.file_card(v.core, filed_by="token:outsider", kind="village.finding",
                            source="Elsewhere", title="Not from the village")
    bench_cards.decide(v.core, other, action="approve")
    event = v.core.tape.latest(limit=1, kind="bench.decided")[0]
    assert benchlink.apply_decision(v, event) is None
    assert v.log.query(kinds=["bench_decided"]) == [] and v.woken == []


def test_a_decision_is_trusted_only_when_filed_by_matches_the_cards_row(v):  # noqa: F811
    cid = finding(v)
    row = benchlink.get(v, cid)
    good = {"card_id": cid, "card_kind": "village.finding", "action": "approve",
            "filed_by": "agent:ada"}
    assert benchlink.trusted(row, good, "owner")
    assert not benchlink.trusted(row, {**good, "filed_by": "agent:bo"}, "owner")
    assert not benchlink.trusted(row, {**good, "filed_by": None}, "owner")
    assert not benchlink.trusted(row, {**good, "card_kind": "village.work"}, "owner")
    assert not benchlink.trusted(row, good, "agent:ada")
    assert not benchlink.trusted(None, good, "owner")
    forged = SimpleNamespace(kind="bench.decided", id="x", ts=0, actor="owner",
                             payload={**good, "filed_by": "agent:bo"})
    assert benchlink.apply_decision(v, forged) is None
    assert benchlink.get(v, cid)["status"] == "open"
    assert benchlink.apply_decision(v, SimpleNamespace(kind="task.filed", payload={})) is None


def test_every_decision_is_logged_wakes_the_agent_and_only_approval_adds_purpose(v):  # noqa: F811
    first, second, third = (finding(v, t) for t in ("Donate button leads to a missing page",
                                                    "Footer shows the wrong year entirely",
                                                    "Volunteer form rejects valid postcodes"))
    a = v.world.agents["ada"]
    purpose = a.needs["purpose"]
    decide(v, first, "not_yet", note="Tell me more")
    decide(v, second, "no", reason="Not a problem")
    assert a.needs["purpose"] == purpose
    decide(v, third, "approve")
    assert a.needs["purpose"] > purpose
    logged = [(e.data["item"], e.data["decision"], e.data["note"])
              for e in v.log.query(agent="ada", kinds=["bench_decided"])]
    assert logged == [(first, "not_yet", "Tell me more"), (second, "denied", "Not a problem"),
                      (third, "approved", "")]
    assert v.woken == ["ada", "ada", "ada"]
    assert [benchlink.get(v, c)["status"] for c in (first, second, third)] == [
        "returned", "declined", "approved"]
    assert benchlink.tally(v, "ada") == {"village.finding": {"returned": 1, "declined": 1,
                                                             "approved": 1}}


def test_a_decision_for_an_agent_who_has_gone_is_still_recorded(v):  # noqa: F811
    cid = finding(v)
    del v.world.agents["ada"]
    result, _ = decide(v, cid, "approve")
    assert result["decision"] == "approved" and benchlink.get(v, cid)["status"] == "approved"


def test_a_card_id_is_read_through_spaces_brackets_and_case(v):  # noqa: F811
    cid = finding(v)
    assert benchlink.card_id_of(f" [{cid.lower()}]. ") == cid
    assert benchlink.get(v, f"[{cid.lower()}]")["card_id"] == cid
    assert benchlink.looks_like_card(v, cid) and not benchlink.looks_like_card(v, "evt-12")


# --- waiting on the owner -----------------------------------------------------------------


def causes(v, agent="ada", need=None):  # noqa: F811
    return [(e.data["need"], e.data["cause"], e.data["delta"])
            for e in v.log.query(agent=agent, kinds=["need_change"])
            if need is None or e.data["need"] == need]


def wait(v, minutes):  # noqa: F811
    """Time passes for the bodies, a tick at a time."""
    for _ in range(int(minutes // 5)):
        v.clock.advance(5)
        v.world.advance(5)


def from_waiting(v, agent="ada"):  # noqa: F811
    return sum(d for _, cause, d in causes(v, agent, "frustration")
               if cause.startswith("waiting_on:"))


def test_a_card_that_needs_the_owner_starts_one_wait_and_gives_purpose_once(v):  # noqa: F811
    assert RULES["bench_waits"].fn is benchlink.waited_on
    a = v.world.agents["ada"]
    cid = finding(v)
    [filed] = v.log.query(agent="ada", kinds=["bench_submitted"])
    assert (filed.data["item"], filed.data["item_kind"]) == (cid, "village.finding")
    assert a.waiting[cid]["evt"] == filed.id and a.waiting[cid]["caused"] == 0.0
    assert a.waiting[cid]["title"] == "Donate button leads to a missing page"
    gained = [(cause, d) for _, cause, d in causes(v, need="purpose")]
    assert gained == [("submitted_to_bench", v.world.S("purpose.submission"))]
    assert benchlink.waited_on("village.work") and benchlink.waited_on("village.priority")
    assert not benchlink.waited_on("village.done", "done")
    assert not benchlink.waited_on("village.work", "done")


def test_a_done_card_informs_and_is_not_waited_on(v):  # noqa: F811
    a = v.world.agents["ada"]
    purpose = a.needs["purpose"]
    benchlink.file(v, "ada", benchlink.DONE, "Done: tidy the notes", lane="done")
    assert a.waiting == {} and a.needs["purpose"] == purpose
    assert len(v.log.query(agent="ada", kinds=["bench_submitted"])) == 1
    wait(v, 60)
    assert from_waiting(v) == 0


def test_waiting_costs_a_little_each_hour_up_to_the_cap_for_one_card(v):  # noqa: F811
    cid = finding(v)
    wait(v, 60)
    assert round(from_waiting(v), 3) == v.world.S("frustration.wait_per_hour")
    v.core.settings.set("village.frustration.wait_per_hour", 20.0, actor="owner")
    wait(v, 60)
    cap = v.world.S("frustration.wait_cap_per_item")
    assert round(from_waiting(v), 3) == cap
    assert round(v.world.agents["ada"].waiting[cid]["caused"], 3) == cap
    wait(v, 60)
    assert round(from_waiting(v), 3) == cap  # however long the owner takes


def test_everything_an_agent_waits_on_together_stays_under_the_total_cap(v):  # noqa: F811
    v.core.settings.set("village.frustration.wait_per_hour", 20.0, actor="owner")
    for title in ("Donate button leads to a missing page", "Footer shows the wrong year entirely",
                  "Volunteer form rejects valid postcodes"):
        finding(v, title)
    wait(v, 120)
    total = v.world.S("frustration.wait_cap_total")
    each = v.world.S("frustration.wait_cap_per_item")
    assert 3 * each > total  # the three caps alone would allow more
    assert round(from_waiting(v), 3) == total
    assert all(w["caused"] <= each + 1e-9 for w in v.world.agents["ada"].waiting.values())


def test_the_owners_decision_ends_the_wait_and_approval_adds_purpose_once(v):  # noqa: F811
    a = v.world.agents["ada"]
    first, second = finding(v), finding(v, "Footer shows the wrong year entirely")
    decide(v, first, "not_yet", note="Tell me more")
    assert list(a.waiting) == [second]
    result, event = decide(v, second, "approve")
    assert a.waiting == {}
    said = [(e.data["item"], e.data["verdict"])
            for e in v.log.query(agent="ada", kinds=["bench_response"])]
    assert said == [(first, "not_yet"), (second, "approved")]
    gains = [cause for _, cause, _ in causes(v, need="purpose")]
    assert gains == ["submitted_to_bench", "submitted_to_bench", "acknowledged"]
    assert benchlink.apply_decision(v, event) is None  # read again: nothing more
    assert len(v.log.query(agent="ada", kinds=["bench_response"])) == 2
    assert [cause for _, cause, _ in causes(v, need="purpose")] == gains
    wait(v, 60)
    assert from_waiting(v) == 0


def test_a_card_sent_back_and_revised_is_waited_on_again_without_new_purpose(v):  # noqa: F811
    a = v.world.agents["ada"]
    cid = finding(v)
    decide(v, cid, "not_yet", note="Which page?")
    assert a.waiting == {}
    purpose = a.needs["purpose"]
    benchlink.revise(v, benchlink.get(v, cid), body="It is the page at https://example.com/give.")
    assert list(a.waiting) == [cid] and a.needs["purpose"] == purpose
    kept = dict(a.waiting[cid], caused=2.5)
    a.waiting[cid] = dict(kept)
    benchlink.revise(v, benchlink.get(v, cid), body="The same, said better.")
    assert a.waiting[cid] == kept  # revising a card still open changes nothing about the wait


def test_a_card_that_replaces_another_takes_over_its_wait(v):  # noqa: F811
    a = v.world.agents["ada"]
    old = finding(v)
    a.waiting[old]["caused"] = 3.0
    purpose = a.needs["purpose"]
    new = benchlink.file(v, "ada", benchlink.FINDING, "Donate button, checked again",
                         body=WHAT, replaces=old)["card_id"]
    benchlink.replace(v, benchlink.get(v, old), "Replaced by a revised version.")
    assert list(a.waiting) == [new] and a.waiting[new]["caused"] == 3.0
    assert a.needs["purpose"] == purpose
    assert len(v.log.query(agent="ada", kinds=["bench_submitted"])) == 1


def test_what_an_agent_waits_on_is_rebuilt_from_the_cards_table(v):  # noqa: F811
    assert RULES["waiting_rebuilt"].fn is benchlink.rebuild_waiting
    a = v.world.agents["ada"]
    kept, lost, answered = (finding(v, t) for t in (
        "Donate button leads to a missing page", "Footer shows the wrong year entirely",
        "Volunteer form rejects valid postcodes"))
    benchlink.file(v, "ada", benchlink.DONE, "Done: tidy the notes", lane="done")
    a.waiting[kept]["caused"] = 4.0
    decide(v, answered, "approve")
    # A body saved before the last card was filed and before the answer came.
    del a.waiting[lost]
    a.waiting[answered] = {"evt": "evt-1", "since": 0.0, "caused": 1.0}
    assert benchlink.rebuild_waiting(v, "ada") == [lost]
    assert set(a.waiting) == {kept, lost} and a.waiting[kept]["caused"] == 4.0
    filed = {e.data["item"]: e.id for e in v.log.query(agent="ada", kinds=["bench_submitted"])}
    assert a.waiting[lost]["evt"] == filed[lost] and a.waiting[lost]["caused"] == 0.0
    assert a.waiting[lost]["since"] == benchlink.get(v, lost)["filed_ts"] / 1000
    assert benchlink.rebuild_waiting(v, "ada") == []  # nothing to do the second time
    assert benchlink.rebuild_waiting(v, "nobody") == []
    wait(v, 60)
    assert from_waiting(v) > 0
