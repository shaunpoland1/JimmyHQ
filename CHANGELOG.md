# Changelog

Every release of JimmyHQ, newest first. Each entry gives the version, the date, a short
summary, and what was added, changed and fixed.

## 0.1.0 - 2026-11-24

The first public version. JimmyHQ runs on your own computer and opens in your browser. You
bring the models, give each role the one you want, and agents and apps put them to work. What
they want to do waits on the Bench for your decision, and every decision and action is written
to the Tape. It is early: most areas work, and each guide says what is still planned.

### Added

- The Tape: an append-only record of every decision and action. Every screen is a view over
  it. Something put on the record can be erased for good when you ask.
- The Bench: one inbox for everything that needs your decision, with Approve, Not yet and No.
  Only your own click records a decision.
- Tasks: one list for your work and your agents' work, with a sheet for today.
- Models: a model server on your own machine or on another machine you own, or a hosted
  provider with your own key. Six roles (default, cheap, smart, code, vision, embeddings),
  each with a first choice and fallbacks. Every call leaves a receipt, and a usage strip
  shows what was used.
- Chat with any model you have set up, on or off the record, with dictation.
- The Village: agents with energy, social, purpose, frustration and curiosity that rise and
  fall, a heartbeat, sleep, a diary, and memory sorted overnight.
- Memory: a library of plain Markdown files you can read and edit, search, sources you switch
  on and off, and a screen that shows what was and was not saved.
- Integrations, web lookup and an agent's own browser, each only once you allow it.
- Apps: described, sandboxed and full apps, with the permissions shown before you install.
  Apps made by the JimmyHQ project, each with a guide, sample content and tests of its own;
  the README lists the ones in this copy.
- Other AI apps: outside assistants connect over MCP to a small, fixed set of tools. Off until
  you switch it on.
- A second machine of yours can lend its models.
- The Wall: a status board for a second screen.
- Backups on a schedule to one or two folders, with a regular restore test, and a restore
  from the command line.
- Settings as files: export, import and shareable profiles.
- Reaching JimmyHQ from your other devices over your own private network.
