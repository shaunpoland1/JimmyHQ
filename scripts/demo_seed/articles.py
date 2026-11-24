"""Sample sites and readers' replies for Articles, through the page's own
calls (``sites.save``, ``new.topic``, ``replies.paste``). Every site publishes through
Practice, which keeps what it would have sent and sends nothing.

Left out, because the calls that make them need a model or the web:
- written articles, their sources, checks and reviews: they come from the research and
  writing jobs; no page call takes an article's text, so Articles, Publish and Published stay
  empty;
- Ideas from readers: ``ideas.find`` asks a model, so the replies are kept and counted but no
  idea is listed;
- header images and WordPress (they need an address the owner adds in Settings).
With ``start_pieces=True`` it also starts three articles. They wait at the first step and need
a model and web lookup to move on, so the demo leaves them out by default."""

from __future__ import annotations

# What this sample cannot hold, printed at the end of scripts/demo_data.py.
NOT_SEEDED = ["Articles: written articles and ideas (they need a model)."]

SETTINGS: dict[str, object] = {}

SITES = [
    {
        "name": "The Garden Gate Blog",
        "audience": "Beginner and returning gardeners with a small plot or a few raised beds.",
        "register": "Friendly and plain, with one practical tip in every section.",
        "samples": (
            "There is no secret to a good tomato, only a few habits. Water early, feed "
            "lightly, and pinch out the side shoots on the vine types.\n"
            "-----\n"
            "A compost heap is a slow kitchen. Feed it a mix of green and brown, turn it when "
            "you remember, and give it a season."),
        "length": "About 900 words, enough to be useful on a first read.",
        "headings": "Short plain headings, one per tip.",
        "closing": "End with one small thing the reader can do this weekend.",
        "publisher": "practice",
        "timezone": "America/Denver",
    },
    {
        "name": "Crumb and Crust Journal",
        "audience": "Home bakers who want reliable bread without special equipment.",
        "register": "Warm and precise. Explains why, not only how.",
        "samples": (
            "Bread is flour, water, salt and time. Most trouble comes from rushing the time.\n"
            "-----\n"
            "A dough that feels sticky is usually fine. Wet your hands, not the bench."),
        "length": "Between 700 and 1,100 words.",
        "headings": "Question headings such as Why did my loaf spread?",
        "closing": "A short checklist for the next bake.",
        "publisher": "by_hand",
        "timezone": "UTC",
    },
]

# (site, topic, angle, notes)
TOPICS = [
    ("The Garden Gate Blog", "How mulch helps a vegetable bed through a dry summer",
     "Compare wood chips, straw and grass clippings for a small plot.",
     "Mention that chips take a season to settle."),
    ("The Garden Gate Blog", "Where wild bees spend the winter",
     "Why leaving some stems standing helps.", ""),
    ("Crumb and Crust Journal", "Why a cold overnight rest improves sourdough",
     "Aimed at bakers who skip the fridge step.", ""),
]

REPLIES = """Loved the piece on tomatoes. Could you write about what to do when the leaves \
turn yellow at the bottom? Mine do it every July.
-----
I would read anything on saving seed. How do you keep beans dry enough over winter?
-----
We have a very shady yard. Is there anything worth growing there besides hostas?
-----
Please do a guide to making a first compost bin from pallets. I have no idea where to start.
-----
My sourdough keeps coming out dense. Is my starter too young or is it my oven?"""


def seed(call, today: str, now_ms: int, start_pieces: bool = False) -> None:
    by_name = {}
    for site in SITES:
        by_name[site["name"]] = call("sites.save", **site)["id"]
    for name, topic, angle, notes in TOPICS if start_pieces else ():
        call("new.topic", site_id=by_name[name], topic=topic, angle=angle, notes=notes)
    call("replies.paste", text=REPLIES, audience="Readers of the garden blog")
