"""Settings for the agents' browser. Importing this module declares them.

Whether one agent has a browser at all is not here: that is the ``browser`` capability,
the owner's switch on each agent. These are the sizes, waits and limits every agent's
browser shares, and the two lists the owner can edit: where no browser may go, and
which kinds of box no agent may type into.
"""

from __future__ import annotations

from jimmyhq.settings import Setting, SettingError, declare, section

PREFIX = "browser."

LEVELS = (("read", "Read only"), ("approve", "Ask me each time"), ("alone", "Alone"))

# The kinds of box an agent is refused in code, as (id, label). The owner can take a
# kind off the list; nothing an agent does can.
FIELD_KINDS = (
    ("password", "Passwords and passphrases"),
    ("email", "Email addresses"),
    ("username", "Sign-in names"),
    ("card", "Card and bank account numbers"),
    ("one_time_code", "One-time and verification codes"),
    ("phone", "Phone numbers"),
    ("identity", "Identity numbers and dates of birth"),
    ("address", "Street addresses and postcodes"),
)

# Where no agent's browser may go, whatever a page or an agent says. A name stands for
# itself and everything under it; a pattern with a star is matched against the whole
# host. Sorted by what it is, and short on purpose: the owner adds their own.
HARD_DENY = (
    # banks and card issuers
    "chase.com", "bankofamerica.com", "wellsfargo.com", "citi.com", "capitalone.com",
    "usbank.com", "americanexpress.com", "discover.com", "hsbc.com", "barclays.co.uk",
    "lloydsbank.com", "natwest.com", "santander.com", "td.com", "rbcroyalbank.com",
    "scotiabank.com", "bmo.com", "commbank.com.au", "anz.com", "westpac.com.au",
    "ing.com", "deutsche-bank.de", "bnpparibas.com", "revolut.com", "monzo.com", "n26.com",
    "wise.com", "*.bank",
    # brokerages
    "fidelity.com", "schwab.com", "vanguard.com", "robinhood.com", "etrade.com",
    "interactivebrokers.com", "webull.com", "wealthfront.com", "betterment.com",
    "etoro.com", "trading212.com",
    # payments
    "paypal.com", "venmo.com", "cash.app", "stripe.com", "squareup.com", "pay.google.com",
    "wallet.google.com", "payments.google.com", "klarna.com", "affirm.com", "afterpay.com",
    "alipay.com", "payoneer.com", "skrill.com", "westernunion.com",
    # crypto
    "coinbase.com", "binance.com", "binance.us", "kraken.com", "gemini.com", "crypto.com",
    "kucoin.com", "bitstamp.net", "okx.com", "bybit.com", "metamask.io", "blockchain.com",
    "ledger.com", "trezor.io", "uniswap.org", "opensea.io",
    # password managers
    "1password.com", "lastpass.com", "bitwarden.com", "dashlane.com", "keepersecurity.com",
    "nordpass.com", "pass.proton.me", "passwords.google.com",
    # webmail and account sign-in
    "accounts.google.com", "mail.google.com", "login.live.com", "login.microsoftonline.com",
    "outlook.live.com", "outlook.office.com", "login.yahoo.com", "mail.yahoo.com",
    "account.proton.me", "mail.proton.me", "appleid.apple.com", "idmsa.apple.com",
    "icloud.com", "app.fastmail.com", "login.aol.com", "mail.aol.com", "accounts.zoho.com",
    "mail.zoho.com",
)

# A button with one of these in its words is treated as one that changes something out
# there, whatever kind of form it sits in.
SUBMIT_WORDS = (
    "buy", "purchase", "pay", "order", "checkout", "check out", "subscribe", "sign up",
    "signup", "register", "create account", "join", "send", "submit", "post", "publish",
    "delete", "remove", "confirm", "donate", "book", "reserve", "apply",
)


def host_patterns(value: list[str]) -> list[str]:
    """Host patterns as they are stored: lower case, one name or star pattern each."""
    out: list[str] = []
    for item in value:
        name = item.strip().lower().strip(".")
        if not name:
            continue
        if "://" in name or any(ch in name for ch in "/@: \t"):
            raise SettingError("Each entry is a site's name on its own, such as "
                               "example.com or *.example.com: no http://, path or port.")
        if name in ("*", "*.*"):
            raise SettingError("An entry that matches every site is not kept. Take the "
                               "browser away from the agent instead.")
        if name not in out:
            out.append(name)
    return out


def _words(value: list[str]) -> list[str]:
    out: list[str] = []
    for item in value:
        word = " ".join(item.lower().split())
        if word and word not in out:
            out.append(word)
    return out


section("browser", "Browser", group="agents", order=15,
        description="The browser an agent drives by itself: where it may go, what it may "
                    "type, and how much it may do.")
section("browser_fence", "What no agent's browser may do", parent="browser", order=10,
        description="Sites it may not reach, boxes it may not type into, and when a form "
                    "waits for you.")
section("browser_limits", "Daily limits", parent="browser", order=20,
        description="How much one agent may do with its browser in a day.")
section("browser_view", "What an agent sees", parent="browser", order=30,
        description="How a page is drawn, how long it is waited for, and how much of it "
                    "an agent is shown.")
section("browser_program", "The browser program", parent="browser", order=40,
        description="Which program runs the agents' browsers, and when it starts and stops.")

# Which sub-heading each number sits under.
PLACE = {
    "idle_stop_s": "browser_program", "start_timeout_s": "browser_program",
    "page_timeout_s": "browser_view", "settle_ms": "browser_view",
    "viewport_width": "browser_view", "viewport_height": "browser_view",
    "max_elements": "browser_view", "element_chars": "browser_view",
    "text_chars": "browser_view", "read_chars": "browser_view",
    "screenshot_width": "browser_view",
    "max_tabs": "browser_limits", "pages_per_day": "browser_limits",
    "actions_per_day": "browser_limits",
    "requests_per_day": "browser_fence", "approval_minutes": "browser_fence",
}

# Waits and sizes most owners never touch: kept behind "Show advanced settings". How
# much an agent may do, and anything about what it may send, stays in sight.
ADVANCED = frozenset({
    "idle_stop_s", "start_timeout_s", "page_timeout_s", "settle_ms", "viewport_width",
    "viewport_height", "max_elements", "element_chars", "text_chars", "read_chars",
    "screenshot_width",
})

# short key -> (type, default, lowest, highest, label, help)
NUMBERS: dict[str, tuple[str, float, float, float, str, str]] = {
    "idle_stop_s": (
        "int", 600, 30, 86400, "Seconds an unused browser stays open",
        "An agent's browser is started when it first opens a page and closed after this "
        "long without an action. Its tabs are gone; its profile is kept."),
    "page_timeout_s": (
        "float", 30, 2, 180, "Longest wait for a page (seconds)",
        "A page that takes longer than this to answer is given up on, and the agent is "
        "told it did not open."),
    "settle_ms": (
        "int", 500, 0, 10000, "Wait after an action (milliseconds)",
        "How long a page is given to react to a click or a key before it is looked at."),
    "start_timeout_s": (
        "float", 20, 2, 120, "Longest wait for the browser to start (seconds)",
        "Past this the browser is stopped again and the agent is told it would not start."),
    "viewport_width": (
        "int", 1280, 320, 3840, "Page width (pixels)",
        "How wide a page is drawn. Sites choose their layout from it."),
    "viewport_height": (
        "int", 800, 240, 2160, "Page height (pixels)",
        "How much of a page is in view, and so in the picture, at once."),
    "max_tabs": (
        "int", 4, 1, 20, "Most tabs one agent may have open",
        "A pop-up or a new window becomes a tab. One opened past this number is closed "
        "at once."),
    "max_elements": (
        "int", 80, 5, 400, "Most page elements listed",
        "The links, buttons and boxes an agent is shown, each with a number. The agent is "
        "told how many were left out."),
    "element_chars": (
        "int", 80, 10, 300, "Longest element label (characters)",
        "A link's or a button's words are cut to this length in the list."),
    "text_chars": (
        "int", 4000, 200, 20000, "Characters of page text shown after an action",
        "The start of the page's text comes with every action. read_page shows more."),
    "read_chars": (
        "int", 16000, 1000, 30000, "Characters of page text read_page shows at once",
        "A longer page is read in parts, and the agent is told how to read on."),
    "screenshot_width": (
        "int", 1024, 320, 2560, "Width of the picture a model is sent (pixels)",
        "The picture of the page is scaled to this width, and made smaller again if it is "
        "still larger than the largest image a model may be sent (Settings, Models)."),
    "pages_per_day": (
        "int", 100, 0, 5000, "Pages one agent may open each day",
        "Counts open_page. Past this the agent is told to work from what it has until "
        "tomorrow."),
    "actions_per_day": (
        "int", 400, 0, 20000, "Page actions one agent may take each day",
        "Counts clicks, typing, keys, scrolling and going back."),
    "requests_per_day": (
        "int", 10, 0, 200, "Times a day one agent may ask you to approve a form",
        "Each one is a card on your Bench. Past this the agent is told to wait."),
    "approval_minutes": (
        "int", 240, 1, 10080, "Minutes an approval stays usable",
        "A form you approve later than this after the agent asked is not sent, and the "
        "agent is told to ask again."),
}


def _declare_all() -> None:
    declare(Setting(
        key=PREFIX + "executable", section="browser_program", type="str", default="",
        label="The browser program to use", portable=False,
        help="Leave empty to use an installed Chrome, Chromium, Edge or Brave. Otherwise "
             "the full path of a Chromium-family browser on this machine. Agents never use "
             "your own browser profile, whichever program this is."))
    for key, (kind, default, low, high, label, text) in NUMBERS.items():
        declare(Setting(key=PREFIX + key, section=PLACE[key], type=kind, default=default,
                        min=low, max=high, advanced=key in ADVANCED, label=label, help=text))
    declare(Setting(
        key=PREFIX + "screenshots", section="browser_view", type="bool", default=True,
        label="Take a picture of the page after each action",
        help="The picture is what you watch on the agent's Browser tab, and it is sent to "
             "the agent's model when that model takes pictures. It is kept as one file "
             "that the next picture replaces, never on the Tape. Off: text only."))
    declare(Setting(
        key=PREFIX + "hard_deny", section="browser_fence", type="list", default=list(HARD_DENY),
        unique=True, validate=host_patterns,
        label="Sites no agent's browser may reach",
        help="Banks, brokerages, payment and crypto sign-ins, password managers and "
             "webmail sign-in pages, to start with. A name covers the site and everything "
             "under it; *.example.com style patterns work too. Every request a browser "
             "makes is checked, not only the page's own address. Add your own sign-in and "
             "admin sites here."))
    declare(Setting(
        key=PREFIX + "refused_fields", section="browser_fence", type="list",
        default=[k for k, _ in FIELD_KINDS], items=FIELD_KINDS, unique=True,
        label="Kinds of box no agent may type into",
        help="Typing into a box of one of these kinds is refused before anything is "
             "typed, and the agent is told to ask you. An agent cannot change this list. "
             "Take a kind off only if you mean agents to fill such boxes in."))
    declare(Setting(
        key=PREFIX + "submit_words", section="browser_fence", type="list",
        default=list(SUBMIT_WORDS), unique=True, validate=_words,
        label="Words that mark a button as changing something",
        help="A button whose words contain one of these is treated like sending a form: "
             "what happens next is the agent's level for sending forms."))
    declare(Setting(
        key=PREFIX + "default_submit_level", section="browser_fence", type="choice",
        default="approve", choices=LEVELS,
        label="Sending a form: the level a new agent starts at",
        help="Read only: a form that changes something is never sent. Ask me each time: "
             "the agent files a card showing exactly what would be sent, and it goes only "
             "when you approve. Alone: it is sent, and you are told. You change one "
             "agent's level on its Browser tab."))


_declare_all()
