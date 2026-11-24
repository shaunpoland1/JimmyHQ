"""Sample brands and rules for Posts, through the page's own calls
(``pages.save``, ``rules.save``, ``new.topic``). Posts books through Practice, which records
what it would have sent and sends nothing.

Left out, because the calls that make them need a model or another app:
- drafts, queued and practice-booked posts, checks and the Went out list: a draft exists only
  once the drafting job has run with a model; no page call writes one;
- articles read in from a link (``new.link`` makes a piece that must be read from the web);
- Ideas from Reader (they arrive from the Reader app's feed);
- accounts (they belong to SocialBu and Buffer, and Practice needs none).
With ``start_pieces=True`` it also starts three topic pieces. They sit at the step before
drafting and need a model to move on, so the demo leaves them out by default."""

from __future__ import annotations

# What this sample cannot hold, printed at the end of scripts/demo_data.py.
NOT_SEEDED = [
    "Posts: drafts, queued and booked posts, the Went out list (drafting needs a model job).",
]

SETTINGS: dict[str, object] = {"scheduler": "practice"}

BRANDS = [
    {
        "name": "Hillside Community Garden",
        "register": "Warm, plain and practical, like a neighbour sharing what worked.",
        "samples": (
            "Our tomato bed is finally producing. If your leaves curl in the heat, water at the "
            "base in the morning and mulch an inch deep.\n\n"
            "Swap table this Saturday: bring spare seedlings, take what you need. Tea is on us.\n\n"
            "Three things we learned from a wet spring: raise the beds, leave the paths wide, "
            "and never plant beans before the soil is warm."),
        "timezone": "America/Denver",
        "hours": ["08:00", "17:30"],
        "tags": ["#gardening", "#community", "#growyourown"],
    },
    {
        "name": "Corner Oven Bakery",
        "register": "Cheerful and short, with the occasional pun. Never pushy.",
        "samples": (
            "Fresh rye is out of the oven. Come early, the crust is still singing.\n\n"
            "Sourdough tip: a cold fridge rest overnight gives you a better crumb and a better "
            "mood on baking day.\n\n"
            "Closed Monday for a deep clean. Back Tuesday at seven with cinnamon knots."),
        "timezone": "UTC",
        "hours": ["07:00", "12:00"],
        "tags": ["#bakery", "#sourdough"],
    },
    {
        "name": "Ridgeway Hiking Club",
        "register": "Encouraging and clear. Safety first, then the view.",
        "samples": (
            "Sunday's walk is eight miles at an easy pace. Bring water, a layer and a snack to "
            "share at the top.\n\n"
            "New walkers welcome. Meet at the car park by nine and we will pair you with a "
            "walking buddy."),
        "timezone": "UTC",
        "hours": ["09:00"],
        "tags": ["#hiking", "#walkingclub"],
    },
]

# (brand, kind, text, says): one rule list for the owner's own standing instructions
RULES = [
    ("Hillside Community Garden", "phrase", "guaranteed results",
     "Gardening is never guaranteed. Say what usually happens."),
    ("Hillside Community Garden", "tag", "#community", "Every post carries our community tag."),
    ("Hillside Community Garden", "question", "Is this advice safe for children and pets?", ""),
    ("Corner Oven Bakery", "phrase", "best in town", "We do not claim to be the best."),
    ("Corner Oven Bakery", "question", "Does it mention nuts or other allergens?", ""),
    ("Ridgeway Hiking Club", "question", "Does it say what to bring and how hard the walk is?",
     ""),
    (None, "phrase", "act now", "Nothing we post is urgent."),
]

# (brand, topic, notes): pieces the owner started by typing a topic
TOPICS = [
    ("Hillside Community Garden", "Why mulch helps beds hold water through a dry summer",
     "Keep it hopeful. Mention the wood chips delivery from the council."),
    ("Corner Oven Bakery", "A guide to keeping sourdough starter alive on holiday",
     "Friendly tone, two practical tips."),
    ("Ridgeway Hiking Club", "Five things to pack for an autumn day walk",
     "Beginners are the audience."),
]


def seed(call, today: str, now_ms: int, start_pieces: bool = False) -> None:
    by_name = {}
    for brand in BRANDS:
        by_name[brand["name"]] = call("pages.save", **brand)["id"]
    for name, kind, text, says in RULES:
        call("rules.save", page_id=by_name[name] if name else None, kind=kind, text=text,
             says=says)
    for name, topic, notes in TOPICS if start_pieces else ():
        call("new.topic", page_id=by_name[name], topic=topic, notes=notes)
