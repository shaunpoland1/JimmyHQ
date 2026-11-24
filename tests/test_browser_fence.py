"""The network fence: every rule by itself, and the proxy that enforces them, spoken to
over a real socket on loopback. No browser is needed here."""

from __future__ import annotations

import asyncio

import pytest

from jimmyhq.browser import config, engine
from jimmyhq.browser.fence import (
    Denied,
    Fence,
    FenceProxy,
    hard_denied,
    host_matches,
    may_send,
    outside_allow_list,
    sends_data,
    shape,
)
from jimmyhq.settings import SettingError
from test_browser_fakes import Site
from test_lookup_fakes import PUBLIC, Names, private

TABLE = {"example.com": [PUBLIC], "www.example.com": [PUBLIC],
         "inside.example.com": [private(10, 0, 0, 5)],
         "tail.example.com": [private(100, 100, 1, 1)],
         "nat.example.com": [private(100, 64, 0, 9)],
         "link.example.com": ["169.254.169.254"], "loop.example.com": ["127.0.0.1"],
         "mixed.example.com": [PUBLIC, private(192, 168, 0, 9)],
         "chase.com": [PUBLIC], "secure.chase.com": [PUBLIC], "mail.google.com": [PUBLIC]}


def fence(core, **more) -> Fence:
    return Fence(core, resolver=Names(TABLE), **more)


def refusal(core, url: str, **more) -> Denied:
    with pytest.raises(Denied) as caught:
        asyncio.run(fence(core, **more).check(url))
    return caught.value


# --- the rules ------------------------------------------------------------------------------


def test_a_public_address_passes_and_is_pinned_to_the_address_that_was_checked(core):
    checked = asyncio.run(fence(core).check("https://example.com/page?x=1"))
    assert checked.address == PUBLIC and checked.host == "example.com" and checked.port == 443


@pytest.mark.parametrize("url", [
    "file:///etc/hosts", "chrome://settings", "ftp://example.com/x", "javascript:alert(1)",
    "data:text/html,hi", "view-source:https://example.com", "https://user:pw@example.com/",
    "http://2130706433/", "http://0x7f.1/", "",
])
def test_only_plain_http_and_https_addresses_have_a_shape_the_fence_accepts(core, url):
    assert refusal(core, url).rule == "browser_address_shape"
    with pytest.raises(Denied):
        shape(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/", "http://localhost/", "http://[::1]/", "http://loop.example.com/",
    "http://inside.example.com/", "http://tail.example.com/", "http://nat.example.com/",
    "http://link.example.com/latest/meta-data", "http://mixed.example.com/",
    "http://169.254.169.254/", "http://[::ffff:127.0.0.1]/",
])
def test_loopback_private_link_local_tailnet_and_nat_addresses_are_refused(core, url):
    assert refusal(core, url).rule == "browser_public_address"


def test_jimmyhqs_own_host_and_port_are_refused_whatever_the_owner_allows(tmp_path):
    from jimmyhq import config as app_config
    from jimmyhq.core import open_core

    core = open_core(app_config.Config(data=tmp_path, host="127.0.0.1", port=8440,
                                       allowed_hosts=("testserver",), remote_setup=True))
    try:
        core.settings.set("lookup.allow_private_hosts", ["127.0.0.1"], actor="owner")
        denied = refusal(core, "http://127.0.0.1:8440/api/settings")
        assert denied.rule == "browser_public_address" and "own address" in denied.sentence
        assert refusal(core, "http://testserver:8440/").rule == "browser_public_address"
        # With the owner's exception another port on that host is reachable; its own is not.
        asyncio.run(fence(core).check("http://127.0.0.1:8441/"))
    finally:
        core.close()


def test_a_host_pattern_covers_the_name_and_everything_under_it_and_nothing_else():
    assert host_matches("chase.com", "chase.com") and host_matches("secure.chase.com", "chase.com")
    assert not host_matches("notchase.com", "chase.com")
    assert not host_matches("chase.com.example.com", "chase.com")
    assert host_matches("my.bank", "*.bank") and not host_matches("bank.example.com", "*.bank")
    assert hard_denied("secure.chase.com", ["example.org", "chase.com"]) == "chase.com"
    assert hard_denied("example.com", ["chase.com"]) is None


def test_the_shipped_deny_list_covers_banks_payments_crypto_password_managers_and_webmail(core):
    listed = core.settings.get("browser.hard_deny")
    for host in ("secure.chase.com", "www.paypal.com", "accounts.coinbase.com",
                 "my.1password.com", "mail.google.com", "accounts.google.com",
                 "login.live.com", "www.schwab.com", "pass.proton.me"):
        assert hard_denied(host, listed), host
    assert hard_denied("www.google.com", listed) is None  # a search page is not a sign-in
    assert refusal(core, "https://secure.chase.com/login").rule == "browser_hard_deny"
    assert refusal(core, "https://mail.google.com/").rule == "browser_hard_deny"


def test_the_deny_list_is_the_owners_to_edit_and_takes_only_host_patterns(core):
    core.settings.set("browser.hard_deny", ["Example.com.", "*.example.org"], actor="owner")
    assert core.settings.get("browser.hard_deny") == ["example.com", "*.example.org"]
    assert refusal(core, "https://www.example.com/").rule == "browser_hard_deny"
    asyncio.run(fence(core).check("https://chase.com/"))  # no longer listed
    for bad in (["https://example.com"], ["example.com/login"], ["*"], ["a b"]):
        with pytest.raises(SettingError):
            core.settings.set("browser.hard_deny", bad, actor="owner")


def test_an_agent_with_an_allow_list_reaches_only_those_sites(core):
    assert outside_allow_list("example.com", None) is False
    assert outside_allow_list("example.com", []) is False
    assert outside_allow_list("www.example.com", ["example.com"]) is False
    assert outside_allow_list("chase.com", ["example.com"]) is True
    config.set_allow_hosts(core, "ada", ["example.com"], actor="owner")
    only = lambda: config.allow_hosts(core, "ada")  # noqa: E731
    asyncio.run(fence(core, allow_only=only).check("https://www.example.com/"))
    core.settings.set("browser.hard_deny", [], actor="owner")
    assert refusal(core, "https://chase.com/", allow_only=only).rule == "browser_allow_list"
    # An allow-list does not open a private address, and belongs to one agent only.
    config.set_allow_hosts(core, "ada", ["inside.example.com"], actor="owner")
    assert refusal(core, "http://inside.example.com/",
                   allow_only=only).rule == "browser_public_address"
    assert config.allow_hosts(core, "bo") == []


def test_only_the_owner_can_set_an_agents_allow_list(core):
    config.set_allow_hosts(core, "ada", ["example.com"], actor="owner")
    core.tape.print("browser.configured", {"agent_id": "ada", "allow_hosts": []},
                    actor="agent:ada", subject="agent:ada")
    assert config.allow_hosts(core, "ada") == ["example.com"]


def test_the_test_only_exception_lets_one_host_and_port_past_the_address_rule_alone(core):
    assert refusal(core, "http://127.0.0.1:5001/").rule == "browser_public_address"
    allowed = fence(core, also_allow=[("127.0.0.1", 5001)])
    assert asyncio.run(allowed.check("http://127.0.0.1:5001/x")).address == "127.0.0.1"
    with pytest.raises(Denied):
        asyncio.run(allowed.check("http://127.0.0.1:5002/"))
    core.settings.set("browser.hard_deny", ["127.0.0.1"], actor="owner")
    with pytest.raises(Denied):
        asyncio.run(allowed.check("http://127.0.0.1:5001/"))


def test_every_refusal_is_remembered_with_its_rule(core):
    f = fence(core)
    for url in ("https://chase.com/", "http://inside.example.com/"):
        with pytest.raises(Denied):
            asyncio.run(f.check(url))
    assert [(r.host, r.rule) for r in f.refused] == [
        ("chase.com", "browser_hard_deny"), ("inside.example.com", "browser_public_address")]
    assert f.refused_total == 2


def test_what_a_page_may_send_follows_the_level():
    for level in ("read", "approve", "alone"):
        assert may_send("GET", level) and may_send("HEAD", level) and may_send("OPTIONS", level)
    assert not may_send("POST", "read") and not may_send("DELETE", "read", permitted=True)
    assert not may_send("POST", "approve")  # nothing the owner approved
    assert may_send("POST", "approve", permitted=True)
    assert may_send("POST", "alone") and may_send("PUT", "alone")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post", "PROPFIND"])
def test_at_ask_each_time_any_request_that_sends_waits_for_the_owner_page_load_or_script(method):
    """A page's own script sending data in the background is held like a form: there is
    no longer a kind of request that is let through because a page made it."""
    assert not may_send(method, "approve")
    assert may_send(method, "approve", permitted=True)


def test_a_get_with_a_body_counts_as_sending_and_is_held_like_a_post():
    assert sends_data("GET", has_body=True) and sends_data("HEAD", has_body=True)
    assert not sends_data("GET") and not sends_data("options")
    assert not may_send("GET", "approve", has_body=True)
    assert not may_send("GET", "read", permitted=True, has_body=True)
    assert may_send("GET", "approve", permitted=True, has_body=True)
    assert may_send("GET", "alone", has_body=True)


# --- how the browser is started -------------------------------------------------------------


def test_the_browser_is_started_with_its_own_profile_the_proxy_and_no_way_round_it(tmp_path):
    flags = engine.flags(tmp_path / "profile", 4321, 1280, 800)
    assert f"--user-data-dir={tmp_path / 'profile'}" in flags
    assert "--proxy-server=http://127.0.0.1:4321" in flags
    assert "--proxy-bypass-list=<-loopback>" in flags  # loopback goes through the fence too
    assert any(f.startswith("--host-resolver-rules=MAP * ~NOTFOUND") for f in flags)
    assert "--remote-debugging-pipe" in flags
    assert not any(f.startswith("--remote-debugging-port") for f in flags)
    assert "--disable-extensions" in flags and "--password-store=basic" in flags
    assert not any("load-extension" in f for f in flags)
    assert flags[-1] == "about:blank"


def test_a_machine_without_a_browser_says_what_to_install(core, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "candidates", lambda *a, **k: [])
    monkeypatch.setattr(engine.shutil, "which", lambda name: None)
    assert engine.find(core) is None
    assert "Install Google Chrome, Chromium, Microsoft Edge or Brave" in engine.problem(core)
    core.settings.set("browser.executable", str(tmp_path / "missing"), actor="owner")
    assert "is not a file on this machine" in engine.problem(core)
    program = tmp_path / "chromium"
    program.write_bytes(b"")
    core.settings.set("browser.executable", str(program), actor="owner")
    assert engine.find(core) == program and engine.problem(core) is None


def test_the_usual_places_are_looked_in_on_each_system():
    mac = engine.candidates("darwin", {})
    assert any(p.name == "Google Chrome" for p in mac) and any(p.name == "Chromium" for p in mac)
    win = engine.candidates("win32", {"PROGRAMFILES": "C:/Program Files"})
    assert any(p.name == "msedge.exe" for p in win) and any(p.name == "chrome.exe" for p in win)
    assert engine.candidates("linux", {}) == []  # found on the PATH instead


# --- the proxy, over a real socket ------------------------------------------------------------


async def ask(port: int, request: str) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request.encode("latin-1"))
    await writer.drain()
    data = await asyncio.wait_for(reader.read(-1), 10)
    writer.close()
    return data


def get(url: str, host: str) -> str:
    return f"GET {url} HTTP/1.1\r\nHost: {host}\r\nProxy-Connection: keep-alive\r\n\r\n"


def test_the_proxy_fetches_an_allowed_page_and_refuses_the_same_host_without_the_exception(core):
    async def run():
        async with Site({"/": (200, {}, "<p>hello from the test site</p>")}) as web:
            here = f"127.0.0.1:{web.port}"
            closed = FenceProxy(fence(core))
            opened = FenceProxy(fence(core, also_allow=[("127.0.0.1", web.port)]))
            try:
                refused = await ask(await closed.start(), get(f"http://{here}/", here))
                fetched = await ask(await opened.start(), get(f"http://{here}/?a=1", here))
            finally:
                await closed.stop()
                await opened.stop()
            return refused, fetched, web.seen
    refused, fetched, seen = asyncio.run(run())
    assert refused.startswith(b"HTTP/1.1 403")
    assert b"X-JimmyHQ-Fence: browser_public_address" in refused
    assert fetched.startswith(b"HTTP/1.1 200") and b"hello from the test site" in fetched
    # One request reached the site, in origin form, without the proxy's own headers.
    assert [(m, t) for m, t, _, _ in seen] == [("GET", "/?a=1")]
    assert "proxy-connection" not in seen[0][3] and seen[0][3]["connection"] == "close"


def test_the_proxy_refuses_a_denied_host_a_private_tunnel_and_a_request_that_names_no_site(core):
    async def run():
        proxy = FenceProxy(fence(core))
        port = await proxy.start()
        try:
            return (await ask(port, get("http://chase.com/", "chase.com")),
                    await ask(port, "CONNECT inside.example.com:443 HTTP/1.1\r\n\r\n"),
                    await ask(port, "CONNECT mail.google.com:443 HTTP/1.1\r\n\r\n"),
                    await ask(port, "CONNECT 127.0.0.1:22 HTTP/1.1\r\n\r\n"),
                    await ask(port, "GET /etc/passwd HTTP/1.1\r\nHost: x\r\n\r\n"),
                    await ask(port, get("ftp://example.com/", "example.com")))
        finally:
            await proxy.stop()
    denied, tunnel, mail, ssh, local, ftp = asyncio.run(run())
    assert b"403" in denied.split(b"\r\n")[0] and b"browser_hard_deny" in denied
    assert b"403" in tunnel.split(b"\r\n")[0] and b"browser_public_address" in tunnel
    assert b"403" in mail.split(b"\r\n")[0] and b"browser_hard_deny" in mail
    assert b"403" in ssh.split(b"\r\n")[0]
    assert b"400" in local.split(b"\r\n")[0] and b"400" in ftp.split(b"\r\n")[0]


def test_the_proxy_tunnels_to_an_allowed_host_and_connects_to_the_checked_address(core):
    """A tunnel is how https travels. The name is resolved by the fence, once, and the
    proxy connects to that address: here a name whose only route is the test site."""
    async def run():
        async with Site({"/x": (200, {}, "through the tunnel")}) as web:
            names = Names({"pinned.example.com": ["127.0.0.1"]})
            f = Fence(core, resolver=names, also_allow=[("pinned.example.com", web.port)])
            proxy = FenceProxy(f)
            port = await proxy.start()
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(f"CONNECT pinned.example.com:{web.port} HTTP/1.1\r\n\r\n".encode())
                opened = await reader.readuntil(b"\r\n\r\n")
                writer.write(b"GET /x HTTP/1.1\r\nHost: pinned.example.com\r\n\r\n")
                await writer.drain()
                answer = await asyncio.wait_for(reader.read(-1), 10)
                writer.close()
            finally:
                await proxy.stop()
            return opened, answer
    opened, answer = asyncio.run(run())
    assert opened.startswith(b"HTTP/1.1 200") and b"through the tunnel" in answer


def test_a_redirect_to_a_private_address_is_a_new_request_and_is_refused(core):
    """The proxy does not follow redirects: the browser asks again, and that request
    is checked like any other."""
    async def run():
        async with Site({"/go": (302, {"Location": "http://inside.example.com/admin"}, "")}) as web:
            here = f"127.0.0.1:{web.port}"
            proxy = FenceProxy(fence(core, also_allow=[("127.0.0.1", web.port)]))
            port = await proxy.start()
            try:
                first = await ask(port, get(f"http://{here}/go", here))
                second = await ask(port, get("http://inside.example.com/admin",
                                             "inside.example.com"))
            finally:
                await proxy.stop()
            return first, second
    first, second = asyncio.run(run())
    assert first.startswith(b"HTTP/1.1 302") and b"inside.example.com" in first
    assert second.startswith(b"HTTP/1.1 403") and b"browser_public_address" in second
