"""What an app's agent tools may do is part of what the owner agrees to. An update that
offers a new tool, moves a tool to a less guarded effect, or gives a role more tools asks
the owner again, so it never goes in by itself; one that only takes away or guards more
does not ask. From the attacker's side: an update that turns a "send" tool into a "read"
one to slip past the ladder."""

from __future__ import annotations

import hashlib
import json

import pytest

from apps_roles_helpers import ROLES, TOOLS, agent, install_roles_app, roles_folder, tool
from jimmyhq.apps import manifest, permissions, store, update
from jimmyhq.apps.plugs import tools as app_tools
from jimmyhq.apps.store import AppError

PERMS = ("agents.tools",)
WRITER_ALONE = [{**ROLES[0], "not_with": []}]  # the writer, in a version with no checker


def posts_manifest(**kw) -> manifest.Manifest:
    folder_tools = kw.pop("tools", TOOLS)
    folder_roles = kw.pop("roles", ROLES)
    return manifest.parse({
        "manifest": 1, "id": "posts", "name": "Posts", "summary": "A test app.",
        "licence": "MIT", "version": "1.0.0", "tier": "full", "api": "1.0",
        "permissions": list(PERMS), "server": {"entry": "posts_main:app"},
        "tools": folder_tools, "roles": folder_roles} | kw)


def newer(tmp_path, *, tools=None, roles=None, version: str = "1.1.0"):
    return roles_folder(tmp_path / version, tools=tools, roles=roles, version=version)


def by_itself(core, folder) -> dict:
    """As ``update.apply_by_itself`` puts a found update in place: with nobody asking."""
    plan = update.plan_folder(core, "posts", folder)
    try:
        update.confirm_update(core, plan["plan"], plan["fingerprint"], actor="system")
    except AppError as refused:
        assert refused.status == 403 and "only you can agree" in refused.detail
    return plan


def version(core) -> str:
    return store.get(core, "posts")["version"]


def with_effect(name: str, effect: str) -> list[dict]:
    return [tool(t["name"], effect) if t["name"] == name else t for t in TOOLS]


# --- the mark ---------------------------------------------------------------------------------


def test_the_mark_of_what_was_agreed_covers_each_tools_effect_and_each_roles_tools():
    mark = permissions.fingerprint(posts_manifest())
    assert mark == permissions.fingerprint(posts_manifest(tools=TOOLS[::-1], roles=ROLES[::-1]))
    assert mark != permissions.fingerprint(posts_manifest(tools=with_effect("publish", "read")))
    assert mark != permissions.fingerprint(posts_manifest(tools=[*TOOLS, tool("extra")]))
    wider = [{**ROLES[0], "tools": [*ROLES[0]["tools"], "publish"]}, ROLES[1]]
    assert mark != permissions.fingerprint(posts_manifest(roles=wider))
    reworded = [{**t, "description": "Other words."} for t in TOOLS]
    assert mark == permissions.fingerprint(posts_manifest(tools=reworded))


def test_an_app_that_offers_no_tool_keeps_the_mark_it_was_installed_with():
    plain = manifest.parse({
        "manifest": 1, "id": "plain", "name": "Plain", "summary": "A test app.",
        "licence": "MIT", "version": "1.0.0", "tier": "full", "api": "1.0",
        "permissions": ["tasks.read"], "server": {"entry": "plain_main:app"}})
    parts = {"v": 1, "id": "plain", "tier": "full", "private": False,
             "permissions": ["tasks.read"]}  # the mark as it was made before tools counted
    text = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert permissions.fingerprint(plain) == hashlib.sha256(text.encode("ascii")).hexdigest()


@pytest.mark.parametrize("effect", manifest.EFFECTS)
def test_every_effect_is_ranked_as_the_ladder_guards_it(effect):
    rank = permissions.effect_guard(effect)
    category = app_tools.ladder_category(effect)
    if category is None:
        assert rank < permissions.effect_guard("create")
    elif category in ("message_person", "money", "delete", "access"):
        assert rank == permissions.effect_guard("send")
    else:
        assert rank == permissions.effect_guard("change")


# --- an update ----------------------------------------------------------------------------------


def test_an_update_that_turns_a_sending_tool_into_a_reading_one_waits_for_the_owner(
        core, tmp_path):
    install_roles_app(core, tmp_path, permissions=PERMS)
    core.settings.set("apps.auto_update", "auto", actor="owner")
    plan = by_itself(core, newer(tmp_path, tools=with_effect("publish", "read")))
    assert plan["update"]["needs_consent"] is True
    [line] = plan["update"]["ask"]
    assert line["permission"] == permissions.TOOL_OFFERED and line["host"] == "publish"
    assert "app_posts_publish" in line["sentence"] and "\"read\"" in line["sentence"]
    assert version(core) == "1.0.0"


@pytest.mark.parametrize(("before", "after"), [
    ("change", "record"), ("record", "read"), ("delete", "update"), ("access", "read")])
def test_an_update_that_moves_a_tool_to_any_less_guarded_effect_waits_for_the_owner(
        core, tmp_path, before, after):
    install_roles_app(core, tmp_path, permissions=PERMS, tools=with_effect("publish", before))
    plan = by_itself(core, newer(tmp_path, tools=with_effect("publish", after)))
    assert [x["id"] for x in plan["update"]["ask"]] == [f"{permissions.TOOL_OFFERED}:publish"]
    assert version(core) == "1.0.0"


def test_an_update_that_offers_a_new_tool_waits_for_the_owner(core, tmp_path):
    install_roles_app(core, tmp_path, permissions=PERMS)
    plan = by_itself(core, newer(tmp_path, tools=[*TOOLS, tool("tidy", "read")]))
    [line] = plan["update"]["ask"]
    assert line["id"] == f"{permissions.TOOL_OFFERED}:tidy"
    assert "app_posts_tidy" in line["sentence"]
    assert version(core) == "1.0.0"


def test_an_update_that_gives_a_role_another_tool_waits_for_the_owner(core, tmp_path):
    install_roles_app(core, tmp_path, permissions=PERMS)
    wider = [{**ROLES[0], "tools": [*ROLES[0]["tools"], "publish"]}, ROLES[1]]
    plan = by_itself(core, newer(tmp_path, roles=wider))
    [line] = plan["update"]["ask"]
    assert line["id"] == f"{permissions.ROLE_TOOLS}:writer"
    assert "Writer" in line["sentence"] and "app_posts_publish" in line["sentence"]
    assert version(core) == "1.0.0"


def test_an_update_with_a_new_role_an_agent_already_holds_waits_for_the_owner(core, tmp_path):
    install_roles_app(core, tmp_path, permissions=PERMS, roles=WRITER_ALONE)
    agent(core, "agent-a", caps=["app.posts.checker"])  # left behind from an earlier version
    plan = by_itself(core, newer(tmp_path, roles=ROLES))
    assert [x["id"] for x in plan["update"]["ask"]] == [f"{permissions.ROLE_TOOLS}:checker"]
    assert version(core) == "1.0.0"


@pytest.mark.parametrize("change", ["more_guarded", "tool_removed", "role_narrowed",
                                    "new_role_nobody_holds", "reworded"])
def test_an_update_that_only_guards_more_or_takes_away_goes_in_by_itself(
        core, tmp_path, change):
    install_roles_app(core, tmp_path, permissions=PERMS, roles=WRITER_ALONE if
                      change == "new_role_nobody_holds" else None)
    tools_, roles_ = TOOLS, ROLES
    if change == "more_guarded":
        tools_ = with_effect("brief", "send")
    elif change == "tool_removed":
        tools_ = [t for t in TOOLS if t["name"] != "publish"]
    elif change == "role_narrowed":
        roles_ = [{**ROLES[0], "tools": ["submit_draft"]}, ROLES[1]]
    elif change == "reworded":
        tools_ = [{**t, "description": "Other words."} for t in TOOLS]
    plan = by_itself(core, newer(tmp_path, tools=tools_, roles=roles_))
    assert plan["update"]["needs_consent"] is False
    assert version(core) == "1.1.0"


def test_an_app_installed_under_the_old_mark_updates_with_its_tools_unchanged_by_itself(
        core, tmp_path, monkeypatch):
    def old_rule(m) -> str:  # the mark before tools counted, as an earlier JimmyHQ kept it
        parts = {"v": 1, "id": m.id, "tier": m.tier, "private": bool(m.private),
                 "permissions": sorted(set(m.permissions))}
        text = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(text.encode("ascii")).hexdigest()

    with monkeypatch.context() as patched:
        patched.setattr(permissions, "fingerprint", old_rule)
        install_roles_app(core, tmp_path, permissions=PERMS)
    kept = store.get(core, "posts")["fingerprint"]
    assert kept != permissions.fingerprint(store.manifest_of(core, "posts"))
    plan = by_itself(core, newer(tmp_path))
    assert plan["update"]["needs_consent"] is False
    assert version(core) == "1.1.0"


def test_the_owner_agreeing_to_a_less_guarded_tool_puts_it_in_place(core, tmp_path):
    install_roles_app(core, tmp_path, permissions=PERMS)
    plan = update.plan_folder(core, "posts", newer(tmp_path, tools=with_effect("publish",
                                                                                  "read")))
    update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert version(core) == "1.1.0"
    effect = {t.name: t.effect for t in store.manifest_of(core, "posts").tools}["publish"]
    assert effect == "read"
