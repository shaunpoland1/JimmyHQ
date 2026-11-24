"""App tokens in ``Auth``: a fourth credential, kept apart from every other."""

from __future__ import annotations

import pytest

from jimmyhq.auth import APP_SCOPE, AuthError


def test_an_app_token_is_never_accepted_as_an_api_token(core):
    token = core.auth.mint_app_token("drafts")
    assert core.auth.check_token(token) is None
    assert core.auth.check_door_token(token) is None


def test_an_api_token_is_never_accepted_as_an_app_token(core):
    token = core.auth.create_token("automation", ["read", "write"])[1]
    assert core.auth.check_app_token(token) is None
    assert core.auth.check_app_token("jhqa_" + token[4:]) is None


def test_an_app_token_stands_for_its_app_and_for_nothing_an_owner_route_accepts(core):
    who = core.auth.check_app_token(core.auth.mint_app_token("drafts"))
    assert who.actor == "app:drafts" and who.scopes == frozenset({APP_SCOPE})
    assert not who.is_owner and not who.can("read") and not who.can("write")


def test_only_the_digest_of_an_app_token_is_kept(core):
    token = core.auth.mint_app_token("drafts")
    assert token.encode() not in core.config.auth_db.read_bytes()


def test_revoking_an_apps_tokens_stops_every_one(core):
    token = core.auth.mint_app_token("drafts")
    assert core.auth.revoke_app_tokens("drafts") == 1
    assert core.auth.check_app_token(token) is None


@pytest.mark.parametrize("app_id", ["", "Drafts", "a", "../x", "app:drafts", "x" * 40])
def test_a_token_is_minted_only_for_an_apps_id(core, app_id):
    with pytest.raises(AuthError):
        core.auth.mint_app_token(app_id)
