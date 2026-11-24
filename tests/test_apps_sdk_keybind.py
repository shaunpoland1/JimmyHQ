"""A key typed for one address on the owner's own network goes to no other, and what is kept
to know that is a fingerprint, never the key."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "sdk" / "python") not in sys.path:
    sys.path.insert(0, str(ROOT / "sdk" / "python"))

from jimmyhq_app import App  # noqa: E402
from jimmyhq_app.keybind import KeyBinding  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

A, B = "http://192.0.2.10:3001", "http://192.0.2.11:3001"
KEY = "sk-test-SECRET-0123456789"


def binding(tmp_path):
    core = FakeCore("house", data_dir=tmp_path)
    with core.installed():
        return KeyBinding(App(), "server"), core


def test_a_key_is_bound_to_the_address_it_is_first_sent_to_and_to_no_other(tmp_path):
    bound, _ = binding(tmp_path)
    assert bound.allows(KEY, A) and bound.allows(KEY, A)
    assert not bound.allows(KEY, B) and bound.bound_to() == A
    assert bound.allows(KEY, A)  # still bound to the first


def test_the_address_is_compared_without_regard_to_case_or_spaces(tmp_path):
    bound, _ = binding(tmp_path)
    assert bound.allows(KEY, "http://Server.example:80")
    assert bound.allows(KEY, " http://server.example:80 ")


def test_a_different_key_is_bound_afresh_and_forgetting_lets_the_same_one_move(tmp_path):
    bound, _ = binding(tmp_path)
    bound.allows(KEY, A)
    assert bound.allows("another-key-1", B) and bound.bound_to() == B
    assert bound.allows(KEY, A) and bound.bound_to() == A  # a key not on record binds afresh
    bound.forget()
    assert bound.bound_to() is None and bound.allows(KEY, B)


def test_two_bindings_of_one_app_do_not_touch_each_other(tmp_path):
    core = FakeCore("house", data_dir=tmp_path)
    with core.installed():
        app = App()
        one, two = KeyBinding(app, "one"), KeyBinding(app, "two")
    assert one.allows(KEY, A) and two.allows(KEY, B)
    assert not one.allows(KEY, B) and not two.allows(KEY, A)


def test_what_is_kept_holds_no_key(tmp_path):
    bound, _ = binding(tmp_path)
    bound.allows(KEY, A)
    kept = b"".join(p.read_bytes() for p in tmp_path.glob("keybind.db*"))
    assert kept and KEY.encode() not in kept and A.encode() in kept
