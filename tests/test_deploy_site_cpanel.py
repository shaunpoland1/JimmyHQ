"""Putting the website into a cPanel account: what is sent, what is refused, and that the
token is never shown. cPanel here is a stand-in; nothing leaves the test."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import deploy_site_cpanel as tool  # noqa: E402

TOKEN = "sk-test-SECRET-0123456789"
LOGIN = tool.Login("someone", TOKEN)


class FakePanel:
    """Answers like cPanel's UAPI and remembers what it was asked."""

    def __init__(self):
        self.seen: list[httpx.Request] = []
        self.files: dict[str, bytes] = {}
        self.domains = {"example.org": "/srv/acct/public_html"}
        self.refuse_upload = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.headers.get("authorization") != f"cpanel someone:{TOKEN}":
            return httpx.Response(401, text="no")
        path, q = request.url.path, dict(request.url.params)
        if path.endswith("DomainInfo/single_domain_data"):
            root = self.domains.get(q["domain"])
            if root is None:
                return self.said(0, errors=["That domain is not on this account."])
            return self.said(1, {"documentroot": root, "homedir": "/srv/acct"})
        if path.endswith("SubDomain/addsubdomain"):
            # as real cPanel does: the folder lands inside public_html whatever was asked
            self.domains[f"{q['domain']}.{q['rootdomain']}"] = f"/srv/acct/public_html/{q['dir']}"
            return self.said(1, None)
        if path.endswith("Fileman/list_files"):
            inside = q["dir"].rstrip("/") + "/"
            names = {k[len(inside):].split("/")[0] for k in self.files if k.startswith(inside)}
            return self.said(1, [{"file": n} for n in sorted(names)])
        if path.endswith("Fileman/move_file"):
            src = q["source"].removeprefix("/srv/acct/")
            dest = q["destination"].removeprefix("/srv/acct/")
            leaf = src.rsplit("/", 1)[1]
            for key in [k for k in self.files if k == src or k.startswith(src + "/")]:
                self.files[f"{dest}/{leaf}{key[len(src):]}"] = self.files.pop(key)
            return self.said(1, {"src": src, "dest": dest})
        if path.endswith("Backup/fullbackup_to_homedir"):
            self.backups = getattr(self, "backups", 0) + 1
            return self.said(1, {"pid": "1"})
        if path.endswith("Fileman/upload_files"):
            body = request.read()
            folder = body.split(b'name="dir"\r\n\r\n')[1].split(b"\r\n")[0].decode()
            assert b'name="overwrite"\r\n\r\n1' in body
            names = [n.decode() for n in __import__("re").findall(rb'filename="([^"]+)"', body)]
            if self.refuse_upload:
                return self.said(1, {"succeeded": 0, "failed": len(names), "uploads": [
                    {"file": n, "status": 0, "reason": "The disk is full."} for n in names]})
            for n in names:
                self.files[f"{folder}/{n}"] = b"x"
            return self.said(1, {"succeeded": len(names), "failed": 0, "uploads": [
                {"file": n, "status": 1, "reason": ""} for n in names]})
        return httpx.Response(404, text="{}")

    @staticmethod
    def said(status: int, data=None, errors=None) -> httpx.Response:
        return httpx.Response(200, text=json.dumps({"status": status, "data": data,
                                                    "errors": errors}))


@pytest.fixture
def fake():
    return FakePanel()


@pytest.fixture
def panel(fake):
    made = tool.CPanel("example.org", LOGIN, transport=httpx.MockTransport(fake))
    yield made
    made.close()


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "shots").mkdir()
    for name in ("index.html", "404.html", "tour.html", "assets/site.css", "shots/a.png"):
        (root / name).write_text("x", encoding="utf-8")
    return root


def test_it_asks_cpanel_on_its_secure_port_with_the_token_in_the_header_only(panel, fake):
    assert panel.domain("example.org") == {"documentroot": "/srv/acct/public_html",
                                           "homedir": "/srv/acct"}
    asked = fake.seen[0]
    assert asked.url.scheme == "https" and asked.url.port == 2083
    assert asked.url.path == "/execute/DomainInfo/single_domain_data"
    assert TOKEN not in str(asked.url)


def test_every_file_goes_to_its_own_folder_with_the_htaccess_beside_the_pages(panel, fake, dist):
    said: list[str] = []
    count = tool.upload_site(panel, dist, "site_new", say=said.append)
    assert count == 6
    assert set(fake.files) == {
        "site_new/index.html", "site_new/404.html", "site_new/tour.html",
        "site_new/assets/site.css", "site_new/shots/a.png", "site_new/.htaccess"}
    assert "ErrorDocument 404 /404.html" in tool.HTACCESS and "-Indexes" in tool.HTACCESS
    assert "RewriteEngine Off" in tool.HTACCESS  # a parent folder's rules stay out


def test_many_or_large_files_are_sent_in_several_requests(tmp_path, monkeypatch):
    paths = []
    for n in range(20):
        path = tmp_path / f"f{n}.png"
        path.write_bytes(b"0" * 10)
        paths.append(path)
    assert [len(b) for b in tool.batches(paths)] == [8, 8, 4]
    monkeypatch.setattr(tool, "BATCH_BYTES", 25)
    assert [len(b) for b in tool.batches(paths[:5])] == [2, 2, 1]


def test_a_preview_makes_the_subdomain_once_and_never_touches_the_main_site(fake, dist, capsys,
                                                                            monkeypatch):
    monkeypatch.setenv("CPANEL_USER", "someone")
    monkeypatch.setenv("CPANEL_TOKEN", TOKEN)
    real = httpx.Client

    def client(**kw):
        return real(**{**kw, "transport": httpx.MockTransport(fake)})

    monkeypatch.setattr(tool.httpx, "Client", client)
    args = ["preview", "--host", "example.org", "--subdomain", "new", "--dist", str(dist)]
    assert tool.main(args) == 0
    assert tool.main(args) == 0
    made = [r for r in fake.seen if r.url.path.endswith("addsubdomain")]
    assert len(made) == 1 and dict(made[0].url.params)["dir"] == "site_new"
    assert all(name.startswith("public_html/site_new/") for name in fake.files)
    assert "public_html/site_new/robots.txt" in fake.files  # a preview asks not to be listed
    out = capsys.readouterr()
    assert "https://new.example.org/" in out.out and "already there" in out.out
    assert TOKEN not in out.out + out.err


def test_a_refusal_is_told_in_cpanels_words_and_never_shows_the_token(panel, fake, dist):
    fake.refuse_upload = True
    with pytest.raises(tool.Stop, match="The disk is full") as stop:
        tool.upload_site(panel, dist, "site_new", say=lambda _: None)
    assert TOKEN not in str(stop.value)
    with pytest.raises(tool.Stop, match="not on this account"):
        panel.domain("other.example")
    wrong = tool.CPanel("example.org", tool.Login("someone", "sk-test-SECRET-other"),
                        transport=httpx.MockTransport(fake))
    with pytest.raises(tool.Stop, match="refused the sign-in") as refused:
        wrong.domain("example.org")
    assert "SECRET" not in str(refused.value) and "Login(…)" == repr(LOGIN)


@pytest.mark.parametrize("folder", ["../etc", "/abs", "a b", "", ".ssh", "x/../../y"])
def test_a_folder_that_could_leave_the_site_is_refused_before_anything_is_sent(panel, fake,
                                                                               dist, folder):
    with pytest.raises(tool.Stop):
        tool.upload_site(panel, dist, folder, say=lambda _: None)
    assert fake.seen == []


def test_nothing_is_sent_without_a_built_site_or_a_login(panel, fake, tmp_path):
    with pytest.raises(tool.Stop, match="Build it first"):
        tool.upload_site(panel, tmp_path, "site_new")
    with pytest.raises(tool.Stop, match="Nothing was sent"):
        tool.login_from({}, None)
    assert fake.seen == []


def test_the_keychain_gives_the_user_and_token_without_either_reaching_the_command_line():
    calls = []

    def run(cmd, **_):
        calls.append(cmd)
        if "-w" in cmd:
            return SimpleNamespace(returncode=0, stdout=TOKEN + "\n")
        return SimpleNamespace(returncode=0, stdout='    "acct"<blob>="someone"\n')

    if sys.platform != "darwin":
        pytest.skip("the Keychain is on macOS only")
    assert tool.login_from({}, "jimmyhq-site-cpanel", run=run) == LOGIN
    assert all(TOKEN not in " ".join(c) and "someone" not in " ".join(c) for c in calls)


OLD = ["public_html/index.php", "public_html/.htaccess", "public_html/wp-admin/x.php",
       "public_html/.well-known/acme/t", "public_html/cgi-bin/keep",
       "public_html/site_new/index.html"]


def test_going_live_backs_up_moves_the_old_site_aside_and_deletes_nothing(panel, fake, dist):
    fake.files.update({name: b"old" for name in OLD})
    kept = tool.go_live(panel, "example.org", dist, "20270115_080000", say=lambda _: None)
    assert kept == "site_before_20270115_080000" and fake.backups == 1
    assert fake.files[f"{kept}/index.php"] == b"old"
    assert fake.files[f"{kept}/wp-admin/x.php"] == b"old" and f"{kept}/.htaccess" in fake.files
    # certificate checks, cgi-bin and the preview stay where they were
    for stays in OLD[3:]:
        assert fake.files[stays] == b"old"
    assert fake.files["public_html/index.html"] == b"x"
    assert "public_html/index.php" not in fake.files
    assert not any(r.url.path.endswith(("delete_file", "trash_file")) for r in fake.seen)


def test_undo_puts_the_old_files_back_and_keeps_the_new_ones_aside(panel, fake, dist):
    fake.files.update({name: b"old" for name in OLD})
    kept = tool.go_live(panel, "example.org", dist, "1", say=lambda _: None)
    aside = tool.undo(panel, "example.org", kept, "2", say=lambda _: None)
    assert fake.files["public_html/index.php"] == b"old"
    assert fake.files["public_html/wp-admin/x.php"] == b"old"
    assert fake.files[f"{aside}/index.html"] == b"x"
    assert "public_html/index.html" not in fake.files
    with pytest.raises(tool.Stop, match="site_before_"):
        tool.undo(panel, "example.org", "public_html", "3")


def test_the_live_site_is_never_replaced_without_saying_so_or_without_a_built_site(
        panel, fake, tmp_path, monkeypatch, capsys):
    fake.files.update({name: b"old" for name in OLD})
    before = dict(fake.files)
    with pytest.raises(tool.Stop, match="Build it first"):
        tool.go_live(panel, "example.org", tmp_path, "1")
    monkeypatch.setenv("CPANEL_USER", "someone")
    monkeypatch.setenv("CPANEL_TOKEN", TOKEN)
    real = httpx.Client
    monkeypatch.setattr(tool.httpx, "Client",
                        lambda **kw: real(**{**kw, "transport": httpx.MockTransport(fake)}))
    assert tool.main(["golive", "--host", "example.org"]) == 1
    assert "--replace-live-site" in capsys.readouterr().err
    assert fake.files == before
