"""Settings for how agents talk and hand work over. Importing this module declares them.

Each one is a limit on what reaches the owner or the other agents in a day or an hour.
They protect attention, not the agents' work: past most of them a finished piece is held
and goes by itself later.
"""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section

PREFIX = "village.comms."
REVIEW_DISTANCE = "village.review.min_distance_min"

section("village_comms", "Messages and hand-overs", parent="village", order=40,
        description="How much an agent may ask of you each day, and how much it may say.")

# short key -> (default, lowest, highest, label, help)
TABLE: dict[str, tuple[int, int, int, str, str]] = {
    "chat_messages_per_day": (
        10, 0, 100, "Messages an agent may start with you each day",
        "A reply to a message of yours is always allowed and is not counted.",
    ),
    "requests_taken_per_day": (
        5, 0, 30, "Requests from chat an agent may turn into tasks each day",
        "How many things you asked for in chat one agent may put on its list in a day.",
    ),
    "own_tasks_per_day": (
        5, 0, 30, "Tasks of its own an agent may start each day",
        "Past this an agent can still do the work. It only stops adding it to its list.",
    ),
    "own_tasks_open": (
        3, 1, 10, "Tasks of its own an agent may have open at once",
        "An agent must finish one of its own tasks before it starts another past this.",
    ),
    "handovers_per_day": (
        6, 0, 30, "Hand-overs one agent may put on your Bench each day",
        "Finished work past this is held and goes on the Bench by itself the next day. "
        "Work you asked for in chat is not held.",
    ),
    "priority_suggestions_per_day": (
        3, 0, 20, "New priorities an agent may suggest each day",
        "Each suggestion is a card on your Bench.",
    ),
    "findings_per_day": (
        3, 0, 20, "Findings an agent starts with each day",
        "A finding is something an agent checked and thinks you should know. The day's "
        "number rises by one for each finding you noted in the last two weeks and falls "
        "by one for each you dismissed.",
    ),
    "findings_max_per_day": (
        6, 1, 20, "Most findings one agent can earn in a day",
        "However good an agent's record, no more of its findings reach you in a day. It "
        "is also how many findings an agent may have held at once.",
    ),
    "findings_open_hold": (
        5, 1, 30, "Hold new findings while this many wait for you",
        "While this many of one agent's findings are unread on your Bench, its new ones "
        "wait their turn.",
    ),
    "urgent_findings_per_day": (
        1, 0, 5, "Urgent findings that may skip the wait each day",
        "A finding an agent marks urgent goes straight to your Bench. Past this number "
        "in a day, an urgent finding waits like any other.",
    ),
    "breakroom_per_hour": (
        30, 0, 600, "Break-room messages allowed per hour, all agents together",
        "A backstop. Conversations should end by themselves well before this, so "
        "reaching it is recorded as a fault in the village.",
    ),
    "board_post_chars": (
        1500, 200, 8000, "Longest work-board post (characters)",
        "A longer post is not posted, and the agent is told to put the text in a file.",
    ),
}


# Fine tuning, kept behind "Show advanced settings". What an agent may ask of the owner
# in a day is not among them: those stay in sight.
ADVANCED = frozenset({"findings_max_per_day", "findings_open_hold", "urgent_findings_per_day",
                      "breakroom_per_hour", "board_post_chars"})


def full_key(short: str) -> str:
    return short if short.startswith("village.") else PREFIX + short


def _declare_all() -> None:
    for key, (default, low, high, label, text) in TABLE.items():
        declare(Setting(key=full_key(key), section="village_comms", type="int",
                        default=default, min=low, max=high, advanced=key in ADVANCED,
                        label=label, help=text))
    declare(Setting(
        key=REVIEW_DISTANCE, section="village_comms", type="int", default=30, min=0, max=1440,
        advanced=True,
        label="Minutes before an agent may review its own draft",
        help="An agent rereads a draft before handing it over. A reread straight after "
             "writing misses things, so the review opens this long after the first draft.",
    ))


_declare_all()
