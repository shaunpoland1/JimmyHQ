"""Settings for the hands module. Importing this declares them.

They are limits on what leaves the machine through an integration and on how much of an
answer an agent is shown at once. Whether an agent may use an action at all is not here:
that is the owner's rule for that agent and that action.
"""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section

SECTION = "hands"
PREFIX = "hands."

section(SECTION, "Integrations", group="agents", order=20,
        description="JimmyHQ reaching out: the outside accounts your agents may act through, and "
                    "the limits on them.")

SECRET_KEYS = [
    "password", "passwd", "passphrase", "secret", "client_secret", "token", "access_token",
    "refresh_token", "id_token", "api_key", "apikey", "authorization", "cookie", "set_cookie",
    "credential", "credentials", "private_key", "session_token", "signature",
]

# short key -> (default, lowest, highest, label, help)
NUMBERS: dict[str, tuple[int, int, int, str, str]] = {
    "max_tools_per_agent": (
        12, 1, 40, "Integration actions one agent may hold",
        "Each action you switch on for an agent becomes one tool it is offered. Past this "
        "number a new one is refused until you switch another off.",
    ),
    "calls_per_day": (
        200, 0, 10000, "Calls one agent may make on one account each day",
        "Every call that leaves the machine counts, whether it worked or not. Past this "
        "the agent is told to carry on without it until tomorrow.",
    ),
    "calls_per_month": (
        5000, 0, 1000000, "Calls all agents may make through one provider each month",
        "A backstop for a provider that bills by the call. Once it is reached no agent "
        "can use that provider until the next month.",
    ),
    "requests_per_day": (
        20, 0, 500, "Approvals one agent may ask you for each day",
        "Each action set to \"Ask me each time\" files a card for you. Past this the agent "
        "is told to wait until tomorrow.",
    ),
    "request_expires_h": (
        72, 1, 720, "Hours an unanswered request stays good",
        "A request you have not decided within this time can no longer be performed. The "
        "agent has to ask again.",
    ),
    "repeat_minutes": (
        10, 0, 1440, "Minutes a reading is kept for a repeat",
        "The same reading action with the same arguments asked for again within this time "
        "is answered from the copy that was kept, with no new call. Never used for an "
        "action that changes something. 0 asks the provider every time.",
    ),
    "value_chars": (
        4000, 200, 30000, "Characters of one value an agent is shown",
        "A longer value in an answer is cut, and the agent is told how much was left out.",
    ),
    "result_chars": (
        24000, 2000, 30000, "Characters of an answer an agent is shown at once",
        "A longer answer is shown in parts. The agent is told how much was left out and "
        "how to read on. Lower it for a model with a small memory.",
    ),
    "run_timeout_s": (
        120, 5, 3600, "Give up waiting for a provider after (seconds)",
        "A call that has not answered by then is stopped. If it was meant to change "
        "something, it is recorded as \"outcome unknown\" and is never tried again by "
        "itself.",
    ),
    "actions_cache_s": (
        3600, 0, 86400, "Remember an account's list of actions for (seconds)",
        "How long a discovered list of actions is reused before asking the provider again.",
    ),
    "composio_timeout_s": (
        30, 1, 300, "Composio: seconds to wait for one request",
        "How long one request to Composio may take. A provider's own \"timeout_s\" "
        "option overrides this for that provider.",
    ),
    "mcp_start_timeout_s": (
        30, 1, 300, "MCP servers: seconds to wait for a server to start",
        "Starting the program, or reaching the address, and agreeing how to speak. A "
        "provider's own \"start_timeout_s\" option overrides this for that server.",
    ),
    "mcp_call_timeout_s": (
        60, 1, 3600, "MCP servers: seconds to wait for one request",
        "One page of a server's list of tools, or one call of a tool. A provider's own "
        "\"call_timeout_s\" option overrides this for that server.",
    ),
    "mcp_idle_s": (
        300, 1, 86400, "MCP servers: stop an unused program after (seconds)",
        "A program nobody has used for this long is stopped, and started again when next "
        "needed. A provider's own \"idle_s\" option overrides this for that server.",
    ),
    "mcp_max_message_bytes": (
        4_000_000, 1000, 64_000_000, "MCP servers: largest message (bytes)",
        "One message in either direction. A larger one from a server ends the connection. "
        "A provider's own \"max_message_bytes\" option overrides this for that server.",
    ),
    "mcp_max_tools": (
        200, 1, 1000, "MCP servers: most tools listed from one server",
        "Tools past this number are left out of the list you choose from. A provider's "
        "own \"max_tools\" option overrides this for that server.",
    ),
}


# Waits, sizes and how long things are remembered: kept behind "Show advanced settings".
# How many calls and approvals are allowed is not among them: those stay in sight.
ADVANCED = frozenset({
    "request_expires_h", "repeat_minutes", "value_chars", "result_chars", "run_timeout_s",
    "actions_cache_s", "composio_timeout_s", "mcp_start_timeout_s", "mcp_call_timeout_s",
    "mcp_idle_s", "mcp_max_message_bytes", "mcp_max_tools",
})


def _declare_all() -> None:
    for key, (default, low, high, label, text) in NUMBERS.items():
        declare(Setting(key=PREFIX + key, section=SECTION, type="int", default=default,
                        min=low, max=high, advanced=key in ADVANCED, label=label, help=text))
    declare(Setting(
        key="hands.default_level_read", section=SECTION, type="choice", default="alone",
        choices=(("alone", "Alone"), ("ask", "Ask me each time")),
        label="When you switch on an action that only reads",
        help="The level a reading action starts at, when it is certain that it only "
             "reads. An action that an outside server merely says is a reading starts at "
             "\"Ask me each time\" whatever is chosen here. You can change it for each "
             "action.",
    ))
    declare(Setting(
        key="hands.default_level_change", section=SECTION, type="choice", default="ask",
        choices=(("ask", "Ask me each time"), ("read", "Read only (refused)"),
                 ("alone", "Alone")),
        label="When you switch on an action that changes something",
        help="The level a creating or changing action starts at. A kind on your "
             "cautious list (sending, deleting, spending and changing access, unless you "
             "changed it under Settings, Bench) never starts at \"Alone\": it starts at "
             "\"Ask me each time\". You can change the level of any single action.",
    ))
    declare(Setting(
        key="hands.hidden_fields", section=SECTION, type="list", default=SECRET_KEYS,
        unique=True,
        label="Field names never shown to an agent",
        help="A field with one of these names is dropped from every answer before an "
             "agent sees it, whatever it holds. Upper case, hyphens and underscores make "
             "no difference.",
    ))
    declare(Setting(
        key="hands.owner_addresses", section=SECTION, type="list", default=[], unique=True,
        portable=False,
        label="Your own addresses",
        help="Email addresses and phone numbers that are you. A rule limited to \"only "
             "me\" lets an action go to these and nowhere else, and mail from an agent's "
             "own inbox to these counts as mail to you.",
    ))
    declare(Setting(
        key="hands.known_addresses", section=SECTION, type="list", default=[], unique=True,
        portable=False,
        label="Addresses you know",
        help="A rule limited to \"people I know\" lets an action go to these and to your "
             "own addresses. Mail from an agent's own inbox to these counts as mail to "
             "someone written to before.",
    ))

    _declare_mail()


MAIL_SECTION = "hands_mail"
MAIL_LEVELS = (("alone", "Alone"), ("ask", "Ask me each time"))


def _declare_mail() -> None:
    """Mail from an agent's own inbox (AgentMail, and any later mail provider)."""
    section(MAIL_SECTION, "Mail from an agent's own inbox", parent=SECTION, order=30,
            description="Who an agent may write to without asking you, and how mail that "
                        "says it is from you is treated.")
    for key, label, default, text in (
        ("mail_to_owner", "Mail to you", "alone",
         "Mail an agent sends only to your own addresses (listed above)."),
        ("mail_to_known", "Mail to someone written to before", "ask",
         "Mail to an address on your list of addresses you know, or one this agent has "
         "already sent mail to from its inbox."),
        ("mail_to_new", "Mail to a new address", "ask",
         "Mail to anyone else."),
    ):
        declare(Setting(
            key=PREFIX + key, section=MAIL_SECTION, type="choice", default=default,
            choices=MAIL_LEVELS, label=label,
            help=text + " A message to people in more than one of these groups is treated "
                        "the most careful way any of them is. The send or reply action "
                        "must also be switched on for the agent; set it to \"Ask me each "
                        "time\" there and every message asks, whoever it goes to."))
    declare(Setting(
        key="hands.mail_owner_is_instruction", section=MAIL_SECTION, type="bool",
        default=False, label="Treat mail from me as an instruction",
        help="Off: mail that says it is from you is read like any other mail, as "
             "information and never as an order. On: mail from one of your own addresses "
             "that passed its sender check is shown to the agent as your instruction. "
             "Passing is needed but is not proof: it shows the mail came through your "
             "address's mail domain, not that you wrote it. Anyone who can send from that "
             "address, or get into your mail account, can then steer the agent. Mail that "
             "failed its check, or was not checked, never counts."))
    declare(Setting(
        key="hands.mail_unknown_is_failed", section=MAIL_SECTION, type="bool", default=True,
        label="Treat an unchecked sender as a failed check",
        help="Some mail arrives without a sender check (the provider did not say). On: it "
             "is handled like mail whose check failed. Off: it is handled like mail from "
             "someone new. It never counts as an instruction either way."))
    declare(Setting(
        key="hands.mail_failed_card", section=MAIL_SECTION, type="bool", default=True,
        label="Tell me about mail that pretends to be from me",
        help="When mail arrives in an agent's inbox claiming to be from one of your "
             "addresses and its sender check failed, a small card says so on the Bench. "
             "The mail itself is not on the card."))
    declare(Setting(
        key="hands.mail_failed_cards_per_day", section=MAIL_SECTION, type="int", default=3,
        min=1, max=100, advanced=True, label="Most such cards each day",
        help="Past this, more failed mail on the same day files no card."))
    declare(Setting(
        key="hands.mail_poll_s", section=MAIL_SECTION, type="int", default=300, min=30,
        max=86400, advanced=True, label="Look for new mail every (seconds)",
        help="How often each agent's inbox is checked for mail that pretends to be from "
             "you. Takes effect when JimmyHQ next starts."))
    declare(Setting(
        key="hands.agentmail_timeout_s", section=MAIL_SECTION, type="int", default=30,
        min=1, max=300, advanced=True, label="AgentMail: seconds to wait for one request",
        help="How long one request to AgentMail may take. A provider's own \"timeout_s\" "
             "option overrides this for that provider."))


_declare_all()
