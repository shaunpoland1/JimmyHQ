"""Two judgements about a page, made from what the page declares and nothing else.

``refused_kind`` says whether a box is one no agent types into: a password, an email
address, a card number, a one-time code and the like. An agent holds no credentials and
has no address of its own, so a site's sign-in, sign-up or payment form is never its to
fill in. The lesson behind it: told only in words not to, an agent filled in and sent a
real sign-up form. So it is refused here, in code, before a key is pressed.

``changes_something`` says whether a click or Enter would do more than look: send a form
by POST, or press a button whose words say buy, sign up, send, delete or confirm.

A page can lie about itself, and a box can be mislabelled. These catch what is declared;
the fence and the rule about what may be sent (``fence.may_send``) are underneath.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from jimmyhq.browser.page import Submit

# kind -> (input types, autocomplete values or prefixes, words in the name, id, label or hint)
_KINDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "password": (("password",), ("current-password", "new-password"),
                 r"pass(word|code|phrase)?\b|passwd|\bpwd\b|\bpin\b"),
    "one_time_code": ((), ("one-time-code",),
                      r"\botp\b|one ?time|verification code|verify code|security code|"
                      r"\b2fa\b|two ?factor|auth(entication|enticator)? code|sms code|"
                      r"\bmfa\b|\btotp\b"),
    "card": ((), ("cc-",),
             r"card ?(number|no\b|holder)|\bcc[- _]?(num|number|exp|csc)|\bcvv\b|\bcvc\b|"
             r"\bcsc\b|expir(y|ation)|\biban\b|account (number|no\b)|routing|sort ?code|"
             r"\bswift\b|\bbic\b"),
    "email": (("email",), ("email",), r"e ?mail"),
    "username": ((), ("username",), r"user ?name|user ?id|\blogin\b|log ?in (name|id)|sign ?in"),
    "phone": (("tel",), ("tel",), r"\bphone|\bmobile\b|\btel\b|telephone|cell ?number"),
    "identity": ((), ("bday",),
                 r"\bssn\b|social security|passport|national (insurance|id)|tax ?(id|number)|"
                 r"date of birth|\bdob\b|birth ?(day|date)|driv(er|ing).? licen[cs]e"),
    "address": ((), ("street-address", "address-line", "postal-code"),
                r"street|address line|post ?code|postal code|zip ?code|\bzip\b|"
                r"(home|billing|shipping|delivery|mailing) address"),
}

WORDS = {
    "password": "a password box", "one_time_code": "a box for a one-time or verification code",
    "card": "a box for a card or bank account number", "email": "a box for an email address",
    "username": "a box for a sign-in name", "phone": "a box for a phone number",
    "identity": "a box for an identity number or a date of birth",
    "address": "a box for a street address or postcode",
}


def refused_kind(attrs: dict[str, Any], kinds: list[str] | tuple[str, ...]) -> str | None:
    """Rule ``browser_no_credentials``: the kind of box this is, if it is one of the
    kinds no agent types into, judged by its type, its autocomplete hint, and the words
    in its name, id, label and placeholder. ``kinds`` is the owner's list
    (``browser.refused_fields``); a kind the owner took off is not refused."""
    kind = str(attrs.get("type") or "").lower()
    hints = str(attrs.get("autocomplete") or "").lower().split()
    words = " ".join(str(attrs.get(k) or "") for k in ("name", "id", "label", "placeholder"))
    words = re.sub(r"[_\-.\[\]]+", " ", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", words)).lower()
    for name, (types, autos, pattern) in _KINDS.items():
        if name not in kinds:
            continue
        if kind in types or any(h.startswith(autos) for h in hints if autos) \
                or re.search(pattern, words):
            return name
    return None


def changes_something(submit: Submit | None, words: list[str] | tuple[str, ...]) -> str | None:
    """Rule ``browser_submit_changes``: why this click or Enter would change something
    out there, or None when it only looks. ``post``: it sends a form whose method is
    POST. ``words``: it presses a button whose words say so (``browser.submit_words``).
    A form sent by GET, which is what a search box is, is looking."""
    if submit is None:
        return None
    if submit.submits and submit.method == "post":
        return "post"
    if submit.role == "button" or submit.submits:
        label = " ".join(submit.label.lower().split())
        for word in words:
            if word and re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", label):
                return "words"
    return None


def frozen(url: str, how: str, submit: Submit) -> str:
    """The fingerprint of one exact sending: the page, the way it is sent, where it
    goes and every field with its value. An approval is good for this and nothing else."""
    shape = [url, how, submit.role, submit.label, submit.in_form, submit.submits,
             submit.method, submit.action, [list(f) for f in submit.fields]]
    return hashlib.sha256(json.dumps(shape, ensure_ascii=False).encode("utf-8")).hexdigest()


def same_form(url: str, submit: Submit) -> str:
    """What two ways of sending one form have in common (its button, or Enter in one of
    its boxes): the page, where it goes and every value. Used to ask the owner once."""
    if not submit.in_form:
        return frozen(url, "", submit)
    shape = [url, submit.method, submit.action, [list(f) for f in submit.fields]]
    return hashlib.sha256(json.dumps(shape, ensure_ascii=False).encode("utf-8")).hexdigest()
