"""The number beside Posts in the sidebar: the program says it, so it is right without the
page open, and it is the number Today's lines add up to."""

from __future__ import annotations

import json

import pytest
from posts import from_reader, waiting
from posts_rig import APP_ROOT

CASES = json.loads((APP_ROOT / "tests" / "fixtures" / "waiting_cases.json").read_text("utf-8"))
BODY = "Solar roofs: 1,240 homes added panels last year, 38% more than the year before."


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_count_over_the_pages_own_list_is_what_the_pages_script_counts(case):
    assert waiting.in_overview(case["data"]) == case["waiting"]


def at(rig) -> tuple[int, int, int]:
    """What the program said last, what the records say, and what the page's list says."""
    seen = rig.core.call("overview", {})
    return rig.core.count, waiting.count(rig.ctx), waiting.in_overview(seen)


def test_the_program_says_zero_when_it_starts_and_the_overview_says_it_too(rig):
    assert rig.core.counts == [0]
    assert rig.core.call("overview", {})["waiting"] == 0


def test_a_draft_waiting_for_the_owner_is_counted_as_soon_as_the_job_that_made_it_ends(rig):
    page = rig.page()
    rig.through(page, BODY)
    assert rig.core.count == 1  # said by the program after its job, with no page open
    assert at(rig) == (1, 1, 1)


def test_deciding_a_draft_from_the_page_takes_it_off_the_count_at_once(rig):
    page = rig.page()
    piece = rig.through(page, BODY)
    post = rig.store.post_of(piece["id"])
    rig.core.call("post.decline", {"id": post["id"], "reason": "Not for us."})
    assert rig.core.count == 0 == waiting.count(rig.ctx)


def test_the_number_is_said_only_when_it_changes(rig):
    page = rig.page()
    rig.through(page, BODY)
    rig.core.call("overview", {})
    rig.core.call("pages.list", {})
    assert all(a != b for a, b in zip(rig.core.counts, rig.core.counts[1:], strict=False))


def test_every_kind_of_line_on_today_is_counted_once_by_the_program_as_by_the_page(rig):
    page = rig.page()
    rig.through(page, BODY)  # a draft to decide
    store = rig.store
    for n, (state, more) in enumerate((("unsure", ""), ("missing", ""),
                                       ("booked", ", out_of_step = 1"),
                                       ("queued", ", slot_ms = NULL"),
                                       ("queued", ", slot_ms = 1900000000000")), start=1):
        piece_id = store.insert("piece", {"page_id": page["id"], "way": "link", "topic": f"t{n}",
                                          "state": "closed", "created": n, "updated": n,
                                          "error": "", "origin": ""})
        post_id = store.insert("post", {"piece_id": piece_id, "page_id": page["id"],
                                        "title": "", "body": "x", "state": "draft",
                                        "revision": 1, "created": n, "updated": n})
        store.conn.execute(f"UPDATE post SET state = ?{more} WHERE id = ?", (state, post_id))
    for n, state in enumerate(("stalled", "stopped_source", "held_overlap", "writing"), 10):
        store.insert("piece", {"page_id": page["id"], "way": "link", "topic": f"t{n}",
                               "state": state, "created": n, "updated": n, "error": "",
                               "origin": ""})
    store.conn.commit()
    # draft 1, unsure 1, missing 1 + out of step 1, untimed 1, stuck 3 (not the one writing)
    assert rig.core.call("overview", {})["waiting"] == 1 + 1 + 2 + 1 + 3
    assert at(rig) == (8, 8, 8)


def test_ideas_from_reader_count_up_to_five_and_a_broken_handover_counts_one_more(make_rig):
    rig = make_rig(reads_reader=True)
    rig.page()
    for n in range(1, 8):
        rig.store.insert("idea", {"seq": n, "url": f"https://news.example.com/{n}",
                                  "title": f"Idea {n}", "topic": "Solar", "why": "",
                                  "state": "new", "created": n})
    assert waiting.count(rig.ctx) == 5
    rig.store.set_meta(from_reader.STATUS, {"ok": False, "code": "internal",
                                            "said": "Reader did not answer.",
                                            "heard_at": 1, "failed_at": rig.clock()})
    assert waiting.count(rig.ctx) == 6 == waiting.in_overview(rig.core.call("overview", {}))


def test_a_first_run_with_no_brand_is_not_counted_as_something_waiting(rig):
    seen = rig.core.call("overview", {})
    assert seen["pages"] == [] and seen["waiting"] == 0
