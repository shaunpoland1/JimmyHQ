"""Settings for the beat (one turn of an agent) and for what an agent reads each beat.

The words on the screen say "turn", the plain word for a beat; the keys keep ``beat``.

Importing this module declares them, as ``village.beat.<name>`` and
``village.context.<name>``. It also declares the handful of world numbers that used to
be fixed in the world's code; the world reads those by their short key and falls back
to the same value when it runs without the settings registry (the simulator).
"""

from __future__ import annotations

from jimmyhq.settings import Setting, declare

BEAT = "village.beat."
CONTEXT = "village.context."

# name -> (type, default, lowest, highest, label, help)
BEAT_TABLE: dict[str, tuple] = {
    "retries_after_refusal": (
        "int", 1, 0, 3, "Second tries after a refused action",
        "When an action is refused, the agent may choose again in the same turn this "
        "many times, with the refusal in front of it."),
    "say_room": (
        "int", 3, 0, 20, "Room left for starting a conversation",
        "An agent may speak first in the break room while its need for company is at "
        "least this far under full."),
    "invite_minutes": (
        "int", 10, 1, 60, "Minutes a coworker's message invites a reply",
        "After a coworker speaks in the break room, others may answer for this long, "
        "whatever they are doing."),
    "max_reply_tokens": (
        "int", 6000, 500, 16000, "Longest reply from a model in one turn (tokens)",
        "A reply that reaches this length is treated as cut off and nothing is done "
        "with it."),
    "temperature_min": (
        "float", 0.6, 0.0, 2.0, "Lowest model temperature for a turn",
        "Each agent has its own voice; its temperature is kept at or above this."),
    "temperature_max": (
        "float", 0.85, 0.0, 2.0, "Highest model temperature for a turn",
        "Each agent has its own voice; its temperature is kept at or below this."),
    "idle_wait_s": (
        "int", 300, 30, 3600, "Seconds between looks at a sleeping or resting agent",
        "An agent that is asleep or resting makes no model call. The village looks "
        "again after this long; a message from you still reaches it at once."),
    "fault_wait_s": (
        "int", 120, 10, 3600, "Seconds an agent waits after a failed turn",
        "When a turn fails (the model is unreachable, or something breaks), the agent "
        "waits this long before its next one."),
    "call_awake_minutes": (
        "int", 60, 5, 480, "Minutes an urgent message keeps an agent up",
        "An urgent message wakes an agent. It then stays awake until it has answered, "
        "or for this long, even at night."),
}

EFFORTS = (("none", "None"), ("low", "Low"), ("medium", "Medium"), ("high", "High"))

# name -> (default, label, help). Each is a whole number that may be set from half to
# four times its default.
CONTEXT_TABLE: dict[str, tuple[int, str, str]] = {
    "total_cap": (
        120000, 'Longest view an agent reads in one turn (characters)',
        'Past this the view is cut, and the agent is told so.'),
    "recent_actions": (
        15, 'Recent actions shown',
        'How many of its last actions an agent sees.'),
    "result_chars": (
        300, 'Characters of each recent result shown',
        'Each recent action shows this much of what came back.'),
    "last_result_chars": (
        34000, 'Longest whole answer shown the next turn (characters)',
        'A long answer from the last action is shown whole up to this size.'),
    "working_files": (
        2, 'Recently used files kept in view',
        'The files an agent last read or changed are shown as they are now.'),
    "lookback": (
        40, 'Actions back that still count as recent',
        'Files and folders used within this many actions can stay in view.'),
    "working_file_chars": (
        16000, 'Longest file shown before its middle is left out',
        'A longer file is shown as its start and its end, with the gap stated.'),
    "just_read_files": (
        3, 'Files just read that stay whole',
        'How many files just read are shown whole.'),
    "just_read_chars": (
        48000, 'Longest file just read that is shown whole (characters)',
        'A file just read is shown whole up to this size.'),
    "just_read_total": (
        72000, 'Room for whole files just read (characters)',
        'All the files shown whole together stay under this size.'),
    "returning_reads": (
        3, 'Reads that make a short file stay in view',
        'A short file read this often stays in view.'),
    "returning_file_chars": (
        8000, 'Longest file that stays in view by being reread',
        'Only files up to this size are kept in view that way.'),
    "returning_files": (
        2, 'Reread files kept in view',
        'How many such files are kept at once.'),
    "folders_seen": (
        2, 'Recently listed folders kept in view',
        'The folders an agent last listed are shown as they are now.'),
    "folder_lines": (
        80, 'Longest folder listing shown (lines)',
        'A longer listing says how many more there are.'),
    "chat_messages": (
        8, 'Messages of the owner chat shown',
        'The newest messages between the agent and the owner.'),
    "chat_days": (
        2, 'Days of the owner chat shown',
        'Older messages are left out of the view.'),
    "chat_own_chars": (
        1500, "Characters of the agent's own chat messages shown",
        "The owner's messages are always shown whole; the agent's own are "
        'shortened past this.'),
    "chat_quiet_hours": (
        3, 'Hours of quiet before the chat view says so',
        "When the newest messages are the agent's own, the view says how long "
        'the owner has been quiet.'),
    "room_hours": (
        2, 'Hours of the break room shown',
        'Older break-room talk is left out of the view.'),
    "room_lines": (
        10, 'Break-room lines shown',
        'The newest lines said in the break room.'),
    "board_posts": (
        12, 'Open work-board posts shown',
        'The newest open posts on the work board.'),
    "board_whole": (
        4, "Coworkers' new posts shown whole",
        'The newest posts by others are shown in full.'),
    "board_whole_hours": (
        24, 'Hours a post counts as new',
        'After this a post is shown shortened.'),
    "board_short_chars": (
        200, 'Characters of an older post shown',
        'An older post is cut here, with a note.'),
    "impressions_owner_hours": (
        6, 'Hours the owner counts as present after chatting',
        'An agent sees its impression of the owner while they have talked this '
        'recently.'),
    "bench_hours": (
        24, "Hours a decision stays in the agent's view",
        "The owner's answers on the Bench are shown to the agent for this long."),
    "circling_window": (
        30, 'Actions checked for going in circles',
        'The last actions looked at when checking for rereading the same '
        'things.'),
    "circling_revisits": (
        10, 'Revisits that count as going in circles',
        'This many returns to a file already seen, with nothing waiting, are '
        'pointed out.'),
    "waiting_quiet": (
        8, 'Actions that made nothing before waiting is named',
        'With work waiting on the owner, this many actions in a row that made '
        'nothing are pointed out.'),
    "news_heads": (
        3, 'Headings of village news shown',
        'The newest changes named until the agent reads the news file.'),
    "diary_entries": (
        3, 'Diary entries shown',
        "The newest of today's diary entries."),
    "diary_chars": (
        400, 'Characters of each diary entry shown',
        'Each diary entry shown is cut here.'),
    "resume_note_hours": (
        6, 'Hours a note for picking a task back up is shown',
        'After a task is picked back up, its note stays in view for this long.'),
}

# What an agent sees is fine tuning, kept behind "Show advanced settings", all but the
# one number that has to fit the model in use.
CONTEXT_BASIC = frozenset({"total_cap"})

# World numbers that used to be fixed in code: short key -> (section, type, default,
# lowest, highest, label, help).
WORLD_TABLE: dict[str, tuple] = {
    "sleep.forced_wake_energy": (
        "village_sleep", "float", 50.0, 10.0, 100.0, 'Energy that ends a collapse',
        'An agent that fell asleep from exhaustion wakes once its energy is '
        'back to this.'),
    "sleep.rest_refusal_energy": (
        "village_sleep", "float", 40.0, 0.0, 100.0, 'Energy above which daytime rest is refused',
        'Before the evening, an agent with at least this much energy is told to'
        ' keep going.'),
    "sleep.rest_min_minutes": (
        "village_sleep", "float", 20.0, 5.0, 120.0, 'Shortest chosen rest (minutes)',
        'A rest an agent asks for lasts at least this long.'),
    "sleep.rest_max_minutes": (
        "village_sleep", "float", 180.0, 30.0, 720.0, 'Longest chosen rest (minutes)',
        'A rest an agent asks for lasts at most this long.'),
    "sleep.nap_from_hour": (
        "village_sleep", "float", 12.0, 8.0, 16.0, 'Earliest hour for a nap',
        'A tired agent may nap from this hour of its day.'),
    "sleep.nap_until_hour": (
        "village_sleep", "float", 18.0, 13.0, 22.0, 'Latest hour for a nap',
        'After this hour a tired agent waits for the night instead.'),
    "modes.rest_margin": (
        "village", "float", 20.0, 0.0, 60.0, 'Energy to regain before rest ends',
        'A resting agent goes back to work once its energy is this far above '
        'the level that sent it to rest.'),
    "modes.outlet_margin": (
        "village", "float", 20.0, 0.0, 60.0, 'Frustration to lose before a break ends',
        'An agent on a break goes back to work once its frustration is this far'
        ' below the level that sent it there.'),
    "modes.explore_margin": (
        "village", "float", 25.0, 0.0, 60.0, 'Curiosity to spend before exploring ends',
        'An exploring agent goes back to work once its curiosity is this far '
        'below the level that sent it exploring.'),
}


def _declare_all() -> None:
    for name, (kind, default, low, high, label, text) in BEAT_TABLE.items():
        declare(Setting(key=BEAT + name, section="village_turn", type=kind, default=default,
                        min=low, max=high, advanced=True, label=label, help=text))
    declare(Setting(
        key=BEAT + "effort", section="village_turn", type="choice", default="none",
        choices=EFFORTS,
        label="How hard a model thinks before each action",
        help="More thinking is slower and costs more. None leaves it to the model."))
    for name, (default, label, text) in CONTEXT_TABLE.items():
        declare(Setting(key=CONTEXT + name, section="village_view", type="int",
                        default=default, min=max(1, default // 2), max=default * 4,
                        advanced=name not in CONTEXT_BASIC, label=label, help=text))
    for key, (section, kind, default, low, high, label, text) in WORLD_TABLE.items():
        declare(Setting(key="village." + key, section=section, type=kind, default=default,
                        min=low, max=high, advanced=True, label=label, help=text))


_declare_all()
