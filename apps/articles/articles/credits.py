"""Who worked on an article: for each step, an agent (by role, and by the label the
platform gives), the plain way, or the owner. The page shows it as one line.

Nothing more about an agent is kept than the platform hands over: its label from
``roles.get`` or the ``work`` delivery, and the opaque id a tool delivery carries, which
is used only to keep a writer from checking its own article and is never shown.
"""

from __future__ import annotations

from typing import Any

from articles.store import Store

# The steps a line speaks of, in the order an article meets them, and how each is said.
STEPS = {"research": "Researched", "writing": "Written", "checking": "Checked"}
# The plain steps that count as each.
PLAIN_STEP = {"summarising": "research", "drafting": "writing", "repairing": "writing",
              "revising": "writing", "reviewing": "checking", "rechecking": "checking"}


def add(store: Store, piece_id: int, step: str, by: str, *, role: str = "",
        label: str | None = None, agent: str | None = None, note: str = "") -> int:
    """One step done by an agent, the plain way, or the owner."""
    return store.insert("credit", {"piece_id": piece_id, "step": step, "by": by, "role": role,
                                   "label": label, "agent": agent, "note": note[:200],
                                   "created": store.now()})


def of(store: Store, piece_id: int) -> list[dict[str, Any]]:
    return store.all("SELECT step, by, role, label, note, created FROM credit WHERE "
                     "piece_id=? ORDER BY id", (piece_id,))


def agents_on(store: Store, piece_id: int, step: str) -> set[str]:
    """The opaque ids of the agents that did ``step`` on this article."""
    return {r["agent"] for r in store.all("SELECT agent FROM credit WHERE piece_id=? AND "
                                          "step=? AND by='agent' AND agent IS NOT NULL",
                                          (piece_id, step))}


def any_agent(store: Store, piece_id: int) -> bool:
    return store.one("SELECT id FROM credit WHERE piece_id=? AND by='agent' LIMIT 1",
                     (piece_id,)) is not None


def _who(row: dict[str, Any]) -> str:
    if row["by"] == "owner":
        return "by you"
    if row["by"] == "plain":
        return "the plain way" + (f" ({row['note']})" if row["note"] else "")
    said = "; ".join(p for p in (row.get("label"), row.get("note")) if p)
    return f"by the {row['role'] or 'agent'}" + (f" ({said})" if said else "")


def line(store: Store, piece_id: int) -> str:
    """\"Researched by the researcher (Scout). Written by the writer (Wren), twice. Checked
    the plain way.\" Each step as it was done, with how many times when more than once."""
    rows = of(store, piece_id)
    parts = []
    for step, said in STEPS.items():
        mine = [r for r in rows if r["step"] == step]
        if not mine:
            continue
        ways = list(dict.fromkeys(_who(r) for r in mine))
        times = len(mine)
        count = "" if times == 1 else ", twice" if times == 2 else f", {times} times"
        parts.append(f"{said} {' then '.join(ways)}{count}.")
    return " ".join(parts) or "Nobody has worked on it yet."
