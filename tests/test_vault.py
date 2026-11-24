from __future__ import annotations

import threading

import pytest

from jimmyhq.tape import Vault


def test_the_same_content_always_gets_the_same_reference(tmp_path):
    vault = Vault(tmp_path)
    ref = vault.put_text("hello")
    assert ref == vault.put_text("hello")
    assert ref.startswith("sha256:")
    assert vault.get_text(ref) == "hello"
    assert vault.has(ref)


def test_a_reference_that_is_not_a_vault_reference_is_refused(tmp_path):
    vault = Vault(tmp_path)
    assert not vault.has("../../etc/passwd")
    with pytest.raises(ValueError, match="not a vault reference"):
        vault.get("../../etc/passwd")


def test_many_writers_storing_identical_content_at_once_all_succeed(tmp_path):
    vault = Vault(tmp_path)
    barrier = threading.Barrier(8)
    errors = []

    def write():
        barrier.wait()
        try:
            vault.put(b"same bytes")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("ref", ["sha256:", "sha256:../secret.txt", "sha256:xx/abs/path",
                                 "sha256:" + "g" * 64, "sha256:" + "A" * 64, None, 7])
def test_only_a_plain_sha256_digest_is_accepted_as_a_reference(tmp_path, ref):
    (tmp_path / "secret.txt").write_text("private")
    vault = Vault(tmp_path / "v")
    assert vault.has(ref) is False
    with pytest.raises(ValueError, match="not a vault reference"):
        vault.get(ref)


def test_an_absolute_path_after_the_prefix_cannot_reach_outside_the_vault(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    vault = Vault(tmp_path / "v")
    with pytest.raises(ValueError, match="not a vault reference"):
        vault.get("sha256:xx" + str(secret))
