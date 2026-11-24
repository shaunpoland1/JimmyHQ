"""Everything out as JSON and the same shape back in, safely and more than once."""

from __future__ import annotations

import json

from heard import backup, shapes
from heard_rig import DAY, START, conversation


def fill(rig):
    rig.send(conversation(id="a"))
    rig.send(conversation(id="b", started_at="2027-01-14T07:30:00+00:00",
                          structured={"title": "Second talk", "overview": "More."}))
    rig.send({"session_id": "s1", "segments": [{"text": "Live words", "start": 0, "end": 2}]})
    garden = next(c for c in rig.call("days")["conversations"] if c["heading"] == "Garden plan")
    rig.call("speaker.rename", id=garden["id"], **{"from": "Speaker 2", "to": "Sam"})
    rig.core.replies.append("A summary.")
    rig.summarise(garden["id"])
    rig.answer({"text": "Send Sam the notes", "quote": "I will send Sam the notes"})
    todo = rig.todos(garden["id"])["todos"][0]
    rig.call("todo.accept", id=todo["id"])


def everything(rig) -> list[dict]:
    parts = rig.call("export.parts")["parts"]
    out: list[dict] = []
    for part in parts:
        text = rig.call("export.part", index=part["index"])["text"]
        out.extend(json.loads(text)["conversations"])
    return out


def snapshot(rig) -> list:
    rows = []
    for c in rig.call("days")["conversations"]:
        got = rig.call("conversation", id=c["id"])
        rows.append((got["heading"], got["summary"], got["day"],
                     [(s["speaker"], s["text"], s["start_ms"]) for s in got["segments"]],
                     [(t["text"], t["status"]) for t in got["todos"]]))
    return sorted(rows)


def test_an_export_names_the_app_and_format_and_holds_every_conversation(rig):
    fill(rig)
    plan = rig.call("export.parts")
    assert plan["total"] == 3
    text = rig.call("export.part", index=0)["text"]
    got = json.loads(text)
    assert got["app"] == "heard" and got["format"] == 1 and got["kind"] == "everything"
    mine = shapes.namespace("Pendant")
    assert {c["key"] for c in got["conversations"]} == {
        f"omi:{mine}:a", f"omi:{mine}:b", f"live:{mine}:s:s1"}


def test_importing_what_was_exported_into_a_fresh_app_gives_the_same_conversations(
        rig, make_rig):
    fill(rig)
    pieces = everything(rig)
    fresh = make_rig()
    done = fresh.call("import.batch", conversations=pieces)
    assert done["conversations"] == 3 and done["skipped"] == [] and done["lines"] == 5
    assert snapshot(fresh) == snapshot(rig)
    assert fresh.call("search", query="notes")["results"]  # the index is rebuilt too


def test_importing_the_same_file_twice_adds_nothing(rig, make_rig):
    fill(rig)
    pieces = everything(rig)
    fresh = make_rig()
    fresh.call("import.batch", conversations=pieces)
    before = snapshot(fresh)
    again = fresh.call("import.batch", conversations=pieces)
    assert again["conversations"] == 0 and again["lines"] == 0
    assert snapshot(fresh) == before


def test_an_import_into_an_app_that_already_has_it_adds_only_what_is_missing(rig):
    fill(rig)
    pieces = everything(rig)
    extra = {**pieces[0], "segments": [*pieces[0]["segments"],
             {"text": "A line added later", "speaker": "Sam", "is_user": False,
              "offset_ms": 99_000, "start_ms": START, "end_ms": START}]}
    done = rig.call("import.batch", conversations=[extra])
    assert done["conversations"] == 0 and done["lines"] == 1


def test_a_conversation_too_long_for_one_piece_is_divided_and_put_back_together(
        rig, make_rig, monkeypatch):
    monkeypatch.setattr(backup, "PIECE_BYTES", 600)
    monkeypatch.setattr(backup, "PART_BYTES", 1500)
    rig.send({"session_id": "long", "segments": [
        {"text": f"Sentence number {n} about the garden.", "start": n, "end": n + 1}
        for n in range(40)]})
    assert len(rig.call("export.parts")["parts"]) > 1
    pieces = everything(rig)
    assert len(pieces) > 2 and len({p["key"] for p in pieces}) == 1
    fresh = make_rig()
    for chunk in range(0, len(pieces), 2):
        fresh.call("import.batch", conversations=pieces[chunk:chunk + 2])
    [row] = fresh.call("days")["conversations"]
    assert row["lines"] == 40


def test_every_part_is_a_whole_file_under_what_one_save_takes(rig, monkeypatch):
    monkeypatch.setattr(backup, "PART_BYTES", 3000)
    for i in range(10):
        rig.clock.now = START + i * DAY
        rig.send(conversation(id=f"c{i}", started_at=None, finished_at=None))
    sizes = [len(rig.call("export.part", index=p["index"])["text"].encode())
             for p in rig.call("export.parts")["parts"]]
    assert len(sizes) > 1 and max(sizes) < 3000 + 600


def test_a_part_that_does_not_exist_is_refused_with_what_to_do(rig):
    fill(rig)
    for index in (9, -1, "0", None, True):
        try:
            rig.call("export.part", index=index)
        except Exception as error:  # noqa: BLE001
            assert "Nothing was changed" in str(error)
        else:
            raise AssertionError(index)


def test_an_import_checks_what_comes_in_as_strictly_as_the_door_does(rig):
    good = {"key": "omi:z", "segments": [{"text": "Fine words", "start_ms": START}]}
    bad = [
        "text", {"segments": []}, {"key": "k", "segments": "no"},
        {"key": "k2", "segments": [{"text": "x", "start_ms": "soon"}]},
        {"key": "k3", "segments": [{"text": "", "start_ms": START}]},
    ]
    done = rig.call("import.batch", conversations=[*bad, good])
    assert done["conversations"] == 1 and done["skipped_count"] == 5
    assert all(msg.strip() for msg in done["skipped"])
    [row] = rig.call("days")["conversations"]
    assert row["lines"] == 1


def test_an_import_cleans_text_and_cuts_what_is_too_long(rig):
    long_line = "a" * 9000
    rig.call("import.batch", conversations=[{
        "key": "x", "title": "T\x00itle" + "t" * 500,
        "segments": [{"text": "Hi\x07 there", "start_ms": START},
                     {"text": long_line, "start_ms": START + 1}]}])
    got = rig.call("conversation", id=rig.first_id())
    assert got["title"].startswith("Title") and len(got["title"]) <= 200
    assert got["segments"][0]["text"] == "Hi there" and len(got["segments"][1]["text"]) == 5000


def test_a_call_with_too_many_conversations_or_the_wrong_shape_keeps_nothing(rig):
    for bad in ("x", None, [{"key": "k"}] * 101):
        try:
            rig.call("import.batch", conversations=bad)
        except Exception as error:  # noqa: BLE001
            assert "Nothing was kept" in str(error)
        else:
            raise AssertionError("no error")
    assert rig.call("days")["conversations"] == []
