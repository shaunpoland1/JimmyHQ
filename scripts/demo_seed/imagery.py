"""Sample gallery for Imagery, made in Practice (a labelled placeholder drawn on this
computer; nothing is sent anywhere) through the page's own ``make`` and ``picture.star``.

Left out: "Start from a picture" (Gemini only, and it needs a picture the owner uploads).
Practice stamps each picture with the time it was drawn, so the gallery's times are those of
the seeding run, not offsets from ``today``."""

from __future__ import annotations

import time

SETTINGS: dict[str, object] = {"provider": "practice"}

# (description, style or "", shape, how many, star the first one)
REQUESTS = [
    ("Raised beds in a community garden at sunrise, rows of lettuce and marigolds",
     "Photo", "3:2", 2, True),
    ("A hand-painted sign reading Seeds and Swaps, hung on a wooden fence",
     "Watercolour", "1:1", 1, False),
    ("A small bakery window with warm light, loaves cooling on a rack",
     "Photo", "16:9", 2, True),
    ("A hiking club crossing a ridge in morning mist, seen from behind",
     "Watercolour", "16:9", 1, False),
    ("A trail map with a dotted route, a campfire and three tents",
     "Line drawing", "2:3", 1, False),
    ("A bicycle leaning on a brick wall beside a flower pot",
     "Flat illustration", "1:1", 2, True),
    ("Jars of plum jam on a kitchen table, handwritten labels",
     "Photo", "3:2", 1, False),
    ("A rain barrel beside a garden shed, a watering can on top",
     "Line drawing", "3:2", 1, False),
]


def _wait_until_idle(call, tries: int = 300) -> None:
    for _ in range(tries):
        if not call("status")["busy"]:
            return
        time.sleep(0.1)  # the page polls the same way while the worker draws


def seed(call, today: str, now_ms: int) -> None:
    for prompt, style, aspect, count, star in REQUESTS:
        call("make", prompt=prompt, style=style, aspect=aspect, count=count)
        _wait_until_idle(call)
        if star:
            made = call("pictures.list", query=prompt.split(",")[0])["rows"]
            call("picture.star", id=made[0]["id"], on=True)
