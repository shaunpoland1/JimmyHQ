"""The app as JimmyHQ sees it: what it offers matches what its manifest declares, and the
shapes other apps will rely on are fixed."""

from __future__ import annotations

import json

from reader import articles, kinds, outside, similar, sources
from reader_rig import APP_ROOT

MANIFEST = json.loads((APP_ROOT / "jimmyhq.app.json").read_text(encoding="utf-8"))


def test_what_the_program_offers_is_what_the_manifest_declares(rig):
    plugs = rig.core.plugs
    assert {j["name"] for j in plugs["jobs"]} == {"search", "advance"}
    assert {j.get("every_setting") for j in plugs["jobs"]} == {"search_minutes",
                                                              "advance_minutes"}
    assert sorted(plugs["tools"]) == sorted(t["name"] for t in MANIFEST["tools"])
    assert plugs["sources"] == [s["id"] for s in MANIFEST["memory_sources"]]
    assert sorted(plugs["decisions"]) == sorted(c["kind"] for c in MANIFEST["cards"])
    [role] = MANIFEST["roles"]
    # Every tool is the researcher's, except the three read-only ones anyone may use.
    assert sorted(role["tools"]) == sorted(set(plugs["tools"]) - set(outside.NAMES))
    assert (APP_ROOT / role["charter"]).is_file()


def test_the_record_tools_are_exactly_the_ones_that_only_write(rig):
    declared = {t["name"]: t["effect"] for t in MANIFEST["tools"]}
    assert {n for n, e in declared.items() if e == "record"} == rig.app.record_tools
    assert {n for n, e in declared.items() if e != "record"} == {
        "topics", "search", "read", "seen", *outside.NAMES}
    assert set(declared.values()) == {"read", "record"}


def test_the_memory_source_starts_off_and_mining_is_off_by_default():
    [source] = MANIFEST["memory_sources"]
    assert source["default_on"] is False
    assert next(s for s in MANIFEST["settings"] if s["key"] == "mine_facts")["default"] is False


def test_the_offer_kind_is_declared_with_the_shape_the_feed_will_carry(rig):
    declared = rig.core.kinds[("app.reader.article.offered", 1)]
    assert set(declared["schema"]["properties"]) == {"offer", "article", "url", "title",
                                                     "topic", "why", "text_ref", "when_ms"}
    assert declared["example"] == kinds.OFFER_EXAMPLE
    assert {name for name, _ in rig.core.kinds} == {
        "app.reader.article.suggested", "app.reader.article.decided",
        "app.reader.fact.mined", "app.reader.article.offered",
        "app.reader.offer.withdrawn"}


def test_the_withdrawn_kind_carries_the_offers_number_and_address_and_nothing_more(rig):
    declared = rig.core.kinds[("app.reader.offer.withdrawn", 1)]
    assert set(declared["schema"]["properties"]) == {"offer", "article", "url", "when_ms"}
    assert declared["example"] == kinds.WITHDRAWN_EXAMPLE


def test_single_letter_keys_are_a_setting_that_starts_on_and_the_page_is_told(rig):
    setting = next(s for s in MANIFEST["settings"] if s["key"] == "single_keys")
    assert setting["type"] == "bool" and setting["default"] is True
    assert not setting.get("advanced")
    assert rig.core.call("overview")["keys_on"] is True
    rig.core.change_setting("single_keys", False)
    assert rig.core.call("overview")["keys_on"] is False


def test_technical_settings_are_marked_advanced_and_the_ones_about_privacy_are_not():
    by_key = {s["key"]: s for s in MANIFEST["settings"]}
    for key in ("similar_close", "similar_same", "words_close", "words_same", "min_chars",
                "keep_chars", "advance_minutes", "tool_page_chars"):
        assert by_key[key].get("advanced") is True, key
    for key in ("mine_facts", "keep_text_days", "searches_per_day", "cards_per_day"):
        assert not by_key[key].get("advanced"), key


def test_an_address_in_a_rankers_or_researchers_words_is_taken_out():
    said = articles.no_addresses("See https://made-up.example.net/a and www.made-up.example.net.")
    assert "made-up" not in said and said.count("(address taken out)") == 2


def test_the_sentence_splitter_numbers_the_same_text_the_same_way_every_time():
    text = sources.clean("# A title\n\nDr. Lee said 3.5 cm was enough. It was not! Why?\n\n"
                         "A second paragraph\r\nwith a broken line.")
    first = sources.split_sentences(text)
    assert first == sources.split_sentences(text)
    assert first == ["A title", "Dr. Lee said 3.5 cm was enough.", "It was not!", "Why?",
                     "A second paragraph with a broken line."]
    assert sources.numbered(first[:2]) == "[1] A title\n[2] Dr. Lee said 3.5 cm was enough."


def test_word_runs_are_compared_the_same_way_every_time_and_hold_no_text():
    a = similar.runs_of("the quick brown fox jumps over the lazy dog")
    b = similar.runs_of("a quick brown fox jumps over a sleepy cat")
    assert similar.word_share(a, b) == similar.word_share(a, b) > 0
    assert similar.word_share(a, a) == 1.0 and similar.word_share(a, []) == 0.0
    assert all(isinstance(n, int) for n in a)


def test_every_refusal_to_an_agent_says_nothing_happened(make_rig):
    rig = make_rig(roles={"researcher": "Scout"})
    for name, args in [("search", {"topic": 9}), ("read", {}), ("read", {"url": "nope"}),
                       ("seen", {}), ("suggest", {"article": 3, "why": "x"}),
                       ("mine", {"article": 1, "sentences": "1"})]:
        said = rig.core.tool(name, args, role="researcher")
        assert not said["ok"], name
        assert "othing was" in said["result"] or "nothing was" in said["result"], (name, said)


def test_reader_numbers_the_shared_sample_article_as_the_sdk_does(rig):
    from jimmyhq_app.testing import SAMPLE_ARTICLE, SAMPLE_ARTICLE_SENTENCES
    from jimmyhq_app.text import clean, split_sentences

    url = "https://news.example.com/2027/sample"
    rig.core.pages[url] = SAMPLE_ARTICLE
    read = sources.read(rig.app, url, min_chars=50, keep_chars=60_000)
    assert read.sentences == split_sentences(clean(SAMPLE_ARTICLE))
    assert len(read.sentences) == SAMPLE_ARTICLE_SENTENCES
