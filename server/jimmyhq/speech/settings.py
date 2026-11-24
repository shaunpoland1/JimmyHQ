"""Settings for speech, under the Voice section.

Keys start with ``speech.`` so that text-to-speech can later sit beside them in
the same section under its own prefix.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from jimmyhq.settings import Setting, SettingError, declare, section

section("voice", "Voice", group="models", order=20,
        description="Turning what you say into text: on this computer, or on a speech server.")


def endpoint_address(value: Any) -> str:
    """The speech server address, refused if it carries anything secret.

    A setting's value is printed on the Tape for good and shown to anything
    that may read settings, so a name, password or key has no place in it.
    Empty means "not set".
    """
    value = value.strip()
    if not value:
        return ""
    if any(c.isspace() or not c.isprintable() for c in value):
        raise SettingError("Speech server address cannot contain spaces, line breaks or "
                           "invisible characters.")
    try:
        parts = urlsplit(value)
        host, _ = parts.hostname, parts.port  # reading the port checks that it is a number
    except ValueError:
        raise SettingError("Speech server address is not a valid address. Use something like "
                           "http://localhost:8000/v1.") from None
    if parts.scheme not in ("http", "https") or not host:
        raise SettingError("Speech server address must start with http:// or https:// and "
                           "name a host.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise SettingError("Do not put a user name, password or key in the speech server "
                           "address. Save the key in the API key field under Settings, Voice "
                           "instead.")
    if "%" in parts.netloc:
        # A name and password written as %3A and %40 would otherwise pass as a host.
        raise SettingError("The host in the speech server address cannot contain %. If that "
                           "is a user name, password or key, save the key in the API key field "
                           "under Settings, Voice instead.")
    if parts.query or parts.fragment or "?" in value or "#" in value:
        raise SettingError("Speech server address cannot contain ? or #. Save the key in the "
                           "API key field under Settings, Voice instead.")
    return value

declare(Setting(
    key="speech.engine", section="voice", type="choice", default="off",
    choices=(("off", "Off"), ("local", "Whisper, on this computer"),
             ("parakeet", "Parakeet, on this computer"), ("endpoint", "A speech server")),
    label="Speech to text",
    help="What turns your voice into text. Whisper and Parakeet both run on this computer and "
         "your audio is not uploaded; each needs its own add-on installed and downloads its "
         "model the first time it is used. Parakeet runs on this computer's processor, hears "
         "25 European languages, and its model is about 670 MB from huggingface.co. 'A speech "
         "server' sends the audio to the address below.",
))
declare(Setting(
    key="speech.language", section="voice", type="str", default="",
    portable=False,
    label="Spoken language",
    help="A two-letter code such as en or fr. Leave empty to detect the language each time. "
         "Parakeet always works out the language itself, and knows only 25 European ones.",
))
declare(Setting(
    key="speech.local_model", section="voice", type="str", default="small",
    portable=False,
    label="Whisper model",
    help="The Whisper model to run here, such as tiny, base, small, medium or large-v3, or a "
         "folder holding one. Larger is more accurate and slower. It is downloaded the first "
         "time it is used.",
))
declare(Setting(
    key="speech.local_device", section="voice", type="choice", default="auto",
    choices=(("auto", "Choose for me"), ("cpu", "Processor"), ("cuda", "Graphics card")),
    portable=False,
    label="Run the Whisper model on",
    help="Leave on 'Choose for me' unless you know this computer has a supported graphics card.",
))
declare(Setting(
    key="speech.local_compute_type", section="voice", type="choice", default="default",
    choices=(("default", "Choose for me"), ("int8", "int8 (smallest, fine on a processor)"),
             ("int8_float16", "int8_float16"), ("float16", "float16 (graphics card)"),
             ("float32", "float32 (largest)")),
    portable=False,
    advanced=True,
    label="Whisper model precision",
    help="How the Whisper model's numbers are stored. Smaller is faster and uses less memory.",
))
declare(Setting(
    key="speech.parakeet_variant", section="voice", type="choice", default="int8",
    choices=(("int8", "Smaller (int8, about 670 MB)"), ("full", "Full precision (about 2.5 GB)")),
    portable=False,
    label="Parakeet model size",
    help="Which copy of Parakeet (NVIDIA's parakeet-tdt-0.6b-v3) to download and run. The "
         "smaller one suits a processor and needs less memory. The full one is almost "
         "four times larger; how much more accurate it is has not been measured here. Either "
         "is downloaded once from huggingface.co and kept.",
))
declare(Setting(
    key="speech.parakeet_threads", section="voice", type="int", default=0, min=0, max=256,
    portable=False,
    advanced=True,
    label="Parakeet processor threads",
    help="How many of the processor's threads Parakeet may use for one recording. 0 lets it "
         "use one per core. Lower it if dictating slows down the rest of this computer.",
))
declare(Setting(
    key="speech.endpoint_url", section="voice", type="str", default="",
    validate=endpoint_address,
    portable=False,
    label="Speech server address",
    help="The base address of a server that speaks the OpenAI-style transcription API, such as "
         "http://localhost:8000/v1 for one on this computer or https://api.example.com/v1 for "
         "a hosted one. Do not put a user name, password or key in it: the key has its own "
         "field.",
))
declare(Setting(
    key="speech.endpoint_model", section="voice", type="str", default="whisper-1",
    portable=False,
    label="Speech server model",
    help="The model name the speech server expects. It must be able to answer with timed "
         "segments (the verbose_json format).",
))
declare(Setting(
    key="speech.endpoint_location", section="voice", type="choice", default="auto",
    choices=(("auto", "Work it out from the address"), ("local", "It is mine: treat as local"),
             ("hosted", "It is someone else's: treat as hosted")),
    portable=False,
    label="Where the speech server is",
    help="A hosted server receives your audio, so each part of JimmyHQ must be allowed to use "
         "it. Addresses on this computer or your own network count as local automatically.",
))
declare(Setting(
    key="speech.endpoint_timeout_seconds", section="voice", type="int", default=60,
    min=2, max=600,
    advanced=True,
    label="Wait for the speech server (seconds)",
    help="How long to wait for the speech server to answer before giving up.",
))
declare(Setting(
    key="speech.hosted_callers", section="voice", type="list", default=[],
    portable=False,
    label="Allowed to use a hosted speech server",
    help="Which parts of JimmyHQ may send audio to a hosted speech server, one name per item, "
         "for example dictation. Anything not listed is refused.",
))
declare(Setting(
    key="speech.max_seconds", section="voice", type="int", default=300, min=5, max=14400,
    advanced=True,
    label="Longest recording (seconds)",
    help="Recordings longer than this are refused.",
))
declare(Setting(
    key="speech.max_megabytes", section="voice", type="int", default=25, min=1, max=500,
    advanced=True,
    label="Largest recording (megabytes)",
    help="Uploads larger than this are refused before they are read in full.",
))
declare(Setting(
    key="speech.busy_wait_seconds", section="voice", type="float", default=20, min=0, max=120,
    advanced=True,
    label="Wait for a busy model (seconds)",
    help="The model on this computer handles one recording at a time. A second one waits this "
         "long before it is told to try again.",
))
declare(Setting(
    key="speech.live_deadline_seconds", section="voice", type="float", default=8,
    min=0.5, max=10,
    advanced=True,
    label="Live audio: answer within (seconds)",
    help="For a wearable that sends short pieces of audio. A piece that cannot be turned into "
         "text in this time is answered empty and finished in the background.",
))
declare(Setting(
    key="speech.live_backlog", section="voice", type="int", default=30, min=1, max=500,
    advanced=True,
    label="Live audio: pieces allowed to wait",
    help="How many pieces of live audio may queue for the model before new ones are turned "
         "away.",
))
declare(Setting(
    key="speech.live_duplicate_seconds", section="voice", type="float", default=120,
    min=0, max=3600,
    advanced=True,
    label="Live audio: ignore repeats for (seconds)",
    help="A piece of live audio that arrives again within this time is recognised as a "
         "resend and not transcribed twice.",
))
