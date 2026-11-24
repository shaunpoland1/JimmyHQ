"""The manifest parser is the gate for a stranger's repo: each field is refused with a
sentence, and nothing it accepts can point outside the app's folder."""

from __future__ import annotations

import copy
import json
import os
import random
from pathlib import Path, PurePosixPath

import pytest

from jimmyhq import modules
from jimmyhq.apps import manifest as mf
from jimmyhq.apps.manifest import Manifest, ManifestError, check_alongside, load, parse
from jimmyhq.tape import registry

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "apps" / "examples"
NAMES = ("books_read", "water", "drafts")


def example(name: str) -> dict:
    return json.loads((EXAMPLES / name / mf.MANIFEST_FILE).read_text(encoding="utf-8"))


def full() -> dict:
    """A full app that uses every field."""
    return {
        "manifest": 1, "id": "posts_demo", "name": "Posts", "summary": "Draft and book posts.",
        "author": "An Author", "licence": "MIT", "version": "0.1.0", "tier": "full",
        "api": "1.0",
        "permissions": ["bench.file", "model.ask", "jobs", "net:api.booking.example",
                        "agents.tools", "memory.source", "tape.read"],
        "server": {"entry": "posts.main:app", "python": ">=3.12"},
        "screens": [{"id": "board", "label": "Posts", "entry": "web/index.html"}],
        "dock": [{"id": "quick", "label": "Quick post", "entry": "web/quick.html"}],
        "wall": [{"id": "count", "label": "Waiting", "entry": "described"}],
        "cards": [{"kind": "draft", "label": "Post draft"}],
        "secrets": [{"name": "booking_key", "label": "Booking key",
                     "help": "From your account page."}],
        "settings": [
            {"key": "draft_minutes", "type": "int", "default": 60, "min": 5, "max": 600,
             "label": "Draft every (minutes)", "help": "How often to draft."},
            {"key": "tone", "type": "choice", "default": "plain", "label": "Tone",
             "help": "How drafts read.", "portable": False,
             "options": [{"value": "plain", "label": "Plain"},
                         {"value": "warm", "label": "Warm"}]},
            {"key": "days", "type": "list", "default": ["mon"], "label": "Days",
             "help": "When to draft.",
             "options": [{"value": "mon", "label": "Monday"},
                         {"value": "thu", "label": "Thursday"}]},
        ],
        "tables": {
            "post": {"label": "Posts", "was": ["entry"], "fields": {
                "title": {"type": "text", "required": True, "was": ["name"]},
                "state": {"type": "choice", "options": ["waiting", "queued"]},
                "cost": {"type": "money", "currency": "EUR", "min": 0},
                "words": {"type": "int", "min": 0, "max": 5000},
                "score": {"type": "number"},
                "sent": {"type": "bool"},
                "day": {"type": "date"},
                "channel": {"type": "link", "table": "channel"},
                "per_word": {"type": "computed", "formula": "cost / words"}}},
            "channel": {"fields": {"name": {"type": "text"}}},
        },
        "views": [{"id": "waiting", "label": "Waiting", "table": "post",
                   "fields": ["title", "state"], "sort": ["-day", "title"], "group": "state",
                   "filter": [{"field": "state", "op": "eq", "value": "waiting"},
                              {"field": "day", "op": "not_empty"}],
                   "totals": {"cost": "sum", "words": "avg"}}],
        "memory_sources": [{"id": "sent", "label": "Sent posts",
                            "description": "The posts you approved.", "default_on": False}],
        "tools": [{"name": "book", "description": "Book a post for a day.",
                   "params": {"post": {"type": "integer", "description": "The post."},
                              "slot": {"type": "string", "description": "When.",
                                       "enum": ["morning", "evening"]}},
                   "required": ["post"], "effect": "send"}],
        "private": False,
    }


def refused(data, field: str) -> str:
    """The sentence the parser gives for a field it must refuse."""
    with pytest.raises(ManifestError) as caught:
        parse(data)
    by_field = {p.field: p.sentence for p in caught.value.problems}
    assert field in by_field, f"expected {field!r} among {sorted(by_field)}"
    sentence = by_field[field]
    assert sentence.endswith((".", ")")) and len(sentence) > 20
    assert "Traceback" not in sentence
    return sentence


def changed(base: dict, **changes) -> dict:
    out = copy.deepcopy(base)
    for key, value in changes.items():
        if value is ...:
            out.pop(key, None)
        else:
            out[key] = value
    return out


# --- what is accepted -----------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_each_shipped_example_loads_from_its_folder(name):
    manifest = load(EXAMPLES / name)
    assert manifest.id == name and manifest.manifest == 1 and manifest.api == (1, 0)


def test_the_three_examples_are_a_described_a_sandboxed_and_a_full_app():
    books, water, drafts = (load(EXAMPLES / n) for n in NAMES)
    assert books.described and books.tables and books.views and not books.entry_files()
    assert water.tier == "sandboxed" and water.entry_files() == ("index.html",)
    assert drafts.tier == "full" and drafts.server.module == "drafts.main"
    assert drafts.allows("jobs") and drafts.cards[0].kind == "draft"
    assert drafts.settings[0].key == "draft_minutes" and drafts.screens[0].id == "board"


def test_a_manifest_using_every_field_is_accepted_and_keeps_what_was_written():
    manifest = parse(full())
    assert manifest.hosts == ("api.booking.example",)
    assert manifest.allows("net", host="api.booking.example")
    assert not manifest.allows("net", host="other.booking.example")
    assert manifest.tools[0].required == ("post",) and manifest.tools[0].effect == "send"
    assert manifest.tables["post"]["fields"]["cost"]["currency"] == "EUR"
    assert manifest.settings[1].options[1].label == "Warm" and not manifest.settings[1].portable


def test_what_an_app_owns_is_named_from_its_id():
    manifest = parse(full())
    assert manifest.actor == "app:posts_demo" and manifest.prefix == "app.posts_demo."
    assert manifest.setting_key("tone") == "app.posts_demo.tone"
    assert manifest.secret_name("booking_key") == "app.posts_demo.booking_key"
    assert manifest.card_kind("draft") == "app.posts_demo.draft"
    assert manifest.tool_name("book") == "app_posts_demo_book"
    assert manifest.source_id("sent") == "app_posts_demo_sent"


def test_an_app_setting_becomes_a_core_setting_under_the_apps_own_prefix():
    manifest = parse(full())
    goal, tone, days = (mf.core_setting(manifest, s) for s in manifest.settings)
    assert goal.key == "app.posts_demo.draft_minutes" and goal.section == "app.posts_demo"
    assert goal.check(5) == 5 and tone.choices == (("plain", "Plain"), ("warm", "Warm"))
    assert days.items == (("mon", "Monday"), ("thu", "Thursday")) and not tone.portable


def test_the_manifest_is_read_from_text_bytes_or_data_alike():
    data = example("water")
    text = json.dumps(data)
    raw = text.encode()
    assert parse(data) == parse(text) == parse(raw) == parse(b"\xef\xbb\xbf" + raw)


@pytest.mark.parametrize("data", [example(n) for n in NAMES] + [full()])
def test_an_accepted_manifest_survives_being_written_out_and_read_back(data):
    manifest = parse(data)
    assert parse(manifest.to_dict()) == manifest
    assert parse(manifest.to_json()) == manifest
    assert parse(manifest.to_dict()).to_dict() == manifest.to_dict()


def test_writing_a_manifest_out_does_not_hand_out_its_own_tables():
    manifest = parse(full())
    manifest.to_dict()["tables"]["post"]["fields"].clear()
    assert "title" in manifest.tables["post"]["fields"]


# --- the top-level fields, each refused with a sentence -------------------------------------


def test_an_unknown_field_is_refused_not_ignored():
    sentence = refused(changed(example("water"), colour="blue"), "colour")
    assert "unknown fields are refused" in sentence and "Remove it" in sentence


def test_an_unknown_field_inside_any_object_is_refused():
    data = full()
    data["screens"][0]["icon"] = "x"
    data["settings"][0]["secret"] = True
    data["server"]["args"] = []
    data["tools"][0]["params"]["post"]["default"] = 1
    data["tables"]["post"]["fields"]["title"]["max"] = 3
    data["views"][0]["limit"] = 5
    with pytest.raises(ManifestError) as caught:
        parse(data)
    assert {"screens[0].icon", "settings[0].secret", "server.args", "tools[0].params.post.default",
            "tables.post.fields.title.max", "views[0].limit"} <= set(caught.value.fields)


@pytest.mark.parametrize("key", mf._REQUIRED)
def test_each_required_field_is_asked_for_by_name_when_missing(key):
    sentence = refused(changed(example("water"), **{key: ...}), key)
    assert f'"{key}" is missing' in sentence and "Add" in sentence or "List" in sentence


def test_every_mistake_is_reported_at_once_not_only_the_first():
    data = changed(example("water"), id="Water!", version="1", name="")
    with pytest.raises(ManifestError) as caught:
        parse(data)
    assert {"id", "version", "name"} <= set(caught.value.fields)
    assert "nothing was installed or changed" in str(caught.value)


@pytest.mark.parametrize("value", [2, 0, "1", True, 1.0, None])
def test_only_format_version_one_is_read(value):
    assert "1" in refused(changed(example("water"), manifest=value), "manifest")


@pytest.mark.parametrize("value", ["a", "Water", "9lives", "wa-ter", "wa.ter", "a" * 32, "", 7,
                                   "wat er", "../x", "w\u00e4sser", "water\n"])
def test_an_id_that_does_not_fit_the_kind_name_rule_is_refused(value):
    assert "lower-case" in refused(changed(example("water"), id=value), "id")


@pytest.mark.parametrize("value", [*modules.MODULES, "app", "apps", "core", "jimmyhq", "owner",
                                   "system", "agent", "token", "con", "nul", "com1", "lpt9"])
def test_an_id_that_reads_as_part_of_the_core_or_a_device_is_refused(value):
    assert "keeps for itself" in refused(changed(example("water"), id=value), "id")


def test_no_core_kind_could_ever_fall_under_an_apps_prefix():
    import jimmyhq.kinds  # noqa: F401
    modules.load()
    kinds = registry.registered_kinds()
    # An installed app's own kinds are registered from its schema; none is the core's.
    assert kinds and not [k for k in kinds
                          if k.startswith("app.") and not registry.is_schema_backed(k)]
    with pytest.raises(ValueError):
        registry.kind("app.water.sip", example={})(registry.Payload)


@pytest.mark.parametrize("key,value", [
    ("name", ""), ("name", 5), ("name", "x" * 61), ("name", " Water"), ("name", "Wa\nter"),
    ("name", "Water\u202e"), ("name", "Wa\u200bter"), ("name", "Wa\x00ter"),
    ("summary", "x" * 201), ("summary", None), ("summary", ["a"]),
    ("author", "x" * 101), ("author", {}), ("licence", ""), ("licence", "x" * 61),
])
def test_text_must_be_one_short_line_of_ordinary_characters(key, value):
    refused(changed(example("water"), **{key: value}), key)


@pytest.mark.parametrize("value", ["1", "1.0", "v1.0.0", "1.0.0.0", "01.0.0", "1.0.0-", 1.0,
                                   "1.0.0 ", "1.0.0\n", "1.0.0/x", "1.0.0+" + "a" * 60, "latest"])
def test_a_version_that_is_not_semver_is_refused(value):
    assert "1.0.0" in refused(changed(example("water"), version=value), "version")


@pytest.mark.parametrize("value", ["1.0.0", "0.1.0", "10.20.30", "1.0.0-rc.1", "1.0.0+build.5"])
def test_semver_versions_are_accepted(value):
    assert parse(changed(example("water"), version=value)).version == value


@pytest.mark.parametrize("value", ["root", "Full", "", None, 1, ["full"]])
def test_a_tier_other_than_the_two_is_refused(value):
    assert "sandboxed" in refused(changed(example("water"), tier=value), "tier")


@pytest.mark.parametrize("value", ["1", "1.0.0", 1.0, "one.zero", "1.x", "01.0", "-1.0", "",
                                   "1.0\n"])
def test_an_api_that_is_not_major_dot_minor_is_refused(value):
    assert "major.minor" in refused(changed(example("water"), api=value), "api")


def test_an_app_that_needs_a_newer_api_is_told_it_needs_a_newer_jimmyhq():
    for value in ("1.1", "2.0"):
        sentence = refused(changed(example("water"), api=value), "api")
        assert "needs a newer JimmyHQ" in sentence and "Update JimmyHQ" in sentence
    assert parse(changed(example("water"), api="1.1"), api=(1, 3)).api == (1, 1)


def test_an_app_written_for_a_major_that_is_no_longer_served_is_refused():
    sentence = refused(changed(example("water"), api="0.9"), "api")
    assert "no longer serves" in sentence
    with pytest.raises(ManifestError):
        parse(example("water"), api=(2, 0))


# --- tier consistency -----------------------------------------------------------------------


@pytest.mark.parametrize("key,value", [
    ("card_subjects", ["reminder"]),
    ("server", {"entry": "water.main:app"}),
    ("cards", [{"kind": "sip", "label": "Sip"}]),
    ("memory_sources", [{"id": "log", "label": "Log", "description": "It.", "default_on": False}]),
    ("tools", [{"name": "sip", "description": "Sip.", "params": {}, "required": [],
                "effect": "read"}]),
    ("private", True), ("private", False), ("server", None),
])
def test_a_sandboxed_app_cannot_declare_server_code_or_plug_points(key, value):
    sentence = refused(changed(example("water"), **{key: value}), key)
    assert "only for a full app" in sentence and '"tier"' in sentence


@pytest.mark.parametrize("permission", ["jobs", "tape.read", "memory.source", "agents.tools"])
def test_a_sandboxed_app_cannot_ask_for_a_full_only_permission(permission):
    sentence = refused(changed(example("water"), permissions=[permission]), "permissions[0]")
    assert "only for a full app" in sentence and permission in sentence


def test_a_sandboxed_app_with_nothing_to_show_is_refused():
    assert "do nothing" in refused(changed(example("water"), screens=...), "screens")
    assert parse(changed(example("water"), screens=..., wall=example("water")["screens"]))


def test_a_full_app_without_server_code_is_refused():
    assert "sandboxed" in refused(changed(example("drafts"), server=...), "server")


def test_a_full_app_may_have_no_screens():
    assert parse(changed(example("drafts"), screens=...)).screens == ()


def test_tools_and_memory_sources_must_match_their_permissions_both_ways():
    data = full()
    assert "agents.tools" in refused(changed(data, tools=...), "permissions")
    assert "memory.source" in refused(changed(data, memory_sources=...), "permissions")
    fewer = [p for p in data["permissions"] if p != "agents.tools"]
    assert "agents.tools" in refused(changed(data, permissions=fewer), "tools")
    fewer = [p for p in data["permissions"] if p != "memory.source"]
    assert "memory.source" in refused(changed(data, permissions=fewer), "memory_sources")


def test_card_types_need_the_permission_to_file_cards():
    assert "bench.file" in refused(changed(example("drafts"), permissions=["jobs"]), "cards")


def test_a_private_app_cannot_offer_its_data_to_memory():
    assert "Memory" in refused(changed(full(), private=True), "private")
    data = changed(full(), private=True, memory_sources=..., tools=...)
    data["permissions"].remove("memory.source")
    data["permissions"].remove("agents.tools")
    assert parse(data).private is True


def test_a_private_app_cannot_offer_tools_to_agents():
    data = changed(full(), private=True, memory_sources=...)
    data["permissions"].remove("memory.source")
    assert "agents" in refused(data, "private")


def test_a_described_screen_needs_tables_and_views():
    assert "tables" in refused(changed(example("books_read"), views=...), "views")
    assert "tables" in refused(changed(example("books_read"), tables=..., views=...), "views")
    refused(changed(example("books_read"), tables=...), "views[0].table")


def test_derived_tool_and_source_names_must_fit_the_cores_limits():
    data = changed(full(), id="a" * 31)
    data["tools"][0]["name"] = "t" * 31
    data["memory_sources"][0]["id"] = "s" * 6
    with pytest.raises(ManifestError) as caught:
        parse(data)
    assert {"tools[0].name", "memory_sources[0].id"} <= set(caught.value.fields)


# --- permissions as the manifest writes them ------------------------------------------------


@pytest.mark.parametrize("value", ["tasks.read", {"tasks.read": True}, None, 3])
def test_permissions_must_be_a_list(value):
    assert "list" in refused(changed(example("water"), permissions=value), "permissions")


@pytest.mark.parametrize("value", ["root", "tasks", "tasks.*", "*", "Tasks.Read", "", 5, None,
                                   ["tasks.read"], "tasks.read:all", "always.full",
                                   "always.data", "net", "net:", "private.off"])
def test_a_permission_jimmyhq_does_not_know_is_refused_with_the_list_to_choose_from(value):
    refused(changed(example("water"), permissions=[value]), "permissions[0]")


def test_a_permission_listed_twice_is_refused():
    data = changed(example("water"), permissions=["tasks.read", "tasks.read"])
    assert "twice" in refused(data, "permissions[1]")


def test_too_many_hosts_are_refused():
    many = [f"net:h{n}.example.com" for n in range(17)]
    assert "16" in refused(changed(example("water"), permissions=many), "permissions")


# --- screens and paths ----------------------------------------------------------------------

LEAVING = ["../index.html", "web/../../index.html", "/etc/index.html", "/index.html",
           "..\\index.html", "web\\index.html", "C:/index.html", "c:index.html",
           "file:///index.html", "https://example.com/index.html", "//example.com/index.html",
           "./index.html", "web//index.html", "web/./index.html", "web/", ".hidden/index.html",
           "web/.index.html", "~/index.html", "web/index.html/", "a b/index.html",
           "web/%2e%2e/index.html", "con/index.html", "web/NUL.html", "aux.html",
           "web/index.html\x00.png", "index.html\n", "web\n/index.html",
           "web/index..html/x.", "x" * 197 + ".html", "", 5, None,
           ["index.html"], "..", "web/.."]


@pytest.mark.parametrize("entry", LEAVING)
@pytest.mark.parametrize("where", ["screens", "dock", "wall"])
def test_an_entry_that_could_leave_the_app_folder_is_refused(where, entry):
    screen = {"id": "main", "label": "Water", "entry": entry}
    data = changed(example("water"), **{where: [screen]})
    sentence = refused(data, f"{where}[0].entry")
    assert "inside the app" in sentence or "in the app" in sentence


@pytest.mark.parametrize("entry", ["app.js", "index.htm", "index", "page.svg", "index.html.exe"])
def test_an_entry_that_is_not_an_html_file_is_refused(entry):
    data = changed(example("water"), screens=[{"id": "main", "label": "Water", "entry": entry}])
    assert ".html" in refused(data, "screens[0].entry")


@pytest.mark.parametrize("screen,field", [
    ({"label": "Water", "entry": "index.html"}, "screens[0].id"),
    ({"id": "main", "entry": "index.html"}, "screens[0].label"),
    ({"id": "main", "label": "Water"}, "screens[0].entry"),
    ({"id": "Main", "label": "Water", "entry": "index.html"}, "screens[0].id"),
    ({"id": "main/../x", "label": "Water", "entry": "index.html"}, "screens[0].id"),
    ({"id": "main", "label": "x" * 41, "entry": "index.html"}, "screens[0].label"),
    ("index.html", "screens[0]"),
])
def test_a_screen_needs_an_id_a_label_and_an_entry(screen, field):
    refused(changed(example("water"), screens=[screen]), field)


def test_two_screens_cannot_share_an_id_and_the_lists_have_limits():
    one = {"id": "main", "label": "Water", "entry": "index.html"}
    assert "own id" in refused(changed(example("water"), screens=[one, one]), "screens")
    many = [{**one, "id": f"s{n}"} for n in range(13)]
    assert "12" in refused(changed(example("water"), screens=many), "screens")
    assert "list" in refused(changed(example("water"), screens={"main": one}), "screens")


def _accepted_paths(manifest: Manifest) -> list[str]:
    paths = list(manifest.entry_files())
    if manifest.server is not None:
        paths.append(manifest.server.module.replace(".", "/") + ".py")
    return paths


def _stays_inside(path: str) -> bool:
    pure = PurePosixPath(path)
    resolved = os.path.normpath(os.path.join("/app", path)).replace(os.sep, "/")
    return (not pure.is_absolute() and ".." not in pure.parts and "\\" not in path
            and ":" not in path and resolved.startswith("/app/"))


# --- settings and secrets -------------------------------------------------------------------

GOAL = {"key": "goal", "type": "int", "default": 8, "label": "Glasses", "help": "Your goal."}


@pytest.mark.parametrize("change,field", [
    ({"key": ...}, "settings[0].key"), ({"type": ...}, "settings[0].type"),
    ({"default": ...}, "settings[0].default"), ({"label": ...}, "settings[0].label"),
    ({"help": ...}, "settings[0].help"),
    ({"key": "Goal"}, "settings[0].key"), ({"key": "a.b"}, "settings[0].key"),
    ({"key": "local_models_only"}, "settings[0].key"),
    ({"type": "secret"}, "settings[0].type"), ({"type": ["int"]}, "settings[0].type"),
    ({"default": "eight"}, "settings[0].default"), ({"default": 8.5}, "settings[0].default"),
    ({"default": True}, "settings[0].default"), ({"default": None}, "settings[0].default"),
    ({"default": 0, "min": 1}, "settings[0].default"),
    ({"default": 31, "max": 30}, "settings[0].default"),
    ({"min": 5, "max": 1}, "settings[0].min"), ({"min": "1"}, "settings[0].min"),
    ({"max": 1.5}, "settings[0].max"), ({"max": True}, "settings[0].max"),
    ({"min": 1e400}, "settings[0].min"),
    ({"options": [{"value": "a", "label": "A"}]}, "settings[0].options"),
    ({"portable": "no"}, "settings[0].portable"), ({"label": ""}, "settings[0].label"),
    ({"advanced": "yes"}, "settings[0].advanced"),
    ({"help": "x" * 301}, "settings[0].help"),
    ({"type": "str", "min": 1, "default": "a"}, "settings[0].min"),
    ({"type": "choice", "default": "a"}, "settings[0].options"),
    ({"type": "choice", "default": "z", "options": [{"value": "a", "label": "A"}]},
     "settings[0].default"),
    ({"type": "choice", "default": "a", "options": []}, "settings[0].options"),
    ({"type": "choice", "default": "a", "options": ["a"]}, "settings[0].options[0]"),
    ({"type": "choice", "default": "a",
      "options": [{"value": "a", "label": "A"}, {"value": "a", "label": "B"}]},
     "settings[0].options"),
    ({"type": "list", "default": "a"}, "settings[0].default"),
    ({"type": "list", "default": ["z"], "options": [{"value": "a", "label": "A"}]},
     "settings[0].default"),
    ({"type": "bool", "default": 1}, "settings[0].default"),
    ({"type": "str", "default": "x" * 2001}, "settings[0].default"),
])
def test_a_setting_that_is_incomplete_or_breaks_its_own_rules_is_refused(change, field):
    setting = changed(GOAL, **change)
    refused(changed(example("water"), settings=[setting]), field)


def test_two_settings_cannot_share_a_key():
    assert "own key" in refused(changed(example("water"), settings=[GOAL, GOAL]), "settings")


def test_a_whole_number_default_is_kept_as_a_whole_number():
    manifest = parse(changed(example("water"), settings=[changed(GOAL, default=8.0)]))
    assert manifest.settings[0].default == 8 and isinstance(manifest.settings[0].default, int)


@pytest.mark.parametrize("secret,field", [
    ({"label": "Key", "help": "It."}, "secrets[0].name"),
    ({"name": "key", "help": "It."}, "secrets[0].label"),
    ({"name": "key", "label": "Key"}, "secrets[0].help"),
    ({"name": "app.other.key", "label": "Key", "help": "It."}, "secrets[0].name"),
    ({"name": "KEY", "label": "Key", "help": "It."}, "secrets[0].name"),
    ({"name": "key", "label": "Key", "help": "It.", "value": "x"}, "secrets[0].value"),
    ("key", "secrets[0]"),
])
def test_a_secret_needs_a_plain_name_a_label_and_help_and_never_carries_a_value(secret, field):
    refused(changed(example("water"), secrets=[secret]), field)


# --- full-tier fields -----------------------------------------------------------------------


@pytest.mark.parametrize("server,field", [
    ({}, "server.entry"), ("drafts.main:app", "server"),
    ({"entry": "drafts.main"}, "server.entry"), ({"entry": "drafts/main.py"}, "server.entry"),
    ({"entry": "drafts.main:app()"}, "server.entry"),
    ({"entry": "../drafts:app"}, "server.entry"), ({"entry": ".drafts:app"}, "server.entry"),
    ({"entry": "drafts..main:app"}, "server.entry"), ({"entry": "os;rm:app"}, "server.entry"),
    ({"entry": "drafts.main:app:x"}, "server.entry"),
    ({"entry": "drafts.main:app\n"}, "server.entry"),
    ({"entry": "drafts.main:app", "python": ">=3.12\n"}, "server.python"),
    ({"entry": 5}, "server.entry"),
    ({"entry": "jimmyhq.core:Core"}, "server.entry"),
    ({"entry": "jimmyhq_app:App"}, "server.entry"),
    ({"entry": "drafts.main:app", "python": "3.12"}, "server.python"),
    ({"entry": "drafts.main:app", "python": ">=3.12; rm"}, "server.python"),
    ({"entry": "drafts.main:app", "python": ">= 3.12"}, "server.python"),
    ({"entry": "drafts.main:app", "python": 3.12}, "server.python"),
    ({"entry": "drafts.main:app", "command": "sh"}, "server.command"),
])
def test_server_code_is_named_as_a_module_and_object_and_nothing_else(server, field):
    refused(changed(example("drafts"), server=server), field)


@pytest.mark.parametrize("card,field", [
    ({"label": "Draft"}, "cards[0].kind"), ({"kind": "draft"}, "cards[0].label"),
    ({"kind": "bench.decided", "label": "Draft"}, "cards[0].kind"),
    ({"kind": "Draft", "label": "Draft"}, "cards[0].kind"),
    ({"kind": "draft", "label": ""}, "cards[0].label"),
])
def test_a_card_type_needs_a_plain_kind_and_a_label(card, field):
    refused(changed(example("drafts"), cards=[card]), field)
    one = {"kind": "draft", "label": "Draft"}
    assert "own kind" in refused(changed(example("drafts"), cards=[one, one]), "cards")


@pytest.mark.parametrize("change,field", [
    ({"id": ...}, "memory_sources[0].id"), ({"label": ...}, "memory_sources[0].label"),
    ({"description": ...}, "memory_sources[0].description"),
    ({"default_on": ...}, "memory_sources[0].default_on"),
    ({"default_on": "yes"}, "memory_sources[0].default_on"),
    ({"id": "Sent"}, "memory_sources[0].id"), ({"collect": "x"}, "memory_sources[0].collect"),
])
def test_a_memory_source_needs_every_one_of_its_fields(change, field):
    data = full()
    data["memory_sources"] = [changed(data["memory_sources"][0], **change)]
    refused(data, field)


@pytest.mark.parametrize("change,field", [
    ({"name": ...}, "tools[0].name"), ({"description": ...}, "tools[0].description"),
    ({"params": ...}, "tools[0].params"), ({"required": ...}, "tools[0].required"),
    ({"effect": ...}, "tools[0].effect"),
    ({"name": "Book It"}, "tools[0].name"), ({"description": ""}, "tools[0].description"),
    ({"effect": "harmless"}, "tools[0].effect"), ({"effect": None}, "tools[0].effect"),
    ({"params": []}, "tools[0].params"),
    ({"params": {"Post": {"type": "integer", "description": "It."}}, "required": []},
     "tools[0].params.Post"),
    ({"params": {"thought": {"type": "string", "description": "It."}}, "required": []},
     "tools[0].params.thought"),
    ({"params": {"post": {"type": "object", "description": "It."}}}, "tools[0].params.post.type"),
    ({"params": {"post": {"type": "integer"}}}, "tools[0].params.post.description"),
    ({"params": {"post": {"type": "integer", "description": "It.", "enum": ["1"]}}},
     "tools[0].params.post.enum"),
    ({"params": {"post": {"type": "string", "description": "It.", "enum": []}}},
     "tools[0].params.post.enum"),
    ({"required": ["nothing"]}, "tools[0].required"),
    ({"required": [["post"]]}, "tools[0].required"),
    ({"required": ["post", "post"]}, "tools[0].required"),
    ({"required": "post"}, "tools[0].required"),
])
def test_a_tool_is_refused_unless_its_name_params_and_effect_are_all_sound(change, field):
    data = full()
    data["tools"] = [changed(data["tools"][0], **change)]
    refused(data, field)


def test_a_tools_effect_uses_the_same_words_as_hands():
    from jimmyhq.hands.provider import EFFECTS
    # "record" is the one more an app has: a tool that only writes into its own records.
    assert mf.EFFECTS == (*EFFECTS, "record")


def test_private_must_be_true_or_false():
    assert "true or false" in refused(changed(example("drafts"), private="yes"), "private")


# --- tables and views -----------------------------------------------------------------------


def _with_field(spec, name="extra") -> dict:
    data = full()
    data["tables"]["post"]["fields"][name] = spec
    return data


@pytest.mark.parametrize("spec,field", [
    ({}, "tables.post.fields.extra.type"), ("text", "tables.post.fields.extra"),
    ({"type": "html"}, "tables.post.fields.extra.type"),
    ({"type": "text", "required": "yes"}, "tables.post.fields.extra.required"),
    ({"type": "text", "min": 1}, "tables.post.fields.extra.min"),
    ({"type": "int", "min": 5, "max": 1}, "tables.post.fields.extra.min"),
    ({"type": "int", "min": 1.5}, "tables.post.fields.extra.min"),
    ({"type": "money"}, "tables.post.fields.extra.currency"),
    ({"type": "money", "currency": "euro"}, "tables.post.fields.extra.currency"),
    ({"type": "money", "currency": "EUR", "max": 9.99}, "tables.post.fields.extra.max"),
    ({"type": "choice"}, "tables.post.fields.extra.options"),
    ({"type": "choice", "options": []}, "tables.post.fields.extra.options"),
    ({"type": "choice", "options": ["a", "a"]}, "tables.post.fields.extra.options"),
    ({"type": "link"}, "tables.post.fields.extra.table"),
    ({"type": "link", "table": "nowhere"}, "tables.post.fields.extra.table"),
    ({"type": "computed"}, "tables.post.fields.extra.formula"),
    ({"type": "computed", "formula": "x" * 301}, "tables.post.fields.extra.formula"),
    ({"type": "computed", "formula": "1", "required": True},
     "tables.post.fields.extra.required"),
    ({"type": "text", "was": ["extra"]}, "tables.post.fields.extra.was"),
    ({"type": "text", "was": "old"}, "tables.post.fields.extra.was"),
    ({"type": "text", "label": 5}, "tables.post.fields.extra.label"),
])
def test_a_table_field_that_is_mistyped_or_incomplete_is_refused(spec, field):
    refused(_with_field(spec), field)


def test_table_and_field_names_are_plain_and_a_field_cannot_be_called_id():
    refused(_with_field({"type": "text"}, name="Bad Name"), "tables.post.fields.Bad Name")
    refused(_with_field({"type": "text"}, name="id"), "tables.post.fields.id")
    data = full()
    data["tables"]["Bad"] = {"fields": {"a": {"type": "text"}}}
    refused(data, "tables.Bad")
    data = full()
    data["tables"]["empty"] = {"fields": {}}
    refused(data, "tables.empty.fields")
    refused(changed(full(), tables=[]), "tables")


@pytest.mark.parametrize("change,field", [
    ({"id": ...}, "views[0].id"), ({"label": ...}, "views[0].label"),
    ({"table": ...}, "views[0].table"), ({"table": "nowhere"}, "views[0].table"),
    ({"fields": ["nothing"]}, "views[0].fields[0]"), ({"fields": []}, "views[0].fields"),
    ({"sort": ["-nothing"]}, "views[0].sort[0]"), ({"sort": "title"}, "views[0].sort"),
    ({"sort": [5]}, "views[0].sort[0]"), ({"group": "nothing"}, "views[0].group"),
    ({"totals": {"nothing": "sum"}}, "views[0].totals.nothing"),
    ({"totals": {"cost": "median"}}, "views[0].totals.cost"),
    ({"totals": ["cost"]}, "views[0].totals"),
    ({"filter": [{"field": "nothing", "op": "eq", "value": 1}]}, "views[0].filter[0].field"),
    ({"filter": [{"field": "state", "op": "like", "value": "w"}]}, "views[0].filter[0].op"),
    ({"filter": [{"field": "state", "op": "eq"}]}, "views[0].filter[0].value"),
    ({"filter": [{"field": "state", "op": "eq", "value": ["w"]}]}, "views[0].filter[0].value"),
    ({"filter": [{"field": "state", "op": "empty", "value": 1}]}, "views[0].filter[0].value"),
    ({"filter": {"state": "waiting"}}, "views[0].filter"),
])
def test_a_view_must_name_a_real_table_and_real_fields(change, field):
    data = full()
    data["views"] = [changed(data["views"][0], **change)]
    refused(data, field)


def test_two_views_cannot_share_an_id():
    data = full()
    data["views"] = data["views"] * 2
    assert "own id" in refused(data, "views")


# --- hostile text ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,word", [
    ("", "JSON"), ("{", "JSON"), ("[]", "object"), ('"water"', "object"), ("null", "object"),
    ('{"manifest": 1, "manifest": 1}', "twice"), ('{"manifest": NaN}', "number"),
    ('{"manifest": Infinity}', "number"), ("[" * 100_000, "deeply"),
    ("id = 'water'", "JSON"), ("manifest: 1", "JSON"),
])
def test_text_that_is_not_one_plain_json_object_is_refused(text, word):
    with pytest.raises(ManifestError) as caught:
        parse(text)
    assert word in str(caught.value)


def test_bytes_that_are_not_utf8_or_are_too_large_are_refused():
    with pytest.raises(ManifestError, match="UTF-8"):
        parse(b"\xff\xfe{}")
    with pytest.raises(ManifestError, match="larger than"):
        parse(b" " * (mf.MAX_BYTES + 1))
    with pytest.raises(ManifestError, match="larger than"):
        parse(" " * (mf.MAX_BYTES + 1))
    with pytest.raises(ManifestError):
        parse(5)  # type: ignore[arg-type]


def test_a_sentence_never_repeats_a_long_or_unprintable_value_in_full():
    sentence = refused(changed(example("water"), id="x\x1b[31m" + "A" * 5000), "id")
    assert "\x1b" not in sentence and len(sentence) < 400


JUNK = [None, True, False, 0, -1, 1.5, 1e308, "", " ", "x", "../../x", "/abs", "described", [],
        {}, [[]], [{}], {"a": {}}, ["x"], "x" * 3000, "\x00", "\u202e", 2**70, "full",
        "sandboxed", "net:*", ["net:localhost"], {"type": "int"}, {"entry": "../x.html"}]


def _spots(node, path=()):
    yield path
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _spots(value, (*path, key))
    elif isinstance(node, list):
        for n, value in enumerate(node):
            yield from _spots(value, (*path, n))


def _mutate(data: dict, rng: random.Random) -> dict:
    out = copy.deepcopy(data)
    for _ in range(rng.randint(1, 3)):
        spots = [p for p in _spots(out) if p]
        path = rng.choice(spots)
        parent = out
        for step in path[:-1]:
            parent = parent[step]
        action = rng.random()
        if action < 0.6:
            parent[path[-1]] = copy.deepcopy(rng.choice(JUNK))
        elif action < 0.8:
            del parent[path[-1]]
        elif isinstance(parent, dict):
            parent[rng.choice(["extra", "id", "entry", "__proto__", "x" * 80])] = rng.choice(JUNK)
        else:
            parent.append(copy.deepcopy(rng.choice(JUNK)))
    return out


def test_whatever_is_thrown_at_the_parser_it_refuses_in_sentences_or_accepts_something_sound():
    rng = random.Random(4)
    bases = [example(n) for n in NAMES] + [full()]
    accepted = 0
    for _ in range(4000):
        data = _mutate(rng.choice(bases), rng)
        try:
            manifest = parse(data)
        except ManifestError as error:
            assert error.problems
            assert all(p.sentence.strip() and len(p.sentence) < 1500 for p in error.problems)
            continue
        accepted += 1
        assert parse(manifest.to_dict()) == manifest
        assert parse(manifest.to_json()) == manifest
        assert all(_stays_inside(p) for p in _accepted_paths(manifest))
        assert manifest.id not in mf.RESERVED_IDS
        if manifest.tier == "sandboxed":
            assert manifest.server is None and not manifest.tools and not manifest.cards
            assert not manifest.memory_sources and not manifest.private
            assert not {"jobs", "tape.read", "memory.source", "agents.tools"} & set(
                manifest.permissions)
    assert accepted > 50  # the mutations are not so harsh that nothing is exercised


def test_the_parser_never_changes_what_it_was_given():
    data = full()
    before = copy.deepcopy(data)
    parse(data)
    assert data == before


# --- clashes with other installed apps ------------------------------------------------------


def _tool_app(app_id: str, tool: str, source: str | None = None) -> Manifest:
    data = changed(full(), id=app_id)
    data["tools"][0]["name"] = tool
    if source:
        data["memory_sources"][0]["id"] = source
    return parse(data)


def test_two_apps_whose_tools_or_sources_would_get_the_same_name_cannot_both_be_installed():
    installed = _tool_app("mail", "box_send", "box_in")
    new = _tool_app("mail_box", "send", "in")
    assert new.tool_name("send") == installed.tool_name("box_send")
    with pytest.raises(ManifestError) as caught:
        check_alongside(new, [installed])
    assert set(caught.value.fields) == {"tools[0].name", "memory_sources[0].id"}
    assert "Rename" in str(caught.value)


def test_an_app_does_not_clash_with_its_own_earlier_version_or_an_unrelated_app():
    check_alongside(_tool_app("mail", "send"), [_tool_app("mail", "send"), parse(example("water")),
                                                _tool_app("mail_box", "sent")])


# --- reading from a folder ------------------------------------------------------------------


def _app(tmp_path: Path, data: dict, files: tuple[str, ...]) -> Path:
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / mf.MANIFEST_FILE).write_text(json.dumps(data), encoding="utf-8", newline="\n")
    for name in files:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<!doctype html>", encoding="utf-8", newline="\n")
    return folder


def test_a_folder_without_a_manifest_is_not_an_app(tmp_path):
    with pytest.raises(ManifestError, match="not a JimmyHQ app"):
        load(tmp_path)


def test_a_screen_whose_file_is_not_in_the_folder_is_refused(tmp_path):
    with pytest.raises(ManifestError) as caught:
        load(_app(tmp_path, example("water"), ()))
    assert caught.value.fields == ("screens[0].entry",) and "no such file" in str(caught.value)
    with pytest.raises(ManifestError):
        folder = tmp_path / "other"
        (folder / "index.html").mkdir(parents=True)
        (folder / mf.MANIFEST_FILE).write_text(json.dumps(example("water")), encoding="utf-8")
        load(folder)


def test_a_full_app_whose_server_module_is_not_in_the_folder_is_refused(tmp_path):
    with pytest.raises(ManifestError) as caught:
        load(_app(tmp_path, example("drafts"), ("web/index.html",)))
    assert caught.value.fields == ("server.entry",)
    assert load(EXAMPLES / "drafts").server.entry == "drafts.main:app"


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow making links here")


def test_a_screen_that_is_a_link_to_a_file_outside_the_folder_is_refused(tmp_path):
    outside = tmp_path / "outside.html"
    outside.write_text("private", encoding="utf-8")
    folder = _app(tmp_path, example("water"), ())
    _link(folder / "index.html", outside)
    with pytest.raises(ManifestError, match="link"):
        load(folder)


def test_a_screen_reached_through_a_linked_folder_is_refused(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "index.html").write_text("private", encoding="utf-8")
    data = changed(example("water"),
                   screens=[{"id": "main", "label": "Water", "entry": "web/index.html"}])
    folder = _app(tmp_path, data, ())
    _link(folder / "web", elsewhere)
    with pytest.raises(ManifestError, match="link"):
        load(folder)
    with pytest.raises(ManifestError, match="link"):
        mf.inside(folder, "web/index.html")


def test_a_manifest_that_is_itself_a_link_is_refused(tmp_path):
    real = tmp_path / "real.json"
    real.write_text(json.dumps(example("books_read")), encoding="utf-8")
    folder = tmp_path / "app"
    folder.mkdir()
    _link(folder / mf.MANIFEST_FILE, real)
    with pytest.raises(ManifestError, match="link"):
        load(folder)


def test_an_oversized_manifest_file_is_refused_before_it_is_read(tmp_path):
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / mf.MANIFEST_FILE).write_bytes(b" " * (mf.MAX_BYTES + 1))
    with pytest.raises(ManifestError, match="larger than"):
        load(folder)


def test_inside_gives_the_real_file_for_a_sound_path_and_refuses_one_that_climbs_out(tmp_path):
    folder = _app(tmp_path, example("water"), ("index.html",))
    (tmp_path / "secret.html").write_text("private", encoding="utf-8")
    assert mf.inside(folder, "index.html") == (folder / "index.html").resolve()
    for path in ("../secret.html", "/etc/hosts", "index.html/../../secret.html"):
        with pytest.raises(ManifestError):
            mf.inside(folder, path)


def test_loading_an_app_never_runs_or_imports_its_code(tmp_path):
    marker = tmp_path / "ran"
    folder = _app(tmp_path, example("drafts"), ("web/index.html",))
    (folder / "drafts").mkdir()
    code = f"open({str(marker)!r}, 'w').close()\n"
    (folder / "drafts" / "__init__.py").write_text(code, encoding="utf-8")
    (folder / "drafts" / "main.py").write_text(code, encoding="utf-8")
    assert load(folder).id == "drafts"
    assert not marker.exists()


def test_an_app_may_declare_up_to_eighty_settings_and_no_more():
    def many(n: int) -> list[dict]:
        return [{"key": f"knob_{i}", "type": "int", "default": 1, "label": f"Knob {i}",
                 "help": "A number."} for i in range(n)]

    assert len(parse(changed(full(), settings=many(80))).settings) == 80
    assert "80" in refused(changed(full(), settings=many(81)), "settings")


def test_an_app_setting_marked_advanced_stays_behind_show_advanced_settings():
    data = full()
    data["settings"][0]["advanced"] = True
    manifest = parse(data)
    minutes, tone, _ = (mf.core_setting(manifest, s) for s in manifest.settings)
    assert minutes.advanced and not tone.advanced
    assert manifest.to_dict()["settings"][0]["advanced"] is True
    assert "advanced" not in manifest.to_dict()["settings"][1]


# --- the words a private app's cards may carry as a subject ------------------------------------


def private_filer() -> dict:
    data = changed(full(), private=True, memory_sources=..., tools=...)
    data["permissions"].remove("memory.source")
    data["permissions"].remove("agents.tools")
    return data


def test_a_private_app_declares_the_short_words_its_cards_may_carry_as_a_subject():
    data = changed(private_filer(), card_subjects=["reminder", "package"])
    manifest = parse(data)
    assert manifest.card_subjects == ("reminder", "package")
    assert manifest.to_dict()["card_subjects"] == ["reminder", "package"]
    assert "card_subjects" not in parse(private_filer()).to_dict()


@pytest.mark.parametrize("words", [
    ["a-free-word-of-many-letters"], ["Reminder"], ["thirteenletter"], ["with space"],
    ["reminder:12"], ["rent_due"], [""], [7], "reminder", ["reminder", "reminder"],
    [f"w{'a' * 11}"] * 2, ["abc"] * 11,
])
def test_a_card_subject_word_is_refused_unless_it_is_up_to_twelve_lower_case_letters(words):
    data = changed(private_filer(), card_subjects=words)
    with pytest.raises(ManifestError) as caught:
        parse(data)
    assert any(p.field.startswith("card_subjects") for p in caught.value.problems)


def test_card_subjects_belong_to_a_private_app_that_files_cards():
    plain = changed(full(), card_subjects=["reminder"])
    assert "private" in refused(plain, "card_subjects")
    silent = changed(private_filer(), card_subjects=["reminder"], cards=...)
    data = silent
    data["permissions"].remove("bench.file")
    assert "cards" in refused(data, "card_subjects")


def test_a_manifest_may_name_an_icon_from_the_fixed_set_and_nothing_else():
    base = {"manifest": 1, "id": "water", "name": "Water", "summary": "Count glasses of water.",
            "version": "1.0.0", "tier": "sandboxed", "api": "1.0", "licence": "MIT",
            "permissions": [],
            "screens": [{"id": "main", "label": "Water", "entry": "index.html"}]}
    assert parse(base).icon == ""
    named = parse({**base, "icon": "truck"})
    assert named.icon == "truck" and named.to_dict()["icon"] == "truck"
    assert parse(named.to_dict()) == named
    assert "icon" not in parse(base).to_dict()
    for bad in ("<svg onload=x>", "https://example.com/a.png", "TRUCK", "", 3, None, ["truck"]):
        with pytest.raises(ManifestError) as caught:
            parse({**base, "icon": bad})
        assert caught.value.fields == ("icon",)
        assert "one of" in str(caught.value)
    assert set(mf.ICONS) >= {"truck", "book"}


def test_every_app_that_ships_with_jimmyhq_names_an_icon_the_shell_draws():
    root = Path(__file__).resolve().parents[1] / "apps"
    for folder in sorted(p for p in root.iterdir() if (p / "jimmyhq.app.json").is_file()):
        made = parse((folder / "jimmyhq.app.json").read_text(encoding="utf-8"))
        assert made.icon in mf.ICONS, folder.name
