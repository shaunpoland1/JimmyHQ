"""WordPress: a draft on the owner's own WordPress site, through its REST API.

**Where.** The site's address is a host the owner gave Articles himself, on its page under
Settings, Apps ("Your own addresses": the manifest's ``owner_hosts``). Each Articles site
names which of those it publishes to (``wordpress_url``, https and the host). The user
name and the application password are that host's own secrets (``wordpress_user`` and
``wordpress_password``, kept for each host), typed in beside its address; Articles reads
them only to sign one request, and never keeps, logs or repeats them.

**What is sent.** ``POST https://<host>/wp-json/wp/v2/posts`` with HTTP basic sign-in
(the user name and application password, which WordPress has taken since 5.6) and JSON
``{"title", "content", "status": "draft"}``: the article's sanitised HTML with its
sources list, without its title heading (WordPress shows the title itself). It is always a
draft: the owner publishes it on WordPress, then presses Published here. A header image,
when there is one, is uploaded first through the same host (``POST /wp-json/wp/v2/media``,
the picture as the whole body, its name in ``Content-Disposition``) and named as the
draft's ``featured_media``; if that fails the draft goes without it, and says so.

**An edit after sending** updates that draft (``POST /wp-json/wp/v2/posts/<id>`` with the
title and content, never a status) only when the site still says it is a draft on the same
host; otherwise nothing is changed there and the owner is told so.

**Answers.** Only a definite answer counts (``read_answer``): a 2xx with the post's id is
sent; a definite no (a 4xx, a redirect, the request refused here before it left) is
refused with the site's own message; anything else (no answer, a timeout, a 5xx, a page
where JSON was expected after a 2xx) is unsure, and nothing retries by itself. A redirect
is never followed: JimmyHQ hands it back, and a redirect to another host is refused.
"""

from __future__ import annotations

import base64
import html
import json
import re
from typing import Any
from urllib.parse import urlsplit

from jimmyhq_app import CallError

from articles.publishers import Outcome, Publication

USER_SECRET = "wordpress_user"  # noqa: S105 - a name the manifest declares
PASSWORD_SECRET = "wordpress_password"  # noqa: S105 - the name the manifest declares, not a value
POSTS = "/wp-json/wp/v2/posts"
MEDIA = "/wp-json/wp/v2/media"
MESSAGE_MOST = 300
HIDDEN = "[hidden]"

# Error codes WordPress answers when the sign-in is not accepted. (From WordPress's own
# code; the reference lists none, so an unknown code with 401 counts as one too.)
SIGN_IN_CODES = frozenset({"incorrect_password", "invalid_username", "invalid_email",
                           "rest_not_logged_in", "application_passwords_disabled",
                           "application_passwords_disabled_for_user", "rest_forbidden"})
CANNOT_CODES = frozenset({"rest_cannot_create", "rest_cannot_edit", "rest_cannot_publish",
                          "rest_cannot_edit_others"})
OFF_CODES = frozenset({"rest_no_route", "rest_disabled", "rest_api_disabled",
                       "rest_login_required", "rest_cannot_access"})


def host_of(site: dict[str, Any]) -> str:
    return (urlsplit(site.get("wordpress_url") or "").hostname or "").lower()


# --- reading what came back, one rule each -------------------------------------------------


def scrub(text: str, hidden: list[str]) -> str:
    """Rule ``the_password_is_never_repeated``: anything the site says back, with every
    secret (and the sign-in made from them) taken out."""
    for value in hidden:
        if value:
            text = text.replace(value, HIDDEN)
    return text


def site_message(data: Any, hidden: list[str]) -> str:
    """The site's own words, as plain text on one line, short, with no secret in them."""
    said = data.get("message") if isinstance(data, dict) else ""
    if not isinstance(said, str):
        return ""
    plain = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", said)).split())
    return scrub(plain, hidden)[:MESSAGE_MOST]


def is_json(reply: dict[str, Any]) -> bool:
    kind = str((reply.get("headers") or {}).get("content-type") or "").lower()
    return "json" in kind or "json" in reply


def body_of(reply: dict[str, Any]) -> Any:
    """The answer's JSON, or None when it is not JSON (a web page, nothing, garbage)."""
    if isinstance(reply.get("json"), dict | list):
        return reply["json"]
    try:
        return json.loads(str(reply.get("text") or ""))
    except ValueError:
        return None


def looks_like_a_page(reply: dict[str, Any]) -> bool:
    """Rule ``a_page_where_json_was_expected``: a security plugin or a firewall answering
    with a web page of its own, instead of the REST API's JSON."""
    kind = str((reply.get("headers") or {}).get("content-type") or "").lower()
    text = str(reply.get("text") or "").lstrip()[:200].lower()
    return "html" in kind or text.startswith(("<!doctype", "<html", "<"))


def redirect_sentence(host: str, location: str) -> str:
    """Rule ``a_redirect_is_never_followed``: what the owner is told about one, by where
    it points."""
    goes = (urlsplit(location).hostname or "").lower()
    if goes and goes != host:
        return (f"Your site at {host} sent the request on to another address, {goes}, which "
                "Articles does not follow, so nothing was sent there. If your site has moved, "
                "add its new address under Settings, Apps, Articles, and choose it for this "
                "site.")
    return (f"Your site at {host} answered with a redirect instead of taking the draft, so "
            "nothing was made there. Check that its address is the one WordPress uses, with "
            "https.")


def refusal_sentence(status: int, data: Any, host: str, hidden: list[str]) -> str:
    """Rule ``the_usual_failures_in_plain_words``: a definite no, said for the owner."""
    code = str(data.get("code") or "") if isinstance(data, dict) else ""
    said = site_message(data, hidden)
    quoted = f" Your site said: “{said}”" if said else ""
    where = f"under Settings, Apps, Articles, beside {host}"
    if code in SIGN_IN_CODES or (status == 401 and code not in CANNOT_CODES | OFF_CODES):
        return (f"WordPress at {host} did not accept the user name and application password "
                f"(HTTP {status}), so nothing was made there.{quoted} Check both {where}; an "
                "application password is made on your site under Users, Profile.")
    if code in CANNOT_CODES:
        return (f"WordPress at {host} accepted the sign-in but that user may not write posts "
                f"(HTTP {status}), so nothing was made there.{quoted} Use a user who can, "
                f"such as an author or editor, {where}.")
    if code in OFF_CODES or status == 404:
        return (f"The REST API of your site at {host} did not answer (HTTP {status}), so "
                f"nothing was sent.{quoted} It may be switched off by a plugin or a setting: "
                "switch it back on for signed-in users, or choose By hand for this site.")
    if status == 429:
        return (f"Your site at {host} asked Articles to wait (HTTP 429), so nothing was made "
                f"there.{quoted} Press Publish again later.")
    return f"WordPress at {host} refused the draft (HTTP {status}), so nothing was made there." \
           f"{quoted}"


def read_answer(reply: Any, host: str, hidden: list[str]) -> Outcome:
    """Rule ``only_a_definite_answer_counts``: 2xx with the post's id is sent; a definite
    no is refused with the site's words; anything else is unsure."""
    if not isinstance(reply, dict) or not isinstance(reply.get("status"), int):
        return Outcome("unsure", "WordPress's answer could not be read. Look on your site "
                       "before trying again.")
    status = reply["status"]
    data = body_of(reply)
    if 300 <= status < 400:
        location = str((reply.get("headers") or {}).get("location") or "")
        return Outcome("refused", redirect_sentence(host, location))
    if 200 <= status < 300:
        if isinstance(data, dict) and isinstance(data.get("id"), int) \
                and not isinstance(data.get("id"), bool):
            link = str(data.get("link") or "")
            return Outcome("sent", f"A draft is on your WordPress site at {host}. Publish it "
                           "there, then press Published here.",
                           {"id": data["id"], "link": link[:500] if link.startswith(
                               "https://") else "", "host": host})
        if looks_like_a_page(reply):
            return Outcome("unsure", f"Your site at {host} answered with a web page instead "
                           "of WordPress's own answer, often a security plugin or a firewall "
                           "in the way. The draft may or may not have been made: look on your "
                           "site before trying again, and let the REST API through for "
                           "signed-in users.")
        return Outcome("unsure", f"WordPress at {host} said yes but did not say which draft "
                       "it made. Look on your site before trying again.")
    if 400 <= status < 500 and status != 408:
        if looks_like_a_page(reply) and not is_json(reply):
            return Outcome("refused", f"Your site at {host} answered with a web page instead "
                           f"of WordPress's own answer (HTTP {status}), often a security "
                           "plugin or a firewall, so nothing was made there. Let the REST API "
                           "through for signed-in users, or choose By hand for this site.")
        return Outcome("refused", refusal_sentence(status, data, host, hidden))
    return Outcome("unsure", f"WordPress at {host} gave no clear answer (HTTP {status}). Look "
                   "on your site before trying again.")


def failed_call(error: CallError, host: str, hidden: list[str]) -> Outcome:
    """Rule ``refused_here_is_a_definite_no``: JimmyHQ refusing the request before it
    left (a host not given, an address now private, a bad request) is refused; a request
    that may have reached the site (no answer, a broken connection) is unsure."""
    said = scrub(str(error.message or ""), hidden)[:MESSAGE_MOST]
    if error.code in ("not_allowed", "invalid", "not_found", "quota"):
        return Outcome("refused", f"JimmyHQ did not send the draft to {host}: {said}")
    return Outcome("unsure", f"Your site at {host} gave no answer ({said}) Look on your site "
                   "before trying again.")


# --- the publisher --------------------------------------------------------------------------


class WordPress:
    id = "wordpress"
    label = "WordPress"
    sends_out = True

    def __init__(self, ctx: Any):
        self.ctx = ctx

    def _secret(self, name: str, host: str) -> str | None:
        try:
            return self.ctx.app.secrets.get(name, host=host)
        except CallError:
            return None

    def needs(self, site: dict[str, Any]) -> str | None:
        host = host_of(site)
        if not host:
            return ("This site has no WordPress address. Choose one of your own addresses "
                    "for it under Sites.")
        if host not in self.ctx.owner_hosts():
            return (f"Articles has not been given {host}. Add it under Settings, Apps, "
                    "Articles, \"Your own addresses\", or choose By hand for this site.")
        if not self._secret(USER_SECRET, host):
            return (f"Type your WordPress user name under Settings, Apps, Articles, beside "
                    f"{host}.")
        if not self._secret(PASSWORD_SECRET, host):
            return (f"Type your WordPress application password under Settings, Apps, "
                    f"Articles, beside {host}. Make one on your site under Users, Profile.")
        return None

    def _sign_in(self, host: str) -> tuple[dict[str, str], list[str]]:
        """The Authorization header for one request, and every form of the secrets that
        must never be repeated."""
        user = self._secret(USER_SECRET, host) or ""
        password = self._secret(PASSWORD_SECRET, host) or ""
        token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        hidden = [password, token, password.replace(" ", "")] if password else [token]
        return {"Authorization": f"Basic {token}"}, hidden

    def _fetch(self, url: str, **how: Any) -> dict[str, Any]:
        return self.ctx.app.net.fetch(url, timeout_s=int(self.ctx.cfg().publish_timeout_s),
                                      **how)

    def _header_image(self, publication: Publication, host: str,
                      auth: dict[str, str], hidden: list[str]) -> tuple[int | None, str]:
        """Upload the header image to the site's media, if there is one. Returns its media
        id, or None and a sentence saying why it was left out."""
        if not publication.header:
            return None, ""
        name = publication.header.rsplit("/", 1)[-1]
        try:
            reply = self._fetch(f"https://{host}{MEDIA}", method="POST", file=publication.header,
                                headers={**auth, "Content-Type": publication.header_type,
                                         "Content-Disposition": f"attachment; filename=\"{name}\""})
        except CallError as error:
            return None, f" The header image was not sent ({scrub(error.message, hidden)})"
        outcome = read_answer(reply, host, hidden)
        if outcome.kind == "sent":
            return int(outcome.external["id"]), ""
        return None, f" The header image was left out: {outcome.detail}"

    def publish(self, publication: Publication) -> Outcome:
        host = host_of(publication.site)
        auth, hidden = self._sign_in(host)
        media, note = self._header_image(publication, host, auth, hidden)
        post: dict[str, Any] = {"title": publication.title, "content": publication.html,
                                "status": "draft"}
        if media is not None:
            post["featured_media"] = media
        try:
            reply = self._fetch(f"https://{host}{POSTS}", method="POST",
                                headers={**auth, "Content-Type": "application/json"}, json=post)
        except CallError as error:
            return failed_call(error, host, hidden)
        outcome = read_answer(reply, host, hidden)
        if outcome.kind == "sent" and media is not None:
            outcome = Outcome("sent", outcome.detail + " Its header image is the draft's "
                              "featured image.", {**outcome.external, "media": media})
        elif note:
            outcome = Outcome(outcome.kind, outcome.detail + note, outcome.external)
        return outcome

    def update(self, publication: Publication, external: dict[str, Any]) -> Outcome:
        """Put an edit into the draft sent before, only while it is still a draft there."""
        host = host_of(publication.site)
        post_id = external.get("id")
        if external.get("host") and external["host"] != host:
            return Outcome("refused", f"The draft was sent to {external['host']}, and this site "
                           f"now publishes to {host}, so the draft was not updated. Your edit "
                           "is kept here: copy it into WordPress by hand.")
        if not isinstance(post_id, int) or isinstance(post_id, bool):
            return Outcome("refused", "Articles does not know which draft it made on your "
                           "site, so the draft was not updated. Your edit is kept here: copy "
                           "it into WordPress by hand.")
        auth, hidden = self._sign_in(host)
        url = f"https://{host}{POSTS}/{post_id}"
        try:
            now = self._fetch(f"{url}?context=edit", method="GET", headers=auth)
        except CallError as error:
            return failed_call(error, host, hidden)
        data = body_of(now) if isinstance(now, dict) else None
        if not isinstance(now, dict) or now.get("status") != 200 or not isinstance(data, dict):
            looked = read_answer(now, host, hidden)
            why = looked.detail if looked.kind != "sent" else "its answer could not be read."
            return Outcome("refused", f"The draft on your site was not updated: Articles could "
                           f"not see it first. {why} Your edit is kept here.")
        if data.get("status") != "draft":
            return Outcome("refused", f"The post on your site at {host} is no longer a draft "
                           f"(it is “{str(data.get('status'))[:20]}”), so Articles "
                           "did not change it. Your edit is kept here: copy it into WordPress "
                           "by hand if you want it there.")
        try:
            reply = self._fetch(url, method="POST",
                                headers={**auth, "Content-Type": "application/json"},
                                json={"title": publication.title, "content": publication.html})
        except CallError as error:
            outcome = failed_call(error, host, hidden)
        else:
            outcome = read_answer(reply, host, hidden)
        if outcome.kind == "sent":
            return Outcome("sent", f"The draft on your site at {host} now has your edit.",
                           {**external, **outcome.external})
        if outcome.kind == "unsure":
            return Outcome("unsure", f"The edit may or may not be in the draft on your site: "
                           f"{outcome.detail}", external)
        return Outcome("refused", f"The draft on your site was not updated. {outcome.detail}",
                       external)


def answer(reply: Any, host: str = "", hidden: list[str] | None = None) -> Outcome:
    """``read_answer`` with nothing to hide: for tests and for the old name."""
    return read_answer(reply, host, hidden or [])
