"""The picture fingerprint (a difference hash), read with no picture library."""

from __future__ import annotations

from picture_rig import GIF, WEBP, blown_up, jpeg, nudged, pattern, png
from posts import imagehash

BLOCKS = pattern(18, 16, seed=7)  # an 18 by 16 grid of blocks: a 144 by 128 picture


def hash_of(tmp_path, data: bytes, name: str = "p.bin") -> str | None:
    path = tmp_path / name
    path.write_bytes(data)
    return imagehash.dhash(path)


def test_the_same_picture_as_png_and_as_jpeg_has_nearly_the_same_fingerprint(tmp_path):
    from_png = hash_of(tmp_path, png(blown_up(BLOCKS)), "a.png")
    from_jpeg = hash_of(tmp_path, jpeg(BLOCKS), "a.jpg")
    assert from_png and from_jpeg and len(from_png) == 16
    assert imagehash.distance(from_png, from_jpeg) <= 2


def test_a_second_shot_of_a_scene_is_close_and_another_scene_is_far(tmp_path):
    first = hash_of(tmp_path, jpeg(BLOCKS), "a.jpg")
    again = hash_of(tmp_path, jpeg(nudged(BLOCKS)), "b.jpg")
    other = hash_of(tmp_path, jpeg(pattern(18, 16, seed=99)), "c.jpg")
    assert imagehash.distance(first, again) <= 6
    assert imagehash.distance(first, other) >= 16


def test_grey_and_colour_jpegs_progressive_ones_and_restart_markers_all_read_alike(tmp_path):
    plain = hash_of(tmp_path, jpeg(BLOCKS), "a.jpg")
    for n, data in enumerate([jpeg(BLOCKS, components=3), jpeg(BLOCKS, progressive=True),
                              jpeg(BLOCKS, components=3, progressive=True),
                              jpeg(BLOCKS, restart=5), jpeg(BLOCKS, components=3, restart=7)]):
        assert hash_of(tmp_path, data, f"v{n}.jpg") == plain, n


def test_a_grey_png_reads_like_a_colour_one(tmp_path):
    shades = blown_up(BLOCKS, 4)
    assert hash_of(tmp_path, png(shades), "a.png") == hash_of(tmp_path, png(shades, colour=False),
                                                              "b.png")


def test_what_cannot_be_read_here_gives_no_fingerprint_and_never_raises(tmp_path):
    assert hash_of(tmp_path, WEBP, "a.webp") is None
    assert hash_of(tmp_path, GIF, "a.gif") is None
    assert hash_of(tmp_path, png(blown_up(BLOCKS), interlaced=True), "i.png") is None
    hash_of(tmp_path, jpeg(BLOCKS)[:120], "cut.jpg")  # cut short: no error either way
    assert hash_of(tmp_path, b"\x89PNG\r\n\x1a\nbroken", "b.png") is None
    assert imagehash.dhash(tmp_path / "missing.png") is None


def test_the_distance_counts_the_bits_that_differ():
    assert imagehash.distance("0" * 16, "0" * 16) == 0
    assert imagehash.distance("0" * 16, "f" * 16) == 64
    assert imagehash.distance("0000000000000001", "0000000000000003") == 1
