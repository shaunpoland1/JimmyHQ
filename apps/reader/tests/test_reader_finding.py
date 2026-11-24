"""Finding with no agent: search, read, check, rank by number, suggest."""

from __future__ import annotations

from reader import finding, rank
from reader_rig import BEES, BEES_TEXT, DAY, SOIL, SOIL_TEXT, TOMATO, TOMATO_TEXT


def test_with_no_topics_nothing_is_searched(rig):
    said = rig.search()
    assert "No topics" in said
    assert rig.core.searched == [] and rig.core.read == [] and rig.core.asked == []


def test_a_paused_topic_is_not_searched(rig):
    topic = rig.topic()
    rig.core.call("topics.pause", {"id": topic["id"]})
    rig.search()
    assert rig.core.searched == []


def test_a_found_article_is_read_ranked_by_number_and_suggested_with_its_why(rig):
    rig.topic()
    rig.results("soil water mulch", (SOIL, SOIL_TEXT), (BEES, BEES_TEXT))
    rig.pick(1, why="A field trial with real numbers.")
    said = rig.search()
    assert "Suggested 1" in said
    [article] = rig.articles("new")
    assert article["url"] == SOIL
    assert article["why"] == ("Garden soil (search “soil water mulch”). Ranker: A "
                              "field trial with real numbers.")
    assert article["sentences"][0] == "Soil that holds water through a dry summer"
    [passed] = rig.articles("passed")
    assert passed["url"] == BEES and passed["text"] == ""  # not picked: text gone at once
    assert rig.core.read == [SOIL, BEES]


def test_a_models_pick_is_a_number_from_the_list_it_was_given_and_a_link_it_invents_goes_nowhere(
        rig):
    rig.topic()
    rig.results("soil water mulch", (SOIL, SOIL_TEXT), (BEES, BEES_TEXT))
    rig.core.replies.append({"picks": [
        {"number": 7, "why": "Not on the list."},
        {"number": 2, "why": "Read it at https://made-up.example.net/win", "url":
         "https://made-up.example.net/win"},
        {"number": "1", "why": "A string, not a number."},
        {"number": 2, "why": "The same one twice."}]})
    rig.search()
    [article] = rig.articles("new")
    assert article["url"] == BEES
    every = rig.articles()
    assert all("made-up.example.net" not in (a["url"] + a["final_url"]) for a in every)
    assert "made-up.example.net" not in rig.core.read
    assert len(every) == 2


def test_picks_are_taken_only_as_numbers_on_the_list_once_each_and_no_more_than_asked():
    data = {"picks": [{"number": 2, "why": "a"}, {"number": 2, "why": "again"},
                      {"number": 0, "why": "zero"}, {"number": True, "why": "bool"},
                      {"number": 3, "why": "b"}, {"number": 1, "why": "past the limit"}]}
    picks, aside = rank.take_picks(data, 3, 2)
    assert [(p.index, p.why) for p in picks] == [(1, "a"), (2, "b")]
    assert aside == 4
    assert rank.take_picks("not an object", 3, 2) == ([], 0)


def test_an_unreadable_article_is_dropped_and_nothing_is_spent_on_it(rig):
    rig.topic()
    rig.results("soil water mulch", (SOIL, SOIL_TEXT))
    rig.core.search_results["soil water mulch"].append(
        {"url": "https://walled.example.com/story", "title": "Behind a wall"})
    short = "https://thin.example.com/teaser"
    rig.core.search_results["soil water mulch"].append({"url": short, "title": "A teaser"})
    rig.core.pages[short] = "Subscribe to read more."
    rig.pick(1)
    rig.search()
    dropped = {a["url"]: a for a in rig.articles("dropped")}
    assert "could not read walled.example.com" in dropped["https://walled.example.com/story"][
        "said"]
    assert "only 23 characters" in dropped[short]["said"]
    assert all("nothing was spent" in a["said"] for a in dropped.values())
    embedded = " ".join(" ".join(batch) for batch in rig.core.embedded)
    assert "Subscribe" not in embedded and "teaser" not in embedded.lower()
    [asked] = rig.core.asked
    assert "Subscribe" not in " ".join(asked["untrusted"])
    assert "[2]" not in asked["untrusted"][0]  # only the readable article was ranked


def test_the_same_address_is_never_read_twice(rig):
    rig.topic()
    rig.results("soil water mulch", (SOIL, SOIL_TEXT))
    rig.pick(1)
    rig.search()
    rig.clock.advance(DAY)
    rig.search()
    assert rig.core.read == [SOIL]
    assert len(rig.core.asked) == 1  # nothing new, so the ranker was not asked again


def test_an_article_already_seen_at_another_address_is_not_suggested_twice(rig):
    rig.topic()
    rig.results("soil water mulch", (SOIL, SOIL_TEXT))
    rig.pick(1)
    rig.search()
    copy = "https://copies.example.com/soil-story"
    rig.clock.advance(DAY)
    rig.results("soil water mulch", (copy, SOIL_TEXT.replace("dry summer", "dry, hot summer")))
    rig.search()
    [dropped] = rig.articles("dropped")
    assert dropped["url"] == copy
    assert dropped["said"].startswith("The same story as article 1")
    assert "by meaning" in dropped["said"]
    assert len(rig.articles("new")) == 1
    assert len(rig.core.asked) == 1  # the copy was never ranked


def test_without_an_embeddings_model_the_same_story_is_found_by_shared_words_and_says_so(rig):
    from reader_rig import no_embeddings

    rig.core.embedder = no_embeddings
    rig.topic()
    rig.results("soil water mulch", (SOIL, SOIL_TEXT))
    rig.pick(1)
    rig.search()
    rig.clock.advance(DAY)
    rig.results("soil water mulch", ("https://copies.example.com/soil", SOIL_TEXT))
    rig.search()
    [dropped] = rig.articles("dropped")
    assert "by shared words" in dropped["said"]
    assert "no embeddings model answered" in dropped["seen"]["note"]


def test_an_article_close_to_one_already_suggested_is_suggested_and_says_how_close(make_rig):
    rig = make_rig(settings={"similar_close": 0.5, "similar_same": 0.99})
    rig.topic()
    rig.results("soil water mulch", (SOIL, SOIL_TEXT))
    rig.pick(1)
    rig.search()
    rig.clock.advance(DAY)
    near = SOIL_TEXT.split("\n\n")[0] + "\n\n" + TOMATO_TEXT.split("\n\n", 1)[1]
    rig.results("soil water mulch", ("https://other.example.com/soil", near))
    rig.pick(1, why="Another angle.")
    rig.search()
    new = rig.articles("new")
    assert len(new) == 2
    assert "Close to article 1" in new[1]["why"] and "% alike by meaning" in new[1]["why"]
    assert new[1]["seen"]["status"] == "close"


def test_past_the_days_searches_nothing_more_is_searched(make_rig):
    rig = make_rig(settings={"searches_per_day": 2})
    for name in ("Garden soil", "Bees", "Tomatoes"):
        rig.topic(name=name, phrasings=[f"{name} news"])
    rig.search()
    assert len(rig.core.searched) == 2
    rig.clock.advance(60_000)
    said = rig.search()
    assert len(rig.core.searched) == 2 and "searches are used" in said
    rig.clock.advance(DAY)
    rig.search()
    assert len(rig.core.searched) == 4  # a new day


def test_the_days_searches_are_counted_on_the_owners_calendar(make_rig):
    rig = make_rig(settings={"searches_per_day": 1}, timezone="Pacific/Auckland")
    rig.topic()
    assert finding.searches_left(rig.ctx) == 1
    rig.search()
    assert finding.searches_left(rig.ctx) == 0


def test_a_search_that_could_not_run_is_not_counted(rig):
    rig.topic()
    rig.core.web_on = False
    said = rig.search()
    assert "web lookup switched off" in said
    assert finding.searches_today(rig.ctx) == 0


def test_no_more_articles_are_kept_in_one_round_than_the_setting_allows(make_rig):
    rig = make_rig(settings={"picks_per_run": 1})
    rig.topic()
    rig.topic(name="Bees", phrasings=["bees winter"])
    rig.results("soil water mulch", (SOIL, SOIL_TEXT))
    rig.results("bees winter", (BEES, BEES_TEXT))
    rig.pick(1)
    rig.search()
    assert len(rig.articles("new")) == 1
    assert len(rig.core.searched) == 1  # the round's room was used, so it stopped


def test_when_the_ranker_does_not_answer_articles_are_kept_in_search_order_and_say_so(rig):
    rig.topic()
    rig.results("soil water mulch", (SOIL, SOIL_TEXT), (TOMATO, TOMATO_TEXT))
    rig.search()  # no reply waiting: the model is unavailable
    new = rig.articles("new")
    assert [a["url"] for a in new] == [SOIL, TOMATO]
    assert "Kept in search order, because the ranking model did not answer" in new[0]["why"]


def test_phrasings_are_searched_in_turn(rig):
    rig.topic(phrasings=["soil water", "mulch depth"])
    for _ in range(3):
        rig.clock.advance(60_000)
        rig.search()
    assert rig.core.searched == ["soil water", "mulch depth", "soil water"]


def test_the_ranker_is_told_what_the_owner_kept_and_skipped_as_data(rig):
    rig.topic()
    rig.results("soil water mulch", (SOIL, SOIL_TEXT), (BEES, BEES_TEXT))
    rig.pick(1, 2)
    rig.search()
    first, second = rig.articles("new")
    rig.core.call("article.choose", {"id": first["id"], "action": "keep"})
    rig.core.call("article.choose", {"id": second["id"], "action": "skip"})
    rig.clock.advance(DAY)
    rig.results("soil water mulch", (TOMATO, TOMATO_TEXT))
    rig.pick(1)
    rig.search()
    asked = rig.core.asked[-1]
    votes = asked["untrusted"][1]
    assert "kept: Soil that holds water" in votes and "skipped: Where bees go" in votes
    assert "Soil that holds water" not in asked["prompt"]
