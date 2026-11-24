"""Asking SocialBu and Buffer which accounts the owner has there, for the page's "Add an
account": one request path for each, only the tool's own host, never retried, every failure
a sentence the owner can read, and a tool that sends nothing out saying so instead.

The answers are written from each service's own reference (SocialBu's OpenAPI file,
Buffer's GraphQL pages), not from the services: nothing here proves a real one answers this
way."""

from __future__ import annotations

from typing import Any

import pytest
from posts.schedulers import Unavailable, lists_accounts, reads_posts
from posts.schedulers.buffer import URL
from posts.schedulers.socialbu import BASE
from posts_rig import Rig
from posts_service import KEY, BufferService, Service, no_answer, reply


def supported(*accounts: tuple[int, str, str]) -> dict[str, Any]:
    """SocialBu's ``GET /posts/supported-options`` as its reference writes it."""
    return reply(200, {"success": True, "accounts": [
        {"account_id": i, "account_type": kind, "account_name": name, "options": {}}
        for i, name, kind in accounts]})


def listing(rig: Rig, scheduler: str = "socialbu") -> dict[str, Any]:
    return rig.core.call("accounts.list", {"scheduler": scheduler})


@pytest.fixture
def sb(make_rig):
    rig = make_rig(settings={"scheduler": "socialbu"}, secrets={"socialbu_key": KEY})
    rig.service = Service(rig.core)
    return rig


# --- SocialBu ----------------------------------------------------------------------------


def test_socialbu_lists_its_accounts_with_label_and_network_from_one_request(sb):
    sb.service.on("GET", "/posts/supported-options",
                  supported((1001, "Garden notes page", "facebook"),
                            (1002, "garden_notes", "instagram")))
    found = listing(sb)
    assert found["can_list"] is True and found["said"] == ""
    assert found["accounts"] == [
        {"account_id": "1001", "label": "Garden notes page", "network": "facebook"},
        {"account_id": "1002", "label": "garden_notes", "network": "instagram"}]
    assert sb.service.asked() == [("GET", "/posts/supported-options")]
    [(_, _, args)] = sb.service.calls
    assert args["headers"]["Authorization"] == f"Bearer {KEY}"
    assert args["url"] == BASE + "/posts/supported-options"


def test_socialbu_with_no_accounts_says_so_and_offers_typing_one_in(sb):
    sb.service.on("GET", "/posts/supported-options",
                  reply(404, {"success": False, "message": "No matching accounts found."}))
    found = listing(sb)
    assert found["can_list"] is True and found["accounts"] == []
    assert "lists no account" in found["said"] and "type its id in" in found["said"]


def test_socialbu_entries_without_a_number_or_repeated_are_left_out(sb):
    sb.service.on("GET", "/posts/supported-options", reply(200, {"accounts": [
        {"account_id": 7, "account_type": "x", "account_name": "  First   one "},
        {"account_id": 7, "account_type": "x", "account_name": "Again"},
        {"account_id": "abc", "account_name": "Not a number"},
        {"account_name": "No id"}, "junk", {"account_id": True, "account_name": "Bool"}]}))
    assert listing(sb)["accounts"] == [{"account_id": "7", "label": "First one",
                                        "network": "x"}]


def test_socialbu_without_a_key_says_where_to_type_it_and_asks_nothing(make_rig):
    rig = make_rig(settings={"scheduler": "socialbu"})
    rig.service = Service(rig.core)
    found = listing(rig)
    assert found["can_list"] is False and found["accounts"] == []
    assert "no SocialBu key" in found["said"] and "Settings page" in found["said"]
    assert rig.service.calls == []


@pytest.mark.parametrize("answer, words", [
    (no_answer(), "no clear answer"),
    (reply(500, {"message": "Server fell over."}), "no clear answer"),
    (reply(401, {"message": "Unauthenticated."}), "said no"),
    (reply(200, text="not json"), "no clear answer"),
])
def test_socialbu_that_cannot_tell_says_why_in_a_sentence_and_is_asked_once(sb, answer, words):
    sb.service.on("GET", "/posts/supported-options", answer)
    found = listing(sb)
    assert found["can_list"] is False and found["accounts"] == []
    assert words in found["said"] and "Nothing was changed" in found["said"]
    assert len(sb.service.calls) == 1  # never retried
    assert KEY not in found["said"]


def test_a_socialbu_answer_that_names_the_key_never_carries_it_into_the_sentence(sb):
    sb.service.on("GET", "/posts/supported-options",
                  reply(401, {"message": f"Bad token {KEY}."}))
    assert KEY not in listing(sb)["said"]


# --- Buffer ------------------------------------------------------------------------------


def orgs(*ids: str) -> dict[str, Any]:
    return reply(200, {"data": {"account": {"organizations": [{"id": i} for i in ids]}}})


def channels(*items: dict[str, Any]) -> dict[str, Any]:
    return reply(200, {"data": {"channels": list(items)}})


@pytest.fixture
def bf(make_rig):
    rig = make_rig(settings={"scheduler": "buffer"}, secrets={"buffer_key": KEY})
    rig.service = BufferService(rig.core)
    return rig


def test_buffer_lists_the_channels_of_its_organization_with_service_as_the_network(bf):
    bf.service.on("organizations", orgs("org-1"))
    bf.service.on("channels", channels(
        {"id": "ch-1", "name": "gardennotes", "displayName": "Garden notes",
         "service": "instagram"},
        {"id": "ch-2", "name": "garden-notes-co", "displayName": None, "service": "linkedin"}))
    found = listing(bf, "buffer")
    assert found["can_list"] is True and found["said"] == ""
    assert found["accounts"] == [
        {"account_id": "ch-1", "label": "Garden notes", "network": "instagram"},
        {"account_id": "ch-2", "label": "garden-notes-co", "network": "linkedin"}]
    assert bf.service.asked() == ["organizations", "channels"]
    query = bf.service.calls[1][1]["json"]["query"]
    assert 'channels(input: {organizationId: "org-1"})' in query
    assert "id name displayName service" in query
    assert all(args["url"] == URL for _, args in bf.service.calls)
    assert all(args["headers"]["Authorization"] == f"Bearer {KEY}"
               for _, args in bf.service.calls)


def test_buffer_with_two_organizations_asks_each_once_and_lists_a_channel_once(bf):
    bf.service.on("organizations", orgs("org-1", "org-2"))
    bf.service.on("channels",
                  channels({"id": "ch-1", "name": "a", "service": "facebook"}),
                  channels({"id": "ch-1", "name": "a", "service": "facebook"},
                           {"id": "ch-3", "name": "c", "service": "threads"}))
    assert [a["account_id"] for a in listing(bf, "buffer")["accounts"]] == ["ch-1", "ch-3"]
    assert bf.service.asked() == ["organizations", "channels", "channels"]


def test_buffer_that_cannot_list_channels_says_so_and_never_gives_half_a_list(bf):
    bf.service.on("organizations", orgs("org-1", "org-2"))
    bf.service.on("channels", channels({"id": "ch-1", "name": "a", "service": "facebook"}),
                  no_answer())
    found = listing(bf, "buffer")
    assert found["can_list"] is False and found["accounts"] == []
    assert "could not list its channels" in found["said"]
    assert bf.service.asked().count("channels") == 2  # each asked once, none retried


def test_buffer_without_an_organization_says_so(bf):
    bf.service.on("organizations", orgs())
    found = listing(bf, "buffer")
    assert found["can_list"] is False and "no organization" in found["said"]


def test_buffer_without_a_key_says_where_to_type_it_and_asks_nothing(make_rig):
    rig = make_rig(settings={"scheduler": "buffer"})
    rig.service = BufferService(rig.core)
    found = listing(rig, "buffer")
    assert "no Buffer key" in found["said"] and rig.service.calls == []


# --- the tools that cannot list ------------------------------------------------------------


def test_a_tool_that_sends_nothing_out_says_it_has_no_accounts_to_list(rig):
    for tool in ("practice", "by_hand"):
        found = listing(rig, tool)
        assert found["can_list"] is False and found["accounts"] == []
        assert "books nothing out" in found["said"] and found["label"]


def test_an_unknown_tool_is_said_not_raised(rig):
    found = listing(rig, "carrier_pigeon")
    assert found["can_list"] is False and "does not offer" in found["said"]


def test_without_a_tool_named_it_asks_the_one_posts_books_through(sb):
    sb.service.on("GET", "/posts/supported-options", supported((1, "Main", "facebook")))
    found = sb.core.call("accounts.list", {})
    assert found["scheduler"] == "socialbu" and found["accounts"][0]["label"] == "Main"


def test_which_tools_can_list_accounts_and_read_posts_is_said_on_the_overview(sb):
    offered = {s["id"]: s for s in sb.core.call("overview", {})["schedulers"]}
    assert offered["socialbu"]["lists_accounts"] and offered["buffer"]["lists_accounts"]
    assert not offered["practice"]["lists_accounts"] and not offered["by_hand"]["lists_accounts"]
    assert not offered["practice"]["reads_posts"] and not offered["by_hand"]["reads_posts"]
    assert lists_accounts(sb.ctx.schedulers["buffer"])
    assert reads_posts(sb.ctx.schedulers["socialbu"])


def test_listing_accounts_changes_no_record_and_tells_no_open_page(sb):
    sb.service.on("GET", "/posts/supported-options", supported((1, "Main", "facebook")))
    before = sb.store.conn.total_changes
    listing(sb)
    assert sb.store.conn.total_changes == before
    assert sb.ctx.emits == 0
    with pytest.raises(Unavailable):
        sb.ctx.schedulers["socialbu"].recent(["abc"], 5)
