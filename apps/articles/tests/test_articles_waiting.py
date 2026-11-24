"""The number beside Articles in the sidebar: the program says it, so it is right without
the page open, and it is the number Today's lines add up to."""

from __future__ import annotations

import json

import pytest
from articles import waiting
from articles_rig import APP_ROOT

CASES = json.loads((APP_ROOT / "tests" / "fixtures" / "waiting_cases.json").read_text("utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_count_over_the_pages_own_list_is_what_the_pages_script_counts(case):
    assert waiting.in_overview(case["data"]) == case["waiting"]


def at(rig) -> tuple[int, int, int]:
    """What the program said last, what the records say, and what the page's list says."""
    seen = rig.core.call("overview", {})
    return rig.core.count, waiting.count(rig.ctx), waiting.in_overview(seen)


def piece_in(rig, site: dict, state: str, n: int) -> int:
    return rig.store.insert("piece", {"site_id": site["id"], "way": "topic", "topic": f"t{n}",
                                      "state": state, "created": n, "updated": n})


def test_the_program_says_zero_when_it_starts_and_the_overview_says_it_too(rig):
    assert rig.core.counts == [0]
    assert rig.core.call("overview", {})["waiting"] == 0


def test_an_article_ready_for_the_owner_is_counted_as_soon_as_the_job_that_made_it_ends(rig):
    rig.to_card()
    assert rig.core.count == 1  # said by the program after its job, with no page open
    assert at(rig) == (1, 1, 1)


def test_deciding_an_article_from_the_page_takes_it_off_the_count_at_once(rig):
    piece = rig.to_card()
    rig.core.call("article.decline", {"id": piece["id"], "reason": "Not for us."})
    assert rig.core.count == 0 == waiting.count(rig.ctx)


def test_the_number_is_said_only_when_it_changes(rig):
    rig.to_card()
    rig.core.call("overview", {})
    rig.core.call("sites.list", {})
    assert all(a != b for a, b in zip(rig.core.counts, rig.core.counts[1:], strict=False))


def test_every_kind_of_line_on_today_is_counted_once_by_the_program_as_by_the_page(rig):
    site = rig.site()
    for n, state in enumerate(("ready", "ready", "unsure", "held_coverage", "stopped_research",
                               "stalled", "approved", "sent", "publishing", "drafting",
                               "published", "dropped"), 1):
        piece_in(rig, site, state, n)
    assert at(rig) == (8, 8, 8)  # ready 2, then one for each of the six that wait on him
    assert rig.core.call("overview", {})["waiting"] == 8


def test_waiting_ideas_and_unread_replies_count_one_line_each(rig):
    rig.site()
    rig.core.call("replies.paste", {"text": "I would love to read about saving money with a "
                                            "small plot of my own.\n---\nComposting in a "
                                            "flat, please, with a tiny balcony."})
    assert at(rig)[1:] == (1, 1)  # replies not looked at yet: one line
    rig.ctx.replies.db.execute("UPDATE reply SET used = 1")
    rig.ctx.replies.db.execute("INSERT INTO idea (created, title) VALUES (1, 'A'), (2, 'B')")
    rig.ctx.replies.db.commit()
    assert at(rig)[1:] == (1, 1)  # two ideas waiting: still one line
    rig.ctx.replies.db.execute("UPDATE idea SET status = 'dismissed'")
    rig.ctx.replies.db.commit()
    assert at(rig)[1:] == (0, 0)


def test_a_first_run_with_no_site_is_not_counted_as_something_waiting(rig):
    seen = rig.core.call("overview", {})
    assert seen["sites"] == [] and seen["waiting"] == 0
