"""An app's page dictating: the server's half of ``hq.speech.dictate()``.

The page never records. It asks, the shell shows its own recording surface, records with
the owner's microphone and sends the recording to ``POST /api/speech/transcribe`` with
the form field ``app=<id>``, as the owner's session. The speech route then asks
``check`` here before anything is transcribed, so the rules below hold even if the
shell's own early checks were skipped:

- the app is installed, switched on, and the owner granted it ``speech.dictate``;
- the caller is ``app.<id>``, whatever the form said, so the hosted-engine rule
  (``speech.hosted_callers``) names the app and not the shell;
- the longest recording is ``apps.dictate_max_s``, or the Voice limit if that is shorter;
- a private app dictates only when the engine in use keeps the words on the owner's own
  machines (``SpeechService.own_machines``): Whisper or Parakeet, or a speech server the
  owner said is theirs or that is this computer. Otherwise it is refused and nothing is
  sent anywhere.

The receipt is the speech module's own (``speech.transcribed``: engine, hosted, caller,
length, time). It never holds the words, for any caller; the words go back to the page
and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jimmyhq.apps import bridge
from jimmyhq.speech import service
from jimmyhq.speech.transcript import SpeechError

if TYPE_CHECKING:
    from jimmyhq.core import Core

PERMISSION = "speech.dictate"
MAX_SECONDS_SETTING = "apps.dictate_max_s"

PRIVATE_NOT_LOCAL = (
    "This app is private, and the speech engine in use is not known to be on your own "
    "machines, so your words would leave them. Nothing was recorded or sent. To dictate "
    "here, choose Whisper or Parakeet under Settings, Voice, or, if the speech server is "
    "yours, set 'Where the speech server is' to 'It is mine'.")


@dataclass(frozen=True)
class Dictating:
    """What the speech route uses for one app's dictation."""

    app_id: str
    caller: str
    max_seconds: int
    private: bool


def caller_of(app_id: str) -> str:
    """The name the speech module knows an app's dictation by: ``app.<id>``."""
    return f"app.{app_id}"


def check(core: Core, app_id: Any) -> Dictating:
    """The rules for one app's dictation, or ``SpeechError`` with a sentence that says
    nothing was done and what to do instead."""
    found = bridge.find_app(core, app_id) if isinstance(app_id, str) else None
    if found is None:
        raise SpeechError(404, "There is no app with that id, so nothing was transcribed. "
                               "Dictate from an app that is installed.")
    if not found.enabled:
        raise SpeechError(409, f"{found.manifest.name} is switched off, so nothing was "
                               "transcribed. Switch it on in Apps first.")
    if PERMISSION not in found.granted:
        raise SpeechError(403, f"{found.manifest.name} was not given \"{PERMISSION}\", so "
                               "nothing was transcribed. The app must ask for it in its "
                               "manifest and be installed again.")
    private = bool(found.manifest.private)
    # Speech switched off is answered by the speech module itself, which says where to
    # turn it on.
    if private and service(core).own_machines() is False:
        raise SpeechError(403, PRIVATE_NOT_LOCAL)
    longest = int(core.settings.get(MAX_SECONDS_SETTING))
    return Dictating(app_id=found.manifest.id, caller=caller_of(found.manifest.id),
                     max_seconds=longest, private=private)
