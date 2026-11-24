"""Sample content for Heard: a handful of short, invented conversations, text only.

Heard is fed only by its inbound addresses, so each conversation is handed to
``post("conversation", payload)`` in the shape Omi's memory-creation webhook sends: an id, when
it began and ended, a title, an overview, optional to-dos the recorder listed, and the lines
with who said them. Times are offsets back from ``now_ms``. Nothing here needs a model, and
no audio is involved. Every name and sentence is invented.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

SETTINGS: dict[str, object] = {}

MINUTE = 60_000
HOUR = 60 * MINUTE

# "You" is the wearer. The other speakers have names the recorder gave them.
YOU, ALEX, JORDAN, PAT = None, "Alex", "Jordan", "Pat"

# (id, minutes ago it ended, title, category, overview, to-dos the recorder listed, lines).
# A line is (speaker, text).
CONVERSATIONS: list[tuple[str, int, str, str, str, list[str], list[tuple[str | None, str]]]] = [
    ("demo-garden", 26 * 60 + 40, "Garden plan for spring", "personal",
     "Alex and the wearer talked about which beds to dig first and when to order bulbs.",
     ["Order tulip bulbs", "Measure the south bed"],
     [(ALEX, "Good morning. Did you get a chance to look at the garden plan?"),
      (YOU, "I did. I think the south bed should be dug first, it gets the most sun."),
      (ALEX, "Agreed. We could plant tulips along the fence and herbs near the door."),
      (YOU, "I will order the tulip bulbs this week so they arrive before the frost."),
      (ALEX, "And someone should measure the south bed so we buy the right amount of soil."),
      (YOU, "I can do that on Saturday morning.")]),
    ("demo-market", 25 * 60, "Planning the weekend market trip", "personal",
     "A short talk about what to buy at the market and who drives.",
     [],
     [(JORDAN, "Are we still going to the market on Saturday?"),
      (YOU, "Yes. I want to get apples, bread and some flowers for the kitchen table."),
      (JORDAN, "I will drive, you carry the bags."),
      (YOU, "Deal. We should leave by nine, before the car park fills up.")]),
    ("demo-standup", 20 * 60 + 15, "Morning check-in on the website project", "work",
     "A quick team check-in: the contact page is done, the photo gallery needs captions.",
     ["Write captions for the gallery"],
     [(PAT, "Quick update from me: the contact page is finished and published."),
      (YOU, "Great. I am still working on the photo gallery, the captions are missing."),
      (PAT, "Do you want me to review the layout once the captions are in?"),
      (YOU, "Yes please. I will write the captions this afternoon and send you a note."),
      (PAT, "Perfect. Then we can look at the footer tomorrow.")]),
    ("demo-recipe", 8 * 60 + 30, "Soup for dinner", "personal",
     "Deciding on a vegetable soup and what is missing from the cupboard.",
     [],
     [(YOU, "Shall we make the vegetable soup tonight?"),
      (ALEX, "Good idea. We have carrots and onions, but no celery."),
      (YOU, "I can pick up celery on the way home, and a loaf of bread."),
      (ALEX, "Then I will start chopping when you are back.")]),
    ("demo-book", 5 * 60 + 10, "Choosing the next book club read", "personal",
     "The group compared three novels and chose a short one for the month.",
     ["Tell the group which book we picked"],
     [(JORDAN, "I vote for the short novel, I have no time for anything long this month."),
      (YOU, "The one about the lighthouse keeper? I have heard good things about it."),
      (PAT, "I would read that. It is only two hundred pages."),
      (JORDAN, "Then it is settled. Someone should tell the rest of the group."),
      (YOU, "I will send a message tonight.")]),
    ("demo-walk", 95, "Evening walk", "personal",
     "Chat on a walk about the weather and a trip next month.",
     [],
     [(ALEX, "The evenings are getting longer, it is nice to walk after dinner."),
      (YOU, "It is. Have you thought about where to go next month?"),
      (ALEX, "I would like the coast. A small guesthouse and long beach walks."),
      (YOU, "That sounds perfect. I will look at the train times tomorrow.")]),
]


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _segments(lines: list[tuple[str | None, str]]) -> list[dict[str, Any]]:
    """Lines placed one after another, a few seconds each, with the speaker numbers a
    recorder gives: 0 for the wearer, then 1, 2, 3 in the order others first speak."""
    numbers: dict[str | None, int] = {YOU: 0}
    out: list[dict[str, Any]] = []
    clock = 0.0
    for who, text in lines:
        number = numbers.setdefault(who, len(numbers))
        length = round(1.5 + len(text) / 14, 1)
        segment: dict[str, Any] = {
            "text": text, "speaker": f"SPEAKER_{number:02d}", "speaker_id": number,
            "is_user": who is YOU, "start": round(clock, 1), "end": round(clock + length, 1)}
        if who is not YOU:
            segment["speaker_name"] = who
        out.append(segment)
        clock += length + 0.8
    return out


def seed(call: Callable[..., Any], today: str, now_ms: int,
         post: Callable[[str, dict[str, Any]], Any] | None = None) -> None:
    """Send the conversations. ``call`` is not used: Heard has no call that adds one."""
    if post is None:
        raise ValueError("Heard is fed only by its inbound address, so seeding it needs post.")
    for cid, ago, title, category, overview, todos, lines in CONVERSATIONS:
        segments = _segments(lines)
        ended = now_ms - ago * MINUTE
        began = ended - int((segments[-1]["end"] + 1) * 1000)
        post("conversation", {
            "id": cid, "started_at": _iso(began), "finished_at": _iso(ended),
            "discarded": False,
            "structured": {"title": title, "overview": overview, "category": category,
                           "action_items": [{"description": t, "completed": False}
                                            for t in todos]},
            "transcript_segments": segments})
