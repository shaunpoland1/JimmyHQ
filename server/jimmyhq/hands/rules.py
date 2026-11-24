"""The rule table: which agent may use which action of which account, and how far.

Nothing is offered until the owner switches one action on for one agent. A rule holds:

- the **level**: ``read`` (may look; a changing action is refused), ``ask`` (every call
  waits for the owner's yes) or ``alone`` (runs inside its limits);
- each **argument's mode**: ``open`` (the agent sets it), ``fixed`` (the owner's value;
  the model never sees or sets it) or ``hidden`` (left out);
- **limits**: calls a day, who may receive, the most money;
- what the agent is **shown** of an answer: addresses or not, and which fields;
- the action's **version** as the owner saw it. A different version withdraws the tool.

Every check is a named function, listed in ``RULES`` in ``jimmyhq.hands``, with its own
test. No check reads the content of an answer or of anything that arrived: only the
owner's click changes a rule.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from jimmyhq.hands.provider import EFFECT_FROM, EFFECTS, READ, Action
from jimmyhq.hands.shape import line
from jimmyhq.models.validate import Invalid

LEVELS = ("read", "ask", "alone")
READ_ONLY, ASK, ALONE = LEVELS
OPEN, FIXED, HIDDEN = "open", "fixed", "hidden"
MODES = (OPEN, FIXED, HIDDEN)
RECIPIENT_LIMITS = ("owner", "known", "anyone")
__all__ = ["HandRefused", "Invalid", "line"]
TOOL_NAME_MAX = 64
START = "start"
START_OTHER = "read_from"

AGENT = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
SLUG = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_SPLIT = re.compile(r"[,;]")


class HandRefused(Exception):  # noqa: N818 - it is an answer to the caller, not a fault
    """A call the hands module will not make. Nothing happened, nothing left.

    ``sentence`` is for the agent: second person, says that nothing happened, gives the
    number, says what to do instead. ``frustrate=False`` marks a limit or a missing
    set-up: the agent did nothing wrong."""

    def __init__(self, rule: str, sentence: str, frustrate: bool = True):
        super().__init__(sentence)
        self.rule = rule
        self.sentence = sentence
        self.frustrate = frustrate


def rule_key(agent: str, account: str, action: str) -> str:
    return "\n".join((agent, account, action))


def rule_hash(rule: dict[str, Any]) -> str:
    """A fingerprint of a whole rule. Any change to it, however small, changes this."""
    return hashlib.sha256(json.dumps(rule, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


# --- names --------------------------------------------------------------------------------


def tool_name(account: str, action: str) -> str:
    """Rule ``hand_tool_name``: ``<account slug>_<action slug>``, lower-case letters,
    digits and underscores, 64 characters at most. A name too long to fit is cut and
    ends in a few characters of its own fingerprint, so two long names stay apart."""
    slug = re.sub(r"[^a-z0-9]+", "_", action.lower()).strip("_") or "action"
    name = f"{account}_{slug}"
    if len(name) <= TOOL_NAME_MAX:
        return name
    tail = hashlib.sha256(action.encode("utf-8")).hexdigest()[:6]
    return f"{name[:TOOL_NAME_MAX - 7].rstrip('_')}_{tail}"


# --- arguments ----------------------------------------------------------------------------


def properties(action: Action) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """An action's arguments by name, and the names that are required."""
    schema = action.arguments if isinstance(action.arguments, dict) else {}
    props = schema.get("properties")
    props = {str(k): (v if isinstance(v, dict) else {}) for k, v in props.items()} \
        if isinstance(props, dict) else {}
    required = [str(r) for r in schema.get("required") or [] if str(r) in props]
    return props, required


def flat_type(schema: dict[str, Any]) -> str | None:
    """The flat type a tool argument can have: ``string``, ``number``, ``integer``,
    ``boolean``, ``enum`` (one of a list of strings) or ``strings`` (a list of strings).
    None for anything nested."""
    kind = schema.get("type")
    if isinstance(kind, list):
        kinds = [k for k in kind if k != "null"]
        kind = kinds[0] if len(kinds) == 1 else None
    options = schema.get("enum")
    if isinstance(options, list) and options:
        ok = all(isinstance(o, str) for o in options) and kind in (None, "string")
        return "enum" if ok else None
    if kind in ("string", "number", "integer", "boolean"):
        return kind
    if kind == "array":
        items = schema.get("items")
        if isinstance(items, dict) and items.get("type") == "string" and "enum" not in items:
            return "strings"
    return None


def default_arguments(action: Action) -> dict[str, dict[str, Any]]:
    """Rule ``hand_argument_defaults``: required arguments start open, the rest hidden."""
    props, required = properties(action)
    return {name: {"mode": OPEN if name in required else HIDDEN, "value": None}
            for name in props}


def offerable(action: Action, arguments: dict[str, dict[str, Any]] | None = None) -> str | None:
    """Rule ``hand_flat_arguments_only``: why this action cannot be offered as a tool
    yet, or None if it can. A tool's arguments are flat: text, a number, yes or no, one
    of a list, or a list of text. An open argument of any other shape cannot be asked
    of a model, so the action waits until the owner fixes or hides that argument."""
    props, _ = properties(action)
    modes = arguments if arguments is not None else default_arguments(action)
    for name, schema in props.items():
        mode = (modes.get(name) or {}).get("mode", HIDDEN)
        if mode == OPEN and flat_type(schema) is None:
            return (f"\"{name}\" is not a plain value (text, a number, yes or no, one of a "
                    "list, or a list of text), so an agent cannot fill it in yet. Give it "
                    "a fixed value or hide it.")
    return None


def _value(kind: str | None, schema: dict[str, Any], value: Any, name: str) -> Any:
    """``value`` as this argument takes it, or ValueError with what is wrong."""
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError(f"\"{name}\" must be text")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"\"{name}\" must be true or false")
    elif kind in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"\"{name}\" must be a number")
        if kind == "integer":
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(f"\"{name}\" must be a whole number")
            value = int(value)
    elif kind == "enum":
        if value not in schema["enum"]:
            raise ValueError(f"\"{name}\" must be one of: {', '.join(schema['enum'])}")
    elif kind == "strings":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"\"{name}\" must be a list of text")
    return value


def check_arguments(action: Action, given: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The owner's choice of mode for every argument, checked. Arguments the owner did
    not mention take their default."""
    props, required = properties(action)
    out = default_arguments(action)
    for name, entry in (given or {}).items():
        if name not in props:
            raise Invalid(f"This action has no argument called \"{name}\".")
        mode = entry.get("mode") if isinstance(entry, dict) else None
        if mode not in MODES:
            raise Invalid(f"\"{name}\" must be open, fixed or hidden.")
        value = entry.get("value") if mode == FIXED else None
        if mode == FIXED:
            if value is None:
                raise Invalid(f"\"{name}\" is fixed, so it needs the value to use.")
            try:
                value = _value(flat_type(props[name]), props[name], value, name)
            except ValueError as exc:
                raise Invalid(f"The fixed value is not right: {exc}.") from None
        out[name] = {"mode": mode, "value": value}
    for name in required:
        if out[name]["mode"] == HIDDEN:
            raise Invalid(f"\"{name}\" is required by this action, so it cannot be hidden. "
                          "Leave it open or give it a fixed value.")
    return out


def start_name(rule: dict[str, Any]) -> str:
    """The name of the extra argument that reads on in a long answer."""
    return START_OTHER if START in rule.get("arguments", {}) else START


def tool_params(rule: dict[str, Any], action: Action
                ) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Rule ``hand_model_sees_open_only``: the arguments a model is told about. Open
    ones only: a fixed or hidden argument is not in the list, by name or by value."""
    props, required = properties(action)
    params: dict[str, dict[str, Any]] = {}
    for name, entry in rule["arguments"].items():
        if entry["mode"] != OPEN or name not in props:
            continue
        schema, kind = props[name], flat_type(props[name])
        param: dict[str, Any]
        if kind == "enum":
            param = {"type": "string", "enum": list(schema["enum"])}
        elif kind == "strings":
            param = {"type": "array", "items": {"type": "string"}}
        else:
            param = {"type": kind or "string"}
        param["description"] = line(schema.get("description") or name)
        params[name] = param
    if rule["effect"] == READ:
        params[start_name(rule)] = {
            "type": "integer",
            "description": "Where to read from in a long answer, in characters (default 0). "
                           "The last answer gives the number."}
    return params, [n for n in required if n in params]


def final_arguments(rule: dict[str, Any], action: Action, given: dict[str, Any] | None
                    ) -> tuple[dict[str, Any], int]:
    """Rule ``hand_arguments_by_mode``: the arguments that would be sent, and where to
    read from. The caller's open arguments, checked against their types, plus the
    owner's fixed ones. A caller that names a fixed or hidden argument, or one the
    action does not have, is refused: it can never set one."""
    props, required = properties(action)
    given = dict(given or {})
    reads_on = start_name(rule) if rule["effect"] == READ else None
    start = 0
    if reads_on and reads_on in given:
        raw = given.pop(reads_on)
        try:
            start = max(0, int(raw or 0))
        except (TypeError, ValueError):
            start = 0
    modes = rule["arguments"]
    final: dict[str, Any] = {}
    for name, value in given.items():
        mode = (modes.get(name) or {}).get("mode")
        if mode != OPEN or name not in props:
            allowed = sorted(n for n, m in modes.items() if m["mode"] == OPEN and n in props)
            raise HandRefused(
                "hand_arguments_by_mode",
                f"Nothing was done: you cannot set \"{line(name, 60)}\" on {rule['tool']}. "
                f"The arguments you can set are: {', '.join(allowed) or 'none'}. Call it "
                "again with only those.")
        if value is None:
            continue
        try:
            final[name] = _value(flat_type(props[name]), props[name], value, name)
        except ValueError as exc:
            raise HandRefused("hand_arguments_by_mode",
                              f"Nothing was done: {exc}. Call {rule['tool']} again with "
                              "that put right.") from None
    missing = [n for n in required if modes.get(n, {}).get("mode") == OPEN and n not in final]
    if missing:
        raise HandRefused("hand_arguments_by_mode",
                          f"Nothing was done: {rule['tool']} needs {', '.join(missing)}. "
                          "Call it again with that filled in.")
    for name, entry in modes.items():
        if entry["mode"] == FIXED and name in props:
            final[name] = entry["value"]
    return final, start


# --- levels and limits --------------------------------------------------------------------


def default_level(core: Any, effect: str, trusted: bool = False) -> str:
    """Rule ``hand_default_level``: the level offered when an action is switched on.

    A reading starts at ``hands.default_level_read`` (which may be "alone") only when
    its being a reading is ``trusted``: the owner said so, or an adapter whose type
    declares its hints trusted did. A reading on anybody else's word starts at "ask".
    Every other effect starts at ``hands.default_level_change``, except that a
    cautious kind (``bench.caution_categories``: sending, deleting, spending and
    changing access, unless the owner changed the list) never starts at "alone".
    This only picks what the form starts at: the owner saves the rule, at any level."""
    if effect != READ:
        start = str(core.settings.get("hands.default_level_change"))
        return ASK if start == ALONE and is_cautious(core, effect) else start
    return str(core.settings.get("hands.default_level_read")) if trusted else ASK


def start_level(core: Any, action: Action, effect: str, trusted: bool = False) -> str:
    """Rule ``hand_mail_starts_by_class``: mail from an agent's own inbox starts at
    "alone", because who receives it picks the level instead: the owner's three mail
    settings (to you, to someone written to before, to someone new). The rule's level
    stays a ceiling: set to "ask", every message asks. Anything else starts as
    ``default_level`` says."""
    if action.recipient_classes and effect == action.effect and effect != READ:
        return ALONE
    return default_level(core, effect, trusted)


MAIL_NOTE = ("Who receives each message decides whether it goes alone or asks you first: "
             "see Settings, Integrations, Mail from an agent's own inbox. Choose \"Ask me "
             "each time\" here to have every message ask.")


def is_cautious(core: Any, effect: str) -> bool:
    """Whether the owner counts this kind of action as one to be careful with. It
    picks a default and a sentence on the screen, and refuses nothing."""
    return effect in set(core.settings.get("bench.caution_categories"))


def ladder_row(agent: str, tool: str, effect: str) -> dict[str, str]:
    """The row of the Bench's tally that a rule's cards are counted on."""
    return {"actor": f"agent:{agent}", "tool": "hands", "action": tool, "category": effect}


def only_owner_raises(before: dict[str, Any] | None, level: str, actor: str) -> str | None:
    """Rule ``hand_only_owner_raises``: anything may lower a rule's level; only the
    owner switches an action on or gives it more freedom. None for yes, or why not."""
    if actor == "owner":
        return None
    if before and LEVELS.index(level) <= LEVELS.index(before["level"]):
        return None
    return ("Nothing was changed: only the signed-in owner can switch an action on or "
            "give it more freedom.")


def level_note(effect: str, effect_from: str, trusted: bool) -> str | None:
    """What the owner is told about a reading whose classification is not trusted: where
    it came from, what it starts at, and that "alone" is still theirs to choose."""
    if effect != READ or trusted:
        return None
    source = {"provider": "The provider itself says this action only reads",
              "adapter": "This action is taken to only read from its name",
              }.get(effect_from, "This action is guessed to only read")
    return (f"{source}, and nothing has checked that. So it starts at \"Ask me each "
            "time\". If you are sure it changes nothing you can still choose \"Alone\".")


NOT_YET = ("Nothing was changed: \"Alone\" is not available yet for an action that does more "
           "than read. Set it to \"Ask me each time\" instead.")

Gate = Callable[..., "str | None"]


def _no_alone_changes(core: Any, *, agent: str, account: str, action: Action, effect: str,
                      level: str, limits: dict[str, Any], confirmed: bool) -> str | None:
    return NOT_YET if level == ALONE and effect != READ else None


_gate: Gate = _no_alone_changes


def may_set_level(core: Any, *, agent: str, account: str, action: Action, effect: str,
                  level: str, limits: dict[str, Any], confirmed: bool) -> str | None:
    """Rule ``hand_level_gate``: may the owner put this action at this level? None for
    yes, or the sentence that says why not.

    This module does not decide when a cautious action may run alone. The Bench's
    ladder does, and a running JimmyHQ hands its answer in with ``set_level_gate``
    (the owner may choose any level). Without one, "alone" is refused for every effect
    but ``read``."""
    return _gate(core, agent=agent, account=account, action=action, effect=effect,
                 level=level, limits=limits, confirmed=confirmed)


def set_level_gate(gate: Gate | None) -> Gate:
    """Supply the answer ``may_set_level`` gives. None puts the cautious default back.
    Returns the gate that was in place."""
    global _gate
    before, _gate = _gate, gate or _no_alone_changes
    return before


def check_limits(action: Action, limits: dict[str, Any] | None) -> dict[str, Any]:
    """The owner's limits for a rule, checked against what the action can be limited by."""
    limits = {k: v for k, v in (limits or {}).items() if v is not None}
    unknown = set(limits) - {"per_day", "recipients", "max_amount"}
    if unknown:
        raise Invalid(f"There is no limit called \"{sorted(unknown)[0]}\".")
    per_day = limits.get("per_day")
    if per_day is not None and (isinstance(per_day, bool) or not isinstance(per_day, int)
                                or not 1 <= per_day <= 100_000):
        raise Invalid("The daily limit must be a whole number from 1 to 100000.")
    who = limits.get("recipients")
    if who is not None:
        if who not in RECIPIENT_LIMITS:
            raise Invalid(f"Who may receive must be one of: {', '.join(RECIPIENT_LIMITS)}.")
        if not action.recipients:
            raise Invalid("This action does not say who receives it, so it cannot be "
                          "limited by recipient.")
    most = limits.get("max_amount")
    if most is not None:
        if isinstance(most, bool) or not isinstance(most, int | float) or most < 0:
            raise Invalid("The largest amount must be a number, 0 or more.")
        if not action.amount:
            raise Invalid("This action does not carry an amount, so it cannot be limited "
                          "by amount.")
    return {"per_day": per_day, "recipients": who, "max_amount": most}


def recipients_of(action: Action, final: dict[str, Any]) -> list[str]:
    """Everyone the final arguments would send to, from the arguments the action names
    as recipients."""
    out: list[str] = []
    for name in action.recipients:
        value = final.get(name)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str):
                out.extend(p.strip() for p in _SPLIT.split(item) if p.strip())
            elif item is not None:
                out.append(str(item))
    return out


def _same(address: str) -> str:
    return address.strip().casefold()


def outside_limits(core: Any, rule: dict[str, Any], action: Action, final: dict[str, Any],
                   done_today: int) -> str | None:
    """Rule ``hand_alone_inside_limits``: why this call is outside the rule's limits, or
    None if it is inside them. Outside them an action set to run alone does not run: it
    becomes a request for the owner."""
    limits = rule.get("limits") or {}
    per_day = limits.get("per_day")
    if per_day is not None and done_today >= per_day:
        return f"today's limit of {per_day} for this action is used up"
    who = limits.get("recipients")
    if who in ("owner", "known"):
        allowed = {_same(a) for a in core.settings.get("hands.owner_addresses")}
        if who == "known":
            allowed |= {_same(a) for a in core.settings.get("hands.known_addresses")}
        if any(_same(r) not in allowed for r in recipients_of(action, final)):
            return ("it would go to someone who is not you" if who == "owner"
                    else "it would go to someone who is not on the list of known addresses")
    most = limits.get("max_amount")
    if most is not None and action.amount:
        amount = final.get(action.amount)
        if amount is not None:
            try:
                over = float(amount) > float(most)
            except (TypeError, ValueError):
                over = True
            if over:
                return f"the amount is above the limit of {most:g}"
    return None


def check_effect(action: Action, effect: str | None) -> tuple[str, str]:
    """The effect a rule records and where it came from. The provider's is a suggestion;
    the owner's correction is a decision."""
    if effect is None or effect == action.effect:
        source = action.effect_from if action.effect_from in EFFECT_FROM else "guess"
        return action.effect, source
    if effect not in EFFECTS:
        raise Invalid(f"The effect must be one of: {', '.join(EFFECTS)}.")
    return effect, "owner"
