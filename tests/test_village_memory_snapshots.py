"""Snapshots: plain copies of memory that make a bad night undoable. Also the diary and
impressions, which a snapshot covers."""

from __future__ import annotations

import json

import pytest

from jimmyhq.village.memory import diary, impressions, snapshots

T0 = 1767600000.0  # 2026-01-05 08:00 UTC


def seed(home, memory="one", impression="kind", entry="first"):
    (home / "memory").mkdir(parents=True, exist_ok=True)
    (home / "memory" / "memory.json").write_text(json.dumps({"v": memory}), encoding="utf-8")
    impressions.write(home, "owner", impression)
    diary.append(home, T0, entry, tz="UTC")


def read(home):
    return json.loads((home / "memory" / "memory.json").read_text(encoding="utf-8"))["v"]


def test_a_snapshot_is_a_plain_copy_with_a_manifest(tmp_path):
    seed(tmp_path)
    seq = snapshots.take(tmp_path, 1767600000000, "night", day="2026-01-05", by="smart")
    snap = tmp_path / "snapshots" / "1"
    assert seq == 1 and (snap / "memory" / "memory.json").is_file()
    assert (snap / "impressions" / "owner.md").read_text(encoding="utf-8") == "kind\n"
    assert snapshots.manifest(tmp_path, 1) == {
        "seq": 1, "ts": 1767600000000, "reason": "night", "day": "2026-01-05", "by": "smart",
        "paths": ["memory", "impressions", "diary"], "files": 3}
    assert snapshots.take(tmp_path, 1767600001000, "night") == 2
    assert [m["seq"] for m in snapshots.list_all(tmp_path)] == [1, 2]


def test_restoring_puts_memory_and_impressions_back_but_leaves_the_diary(tmp_path):
    seed(tmp_path)
    snapshots.take(tmp_path, 1, "night")
    seed(tmp_path, memory="two", impression="curt", entry="second")
    (tmp_path / "memory" / "extra.json").write_text("{}", encoding="utf-8")
    assert snapshots.restore(tmp_path, 1) == ["memory", "impressions"]
    assert read(tmp_path) == "one" and impressions.read(tmp_path, "owner") == "kind\n"
    assert not (tmp_path / "memory" / "extra.json").exists()
    assert [e["text"] for e in diary.entries(tmp_path, "2026-01-05")] == ["first", "second"]
    assert not list(tmp_path.glob("*.restoring")) and not list(tmp_path.glob("*.replaced"))


def test_the_diary_is_restored_only_when_asked(tmp_path):
    seed(tmp_path)
    snapshots.take(tmp_path, 1, "night")
    diary.append(tmp_path, T0 + 60, "second", tz="UTC")
    assert snapshots.restore(tmp_path, 1, diary=True) == ["memory", "impressions", "diary"]
    assert [e["text"] for e in diary.entries(tmp_path, "2026-01-05")] == ["first"]


def test_a_folder_that_did_not_exist_at_the_snapshot_is_removed_on_restore(tmp_path):
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "memory.json").write_text('{"v": "one"}', encoding="utf-8")
    snapshots.take(tmp_path, 1, "night")
    impressions.write(tmp_path, "agent-b", "new today")
    assert snapshots.manifest(tmp_path, 1)["paths"] == ["memory"]
    assert snapshots.restore(tmp_path, 1) == ["memory", "impressions"]
    assert not (tmp_path / "impressions").exists()


def test_restoring_a_snapshot_that_is_not_there_changes_nothing(tmp_path):
    seed(tmp_path)
    with pytest.raises(snapshots.SnapshotError, match="no snapshot 7"):
        snapshots.restore(tmp_path, 7)
    assert read(tmp_path) == "one"


def test_only_the_newest_snapshots_are_kept(tmp_path):
    seed(tmp_path)
    for i in range(7):
        snapshots.take(tmp_path, i, "night", kept=5)
    assert [m["seq"] for m in snapshots.list_all(tmp_path)] == [3, 4, 5, 6, 7]
    assert snapshots.take(tmp_path, 8, "before rollback", kept=0) == 8
    assert len(snapshots.list_all(tmp_path)) == 6
    assert snapshots.prune(tmp_path, 2) == [3, 4, 5, 6]


def test_a_copy_that_was_interrupted_is_ignored_and_its_number_not_reused(tmp_path):
    seed(tmp_path)
    snapshots.take(tmp_path, 1, "night")
    (tmp_path / "snapshots" / "2" / "memory").mkdir(parents=True)
    (tmp_path / "snapshots" / "notes").mkdir()
    assert [m["seq"] for m in snapshots.list_all(tmp_path)] == [1]
    assert snapshots.manifest(tmp_path, 2) is None
    assert snapshots.take(tmp_path, 2, "night") == 3


def test_an_agent_with_nothing_yet_can_still_be_snapshotted(tmp_path):
    assert snapshots.take(tmp_path, 1, "night") == 1
    assert snapshots.manifest(tmp_path, 1)["paths"] == []
    assert snapshots.restore(tmp_path, 1) == []


# --- the diary -------------------------------------------------------------------


def test_a_diary_entry_goes_under_its_local_day_and_time(tmp_path):
    late = T0 + 15.5 * 3600  # 23:30 UTC, already tomorrow in Tokyo
    path = diary.append(tmp_path, late, "  Quiet evening.  ", tz="Asia/Tokyo")
    assert path.name == "2026-01-06.md"
    assert path.read_bytes() == b"## 08:30\nQuiet evening.\n\n"
    assert diary.append(tmp_path, late, "Same moment.", tz="UTC").name == "2026-01-05.md"
    assert diary.days(tmp_path) == ["2026-01-05", "2026-01-06"]


def test_dreams_are_marked_and_can_be_left_out(tmp_path):
    diary.append(tmp_path, T0, "Worked on the outline.\n## not a header", tz="UTC")
    diary.append(tmp_path, T0 + 60, "A river of paper.", dream=True, tz="UTC")
    day = "2026-01-05"
    assert diary.entries(tmp_path, day) == [
        {"time": "08:00", "dream": False, "text": "Worked on the outline.\n## not a header"},
        {"time": "08:01", "dream": True, "text": "A river of paper."}]
    assert [e["text"] for e in diary.entries(tmp_path, day, include_dreams=False)] == [
        "Worked on the outline.\n## not a header"]


def test_a_day_that_is_not_a_date_reads_as_empty_and_never_as_a_path(tmp_path):
    (tmp_path / "diary").mkdir()
    (tmp_path / "secret.md").write_text("## 08:00\nnot yours\n", encoding="utf-8")
    (tmp_path / "diary" / "notes.md").write_text("## 08:00\nstray\n", encoding="utf-8")
    assert diary.entries(tmp_path, "../secret") == []
    assert diary.entries(tmp_path, "2026-01-09") == []
    assert diary.days(tmp_path) == []


# --- impressions -----------------------------------------------------------------


def test_an_impression_is_kept_per_party_under_a_safe_name(tmp_path):
    assert impressions.write(tmp_path, "../Agent-B", "x" * 2000) == \
        "impression of agent-b rewritten"
    assert impressions.known(tmp_path) == ["agent-b"]
    assert len(impressions.read(tmp_path, "agent-b")) == impressions.MAX_CHARS + 1
    assert impressions.read(tmp_path, "nobody") == ""
    assert impressions.safe_name("../..") == "unknown"


def test_only_parties_really_met_get_their_impression_rewritten(tmp_path):
    impressions.write(tmp_path, "agent-b", "Careful and slow.")
    out = impressions.apply(tmp_path, [
        {"about": "Owner", "text": " Direct, likes sources. "},
        {"about": "agent-b", "text": "Rushed today."},
        {"about": "", "text": "nobody"}, "junk"], met={"owner"})
    assert out == ["impression of owner rewritten",
                   "rejected impression of agent-b: no meeting with them today",
                   "rejected impression of ?: no meeting with them today",
                   "rejected impression of ?: no meeting with them today"]
    assert impressions.read(tmp_path, "owner") == "Direct, likes sources.\n"
    assert impressions.read(tmp_path, "agent-b") == "Careful and slow.\n"


def test_an_empty_impression_never_replaces_a_real_one(tmp_path):
    impressions.write(tmp_path, "owner", "Values sourced work.")
    imps = [{"about": "owner", "text": "   "}, {"about": "agent-b", "summary": "Under another key"},
            {"about": "agent-c", "text": ""}]
    assert impressions.empty_about(imps, {"owner", "agent-b"}) == ["owner"]
    out = impressions.apply(tmp_path, imps, {"owner", "agent-b"})
    assert out[0] == "rejected impression of owner: no text"
    assert impressions.read(tmp_path, "owner") == "Values sourced work.\n"
    assert impressions.read(tmp_path, "agent-b") == "Under another key\n"
