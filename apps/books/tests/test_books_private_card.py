"""The card books files is one a private app may file: the platform's own rule for a private
app's card (a title, and parts that hold none of the app's words) accepts every card it
sent, and the fake core it is tested against enforces the same rule."""

from __future__ import annotations

from pathlib import Path

from books.config import MANIFEST

from jimmyhq.apps import bridge, dev

APP = Path(MANIFEST).parent


def test_every_card_books_files_passes_the_platforms_rule_for_a_private_app(rig):
    rig.call("reminder.add", {"name": "Cash rent", "amount": "300", "kind": "bill",
                              "paid_other": True, "every": "monthly",
                              "next_due": "2027-01-17"})
    rig.core.run_job("due")
    found, findings = dev.check_manifest(APP)
    assert findings == [] and found.private is True
    assert rig.core.filed, "the test must make the app file a card"
    for args in rig.core.filed:
        bridge.check_private_card(found, args)
        bridge.card_kind(found, args["kind"])
        assert "body" not in args and "summary" not in args and "why" not in args
        assert args["title"]
