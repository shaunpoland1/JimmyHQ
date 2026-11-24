"""The SDK's pictures, similarity, web and calendar: against its own fake core, as an app
author tests, and against the real app API."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from jimmyhq_app import App, Calendar, CallError, cosine, similar  # noqa: E402
from jimmyhq_app._transport import HttpTransport  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

from apps_full_helpers import client_for, install_full  # noqa: E402
from test_lookup_fakes import switch_on  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def fake_app(tmp_path, **how) -> tuple[FakeCore, App]:
    core = FakeCore("reader", data_dir=tmp_path, **how)
    return core, App(transport=core)


# --- similarity -----------------------------------------------------------------------------


def test_similar_puts_the_most_alike_first_and_keeps_order_between_equals():
    query = [1.0, 0.0]
    vectors = [[0.0, 1.0], [1.0, 0.1], [2.0, 0.0], [1.0, 0.0]]
    assert [i for i, _ in similar(query, vectors)] == [2, 3, 1, 0]
    assert [i for i, _ in similar(query, vectors, top=2)] == [2, 3]
    assert [i for i, _ in similar(query, vectors, least=0.5)] == [2, 3, 1]


def test_cosine_is_zero_for_an_empty_vector_and_refuses_two_models_vectors():
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([1.0, 2.0], [2.0, 4.0]) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="different models"):
        cosine([1.0], [1.0, 2.0])


def test_the_fake_core_embeds_texts_and_holds_the_app_to_model_ask(tmp_path):
    core, app = fake_app(tmp_path, permissions=["model.ask"])
    found = app.model.embed(["broad beans", "beans broad", "a train"])
    assert found.model == "fake/embed" and core.embedded == [["broad beans", "beans broad",
                                                              "a train"]]
    best = similar(found.vectors[0], found.vectors[1:], top=1)
    assert best[0][0] == 0
    core, app = fake_app(tmp_path, permissions=[])
    with pytest.raises(CallError) as refused:
        app.model.embed(["x"])
    assert refused.value.code == "not_allowed"


# --- pictures and outside text ----------------------------------------------------------------


def test_the_fake_core_takes_a_picture_from_the_data_folder_and_refuses_one_outside(tmp_path):
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "photo.png").write_bytes(PNG)
    core, app = fake_app(tmp_path, permissions=["model.ask"])
    core.replies.append("A bean.")
    assert app.model.ask("What is it?", images=["uploads/photo.png"]).text == "A bean."
    assert core.asked[0]["images"] == ["uploads/photo.png"] and "role" not in core.asked[0]
    for name in ("../photo.png", "/etc/photo.png", "uploads/missing.png"):
        with pytest.raises(CallError):
            app.model.ask("What is it?", images=[name])
    with pytest.raises(CallError) as refused:
        app.model.ask("What is it?", role="default", images=["uploads/photo.png"])
    assert refused.value.code == "invalid"
    core.vision_on = False
    with pytest.raises(CallError) as refused:
        app.model.ask("What is it?", images=["uploads/photo.png"])
    assert refused.value.code == "unavailable"


def test_the_sdk_sends_outside_text_apart_from_the_prompt(tmp_path):
    core, app = fake_app(tmp_path, permissions=["model.ask"])
    core.replies.append("A summary.")
    app.model.ask("Summarise it.", untrusted="page text")
    assert core.asked[0]["prompt"] == "Summarise it."
    assert core.asked[0]["untrusted"] == "page text"


# --- the web --------------------------------------------------------------------------------


def test_the_fake_core_searches_and_pages_through_a_page(tmp_path):
    core, app = fake_app(tmp_path, permissions=["web"], page_chars=4)
    core.search_results["beans"] = [{"title": "Beans", "url": "https://example.com/b"}]
    core.pages["https://example.com/b"] = "0123456789"
    [result] = app.web.search("beans")
    assert result.url == "https://example.com/b" and core.searched == ["beans"]
    first = app.web.read(result.url)
    assert (first.text, first.next, first.total) == ("0123", 4, 10)
    assert app.web.read_all(result.url) == "0123456789"


def test_the_fake_core_holds_the_app_to_web_and_to_the_owners_switch(tmp_path):
    core, app = fake_app(tmp_path, permissions=["model.ask"])
    with pytest.raises(CallError) as refused:
        app.web.search("beans")
    assert refused.value.code == "not_allowed"
    core, app = fake_app(tmp_path, permissions=["web"])
    core.web_on = False
    with pytest.raises(CallError) as refused:
        app.web.read("https://example.com/")
    assert "switched off" in refused.value.message


# --- the calendar ---------------------------------------------------------------------------


def test_the_app_knows_the_owners_calendar_and_hears_when_it_changes(tmp_path):
    core, app = fake_app(tmp_path, timezone="Europe/Paris", week_start="sunday")
    heard: list[str] = []
    app.on_settings(heard.append)
    assert app.calendar == Calendar("Europe/Paris", "sunday")
    core.change_calendar(timezone="Asia/Tokyo")
    assert heard == ["owner.timezone"] and app.timezone == "Asia/Tokyo"
    core.change_calendar(week_start="monday")
    assert app.week_start == "monday" and heard[-1] == "owner.week_start"


def test_a_change_to_the_apps_own_setting_reaches_its_handler_by_its_key(tmp_path):
    core, app = fake_app(tmp_path, settings={"goal": 8})
    heard: list[str] = []
    app.on_settings(heard.append)
    core.change_setting("goal", 9)
    assert heard == ["goal"] and app.settings.get("goal") == 9


# --- against the real app API -----------------------------------------------------------------


@pytest.fixture
def real(core, tmp_path):
    install_full(core, tmp_path, "realweb", permissions=["web", "model.ask"])
    with client_for(core) as web:
        transport = HttpTransport(client=web, token=core.auth.mint_app_token("realweb"))
        yield App(transport=transport, app_id="realweb", data_dir=tmp_path / "data")


def test_the_sdk_reads_the_calendar_and_the_web_through_the_real_app_api(core, real):
    core.settings.set("owner.timezone", "Europe/Paris", actor="owner")
    assert real.calendar.timezone == "Europe/Paris"
    switch_on(core, {("example.com", "/a"): "<html><title>A</title><p>Hello there.</p>"},
              page_chars=500)
    page = real.web.read("https://example.com/a")
    assert page.title == "A" and "Hello there." in page.text and page.next is None
    with pytest.raises(CallError) as refused:
        real.web.read("http://127.0.0.1/")
    assert refused.value.code == "not_allowed"


def test_a_real_similarity_call_with_no_model_set_up_is_a_sentence(real):
    with pytest.raises(CallError) as refused:
        real.model.embed(["hello"])
    assert refused.value.code == "unavailable" and "'embeddings' role" in refused.value.message
