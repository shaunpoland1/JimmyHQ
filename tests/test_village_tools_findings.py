"""Findings: what reaches the Bench, what is held, and corrections."""

from __future__ import annotations

from jimmyhq.village.comms import benchlink, findings
from test_village_tools_fakes import (  # noqa: F401
    call,
    card_on_bench,
    decide,
    refused,
    v,
)

WHAT = "The donate button on https://example.com/give opens a page that says not found."
WHY = "Every visitor who tries to give this week is turned away."


def report(v, title, agent="ada", mode="work", **more):  # noqa: F811
    return call(v, agent, "report_finding", {"title": title, "what": WHAT, "why": WHY, **more},
                mode=mode)


TITLES = ["Donate button leads to a missing page", "Newsletter footer shows last year",
          "Volunteer form rejects valid postcodes", "Events calendar repeats the spring fair",
          "Contact address bounces every message", "Search box returns nothing for names",
          "Privacy notice links to an empty file", "Shop basket forgets items on reload"]


def test_a_finding_goes_on_the_bench_with_what_why_and_fix(v):  # noqa: F811
    call(v, "ada", "write_file", {"path": "notes/donate.md", "content": "what I saw"})
    d = report(v, TITLES[0], mode="explore", fix="Point the button at /give-now",
               files=["notes/donate.md"])
    card = benchlink.cards(v, "ada")[0]
    assert d.result == f"put on Sam's Bench as {card['card_id']}; you will see their answer."
    assert d.args_logged == {"title": TITLES[0]}
    on_bench = card_on_bench(v, card["card_id"])
    assert on_bench["kind"] == "village.finding" and on_bench["filed_by"] == "agent:ada"
    assert (on_bench["title"], on_bench["body"], on_bench["why"]) == (TITLES[0], WHAT, WHY)
    assert on_bench["details"] == [{"label": "What would fix it",
                                    "text": "Point the button at /give-now"}]
    assert on_bench["files"] and card["paths"] == ["notes/donate.md"]
    kept = v.agent_dir("ada") / "deliverables" / card["card_id"] / "notes" / "donate.md"
    assert kept.read_text(encoding="utf-8") == "what I saw"


def test_what_needs_forty_characters(v):  # noqa: F811
    e = refused(v, "ada", "report_finding", {"title": "Broken", "what": "The page is broken.",
                                             "why": WHY})
    assert e.rule == "finding_what"
    assert e.sentence == ("Nothing was put on the Bench: 'what' is 19 characters and needs at "
                          "least 40. Say what is wrong and exactly where you saw it (the "
                          "address, page or file), so Sam can see it too.")


def test_a_finding_with_a_wrong_day_count_is_sent_back_once(v):  # noqa: F811
    args = {"title": "Last call email goes too early", "why": WHY,
            "what": WHAT + " The send date is 1 January 2031, two days away."}
    e = refused(v, "ada", "report_finding", args)
    assert e.rule == "date_slip" and 'Your text says "two days away"' in e.sentence
    assert "put on Sam's Bench" in call(v, "ada", "report_finding", args).result


def test_a_finding_naming_a_missing_file_is_refused(v):  # noqa: F811
    e = refused(v, "ada", "report_finding", {"title": TITLES[0], "what": WHAT, "why": WHY,
                                             "files": ["notes/nope.md"]})
    assert e.rule == "file_missing" and e.sentence.startswith(
        "Nothing was put on the Bench: there is no file notes/nope.md.")


# --- the same finding again ----------------------------------------------------------------


def test_a_twin_of_an_open_finding_points_to_correct_finding(v):  # noqa: F811
    report(v, "Autumn appeal email is addressed to a deleted mailing list")
    cid = benchlink.cards(v, "ada")[0]["card_id"]
    e = refused(v, "ada", "report_finding", {
        "title": "The autumn appeal email (next week) is addressed to a deleted mailing list",
        "what": WHAT, "why": WHY})
    assert e.rule == "finding_twin"
    assert e.sentence.startswith(
        f"Nothing was put on the Bench: this looks like 1 finding you already reported, {cid} "
        "(\"Autumn appeal email is addressed to a deleted mailing list\"), which has been on Sam's "
        "Bench since ")
    assert e.sentence.endswith(f"If something about it changed, use correct_finding on {cid}.")


def test_a_twin_of_a_decided_finding_says_what_the_owner_said(v):  # noqa: F811
    report(v, "Autumn appeal email is addressed to a deleted mailing list")
    cid = benchlink.cards(v, "ada")[0]["card_id"]
    decide(v, cid, "approve", note="Known, it will be fixed before sending")
    v.later(60 * 30)
    assert benchlink.view(v, "ada") == []  # gone from the bench view, but not forgotten
    args = {"title": "Autumn appeal email still addressed to the deleted mailing list",
            "what": WHAT, "why": WHY}
    e = refused(v, "ada", "report_finding", args)
    assert e.rule == "finding_twin"
    assert "Sam noted it: \"Known, it will be fixed before sending\". Card ids never change." \
        in e.sentence
    assert e.sentence.endswith(f"say in it what changed since {cid}, or tell them with "
                               "message_owner.")
    # saying what changed since the old card lets it through
    args["what"] = WHAT + f" It has been sent since {cid} was noted."
    assert "put on Sam's Bench" in call(v, "ada", "report_finding", args).result


def test_twins_share_four_words_and_sixty_percent(v):  # noqa: F811
    report(v, "Donate button leads to a missing page")
    assert findings.finding_twin(v, "ada", "The donate button leads to the missing page")
    assert findings.finding_twin(v, "ada", "Donate button color is off") is None
    assert findings.finding_twin(v, "ada", "Donate button page") is None  # three shared words
    report(v, "Spring fair listing shows wrong venue street town postcode map and date")
    # four shared words, but under sixty percent of the shorter title
    assert findings.finding_twin(v, "ada", "Autumn fair listing shows wrong opening hours "
                                           "for visitors") is None
    assert findings.finding_twin(v, "bo", "Donate button leads to a missing page") is None


# --- room -----------------------------------------------------------------------------------


def test_the_days_room_moves_with_the_record_between_one_and_the_most():
    assert findings.finding_room(3, 6, 0, 0) == 3
    assert findings.finding_room(3, 6, 2, 0) == 5
    assert findings.finding_room(3, 6, 9, 0) == 6
    assert findings.finding_room(3, 6, 0, 1) == 2
    assert findings.finding_room(3, 6, 0, 9) == 1
    assert findings.finding_room(3, 6, 2, 2) == 3


def test_past_the_days_room_a_finding_is_held_and_goes_by_itself(v):  # noqa: F811
    for title in TITLES[:3]:
        report(v, title)
    assert findings.why_no_room(v, "ada") == ("finding_room",
                                              "today's 3 findings are on Sam's Bench already")
    before = v.world.agents["ada"].needs["frustration"]
    d = report(v, TITLES[3])
    assert d.result == ("today's 3 findings are on Sam's Bench already, so this one is held "
                        "(number 1). It goes on the Bench by itself when there is room; if it "
                        "stops being true, report it again with the same title and what "
                        "changed.")
    assert v.world.agents["ada"].needs["frustration"] <= before
    assert len(benchlink.cards(v, "ada")) == 3
    assert [q["title"] for q in findings.queued(v, "ada")] == [TITLES[3]]
    d = report(v, "The events calendar repeats the spring fair")
    assert "held (number 1, replacing the held one with this title)" in d.result
    assert len(findings.queued(v, "ada")) == 1
    assert findings.flush(v, "ada") == []
    v.later(60 * 24)
    sent = findings.flush(v, "ada")
    assert [c["title"] for c in sent] == ["The events calendar repeats the spring fair"]
    assert findings.queued(v, "ada") == []


def test_noted_findings_earn_room_and_dismissed_ones_lose_it(v):  # noqa: F811
    for title in TITLES[:3]:
        report(v, title)
    ids = [c["card_id"] for c in benchlink.cards(v, "ada")]
    decide(v, ids[0], "approve")
    decide(v, ids[1], "approve")
    assert findings.record(v, "ada") == (2, 0) and findings.allowed(v, "ada") == 5
    decide(v, ids[2], "no", reason="Known and harmless")
    assert findings.record(v, "ada") == (2, 1) and findings.allowed(v, "ada") == 4
    assert v.log.last(agent="ada", kinds=["bench_decided"]).data["decision"] == "denied"
    assert "put on Sam's Bench" in report(v, TITLES[3]).result  # the fourth of the day
    v.later(60 * 24 * 15)
    assert findings.record(v, "ada") == (0, 0) and findings.allowed(v, "ada") == 3


def test_new_findings_wait_while_five_are_unread(v):  # noqa: F811
    v.core.settings.set("village.comms.findings_per_day", 20, actor="owner")
    v.core.settings.set("village.comms.findings_max_per_day", 20, actor="owner")
    for title in TITLES[:5]:
        report(v, title)
    assert findings.waiting(v, "ada") == 5
    assert findings.why_no_room(v, "ada") == (
        "findings_open_hold", "5 of your findings are still waiting for Sam to read them")
    d = report(v, TITLES[5])
    assert d.result.startswith("5 of your findings are still waiting for Sam to read them, so "
                               "this one is held (number 1)")
    decide(v, benchlink.cards(v, "ada")[0]["card_id"], "not_yet", note="Which page exactly?")
    assert findings.waiting(v, "ada") == 4  # asked for more: the next move is the agent's
    assert [c["title"] for c in findings.flush(v, "ada")] == [TITLES[5]]


def test_the_held_queue_takes_as_many_as_a_days_most(v):  # noqa: F811
    v.core.settings.set("village.comms.findings_per_day", 1, actor="owner")
    v.core.settings.set("village.comms.findings_max_per_day", 2, actor="owner")
    for title in TITLES[:3]:
        report(v, title)
    e = refused(v, "ada", "report_finding", {"title": TITLES[3], "what": WHAT, "why": WHY})
    assert e.rule == "findings_held_full" and not e.frustrate
    assert e.sentence == ("Nothing was held: you already have 2 findings held for Sam, as many "
                          "as they take in a day (2). Keep this one in your notes, or report a "
                          "held one again by its title to replace it.")


def test_one_urgent_finding_a_day_skips_the_wait(v):  # noqa: F811
    for title in TITLES[:3]:
        report(v, title)
    d = report(v, TITLES[3], urgent=True)
    card = benchlink.cards(v, "ada")[-1]
    assert d.result == (f"put on Sam's Bench as {card['card_id']}, marked urgent; you will see "
                        "their answer.")
    assert card["urgent"] and card["title"] == "Urgent: " + TITLES[3]
    assert card["first_title"] == TITLES[3]
    assert card_on_bench(v, card["card_id"])["title"] == "Urgent: " + TITLES[3]
    assert findings.urgent_today(v, "ada") == 1 and not findings.urgent_allowed(v, "ada")
    d = report(v, TITLES[4], urgent=True)
    assert "so this one is held (number 1)" in d.result
    assert d.result.endswith("It was not sent as urgent: today's urgent findings are used up, "
                             "so it waits its turn like any other.")
    v.later(60 * 24)
    assert findings.urgent_allowed(v, "ada")


def test_the_same_open_title_is_not_filed_or_held_twice(v):  # noqa: F811
    report(v, "Odd")
    e = refused(v, "ada", "report_finding", {"title": "odd", "what": WHAT, "why": WHY})
    assert e.rule == "finding_duplicate" and "1 finding with that title is already on Sam's " \
        "Bench" in e.sentence


# --- correct_finding --------------------------------------------------------------------------

FIX = "The careers link is there after all: it is under About. I opened the menu to check."


def test_a_correction_goes_on_top_and_what_was_first_sent_stays_below(v):  # noqa: F811
    report(v, "Careers page has no menu link")
    cid = benchlink.cards(v, "ada")[0]["card_id"]
    d = call(v, "ada", "correct_finding", {"card": cid.lower(), "correction": FIX},
             mode="explore")
    assert d.result == (f"{cid} now opens with your correction; Sam reads it first. Its title is "
                        "now \"Corrected: Careers page has no menu link\". If the title itself "
                        "is wrong, correct it once more with a new title")
    on_bench = card_on_bench(v, cid)
    assert on_bench["title"] == "Corrected: Careers page has no menu link"
    head, rest = on_bench["body"].split("\n\n", 1)
    assert head.startswith("CORRECTION (") and head.endswith(f"): {FIX}")
    assert rest == f"--- as first sent ---\n{WHAT}"

    second = "Correction to the correction: the link is in the footer, not the menu."
    d = call(v, "ada", "correct_finding", {"card": cid, "correction": second,
                                           "title": "Careers link is only in the footer"})
    assert "This replaced your earlier correction; the card holds only the newest one" in d.result
    on_bench = card_on_bench(v, cid)
    assert on_bench["title"] == "Corrected: Careers link is only in the footer"
    assert FIX not in on_bench["body"] and second in on_bench["body"]
    assert on_bench["body"].endswith(
        f"--- as first sent: Careers page has no menu link ---\n{WHAT}")
    assert benchlink.get(v, cid)["first_title"] == "Careers page has no menu link"


def test_a_correction_is_twenty_to_1500_characters(v):  # noqa: F811
    report(v, "Careers page has no menu link")
    cid = benchlink.cards(v, "ada")[0]["card_id"]
    for text, n in (("It was wrong.", 13), ("x" * 1501, 1501)):
        e = refused(v, "ada", "correct_finding", {"card": cid, "correction": text})
        assert e.rule == "correction_size"
        assert e.sentence == (f"Nothing was corrected: a correction is 20 to 1500 characters "
                              f"and yours is {n}. Say what was wrong, what is true instead and "
                              "how you checked, in a few sentences.")


def test_only_the_agents_own_open_finding_or_hand_over_can_be_corrected(v):  # noqa: F811
    report(v, "Careers page has no menu link")
    cid = benchlink.cards(v, "ada")[0]["card_id"]
    e = refused(v, "bo", "correct_finding", {"card": cid, "correction": FIX})
    assert e.rule == "own_open_card"
    assert e.sentence == (f"Nothing was corrected: {cid} is not 1 of your findings or "
                          "hand-overs. Use the id of one of your own, as shown in your bench "
                          "view.")
    e = refused(v, "ada", "correct_finding", {"card": "evt-12", "correction": FIX})
    assert e.sentence == (f"Nothing was corrected: there is no card EVT-12. You have 1 open "
                          f"findings and hand-overs: {cid}. Use one of those ids, as shown in "
                          "your bench view.")
    call(v, "ada", "suggest_priority", {"title": "A priority", "why": "w"})
    other = benchlink.cards(v, "ada")[1]["card_id"]
    assert refused(v, "ada", "correct_finding", {"card": other, "correction": FIX}).rule == \
        "own_open_card"
    decide(v, cid, "approve")
    e = refused(v, "ada", "correct_finding", {"card": cid, "correction": FIX})
    assert e.sentence == (f"Nothing was corrected: Sam already decided {cid}, so the card is "
                          "closed. Tell them what changed with message_owner (1 message).")


def test_a_correction_answers_a_request_for_more_and_returns_the_card_to_the_owner(v):  # noqa: F811
    report(v, "Careers page has no menu link")
    cid = benchlink.cards(v, "ada")[0]["card_id"]
    decide(v, cid, "not_yet", note="Which menu?")
    assert card_on_bench(v, cid)["lane"] == "waiting"
    call(v, "ada", "correct_finding", {"card": cid, "correction": FIX})
    assert card_on_bench(v, cid)["lane"] == "needs"
    assert benchlink.get(v, cid)["status"] == "open"
