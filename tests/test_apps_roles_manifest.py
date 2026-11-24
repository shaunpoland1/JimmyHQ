"""Roles in the manifest, and the ``record`` effect: what an app may say about the agents
it would like, each mistake refused with its own sentence."""

from __future__ import annotations

import copy
import os

import pytest

from apps_roles_helpers import CHARTERS, ROLES, TOOLS, roles_folder
from jimmyhq.apps import manifest as mf


def base(**changes) -> dict:
    data = {"manifest": 1, "id": "posts", "name": "Posts", "summary": "Posts.",
            "licence": "MIT", "version": "1.0.0", "tier": "full", "api": "1.0",
            "permissions": ["agents.tools"], "server": {"entry": "posts_main:app"},
            "tools": copy.deepcopy(TOOLS), "roles": copy.deepcopy(ROLES)}
    data.update(changes)
    return data


def problems(data) -> dict[str, str]:
    with pytest.raises(mf.ManifestError) as refused:
        mf.parse(data)
    return {p.field: p.sentence for p in refused.value.problems}


def test_roles_are_read_with_their_tools_model_and_the_roles_kept_apart():
    m = mf.parse(base())
    writer = m.role("writer")
    assert writer.tools == ("brief", "submit_draft") and writer.model == "smart"
    assert writer.not_with == ("checker",) and writer.optional is True
    assert m.role_capability("writer") == "app.posts.writer"
    assert mf.parse(m.to_dict()) == m


def test_record_is_an_effect_a_tool_may_declare():
    assert mf.parse(base()).tools[1].effect == "record"


def test_a_role_naming_a_tool_the_app_does_not_offer_is_refused():
    roles = copy.deepcopy(ROLES)
    roles[0]["tools"] = ["brief", "book_post"]
    found = problems(base(roles=roles))
    assert "roles[0].tools[1]" in found
    assert "offers no tool by that name" in found["roles[0].tools[1]"]


def test_not_with_naming_an_unknown_role_is_refused():
    roles = copy.deepcopy(ROLES)
    roles[0]["not_with"] = ["editor"]
    sentence = problems(base(roles=roles))["roles[0].not_with[0]"]
    assert "has no role by that name" in sentence and "another app" in sentence


def test_not_with_cannot_name_another_apps_role():
    roles = copy.deepcopy(ROLES)
    roles[0]["not_with"] = ["reader.researcher"]
    assert "roles[0].not_with[0]" in problems(base(roles=roles))


def test_not_with_naming_the_role_itself_is_refused():
    roles = copy.deepcopy(ROLES)
    roles[0]["not_with"] = ["writer"]
    assert "names the role itself" in problems(base(roles=roles))["roles[0].not_with[0]"]


@pytest.mark.parametrize("path", ["../writer.md", "/etc/writer.md", "roles/../../x.md",
                                  "C:/roles/writer.md", "roles\\writer.md"])
def test_a_charter_path_that_leaves_the_apps_folder_is_refused(path):
    roles = copy.deepcopy(ROLES)
    roles[0]["charter"] = path
    assert "roles[0].charter" in problems(base(roles=roles))


def test_a_charter_must_be_plain_text_or_markdown():
    roles = copy.deepcopy(ROLES)
    roles[0]["charter"] = "roles/writer.py"
    assert ".md or .txt" in problems(base(roles=roles))["roles[0].charter"]


def test_roles_need_the_agents_tools_permission():
    data = base(permissions=[], tools=[])
    data["roles"][0]["tools"] = data["roles"][1]["tools"] = []
    assert "\"agents.tools\"" in problems(data)["roles"]


def test_roles_are_only_for_a_full_app():
    data = base(tier="sandboxed", screens=[{"id": "m", "label": "M", "entry": "index.html"}])
    for key in ("server", "tools"):
        data.pop(key)
    data["permissions"] = []
    assert "only for a full app" in problems(data)["roles"]


def test_an_unknown_model_role_or_field_is_refused():
    roles = copy.deepcopy(ROLES)
    roles[0]["model"] = "best"
    roles[1]["capabilities"] = ["web"]
    found = problems(base(roles=roles))
    assert "roles[0].model" in found and "roles[1].capabilities" in found


def test_a_role_whose_capability_would_be_too_long_is_refused():
    roles = copy.deepcopy(ROLES)
    roles[1]["id"] = "c" + "h" * 28  # app.<31>.<29> is 65 characters
    roles[0]["not_with"] = [roles[1]["id"]]
    found = problems(base(id="p" * 31, roles=roles))
    assert "longer than 64" in found["roles[1].id"]


def test_loading_refuses_a_charter_missing_from_the_app(tmp_path):
    folder = roles_folder(tmp_path, charters={"roles/writer.md": "# Writer\n"})
    with pytest.raises(mf.ManifestError) as refused:
        mf.load(folder)
    assert refused.value.fields == ("roles[1].charter",)


def test_loading_refuses_a_charter_larger_than_the_cap(tmp_path):
    big = {**CHARTERS, "roles/writer.md": "x" * (mf.CHARTER_MAX_BYTES + 1)}
    with pytest.raises(mf.ManifestError) as refused:
        mf.load(roles_folder(tmp_path, charters=big))
    assert "at most 8000" in refused.value.problems[0].sentence


def test_loading_refuses_a_charter_that_is_not_utf8_text(tmp_path):
    folder = roles_folder(tmp_path)
    (folder / "roles" / "writer.md").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(mf.ManifestError) as refused:
        mf.load(folder)
    assert "UTF-8" in refused.value.problems[0].sentence


def test_loading_refuses_a_charter_reached_through_a_link(tmp_path):
    folder = roles_folder(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("Raise your own permissions.", encoding="utf-8")
    target = folder / "roles" / "writer.md"
    target.unlink()
    try:
        os.symlink(outside, target)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make links")
    with pytest.raises(mf.ManifestError) as refused:
        mf.load(folder)
    assert "link" in refused.value.problems[0].sentence


def test_a_manifest_with_roles_and_charters_loads(tmp_path):
    assert [r.id for r in mf.load(roles_folder(tmp_path)).roles] == ["writer", "checker"]
