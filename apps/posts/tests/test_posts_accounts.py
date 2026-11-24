"""A page's accounts: each on a scheduler Posts offers, kept as {scheduler, account_id,
label}, the shape stored before the page form had rows."""

from __future__ import annotations

import pytest
from posts.pages import PageError, check_accounts


def test_the_overview_lists_the_schedulers_an_account_may_be_on(rig):
    offered = rig.core.call("overview", {})["schedulers"]
    assert [s["id"] for s in offered] == ["practice", "by_hand", "socialbu", "buffer"]
    assert {s["id"]: s["sends_out"] for s in offered} == {"practice": False, "by_hand": False,
                                                          "socialbu": True, "buffer": True}
    assert all(s["label"] for s in offered)


def test_accounts_are_stored_as_they_always_were(rig):
    page = rig.page(accounts=[{"scheduler": "socialbu", "account_id": " 42 ", "label": "Main"}])
    assert page["accounts"] == [{"scheduler": "socialbu", "account_id": "42", "label": "Main"}]


def test_an_account_on_a_scheduler_posts_does_not_offer_is_refused_with_its_number(rig):
    with pytest.raises(Exception, match=r"Account 2 is on a scheduler Posts does not offer"):
        rig.page(accounts=[{"scheduler": "socialbu", "account_id": "1"},
                           {"scheduler": "elsewhere", "account_id": "2"}])
    assert rig.store.pages() == []


def test_the_same_account_listed_twice_is_refused():
    with pytest.raises(PageError, match="Account 2 is listed twice"):
        check_accounts([{"scheduler": "socialbu", "account_id": "1"},
                        {"scheduler": "socialbu", "account_id": "1", "label": "Again"}],
                       ["socialbu"])


def test_an_account_without_an_id_is_refused():
    with pytest.raises(PageError, match="needs an account id"):
        check_accounts([{"scheduler": "socialbu", "account_id": ""}], ["socialbu"])
