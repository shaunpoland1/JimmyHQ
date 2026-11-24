"""Drafts, review passes and hand-overs."""

from __future__ import annotations

from jimmyhq.village.comms import benchlink, work
from test_village_tools_fakes import (  # noqa: F401
    call,
    card_on_bench,
    decide,
    refused,
    say_owner,
    v,
)

TLDR = "The welcome email is ready. Please approve it so it can be scheduled."
DETAILS = ("A welcome email for new subscribers. Checked against the reviewing list, every "
           "link opened, tone read aloud. Sources: the brand notes.")
NOTES = "Read it aloud, fixed two long sentences and checked every link."


def draft(v, path="drafts/welcome.md", text="Dear friend, welcome. " * 5, agent="ada"):  # noqa: F811
    return call(v, agent, "write_file", {"path": path, "content": text, "replace": True})


def reviewed(v, path="drafts/welcome.md", agent="ada", passes=1):  # noqa: F811
    draft(v, path, agent=agent)
    v.later(31)
    for _ in range(passes):
        call(v, agent, "review_work", {"path": path, "kind": "email", "notes": NOTES})
    return path


def hand(v, path="drafts/welcome.md", title="Welcome email", agent="ada", **more):  # noqa: F811
    return call(v, agent, "hand_over", {"title": title, "paths": [path], "tldr": TLDR,
                                        "details": DETAILS, **more})


# --- review_work -------------------------------------------------------------------------


def test_a_draft_cannot_be_reviewed_straight_after_writing(v):  # noqa: F811
    draft(v)
    v.later(12.5)
    e = refused(v, "ada", "review_work", {"path": "drafts/welcome.md", "kind": "email",
                                          "notes": NOTES})
    assert e.rule == "review_distance"
    assert e.sentence == ("Nothing was recorded: drafts/welcome.md was first written 12 minutes "
                          "ago and a review needs 30 minutes of distance. Review it from 10:30; "
                          "work on something else until then.")
    assert work.review_wait(v, "ada") == [{"path": "drafts/welcome.md",
                                           "opens": v.clock.now() + 17.5 * 60}]
    v.later(17.5)
    assert work.review_wait(v, "ada") == []
    d = call(v, "ada", "review_work", {"path": "/files/drafts/welcome.md", "kind": "email",
                                       "notes": NOTES})
    assert d.result == "review pass 1 of drafts/welcome.md recorded"
    assert d.args_logged == {"path": "drafts/welcome.md", "pass": 1}


def test_the_time_review_opens_is_rounded_up_to_the_whole_minute(v):  # noqa: F811
    v.later(0.4)  # 10:00:24
    draft(v)
    e = refused(v, "ada", "review_work", {"path": "drafts/welcome.md", "kind": "email",
                                          "notes": NOTES})
    assert "Review it from 10:31;" in e.sentence


def test_the_review_distance_is_a_setting(v):  # noqa: F811
    v.core.settings.set("village.review.min_distance_min", 0, actor="owner")
    draft(v)
    assert "recorded" in call(v, "ada", "review_work", {"path": "drafts/welcome.md",
                                                        "kind": "email", "notes": NOTES}).result


def test_review_notes_need_forty_characters(v):  # noqa: F811
    draft(v)
    v.later(31)
    e = refused(v, "ada", "review_work", {"path": "drafts/welcome.md", "kind": "email",
                                          "notes": "looks fine"})
    assert e.rule == "review_notes"
    assert e.sentence == ("Nothing was recorded: your notes are 10 characters and a review pass "
                          "needs at least 40. Say what you checked and what you changed, in a "
                          "few sentences, so the pass means something.")


def test_a_third_pass_on_unchanged_text_is_not_recorded(v):  # noqa: F811
    reviewed(v, passes=1)
    d = call(v, "ada", "review_work", {"path": "drafts/welcome.md", "kind": "nonsense",
                                       "notes": NOTES})
    assert d.result == ("review pass 2 of drafts/welcome.md recorded. That's two passes: if "
                        "it's good enough to show Sam, hand it over.")
    v.later(3)
    d = call(v, "ada", "review_work", {"path": "drafts/welcome.md", "kind": "email",
                                       "notes": NOTES})
    assert d.result == ("drafts/welcome.md already has two review passes as it is now (the last "
                        "at 10:31) and hasn't changed since, so this one isn't recorded: "
                        "another reread adds nothing. Hand it over if it isn't already, or move "
                        "on to something new")
    assert len(work.passes(v, "ada", "drafts/welcome.md")) == 2
    assert work.passes(v, "ada", "drafts/welcome.md")[1].data["work_kind"] == "other"
    # an edit makes it a new text, which takes a pass again
    call(v, "ada", "add_to_file", {"path": "drafts/welcome.md", "content": " PS."})
    assert "review pass 3" in call(v, "ada", "review_work", {
        "path": "drafts/welcome.md", "kind": "email", "notes": NOTES}).result


def test_reviewing_a_missing_file_offers_files_with_similar_names(v):  # noqa: F811
    draft(v, "drafts/welcome-email-series.md")
    e = refused(v, "ada", "review_work", {"path": "deliverables/welcome-email.md",
                                          "kind": "email", "notes": NOTES})
    assert e.rule == "file_missing"
    assert e.sentence == ("Nothing was recorded: there is no file deliverables/welcome-email.md. "
                          "The 1 files with similar names are: drafts/welcome-email-series.md. "
                          "Use list_files to see where it is, and give the path from your top "
                          "folder.")


def test_a_coworkers_shared_draft_can_be_reviewed_where_it_is(v):  # noqa: F811
    draft(v, "shared/bo/plan.md", agent="bo")
    d = call(v, "ada", "review_work", {"path": "shared/bo/plan.md", "kind": "document",
                                       "notes": NOTES})
    assert d.result == "review pass 1 of shared/bo/plan.md recorded"


# --- hand_over ---------------------------------------------------------------------------------


def test_a_reviewed_file_is_handed_over_to_the_bench_the_vault_and_deliverables(v):  # noqa: F811
    reviewed(v, passes=2)
    purpose = v.world.agents["ada"].needs["purpose"]
    d = hand(v, unsure="The sign-off name", needs="A send date")
    card = benchlink.cards(v, "ada")[0]
    cid = card["card_id"]
    assert d.result == f"handed over to Sam as {cid}; you will see their answer"
    assert d.args_logged == {"title": "Welcome email", "paths": ["drafts/welcome.md"]}
    assert card["kind"] == "village.work" and card["paths"] == ["drafts/welcome.md"]
    assert card["status"] == "open" and card["filed_ts"] == round(v.clock.now() * 1000)
    on_bench = card_on_bench(v, cid)
    assert (on_bench["filed_by"], on_bench["lane"]) == ("agent:ada", "needs")
    assert on_bench["title"] == "Welcome email" and on_bench["summary"] == TLDR
    assert on_bench["body"] == DETAILS
    assert {x["label"]: x["text"] for x in on_bench["details"]} == {
        "Not sure about": "The sign-off name", "Needs from you": "A send date",
        "Review passes": "drafts/welcome.md: 2"}
    assert [a["id"] for a in on_bench["actions"]] == ["approve", "not_yet", "no"]
    text = (v.home("ada") / "drafts" / "welcome.md").read_bytes()
    filed = v.core.tape.latest(limit=1, kind="bench.filed")[0].payload["files"]
    assert filed[0]["name"] == "drafts_welcome.md" and v.core.vault.get(filed[0]["ref"]) == text
    kept = v.agent_dir("ada") / "deliverables" / cid / "drafts" / "welcome.md"
    assert kept.read_bytes() == text
    assert v.world.agents["ada"].needs["purpose"] > purpose
    assert [c["card_id"] for c in benchlink.waiting(v, "ada")] == [cid]


def test_a_file_changed_since_its_review_is_not_handed_over(v):  # noqa: F811
    draft(v)
    e = refused(v, "ada", "hand_over", {"title": "Welcome email", "paths": ["drafts/welcome.md"],
                                        "tldr": TLDR, "details": DETAILS})
    assert e.rule == "reviewed_as_is"
    assert e.sentence == ("Nothing was handed over: 1 of the 1 files are not ready. "
                          "drafts/welcome.md has 0 review passes (review_work it first, with "
                          "village/reviewing.md).")
    reviewed(v)
    call(v, "ada", "add_to_file", {"path": "drafts/welcome.md", "content": " PS."})
    e = refused(v, "ada", "hand_over", {"title": "Welcome email",
                                        "paths": ["drafts/welcome.md", "nope.md"],
                                        "tldr": TLDR, "details": DETAILS})
    assert e.rule == "reviewed_as_is"
    assert e.sentence == ("Nothing was handed over: 2 of the 2 files are not ready. "
                          "drafts/welcome.md changed after its last review pass (pass 1); review "
                          "it again as it is now. there is no file nope.md. Use list_files to "
                          "see where it is, and give the path from your top folder.")
    assert benchlink.cards(v, "ada") == []


def test_a_hand_over_needs_files_a_short_version_and_details(v):  # noqa: F811
    reviewed(v)
    base = {"title": "Welcome email", "paths": ["drafts/welcome.md"], "tldr": TLDR,
            "details": DETAILS}
    e = refused(v, "ada", "hand_over", {**base, "paths": []})
    assert e.rule == "handover_files" and "you named 0 files" in e.sentence
    e = refused(v, "ada", "hand_over", {**base, "tldr": "x" * 601})
    assert e.rule == "tldr_size"
    assert e.sentence == ("Nothing was handed over: the short version is 601 characters and it "
                          "takes 1 to 600. Write the two or three sentences Sam can act on.")
    assert refused(v, "ada", "hand_over", {**base, "tldr": "  "}).rule == "tldr_size"
    e = refused(v, "ada", "hand_over", {**base, "details": "It is done."})
    assert e.rule == "details_short"
    assert e.sentence == ("Nothing was handed over: the details are 11 characters and need at "
                          "least 80. They are for checking your work later: say what it is and "
                          "who it is for, what you checked, and your sources.")


def test_a_short_version_longer_than_the_bench_shows_is_kept_whole_in_the_details(v):  # noqa: F811
    reviewed(v)
    long = "Sentence number one is here. " * 20  # 580 characters
    call(v, "ada", "hand_over", {"title": "Welcome email", "paths": "drafts/welcome.md",
                                 "tldr": long, "details": DETAILS})
    on_bench = card_on_bench(v, benchlink.cards(v, "ada")[0]["card_id"])
    assert 290 < len(on_bench["summary"]) <= 300 and on_bench["summary"].endswith("…")
    assert on_bench["details"][0] == {"label": "The short version, whole", "text": long.strip()}


def test_a_day_count_that_does_not_fit_its_date_is_sent_back_once(v):  # noqa: F811
    reviewed(v)
    args = {"title": "Welcome email", "paths": ["drafts/welcome.md"], "tldr": TLDR,
            "details": DETAILS + " The campaign on 1 January 2031 is three days out."}
    e = refused(v, "ada", "hand_over", args)
    assert e.rule == "date_slip"
    assert e.sentence.startswith("Nothing was sent yet: before this goes to Sam, check the days.")
    assert 'Your text says "three days out"' in e.sentence and "Wed 1 Jan is" in e.sentence
    assert benchlink.cards(v, "ada") == []
    assert "handed over to Sam" in call(v, "ada", "hand_over", args).result  # unchanged: it goes


def test_handing_over_the_same_files_again_updates_the_open_card(v):  # noqa: F811
    reviewed(v)
    hand(v)
    cid = benchlink.cards(v, "ada")[0]["card_id"]
    d = hand(v, title="Welcome email, with a clearer title")
    assert d.result == (f"{cid} already had these files, so it now shows Sam this version (no "
                        "new card)")
    assert len(benchlink.cards(v, "ada")) == 1 and work.handovers_today(v, "ada") == 1
    assert card_on_bench(v, cid)["title"] == "Welcome email, with a clearer title"
    assert benchlink.get(v, cid)["extra"]["revised_ts"] == round(v.clock.now() * 1000)


def test_a_returned_hand_over_with_new_files_is_refiled_with_the_owners_note(v):  # noqa: F811
    reviewed(v)
    hand(v)
    old = benchlink.cards(v, "ada")[0]["card_id"]
    result, _ = decide(v, old, "not_yet", note="Warmer opening, please")
    assert result["decision"] == "not_yet" and benchlink.get(v, old)["status"] == "returned"
    assert benchlink.waiting(v, "ada") == []
    call(v, "ada", "edit_file", {"path": "drafts/welcome.md", "old_text": "Dear friend",
                                 "new_text": "Hello friend", "every": True})
    call(v, "ada", "review_work", {"path": "drafts/welcome.md", "kind": "email", "notes": NOTES})
    d = hand(v)
    new = benchlink.cards(v, "ada")[1]
    assert d.result == (f"your revision was sent: {new['card_id']} now holds these files as "
                        "they are, in place of the earlier card (it does not count as a new "
                        "hand-over)")
    assert benchlink.get(v, old)["status"] == "replaced"
    assert card_on_bench(v, old)["status"] == "withdrawn"
    replaces = card_on_bench(v, new["card_id"])["details"][-1]
    assert replaces["label"] == "Replaces" and "Warmer opening, please" in replaces["text"]
    assert work.handovers_today(v, "ada") == 1
    kept = v.agent_dir("ada") / "deliverables" / new["card_id"] / "drafts" / "welcome.md"
    assert kept.read_text(encoding="utf-8").startswith("Hello friend")


def test_past_the_days_limit_a_hand_over_is_queued_and_goes_the_next_day(v):  # noqa: F811
    v.core.settings.set("village.comms.handovers_per_day", 1, actor="owner")
    reviewed(v, "one.md")
    reviewed(v, "two.md")
    reviewed(v, "three.md")
    hand(v, "one.md", "One")
    before = v.world.agents["ada"].needs["frustration"]
    d = hand(v, "two.md", "Two")
    assert d.result == ("Sam has had as many hand-overs as they can take today, so this one is "
                        "queued (number 1): it goes on the Bench by itself tomorrow morning, "
                        "nothing more to do. The day isn't over: keep working. The limit is on "
                        "what Sam reads in a day, not on what you make, so it is no reason to "
                        "stop drafting or to keep your own queue file")
    assert v.world.agents["ada"].needs["frustration"] <= before
    assert [q["title"] for q in work.queued(v, "ada")] == ["Two"]
    assert work.queued_position(v, "ada", "two.md") == 1
    assert len(benchlink.cards(v, "ada")) == 1

    v.later(20)
    d = hand(v, "two.md", "Two, renamed")
    assert d.result.startswith("these files were already queued for tomorrow (number 1, since "
                               "11:33); this replaced that entry's title and notes")
    assert [q["title"] for q in work.queued(v, "ada")] == ["Two, renamed"]

    e = refused(v, "ada", "hand_over", {"title": "Three", "paths": ["three.md"], "tldr": TLDR,
                                        "details": DETAILS})
    assert e.rule == "handover_queue_full" and not e.frustrate
    assert e.sentence == ("Nothing was queued: your queue for tomorrow already holds 1, as many "
                          "as Sam takes in a day (1). Keep this one in your files, or hand over "
                          "the same files as a held one to replace it.")

    d = call(v, "ada", "review_work", {"path": "two.md", "kind": "email", "notes": NOTES})
    assert "already queued for tomorrow (number 1)" in d.result

    assert work.flush(v, "ada") == []  # still today
    v.later(60 * 24)
    sent = work.flush(v, "ada")
    assert [c["title"] for c in sent] == ["Two, renamed"] and work.queued(v, "ada") == []
    assert len(benchlink.cards(v, "ada")) == 2


def test_a_queued_file_edited_since_its_review_stays_queued_with_the_problem(v):  # noqa: F811
    v.core.settings.set("village.comms.handovers_per_day", 1, actor="owner")
    reviewed(v, "one.md")
    reviewed(v, "two.md")
    hand(v, "one.md", "One")
    hand(v, "two.md", "Two")
    call(v, "ada", "add_to_file", {"path": "two.md", "content": " more"})
    v.later(60 * 24)
    assert work.flush(v, "ada") == []
    assert "changed after its last review pass" in work.queued(v, "ada")[0]["problem"]


def test_work_the_owner_asked_for_is_not_held_by_the_days_limit(v):  # noqa: F811
    v.core.settings.set("village.comms.handovers_per_day", 1, actor="owner")
    reviewed(v, "one.md")
    reviewed(v, "two.md")
    hand(v, "one.md", "One")
    say_owner(v, "ada", "Please draft the welcome email today")
    call(v, "ada", "take_on", {"title": "Welcome email",
                               "owner_said": "draft the welcome email"})
    assert not work.over_day_limit(v, "ada", ["two.md"], "T1")
    assert work.over_day_limit(v, "ada", ["two.md"], None)
    assert "handed over to Sam" in hand(v, "two.md", "Two").result
    assert benchlink.cards(v, "ada")[1]["extra"]["task"] == "T1"


def test_approval_is_final_and_adds_purpose(v):  # noqa: F811
    reviewed(v)
    hand(v)
    cid = benchlink.cards(v, "ada")[0]["card_id"]
    purpose = v.world.agents["ada"].needs["purpose"]
    result, event = decide(v, cid, "approve", note="Lovely")
    assert result == {"card_id": cid, "agent": "ada", "kind": "village.work",
                      "decision": "approved", "note": "Lovely", "event": result["event"]}
    final = v.log.last(agent="ada", kinds=["work_final"]).data
    assert final["item"] == cid and final["files"] == ["drafts/welcome.md"]
    assert v.world.agents["ada"].needs["purpose"] > purpose and v.woken == ["ada"]
    row = benchlink.get(v, cid)
    assert row["status"] == "approved" and row["note"] == "Lovely"
    assert row["decided_ts"] == round(v.clock.now() * 1000)
    assert [c["card_id"] for c in benchlink.view(v, "ada")] == [cid]
    v.later(60 * 25)
    assert benchlink.view(v, "ada") == []
    assert benchlink.tally(v, "ada") == {"village.work": {"approved": 1}}
