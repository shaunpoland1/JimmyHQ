"""The tools Reader offers anyone who holds no role: an agent with no round of looking from
Reader and an outside client the owner let through the door. They only read, are bounded,
never reach out, never say who found an article, and count against nobody's round. Sample
data is invented and every address is on example.com."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from reader import outside
from reader_rig import START

MANIFEST = Path(__file__).resolve().parents[1] / "jimmyhq.app.json"
CLIENT = "client:test"
LONELY = "agent:nobody"  # a real agent that holds no role of Reader
HOUR = 3_600_000
SENTENCES = [f"Sentence number {n} says something plain." for n in range(1, 41)]


@pytest.fixture
def reader(make_rig):
    rig = make_rig()
    rig.soil = rig.topic()
    rig.birds = rig.topic(name="Garden birds", phrasings=["feeders seed", "winter birds"])
    return rig


def put(rig, state, title, *, topic=None, why="Worth a look.", at=0, sentences=None, **more):
    found = SENTENCES[:3] if sentences is None else sentences
    return rig.store.insert("article", {
        "url": f"https://news.example.com/{title.replace(' ', '-').lower()}", "host":
        "news.example.com", "title": title, "topic_id": (topic or rig.soil)["id"],
        "why": why, "state": state, "sentences": found, "text": " ".join(found),
        "by": "researcher", "agent": "agent:finder-7", "note": "private owner note",
        "created": START + at, "suggested": START + at, "updated": START + at, **more})


def call(rig, name, args=None, agent=CLIENT, role=None):
    return rig.core.tool(name, args or {}, agent=agent, role=role)


def dump(said) -> str:
    return json.dumps(said, ensure_ascii=False)


def test_topic_list_gives_each_topics_name_phrasings_and_whether_it_is_paused(reader):
    reader.store.update("topic", reader.birds["id"], {"paused": 1})
    said = call(reader, "topic_list")
    assert said["ok"] and said["result"] == "2 topics."
    shown = said["shown"]
    assert "Garden soil" in shown and "Phrasings: soil water mulch" in shown
    assert "Garden birds (paused: not searched for)" in shown
    assert "feeders seed; winter birds" in shown
    assert "Garden soil (paused" not in shown


def test_article_list_gives_title_address_topic_state_in_words_and_the_summary_held(reader):
    put(reader, "kept", "Mulch trial", why="Shows mulch halves the watering.", at=HOUR)
    put(reader, "new", "Bird feeders", topic=reader.birds, at=2 * HOUR)
    said = call(reader, "article_list")
    shown = said["shown"]
    assert said["ok"] and said["result"] == "2 articles."
    assert shown.index("Bird feeders") < shown.index("Mulch trial")
    assert "Address: https://news.example.com/mulch-trial" in shown
    assert "Topic: Garden soil. kept by the owner, 2027-01-15 09:00 UTC" in shown
    assert "suggested, waiting for the owner" in shown and "Topic: Garden birds" in shown
    assert "Summary Reader holds: Shows mulch halves the watering." in shown


def test_article_list_leaves_out_what_reader_only_read_dropped_passed_or_the_owner_skipped(
        reader):
    for state in ("read", "dropped", "passed", "skipped"):
        put(reader, state, f"Hidden {state}")
    put(reader, "later", "Saved one")
    put(reader, "sent", "Sent one")
    shown = call(reader, "article_list")["shown"]
    assert "Saved one" in shown and "Sent one" in shown and "Hidden" not in shown


def test_article_list_can_be_narrowed_by_state_and_by_topic(reader):
    put(reader, "kept", "Kept soil")
    put(reader, "new", "Waiting soil")
    put(reader, "kept", "Kept birds", topic=reader.birds)
    only = call(reader, "article_list", {"state": "waiting"})["shown"]
    assert "Waiting soil" in only and "Kept soil" not in only
    birds = call(reader, "article_list", {"topic": reader.birds["id"]})
    assert "Kept birds" in birds["shown"] and "Kept soil" not in birds["shown"]
    assert birds["result"] == "1 articles."


def test_article_text_gives_the_stored_sentences_numbered_and_says_where_it_ends(reader):
    number = put(reader, "kept", "Mulch trial")
    said = call(reader, "article_text", {"article": number})
    assert said["ok"]
    assert "[1] Sentence number 1 says something plain." in said["shown"]
    assert "the end" in said["shown"] and "came from the web" in said["shown"]


def test_article_text_is_cut_to_a_page_and_says_how_to_read_on(reader):
    long = [f"Sentence {n} " + "word " * 80 for n in range(1, 80)]
    number = put(reader, "kept", "Long one", sentences=long)
    said = call(reader, "article_text", {"article": number})
    assert len(said["shown"]) <= int(reader.ctx.cfg().tool_page_chars)
    assert "ask again with start=" in said["shown"]
    start = int(said["shown"].rsplit("start=", 1)[1].split(" ")[0])
    assert f"[{start}] Sentence {start}" in call(reader, "article_text",
                                                 {"article": number, "start": start})["shown"]


def test_article_text_refuses_an_article_that_is_not_kept_or_waiting_or_not_there(reader):
    skipped = put(reader, "skipped", "Not for you")
    for number in (skipped, 9999):
        said = call(reader, "article_text", {"article": number})
        assert said["ok"] is False and "Nothing was read" in said["result"]
        assert "Sentence number" not in said["shown"]


def test_article_text_says_so_when_the_text_was_deleted(reader):
    number = put(reader, "kept", "Gone text", sentences=[])
    said = call(reader, "article_text", {"article": number})
    assert said["ok"] and "no text kept" in said["result"]


def test_with_nothing_yet_each_tool_says_so_in_words(make_rig):
    rig = make_rig()
    assert "no topics yet" in call(rig, "topic_list")["shown"]
    assert "no articles like that" in call(rig, "article_list")["shown"]


def test_a_list_holds_at_most_the_limit_and_says_how_many_were_left_out(reader):
    for n in range(8):
        put(reader, "kept", f"Article {n}", at=n)
    said = call(reader, "article_list", {"limit": 3})
    assert said["shown"].count("- Article ") == 3
    assert "5 more not shown here: 3 of 8" in said["shown"]
    assert call(reader, "article_list")["shown"].count("- Article ") == 8


def test_a_long_list_is_cut_to_what_one_answer_may_hold_and_says_so(reader):
    for n in range(outside.MOST + 5):
        put(reader, "kept", f"Article {n} " + "title " * 30, why="why " * 200, at=n)
    said = call(reader, "article_list", {"limit": outside.MOST})
    assert len(said["shown"]) <= int(reader.ctx.cfg().tool_page_chars)
    assert "more not shown here" in said["shown"]


@pytest.mark.parametrize("name, args", [
    ("topic_list", {"limit": 0}), ("topic_list", {"limit": 26}),
    ("article_list", {"limit": "ten"}), ("article_list", {"limit": True}),
    ("article_list", {"state": "skipped"}), ("article_list", {"topic": "x"}),
    ("article_list", {"topic": 999}), ("article_text", {}),
    ("article_text", {"article": "x"}), ("article_text", {"article": 1, "start": 0}),
])
def test_an_argument_that_does_not_fit_is_refused_plainly_and_nothing_is_read(
        reader, name, args):
    put(reader, "kept", "Something")
    said = call(reader, name, args)
    assert said["ok"] is False and "Nothing was" in said["result"]


def test_no_agent_model_note_vector_or_other_internal_field_appears(reader):
    number = put(reader, "kept", "Mulch trial", vector=[0.1, 0.2], vector_model="m-1",
                 card_id="card-secret-id", said="internal said")
    reader.store.insert("offer", {"article_id": number, "url": "u", "title": "t",
                                  "topic": "x", "why": "w", "text_ref": "ref-5555",
                                  "created": START})
    text = " ".join(dump(call(reader, name, args)) for name, args in (
        ("topic_list", {}), ("article_list", {"limit": 25}), ("article_text", {"article": number})))
    for forbidden in ("finder-7", "private owner note", "card-secret-id", "m-1", "0.1",
                      "ref-5555", "internal said", "researcher"):
        assert forbidden not in text, forbidden


def test_the_tools_are_the_same_for_a_client_and_an_agent_with_no_role(reader):
    number = put(reader, "kept", "Mulch trial")
    for name, args in (("topic_list", {}), ("article_list", {}),
                       ("article_text", {"article": number})):
        assert call(reader, name, args, agent=CLIENT) == call(reader, name, args,
                                                              agent=LONELY)


def test_calling_them_counts_no_step_credits_nobody_and_writes_nothing(reader):
    number = put(reader, "kept", "Mulch trial")
    tables = ("agent_step", "round", "article", "topic", "search", "fact", "offer", "meta")
    before = {t: reader.store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in tables}
    changes = reader.store.conn.total_changes
    for agent in (CLIENT, LONELY):
        for name, args in (("topic_list", {}), ("article_list", {}),
                           ("article_text", {"article": number})):
            assert call(reader, name, args, agent=agent)["ok"]
    after = {t: reader.store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in tables}
    assert after == before and reader.store.conn.total_changes == changes
    assert reader.core.work == {}


def test_they_reach_nothing_outside_reader_no_search_no_fetch_no_model(reader):
    number = put(reader, "kept", "Mulch trial")
    reader.core.replies.clear()
    reader.core.pages.clear()
    reader.core.search_results.clear()
    for name, args in (("topic_list", {}), ("article_list", {}),
                       ("article_text", {"article": number})):
        assert call(reader, name, args)["ok"]
    assert reader.core.replies == [] and reader.core.pages == {}


def test_none_of_them_belongs_to_a_role_and_a_role_tool_still_asks_for_its_role(reader):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    in_roles = {t for r in manifest["roles"] for t in r["tools"]}
    declared = {t["name"]: t["effect"] for t in manifest["tools"]}
    for name in outside.NAMES:
        assert name not in in_roles and declared[name] == "read"
    said = call(reader, "topics", {}, agent="agent:scout", role="writer")
    assert said["ok"] is False and "belongs to the researcher" in said["shown"]
