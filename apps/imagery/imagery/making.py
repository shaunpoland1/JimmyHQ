"""Making pictures: a request is checked, becomes a run, and a worker thread makes the
pictures one at a time while the page polls.

A page's call may wait only 30 seconds and an image service can take longer, so ``Maker.start``
checks everything it can, writes the run and returns at once; ``drain`` (on its own thread)
does the asking. Each picture is one request to the service, counted against the day's limit
as it is sent, and kept as soon as it arrives, so a failure part-way keeps what was made.
The run ends with one sentence the owner reads. Tests set ``Ctx.threaded`` off and run the
queue themselves with ``drain``.
"""

from __future__ import annotations

import collections
import logging
import threading
from typing import Any

from jimmyhq_app import CallError

from imagery import gallery, spend
from imagery.context import Ctx
from imagery.providers import ProviderError, Reference, Request
from imagery.rules import (
    Refused,
    clean_prompt,
    count_of,
    join_style,
    parse_presets,
    whole,
)

log = logging.getLogger("imagery")

OPEN_MOST = 5  # runs waiting or being made at once
KEEP_MS = 3 * 86_400_000  # how long a finished run the owner never dismissed is kept
RESTARTED = ("Imagery was restarted while this was being made, so it stopped. Pictures "
             "already made are in the gallery. Make the rest again.")


def style_ending(cfg: Any, name: str) -> str:
    """The words a named style adds, or a ``Refused`` when the owner's list has no such one."""
    if not name:
        return ""
    for preset in parse_presets(cfg.style_presets):
        if preset["name"] == name:
            return preset["ending"]
    raise Refused(f"There is no style called \"{name}\" in your list, so nothing was made. "
                  "Choose one from the list, or none, or add it to \"Style endings\" in "
                  "Imagery's settings.")


class Maker:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self._queue: collections.deque[int] = collections.deque()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._active = False
        self._closed = False
        self._recovered = False
        self._stopping: set[int] = set()

    # --- asking ---------------------------------------------------------------------------

    def start(self, args: dict[str, Any]) -> dict[str, Any]:
        """Check a request and queue it. Nothing is sent to a service here."""
        ctx = self.ctx
        self._recover()
        cfg, store = ctx.cfg(), ctx.store
        prompt = clean_prompt(args.get("prompt"), int(cfg.prompt_max_chars))
        provider = ctx.provider()
        problem = provider.problem(cfg)
        if problem:
            raise Refused(problem)
        aspect = str(args.get("aspect") or "").strip()
        if aspect not in provider.aspects(cfg):
            raise Refused(f"{provider.label} cannot make pictures in the shape "
                          f"\"{aspect or 'none chosen'}\". Nothing was made. Choose one of "
                          "the shapes on the page, or change the shapes in Imagery's "
                          "settings.")
        count = count_of(args.get("count", 1), int(cfg.max_count))
        style = str(args.get("style") or "").strip()
        full = join_style(prompt, style_ending(cfg, style))
        reference = self._reference(args.get("reference"), provider)
        spend.check(ctx, provider, count)
        room = gallery.room_problem(ctx)
        if room:
            raise Refused(room)
        if store.number("SELECT COUNT(*) FROM run WHERE state IN ('queued','working')") \
                >= OPEN_MOST:
            raise Refused(f"{OPEN_MOST} requests are already waiting or being made. Nothing "
                          "was made. Wait for one to finish, then ask again.")
        now = ctx.now()
        run_id = store.insert("run", {
            "prompt": prompt, "style": style, "full_prompt": full, "provider": provider.id,
            "model": provider.model(cfg), "aspect": aspect, "want": count, "state": "queued",
            "reference_id": reference["id"] if reference else None, "created": now,
            "updated": now})
        with self._lock:
            self._queue.append(run_id)
        if ctx.threaded:
            self._ensure_thread()
        ctx.emit()
        return {"run": self.run_row(store.run(run_id) or {}), "said": (
            f"Making {count} picture{'s' if count != 1 else ''}. They appear in the gallery "
            "as each one is ready.")}

    def _reference(self, picture_id: Any, provider: Any) -> dict[str, Any] | None:
        if picture_id in (None, "", 0):
            return None
        if not provider.supports_reference:
            raise Refused(f"{provider.label} cannot start from a picture, so nothing was "
                          "made. Take the starting picture out, or choose an image service "
                          "that can (Gemini).")
        pic = self.ctx.store.picture(whole(picture_id, "reference"))
        if pic is None:
            raise Refused("The starting picture is gone, so nothing was made. Choose "
                          "another, or none.")
        return pic

    def stop(self, args: dict[str, Any]) -> dict[str, Any]:
        """Stop a run after the picture being made: the rest of it is not asked for."""
        run = self.ctx.store.run(whole(args.get("id"), "run"))
        if run is None or run["state"] not in ("queued", "working"):
            raise Refused("That request is not running, so there is nothing to stop.")
        with self._lock:
            self._stopping.add(run["id"])
            if run["state"] == "queued" and run["id"] in self._queue:
                self._queue.remove(run["id"])
                self._finish(run["id"], "stopped", "Stopped before anything was asked.")
        self.ctx.emit()
        return self.status()

    def dismiss(self, args: dict[str, Any]) -> dict[str, Any]:
        run = self.ctx.store.run(whole(args.get("id"), "run"))
        if run and run["state"] not in ("queued", "working"):
            self.ctx.store.forget_run(run["id"])
        return self.status()

    # --- what the page is shown -------------------------------------------------------------

    def run_row(self, run: dict[str, Any]) -> dict[str, Any]:
        keys = ("id", "prompt", "style", "provider", "model", "aspect", "want", "made",
                "state", "said", "created", "updated")
        return {k: run[k] for k in keys}

    def status(self) -> dict[str, Any]:
        """The runs waiting, being made, or finished and not yet dismissed."""
        self._recover()
        store = self.ctx.store
        for old in store.all("SELECT id FROM run WHERE state NOT IN ('queued','working') "
                             "AND updated<?", (self.ctx.now() - KEEP_MS,)):
            store.forget_run(old["id"])
        rows = store.all("SELECT * FROM run ORDER BY id DESC LIMIT 12")
        return {"runs": [self.run_row(r) for r in rows],
                "busy": any(r["state"] in ("queued", "working") for r in rows)}

    # --- doing the work -------------------------------------------------------------------

    def _recover(self) -> None:
        """A run left queued or working by an earlier start of the program cannot be
        going on: say so."""
        if self._recovered:
            return
        self._recovered = True
        store = self.ctx.store
        for run in store.all("SELECT id, made FROM run WHERE state IN ('queued','working')"):
            self._finish(run["id"], "failed", RESTARTED)

    def _finish(self, run_id: int, state: str, said: str) -> None:
        self.ctx.store.update("run", run_id, state=state, said=said, updated=self.ctx.now())

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._active:
                return
            self._active = True  # cleared under this lock by the thread as it finds no work
            self._thread = threading.Thread(target=self.drain, name="imagery-maker",
                                            daemon=True)
            self._thread.start()

    def wait(self, timeout: float = 10.0) -> None:
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def shut_down(self) -> None:
        self._closed = True
        with self._lock:
            self._queue.clear()
        self.wait(5)

    def drain(self) -> None:
        """Make every queued run, one after another, until none is left."""
        while not self._closed:
            with self._lock:
                if not self._queue:
                    self._active = False
                    break
                run_id = self._queue.popleft()
            try:
                self._do(run_id)
            except Exception:  # noqa: BLE001 - one bad run must not kill the queue
                log.exception("an Imagery run failed")
                self._finish(run_id, "failed", "Something went wrong inside Imagery, so "
                             "the rest was not made. Try again.")
            self.ctx.emit()
        self.ctx.emit()

    def _do(self, run_id: int) -> None:
        ctx = self.ctx
        store = ctx.store
        run = store.run(run_id)
        if run is None or run["state"] != "queued":
            return
        store.update("run", run_id, state="working", updated=ctx.now())
        ctx.emit()
        provider = ctx.providers.get(run["provider"])
        made = 0
        said = ""
        state = "done"
        try:
            if provider is None:
                raise Refused("The image service this was asked of is no longer there, so "
                              "nothing was made.")
            for _ in range(int(run["want"])):
                if run_id in self._stopping:
                    state, said = "stopped", "Stopped. Nothing more was asked for."
                    break
                self._one(run, provider)
                made += 1
                store.update("run", run_id, made=made, updated=ctx.now())
                ctx.emit()
        except ProviderError as error:
            state, said = "failed", error.said
        except Refused as error:
            state, said = "failed", str(error)
        except CallError as error:
            state, said = "failed", error.message
        self._stopping.discard(run_id)
        if state == "done":
            said = f"Made {made} picture{'s' if made != 1 else ''}."
        else:
            said = f"Made {made} of {run['want']}. {said}" if made else said
        store.update("run", run_id, state=state, said=said, made=made, updated=ctx.now())

    def _one(self, run: dict[str, Any], provider: Any) -> None:
        """Ask for one picture and keep it."""
        ctx = self.ctx
        cfg = ctx.cfg()
        reference = None
        if run["reference_id"] is not None:
            pic = ctx.store.picture(run["reference_id"])
            if pic is None:
                raise Refused("The starting picture was deleted before it could be used, "
                              "so nothing more was asked for.")
            reference = Reference(pic["ref"], pic["mime"])
        taken = spend.take(ctx, provider, run["id"])
        try:
            got = provider.make(Request(run["full_prompt"], run["aspect"], reference), cfg)
        except ProviderError as error:
            if not error.sent:
                spend.give_back(ctx, taken)
            raise
        kept = gallery.keep(
            ctx, got.data, got.mime, kind="made", prompt=run["prompt"], style=run["style"],
            provider=provider.id, model=run["model"], aspect=run["aspect"], said=got.said,
            run_id=run["id"])
        gallery.prune(ctx, spare_id=kept.get("id"))
