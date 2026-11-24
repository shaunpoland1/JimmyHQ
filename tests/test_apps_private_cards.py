"""What a private app may put on the owner's Bench and task list, proven through the real
Python SDK, the real bridge and the real Bench, and again through the SDK's fake core with
the same cases, so the fake cannot accept what JimmyHQ refuses."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from jimmyhq_app import App, CallError  # noqa: E402
from jimmyhq_app._transport import HttpTransport  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

from apps_full_helpers import client_for, install_full  # noqa: E402
from jimmyhq import bench  # noqa: E402

GOT_IT = {"id": "ok", "label": "Got it", "style": "primary", "closes": True}
PERMISSIONS = ["bench.file", "tasks.write"]
SUBJECTS = ["reminder", "silence"]  # the words this app's manifest declares

# (what the app passes to app.bench.file, whether a private app may)
CARDS = [
    ({}, True),
    ({"body": "", "actions": [], "subject": None}, True),  # left empty: nothing is sent
    ({"actions": [GOT_IT]}, True),
    ({"actions": [{"id": "approve", "label": "Approve", "style": "primary", "closes": True}]},
     True),
    ({"subject": "reminder:12"}, True),
    ({"subject": "silence"}, True),
    ({"lane": "done"}, True),
    ({"body": "Rent is 1200 to Example Landlord"}, False),
    ({"summary": "Rent is 1200"}, False),
    ({"why": "Because"}, False),
    ({"details": [{"label": "Payee", "text": "Example Landlord"}]}, False),
    ({"actions": [{"id": "pay", "label": "Pay Example Landlord 1200"}]}, False),
    ({"actions": [{"id": "ok", "label": "Got it, thanks"}]}, False),  # not the Bench's own
    ({"subject": "rent:Example Landlord"}, False),
    ({"subject": "reminder:12345678901"}, False),
    ({"subject": "reminder"}, True),
    ({"subject": "payrent"}, False),  # a short word, but one the manifest does not declare
    ({"subject": "a-free-word-of-up-to-24"}, False),  # the old free word, hyphens and all
    ({"subject": "reminders-for-the-landlord:3"}, False),
    ({"subject": "Reminder:3"}, False),
    ({"subject": "reminder:3:4"}, False),
    ({"subject": "reminder:"}, False),
    ({"subject": 7}, False),
]


@pytest.fixture
def private_app(core, tmp_path):
    """A private app, installed for real, whose SDK talks to the real core."""
    install_full(core, tmp_path, "diary", private=True, permissions=PERMISSIONS,
                 card_subjects=SUBJECTS)
    with client_for(core) as web:
        app = App(transport=HttpTransport(client=web, token=core.auth.mint_app_token("diary")),
                  app_id="diary", data_dir=tmp_path / "data")
        yield app


@pytest.fixture
def fake_app(tmp_path):
    core = FakeCore("diary", data_dir=tmp_path, permissions=PERMISSIONS, cards=["draft"],
                    private=True, card_subjects=SUBJECTS)
    with core.installed():
        yield App()


def file_card(app, extra):
    return app.bench.file("draft", "Rent is due on 3 December.", **extra)


def test_a_private_app_files_a_card_with_a_title_only_through_the_real_bridge(core, private_app):
    card_id = private_app.bench.file("draft", "Rent is due on 3 December.")
    card = bench.get_card(core, card_id)
    assert card["title"] == "Rent is due on 3 December."
    assert card["kind"] == "app.diary.draft" and card["filed_by"] == "app:diary"
    assert not card["body"] and not card["summary"]


def test_a_private_app_that_leaves_the_rest_empty_is_not_refused_for_what_it_did_not_give(
        core, private_app):
    card_id = private_app.bench.file("draft", "Done.", body="", actions=[], subject=None)
    assert bench.get_card(core, card_id)["title"] == "Done."


def test_the_real_bridge_refuses_a_body_on_a_private_apps_card_and_files_nothing(
        core, private_app):
    with pytest.raises(CallError) as caught:
        private_app.bench.file("draft", "Rent is due.", body="Rent is 1200 to Example Landlord")
    assert caught.value.code == "invalid" and "title only" in caught.value.message
    assert bench.board(core)["lanes"]["needs"] == []


@pytest.mark.parametrize(("extra", "allowed"), CARDS)
def test_the_real_bridge_takes_what_the_fake_core_takes_for_a_private_apps_card(
        core, private_app, fake_app, extra, allowed):
    outcomes = []
    for app in (private_app, fake_app):
        try:
            file_card(app, extra)
            outcomes.append(True)
        except CallError as error:
            assert error.code == "invalid"
            outcomes.append(False)
    assert outcomes == [allowed, allowed]


def test_a_private_apps_card_offers_the_benches_own_got_it_button(core, private_app):
    card_id = private_app.bench.file("draft", "Rent is due.", actions=[GOT_IT],
                                     subject="reminder:7")
    card = bench.get_card(core, card_id)
    assert [a["label"] for a in card["actions"]] == ["Got it"]
    assert card["subject"] == "app.diary:reminder:7"


@pytest.mark.parametrize("name", ["real", "fake"])
def test_a_private_app_changes_its_card_title_and_withdraws_it_with_no_note_and_nothing_else(
        name, core, private_app, fake_app):
    app = private_app if name == "real" else fake_app
    card_id = file_card(app, {})
    app.bench.update(card_id, title="Rent is due tomorrow.")
    for refused in ({"note": "Paid"}, {"body": "Rent is 1200"}, {"summary": "1200"}):
        with pytest.raises(CallError) as caught:
            app.bench.update(card_id, **refused)
        assert caught.value.code == "invalid"
    with pytest.raises(CallError) as caught:
        app.bench.withdraw(card_id, note="Paid to Example Landlord")
    assert caught.value.code == "invalid"
    app.bench.withdraw(card_id)


@pytest.mark.parametrize("name", ["real", "fake"])
def test_a_private_app_adds_a_task_with_a_title_only_and_notes_are_refused(
        name, core, private_app, fake_app):
    app = private_app if name == "real" else fake_app
    assert app.tasks.create("Call the landlord", notes="")["title"] == "Call the landlord"
    with pytest.raises(CallError) as caught:
        app.tasks.create("Call the landlord", notes="About the 1200 rent")
    assert caught.value.code == "invalid" and "title only" in caught.value.message


@pytest.mark.parametrize("name", ["real", "fake"])
def test_a_private_app_that_declares_no_subject_words_files_cards_with_no_subject_only(
        name, core, tmp_path):
    if name == "real":
        install_full(core, tmp_path, "diary", private=True, permissions=PERMISSIONS)
        with client_for(core) as web:
            app = App(transport=HttpTransport(client=web,
                                              token=core.auth.mint_app_token("diary")),
                      app_id="diary", data_dir=tmp_path / "data")
            file_card(app, {})
            with pytest.raises(CallError) as caught:
                file_card(app, {"subject": "reminder:12"})
            assert caught.value.code == "invalid" and "card_subjects" in caught.value.message
        return
    fake = FakeCore("diary", data_dir=tmp_path, permissions=PERMISSIONS, cards=["draft"],
                    private=True)
    with fake.installed():
        app = App()
        file_card(app, {})
        with pytest.raises(CallError) as caught:
            file_card(app, {"subject": "reminder:12"})
        assert caught.value.code == "invalid" and "card_subjects" in caught.value.message
