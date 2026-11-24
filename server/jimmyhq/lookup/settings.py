"""Settings for web lookup. Importing this module declares them."""

from __future__ import annotations

from urllib.parse import urlsplit

from jimmyhq.settings import Setting, SettingError, declare, section

# The search backends, as (id, label). "none" means searching is not set up.
PROVIDERS = (("none", "None"), ("exa", "Exa"), ("tavily", "Tavily"),
             ("brave", "Brave Search"), ("searxng", "SearXNG (your own server)"),
             ("jina", "Jina"))


def _server_address(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - reading it is the check
    except ValueError:
        parts = None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        raise SettingError("The SearXNG address must start with http:// or https:// and "
                           "name a server, such as https://search.example.com.")
    if parts.username is not None or parts.password is not None:
        raise SettingError("The SearXNG address cannot carry a user name or password.")
    if parts.query or parts.fragment:
        raise SettingError("The SearXNG address is the server's own address, with nothing "
                           "after a ? or a #.")
    return value


def _host_names(value: list[str]) -> list[str]:
    out = []
    for item in value:
        name = item.strip().lower().rstrip(".")
        if name.startswith("[") and name.endswith("]"):
            name = name[1:-1]
        if not name:
            continue
        if any(ch in name for ch in "/@ \t") or "://" in name:
            raise SettingError("Each allowed host is a name or an address on its own, such "
                               "as wiki.example.com: no http://, path or port.")
        if name not in out:
            out.append(name)
    return out


section("lookup", "Web lookup", group="models", order=30,
        description="Whether models may search the web and read pages, and how far they may go.")

declare(Setting(
    key="lookup.enabled", section="lookup", type="bool", default=False,
    label="Let models look things up on the web",
    help="Off: JimmyHQ never searches the web or reads a page for a model. On: a model in a "
         "chat with \"Web\" switched on may search and read public pages. Searches go to "
         "the search provider you choose below, and what a page says is sent to the model "
         "that is answering, which is a hosted provider if that is the model you chose.",
))
declare(Setting(
    key="lookup.search_provider", section="lookup", type="choice", default="none",
    choices=PROVIDERS,
    label="Search provider",
    help="Who answers web searches. Each hosted provider needs its own key, saved below "
         "and kept with your other secrets. SearXNG is a search server you run yourself "
         "and needs only its address. With \"None\", a model can still read a page you "
         "give it the address of.",
))
declare(Setting(
    key="lookup.searxng_url", section="lookup", type="str", default="",
    label="SearXNG address",
    help="The address of your own SearXNG server, such as https://search.example.com. "
         "JSON output must be switched on in its settings.yml (search: formats: json).",
    validate=_server_address,
))
declare(Setting(
    key="lookup.results", section="lookup", type="int", default=6, min=1, max=20,
    advanced=True,
    label="Results per search",
    help="How many results one search returns to the model.",
))
declare(Setting(
    key="lookup.fetch_max_bytes", section="lookup", type="int", default=5 * 1024 * 1024,
    min=1024, max=50 * 1024 * 1024,
    advanced=True,
    label="Largest page (bytes)",
    help="Reading a page stops at this size. The rest is left out and the model is told "
         "the page was cut.",
))
declare(Setting(
    key="lookup.fetch_timeout_s", section="lookup", type="float", default=30, min=1, max=120,
    advanced=True,
    label="Longest wait for a page (seconds)",
    help="A page, or a search, that takes longer than this is given up on.",
))
declare(Setting(
    key="lookup.page_chars", section="lookup", type="int", default=12000, min=500,
    max=400000,
    advanced=True,
    label="Characters of a page a model sees",
    help="A page longer than this is cut before it is shown to the model. Lower it for a "
         "model with a small memory.",
))
declare(Setting(
    key="lookup.steps_per_reply", section="lookup", type="int", default=6, min=1, max=30,
    advanced=True,
    label="Most lookup rounds in one reply",
    help="How many times in one reply a model may go back to the web before it has to "
         "answer with what it has.",
))
declare(Setting(
    key="lookup.allow_private_hosts", section="lookup", type="list", default=[],
    label="Private hosts a model may read",
    help="Web lookup refuses every address on your own machine or private network. Name a "
         "host here (its name or address, nothing else) to let models read pages from it "
         "anyway. Leave empty unless you mean it: whatever those hosts serve becomes "
         "readable by any model with web lookup on.",
    unique=True, validate=_host_names,
))
declare(Setting(
    key="lookup.fetch_any_url", section="lookup", type="bool", default=False,
    label="Let a model read any address it comes up with",
    help="Off: a model may only read an address you wrote in the conversation, one a search "
         "returned, or one linked from a page it has already read in this reply. That "
         "stops a page with hidden instructions from making the model send your words to "
         "an address of its choosing. On: the model may read any public address, which "
         "also lets it open pages it knows by heart.",
))
declare(Setting(
    key="lookup.user_agent", section="lookup", type="str",
    default="Mozilla/5.0 (compatible; JimmyHQ web lookup)",
    advanced=True,
    label="How JimmyHQ names itself to web sites",
    help="Sent as the User-Agent when a page is read. Some sites refuse names they do not "
         "know.",
))
declare(Setting(
    key="lookup.chat_default", section="lookup", type="bool", default=False,
    label="Start new conversations with web lookup on",
    help="Whether the \"Web\" switch in chat starts out on. It only has an effect while "
         "web lookup itself is on. If the conversation's model is a hosted one, what a "
         "page says is sent to that provider.",
))
