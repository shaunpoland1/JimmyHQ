"""An app that needs more storage asks for it in its manifest: the owner reads it at
install, it is given only with their consent, and they can change it afterwards."""

from __future__ import annotations

import pytest

from apps_install_helpers import example, install_folder, write_manifest
from jimmyhq.apps import install, quotas, storage_ask, update
from jimmyhq.apps import manifest as mf
from test_apps_bridge_fakes import WATER


def water_asking(tmp_path, mb, version=None, at="v1"):
    folder = example(tmp_path / at, "water")
    changes = {"storage_mb": mb} if mb is not None else {}
    if version:
        changes["version"] = version
    write_manifest(folder, **changes)
    return folder


def test_a_manifest_may_ask_for_storage_as_a_whole_number_of_megabytes():
    assert mf.parse({**WATER, "storage_mb": 500}).storage_mb == 500
    assert mf.parse(WATER).storage_mb is None
    assert mf.parse({**WATER, "storage_mb": 500}).to_dict()["storage_mb"] == 500


@pytest.mark.parametrize("value", [0, -1, 100_001, 1.5, "500", True, None])
def test_a_storage_ask_that_is_not_a_sensible_number_is_refused_with_a_sentence(value):
    with pytest.raises(mf.ManifestError) as caught:
        mf.parse({**WATER, "storage_mb": value})
    assert "storage_mb" in str(caught.value) and "megabytes" in str(caught.value)


def test_the_install_plan_shows_the_ask_as_a_sentence_only_when_it_is_more(core, tmp_path):
    plan = install.plan_folder(core, water_asking(tmp_path, 500))
    assert plan["storage"]["asked_mb"] == 500 and plan["storage"]["usual_mb"] == 50
    assert "500 MB" in plan["storage"]["sentence"] and "50 MB" in plan["storage"]["sentence"]
    assert "Settings, Apps" in plan["storage"]["sentence"]
    install.cancel(core, plan["plan"])
    assert install.plan_folder(core, water_asking(tmp_path, 10, at="v2"))["storage"] is None
    assert install.plan_folder(core, water_asking(tmp_path, None, at="v3"))["storage"] is None


def test_installing_gives_the_storage_asked_for_as_the_apps_own_setting(core, tmp_path):
    install_folder(core, water_asking(tmp_path, 500))
    assert quotas.limit(core, "water", "storage_mb") == 500
    assert core.settings.get(quotas.OVERRIDES) == ["water.storage_mb=500"]
    assert quotas.limit(core, "other", "storage_mb") == 50


def test_the_owner_can_change_it_afterwards_like_any_limit_for_one_app(core, tmp_path):
    install_folder(core, water_asking(tmp_path, 500))
    core.settings.set(quotas.OVERRIDES, ["water.storage_mb=80"], actor="owner")
    assert quotas.limit(core, "water", "storage_mb") == 80


def test_a_cancelled_install_gives_nothing(core, tmp_path):
    plan = install.plan_folder(core, water_asking(tmp_path, 500))
    install.cancel(core, plan["plan"])
    assert core.settings.get(quotas.OVERRIDES) == []


def test_an_update_that_asks_for_more_is_given_it_only_when_the_owner_presses_update(
        core, tmp_path):
    install_folder(core, water_asking(tmp_path, None))
    plan = update.plan_folder(core, "water", water_asking(tmp_path, 900, "1.1.0", at="v2"))
    assert plan["update"]["storage"]["asked_mb"] == 900
    update.confirm_update(core, plan["plan"], plan["fingerprint"], actor="system")
    assert quotas.limit(core, "water", "storage_mb") == 50  # by itself: nothing more
    plan = update.plan_folder(core, "water", water_asking(tmp_path, 900, "1.2.0", at="v3"))
    update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert quotas.limit(core, "water", "storage_mb") == 900


def test_an_install_never_lowers_what_the_owner_already_gave(core, tmp_path):
    core.settings.set(quotas.OVERRIDES, ["water.storage_mb=2000", "water.records=5"],
                      actor="owner")
    install_folder(core, water_asking(tmp_path, 500))
    assert core.settings.get(quotas.OVERRIDES) == ["water.storage_mb=2000", "water.records=5"]


def test_override_lines_replace_only_this_apps_storage_line():
    lines = ["water.storage_mb=60", "water.records=5", "tea.storage_mb=70"]
    assert storage_ask.override_lines(lines, "water", 500) == [
        "water.records=5", "tea.storage_mb=70", "water.storage_mb=500"]
