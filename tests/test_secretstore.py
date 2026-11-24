from __future__ import annotations

import threading

from jimmyhq.secretstore import SecretStore


def test_two_stores_on_one_file_changing_different_secrets_lose_nothing(tmp_path):
    """The web and worker processes each hold their own store on the same file."""
    path = tmp_path / "secrets.json"
    stores = [SecretStore(path), SecretStore(path)]
    errors = []

    def write(store, prefix):
        try:
            for i in range(40):
                store.set(f"{prefix}.{i}", "v")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    pairs = zip(stores, "ab", strict=True)
    threads = [threading.Thread(target=write, args=pair) for pair in pairs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(stores[0].names()) == 80
    assert not list(tmp_path.glob("*.tmp"))
