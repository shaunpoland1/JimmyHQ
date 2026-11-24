"""What apps declare stays in step with what is installed in every process, whoever made
the change. "Another process" is a second Core on the same data folder, run with this
process's registries put back exactly as they were afterwards: what it changed on disk
and on the Tape is all this process can know of it, as with a real second process."""

from __future__ import annotations

import contextlib
import copy
import json
import logging

import pytest

from apps_full_helpers import client_for, full_app, install_full
from apps_install_helpers import install_folder
from apps_plugs_helpers import offer, running, speak
from apps_roles_helpers import agent, caps, install_roles_app
from jimmyhq import settings as registry
from jimmyhq.apps import appdata, install, roles, schemas, store, sync, update
from jimmyhq.apps.plugs import tools
from jimmyhq.core import open_core
from jimmyhq.memory import sources
from jimmyhq.tape import registry as kinds_registry

PASSWORD = "correct horse battery"
GOAL = {"key": "goal", "type": "int", "default": 8, "min": 1, "max": 30,
        "label": "Glasses a day", "help": "Your daily goal."}
NOTE = {"key": "note", "type": "str", "default": "", "label": "A note",
        "help": "Anything at all."}
SOURCE = {"id": "notes", "label": "Notes", "description": "The notes you keep in it.",
          "default_on": True}
LOOKUP = {"name": "lookup", "description": "Find a book by its title.",
          "params": {"title": {"type": "string", "description": "The title."}},
          "required": ["title"], "effect": "read"}
KIND = {"name": "drafted", "v": 1,
        "schema": {"type": "object", "properties": {"words": {"type": "integer"}},
                   "required": ["words"], "additionalProperties": False},
        "example": {"words": 3}}


def _apps_own(name: str, key) -> bool:
    """What an app puts in a registry, as against what the core's modules declare."""
    if name == "declared":
        return key.startswith(("app.", "memory.source.app_"))
    if name == "sections":
        return key.startswith("app.")
    if name == "sources":
        return key.startswith("app_")
    kind = key[0] if isinstance(key, tuple) else key
    return kind in kinds_registry._SCHEMA_BACKED or kind.startswith("app.")


def _registries() -> dict:
    return {"sections": registry._SECTIONS, "declared": registry._DECLARED,
            "sources": sources._SOURCES, "kinds": kinds_registry._KINDS,
            "owners": kinds_registry._OWNERS, "examples": kinds_registry._EXAMPLES,
            "schema_examples": kinds_registry._SCHEMA_EXAMPLES}


@contextlib.contextmanager
def as_they_were():
    """What apps put in this process's registries, put back as it was when the block
    ends. What a core module declares meanwhile, on first import, stays."""
    kept = {name: {k: copy.copy(v) for k, v in table.items() if _apps_own(name, k)}
            for name, table in _registries().items()}
    backed = set(kinds_registry._SCHEMA_BACKED)
    try:
        yield
    finally:
        for name, table in _registries().items():
            for key in [k for k in table if _apps_own(name, k)]:
                del table[key]
            table.update(kept[name])
        kinds_registry._SCHEMA_BACKED.clear()
        kinds_registry._SCHEMA_BACKED.update(backed)


@contextlib.contextmanager
def elsewhere(cfg):
    """Another process: its own Core on the same data folder. This process's registries
    are put back as they were when it is done."""
    with as_they_were():
        other = open_core(cfg)
        try:
            yield other
        finally:
            other.close()


@pytest.fixture(autouse=True)
def _look_every_time_and_leave_no_kinds(monkeypatch):
    """Tests share one process on Windows: the kinds these tests register go with them."""
    monkeypatch.setattr(sync, "STEP_S", 0)
    with as_they_were():
        yield


def keys() -> set[str]:
    return {s.key for s in registry.declared()}


def pages() -> set[str]:
    return {s.id for s in registry.ordered_sections()}


def app_sources() -> set[str]:
    return {s.id for s in sources.all_sources() if s.id.startswith("app_")}


def owner(core):
    client = client_for(core)
    assert client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
    return client


def newer(tmp_path, app_id, version, **extra):
    return full_app(tmp_path / version, app_id, version=version, **extra)


def updated(core, tmp_path, app_id, version, **extra) -> dict:
    plan = update.plan_folder(core, app_id, newer(tmp_path, app_id, version, **extra))
    return update.confirm_update(core, plan["plan"], plan["fingerprint"],
                                 typed_name=plan["app"]["name"])


# --- the rules ------------------------------------------------------------------------------


def test_a_process_looks_again_at_most_every_step_and_never_later():
    assert not sync.due(10.0, 10.2, every_s=0.5)
    assert sync.due(10.0, 10.5, every_s=0.5)
    assert sync.due(float("-inf"), 0.0)


def test_an_open_database_is_let_go_when_its_app_is_removed_switched_off_or_moved_on():
    row = {"version": "1.0.0", "tree": "a", "enabled": True, "fingerprint": "f",
           "installed_at": 1, "updated_at": None}
    before = {"gone": sync.entry(row), "off": sync.entry(row), "newer": sync.entry(row),
              "granted": sync.entry(row), "same": sync.entry(row)}
    now = {"off": sync.entry(row | {"enabled": False}),
           "newer": sync.entry(row | {"version": "1.1.0", "tree": "b"}),
           "granted": sync.entry(row | {"fingerprint": "g"}), "same": sync.entry(row)}
    assert sync.to_let_go(before, now) == ["gone", "newer", "off"]


# --- installed elsewhere ----------------------------------------------------------------------


def test_an_app_installed_by_another_process_gets_its_settings_page_without_a_restart(
        core, cfg, tmp_path):
    web = owner(core)
    with elsewhere(cfg) as other:
        install_full(other, tmp_path, "counter", settings=[GOAL])
    assert "app.counter.goal" not in keys()  # nothing in this process knew of it
    described = web.get("/api/settings").json()
    assert {"app.counter.goal", "app.counter.auto_update"} <= {
        s["key"] for s in described["settings"]}
    assert described["layout"]["sections"]["app.counter"]["group"] == "apps"
    assert web.put("/api/settings/app.counter.goal", json={"value": 12}).is_success
    assert core.settings.get("app.counter.goal") == 12


def test_an_app_installed_by_another_process_offers_its_memory_sources_here(
        core, cfg, tmp_path):
    with elsewhere(cfg) as other:
        install_full(other, tmp_path, "gleaner", permissions=["memory.source"],
                     memory_sources=[SOURCE])
    sync.keep_in_step(core)
    assert app_sources() == {"app_gleaner_notes"}
    assert "memory.source.app_gleaner_notes" in keys()
    assert core.settings.get("memory.source.app_gleaner_notes") is False


def test_kinds_another_process_kept_are_registered_here(core, cfg, tmp_path):
    install_full(core, tmp_path, "kindly")
    name = "app.kindly.drafted"
    with elsewhere(cfg) as other:
        schemas.declare(other, store.manifest_of(other, "kindly"), [KIND])
    assert not kinds_registry.is_schema_backed(name)
    sync.keep_in_step(core)
    assert kinds_registry.is_schema_backed(name)


# --- changed elsewhere ------------------------------------------------------------------------


def test_an_update_that_drops_a_setting_withdraws_it_everywhere(core, cfg, tmp_path):
    install_full(core, tmp_path / "one", "counter", settings=[GOAL, NOTE])
    assert {"app.counter.goal", "app.counter.note"} <= keys()
    with elsewhere(cfg) as other:
        updated(other, tmp_path, "counter", "1.1.0", settings=[GOAL])
    sync.keep_in_step(core)
    assert "app.counter.note" not in keys()
    assert "app.counter.goal" in keys()


def test_an_update_that_changes_a_setting_replaces_it_and_keeps_its_value(core, cfg,
                                                                         tmp_path):
    install_full(core, tmp_path / "one", "counter", settings=[GOAL])
    core.settings.set("app.counter.goal", 12, actor="owner")
    with elsewhere(cfg) as other:
        updated(other, tmp_path, "counter", "1.1.0",
                settings=[GOAL | {"label": "Cups a day", "max": 40}])
    sync.keep_in_step(core)
    [spec] = [s for s in registry.declared() if s.key == "app.counter.goal"]
    assert (spec.label, spec.max) == ("Cups a day", 40)
    assert core.settings.get("app.counter.goal") == 12


def test_an_update_that_renames_a_memory_source_replaces_it_and_its_switch(core, cfg,
                                                                          tmp_path):
    install_full(core, tmp_path / "one", "gleaner", permissions=["memory.source"],
                 memory_sources=[SOURCE])
    with elsewhere(cfg) as other:
        updated(other, tmp_path, "gleaner", "1.1.0", permissions=["memory.source"],
                memory_sources=[SOURCE | {"label": "Jottings"}])
    sync.keep_in_step(core)
    assert sources.get_source("app_gleaner_notes").label == "Gleaner: Jottings"
    [switch] = [s for s in registry.declared() if s.key == "memory.source.app_gleaner_notes"]
    assert switch.label == "Learn from: Gleaner: Jottings"


@pytest.mark.parametrize("by", ["owner", "system"])
def test_a_rollback_by_another_process_takes_back_what_the_newer_version_added(
        core, cfg, tmp_path, by):
    install_full(core, tmp_path / "one", "counter", settings=[GOAL])
    with elsewhere(cfg) as other:
        updated(other, tmp_path, "counter", "1.1.0", settings=[GOAL, NOTE])
    sync.keep_in_step(core)
    assert "app.counter.note" in keys()
    old = next(v["name"] for v in update.kept(core, "counter") if not v["current"])
    with elsewhere(cfg) as other:  # the owner's click, or the worker after a crash loop
        update.rollback(other, "counter", old, actor=by)
    sync.keep_in_step(core)
    assert "app.counter.note" not in keys() and "app.counter.goal" in keys()


def test_an_app_switched_off_elsewhere_keeps_its_page_and_lets_go_of_its_database(
        core, cfg, tmp_path):
    install_full(core, tmp_path, "counter", settings=[GOAL])
    opened = appdata.open_data(core, "counter")
    with elsewhere(cfg) as other:
        install.disable(other, "counter")
    done = sync.keep_in_step(core)
    assert done is not None and done.let_go == ["counter"]
    assert "app.counter.goal" in keys()
    assert appdata.open_data(core, "counter") is not opened
    with elsewhere(cfg) as other:
        install.enable(other, "counter")
    sync.keep_in_step(core)
    assert "app.counter.goal" in keys()


# --- removed elsewhere ------------------------------------------------------------------------


@pytest.mark.parametrize("keep_data", [True, False])
def test_an_app_removed_by_another_process_loses_its_settings_sources_tools_and_roles_here(
        core, cfg, tmp_path, keep_data):
    install_roles_app(core, tmp_path, permissions=["agents.tools", "memory.source"],
                      settings=[GOAL], memory_sources=[SOURCE])
    web = client_for(core)
    offer(speak(web, core, "posts"), tools=["brief", "submit_draft"])
    running(core, "posts")
    agent(core, "agent-a")
    roles.fill(core, "posts", "writer", "agent-a")
    assert tools.all_tools(core) and "app.posts.goal" in keys() and app_sources()
    with elsewhere(cfg) as other:
        install.remove(other, "posts", keep_data=keep_data)
    sync.keep_in_step(core)
    assert not {k for k in keys() if k.startswith("app.posts.")}
    assert "app.posts" not in pages()
    assert app_sources() == set()
    assert "memory.source.app_posts_notes" not in keys()
    assert tools.all_tools(core) == [] and tools.capabilities(core) == []
    assert caps(core, "agent-a") == [] and roles.holder(core, "posts", "writer") is None


def test_a_removed_apps_kinds_stay_readable_so_what_it_wrote_can_still_be_read(core, cfg,
                                                                              tmp_path):
    install_full(core, tmp_path, "drafty")
    schemas.declare(core, store.manifest_of(core, "drafty"), [KIND])
    with elsewhere(cfg) as other:
        install.remove(other, "drafty")
    sync.keep_in_step(core)
    assert kinds_registry.is_schema_backed("app.drafty.drafted")


# --- whole and only once ----------------------------------------------------------------------


def test_a_broken_app_does_not_stop_the_others_syncing(core, cfg, tmp_path, caplog):
    with elsewhere(cfg) as other:
        install_full(other, tmp_path / "a", "counter", settings=[GOAL])
        install_full(other, tmp_path / "b", "tally", settings=[GOAL])
        manifest = store.code_dir(other, "counter") / "jimmyhq.app.json"
        manifest.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="jimmyhq.apps.sync"):
        done = sync.keep_in_step(core)
    assert done is not None and done.broken == ["counter"]
    assert "app.tally.goal" in keys()
    assert not {k for k in keys() if k.startswith("app.counter.")}
    assert "The app counter is skipped" in caplog.text
    shown = {a["id"]: a["broken"] for a in install.listing(core)}
    assert shown["counter"] and shown["tally"] is None


def test_an_app_whose_settings_cannot_all_be_declared_has_none_of_them(core, tmp_path,
                                                                      monkeypatch):
    real = registry.declare

    def refuse_note(spec):
        if spec.key == "app.counter.note":
            raise ValueError("refused for the test")
        return real(spec)
    monkeypatch.setattr(registry, "declare", refuse_note)
    install_full(core, tmp_path / "a", "counter", settings=[GOAL, NOTE])
    install_full(core, tmp_path / "b", "tally", settings=[GOAL])
    assert not {k for k in keys() if k.startswith("app.counter.")}
    assert "app.counter" not in pages()
    assert "app.tally.goal" in keys()


def test_syncing_twice_changes_nothing(core, cfg, tmp_path):
    with elsewhere(cfg) as other:
        install_full(other, tmp_path / "a", "counter", settings=[GOAL])
        install_full(other, tmp_path / "b", "gleaner", permissions=["memory.source"],
                     memory_sources=[SOURCE])
    first = sync.sync(core)
    assert first.changed()
    before = (copy.copy(registry._DECLARED), list(registry._SECTIONS), app_sources())
    again = sync.sync(core)
    assert not again.changed()
    assert (registry._DECLARED, list(registry._SECTIONS), app_sources()) == before


def test_nothing_is_synced_while_nothing_was_installed_or_changed(core, tmp_path,
                                                                 monkeypatch):
    install_full(core, tmp_path, "counter", settings=[GOAL])
    sync.keep_in_step(core)
    ran = []
    monkeypatch.setattr(sync, "sync", lambda c: ran.append(c))
    core.settings.set("app.counter.goal", 3, actor="owner")  # the Tape moved, apps did not
    assert sync.keep_in_step(core) is None
    assert ran == []


def test_the_settings_command_sees_an_installed_apps_settings(cfg, tmp_path, capsys,
                                                             monkeypatch):
    from jimmyhq import cli

    with elsewhere(cfg) as other:
        install_full(other, tmp_path, "counter", settings=[GOAL])
    monkeypatch.setenv("JIMMYHQ_DATA", str(cfg.data))
    monkeypatch.setattr(cli.config, "load", lambda *a, **k: cfg)
    assert cli.main(["settings", "get", "app.counter.goal"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == 8


def test_the_worker_tick_brings_an_app_installed_elsewhere_into_step(core, cfg, tmp_path):
    from jimmyhq.apps import plugs

    with elsewhere(cfg) as other:
        install_folder(other, full_app(tmp_path, "counter", settings=[GOAL]))
    plugs.tick(core)
    assert "app.counter.goal" in keys()


# --- feeds between apps ---------------------------------------------------------------------

FEED = {"name": "articles", "kind": "drafted", "label": "Drafts",
        "description": "Drafts it chose to offer."}


def test_a_feed_added_and_printed_by_another_process_is_read_here_at_once(core, cfg,
                                                                           tmp_path):
    from jimmyhq.apps import bridge, feeds

    install_full(core, tmp_path / "one", "kindly")
    install_full(core, tmp_path / "one", "reader", permissions=["feed:kindly.articles"])
    reader = bridge.Ctx(core, bridge.find_app(core, "reader"))
    with pytest.raises(bridge.BridgeError, match="no longer offers"):
        feeds.feed_read(reader, {"app": "kindly", "feed": "articles"})
    with elsewhere(cfg) as other:
        updated(other, tmp_path, "kindly", "1.1.0", feeds=[FEED])
        schemas.declare(other, store.manifest_of(other, "kindly"), [KIND])
        other.tape.print("app.kindly.drafted", {"words": 3}, actor="app:kindly")
    assert not kinds_registry.is_schema_backed("app.kindly.drafted")
    reader = bridge.Ctx(core, bridge.find_app(core, "reader"))
    [item] = feeds.feed_read(reader, {"app": "kindly", "feed": "articles"})["items"]
    assert item["item"] == {"words": 3} and not item["erased"]
