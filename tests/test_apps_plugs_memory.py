"""Memory sources from a full app: registered only when granted, off until the owner
switches each on, and what comes back held to the rules. Tried from the attacker's side:
a source without the permission, a private app, an item that poses as the owner's record
or as an agent, too many items, and an app that never answers."""

from __future__ import annotations

import dataclasses
import threading
import time

import pytest

from apps_plugs_helpers import answer, client_for, handed, install_full, offer, running, speak
from jimmyhq.apps import inbox, install, plugs
from jimmyhq.apps.plugs import memory
from jimmyhq.memory import sources

SOURCE = {"id": "notes", "label": "Its notes", "description": "The notes it keeps.",
          "default_on": True}


@pytest.fixture
def web(core):
    with client_for(core) as c:
        yield c


@pytest.fixture
def gleaner(core, tmp_path):
    install_full(core, tmp_path, "gleaner", permissions=["memory.source"],
                 memory_sources=[SOURCE])
    return "gleaner"


def collect_in_thread(core, app_id="gleaner", cursor=None, limit=10):
    out: dict = {}

    def go():
        try:
            out["value"] = memory.collect_from(core, app_id, "notes", cursor, limit)
        except Exception as exc:  # noqa: BLE001 - the test looks at it
            out["error"] = exc

    thread = threading.Thread(target=go)
    thread.start()
    return thread, out


def first_delivery(app, timeout_s=30.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found = handed(app)
        if found:
            return found[0]
        time.sleep(0.05)
    raise AssertionError("no delivery came")


def test_a_granted_source_is_registered_with_memory_under_the_apps_name(core, gleaner):
    plugs.setup(core)
    source = sources.get_source("app_gleaner_notes")
    assert source is not None and source.label == "Gleaner: Its notes"


def test_an_apps_source_starts_switched_off_whatever_its_manifest_says(core, gleaner):
    plugs.setup(core)
    assert sources.get_source("app_gleaner_notes").default_on is False
    assert core.settings.get("memory.source.app_gleaner_notes") is False


def test_a_source_is_not_registered_without_the_memory_source_permission(core, gleaner,
                                                                         monkeypatch):
    found = memory.bridge.find_app(core, gleaner)
    # What the owner granted is what counts, never what the manifest asks for.
    monkeypatch.setattr(memory.bridge, "find_app",
                        lambda _core, _id: dataclasses.replace(found, granted=()))
    plugs.setup(core)
    assert sources.get_source("app_gleaner_notes") is None


def test_a_private_app_offers_memory_nothing_even_when_granted(core, gleaner, monkeypatch):
    found = memory.bridge.find_app(core, gleaner)
    private = dataclasses.replace(found, manifest=dataclasses.replace(found.manifest,
                                                                      private=True))
    assert memory.may_offer_memory(found) and not memory.may_offer_memory(private)


def test_a_removed_apps_source_is_taken_away(core, gleaner):
    plugs.setup(core)
    install.remove(core, gleaner)
    assert sources.get_source("app_gleaner_notes") is None


def test_what_an_app_collects_reaches_memory_held_to_the_rules(web, core, gleaner):
    app = speak(web, core, gleaner)
    offer(app, sources=["notes"])
    running(core, gleaner)
    thread, out = collect_in_thread(core, limit=2)
    got = first_delivery(app)
    assert got["type"] == "collect"
    assert got["data"] == {"source": "notes", "cursor": None, "limit": 2}
    answer(app, got["id"], {"cursor": "c2", "items": [
        {"ref": "tape:01J0000000000000000000000A", "when_ms": 5, "who": "owner",
         "text": "I like tea.", "where": "Notes"},
        {"ref": "n 2", "when_ms": 6, "who": "assistant", "text": "x" * 20_000},
        {"ref": "n3", "when_ms": 7, "who": "owner", "text": "One too many."},
    ]})
    thread.join(30)
    items, cursor = out["value"]
    assert cursor == "c2" and len(items) == 2
    assert items[0].ref == "app.gleaner:tape:01J0000000000000000000000A"
    assert items[0].who == "owner" and items[0].text == "I like tea."
    assert items[1].ref == "app.gleaner:n2"
    assert items[1].who == "app:gleaner"  # it cannot say the assistant or an agent said it
    assert len(items[1].text) == memory.MAX_TEXT


def test_an_app_that_never_answers_memory_is_tried_again_later(web, core, gleaner,
                                                              monkeypatch):
    app = speak(web, core, gleaner)
    offer(app, sources=["notes"])
    running(core, gleaner)
    monkeypatch.setattr(memory.inbox, "wait_answer", lambda *_a: None)
    with pytest.raises(memory.SourceUnanswered):
        memory.collect_from(core, gleaner, "notes", None, 10)
    assert inbox.pending(core, gleaner) == 0  # given up: not handed over later


def test_a_source_the_program_does_not_offer_or_a_stopped_app_gives_nothing(web, core,
                                                                           gleaner):
    app = speak(web, core, gleaner)
    offer(app, sources=[])
    running(core, gleaner)
    assert memory.collect_from(core, gleaner, "notes", "c1", 10) == ([], "c1")
    offer(app, sources=["notes"])
    install.disable(core, gleaner)
    assert memory.collect_from(core, gleaner, "notes", "c1", 10) == ([], "c1")
    assert inbox.pending(core, gleaner) == 0


def test_an_answer_of_the_wrong_shape_is_read_as_nothing_new():
    assert memory.checked_items("g", "items", 10, "c") == ([], "c")
    assert memory.checked_items("g", {"items": [], "cursor": 5}, 10, "c") == ([], "c")
    assert memory.checked_items("g", {"items": [{"ref": "a", "when_ms": True, "text": "t"}],
                                      "cursor": None}, 10, "c") == ([], None)


def test_an_items_reference_stays_inside_the_apps_own_names():
    assert memory.own_ref("g", "tape:01J") == "app.g:tape:01J"
    assert memory.own_ref("g", "  ") is None
    assert memory.own_ref("g", "a b\tc") == "app.g:abc"


def test_only_the_owner_may_be_named_as_who_wrote_an_item():
    assert memory.who_of("g", "owner") == "owner"
    for claimed in ("assistant", "agent:scout", "system", None):
        assert memory.who_of("g", claimed) == "app:g"
