# Contributing to JimmyHQ

Thank you for helping. This page covers how to set up, the rules every change follows,
and how a change reaches the main branch.

## Set up

```bash
uv sync
cd web && npm install && cd ..
uv run pytest
uv run jimmyhq run
```

Your data lives outside the repo. `uv run jimmyhq data-dir` prints where. Set
`JIMMYHQ_DATA` to use a scratch folder while developing.

## Design rules

1. **Settings first.** Anything that could differ between two users is a setting with a
   sensible default. No host, model name, person's name, schedule or path in the code.
2. **A human click for anything that leaves the building.** Sending, posting, paying and
   deleting wait for approval unless the owner has allowed that specific action.
3. **Local first, visible fallback.** Nothing is sent to a hosted model unasked, and the
   screen says when one is in use.
4. **The Tape is the record.** Decisions and actions are appended to it. Nothing is
   rewritten, except that the owner can have something erased for good (`Tape.erase`:
   the event keeps its place and loses what it said). Screens read stored views
   (lenses), not the Tape itself.
5. **Private things stay off the record.** Journals, health data and transcripts get their
   own store with a real delete.
6. **Outside text is data, not instructions.** Text from a page, an email or a file cannot
   trigger an action without approval.
7. **Select, never invent.** Where a model picks from evidence, it picks by number.
8. **Errors say what to do next.** Write the message for the person, or the agent, who
   has to recover from it.
9. **Public-safe.** No real names, hosts, addresses or personal examples anywhere,
   including tests and sample data.

## Changing a store's schema

A store that cannot be rebuilt (see the Stores table in
[docs/architecture.md](docs/architecture.md)) declares its schema with
`storeversion.declare` and opens through `storeversion.prepare`.

- To change its shape, add a step to the end of its `steps`. The step takes the file from
  the version before it to the next one.
- Never edit a released step, or the first schema. Files out there were made by them.
- Add a test that builds a file in the old shape, opens it with the new code, and finds
  its rows kept and the new shape in place.
- A new store that cannot be rebuilt is declared the same way, listed in
  `storeversion.DECLARED_IN`, added to the Stores table and given an opener in
  `tests/test_storeversion_stores.py`, which fails until they agree.

## How a change gets in

1. Open an issue first for anything larger than a small fix, so the approach can be agreed.
2. Create a branch and make the change. Add or update tests. Name a test as a sentence
   that says what must stay true.
3. Run the checks: `uv run ruff check`, `uv run pytest`, and in `web/`:
   `npm run typecheck && npm test && npm run build`.
4. Sign off each commit with `git commit -s`. This confirms you have the right to submit
   the work under the project's licence
   ([Developer Certificate of Origin](https://developercertificate.org)).
5. Open a pull request and fill in the template. The automated checks must pass, and a
   maintainer reviews and merges. Nobody pushes to `main` directly.

## Submitting an app

The app platform works: you can write an app, check it and install it from a folder or a
git address. [docs/apps.md](docs/apps.md) says how, and `uv run jimmyhq apps new` writes a
skeleton. What is missing is the store: there is no list of apps to submit to and no
review yet. See the [roadmap](ROADMAP.md).
