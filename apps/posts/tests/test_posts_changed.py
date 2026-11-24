"""Every call of the page that changes a record tells the open pages ``posts.changed``,
so a view drawn before the change is drawn again (a page saved in one view shows in New
and Today without a reload)."""

from __future__ import annotations

from posts.program import READS, announcing, calls
from posts_rig import ARTICLE_TEXT, draft_reply, ship

CHANGED = "posts.changed"


def _told(rig) -> int:
    return sum(1 for e in rig.core.emitted if e["event"] == CHANGED)


def test_a_call_that_changes_a_record_without_saying_so_is_announced(rig):
    def quiet_write(args):
        rig.store.insert("rule", {"page_id": None, "kind": "phrase", "text": "x",
                                  "says": "", "created": 1})
        return True

    before = _told(rig)
    assert announcing(rig.ctx, quiet_write)({}) is True
    assert _told(rig) == before + 1


def test_a_call_that_changes_nothing_is_not_announced(rig):
    before = _told(rig)
    announcing(rig.ctx, lambda args: rig.store.pages())({})
    assert _told(rig) == before


def test_a_call_that_announced_its_own_change_is_not_announced_twice(rig):
    def loud_write(args):
        rig.store.set_meta("seen", 1)
        rig.ctx.emit(CHANGED)

    before = _told(rig)
    announcing(rig.ctx, loud_write)({})
    assert _told(rig) == before + 1


def test_a_refused_call_that_changed_a_record_on_the_way_is_still_announced(rig):
    def half_done(args):
        rig.store.set_meta("tried", 1)
        raise ValueError("No.")

    before = _told(rig)
    try:
        announcing(rig.ctx, half_done)({})
    except ValueError:
        pass
    assert _told(rig) == before + 1


def test_every_call_of_the_page_that_is_not_a_read_is_announced(rig):
    names = set(calls(rig.ctx))
    assert READS <= names
    for name in names - READS:
        assert getattr(rig.app._calls[name], "__wrapped__", None) is not None, name  # noqa: SLF001
    for name in READS:
        assert not hasattr(rig.app._calls[name], "__wrapped__"), name  # noqa: SLF001


def test_walking_every_changing_call_each_one_that_changed_a_record_told_the_pages(rig):
    """The page's calls in the order an owner would use them. Each call that changed a
    record must have emitted, and the walk must reach the calls that change the owner's
    configuration and drafts, so the test cannot pass by changing nothing."""
    changed: list[str] = []

    def step(name, args):
        conn = rig.store.conn
        changes, before = conn.total_changes, _told(rig)
        try:
            result = rig.core.call(name, args)
        except Exception:  # noqa: BLE001 - a refusal is fine; what it changed is checked
            result = None
        if conn.total_changes != changes:
            changed.append(name)
            assert _told(rig) > before, f"{name} changed a record and told no page"
        return result

    page = step("pages.save", {"name": "Garden notes", "register": "Warm and plain.",
                               "hours": ["08:00"]})
    step("pages.save", {"id": page["id"], "name": "Garden notes", "hours": ["09:00"]})
    rule = step("rules.save", {"page_id": None, "kind": "phrase", "text": "game changer"})
    step("rules.delete", {"id": rule["id"]})
    for name in sorted(READS):
        step(name, {"id": 1} if name == "piece.get" else {})

    rig.core.pages["https://news.example.com/a"] = ARTICLE_TEXT
    piece = step("new.link", {"page_id": page["id"], "url": "https://news.example.com/a"})
    step("new.topic", {"page_id": page["id"], "topic": "Solar roofs", "notes": "From notes."})
    step("piece.drop", {"id": piece["id"] + 1})
    rig.core.replies += [draft_reply("Solar roofs: 1,240 homes and 99 more we made up."),
                         ship()]
    rig.advance(4)
    post = rig.store.post_of(piece["id"])
    asks = [c for c in rig.store.checks(post["id"]) if c["level"] == "ask"]
    if asks:
        step("check.answer", {"id": asks[0]["id"], "answer": "Yes, it is in my notes."})
    step("post.edit", {"id": post["id"], "body": "Solar roofs: 1,240 homes added panels."})
    step("post.approve", {"id": post["id"]})
    for name in ("post.book", "post.move", "post.cancel", "post.clear", "post.book_again",
                 "post.posted", "post.send_back", "post.decline", "piece.retry",
                 "piece.draft_anyway", "work.choose"):
        step(name, {"id": post["id"], "slot_ms": rig.clock.now + 86_400_000,
                    "note": "Shorter.", "reason": "Not for us.", "action": "wait"})

    for needed in ("pages.save", "rules.save", "rules.delete", "new.link", "new.topic",
                   "piece.drop", "post.edit", "post.approve"):
        assert needed in changed, f"the walk never changed anything with {needed}"
