"""Memory: one Library of Markdown notes, one index over it, one way to recall.

Three layers. The Tape is the raw record of what happened. The **Library** is
plain Markdown files in ``<data folder>/memory/`` that a person can read, edit
and grep; the files are the truth. The **Index** (``memory.db`` beside it) is
search over the Library and can be rebuilt from the files at any time.

Other modules use the functions exported here:

    from jimmyhq import memory

    found = await memory.arecall(core, "when do they like to meet?",
                                 actor="agent:scout", caller="chat.reply")
    prompt_block = found.render()        # the only text to put in a prompt

    fact = memory.remember(core, "Prefers meetings after 10 a.m.", kind="preference",
                           actor="agent:miner", confidence=0.7,
                           evidence=["tape:01J..."])
    memory.forget(core, fact.id, actor="owner", reason="No longer true.")

Beside the index sits ``memory_store.db``, which cannot be rebuilt: earlier
versions of notes (with a real delete: ``delete_note(history=True)``,
``forget_everywhere``), and what **mining** proposed. Mining reads sources and
proposes facts; a module or app adds a source with ``register_source``:

    memory.register_source(memory.Source(
        id="journal", label="Journal", description="What you write in your journal.",
        default_on=False, collect=collect))   # collect(core, cursor, limit)

See jimmyhq/modules.py for how a module plugs in, and docs/memory.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jimmyhq.core import Core
from jimmyhq.ids import now_ms
from jimmyhq.memory import mining as _mining
from jimmyhq.memory import search as _search
from jimmyhq.memory.errors import LibraryError
from jimmyhq.memory.library import DISK, Fact, Memory
from jimmyhq.memory.search import Passage, Recall
from jimmyhq.memory.sources import Item, Source, register_source, unregister_source
from jimmyhq.scheduler import Scheduler

__all__ = [
    "DISK", "Fact", "Item", "LibraryError", "Memory", "Passage", "Recall", "Source",
    "activity", "arecall", "build", "decline_candidate", "delete_note", "edit_fact",
    "embed_pending", "erase_candidate", "evidence_erased", "forget", "forget_everywhere",
    "get_fact", "jobs",
    "keep_candidate",
    "library", "list_candidates", "list_facts", "mine", "move_note", "prune_history",
    "read_note", "recall", "register_source", "reindex", "remember", "save_note", "scan",
    "sources_status", "status", "unregister_source",
]

# The owner's click on a memory card reaches memory wherever the click lands.
_mining.register_bench_handler()

JOB_TICK_S = 10  # how often the worker asks whether a scan or more embedding is due


def library(core: Core) -> Memory:
    """This process's handle on the Library. It keeps nothing that matters in
    memory, so the web and worker processes each have one."""
    return core.service("memory", Memory)


def recall(core: Core, query: str, *, actor: str, caller: str,
           scope: tuple[str, ...] | list[str] | str = ("shared",), k: int | None = None,
           budget_chars: int | None = None, private: bool = False,
           related_max: int | None = None) -> Recall:
    """Find what memory holds about ``query`` and print a receipt for it.

    The receipt never carries the question: only its length and a keyed hash
    that cannot be guessed at without this install's key. With ``private``
    (an off-the-record conversation) it carries neither, and nothing that
    ties it to a conversation.

    ``scope`` names the folders to look in: ``"shared"``, ``"agents/<id>"``,
    any other folder of the Library, or ``"*"`` for everything. ``k`` is the
    most passages, ``budget_chars`` the most characters ``render()`` may
    return; both default to the owner's settings. ``related_max`` is for
    showing results to a person: matches first, then at most that many
    passages related only in meaning. Works from any code; async
    code should use ``arecall`` so the event loop is not held.
    """
    return _search.recall_sync(library(core), query, actor=actor, caller=caller, scope=scope,
                               k=k, budget_chars=budget_chars, private=private,
                               related_max=related_max)


async def arecall(core: Core, query: str, *, actor: str, caller: str,
                  scope: tuple[str, ...] | list[str] | str = ("shared",),
                  k: int | None = None, budget_chars: int | None = None,
                  private: bool = False, related_max: int | None = None) -> Recall:
    """``recall`` for async code."""
    return await _search.arecall(library(core), query, actor=actor, caller=caller,
                                 scope=scope, k=k, budget_chars=budget_chars, private=private,
                                 related_max=related_max)


def remember(core: Core, text: str, *, kind: str, note: str | None = None, actor: str,
             confidence: float = 1.0, evidence: tuple[str, ...] | list[str] = (),
             confirmed: bool = False) -> Fact:
    """Add a fact to the right note, unless a live fact already says it.

    Returns the fact. ``fact.created`` is False when an existing fact (the
    same words, or nearly) was returned instead. Without ``note``, the kind
    decides the note (the ``memory.kind_notes`` setting).
    """
    return library(core).remember(text, kind=kind, note=note, actor=actor,
                                  confidence=confidence, evidence=evidence,
                                  confirmed=confirmed)


def forget(core: Core, fact_id: str, *, actor: str, reason: str | None = None) -> Fact:
    """Remove a fact's line from its note and retire it. Returns it as it was."""
    return library(core).forget(fact_id, actor=actor, reason=reason)


def get_fact(core: Core, fact_id: str, *, live_only: bool = True) -> Fact:
    return library(core).get_fact(fact_id, live_only=live_only)


def edit_fact(core: Core, fact_id: str, *, actor: str, text: str | None = None,
              kind: str | None = None, confirmed: bool | None = None) -> Fact:
    return library(core).edit_fact(fact_id, actor=actor, text=text, kind=kind,
                                   confirmed=confirmed)


def list_facts(core: Core, **filters: Any) -> dict[str, Any]:
    """``kind``, ``q``, ``confirmed``, ``note``, ``limit``, ``before``. Returns
    ``{"facts": [...], "next_before": marker or None}``."""
    return library(core).list_facts(**filters)


def read_note(core: Core, path: str) -> dict[str, Any]:
    return library(core).read_note(path)


def save_note(core: Core, path: str, text: str, *, actor: str,
              revision: str | None = None) -> dict[str, Any]:
    return library(core).save_note(path, text, actor=actor, revision=revision)


def delete_note(core: Core, path: str, *, actor: str, history: bool = False
                ) -> dict[str, int] | None:
    """Delete a note. With ``history``, every stored version of it is deleted
    for good as well, and the counts are returned."""
    return library(core).delete_note(path, actor=actor, history=history)


def forget_everywhere(core: Core, fact_id: str, *, actor: str,
                      reason: str | None = None) -> dict[str, Any]:
    """Forget a fact and delete every stored version of any note that
    contains its line. Returns ``{"fact": Fact, "purged": counts}``."""
    return library(core).forget_everywhere(fact_id, actor=actor, reason=reason)


def prune_history(core: Core, *, force: bool = False) -> dict[str, int] | None:
    """Apply the limits on kept versions (``memory.history_keep_versions`` and
    ``memory.history_keep_days``)."""
    return library(core).prune_history(force=force)


def mine(core: Core, *, source: str | None = None, trigger: str = "schedule") -> dict[str, Any]:
    """Read a bounded batch from each source that is on and propose facts."""
    return _mining.mine(core, source=source, trigger=trigger)


def sources_status(core: Core) -> dict[str, Any]:
    return _mining.sources_status(core)


def list_candidates(core: Core, **filters: Any) -> dict[str, Any]:
    """``state``, ``source``, ``limit``, ``before``."""
    return _mining.list_candidates(core, **filters)


def keep_candidate(core: Core, candidate_id: str) -> dict[str, Any]:
    return _mining.keep(core, candidate_id)


def decline_candidate(core: Core, candidate_id: str, reason: str) -> dict[str, Any]:
    return _mining.decline(core, candidate_id, reason)


def erase_candidate(core: Core, candidate_id: str) -> dict[str, Any]:
    """Delete for good what memory keeps of a proposed fact. Only a hash of
    its statement stays, so the same words are not proposed again."""
    return _mining.erase(core, candidate_id)


def evidence_erased(core: Core, refs: Any, *, actor: str, reason: str) -> dict[str, int]:
    """Tell memory that something it may have read was erased for good, by the
    references mining knew it by (``tape:<event id>``). Quotes of it are
    removed from mining's own store and from the Bench cards that showed
    them; facts already saved stay. See ``mining.evidence_erased``."""
    return _mining.evidence_erased(core, refs, actor=actor, reason=reason)


def activity(core: Core, *, limit: int = 50, before: str | None = None) -> dict[str, Any]:
    return _mining.activity(core, limit=limit, before=before)


def move_note(core: Core, src: str, dst: str, *, actor: str) -> dict[str, Any]:
    return library(core).move_note(src, dst, actor=actor)


def scan(core: Core, *, settle: bool = False) -> dict[str, Any]:
    """Notice what changed on disk and bring the index and the record up to date.

    With ``settle``, a note changed in the last two seconds is left for the
    next scan rather than given ids while a program may still be writing it.
    """
    return library(core).scan(settle=settle)


def reindex(core: Core) -> dict[str, Any]:
    """Rebuild the search index from the files."""
    return library(core).reindex()


def embed_pending(core: Core, limit: int | None = None) -> dict[str, Any]:
    """Prepare one batch of passages for meaning search."""
    return _search.embed_pending(library(core), limit)


def status(core: Core, *, show_path: bool = True) -> dict[str, Any]:
    """``show_path`` False leaves out where the Library is on disk: that is
    for the owner, not for a token."""
    mem = library(core)
    mem.ensure()
    state = _search.embeddings_state(mem)
    counts = mem.counts()
    model = state["model"]
    with_vectors = 0
    if model is not None:
        with_vectors = mem.read(
            "SELECT COUNT(*) FROM chunks c WHERE EXISTS (SELECT 1 FROM vectors v"
            " WHERE v.hash = c.hash AND v.model = ?)", (model,))[0][0]
    reason = state["reason"]
    failed = mem.meta("embed_failed")
    if model is not None and failed and failed.get("model") == model:
        reason = f"{failed['reason']} Search matches words only until it answers again."
    elif model is not None and counts["chunks"] and with_vectors == 0:
        reason = ("Passages are being prepared for meaning search in the background. Until "
                  "the first are ready, search matches words only.")
    return {
        "library_path": str(mem.root) if show_path else None,
        "counts": counts | {"chunks_with_vectors": with_vectors},
        "index_version": (mem.meta("index") or {}).get("version"),
        "last_scan": mem.meta("last_scan"),
        "skipped": mem.skipped(),
        "meaning_search": {
            "on": model is not None and reason is None,
            "reason": reason,
            "model": model,
            "local_only": state["local_only"],
            "ready": with_vectors,
            "waiting": counts["chunks"] - with_vectors if model is not None else None,
        },
        "watch_disk": core.settings.get("memory.watch_disk"),
    }


def build(core: Core) -> APIRouter:
    from jimmyhq.memory.routes import build_router  # noqa: PLC0415 - keeps import light

    return build_router(core)


def jobs(core: Core, scheduler: Scheduler) -> None:
    """The worker's memory jobs. Each asks, every few seconds, whether it
    has anything to do, so a change to a setting takes effect without a restart."""

    def scan_if_due() -> None:
        if not core.settings.get("memory.watch_disk"):
            return
        last = library(core).meta("last_scan") or {}
        # A note that was still being written last time is looked at again at
        # the next tick rather than a whole interval later.
        wait_s = JOB_TICK_S if last.get("deferred") else core.settings.get(
            "memory.scan_interval_s")
        if now_ms() - last.get("at_ms", 0) >= wait_s * 1000:
            scan(core, settle=True)

    def embed_some() -> None:
        embed_pending(core)

    def prune() -> None:
        prune_history(core)

    def mine_some() -> None:
        _mining.mine_if_due(core)

    scheduler.every("memory.scan", JOB_TICK_S, scan_if_due)
    scheduler.every("memory.embed", JOB_TICK_S, embed_some)
    scheduler.every("memory.prune", JOB_TICK_S, prune)
    scheduler.every("memory.mine", JOB_TICK_S, mine_some)
