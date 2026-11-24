"""Settings for memory. Importing this module declares them."""

from __future__ import annotations

import re
from typing import Any

from jimmyhq.memory.errors import LibraryError
from jimmyhq.memory.notes import words
from jimmyhq.memory.paths import clean_path
from jimmyhq.models.shapes import ROLES
from jimmyhq.settings import Setting, SettingError, declare, section

KIND = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")

DEFAULT_KIND_NOTES = [
    "preference=shared/preferences.md",
    "person=shared/people.md",
    "project=shared/projects.md",
    "decision=shared/decisions.md",
    "rule=shared/rules.md",
]


def _note_path(value: Any) -> Any:
    try:
        return clean_path(value)
    except LibraryError as exc:
        raise SettingError(exc.detail) from None


def _kind_notes(value: Any) -> Any:
    seen = set()
    out = []
    for item in value:
        kind, sep, path = item.partition("=")
        kind = kind.strip()
        if not sep or not KIND.match(kind):
            raise SettingError(
                "Each line must look like kind=path, for example person=shared/people.md, "
                "with the kind in lower-case letters.")
        if kind in seen:
            raise SettingError(f"The kind '{kind}' is listed twice. Keep one line for it.")
        seen.add(kind)
        out.append(f"{kind}={_note_path(path.strip())}")
    return out


# Words that say nothing about what a search is looking for. Matching on them
# alone turns up every passage that has them ("how old am I?" and every fact
# with "I" in it).
DEFAULT_IGNORE_WORDS = [
    "a", "about", "am", "an", "and", "any", "are", "as", "at", "be", "been", "but", "by",
    "can", "could", "did", "do", "does", "for", "from", "had", "has", "have", "how", "i",
    "if", "in", "into", "is", "it", "its", "me", "my", "of", "on", "or", "our", "should",
    "so", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "to", "us", "was", "we", "were", "what", "when", "where", "which", "who", "why",
    "will", "with", "would", "you", "your",
]


def _ignore_words(value: Any) -> Any:
    out: list[str] = []
    for item in value:
        found = words(item)
        if len(found) != 1:
            raise SettingError("Put one word on each line, such as the or and.")
        if found[0] not in out:
            out.append(found[0])
    return out


def kind_notes(value: list[str]) -> dict[str, str]:
    return dict(item.split("=", 1) for item in value)


section("memory", "Memory", group="work", order=40,
        description="Your notes, how they are searched, and what JimmyHQ may learn on its own.")
# The page is long, so it is in headed parts. A setting's key never says which
# part it is in: moving one between parts changes nothing stored or exported.
section("memory_notes", "Your notes", parent="memory", order=10,
        description="The notes in your Library: edits made outside the app, and older versions.")
section("memory_search", "Search", parent="memory", order=20,
        description="How a search or a recall finds passages, by their words and by their meaning.")
section("memory_learning", "Learning on its own", parent="memory", order=30,
        description="Whether JimmyHQ proposes facts from what happens, and when it asks you first.")
section("memory_sources", "What it learns from", parent="memory", order=40,
        description="The places JimmyHQ reads when it looks for facts worth remembering.")

declare(Setting(
    key="memory.watch_disk", section="memory_notes", type="bool", default=True,
    label="Pick up edits made outside the app",
    help="When on, notes you add, change, move or delete with your own editor are noticed "
         "on their own. When off, they are noticed only when you press Scan.",
))
declare(Setting(
    key="memory.scan_interval_s", section="memory_notes", type="int", default=60, min=10, max=86400,
    advanced=True,
    label="Look for outside edits every (seconds)",
    help="How often the Library folder is checked for changes made outside the app. "
         "Unchanged notes are not read again, so a short interval is cheap.",
))
declare(Setting(
    key="memory.max_note_kb", section="memory_notes", type="int", default=512, min=1, max=4096,
    advanced=True,
    label="Largest note (kilobytes)",
    help="A note larger than this is left out of memory and listed as skipped.",
))
declare(Setting(
    key="memory.chunk_chars", section="memory_search", type="int", default=800, min=200, max=8000,
    advanced=True,
    label="Passage size (characters)",
    help="Free text in a note is cut into passages of about this size for search. A fact "
         "is always its own passage. Changing this rebuilds the index at the next scan.",
))
declare(Setting(
    key="memory.recall_passages", section="memory_search", type="int", default=8, min=1, max=50,
    advanced=True,
    label="Passages to recall",
    help="How many passages a recall returns when the caller does not say.",
))
declare(Setting(
    key="memory.recall_chars", section="memory_search", type="int", default=4000, min=200,
    max=100_000,
    advanced=True,
    label="Recall size (characters)",
    help="The most text a recall hands over when the caller does not say. Passages are "
         "never cut: one that does not fit is left out.",
))
declare(Setting(
    key="memory.weight_keyword", section="memory_search", type="float", default=1.0, min=0, max=10,
    advanced=True,
    label="Weight of matching words",
    help="How much a passage's place in the keyword results counts when the two kinds of "
         "search are combined. 0 leaves keyword results out.",
))
declare(Setting(
    key="memory.weight_meaning", section="memory_search", type="float", default=1.0, min=0, max=10,
    advanced=True,
    label="Weight of matching meaning",
    help="How much a passage's place in the meaning results counts when the two kinds of "
         "search are combined. 0 leaves meaning results out.",
))
declare(Setting(
    key="memory.keyword_floor", section="memory_search", type="float", default=0.2, min=0, max=1,
    advanced=True,
    label="Weakest keyword match to keep",
    help="A passage that matches the words of a search less than this fraction as well as "
         "the best match is left out, so passages that only share common words do not "
         "pad the results. 0 keeps every match.",
))
declare(Setting(
    key="memory.ignore_words", section="memory_search", type="list", default=DEFAULT_IGNORE_WORDS,
    unique=True, validate=_ignore_words,
    advanced=True,
    label="Words a search does not match on",
    help="Small words such as the, and or I appear in nearly everything, so a passage is "
         "not counted as matching a search because it shares only these. One word per "
         "line. A search made only of these words still matches them.",
))
declare(Setting(
    key="memory.min_similarity", section="memory_search", type="float", default=0.25, min=0, max=1,
    advanced=True,
    label="Least similarity for a meaning match",
    help="A passage whose meaning is less similar to the question than this (0 to 1) is "
         "not offered by meaning search. Raise it if unrelated passages turn up.",
))
declare(Setting(
    key="memory.related_margin", section="memory_search", type="float", default=0.1, min=0, max=1,
    advanced=True,
    label="How close a related passage must be to the closest one",
    help="Meaning search keeps a passage only when its similarity to the question (0 to 1) "
         "is within this much of the most similar passage. This keeps facts that are merely "
         "the nearest of a few out of search results and out of chat. Lower it if unrelated "
         "facts still turn up; raise it if related ones go missing. 1 turns this off.",
))
declare(Setting(
    key="memory.related_max", section="memory_search", type="int", default=3, min=0, max=50,
    label="Related passages shown in a search",
    help="On the Memory screen, passages that share a word with your search are listed "
         "first. After them come at most this many passages that only seem related in "
         "meaning, marked as related. 0 shows word matches only. Chat is not limited by "
         "this.",
))
declare(Setting(
    key="memory.confirmed_boost", section="memory_search", type="float", default=0.15, min=0, max=2,
    advanced=True,
    label="Preference for confirmed facts",
    help="A fact you have confirmed scores this much higher (0.15 is 15%) than the same "
         "match on anything else.",
))
declare(Setting(
    key="memory.recency_boost", section="memory_search", type="float", default=0.1, min=0, max=2,
    advanced=True,
    label="Preference for newer text",
    help="Text changed today scores this much higher (0.1 is 10%); the advantage halves "
         "with every half-life below.",
))
declare(Setting(
    key="memory.recency_half_life_days", section="memory_search", type="int", default=90, min=1,
    max=3650,
    advanced=True,
    label="Half-life of the preference for newer text (days)",
    help="After this many days the advantage of newer text has halved.",
))
declare(Setting(
    key="memory.embed_local_only", section="memory_search", type="bool", default=True,
    label="Keep meaning search on my own machines",
    help="To search by meaning, note text is sent to the embeddings model (Settings, "
         "Models), which turns it into numbers that can be compared. When on, only a "
         "provider marked local may receive it; with only a hosted embeddings model, "
         "search uses keywords alone.",
))
declare(Setting(
    key="memory.embed_batch", section="memory_search", type="int", default=64, min=1, max=1000,
    advanced=True,
    label="Passages made ready for meaning search per run",
    help="How many passages the background job prepares for meaning search each time it "
         "runs, so one run never takes long.",
))
declare(Setting(
    key="memory.embed_timeout_s", section="memory_search", type="float",
    default=10.0, min=1, max=120,
    advanced=True,
    label="Give up on meaning search after (seconds)",
    help="If the embeddings model has not answered a search by then, the search uses "
         "keywords alone and says so.",
))
declare(Setting(
    key="memory.embed_retry_s", section="memory_search", type="int", default=300, min=10, max=86400,
    advanced=True,
    label="After the embeddings model fails, wait (seconds)",
    help="How long the background job waits before asking an embeddings model that just "
         "failed again.",
))
declare(Setting(
    key="memory.kind_notes", section="memory_notes", type="list", default=DEFAULT_KIND_NOTES,
    unique=True, validate=_kind_notes,
    portable=False,
    label="Where each kind of fact is kept",
    help="One line per kind, as kind=path, for example person=shared/people.md. A "
         "remembered fact of that kind is added to that note unless the caller names one.",
))
declare(Setting(
    key="memory.default_note", section="memory_notes", type="str", default="shared/about-me.md",
    validate=_note_path,
    portable=False,
    label="Where every other kind of fact is kept",
    help="The note a remembered fact is added to when its kind has no line above.",
))
declare(Setting(
    key="memory.history_keep_versions", section="memory_notes", type="int", default=0, min=0,
    max=10_000,
    label="Versions to keep of each note",
    help="Each time a note changes, its earlier text is kept so you can look back. This is "
         "the most versions kept per note; older ones are deleted for good. 0 keeps all.",
))
declare(Setting(
    key="memory.history_keep_days", section="memory_notes", type="int",
    default=0, min=0, max=36_500,
    label="Delete note versions older than (days)",
    help="A kept version of a note older than this is deleted for good, even when it is the "
         "only one. The note itself is never touched. 0 keeps versions however old.",
))

# --- mining: reading what happens and proposing facts -----------------------------

declare(Setting(
    key="memory.mining", section="memory_learning", type="bool", default=True,
    label="Learn from what happens",
    help="When on, the sources switched on below are read in the background and facts worth "
         "remembering are proposed. When off, memory only holds what you put in it.",
))
declare(Setting(
    key="memory.mine_interval_s", section="memory_learning", type="int", default=900, min=30,
    max=604_800,
    advanced=True,
    label="Look for new facts every (seconds)",
    help="How often the sources are read for anything new. Each pass reads a limited number "
         "of items, so a long backlog is worked through over several passes.",
))
declare(Setting(
    key="memory.mine_role", section="memory_learning", type="choice", default="cheap",
    choices=tuple((r, r.title()) for r in ROLES if r != "embeddings"),
    label="Model role for finding facts",
    help="Which role's model reads your sources (Settings, Models). A role with no model "
         "chosen uses the default role's.",
))
declare(Setting(
    key="memory.mine_local_only", section="memory_learning", type="bool", default=True,
    label="Only find facts on my own machines",
    help="Finding facts sends what you wrote to a model. When on, only a provider marked "
         "local may read it; with only a hosted model, nothing is read and the Memory "
         "screen says so.",
))
# Each of the two thresholds can hold any value in its range. A validator sees
# one setting at a time, so the pair is read together where it is used
# (``mining.thresholds``): if they are set so that they cross, nothing is sent
# to the Bench, nothing below "save at or above" is saved, and the Memory
# screen says so.
declare(Setting(
    key="memory.auto_save_above", section="memory_learning", type="float",
    default=0.85, min=0, max=2,
    label="Save without asking at or above (confidence)",
    help="A proposed fact the model is at least this sure of (0 to 1) is saved straight "
         "away and listed as saved automatically until you confirm or forget it. Anything "
         "above 1 means nothing is saved without you. Keep it above the setting that "
         "follows: what lies between the two is what you are asked about.",
))
declare(Setting(
    key="memory.ignore_below", section="memory_learning", type="float", default=0.4, min=0, max=1,
    label="Ignore below (confidence)",
    help="A proposed fact the model is less sure of than this is recorded as ignored and "
         "goes no further. 0 ignores nothing. Everything between this and the setting above "
         "is sent to the Bench for you to decide. If this is set higher than the setting "
         "above, nothing is sent to the Bench and the Memory screen says so.",
))
declare(Setting(
    key="memory.mine_pass_seconds", section="memory_learning", type="int",
    default=60, min=1, max=3600,
    advanced=True,
    label="Longest a mining pass may take (seconds)",
    help="Other background work waits while a pass runs. A model that has not answered by "
         "then is given up for now, fewer items are tried next time, and the sources not "
         "reached are read first at the next pass.",
))
declare(Setting(
    key="memory.mine_batch", section="memory_learning", type="int", default=20, min=1, max=100,
    advanced=True,
    label="Items to read per source each pass",
    help="The most items from one source shown to the model in one pass.",
))
declare(Setting(
    key="memory.mine_item_chars", section="memory_learning", type="int", default=4000, min=200,
    max=20_000,
    advanced=True,
    label="Longest item to read (characters)",
    help="A message, task or card longer than this is passed over and counted as too long.",
))
declare(Setting(
    key="memory.mine_max_open_cards", section="memory_learning", type="int",
    default=5, min=0, max=100,
    label="Most memory cards on the Bench at once",
    help="Proposed facts waiting for you are put on the Bench this many at a time; the rest "
         "wait and follow as you decide. 0 keeps them all on the Memory screen.",
))

from jimmyhq.memory import sources  # noqa: E402, F401 - declares one switch per source
