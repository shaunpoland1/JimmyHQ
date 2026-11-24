"""A first-person story the owner did not tell becomes a question, never a block. The check
is deterministic and conservative, so most of these tests are the sentences it must leave
alone."""

from __future__ import annotations

import pytest
from posts import config, sources
from posts.checks import Draft, Material, lived
from posts_rig import ARTICLE, ARTICLE_TEXT

CFG = config.Config(config.defaults())
SENTENCES = sources.split_sentences(sources.clean(ARTICLE_TEXT))


def asks(body: str, notes: str = "") -> list[str]:
    found = lived.lived_experience(Draft("", body), Material(sentences=SENTENCES, notes=notes,
                                                             source_url=ARTICLE), CFG)
    return [r.detail for r in found if r.level == "ask"]


@pytest.mark.parametrize("body", [
    "I planted twelve apple trees last spring.",
    "We visited three installers before choosing one.",
    "Last year I finally installed panels on the shed.",
    "I've been testing a smaller system all winter.",
    "When I was young, nobody had panels.",
    "In my experience the grant takes months.",
    "I used to think solar was only for big houses.",
    "We’ve tried two kinds of panel on the garage.",
    "My neighbour and I built a frame for them.",
])
def test_a_first_person_story_not_in_the_notes_asks_did_this_happen(body):
    [said] = asks(body)
    assert said.startswith("Did this happen?") and body.split()[0] in said


@pytest.mark.parametrize("body", [
    "I think the grant favours larger houses.",
    "I love how quickly this spread.",
    "Have you tried asking an installer about waiting lists?",
    "If you visited the valley last year, you saw the roofs change.",
    "If I had installed panels then, I would have saved money.",
    "Imagine we planted a tree for every roof.",
    "“We have never seen demand like this,” said the council's energy officer.",
    "We have seen prices fall before.",
    "We found that the grant helps most in winter.",
    "The council said 1,240 homes added panels.",
    "Installers report waiting lists of up to 14 weeks.",
    "Did I mention that we went solar?",
])
def test_opinions_questions_suppositions_quotes_and_generic_we_are_left_alone(body):
    assert asks(body) == []


def test_a_story_whose_words_are_in_the_owners_notes_is_theirs():
    body = "I planted twelve tomato plants in March."
    assert asks(body, notes="Planted 12 tomato plants in the greenhouse in March.") == []
    found = lived.lived_experience(Draft("", body), Material(notes="planted tomato plants "
                                                             "march"), CFG)
    assert found[0].level == "ok" and found[0].evidence == ["notes"]


def test_notes_about_something_else_do_not_cover_a_story():
    assert asks("I planted twelve tomato plants in March.", notes="Mention the grant.")


def test_the_share_of_words_needed_from_the_notes_is_a_setting():
    body = "We visited three installers near the river."
    notes = "visited installers"
    strict = config.Config({**config.defaults(), "lived_notes_share": 0.9})
    loose = config.Config({**config.defaults(), "lived_notes_share": 0.3})
    material = Material(notes=notes)
    assert lived.lived_experience(Draft("", body), material, strict)[0].level == "ask"
    assert lived.lived_experience(Draft("", body), material, loose)[0].level == "ok"


def test_the_source_s_own_first_person_words_are_not_the_owners_story():
    source = ["We visited every school in the district last year."]
    found = lived.lived_experience(Draft("", "We visited every school in the district last "
                                         "year."), Material(sentences=source), CFG)
    assert all(r.level == "ok" for r in found)


def test_with_no_story_the_check_says_so_once():
    [found] = lived.lived_experience(Draft("", "Panels are spreading fast."), Material(), CFG)
    assert found.level == "ok" and found.key == "none"


def test_story_in_names_the_words_that_tell_it():
    assert lived.story_in("Then I went to the market.") == "I went"
    assert lived.story_in("Why did I go?") is None
