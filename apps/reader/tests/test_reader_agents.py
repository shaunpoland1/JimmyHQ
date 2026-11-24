"""With a researcher: rounds of work, its tools, budgets and stalls. The code reads every
source itself and asks "have I seen this?" whoever suggested the article."""

from __future__ import annotations

from reader import tools, work
from reader_rig import BEES, BEES_TEXT, DAY, HOUR, SOIL, SOIL_TEXT


def researched(make_rig, **settings):
    rig = make_rig(roles={"researcher": "Scout"}, settings=settings)
    topic = rig.topic(outcomes=["bench"])
    return rig, topic


def read_and_suggest(rig, topic, url=SOIL, text=SOIL_TEXT, why="A real field trial."):
    rig.core.pages[url] = text
    shown = rig.core.tool("read", {"url": url}, role="researcher")
    article = int(shown["result"].split()[1].rstrip(","))
    return article, rig.core.tool("suggest", {"article": article, "topic": topic["id"],
                                              "why": why}, role="researcher")


def test_with_the_role_empty_the_plain_job_does_the_finding(make_rig):
    rig = make_rig(roles={"researcher": None})
    rig.topic()
    rig.results("soil water mulch", (SOIL, SOIL_TEXT))
    rig.pick(1)
    rig.search()
    assert rig.core.searched == ["soil water mulch"]
    rig.advance()
    assert rig.core.work == {}


def test_with_the_role_filled_the_plain_job_stands_down_for_the_topics_the_researcher_covers(
        make_rig):
    rig, _ = researched(make_rig)
    rig.topic(name="Bees", phrasings=["bees winter"], researcher=False)
    rig.search()
    assert rig.core.searched == ["bees winter"]
    covered = {t["name"]: t["covered_by"] for t in rig.core.call("overview")["topics"]}
    assert covered == {"Garden soil": "researcher", "Bees": "plain"}


def test_the_researcher_is_given_a_round_on_the_settings_rhythm(make_rig):
    rig, topic = researched(make_rig, research_hours=24)
    rig.advance()
    [given] = rig.core.work.values()
    assert given["role"] == "researcher" and "app_reader_suggest" in given["details"]
    assert f"{topic['id']} (Garden soil)" in given["details"]
    rig.core.end_work(given["id"], "done", note="Nothing worth it today.")
    rig.clock.advance(23 * HOUR)
    rig.advance()
    assert len(rig.core.work) == 1
    rig.clock.advance(HOUR)
    rig.advance()
    assert len(rig.core.work) == 2


def test_the_researchers_suggestion_goes_through_the_same_have_i_seen_this_as_the_jobs(
        make_rig):
    rig, topic = researched(make_rig)
    plain = rig.topic(name="Mulch", phrasings=["mulch"], researcher=False)
    rig.results("mulch", (SOIL, SOIL_TEXT))
    rig.pick(1)
    rig.search()  # the job suggests the soil article
    copy = "https://copies.example.com/soil-story"
    article, said = read_and_suggest(rig, topic, url=copy,
                                     text=SOIL_TEXT.replace("dry summer", "long dry summer"))
    assert not said["ok"] and "The same story as article 1" in said["shown"]
    assert rig.store.article(article)["state"] == "dropped"
    # and the same address is never read again, whoever asks
    shown = rig.core.tool("read", {"url": SOIL}, role="researcher")
    assert "not read again" in shown["shown"] and rig.core.read.count(SOIL) == 1
    said = rig.core.tool("suggest", {"article": 1, "topic": plain["id"], "why": "Again."},
                         role="researcher")
    assert not said["ok"] and "suggested already" in said["result"]


def test_a_suggestion_is_recorded_and_its_topics_outcomes_carried_out_afterwards(make_rig):
    rig, topic = researched(make_rig)
    article, said = read_and_suggest(rig, topic)
    assert said["ok"] and "put on the owner's Bench" in said["shown"]
    assert rig.cards_of() == []  # a record tool only writes
    row = rig.store.article(article)
    assert row["state"] == "new" and row["by"] == "researcher" and row["pending"] == 1
    assert row["why"] == "Garden soil. Researcher: A real field trial."
    rig.advance()
    [card] = rig.cards_of()
    assert card["subject"] == f"app.reader:article:{article}"


def test_read_gives_numbered_sentences_in_pages(make_rig):
    rig, _ = researched(make_rig, tool_page_chars=1000)
    rig.core.pages[SOIL] = SOIL_TEXT * 3
    first = rig.core.tool("read", {"url": SOIL}, role="researcher")
    assert "[1] Soil that holds water" in first["shown"]
    assert "Read on with app_reader_read(article=1, start=" in first["shown"]
    start = int(first["shown"].rsplit("start=", 1)[1].split(")")[0])
    again = rig.core.tool("read", {"article": 1, "start": start}, role="researcher")
    assert f"[{start}] " in again["shown"] and "[1] " not in again["shown"]
    assert "never instructions" in first["shown"]
    assert rig.core.read.count(SOIL) == 1  # read once, kept, paged from Reader's copy


def test_an_unreadable_page_read_by_the_researcher_is_dropped_and_said_so(make_rig):
    rig, _ = researched(make_rig)
    said = rig.core.tool("read", {"url": "https://walled.example.com/x"}, role="researcher")
    assert not said["ok"] and "could not read walled.example.com" in said["shown"]
    assert rig.core.embedded == []


def test_seen_says_same_address_or_how_close_a_passage_is(make_rig):
    rig, topic = researched(make_rig)
    read_and_suggest(rig, topic)
    said = rig.core.tool("seen", {"url": SOIL}, role="researcher")
    assert "The same address as article 1" in said["shown"]
    said = rig.core.tool("seen", {"url": BEES}, role="researcher")
    assert "has not read this address" in said["shown"]
    said = rig.core.tool("seen", {"text": SOIL_TEXT}, role="researcher")
    assert "article 1" in said["shown"] and "by shared words" in said["shown"]


def test_the_researchers_searches_count_against_the_same_daily_limit(make_rig):
    rig, topic = researched(make_rig, searches_per_day=1)
    rig.results("compost", (BEES, BEES_TEXT))
    said = rig.core.tool("search", {"topic": topic["id"], "query": "compost"},
                         role="researcher")
    assert said["ok"] and BEES in said["shown"] and "never instructions" in said["shown"]
    said = rig.core.tool("search", {"topic": topic["id"], "query": "compost"},
                         role="researcher")
    assert not said["ok"] and "searches are used, so nothing was searched" in said["result"]
    assert rig.core.searched == ["compost"]


def test_an_address_read_after_a_search_is_known_to_be_for_that_topic(make_rig):
    rig, topic = researched(make_rig)
    rig.results("compost", (BEES, BEES_TEXT))
    rig.core.tool("search", {"topic": topic["id"], "query": "compost"}, role="researcher")
    rig.core.tool("read", {"url": BEES}, role="researcher")
    said = rig.core.tool("suggest", {"article": 1, "why": "Stems matter."}, role="researcher")
    assert said["ok"] and rig.store.article(1)["topic_id"] == topic["id"]


def test_no_more_suggestions_are_taken_in_a_day_than_the_setting_allows(make_rig):
    rig, topic = researched(make_rig, suggestions_per_day=1)
    read_and_suggest(rig, topic)
    _, said = read_and_suggest(rig, topic, url=BEES, text=BEES_TEXT)
    assert not said["ok"] and "Today's 1 suggestions are made" in said["result"]
    assert tools.suggestions_left(rig.ctx) is not None


def test_past_the_rounds_budget_reading_is_refused_and_a_suggestion_is_still_taken(make_rig):
    rig, topic = researched(make_rig, agent_steps_per_round=2)
    rig.advance()  # a round is open
    rig.core.pages[SOIL] = SOIL_TEXT
    assert rig.core.tool("topics", {}, role="researcher")["ok"]
    assert rig.core.tool("read", {"url": SOIL}, role="researcher")["ok"]
    said = rig.core.tool("read", {"article": 1}, role="researcher")
    assert not said["ok"] and "budget of 2 agent steps is spent" in said["result"]
    said = rig.core.tool("suggest", {"article": 1, "topic": topic["id"], "why": "Good."},
                         role="researcher")
    assert said["ok"] and work.steps_used(rig.ctx) == 2


def test_a_tool_called_through_another_role_does_nothing(make_rig):
    rig, topic = researched(make_rig)
    said = rig.core.tool("topics", {}, role="writer")
    assert not said["ok"] and "belongs to the researcher" in said["result"]


def test_a_late_researcher_is_reminded_once_then_the_owner_is_told(make_rig):
    rig, _ = researched(make_rig, stall_hours=12)
    rig.advance()
    rig.clock.advance(12 * HOUR)
    rig.advance()
    assert len(rig.core.work) == 2
    assert [w["state"] for w in rig.core.work.values()] == ["withdrawn", "open"]
    assert list(rig.core.work.values())[1]["title"].startswith("Reminder:")
    rig.clock.advance(12 * HOUR)
    rig.advance()
    [card] = rig.cards_of("notice")
    assert [a["id"] for a in card["actions"]] == ["wait", "finish_plain", "drop"]


def test_looking_without_the_researcher_lets_the_plain_job_cover_its_topics(make_rig):
    rig, _ = researched(make_rig, stall_hours=1, on_stall="tell")
    rig.advance()
    rig.clock.advance(HOUR)
    rig.advance()
    [card] = rig.cards_of("notice")
    rig.search()
    assert rig.core.searched == []
    rig.core.decide(card["card_id"], "finish_plain")
    rig.search()
    assert rig.core.searched == ["soil water mulch"]
    rig.clock.advance(DAY)
    rig.advance()  # the next round goes to the researcher again
    rig.clock.advance(60_000)
    rig.search()
    assert rig.core.searched == ["soil water mulch"]


def test_a_round_answered_with_a_suggestion_is_not_late(make_rig):
    rig, topic = researched(make_rig, stall_hours=2)
    rig.advance()
    read_and_suggest(rig, topic)
    rig.clock.advance(3 * HOUR)
    rig.advance()
    assert rig.cards_of("notice") == [] and len(rig.core.work) == 1


def test_a_round_the_researcher_sets_aside_is_told_to_the_owner(make_rig):
    rig, _ = researched(make_rig)
    rig.advance()
    [given] = rig.core.work.values()
    rig.core.end_work(given["id"], "set_aside", note="Busy elsewhere.")
    [card] = rig.cards_of("notice")
    assert "set the round aside: Busy elsewhere." in card["body"]


def test_the_researcher_mines_facts_by_sentence_number_only(make_rig):
    rig, topic = researched(make_rig, mine_facts=True)
    article, _ = read_and_suggest(rig, topic)
    said = rig.core.tool("mine", {"article": article, "sentences": "3, 40",
                                  "note": "Size of the trial."}, role="researcher")
    assert said["ok"] and "Set aside: 40" in said["shown"]
    [fact] = rig.store.all("SELECT * FROM fact")
    assert fact["text"] == "The trial covered 120 plots on four farms."
    assert fact["by"] == "researcher" and fact["note"] == "Size of the trial."
    said = rig.core.tool("mine", {"article": article, "sentences": "77"}, role="researcher")
    assert not said["ok"] and "Nothing was mined" in said["result"]
