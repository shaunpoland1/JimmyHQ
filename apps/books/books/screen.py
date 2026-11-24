"""What Books' own page may ask (``hq.app.call``). Each call returns plain JSON.

The page shows every string as text, never as HTML. A refusal is a sentence the page
puts on its status line. Amounts reach the page only here, on the owner's own screen.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from books import auto, backup, debts, importing, money, ops, reconcile, reminders, reports, tell
from books.context import Ctx


def overview(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store, style, cfg = ctx.store, ctx.style(), ctx.cfg()
    accounts = [ops.summary(ctx, a) for a in store.accounts()]
    net = sum(a["today"]["cents"] for a in accounts if not a["closed"])
    have = sum(a["today"]["cents"] for a in accounts if not a["closed"] and not a["owed"])
    owe = -sum(a["today"]["cents"] for a in accounts if not a["closed"] and a["owed"])
    return {
        "today": ctx.today().isoformat(), "style": style,
        "accounts": accounts,
        "total": {"text": money.format_cents(net, style), "neg": net < 0,
                  "have": money.format_cents(have, style), "owe": money.format_cents(owe, style)},
        "kinds": [{"id": k, "words": w} for k, w in ops.KINDS.items()],
        "categories": [{"id": c["id"], "name": c["name"], "income": bool(c["income"])}
                       for c in store.categories()],
        "due_now": reminders.due_now(store, ctx.today()),
        "due_days": int(cfg.due_days), "ahead_days": int(cfg.ahead_days),
        "import_bytes": importing.most_bytes(ctx),
        "empty": backup.is_empty(store),
    }


def _changed(ctx: Ctx, fn: Callable[[Ctx, dict[str, Any]], dict[str, Any]]
             ) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A call that changes something: its open pages hear about it."""
    def run(args: dict[str, Any]) -> dict[str, Any]:
        out = fn(ctx, args)
        if not out.get("needs_confirm"):
            ctx.emit()
        return out
    return run


def _reminder(ctx: Ctx, fn: Callable[[Ctx, dict[str, Any]], dict[str, Any]], note: str
              ) -> Callable[[Ctx, dict[str, Any]], dict[str, Any]]:
    """A change to a reminder: its old card goes, and the Bench is looked at again."""
    def run(_: Ctx, args: dict[str, Any]) -> dict[str, Any]:
        out = tell.settled(ctx, fn(ctx, args), note)
        auto.release(ctx)  # the owner dealt with what was overdue: nothing is stopped now
        tell.check(ctx)
        return out
    return run


def _erase(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    out = backup.erase_everything(ctx, args)
    for card in out.pop("cards", []):
        tell.withdraw(ctx, card, "Books was erased.")
    return out


def reminders_listing(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """The reminders, and what Books posted by itself lately."""
    out = reminders.listing(ctx, args)
    accounts = {a["id"]: a["name"] for a in ctx.store.accounts()}
    out["auto_recent"] = [{**e, "account_name": accounts.get(e["account"], ""),
                           "to_account_name": accounts.get(e["to_account"], "")}
                          for e in auto.recent(ctx)]
    return out


def ahead(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """One account's forward look, with what Books posted by itself for that account lately
    (and for any reminder paid another way, which belongs to none)."""
    out = reminders.ahead(ctx, args)
    mine = out["account"]
    out["auto_recent"] = [e for e in auto.recent(ctx)
                          if e["paid_other"] or mine in (e["account"], e["to_account"])]
    return out


def calls(ctx: Ctx) -> dict[str, Callable[[dict[str, Any]], Any]]:
    def read(fn: Callable[[Ctx, dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
        return lambda a: fn(ctx, a)

    def change(fn: Callable[[Ctx, dict[str, Any]], dict[str, Any]]
               ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        return _changed(ctx, fn)

    def reminder_add(c: Ctx, a: dict[str, Any]) -> dict[str, Any]:
        out = reminders.add(c, a)
        tell.check(c)
        return out

    return {
        "overview": read(overview),
        "account.add": change(ops.add_account),
        "account.edit": change(ops.edit_account),
        "account.delete": change(ops.delete_account),
        "category.add": change(ops.add_category),
        "category.rename": change(ops.rename_category),
        "category.merge": change(ops.merge_category),
        "register": read(ops.register),
        "txn.add": change(ops.add_txn),
        "txn.edit": change(ops.edit_txn),
        "txn.delete": change(ops.delete_txn),
        "txn.clear": change(ops.toggle_cleared),
        "transfer.add": change(ops.add_transfer),
        "reminders": read(reminders_listing),
        "reminder.add": change(reminder_add),
        "reminder.edit": change(_reminder(ctx, reminders.edit, "The bill was changed.")),
        "reminder.delete": change(_reminder(ctx, reminders.delete, "The bill was deleted.")),
        "reminder.post": change(_reminder(ctx, reminders.post, "Posted in Books.")),
        "reminder.skip": change(_reminder(ctx, reminders.skip, "Skipped in Books.")),
        "ahead": read(ahead),
        "debts": read(debts.listing),
        "debt.add": change(debts.add),
        "debt.edit": change(debts.edit),
        "debt.balance": change(debts.set_balance),
        "debt.delete": change(debts.delete),
        "reconcile.start": read(reconcile.start),
        "reconcile.view": read(reconcile.view),
        "reconcile.tick": change(reconcile.tick),
        "reconcile.finish": change(reconcile.finish),
        "spending": read(reports.spending),
        "spending.rows": read(reports.category_rows),
        "upload.start": read(importing.upload_start),
        "upload.part": read(importing.upload_part),
        "import.preview": read(importing.preview),
        "import.commit": change(importing.commit),
        "import.list": read(importing.batches),
        "import.undo": change(importing.undo),
        "export.part": read(backup.export_part),
        "everything.import": change(backup.import_everything),
        "everything.erase": change(_erase),
    }
