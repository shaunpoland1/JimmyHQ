"""What the sleeping mind is shown, built from the village's event log."""

from __future__ import annotations

from jimmyhq.village.memory import gather
from jimmyhq.village.world import MemoryLog

ME, OTHER = "agent-a", "agent-b"
H = 3600.0
T0 = 1767600000.0  # 2026-01-05 08:00 UTC


def test_a_pass_that_failed_or_had_every_operation_rejected_did_not_sort_the_day():
    log = MemoryLog()
    good = log.append("consolidation", ME, 1, ops=2, results=["added #1", "rejected: x"])
    empty = log.append("consolidation", ME, 2, ops=0, results=[])
    failed = log.append("consolidation", ME, 3, failed=True, ops=0, results=[])
    refused = log.append("consolidation", ME, 4, ops=2,
                         results=["rejected: a", "rejected: b", "impression of owner rewritten"])
    assert [gather.succeeded(e) for e in (good, empty, failed, refused)] == [
        True, True, False, False]


def test_the_night_reaches_back_past_a_nap_to_the_last_night():
    log = MemoryLog()
    log.append("born", ME, T0)
    assert gather.since_last(log, ME) == T0
    log.append("consolidation", ME, T0 + 1 * H, ops=1, results=["added #1"])
    log.append("consolidation", ME, T0 + 8 * H, ops=1, results=["added #2"], nap=True)
    log.append("consolidation", ME, T0 + 9 * H, failed=True)
    assert gather.since_last(log, ME) == T0 + 8 * H
    assert gather.since_last(log, ME, nights_only=True) == T0 + 1 * H
    assert gather.napped(log, ME, T0 + 1 * H) and not gather.napped(log, ME, T0 + 8.5 * H)


def test_what_the_agent_asked_to_remember_can_be_rebuilt_from_the_log():
    log = MemoryLog()
    a = log.append("action", ME, T0, tool="read_file", args={"path": "a.md"}, ok=True)
    b = log.append("action", ME, T0 + 1, tool="write_file", args={"path": "b.md"}, ok=True)
    c = log.append("action", ME, T0 + 2, tool="remember",
                   args={"section": "skills", "text": "I can outline"}, ok=True)
    log.append("action", ME, T0 + 3, tool="remember", args={"section": "", "text": "x"}, ok=True)
    log.append("action", OTHER, T0 + 4, tool="remember",
               args={"section": "skills", "text": "not mine"}, ok=True)
    assert gather.candidates_from_log(log, ME, T0) == [
        {"ts": round(c.ts * 1000), "section": "skills", "text": "I can outline",
         "evidence": [a.id, b.id, c.id]}]


def test_a_long_day_loses_thoughts_then_feelings_then_old_actions_but_never_chat():
    lines = [f"line {i:02d} " + "x" * 90 for i in range(40)]
    thoughts, feelings, chats = set(range(0, 10)), set(range(10, 16)), {16, 17, 39}
    assert gather.cut_for_length(lines, thoughts, feelings, chats, 100_000) == lines
    some = gather.cut_for_length(lines, thoughts, feelings, chats, 3600)
    assert some[0].startswith("[cut for length: 6 of the day's thoughts and 0 other lines")
    assert some[1:] == lines[6:]
    hard = gather.cut_for_length(lines, thoughts, feelings, chats, 1000)
    assert "10 of the day's thoughts and 22 other lines" in hard[0]
    assert all(lines[i] in hard for i in chats)
    assert hard[1:] == [lines[16], lines[17]] + lines[34:]


def test_the_record_names_each_event_and_shows_the_owners_words_whole():
    log = MemoryLog()
    news = "You will lead the newsletter. " * 40
    said = log.append("chat", ME, T0 + 60, sender="owner", text=news)
    log.append("chat", ME, T0 + 120, sender="agent", text="y" * 900)
    log.append("action", ME, T0 + 180, tool="write_file", args={"path": "a.md"}, ok=False,
               result="no such folder")
    log.append("thought", ME, T0 + 240, text="hmm")
    log.append("feeling", ME, T0 + 300, phrase="steady")
    room = log.append("breakroom", OTHER, T0 + 360, text="Morning all")
    log.append("action", OTHER, T0 + 420, tool="write_file", ok=True)
    text, days = gather.record(log, ME, T0, T0 + H, tz="UTC", owner="Sam")
    assert days == ["2026-01-05"]
    rows = text.split("\n")
    assert rows[0] == f"{room.id} 08:06 break room, agent-b: Morning all"
    assert rows[1] == f"{said.id} 08:01 chat from Sam: {news[:1500]}"
    assert rows[2].endswith("chat from me: " + "y" * 600)
    assert 'action write_file {"path": "a.md"} -> FAILED: no such folder' in rows[3]
    assert rows[4].endswith("thought: hmm") and rows[5].endswith("felt steady")
    assert len(rows) == 6


def test_a_record_over_several_days_dates_each_line_and_reads_every_days_diary():
    log = MemoryLog()
    log.append("sleep", ME, T0 + 60)
    text, days = gather.record(log, ME, T0, T0 + 50 * H, tz="UTC")
    assert days == ["2026-01-05", "2026-01-06", "2026-01-07"]
    assert text == "evt-1 05 Jan 08:01 sleep"
    assert len(gather.days_between(T0 - 90 * 86400, T0, "UTC")) == gather.DAYS_MOST


def test_a_day_too_long_even_without_thoughts_keeps_the_owners_words():
    log = MemoryLog()
    said = log.append("chat", ME, T0, sender="owner", text="The plan changed this morning.")
    for i in range(300):
        log.append("thought", ME, T0 + 1 + i, text="t" * 200)
        log.append("action", ME, T0 + 1 + i, tool="read_file", args={"path": "p" * 80},
                   ok=True, result="r" * 90)
    text, _ = gather.record(log, ME, T0, T0 + H, tz="UTC", limit=20_000)
    assert len(text) <= 20_000
    assert f"{said.id} 08:00 chat from the owner: The plan changed this morning." in text
    assert "thought:" not in text


def test_what_the_owner_said_earlier_and_no_line_records_is_carried_forward():
    log = MemoryLog()
    day = 86400.0
    too_old = log.append("chat", ME, T0 - 4 * day, sender="owner", text="ancient")
    cited = log.append("chat", ME, T0 - 2 * day, sender="owner", text="cited")
    heard = log.append("chat", ME, T0 - 2 * day + 1, sender="owner", text="thanks")
    lost = log.append("chat", ME, T0 - day, sender="owner", text="You are the first of five.")
    log.append("chat", ME, T0 - day + 5, sender="agent", text="mine")
    log.append("chat", OTHER, T0 - day, sender="owner", text="for someone else")
    log.append("chat", ME, T0 + 5, sender="owner", text="today's, in the record already")
    items = [{"id": 1, "evidence": [cited.id]}]
    got = gather.unrecorded_from_owner(log, ME, items, {heard.id}, T0, 3)
    assert [e.id for e in got] == [lost.id] and too_old.id not in [e.id for e in got]
    assert gather.unrecorded_from_owner(log, ME, items, set(), T0, 0) == []


def test_only_those_really_met_since_the_last_sleep_count_as_met():
    log = MemoryLog()
    log.append("breakroom", OTHER, T0 - 10, text="before")
    log.append("chat", ME, T0 - 5, sender="owner", text="before")
    assert gather.met_since(log, ME, T0) == set()
    log.append("breakroom", OTHER, T0 + 1, text="hello")
    log.append("breakroom", ME, T0 + 2, text="hello back")
    log.append("chat", ME, T0 + 3, sender="agent", text="to the owner")
    assert gather.met_since(log, ME, T0) == {OTHER}
    log.append("chat", ME, T0 + 4, sender="owner", text="hi")
    assert gather.met_since(log, ME, T0) == {OTHER, "owner"}
