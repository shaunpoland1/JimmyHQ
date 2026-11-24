# Releasing

The steps to make a release, in order. Each step is one fenced block with a `# step:` name
on its first line and commands that need nothing typed in, so a script can run them one
after another and stop at the first one that fails. Run them from the top of the repository.

Two variables name the demo data folder and its port. Use a folder that does not exist yet
(a new one each time), never your own data folder, and never port 8440:

```bash
# step: set-variables
export JIMMYHQ_DATA="$(mktemp -d)/jimmyhq-demo"
export JIMMYHQ_PORT=8478
```

`scripts/demo_data.py` and `scripts/take_screenshots.py` refuse to run without both, so a
mistake stops them rather than touching real data. On Windows, set them in PowerShell:
`$env:JIMMYHQ_DATA = "C:\Temp\jimmyhq-demo"` and `$env:JIMMYHQ_PORT = "8478"`.

## 1. Choose the version

Change the version in one place for the project and keep the others in step. A test fails
(`tests/test_readme_current.py`) if the README, `pyproject.toml` and the package disagree.

- `pyproject.toml`: `version`
- `server/jimmyhq/__init__.py`: `__version__`
- `web/package.json` and its lock file: `version`

Then write the release's entry at the top of `CHANGELOG.md`: the version, today's date, a
short summary, and what was Added, Changed and Fixed. The website's dev log is made from this
file, and the site will not build for release while the newest entry says `unreleased` or its
version differs from the project's. Move what shipped out of `ROADMAP.md` (the website's
backlog). An app's own version is in its manifest and changes only when the app changes.

## 2. Install what the steps need

```bash
# step: install
uv sync --locked
(cd web && npm ci && npm run build)
```

## 3. Refresh the demo data

This fills a new folder with the invented household, through the product's own code. It
takes a few minutes. It stops by itself and prints what it could not fill.

```bash
# step: demo-data
uv run python scripts/demo_data.py
```

## 4. Refresh the screenshots

This starts JimmyHQ on the demo folder, drives a headless Chrome-family browser, writes the
pictures to `docs/screenshots/`, and stops JimmyHQ by the process id it started. Look at every
picture afterwards: no error messages, no empty screens where there should be content, nothing
cut off. `--only today,bench` takes just those.

```bash
# step: screenshots
uv run python scripts/take_screenshots.py
```

## 5. Refresh the README's generated parts

The apps table and the version are written between marker comments in `README.md`, from the
app manifests and `pyproject.toml`.

```bash
# step: readme-tables
uv run python scripts/readme_tables.py --write
uv run python scripts/readme_tables.py --check
```

## 6. Run the checks

```bash
# step: checks-server
uv run ruff check
uv run pytest -n auto
```

```bash
# step: checks-web
(cd web && npm run typecheck && npm test && npm run build)
```

`tests/test_readme_current.py` checks that the apps table, the version, every picture and every
link in the README are in step with the repository.

## 7. Build the website

The website under `site/` is made from the repository: the version, the apps, the dev log,
the backlog and the screenshots. `--strict` fails if a screenshot is missing or the dev log's
newest entry is not this version with a date.

```bash
# step: site
uv run python site/build.py --strict
```

The pages are written to `site/dist/`. Putting them on the web is step 10.

If you publish from a snapshot that leaves some files out (see `.publicignore`), the site
that goes on the web must be built again inside that public copy, after step 9. A site built
here would list apps and pictures the public repository does not have.

## 8. Build and commit

```bash
# step: build
uv run python scripts/build_release.py
```

This builds the web app, packs it into the Python package and writes the wheel and the source
archive to `dist/`. Then commit the changed version, dev log, README and pictures with a
signed-off commit (`git commit -s`), and open a pull request as in
[CONTRIBUTING.md](../CONTRIBUTING.md).

`uv run python scripts/release.py` runs every step above in order and stops at the first that
fails. `--from <step>` starts part-way; `--list` shows the steps.

## 9. Publish the code

Publishing is a person's decision, so a script never does it alone.

If you develop in one repository and publish from another (so the history, branches and
pull requests of the first stay private), copy the merged commit across as one new commit.
The script checks the files for personal details and likely secrets first, and never pushes:

```bash
# step: snapshot (needs a person)
uv run python scripts/publish_snapshot.py --to ../jimmyhq-public -m "0.1.0: first public version"
git -C ../jimmyhq-public push
```

If the repository has a `.publicignore` at its root, the paths it lists (one per line, `#`
for comments) are left out of the copy, and the README's generated parts are written again
for the apps that are left. A line that matches nothing stops the copy. Pictures show the
sidebar, so the ones in `docs/screenshots/` that go public are taken from such a copy, never
from a checkout that has more apps than the copy will.

`--terms <file>` names a file of your own private words, one per line, kept outside the
repository; the copy is refused while any of them appears.

If you publish from the repository you develop in, tag it instead:

```bash
# step: tag (needs a person)
git tag -s "v$(uv run python -c 'import jimmyhq; print(jimmyhq.__version__)')" -m "Release"
git push origin --tags
```

Then create a release on GitHub from that tag, with the files from `dist/` attached and the
notes from the dev log entry.

## 10. Publish the website and say so

```bash
# step: deploy-site (needs a person)
# Copy site/dist/ to wherever the site is served from. site/README.md says what a host needs.
# For a cPanel account, with the token in the environment or the macOS Keychain:
uv run python scripts/deploy_site_cpanel.py preview --host example.org --subdomain new
uv run python scripts/deploy_site_cpanel.py upload --host example.org --dir public_html
```

Add `--dist <public copy>/site/dist` to send the site built in the public copy. After an
upload, files an earlier build left behind and this one no longer has are moved into a
`site_dropped_…` folder in the account's home folder. Nothing is deleted.

Then tell people. A release worth announcing gets a short post written from its dev log
entry: what is new, in plain words, with a link to the code and to the website.
