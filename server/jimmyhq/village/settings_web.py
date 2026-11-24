"""Settings for what an agent with the ``web`` capability may do on the web. Importing
this module declares them.

They are limits on what leaves the machine in a day and a month, and on how much of a
page an agent is shown at once. Whether an agent may use the web at all is not here: that
is the owner's switch on each agent, and ``lookup.enabled`` for the whole of JimmyHQ.
"""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section

PREFIX = "village.web."

section("village_web", "Web lookup", parent="village", order=60,
        description="How much searching and reading on the web agents may do.")

# short key -> (default, lowest, highest, label, help)
TABLE: dict[str, tuple[int, int, int, str, str]] = {
    "searches_per_day": (
        30, 0, 500, "Web searches one agent may make each day",
        "Each one sends the agent's search words to your search provider. Past this the "
        "agent is told to work from what it has until tomorrow.",
    ),
    "pages_per_day": (
        60, 0, 1000, "Web pages one agent may read each day",
        "Each one is fetched from this machine and its text is sent to the agent's model. "
        "Reading on in a page already fetched is not counted again.",
    ),
    "searches_per_month": (
        3000, 0, 100000, "Web searches all agents may make each month, together",
        "A backstop for a search provider that bills by the search. Once it is reached no "
        "agent can search until the next month; pages can still be read.",
    ),
    "repeat_minutes": (
        60, 0, 1440, "Minutes a search or a page is kept for a repeat",
        "The same search, or the same page, asked for again within this time is answered "
        "from the copy that was kept, with no new request, and the agent is told so. "
        "0 asks the web every time.",
    ),
    "result_chars": (
        24000, 2000, 30000, "Characters of a page an agent is shown at once",
        "A longer page is shown in parts. The agent is told how much was left out and how "
        "to read on. Lower it for a model with a small memory.",
    ),
}


def full_key(short: str) -> str:
    return short if short.startswith("village.") else PREFIX + short


def _declare_all() -> None:
    for key, (default, low, high, label, text) in TABLE.items():
        # How many searches and pages are allowed costs money, so those stay in sight.
        declare(Setting(key=full_key(key), section="village_web", type="int",
                        default=default, min=low, max=high,
                        advanced=key in ("repeat_minutes", "result_chars"),
                        label=label, help=text))


_declare_all()
