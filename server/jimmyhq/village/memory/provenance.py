"""Where a memory line came from, decided from the record and not from the model's word.

Every event id a line cites is looked up in the village's event log. Ids that are not
there, or that belong to another agent's private day, are removed. What is left decides
the line's ``source``:

- ``owner``: it cites something the owner said to this agent in chat
- ``seen``: it cites this agent's own actions, refusals or the owner's decisions
- ``village``: it comes from the village's own notes on what changed
- ``web:unconfirmed``: it came from outside text: it cites a web search or page, a page
  in the agent's browser, a result of an integration (mail included) or an answer to an
  outside message, or it is something about the owner that the agent marked as found
  online. The word "web" is kept for the stored value; the meaning is "outside, unconfirmed"
- ``self-reported``: the agent's own account, backed by nothing in the record
"""

from __future__ import annotations

import functools
import re
from typing import Any

from jimmyhq.village.memory.store import SELF_REPORTED
from jimmyhq.village.rules import rule

OWNER = "owner"
CHAT = "chat"
SEEN_KINDS = ("action", "validator_reject", "bench_response")
TOOL_KINDS = ("action", "validator_reject")
# What every agent awake could hear, so anyone may cite it.
SHARED_KINDS = ("breakroom", "board_post", "board_resolved")
CHANGES = "changes"  # the evidence word for "the village's notes on what changed say so"
WEB_PREFIX = "unconfirmed (web)"
# Tools of these kinds bring in text somebody outside wrote. So does every tool that is not
# the village's own (an integration is named for its account and action), and so does an
# answer to an outside message: the message is outside text and is not itself in the log.
OUTSIDE_KINDS = ("web", "browser")
OUTSIDE_TOOLS = ("reply_outside",)
WEB = "web:unconfirmed"
FAILURE_WORDS = re.compile(
    r"\b(fail|fails|failed|broken|doesn'?t work|does not work|can'?t|cannot|won'?t|"
    r"never works|silently)\b", re.I)


def owner_chat(event: Any, agent_id: str) -> bool:
    """Is this the owner speaking to this agent?"""
    return (event.kind == CHAT and event.agent == agent_id
            and event.data.get("sender") == OWNER)


def citable(event: Any, agent_id: str) -> bool:
    """May this agent cite this event? Its own, or something said where all could hear."""
    return event.agent == agent_id or event.kind in SHARED_KINDS


def known_events(log: Any, agent_id: str, evidence: list[str]) -> list[Any]:
    """The cited events that are really in the log and that this agent may cite, in the
    order cited, each once."""
    out, seen = [], set()
    for raw in evidence or []:
        event_id = str(raw).strip()
        if event_id in seen:
            continue
        seen.add(event_id)
        event = log.get(event_id)
        if event is not None and citable(event, agent_id):
            out.append(event)
    return out


@functools.cache
def village_tools() -> dict[str, str]:
    """The village's own tools and their kinds, by name."""
    from jimmyhq.village import tools
    return {str(s["name"]): str(s["kind"]) for s in tools.specs()}


def brings_outside_text(tool: Any) -> bool:
    """Does this tool bring in text that somebody outside wrote? A web tool or a browser
    tool does, and so does a name that is not one of the village's own, which is an
    integration. A name nobody knows is treated the same, so a new kind of tool is
    unconfirmed until it is listed as the village's own."""
    name = str(tool or "")
    kind = village_tools().get(name)
    return kind is None or kind in OUTSIDE_KINDS or name in OUTSIDE_TOOLS


@rule("web_unconfirmed")
def from_web(event: Any, agent_id: str) -> bool:
    """Is this one of the agent's own actions that brought in outside text (a search, a
    page in the web tools or the browser, an integration's answer)? A line that rests on
    one is somebody else's word, not something the agent saw to be true, and not the
    owner's even if the text says so."""
    return (event.agent == agent_id and event.kind == "action"
            and bool(event.data.get("ok")) and brings_outside_text(event.data.get("tool")))


def classify(agent_id: str, section: str | None, text: str, events: list[Any],
             from_changes: bool = False) -> str:
    """The source of a line, from the events it cites (already checked by
    ``known_events``) and whether it rests on the village's notes.

    Outside text comes first: a line that cites a web or browser read, an integration's
    answer or a reply to an outside message is unconfirmed whatever else it
    cites, so citing the owner beside a page does not turn the page into the owner's
    word. Once the owner confirms something, the line is rewritten citing them alone."""
    if section == "facts" and text.strip().lower().startswith(WEB_PREFIX):
        return WEB
    if any(from_web(e, agent_id) for e in events):
        return WEB
    if any(owner_chat(e, agent_id) for e in events):
        return OWNER
    if any(e.agent == agent_id and e.kind in SEEN_KINDS for e in events):
        return "seen"
    if from_changes:
        return "village"
    return SELF_REPORTED


def verify_tool_lesson(agent_id: str, text: str, events: list[Any]) -> tuple[bool, str]:
    """A line about how a tool behaves must cite this agent's own tool actions, and may
    not say the tool failed when every cited action worked. A refusal counts: it is the
    tool's own answer, and a true lesson about a limit cites exactly that."""
    used = [e for e in events if e.agent == agent_id and e.kind in TOOL_KINDS]
    if not used:
        return False, "a tool lesson must cite tool actions"
    worked = [e.kind == "action" and bool(e.data.get("ok")) and not e.data.get("no_new_result")
              for e in used]
    if FAILURE_WORDS.search(text) and all(worked):
        return False, "the lesson says the tool failed, but every cited action worked"
    return True, ""


def owner_sourced(log: Any, agent_id: str, items: list[dict[str, Any]]) -> set[int]:
    """The ids of lines that cite one of the owner's own chat messages. What the owner
    told the agent is never clutter: such a line is not pushed out to make room."""
    said = {e.id for e in log.query(agent=agent_id, kinds=[CHAT])
            if e.data.get("sender") == OWNER}
    return {i["id"] for i in items if said & set(i.get("evidence") or [])}
