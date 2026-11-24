"""What Imagery's own page may ask (``hq.app.call``). Each call returns plain JSON.

The page shows every string as text, never as HTML. A refusal is a sentence the page puts on
its status line.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from imagery import gallery, spend
from imagery.context import Ctx
from imagery.rules import parse_presets


def overview(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    cfg = ctx.cfg()
    provider = ctx.provider()
    problem = provider.problem(cfg)
    return {
        "provider": {"id": provider.id, "label": provider.label, "outside": bool(provider.outside),
                     "problem": problem, "model": provider.model(cfg),
                     "supports_reference": bool(provider.supports_reference),
                     "aspects": provider.aspects(cfg)},
        "presets": parse_presets(cfg.style_presets),
        "max_count": int(cfg.max_count),
        "prompt_max": int(cfg.prompt_max_chars),
        "today": spend.status(ctx),
        "gallery": gallery.counts(ctx),
        "page_size": int(cfg.page_size),
        "pruned": ctx.store.get_meta("pruned"),
        **ctx.maker.status(),
    }


def pruned_seen(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    ctx.store.set_meta(pruned=None)
    return {"ok": True}


def made(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    return ctx.maker.start(args)


def calls(ctx: Ctx) -> dict[str, Callable[[dict[str, Any]], Any]]:
    return {
        "overview": lambda a: overview(ctx, a),
        "status": lambda a: {**ctx.maker.status(), "today": spend.status(ctx),
                             "gallery": gallery.counts(ctx)},
        "make": lambda a: made(ctx, a),
        "run.stop": lambda a: ctx.maker.stop(a),
        "run.dismiss": lambda a: ctx.maker.dismiss(a),
        "pictures.list": lambda a: gallery.listing(ctx, a),
        "picture.star": lambda a: gallery.star(ctx, a),
        "picture.delete": lambda a: gallery.remove(ctx, a),
        "reference.add": lambda a: gallery.add_given(ctx, a),
        "pruned.dismiss": lambda a: pruned_seen(ctx, a),
    }
