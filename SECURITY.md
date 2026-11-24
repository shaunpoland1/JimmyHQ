# Security

## Reporting a problem

Please do not open a public issue for a security problem. Use GitHub's private reporting
instead: on the repository page, open the **Security** tab and choose
**Report a vulnerability**. You will get a reply within a week.

Include what you found, how to reproduce it, and what an attacker could do with it.

## What to know about running JimmyHQ

- It listens on your own machine only by default. Opening it to a network is your choice;
  use a private network, not the public internet. A request that Tailscale Funnel brings
  in from the public internet is refused unless you set `allow_public = true` in
  `config.toml` ([docs/remote.md](docs/remote.md)).
- A request relayed by a proxy or tunnel on the same computer is never treated as coming
  from the computer itself: it must be addressed to a name you allowed, and its wrong
  passwords are counted apart from yours.
- It has one owner account. API tokens are made, listed and revoked by the owner, under
  Settings, Access or with `uv run jimmyhq owner token`. Each is a reader or a writer,
  never the owner: no token can change settings, passwords or other tokens. A revoked
  token stops working on its next request.
- Passwords and tokens are stored as hashes. Keys for outside services are kept in an
  owner-only file in your data folder and are never written to the Tape or to logs.
- A full app runs with your user's access, and JimmyHQ cannot limit it. There is no store
  and no review yet, so an app from anyone else is unreviewed. Install only the ones you
  trust.
- A sandboxed app can do only what its permissions list. A host you grant it receives
  what the app sends there, and if a role uses a hosted model, what the app asks leaves
  your computer. [docs/apps.md](docs/apps.md) lists the limits.
