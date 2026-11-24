# Web lookup

`server/jimmyhq/lookup/` lets a model search the web and read pages, once the owner
switches it on. It is two functions, one receipt for each, and a set of rules about where
a request may go.

```python
from jimmyhq import lookup

results = await lookup.search(core, "broad beans sowing time",
                              actor=who.actor, caller="chat.reply")
page = await lookup.fetch(core, results[0].url, actor=who.actor, caller="chat.reply")
```

| Function | Gives back |
|---|---|
| `search(core, query, *, actor, caller, private=False, limit=None)` | `list[Result(title, url, snippet, text=None)]` |
| `fetch(core, url, *, actor, caller, private=False, format="markdown")` | `Page(url, final_url, title, text, truncated, bytes, content_type, links)` |

Both also take `cause` and `root`, the Tape lineage of the receipt. Both raise
`lookup.WebError(kind, message)`: `message` is one sentence that says what to do, and is
written to be shown to the owner or handed back to a model as a failed tool.

**Nothing leaves until the owner says so.** `lookup.enabled` is off on a new install, and
while it is off both functions refuse before anything is resolved or contacted. Turning
it on is the owner's click for what leaves the machine: after that, a search sends the
query to the chosen search provider, and a fetch asks a public web server for a page.

**What comes back is data, not instructions.** A page is written by a stranger. Callers
must treat `Page.text` and `Result.snippet` as untrusted, and chat does (see
[Chat: web lookup](chat.md#web-lookup)).

## Where a fetch may go

A model chooses the URL, and a page can talk a model into choosing one. So `fetch` trusts
nothing about an address until `safety.check` has passed it, and then connects to exactly
what was checked. The rules, each with its own tests in `tests/test_lookup_safety.py`:

1. **Scheme.** Only `http` and `https`. `file:`, `ftp:`, `data:` and the rest are refused.
2. **No credentials.** A URL with a user name or password is refused, which also ends the
   `http://trusted@other/` trick.
3. **Clean.** No space, backslash or control character; at most 4000 characters.
4. **No numbers in disguise.** A host written as a number in an odd form is refused
   without being looked up: one decimal number (`2130706433`), hex (`0x7f.0.0.1`), octal
   (`0177.0.0.1`), or fewer than four parts (`127.1`). Those forms exist to slip past
   checks like this one. An ordinary dotted address is read as the address it is.
5. **Public addresses only.** The host is resolved, and *every* address it resolves to
   must be public. Refused: loopback, private ranges (10/8, 172.16/12, 192.168/16, IPv6
   unique-local), link-local (which holds the cloud metadata address), carrier-grade NAT
   (100.64/10, where tailnets live), multicast, unspecified, reserved and documentation
   ranges, in IPv4 and IPv6. An IPv4 address carried inside an IPv6 one is unwrapped and
   judged as IPv4: mapped (`::ffff:a.b.c.d`), the old compatible form, 6to4, NAT64 and
   Teredo.
6. **Resolved once, and pinned.** The connection goes to the address that passed the
   check, with the site's name sent in the `Host` header and, for https, used as the name
   the certificate must match. The name is never resolved a second time, so a name that
   answers "public" to the check and "private" a moment later (DNS rebinding) gains
   nothing.
7. **Every hop.** The HTTP client follows no redirects. Each one is read, checked as a
   new URL by all of these rules, and followed at most five times.
8. **Never itself.** JimmyHQ's own address and port are refused whatever the settings
   say: any name the owner reaches JimmyHQ by (the configured host, and "Other names for
   this server") on its port, and any private address at all on its port.
9. **No proxy.** Proxy settings in the environment are ignored. A proxy would resolve the
   name itself, somewhere the check cannot see.
10. **Nothing of the owner's is sent.** No cookies are kept or sent, between hops or
    between fetches, and no header identifies the owner. The `User-Agent` is the setting
    `lookup.user_agent`.

**The owner's exceptions.** A host named in `lookup.allow_private_hosts` (a name or an
address, nothing else) skips rule 5 and only rule 5. Use it for an internal wiki or a
documentation server. Whatever those hosts serve becomes readable by any model with web
lookup on, so leave it empty unless you mean it. Rule 8 still applies: an allowed host is
never read on JimmyHQ's own port.

### What is read

- **Text only.** `text/*`, JSON, XML and their kin. Anything else (an image, a PDF, a
  download) is refused by its `Content-Type` before its body is read. A body with no type
  is judged by its bytes.
- **A size limit.** Reading stops at `lookup.fetch_max_bytes` (5 MiB), counted after
  decompression, so a small download cannot unpack into a huge one. `Page.truncated` says
  the page was cut there.
- **A deadline.** The whole fetch, redirects included, has `lookup.fetch_timeout_s`.
- **HTML becomes Markdown** (`format="markdown"`, the default), with what a reader never
  sees taken out first: scripts, styles, comments, forms, frames, navigation and footers,
  images, and elements hidden with `hidden`, `aria-hidden` or an inline style. Hidden
  text is where a page puts words meant for a model and not for a person. This is a
  filter, not a guarantee: a page can hide text in ways it does not catch (a stylesheet
  class, white text), which is why the result is still treated as untrusted.
  `format="text"` is the same reading without link addresses; `format="html"` is the
  page as it was sent.
- Links are made absolute, and `Page.links` lists the web addresses the page links to.

The conversion uses [markdownify](https://pypi.org/project/markdownify/) on
[Beautiful Soup](https://pypi.org/project/beautifulsoup4/) with Python's own HTML parser:
pure Python, MIT licensed, no compiled parts, so it installs the same on every platform.
A large page takes a moment to convert, so it is done off the event loop.

## Searching

`search` asks the backend chosen by `lookup.search_provider`:

| Backend | Needs | Asked with | Reference checked |
|---|---|---|---|
| `exa` | a key | `POST https://api.exa.ai/search`, `x-api-key` | docs.exa.ai/reference/search |
| `tavily` | a key | `POST https://api.tavily.com/search`, `Authorization: Bearer` | docs.tavily.com, "Search" endpoint |
| `brave` | a key | `GET https://api.search.brave.com/res/v1/web/search`, `X-Subscription-Token` | api-dashboard.search.brave.com, web search documentation |
| `searxng` | your server's address (`lookup.searxng_url`), no key | `GET {address}/search?q=&format=json` | docs.searxng.org/dev/search_api.html |
| `jina` | a key | `POST https://s.jina.ai/`, `Authorization: Bearer`, `X-Respond-With: no-content` | docs.jina.ai |
| `none` (the default) | | Searching is refused with a sentence that says where to choose a provider | |

- **Keys live in the secret store**, under `lookup.<backend>.api_key`. They are set and
  cleared through the API below, and are never in a setting, an event or an error.
- **A backend with no key is not contacted.** The error says where to add the key.
- **A rate limit is said plainly** (`WebError.kind == "rate_limited"`, HTTP 429 from the
  API), with the wait the provider asked for when it gave one. A refused key, a used-up
  plan, a provider in trouble and an unreadable answer each have their own sentence.
- **Redirects are not followed.** A search address is where the key is sent.
- **Results are cleaned.** Markup and control characters are taken out of titles and
  snippets, lengths are capped, and a result without an `http` or `https` address is left
  out.
- SearXNG must have JSON output switched on (`search: formats:` in its `settings.yml`);
  a 403 from it is reported as exactly that. Its address is the owner's own setting and
  is not put through the public-address rule: a self-hosted search server is usually on a
  private network.

### Adding a backend

1. Check the provider's current API reference. Do not write the wire format from memory.
2. Write a `Backend` subclass in `searching.py` with `id`, `label`, `address` and
   `search(query, limit)`. Use `self.ask(...)` for the request (it maps HTTP failures to
   sentences) and `self.results(items, limit, snippet_fields, text_field)` to read the
   answer. Override `error` for a status code that means something special there.
3. Add it to `BACKENDS` and to `PROVIDERS` in `settings.py` (an assertion keeps the two
   in step).
4. Add its answer shape to `ANSWERS` in `tests/test_lookup_search.py`, and a test of the
   request it sends. The shared tests then cover keys, rate limits and results.

## Receipts

Every search, and every fetch that was attempted or refused, prints one event. The one
exception is a call refused before it starts (web lookup is switched off, or the call
itself is malformed): nothing was tried and nothing is printed. A receipt
says that something left the machine, where to, how much came back and how long it took.
It never holds what a page or a result said.

| Kind | Holds |
|---|---|
| `lookup.searched` | `caller`, `provider`, `ok`, `error_kind`, `results`, `chars`, `duration_ms`, `private`, and `query` **or** `query_hash` |
| `lookup.fetched` | `caller`, `ok`, `error_kind`, `status`, `content_type`, `bytes`, `chars`, `truncated`, `hops`, `duration_ms`, `private`, and `host` and `url` **or** `url_hash` |

With `private=False` the receipt names the query, or the address and its host. With
`private=True` it names none of them and carries a keyed hash instead (HMAC-SHA-256 with a
key made on first use and kept in the secret store), so the same lookup can be recognised
on this install and nobody without the key can test a guess. Chat passes `private=True`
for anything said off the record. A failure keeps only its kind, a word: the sentence may
name an address.

## HTTP API

| Route | Who | Does |
|---|---|---|
| `GET /api/lookup/status` | owner or a read token | Whether web lookup is on, the chosen provider, whether its key is saved, and what is still missing |
| `PUT /api/lookup/providers/{id}/key` | owner | Save a provider's key: `{"key": "..."}` |
| `DELETE /api/lookup/providers/{id}/key` | owner | Clear it |
| `POST /api/lookup/try` | owner | Run one search, `{"query": "..."}`, and return its results |

The status answers `{enabled, provider, provider_label, needs_key, key_set, search_ready,
search_problem, chat_default, providers: [{id, label, needs_key, key_set}]}`.
`search_problem` is the sentence that says what to set up, or null. No route ever returns
a key. Saving and clearing answer with the new status.

`POST /api/lookup/try` answers `{provider, provider_label, results: [{title, url, snippet,
text}], duration_ms}`, or the failure's sentence with its status (409 not set up, 429
rate limited, 502 or 504 for the provider's own trouble). It is the owner's own click, so
it works before `lookup.enabled` is on: that is how a provider and its key are checked
first. It leaves a receipt like any search, with the caller `lookup.try`.

## Settings (section "Web lookup")

| Key | Default | Meaning |
|---|---|---|
| `lookup.enabled` | off | Whether a model may search or read pages at all |
| `lookup.search_provider` | `none` | Which backend answers searches |
| `lookup.searxng_url` | empty | The address of your own SearXNG server |
| `lookup.results` | 6 | Results one search returns |
| `lookup.fetch_max_bytes` | 5242880 | Reading a page stops here |
| `lookup.fetch_timeout_s` | 30 | Longest wait for a page, or a search |
| `lookup.page_chars` | 12000 | How much of a page a model in chat is shown |
| `lookup.steps_per_reply` | 6 | Most rounds of lookups in one chat reply |
| `lookup.allow_private_hosts` | none | Private hosts a model may read anyway |
| `lookup.fetch_any_url` | off | In chat: let a model read any public address, not only ones it was given |
| `lookup.user_agent` | a generic name | Sent as the `User-Agent` when a page is read |
| `lookup.chat_default` | off | Whether chat's "Web" switch starts out on |

## Layout

| File | Holds |
|---|---|
| `safety.py` | Parsing a URL, judging an address, resolving once: `check()` and `is_public()` |
| `fetching.py` | `fetch`, the redirect loop, the size and time limits, and this process's `Lookup` service |
| `pages.py` | Bytes to text: charsets, HTML to Markdown, what is taken out |
| `searching.py` | `search` and the backends |
| `receipts.py`, `kinds.py` | The two event kinds, and the keyed hash |
| `settings.py`, `routes.py`, `errors.py` | Settings, the API under `/api/lookup`, `WebError` |

## Limits worth knowing

- **Pages that need JavaScript** come back empty or nearly so: nothing is run.
- **Some sites refuse automated readers.** They answer 403 or a challenge page; the
  model is told the page could not be read. Nothing is done to get round it.
- **The search provider sees every query**, and a web server sees the address JimmyHQ
  asks from. Choose a SearXNG server of your own to keep searches in the house.
- **Hidden-text filtering is partial**, as said above.
- **An allowed private host is trusted on every port but JimmyHQ's own.** If another
  service on that host must stay out of reach, do not allow the host.
- **Agents have their own caller.** A Village agent with the `web` capability uses these
  two functions as `village.web`, with stricter rules of its own: see
  [The Village](village.md#capabilities-the-web). `lookup.fetch_any_url` is chat's
  setting and does nothing for agents.
- **The address rule for chat** (`lookup.fetch_any_url` off) closes the obvious way a
  page could have the owner's words sent somewhere. It does not close every way: a page
  can still offer a set of links and ask the model to pick one, which leaks a few bits
  per page read.
