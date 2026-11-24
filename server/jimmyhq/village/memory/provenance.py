"""Where a memory line came from, decided from the record and not from the model's word.

Every event id a line cites is looked up in the village's event log. Ids that are not
there, or that belong to another agent's private day, are removed. What is left decides
the line's ``source``:

- ``owner``: it cites something the owner said to this agent in chat
- ``seen``: it cites this agent's own actions, refusals or the owner's decisions
- ``village``: it comes from the village's own notes on what changed
- ``web:unconfirmed``: it came from the web: it cites a search or a page the agent read,
  or it is something about the owner that the agent marked as found online
- ``self-reported``: the agent's own account, backed by nothing in the record
"""

from __future__ import annotations

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
WEB_TOOLS = ("web_search", "web_read")
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


@rule("web_unconfirmed")
def from_web(event: Any, agent_id: str) -> bool:
    """Is this the agent's own search or page read that brought text in from the web?
    A line that rests on one is the web's word, not something the agent saw to be true."""
    return (event.agent == agent_id and event.kind == "action"
            and event.data.get("tool") in WEB_TOOLS and bool(event.data.get("ok")))


def classify(agent_id: str, section: str | None, text: str, events: list[Any],
             from_changes: bool = False) -> str:
    """The source of a line, from the events it cites (already checked by
    ``known_events``) and whether it rests on the village's notes.

    The web comes first: a line that cites a web read is unconfirmed whatever else it
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
