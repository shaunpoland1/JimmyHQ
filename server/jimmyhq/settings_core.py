"""Settings declared by the core. Importing this module registers them."""

from __future__ import annotations

from typing import Any

from jimmyhq.settings import Setting, SettingError, declare, section
from jimmyhq.web.locality import is_local_name

_OWN_NAMES = ("{names} {is_are} this computer's own, so cannot be added here, and nothing "
              "was saved. This computer's own names always work from this computer and are "
              "never accepted through a tunnel or from another device. Add the name your "
              "other devices use instead, such as this machine's tailnet name.")


def _other_names(value: list[str]) -> list[str]:
    """Rule: the allowed hosts are names for other devices, never this computer's own."""
    own = [h for h in value if is_local_name(h)]
    if own:
        raise SettingError(_OWN_NAMES.format(names=", ".join(own),
                                             is_are="is" if len(own) == 1 else "are"))
    return value


def _without_own_names(value: Any) -> list[str]:
    """A list stored before the rule above: this computer's own names are dropped, the
    rest kept. A log line at start-up says they are ignored (web.app)."""
    return [h for h in value if isinstance(h, str) and not is_local_name(h)]


section("profile", "Profile", group="you", order=10,
        description="Who you are and how your days and weeks run.")
section("appearance", "Appearance", group="you", order=20,
        description="How JimmyHQ looks, and which screens and panels it shows.")
section("access", "Access", group="you", order=30,
        description="Signing in, and reaching JimmyHQ from your other devices.")
# The page the web app draws for saving settings to a file and reading one back.
section("file", "Import and export", group="system", order=90, own_screen=True,
        description="Keep a copy of your settings, move them to another computer, or share "
                    "a set you like.")

declare(Setting(
    key="owner.name", section="profile", type="str", default="",
    portable=False,
    label="Your name",
    help="How JimmyHQ and your agents refer to you.",
))
declare(Setting(
    key="owner.timezone", section="profile", type="str", default="",
    portable=False,
    format="timezone",
    label="Time zone",
    help="Where you are, so days, mornings and due dates fall at the right time. Left "
         "empty, JimmyHQ uses the time zone of the computer it runs on.",
))
declare(Setting(
    key="owner.week_starts", section="profile", type="choice", default="monday",
    choices=(("monday", "Monday"), ("sunday", "Sunday"), ("saturday", "Saturday")),
    label="Week starts on",
    help="The first day of your week, used wherever JimmyHQ says \"this week\" or \"next week\".",
))
declare(Setting(
    key="appearance.theme", section="appearance", type="choice", default="system",
    choices=(("system", "Match my system"), ("light", "Light"), ("dark", "Dark")),
    label="Theme",
    help="Light, dark, or whatever your system is set to.",
))
declare(Setting(
    key="appearance.sidebar_order", section="appearance", type="list",
    default=["bench", "tasks", "chat", "village", "memory", "apps"],
    label="Sidebar order",
    help="The order of the main screens in the sidebar. Remove one to hide it.",
    items=(("bench", "Bench"), ("tasks", "Tasks"), ("chat", "Chat"), ("village", "Village"),
           ("memory", "Memory"), ("apps", "Apps")),
    unique=True,
))
declare(Setting(
    key="appearance.dock_panels", section="appearance", type="list",
    default=["today", "tape", "assistant"],
    label="Right dock panels",
    help="Which panels the right dock shows, in order.",
    items=(("today", "Today"), ("tape", "Tape"), ("assistant", "Assistant")),
    unique=True,
))
declare(Setting(
    key="appearance.show_usage_strip", section="appearance", type="bool", default=True,
    label="Show the usage strip",
    help="The slim line at the top with Tape size and model usage.",
))
declare(Setting(
    key="appearance.action_message_seconds", section="appearance", type="int", default=20,
    min=5, max=600,
    label="Keep a message with a button for (seconds)",
    help="How long a message with something to press, such as Undo, stays on screen. The "
         "countdown waits while the pointer or the keyboard is on a message.",
))
declare(Setting(
    key="access.session_days", section="access", type="int", default=30, min=1, max=365,
    label="Stay signed in for (days)",
    help="How long a browser stays signed in before it asks for the password again.",
))
declare(Setting(
    key="access.allowed_hosts", section="access", type="list", default=[],
    validate=_other_names, tidy=_without_own_names, portable=False,
    label="Other names for this server",
    help="Names or addresses you use to reach JimmyHQ from other devices, such as a private "
         "network name. Requests addressed to any other name are refused.",
))
declare(Setting(
    key="statusbar.show", section="appearance", type="bool", default=True,
    label="Show the status bar",
    help="The slim bar at the bottom with connection status and links.",
))

# How far the owner got through the welcome steps shown after the owner is created.
# Kept on the server so it follows the owner to any device; never in a shared profile.
declare(Setting(
    key="setup.welcome", section="profile", type="choice", default="new",
    choices=(("new", "Not started"), ("about_you", "About you done"),
             ("model", "Model step done"), ("done", "Finished")),
    portable=False,
    label="Welcome steps",
    help="How far you got through the welcome steps shown when JimmyHQ was first set up. "
         "Choose Not started to go through the welcome steps again the next time you open "
         "JimmyHQ.",
))
