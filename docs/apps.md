# Apps

An app adds a screen, a tile or a skill to JimmyHQ. `server/jimmyhq/apps/` installs apps,
keeps the record of what the owner agreed to, and stands between a running app and
everything else. This page is for someone writing an app. If you only want to use Posts,
Reader, Articles or Packages, start with [First steps](first-steps.md).

**What works today:** described, sandboxed and full apps install and run. A full app's
program is started by JimmyHQ, talks to it through the app API, and can run jobs, put
cards of its own on the Bench and hear the owner's decision, offer Memory a source and
offer agents tools (see [A full app](#a-full-app)). Start one with `jimmyhq apps new`
(see [Developing](#developing)).

## The three kinds

| Kind | Is | Can do |
|---|---|---|
| Described | A manifest and nothing else: tables and views | Keep and show records. It has no code. |
| Sandboxed | A web page in a locked frame | Only what its permissions list, through the bridge |
| Full | A program that runs as the owner | Anything the owner can. JimmyHQ cannot limit it, and the install dialog says so. |

A described app is a sandboxed app whose every screen has `"entry": "described"`.

## The manifest

One file at the app's root, `jimmyhq.app.json`. An unknown key is an error, and every error
is a sentence that names the field.

```json
{"manifest": 1, "id": "water", "name": "Water", "summary": "Count glasses of water.",
 "version": "1.0.0", "tier": "sandboxed", "api": "1.0", "licence": "MIT",
 "permissions": ["tasks.write"],
 "screens": [{"id": "main", "label": "Water", "entry": "index.html"}],
 "settings": [{"key": "goal", "type": "int", "default": 8, "min": 1, "max": 30,
               "label": "Glasses a day", "help": "Your daily goal."}]}
```

- `id`: 2 to 31 lower-case letters, digits or underscores, starting with a letter. The app
  acts as `app:<id>` and owns the names `app.<id>.*` and the folder `<data>/apps/<id>/`.
- `version` is semver. `api` is the app API it needs, `"major.minor"`; this JimmyHQ speaks `1.0`.
- `tier` is `"sandboxed"` or `"full"`. `licence` is required.
- `icon` (optional): the picture beside the app's line in the sidebar, one of `book`,
  `calendar`, `camera`, `chart`, `heart`, `home`, `image`, `mail`, `megaphone`, `mic`,
  `newspaper`, `package`, `pen`, `truck`, `users`, `wallet`. JimmyHQ draws it itself: an app
  names a picture and never brings markup or an image, so any other word is refused. Left
  out, the app gets the usual window picture.
- `screens`, `dock`, `wall`: lists of `{id, label, entry}`. `entry` is an HTML file inside the
  app, or `"described"`.
- `settings`: declared as `app.<id>.<key>`, on a Settings page of the app's own, under
  Apps. The owner changes them there; an app reads them with `settings.get`. An app may declare up
  to 80. Mark a tuning number most people never need (a margin, a timeout, a cap on size)
  `"advanced": true` and the page keeps it behind "Show advanced settings"; never one about
  safety, permissions, privacy or money.
- `tables` and `views`: records (see [Described apps](#described-apps)).
- `secrets`: keys and tokens the app needs, `[{name, label, help}]`. The owner types each
  in on the app's own Settings page (see [Secrets](#secrets)); the program reads one with
  `secrets.get`.
- `owner_hosts`: `{"purpose": "Publish drafts to your own site.", "max": 3}` for an app
  that may ask the owner for addresses of his own, such as his own site's (see
  [Hosts the owner gives an app](#hosts-the-owner-gives-an-app)).
- `net_expects`: what the app expects of a host it names with `net:<host>`, such as
  `{"api.example.com": {"max_kb": 20480, "timeout_s": 180}}` (see
  [What an app expects of a host](#what-an-app-expects-of-a-host)).
- `storage_mb`: storage the app needs beyond the usual limit, in megabytes, such as `500`
  for an app that keeps pictures. The owner reads it at install (see
  [Pictures from the owner](#pictures-from-the-owner)).
- Full tier only: `server`, `cards`, `memory_sources`, `tools`, `private`, `card_subjects`
  (the subject words a private app's cards may use, see [A private app's card and
  task](#what-a-full-app-can-plug-into)), `roles` (see
  [Working with agents](#working-with-agents)), `feeds` (see
  [Feeds between apps](#feeds-between-apps)). Only a full app's program can read a secret.
- A tool's `effect` is one of `read`, `record`, `send`, `create`, `update`, `delete`,
  `spend`, `access`, `change`. Each tool's name and effect, and the tools each role
  offers, are part of what the owner agrees to (they are in the app's fingerprint). An
  update that offers a new tool, moves a tool to an effect the ladder guards less (from
  closest: `send`, `spend`, `delete` and `access`; then `create`, `update` and `change`;
  then `record`; then `read`), gives a role more tools, or adds a role an agent of the
  owner's already holds, asks the owner again, so it never goes in by itself even with
  `apps.auto_update` on "auto". A tool removed or guarded more, a role that offers fewer
  tools, a new role nobody holds yet, or new words for a tool ask nothing. An app that
  offers agents no tool keeps the fingerprint it had.

Permissions, and the sentence the owner reads for each:

| Permission | The owner reads |
|---|---|
| always | Keep its own data on this computer. |
| `tasks.read` | See your tasks. |
| `tasks.write` | Add tasks, and mark the ones it added as done. |
| `bench.file` | Put cards on your Bench for you to decide. It cannot decide for you. |
| `model.ask` | Ask your AI models, within your roles and limits, and show them pictures from its own data. If a role uses a hosted model, what it asks leaves this computer. |
| `web` | Search the web and read public pages, under your web lookup settings. What it searches for, and the addresses it reads, leave this computer. |
| `memory.recall` | Search what JimmyHQ remembers about you. |
| `speech.dictate` | Ask you to dictate: you see when it is recording, and it gets only the words. |
| `net:<host>` | Send data to and read data from `<host>`. (With `net_expects` for it: "It expects answers of up to 20 MB and to wait up to 3 minutes for one; the most JimmyHQ allows is yours to set under Settings, Apps.") |
| `owner_hosts` (its own key) | It may ask you for the address of your own site, and will be able to send to and read from the addresses you give it, *max* at most. It says why: "*purpose*" |
| `jobs` (full) | Work in the background on a schedule. |
| `tape.read` (full) | Read your whole record, not only its own events. |
| `feed:<app>.<feed>` (full) | Read "Articles" from Reader: *the feed's own sentence*. Nothing else of Reader's or on your record. (When that app is not installed: that it reads nothing until you install it.) |
| `memory.source` (full) | Offer what it collects to Memory. You switch each source on yourself. |
| `agents.tools` (full) | Offer tools to your agents. You choose which agents. |
| `inbound` (full) | Take messages from devices and services on your network, such as your home's hub. Each one needs a key you make for it on the app's page. |
| always, full | **Runs as you on this computer. It can read and change your files and use the network. JimmyHQ cannot limit this.** |

`apps/examples/` has `water` (sandboxed), `books_read` and `chores` (described), and
`watering` and `drafts` (full). They ship with JimmyHQ, are made by the JimmyHQ project,
and install from the Apps screen by name. `watering` is the one to read first: a job, a
card type with its own buttons, the owner's decision and a screen, in one short file.

First-party apps live beside them in `apps/` (today `posts` and `reader`, which also show
feeds between apps, `articles`, and `packages`, which looks parcels up through a tracker
file for each service). They are made by the
JimmyHQ project, ship with JimmyHQ the same way, and install from the Apps screen by
name under "Made by the JimmyHQ project". An app counts as coming with JimmyHQ only
when it was copied from one of those folders inside this copy of JimmyHQ and its files
are byte for byte what ships there; what its manifest says about who made it never
counts.

## A sandboxed app

The app's page runs in `<iframe sandbox="allow-scripts allow-forms">`. Its origin is opaque:
no cookies, no reach into JimmyHQ's page, no popups, no network of its own
(`connect-src 'none'`). The only way out is the bridge.

```html
<!doctype html><meta charset="utf-8"><title>Water</title>
<button id="add">One more glass</button> <p id="out"></p>
<script type="module" src="app.js"></script>
```

```js
import { hq } from "./_bridge.js";
const { timezone, week_start, theme, permissions } = await hq.ready;
const n = (await hq.storage.get("today")) ?? 0;
await hq.storage.set("today", n + 1);
await hq.tasks.create({ title: "Keep drinking water" });
```

`./_bridge.js` is supplied by JimmyHQ beside your files, so it always matches. So is
`./_ui.js`, a small page helper the first-party apps use: `h(tag, props, ...children)`
builds an element whose every piece of text is a text node (an attribute off its short
list, an `on...` that is not a function and a `src` that is not a `blob:` are left out),
and `say`, `dialog`, `ask` and `field` give the page its own messages and dialogs, never
the browser's; `dictateButton(hq, box, {hint})` is a Dictate button for a text box (see
[Dictation](#dictation)). Every call
returns a promise. A refusal rejects with an `Error` that has `code` (`not_allowed`,
`quota`, `invalid`, `not_found`, `too_large`, `unavailable`, `internal`, and `cancelled`
when the owner cancelled something the page asked for), a `message` that
says nothing happened and what to do instead, for a quota `retry_after_s`, and sometimes
an `owner_message`: the same said in words the owner can act on, written by JimmyHQ (never
taken from your app's program), such as where to set up a model. Show the owner
`why(error)` from `_ui.js`, which uses `owner_message` when there is one. A full app's own
refusal, the sentence its program raised, arrives as the `message` as it was written: there
is nothing in front of it to strip.

`hq.ready` resolves to `{app, version, theme, timezone, week_start, permissions, save}`. The
time zone (an IANA name) and the day the week starts on are the only two things an app is
told about the person using it. `save` is what the owner lets `hq.ui.save` do, so a page
can size an export before it tries: `{max_mb, parts, per_minute, html}`, which are the
owner's settings `apps.save_max_mb` (the most one file may weigh, in whole megabytes),
`apps.save_parts` (files from one click), `apps.save_per_minute` (files in a minute) and
`apps.save_html` (whether `text/html` is saved as a web page rather than as plain text).
An older JimmyHQ leaves `save` out, so read it as `ready.save ?? {}` and fall back to the
defaults (8, 5, 12, true); a page that never reads it is refused with `too_large` or
`quota` as before.

**An app is one page.** If the page loads a second time (a link to another page of yours,
a form that submits, `location.reload()`), JimmyHQ closes the bridge and takes the frame
down, because it can no longer tell what is in it. Change what is shown with script, and
keep what must last in `hq.storage` or `hq.records`.

Scripts and styles must be files in the app: inline `<script>` is blocked, inline styles
are allowed. Only web file types are served (HTML, JS, CSS, JSON, images, fonts, audio, video).

### The calls

| Call | Needs | Does |
|---|---|---|
| `storage.get/set/delete/keys` | | JSON values by key, in the app's own database |
| `records.list/get/create/update/delete` | | Rows of a table the manifest declares |
| `tasks.list` | `tasks.read` | The owner's open tasks (`{when: "open" | "today"}`) |
| `tasks.create` | `tasks.write` | A task for the owner, filed by the app |
| `tasks.complete` | `tasks.write` | Marks done a task **this app filed**. Any other is refused. |
| `bench.file` | `bench.file` | A card for the owner to decide. Filed by `app:<id>`, kind `app.<id>.<kind>`, source the app's name |
| `settings.get` | | A setting the manifest declared; with no key, all of them |
| `model.ask` | `model.ask` | `{prompt, system?, role?, json_schema?, images?, untrusted?, stay_local?, off_record?}` through the owner's roles and limits. With no model set up, the call is refused with `unavailable`, and its `owner_message` names the app and the empty role (the default role, the role you named, `vision` when you sent pictures, or `embeddings` for `model.embed`) and tells the owner to add one under Settings, Models; for a `private` app, or when the owner or your call held it to their own machines, it says the model must be on the owner's own machines |
| `model.embed` | `model.ask` | `{texts}`: one vector per text, from the owner's `embeddings` role |
| `web.search` | `web` | `{query, limit?}` through the owner's web lookup |
| `web.read` | `web` | `{url, start?, chars?}`: a piece of a public page as cleaned text |
| `memory.recall` | `memory.recall` | Searches the shared part of memory |
| `net.fetch` | `net:<host>` | An https request made by JimmyHQ to exactly that host, or to exactly an address on the owner's own network he added for the app (see [Addresses on the owner's own network](#addresses-on-the-owners-own-network)). Redirects are returned, not followed. A body may be built from the app's own files (see [Sending files](#sending-files)). `timeout_s` asks for a shorter wait than the owner's limit, never a longer one. JimmyHQ sets `Host`, `Cookie`, `User-Agent`, the length and connection headers itself, and never sends `Forwarded`, `X-Forwarded-*` or `X-Real-IP` from an app (refused with `invalid`). |
| `hosts.list` | | The hosts the owner gave the app himself: `{hosts, max, purpose}`. There is no call that adds one. |
| `speech.dictate`, `speech.available` | `speech.dictate` | `{hint?}`: the owner dictates on JimmyHQ's own surface and the page gets `{text}`; whether it can now: `{available, reason?}` (see [Dictation](#dictation)) |
| `files.upload`, `files.thumbnail`, `files.picture` | | A picture the owner picked, kept in the app's storage; one of its pictures back as a small Blob, `files.thumbnail(ref, size?)`, or large for a full view, `files.picture(ref, size?)` (see [Pictures from the owner](#pictures-from-the-owner)) |
| `ui.toast`, `ui.badge` | | A short message, and a count on the app's sidebar line. `ui.badge` is what the page says while its frame is open: it wins over the program's number (`ui.count`) for as long as the page is open, stays after the owner leaves the app, and is not updated while the app is closed. A full app should also say its number from its program, so the sidebar is right without opening the app |
| `ui.settings` | | Opens the app's own page under Settings, over the page that is showing, so nothing typed there is lost. It takes no arguments: the shell decides where it goes, never the app. It needs the owner's own click or touch on the page just before, and is refused otherwise. Use it for a button such as "Open settings", beside a sentence that says what to do there. |
| `ui.limits` | | What the owner lets an app do at the edges, as plain numbers, so the app sizes its work to them instead of finding them out by being refused: `{save_max_mb, save_parts, save_per_minute, save_html, call_max_kb, picture_px}`. They are `apps.save_max_mb` (the most one saved file may weigh, in whole megabytes), `apps.save_parts` (files from one click), `apps.save_per_minute` (files in a minute), `apps.save_html` (`true` when a web page is saved as a web page), `apps.call_max_kb` (the most one answer from a full app's program may weigh, in KB) and `apps.picture_px` (the longest side of a picture from `files.picture`), as they are when you ask. It needs no permission, takes no arguments and tells nothing else about the owner; the server answers it, so it is cheap but counts as a call. An older JimmyHQ refuses it: catch that and keep to the numbers your app always used. A full app's program asks with `app.limits()`. |
| `ui.save` | | `{name?, type?, text}` or `{name?, type?, bytes}` (or `{name?, blob}`): hands the owner a file to keep: text, a web page, or a picture. A page cannot download by itself (its frame is sandboxed without downloads), so the shell, outside the frame, makes the file and the browser saves it. The owner sees each one named, with its size, as it is saved. It needs the owner's own click or key press inside your page within the last 5 seconds. One click may save a few files in a row, for an export split into parts: up to `apps.save_parts` (5 by default, at most 10; Settings, Apps), the first within 5 seconds of the click, each next within 5 seconds of the one before, and the run over within 30 seconds of the click. After that, or after a pause, a save is refused until the owner touches the page again (a browser may also ask the owner whether to allow several downloads). However many clicks there are, one app saves at most `apps.save_per_minute` files (12 by default) in any minute; past that a save is refused with `quota` until the minute has moved on. (The shell cannot see inside your frame, so it takes a touch to be yours when the browser says there was one, your frame holds the focus, and the shell heard no click or key of its own since; asking again while one click's activation lasts is the same touch. A new touch, and so a new run, begins when the browser's activation was seen to end and begin again, or when the first one's 5 seconds have passed and the browser still says the tab was touched. What the page says never begins one: the bridge script sends `hq.ui.touch()` on each real click or key press, but a page can send that without any click, so the shell takes it only as a reason to look at the browser's activation now. So a second click within 5 seconds of the first is the same touch: it can save only what is left of that touch's `apps.save_parts` files, and a run that has used them all waits, at most 5 seconds, for the next click to be a new touch. Dictation and Settings follow the same touches. The per-minute limit holds however many touches there are.) A file may weigh at most `apps.save_max_mb` (8 MB by default), counted in bytes; more is refused with `too_large`, so split it into parts and save them one after another. **Text.** `text` is a string of at least one character. `type` is one of `text/plain` (`.txt`), `text/markdown` (`.md`), `text/csv` (`.csv`), `application/json` (`.json`) or `text/html` (`.html`); anything else is saved as plain text. **A web page.** `text/html` is saved as a download only, never opened or shown by JimmyHQ. Because a saved page opened later from the disk is no longer inside your frame's sandbox, JimmyHQ puts a Content-Security-Policy meta tag (and the HTML5 doctype and a character set) ahead of everything you wrote, which blocks every script (inline and remote), form submission, `<base>` and every load that is not a `data:` picture, font or media, and renames each `<meta` and `<link` tag you wrote (to `<x-meta`, `<x-link`) so a refresh or a prefetch hidden in one does nothing. So a saved page is a document, not a program: put its styles in a `<style>` and its pictures in `data:` addresses. What stays: a link in it goes where it points when the owner clicks it, and the owner is told, as the file is saved, that it is a web page saved with its scripts switched off (in full the first time your app saves one). The owner can turn web pages off (`apps.save_html`); `text/html` is then saved as plain text, `.txt`, as it used to be always. **Pictures.** `bytes` is an `ArrayBuffer` or a typed array (for a canvas, `await canvas.convertToBlob()` or `toBlob`, then `hq.ui.save({name, blob})`, which sends the Blob's bytes and takes its type), with `type` one of `image/png` (`.png`), `image/jpeg` (`.jpg`) or `image/webp` (`.webp`). The bytes are copied as they arrive and must start the way that picture type does, or the call is refused with `invalid`: a `.png` is never a page in disguise. Bytes cross the bridge as they are (several MB is fine); no base64 is needed. Bytes with any other type, a picture type with `text`, or both `text` and `bytes` are refused. `type` only picks the file's ending: JimmyHQ always gives the browser the file as an opaque download (`application/octet-stream`). `name` is made safe to be a file name anywhere (no path, no control, format or invisible characters, no leading dot, no device names, at most 100 characters before the ending) and is shown to the owner in quotes, shortened, after JimmyHQ's own words and always ends with the type's ending, so `report` as Markdown is `report.md`; with no usable name it is `file`. There is no address and no navigation: the shell makes the object address, clicks it and revokes it, and the page never sees it. It resolves `{name, bytes}` once the browser has been given the file; it cannot tell whether the owner then chose to keep it. Use it for a button such as "Save as a file", beside Copy. |

A page makes each call through the bridge script, one group to a name before the dot:
`hq.model.embed({texts})`, `hq.web.search({query})`, `hq.web.read({url})` and
`hq.hosts.list()` are the page's forms of `model.embed`, `web.search`, `web.read` and
`hosts.list`, with the permission each row above names. A page may also reach a host the
owner gave the app himself with `hq.net.fetch`, exactly as it reaches a `net:<host>`
host.

Who is asking is never an argument. The app is the one JimmyHQ mounted, and a call that
tries to say otherwise is refused. Events arrive with `hq.on(name, fn)`: `theme`,
`visible` and `settings.changed`, and for a full app whatever its own program sends with
`app.emit`. A page loads again by itself when a new copy of its app is put in place.

### Sending files

`net.fetch` can send files from the app's own data folder (`<data>/apps/<id>/data`)
without the app ever holding them as text: it names them by path, and JimmyHQ reads them
while it sends. The host is checked as for any fetch, first, so a file meant for a host
that was not granted is never opened. Three forms, each with `POST`, `PUT` or `PATCH`:

```js
// The file is the whole body, as for a signed upload address.
await hq.net.fetch({url: "https://uploads.example.com/put/123?sig=...", method: "PUT",
                    file: ref, headers: {"Content-Type": "image/jpeg"}});
// A form: text fields and file parts, in order.
await hq.net.fetch({url: "https://api.example.com/v1/media", method: "POST", multipart: [
  {name: "caption", value: "Morning light"},
  {name: "media", file: ref, type: "image/jpeg", filename: "morning.jpg"}]});
// JSON with the file filled in, as a data: URL or as plain base64.
await hq.net.fetch({url: "https://api.example.com/v1/posts", method: "POST", json: {
  caption: "Morning light", media: {"$file": ref, as: "data_url"}}});
```

- A raw body goes as `application/octet-stream` unless a `Content-Type` header is given;
  `"Content-Type": ""` sends none. A form's content type, with its boundary, is JimmyHQ's.
- A file part's `type`, and a data URL's, default to what a picture's bytes say.
- Each path is checked by the manifest's path rule (forward slashes, no `..`, no drive,
  no name Windows keeps for a device), walked refusing any link, and proven to stay inside
  the folder. The core's own `app.db` (and its `-wal`, `-shm` and `-journal` files) is never
  sent, however the name's letters are cased.
- Each file may weigh `apps.net_fetch_file_max_mb` (100) and the whole body, as sent,
  `apps.net_fetch_body_max_mb` (150); a file as base64 takes a third more. The length is
  worked out first and sent as `Content-Length`. Such a request may take
  `apps.net_fetch_file_timeout_s` (300).
- `fresh_connection: true` sends `Connection: close`, for a host that closes the
  connection after a large upload. JimmyHQ never reuses a connection between fetches.
- The receipt, `apps.file_sent`, names the host, the method, the form, each file's path
  and size, and the whole size: never what was in them or the other fields.

### What an app expects of a host

An answer from a host may weigh at most "Network answer size for a host an app says nothing
about (KB)" (`apps.net_fetch_default_kb`, 1024 to begin with) and take at most "Wait for a
host an app says nothing about (seconds)" (`apps.net_fetch_default_s`, 20). The owner's
ceilings above them are "Largest network answer for an app (KB)" (`apps.net_fetch_max_kb`,
100,000 to begin with) and "Longest wait for an app's network request (seconds)"
(`apps.net_fetch_timeout_s`, 300).
An app whose host sends large answers, or takes its time, such as an image service that
answers a picture as base64 JSON after a minute, says so in its manifest, for each host it
names:

```json
"permissions": ["net:images.example.com"],
"net_expects": {"images.example.com": {"max_kb": 20480, "timeout_s": 180}}
```

- `max_kb` (1 to 100,000) and `timeout_s` (1 to 3600) are each optional; at least one is
  given. A host not named in `permissions` is refused.
- **At install** the owner reads it on that host's line in plain words: "It expects
  answers of up to 20 MB and to wait up to 3 minutes for one; the most JimmyHQ allows is
  yours to set under Settings, Apps." It is not a new consent: changing it in an update
  asks nothing again.
- **It sets what that host gets, never past the owner's ceilings.** For that host, the most
  read is `max_kb`, and the longest wait `timeout_s`, each cut down to the owner's ceiling
  if that is lower (`apps.net_fetch_file_timeout_s` for a request that sends files). Without
  it, a host gets the owner's defaults above. `timeout_s` on a call may ask for less again,
  never more.
- **When the owner allows less than the app expects,** the app's page under Apps says so
  under "What it may do", with the setting to raise, and a fetch that runs into it is
  refused naming that setting: `too_large` ("larger than the 1024 KB the owner lets an
  app be given ("Largest network answer for an app (KB)", apps.net_fetch_max_kb) ... Your
  manifest expects up to 20480 KB") or `unavailable` for the wait, each with an
  `owner_message` for the owner. When it is the app's own `net_expects` that was too
  small, the sentence says so instead.
- The SDK waits for `app.net.fetch` as long as JimmyHQ may let it take, so the owner's
  limit is the one that ends a slow request.

### Pictures from the owner

A page can let the owner pick pictures and keep them in the app's own storage. The page
uses an ordinary `<input type="file">` and hands each file over from its change handler:

```js
input.addEventListener("change", async () => {
  for (const file of input.files) {
    const {ref, name, size, type} = await hq.files.upload(file);
    img.src = URL.createObjectURL(await hq.files.thumbnail(ref));
  }
});
```

- The frame never sends anything itself. The shell, in the owner's session, sends the
  bytes to `POST /api/apps/<id>/files` for the app it mounted, and refuses a hand-over
  that does not follow the owner's own touch of the page where the browser can tell.
- Only pictures are kept: PNG, JPEG, GIF, WebP, AVIF and HEIC, read from their first
  bytes, never from the name or the type the browser declared. SVG is not taken.
- One picture may weigh `apps.upload_max_mb` (25), and pictures count towards the app's
  storage with its values and records. Over either, or when it is not a picture, nothing
  is left on disk.
- `ref` is `uploads/<random>.<ending>`. `name` is the picture's name made safe to use as
  a file name anywhere; it is never used as one. A name with a slash is refused.
- `hq.files.thumbnail(ref, size?)` resolves to a Blob of a small version of the picture.
  The shell fetches the picture from `GET /api/apps/<id>/files/<file>` for this app only
  (JimmyHQ's own screens show a picture from the same address, full size) and makes the
  small one in the browser before handing it over: the core has no image library, and
  none was added. The longest side is `apps.thumbnail_px` (320, between 64 and 2048,
  under Settings, Apps, advanced); `size` may ask for fewer pixels, never more. Nothing is
  kept: the picture in the app's storage is not changed, and a small one is made again
  each time the page asks, so keep the Blob URL you make from it. The picture's
  orientation is respected; an animated GIF comes back as its first frame; JPEG stays
  JPEG and the rest come back as PNG. A picture that is already small enough, one the
  browser cannot decode (HEIC in most browsers) and one whose small version would not be
  smaller come back as they are. A picture's size in pixels is read from its first bytes
  before anything is decoded: more than 64 million pixels, or a side over 32,768, is
  refused with `too_large` and nothing is shown. AVIF and HEIC are not read that way and
  are handed back untouched.
- `hq.files.picture(ref, size?)` is the same picture large, for a full view: the same
  path as a thumbnail (the shell fetches it for this app only, reads its size in pixels
  before decoding, refuses more than 64 million pixels, draws it in the browser), with
  larger limits. The longest side is `apps.picture_px` (2048, between 64 and 8192); `size`
  may ask for fewer pixels, never more. A kept picture heavier than `apps.picture_max_mb`
  (25) is not read: the call is refused with `too_large`, naming the setting, and nothing
  is shown. One open screen may ask for `apps.pictures_per_minute` (60) full views in a
  minute, made one at a time; more is refused with `quota` and `retry_after_s`. The
  thumbnail and its setting are unchanged, so a gallery shows thumbnails and opens one
  picture with `files.picture`:

  ```js
  big.src = URL.createObjectURL(await hq.files.picture(ref));
  ```
- A full app's program opens it with `app.files.path(ref)` or `app.files.open(ref)`; any
  app sends it on with `net.fetch` and `file: ref`. A picture the program writes itself
  under `uploads/` as `<32 hex>.<ending>` is shown the same way: the ref is all a page
  needs.
- The receipt, `apps.file_received`, says which app, the ref, the size and the type, not
  the picture's own name.

A pictures app needs more than the usual 50 MB: it asks in its manifest with
`"storage_mb": 500`. The install dialog says "Keep up to 500 MB of its own data ...";
installing gives the app its own line in "Limits for one app" under Settings, Apps
(`apps.quota_overrides`, `posts.storage_mb=500`), where the owner changes it afterwards.
An update that asks for more says so and is given it only when the owner presses Update.

### Dictation

A page's frame has no microphone, and never gets one. To let the owner speak instead of
type, the page asks JimmyHQ, which records on its own surface over the app and hands back
only the words:

```js
button.disabled = !(await hq.speech.available()).available;
button.onclick = async () => {
  try {
    const {text} = await hq.speech.dictate({hint: "What happened today?"});
    box.value += text + " ";
  } catch (error) {
    if (error.code !== "cancelled") say(why(error), "bad");
  }
};
```

`dictateButton(hq, box, {hint})` from `./_ui.js` does all of this: it greys itself out
with the reason, puts the words in at the cursor with a space after, and sends the box an
`input` event.

- It needs the permission `speech.dictate` and the owner's own click or key press in your
  page within the last 5 seconds, as `ui.save` does; one click is one dictation.
- The surface is the shell's: it names your app, shows `hint` as plain text (one line,
  control and direction characters removed, at most 120 characters), says "Recording…
  press Stop when you are done", shows a clock, Stop and Cancel; Escape cancels. It covers
  your frame while it is open.
- The promise answers nothing while recording. It resolves `{text}` (empty when no speech
  was heard) once the words are ready, and rejects with `cancelled` when the owner cancels.
  Your page never gets the audio and cannot start, stop or watch the recording.
- One recording at a time in the whole of JimmyHQ: a call while any recording is open
  (another app's, or one of JimmyHQ's own dictate buttons) is refused with `quota`.
- The shell uses its own recorder and `POST /api/speech/transcribe` with `app=<id>`, where
  the server checks the app, its permission and its privacy again. The caller is
  `app.<id>`, so a hosted speech server needs `app.<id>` in "Allowed to use a hosted speech
  server" ([Speech](speech.md)). The receipt (`speech.transcribed`) holds the engine, the
  caller and the length, never the words.
- A recording stops by itself after `apps.dictate_max_s` (300 seconds, under Settings,
  Apps, "What an app may use"), or the Voice limit if that is shorter.
- With dictation off or not set up, the call is refused and `available()` says to turn it
  on under Settings, Voice.
- **A private app** dictates only when the words stay on the owner's own machines: Whisper
  or Parakeet, or a speech server on this computer or one the owner marked "It is mine". A
  server JimmyHQ guesses is local from its address alone (an address on the home network)
  does not count. Otherwise the call is refused, `available()` says why, and nothing is
  recorded or sent.

### Secrets

An app that needs a key or token for an outside service declares it:
`"secrets": [{"name": "booking_key", "label": "Booking key", "help": "From your account page."}]`.
The owner types it in on the app's own page under Settings, Apps: one field for each,
with its label and help, that says only "Set" or "Not set". Clear asks first.

- `PUT /api/apps/<id>/secrets/<name>` `{value}` and `DELETE` the same address: the
  signed-in owner only, from JimmyHQ's own page. A token, an app's program and an app's
  frame are refused. A name the manifest does not declare is refused.
- The value goes to JimmyHQ's secret store as `app.<id>.<name>` and nowhere else. No
  answer, log line or event holds it: `GET /api/apps/<id>/secrets` says set or not set,
  and the receipt, `apps.secret_changed`, which secret and whether it was set or cleared.
- Only the app's own program reads it, with `secrets.get` (`app.secrets.get(name)`); an
  app's page never can.
- A secret is one line of up to `apps.app_key_max_chars` (4000) characters; the spaces and
  line ending of a paste are taken off.
- Removing the app removes its secrets, whether its data is kept or deleted. Kept data is
  there after a reinstall; the secrets are typed in again.

### Hosts the owner gives an app

An app that publishes to the owner's own site cannot name its host in the manifest: only
the owner knows it. It declares instead that it may ask him for hosts of his choosing,
for one purpose, up to a small number (1 to 5):

```json
"owner_hosts": {"purpose": "Publish drafts to your own site.", "max": 3,
                "next": "Then open the app and choose it."},
"secrets": [{"name": "site_user", "label": "User name", "help": "...", "for_each": "owner_host"},
            {"name": "site_password", "label": "Application password", "help": "...",
             "for_each": "owner_host"}]
```

- **`next`** is optional: one plain sentence (up to 200 characters) the app's Settings page
  shows under "Your own addresses", saying what to do once an address is added. It is not
  part of what the owner agrees to, so changing it never asks again. A page can offer a
  button to that Settings page with `hq.ui.settings()`.
- **At install** the owner reads it as its own sentence, with the app's purpose and the
  number, and his consent covers both: an update that adds the key, changes the purpose
  or raises the number asks again.
- **On the app's page** under Settings, Apps, the owner adds an address and removes one,
  each after a question in JimmyHQ's own dialog (`POST /api/apps/<id>/hosts {host}`,
  `DELETE /api/apps/<id>/hosts/<host>`; the signed-in owner only, from JimmyHQ's own
  page). The app can list them (`hosts.list`, `app.hosts.list()`) and never add one.
- **What is taken:** https only (or a bare name), one exact host name, nothing after it:
  no wildcard, no address written as numbers, no port, path or sign-in part, no name that
  only works on one network (`localhost`, `.local`, `ts.net` and the rest of the manifest's
  rule), never a name JimmyHQ itself is reached by. The name is resolved when it is added
  and every address it gives must be public (web lookup's rule: no loopback, private,
  link-local, carrier-grade NAT, which is where tailnets live, or reserved range), and an
  exception the owner made for web lookup does not count.
- **What it gives:** `net.fetch` treats it exactly as a host the manifest names, nothing
  more. Each request resolves and checks the name again, so a name that later points at a
  private address reaches nothing.
- **On the record:** `apps.host_added` and `apps.host_removed`. Every process reads them
  afresh on every call, so a host removed in one is gone in all. Removing the app drops
  them, and its secrets; a reinstall starts with none. An update or rollback to a version
  that no longer declares `owner_hosts` removes them, and one that allows fewer keeps the
  oldest.
- **A secret for each host.** `"for_each": "owner_host"` keeps one value per granted host,
  typed in beside its address and write only, like any secret
  (`PUT /api/apps/<id>/hosts/<host>/secrets/<name>`). The program reads it with
  `secrets.get {name, host}` (`app.secrets.get(name, host=host)`), only for a host it holds
  now; removing the host clears it. The receipt, `apps.host_secret_changed`, says which and
  for which host, never the value.
- **Testing:** `FakeCore(owner_hosts={...})`, `core.grant_host(host, secrets={...})`,
  `core.remove_host(host)`.

### Addresses on the owner's own network

An app reaches public https hosts only: an address on the owner's own machine or network
is refused, whatever a name points at, so an app cannot probe his router, his printer or
JimmyHQ itself. That holds for a host the manifest names (`net:<host>`) as much as for one
the owner gave the app, and a host the owner let web lookup read on his network
(`lookup.allow_private_hosts`) is no exception: that setting is for web lookup only, and
`net.fetch` refuses such an address with `not_allowed`, pointing to "Addresses on your own
network". A service the owner wants an app to use may live exactly there, such as an
image server on another computer at home, on a tailnet, or on this computer. For that, the
owner, and only the owner, adds one exact address for one app:

- **Where:** the app's page under Apps, "Addresses on your own network". He types the
  scheme, host and port, such as `http://192.0.2.10:8000`, `http://localhost:8000` or
  `https://example-machine.example-tailnet.ts.net:8443`. JimmyHQ resolves the name then, and
  its own dialog warns him plainly that the app will be able to send to and read from that
  address on his network, exactly that address and port and nothing else there, and shows
  the addresses the name leads to just now. Those addresses are stored with it. A name that
  cannot be found is refused. The signed-in owner only, from JimmyHQ's own page:
  - `GET /api/apps/<id>/local-addresses`: `{max, warning, addresses: [{address, key,
    added_at, resolved}]}`.
  - `POST /api/apps/<id>/local-addresses/check {address}`: where it leads now, `{address,
    key, resolved, warning, added, stored}`; nothing is recorded.
  - `POST /api/apps/<id>/local-addresses {address, resolved}`: adds it, `resolved` being
    exactly the addresses he was shown; refused if the name leads anywhere else by then.
  - `POST /api/apps/<id>/local-addresses/<key>/confirm {resolved}`: he agrees to where a
    name he added leads now (see below).
  - `DELETE /api/apps/<id>/local-addresses/<key>`.
- **The app cannot add or ask for one.** No call adds one, its manifest says nothing about
  it, and a token or a frame is refused at the routes. An app tells the owner which address
  to add in its own words, beside the setting where he types it for the app (Imagery's
  "Your server's address", say), then sends to it with `net.fetch` like any other:

  ```python
  base = app.settings.get("server_address")  # "http://192.0.2.10:8000", typed by the owner
  reply = app.net.fetch(f"{base}/v1/images/generations", method="POST", json=body)
  ```
- **Exactly that address.** The request's scheme, host and port must be exactly one the
  owner added; another port, `https` for `http`, another name for the same computer, or a
  neighbour is refused. Redirects are handed back, never followed. A host the app may also
  reach the ordinary way (`https://<host>` on the standard port, with `net:<host>` or given
  by the owner) goes the ordinary way while it leads to public addresses; once it leads to
  his own network, it is held to the rules here, and only if he added exactly
  `https://<host>`.
- **Checked again at every request.** The name is resolved again, every rule here is
  checked again, and every address it gives now must be one of those stored when the owner
  added it: whoever runs the name's DNS cannot point it at another machine on his network.
  If it leads anywhere else, `net.fetch` is refused with `not_allowed` ("...now points
  somewhere else..."), until the owner presses Check beside it on the app's page and agrees
  to the new address (`apps.local_address_confirmed`). The request connects to the address
  that was checked and nowhere else, and the receipt (`apps.fetched`) names it in
  `address`. A public-looking name the owner did not add never reaches a private address,
  even one he added: only the exact name he typed is let through.
- **This computer** (loopback) only when he typed `localhost` or a loopback address such as
  `127.0.0.1` or `[::1]`: never through another name that leads here.
- **Never:** a cloud computer's metadata service (all of link-local, `fd00:ec2::254`,
  `fd20:ce::254`, `fd00:c1::a9fe:a9fe`, and the other known addresses and names, in any form
  an IPv6 address can carry: mapped, 6to4, Teredo, NAT64 including a network's own
  `64:ff9b:1::/48`, `::ffff:0:a.b.c.d`, ISATAP, and the last 32 bits of any IPv6 address);
  JimmyHQ itself: its own port and the door's own port (while the door is set to listen
  there) on any host, and any name JimmyHQ is reached by (its allowed hosts, the tailnet
  name remote access added, the door's names) on any port; on this computer, a port this
  JimmyHQ listens on just now (each agent browser's fence proxy); or an address on the
  public internet (that is an ordinary host, over https).
- **What JimmyHQ cannot know.** A port another program opened on this computer is not
  JimmyHQ's own to know of, even when it serves JimmyHQ: the web dev server (`npm run dev`)
  while JimmyHQ is being worked on, a reverse proxy or tunnel in front of it on another
  port, or a JimmyHQ started separately. `localhost` on such a port is not refused. Add only
  a port you know is the service the app needs.
- **An update keeps them only within its tier.** An update or a rollback to a version of the
  same tier keeps the addresses, and its dialog lists them; one that changes the tier takes
  them all back (its dialog says so), and the owner adds them again if he wants.
- **How many:** `apps.local_addresses_max` (3) for each app.
- **On the record:** `apps.local_address_added` (with `resolved`),
  `apps.local_address_confirmed` and `apps.local_address_removed`, by the owner. Every process reads them afresh on every call; removing the app drops them. A page
  whose screen is open when one is added sees it after the screen is opened again. A
  `private` app is held to the same rule.

### Pictures, similarity and the web

Each of these is an ordinary call: the permission is checked on every call, the owner's
limits count it, and it leaves the same receipt on the record as when JimmyHQ does it
for itself. A sandboxed page will reach them once the shell's bridge lists them; a full
app's program reaches them now.

**Pictures.** `model.ask` takes `images`: paths inside the app's own data folder
(`<data>/apps/<id>/data`, which a program knows as `app.data_dir`), with forward slashes,
such as `"uploads/photo.jpg"`. PNG, JPEG and WebP only. A path that is absolute, names a
drive, climbs with `..`, is or passes through a link, or is an address, is refused, and
nothing is sent. Pictures go to the owner's `vision` role: leave `role` out, or set it to
`vision`. That role never borrows the default role's models, so if the owner has not
chosen one the app is told so in a sentence. The model layer's size limits
(`models.max_image_bytes`, `models.max_total_image_bytes`) apply, and at most
`apps.quota.images_per_ask` pictures go in one call. Picture data is never kept: the
receipt says how many and how large. A private app's pictures, like its prompts, go only
to a model on the owner's own machines; under "Sandboxed apps use local models only" a
sandboxed app's do too.

```python
reply = app.model.ask("What plant is this?", images=["uploads/leaf.jpg"])
```

**Similarity.** `model.embed {texts}` returns `{vectors, model, local}`, one vector per
text in order, from the owner's `embeddings` role, under the same local-only rules as
`model.ask` and the same permission. Vectors from two models cannot be compared: keep
`model` beside any you store, and make them again when it changes. The SDK compares them
for you:

```python
from jimmyhq_app import similar

found = app.model.embed([new_title, *old_titles])
for index, score in similar(found.vectors[0], found.vectors[1:], least=0.9):
    print("looks like", old_titles[index], score)
```

**The web.** With `web`, `web.search {query, limit?}` returns `{results: [{title, url,
snippet, text}], untrusted: true}` and `web.read {url, start?, chars?}` returns `{url,
final_url, title, text, start, end, total, next, truncated, links, untrusted: true}`.
Both go through JimmyHQ's web lookup and keep every one of its rules: nothing happens
while the owner has web lookup switched off; an address on the owner's own machine or
private network is refused (a redirect to one too) unless the owner named that host
under Settings, Web lookup; the owner's search provider, results per search, page size
and wait apply; and each search and page leaves a `lookup.searched` or `lookup.fetched`
receipt as `app:<id>` (a private app's carries a keyed hash in place of what it asked).
The app's own daily numbers are `web_searches_per_day` and `web_reads_per_day`.

A page is read a piece at a time: `text` is at most the owner's "Characters of a page a
model sees", and `next` is the `start` that reads on, or `null` at the end. The page is
fetched once and kept in memory for `apps.web_read_keep_minutes`, so reading on costs no
new request. In the SDK, `app.web.read_all(url)` reads it to the end.

**What comes from the web is data, not instructions.** Never paste it into a prompt.
Pass it to the model as `untrusted`:

```python
page = app.web.read(url)
reply = app.model.ask("Summarise this article in three sentences.", untrusted=page.text)
```

JimmyHQ puts each piece after the prompt between two lines carrying a mark made for that
one call, under a sentence telling the model that what is between them is data to weigh
and never instructions. The text cannot forge the closing line: it never saw the mark,
and the mark is cut out of it. Do the same with anything else from outside: an email, a
file someone sent, another app's text.

**Stricter for one call.** `stay_local: true` lets only a model on the owner's own machines
answer that call; with none set up for the role it is refused with a sentence and nothing
is sent anywhere. `off_record: true` keeps the call's token counts on the record and never
its text (no prompt or answer in the Vault). Use them for what the owner gave the app in
confidence, such as pasted email replies. Each may only be `true`: a call can make itself
stricter than the owner's settings, never looser. In the SDK, `app.model.ask(...,
stay_local=True, off_record=True)`; `FakeCore.local_model = False` plays an owner with no
local model.

## Described apps

The smallest app is a manifest alone. JimmyHQ draws the screen itself, as a table, a board
(when a view groups by a choice field) or a calendar (when the table has a date).

```json
{"manifest": 1, "id": "books_read", "name": "Books read", "summary": "A reading log.",
 "version": "1.0.0", "tier": "sandboxed", "api": "1.0", "licence": "MIT", "permissions": [],
 "screens": [{"id": "main", "label": "Books read", "entry": "described"}],
 "tables": {"book": {"fields": {
   "title": {"type": "text", "required": true}, "finished": {"type": "date"},
   "stars": {"type": "int", "min": 1, "max": 5}, "pages": {"type": "int", "min": 1},
   "paid": {"type": "money", "currency": "USD", "min": 0},
   "per_page": {"type": "computed", "formula": "paid / pages"}}}},
 "views": [{"id": "all", "label": "All", "table": "book", "sort": ["-finished"],
            "totals": {"stars": "avg"}}]}
```

Field types: `text`, `int`, `number`, `bool`, `date` (`YYYY-MM-DD`), `choice` (with
`options`), `money` (whole minor units, with `currency`), `link` (the id of a record in
`table`) and `computed` (a `formula` of numbers, numeric field names, `+ - * /` and
brackets; worked out on reading, never stored). `was` on a table or field lists its earlier
names, so a rename keeps its records. A view has `table` and may have `fields`, `filter`,
`sort`, `group` and `totals` (`sum`, `avg`, `min`, `max`, `count`).

A described screen is not put in a frame. A frame exists to contain an app's code, and a
described app has none: what runs is JimmyHQ's own renderer, which shows every value as
text. Its records still go through the bridge as `app:<id>`, under the same limits.

### Records

The same five calls serve a described app's screen and a sandboxed app's own page
(`hq.records.*`), with one shape:

```js
await hq.records.create({ table: "book", values: { title: "Dune", stars: 5 } });
// -> { record: { id: 12, label: "Dune", values: { title: "Dune", stars: 5, ... },
//                created: 1795000000000, updated: 1795000000000 }, labels: {} }
await hq.records.list({ table: "book", view: "all", sort: ["-stars"], limit: 50,
                        filter: [{ field: "stars", op: "ge", value: 4 }], search: "du" });
// -> { records: [...], total: 1, next: null, labels: {}, totals: { stars: 5 } }
await hq.records.update({ table: "book", id: 12, values: { stars: 4 } });
await hq.records.delete({ table: "book", id: 12, unlink: false });
```

- `id` is a whole number given by JimmyHQ, counted across all the app's tables and never
  used twice. `created` and `updated` are unix milliseconds, UTC. They sit beside
  `values`, so a field may have any name.
- `values` holds every declared field: empty ones are `null`, computed ones are worked out.
- `labels` gives, for each link field, the label of each record pointed at.
- Filter ops: `eq`, `ne`, `lt`, `le`, `gt`, `ge`, `contains`, `empty`, `not_empty`. Pass
  `next` back as `after` for the following page.
- A record others link to is not deleted unless `unlink` is true, which clears those links.

Records live in `<data>/apps/<id>/data/app.db` with `secure_delete` on. They are never on
the Tape.

## Installing

From the Apps screen, or `jimmyhq apps install <folder, https address or id> [--commit <hash>]`.

- **A folder** on this computer. It is copied; later changes to the folder change nothing.
- **A git address**: https on any host, at a full 40-character commit. A branch or tag is
  looked up once and the commit it names is pinned. No checkout happens and no hook,
  filter or submodule can run; links in the tree are refused.
- **An app that ships with JimmyHQ** (an example or a first-party app), by its id:
  `jimmyhq apps install water`. A folder of that name where you run the command is
  taken as the folder instead.

Installing is two steps, and nothing of the app runs before the second. The plan shows
who the app says it is, where it is from, each permission as a sentence and what it adds.
Confirming installs exactly the files that were shown. A full app that nobody has reviewed
also needs its name typed. Removing keeps the app's data unless the owner chooses to
delete it; kept data is there again after a reinstall.

### Updating, rolling back, removing

- **Finding an update.** Every `apps.update_check_hours` (24) JimmyHQ asks the git address
  of an app installed with a branch or tag which commit that name points at now, and
  compares an app copied from an example with the copy that ships. Nothing is fetched by
  looking. The Apps screen then says "Update available". You can also update from any
  folder or address yourself.
- **Updating** is two steps like installing. The new version is staged beside the old,
  checked the same way, and the dialog lists only what it asks for that was not agreed
  before (and a change of tier). Nothing installs itself unless `apps.auto_update`, or
  the app's own `app.<id>.auto_update`, says so, and even then a version that asks for
  more waits for the owner.
- **Switching** stops the program and waits until it has gone, copies `data/` to
  `backups/<old version>/`, then starts the new version. The last `apps.keep_versions` (2)
  versions are kept with their environments and backups.
- **Roll back** switches to a kept version and can put back the data copied when it was
  replaced. Its permissions are again the ones agreed to for it.
- **Its own environment.** A full app that ships `pyproject.toml` and `uv.lock` gets one
  per version from `uv sync --frozen --no-dev --no-build --no-install-project`: exactly
  the locked hashes, wheels only, so no build script runs. uv is found from
  `apps.uv_path`, then `PATH`; without it the app runs with JimmyHQ's own Python and its
  page says so.
- **Removing with the data deleted** deletes its data folder (its private store too),
  every backup and its log files, and erases what it wrote on the Tape: every event
  printed as `app:<id>` and every event of its own kinds keeps its place and loses what it
  said. The record that you installed, updated and removed it stays. What it cannot
  reach: a copy of the data folder made outside JimmyHQ, what Memory already learned from
  its source, Vault files it put there and never named on the Tape, and what it sent
  elsewhere.

### The routes

Words about all apps start with an underscore, which an id never can, so no app can take
the place of one.

| Route | Who | |
|---|---|---|
| `GET /api/apps` | read | What the shell draws: ids, names, screens, whether each is on |
| `GET /api/apps/_installed` | owner | Every app with where it came from |
| `GET /api/apps/_examples` | owner | The examples that ship |
| `POST /api/apps/_install/plan` | owner | `{folder}`, `{url, commit}` or `{url, ref}`, or `{example}` |
| `POST /api/apps/_install/confirm` | owner | `{plan, fingerprint, typed_name?}` |
| `POST /api/apps/_install/cancel` | owner | `{plan}` |
| `GET /api/apps/<id>` | owner | One app, with what was consented to |
| `GET /api/apps/<id>/described` | owner | Its tables and views |
| `POST /api/apps/<id>/enable`, `/disable` | owner | |
| `POST /api/apps/<id>/remove` | owner | `{keep_data?, erase_record?}`; deleting the data erases the record too unless `erase_record` is false |
| `POST /api/apps/<id>/update/plan` | owner | `{}` for the update found, `{folder}`, `{url?, commit}` or `{url?, ref}`, or `{example: true}` |
| `POST /api/apps/<id>/update/confirm` | owner | `{plan, fingerprint, typed_name?}` |
| `POST /api/apps/<id>/update/check` | owner | Look for a newer version now |
| `POST /api/apps/<id>/rollback` | owner | `{version: "<version>-<hash8>", restore_data?}` |
| `POST /api/apps/<id>/mount` | owner | `{screen?}`; answers the frame's address and what the page is told |
| `POST /api/apps/<id>/bridge/<call>` | owner, from JimmyHQ's own page | `{id?, call?, args}`; always 200 with `{id, ok, result}` or `{id, ok: false, error}` |
| `GET /api/apps/<id>/secrets` | owner, from JimmyHQ's own page | `{secrets: [{name, label, help, set}]}`, never a value |
| `PUT`, `DELETE /api/apps/<id>/secrets/<name>` | owner, from JimmyHQ's own page | `{value}`; set or clear one declared secret |
| `GET`, `POST /api/apps/<id>/hosts`; `DELETE /api/apps/<id>/hosts/<host>` | owner, from JimmyHQ's own page | the hosts the owner gave it; `{host}` adds one |
| `PUT`, `DELETE /api/apps/<id>/hosts/<host>/secrets/<name>` | owner, from JimmyHQ's own page | `{value}`; a secret kept for that host |
| `POST /api/apps/<id>/files` | owner, from JimmyHQ's own page | a picture's bytes, its name in `X-JimmyHQ-File-Name`; `{ref, name, size, type}` |
| `GET /api/apps/<id>/files/<file>` | owner, from JimmyHQ's own page | one of the app's pictures |
| `GET /appfiles/<mount>/<path>` | the mount in the address | An app's own files, for its frame |

A token cannot install, remove, switch, mount or relay a call, whatever its scopes.

## Limits

All are settings under Settings, Apps (`apps.quota.<name>`). One app's can differ with a
line in `apps.quota_overrides` such as `water.model_calls_per_hour=100`.

| Limit | Default |
|---|---|
| Storage for each app | 50 MB |
| One stored value or request | 256 KB |
| Records for each app; in one table | 20,000; 50,000 |
| One record; one text field | 64 KB; 2,000 characters |
| Requests an app makes to JimmyHQ | 20 a second, 600 a minute, 50,000 a day |
| Model calls; tokens | 30 an hour, 200 a day; 200,000 tokens a day |
| Network requests | 60 an hour, 3 under way at once for each app (`apps.net_fetch_at_once`, up to 50; one more is refused with `quota`), 1 MB an answer (`apps.net_fetch_default_kb`) and 20 seconds a wait (`apps.net_fetch_default_s`) from a host the manifest says nothing about, more where `net_expects` says so, up to the ceilings `apps.net_fetch_max_kb` (up to 100,000 KB) and `apps.net_fetch_timeout_s` (up to 300) |
| Files sent | 100 MB a file, 150 MB a request |
| One picture the owner gives an app | 25 MB, counted in its storage |
| One file an app hands the owner to keep (`ui.save`); files from one click; files in a minute | 8 MB (`apps.save_max_mb`); 5, at most 10 (`apps.save_parts`); 12 (`apps.save_per_minute`) |
| The wait for a full app's program to answer its page (`app.call`) | 30 seconds (`apps.call_timeout_s`, 1 to 300) |

| Similarity calls | 120 an hour; 64 texts of at most 8,000 characters each a call |
| Pictures in one model call | 8 (and the model layer's own size limits) |
| Web searches; pages read | 50 a day; 200 a day |
| Feed reads; items in one read | 1,000 a day; 100 |
| Open Bench cards; new tasks | 10; 50 a day |

Over a limit, a call is refused with `quota` and nothing is done. Counts survive a restart.

## What an app can never do

A sandboxed or described app cannot:

- reach the network itself, or any host the owner did not grant;
- read JimmyHQ's page, cookies, session or any `/api/` route;
- read another app's data, or name another app in a call;
- decide a Bench card, complete a task it did not file, or say who filed something;
- read the Tape, change a setting, or read a setting it did not declare;
- learn anything about the owner beyond their time zone and week start;
- send a model a file from outside its own data folder, or read the web without `web`
  while the owner's web lookup is on;
- navigate itself somewhere else: the shell frames only this server
  (`frame-src 'self'`), and this server answers only an app's files into a frame;
- keep working after it is switched off: every call is refused and its files stop being served.

What it sends is data. Text in a task or a card is never read as an instruction, and only
the owner decides. Two limits are known and stated: a granted host receives what the app
sends it, and a determined page may leak a few bytes through browser gaps such as DNS prefetch.

## A full app

A full app is a Python program that runs as the owner. JimmyHQ starts it, stops it and
restarts it; it talks to JimmyHQ only over local HTTP with a token of its own, never by
importing JimmyHQ. Write it with the SDK in `sdk/python/jimmyhq_app` (needs only `httpx`
and `pydantic`):

```python
from jimmyhq_app import App, Payload

app = App()

@app.kind("drafted", example={"post": 1})
class Drafted(Payload):
    post: int

@app.job("draft", every_setting="draft_minutes")
def draft():
    text = app.model.ask("Draft a short post about this week's work.").text
    card = app.bench.file("draft", "Draft ready", body=text, subject="post:1")
    app.tape.print("drafted", {"post": 1}, subject="post:1")

@app.call("board")
def board(args):
    return []

app.run()
```

Helpers both first-party apps use, for any app: `jimmyhq_app.text` (`clean`,
`split_sentences`, `numbered`, `sentences_page`, `excerpt`: an article's cleaned text and
its sentences, numbered the same way by every app that reads it),
`jimmyhq_app.word_runs`/`word_share`/`word_overlap` (comparing texts by shared three-word
runs, with no model), `jimmyhq_app.claims` (`find_numbers`, `number_matches`, `where_number`,
`quoted`, `where_quote`: a draft's numbers and quotations found in its sources by one rule,
with the shared cases `testing.NUMBER_CASES` and `testing.QUOTE_CASES`), and `jimmyhq_app.settings.ManifestSettings` (an app's settings read
in one place with its manifest's defaults, and `pair` for two levels that must stay the
right way round). Three more are for apps that handle text from outside:
`jimmyhq_app.sheets` (`guard` writes a CSV cell so a spreadsheet will not run it as a formula,
and `unguard` reads it back exactly as it was written), `jimmyhq_app.grounding`
(`shares_words(suggestion, quote)`: whether words a model suggested rest on the words it
quoted, by their content words, the same rule in every app) and `jimmyhq_app.keybind`
(`KeyBinding(app, name).allows(key, origin)`: a key typed for a server on the owner's own
network goes to the address it was first sent to and no other; a key for a host the owner added
under Your own addresses needs none of this, since `secrets.get(name, host=host)` is bound to
that host by JimmyHQ). `jimmyhq_app.testing.SAMPLE_ARTICLE` is one article every app's tests
can read.

Test it without JimmyHQ with `jimmyhq_app.testing.FakeCore`: make the app inside
`core.installed()`, then drive it with `core.run_job`, `core.decide`, `core.call`,
`core.tool` and `core.collect`, and look at `core.cards`, `core.events`, `core.asked` and `core.counts`
(each number given to `app.ui.count`; `core.count` is the last).
`core.upload(data, name)` plays the owner giving the app a picture and returns its
`{ref, name, size, type}`; `core.sent_files` lists each file a fetch sent, as
`{url, ref, data}`, held to the same path rules as JimmyHQ.

```python
reply = app.net.fetch(url, method="PUT", file=ref, headers={"Content-Type": "image/jpeg"},
                      fresh_connection=True)         # also body=, json=, multipart=, as_=
with app.files.open(ref) as picture:                 # app.files.path(ref) -> Path
    data = picture.read()
```

Set what the fake answers with `core.replies`, `core.pages` (web.read, by address),
`core.search_results` (by query) and `core.embedder`; switch `core.web_on` or
`core.vision_on` off to see the owner's refusals; `core.change_calendar(timezone=...)`
and `core.change_setting(key, value)` deliver what the owner changed.

`app.calendar` (and `app.timezone`, `app.week_start`) is the owner's time zone, an IANA
name or `""` for the computer's own, and the day their week starts on. It is asked of
`/hello` once and asked again when the owner changes either; `@app.on_settings` hears
`fn("owner.timezone")` or `fn("owner.week_start")` then, and `fn(key)` for the app's own
settings. Stored times stay unix milliseconds in UTC; convert with the time zone only to
show them or to decide what "today" is.

`app.limits()` is the owner's limits as they are now, as a `Limits`: `save_max_mb`, `save_parts`,
`save_per_minute`, `save_html`, `call_max_kb` and `picture_px`. It needs no permission. Use
`call_max_kb` to size what the program answers its page with (a file in parts, a long list in
pages) rather than guessing a number: an answer larger than that is refused. An older
JimmyHQ refuses the call with a `CallError`. In tests, `FakeCore(limits={"call_max_kb": 64})`
or `core.limits["save_max_mb"] = 2` plays an owner who set them differently.

**Refusing.** When a handler raises, the owner (or your page) reads the exception's own
text, as it is. Write that text as a sentence for the person, say nothing was changed and
what to do instead: `raise ValueError("The list is empty. Nothing was changed. Add a
line first.")`. The SDK no longer puts "The app could not do that: " in front of it (a page
that strips that lead-in keeps working, as nothing matches it any more). A fault in your
own code (`KeyError`, `TypeError`, ...) or an exception with no words is shown as one plain
sentence that names the kind of fault, never its raw text. If the handler lets a `CallError`
from JimmyHQ through (a call to `app.model.ask` that was refused, say), its `owner_message`
goes to your page with it, so `why(error)` shows the owner the sentence JimmyHQ wrote, such
as which model role is empty and where to fill it. Only a sentence JimmyHQ itself gave your
program counts: words your program writes into `owner_message` are dropped, and the page
gets your `message` as it was.

**Slow work: start it, then ask again.** The page waits for `hq.app.call` for at most
`apps.call_timeout_s` (30 seconds; the owner can set 1 to 300 under Settings, Apps), then
is told the program did not answer, and the call is not carried out later. Your program
also answers one delivery at a time, so a call that runs for a minute holds up its jobs and
every other call. For anything that can take longer than a few seconds, answer at once and
do the work in a thread; the page asks again for the result:

```python
import threading, uuid
runs = {}                                    # run id -> {"state": ..., "done": ..., "result": ...}

@app.call("export_start")
def export_start(args):
    run = uuid.uuid4().hex
    runs[run] = {"state": "running", "done": 0}
    def work():
        try:
            runs[run]["result"] = build_export(args, progress=lambda n: runs[run].update(done=n))
            runs[run]["state"] = "done"
        except Exception as error:           # the page is told, in a sentence
            runs[run].update(state="failed", why=str(error) or "The export failed. Nothing was changed.")
    threading.Thread(target=work, daemon=True).start()
    return {"run": run}

@app.call("export_status")
def export_status(args):
    found = runs.get(args.get("run"))
    if found is None:
        raise ValueError("That export is not running any more. Start it again.")
    return found if found["state"] != "done" else {"state": "done", "result": found.pop("result")}
```

```js
const { run } = await hq.app.call("export_start", { part: 1 });
let status;
do {                                         // every second or two, not in a tight loop
  await new Promise((resolve) => setTimeout(resolve, 1500));
  status = await hq.app.call("export_status", { run });
  showProgress(status.done);
} while (status.state === "running");
```

Keep the state in memory only for what can be started again (a restart of the program
loses it); keep what must last in the app's own database. Each poll is a call, so it counts
towards the app's request limits; poll every second or two. Reading the result and saving
it with `hq.ui.save` needs the owner's click just before, so ask them to press a "Save"
button when the status says `done`, rather than saving from the end of the loop.

### How it runs

JimmyHQ starts `<python> -m jimmyhq_app <server.entry>` in the app's version folder, with
no shell. `<python>` is the app's own environment when it has one, otherwise JimmyHQ's
own interpreter. The environment is rebuilt: only the system's basics, then
`JIMMYHQ_SUPERVISED=1`, `JIMMYHQ_APP_ID`, `JIMMYHQ_APP_API` (this computer's address for
JimmyHQ) and `JIMMYHQ_APP_DATA` (its own data folder).

The token is the first line of the program's standard input, `{"token": "jhqa_..."}`. It
is never in the environment or on the command line. A new one is made at each start, and
the old one stops working. While developing by hand, set `JIMMYHQ_APP_TOKEN` instead.
When standard input closes, the program should stop: the SDK does.

A program that exits is started again after a short wait. One that exits
`apps.crash_limit` times in a row, each within a minute of starting, is held back (see
below) until the owner presses Restart, or, as the owner chose in `apps.crash_loop`,
switched off, or rolled back when the crashing began with an update. What it prints goes to
`app-<id>.log` in JimmyHQ's log folder. The Apps screen reads `GET /api/apps/<id>/health`
(state, label, restarts, last exit, the log's last lines) and restarts it with
`POST /api/apps/<id>/restart`.

### The app API

Every route is under `/api/app/v1/` and takes only the app's token, as
`Authorization: Bearer <token>`, and only from this computer. Nothing else works there,
and the token works nowhere else. Every answer is HTTP 200 with
`{ok: true, result}` or `{ok: false, error: {code, message, retry_after_s?}}`; a missing
or stopped token is 401.

| Route | Does |
|---|---|
| `GET /hello` | who it is, what it was granted, its settings, and the owner's `timezone` and `week_start` |
| `POST /call/<call>` `{args}` | one call: every bridge call but `app.call`, and `tape.print`, `tape.read`, `secrets.get`, `vault.put`, `bench.update`, `bench.withdraw`, `ladder.check`, `app.emit`, `feed.read`, and `ui.limits` |
| `PUT /kinds` `{kinds: [{name, v, schema, example}]}` | declare its own kinds of event, as JSON Schema |
| `PUT /plugs` `{jobs, decisions, calls, tools, sources, inbound?}` | what it offers, checked against its manifest and grant |
| `GET /inbox?wait=25` | work for it: `{deliveries: [{id, type, data, offers}]}` |
| `POST /inbox/<id>` `{ok, result}` or `{ok: false, error}` | its answer to one delivery |

- `tape.print` writes only the app's own declared kinds (`app.<id>.<name>`), as
  `app:<id>`, with its subject kept as `app.<id>:<subject>`. A private app cannot.
- `tape.read` returns what the app wrote, or with `tape.read` the whole record:
  `{events, next}`.
- A declared `(name, v)` never changes: declare a change as the next version, and upgrade
  old payloads in the app (`@app.upcaster`). JimmyHQ understands the part of JSON Schema a
  pydantic model writes, and refuses `pattern`.
- Deliveries are kept until answered and handed over again if not answered within
  `apps.redeliver_s`, so a handler may see one twice. Types: `job`, `decision`, `tool`,
  `collect`, `call`, `settings`, `work`, `inbound`.

### What a full app can plug into

Each needs its permission, granted by the owner, and each reaches the program as a
delivery in its inbox. The SDK turns each into a call of the function you decorated.

| Plug | Needs | In the SDK | What JimmyHQ does |
|---|---|---|---|
| Jobs | `jobs` | `@app.job(name, every=s)` or `every_setting=key` | The worker queues a run when it is due, one at a time. A run not answered within `apps.job_timeout_s` is given up and shown as not answering. |
| Cards | `bench.file` | `app.bench.file(kind, title, actions=[...])`, `.update(card)`, `.withdraw(card)` | Files it as `app.<id>.<kind>`, by the app. Your buttons are checked like anyone's: one that looks like the Bench's own must behave like it, and one must close the card. |
| Decisions | `bench.file` | `@app.on_decision(kind)` | Hands over each decision on a card **your app filed**, of a kind you handle. A card someone else files under your kind is not yours. |
| The ladder | | `app.ladder.check(tool, action, category)` | `read`, `approve` or `alone`, for your app. Ask before anything that leaves the building. |
| Memory | `memory.source` | `@app.memory_source(id)`: `fn(cursor, limit) -> (items, cursor)` | Registers `app_<id>_<source>`, **off until the owner switches it on**. Each item's `ref` is kept as `app.<id>:<ref>`; `who` is `owner` or your app, never an agent. |
| Agent tools | `agents.tools` | `@app.tool(name)`: `fn(args, agent[, role]) -> str` or `{result, ok, shown}`; `@app.record_tool(name)` | Offered as `app_<id>_<name>` only to agents the owner gives your app (capability `app.<id>` on the Village screen) or one of its roles (`app.<id>.<role>`, that role's tools only). A tool whose `effect` is not `read` or `record` runs only once the owner lets that agent use it alone on the ladder. `shown` reaches the agent marked as text from an app. |
| Roles and work | `agents.tools` | `app.roles.get()`, `app.work.give(role, title, ...)`, `app.work.withdraw(id)`, `@app.on_work` | See [Working with agents](#working-with-agents). |
| Settings | | `app.settings.get(key)`, `@app.on_settings` | The owner changes them under Settings; your program is handed `settings {key}` when one changes, and `settings {key: "owner.timezone"}` or `{key: "owner.week_start"}` when the owner's calendar does. |
| Sidebar count | | `app.ui.count(n)`, or `@app.ui.counter` on the one function that counts | The number beside the app in the sidebar, right without the app being open: how many things wait on the owner, a whole number from 0 to 999 (anything else is refused with a sentence and nothing is kept). It needs no permission, since it shows only on the app's own line. JimmyHQ keeps the latest one for each app and sends it with the list of installed apps the shell loads, which the shell reads again every `apps.count_refresh_s` seconds (60 by default). It is not shown while the app is switched off, and is deleted when the app is removed. A function given to `@app.ui.counter` is asked after every delivery the program handles and when it starts, and its number is said only when it changed (above 999 is said as 999). While the app's page is open and says `ui.badge`, the page's number is the one shown; count the same way in both |
| Messages from devices | `inbound` | `@app.inbound(name)`: `fn(message)` | See [Messages from your devices](#messages-from-your-devices). |
| Its pages | | `app.emit(event, data)` and `@app.call(name)` | Your open pages hear `hq.on(event)`; they ask your program with `hq.app.call(name, args)`. An event may not be named like one JimmyHQ sends. |

**A private app's card and task.** A card is on the owner's Bench, and so on their
record, so a `private` app's card carries **its title and none of the app's other words**.
`bench.file` takes only:

- `kind` and `title`. **The title is still the app's own words**: JimmyHQ cannot check
  it, so it must carry nothing private beyond what the app promises the owner it would
  show (a bill's name and its date, or a count), and nothing a person would not want on
  their record;
- `lane` (`needs` or `done`), which is one of two fixed words;
- `actions`, only the Bench's own buttons (`approve`, `not_yet`, `no`, `ok`, `undo`)
  written exactly as the Bench writes them, never a button with words of your own (leave
  `actions` out for the usual ones);
- `subject`, only one of the words the manifest lists under `"card_subjects"` (up to ten
  words, each 1 to 12 lower-case letters, such as `"card_subjects": ["reminder",
  "package"]`), with at most a number after it, such as `reminder:12`. Any other word, a
  hyphen, an upper-case letter or text about what the card is for is refused, and an app
  that declares no words may file no subject. `"card_subjects"` is only for a `private`
  app that declares `cards`.

`summary`, `body`, `why`, `details`, `files` and any other argument are refused with
`invalid` and nothing is filed. `bench.update` may change the title and nothing else, and
`bench.withdraw` takes no `note`. A private app's task (`tasks.create`) is a title only: a
non-empty `notes` is refused. The Python SDK sends only what you gave (an empty `body`,
`actions`, `subject` or `notes` is left out), so `app.bench.file(kind, title)` is always
accepted. Because the card has no words to say where to go, the Bench shows an
**Open <app>** button on every card an installed app filed, which takes the owner to the
app's page. `FakeCore(private=True, card_subjects=[...])` enforces the same rule, so a
card that is refused by JimmyHQ is refused in your tests too; `core.filed` holds the arguments of each card the
app filed.

**When it keeps crashing.** A program that stops `apps.crash_limit` times in a row, each
soon after starting, is held back: the Apps screen says "Held back after crashing", the
record says so (`apps.held_back`), and by default the owner gets a card. It stays
installed and switched on and its data is untouched; Restart on the Apps screen starts it
again. `apps.crash_loop` can instead hold it back quietly, or keep restarting it ever more
slowly. JimmyHQ never switches it off or rolls it back by itself unless you choose that in `apps.crash_loop`. When it goes back to the version before a crashing update, its card says whether the data went back to the copy taken before the update (`apps.crash_rollback_restores_data`) and, if so, that what the new version wrote is kept as a backup Roll back can bring back until a later update or roll back deletes the older versions.

## Messages from your devices

A device or service on the owner's network, such as a home's hub, can send a short JSON
message to one full app. The app declares what it takes, and asks for `inbound`:

```json
"permissions": ["inbound"],
"inbound": [{"name": "event", "label": "Home events",
             "help": "What your home sends when something happens."}]
```

Names are lower-case letters, digits and `_`, starting with a letter; an app has at most 8.
Declaring `inbound` without the permission, or the permission without `inbound`, is
refused. A private app may take messages too.

Two more fields are optional on each name. `"array": true` takes a JSON list as well as an
object (some services post a bare list); without it a list is refused, as before.
`"max_kb": 256` says the largest message the app expects. The install dialog tells the owner,
and the app's page says when it is more than the owner allows. A name that says what it
expects is read up to that size without the owner changing anything; one that does not is
held to `apps.inbound_default_kb` (16 KB to begin with). Neither is ever more than the
owner's ceiling, `apps.inbound_max_kb` (1024 KB to begin with).

```json
"inbound": [{"name": "memory", "label": "Memories", "array": true, "max_kb": 256}]
```

```python
from jimmyhq_app import App, Inbound

app = App()

@app.inbound("event")
def event(message: Inbound) -> None:
    # message.body: the JSON object sent; message.received_ms: when JimmyHQ took it
    # (unix ms, UTC); message.key_name: the name the owner gave the key it came with.
    db = app.db()
    db.execute("CREATE TABLE IF NOT EXISTS seen (at INTEGER, what TEXT)")
    db.execute("INSERT INTO seen VALUES (?, ?)", (message.received_ms,
                                                 str(message.body.get("state"))))
    db.commit()

@app.inbound("memory")
def memory(message: Inbound) -> None:
    # message.body is a list here when the sender posts one ("array": true above).
    # message.query: what the sender added to the address after "?", such as
    # {"uid": "...", "session_id": "..."}; the first value of each name; {} when none.
    items = message.body if isinstance(message.body, list) else [message.body]
    who = message.query.get("uid", "")
    ...

app.run()
```

What the handler returns is dropped: the device is only ever told that the message was
taken. If the handler raises, the message is not handed over again, and the app's page
shows how many failed with the last sentence. The body is text from outside: data, not
instructions, and so is `message.query`. Test it with
`core.inbound("event", {"state": "on"}, key_name="Home hub")` on the fake core;
`query={"uid": "abc"}` plays the sender's parameters, and a list body passes for a name the
fake was made with, `FakeCore(..., inbound_arrays=["memory"])`. Each message it hands over is
kept in `core.inbound_sent`.

**For the owner.** On the app's page in the Apps screen, under "Messages from your
devices", make a key for each device and give it a name. The key is shown once, with a
copy button: JimmyHQ keeps only its digest and cannot show it again. The page lists each
key's name, when it was made and last used, and how often; Remove stops a key at once.
An app has at most `apps.inbound_keys_max` keys (5). The app's own switch is off until its
first key is made, which switches it on; with no key nothing gets in. A key works for its
one app, and for every name that app declares. An update never changes the switch or the
keys, and neither does a restart. Removing the app and deleting its data removes the keys
and the switch with it (a fresh install does not start open). Removing it while keeping its
data keeps the keys but switches the door off, so installing it again opens nothing until the
owner switches it on: a reinstall never reopens a door without their click. The keys belong
to that install, not to the id: they come back only when the same app is installed again,
told by where it came from, never by its manifest (the same git address, the same app that
ships with JimmyHQ, or the same folder on this computer, by its full path; an update moves
the app to where that version came from). An app from anywhere else under the same id
starts with no keys, and the kept ones are removed for good, each recorded as
`apps.inbound_key_removed`, with the old counts and receipts. Keys kept by an app installed
from a folder before JimmyHQ kept where installs came from cannot be told apart, and are
removed the same way. Anyone who can push to that git address or write in that folder can
put another app there, so it is only as safe as the place itself. The removal
dialog says so ("its keys are kept; its door will be off until you switch it on again"), and
so does the app's page when keys exist and the door is off ("This door was switched off when
the app was removed, or by you. Its keys still work once you switch it on.").

**Sending one.** The page shows the address, made from the page's own address:

```bash
curl -X POST https://jimmyhq.example.com/api/in/lights/event \
  -H "Authorization: Bearer <your key>" \
  -H "Content-Type: application/json" \
  -d '{"entity": "light.porch", "state": "on"}'
```

A device must use an address it can reach, which may not be the one in your browser, and a
name JimmyHQ answers to (Settings, Access). The answer is `202 {"ok": true}` once the
message is waiting for the app.

**A sender that can only be given an address.** Some devices and services (a pendant's
webhook, a "notify this URL" box) cannot set a header. When making the key, tick "For a
sender that can only be given an address". The key then goes in the address itself, and the
page shows each name's full address once, with a copy button:

```text
https://jimmyhq.example.com/api/in/lights/event/k/<your key>
```

Only a key made that way is taken from the address; any other key there gets the same `401`
as a wrong one. (A key made for the address also works in the header.) Anyone who sees the
address can send, so treat it like the key it holds: give it only to the sender, and remove
the key if anyone else sees it. JimmyHQ never writes it down: the access log shows
`/api/in/lights/event/...`, every answer says `Referrer-Policy: no-referrer`, and the app,
the record and the receipts get the key's name, never the key.

**The sender's own parameters.** Whatever a sender adds after `?` (`?uid=...&session_id=...`)
reaches the app as `message.query`, the first value of each name. At most
`apps.inbound_query_max` (20) names, each name and value at most `apps.inbound_query_chars`
(500) characters, or the message is refused with `400`. A parameter that carries one of
JimmyHQ's keys is dropped, never passed on.

| Answer | When |
|---|---|
| `202` | Taken: it waits in the app's inbox. |
| `401` | The same answer, byte for byte, for a wrong or missing key, an app that does not exist or does not take messages, a name it does not declare, an app that is switched off or closed to messages, and the whole door switched off. |
| `403` | The request carries an `Origin` header: this is for devices, not web pages. |
| `405` | Anything but `POST`. |
| `415` / `413` / `400` | Not `Content-Type: application/json`; larger than the name's own `max_kb` (or `apps.inbound_default_kb`, 16 KB, when it has none) or the owner's ceiling `apps.inbound_max_kb`, refused before it is read; not one JSON object (or list, for a name with `"array": true`), or more `?` parameters than allowed. |
| `429` | Too many, with `Retry-After`: more than `apps.inbound_per_minute` (120) from one key, more than `apps.inbound_queue_max` (500) waiting for the app (nothing waiting is dropped), or, without a right key, an address turned away for `apps.inbound_block_s` (300 seconds) after `apps.inbound_bad_per_minute` (20) refusals in a minute. A right key is never turned away for its address. |

**What guards it.** This address is reachable by anything that can reach JimmyHQ's port: it
takes no sign-in, and a session cookie does nothing there. What stands in the way is the
key (random, long, kept only as a digest, one app's only), the switches (`apps.inbound_enabled`
for every app, and each app's own), the limits above, and the refusal that is the same
whatever was wrong, so a caller cannot learn which apps exist. Behind a proxy on the same
computer every device connects from the proxy's address. A request relayed that way is
counted under the address the proxy forwards for (`forwarded:<the last X-Forwarded-For
entry>`, the one the proxy added), never together with a caller on the computer itself;
the limit on refusals still holds back only messages without a right key. Over all
addresses together, more than `apps.inbound_bad_all_per_minute` (200) refusals in a minute
turn away every message without a right key until the minute has moved on. JimmyHQ uses a forwarding header as the visitor's real
address only for `trusted_proxies` in config.toml. No CORS headers are ever sent.

**What is kept.** The body goes into the app's inbox and nowhere else, and is deleted there
once the app has answered (an old copy may stay in the database's write-ahead file until
SQLite writes over it). Messages wait behind the app's other work, never ahead of it. The record gets the owner's own steps (`apps.inbound_key_made`,
`apps.inbound_key_removed`, `apps.inbound_switched`), never a key or a body. Each message
leaves a receipt on the app's page: the name, the key's name, the time, its size and how it
went (the last `apps.inbound_receipts_max`, 200, for each app). Nothing about a body is written to the record or the logs.

## Working with agents

The pattern: **the app holds** the records and the tools; **agents in the Village bring
judgment**; **code keeps the guarantees**; **the owner decides**. An app asks for
`roles` to be filled, and JimmyHQ helps the owner fill them. Nothing is forced: every role
may be left empty, and the app must then do its plain way (a job, one model call).

```json
"roles": [{"id": "writer", "label": "Writer", "purpose": "Drafts posts from articles.",
           "charter": "roles/writer.md", "tools": ["brief", "submit_draft"],
           "model": "smart", "not_with": ["checker"], "optional": true}]
```

- **Filling.** At install, and later on the app's page under Roles, the owner creates an
  agent for each role, gives it to one they have, or leaves it empty. The agent gets the
  capability `app.<id>.<role>`, which offers **only that role's tools**, and the charter
  (a `.md` or `.txt` file in the app, at most 8000 bytes, shown to the owner first) as a
  standing priority for that agent alone. A charter is text for the agent: it never
  changes a level on the ladder. Emptying a role or removing the app takes both away;
  the agent stays.
- **Kept apart.** One agent may hold many roles across apps, never two of one app named
  together in `not_with` (either side naming the other is enough). Given both by hand on
  the Village screen, it is offered neither role's tools. Two such roles on one model are
  allowed, with a warning.
- **`record` tools.** A tool that only writes into your own records ("submit a draft")
  declares `"effect": "record"`. It runs without the ladder, and each run leaves a receipt
  (`apps.tool_recorded`). While one is in your program's hands, every call that reaches
  outside your app (network, models, tasks, the Bench, work, the Vault) is refused, from
  the program and the page. That holds after the agent was told it took too long, until
  your program answers it late, starts again, or an hour has passed. JimmyHQ cannot stop a full app's own code from acting later on
  what was recorded: anything outward still goes through your permissions, the Bench and
  your app's own row on the ladder.
- **Outside clients.** The owner may also give a tool of yours to an outside AI client
  through the MCP door ([docs/door.md](door.md#tools-from-your-apps)), one tool and one
  client at a time. Only a `read` or `record` tool that none of your roles lists can be
  given; anything stronger, or a role's tool, never is. Your program gets the usual `tool`
  delivery with `agent` set to `client:<name>` (an agent's id never holds a colon), `role`
  None and `via` `"door"`: treat it as someone who is not one of the owner's agents, holds
  no role and was given no work, and never credit it as one. **A tool called through the
  door cannot reach outside your app**, whatever its effect: while such a delivery is in
  your program's hands, every call that reaches outside (network, web, models, tasks, the
  Bench, work, the Vault) is refused, as for a `record` tool, so answer it from your own
  records. A delivery whose caller gave up waiting still counts as in your hands until you
  answer it, your program starts again, or an hour passes. Write each such tool's
  `description` for a reader with no context, who has never seen your app or its records:
  what it does, what each argument means, and what comes back.
- **Giving work.** `app.work.give(role, title, details=, subject=, due_ms=)` puts a task on
  the list of whoever holds the role and returns its id; with nobody in the role it raises
  `CallError` (`not_found`), so fall back. `app.roles.get()` says who holds each role, by
  label only. `@app.on_work` hears `Work(id, role, state, subject, note, agent_label)` when
  it is `done`, `set_aside`, `dropped` or `reassigned`. Work you give files no "done" card:
  report the outcome your own way. Limits: `apps.roles.work_open`, `apps.roles.work_per_day`.
- **Testing.** `FakeCore(roles={"writer": "Wren", "checker": None})`, `core.tool(name,
  args, role=...)` and `core.end_work(id, "done")`.

## Feeds between apps

One app can offer a stream of items that another may read, with the owner's consent and
nothing wider. Reader offers the articles the owner chose to pass on; Posts reads them and
starts a piece from each. Each app works alone: a feed is something extra.

**Offering.** A full app declares each feed in its manifest. `kind` is one of its own kinds
of event, written short: the items are exactly the events of `app.<id>.<kind>` that the
app prints itself with `tape.print`. Nothing else is needed.

```json
"feeds": [{"name": "articles", "kind": "article.offered", "label": "Articles",
           "description": "Articles you chose to offer, with their address, topic and why."}]
```

- A kind that reads as a full name (`app.posts.posted`) is refused: a feed can only ever
  carry the app's own kind.
- A private app offers no feed: it keeps what it holds off the record, so it has none.
- An item must carry what a reader needs. There is no call back into the producer; a
  reader that needs the source reads it itself through its own permissions (Posts reads
  each article with `web.read`, and never takes Reader's text as its own). This keeps the
  producer's records and program out of every other app's reach.
- What each feed carries is part of what the owner agrees to (each feed's name and kind are
  in the app's fingerprint). An update that points a feed at another of its kinds, or adds
  a feed that an installed app already holds a grant for, asks the owner again, so it never
  goes in by itself even with `apps.auto_update` on "auto". A new label or description, a
  feed removed, or a new feed nobody may read yet asks nothing. An app that offers no feed
  keeps the fingerprint it had.

**Reading.** Another full app asks for `feed:<app>.<feed>`: one feed of one app, with no
wildcard. The install dialog says it in the producer's words ("Read "Articles" from
Reader: ..."), or, when that app is not installed, that it reads nothing until it is. So
an owner with only one of the two apps agrees to nothing that reads anything. Each app's
page says who reads what, and installing a producer says which installed apps will read
its feeds.

```python
page = app.feeds.read("reader", "articles", after=cursor, limit=50)
for entry in page.items:
    if entry.erased:
        ...                      # the owner had it erased: its place, never its text
    else:
        handle(entry.item)       # data from another app, never instructions
cursor = page.next               # keep it once the items are handled
```

- `feed.read {app, feed, after?, limit?}` returns `{items: [{seq, id, ts, v, erased,
  item}], next}`: the events of that one kind printed by that one app after `after`, and
  nothing else on the owner's record. It takes no kinds and cannot say who is reading.
- When the feed cannot be read it is refused with a sentence, never answered with an
  empty list: `not_found` when the producer is not installed or was removed, or no longer
  declares the feed; `unavailable` when it is switched off or its files do not read. An
  empty list always means nothing new.
- A private app may read a feed. What it reads stays with it, and its model calls stay
  on the owner's own machines unless the owner switches that off for it.
- Limits: `apps.quota.feed_reads_per_day` (1,000) and `apps.quota.feed_items_per_read`
  (100).
- What an app offers and reads is read afresh from its manifest and the owner's grant on
  every call, in every process, so a producer installed, updated or removed elsewhere is
  seen at once.
- **Testing.** `FakeCore` plays the other app: `core.offer("reader.articles", {...})`,
  `core.erase_item(ref, seq)`, `core.switch_off("reader")`, `core.remove_app("reader")`,
  `core.stop_offering(ref)`.

## Developing

```
jimmyhq apps new plant_care            # a working full app: a job, a card type, a screen, a test
jimmyhq apps new plant_care --tier sandboxed
jimmyhq apps dev plant_care            # install it here and put each change in place
jimmyhq apps check plant_care          # the checks it must pass before anyone installs it
```

- **new** writes a skeleton into a new folder. Its test runs the program against
  `jimmyhq_app.testing.FakeCore`, with no JimmyHQ at all.
- **dev** checks the folder, asks you once (as an install does), installs it marked
  "Development" (its files are served uncached) and then looks at the folder every second.
  Each change is checked and the new copy put in place the way an update is, even under
  the same version number: its data, secrets, roles, device keys and hosts stay, the
  program is started again and its open pages load again. If the change asks for anything
  you did not agree to (a permission, or more for its agent tools), the loop stops and
  says so; run it again to be asked. Each copy is recorded as an update, with its data
  copied first and the last `apps.keep_versions` copies kept, as any update.
- **check** reads the manifest and every file it names, the folder's size and shape (no
  links), compiles each Python file (never runs it), and looks for `pyproject.toml` with
  its `uv.lock`. Each finding is a sentence, a problem or a warning.

## Inside JimmyHQ

What the parts of the platform call (all in `jimmyhq.apps`):

- `inbox.deliver(core, app_id, type, data, *, key=None) -> id`; `inbox.wait_answer(core,
  id, timeout_s)` and `await inbox.await_answer(...)` return `Answer(ok, result, error)` or
  None; `inbox.expire(core, id)`; `inbox.plugs(core, app_id) -> {jobs, decisions, calls,
  tools, sources}`; `inbox.responding(core, app_id) -> bool`.
- The data each delivery carries: `job {name}`; `decision {card_id, card_kind, action,
  note, reason, subject, event_id, ts}`; `tool {name, args, agent}` answered with
  `{result, ok, shown?}`; `collect {source, cursor, limit}` answered with
  `{items: [{ref, when_ms, who, text, where}], cursor}`; `call {name, args}`;
  `settings {key}`.
- `api.add_call(bridge.Call(name, run, permission, tiers=("full",)))` adds a program-only
  call; `plugs.bench` adds `bench.update`, `bench.withdraw` and `ladder.check`, and
  `plugs.emit` adds `app.emit`.
- `plugs.setup(core)` declares the installed apps' settings and memory sources in this
  process; `plugs.tick(core)` is the worker's pass (jobs, decisions and settings followed
  on the Tape with a cursor per app, crash loops); `plugs.tools.offered(v, agent_id,
  capabilities)` is what the Village's beat adds to its tools.
- `runtime.publish(core, *, restart=None)`, `runtime.restart(core, app_id)`,
  `runtime.health(core, app_id)`, `runtime.log_tail(core, app_id, lines)`;
  `GET /api/apps/<id>/health` also lists each job's last run.
- `GET /api/apps/<id>/emitted?after=<seq>` (owner) is what an open frame reads for
  `app.emit`.
- `schemas.declare(core, manifest, kinds)`, `schemas.load_all(core)`.
- `feeds.feed_read(ctx, args)` (the call), `feeds.explain(core, lines)` (feed lines in the
  producer's words), `feeds.who_reads_what(core, manifest, granted)`,
  `feeds.plan_lines(core, manifest)`, `feeds.readers_of(core, app_id, feed)`.
- `dev.check(core, folder)`, `dev.new(app_id, folder, tier=)`, `dev.watch(core, folder,
  agree=)`.
