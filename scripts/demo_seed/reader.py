"""Sample topics for Reader, saved through the page's own ``topics.save``.

Left out: suggested articles with their reasons. Reader has no page call that takes an
article; they come only from the search job (the web and a model), so the Triage, Articles
and Offered screens stay at their empty states until Reader has looked."""

from __future__ import annotations

# What this sample cannot hold, printed at the end of scripts/demo_data.py.
NOT_SEEDED = ["Reader: suggested articles (no call takes an article; they come from a web search)."]

SETTINGS: dict[str, object] = {}

# (name, search phrasings, who it is for, what happens to an article found for it)
TOPICS = [
    ("Garden soil and water",
     ["mulch and soil moisture in a dry summer", "raised bed watering tips",
      "composting for small gardens"],
     "Volunteers at a community garden", ["list", "bench"]),
    ("Sourdough baking",
     ["sourdough starter troubleshooting", "overnight fermentation crumb",
      "baking with rye flour"],
     "", ["list"]),
    ("Day walks and trail safety",
     ["autumn day hike packing list", "walking group leader safety basics"],
     "Members of a small hiking club", ["list", "offer"]),
    ("Local pollinators",
     ["wild bees nesting in gardens", "planting for butterflies"],
     "", ["list"]),
]


def seed(call, today: str, now_ms: int) -> None:
    for name, phrasings, audience, outcomes in TOPICS:
        call("topics.save", name=name, phrasings=phrasings, audience=audience,
             outcomes=outcomes)
    # a topic the owner has set aside shows what a paused one looks like
    paused = call("topics.save", name="Seed catalogues",
                  phrasings=["heirloom seed catalogue new varieties"],
                  outcomes=["list"])
    call("topics.pause", id=paused["id"], paused=True)
