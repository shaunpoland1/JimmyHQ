"""The "Build your own" guide on the Apps screen shows a description file to copy. It must
be one this JimmyHQ really installs."""

from __future__ import annotations

from pathlib import Path

from jimmyhq.apps import install, store
from jimmyhq.apps import manifest as mf

GUIDE = Path(__file__).resolve().parents[1].joinpath(
    "web", "src", "modules", "apps", "lib", "build.ts")


def described_example() -> str:
    text = GUIDE.read_text(encoding="utf-8")
    body = text.split("// --- described example ---", 1)[1]
    body = body.split("// --- end described example ---", 1)[0]
    return body.split("`", 2)[1]


def test_the_description_the_guide_offers_to_copy_installs_as_a_described_app(core, tmp_path):
    folder = tmp_path / "reading"
    folder.mkdir()
    (folder / mf.MANIFEST_FILE).write_text(described_example(), encoding="utf-8")
    m = mf.load(folder)
    assert store.no_code(m)
    plan = install.plan_folder(core, folder)
    assert plan["described"] is True and plan["provenance"] == "folder"
    install.confirm(core, plan["plan"], plan["fingerprint"])
    assert install.detail(core, m.id)["described"] is True
