"""Practice: tracks nothing outside. The owner sets each status by hand.

For trying Packages out, and for anyone whose parcels are not worth a tracking service.
It sends nothing anywhere, registers nothing and uses no quota; a check of it changes
nothing, because nobody outside knows anything. Under Practice a package that is not set
by hand moves along by itself over ``practice_minutes`` (see ``packages.pretend``), so the
whole path to a delivered package and its card can be seen.
"""

from __future__ import annotations

from packages.status import STATUSES
from packages.trackers import NONE, YES, Item, Reply, Result

MOST_CHARS = 40


class Practice:
    id = "practice"
    label = "Practice (nothing is looked up)"
    outside = False
    registers = False
    moves_by_itself = True
    batch_most = 1000
    hand_statuses = STATUSES
    carrier_help = ("Any name will do. Practice looks nothing up: a package you add moves "
                    "along by itself, and the buttons on it let you set its status yourself.")

    def key_problem(self) -> str | None:
        return None

    def check_carrier(self, code: str) -> str | None:
        if len(code) > MOST_CHARS:
            return (f"A carrier is written in up to {MOST_CHARS} characters. Nothing was "
                    "changed. Shorten it.")
        return None

    def register(self, items: list[Item]) -> Reply:
        return Reply(YES, items=tuple(Result(i.id, YES) for i in items))

    def read(self, items: list[Item]) -> Reply:
        return Reply(YES, items=tuple(Result(i.id, YES, reading=None) for i in items))

    def change_carrier(self, item: Item, code: str) -> Reply:
        return Reply(YES, items=(Result(item.id, YES),))

    def forget(self, item: Item) -> Reply:
        return Reply(YES, items=(Result(item.id, YES),))

    def quota(self) -> Reply:
        return Reply(NONE, "Practice has no quota.")
