"""The helpers the SDK gives every app, so two apps need not keep copies of their own:
article text and its numbered sentences, comparing texts by shared words, and an app's
settings read in one place. And the one sample article both first-party apps read, to
show they number its sentences the same way."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "sdk" / "python", ROOT / "apps" / "reader", ROOT / "apps" / "posts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from jimmyhq_app import App, word_overlap, word_runs, word_share  # noqa: E402
from jimmyhq_app.settings import ManifestSettings  # noqa: E402
from jimmyhq_app.testing import SAMPLE_ARTICLE, SAMPLE_ARTICLE_SENTENCES, FakeCore  # noqa: E402
from jimmyhq_app.text import (  # noqa: E402
    clean,
    excerpt,
    numbered,
    sentences_page,
    split_sentences,
)

URL = "https://news.example.com/2027/beds"


# --- text ---------------------------------------------------------------------------------------


def test_the_sample_article_splits_into_its_sentences_whatever_the_line_endings():
    sentences = split_sentences(clean(SAMPLE_ARTICLE))
    assert len(sentences) == SAMPLE_ARTICLE_SENTENCES
    assert sentences == split_sentences(clean(SAMPLE_ARTICLE.replace("\r\n", "\n")))
    assert sentences[1].startswith("A three-year trial led by Dr. Lee found")
    assert sentences[2] == "The beds were 3.5 metres long, e.g. about the size of a garden plot."
    assert sentences[3] == "A. N. Other, who ran one farm, said: “We watered half as often.”"
    assert sentences[5] == "The chips should be at least 8 centimetres deep."
    assert sentences[-1] == "A report is due in Jan. 2028."


def test_cleaning_takes_off_markdown_marks_and_keeps_paragraphs():
    assert clean("# Title\r\n\r\n**Bold** words and [a link](https://x.example.com).\n- item") \
        == "Title\n\nBold words and a link. item"


def test_numbering_paging_and_excerpts_count_from_one():
    sentences = split_sentences(clean(SAMPLE_ARTICLE))
    assert numbered(sentences[:2]).splitlines()[1].startswith("[2] A three-year")
    assert numbered(sentences[2:3], start=3).startswith("[3] The beds")
    page, more = sentences_page(sentences, 1, 120)
    assert page.startswith("[1] Beds") and more is not None
    rest, done = sentences_page(sentences, more, 100_000)
    assert rest.startswith(f"[{more}] ") and done is None
    assert excerpt(sentences, 1) == sentences[0] and excerpt(sentences, 0) == ""


# --- both apps read it the same way ---------------------------------------------------------


def test_reader_and_posts_number_an_articles_sentences_identically(tmp_path):
    from posts import sources as posts_sources
    from reader import sources as reader_sources

    core = FakeCore("shared", data_dir=tmp_path)
    core.pages[URL] = SAMPLE_ARTICLE
    app = App(transport=core)
    by_reader = reader_sources.read(app, URL, min_chars=50, keep_chars=60_000)
    by_posts = posts_sources.read(app, URL, min_chars=50, keep_chars=60_000)
    assert by_reader.text == by_posts.text == clean(SAMPLE_ARTICLE)
    assert by_reader.sentences == by_posts.sentences == split_sentences(clean(SAMPLE_ARTICLE))
    assert reader_sources.numbered(by_reader.sentences) == posts_sources.numbered(
        by_posts.sentences)


def test_neither_app_keeps_a_copy_of_the_shared_helpers():
    for path in (ROOT / "apps" / "reader" / "reader" / "sources.py",
                 ROOT / "apps" / "posts" / "posts" / "sources.py",
                 ROOT / "apps" / "reader" / "reader" / "similar.py",
                 ROOT / "apps" / "posts" / "posts" / "similar.py",
                 ROOT / "apps" / "reader" / "reader" / "config.py",
                 ROOT / "apps" / "posts" / "posts" / "config.py"):
        text = path.read_text(encoding="utf-8")
        assert "COPIED" not in text and "def split_sentences" not in text, path.name
        assert "ABBREVIATIONS = " not in text and "zlib" not in text, path.name
        assert "def manifest_settings" not in text, path.name
    for app in ("reader", "posts"):
        assert not (ROOT / "apps" / app / "web" / "ui.js").exists()


# --- comparing by words -------------------------------------------------------------------------


def test_word_runs_compare_two_texts_the_same_way_every_time():
    a = "the quick brown fox jumps over the lazy dog"
    runs = word_runs(a)
    assert runs == sorted(set(runs)) and all(isinstance(r, int) for r in runs)
    assert word_share(runs, runs) == 1.0 and word_share(runs, []) == 0.0
    assert word_overlap(a, a.upper()) == 1.0
    assert word_overlap(a, "a recipe for bread with seeds") == 0.0
    assert 0 < word_overlap(a, "a quick brown fox jumps over a sleepy cat") < 1
    assert word_runs("two words") == word_runs("Two  WORDS")


# --- settings -------------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path) -> ManifestSettings:
    manifest = tmp_path / "jimmyhq.app.json"
    manifest.write_text(
        '{"settings": [{"key": "close", "type": "float", "default": 0.8, "label": "Close at"},'
        ' {"key": "same", "type": "float", "default": 0.9, "label": "Same at"},'
        ' {"key": "days", "type": "int", "default": 30, "label": "Days"}]}', encoding="utf-8")
    return ManifestSettings(manifest, "Tester")


class Meta:
    def __init__(self):
        self.kept: dict = {}

    def get_meta(self, key):
        return self.kept.get(key)

    def set_meta(self, key, value):
        self.kept[key] = value


def test_settings_fall_back_to_the_manifests_defaults_for_any_not_answered(settings, tmp_path):
    core = FakeCore("tester", data_dir=tmp_path, settings={"days": 7, "close": None})
    cfg = settings.load(App(transport=core))
    assert (cfg.days, cfg.close, cfg.same) == (7, 0.8, 0.9)
    with pytest.raises(AttributeError, match="Tester has no setting called 'nope'"):
        _ = cfg.nope


def test_a_pair_the_wrong_way_round_is_refused_and_the_last_good_pair_used(settings, tmp_path):
    store = Meta()
    good = settings.load(App(transport=FakeCore("t", data_dir=tmp_path,
                                                settings={"close": 0.7, "same": 0.9})))
    assert settings.pair(good, store, "close", "same", remember="p") == (
        settings.pair(good, store, "close", "same", remember="p"))
    assert settings.pair(good, store, "close", "same", remember="p").refused is None
    wrong = settings.load(App(transport=FakeCore("t", data_dir=tmp_path,
                                                 settings={"close": 0.95, "same": 0.9})))
    kept = settings.pair(wrong, store, "close", "same", remember="p")
    assert (kept.low, kept.high) == (0.7, 0.9)
    assert "\"Close at\" to 0.95, which is not below \"Same at\" at 0.90" in kept.refused
    assert "Tester did not take the change" in kept.refused
    assert "keeps comparing at 0.70 and 0.90" in kept.refused


def test_a_pair_remembered_before_the_sdk_kept_it_is_still_read(settings, tmp_path):
    store = Meta()
    store.kept["p"] = {"close": 0.6, "same": 0.85}  # an app's own earlier shape
    wrong = settings.load(App(transport=FakeCore("t", data_dir=tmp_path,
                                                 settings={"close": 0.99, "same": 0.9})))
    kept = settings.pair(wrong, store, "close", "same", remember="p")
    assert (kept.low, kept.high) == (0.6, 0.85)


def test_a_refusal_keeps_the_words_jimmyhq_wrote_for_the_owner():
    from jimmyhq_app import CallError
    from jimmyhq_app._transport import unwrap

    with pytest.raises(CallError) as plain:
        unwrap({"ok": False, "error": {"code": "not_found", "message": "For the author."}})
    assert plain.value.owner_message is None
    with pytest.raises(CallError) as worded:
        unwrap({"ok": False, "error": {"code": "not_found", "message": "For the author.",
                                       "owner_message": "No model is set up yet."}})
    assert worded.value.message == "For the author."
    assert worded.value.owner_message == "No model is set up yet."
    with pytest.raises(CallError) as odd:
        unwrap({"ok": False, "error": {"code": "internal", "message": "x",
                                       "owner_message": {"not": "words"}}})
    assert odd.value.owner_message is None
