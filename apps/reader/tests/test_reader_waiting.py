"""The number beside Reader in the sidebar: the program says it, so it is right without the
page open, and it is the number of articles in triage that the page lists."""

from __future__ import annotations

from reader import screen
from reader_rig import BEES, BEES_TEXT, SOIL, SOIL_TEXT


def find_two(rig) -> list[dict]:
    rig.topic()
    rig.results("soil water mulch", (SOIL, SOIL_TEXT), (BEES, BEES_TEXT))
    rig.pick(1, 2)
    rig.search()
    return rig.articles("new")


def test_the_program_says_zero_when_it_starts(rig):
    assert rig.core.counts == [0]
    assert rig.core.call("overview", {})["waiting"] == 0


def test_articles_suggested_by_a_job_are_counted_with_no_page_open(rig):
    found = find_two(rig)
    assert len(found) == 2
    assert rig.core.count == 2  # said by the program after the search job ended


def test_the_program_says_the_number_the_page_lists_in_triage(rig):
    find_two(rig)
    seen = rig.core.call("overview", {})
    assert rig.core.count == screen.waiting(rig.ctx) == seen["waiting"] == len(seen["triage"])


def test_choosing_an_article_takes_it_off_the_count_at_once(rig):
    first, _second = find_two(rig)
    rig.core.call("article.choose", {"id": first["id"], "action": "skip"})
    assert rig.core.count == 1


def test_the_count_is_held_to_the_rows_the_page_lists(make_rig):
    rig = make_rig(settings={"screen_rows": 20})
    rig.topic()
    for n in range(25):
        rig.store.insert("article", {
            "url": f"https://news.example.com/{n}", "final_url": f"https://news.example.com/{n}",
            "host": "news.example.com", "title": f"Article {n}", "state": "new",
            "created": n, "updated": n, "suggested": n})
    assert screen.waiting(rig.ctx) == 20 == len(rig.core.call("overview", {})["triage"])
