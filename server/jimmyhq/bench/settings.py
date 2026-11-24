"""Settings for the Bench. Importing this module declares them."""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section

section("bench", "Bench", group="work", order=30,
        description="Snoozing, tidying up, and when to suggest giving an action more freedom.")

declare(Setting(
    key="bench.snooze_hours", section="bench", type="int", default=24, min=1, max=720,
    label="Default snooze length (hours)",
    help="How long a card stays hidden when you snooze it without picking a time.",
))
declare(Setting(
    key="bench.snooze_later_hours", section="bench", type="int", default=3, min=1, max=12,
    label="\"Later today\" snooze (hours)",
    help="How far ahead the quick \"Later today\" snooze lands, rounded up to the hour.",
))
declare(Setting(
    key="bench.snooze_morning_hour", section="bench", type="int", default=9, min=0, max=23,
    label="Morning starts at (hour, 0 to 23)",
    help="The hour the quick \"Tomorrow morning\" and \"Next week\" snoozes bring a card back, "
         "in your time zone. Next week follows the \"Week starts on\" setting in Profile.",
))
declare(Setting(
    key="bench.recent_decisions", section="bench", type="int", default=20, min=0, max=200,
    label="Recent decisions to show",
    help="How many of your latest decisions the Bench lists under the lanes. 0 hides the list.",
))
declare(Setting(
    key="bench.done_expire_days", section="bench", type="int", default=7, min=0, max=365,
    label="Clear \"done for you\" cards after (days)",
    help="Cards that only tell you something was done leave the Bench on their own after this "
         "many days. 0 keeps them until you dismiss them, for up to a year.",
))
declare(Setting(
    key="bench.single_keys", section="bench", type="bool", default=True,
    label="Single-letter keys on the Bench",
    help="Lets j, k, a, n, d, s and ? work on the Bench without another key held. Switch "
         "this off if you dictate, or if a stray key press could decide a card. Enter "
         "still opens a card.",
))

section("bench_freedom", "Asking and acting alone", parent="bench", order=10,
        description="Which kinds of action start by asking you, and when the Bench "
                    "suggests more freedom.")

declare(Setting(
    key="bench.promotion_streak", section="bench_freedom", type="int", default=10, min=1, max=100,
    label="Suggest more freedom after (approvals in a row)",
    help="After you approve the same kind of action this many times in a row, the Bench "
         "suggests letting it happen without asking. It only suggests: you decide.",
))
declare(Setting(
    key="bench.caution_categories", section="bench_freedom", type="list", unique=True,
    default=["send", "delete", "spend", "access"],
    label="Kinds of action that start at \"Ask me each time\"",
    help="An action of one of these kinds waits for your yes each time until you say "
         "otherwise. You can set any single action to \"Alone\" whenever you like: this "
         "list only decides where a new one starts. The kinds are send, create, update, "
         "delete, spend, access and change.",
))
declare(Setting(
    key="bench.undo_drops_alone", section="bench_freedom", type="bool", default=True,
    label="A No or an Undo takes \"Alone\" back",
    help="When you say No to, or undo, something that was allowed to happen without "
         "asking, it goes back to asking you each time. You can raise it again whenever "
         "you like. Switch this off to keep the level as it is.",
))
