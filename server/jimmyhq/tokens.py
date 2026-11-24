"""API tokens as the owner sees them: list, make and revoke.

A token is for a script or a screen that reads JimmyHQ, or also makes changes, without
the owner's password. It is made with a label and a level: ``reader`` (scope ``read``)
or ``writer`` (``read`` and ``write``). No token is ever an owner: settings, credentials
and decisions stay with the signed-in owner. The token itself is given out once, when it
is made, and only its digest is kept (``jimmyhq.auth``). Revoking one stops it on its
very next request, an open live feed included.

The routes (``web.app``) and the ``jimmyhq owner token`` command both come here, so
both print the same events: ``token.created`` (the id, the label and the scopes, never
the token) and ``token.revoked``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jimmyhq.auth import TOKEN_LEVELS, AuthError

if TYPE_CHECKING:
    from jimmyhq.core import Core


def level_of(scopes: list[str]) -> str:
    """``writer`` for a token that can make changes, otherwise ``reader``."""
    return "writer" if "write" in scopes else "reader"


def _shown(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "label": row["name"], "level": level_of(row["scopes"]),
            "scopes": row["scopes"], "created": row["created"], "revoked": row["revoked"]}


def listed(core: Core) -> list[dict[str, Any]]:
    """Every token, oldest first, revoked ones included, never the token itself."""
    return [_shown(row) for row in core.auth.list_tokens()]


def create(core: Core, label: str, level: str, *, actor: str) -> tuple[dict[str, Any], str]:
    """Make a token. Returns (what the list shows for it, the token itself)."""
    scopes = TOKEN_LEVELS.get(level)
    if scopes is None:
        raise AuthError(f"A token is a reader or a writer, not {level!r}. Nothing was made.")
    token_id, secret = core.auth.create_token(label, list(scopes))
    core.tape.print("token.created",
                    {"token_id": token_id, "name": label.strip(), "scopes": list(scopes)},
                    actor=actor)
    made = next(t for t in listed(core) if t["id"] == token_id)
    return made, secret


def revoke(core: Core, token_id: str, *, actor: str) -> dict[str, Any]:
    """Stop a token. Revoking one that is already revoked changes nothing and is not an
    error. Raises ``LookupError`` with a sentence for an id that is not known."""
    found = next((t for t in listed(core) if t["id"] == token_id), None)
    if found is None:
        raise LookupError("There is no token with that id. Nothing was revoked. "
                          "List the tokens to see their ids.")
    if core.auth.revoke_token(token_id):
        core.tape.print("token.revoked", {"token_id": token_id}, actor=actor)
    return next(t for t in listed(core) if t["id"] == token_id)
