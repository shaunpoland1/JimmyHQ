# Working on JimmyHQ

Read `CONTRIBUTING.md` first: the design rules there apply to every change.

## Commands

- Install: `uv sync` and `cd web && npm install`
- Server tests: `uv run pytest -n auto` for everything (about 25 seconds). While working on one
  module, run only its files: `uv run pytest tests/test_<name>*.py`. The whole suite is for
  the end, before a push.
- Lint: `uv run ruff check`
- Regenerate shared types after changing an event kind: `uv run python scripts/gen_types.py`
- Web: `cd web && npm run typecheck && npm test && npm run build`
- Run locally: `uv run jimmyhq run`, then open http://127.0.0.1:8440

## Layout

- `server/jimmyhq/` is the Python core. `tape/` is the event ledger, `lens.py` the stored
  views, `settings.py` the settings registry, `auth.py` access, `web/app.py` the API.
- `web/` is the React app. Pure logic lives in `web/src/lib/` with tests in `web/tests/`.
- `tests/` holds server tests. Test names are sentences that say what must stay true.

## Rules that are easy to break

- Nothing personal in the repo: no real names, hosts, addresses or home paths, in code,
  comments, tests or sample data. `tests/test_public_safe.py` checks for the obvious ones.
- Anything that could differ between two users is a setting, declared in
  `settings_core.py` or by the app that owns it. Do not hard-code it.
- The only way to write to the Tape is `Tape.print`. A new event kind needs an example.
- Screens read through a lens. Do not read the Tape directly per request.
- Stored times are unix milliseconds in UTC. Convert at the display edge only.
- Never use the browser's `alert`, `confirm` or `prompt`.

## Modules

Features live in modules. A server module is `server/jimmyhq/<name>/` and is listed in
`server/jimmyhq/modules.py`; it may have `kinds.py`, `settings.py`, and `build(core)` and
`jobs(core, scheduler)` in its `__init__.py`. Routes use the dependencies in
`web/deps.py` (`reader`, `writer`, `owner`). Its tests are `tests/test_<name>*.py`.

A web module is `web/src/modules/<name>/`: its own `api.ts` built on `request` from
`src/api.ts`, its components, and pure logic in `lib/` with tests in
`web/tests/<name>.*.test.ts`. A module should not edit another module's files or the
shared shell; the shell reaches into modules only through the small hooks and components
already wired in (`TodayPanel`, `useBenchCount`, `useModelUsageText`,
`useModelIndicators`, `settings/customSections.tsx`, `WallScreen`).
