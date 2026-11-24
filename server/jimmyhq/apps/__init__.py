"""Apps: what other people build on JimmyHQ. See jimmyhq/modules.py for how a module
plugs in.

This package starts with the two things everything else stands on:

- ``manifest``: ``load(folder)`` and ``parse(text)`` turn an app's ``jimmyhq.app.json``
  into a checked ``Manifest``, or refuse it with a sentence for each mistake.
- ``permissions``: the table of what an app may ask for, ``describe(manifest)`` for the
  consent dialog's sentences, ``diff(old, new)`` for an update, and
  ``fingerprint(manifest)`` for the record of what the owner agreed to.

``kinds`` holds the Tape kinds for an app's life (installed, updated, removed and so on).

``install`` takes an app from a folder or a git commit to installed, in two calls with
the owner's consent between them; ``store`` is where apps live on disk and how other
packages ask what is installed; ``lens`` folds the record; ``routes`` is ``/api/apps``.

``bridge`` carries out what an app's page asks for, as ``app:<id>`` and only within what
the owner granted; ``appdata`` keeps each app's own values and records; ``described``
is what a record may hold and every way of reading them; ``quotas`` are the limits;
``files`` serves an app's own files into its sandboxed frame.

A full app's program talks to JimmyHQ through ``api`` (``/api/app/v1/``) with its own
token, and JimmyHQ hands it work through its ``inbox``; ``schemas`` keeps the kinds of event
it declares. ``update`` puts a new version in place, rolls back, and erases what an app
wrote when it is removed; ``environment`` builds a full app's own Python environment.
``plugs`` joins a program to jobs, the Bench, Memory, agents' tools and its own settings;
``dev`` links a folder for development. ``inbound`` takes messages from the owner's
devices for an app that asks for them (``POST /api/in/<app>/<name>``, ``inbound_routes``).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from jimmyhq.apps.manifest import (
    APP_API,
    MANIFEST_FILE,
    Manifest,
    ManifestError,
    Problem,
    check_alongside,
    load,
    parse,
)
from jimmyhq.apps.permissions import Diff, Line, describe, diff, fingerprint

if TYPE_CHECKING:
    from fastapi import APIRouter

    from jimmyhq.core import Core
    from jimmyhq.scheduler import Scheduler

__all__ = [
    "APP_API", "MANIFEST_FILE", "Diff", "Line", "Manifest", "ManifestError", "Problem",
    "build", "check_alongside", "jobs", "describe", "diff", "fingerprint", "load", "parse",
]


def build(core: Core) -> APIRouter:
    """Every route of the app platform as one router. ``routes`` holds the table."""
    from fastapi import APIRouter  # noqa: PLC0415 - keeps import light

    from jimmyhq.apps import (  # noqa: PLC0415
        api,
        bridge,
        files,
        inbound_routes,
        local_reach,
        owner_hosts,
        plugs,
        routes,
        runtime,
        secrets_entry,
        update,
        uploads,
    )
    from jimmyhq.apps.plugs import emit  # noqa: PLC0415

    # Every app's own kinds, its settings on the Settings screen, its sources in Memory;
    # kept in step from then on before each request (``sync``, from the web app's gate).
    plugs.setup(core)
    with contextlib.suppress(OSError):
        runtime.publish(core)  # which programs should run, as of this start
    router = APIRouter()
    # Installing and the record first: its fixed words are matched before any app's id.
    for part in (routes, update, bridge, files, runtime, emit, api, secrets_entry, uploads,
                 owner_hosts, local_reach, inbound_routes):
        router.include_router(part.build_router(core))
    from jimmyhq.apps import roles_routes  # noqa: PLC0415 - roles: who fills an app's roles

    router.include_router(roles_routes.build_router(core))
    return router


def jobs(core: Core, scheduler: Scheduler) -> None:
    """The worker: every full app's own kinds are registered here too, so what the apps
    wrote reads back in this process, and the plug points' clock runs here
    (``plugs.tick``): jobs, decisions, settings and crash loops."""
    from jimmyhq.apps import plugs, update  # noqa: PLC0415

    plugs.register(core, scheduler)  # also what the apps declare, kept in step each tick
    update.register(core, scheduler)  # looking for new versions, and those allowed alone
