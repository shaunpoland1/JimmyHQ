"""The separate checker's points, held to what the code can verify, and a warning when
the checker runs on the same model role as the writer."""

from __future__ import annotations

from typing import Any

from posts.checks import Draft, Material, Result
from posts.checks.quotes import normalise


def usable_point(point: dict[str, Any], body: str, sentences: int) -> dict[str, Any] | None:
    """Rule ``a_point_quotes_and_cites``: a point quotes the draft word for word, says
    what is wrong, and names a real source sentence or says that none supports it
    (``"none": true``). Anything else is None."""
    quote = normalise(str(point.get("quote") or ""))
    problem = str(point.get("problem") or "").strip()
    cited = [n for n in point.get("sentences") or []
             if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= sentences]
    unsupported = point.get("none") is True and not point.get("sentences")
    if not quote or quote not in normalise(body) or not problem:
        return None
    if not cited and not unsupported:
        return None
    out = {"quote": str(point["quote"]), "sentences": cited, "problem": problem[:400]}
    if unsupported:
        out["none"] = True
    return out


def usable_points(points: list[dict[str, Any]], body: str, sentences: int
                  ) -> tuple[list[dict[str, Any]], int]:
    """The points that hold to ``usable_point``, and how many were set aside."""
    keep, dropped = [], 0
    for point in points:
        found = usable_point(point, body, sentences)
        if found is None:
            dropped += 1
        else:
            keep.append(found)
    return keep, dropped


def checker_points(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    if material.checker_points is None:
        return []
    points, dropped = usable_points(material.checker_points, draft.body,
                                    len(material.sentences))
    out = [Result("checker", "warn", f"On “{p['quote']}”: {p['problem']}"
                  + (" The checker found no source sentence that supports it." if p.get("none")
                     else ""), p["sentences"], normalise(p["quote"])[:80]) for p in points]
    if dropped:
        out.append(Result("checker", "ok", f"{dropped} of the checker's points were set aside: "
                          "they did not quote the draft and name a source sentence, or say "
                          "none supports it.", [], "dropped"))
    if not out:
        out.append(Result("checker", "ok", "The checker found nothing to raise.", [], "ok"))
    return out


def same_role(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    writer, checker = material.roles
    if writer and writer == checker:
        return [Result("checker", "warn", f"The draft was checked by the same model role "
                       f"(“{writer}”) that wrote it, so it may share its blind spots. "
                       "Choose another under Posts' settings.", [], "same_role")]
    return []
