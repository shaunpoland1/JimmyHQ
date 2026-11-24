"""The card packages files is one a private app may file: the platform's own rule for a private
app's card (a title, and parts that hold none of the app's words) accepts every card it
sent, and the fake core it is tested against enforces the same rule."""

from __future__ import annotations

from pathlib import Path

from packages.config import MANIFEST
from packages_rig import HOUR
from packages_service import ok, quota, registered, tracking

from jimmyhq.apps import bridge, dev

APP = Path(MANIFEST).parent


def test_every_card_packages_files_passes_the_platforms_rule_for_a_private_app(rig):
    rig.service.on("register", ok([registered("TRK-1")]))
    rig.service.on("gettrackinfo", ok([tracking("TRK-1", "Delivered")]))
    rig.service.on("getquota", quota())
    rig.add(number="TRK-1")
    rig.clock.advance(5 * HOUR)
    rig.sync()
    found, findings = dev.check_manifest(APP)
    assert findings == [] and found.private is True
    assert rig.core.filed, "the test must make the app file a card"
    for args in rig.core.filed:
        bridge.check_private_card(found, args)
        bridge.card_kind(found, args["kind"])
        assert "body" not in args and "summary" not in args and "why" not in args
        assert args["title"]


def test_a_delivery_is_filed_as_news_done_for_the_owner_and_a_problem_still_needs_them(rig):
    from packages import tell

    assert tell.lane_of("delivered") == "done"
    assert tell.lane_of("problem") == "needs"
    assert tell.lane_of("on_its_way") == "needs"
    rig.service.on("register", ok([registered("TRK-2")]))
    rig.service.on("gettrackinfo", ok([tracking("TRK-2", "Delivered")]))
    rig.service.on("getquota", quota())
    rig.add(number="TRK-2")
    rig.clock.advance(5 * HOUR)
    rig.sync()
    [card] = [a for a in rig.core.filed if a["title"].endswith("was delivered.")]
    assert card["lane"] == "done"
    found, _ = dev.check_manifest(APP)
    bridge.check_private_card(found, card)
