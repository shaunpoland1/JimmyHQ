"""Helpers for the bridge and app-file tests: a made-up record of what is installed
(the seam with the install package), and a model that only remembers what it was asked."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jimmyhq.apps import bridge, manifest
from jimmyhq.models.shapes import Reply

WATER: dict[str, Any] = {
    "manifest": 1, "id": "water", "name": "Water", "summary": "Count glasses of water.",
    "version": "1.0.0", "tier": "sandboxed", "api": "1.0", "licence": "MIT",
    "permissions": ["tasks.write"],
    "screens": [{"id": "main", "label": "Water", "entry": "index.html"}],
    "settings": [{"key": "goal", "type": "int", "default": 8, "min": 1, "max": 30,
                  "label": "Glasses a day", "help": "Your daily goal."}],
}

BOOKS: dict[str, Any] = {
    "manifest": 1, "id": "books_read", "name": "Books read", "summary": "A reading log.",
    "version": "1.0.0", "tier": "sandboxed", "api": "1.0", "licence": "MIT",
    "permissions": [],
    "screens": [{"id": "main", "label": "Books read", "entry": "described"}],
    "tables": {
        "book": {"fields": {
            "title": {"type": "text", "required": True},
            "finished": {"type": "date"},
            "stars": {"type": "int", "min": 1, "max": 5},
            "shelf": {"type": "choice", "options": ["home", "lent"]},
            "price": {"type": "money", "currency": "EUR", "min": 0},
            "author": {"type": "link", "table": "writer"},
            "worth": {"type": "computed", "formula": "price * stars"},
        }},
        "writer": {"fields": {"name": {"type": "text", "required": True}}},
    },
    "views": [{"id": "all", "label": "All", "table": "book", "sort": ["-finished"]}],
}

EVERYTHING = ("tasks.read", "tasks.write", "bench.file", "model.ask", "memory.recall")


class Installs:
    """What is installed, as the install package would answer it."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "installed"
        self.apps: dict[str, bridge.Installed] = {}

    def find(self, core: Any, app_id: str) -> bridge.Installed | None:
        return self.apps.get(app_id)

    def add(self, source: dict[str, Any], *, granted: Any = None, enabled: bool = True,
            development: bool = False, hash8: str = "0a1b2c3d", **changes: Any
            ) -> bridge.Installed:
        """Install an app. ``granted`` is what the owner agreed to; it defaults to what
        the manifest asks for. ``changes`` replace manifest fields."""
        parsed = manifest.parse({**source, **changes})
        folder = self.root / parsed.id / "versions" / f"{parsed.version}-{hash8}"
        folder.mkdir(parents=True, exist_ok=True)
        app = bridge.Installed(
            manifest=parsed, enabled=enabled, folder=folder, development=development,
            granted=tuple(parsed.permissions if granted is None else granted))
        self.apps[parsed.id] = app
        return app


def installs(monkeypatch: Any, tmp_path: Path) -> Installs:
    made = Installs(tmp_path)
    monkeypatch.setattr(bridge, "find_app", made.find)
    return made


def call(core: Any, app_id: Any, name: Any, args: Any = None) -> Any:
    """One bridge call, as the route would make it. Raises ``BridgeError``."""
    return asyncio.run(bridge.dispatch(core, app_id, name, args))


def refused(core: Any, app_id: Any, name: Any, args: Any = None) -> bridge.BridgeError:
    """The refusal a call ends in. Fails the test if the call went through."""
    try:
        result = call(core, app_id, name, args)
    except bridge.BridgeError as error:
        return error
    raise AssertionError(f"{name} was carried out and returned {result!r}")


class Model:
    """Stands in for the Brain: answers at once and keeps the options of each call.
    ``roles`` are the roles the owner has chosen a model for."""

    def __init__(self, text: str = "an answer", tokens: tuple[int, int] = (7, 5),
                 fail: Exception | None = None,
                 roles: tuple[str, ...] = ("default", "vision", "embeddings")):
        self.text, self.tokens, self.fail = text, tokens, fail
        self.roles = roles
        self.calls: list[dict[str, Any]] = []

    def config(self) -> Any:
        return SimpleNamespace(roles={role: ["chosen"] for role in self.roles})

    async def chat(self, messages: Any, **options: Any) -> Reply:
        self.calls.append({"messages": list(messages), **options})
        if self.fail is not None:
            raise self.fail
        return Reply(text=self.text, provider_id="home", provider_type="example",
                     model="example-model", input_tokens=self.tokens[0],
                     output_tokens=self.tokens[1], duration_ms=1, fallback_from=None,
                     fallback_reason=None, local=True)


def model(monkeypatch: Any, **how: Any) -> Model:
    made = Model(**how)
    monkeypatch.setattr(bridge, "_brain", lambda core: made)
    return made


def events(core: Any, kind: str) -> list[Any]:
    return [e for e in core.tape.after(0, limit=100000) if e.kind == kind]
