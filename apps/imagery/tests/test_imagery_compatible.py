"""Your own server: an OpenAI-style images endpoint at an address the owner sets."""

from __future__ import annotations

import pytest
from imagery.providers import openai_compatible as oc
from imagery.providers.net import not_added_sentence
from imagery_rig import OWN_BASE
from imagery_service import (
    KEY,
    OWN_HOST,
    no_answer,
    not_added,
    own_error,
    own_image,
    reply,
    tiny_png,
)
from jimmyhq_app import CallError

URL = f"{OWN_BASE}/images/generations"
LOCAL = "http://192.0.2.10:8000"  # a documentation address, as a server at home would be
LOCAL_URL = f"{LOCAL}/v1/images/generations"


@pytest.fixture
def own(make_rig):
    return make_rig(provider="openai_compatible")


def failed(rig) -> str:
    run = rig.last_run()
    assert run["state"] == "failed" and run["made"] == 0, run
    return run["said"]


def test_the_request_goes_to_images_generations_with_the_documented_fields(own):
    own.service.on(URL, own_image())
    own.make("A red kite", aspect="3:2", count=1)
    [sent] = own.service.sent
    assert sent["url"] == URL and sent["method"] == "POST"
    assert sent["headers"]["Authorization"] == f"Bearer {KEY}"
    assert sent["json"] == {"model": "gpt-image-1-mini", "prompt": "A red kite", "n": 1,
                            "size": "1536x1024", "response_format": "b64_json"}


def test_the_picture_comes_back_as_base64_and_is_kept(own):
    own.service.on(URL, own_image(tiny_png(5)))
    own.make("A red kite")
    [row] = own.pictures()
    assert row["provider"] == "openai_compatible" and row["model"] == "gpt-image-1-mini"
    assert next(p for p in own.files()).read_bytes() == tiny_png(5)


def test_several_pictures_are_several_requests_of_one_each(own):
    own.service.on(URL, own_image(tiny_png(1)), own_image(tiny_png(2)))
    own.make("A red kite", count=2)
    assert [s["json"]["n"] for s in own.service.sent] == [1, 1]
    assert len(own.pictures()) == 2


def test_the_ask_for_base64_field_can_be_left_out_for_servers_that_refuse_it(make_rig):
    rig = make_rig(provider="openai_compatible", settings={"openai_ask_base64": False})
    rig.service.on(URL, own_image())
    rig.make()
    assert "response_format" not in rig.service.sent[0]["json"]


def test_a_server_that_needs_no_key_is_asked_without_one(make_rig):
    rig = make_rig(provider="openai_compatible")
    rig.core.host_secrets.clear()
    rig.service.on(URL, own_image())
    rig.make()
    assert "Authorization" not in rig.service.sent[0]["headers"]


def test_the_shapes_offered_are_the_ones_the_sizes_setting_maps(own):
    assert own.overview()["provider"]["aspects"] == ["1:1", "3:2", "2:3"]
    with pytest.raises(CallError):
        own.make(aspect="16:9")
    assert own.core.fetched == []


def test_an_address_the_owner_has_not_added_under_their_own_addresses_is_not_used(make_rig):
    rig = make_rig(provider="openai_compatible", settings={
        "openai_base": "https://other.example.org/v1"})
    with pytest.raises(CallError) as refused:
        rig.make()
    assert "Add other.example.org under Your own addresses" in refused.value.message
    assert rig.core.fetched == []


@pytest.mark.parametrize("text", [
    "", "ftp://images.example.com/v1", "https://user:pw" + "@images.example.com/v1",
    "http://192.0.2.10:8000/v1?x=1", "https://images.example.com/v1#top", "not an address",
    "http://192.0.2.10:notaport/v1"])
def test_an_address_that_is_not_a_plain_http_or_https_address_is_refused_with_a_sentence(text):
    found = oc.parse_base(text)
    assert isinstance(found, str) and "Nothing was sent" in found


def test_an_address_is_read_into_its_host_and_a_base_without_a_trailing_slash():
    found = oc.parse_base("https://Images.Example.com/v1/")
    assert found == oc.Target(OWN_HOST, OWN_BASE, f"https://{OWN_HOST}", False)
    assert oc.parse_base("https://images.example.com").base == f"https://{OWN_HOST}"
    assert oc.parse_base("https://images.example.com:443/v1").own_net is False


@pytest.mark.parametrize("text,origin,base", [
    (LOCAL + "/v1", LOCAL, LOCAL + "/v1"),
    (LOCAL + "/v1/", LOCAL, LOCAL + "/v1"),
    ("http://192.0.2.10/v1", "http://192.0.2.10", "http://192.0.2.10/v1"),
    ("HTTP://Localhost:8000", "http://localhost:8000", "http://localhost:8000"),
    ("https://images.example.com:8443/v1", "https://images.example.com:8443",
     "https://images.example.com:8443/v1"),
    ("http://[::1]:8000/v1", "http://[::1]:8000", "http://[::1]:8000/v1")])
def test_http_or_a_port_makes_an_address_on_the_owners_own_network_with_its_exact_origin(
        text, origin, base):
    found = oc.parse_base(text)
    assert found.own_net is True and found.origin == origin and found.base == base


def test_a_public_address_needs_its_host_added_but_a_local_one_is_not_checked_first(make_rig):
    local = make_rig(provider="openai_compatible", settings={"openai_base": LOCAL + "/v1"})
    assert local.overview()["provider"]["problem"] is None
    assert local.ctx.provider().hosts(local.ctx.cfg()) == ()
    public = make_rig(provider="openai_compatible")
    assert public.ctx.provider().hosts(public.ctx.cfg()) == (OWN_HOST,)


@pytest.fixture
def added(make_rig):
    """The owner added the server's exact address under Addresses on your own network (the
    fake core lets a fetch through to the origin it is given a permission for)."""
    rig = make_rig(provider="openai_compatible", settings={"openai_base": LOCAL + "/v1"},
                   permissions=["agents.tools", f"net:{LOCAL.split('//')[1]}"],
                   secrets={"gemini_key": KEY, "own_network_key": "k-test-SECRET-own"})
    rig.service.on(LOCAL_URL, own_image(tiny_png(7)))
    return rig


def test_a_server_on_the_owners_own_network_is_asked_over_http_at_its_exact_address(added):
    added.make("A red kite", aspect="3:2")
    [sent] = added.service.sent
    assert sent["url"] == LOCAL_URL and sent["method"] == "POST"
    assert sent["json"]["size"] == "1536x1024"
    [row] = added.pictures()
    assert row["provider"] == "openai_compatible"
    assert next(p for p in added.files()).read_bytes() == tiny_png(7)
    assert added.overview()["today"]["used"] == 1


def test_a_server_on_the_own_network_gets_the_key_made_for_it_and_never_a_public_hosts_key(added):
    added.make()
    assert added.service.sent[0]["headers"]["Authorization"] == "Bearer k-test-SECRET-own"
    assert KEY not in repr(added.service.sent)


def test_a_server_on_the_own_network_that_needs_no_key_is_asked_without_one(added):
    added.core.secrets.pop("own_network_key")
    added.make()
    assert "Authorization" not in added.service.sent[0]["headers"]


def test_a_server_on_the_own_network_that_does_not_take_the_key_says_which_key_to_check(added):
    added.service.on(LOCAL_URL, own_error(401, "No."))
    added.make()
    said = failed(added)
    assert "Key for a server on your own network" in said and "Your own addresses" not in said


def test_an_address_the_owner_has_not_added_yet_says_where_to_add_it_and_nothing_is_sent(make_rig):
    rig = make_rig(provider="openai_compatible", settings={"openai_base": LOCAL + "/v1"})
    rig.service.on(LOCAL_URL, own_image())
    rig.make()
    said = failed(rig)
    assert f"did not let Imagery reach {LOCAL}, so nothing was sent" in said
    assert "Settings, Apps, Imagery" in said and "\"Addresses on your own network\"" in said
    assert f"add exactly {LOCAL} there" in said
    assert rig.overview()["today"]["used"] == 0


def test_the_not_added_sentence_does_not_repeat_what_jimmyhq_already_told_about_where_to_add_it():
    real = not_added().message
    said = not_added_sentence(LOCAL, LOCAL, real)
    assert said.count("Addresses on your own network") == 1
    assert "JimmyHQ said" not in said
    other = not_added_sentence(LOCAL, LOCAL, "The address now leads somewhere it may not.")
    assert "JimmyHQ said: The address now leads somewhere it may not." in other


def test_a_server_that_is_off_or_unreachable_is_told_as_not_answering_not_as_not_added(added):
    added.service.on(LOCAL_URL, no_answer(f"{LOCAL} could not be reached, so nothing came back."))
    added.make()
    said = failed(added)
    assert "did not answer in time, or could not be reached" in said
    assert "check that it is running" in said and "Addresses on your own network" not in said


def test_a_key_is_never_put_in_a_sentence_about_an_own_network_server(added):
    added.service.on(LOCAL_URL, reply(400, {"error": "bad key k-test-SECRET-own"}))
    added.make()
    assert "k-test-SECRET-own" not in failed(added)
    assert "k-test-SECRET-own" not in added.wherever_it_could_be_written().replace(
        "'Authorization': 'Bearer k-test-SECRET-own'", "")


def test_an_answer_with_only_an_address_is_not_followed_and_says_what_to_change(own):
    own.service.on(URL, reply(200, {"data": [{"url": "https://cdn.example.net/x.png"}]}))
    own.make()
    said = failed(own)
    assert "address for the picture instead of the picture itself" in said
    assert len(own.service.sent) == 1


def test_a_content_policy_error_is_told_as_a_refusal_of_the_words(own):
    own.service.on(URL, own_error(400, "Your request was rejected.",
                                  code="content_policy_violation"))
    own.make()
    said = failed(own)
    assert "would not make this picture" in said and "Change the words" in said


@pytest.mark.parametrize("status,part", [
    (401, "did not accept the key"), (403, "did not accept the key"),
    (429, "over its limit or out of credit"), (404, "no images endpoint"),
    (503, "busy or having trouble"), (504, "took too long")])
def test_each_status_from_the_server_is_told_in_plain_words(own, status, part):
    own.service.on(URL, own_error(status, "Something."))
    own.make()
    assert part in failed(own)


def test_a_plain_text_error_body_is_still_a_sentence_and_the_key_is_taken_out(own):
    own.service.on(URL, reply(400, {"error": f"bad key {KEY}"}))
    own.make()
    said = failed(own)
    assert KEY not in said and "[your key]" in said


def test_an_answer_that_is_not_json_is_told_plainly(own):
    own.service.on(URL, reply(200, text="<html>hello</html>"))
    own.make()
    assert "no picture Imagery can read" in failed(own)


def test_the_key_typed_for_one_host_is_not_sent_to_another(make_rig):
    rig = make_rig(provider="openai_compatible", settings={"openai_base": OWN_BASE})
    rig.core.grant_host("second.example.org", secrets={"compatible_key": "sk-test-OTHER-000"})
    rig.service.on(URL, own_image())
    rig.make()
    assert rig.service.sent[0]["headers"]["Authorization"] == f"Bearer {KEY}"
