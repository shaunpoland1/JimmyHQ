"""Settings for the night: how an agent's day is sorted into memory while it sleeps.

Importing this module declares them. Read one with ``night_setting(core, "tries")``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jimmyhq.settings import Setting, declare, section

if TYPE_CHECKING:
    from jimmyhq.core import Core

PREFIX = "village.night."

section("village_night", "Night and memory", parent="village", order=50,
        description="How each agent sorts its day into memory while it sleeps.")

# short key -> (default, lowest, highest, label, help)
NIGHT: dict[str, tuple[int, int, int, str, str]] = {
    "tries": (
        6, 1, 12, "Tries at sorting a day, per sleep",
        "While an agent sleeps, its day is sorted into memory. If that fails (the model "
        "is unreachable, or its answer is unusable) it is tried again, up to this many "
        "times in one sleep.",
    ),
    "retry_gap_min": (
        45, 5, 240, "Minutes between tries",
        "How long to wait after a failed try. Spreading tries over the night means a "
        "model that is down for an hour or two does not cost the agent its day.",
    ),
    "smart_only_tries": (
        3, 0, 12, "Tries that wait for the night model",
        "The first tries of a night use only the agent's night model. After this many, "
        "a try may use the agent's everyday model instead, and the night is marked as "
        "sorted by the fallback. 0 lets every try fall back.",
    ),
    "carry_days": (
        3, 0, 14, "Days an unrecorded message from you is shown again",
        "Something you told an agent that no memory line records is put in front of "
        "its sleeping mind again for this many days, until it is recorded or set aside.",
    ),
    "name_lock_nights": (
        3, 1, 10, "Nights before a chosen name settles",
        "An agent's name becomes permanent once it has come up on this many different "
        "nights.",
    ),
    "snapshots_kept": (
        30, 5, 365, "Memory snapshots kept per agent",
        "A copy of an agent's memory is kept after every sorted night and before every "
        "rollback. Older copies beyond this many are removed.",
    ),
    "section_cap": (
        24, 8, 60, "Lines per memory section",
        "Each of the seven sections of an agent's memory holds at most this many lines. "
        "A full section pushes out its weakest line to make room.",
    ),
    "input_chars": (
        90000, 20000, 400000, "Longest day's record given to the night model (characters)",
        "A longer day is shortened: thoughts go first, then feelings, then the oldest "
        "actions. What you said in chat is never cut.",
    ),
}

for _key, (_default, _low, _high, _label, _help) in NIGHT.items():
    # All fine tuning: kept behind "Show advanced settings" until one is changed.
    declare(Setting(key=PREFIX + _key, section="village_night", type="int", default=_default,
                    min=_low, max=_high, advanced=True, label=_label, help=_help))


def night_setting(core: Core, key: str) -> Any:
    """A night setting by its short key, read live."""
    return core.settings.get(PREFIX + key)
