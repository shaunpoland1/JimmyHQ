"""Sites: the places Articles writes for, as records the owner edits.

A site has a name, who it writes for, how it sounds (a register sentence and writing
samples), the shape an article usually takes, in the owner's own words (its length, its
headings, a closing note), where it publishes, and the time zone its times are read in
(empty: the owner's own). The shape is guidance the writer is
given, never a template the code enforces: nothing here counts words or headings.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from articles.store import Store

NO_SITES = ("There are no sites yet. Add one under Sites: give it a name, say who it writes "
            "for and how it sounds, and choose where it publishes. Practice publishes "
            "nothing, so you can try everything first.")

PUBLISHERS = ("practice", "by_hand", "wordpress")
TEXT_FIELDS = {"audience": 500, "register": 1000, "samples": 20000, "length": 300,
               "headings": 300, "closing": 1000}
NAME_MOST = 120


class SiteError(ValueError):
    """The site was not saved. The message says why and what to do."""


def check_name(value: Any) -> str:
    name = " ".join(str(value or "").split())
    if not name:
        raise SiteError("Give the site a name. Nothing was saved.")
    if len(name) > NAME_MOST:
        raise SiteError(f"A site's name is at most {NAME_MOST} characters. Nothing was saved.")
    return name


def check_publisher(value: Any) -> str:
    chosen = str(value or "practice")
    if chosen not in PUBLISHERS:
        raise SiteError("Choose where the site publishes: Practice, By hand or WordPress. "
                        "Nothing was saved.")
    return chosen


def check_wordpress_url(value: Any) -> str:
    """Rule ``a_site_names_one_host``: which of the owner's own addresses the site
    publishes to, as https and a host only (a bare host is taken too), or empty. Whether
    Articles was given that host is asked when publishing (``WordPress.needs``), since the
    owner may add or remove it at any time."""
    url = str(value or "").strip().rstrip("/")
    if url and "://" not in url:
        url = f"https://{url}"
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.path not in ("", "/") \
            or parts.query or parts.username or parts.port:
        raise SiteError("Give the WordPress site's address as https:// and its host only, "
                        "such as https://blog.example.com. Nothing was saved.")
    return f"https://{parts.hostname.lower()}"


def check_timezone(value: Any) -> str:
    """An IANA time zone name, or empty for the owner's own."""
    name = str(value or "").strip()
    if not name:
        return ""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise SiteError(f"Articles does not know the time zone {name[:60]!r}. Choose one from "
                        "the list, or Use mine. Nothing was saved.") from None
    return name


def save_site(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    """Add a site, or change one (``id``). Returns it."""
    values: dict[str, Any] = {"name": check_name(args.get("name"))}
    for key, most in TEXT_FIELDS.items():
        text = str(args.get(key) or "").replace("\r\n", "\n").strip()
        if len(text) > most:
            raise SiteError(f"\"{key}\" is at most {most} characters. Nothing was saved.")
        values[key] = text
    values["publisher"] = check_publisher(args.get("publisher"))
    values["wordpress_url"] = check_wordpress_url(args.get("wordpress_host")
                                                  or args.get("wordpress_url"))
    values["timezone"] = check_timezone(args.get("timezone"))
    if values["publisher"] == "wordpress" and not values["wordpress_url"]:
        raise SiteError("A site that publishes to WordPress needs one of your own addresses, "
                        "such as https://blog.example.com: add it under Settings, Apps, "
                        "Articles, then choose it here. Nothing was saved.")
    clash = store.one("SELECT id FROM site WHERE name=?", (values["name"],))
    now = store.now()
    if args.get("id") is not None:
        site_id = int(args["id"])
        if store.site(site_id) is None:
            raise SiteError("There is no such site. Nothing was saved.")
        if clash and clash["id"] != site_id:
            raise SiteError("Another site has that name. Choose another. Nothing was saved.")
        store.update("site", site_id, {**values, "updated": now})
    else:
        if clash:
            raise SiteError("A site with that name exists. Choose another, or change that "
                            "one. Nothing was saved.")
        site_id = store.insert("site", {**values, "created": now, "updated": now})
    return store.site(site_id) or {}


def guidance(site: dict[str, Any]) -> list[str]:
    """The site's shape, in the owner's words, as lines for the writer."""
    lines = []
    if site.get("length"):
        lines.append(f"Length, in the owner's words: {site['length']}")
    if site.get("headings"):
        lines.append(f"Headings, in the owner's words: {site['headings']}")
    if site.get("closing"):
        lines.append(f"A closing note, in the owner's words: {site['closing']}")
    return lines
