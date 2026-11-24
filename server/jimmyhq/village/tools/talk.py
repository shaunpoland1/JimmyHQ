"""Tools for talking: a message to the owner, the break room, and the work board. The
rules live in ``comms/chat.py``, ``comms/breakroom.py`` and ``comms/board.py``."""

from __future__ import annotations

from typing import Any

from jimmyhq.village.comms import Refused, board, breakroom, chat, owner
from jimmyhq.village.tools.seams import SOCIAL, WEO, WORK, done, refuse_as, text


async def message_owner(ctx: Any, args: dict[str, Any]) -> Any:
    v, said = ctx.v, text(args, "text")
    try:
        chat.send(v, ctx.agent_id, said)
    except Refused as e:
        refuse_as(e, {"length": len(said)})
    return done(f"sent to {owner(v)}", {"length": len(said)})


async def say(ctx: Any, args: dict[str, Any]) -> Any:
    said = text(args, "text")
    try:
        _, note = breakroom.speak(ctx.v, ctx.agent_id, said)
    except Refused as e:
        refuse_as(e, {"length": len(said)})
    return done(note, {"length": len(said)})


async def post_to_board(ctx: Any, args: dict[str, Any]) -> Any:
    kind, said = text(args, "kind"), text(args, "text")
    try:
        ev = board.post(ctx.v, ctx.agent_id, kind, said)
    except Refused as e:
        refuse_as(e, {"kind": kind, "length": len(said)})
    return done(f"posted on the work board as {ev.id}", {"kind": kind, "length": len(said)})


async def resolve_post(ctx: Any, args: dict[str, Any]) -> Any:
    post = text(args, "post").strip()
    try:
        board.resolve(ctx.v, ctx.agent_id, post, text(args, "note"))
    except Refused as e:
        refuse_as(e, {"post": post[:60]})
    return done("marked as dealt with: it has left the board and nobody sees it there any "
                "more", {"post": post})


SPECS = [
    {"name": "message_owner", "kind": "self", "modes": WEO, "run": message_owner,
     "description": "Send {owner} a message in your chat with them (a few a day at most; "
                    "they may not answer right away). Only {owner} reads it: your coworkers "
                    "never see this chat.",
     "params": {"text": {"type": "string"}}, "required": ["text"]},
    {"name": "say", "kind": "social", "modes": (SOCIAL,), "run": say,
     "description": "Say something in the break room, where your coworkers can hear you. It "
                    "is for talk: a few sentences (a longer message is not sent). Anything "
                    "longer goes on the work board or in a file.",
     "params": {"text": {"type": "string"}}, "required": ["text"]},
    {"name": "post_to_board", "kind": "social", "modes": (WORK,), "run": post_to_board,
     "description": "Post on the work board: ask for help, say what you're good at, or hand "
                    "something off. A post that is too long is not posted. Coworkers can't "
                    "open your own files. Anything long that they must read (a draft to "
                    "review) goes in a file in your shared folder, shared/<your id>/, which "
                    "they can read; name it in the post.",
     "params": {"kind": {"type": "string", "enum": list(board.KINDS)},
                "text": {"type": "string"}},
     "required": ["kind", "text"]},
    {"name": "resolve_post", "kind": "social", "modes": (WORK,), "run": resolve_post,
     "description": "Mark a work-board post as dealt with, by its id. A post marked this way "
                    "leaves the board: nobody sees it there afterwards. Leave a post open if "
                    "others should still read it.",
     "params": {"post": {"type": "string",
                         "description": "The post's event id, like evt-123"},
                "note": {"type": "string"}},
     "required": ["post", "note"]},
]

RULES = [
    ("chat_messages_per_day", "village.comms.chat_messages_per_day",
     chat.chat_messages_per_day),
    ("chat_too_long", None, chat.too_long),
    ("breakroom_per_hour", "village.comms.breakroom_per_hour", breakroom.breakroom_per_hour),
    ("say_too_long", "village.social.say_chars", breakroom.say_too_long),
    ("board_post_size", "village.comms.board_post_chars", board.too_long),
    ("board_post_only", None, board.resolve),
]
