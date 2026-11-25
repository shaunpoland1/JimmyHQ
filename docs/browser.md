# The browser

`server/jimmyhq/browser/` gives an agent a headless browser of its own: the smallest
useful version of a computer of its own. An agent with the `browser` capability opens
pages, clicks, types, scrolls and reads. Unlike [web lookup](lookup.md), this acts on
live sites, so every safety rule here is code with a test, not a sentence in a prompt.

| File | What it holds |
|---|---|
| `engine.py` | Which browser program is used, and every argument it is started with |
| `cdp.py` | The DevTools protocol over a pipe |
| `fence.py` | The rules every request must pass, and the proxy that enforces them |
| `page.py` | The page driver: numbered elements, text, a picture, the actions |
| `session.py` | One profile and one running browser per agent, stopped when idle |
| `fields.py` | Which boxes are never typed into; which actions change something |
| `config.py` | The owner's per-agent choices: the level for forms, the allow-list |
| `routes.py` | What the owner's Browser tab reads |
| `village/tools/browser.py` | The agent's nine tools and the rules that hold them |

## For owners

**Switching it on.** Open an agent in the Village screen and give it "A browser of its
own". It is off for every agent until you do. The description beside the switch says what
it allows. The limits and the two lists are under Settings, Browser.

**What an agent can do with it.** Open a public page; click links and buttons; type into
ordinary boxes; press Enter, Tab, Escape and the arrow keys; scroll; go back; use a few
tabs; read the page's text. A search box works: what it types there is sent to that site.

**What it cannot do, whatever a page or the agent says.**

- Reach this machine, your private network, your tailnet, or JimmyHQ itself.
- Reach a site on your deny list (`browser.hard_deny`). It ships with banks and card
  issuers, brokerages, payment and crypto sign-ins, password managers and webmail sign-in
  pages. Add your own sign-in and admin sites. The list is about *hosts*: see "What the
  fence cannot see" below.
- Type into a password box, or a box that asks for an email address, a sign-in name, a
  card or account number, a one-time code, a phone number, an identity number or a street
  address (`browser.refused_fields`). You can take a kind off the list; an agent cannot.
- Download or upload a file. Answer a dialog box. Open a `file:` address or one of the
  browser's own pages. Use an extension. See your own browser's profile, cookies or saved
  passwords: each agent has an empty profile of its own, kept under its folder.

**A form that changes something.** A click or Enter that would send a form by POST, or
press a button whose words say buy, sign up, send, delete or confirm
(`browser.submit_words`), follows the level you chose for that agent on its Browser tab:

| Level | What happens |
|---|---|
| Read only | Refused. The agent is told to ask you. |
| Ask me each time | A card on your Bench shows the page's address, where the form goes and every field with its value, exactly as it would be sent. Approve sends that, and only if the page still shows exactly that; otherwise nothing is sent and the agent is told the page changed. |
| Alone | Sent. A card tells you what was sent; "No" on it puts the agent back to asking. |

A new agent starts at "Ask me each time" (`browser.default_submit_level`). You can change
the level directly, in one click, in either direction.

**Watching.** The agent's Browser tab shows its tabs, the address and title, the newest
picture of the page, today's use against the limits, and what the fence refused. You
watch; you do not drive. The picture is one file that the next picture replaces. It is
never on the Tape.

**Where the page goes.** What a page shows is sent to the agent's model, as text and, if
the model takes pictures, as a picture. With "Local only" on for the agent, that model is
on this machine or the call is not made. Nothing else in JimmyHQ sends page content to a
model. In the village's own log an action leaves one line the village wrote: the site's host
and sizes. The title and the full address reach the agent only inside the marked view.

## Installing the engine

JimmyHQ ships no browser and needs no extra Python package for this. It drives a
Chromium-family browser that is already installed: Chrome, Chromium, Edge or Brave. A
default install with none of them works exactly as before; the capability then says "No
Chromium-family browser was found" beside its switch, with what to install.

| System | One way to get one |
|---|---|
| macOS | `brew install --cask google-chrome`, or install Chrome, Edge or Brave by hand |
| Windows | Edge is already there. Or `winget install Google.Chrome` |
| Debian, Ubuntu | `sudo apt install chromium` (or Google's `google-chrome-stable` package) |
| Fedora | `sudo dnf install chromium` |

If the program is somewhere unusual, put its full path in `browser.executable`. A browser
installed as a confined package (a snap or a flatpak) may not be allowed to use a profile
folder outside its own area or to inherit the control pipe; if the agent is told "the
browser would not start", install the ordinary package instead.

**Why an installed browser and not Playwright.** Both were weighed.

| | Installed browser over the DevTools protocol | Playwright |
|---|---|---|
| Install size | Nothing to download if a browser is there | A wheel of roughly 35 MB with its own Node runtime, then a browser download of a few hundred MB |
| Licence | No new dependency. The browser is the owner's own | Apache-2.0, compatible, but one more thing to track |
| Default install | Untouched: nothing optional to install | Needs an extra and a second `playwright install` step |
| Systems | Run against a real browser on macOS only so far. Linux takes the same path as macOS (the pipe on descriptors 3 and 4). Windows uses the browser's `--remote-debugging-io-pipes` with inherited handles and is **written but not yet run on a Windows machine** | Well tested on all three |
| Control | Every start-up flag and every protocol call is ours, in two short files | A large layer we do not read |
| Cost | We own about 600 lines of protocol and page script, and must keep up if the protocol changes | Maintained for us |

The deciding points were the default install and the fence: the fence needs exact control
of how the browser is started (its proxy, no bypass for loopback, no name lookups of its
own, no debugging port), and with a pipe instead of a port nothing else on the machine can
attach to the agent's browser. `page.Driver` is the whole interface the rest of JimmyHQ
uses, so a Playwright driver could be added behind it later without touching a rule.

## The fence

Every request the browser makes goes through one place: `fence.FenceProxy`, a small
forward proxy on loopback that the browser is started with as its only way out
(`engine.flags`: `--proxy-server`, `--proxy-bypass-list=<-loopback>` so loopback is not
exempt, and `--host-resolver-rules` so the browser resolves no names itself). A page's own
address, a redirect, an image, a script, a frame, a background request and a web socket
are all the same thing there: one request for one host and port. Each is checked by
`Fence.check` before a byte leaves:

1. `shape`: only `http` and `https`, a readable host, no user name or password.
2. `hard_denied`: the owner's deny list.
3. `outside_allow_list`: for an agent with an allow-list, everything not on it.
4. `address`: [web lookup's address rules](lookup.md), unchanged. No loopback, private,
   link-local, tailnet or carrier-grade NAT address; never JimmyHQ's own host and port;
   the owner's `lookup.allow_private_hosts` exceptions apply. The name is resolved once,
   by the fence, and the proxy connects to the address that passed.

A redirect is a new request and is checked again. `open_page` runs the same check first,
so the agent gets a sentence instead of an error page.

**What the fence cannot see.** Inside an `https` connection the proxy knows the host and
the port, nothing else: not the path, not what is sent. So the deny list and the
allow-list are lists of hosts, and a rule like "this site, but not its sign-in page"
cannot be enforced there. What is sent is judged elsewhere:

- before a click or Enter, from the page itself (`fields.changes_something`);
- as it leaves, by method and body (`fence.may_send`, enforced through the protocol's
  request interception in `page.ChromeDriver`): at "Read only" nothing but GET, HEAD and
  OPTIONS without a body goes; at "Ask me each time" any other request, a page load or a
  page's own background request (fetch, XMLHttpRequest, a beacon), goes only in the few
  seconds after the owner approved a form on that page. This is what stops a page's own
  script from sending, whatever button started it. Frames from another site and workers
  are attached to as they start (`ChromeDriver._children`), so their requests are held
  the same way.

Other limits worth knowing:

- At "Ask me each time" and "Read only", a site that uses POST to read (many search and
  results pages do) cannot load those results: the request is held like any other that
  sends. The agent is told something was stopped.
- Data a page puts into an address and fetches with GET (a GET form, a link, a picture,
  a script setting the address) is not held at any level: at the network it looks the
  same as reading, and a search form is meant to work at "Read only". Nor is what a page
  sends over a web socket once it is open, which request interception does not see. A
  hostile page can use either to send what the agent typed into it.
- The driver looks at the main document. What is inside a frame is not listed and cannot
  be clicked or typed into.
- The method rule covers pages and workers the browser reports. The address rules cover
  everything, because they are the proxy.
- The fence is the browser's configuration and a proxy in the same process, not a
  container or the kernel. A browser bug that ignored its proxy would get past it.
- A page controls what it declares. A password box disguised as a plain one with no
  telling name is not recognised.

## The rules, by name

Each is a function with its own test (`tests/test_browser_*.py`), listed with the
setting it reads in `village/tools/browser.py` (`RULES`).

| Rule | Says |
|---|---|
| `browser_address_shape`, `browser_hard_deny`, `browser_allow_list`, `browser_public_address` | The fence, above |
| `browser_sending_needs_leave` | What a page may send, by method and level |
| `browser_no_credentials` | No typing into a credential box |
| `browser_submit_changes` | Which clicks and keys change something |
| `browser_changing_needs_leave`, `browser_submit_level` | The owner's level decides |
| `browser_card_shows_what_is_sent` | The card carries the exact fields and values |
| `browser_decision_trusted` | Only the owner's decision on the agent's own card counts |
| `browser_approval_fits_the_page` | An approval is used only for the page it was given for |
| `browser_pages_per_day`, `browser_actions_per_day`, `browser_requests_per_day` | Daily limits, counted from the log |
| `browser_repeat_same_page` | The same action on the same page is a repeat |
| `page_wall` (the world's) | After `village.guards.wall_after` fruitless actions, only leaving, going back or scrolling |
| `browser_view_budget` | The view is cut to its sizes, and says what was left out |
| `browser_picture_needs_a_model_that_sees`, `picture_fallback` | A model that refuses a picture gets the text view, and is told once |
| `browser_idle_stop` | An unused browser is closed |

## Testing

`tests/test_browser_real.py` starts a real headless browser against a throwaway server
on a loopback port, and is skipped with a reason when no browser is installed. Loopback is
what the fence refuses, so those tests tell the fence, through `Manager.also_allow`, to
let that one host and port past the address rule; nothing outside the tests sets it, and
one test checks that without it the same site is refused. Everything else runs through
`FakeDriver` (`tests/test_browser_fakes.py`), which has the driver's interface and sends
every request it pretends to make through the real `Fence.check` and `may_send`.

```bash
uv run pytest -n auto tests/test_browser*.py
```
