"""Sites as records, the state table, the settings and the manifest."""

from __future__ import annotations

import json
import re

import pytest
from articles import config, states
from articles.sites import guidance
from articles_rig import APP_ROOT

MANIFEST = json.loads((APP_ROOT / "jimmyhq.app.json").read_text(encoding="utf-8"))


def test_a_site_keeps_its_voice_and_shape_in_the_owners_words(rig):
    site = rig.site(register="Plain, warm, no jargon.", samples="We planted early.",
                    headings="a few, for longer pieces", closing="End with where to ask.")
    assert site["publisher"] == "practice"
    lines = guidance(site)
    assert lines == ["Length, in the owner's words: about 900 words",
                     "Headings, in the owner's words: a few, for longer pieces",
                     "A closing note, in the owner's words: End with where to ask."]
    changed = rig.core.call("sites.save", {**site, "length": "short"})
    assert changed["id"] == site["id"] and changed["length"] == "short"


@pytest.mark.parametrize("given, said", [
    ({"name": " "}, "Give the site a name"),
    ({"publisher": "carrier pigeon"}, "Choose where the site publishes"),
    ({"publisher": "wordpress"}, "needs one of your own addresses"),
    ({"wordpress_url": "http://blog.example.com"}, "https:// and its host only"),
    ({"wordpress_url": "https://blog.example.com/wp-admin"}, "https:// and its host only"),
])
def test_a_site_that_cannot_be_saved_says_why_and_saves_nothing(rig, given, said):
    with pytest.raises(Exception, match=said):
        rig.core.call("sites.save", {"name": "Garden notes", **given})
    assert rig.store.sites() == []


def test_two_sites_cannot_share_a_name(rig):
    rig.site()
    with pytest.raises(Exception, match="A site with that name exists"):
        rig.site()


def test_the_state_table_refuses_a_skipped_step_with_its_ways_on(rig):
    piece = rig.topic(rig.site())
    with pytest.raises(states.StateError, match="cannot be approved now") as said:
        rig.store.move(piece["id"], "approved")
    assert "Its ways on:" in str(said.value)
    assert rig.store.piece(piece["id"])["state"] == "coverage_check"


def test_every_state_has_its_words_and_its_way_out():
    assert set(states.WORDS) == set(states.PIECE) == set(states.WAYS_OUT)
    for old, new in states.PIECE.items():
        assert new <= set(states.PIECE), old
    finals = {s for s, new in states.PIECE.items() if not new}
    assert finals == {"published", "declined", "dropped"}


def test_dropping_withdraws_the_card_and_ends_the_article(rig):
    piece = rig.to_card()
    rig.core.call("piece.drop", {"id": piece["id"]})
    assert rig.store.piece(piece["id"])["state"] == "dropped"
    assert rig.cards_of("article") == []
    with pytest.raises(Exception, match="cannot be dropped"):
        rig.core.call("piece.drop", {"id": piece["id"]})


def test_every_setting_the_manifest_declares_is_read_through_config_and_used():
    code = "\n".join(p.read_text(encoding="utf-8") for p in (APP_ROOT / "articles").rglob("*.py"))
    for key in config.defaults():
        assert re.search(rf"\.{key}\b|every_setting=\"{key}\"", code), \
            f"{key} is declared and never read"
    assert config.label("number_tolerance_pct") == "Number rounding allowed (percent)"


def test_no_module_but_config_reads_the_settings_itself():
    for path in (APP_ROOT / "articles").rglob("*.py"):
        if path.name != "config.py":
            assert "settings.get(" not in path.read_text(encoding="utf-8"), path.name


def test_technical_settings_are_advanced_and_none_about_privacy_or_publishing_is():
    by_key = {s["key"]: s for s in MANIFEST["settings"]}
    for key in ("ideas_hosted", "number_tolerance_pct", "repair_passes", "min_sources",
                "weak_hosts", "advice_health"):
        assert not by_key[key].get("advanced"), key
    for key in ("results_per_search", "step_timeout_minutes", "material_chars",
                "same_story_share", "publish_timeout_s"):
        assert by_key[key].get("advanced"), key
    assert by_key["ideas_hosted"]["default"] is False


def test_the_manifest_asks_only_for_what_this_part_needs():
    assert MANIFEST["tier"] == "full" and not MANIFEST.get("private")
    assert MANIFEST["permissions"] == ["jobs", "bench.file", "model.ask", "web", "agents.tools"]
    assert [c["kind"] for c in MANIFEST["cards"]] == ["article", "coverage", "notice"]
    assert [(s["name"], s["for_each"]) for s in MANIFEST["secrets"]] == [
        ("wordpress_user", "owner_host"), ("wordpress_password", "owner_host")]
    assert MANIFEST["owner_hosts"]["max"] == 3  # the owner's own site, asked for at install
    assert "memory_sources" not in MANIFEST
    assert {r["id"] for r in MANIFEST["roles"]} == {"researcher", "writer", "checker"}
    assert "feeds" not in MANIFEST and MANIFEST["storage_mb"] == 300
