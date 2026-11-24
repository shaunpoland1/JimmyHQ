"""The owner's pictures against the pictures of the page's recent posts: a picture that
looks like one already posted is a warning, never a block. Measured first
(``pictures.likeness``) and handed in as ``Material.pictures``."""

from __future__ import annotations

from typing import Any

from posts.checks import Draft, Material, Result


def likeness(draft: Draft, material: Material, cfg: Any) -> list[Result]:
    if not material.pictures:
        return []
    out = []
    for picture in material.pictures:
        n = picture["n"]
        if picture["looks_like"]:
            out.append(Result("pictures", "warn", f"{picture['looks_like']} Make sure it is "
                              "not the same picture again.",
                              [f"post:{picture['post']}"] if picture["post"] else [],
                              f"like:{n}"))
        elif not picture["hashed"]:
            out.append(Result("pictures", "warn", f"Posts could not compare picture {n} with "
                              "the pictures of earlier posts: it compares only JPEG and PNG "
                              "pictures.", [], f"unhashed:{n}"))
    if not out:
        count = len(material.pictures)
        which = "The picture does not look" if count == 1 else \
            f"None of the {count} pictures looks"
        out.append(Result("pictures", "ok", f"{which} like one in the brand's posts of the "
                          f"last {material.like_days} days.", [], "ok"))
    return out
