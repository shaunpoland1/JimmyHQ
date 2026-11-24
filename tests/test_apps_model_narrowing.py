"""A model call may make itself stricter than the owner's settings, never looser:
``stay_local`` and ``off_record`` (rule ``a call may only narrow``)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jimmyhq.apps import bridge
from jimmyhq.models.shapes import ModelError
from test_apps_bridge_fakes import WATER, call, events, installs, model, refused

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

from jimmyhq_app import App, CallError  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

DRAFTS = json.loads((Path(__file__).resolve().parents[1] / "apps" / "examples" / "drafts"
                     / "jimmyhq.app.json").read_text(encoding="utf-8"))


@pytest.fixture
def apps(monkeypatch, tmp_path):
    return installs(monkeypatch, tmp_path)


def test_a_call_that_asks_to_stay_local_is_answered_only_by_a_local_model(
        core, apps, monkeypatch):
    apps.add(DRAFTS)
    asked = model(monkeypatch)
    call(core, "drafts", "model.ask", {"prompt": "Hello"})
    call(core, "drafts", "model.ask", {"prompt": "Hello", "stay_local": True})
    assert [c["local_only"] for c in asked.calls] == [False, True]


def test_a_call_off_the_record_keeps_no_text(core, apps, monkeypatch):
    apps.add(DRAFTS)
    asked = model(monkeypatch)
    call(core, "drafts", "model.ask", {"prompt": "Hello"})
    call(core, "drafts", "model.ask", {"prompt": "Hello", "off_record": True})
    assert [c["keep_text"] for c in asked.calls] == [True, False]


@pytest.mark.parametrize("flag", ["stay_local", "off_record"])
@pytest.mark.parametrize("value", [False, "yes", 1])
def test_a_call_can_never_loosen_what_the_owner_set(core, apps, monkeypatch, flag, value):
    apps.add(DRAFTS, private=True)
    asked = model(monkeypatch)
    error = refused(core, "drafts", "model.ask", {"prompt": "Hello", flag: value})
    assert error.code == "invalid" and "can only be true" in error.message
    assert "Nothing was sent" in error.message and asked.calls == []


def test_a_sandboxed_app_under_local_only_stays_local_whatever_it_asks(
        core, apps, monkeypatch):
    apps.add(WATER, granted=["model.ask"])
    core.settings.set(bridge.LOCAL_ONLY, True, actor="owner")
    asked = model(monkeypatch)
    call(core, "water", "model.ask", {"prompt": "Hello", "off_record": True})
    assert asked.calls[0]["local_only"] is True and asked.calls[0]["keep_text"] is False


def test_with_no_local_model_a_call_that_asked_to_stay_local_sends_nothing(
        core, apps, monkeypatch):
    apps.add(DRAFTS)
    model(monkeypatch, fail=ModelError("local_only", "No local model: hosted-x."))
    error = refused(core, "drafts", "model.ask", {"prompt": "Hello", "stay_local": True})
    assert error.code == "unavailable" and "This call asked to stay" in error.message
    assert "Nothing was sent anywhere" in error.message and "hosted-x" not in error.message
    assert events(core, "model.called") == []


def test_the_sdk_and_its_fake_core_carry_both_flags(tmp_path):
    core = FakeCore("drafts", data_dir=tmp_path, permissions=["model.ask"])
    app = App(transport=core)
    core.replies.append("fine")
    assert app.model.ask("Hello", stay_local=True, off_record=True).local is True
    assert core.asked[0]["stay_local"] is True and core.asked[0]["off_record"] is True
    core.local_model = False
    core.replies.append("unused")
    with pytest.raises(CallError, match="stay on the owner's own computer"):
        app.model.ask("Hello", stay_local=True)
    assert len(core.asked) == 1
    core.replies.clear()
    core.replies.append("from a hosted model")
    assert app.model.ask("Hello").local is False


# --- when no model can answer: what the owner reads ---------------------------------------------


def test_with_no_model_set_up_the_owner_is_told_where_to_set_one_up_and_the_author_how_to_say_it(
        core, apps):
    apps.add(WATER, granted=["model.ask"])
    error = refused(core, "water", "model.ask", {"prompt": "Hello"})
    assert error.code == "unavailable"
    assert error.owner_message == ("Water needs a model, and none is set up yet. Add one to "
                                   "the default role under Settings, Models, then try again.")
    assert "owner_message" in error.message and "nothing was asked" in error.message
    assert "'default' role" not in error.message  # the plumbing's wording is not handed on
    assert bridge.failed(7, error)["error"]["owner_message"] == error.owner_message


def test_a_private_app_with_no_model_is_told_it_needs_one_on_the_owners_own_machines(
        core, apps, monkeypatch):
    apps.add(DRAFTS, private=True)
    for kind in ("not_configured", "local_only"):
        model(monkeypatch, fail=ModelError(kind, "No model: hosted-x."))
        error = refused(core, "drafts", "model.ask", {"prompt": "Hello"})
        assert error.owner_message == (
            "Drafts is a private app, so it needs a model on your own machines, and none is "
            "set up yet. Add one that runs on your own machines to the default role under "
            "Settings, Models, then try again."), kind
        assert "hosted-x" not in error.owner_message and "hosted-x" not in error.message


def test_an_app_held_to_the_owners_own_machines_by_a_setting_says_so_when_none_is_set_up(
        core, apps, monkeypatch):
    apps.add(WATER, granted=["model.ask"])
    core.settings.set(bridge.LOCAL_ONLY, True, actor="owner")
    model(monkeypatch, fail=ModelError("local_only", "No local model: hosted-x."))
    error = refused(core, "water", "model.ask", {"prompt": "Hello"})
    assert error.owner_message == (
        "Water may only use a model on your own machines, and none is set up yet. Add one "
        "that runs on your own machines to the default role under Settings, Models, then "
        "try again.")
    assert "Nothing was sent anywhere" in error.message  # the author's sentence is as it was


def test_other_model_failures_carry_no_owner_sentence_of_ours(core, apps, monkeypatch):
    apps.add(WATER, granted=["model.ask"])
    model(monkeypatch, fail=ModelError("timeout", "The model took too long."))
    error = refused(core, "water", "model.ask", {"prompt": "Hello"})
    assert error.owner_message is None
    assert "owner_message" not in bridge.failed(1, error)["error"]


def test_a_call_that_asked_to_stay_local_says_it_was_the_app_and_not_the_owners_setting(
        core, apps, monkeypatch):
    apps.add(WATER, granted=["model.ask"])
    model(monkeypatch, fail=ModelError("local_only", "No local model: hosted-x."))
    error = refused(core, "water", "model.ask", {"prompt": "Hello", "stay_local": True})
    assert error.owner_message == (
        "Water asked to keep this on your own machines, and there is no model there for the "
        "default role. Add one that runs on your own machines to it under Settings, Models, "
        "then try again.")


@pytest.mark.parametrize("role", ["cheap", "smart", "code"])
def test_a_named_text_role_with_no_model_tells_the_owner_it_may_borrow_the_default_roles(
        core, apps, monkeypatch, role):
    apps.add(WATER, granted=["model.ask"])
    model(monkeypatch, fail=ModelError("not_configured", f"No model for '{role}'."))
    error = refused(core, "water", "model.ask", {"prompt": "Hello", "role": role})
    assert error.owner_message == (
        f"Water needs a model, and none is set up yet. Add one to the {role} role (or the "
        "default role) under Settings, Models, then try again.")


def test_with_no_vision_model_the_owner_is_told_which_role_is_empty_and_where_to_fill_it(
        core, apps, monkeypatch):
    apps.add(WATER, granted=["model.ask"])
    from test_apps_reach_vision import picture  # noqa: PLC0415
    picture(core, "photo.png")
    model(monkeypatch, roles=("default",))
    error = refused(core, "water", "model.ask", {"prompt": "Look.", "images": ["photo.png"]})
    assert error.owner_message == (
        "Water needs a model that can see pictures, and none is set up yet. Add one to the "
        "vision role under Settings, Models, then try again.")
    assert bridge.failed(3, error)["error"]["owner_message"] == error.owner_message


def test_a_private_app_with_no_local_vision_model_is_told_to_add_one_to_the_vision_role(
        core, apps, monkeypatch):
    apps.add(DRAFTS, private=True)
    from test_apps_reach_vision import picture  # noqa: PLC0415
    picture(core, "photo.png", app_id="drafts")
    model(monkeypatch, fail=ModelError("local_only", "No local model: hosted-x."))
    error = refused(core, "drafts", "model.ask", {"prompt": "Look.", "images": ["photo.png"]})
    assert error.owner_message == (
        "Drafts is a private app, so it needs a model that can see pictures on your own "
        "machines, and none is set up yet. Add one that runs on your own machines to the "
        "vision role under Settings, Models, then try again.")


def test_a_vision_model_that_cannot_read_pictures_is_named_to_the_owner_too(
        core, apps, monkeypatch):
    apps.add(WATER, granted=["model.ask"])
    from test_apps_reach_vision import picture  # noqa: PLC0415
    picture(core, "photo.png")
    model(monkeypatch, fail=ModelError("no_images", "The model cannot see."))
    error = refused(core, "water", "model.ask", {"prompt": "Look.", "images": ["photo.png"]})
    assert "vision role" in error.owner_message and "Settings, Models" in error.owner_message
    assert error.owner_message.startswith("Water ")


def test_with_no_embeddings_model_the_owner_is_told_to_fill_the_embeddings_role(core, apps):
    from jimmyhq.models import brain  # noqa: PLC0415
    apps.add(WATER, granted=["model.ask"])
    brain(core)
    error = refused(core, "water", "model.embed", {"texts": ["a"]})
    assert error.owner_message == (
        "Water needs a model for similarity search, and none is set up yet. Add one to the "
        "embeddings role under Settings, Models, then try again.")


def test_every_sentence_for_an_owner_names_the_app_the_role_and_where_to_set_it_up():
    for role in ("default", "cheap", "smart", "code", "vision", "embeddings"):
        for private, own, asked in ((False, False, False), (True, True, False),
                                    (False, True, False), (False, True, True)):
            words = bridge.no_model_words("Water", private, own, role, asked_local=asked)
            assert words.startswith("Water ") and f"{role} role" in words, words
            assert "Settings, Models" in words and words.endswith("try again."), words
