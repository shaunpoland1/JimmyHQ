"""The people in an agent's view: the owner's chat, coworkers, the break room, the work
board, and the agent's private impressions of whoever is in front of it.

The owner's words come from the owner, so they are trusted in a way a web page is not.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.village.beat.room import can_say, spoken
from jimmyhq.village.rules import rule
from jimmyhq.village.runtime import display_name

CUT_OWN = " [... shortened here to save room; the whole message was sent]"


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _owner_wrote(m: dict[str, Any], agent_id: str) -> bool:
    return m.get("sender") != agent_id


@rule("urgent_call_first")
def call_text(v: Any, agent_id: str) -> str:
    """The owner's urgent message, above everything else until the agent answers."""
    call = v.ask("comms.chat", "open_call", agent_id)
    if not call:
        return ""
    return (f"# {cap(v.owner_name())} is calling you (urgent)\n{call['text']}\n"
            "This was marked urgent, so it comes before anything else: answer with "
            "message_owner.\n\n")


@rule("chat_view", "village.context.chat_messages")
def chat_text(v: Any, agent_id: str) -> str:
    """The recent chat with the owner. The owner's messages are shown whole: the agent
    has no other way to read them. Its own are shortened under a marker, so a shortened
    one is not mistaken for a message that was sent cut off. When the newest lines are
    its own, the view says how long the owner has been quiet, so old words are not
    answered as if they were new."""
    c, now, owner = v.ctx_s, v.clock.now(), v.owner_name()
    since = now - int(c("chat_days")) * 86400
    msgs = v.ask("comms.chat", "history", agent_id, since=since, limit=int(c("chat_messages")),
                 default=[])
    if not msgs:
        return ""
    own_chars, lines = int(c("chat_own_chars")), []
    for m in msgs:
        t = f"{v.when(m['ts']):%a %H:%M}"
        mine = not _owner_wrote(m, agent_id)
        text = m["text"] if not mine or len(m["text"]) <= own_chars else (
            m["text"][:own_chars] + CUT_OWN)
        lines.append(f"{t} {'You' if mine else cap(owner)}: {text}")
    tail = ""
    if v.ask("comms.chat", "unanswered", agent_id):
        tail = f"\n({cap(owner)}'s last message has not been answered yet.)"
    if not _owner_wrote(msgs[-1], agent_id):
        every = v.ask("comms.chat", "history", agent_id, since=since, default=[])
        theirs = [m for m in every if _owner_wrote(m, agent_id)]
        if not theirs:
            tail = (f"\n({cap(owner)} has not written in the last {int(c('chat_days'))} days: "
                    "every message here is yours.)")
        elif now - theirs[-1]["ts"] >= int(c("chat_quiet_hours")) * 3600:
            last = theirs[-1]["ts"]
            tail = (f"\n(Nothing from {owner} since {v.when(last):%a %H:%M}, "
                    f"{int((now - last) // 3600)} hours ago: the messages after it are yours.)")
    return f"# Your chat with {owner}\n" + "\n".join(lines) + tail + "\n\n"


def _who(v: Any, who: str) -> str:
    """A coworker's name. Someone who has left is marked, so their old words are not
    taken for a coworker's who is still here."""
    return display_name(who) + ("" if who in v.world.agents else " (has left the village)")


@rule("room_view", "village.context.room_lines")
def room_lines(v: Any, agent_id: str) -> list[tuple[str, float, str]]:
    """The last hours of the break room, newest last."""
    c = v.ctx_s
    talk = v.ask("comms.breakroom", "recent", agent_id, default=[],
                 minutes=int(c("room_hours")) * 60, limit=int(c("room_lines")))
    return [spoken(e)[:3] for e in talk or []][-int(c("room_lines")):]


@rule("board_view", "village.context.board_posts")
def board_shown(v: Any, agent_id: str, posts: list[dict[str, Any]]) -> dict[str, str]:
    """The text of each open post as this agent sees it: the newest few by others,
    while they are new, in full; the rest cut short with a note saying so. The board
    does the cutting; how many, how new and how short are settings."""
    c = v.ctx_s
    return v.ask("comms.board", "shown", agent_id, posts, whole=int(c("board_whole")),
                 whole_s=int(c("board_whole_hours")) * 3600,
                 short=int(c("board_short_chars")), default=None) \
        or {p["id"]: str(p["text"]) for p in posts}


@rule("impressions_present", "village.context.impressions_owner_hours")
def present(v: Any, agent_id: str, talk: list[tuple[str, float, str]]) -> list[str]:
    """Who is in front of the agent right now: whoever spoke in the room view, and the
    owner if they have chatted lately. It carries its take on them and nobody else's."""
    out: list[str] = []
    for who, _ts, _text in talk:
        if who and who != agent_id and who not in out:
            out.append(who)
    since = v.clock.now() - int(v.ctx_s("impressions_owner_hours")) * 3600
    if v.ask("comms.chat", "history", agent_id, since=since, limit=1, default=[]):
        out.append("owner")
    return out


def closed_room_text(v: Any, agent_id: str) -> str:
    me = v.world.agents[agent_id]
    others_awake = any(o.id != agent_id and not o.asleep for o in v.world.agents.values())
    if me.mode == "social" or me.asleep or not others_awake or can_say(v, agent_id):
        return ""
    return ("The break room is closed to you for the moment (you've had your fill of talk, "
            "or nobody answered last time); it opens again by itself, or as soon as a "
            f"coworker speaks. Your coworkers cannot see your chat with {v.owner_name()}: "
            "anything meant for one of them goes on the work board.\n\n")


def social_text(v: Any, agent_id: str) -> str:
    """Coworkers (as behaviour, never numbers), the break room, the work board and
    impressions. An agent with no coworkers sees none of it."""
    c = v.ctx_s
    out = v.ask("comms.status", "coworkers_text", agent_id, default="") or ""
    talk: list[tuple[str, float, str]] = []
    if out:
        out += closed_room_text(v, agent_id)
        talk = room_lines(v, agent_id)
        if talk:
            lines = [f"{v.when(ts):%H:%M} {'You' if who == agent_id else _who(v, who)}: {text}"
                     for who, ts, text in talk]
            out += (f"# The break room (last {int(c('room_hours'))} hours)\n"
                    + "\n".join(lines) + "\n\n")
        posts = v.ask("comms.board", "open_posts", agent_id, default=[],
                      limit=int(c("board_posts"))) or []
        if posts:
            text = board_shown(v, agent_id, posts)
            out += "# The work board\n" + "\n".join(
                f"- {p['id']} {'You' if p['by'] == agent_id else _who(v, p['by'])} "
                f"({p['kind']}): {text[p['id']]}" for p in posts) + "\n\n"
    read = getattr(v.seam("memory.impressions"), "read", None)
    for who in present(v, agent_id, talk) if read else []:
        impression = read(v.agent_dir(agent_id), who)
        if impression:
            name = v.owner_name() if who == "owner" else _who(v, who)
            out += f"# Your private impression of {name}\n{impression}\n"
    return out
