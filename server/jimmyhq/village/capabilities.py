"""What an agent may use beyond the basics, and what each such thing means.

A capability is a name in an agent's configuration, put there by the owner and by nobody
else. A tool that names a capability is offered only to an agent that holds it. Every
agent starts with none.

Each one is described here in the owner's words, because the Village screen shows the
description next to the switch: what the agent can then do by itself, and what leaves the
machine when it does. Some depend on something else being set up; ``problem`` says what,
in a sentence the screen can show.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from jimmyhq.village.rules import rule

WEB = "web"
WEB_SETTINGS = "lookup"  # the settings section the screen links to


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    description: str
    on: Callable[[Any], bool]  # whether it can be used at all right now
    problem: Callable[[Any], str | None]  # what is not set up yet, for the owner
    settings: str | None = None  # the settings section where that is fixed


WEB_DESCRIPTION = (
    "Lets this agent look things up on the web by itself, without asking you each time. It "
    "can search, and it can read public pages. Both only read: nothing is posted, sent or "
    "bought. The words it searches for go to your search provider. Pages are fetched from "
    "this machine. What a page or a result says is then sent to the agent's model, which "
    "matters if that model is hosted; with \"Local only\" on for this agent it never leaves "
    "this machine. Off for every agent until you switch it on here. How many searches and "
    "pages a day is under Settings, Village: web lookup.")
WEB_OFF = ("Web lookup is switched off, so this does nothing yet. Switch it on under "
           "Settings, Web lookup.")
WEB_NO_SEARCH = ("No search provider is ready, so the agent can read a page you give it "
                 "the address of but cannot search yet.")


def _lookup_on(core: Any) -> bool:
    return bool(core.settings.get("lookup.enabled"))


def _web_problem(core: Any) -> str | None:
    from jimmyhq.lookup.routes import status

    state = status(core)
    if not state["enabled"]:
        return WEB_OFF
    if not state["search_ready"]:
        return f"{WEB_NO_SEARCH} {state['search_problem'] or ''}".strip()
    return None


OUTSIDE = "outside"
OUTSIDE_DESCRIPTION = (
    "Lets this agent hear from, and answer, the outside systems you have named under "
    "Settings, Other AI apps. Without it no outside message reaches this agent: one sent to it "
    "is refused. With it, a client you gave the message tool can leave this agent a "
    "message, which it reads as untrusted text that it need not act on, and the agent may "
    "write back, in its own words, to a client that wrote first: a few answers a day, each "
    "of a limited size. What it writes leaves this machine when that client next asks. Off "
    "for every agent until you switch it on here. The numbers are under Settings, Other AI apps.")
OUTSIDE_OFF = ("Connecting from other AI apps is switched off, so no outside system can write "
               "to this agent yet. Switch it on under Settings, Other AI apps.")


def _door_on(core: Any) -> bool:
    return bool(core.settings.get("door.enabled"))


def _door_problem(core: Any) -> str | None:
    return None if _door_on(core) else OUTSIDE_OFF


# Held by an agent the owner switched at least one integration action on for. It is
# derived from those rules: there is no switch of its own, and writing the name into an
# agent's configuration does nothing.
HANDS = "hands"
DERIVED = frozenset({HANDS})


@rule("hands_need_a_rule")
def has_hands(core: Any, agent_id: str) -> bool:
    """Rule ``hands_need_a_rule``: an agent holds the integrations capability exactly
    when the owner has switched on at least one action for it."""
    from jimmyhq import hands

    return bool(hands.hands(core).rules(agent_id))


BROWSER = "browser"
BROWSER_DESCRIPTION = (
    "Gives this agent a browser of its own, which it drives by itself: it opens pages, "
    "clicks, types, scrolls and reads. Unlike web lookup this is not read only. It can "
    "use a live site, and whatever it types into a search box or sends in a form goes to "
    "that site. What is fenced in code: it cannot reach this machine, your private "
    "network or the sites on your deny list (banks, payments, password managers, webmail "
    "sign-in, and any you add); it cannot type into a password, email, card or code box; "
    "it cannot download or upload files; and a form that changes something follows the "
    "level you choose for this agent on its Browser tab, which starts at \"Ask me each "
    "time\", as does anything a page's own script tries to send. A page can still carry "
    "what the agent typed into it away in a web address, which no level can hold. It "
    "uses its own empty profile, never your browser or your saved passwords. "
    "What a page shows is sent to the agent's model, as text and as a picture, which "
    "matters if that model is hosted; with \"Local only\" on for this agent it never "
    "leaves this machine. Off for every agent until you switch it on here. The limits "
    "and lists are under Settings, Browser.")


def _browser_on(core: Any) -> bool:
    from jimmyhq.browser import engine

    return engine.find(core) is not None


def _browser_problem(core: Any) -> str | None:
    from jimmyhq.browser import engine

    return engine.problem(core)


KNOWN: dict[str, Capability] = {
    BROWSER: Capability(BROWSER, "A browser of its own", BROWSER_DESCRIPTION, _browser_on,
                        _browser_problem, "browser"),
    WEB: Capability(WEB, "Web lookup", WEB_DESCRIPTION, _lookup_on, _web_problem,
                    WEB_SETTINGS),
    OUTSIDE: Capability(OUTSIDE, "Answer outside systems", OUTSIDE_DESCRIPTION, _door_on,
                        _door_problem, "door"),
}


@rule("capability_needs_its_switch", "lookup.enabled")
def usable(core: Any, held: Iterable[str]) -> list[str]:
    """The capabilities an agent holds that can be used right now. The owner gives a
    capability to an agent; some also need a switch for the whole of JimmyHQ (the web
    needs ``lookup.enabled``). While that is off, the agent is not offered the tools and
    is not told about them."""
    out = []
    for name in dict.fromkeys(held or ()):
        if name in DERIVED:
            continue  # never given by configuration: see ``held``
        known = KNOWN.get(name)
        if known is None or known.on(core):
            out.append(name)
    return out


def held(v: Any, agent_id: str) -> list[str]:
    """This agent's usable capabilities: those in its configuration, and the
    integrations one when the owner's rules give it any action."""
    out = usable(v.core, (v.cfg(agent_id) or {}).get("capabilities") or ())
    if has_hands(v.core, agent_id):
        out.append(HANDS)
    return out


def describe(core: Any, specs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every capability a tool is offered under, for the Village screen: ``{id, label,
    description, tools, ready, problem, settings}``. One the registry does not describe
    is described by the tools it unlocks."""
    unlocks: dict[str, list[str]] = {}
    for spec in specs:
        if spec.get("capability") and spec["capability"] not in DERIVED:
            unlocks.setdefault(spec["capability"], []).append(spec["name"])
    out = []
    for cap, names in sorted(unlocks.items()):
        known = KNOWN.get(cap)
        problem = known.problem(core) if known else None
        out.append({
            "id": cap,
            "label": known.label if known
            else cap.replace("_", " ").replace("-", " ").capitalize(),
            "description": known.description if known
            else "Lets the agent use " + ", ".join(names) + ".",
            "tools": names, "ready": problem is None, "problem": problem,
            "settings": known.settings if known else None,
        })
    return out
