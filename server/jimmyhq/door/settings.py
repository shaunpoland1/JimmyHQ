"""Settings for the MCP door. Importing this declares them.

The door is off until the owner switches it on, and a client can use no tool until the
owner gives it one. Everything here is a limit on what an outside system may ask for or
be shown; none of it opens the door to the internet, which is the owner's own step.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from jimmyhq.settings import Setting, SettingError, declare, section

SECTION = "door"
# The headed parts of the page. A setting's key never says which part it is in.
ADDRESS, LIMITS, MESSAGES, TOOLS = "door_address", "door_limits", "door_messages", "door_tools"
PREFIX = "door."
LIMIT = PREFIX + "limit."
OWN_PORT, MAIN_PORT = "own_port", "main_port"

# The page was called "MCP door". Only its name on the screen changed: the keys, the
# routes and the module keep "door", and the page still calls the way in "the door".
section(SECTION, "Other AI apps", group="agents", order=30,
        description="Others reaching in: assistants and tools you use elsewhere can read from "
                    "and work with JimmyHQ, using the Model Context Protocol (MCP).")
section(ADDRESS, "Where the door listens", parent=SECTION, order=10,
        description="The address other AI apps connect to, and the names it answers to.")
section(LIMITS, "Limits on every client", parent=SECTION, order=20,
        description="How fast a client may ask, and how much one answer may hold.")
section(MESSAGES, "Messages to your agents", parent=SECTION, order=30,
        description="What a client may write to an agent, and what an agent may write back.")
section(TOOLS, "Daily limit for each tool", parent=SECTION, order=40,
        description="How many times a day one client may use each tool. 0 switches a tool off.")

_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")


def _host(value: Any) -> str:
    text = str(value).strip().lower()
    try:
        ipaddress.ip_address(text)
        return text
    except ValueError:
        pass
    if not _HOST.match(text):
        raise SettingError("The address to listen on must be an IP address such as 127.0.0.1, "
                           "or a plain host name.")
    return text


def _names(value: list[str]) -> list[str]:
    out = []
    for item in value:
        text = item.strip().lower()
        if not text:
            continue
        if "/" in text or " " in text or "@" in text:
            raise SettingError("Give each name by itself, such as door.example.com, with no "
                               "https:// and no path.")
        out.append(text)
    return out


def _public_url(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SettingError("The public address must start with https:// or http:// and name "
                           "a host, such as https://door.example.com/mcp.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise SettingError("The public address cannot carry a password, a question mark or "
                           "a #. Give only the address itself.")
    return text


declare(Setting(
    key="door.enabled", section=SECTION, type="bool", default=False,
    label="Let other AI apps connect",
    help="This opens the door other AI apps come in by. Off, nothing answers at the door's "
         "address and every client is shut out. On, a client you have named can use the "
         "tools you gave it, with its own token.",
))
declare(Setting(
    key="door.listen", section=ADDRESS, type="choice", default=OWN_PORT,
    choices=((OWN_PORT, "On its own port, which answers nothing else"),
             (MAIN_PORT, "On JimmyHQ's own address, at /mcp (private networks only)")),
    label="Where the door listens",
    help="Its own port is the one to point a tunnel at: nothing but /mcp is there. The "
         "second choice puts /mcp beside the app, for a network you trust.",
))
declare(Setting(
    key="door.host", section=ADDRESS, type="str", default="127.0.0.1", validate=_host,
    portable=False, label="Address the door's own port listens on",
    help="127.0.0.1 means only programs on this computer, such as a tunnel, can reach it. "
         "Change it only to listen on a private network.",
))
declare(Setting(
    key="door.port", section=ADDRESS, type="int", default=8441, min=1024, max=65535,
    portable=False, label="The door's own port",
    help="Used when the door listens on its own port.",
))
declare(Setting(
    key="door.allowed_hosts", section=ADDRESS, type="list", default=[], unique=True,
    validate=_names, portable=False, label="Names the door answers to",
    help="The host name of your tunnel or private address, such as door.example.com. The "
         "door refuses a request addressed to any other name. This computer's own names "
         "always work.",
))
declare(Setting(
    key="door.allowed_origins", section=ADDRESS, type="list", default=[], unique=True,
    validate=_names, portable=False, label="Web pages that may call the door",
    help="Leave empty unless a client runs inside a web page. A request that says it comes "
         "from a web page is refused unless that page's host is listed here.",
))
declare(Setting(
    key="door.public_url", section=ADDRESS, type="str", default="", validate=_public_url,
    portable=False, label="The address you give to clients",
    help="The full address clients reach the door at, such as https://door.example.com/mcp. "
         "Empty shows the local address.",
))

# short key -> (default, lowest, highest, label, help)
NUMBERS: dict[str, tuple[int, int, int, str, str]] = {
    "calls_per_minute": (
        30, 1, 600, "Requests one client may make each minute",
        "Past this a client is told to slow down. A client can be given its own number.",
    ),
    "failed_tries": (
        10, 1, 1000, "Wrong tokens allowed from one address in 15 minutes",
        "Past this, requests with a wrong token from that address are turned away without "
        "being checked. A request with a right token still gets in.",
    ),
    "failed_tries_all": (
        100, 1, 10_000, "Wrong tokens allowed from all addresses together in 15 minutes",
        "Past this, every request with a wrong token is turned away unchecked for a while. "
        "A right token still gets in.",
    ),
    "max_request_bytes": (
        65536, 1024, 4_000_000, "Largest request the door reads, in bytes",
        "A larger request is refused before it is read.",
    ),
    "max_result_chars": (
        20000, 500, 400_000, "Most characters in one answer",
        "A longer answer is cut, and says that it was cut.",
    ),
    "list_cap": (
        50, 1, 500, "Most items in one list",
        "A longer list is cut, and says how many there were.",
    ),
    "text_chars": (
        600, 50, 20000, "Most characters of one piece of text in an answer",
        "A task's notes or a card's body longer than this is cut, and says so.",
    ),
    "message_chars": (
        1500, 50, 8000, "Longest message a client may send an agent",
        "A longer message is not delivered.",
    ),
    "messages_shown": (
        5, 0, 30, "Outside messages an agent sees at once",
        "The newest few. 0 means agents are shown none.",
    ),
    "messages_hours": (
        72, 1, 720, "Hours an outside message stays in an agent's view",
        "After this it is still on the record, and the agent no longer sees it.",
    ),
    "agent_replies_per_day": (
        10, 0, 200, "Answers one agent may send outside clients each day",
        "Only an agent you have let answer outside clients can send any.",
    ),
    "reply_chars": (
        1500, 50, 8000, "Longest answer an agent may send an outside client",
        "A longer answer is not sent.",
    ),
}
# Which part of the page each number is shown in.
_PART = {"calls_per_minute": LIMITS, "failed_tries": LIMITS, "failed_tries_all": LIMITS,
         "max_request_bytes": LIMITS,
         "max_result_chars": LIMITS, "list_cap": LIMITS, "text_chars": LIMITS}
# Sizes and windows few people change, kept behind "Show advanced settings". The limits
# on what a client may do in a day or a minute are not among them: those stay in sight.
ADVANCED = frozenset({"max_request_bytes", "max_result_chars", "list_cap", "text_chars",
                      "message_chars", "messages_shown", "messages_hours", "reply_chars"})
for _key, (_default, _low, _high, _label, _help) in NUMBERS.items():
    declare(Setting(key=PREFIX + _key, section=_PART.get(_key, MESSAGES), type="int",
                    default=_default, min=_low, max=_high, advanced=_key in ADVANCED,
                    label=_label, help=_help))

# tool -> (calls a day, what the limit is called). Each tool has its own setting.
TOOL_LIMITS: dict[str, tuple[int, str]] = {
    "whoami": (200, "\"Who am I\" calls one client may make each day"),
    "village_agents": (200, "Times a client may list the agents each day"),
    "village_priorities": (100, "Times a client may list the priorities each day"),
    "village_messages": (200, "Times a client may read an agent's answers each day"),
    "village_send_message": (20, "Messages a client may send agents each day"),
    "bench_cards": (100, "Times a client may list the Bench each day"),
    "bench_file_card": (10, "Cards a client may file for you each day"),
    "tasks_list": (200, "Times a client may list tasks each day"),
    "tasks_add": (20, "Tasks a client may add each day"),
    "tasks_complete": (20, "Tasks a client may complete each day"),
    "memory_recall": (50, "Memory searches a client may make each day"),
    "memory_propose": (10, "Facts a client may propose each day"),
}
for _tool, (_default, _label) in TOOL_LIMITS.items():
    declare(Setting(
        key=LIMIT + _tool, section=TOOLS, type="int", default=_default, min=0, max=100_000,
        label=_label,
        help="Counted per client, over a day that starts at 00:00 UTC. A client can be "
             "given its own number. 0 switches the tool off for every client without one.",
    ))

# Tools of installed apps (``apptools.py``): one setting for each effect the door offers,
# applied to each app tool on its own. A tool that writes gets fewer.
APP_LIMITS: dict[str, tuple[int, str]] = {
    "app_read": (50, "Times a client may use each app tool that only reads, each day"),
    "app_record": (10, "Times a client may use each app tool that writes into the app's own "
                       "records, each day"),
}
for _tool, (_default, _label) in APP_LIMITS.items():
    declare(Setting(
        key=LIMIT + _tool, section=TOOLS, type="int", default=_default, min=0, max=100_000,
        label=_label,
        help="Counted for each app tool and each client on its own, over a day that starts "
             "at 00:00 UTC. A client can be given its own number for one tool. 0 switches "
             "every such tool off for every client without one.",
    ))
