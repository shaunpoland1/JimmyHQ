"""The one retrieval function, the vectors behind meaning search, and receipts.

``recall`` is the only way memory reaches a prompt. It searches by keyword
(FTS5) and, when an embeddings model is usable, by meaning (vectors compared
with numpy); fuses the two rankings; drops near-duplicates; prefers confirmed
facts and newer text a little; then admits whole passages until the caller's
character budget is full. What it hands back is a ``Recall`` whose
``render()`` is the exact text for the prompt, and the ``memory.recalled``
receipt records that text's length and the passages in it, nothing more.

**Fusion.** Reciprocal rank fusion: a passage scores
``weight / (60 + rank)`` in each list it appears in, summed. That needs no
agreement between the two methods' own scores, only their order. The sum is
then multiplied by ``1 + confirmed_boost`` for a confirmed fact and by
``1 + recency_boost * 0.5 ** (age / half_life)``.

**Matches and related passages.** A passage that shares a word with the
question (``why`` is "keyword" or "both") is a match. One found by meaning
alone (``why`` is "meaning") is only related. Meaning search ranks every
passage, so in a small Library everything is "nearest" to something; a
related passage is therefore kept only when it is at least
``memory.min_similarity`` similar to the question *and* within
``memory.related_margin`` of the most similar passage in scope. The search
screen also passes ``related_max``: matches come first there, and at most that
many related passages follow them.

**Scale.** Vectors are compared by brute force: one matrix product over every
passage in scope. That is a few milliseconds up to tens of thousands of
passages. Past roughly a hundred thousand it stops being true (hundreds of
megabytes held in memory, and a noticeable pause on every search) and an
approximate index would be needed.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from jimmyhq.core import Core
from jimmyhq.ids import now_ms
from jimmyhq.memory import notes as md
from jimmyhq.memory.errors import LibraryError
from jimmyhq.memory.library import Memory, storable
from jimmyhq.memory.paths import clean_folder
from jimmyhq.models import ModelError, brain
from jimmyhq.models.validate import CALLER

log = logging.getLogger("jimmyhq.memory")

RRF_K = 60
CANDIDATES = 60  # how far down each ranking fusion looks
MAX_QUERY = 4000
MAX_EMBED_CHARS = 8000  # of one passage sent to the embeddings model
MAX_TERMS = 48
DAY_MS = 86_400_000
EVERYWHERE = "*"
RECEIPT_KEY = "memory.receipt_key"  # the secret that keys the question's hash


def _receipt_key(core: Core) -> bytes:
    """This install's key for hashing questions, made the first time one is
    needed and kept with the other secrets. Never logged or returned."""
    key = core.secrets.get(RECEIPT_KEY)
    if not key:
        core.secrets.set(RECEIPT_KEY, secrets.token_hex(32))
        # Read back: if the other process made one at the same moment, the two
        # must agree on whichever was written last.
        key = core.secrets.get(RECEIPT_KEY) or ""
    return key.encode("ascii")


def query_hash(core: Core, query: str) -> str:
    """A keyed hash of a question: the same question gives the same hash on
    this install, and nobody without the key can test a guess against it."""
    return hmac.new(_receipt_key(core), query.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Passage:
    id: str  # the fact id, or "c:" and the start of the passage's hash
    text: str
    note: str
    heading: str
    fact_id: str | None
    score: float
    why: str  # "keyword", "meaning" or "both"
    confirmed: bool | None  # None for free text

    @property
    def chars(self) -> int:
        return len(self.text)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "note": self.note, "heading": self.heading,
                "fact_id": self.fact_id, "score": self.score, "why": self.why,
                "confirmed": self.confirmed, "chars": self.chars}


def _block(number: int, passage: Passage) -> str:
    where = f"{passage.note} > {passage.heading}" if passage.heading else passage.note
    return f"[{number}] ({where})\n{passage.text}"


@dataclass(frozen=True)
class Recall:
    """What one retrieval found. ``render()`` is the only text to put in a prompt."""

    passages: tuple[Passage, ...]
    trimmed: tuple[Passage, ...]  # ranked high enough, but did not fit the budget
    methods: tuple[str, ...]  # which of "keyword" and "meaning" ran
    meaning_note: str | None  # why meaning search did not run, in plain words
    embedding_model: str | None
    scope: tuple[str, ...]
    k: int
    budget_chars: int
    receipt_id: str | None = None

    def render(self) -> str:
        """The numbered block for a prompt. Empty when nothing was found."""
        return "\n\n".join(_block(n, p) for n, p in enumerate(self.passages, 1))

    @property
    def shown_chars(self) -> int:
        return len(self.render())

    def as_dict(self) -> dict[str, Any]:
        return {
            "passages": [p.as_dict() for p in self.passages],
            "trimmed": [{"id": p.id, "note": p.note, "fact_id": p.fact_id, "chars": p.chars}
                        for p in self.trimmed],
            "methods": list(self.methods),
            "meaning": {"used": "meaning" in self.methods, "reason": self.meaning_note,
                        "model": self.embedding_model},
            "scope": list(self.scope), "k": self.k, "budget_chars": self.budget_chars,
            "shown_chars": self.shown_chars, "receipt_id": self.receipt_id,
        }


# --- vectors -----------------------------------------------------------------------


def embeddings_state(mem: Memory) -> dict[str, Any]:
    """Whether meaning search can work right now, and if not, why and what to do."""
    local_only = mem.core.settings.get("memory.embed_local_only")
    provider, target, reason = brain(mem.core).embeddings_target(local_only=local_only)
    if target is None or provider is None:
        if local_only and reason and "own machines" in reason:
            reason = ("Memory text is set to stay on your own machines, and the embeddings "
                      "model is on a hosted provider. Choose an embeddings model on a "
                      "provider marked local under Settings, Models, or turn off 'Keep meaning "
                      "search on my own machines' under Settings, Memory.")
        elif reason and reason.startswith("No embeddings model"):
            reason += " Until then, search matches words only."
        return {"model": None, "reason": reason, "local_only": local_only}
    return {"model": f"{provider.id}/{target.model}", "reason": None, "local_only": local_only}


def pending(mem: Memory, model: str, limit: int) -> list[Any]:
    return mem.read(
        "SELECT hash, heading, text FROM chunks c WHERE NOT EXISTS"
        " (SELECT 1 FROM vectors v WHERE v.hash = c.hash AND v.model = ?)"
        " GROUP BY hash ORDER BY MIN(id) LIMIT ?", (model, limit))


def _store(mem: Memory, model: str, hashes: list[str], vectors: list[list[float]]) -> None:
    rows = []
    for digest, vector in zip(hashes, vectors, strict=True):
        array = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(array))
        if norm > 0:
            array = array / norm  # unit length, so a dot product is the cosine
        rows.append((digest, model, int(array.shape[0]), array.astype(np.float32).tobytes()))
    # Under the Library's lock, like every other write: a long scan in the other
    # process is waited for, however long, instead of timing out on the database.
    with mem.exclusive():
        mem._db.execute("BEGIN IMMEDIATE")  # noqa: SLF001 - the Library's own connection
        try:
            mem._db.executemany(  # noqa: SLF001
                "INSERT OR REPLACE INTO vectors (hash, model, dim, vec) VALUES (?,?,?,?)", rows)
            mem._bump()  # noqa: SLF001
            mem._db.execute("COMMIT")  # noqa: SLF001
        except BaseException:
            mem._db.execute("ROLLBACK")  # noqa: SLF001
            raise


def embed_pending(mem: Memory, limit: int | None = None) -> dict[str, Any]:
    """Make vectors for passages that lack one from the current model. One
    bounded batch; the worker calls it again. Never raises for a provider's sake."""
    mem.ensure()
    state = embeddings_state(mem)
    model = state["model"]
    if model is None:
        return {"embedded": 0, "left": None, "model": None, "reason": state["reason"]}
    limit = limit or mem.core.settings.get("memory.embed_batch")
    rows = pending(mem, model, limit)
    if not rows:
        # Everything has a vector from this model: older models' vectors, and
        # those of passages that no longer exist, are no use to anyone.
        with mem.exclusive():
            cur = mem._db.execute(  # noqa: SLF001
                "DELETE FROM vectors WHERE model != ? OR hash NOT IN (SELECT hash FROM chunks)",
                (model,))
            if cur.rowcount:
                mem._bump()  # noqa: SLF001
        return {"embedded": 0, "left": 0, "model": model, "reason": None}
    failed = mem.meta("embed_failed")
    retry_ms = mem.core.settings.get("memory.embed_retry_s") * 1000
    if failed and failed.get("model") == model and now_ms() - failed["at_ms"] < retry_ms:
        return {"embedded": 0, "left": None, "model": model, "reason": failed["reason"]}
    texts = [(f"{r['heading']}\n{r['text']}" if r["heading"] else r["text"])[:MAX_EMBED_CHARS]
             for r in rows]
    try:
        reply = brain(mem.core).embed_sync(
            texts, actor="system", caller="memory.index", local_only=state["local_only"],
            no_fallback=True)
    except ModelError as exc:
        mem.set_meta("embed_failed", {"model": model, "at_ms": now_ms(), "reason": exc.public})
        log.warning("memory embedding failed: reason=%s", exc.kind)
        return {"embedded": 0, "left": None, "model": model, "reason": exc.public}
    _store(mem, reply.name, [r["hash"] for r in rows], reply.vectors)
    if failed:
        mem.set_meta("embed_failed", None)
    left = mem.read(
        "SELECT COUNT(DISTINCT hash) FROM chunks c WHERE NOT EXISTS"
        " (SELECT 1 FROM vectors v WHERE v.hash = c.hash AND v.model = ?)", (model,))[0][0]
    return {"embedded": len(rows), "left": left, "model": model, "reason": None}


_cache_lock = threading.Lock()


def _matrix(mem: Memory, model: str, dim: int):
    """Every current passage's vector for one model, as
    ``(ids, notes, note_of, matrix)``: the passage ids, the distinct note paths,
    which of those each passage is in, and one unit vector per row.

    Cached per process and thrown away whenever the index changes (the ``gen``
    counter in the database), so two processes never disagree for long. Rows
    are copied straight into one array, and the old array is let go first, so
    loading never holds more than about one copy of the vectors.
    """
    gen = mem.meta("gen", 0)
    with _cache_lock:
        cached = mem.vector_cache
        if cached and cached[:3] == (gen, model, dim):
            return cached[3:]
        mem.vector_cache = None
    del cached
    sql = (" FROM chunks c JOIN vectors v ON v.hash = c.hash AND v.model = ? AND v.dim = ?"
           " AND length(v.vec) = ?")
    params = (model, dim, dim * 4)
    with mem._rlock:  # noqa: SLF001 - one snapshot for the count and the rows
        db = mem._rdb  # noqa: SLF001
        db.execute("BEGIN")
        try:
            count = db.execute("SELECT COUNT(*)" + sql, params).fetchone()[0]  # noqa: S608
            ids = np.empty(count, dtype=np.int64)
            note_of = np.empty(count, dtype=np.int32)
            matrix = np.empty((count, dim), dtype=np.float32)
            notes: dict[str, int] = {}
            for row, (chunk_id, path, vec) in enumerate(
                    db.execute("SELECT c.id, c.path, v.vec" + sql, params)):  # noqa: S608
                ids[row] = chunk_id
                note_of[row] = notes.setdefault(path, len(notes))
                matrix[row] = np.frombuffer(vec, dtype=np.float32)
        finally:
            db.execute("COMMIT")
    found = (ids, list(notes), note_of, matrix)
    with _cache_lock:
        mem.vector_cache = (gen, model, dim, *found)
    return found


def _in_scope(path: str, scope: tuple[str, ...]) -> bool:
    return EVERYWHERE in scope or any(path.startswith(f"{folder}/") for folder in scope)


# --- recall ------------------------------------------------------------------------


@dataclass(frozen=True)
class _Ask:
    query: str
    actor: str
    caller: str
    scope: tuple[str, ...]
    k: int
    budget: int
    state: dict[str, Any]
    timeout: float
    private: bool = False
    related_max: int | None = None


def _prepare(mem: Memory, query: Any, actor: Any, caller: Any, scope: Any, k: Any,
             budget_chars: Any, private: Any = False, related_max: Any = None) -> _Ask:
    mem.ensure()
    if not isinstance(private, bool):
        raise LibraryError(422, "private must be true or false.")
    if related_max is not None and (isinstance(related_max, bool)
                                    or not isinstance(related_max, int)
                                    or not 0 <= related_max <= 50):
        raise LibraryError(422, "related_max must be a whole number from 0 to 50.")
    if not isinstance(query, str) or not storable(query):
        raise LibraryError(422, "The search must be text.")
    if len(query) > MAX_QUERY:
        raise LibraryError(422, f"The search cannot be longer than {MAX_QUERY} characters. "
                                "Ask about one thing at a time.")
    if not isinstance(actor, str) or not actor:
        raise LibraryError(422, "recall needs an actor, such as owner or agent:scout.")
    if not isinstance(caller, str) or not CALLER.match(caller) or len(caller) > 100:
        raise LibraryError(422, "recall needs a caller: a dotted lower-case name such as "
                                "chat.reply.")
    if isinstance(scope, str):
        scope = (scope,)
    if not isinstance(scope, Sequence) or not scope or len(scope) > 20:
        raise LibraryError(422, "scope must name one to twenty folders, such as shared or "
                                "agents/scout.")
    folders = tuple(dict.fromkeys(
        EVERYWHERE if s == EVERYWHERE else clean_folder(s) for s in scope))
    get = mem.core.settings.get
    if k is None:
        k = get("memory.recall_passages")
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 50:
        raise LibraryError(422, "k must be a whole number from 1 to 50.")
    if budget_chars is None:
        budget_chars = get("memory.recall_chars")
    if isinstance(budget_chars, bool) or not isinstance(budget_chars, int) \
            or not 1 <= budget_chars <= 1_000_000:
        raise LibraryError(422, "budget_chars must be a whole number from 1 to 1000000.")
    wants_meaning = get("memory.weight_meaning") > 0 and bool(md.words(query))
    state = (embeddings_state(mem) if wants_meaning
             else {"model": None, "reason": None, "local_only": True})
    return _Ask(query, actor, caller, folders, k, budget_chars, state,
                get("memory.embed_timeout_s"), private, related_max)


def _keyword(mem: Memory, ask: _Ask) -> list[int]:
    terms = list(dict.fromkeys(md.words(ask.query)))
    if not terms:
        return []
    ignored = set(mem.core.settings.get("memory.ignore_words"))
    # A search made only of small words ("who") still looks for them.
    terms = ([t for t in terms if t not in ignored] or terms)[:MAX_TERMS]
    where, params = "", []
    if EVERYWHERE not in ask.scope:
        parts = []
        for folder in ask.scope:
            # A range, not LIKE: LIKE ignores the case of ASCII letters, and
            # agents/Scout is not agents/scout. "0" is the character after "/".
            parts.append("(c.path >= ? AND c.path < ?)")
            params += [f"{folder}/", f"{folder}0"]
        where = f" AND ({' OR '.join(parts)})"
    match = " OR ".join(f'"{t}"' for t in terms)
    rows = mem.read(
        "SELECT c.id, bm25(chunks_fts, 2.0, 1.0) AS rank FROM chunks_fts"  # noqa: S608
        f" JOIN chunks c ON c.id = chunks_fts.rowid WHERE chunks_fts MATCH ?{where}"
        " ORDER BY rank, c.id LIMIT ?",
        (match, *params, CANDIDATES))
    if not rows:
        return []
    # bm25 is negative, and more negative is better. A passage that matched
    # only on words found everywhere scores a small fraction of the best one.
    floor = rows[0]["rank"] * mem.core.settings.get("memory.keyword_floor")
    return [r["id"] for r in rows if r["rank"] <= floor]


def _meaning(mem: Memory, ask: _Ask, model: str, vector: list[float]
             ) -> tuple[list[int], bool]:
    """Passage ids by similarity, and whether any passage had a vector to compare."""
    query = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(query))
    ids, notes, note_of, matrix = _matrix(mem, model, int(query.shape[0]))
    if norm == 0 or matrix.shape[0] == 0:
        return [], matrix.shape[0] > 0
    scores = matrix @ (query / norm)
    keep = np.ones(scores.shape[0], dtype=bool)
    if EVERYWHERE not in ask.scope:
        # Decided once per note, not once per passage.
        allowed = np.fromiter((_in_scope(n, ask.scope) for n in notes), dtype=bool,
                              count=len(notes))
        keep &= allowed[note_of]
    if not keep.any():
        return [], True
    # Every passage is some distance from the question, so "nearest" alone
    # means nothing in a small Library. Keep only passages about as close as
    # the closest one, and never below the absolute floor.
    best = float(scores[keep].max())
    floor = max(mem.core.settings.get("memory.min_similarity"),
                best - mem.core.settings.get("memory.related_margin"))
    keep &= scores >= floor
    chosen = np.flatnonzero(keep)
    order = chosen[np.argsort(-scores[chosen], kind="stable")][:CANDIDATES]
    return [int(ids[i]) for i in order], True


def _finish(mem: Memory, ask: _Ask, vector: list[float] | None, model: str | None,
            note: str | None) -> Recall:
    get = mem.core.settings.get
    w_key, w_mean = get("memory.weight_keyword"), get("memory.weight_meaning")
    methods: list[str] = []
    by_key: list[int] = []
    by_meaning: list[int] = []
    if w_key > 0 and md.words(ask.query):
        by_key = _keyword(mem, ask)
        methods.append("keyword")
    if vector is not None and model is not None:
        by_meaning, any_vectors = _meaning(mem, ask, model, vector)
        if any_vectors:
            methods.append("meaning")
        else:
            note = ("Passages are still being prepared for meaning search, so this search "
                    "matched words only. It will improve on its own shortly.")
    scores: dict[int, float] = {}
    for weight, ranking in ((w_key, by_key), (w_mean, by_meaning)):
        for rank, chunk_id in enumerate(ranking, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (RRF_K + rank)
    ranked: list[Passage] = []
    if scores:
        marks = ",".join("?" * len(scores))
        rows = mem.read(
            "SELECT c.id, c.path, c.fact_id, c.heading, c.text, c.hash, n.modified_ms,"  # noqa: S608
            " f.confirmed, f.updated_ms FROM chunks c LEFT JOIN notes n ON n.path = c.path"
            " LEFT JOIN facts f ON f.id = c.fact_id AND f.retired_ms IS NULL"
            f" WHERE c.id IN ({marks})", tuple(scores))
        now = now_ms()
        boost_confirmed = get("memory.confirmed_boost")
        boost_recent = get("memory.recency_boost")
        half_life = get("memory.recency_half_life_days") * DAY_MS
        in_key, in_meaning = set(by_key), set(by_meaning)
        scored = []
        for row in rows:
            score = scores[row["id"]]
            if row["confirmed"]:
                score *= 1 + boost_confirmed
            changed = row["updated_ms"] or row["modified_ms"] or 0
            score *= 1 + boost_recent * 0.5 ** (max(0, now - changed) / half_life)
            both = row["id"] in in_key and row["id"] in in_meaning
            why = "both" if both else "keyword" if row["id"] in in_key else "meaning"
            scored.append((-score, row["id"], Passage(
                id=row["fact_id"] or f"c:{row['hash'][:16]}", text=row["text"],
                note=row["path"], heading=row["heading"], fact_id=row["fact_id"],
                score=round(score, 6), why=why,
                confirmed=None if row["fact_id"] is None else bool(row["confirmed"]))))
        ranked = [p for _, _, p in sorted(scored, key=lambda item: item[:2])]
        if ask.related_max is not None:
            # For a person reading results: what matched the words first, then
            # a few passages related only in meaning, never mixed in among them.
            matches = [p for p in ranked if p.why != "meaning"]
            related = [p for p in ranked if p.why == "meaning"][:ask.related_max]
            ranked = matches + related
    admitted: list[Passage] = []
    trimmed: list[Passage] = []
    used = 0
    for passage in ranked:
        if len(admitted) >= ask.k:
            break
        if any(passage.text == kept.text or md.near_duplicate(passage.text, kept.text)
               for kept in admitted):
            continue  # says what an admitted passage already says
        cost = len(_block(len(admitted) + 1, passage)) + (2 if admitted else 0)
        if used + cost > ask.budget:
            trimmed.append(passage)  # never cut: left out whole, and listed
            continue
        admitted.append(passage)
        used += cost
    recall = Recall(tuple(admitted), tuple(trimmed), tuple(methods), note,
                    model if "meaning" in methods else None, ask.scope, ask.k, ask.budget)
    event = mem.core.tape.print("memory.recalled", {
        # A private question leaves no trace of itself: not its length, and
        # not a hash that would show the same thing being asked twice.
        "caller": ask.caller, "query_chars": 0 if ask.private else len(ask.query),
        "query_hash": "" if ask.private else query_hash(mem.core, ask.query),
        "scope": list(ask.scope),
        "passages": [{"id": p.id, "note": p.note, "chars": p.chars, "why": p.why}
                     for p in recall.passages],
        "trimmed": len(recall.trimmed), "methods": list(recall.methods),
        "shown_chars": recall.shown_chars, "k": ask.k, "budget_chars": ask.budget,
        "embedding_model": recall.embedding_model,
    }, actor=ask.actor)
    return Recall(recall.passages, recall.trimmed, recall.methods, recall.meaning_note,
                  recall.embedding_model, recall.scope, recall.k, recall.budget_chars, event.id)


async def arecall(mem: Memory, query: Any, *, actor: Any, caller: Any,
                  scope: Any = ("shared",), k: Any = None, budget_chars: Any = None,
                  private: Any = False, related_max: Any = None) -> Recall:
    """With ``private``, the receipt says what was handed over and nothing
    about the question: an empty hash, a length of 0, and no cause, root or
    subject tying it to a conversation.

    With ``related_max`` (the search screen), matches are listed before
    passages related only in meaning, and at most that many of those follow.
    Without it (a prompt), the fused order stands and only ``k`` limits them."""
    ask = await asyncio.to_thread(_prepare, mem, query, actor, caller, scope, k, budget_chars,
                                  private, related_max)
    vector: list[float] | None = None
    model: str | None = None
    note: str | None = ask.state["reason"]
    if ask.state["model"] is not None:
        try:
            reply = await asyncio.wait_for(
                brain(mem.core).embed([ask.query[:MAX_EMBED_CHARS]], actor=ask.actor,
                                      caller="memory.recall",
                                      local_only=ask.state["local_only"], no_fallback=True),
                ask.timeout)
            vector, model = reply.vectors[0], reply.name
        except TimeoutError:
            note = ("The embeddings model took too long to answer, so this search matched "
                    "words only.")
        except ModelError as exc:
            note = f"{exc.public} This search matched words only."
        except Exception as exc:  # noqa: BLE001 - meaning search is optional, always
            log.warning("memory recall could not embed the question: %s", type(exc).__name__)
            note = ("The embeddings model could not be used because of an unexpected error, "
                    "so this search matched words only. The server log names the error.")
    return await asyncio.to_thread(_finish, mem, ask, vector, model, note)


def recall_sync(mem: Memory, query: Any, **options: Any) -> Recall:
    """``arecall`` from code that is not async. Safe to call anywhere: inside a
    running event loop the work is done on a helper thread (the caller still
    waits, so async code should prefer ``arecall``)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(arecall(mem, query, **options))
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = asyncio.run(arecall(mem, query, **options))
        except BaseException as exc:  # noqa: BLE001 - handed to the caller below
            box["error"] = exc

    thread = threading.Thread(target=run, name="memory-recall")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


def core_memory(core: Core) -> Memory:
    return core.service("memory", Memory)
