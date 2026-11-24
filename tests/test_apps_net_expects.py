"""What an app expects of a host it names (``net_expects``): shown to the owner at install
in plain words, never more than the owner's settings, and a refusal that names the setting
when the owner's ceiling is lower than the app needs."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from jimmyhq import lookup
from jimmyhq.apps import bridge, manifest
from jimmyhq.apps import permissions as perms
from jimmyhq.apps.manifest import ManifestError
from test_apps_bridge_fakes import WATER, call, events, installs, refused
from test_lookup_fakes import PUBLIC, Names, Web

HOST = "images.example.com"
OTHER = "api.example.com"
NET = {**WATER, "permissions": [f"net:{HOST}", f"net:{OTHER}"]}


@pytest.fixture
def apps(monkeypatch, tmp_path):
    return installs(monkeypatch, tmp_path)


def expecting(**said):
    return {**NET, "net_expects": {HOST: said}}


def internet(core, pages):
    service = lookup.service(core)
    service.transport = Web(pages)
    service.resolver = Names({HOST: [PUBLIC], OTHER: [PUBLIC]})
    return service.transport


def slow(core, seconds: float):
    """A host that answers only after ``seconds``."""
    async def answer(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(seconds)
        return httpx.Response(200, text="late")

    service = lookup.service(core)
    service.transport = httpx.MockTransport(answer)
    service.resolver = Names({HOST: [PUBLIC], OTHER: [PUBLIC]})


# --- the manifest ----------------------------------------------------------------------------


def test_a_manifest_may_say_what_it_expects_of_a_host_it_names():
    got = manifest.parse(expecting(max_kb=20480, timeout_s=180))
    assert got.expects(HOST) == manifest.NetExpect(HOST, max_kb=20480, timeout_s=180)
    assert got.expects(OTHER) is None
    assert got.to_dict()["net_expects"] == {HOST: {"max_kb": 20480, "timeout_s": 180}}
    assert manifest.parse(got.to_dict()) == got
    assert "net_expects" not in manifest.parse(NET).to_dict()


def test_a_manifest_cannot_say_what_it_expects_of_a_host_it_does_not_name():
    with pytest.raises(ManifestError) as caught:
        manifest.parse({**NET, "net_expects": {"elsewhere.example.com": {"max_kb": 10}}})
    assert caught.value.fields == ("net_expects.elsewhere.example.com",)
    assert "net:elsewhere.example.com" in str(caught.value)


@pytest.mark.parametrize("said", [
    {}, {"max_kb": 0}, {"max_kb": 100_001}, {"max_kb": "big"}, {"max_kb": 2.5},
    {"timeout_s": 0}, {"timeout_s": 3601}, {"timeout_s": True}, {"retries": 3},
    {"max_kb": 10, "follow_redirects": True}, [10], "20 MB",
])
def test_an_expectation_out_of_bounds_or_of_another_shape_is_refused(said):
    with pytest.raises(ManifestError) as caught:
        manifest.parse({**NET, "net_expects": {HOST: said}})
    assert caught.value.fields[0].startswith(f"net_expects.{HOST}")


def test_net_expects_that_is_not_an_object_of_hosts_is_refused():
    for value in ([], {}, "x", None):
        with pytest.raises(ManifestError) as caught:
            manifest.parse({**NET, "net_expects": value})
        assert caught.value.fields == ("net_expects",)


def test_the_install_dialog_says_what_the_app_expects_in_plain_words():
    lines = {line.id: line.sentence for line in
             perms.describe(manifest.parse(expecting(max_kb=20480, timeout_s=180)))}
    assert lines[f"net:{HOST}"] == (
        f"Send data to and read data from {HOST}. It expects answers of up to 20 MB and to "
        "wait up to 3 minutes for one; the most JimmyHQ allows is yours to set under "
        "Settings, Apps.")
    assert lines[f"net:{OTHER}"] == f"Send data to and read data from {OTHER}."
    only_size = {line.id: line.sentence for line in
                 perms.describe(manifest.parse(expecting(max_kb=512)))}
    assert "answers of up to 512 KB;" in only_size[f"net:{HOST}"]
    only_wait = {line.id: line.sentence for line in
                 perms.describe(manifest.parse(expecting(timeout_s=45)))}
    assert "It expects to wait up to 45 seconds for one;" in only_wait[f"net:{HOST}"]


def test_an_expectation_is_not_a_new_consent():
    plain, said = manifest.parse(NET), manifest.parse(expecting(max_kb=20480, timeout_s=180))
    assert perms.fingerprint(plain) == perms.fingerprint(said)
    change = perms.diff(plain, said)
    assert change.added == () and change.widened == () and change.removed == ()


def test_sizes_and_waits_read_naturally():
    assert [perms.size_words(n) for n in (1, 1023, 1024, 1536, 20480)] == [
        "1 KB", "1023 KB", "1 MB", "1.5 MB", "20 MB"]
    assert [perms.wait_words(n) for n in (1, 59, 60, 90, 180)] == [
        "1 second", "59 seconds", "1 minute", "1.5 minutes", "3 minutes"]


# --- the limits at fetch time -----------------------------------------------------------------


def test_a_host_the_app_says_what_it_expects_of_gets_that_without_any_setting_changed(core, apps):
    apps.add(expecting(max_kb=20480, timeout_s=180))
    limits = bridge.fetch_limits(core, bridge.find_app(core, "water"), HOST, upload=False)
    assert (limits.most_kb, limits.wait_s) == (20480, 180)


def test_the_owners_ceiling_holds_whatever_the_app_expects(core, apps):
    apps.add(expecting(max_kb=20480, timeout_s=180))
    core.settings.set(bridge.FETCH_MAX_KB, 2048, actor="owner")
    core.settings.set(bridge.FETCH_TIMEOUT_S, 30, actor="owner")
    limits = bridge.fetch_limits(core, bridge.find_app(core, "water"), HOST, upload=False)
    assert (limits.most_kb, limits.wait_s) == (2048, 30)


def test_a_host_the_app_says_nothing_about_gets_the_owners_defaults_for_it(core, apps):
    apps.add(expecting(max_kb=10, timeout_s=5))
    limits = bridge.fetch_limits(core, bridge.find_app(core, "water"), OTHER, upload=False)
    assert (limits.most_kb, limits.wait_s) == (1024, 20)
    core.settings.set(bridge.FETCH_DEFAULT_KB, 4096, actor="owner")
    core.settings.set(bridge.FETCH_DEFAULT_S, 60, actor="owner")
    limits = bridge.fetch_limits(core, bridge.find_app(core, "water"), OTHER, upload=False)
    assert (limits.most_kb, limits.wait_s) == (4096, 60)


def test_a_large_answer_from_a_host_the_app_says_nothing_about_names_the_default_setting(
        core, apps):
    apps.add(NET)
    internet(core, {(HOST, "/picture"): httpx.Response(200, content=b"x" * 2_000_000)})
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/picture"})
    assert error.code == "too_large" and "net_expects" in error.message
    assert "Network answer size for a host an app says nothing about" in error.message
    assert error.owner_message and "Settings, Apps" in error.owner_message


def test_the_ceilings_go_high_enough_for_a_large_picture_and_a_slow_service(core):
    core.settings.set(bridge.FETCH_MAX_KB, 20 * 1024, actor="owner")
    core.settings.set(bridge.FETCH_TIMEOUT_S, 180, actor="owner")
    assert core.settings.get(bridge.FETCH_MAX_KB) == 20480
    assert core.settings.get(bridge.FETCH_TIMEOUT_S) == 180
    assert core.settings.get("apps.net_fetch_timeout_s") == 180


def test_a_large_answer_is_refused_naming_the_owners_setting_when_it_is_the_lower(core, apps):
    apps.add(expecting(max_kb=20480))
    core.settings.set(bridge.FETCH_MAX_KB, 2, actor="owner")
    internet(core, {(HOST, "/picture"): httpx.Response(200, content=b"x" * 5000)})
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/picture"})
    assert error.code == "too_large"
    assert "Largest network answer for an app (KB)" in error.message
    assert "apps.net_fetch_max_kb" in error.message and "20480 KB" in error.message
    assert error.owner_message and "Settings, Apps" in error.owner_message
    assert "xxxx" not in error.message and events(core, "apps.fetched") == []


def test_a_large_answer_is_refused_naming_the_manifest_when_it_expected_less(core, apps):
    apps.add(expecting(max_kb=2))
    core.settings.set(bridge.FETCH_MAX_KB, 1024, actor="owner")
    internet(core, {(HOST, "/picture"): httpx.Response(200, content=b"x" * 5000)})
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/picture"})
    assert error.code == "too_large" and "net_expects" in error.message
    assert "2 KB" in error.message and error.owner_message is None


def test_an_answer_within_what_was_expected_and_allowed_comes_back(core, apps):
    apps.add(expecting(max_kb=8))
    core.settings.set(bridge.FETCH_MAX_KB, 8, actor="owner")
    internet(core, {(HOST, "/picture"): httpx.Response(200, content=b"x" * 5000)})
    got = call(core, "water", "net.fetch", {"url": f"https://{HOST}/picture"})
    assert got["status"] == 200 and len(got["text"]) == 5000


def test_a_slow_host_is_stopped_at_the_owners_limit_and_the_refusal_names_it(core, apps):
    apps.add(expecting(timeout_s=180))
    core.settings.set(bridge.FETCH_TIMEOUT_S, 1, actor="owner")
    slow(core, 5)
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/make",
                                                 "timeout_s": 180})
    assert error.code == "unavailable"
    assert "Longest wait for an app's network request (seconds)" in error.message
    assert "apps.net_fetch_timeout_s" in error.message and "180 seconds" in error.message
    assert error.owner_message


def test_a_slow_host_is_stopped_at_what_the_manifest_expected_when_that_is_shorter(core, apps):
    apps.add(expecting(timeout_s=1))
    slow(core, 5)
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/make"})
    assert error.code == "unavailable" and "net_expects" in error.message
    assert error.owner_message is None


def test_an_app_that_asks_for_a_shorter_wait_is_told_it_was_its_own(core, apps):
    apps.add(NET)
    slow(core, 5)
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/make",
                                                 "timeout_s": 1})
    assert error.code == "unavailable" and "the wait you asked for" in error.message


# --- the app's page --------------------------------------------------------------------------


def test_the_apps_page_says_when_it_expects_more_than_the_owner_allows(core, tmp_path):
    from apps_install_helpers import example, install_folder, write_manifest
    from jimmyhq.apps import install

    folder = example(tmp_path, "water")
    write_manifest(folder, permissions=["tasks.write", f"net:{HOST}"],
                   net_expects={HOST: {"max_kb": 20480, "timeout_s": 180}})
    plan = install.plan_folder(core, folder)
    shown = {line["id"]: line["sentence"] for line in plan["permissions"]}
    assert "answers of up to 20 MB" in shown[f"net:{HOST}"]
    install_folder(core, folder)
    got = install.detail(core, "water")
    assert "answers of up to 20 MB" in {
        line["id"]: line["sentence"] for line in got["lines"]}[f"net:{HOST}"]
    assert got["net_notes"] == []  # the ceilings begin high enough
    core.settings.set(bridge.FETCH_MAX_KB, 1024, actor="owner")
    core.settings.set(bridge.FETCH_TIMEOUT_S, 20, actor="owner")
    assert install.detail(core, "water")["net_notes"] == [
        f"Water expects answers of up to 20 MB from {HOST}, and you allow 1 MB: larger ones "
        "are refused. Raise \"Largest network answer for an app (KB)\" under Settings, Apps, "
        "advanced, if you want them.",
        f"Water expects to wait up to 3 minutes for {HOST}, and you allow 20 seconds: a "
        "slower answer is cut off. Raise \"Longest wait for an app's network request "
        "(seconds)\" under Settings, Apps, advanced, if it needs longer."]
