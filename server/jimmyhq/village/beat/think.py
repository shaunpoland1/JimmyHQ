"""The one model call of a beat.

The model must call a tool. What comes back is either a reply to judge, a slip of the
model's (nothing usable came back; it gets another go), or a fault (the call itself
failed; the beat ends and the loop waits).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jimmyhq.village.rules import CUT_OFF, rule

CALLER = "village.beat"
DEFAULT_TEMPERATURE = 0.7
# How a model error that is really the model's slip is treated: as a refused reply.
SLIPS = {CUT_OFF: "cut_off", "no_tools": "no_action", "bad_tool_call": "unreadable"}


NO_IMAGES = "no_images"  # the model layer's word for "this model takes no pictures"


@rule("picture_fallback")
def without_pictures(messages: list[dict[str, Any]]) -> bool:
    """Take every picture out of the conversation, in place, leaving the text. True when
    there was one. The text view of a page is complete without its picture."""
    found = False
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list) or not any(p.get("type") == "image" for p in content):
            continue
        found = True
        kept = [p for p in content if p.get("type") != "image"]
        message["content"] = (kept[0]["text"] if len(kept) == 1 and kept[0].get("type") == "text"
                              else kept)
    return found


@dataclass
class Thought:
    reply: Any = None  # the model's reply, when one came back
    slip: str | None = None  # cut_off | no_action | unreadable
    fault: str | None = None  # the call failed: a sentence for the log


def target(cfg: dict[str, Any], which: str = "beat") -> dict[str, Any]:
    """The model options for this agent: its role or its named model, and whether it
    may only use models on this computer."""
    chosen = cfg.get(which) or {}
    out: dict[str, Any] = {"local_only": bool(cfg.get("local_only"))}
    if chosen.get("provider_id") and chosen.get("model"):
        out.update(provider_id=chosen["provider_id"], model=chosen["model"])
    else:
        out["role"] = chosen.get("role") or "default"
    return out


def sampling(v: Any, a: Any) -> dict[str, Any]:
    """Each agent has its own voice, inside bounds the owner sets."""
    voice = a.temperament.get("voice", {}) or {}
    lo, hi = v.beat_s("temperature_min"), v.beat_s("temperature_max")
    out = {"max_tokens": int(v.beat_s("max_reply_tokens")),
           "temperature": max(lo, min(max(lo, hi), voice.get("temperature",
                                                             DEFAULT_TEMPERATURE)))}
    if v.beat_s("effort") != "none":
        out["effort"] = v.beat_s("effort")
    return out


async def think(v: Any, agent_id: str, messages: list[dict[str, Any]], system: str,
                schemas: list[dict[str, Any]], mode: str, retry: bool) -> Thought:
    from jimmyhq.models import ModelError

    a = v.world.agents[agent_id]
    options = dict(system=system, tools=schemas, tool_choice="required",
                   actor=f"agent:{agent_id}", caller=CALLER, **sampling(v, a),
                   **target(v.cfg(agent_id)))
    try:
        try:
            reply = await v.brain().chat(messages, **options)
        except ModelError as e:
            if e.kind != NO_IMAGES or not without_pictures(messages):
                raise
            # This model takes no pictures: remember that, and ask again with the text
            # view alone, which says everything the picture showed.
            v.ask("tools.browser", "no_pictures", agent_id)
            reply = await v.brain().chat(messages, **options)
    except ModelError as e:
        if e.kind in SLIPS:
            v.log.append("model_call", agent_id, v.clock.now(), mode=mode, retry=retry,
                         slip=e.kind)
            return Thought(slip=SLIPS[e.kind])
        return _fault(v, agent_id, e.public)
    except Exception as e:  # noqa: BLE001 - any failure of the call is a fault, not a crash
        return _fault(v, agent_id, type(e).__name__)
    v.log.append("model_call", agent_id, v.clock.now(), ms=reply.duration_ms, mode=mode,
                 retry=retry, input_tokens=reply.input_tokens,
                 output_tokens=reply.output_tokens, model=reply.model)
    return Thought(reply=reply)


def _fault(v: Any, agent_id: str, detail: str) -> Thought:
    v.log.append("platform_fault", agent_id, v.clock.now(), what="model call failed",
                 detail=str(detail)[:300])
    return Thought(fault=str(detail)[:300])


def with_refusal(messages: list[dict[str, Any]], thought: Thought,
                 reason: str) -> list[dict[str, Any]]:
    """The conversation for a second go in the same beat: the refusal is the result of
    the call that was refused."""
    note = (f"That was refused: {reason.rstrip('.')}. Nothing happened. Choose a different "
            "action now.")
    calls = thought.reply.tool_calls if thought.reply is not None else ()
    if not calls:
        last = messages[-1]
        if isinstance(last["content"], list):  # a view with a picture: the note goes last
            return [*messages[:-1], {"role": "user", "content": [
                *last["content"], {"type": "text", "text": note}]}]
        return [*messages[:-1], {"role": "user", "content": f"{last['content']}\n\n{note}"}]
    results = [{"type": "tool_result", "tool_call_id": call.id, "is_error": True,
                "content": note if i == 0 else "Only one action a turn: this was not done."}
               for i, call in enumerate(calls)]
    return [*messages, thought.reply.as_message(), {"role": "user", "content": results}]
