"""Judging the model's reply before anything is done with it.

A reply the tools cannot read is the model's slip, not the world in the agent's way:
it is refused and tried again without frustration. Charged like a real refusal, a run
of malformed replies alone can push an agent out of its work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jimmyhq.village.catalog import Catalog, ToolError, repair
from jimmyhq.village.rules import rule

CLIP = 300
LEFT_OUT = "[left out: it looked like a secret]"


@dataclass
class Choice:
    tool: str
    args: dict[str, Any]
    thought: str


def clip_args(args: Any) -> Any:
    """Arguments as they go in the log: long text shortened, and anything that looks
    like a key or a password left out."""
    if not isinstance(args, dict):
        return str(args)[:CLIP]
    try:
        from jimmyhq.memory.mining import looks_secret
    except ImportError:  # the memory module is optional here
        def looks_secret(text: str) -> bool:
            return False
    out: dict[str, Any] = {}
    for k, x in args.items():
        if isinstance(x, str):
            out[k] = LEFT_OUT if looks_secret(x[:2000]) else x[:CLIP]
        elif isinstance(x, list):
            out[k] = [str(i)[:CLIP] for i in x[:20]]
        else:
            out[k] = x
    return out


@rule("no_act_asleep")
def dropped_asleep(a: Any) -> bool:
    """The body fell asleep while the model was thinking. A sleeping agent does not
    act: the reply is dropped."""
    return bool(a.asleep)


@rule("reply_cut_off", "village.beat.max_reply_tokens")
def cut_off_sentence(limit: int, chose_tool: bool) -> str:
    """A reply that ran out of room. Say what really happened: thinking that used the
    whole reply needs different advice from an action that was too long to send."""
    if not chose_tool:
        return (f"your answer was cut off at the {limit}-token limit while you were still "
                "thinking, before you chose an action, so nothing happened. Think less "
                "before acting: choose the next step now and check the rest after it")
    return (f"your answer was cut off at the {limit}-token limit before the action was "
            "complete, so nothing happened. Write long text in parts: write_file with the "
            "first part, then add_to_file for each next part. To change one part of a file "
            "that is already written, use edit_file")


@rule("no_action_chosen")
def no_action_sentence() -> str:
    return "you did not choose an action, so nothing happened. Choose one tool now"


def _slip(v: Any, agent_id: str, tool: str, args: Any, sentence: str) -> None:
    v.world.refuse(agent_id, tool, args if isinstance(args, dict) else {}, sentence,
                   frustrate=False)


def judge(v: Any, agent_id: str, thought: Any, catalog: Catalog, mode: str,
          capabilities: list[str], extra: tuple[str, ...], retry: bool) -> Choice | None:
    """The agent's choice, cleaned, or None when the reply was refused (the refusal is
    then in the log and in ``agent.last_refusal``)."""
    now, limit = v.clock.now(), int(v.beat_s("max_reply_tokens"))
    reply = thought.reply
    calls = reply.tool_calls if reply is not None else ()
    text = (reply.text or "") if reply is not None else ""
    cut = thought.slip == "cut_off" or (reply is not None and reply.output_tokens >= limit)
    if thought.slip == "unreadable":
        v.log.append("thought", agent_id, now, text="", tool=None, retry=retry)
        _slip(v, agent_id, "(none)", {}, "you called a tool that is not on offer right now, "
              "or its arguments could not be read, so nothing happened. Choose one of the "
              "tools you were given")
        return None
    if cut:
        name = calls[0].name if calls else None
        v.log.append("thought", agent_id, now, text=text[:1000], tool=name, cut_off=True,
                     retry=retry)
        _slip(v, agent_id, name or "(none)", {}, cut_off_sentence(limit, bool(calls)))
        return None
    if not calls:
        v.log.append("thought", agent_id, now, text=text[:1000], tool=None, retry=retry)
        _slip(v, agent_id, "(none)", {}, no_action_sentence())
        return None
    name, raw = calls[0].name, calls[0].arguments
    if isinstance(raw, dict):
        raw = dict(raw)
        text = str(raw.pop("thought", "") or text)
    if name in catalog.tools:
        raw, text = repair(catalog.tools[name], raw, text)
        if isinstance(raw, dict):
            raw.pop("thought", None)
    try:
        args = catalog.validate(name, raw, mode, capabilities, extra)
    except ToolError as e:
        v.log.append("thought", agent_id, now, text=text[:1000], tool=name,
                     args=clip_args(raw), retry=retry)
        _slip(v, agent_id, name, clip_args(raw), f"{e}, so nothing happened")
        return None
    v.log.append("thought", agent_id, now, text=text[:1000], tool=name, args=clip_args(args),
                 retry=retry)
    return Choice(name, args, text)
