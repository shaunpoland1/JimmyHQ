"""An app may ask a fetch to give up sooner than the owner's limit, never later."""

from __future__ import annotations

import pytest

from jimmyhq.apps import bridge


def test_a_fetch_waits_the_owners_limit_when_the_app_asks_nothing():
    assert bridge.fetch_timeout(20, None) == 20


def test_a_fetch_may_ask_to_give_up_sooner_than_the_owners_limit():
    assert bridge.fetch_timeout(20, 5) == 5


def test_a_fetch_can_never_wait_longer_than_the_owners_limit():
    assert bridge.fetch_timeout(20, 600) == 20


@pytest.mark.parametrize("asked", [0, -1, 2.5, "10", True, [5]])
def test_a_fetch_timeout_that_is_not_a_whole_number_of_seconds_sends_nothing(asked):
    with pytest.raises(bridge.BridgeError) as caught:
        bridge.fetch_timeout(20, asked)
    assert caught.value.code == "invalid" and "Nothing was sent" in caught.value.message
