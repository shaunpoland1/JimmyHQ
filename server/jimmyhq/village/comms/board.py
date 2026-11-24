"""The work board: "I need help with X", "I'm good at Y", "this is yours now". Roles grow
from who keeps picking up what. Posts and resolutions are events in the village log.

A post over the size is not posted and the poster is told: stored cut, a post loses its
end without a word to anyone. A coworker's recent post is shown whole.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.village.comms import Refused, limit

KINDS = ("need_help", "good_at", "handoff")
OPEN_SHOWN = 12  # open posts in a view
SHOWN_WHOLE = 4  # a reader sees this many of the newest posts by others in full
SHOWN_WHOLE_S = 24 * 3600  # while they are this new
SHORT = 200  # the rest are shown cut to this, with a marker
NOTE_CHARS = 300


def post_chars(v) -> int:
    return limit(v, "board_post_chars")


def too_long(v, text: str) -> str | None:
    """Rule ``board_post_size``."""
    most = post_chars(v)
    if len(text) <= most:
        return None
    return (f"Nothing was posted: that is {len(text)} characters and a post takes {most}. "
            "Post the part a coworker needs to act on. Anything long (a draft to review) "
            "goes in a file in your shared folder, shared/<your id>/, which your coworkers "
            "can read: name the file in the post.")


def post(v, agent_id: str, kind: str, text: str) -> Any:
    why = too_long(v, text)
    if why:
        raise Refused("board_post_size", why, frustrate=False,
                      args_logged={"kind": kind, "length": len(text)})
    if kind not in KINDS:
        kind = "need_help"
    return v.log.append("board_post", agent_id, v.clock.now(), post_kind=kind, text=text)


def resolve(v, agent_id: str, post_id: str, note: str) -> Any:
    """Rule ``board_post_only``: only a work-board post can be marked as dealt with."""
    post_id = str(post_id or "").strip()
    e = v.log.get(post_id)
    if e is None or e.kind != "board_post":
        from jimmyhq.village.comms import benchlink

        why = f"Nothing was marked: {post_id[:60] or 'that'} is not a work-board post."
        if benchlink.looks_like_card(v, post_id):
            why += (" It is 1 of the cards on the Bench: to fix one of your own, use "
                    "correct_finding.")
        else:
            why += " Use the id shown next to the post on the board, like evt-123."
        raise Refused("board_post_only", why)
    return v.log.append("board_resolved", agent_id, v.clock.now(), post=post_id,
                        note=note[:NOTE_CHARS], by=agent_id, poster=e.agent)


def open_posts(v, agent_id: str | None = None, limit: int = OPEN_SHOWN) -> list[dict[str, Any]]:
    """The newest open posts, oldest first: ``{id, by, kind, text, ts}`` (``ts`` seconds).
    Everyone sees the same board."""
    resolved = {e.data["post"] for e in v.log.query(kinds=["board_resolved"])}
    posts = [e for e in v.log.query(kinds=["board_post"]) if e.id not in resolved]
    return [{"id": e.id, "by": e.agent, "kind": e.data["post_kind"], "text": e.data["text"],
             "ts": e.ts} for e in posts[-limit:]]


def shown(v, agent_id: str, posts: list[dict[str, Any]] | None = None, *,
          whole: int = SHOWN_WHOLE, whole_s: float = SHOWN_WHOLE_S,
          short: int = SHORT) -> dict[str, str]:
    """The text of each open post as this agent's view shows it, by post id."""
    if posts is None:
        posts = open_posts(v, agent_id)
    now = v.clock.now()
    full = [p["id"] for p in posts if p["by"] != agent_id and now - p["ts"] < whole_s]
    full = full[-whole:] if whole > 0 else []
    out = {}
    for p in posts:
        t = p["text"]
        if p["id"] in full or len(t) <= short:
            out[p["id"]] = t
        else:
            out[p["id"]] = t[:short] + (" [... shortened here to save room. A long text you "
                                        "need whole belongs in a file in its writer's shared "
                                        "folder: ask for it there]")
    return out
