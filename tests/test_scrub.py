"""The shared scrubber: what is taken out of a text as a secret, and what is left whole."""

from __future__ import annotations

from jimmyhq import scrub
from jimmyhq.village.tools import logged

KEY = "sk-test-SECRET-0123456789"
SHAPES = [
    KEY,
    "ghp_" + "a1B2" * 6,
    "AKIA" + "ABCDEFGH12345678",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl",
    "Bearer abcdefghijklmnop1234",
    "-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----",
]
KEPT = [
    "an ordinary sentence about lunch",
    "notes/2024/plan-for-the-week.md",
    "https://example.com/a/long/path/to/a/page.html?page=2",
    "01HZX3KQ9T8Y7W6V5S4R3Q2P1N",  # a Bench card id
    "9f2c1e5a7b3d4c6e",  # one of our own short fingerprints
]


def test_every_known_shape_of_a_key_is_taken_out_of_a_text():
    for secret in SHAPES:
        out = scrub.text(f"the value {secret} was in the page")
        assert secret not in out and scrub.REMOVED in out, secret
        assert scrub.looks_secret(secret)


def test_a_secret_someone_wrote_out_loses_its_value_and_keeps_its_words():
    assert scrub.text("the password is hunter2 for now") == "the password is [removed] for now"
    assert scrub.text("api key: abc123") == "api key: [removed]"
    assert scrub.text("token=abc123") == "token=[removed]"


def test_a_card_number_goes_and_a_plain_long_number_stays():
    assert scrub.text("card 4242 4242 4242 4242 thanks") == "card [removed] thanks"
    assert scrub.text("order 1234567890123456") == "order 1234567890123456"


def test_paths_addresses_ids_and_sentences_are_left_whole():
    for keep in KEPT:
        assert scrub.text(keep) == keep and not scrub.looks_secret(keep), keep


def test_has_key_shape_is_narrower_than_looks_secret():
    """A long mixed run looks secret, but only a known prefix or block is a key's shape."""
    run = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4"
    assert scrub.looks_secret(run) and not scrub.has_key_shape(run)
    assert all(scrub.has_key_shape(secret) for secret in SHAPES)
    assert not any(scrub.has_key_shape(keep) for keep in KEPT)


def test_the_village_log_still_scrubs_with_the_very_same_functions():
    assert logged.text is scrub.text and logged.looks_secret is scrub.looks_secret
    assert logged.REMOVED == scrub.REMOVED
    assert logged.args({"note": f"token = {KEY}", "password": "hunter2"}) == {
        "note": "token = [removed]", "password": "[removed]"}
    assert KEY not in logged.result(f"wrote {KEY}")

