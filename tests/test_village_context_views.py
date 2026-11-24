"""What an agent reads each beat. One test or more for each numbered context rule."""

from __future__ import annotations

from types import SimpleNamespace

from jimmyhq.village.context import (
    bench_view,
    build,
    clock_view,
    files_view,
    nudges,
    situation,
    talk_view,
)
from jimmyhq.village.context.system import system_text
from jimmyhq.village.rules import RULES
from test_village_beat_fakes import ME, Box, Memory, item, kinds, make_village, owner_says, step

CONTEXT_RULES = (
    "total_cap", "recent_actions", "working_files", "just_read_whole", "focus_stays",
    "returning_file", "folders_seen", "chat_view", "urgent_call_first", "room_view",
    "board_view", "impressions_present", "bench_view", "circling", "waiting_is_not_done",
    "news_line", "review_wait", "parked", "diary_view", "time_view", "all_done",
    "blocked_ladder", "resume_note_shown",
)


def did(v, tool, ok=True, result="ok", making=False, agent=ME, **args):
    return v.log.append("action", agent, v.clock.now(), tool=tool, args=args, ok=ok,
                        making=making, no_new_result=False, result=result, task=None)


def put(v, path, text, agent=ME):
    target = v.files_dir(agent) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))


def numbered(n: int) -> str:
    """Text whose every part says where it is: character i starts the word for i // 10."""
    return "".join(f"{i:09d} " for i in range(n // 10))


def test_every_numbered_context_rule_is_a_named_function():
    for name in CONTEXT_RULES:
        assert name in RULES and callable(RULES[name].fn), name


# ------------------------------------------------------------ the order
def test_the_system_text_is_the_frame_and_then_what_the_agent_knows_of_itself(core):
    docs = SimpleNamespace(
        frame=lambda name, owner, capabilities=(): f"FRAME for {name}, brought on by {owner}.")
    memory = type("M", (Memory,), {"text": "## self\n- [3] I like maps.", "name": "Wren"})
    v = make_village(core, docs=docs, **{"memory.store": memory})
    text = system_text(v, ME)
    assert text == ("FRAME for Wren, brought on by the owner.\n\n"
                    "# What you know about yourself\n\n## self\n- [3] I like maps.")
    assert system_text(v, ME, with_memory=False).endswith("Nothing yet. You are brand new.")


def test_a_new_agent_without_the_villages_documents_still_gets_a_frame(core):
    core.settings.set("owner.name", "Robin", actor="owner")
    v = make_village(core)
    text = system_text(v, ME)
    assert "For now you are called Ada" in text and "Robin brought you on" in text
    assert text.endswith("Nothing yet. You are brand new.")


def test_the_view_is_ordered_from_what_changes_least_to_what_changes_every_beat(core):
    v = make_village(core, agents=(ME, "ben"))
    owner_says(v, "Please look at this now.", urgent=True)
    v.log.append("breakroom", "ben", v.clock.now(), text="Morning.")
    v.log.append("board_post", "ben", v.clock.now(), post_kind="need_help", text="Maps?")
    put(v, "notes/a.md", "my notes")
    did(v, "read_file", path="notes/a.md")
    did(v, "list_files", path="notes")
    text = build.user_text(v, ME, [{"phrase": "useful"}], ("write_diary", "the whole answer"))
    heads = ["is calling you (urgent)", "# Your priorities and tasks", "# Right now",
             "# Your chat with the owner", "# Your coworkers right now", "# The break room",
             "# The work board", "# Your last actions", "# Files you are working with",
             "# Folders you looked at", "# What came back from write_diary",
             "# Your diary today", "# How you feel", "# Time", "Choose one action."]
    places = [text.index(h) for h in heads]
    assert places == sorted(places) and text.startswith("# The owner is calling you")
    assert "useful: you finished something recently. You feel like getting things done." in text


# ---------------------------------------------------------------- rule 20
def test_a_view_over_the_cap_is_cut_and_says_so(core):
    assert build.cut_to_cap("x" * 10, 10) == "x" * 10
    assert build.cut_to_cap("x" * 11, 10) == "x" * 10 + build.CUT_NOTE
    core.settings.set("village.context.total_cap", 60000, actor="owner")
    v = make_village(core)
    put(v, "big.md", "a" * 47000)
    did(v, "read_file", path="big.md")
    text = build.user_text(v, ME, [], ("write_diary", "b" * 30000))
    assert len(text) == 60000 + len(build.CUT_NOTE)
    assert text.endswith("[... cut: this moment's view was too long to show in full]")


# ---------------------------------------------------------------- rule 21
def test_the_last_fifteen_actions_are_shown_a_line_each_with_their_ids(core):
    v = make_village(core)
    for i in range(20):
        did(v, "write_diary", text=f"entry {i}")
    lines = situation.recent_actions_text(v, ME).split("\n")
    assert len(lines) == 15 and "entry 5" in lines[0] and "entry 19" in lines[-1]
    last = kinds(v, "action")[-1]
    assert lines[-1] == f"{last.id}: write_diary(text='entry 19') -> ok: ok"
    assert situation.recent_actions_text(make_village(core), ME) == "(nothing yet)"


def test_a_result_is_cut_at_three_hundred_characters_and_a_long_argument_at_sixty(core):
    v = make_village(core)
    did(v, "read_file", ok=False, result="r" * 400, path="p" * 100)
    v.log.append("action", ME, v.clock.now(), tool="read_file", args={"path": "a.md"}, ok=True,
                 no_new_result=True, result="same")
    first, second = situation.recent_actions_text(v, ME).split("\n")
    assert first.endswith("-> FAILED: " + "r" * 300) and f"path='{'p' * 60}'" in first
    assert second.endswith("-> ok: same (nothing new)")


def test_a_refusal_shows_its_arguments_and_other_events_read_plainly(core):
    v = make_village(core)
    v.world.refuse(ME, "click", {"element": 7}, "repeat_same_state")
    v.world.refuse(ME, "finish_task", {"task": "T9"}, "T9 is not on your list")
    v.world.complete(ME, "T1", ["evt-1"])
    v.world.complete(ME, "T2", None)
    v.world.set_aside(ME, "P1", "later")
    v.world.resume_by_choice(ME, "P1")
    lines = situation.recent_actions_text(v, ME).split("\n")
    assert "click(element='7') was refused: you just did exactly this" in lines[0]
    assert lines[1].endswith("finish_task(task='T9') was refused: T9 is not on your list")
    assert lines[2].endswith(": finished T1")
    assert lines[3].endswith(": tried to finish T2 without evidence; not counted")
    assert lines[4].endswith(": P1 was set aside") and lines[5].endswith(": P1 picked back up")


# ---------------------------------------------------------------- rule 22
def test_the_two_files_used_last_are_shown_as_they_are_now(core):
    v = make_village(core)
    for name in ("a.md", "b.md", "c.md"):
        put(v, name, f"text of {name}")
        did(v, "write_file", making=True, path=name)
    for _ in range(6):
        did(v, "write_diary", text="x")
    put(v, "c.md", "c.md as it is NOW")
    text = files_view.working_files_text(v, ME)
    assert text.startswith("# Files you are working with (their current text; no need to read")
    assert "## c.md (17 characters, as it is now; shown whole, nothing left out)\n" in text
    assert "c.md as it is NOW" in text and "text of b.md" in text and "a.md" not in text
    assert files_view.working_files_text(make_village(core), ME) == ""


def test_a_file_used_longer_ago_than_the_lookback_or_never_successfully_is_not_shown(core):
    v = make_village(core)
    put(v, "old.md", "old")
    did(v, "write_file", path="old.md")
    did(v, "read_file", ok=False, path="missing.md")
    for _ in range(40):
        did(v, "write_diary", text="x")
    assert files_view.working_files_text(v, ME) == ""


def test_a_long_file_is_shown_as_head_and_tail_with_the_gap_and_its_start_stated():
    body = numbered(20000)
    shown, hidden = files_view.head_tail(body, 16000)
    assert hidden == (4000, 8000)
    assert shown.startswith(body[:4000]) and shown.endswith(body[8000:])
    assert ("[... 4000 characters from the middle are not shown here (characters 4000 to "
            "8000); read_file with start=4000 shows them ...]") in shown
    assert files_view.head_tail("short", 16000) == ("short", None)


def test_a_file_only_written_is_capped_with_its_middle_left_out(core):
    v = make_village(core)
    put(v, "long.md", numbered(20000))
    did(v, "add_to_file", making=True, path="long.md")
    text = files_view.working_files_text(v, ME)
    assert "## long.md (20000 characters, as it is now; its middle is left out below)" in text
    assert "read_file with start=4000 shows them" in text


# ---------------------------------------------------------------- rule 23
def test_a_file_just_read_stays_whole_for_as_long_as_rereading_would_be_a_repeat(core):
    v = make_village(core)
    put(v, "long.md", numbered(20000))
    did(v, "read_file", path="long.md")
    for _ in range(5):  # the repeat window is six actions
        did(v, "write_diary", text="x")
    text = files_view.working_files_text(v, ME)
    assert "## long.md (20000 characters, as it is now; shown whole, nothing left out)" in text
    assert numbered(20000) in text
    did(v, "write_diary", text="x")
    assert "its middle is left out below" in files_view.working_files_text(v, ME)


def test_at_most_three_files_just_read_stay_and_each_whole_only_up_to_its_cap(core):
    v = make_village(core)
    for name in ("a.md", "b.md", "c.md", "d.md"):
        put(v, name, name * 10)
        did(v, "read_file", path=name)
    text = files_view.working_files_text(v, ME)
    assert [n for n in ("a.md", "b.md", "c.md", "d.md") if f"## {n} " in text] == [
        "b.md", "c.md", "d.md"]
    put(v, "huge.md", numbered(50000))
    did(v, "read_file", path="huge.md")
    assert "## huge.md (50000 characters, as it is now; its middle is left out" in \
        files_view.working_files_text(v, ME)


def test_whole_files_just_read_share_one_budget(core):
    v = make_village(core)
    put(v, "one.md", "1" * 40000)
    put(v, "two.md", "2" * 40000)
    did(v, "read_file", path="one.md")
    did(v, "read_file", path="two.md")
    text = files_view.working_files_text(v, ME)
    assert "## two.md (40000 characters, as it is now; shown whole, nothing left out)" in text
    assert "## one.md (40000 characters, as it is now; its middle is left out below)" in text


# ---------------------------------------------------------------- rule 24
def test_the_part_of_a_long_file_last_read_stays_in_view(core):
    v = make_village(core)
    body = numbered(70000)
    put(v, "doc.md", body)
    did(v, "read_file", path="doc.md", start=30000)
    text = files_view.working_files_text(v, ME)
    assert ("## doc.md (70000 characters, as it is now; parts are left out below; the part "
            "you read or changed last is shown)") in text
    assert body[30000:60000] in text  # the whole part that was read
    assert ("[... characters 8000 to 30000 are not shown here; read_file with start=8000 "
            "shows them ...]") in text  # the gap before it, stated with its start
    assert text.count("are not shown here") == 1 and text.rstrip().endswith(body[-20:].rstrip())


def test_the_place_of_an_edit_stays_in_view_and_a_whole_write_has_no_place(core):
    body = numbered(70000)
    evs = [SimpleNamespace(data={"tool": "edit_file", "ok": True,
                                 "args": {"path": "doc.md", "new_text": body[40000:40050]}}),
           SimpleNamespace(data={"tool": "write_file", "ok": True, "args": {"path": "w.md"}}),
           SimpleNamespace(data={"tool": "read_file", "ok": True, "args": {"path": "r.md"}}),
           SimpleNamespace(data={"tool": "read_file", "ok": False,
                                 "args": {"path": "f.md", "start": 9}})]
    assert files_view.focus_points(evs, 6, lambda path: body) == {"doc.md": 36000}
    shown, cut = files_view.focused_view(body, 48000, 36000)
    assert cut is True and body[36000:66000] in shown and len(shown) < 48400
    assert files_view.focused_view("short", 48000, 0) == ("short", False)


# ---------------------------------------------------------------- rule 25
def test_a_short_file_read_three_times_stays_in_view(core):
    v = make_village(core)
    put(v, "plan.md", "the plan")
    put(v, "twice.md", "read twice")
    put(v, "big.md", "b" * 9000)
    for _ in range(3):
        did(v, "read_file", path="plan.md")
        did(v, "read_file", path="big.md")
    did(v, "read_file", path="twice.md")
    did(v, "read_file", path="twice.md")
    for name in ("x.md", "y.md"):
        put(v, name, name)
        did(v, "write_file", making=True, path=name)
    for _ in range(6):
        did(v, "write_diary", text="x")
    text = files_view.working_files_text(v, ME)
    assert "## plan.md " in text and "the plan" in text
    assert "big.md" not in text and "twice.md" not in text  # too long; read too seldom
    assert "## x.md " in text and "## y.md " in text


# ---------------------------------------------------------------- rule 26
def test_the_two_folders_listed_last_are_shown_as_they_are_now(core):
    v = make_village(core)
    put(v, "notes/a.md", "a")
    put(v, "drafts/d.md", "d")
    put(v, "past/o.md", "o")
    for folder in ("past", "notes", "/drafts/", "notes"):
        did(v, "list_files", path=folder)
    put(v, "notes/new.md", "written after the listing")
    (v.files_dir(ME) / "notes" / "sub").mkdir()
    text = files_view.folders_text(v, ME)
    assert text.startswith("# Folders you looked at (as they are now; no need to list them")
    assert "## notes/\na.md (1 bytes)\nnew.md (25 bytes)\nsub/" in text
    assert "## drafts/\nd.md (1 bytes)" in text and "past" not in text


def test_a_long_folder_is_cut_at_eighty_lines_and_says_how_many_more(core):
    v = make_village(core)
    for i in range(85):
        put(v, f"f{i:03d}.md", "x")
    did(v, "list_files", path="")
    text = files_view.folders_text(v, ME)
    assert "## (top folder)/\nf000.md" in text and "f079.md" in text and "f080.md" not in text
    assert text.endswith("[... and 5 more]\n\n")


def test_a_path_that_would_leave_the_agents_files_is_never_read(core):
    v = make_village(core)
    put(v, "a.md", "mine")
    (v.shared_dir / "ben").mkdir(parents=True)
    (v.shared_dir / "ben" / "s.md").write_bytes(b"line one\r\nline two")
    assert files_view.read_text(v, ME, "a.md") == "mine"
    assert files_view.read_text(v, ME, "shared/ben/s.md") == "line one\nline two"
    assert files_view.read_text(v, ME, "../../state.json") is None
    assert files_view.read_text(v, ME, "a.md/../../../x") is None
    assert files_view.listing(v, ME, "nowhere") is None


def test_the_last_answer_is_shown_whole_up_to_its_cap_and_the_cut_is_stated():
    assert files_view.last_result_text(None, 34000) == ""
    assert files_view.last_result_text(("rest", "resting"), 34000) == (
        "# What came back from rest\nresting\n\n")
    cut = files_view.last_result_text(("read_file", "x" * 34010), 34000)
    assert cut.endswith("x\n[... cut: 10 more characters not shown]\n\n")


# ---------------------------------------------------------------- rule 27
def test_the_chat_view_shows_the_last_eight_messages_of_two_days(core):
    v = make_village(core)
    owner_says(v, "three days old")
    step(v, 60, times=49)
    for i in range(5):
        owner_says(v, f"question {i}")
        v.log.append("chat", ME, v.clock.now(), sender=ME, text=f"answer {i}")
    text = talk_view.chat_text(v, ME)
    assert text.startswith("# Your chat with the owner\n")
    lines = text.strip().split("\n")[1:]
    assert len(lines) == 8 and "three days old" not in text and "question 0" not in text
    assert lines[0].endswith("The owner: question 1") and lines[-1].endswith("You: answer 4")
    assert lines[0].startswith("Wed 11:00 ")
    assert talk_view.chat_text(make_village(core), ME) == ""


def test_the_owners_words_are_whole_and_the_agents_own_are_cut_with_a_marker(core):
    v = make_village(core)
    owner_says(v, "o" * 3001)
    v.log.append("chat", ME, v.clock.now(), sender=ME, text="m" * 1501)
    v.log.append("chat", ME, v.clock.now(), sender=ME, text="s" * 1500)
    text = talk_view.chat_text(v, ME)
    assert "o" * 3001 in text and "s" * 1500 + "\n" in text
    assert "m" * 1500 + " [... shortened here to save room; the whole message was sent]" in text
    assert "m" * 1501 not in text


def test_the_chat_view_says_when_the_owner_is_waiting_for_an_answer(core):
    v = make_village(core)
    owner_says(v, "Are you there?")
    assert talk_view.chat_text(v, ME).endswith(
        "\n(The owner's last message has not been answered yet.)\n\n")


def test_the_chat_view_says_how_long_the_owner_has_been_quiet(core):
    v = make_village(core)
    owner_says(v, "Good work.")
    v.log.append("chat", ME, v.clock.now(), sender=ME, text="Thanks.")
    step(v, 60, times=2)
    assert talk_view.chat_text(v, ME).endswith("You: Thanks.\n\n")  # under three hours
    step(v, 60)
    assert talk_view.chat_text(v, ME).endswith(
        "\n(Nothing from the owner since Mon 10:00, 3 hours ago: the messages after it are "
        "yours.)\n\n")
    step(v, 60, times=46)
    v.log.append("chat", ME, v.clock.now(), sender=ME, text="Still here.")
    assert talk_view.chat_text(v, ME).endswith(
        "\n(The owner has not written in the last 2 days: every message here is yours.)\n\n")


# ---------------------------------------------------------------- rule 28
def test_an_urgent_call_comes_first_until_it_is_answered(core):
    core.settings.set("owner.name", "Robin", actor="owner")
    v = make_village(core)
    assert talk_view.call_text(v, ME) == ""
    owner_says(v, "The site is down. Look now.", urgent=True)
    text = build.user_text(v, ME, [])
    assert text.startswith("# Robin is calling you (urgent)\nThe site is down. Look now.\n"
                           "This was marked urgent, so it comes before anything else: answer "
                           "with message_owner.\n\n# Your priorities and tasks")
    v.log.append("chat", ME, v.clock.now(), sender=ME, text="Looking.")
    assert talk_view.call_text(v, ME) == ""
    assert build.user_text(v, ME, []).startswith("# Your priorities and tasks")


# ---------------------------------------------------------------- rule 29
def test_the_room_view_shows_two_hours_and_ten_lines_and_marks_who_has_left(core):
    v = make_village(core, agents=(ME, "ben"))
    v.log.append("breakroom", "ben", v.clock.now(), text="three hours ago")
    step(v, 60, times=3)
    v.log.append("breakroom", "old-timer", v.clock.now(), text="I was here once.")
    for i in range(10):
        v.log.append("breakroom", ME if i % 2 else "ben", v.clock.now(), text=f"line {i}")
    text = talk_view.social_text(v, ME)
    room = text.split("# The break room (last 2 hours)\n")[1].split("\n\n")[0].split("\n")
    assert len(room) == 10 and "three hours ago" not in text and "I was here once" not in text
    assert room[0] == "13:00 Ben: line 0" and room[1] == "13:00 You: line 1"
    v.log.append("breakroom", "old-timer", v.clock.now(), text="Back for a word.")
    assert "13:00 Old Timer (has left the village): Back for a word." in \
        talk_view.social_text(v, ME)


def test_an_agent_alone_sees_no_coworkers_room_or_board(core):
    v = make_village(core)
    v.log.append("board_post", ME, v.clock.now(), post_kind="good_at", text="Maps.")
    assert talk_view.social_text(v, ME) == ""


def test_a_closed_room_is_said_so_and_points_to_the_work_board(core):
    v = make_village(core, agents=(ME, "ben"))
    assert "The break room is closed" not in talk_view.social_text(v, ME)
    v.world.agents[ME].needs["social"] = 84  # had its fill
    text = talk_view.social_text(v, ME)
    assert "The break room is closed to you for the moment" in text
    assert ("Your coworkers cannot see your chat with the owner: anything meant for one of "
            "them goes on the work board.") in text
    v.world.agents[ME].mode = "social"
    assert "The break room is closed" not in talk_view.social_text(v, ME)


# ---------------------------------------------------------------- rule 30
def test_the_newest_posts_by_others_are_whole_and_the_rest_are_cut_with_a_marker(core):
    v = make_village(core)
    now = v.clock.now()
    posts = [{"id": f"evt-{i}", "by": "ben", "kind": "need_help", "text": f"{i}" * 300,
              "ts": now - 3600} for i in range(1, 7)]
    posts[0]["ts"] = now - 25 * 3600  # too old to be shown whole
    posts.append({"id": "evt-7", "by": ME, "kind": "good_at", "text": "m" * 300, "ts": now})
    posts.append({"id": "evt-8", "by": "ben", "kind": "good_at", "text": "short", "ts": now})
    shown = talk_view.board_shown(v, ME, posts)  # the board cuts; the numbers are settings
    whole = [p["id"] for p in posts if shown[p["id"]] == p["text"]]
    assert whole == ["evt-4", "evt-5", "evt-6", "evt-8"]  # the four newest by others
    assert shown["evt-7"].startswith("m" * 200 + " [... shortened here to save room.")
    assert "its writer's shared folder" in shown["evt-1"] and len(shown["evt-2"]) < 400


def test_the_board_shows_twelve_open_posts_with_who_posted_each(core):
    v = make_village(core, agents=(ME, "ben"))
    for i in range(14):
        v.log.append("board_post", ME if i == 13 else "ben", v.clock.now(),
                     post_kind="need_help", text=f"post {i}")
    board = talk_view.social_text(v, ME).split("# The work board\n")[1].strip().split("\n")
    assert len(board) == 12 and "post 2" in board[0]
    assert board[-1].endswith(" You (need_help): post 13")
    assert " Ben (need_help): post 12" in board[-2]


# ---------------------------------------------------------------- rule 31
def test_an_agent_sees_its_impressions_only_of_whoever_is_in_view(core):
    notes = {"ben": "Careful, kind.", "cy": "Fast.", "owner": "Direct; means well."}
    seam = SimpleNamespace(read=lambda agent_dir, who: notes.get(who, ""))
    v = make_village(core, agents=(ME, "ben", "cy"), **{"memory.impressions": seam})
    assert "private impression" not in talk_view.social_text(v, ME)
    v.log.append("breakroom", "ben", v.clock.now(), text="Morning.")
    text = talk_view.social_text(v, ME)
    assert "# Your private impression of Ben\nCareful, kind.\n" in text
    assert "Fast." not in text and "Direct" not in text
    owner_says(v, "Hello.")
    assert "# Your private impression of the owner\nDirect; means well.\n" in \
        talk_view.social_text(v, ME)
    step(v, 60, times=7)  # no chat within six hours, nobody in the room view
    assert "private impression" not in talk_view.social_text(v, ME)
    assert talk_view.present(v, ME, [("ben", 0.0, "x"), (ME, 0.0, "y"), ("ben", 0.0, "z")]) == [
        "ben"]


# ---------------------------------------------------------------- rule 32
def card(v, n, kind, status, title, note=None, decided_h=None, filed_h=30, paths=()):
    now = v.clock.now()
    return {"card_id": f"card-{n}", "agent": ME, "kind": f"village.{kind}", "title": title,
            "status": status, "note": note, "paths": list(paths),
            "filed_ts": round((now - filed_h * 3600) * 1000),
            "decided_ts": None if decided_h is None else round((now - decided_h * 3600) * 1000)}


def test_the_bench_view_shows_what_waits_and_a_day_of_decisions_with_dated_notes(core):
    v = make_village(core)
    cards = [
        card(v, 1, "work", "open", "The newsletter draft"),
        card(v, 2, "work", "approved", "The October report", decided_h=2),
        card(v, 3, "finding", "approved", "A dead link", decided_h=3),
        card(v, 4, "finding", "returned", "Slow page", note="Which page?", decided_h=1),
        card(v, 5, "finding", "declined", "Odd colour", decided_h=5),
        card(v, 6, "priority", "declined", "Redo the site", decided_h=6),
        card(v, 7, "work", "approved", "Last week's letter", decided_h=30),
    ]
    v.fakes["comms.benchlink"] = Box(view=cards, waiting=[{"card_id": "card-1"}])
    lines = bench_view.bench_text(v, ME).split("\n")
    assert lines[0] == "On the owner's bench:"
    assert lines[1] == "- card-1 waiting for the owner: The newsletter draft"
    assert lines[2] == "- The owner approved (final) card-2: The October report"
    assert lines[3] == "- The owner noted card-3: A dead link"
    assert lines[4] == ('- The owner asked for more on card-4: Slow page. The note (written '
                        'Mon 09 Nov 09:00): "Which page?"')
    assert lines[5] == "- The owner dismissed card-5: Odd colour"
    assert lines[6] == "- The owner said no to card-6: Redo the site"
    assert not any("card-7" in line for line in lines)  # decided more than a day ago
    assert lines[7] == ("  (All told, since you were born: 7 items from you; 3 approved, 2 no, "
                        "1 not yet, 1 waiting.)")
    assert bench_view.bench_text(make_village(core), ME) == ""


def test_held_items_are_shown_with_the_reminder_that_the_limit_is_on_reading(core):
    v = make_village(core)
    v.fakes["comms.work"] = Box(queued=[
        {"title": "Draft two", "paths": ["d2.md"]},
        {"title": "Draft three", "paths": ["d3.md"], "problem": "d3.md needs a second review"}])
    v.fakes["comms.findings"] = Box(queued=[{"title": "A typo"}])
    lines = bench_view.bench_text(v, ME).split("\n")
    assert lines[1] == "- queued for tomorrow morning (goes by itself): Draft two"
    assert lines[2] == ("- held back from the owner's bench: Draft three. d3.md needs a second "
                        "review; it goes by itself once that is done")
    assert lines[3] == ("- finding held for the owner's bench (goes by itself when there is "
                        "room): A typo")
    assert lines[4] == ("  (The limit is on what the owner reads in a day, not on what you "
                        "make: keep doing useful work.)")


def test_work_sent_back_says_once_its_revision_has_gone_or_is_queued(core):
    v = make_village(core)
    back = card(v, 1, "work", "returned", "The letter", note="Shorter.", decided_h=3,
                paths=["letter.md"])
    v.fakes["comms.benchlink"] = Box(view=[back], waiting=[])
    assert "revision" not in bench_view.bench_text(v, ME)
    v.fakes["comms.work"] = Box(queued=[{"title": "The letter", "paths": ["letter.md"]}])
    assert ("(Your revision is queued for tomorrow morning and goes by itself; this one is "
            "answered.)") in bench_view.bench_text(v, ME)
    v.fakes["comms.work"] = Box(queued=[])
    again = card(v, 2, "work", "open", "The letter, shorter", filed_h=1, paths=["letter.md"])
    v.fakes["comms.benchlink"] = Box(view=[back, again], waiting=[{"card_id": "card-1"}])
    assert "(You sent your revision as card-2; this one is answered.)" in \
        bench_view.bench_text(v, ME)


def test_the_lifetime_tally_counts_every_card_not_only_those_in_view(core):
    v = make_village(core)
    every = [card(v, n, "work", status, "t") for n, status in enumerate(
        ["approved"] * 14 + ["declined"] * 6 + ["returned"] * 2 + ["open"])]
    every.append(card(v, 99, "work", "replaced", "an earlier version"))  # not an item of its own
    v.fakes["comms.benchlink"] = Box(view=[card(v, 22, "work", "open", "Newest")],
                                     waiting=[{"card_id": "card-22"}], cards=every)
    assert bench_view.bench_text(v, ME).endswith(
        "(All told, since you were born: 23 items from you; 14 approved, 6 no, 2 not yet, "
        "1 waiting.)")


# ---------------------------------------------------------- rules 33 and 34
def test_going_back_over_the_same_files_with_nothing_waiting_is_named(core):
    v = make_village(core)
    for i in range(30):
        did(v, "read_file", path=f"plan-{i % 3}.md")
    text = nudges.circling_text(v, ME)
    assert text.startswith("Nothing of yours is waiting for the owner, and 27 of your last 30 "
                           "actions went back to a file or page you had already looked at.")
    assert "start_own_task" in text and text in situation.right_now_text(v, ME)


def test_circling_is_not_named_when_something_waits_or_the_rereading_follows_changes(core):
    v = make_village(core)
    for _ in range(15):
        did(v, "read_file", path="draft.md")
        did(v, "edit_file", making=True, path="draft.md")
    assert nudges.circling_text(v, ME) == ""  # reading a file again after changing it is work
    quiet = make_village(core)
    for _ in range(29):
        did(quiet, "read_file", path="plan.md")
    assert nudges.circling_text(quiet, ME) == ""  # fewer than thirty actions to judge by
    did(quiet, "read_file", path="plan.md")
    assert nudges.circling_text(quiet, ME) != ""
    quiet.fakes["comms.benchlink"] = Box(view=[], waiting=[{"card_id": "card-1"}])
    assert nudges.circling_text(quiet, ME) == ""
    varied = make_village(core)
    for i in range(30):
        did(varied, "read_file", path=f"plan-{i % 21}.md")
    assert nudges.circling_text(varied, ME) == ""  # nine revisits: one short


def test_work_waiting_on_the_owner_in_daytime_is_not_a_finished_day(core):
    v = make_village(core)
    v.fakes["comms.benchlink"] = Box(view=[], waiting=[{"card_id": "card-1"}])
    v.fakes["comms.findings"] = Box(queued=[{"title": "x"}])
    for _ in range(7):
        did(v, "read_file", path="list.md")
    assert nudges.waiting_text(v, ME) == ""  # seven actions: one short
    did(v, "read_file", path="list.md")
    text = nudges.waiting_text(v, ME)
    assert text.startswith("2 things of yours are with the owner or queued. That is waiting, "
                           "not a finished day")
    assert text in situation.right_now_text(v, ME)
    did(v, "write_file", making=True, path="new.md")
    assert nudges.waiting_text(v, ME) == ""  # it made something


def test_waiting_is_not_named_in_the_evening_or_with_nothing_waiting(core):
    evening = make_village(core, hour=16)
    evening.fakes["comms.benchlink"] = Box(view=[], waiting=[{"card_id": "card-1"}])
    for _ in range(8):
        did(evening, "read_file", path="list.md")
    assert nudges.waiting_text(evening, ME) == ""
    day = make_village(core, hour=15)
    for _ in range(8):
        did(day, "read_file", path="list.md")
    assert nudges.waiting_text(day, ME) == ""
    day.fakes["comms.benchlink"] = Box(view=[], waiting=[{"card_id": "card-1"}])
    assert nudges.waiting_text(day, ME).startswith("1 thing of yours is with the owner")


# ---------------------------------------------------------------- rule 35
def test_news_is_named_with_its_newest_headings_until_the_agent_reads_it(core):
    from jimmyhq.village import docs  # the real reader of the agent's changes file

    v = make_village(core, docs=docs)
    assert nudges.news_text(v, ME) == ""
    put(v, "village/changes.md", "# Changes\n\n## Newest\n- **Findings are held, not refused.** "
        "More.\n- **Rest opens earlier.** More.\n- **Three.** x\n- **Four.** x\n\n"
        "## Older\n- **Old news.** x\n")
    v.log.append("village_news", ME, v.clock.now(), file="village/changes.md")
    text = nudges.news_text(v, ME)
    assert text == ("News from the village: some things on your computer have been fixed or "
                    "changed (Findings are held, not refused. Rest opens earlier. Three.). "
                    "village/changes.md says more (it may correct things you remember about "
                    "your tools).")
    assert text in situation.right_now_text(v, ME)
    did(v, "read_file", ok=False, path="village/changes.md")
    assert nudges.news_text(v, ME) != ""
    did(v, "read_file", path="/village/changes.md")
    assert nudges.news_text(v, ME) == ""
    v.clock.advance(1)
    v.log.append("village_news", ME, v.clock.now(), file="village/changes.md")
    assert nudges.news_text(v, ME) != ""  # newer news


def test_the_villages_own_headings_are_used_when_it_gives_them(core):
    v = make_village(core, docs=SimpleNamespace(
        frame=lambda name, owner, capabilities=(): "frame",
        news_heads=lambda v, agent_id, limit=3: ["A fix.", "Another."][:limit]))
    v.log.append("village_news", ME, v.clock.now(), file="village/changes.md")
    assert "changed (A fix. Another.). village/changes.md" in nudges.news_text(v, ME)


# ---------------------------------------------------------------- rule 36
def test_drafts_too_new_to_review_are_listed_with_the_minute_review_opens(core):
    v = make_village(core)
    assert nudges.review_wait_text(v, ME) == ""
    opens = v.clock.now() + 12 * 60 + 20  # 10:12:20, shown as the next whole minute
    v.fakes["comms.work"] = Box(queued=[], review_wait=[
        {"path": "drafts/letter.md", "opens": opens},
        {"path": "drafts/note.md", "opens": opens + 600}])
    text = nudges.review_wait_text(v, ME)
    assert text == ("Too new to review yet (review_work opens): drafts/letter.md from 10:13; "
                    "drafts/note.md from 10:23.")
    assert text in situation.right_now_text(v, ME)


# ---------------------------------------------------------------- rule 37
def test_a_task_set_aside_is_shown_with_the_minute_it_can_be_picked_back_up(core):
    v = make_village(core)
    a = v.world.agents[ME]
    v.world.set_aside(ME, "T1", "stuck on the login")
    assert situation.parked_lines(v, a) == [
        "Set aside since 10:00: T1. You can pick it back up with focus_on from 10:20."]
    v.clock.advance(20)
    assert situation.parked_lines(v, a) == [
        "Set aside since 10:00: T1. You can pick it back up with focus_on whenever you like."]
    assert situation.parked_lines(v, a)[0] in situation.right_now_text(v, ME)


# ---------------------------------------------------------------- rule 38
def test_the_diary_view_shows_the_last_three_entries_cut_and_how_many_there_are(core):
    entries = [{"time": f"09:{i:02d}", "dream": i == 3, "text": f"entry {i} " + "x" * 500}
               for i in range(5)]
    asked = []

    def read(agent_dir, day, include_dreams=True):
        asked.append((agent_dir.name, day))
        return entries

    v = make_village(core, **{"memory.diary": SimpleNamespace(entries=read)})
    count, text = situation.diary_text(v, ME)
    assert asked == [(ME, "2026-11-09")]
    assert count == " (5 entries so far, the last at 09:04)"
    lines = text.split("\n")
    assert len(lines) == 3 and lines[0].startswith("09:02: entry 2 x")
    assert lines[1].startswith("09:03 (dream): entry 3") and len(lines[0]) == len("09:02: ") + 400
    del entries[3:]
    assert situation.diary_text(v, ME)[0] == ""
    assert situation.diary_text(make_village(core), ME) == ("", "(nothing written today)")


# ---------------------------------------------------------------- rule 39
def test_the_time_is_said_with_weekday_date_clock_part_of_day_and_day_of_life(core):
    v = make_village(core, hour=10)
    assert clock_view.time_text(v, ME) == (
        "Monday November 9, 2026, 10:00 (morning). Day 1 of your life. You usually go to sleep "
        "around 23:00 (from about 20:00 if you're tired with nothing on), so the evening is "
        "still part of your day.")
    step(v, 60, times=15)  # 01:00 on Tuesday: past bedtime, and a new calendar day
    assert clock_view.time_text(v, ME) == (
        "Tuesday November 10, 2026, 01:00 (night). Day 2 of your life.")


def test_the_words_beside_the_clock_and_the_usual_bedtime():
    assert [clock_view.part_of_day(h) for h in (4, 5, 11, 12, 16, 17, 20, 21, 23)] == [
        "night", "morning", "morning", "afternoon", "afternoon", "evening", "evening", "night",
        "night"]
    late = clock_view.bedtime_text(23, 20, 45, 22.5)  # a body clock that runs late
    assert "around 23:45 (from about 20:45" in late
    assert clock_view.bedtime_text(23, 20, 0, 23.0) == ""
    assert clock_view.bedtime_text(23, 20, 0, 2.0) == ""  # after midnight is still that night


# ---------------------------------------------------------------- rule 40
def test_the_list_shows_each_reference_and_says_when_everything_is_done(core):
    v = make_village(core)
    assert situation.priorities_text(v, ME) == (
        "- [ongoing] (from the owner) P1: Write the newsletter\n- [open] T1: Check the links")
    v.fakes["comms.tasklist"] = Box(items=[item("T1", "Check the links", "done")])
    assert situation.priorities_text(v, ME) == (
        "- [done] T1: Check the links\n" + situation.ALL_DONE)
    v.fakes["comms.tasklist"] = Box(items=[])
    assert situation.priorities_text(v, ME) == "(none)\n" + situation.ALL_DONE
    assert "You don't need a task to keep working" in situation.ALL_DONE


def test_waiting_on_someone_shows_the_ladder_of_what_to_do_meanwhile(core):
    v = make_village(core)
    a = v.world.agents[ME]
    assert situation.blocked_text(a) == ""
    v.world.submit(ME, "card-1")
    text = situation.blocked_text(a)
    assert text == ("You are waiting on someone for card-1. While you wait: ask a coworker "
                    "(the work board), look for another way to the same goal, or pick the next "
                    "item on your list.")
    assert text in situation.right_now_text(v, ME)
    # A Bench card is named by its title, and a long list is counted, not recited.
    a.waiting["card-1"]["title"] = "Welcome letter"
    assert situation.blocked_text(a).startswith("You are waiting on someone for Welcome letter.")
    for n in range(2, 9):
        v.world.submit(ME, f"card-{n}")
    assert "card-5, and 3 more. While you wait" in situation.blocked_text(a)


def test_a_task_picked_back_up_shows_its_note_for_six_hours(core):
    v = make_village(core)
    a = v.world.agents[ME]
    assert situation.right_now_text(v, ME).startswith(
        "You are not working on a particular task right now.")
    v.world.act(ME, "read_file", {"path": "a.md"}, None, "x", True, "T1")
    v.world.act(ME, "edit_file", {"path": "a.md"}, None, "no match", False, "T1")
    v.world.set_aside(ME, "T1", "try the other heading")
    v.world.resume_by_choice(ME, "T1")
    text = situation.right_now_text(v, ME)
    assert text.startswith(
        "You are working on: T1\nYou set this aside earlier. You had tried: read_file -> ok; "
        "edit_file -> failed. Your note for next time: try the other heading")
    step(v, 60, times=5)
    assert "You set this aside earlier" in situation.resume_note_text(v, ME, "T1")
    step(v, 61)
    assert situation.resume_note_text(v, ME, "T1") == "" and a.current_task == "T1"
    assert situation.resume_note_text(v, ME, "P1") == ""
