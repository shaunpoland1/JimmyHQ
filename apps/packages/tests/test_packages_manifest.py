"""The manifest as JimmyHQ's own checker reads it, and the list that offers the app."""

from __future__ import annotations

from pathlib import Path

from jimmyhq.apps import dev, install

REPO = Path(__file__).resolve().parents[3]
APP = REPO / "apps" / "packages"


def test_the_manifest_passes_the_platforms_own_check_with_no_finding():
    manifest, findings = dev.check_manifest(APP)
    assert findings == [] and manifest is not None
    assert manifest.private is True and manifest.tier == "full"
    assert dev.check_python(APP, manifest) == []


def test_the_app_is_one_of_the_first_party_apps_the_apps_screen_offers():
    shipped = {e["id"]: e["group"] for e in install.examples()}
    assert shipped["packages"] == "first_party"

