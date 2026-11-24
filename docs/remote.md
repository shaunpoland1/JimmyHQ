# Reaching JimmyHQ from your other devices

JimmyHQ listens on `127.0.0.1`, so only the computer it runs on can open it. To use it from
a phone or a laptop, put it on a private network you already trust. This page shows how
with [Tailscale](https://tailscale.com), which is the one JimmyHQ can set up for you, and
what to do with any other VPN or reverse proxy.

**None of this opens JimmyHQ to the public internet.** Tailscale Serve reaches only
devices signed in to your own tailnet, and JimmyHQ never runs `tailscale funnel`, the
public one. If Funnel is switched on for JimmyHQ's address anyway, JimmyHQ refuses every
request that arrives through it (see [The public internet](#the-public-internet)). Every
device still signs in to JimmyHQ with your password.

## The short way: Settings, Access

Open JimmyHQ **on the computer it runs on** (http://127.0.0.1:8440), go to **Settings,
Access**, and use the card **Reach JimmyHQ from your other devices**:

1. It tells you in plain words where things stand: Tailscale not installed, not signed in,
   turned off, ready, or already reachable at an address.
2. **Turn on** shows the exact command it will run and what changes, and waits for your
   confirmation. Then it runs it and adds the computer's tailnet name to **Other names for
   this server** (`access.allowed_hosts`).
3. Open the address it shows on any device signed in to your tailnet.
4. **Turn off** stops the forwarding. It offers, and does not force, removing the name
   from the allowed hosts.

If you set the forwarding up by hand, **Add this address to allowed hosts** does only the
second half. It is not offered, and the server refuses it, while Funnel makes the address
public; so is **Turn on** over a public mapping. Turn Funnel off first. Viewed from another device, the card replaces the buttons that change the
computer with a sentence: turning access on or off is done at the computer itself, because
it changes what that computer opens to a network. (It is refused by the server too, for
any request that does not come straight from the computer, and for an API token.)

The command is:

```text
tailscale serve --bg --https=8440 http://127.0.0.1:8440
```

with your real port in place of 8440. `--bg` keeps it running across restarts. To turn it
off: `tailscale serve --https=8440 off`. To see what Tailscale serves: `tailscale serve
status`.

## Doing it yourself

1. **Install Tailscale and sign in** on the JimmyHQ computer and on each device you want
   to use. In the Tailscale admin console, on the DNS page, turn on **MagicDNS** and
   **HTTPS certificates**. Tailscale Serve needs both. Its own pages:
   [Serve](https://tailscale.com/docs/reference/tailscale-cli/serve),
   [HTTPS](https://tailscale.com/kb/1153/enabling-https).
2. **Map your tailnet address to JimmyHQ**, in a terminal on the JimmyHQ computer:
   `tailscale serve --bg --https=8440 http://127.0.0.1:8440`. Tailscale prints the address,
   which looks like `https://example-machine.example-tailnet.ts.net:8440`. (Run the command
   once in a terminal if it prints a link: Tailscale may ask you to switch Serve on.)
3. **Let JimmyHQ answer to that name.** JimmyHQ refuses a request addressed to a name it
   does not know, which stops another website from pointing its own name at your computer.
   A request that a proxy or tunnel relays is never answered for `localhost` or
   `127.0.0.1`, whatever Host it carries: only for a name in this list. This computer's
   own names cannot be added to it (they always work from this computer); one saved there
   before is ignored, and a line in the log says so.
   Add `example-machine.example-tailnet.ts.net` (your real name, no `https://`, no port) to
   Settings, Access, **Other names for this server**. Or add it to `allowed_hosts` in
   `config.toml` and restart, or set `JIMMYHQ_ALLOWED_HOSTS`.
4. **Open the address** on the other device, signed in to the same tailnet.

## By system

- **macOS.** The command is `/usr/local/bin/tailscale` if you installed the command line
  integration from the Tailscale app's settings, or inside the app at
  `/Applications/Tailscale.app/Contents/MacOS/Tailscale` for the App Store version. JimmyHQ
  looks in both, and in your `PATH`.
- **Linux.** The command is normally `/usr/bin/tailscale`. Changing Serve needs permission:
  run the command with `sudo`, or once run `sudo tailscale set --operator=$USER` so your own
  user may, which also lets the Turn on button work.
- **Windows.** The command is `tailscale.exe`, usually in `C:\Program Files\Tailscale\` (older
  installers used `Tailscale IPN` under Program Files (x86)). JimmyHQ looks in both and in your
  `PATH`. Run the command in a terminal opened as administrator if it is refused.
- **Somewhere else.** Put the full path of the `tailscale` program in Settings, Access,
  **Path to the Tailscale command** (`remote.tailscale_path`).

## Any other VPN or reverse proxy

JimmyHQ does not care how the traffic arrives, only what name the request is addressed to.

- Add the host name the proxy or VPN uses to **Other names for this server**.
- If a **reverse proxy** on the JimmyHQ computer, or one in front of it, forwards requests,
  list its address in `trusted_proxies` in `config.toml` (or `JIMMYHQ_TRUSTED_PROXIES`), so
  JimmyHQ believes the visitor address it forwards, and not before.
- Creating the owner account is allowed only at the computer itself, never through a proxy
  or tunnel, unless you set `remote_setup = true`. Create the account first.
- A proxy or tunnel on this computer connects from `127.0.0.1`, just as your own browser
  does. JimmyHQ tells them apart by the forwarding header the proxy adds
  (`X-Forwarded-For`, `Forwarded` or `X-Real-IP`) and by the name the request is addressed
  to. A relayed request is never "this computer": its wrong passwords are counted apart
  from yours, so nobody on the far side can lock you out at the machine itself, and the
  sign-in cookie is marked secure when the proxy says the browser used HTTPS.
- **Raw TCP tunnels** (`tailscale serve --tcp`, `tailscale funnel --tcp`, an SSH `-L`
  forward) add no header at all, so a request through one looks like a request made on
  this computer if it is addressed to `localhost`. Do not point one at JimmyHQ's port. Use
  `tailscale serve --https` (what the Turn on button runs) or a reverse proxy that adds
  `X-Forwarded-For`.

## The public internet

Making JimmyHQ public is your decision, with its own risks. Nothing here does it for you.
Tailscale's public option is documented at
[tailscale.com/kb/1223/funnel](https://tailscale.com/kb/1223/funnel). JimmyHQ shows a
warning if it finds Funnel switched on for its address, and by default refuses every
request that came in that way (tailscaled marks them with `Tailscale-Funnel-Request` and
removes any copy a client sends). The refusal says how to turn Funnel off:
`tailscale funnel --https=<port> off`.

If you really want JimmyHQ to answer the public internet through Funnel, put this in
`config.toml` in the data folder and restart:

```toml
[server]
allow_public = true
```

(or set `JIMMYHQ_ALLOW_PUBLIC=true`). The name must still be in the allowed hosts, and
anyone who finds the address can then try your password.

## What JimmyHQ runs and records

Only these, always as a list of arguments (never through a shell) with a time limit:

| Command | When |
| --- | --- |
| `tailscale status --json` | Reading the status. Changes nothing. |
| `tailscale serve status --json` | Reading the status. Changes nothing. |
| `tailscale serve --bg --https=<port> http://127.0.0.1:<port>` | Turn on, after you confirm. |
| `tailscale serve --https=<port> off` | Turn off. |

A mapping on the same port that already forwards somewhere else is never replaced. A
Tailscale that is too old to answer `--json` is reported as such, with the steps above.

Turning access on or off leaves an event on the Tape, `remote.serve_changed`, holding only
whether it was turned on and the local port, never a name. Adding or removing the name is an
ordinary setting change and is recorded as one. JimmyHQ stores no tailnet name anywhere
except in the allowed hosts you chose to add.

## The routes

All need the signed-in owner. Under `/api/remote`:

| Route | What |
| --- | --- |
| `GET /status` | State, this computer's tailnet name, the mapping if any, whether the name is allowed, whether the request is from the computer itself, and the exact commands. |
| `POST /tailscale/serve` | Run the one `serve` command and allow the name. Only from the computer itself. Refused (409) while Funnel makes the mapping public. |
| `DELETE /tailscale/serve?remove_host=true` | Turn the mapping off; with `remove_host` also take the name out. Only from the computer itself. |
| `POST /allow-host` | Add the name to the allowed hosts. Touches nothing in Tailscale. Refused (409) while Funnel makes the mapping public. |
| `DELETE /allow-host` | Take the name out again. |

Command line reference used: [status and version](https://tailscale.com/docs/reference/tailscale-cli),
[serve](https://tailscale.com/docs/reference/tailscale-cli/serve).
