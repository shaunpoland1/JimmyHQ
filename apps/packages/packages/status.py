"""The words a package's status is told in: a small fixed vocabulary of Packages' own.

A tracking service has its own statuses; its tracker file turns them into these, so
nothing else in Packages knows any one service's words.
"""

from __future__ import annotations

STATUSES = ("waiting", "on_its_way", "out_for_delivery", "pickup", "delivered", "problem",
            "not_found", "expired")

WORDS = {
    "waiting": "Waiting for the carrier",
    "on_its_way": "On its way",
    "out_for_delivery": "Out for delivery",
    "pickup": "Ready to collect",
    "delivered": "Delivered",
    "problem": "Problem",
    "not_found": "Not found",
    "expired": "Expired",
}

# Said in a card, with the package's name in front.
SENTENCES = {
    "waiting": "is waiting for the carrier",
    "on_its_way": "is on its way",
    "out_for_delivery": "is out for delivery",
    "pickup": "is ready to collect",
    "delivered": "was delivered",
    "problem": "has a problem",
    "not_found": "was not found by the carrier",
    "expired": "has expired with the carrier",
}

LOUD = ("out_for_delivery", "problem")  # the ones the sidebar counts


def words(status: str) -> str:
    return WORDS.get(status, status)


def is_status(value: object) -> bool:
    return isinstance(value, str) and value in STATUSES
