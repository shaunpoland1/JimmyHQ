# The JimmyHQ website

The public site, as hand-written HTML and CSS with one small script. No framework, no
runtime dependencies, no trackers, and no request to any other host: every page carries a
content security policy that allows only its own files.

## Build

```bash
uv run python site/build.py              # writes site/dist
uv run python site/build.py --strict     # for a release: fails if anything is missing
```

`build.py` uses the standard library only (Python 3.12 or later, as the project does). It
renders `site/src/` into `site/dist/` and stamps in facts read from the repo, so the site
cannot drift from the product:

| On the site | Read from |
|---|---|
| The version | `pyproject.toml` |
| The apps, their tier, summary and version | `apps/*/jimmyhq.app.json` and `apps/examples/*/jimmyhq.app.json` |
| What each app asks before it installs | `server/jimmyhq/apps/permissions.py`, the install dialog's own sentences |
| Colours and fonts | the tokens in `web/src/index.css` |
| Model roles and providers | `server/jimmyhq/models/shapes.py` and `web/src/modules/models/lib/` |
| Where the project stands, and what each screenshot shows | `README.md`: its Project status section and its pictures' descriptions |
| The design rules | `CONTRIBUTING.md` |
| The Dev log | `CHANGELOG.md` |
| The Backlog | `ROADMAP.md` |
| The logo | `web/public/` |
| Screenshots | `docs/screenshots/` |

The link to the code is one constant, `REPO_URL`, at the top of `build.py`. The site's own
address is `SITE_URL`.

### Screenshots

The list of screenshots is `SHOTS` in `build.py`. A file that is missing from
`docs/screenshots/` is drawn as a plain placeholder of the same shape, so the site can be
built and checked before the pictures exist. `--strict` refuses to build while any is
missing, or while the newest release in `CHANGELOG.md` says `unreleased`
(`--allow-unreleased` lets that one through, to check a build before the date is set). A
picture taken at two or three times its size is shown at the size it stands for, and a
`-light` picture is offered to visitors whose system is in the light theme.

### CHANGELOG.md and ROADMAP.md

Both are read with a small, strict Markdown: headings (`#`, `##`, `###`), paragraphs, lists
(`-` or `1.`, with indented lines carrying an item on), `code`, **bold** and links. A link is
a path in the repo (it becomes a link to GitHub) or an address in the repo or on the site.
Anything else, a table or a link to another site for example, stops the build and names the
line.

- A release is `## 1.2.3 - 2026-01-31` (or `- unreleased`), then a short summary, then any of
  `### Added`, `### Changed` and `### Fixed`, each with a list. Newest first. The newest must be
  the version in `pyproject.toml`; a test checks it. Home shows the newest three.
- The roadmap has `## Now`, `## Next`, `## Later`, `## Not yet exercised against real
  services` and `## Considering`, each with a list and an optional sentence before it.

## Look at it

```bash
cd site/dist && python3 -m http.server 8479 --bind 127.0.0.1
```

Then open <http://127.0.0.1:8479>. The pages use paths from the site's root (`/tour.html`),
so open them through a server rather than as files.

## Test

```bash
uv run pytest tests/test_site_build.py
```

The tests build into a temporary folder and check that every link, asset and anchor
resolves, that nothing outside the repo and the site is referenced, that every page has its
content security policy, and that the version, apps, roles, Dev log and Backlog match the
repo.

## Deploy

The output is a folder of static files. Any static host serves it. One way is an S3 bucket
behind CloudFront:

- upload `site/dist/` to a private bucket and serve it through CloudFront with origin access
  control, HTTPS only;
- set `index.html` as the default root object, and `404.html` as the response for 403 and
  404 errors, with status 404;
- cache `assets/` and `shots/` for a long time and the `.html` files briefly, or invalidate
  them on each deploy;
- optionally add the same content security policy, and `Strict-Transport-Security`, as
  response headers.

Build with `--strict` before a deploy.
