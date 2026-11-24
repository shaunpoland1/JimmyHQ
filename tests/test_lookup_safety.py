"""Where web lookup may go: the address rules, one by one. A model chooses the
address, so every rule here is a way a page could otherwise reach inside."""

from __future__ import annotations

import asyncio
import ipaddress

import pytest

from jimmyhq import config, lookup
from jimmyhq.core import open_core
from jimmyhq.lookup import safety
from test_lookup_fakes import OTHER_PUBLIC, PUBLIC, Names, private

LOOPBACK = "127.0.0.1"
PRIVATE_ADDRESSES = [
    LOOPBACK, "127.8.9.1", private(10, 0, 0, 5), private(172, 16, 4, 4),
    private(192, 168, 1, 20), "169.254.169.254", private(100, 64, 0, 1),
    private(100, 100, 50, 2), "0.0.0.0", "224.0.0.1", "239.255.255.250", "240.0.0.1",
    "255.255.255.255", "192.0.2.7", "198.51.100.7", "203.0.113.7", "198.18.0.1",
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456:789a::1", "ff02::1", "ff0e::1",
    "2001:db8::1", "fec0::1",
    # An IPv4 address carried inside an IPv6 one.
    "::ffff:127.0.0.1", f"::ffff:{private(10, 0, 0, 5)}", "::ffff:169.254.169.254",
    "::127.0.0.1", "64:ff9b::7f00:1", "64:ff9b:1::1", "2002:7f00:1::1",
    f"2002:{0x0a00:x}:5::1",
]


def check(core, url, table=None):
    return asyncio.run(safety.check(core, url, Names(table)))


def refused(core, url, table=None) -> lookup.WebError:
    with pytest.raises(lookup.WebError) as caught:
        check(core, url, table)
    return caught.value


@pytest.mark.parametrize("address", PRIVATE_ADDRESSES)
def test_an_address_that_is_not_public_is_not_public(address):
    assert safety.is_public(ipaddress.ip_address(address)) is False


@pytest.mark.parametrize("address", [PUBLIC, OTHER_PUBLIC, "2606:4700::1111",
                                     f"::ffff:{PUBLIC}", "64:ff9b::c058:630a"])
def test_a_public_address_is_public(address):
    assert safety.is_public(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize("address", PRIVATE_ADDRESSES)
def test_a_name_that_resolves_to_an_address_that_is_not_public_is_refused(core, address):
    error = refused(core, "https://inside.example.com/admin", {"inside.example.com": [address]})
    assert error.kind == "refused"
    # The sentence says what to do about it, and where.
    assert "inside.example.com" in error.message and "Settings, Web lookup" in error.message


@pytest.mark.parametrize("address", PRIVATE_ADDRESSES)
def test_an_address_that_is_not_public_is_refused_when_given_as_the_host_itself(core, address):
    host = f"[{address}]" if ":" in address else address
    names = Names()
    with pytest.raises(lookup.WebError) as caught:
        asyncio.run(safety.check(core, f"http://{host}/", names))
    assert caught.value.kind == "refused" and names.asked == []


@pytest.mark.parametrize("host", [
    "2130706433",  # 127.0.0.1 as one decimal number
    "0x7f000001", "0x7f.0.0.1", "0x7F.0x0.0x0.0x1",  # hex
    "0177.0.0.1", "017700000001", "0177.0.0.01",  # octal
    "127.1", "127.0.1", "10.1", "192.168.257",  # fewer than four parts
    "1.2.3.4.5", "0", "1.1.1.0x1", "0x",
    f"{PUBLIC.rsplit('.', 1)[0]}.010",  # even a public address, written oddly
])
def test_a_host_written_as_a_number_in_an_odd_form_is_refused_without_being_looked_up(
        core, host):
    names = Names()
    with pytest.raises(lookup.WebError) as caught:
        asyncio.run(safety.check(core, f"http://{host}/", names))
    assert caught.value.kind == "refused" and "unusual form" in caught.value.message
    assert names.asked == []


def test_a_final_dot_does_not_hide_an_address(core):
    assert refused(core, "http://127.0.0.1./").kind == "refused"
    assert refused(core, "http://localhost./", {"localhost": [LOOPBACK]}).kind == "refused"


@pytest.mark.parametrize("url", [
    "ftp://example.com/file", "file:///etc/passwd", "gopher://example.com/",
    "javascript:alert(1)", "data:text/html,hello", "ws://example.com/", "//example.com/",
    "example.com/page", "", "   ", None, 5,
])
def test_only_http_and_https_addresses_are_read(core, url):
    assert refused(core, url).kind == "bad_url"


@pytest.mark.parametrize("url", [
    "https://sam:secret@example.com/", "https://sam@example.com/", "https://@example.com/",
    "http://example.com@127.0.0.1/",
])
def test_an_address_with_a_user_name_or_password_is_refused(core, url):
    error = refused(core, url)
    assert error.kind == "bad_url" and "user name or password" in error.message


@pytest.mark.parametrize("url", [
    "http://example.com\\@127.0.0.1/", "http://exa mple.com/", "http://example.com/\n/x",
    "http://example.com/a\tb", "http://example.com:99999/", "http://example.com:0/",
    "http://example.com:port/", "http:///nothing", "http://[::1/", "http://-bad-.example.com/",
    "http://exa$mple.com/", "http://" + "a" * 5000 + ".example.com/",
    "http://[fe80::1%25eth0]/",
])
def test_a_malformed_address_is_refused_with_a_sentence(core, url):
    error = refused(core, url)
    assert error.kind in ("bad_url", "refused") and error.message.endswith(".")


def test_a_public_page_passes_and_the_address_that_was_checked_is_the_one_to_connect_to(core):
    checked = check(core, "HTTPS://Example.COM/a/b?q=1#part")
    assert checked.url == "https://example.com/a/b?q=1"  # no fragment; the name in lower case
    assert (checked.host, checked.port, checked.address) == ("example.com", 443, PUBLIC)
    assert checked.connect_url == f"https://{PUBLIC}:443/a/b?q=1"
    assert checked.host_header == "example.com"
    other = check(core, "http://example.com:8080")
    assert other.connect_url == f"http://{PUBLIC}:8080/" and other.host_header == "example.com:8080"
    six = check(core, "http://six.example.com/", {"six.example.com": ["2606:4700::1111"]})
    assert six.connect_url == "http://[2606:4700::1111]:80/"


def test_a_name_is_resolved_once_so_a_second_answer_cannot_be_swapped_in(core):
    """DNS rebinding: public for the check, private a moment later. The fetch
    connects to ``Checked.address`` and never asks again."""
    answers = iter([[PUBLIC], [LOOPBACK], [LOOPBACK]])
    names = Names({"rebind.example.com": lambda: next(answers)})
    checked = asyncio.run(safety.check(core, "http://rebind.example.com/", names))
    assert checked.address == PUBLIC and names.asked == ["rebind.example.com"]


def test_a_name_with_one_private_address_among_public_ones_is_refused(core):
    table = {"mixed.example.com": [PUBLIC, private(10, 0, 0, 5)]}
    assert refused(core, "http://mixed.example.com/", table).kind == "refused"
    table = {"mixed.example.com": [private(192, 168, 0, 9), PUBLIC]}
    assert refused(core, "http://mixed.example.com/", table).kind == "refused"


def test_a_name_that_does_not_resolve_says_so(core):
    error = refused(core, "http://nowhere.example.com/", {})
    assert error.kind == "unreachable" and "nowhere.example.com" in error.message
    assert refused(core, "http://junk.example.com/", {"junk.example.com": ["not-an-ip"]}
                   ).kind == "unreachable"


def test_localhost_and_other_names_for_this_machine_are_refused(core):
    for name in ("localhost", "localhost.", "LOCALHOST", "app.localhost"):
        assert refused(core, f"http://{name}/", {name.lower().rstrip("."): [LOOPBACK]}
                       ).kind == "refused"


def test_a_host_the_owner_allows_may_be_private_and_only_that_host(core):
    inside = private(10, 0, 0, 5)
    table = {"wiki.example.com": [inside], "other.example.com": [inside]}
    core.settings.set("lookup.allow_private_hosts", ["Wiki.Example.com."], actor="owner")
    assert core.settings.get("lookup.allow_private_hosts") == ["wiki.example.com"]
    checked = check(core, "http://wiki.example.com/page", table)
    assert checked.address == inside and checked.allowed_private is True
    assert refused(core, "http://other.example.com/page", table).kind == "refused"
    assert refused(core, "http://sub.wiki.example.com/", {"sub.wiki.example.com": [inside]}
                   ).kind == "refused"
    # Allowing a host bends the address rule and no other.
    assert refused(core, "ftp://wiki.example.com/", table).kind == "bad_url"
    assert refused(core, "http://sam:pw@" + "wiki.example.com/", table).kind == "bad_url"
    # An address can be allowed by itself, too.
    core.settings.set("lookup.allow_private_hosts", [inside], actor="owner")
    assert check(core, f"http://{inside}/").address == inside


@pytest.mark.parametrize("bad", ["http://wiki.example.com", "wiki.example.com/page",
                                 "sam@" + "wiki.example.com", "two words"])
def test_the_allowed_hosts_setting_takes_names_only(core, bad):
    from jimmyhq.settings import SettingError  # noqa: PLC0415

    with pytest.raises(SettingError, match="name or an address on its own"):
        core.settings.set("lookup.allow_private_hosts", [bad], actor="owner")


@pytest.fixture
def served(tmp_path):
    """A core that believes it is being served on port 8440."""
    cfg = config.Config(data=tmp_path, host="127.0.0.1", port=8440,
                        allowed_hosts=("hq.example.com", "tail.example.com:8440"))
    core = open_core(cfg)
    yield core
    core.close()


def test_jimmyhq_never_reads_its_own_address_whatever_the_owner_has_allowed(served):
    inside = private(192, 168, 1, 20)
    table = {"localhost": [LOOPBACK], "hq.example.com": [PUBLIC], "box.example.com": [inside],
             "tail.example.com": [private(100, 100, 1, 1)]}
    served.settings.set("lookup.allow_private_hosts",
                        ["localhost", LOOPBACK, "box.example.com", "::1"], actor="owner")
    for url in ("http://127.0.0.1:8440/api/status", "http://localhost:8440/",
                "http://[::1]:8440/", "http://box.example.com:8440/",
                # A name the owner reaches JimmyHQ by, even where that name is public.
                "http://hq.example.com:8440/api/chat/conversations",
                "http://tail.example.com:8440/"):
        error = refused(served, url, table)
        assert error.kind == "refused" and "own address" in error.message, url
    # The same allowed hosts on another port are some other service: allowed.
    assert check(served, "http://box.example.com:3000/", table).address == inside
    assert check(served, "http://localhost:9000/", table).address == LOOPBACK


def test_a_name_added_under_other_names_for_this_server_is_refused_on_its_port(served):
    served.settings.set("access.allowed_hosts", ["Home.Example.com"], actor="owner")
    error = refused(served, "http://home.example.com:8440/", {"home.example.com": [PUBLIC]})
    assert "own address" in error.message
