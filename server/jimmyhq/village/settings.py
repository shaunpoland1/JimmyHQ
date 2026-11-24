"""Settings for the Village. Importing this module declares them.

The numbers (default, lowest, highest) live in ``world/defaults.py``, which the pure
world and the simulator also read. This file gives each one its words and its place on
the settings screen. A world setting with the short key ``energy.drain_per_min`` is
declared as ``village.energy.drain_per_min``.

Needs run from 0 to 100, and "points" below means points on that scale.
"""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section
from jimmyhq.village.world.defaults import TABLE

PREFIX = "village."

section("village", "Village", group="agents", order=10,
        description="Your agents: how many there are, how often they act, what each one sees.")
# The code and the Tape call a turn a "beat"; the screens say "turn".
section("village_turn", "Each turn", parent="village", order=3,
        description="How often an agent acts, and how a model answers each time. A turn is "
                    "one look, decide, act.")
section("village_view", "What an agent sees", parent="village", order=6,
        description="How much of its files, its chats and its recent work an agent is shown "
                    "each turn.")
section("village_body", "Needs and moods", parent="village", order=10,
        description="How energy, frustration, company, curiosity and purpose rise and fall.")
section("village_sleep", "Rest and sleep", parent="village", order=20,
        description="Bedtimes, naps and when a tired agent must stop.")
section("village_guards", "Guards and checks", parent="village", order=30,
        description="What stops an agent going in circles or misreporting what it did.")

SECTION_OF = {
    "village": "village",
    "modes": "village",
    "energy": "village_body",
    "frustration": "village_body",
    "social": "village_body",
    "curiosity": "village_body",
    "purpose": "village_body",
    "sleep": "village_sleep",
    "guards": "village_guards",
    "divergence": "village_guards",
}
# A setting that sits somewhere other than its group's part of the page.
SECTION_OF_KEY = {
    "village.heartbeat_min_s": "village_turn",
    "village.heartbeat_max_s": "village_turn",
}
# The few world numbers a person plausibly sets on day one. Every other one is fine
# tuning, kept behind "Show advanced settings" until it is changed.
BASIC = frozenset({
    "village.headcount_cap", "village.heartbeat_min_s", "village.heartbeat_max_s",
    "sleep.start_hour", "sleep.wake_hour",
})


def full_key(short: str) -> str:
    """The registry key for a world setting's short key."""
    return short if short.startswith(PREFIX) else PREFIX + short


# short key -> (label, help)
TEXT: dict[str, tuple[str, str]] = {
    # --- general ---------------------------------------------------------------
    "village.headcount_cap": (
        "Most agents at once",
        "The village will not hold more agents than this.",
    ),
    "village.heartbeat_min_s": (
        "Shortest pause between turns (seconds)",
        "A turn is one look, decide, act by an agent. This is the pause between turns "
        "when work is going well. Shorter pauses mean more model calls.",
    ),
    "village.heartbeat_max_s": (
        "Longest pause between turns (seconds)",
        "The pause between turns when there is little to do.",
    ),
    "modes.rest_below_energy": (
        "Rest when energy falls below",
        "An agent whose energy drops under this stops and rests. It goes back to other "
        "things once it has regained 20 points.",
    ),
    "modes.outlet_at_frustration": (
        "Take a break when frustration reaches",
        "An agent this frustrated leaves its work for something easy and absorbing until "
        "it has calmed down by 20 points.",
    ),
    "modes.social_low": (
        "Seek company when social falls below",
        "An agent whose social need drops under this goes to the break room, if anyone "
        "is there to talk to.",
    ),
    "modes.curiosity_high": (
        "Go exploring when curiosity reaches",
        "An agent this curious sets its work aside to follow an interest, until "
        "curiosity has fallen by 25 points.",
    ),
    # --- energy ----------------------------------------------------------------
    "energy.drain_per_min": (
        "Energy lost per minute awake",
        "How fast an agent tires just from being awake. Each agent's own temperament "
        "scales this a little up or down.",
    ),
    "energy.late_day_multiplier": (
        "How much faster energy drains late at night",
        "Tiredness speeds up through the evening. This is the multiplier reached by "
        "11 pm and held until morning. 1 means the time of day makes no difference.",
    ),
    "energy.action_cost": (
        "Energy cost of a stretch of work",
        "Energy an agent spends on one full stretch of working. The next setting says "
        "how long a full stretch is.",
    ),
    "energy.effort_full_after_min": (
        "Minutes of work that cost the full effort",
        "An action that follows the last one sooner than this costs proportionally "
        "less, so a fast worker does not tire faster per hour than a slow one.",
    ),
    "energy.rest_gain_per_min": (
        "Energy regained per minute of rest",
        "How fast energy comes back while an agent rests during the day.",
    ),
    "energy.sleep_gain_per_min": (
        "Energy regained per minute asleep",
        "How fast energy comes back during sleep.",
    ),
    # --- frustration -----------------------------------------------------------
    "frustration.failed_action": (
        "Frustration from a failed action",
        "Added each time something an agent tries does not work.",
    ),
    "frustration.same_no_new_result": (
        "Frustration from repeating with nothing new",
        "Added when an agent does the same thing again and gets the same result.",
    ),
    "frustration.guard_block": (
        "Frustration from a refused action",
        "Added when a guard refuses an action before it happens.",
    ),
    "frustration.wait_per_hour": (
        "Frustration per hour of waiting",
        "Added for each hour an agent waits on an answer to something it handed in.",
    ),
    "frustration.wait_cap_per_item": (
        "Most frustration one waiting item can cause",
        "However long the wait, a single item never adds more than this.",
    ),
    "frustration.wait_cap_total": (
        "Most frustration all waiting can cause",
        "Everything an agent is waiting on, together, never adds more than this. Keep "
        "it well under the level that sends an agent on a break, so that a slow answer "
        "from you never does that by itself.",
    ),
    "frustration.relief_floor": (
        "Resting level of frustration",
        "Relief never takes frustration below this. Calm is not zero.",
    ),
    "frustration.small_win": (
        "Relief from a finished task",
        "Frustration removed when a task is finished and the result has been checked.",
    ),
    "frustration.progress_relief": (
        "Relief from a real change to a file",
        "Frustration removed each time work produces a real change to a file.",
    ),
    "frustration.outlet_relief": (
        "Relief per action while on a break",
        "Frustration removed by each thing an agent does while taking a break.",
    ),
    "frustration.vent_relief": (
        "Relief from talking it over",
        "Frustration removed when an agent tells someone what is bothering it.",
    ),
    "frustration.fatigue_factor": (
        "Extra frustration when tired",
        "Below half energy, everything frustrates more. At 0.5, an exhausted agent "
        "takes one and a half times the usual amount. 0 turns this off.",
    ),
    "frustration.sleep_baseline": (
        "Level frustration settles to during sleep",
        "Sleep moves frustration toward this level, from above or below.",
    ),
    "frustration.sleep_settle_per_min": (
        "How fast sleep settles frustration (per minute)",
        "Points per minute that frustration moves toward its sleeping level.",
    ),
    "frustration.set_aside_at": (
        "Set a task aside when frustration reaches",
        "At this level the task an agent is on is taken off it, with a note of what it "
        "tried, so it stops pushing at something that is not working.",
    ),
    "frustration.resume_below": (
        "Give set-aside tasks back when frustration falls below",
        "Once an agent is this calm, the tasks that were set aside return to it.",
    ),
    "frustration.resume_by_choice_min": (
        "Minutes before an agent may pick a set-aside task back up",
        "An agent can choose to return to a set-aside task sooner than it would come "
        "back by itself, but not before this many minutes have passed.",
    ),
    "frustration.chosen_resume_margin": (
        "Extra room for a task picked up by choice",
        "A task an agent chooses while already frustrated can take this much more "
        "frustration before it is set aside again. Without it the task would be taken "
        "away again within a minute.",
    ),
    # --- social ----------------------------------------------------------------
    "social.drain_per_min": (
        "Social need lost per minute alone",
        "How fast an agent comes to miss company. It only applies when there is "
        "someone it could talk to.",
    ),
    "social.message_fill": (
        "Social need filled per message",
        "How much one message in a conversation restores for the one who sent it.",
    ),
    "social.heard_share": (
        "Share gained from being spoken to",
        "The one who receives a message gets this share of what the sender gets.",
    ),
    "social.topic_match_mult": (
        "Bonus when the talk is about a shared interest",
        "A message about something the agent is curious about fills this many times "
        "the usual amount.",
    ),
    "social.forced_mult": (
        "Share gained from talk that is only business",
        "A message exchanged because the work required it fills this share of the "
        "usual amount.",
    ),
    "social.empty_room_wait_min": (
        "Minutes to wait in an empty break room",
        "How long an agent waits for an answer before it gives up and goes back to "
        "other things.",
    ),
    "social.empty_room_cooldown_min": (
        "Minutes before trying the break room again",
        "After nobody answered, an agent does not go looking for company again for "
        "this long.",
    ),
    "social.frustrated_say_chars": (
        "Longest break-room message when short-tempered (characters)",
        "A frustrated agent is kept to short messages.",
    ),
    "social.say_chars": (
        "Longest break-room message otherwise (characters)",
        "The limit on one break-room message when the agent is calm.",
    ),
    "social.full_at": (
        "Conversations wind down when social reaches",
        "Above this level an agent has had enough company and goes back to other things.",
    ),
    # --- curiosity -------------------------------------------------------------
    "curiosity.routine_gain_per_min": (
        "Curiosity gained per minute of routine work",
        "How fast the urge to explore builds while an agent works.",
    ),
    "curiosity.idle_gain_per_min": (
        "Curiosity gained per minute otherwise",
        "How fast the urge to explore builds while an agent rests, talks or takes a "
        "break.",
    ),
    "curiosity.explore_relief": (
        "Curiosity satisfied per discovery",
        "How much each thing an agent finds while exploring settles its curiosity.",
    ),
    # --- purpose ---------------------------------------------------------------
    "purpose.drain_per_min": (
        "Purpose lost per minute awake",
        "How fast the sense of being useful fades with nothing finished. It slows as "
        "purpose gets low, so it never reaches zero just from time passing.",
    ),
    "purpose.verified_completion": (
        "Purpose from a finished task",
        "Gained when a task is finished and the result has been checked. A task an "
        "agent only says it finished earns nothing.",
    ),
    "purpose.submission": (
        "Purpose from handing work in",
        "Gained when an agent puts a piece of work on your bench.",
    ),
    "purpose.acknowledgement": (
        "Purpose from your approval or thanks",
        "Gained when you approve or acknowledge something an agent handed in.",
    ),
    # --- sleep -----------------------------------------------------------------
    "sleep.start_hour": (
        "Usual bedtime (hour, 24-hour clock)",
        "Agents go to sleep at this hour. Use a number above 24 for after midnight: "
        "25 is 1 am. Each agent's own body clock runs up to an hour early or late.",
    ),
    "sleep.wake_hour": (
        "Usual wake time (hour)",
        "The earliest an agent wakes from a night's sleep.",
    ),
    "sleep.min_energy_to_wake": (
        "Energy needed to wake on time",
        "An agent with less energy than this at wake time sleeps in.",
    ),
    "sleep.latest_wake_extra_h": (
        "Longest lie-in (hours)",
        "However tired, an agent wakes this many hours after the usual wake time.",
    ),
    "sleep.forced_sleep_energy": (
        "Energy that forces sleep at any hour",
        "At or below this an agent falls asleep whatever the time, and wakes once it "
        "is back to half energy.",
    ),
    "sleep.nap_below_energy": (
        "Afternoon energy that leads to a nap",
        "An agent with less energy than this between noon and 6 pm takes a nap, at "
        "most one a day.",
    ),
    "sleep.nap_minutes": (
        "Nap length (minutes)",
        "How long a nap lasts.",
    ),
    "sleep.early_night_hour": (
        "Early nights start at (hour)",
        "From this hour, an agent that is resting, or is tired with nothing to do, "
        "goes to bed for the night.",
    ),
    "sleep.chosen_rest_from_hour": (
        "Agents may choose to rest from (hour)",
        "From this hour an agent may decide to stop for a while however much energy it "
        "has. Earlier in the day it may only do so when it is tired.",
    ),
    "sleep.early_night_energy": (
        "Evening energy that sends an idle agent to bed",
        "In the evening, an agent with no task and less energy than this goes to bed. "
        "At 0, only an agent that is already resting gets an early night.",
    ),
    # --- guards ----------------------------------------------------------------
    "guards.repeat_window": (
        "Recent actions checked for repeats",
        "Doing the same thing from the same starting point as one of this many recent "
        "actions is refused. Doing the same thing from a new starting point, such as "
        "scrolling down a page, is always allowed.",
    ),
    "guards.wall_after": (
        "Fruitless browser actions before a page counts as a wall",
        "After this many browser actions in a row that failed, were refused or showed "
        "nothing new, the agent may only leave the page, go back, switch tabs or scroll.",
    ),
    # --- divergence ------------------------------------------------------------
    "divergence.window_minutes": (
        "How far back the honesty check looks (minutes)",
        "The check compares what an agent's body says it feels with what the agent "
        "actually did over this long.",
    ),
    "divergence.feeling_repeat_share": (
        "Share of identical feelings that counts as repeating",
        "If at least this share of an agent's recent feelings are the same one while "
        "its actions vary, the feeling is flagged as a fault in the body, not in the "
        "agent.",
    ),
    "divergence.action_diversity_min": (
        "Share of different actions that counts as varied",
        "Activity counts as varied when at least this share of recent actions differ "
        "from each other.",
    ),
    "divergence.progress_min": (
        "Productive actions that count as progress",
        "An agent that made at least this many productive actions cannot honestly be "
        "called stuck or aimless, and a claim that it is gets flagged.",
    ),
}


def _declare_all() -> None:
    for key, kind, default, low, high in TABLE:
        label, text = TEXT[key]
        group = key.split(".", 1)[0]
        declare(Setting(
            key=full_key(key), section=SECTION_OF_KEY.get(key, SECTION_OF[group]), type=kind,
            default=default, min=low, max=high, advanced=key not in BASIC, label=label,
            help=text,
        ))


_declare_all()

declare(Setting(
    key=PREFIX + "show_vitals", section="village", type="bool", default=True,
    label="Show needs and mood on agent cards",
    help="Shows each agent's energy, frustration, social, curiosity and purpose, and its "
         "mood, on the Village screen. Turn it off for a plainer card."))

declare(Setting(
    key="village.files.download_cap_mb", section="village", type="int", default=200,
    min=1, max=4000, advanced=True, label="Largest zip of an agent's files, in megabytes",
    help="The Download all button on an agent's Files tab makes one zip. If the files "
         "add up to more than this, nothing is sent and you are asked to download a "
         "smaller folder or one file at a time. Lower it on a small disk."))

from jimmyhq.village import (  # noqa: E402, F401
    settings_beat,  # declares village.beat.*, village.context.* and the hoisted world numbers
    settings_comms,  # declares village.comms.* and village.review.*
    settings_night,  # declares village.night.*
    settings_web,  # declares village.web.*
)
