"""Reading, renaming, searching and deleting conversations, and the retention."""

from __future__ import annotations

from heard_rig import CHAT, DAY, HOUR, START, conversation


def make_days(rig, n=3):
    for i in range(n):
        rig.clock.now = START + i * DAY
        rig.send(conversation(id=f"c-{i}", started_at=None, finished_at=None,
                              structured={"title": f"Talk {i}"}))
    rig.clock.now = START + n * DAY


def test_conversations_come_newest_first_with_the_day_they_started_on_in_the_owners_zone(
        make_rig):
    rig = make_rig(timezone="Pacific/Auckland")
    rig.clock.now = START + 16 * HOUR  # after it began
    rig.send(conversation(started_at="2027-01-15T23:30:00+00:00"))
    [row] = rig.call("days")["conversations"]
    assert row["day"] == "2027-01-16"  # already tomorrow in Auckland


def test_the_list_pages_by_a_cursor_and_says_when_there_is_no_more(make_rig):
    rig = make_rig(settings={"page_size": 10})
    for i in range(25):
        rig.clock.now = START + i * HOUR
        rig.send(conversation(id=f"c-{i}", started_at=None, finished_at=None,
                              structured={"title": f"Talk {i}"}))
    first = rig.call("days")
    assert len(first["conversations"]) == 10 and first["next"]
    assert first["conversations"][0]["heading"] == "Talk 24"
    second = rig.call("days", before=first["next"])
    third = rig.call("days", before=second["next"])
    assert [len(p["conversations"]) for p in (second, third)] == [10, 5]
    assert third["next"] is None
    seen = [c["id"] for p in (first, second, third) for c in p["conversations"]]
    assert len(set(seen)) == 25


def test_a_conversation_with_no_title_is_headed_by_its_first_words(rig):
    rig.send({"text": "Could you pass the salt and also the very good pepper over here "
                      "please, thank you so much"})
    heading = rig.call("days")["conversations"][0]["heading"]
    assert heading.startswith("Could you pass the salt") and heading.endswith("…")
    assert len(heading) <= 72


def test_the_list_says_how_long_how_many_words_and_who_spoke(rig):
    rig.send(conversation())
    [row] = rig.call("days")["conversations"]
    assert (row["seconds"], row["words"], row["has_summary"], row["open"]) == (60, 19, False, False)


def test_a_speaker_is_renamed_on_every_line_of_that_conversation_only(rig):
    rig.send(conversation(id="a"))
    rig.send(conversation(id="b", started_at="2027-01-14T07:30:00+00:00"))
    ids = [c["id"] for c in rig.call("days")["conversations"]]
    done = rig.call("speaker.rename", id=ids[0], **{"from": "Speaker 2", "to": "  Sam  "})
    assert done["changed"] == 1 and "Sam" in done["said"]

    def names(i):
        return [s["name"] for s in rig.call("conversation", id=i)["speaker_list"]]

    assert names(ids[0]) == ["You", "Sam"] and names(ids[1]) == ["You", "Speaker 2"]


def test_renaming_to_a_name_already_there_joins_the_two_speakers(rig):
    rig.send(conversation())
    cid = rig.first_id()
    rig.call("speaker.rename", id=cid, **{"from": "Speaker 2", "to": "You"})
    assert [s["name"] for s in rig.call("conversation", id=cid)["speaker_list"]] == ["You"]


def test_a_rename_with_no_name_or_an_unknown_speaker_changes_nothing_and_says_what_to_do(rig):
    rig.send(conversation())
    cid = rig.first_id()
    for args in ({"from": "Speaker 2", "to": "  "}, {"from": "Nobody", "to": "Sam"},
                 {"from": "", "to": "Sam"}):
        try:
            rig.call("speaker.rename", id=cid, **args)
        except Exception as error:  # noqa: BLE001
            assert "Nothing was changed" in str(error) or "nothing was changed" in str(error)
        else:
            raise AssertionError(args)
    assert [s["name"] for s in rig.call("conversation", id=cid)["speaker_list"]] == [
        "You", "Speaker 2"]


def test_deleting_a_conversation_removes_its_lines_todos_and_search_hits(rig):
    rig.send(conversation())
    rig.send(conversation(id="other", started_at="2027-01-14T07:30:00+00:00",
                          transcript_segments=[{"text": "Something else entirely"}]))
    cid = [c for c in rig.call("days")["conversations"] if c["heading"] == "Garden plan"][0]["id"]
    assert rig.call("search", query="garden")["results"]
    rig.call("conversation.delete", id=cid)
    assert rig.call("search", query="garden")["results"] == []
    assert len(rig.call("days")["conversations"]) == 1
    store = rig.ctx.store
    assert store.one("SELECT COUNT(*) AS n FROM segment WHERE conv_id=?", (cid,))["n"] == 0
    assert store.one("SELECT COUNT(*) AS n FROM todo WHERE conv_id=?", (cid,))["n"] == 0


def test_a_deleted_conversations_words_are_gone_from_the_file_on_disk_too(rig):
    word = "quincunx"
    rig.send(conversation(id="x", transcript_segments=[
        {"text": f"The {word} is private and must not linger anywhere."}]))
    rig.send(conversation(id="y", started_at="2027-01-14T07:30:00+00:00",
                          transcript_segments=[{"text": "A different keepsake remains."}]))
    cid = rig.call("search", query=word)["results"][0]["conv_id"]
    rig.call("conversation.delete", id=cid)
    rig.ctx.store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    on_disk = b"".join(p.read_bytes() for p in rig.data.glob("heard.db*"))
    assert word.encode() not in on_disk
    assert b"keepsake" in on_disk  # the other conversation is still there


def test_deleting_an_unknown_conversation_says_so_and_changes_nothing(rig):
    rig.send(conversation())
    try:
        rig.call("conversation.delete", id=999)
    except Exception as error:  # noqa: BLE001
        assert "gone" in str(error)
    else:
        raise AssertionError("no error")
    assert len(rig.call("days")["conversations"]) == 1


def test_search_finds_every_word_in_any_order_and_the_start_of_the_last_word(rig):
    rig.send(conversation())
    for query in ("garden plan", "plan garden", "gard", "NOTES tomorrow", "send sam"):
        assert rig.call("search", query=query)["results"], query
    assert rig.call("search", query="garden spaceship")["results"] == []
    assert rig.call("search", query="   ")["results"] == []


def test_search_marks_the_words_that_matched_and_names_the_conversation_and_day(rig):
    rig.send(conversation())
    hit = rig.call("search", query="notes")["results"][0]
    assert hit["heading"] == "Garden plan" and hit["day"] == "2027-01-15"
    marked = [text for text, on in hit["parts"] if on]
    assert marked == ["notes"]
    assert "".join(text for text, _ in hit["parts"]).replace("…", "") in PLAN_LINE


PLAN_LINE = CHAT[1]["text"]


def test_search_takes_punctuation_and_query_syntax_as_plain_words(rig):
    rig.send(conversation())
    for query in ('"', "garden AND", "garden*", "NEAR(", "garden OR (", "a:b", "-garden", "'; --"):
        rig.call("search", query=query)  # none of these may raise


def test_search_without_the_fast_index_still_finds_lines(rig):
    rig.send(conversation())
    rig.ctx.store.has_fts = False
    hits = rig.call("search", query="garden plan")["results"]
    assert len(hits) == 2 and any(on for _, on in hits[0]["parts"])
    assert rig.call("search", query="100%")["results"] == []


def test_search_stops_at_the_owners_limit_and_says_it_was_full(make_rig):
    rig = make_rig(settings={"search_limit": 5})
    rig.send({"session_id": "s", "segments": [{"text": f"garden line {n}", "start": n,
                                               "end": n + 1} for n in range(20)]})
    found = rig.call("search", query="garden")
    assert len(found["results"]) == 5 and found["full"] is True


def test_nothing_is_deleted_by_retention_when_it_is_set_to_zero(rig):
    make_days(rig)
    assert rig.core.run_job("retention") == "Keeping everything."
    assert len(rig.call("days")["conversations"]) == 3


def test_retention_deletes_conversations_that_ended_longer_ago_than_the_days_set(make_rig):
    rig = make_rig(settings={"keep_days": 2})
    make_days(rig, 4)  # ended roughly 4, 3, 2, 1 days before now
    rig.clock.now = START + 4 * DAY - HOUR
    said = rig.core.run_job("retention")
    heads = sorted(c["heading"] for c in rig.call("days")["conversations"])
    assert heads == ["Talk 2", "Talk 3"] and "Deleted 2 conversations" in said


def test_retention_is_run_by_a_daily_job_the_app_declares(rig):
    assert rig.app.plugs()["jobs"] == [{"name": "retention", "every": 86_400}]


def test_delete_everything_needs_the_word_and_then_leaves_nothing(rig):
    rig.send(conversation())
    try:
        rig.call("delete.all", typed="delete")
    except Exception as error:  # noqa: BLE001
        assert "Nothing was deleted" in str(error)
    else:
        raise AssertionError("no error")
    assert rig.call("delete.all", typed="DELETE")["deleted"] == 1
    assert rig.call("days")["conversations"] == []
    assert rig.call("overview")["connected"] is False


def test_a_conversation_saves_as_markdown_with_its_speakers_and_summary(rig):
    rig.send(conversation())
    out = rig.call("conversation.text", id=rig.first_id())
    assert out["name"].startswith("heard-2027-01-15-")
    assert out["text"].startswith("# Garden plan")
    assert "**You:** I will send Sam" in out["text"]
