"""A picture handed over twice is not lost, and a key made for one address on the owner's own
network goes to no other."""

from __future__ import annotations

import pytest
from imagery_service import KEY, own_image, tiny_png
from jimmyhq_app import CallError

LOCAL = "http://192.0.2.10:8000"
OTHER = "http://192.0.2.11:8000"
KEY_OWN = "k-test-SECRET-own"


# --- a starting picture whose ref the gallery already holds ------------------------------------


@pytest.fixture
def gem(make_rig):
    return make_rig(provider="gemini")


def test_handing_over_the_same_starting_picture_twice_keeps_one_picture_and_its_file(gem):
    given = gem.core.upload(tiny_png(41), "mine.png")
    first = gem.core.call("reference.add", {"ref": given["ref"], "name": "mine.png"})
    again = gem.core.call("reference.add", {"ref": given["ref"], "name": "other.png"})
    assert again["id"] == first["id"] and len(gem.pictures()) == 1
    assert (gem.core.data_dir / given["ref"]).read_bytes() == tiny_png(41)


def test_a_ref_that_belongs_to_a_picture_already_here_is_never_wiped_by_a_refused_add(gem):
    given = gem.core.upload(tiny_png(42), "mine.png")
    first = gem.core.call("reference.add", {"ref": given["ref"], "name": "mine.png"})
    gem.core.settings["reference_max_mb"] = 0  # the checks below would now refuse and wipe
    gem.ctx.forget_settings()
    again = gem.core.call("reference.add", {"ref": given["ref"], "name": "mine.png"})
    assert again["id"] == first["id"]
    assert (gem.core.data_dir / given["ref"]).read_bytes() == tiny_png(42)
    assert [p["id"] for p in gem.pictures()] == [first["id"]]


def test_a_ref_of_a_picture_imagery_made_is_not_taken_in_again_or_wiped(make_rig):
    rig = make_rig()  # Practice makes a picture
    rig.make("A red kite")
    [made] = rig.pictures()
    ref = rig.store.picture(made["id"])["ref"]
    rig.core.settings["reference_max_mb"] = 0
    rig.ctx.forget_settings()
    again = rig.core.call("reference.add", {"ref": ref, "name": "x.png"})
    assert again["id"] == made["id"] and again["kind"] != "given"
    assert len(rig.pictures()) == 1 and (rig.core.data_dir / ref).exists()


# --- the key for an address on the owner's own network ----------------------------------------


@pytest.fixture
def added(make_rig):
    rig = make_rig(provider="openai_compatible", settings={"openai_base": LOCAL + "/v1"},
                   permissions=["agents.tools", "net:192.0.2.10:8000", "net:192.0.2.11:8000"],
                   secrets={"gemini_key": KEY, "own_network_key": KEY_OWN})
    rig.service.on(f"{LOCAL}/v1/images/generations", own_image(tiny_png(7)))
    rig.service.on(f"{OTHER}/v1/images/generations", own_image(tiny_png(8)))
    return rig


def point_at(rig, base):
    rig.core.settings["openai_base"] = base
    rig.ctx.forget_settings()


def test_the_key_is_sent_to_the_address_it_was_first_sent_to_again_and_again(added):
    added.make()
    added.make()
    assert [s["headers"]["Authorization"] for s in added.service.sent] == [f"Bearer {KEY_OWN}"] * 2


def test_the_key_is_not_sent_when_the_address_in_the_settings_has_changed_to_another(added):
    added.make()
    point_at(added, OTHER + "/v1")
    added.make()
    assert [s["url"] for s in added.service.sent] == [f"{LOCAL}/v1/images/generations"]
    said = added.last_run()["said"]
    assert f"typed for {LOCAL}" in said and f"now name {OTHER}" in said
    assert KEY_OWN not in said and added.last_run()["state"] == "failed"
    assert KEY_OWN not in repr(added.service.sent[1:])


def test_clearing_the_key_and_typing_it_again_moves_it_to_the_new_address(added):
    added.make()
    point_at(added, OTHER + "/v1")
    added.core.secrets.pop("own_network_key")  # the owner clears it
    added.make()  # asked without a key; Imagery sees it is gone
    assert "Authorization" not in added.service.sent[-1]["headers"]
    added.core.secrets["own_network_key"] = KEY_OWN  # and types it again
    added.make()
    assert added.service.sent[-1]["url"].startswith(OTHER)
    assert added.service.sent[-1]["headers"]["Authorization"] == f"Bearer {KEY_OWN}"


def test_a_different_key_typed_for_the_new_address_is_sent_there(added):
    added.make()
    point_at(added, OTHER + "/v1")
    added.core.secrets["own_network_key"] = "k-test-SECRET-second"
    added.make()
    assert added.service.sent[-1]["headers"]["Authorization"] == "Bearer k-test-SECRET-second"


def test_the_record_of_which_address_a_key_is_for_holds_no_key(added):
    added.make()
    kept = b"".join(p.read_bytes() for p in added.core.data_dir.glob("keybind.db*"))
    assert kept and KEY_OWN.encode() not in kept


def test_a_public_hosts_key_is_still_the_one_typed_beside_that_host_and_never_the_own_network_one(
        make_rig):
    rig = make_rig(provider="openai_compatible", secrets={"own_network_key": KEY_OWN})
    rig.service.on("https://images.example.com/v1/images/generations", own_image(tiny_png(9)))
    rig.make()
    assert rig.service.sent[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert KEY_OWN not in repr(rig.service.sent)


def test_the_call_that_adds_a_picture_still_refuses_a_ref_that_is_not_one(gem):
    with pytest.raises(CallError):
        gem.core.call("reference.add", {"ref": "uploads/" + "0" * 32 + ".png", "name": "x"})
