"""Note history that can really be deleted: purging, forgetting everywhere, retention."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jimmyhq import memory
from jimmyhq.core import open_core
from jimmyhq.memory.errors import LibraryError
from jimmyhq.web.app import create_app
from test_memory_fakes import found_in, tape_text

PASSWORD = "correct horse battery"
API = "/api/memory"
NOTE = "shared/journal.md"
DAY = 86_400_000


@pytest.fixture
def lib(core):
    memory.status(core)
    return core.config.data


def events(core, kind):
    return [e for e in core.tape.after(0, limit=100_000) if e.kind == kind]


def write(core, *texts, path=NOTE):
    for text in texts:
        memory.save_note(core, path, text, actor="owner")


def versions(core, path=NOTE):
    return memory.library(core).history(path)


def test_versions_of_a_note_are_kept_by_memory_and_not_in_the_shared_vault(core, lib):
    write(core, "# Journal\n\nThe heron came back to the pond.\n",
          "# Journal\n\nThe heron left again.\n")
    newest, oldest = versions(core)
    assert newest["available"] and oldest["available"]
    assert memory.library(core).version(NOTE, oldest["version"])["text"].endswith(
        "came back to the pond.\n")
    assert not [p for p in (lib / "vault").rglob("*") if p.is_file()]
    assert any(f.startswith("memory_store.db") for f in found_in(lib, "heron came back"))
    # The event still carries the reference and the size, never the text.
    saved = events(core, "memory.note.saved")[-2].payload
    assert saved["ref"].startswith("sha256:") and saved["size"] == 44
    assert "heron" not in tape_text(core)


def test_identical_versions_share_one_body_which_goes_with_the_last_of_them(core, lib):
    first, other = "# Journal\n\nPlum jam.\n", "# Journal\n\nQuince jam.\n"
    write(core, first, other, first)
    store = memory.library(core).store
    count = "SELECT COUNT(*) AS n FROM bodies b JOIN versions v ON v.hash = b.hash WHERE path = ?"
    assert store.one("SELECT COUNT(DISTINCT b.hash) AS n" + count[20:], (NOTE,))["n"] == 2
    newest, middle, oldest = versions(core)
    assert store.purge([oldest["version"]]) == {"versions": 1, "bodies": 0}
    assert memory.library(core).version(NOTE, newest["version"])["text"] == first
    assert store.purge([newest["version"]]) == {"versions": 1, "bodies": 1}
    assert [v["available"] for v in versions(core)] == [False, True, False]


def test_a_version_whose_text_is_gone_says_it_is_no_longer_stored(core, lib):
    write(core, "# Journal\n\nOne.\n", "# Journal\n\nTwo.\n")
    oldest = versions(core)[-1]
    memory.library(core).store.purge([oldest["version"]])
    with pytest.raises(LibraryError) as refused:
        memory.library(core).version(NOTE, oldest["version"])
    assert refused.value.status == 410 and "no longer stored" in refused.value.detail
    assert versions(core)[-1]["available"] is False


def test_deleting_a_note_with_its_history_leaves_its_text_in_no_file(core, lib):
    write(core, "# Journal\n\nThe zeppelin landed in the orchard.\n\n- Saw a kingfisher today\n",
          "# Journal\n\nThe zeppelin left the orchard at dusk.\n\n- Saw a kingfisher today\n",
          "# Journal\n\nNothing happened.\n")
    assert found_in(lib, "zeppelin") and found_in(lib, "kingfisher")
    assert memory.delete_note(core, NOTE, actor="owner", history=True) == {
        "versions": 3, "bodies": 3}
    for needle in ("zeppelin", "orchard at dusk", "kingfisher", "Nothing happened"):
        assert found_in(lib, needle) == [], needle
    (purged,) = events(core, "memory.history.purged")
    assert purged.payload == {"why": "note_deleted", "versions": 3, "bodies": 3, "notes": 1}
    assert all(v["available"] is False for v in versions(core))
    assert memory.list_facts(core)["facts"] == []


def test_deleting_a_note_without_its_history_keeps_what_it_said(core, lib):
    write(core, "# Journal\n\nThe zeppelin landed.\n")
    assert memory.delete_note(core, NOTE, actor="owner") is None
    (only,) = versions(core)
    assert "zeppelin" in memory.library(core).version(NOTE, only["version"])["text"]
    assert events(core, "memory.history.purged") == []
    # The history of a note that is already gone can still be deleted later.
    assert memory.delete_note(core, NOTE, actor="owner", history=True)["versions"] == 1
    assert found_in(lib, "zeppelin") == []
    with pytest.raises(LibraryError) as refused:
        memory.delete_note(core, NOTE, actor="owner", history=True)
    assert refused.value.status == 404


def test_purging_a_renamed_note_takes_its_earlier_name_and_spares_a_newer_note_there(core, lib):
    write(core, "# Journal\n\nThe marmalade recipe.\n")
    memory.move_note(core, NOTE, "shared/kitchen.md", actor="owner")
    write(core, "# Kitchen\n\nThe marmalade recipe, improved.\n", path="shared/kitchen.md")
    write(core, "# Journal\n\nA different note about gooseberries.\n")  # the old name, reused
    assert memory.delete_note(core, "shared/kitchen.md", actor="owner", history=True)[
        "versions"] == 3
    assert found_in(lib, "marmalade") == []
    kept, earlier = versions(core)  # the old name's own history: the new note, then the old
    assert "gooseberries" in memory.library(core).version(NOTE, kept["version"])["text"]
    assert kept["available"] and not earlier["available"]


def test_forgetting_a_fact_everywhere_leaves_its_words_in_no_file(core, lib):
    secret = "Keeps the xylophone in the greenhouse"
    kept = memory.remember(core, "Waters the ferns on Sunday", kind="about", actor="owner")
    fact = memory.remember(core, secret, kind="about", actor="owner", confirmed=True)
    memory.remember(core, "Feeds the hens at seven", kind="about", actor="owner")
    memory.edit_fact(core, kept.id, actor="owner", text="Waters the ferns on Monday")
    memory.recall(core, "xylophone", actor="owner", caller="memory.search")
    assert found_in(lib, "xylophone")
    done = memory.forget_everywhere(core, fact.id, actor="owner", reason="Not for keeping.")
    assert done["fact"].retired_ms and done["fact"].text == ""
    assert done["purged"]["versions"] >= 3 and done["purged"]["notes"] == 1
    for needle in (secret, "xylophone", "greenhouse", "Xylophone"):
        assert found_in(lib, needle) == [], needle
    # The Tape knows the fact existed (its id), never what it said.
    assert "xylophone" not in tape_text(core) and found_in(lib, fact.id)
    (purged,) = events(core, "memory.history.purged")
    assert purged.payload["why"] == "forgotten_everywhere" and purged.subject == (
        f"memory_fact:{fact.id}")
    assert set(purged.payload) == {"why", "versions", "bodies", "notes"}
    # What was not forgotten is still there, and so is the note.
    about = memory.read_note(core, "shared/about-me.md")["text"]
    assert "ferns on Monday" in about and "hens at seven" in about and "xylophone" not in about
    assert memory.recall(core, "xylophone", actor="owner", caller="memory.search").passages == ()
    assert [p.text for p in memory.recall(core, "hens", actor="owner",
                                          caller="memory.search").passages]


def test_forgetting_everywhere_works_on_a_fact_already_forgotten_and_survives_a_restart(
        core, cfg, lib):
    fact = memory.remember(core, "The safe is behind the tapestry", kind="about",
                           actor="owner")
    memory.forget(core, fact.id, actor="owner")
    assert found_in(lib, "tapestry")  # an ordinary forget keeps history
    memory.forget_everywhere(core, fact.id, actor="owner")
    assert found_in(lib, "tapestry") == []
    again = open_core(cfg)
    try:
        memory.scan(again)
        memory.reindex(again)
        assert found_in(lib, "tapestry") == []
        assert memory.get_fact(again, fact.id, live_only=False).text == ""
    finally:
        again.close()


def test_retention_keeps_at_most_n_versions_per_note_and_nothing_older_than_d_days(core, lib):
    write(core, *(f"# Journal\n\nEntry number {n} about the lighthouse.\n" for n in range(6)))
    write(core, "# Other\n\nOne version only.\n", path="shared/other.md")
    assert memory.prune_history(core, force=True) == {"versions": 0, "bodies": 0}  # 0 keeps all
    assert events(core, "memory.history.purged") == []
    core.settings.set("memory.history_keep_versions", 2, actor="owner")
    assert memory.prune_history(core, force=True) == {"versions": 4, "bodies": 4}
    assert [v["available"] for v in versions(core)] == [True, True, False, False, False, False]
    assert found_in(lib, "Entry number 3") == [] and found_in(lib, "Entry number 5")
    assert versions(core, "shared/other.md")[0]["available"]
    (purged,) = events(core, "memory.history.purged")
    assert purged.payload == {"why": "retention", "versions": 4, "bodies": 4, "notes": 0}
    # Age: everything saved more than three days ago goes, even a note's only version.
    store = memory.library(core).store
    with store.tx() as db:
        db.execute("UPDATE versions SET ts = ts - ? WHERE path = 'shared/other.md'", (5 * DAY,))
    core.settings.set("memory.history_keep_days", 3, actor="owner")
    assert memory.prune_history(core, force=True) == {"versions": 1, "bodies": 1}
    # The note itself and its index entry stay; the kept copy of its text is gone.
    assert "memory/shared/other.md" in found_in(lib, "One version only")
    assert not [f for f in found_in(lib, "One version only") if f.startswith("memory_store")]
    assert versions(core, "shared/other.md")[0]["available"] is False
    assert versions(core)[0]["available"]


def test_the_worker_prunes_at_most_once_an_hour(core, lib):
    write(core, "# Journal\n\nOne.\n", "# Journal\n\nTwo.\n")
    core.settings.set("memory.history_keep_versions", 1, actor="owner")
    assert memory.prune_history(core)["versions"] == 1
    write(core, "# Journal\n\nThree.\n")
    assert memory.prune_history(core) is None  # done a moment ago
    assert len([v for v in versions(core) if v["available"]]) == 2


# --- over HTTP -----------------------------------------------------------------------


@pytest.fixture
def owner(core):
    with TestClient(create_app(core), base_url="http://testserver",
                    raise_server_exceptions=False) as client:
        assert client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}
                           ).status_code == 200
        yield client


def token_client(owner, core, *scopes):
    headers = {"authorization": f"Bearer {core.auth.create_token('helper', list(scopes))[1]}"}
    return TestClient(owner.app, base_url="http://testserver", headers=headers,
                      raise_server_exceptions=False)


def test_the_owner_deletes_a_note_with_its_history_over_http(owner, core):
    data = core.config.data
    owner.put(f"{API}/notes/{NOTE}", json={"text": "# Journal\n\nThe otter story.\n"})
    owner.put(f"{API}/notes/{NOTE}", json={"text": "# Journal\n\nThe otter story, longer.\n"})
    with token_client(owner, core, "read", "write") as helper:
        assert helper.delete(f"{API}/notes/{NOTE}?history=true").status_code == 403
    assert owner.delete(f"{API}/notes/{NOTE}?history=maybe").status_code == 422
    done = owner.delete(f"{API}/notes/{NOTE}?history=true")
    assert done.status_code == 200
    assert done.json() == {"ok": True, "purged": {"versions": 2, "bodies": 2}}
    assert found_in(data, "otter") == []
    history = owner.get(f"{API}/notes/{NOTE}/history").json()["versions"]
    assert [v["available"] for v in history] == [False, False]
    gone = owner.get(f"{API}/notes/{NOTE}/history/{history[0]['version']}")
    assert gone.status_code == 410 and "no longer stored" in gone.json()["detail"]
    assert owner.delete(f"{API}/notes/{NOTE}?history=true").status_code == 404
    # Without the flag the answer is what it always was.
    owner.put(f"{API}/notes/{NOTE}", json={"text": "# Journal\n"})
    assert owner.delete(f"{API}/notes/{NOTE}").json() == {"ok": True}


def test_the_owner_forgets_a_fact_everywhere_over_http(owner, core):
    data = core.config.data
    made = owner.post(f"{API}/facts", json={"text": "Hides the trombone in the attic",
                                           "kind": "about"}).json()["fact"]
    with token_client(owner, core, "read", "write") as helper:
        assert helper.delete(f"{API}/facts/{made['id']}?everywhere=true").status_code == 403
    done = owner.request("DELETE", f"{API}/facts/{made['id']}?everywhere=true",
                         json={"reason": "Private."})
    assert done.status_code == 200
    body = done.json()
    assert body["fact"]["text"] == "" and body["fact"]["retired_reason"] == "Private."
    assert set(body["purged"]) == {"versions", "bodies", "notes"}
    assert found_in(data, "trombone") == []
    assert owner.delete(f"{API}/facts/01JABCDEFGHJKMNPQRSTVWXYZ0?everywhere=true"
                        ).status_code == 404
    # Doing it twice is harmless.
    assert owner.delete(f"{API}/facts/{made['id']}?everywhere=true").status_code == 200


def test_only_the_owner_is_told_where_the_library_is_on_disk(owner, core):
    assert owner.get(f"{API}/status").json()["library_path"] == str(core.config.data / "memory")
    for scopes in (("read",), ("read", "write")):
        with token_client(owner, core, *scopes) as helper:
            status = helper.get(f"{API}/status")
            assert status.status_code == 200 and status.json()["library_path"] is None
            assert str(core.config.data) not in status.text
            assert status.json()["counts"]["notes"] == 6
