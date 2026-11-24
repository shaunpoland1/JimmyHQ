"""The tool catalog: what a tool is, the schemas a model is shown, and the reading of
what comes back.

A model's reply is checked here before anything is done with it. A reply the tools
cannot read is the model's slip, not the world in the agent's way, so the reasons below
are written to be shown to the agent and tried again.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from jimmyhq.village.rules import rule

MODES = {"W": "work", "E": "explore", "O": "outlet", "S": "social"}

THOUGHT = {"type": "string", "description":
           "What you are thinking right now, in a sentence or two, in your own words."}

# Names a model reaches for instead of the real argument name.
ALIASES = {
    "ref": "element", "element_id": "element", "number": "element", "index": "element",
    "id": "element", "link": "url", "address": "url", "folder": "path", "file": "path",
    "filename": "path", "file_path": "path", "body": "content",
}
WRAPPERS = ("args", "arguments", "parameters", "input")


class ToolError(ValueError):
    """The reply could not be read as a call to a tool on offer. The text is for the
    agent."""


@dataclass
class Done:
    """What a tool did. ``result`` is the sentence the agent reads. ``args_logged``
    replaces the arguments in the log.

    One rule decides between this and a ``Refusal``. When the village itself says no
    (a rule, a limit, a path that does not exist in the agent's own files) nothing was
    tried: the tool raises ``Refusal``, and the agent gets another go in the same beat.
    ``ok=False`` is only for an action that was really tried on something outside the
    village and failed there (a page that would not load): it is recorded as a failed
    action. The web tools return it for a search or a page that failed out there.

    ``shown`` is for a tool that brings in text from outside. ``result`` is then a line
    the village wrote (it is logged, listed among the last actions and read by the
    night); ``shown`` is what the agent reads in its next view, once, with the outside
    text marked as untrusted. It is never logged."""

    result: str
    args_logged: dict[str, Any] | None = None
    category: str = "self"
    ok: bool = True
    shown: str | None = None


@dataclass
class ToolCtx:
    v: Any
    agent_id: str
    agent: Any
    mode: str
    task: str | None
    now: float


@dataclass(frozen=True)
class Tool:
    name: str
    kind: str
    modes: Any
    description: str
    params: dict[str, dict[str, Any]]
    required: tuple[str, ...] | list[str]
    run: Callable[[ToolCtx, dict[str, Any]], Awaitable[Done]]
    capability: str | None = None


def mode_names(modes: Any) -> frozenset[str]:
    """The modes a tool is offered in, whether written ``"WEO"`` or as full names."""
    parts: Iterable[str] = modes if not isinstance(modes, str) else (
        list(modes) if modes.isupper() else [modes])
    return frozenset(MODES.get(p, p) for p in parts)


def named(value: Any, owner: str) -> Any:
    """A tool's words with ``{owner}`` filled in, at any depth of its schema."""
    if isinstance(value, str):
        return value.replace("{owner}", owner)
    if isinstance(value, dict):
        return {k: named(x, owner) for k, x in value.items()}
    if isinstance(value, (list, tuple)):
        return [named(x, owner) for x in value]
    return value


@rule("thought_first")
def with_thought(tool: Tool, owner: str = "the owner") -> dict[str, Any]:
    """The schema a model sees. When a tool call is required a model skips free text,
    so the thought rides along as the first required argument of every tool. Tools are
    written with ``{owner}`` where the owner's name goes."""
    return {"name": tool.name, "description": named(tool.description, owner), "parameters": {
        "type": "object", "properties": {"thought": THOUGHT, **named(tool.params, owner)},
        "required": ["thought", *tool.required]}}


def unwrap(args: Any, params: dict[str, Any]) -> Any:
    """Arguments sent inside a wrapper, such as ``{"arguments": "{...}"}`` or the whole
    call ``{"name": ..., "arguments": ...}``: use the inside."""
    if isinstance(args, dict) and len(args) == 2 and "name" in args and "name" not in params:
        inner = next((k for k in args if k != "name"), "")
        if inner in WRAPPERS:
            args = {inner: args[inner]}
    if isinstance(args, dict) and len(args) == 1:
        (k, v), = args.items()
        if k in WRAPPERS and k not in params:
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    return args
            if isinstance(v, dict):
                return v
    return args


def as_list(v: Any) -> list[Any]:
    """A list sent as its own text, such as ``"['notes/a.md']"``."""
    if isinstance(v, str) and v.strip().startswith("[") and v.strip().endswith("]"):
        for parse in (json.loads, ast.literal_eval):
            try:
                out = parse(v.strip())
            except (ValueError, SyntaxError):
                continue
            if isinstance(out, list):
                return out
    return [v]


PARAM_TAG = re.compile(
    r'<parameter name="([^"]+)">\s*(.*?)\s*(?=</parameter>|<parameter |</invoke>|$)', re.S)


def salvage(text: str, params: dict[str, Any]) -> dict[str, Any]:
    """Arguments written into the thought as markup instead of being sent. Only names
    the tool knows are kept."""
    out: dict[str, Any] = {}
    for name, value in PARAM_TAG.findall(text or ""):
        value = value.strip()
        if value.startswith("{"):
            try:
                inner = json.loads(value)
            except ValueError:
                inner = None
            if isinstance(inner, dict):
                out.update({k: x for k, x in inner.items() if k in params and k not in out})
                continue
        if name in params and name not in out and value:
            out[name] = value
    return out


@rule("argument_repair")
def repair(tool: Tool, args: Any, thought: str) -> tuple[Any, str]:
    """Mend the ways a model commonly garbles a call before judging it: arguments in a
    wrapper, and arguments written as markup inside the thought. (Other names for an
    argument, a list sent as text and an empty optional are mended in ``validate``.)
    Returns the arguments and the thought with any markup taken out."""
    args = unwrap(args, tool.params)
    if args in ({}, None, "") and "<parameter" in (thought or ""):
        found = salvage(thought, tool.params)
        if found:
            return found, thought.split("</thinking>")[0].split("<parameter")[0].strip()
    return args, thought


class Catalog:
    def __init__(self, tools: Iterable[Tool]):
        self.tools: dict[str, Tool] = {t.name: t for t in tools}

    def _on_offer(self, t: Tool, mode: str, capabilities: Iterable[str],
                  extra: Iterable[str]) -> bool:
        if t.capability is not None and t.capability not in capabilities:
            return False
        return t.name in extra or mode in mode_names(t.modes)

    def offered(self, mode: str, capabilities: Iterable[str] = (),
                extra: Iterable[str] = ()) -> list[str]:
        """The tools this agent may use right now. A tool with a capability is offered
        only to an agent whose configuration lists it; ``extra`` names tools the
        situation offers outside their mode."""
        caps, more = tuple(capabilities), tuple(extra)
        return [n for n, t in self.tools.items() if self._on_offer(t, mode, caps, more)]

    def schemas(self, mode: str, capabilities: Iterable[str] = (),
                extra: Iterable[str] = (), owner: str = "the owner") -> list[dict[str, Any]]:
        return [with_thought(self.tools[n], owner)
                for n in self.offered(mode, capabilities, extra)]

    def better_fit(self, name: str, args: Any, on_offer: list[str]) -> str:
        """When the arguments clearly belong to another tool on offer, say which."""
        if not isinstance(args, dict) or not args:
            return ""
        fits = []
        for other in on_offer:
            t = self.tools[other]
            if other == name:
                continue
            keys = {k if k in t.params else ALIASES.get(k, k) for k in args}
            if keys <= set(t.params) and set(t.required) <= keys:
                fits.append(other)
        if not fits:
            return ""
        return f". Those arguments fit {' or '.join(fits[:3])}: use that instead"

    @rule("unreadable_arguments")
    def validate(self, name: str, args: Any, mode: str, capabilities: Iterable[str] = (),
                 extra: Iterable[str] = ()) -> dict[str, Any]:
        """Clean arguments, or ``ToolError`` with a reason the agent can read: an
        unknown tool, one not on offer, a missing required argument, a wrong type or a
        value outside the allowed ones."""
        if name not in self.tools:
            raise ToolError(f"there is no tool called {name}")
        t = self.tools[name]
        on_offer = self.offered(mode, capabilities, extra)
        if name not in on_offer:
            raise ToolError(f"{name} is not available right now ({mode})")
        args = unwrap(args, t.params)
        if not isinstance(args, dict):
            raise ToolError("the arguments were not readable")
        clean: dict[str, Any] = {}
        for k, v in args.items():
            k2 = k if k in t.params else ALIASES.get(k, k)
            if k2 in t.params and k2 not in clean:
                clean[k2] = v
        for req in t.required:
            if req not in clean or clean[req] in (None, ""):
                raise ToolError(f"{name} needs {req}" + self.better_fit(name, args, on_offer))
        for k, spec in t.params.items():
            if k not in clean:
                continue
            v = clean[k]
            kind = spec.get("type", "string")
            if kind != "string" and k not in t.required and v in (None, ""):
                del clean[k]  # an empty optional is unset
                continue
            try:
                if kind == "integer":
                    clean[k] = int(str(v).strip("[] "))
                elif kind == "number":
                    clean[k] = float(str(v).strip("[] "))
                elif kind == "boolean":
                    clean[k] = v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes")
                elif kind == "string":
                    clean[k] = str(v)
                elif kind == "array":
                    clean[k] = [str(x) for x in (v if isinstance(v, list) else as_list(v))]
            except (TypeError, ValueError) as e:
                raise ToolError(f"{name}: {k} should be a {kind}") from e
            if "enum" in spec and clean[k] not in spec["enum"]:
                raise ToolError(f"{name}: {k} must be one of "
                                f"{', '.join(str(x) for x in spec['enum'])}")
        return clean
