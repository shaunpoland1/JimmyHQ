"""Pictures as a way in: taken in only when they are pictures a model can be shown,
looked at before any caption, compared with recent posts' pictures, captioned through the
same checks and card as every post, and deleted once no post needs them."""

from __future__ import annotations

import json

import pytest
from picture_rig import GIF, WEBP, jpeg, nudged, pattern
from posts import pictures

DAY_MS = 86_400_000
SCENE = pattern(18, 16, seed=7)
OTHER = pattern(18, 16, seed=99)


def seen(what: str = "A wooden bench under a pear tree in flower.", **more) -> dict:
    return {"what": what, "setting": "A small walled garden.", "objects": ["bench", "tree"],
            "text_seen": "", "people": 0, "mood": "Quiet", **more}


def caption(body: str = "The pear tree is out at last, and the bench is ours again.") -> dict:
    return {"title": "Pear blossom", "body": body, "claims": [], "links": [],
            "gloss": "Wrote a caption from the pictures."}


SHIP = {"verdict": "ship", "points": [], "gloss": "Nothing to raise."}


def start(rig, page, shots=(SCENE,), notes="First warm week of the year.", **more):
    given = [{**rig.core.upload(jpeg(s), "garden.jpg"), "note": f"Shot {n}"}
             for n, s in enumerate(shots, 1)]
    return rig.core.call("new.pictures", {"page_id": page["id"], "notes": notes,
                                          "pictures": [{"ref": g["ref"], "note": g["note"]}
                                                       for g in given], **more})


def through(rig, page, shots=(SCENE,), body=None, notes="First warm week of the year."):
    """Pictures to the draft card: looked at, compared, captioned, checked."""
    piece = start(rig, page, shots, notes=notes)
    rig.core.replies += [*(seen() for _ in shots), caption(body) if body else caption(), SHIP]
    rig.advance(len(shots) + 4)
    return rig.store.piece(piece["id"])


def asked_with_images(rig) -> list[dict]:
    return [a for a in rig.core.asked if a.get("images")]


# --- taking them in -------------------------------------------------------------------------


def test_pictures_start_a_piece_that_is_looked_at_first(rig):
    piece = start(rig, rig.page(), shots=(SCENE, OTHER))
    assert piece["state"] == "analysing" and piece["way"] == "pictures"
    kept = pictures.of_piece(rig.ctx, piece["id"])
    assert [k["note"] for k in kept] == ["Shot 1", "Shot 2"]
    assert all(len(k["sha256"]) == 64 and k["analysis"] is None for k in kept)
    assert rig.core.events[-1]["payload"] == {"piece": piece["id"], "way": "pictures"}


def _bad_upload(rig) -> str:
    folder = rig.core.data_dir / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ("ab" * 16 + ".jpg")).write_bytes(b"<p>hello</p>")
    return f"uploads/{'ab' * 16}.jpg"


@pytest.mark.parametrize("make, said", [
    (lambda rig: rig.core.upload(GIF, "a.gif")["ref"], "is a GIF, which a model cannot be shown"),
    (_bad_upload, "is not a picture Posts can read"),
    (lambda rig: "uploads/" + "cd" * 16 + ".png", "is no longer kept"),
    (lambda rig: "../posts.db", "was not uploaded through this page"),
])
def test_an_upload_that_is_not_a_picture_is_refused_with_a_sentence(rig, make, said):
    page = rig.page()
    with pytest.raises(Exception, match=said) as refused:
        rig.core.call("new.pictures", {"page_id": page["id"],
                                       "pictures": [{"ref": make(rig), "note": ""}]})
    assert "Nothing was started." in str(refused.value)
    assert rig.store.all("SELECT id FROM piece") == []
    assert rig.store.all("SELECT id FROM picture") == []


def test_a_webp_picture_is_taken_because_a_model_can_be_shown_it(rig):
    ref = rig.core.upload(WEBP, "leaf.webp")["ref"]
    piece = rig.core.call("new.pictures", {"page_id": rig.page()["id"],
                                           "pictures": [{"ref": ref}]})
    assert piece["state"] == "analysing"


def test_no_pictures_or_too_many_are_refused_with_a_sentence(make_rig):
    rig = make_rig(settings={"pictures_per_piece": 2})
    page = rig.page()
    with pytest.raises(Exception, match="Choose at least one picture"):
        rig.core.call("new.pictures", {"page_id": page["id"], "pictures": []})
    with pytest.raises(Exception, match="at most 2"):
        start(rig, page, shots=(SCENE, OTHER, SCENE))
    assert rig.store.all("SELECT id FROM picture") == []


# --- looking at them ------------------------------------------------------------------------


def test_a_picture_is_never_captioned_without_being_looked_at(rig):
    rig.core.vision_on = False
    piece = start(rig, rig.page())
    rig.core.replies += [caption(), SHIP]
    rig.advance(5)
    assert rig.store.piece(piece["id"])["state"] == "stalled"
    assert rig.core.asked == []  # no caption, no check: nothing asked of any model
    assert rig.store.post_of(piece["id"]) is None


def test_a_piece_with_pictures_waits_and_says_so_when_no_model_can_see_pictures(rig):
    rig.core.vision_on = False
    piece = start(rig, rig.page())
    rig.advance()
    stuck = rig.store.piece(piece["id"])
    assert stuck["state"] == "stalled" and stuck["came_from"] == "analysing"
    assert "No model that can see pictures" in stuck["error"]
    assert "will not caption it blind" in stuck["error"] and "Press Retry" in stuck["error"]
    rig.core.vision_on = True
    rig.core.call("piece.retry", {"id": piece["id"]})
    rig.core.replies += [seen(), caption(), SHIP]
    rig.advance(5)
    assert rig.store.piece(piece["id"])["state"] == "ready"
    [looked] = asked_with_images(rig)
    assert looked["role"] == "vision" and rig.core.asked.index(looked) == 0


def test_each_picture_is_looked_at_in_its_own_run_before_anything_else(rig):
    piece = start(rig, rig.page(), shots=(SCENE, OTHER))
    rig.core.replies += [seen(), seen("A watering can on a stone step.")]
    rig.advance()
    assert rig.store.piece(piece["id"])["state"] == "analysing"
    rig.advance()
    after = rig.store.piece(piece["id"])
    assert after["state"] in ("overlap_check", "captioning")
    assert "watering can" in after["source_title"]
    assert [a["images"] for a in rig.core.asked] == [[p["ref"]] for p in
                                                     pictures.of_piece(rig.ctx, piece["id"])]


def test_a_description_never_names_who_a_person_is(rig):
    schema = pictures.ANALYSIS_SCHEMA
    assert not {"who", "name", "names", "person", "identity"} & set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert "Never say or guess who a person is" in pictures.ANALYSE_PROMPT
    piece = start(rig, rig.page())
    rig.core.replies += [seen("Two people on a bench.", people=2, who="Ada Example"),
                         caption(), SHIP]
    rig.advance(5)
    [kept] = pictures.of_piece(rig.ctx, piece["id"])
    assert "who" not in kept["analysis"] and kept["analysis"]["people"] == 2
    assert "Ada" not in json.dumps(rig.core.asked[1:])


# --- captioning: the same road as every post ------------------------------------------------


def test_a_picture_caption_goes_through_the_same_checks_and_the_same_draft_card(rig):
    page = rig.page(tags=["#garden"])
    piece = through(rig, page, body="Our pear tree has 40 blossoms this year.")
    assert piece["state"] == "ready"
    write = rig.core.asked[1]
    assert write["role"] == "smart"  # role_caption
    assert "First warm week of the year." in write["prompt"] and "Shot 1" in write["prompt"]
    assert "pear tree in flower" in "\n".join(write["untrusted"])
    assert "pear tree" not in write["prompt"]  # what a model saw is never in the prompt
    post = rig.store.post_of(piece["id"])
    names = {c["name"]: c for c in rig.store.checks(post["id"])}
    assert {"numbers", "near_duplicate", "rules", "pictures", "source"} <= set(names)
    assert names["numbers"]["level"] == "ask"  # "40" is in no note: a question
    assert names["pictures"]["level"] == "ok"
    [card] = rig.cards_of("draft")
    assert card["subject"].endswith(f"post:{post['id']}")
    done = rig.core.call("post.approve", {"id": post["id"]})
    assert done["state"] == "booked"  # Practice books it like any other post
    assert all(p["post_id"] == post["id"] for p in pictures.of_piece(rig.ctx, piece["id"]))


def test_a_number_in_the_owners_note_on_a_picture_counts_as_found(rig):
    piece = start(rig, rig.page())
    rig.store.update("picture", pictures.of_piece(rig.ctx, piece["id"])[0]["id"],
                     {"note": "The tree is 12 years old."})
    rig.core.replies += [seen(), caption("Twelve springs on, and 12 years of pears."), SHIP]
    rig.advance(5)
    post = rig.store.post_of(piece["id"])
    numbers = [c for c in rig.store.checks(post["id"]) if c["name"] == "numbers"]
    assert numbers[0]["level"] == "ok" and numbers[0]["evidence"] == ["notes"]


def test_piece_view_gives_each_pictures_ref_note_and_description_never_its_bytes(rig):
    piece = through(rig, rig.page())
    got = rig.core.call("piece.get", {"id": piece["id"]})
    [shown] = got["pictures"]
    assert set(shown) == {"id", "ref", "note", "analysis", "looks_like", "deleted"}
    assert shown["ref"].startswith("uploads/") and shown["note"] == "Shot 1"
    assert shown["analysis"]["what"].startswith("A wooden bench")
    assert shown["looks_like"] is None and shown["deleted"] is False


# --- "looks like the post of last week" -----------------------------------------------------


def test_a_picture_that_looks_like_a_recent_posts_says_so(rig):
    page = rig.page()
    first = through(rig, page, notes="Blossom week.")
    rig.core.call("post.approve", {"id": rig.store.post_of(first["id"])["id"]})
    rig.clock.advance(7 * DAY_MS)
    second = through(rig, page, shots=(nudged(SCENE), OTHER),
                     body="A second look at the old corner, with new chairs.",
                     notes="Painted the chairs green on Sunday.")
    post = rig.store.post_of(second["id"])
    said = [c for c in rig.store.checks(post["id"]) if c["name"] == "pictures"]
    assert len(said) == 1 and said[0]["level"] == "warn"
    assert said[0]["detail"].startswith("Picture 1 looks like a picture in the post of")
    assert said[0]["evidence"] == [f"post:{rig.store.post_of(first['id'])['id']}"]
    view = rig.core.call("piece.get", {"id": second["id"]})["pictures"]
    assert view[0]["looks_like"] and view[1]["looks_like"] is None


def test_a_picture_like_one_never_posted_is_not_said_to_look_like_it(rig):
    page = rig.page()
    through(rig, page, notes="Blossom week.")  # a draft only: never approved
    second = through(rig, page, shots=(nudged(SCENE),),
                     notes="Painted the chairs green on Sunday.")
    post = rig.store.post_of(second["id"])
    [said] = [c for c in rig.store.checks(post["id"]) if c["name"] == "pictures"]
    assert said["level"] == "ok"


def test_a_picture_posts_cannot_compare_says_so_on_the_draft(rig):
    ref = rig.core.upload(WEBP, "leaf.webp")["ref"]
    piece = rig.core.call("new.pictures", {"page_id": rig.page()["id"],
                                           "pictures": [{"ref": ref}]})
    rig.core.replies += [seen(), caption(), SHIP]
    rig.advance(5)
    post = rig.store.post_of(piece["id"])
    [said] = [c for c in rig.store.checks(post["id"]) if c["name"] == "pictures"]
    assert said["level"] == "warn" and "could not compare picture 1" in said["detail"]


# --- tidying up -----------------------------------------------------------------------------


def test_pictures_of_a_post_that_went_out_are_deleted_after_the_set_days_and_not_before(
        make_rig):
    rig = make_rig(settings={"scheduler": "by_hand", "picture_keep_days": 30})
    piece = through(rig, rig.page())
    post = rig.store.post_of(piece["id"])
    rig.core.call("post.approve", {"id": post["id"]})
    out = rig.core.call("post.posted", {"id": post["id"]})
    assert out["state"] == "went_out"
    [picture] = pictures.of_piece(rig.ctx, piece["id"])
    path = rig.ctx.app.files.path(picture["ref"])
    rig.clock.now = out["slot_ms"] + 30 * DAY_MS - 60_000
    rig.advance()
    assert path.is_file() and pictures.of_piece(rig.ctx, piece["id"])[0]["deleted"] is None
    rig.clock.now = out["slot_ms"] + 30 * DAY_MS
    rig.advance()
    kept = pictures.of_piece(rig.ctx, piece["id"])[0]
    assert not path.exists() and kept["deleted"] is not None
    assert kept["dhash"]  # the fingerprint stays, to compare later pictures with
    assert rig.core.call("piece.get", {"id": piece["id"]})["pictures"][0]["ref"] is None


def test_pictures_of_a_dropped_piece_are_deleted_after_the_set_days(make_rig):
    rig = make_rig(settings={"picture_keep_days": 2})
    piece = start(rig, rig.page())
    path = rig.ctx.app.files.path(pictures.of_piece(rig.ctx, piece["id"])[0]["ref"])
    rig.core.call("piece.drop", {"id": piece["id"]})
    rig.clock.advance(DAY_MS)
    rig.advance()
    assert path.is_file()
    rig.clock.advance(DAY_MS)
    rig.advance()
    assert not path.exists()


def test_pictures_of_a_post_still_queued_are_never_deleted(make_rig):
    rig = make_rig(settings={"picture_keep_days": 1})
    piece = through(rig, rig.page())
    rig.core.call("post.approve", {"id": rig.store.post_of(piece["id"])["id"]})
    rig.clock.advance(400 * DAY_MS)
    rig.advance()
    assert pictures.of_piece(rig.ctx, piece["id"])[0]["deleted"] is None


def test_a_post_made_from_uploaded_pictures_returns_them_in_order(rig):
    page = rig.page()
    piece = through(rig, page, shots=(SCENE, OTHER))
    post = rig.store.post_of(piece["id"])
    kept = pictures.of_piece(rig.ctx, piece["id"])
    assert pictures.for_post(rig.ctx, post["id"]) == [
        {"ref": kept[0]["ref"], "name": "picture-1.jpg"},
        {"ref": kept[1]["ref"], "name": "picture-2.jpg"}]


def test_a_post_with_no_pictures_has_none_to_send(rig):
    page = rig.page()
    piece = rig.through(page, "Solar roofs spread across the valley.")
    assert pictures.for_post(rig.ctx, rig.store.post_of(piece["id"])["id"]) == []
