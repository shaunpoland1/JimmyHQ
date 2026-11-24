# Speech

`server/jimmyhq/speech/` turns a recording into text. It serves dictation today and is the
base for voice commands, room recorders and a wearable later. Text-to-speech is not here
yet; it will be a second engine interface in its own file in this package, with its
settings in the same Voice section under their own prefix.

## The rules this module keeps

- **No audio and no words are kept.** A recording is held in memory for the length of the
  call and is never written to disk. The only record is a receipt on the Tape
  (`speech.transcribed`: engine, whether it was hosted, the caller, the audio length, the
  processing time). A failure is `speech.failed` with a reason from a fixed list. What a
  caller does with the text is that caller's decision.
- **Local by default, hosted only by permission.** A hosted engine sends audio off the
  machine, so each calling surface must be listed in the setting `speech.hosted_callers`
  before it may use one. A caller that is not listed is refused with a message that says
  where to allow it, and nothing is sent.
- **Transcribing over HTTP is for the signed-in owner.** `POST /api/speech/transcribe` uses
  the owner's engine and, for a hosted one, the owner's key, and the `caller` it names is
  the request's own word. So the route needs the owner's session, as chat and models do:
  a token cannot transcribe in this release, whatever its scopes (403).
  `GET /api/speech/status` needs the read scope; `PUT` and `DELETE /api/speech/key` need
  the owner, and so does `POST /api/speech/prepare` (download Parakeet's model now).
- **Nothing secret in the address.** `speech.endpoint_url` is a setting, so its value is
  printed on the Tape and shown to anything that may read settings. An address with a user
  name, a password, a `?` or a `#` is refused (422) with a message that points at the key
  field; so is one with a `%` in the host part (a name and password written as `%3A` and
  `%40`), one with an invisible character, and one that is not `http://` or `https://` with
  a host. Empty means not set. An address saved before this rule that breaks it reads as
  not set, so enter a clean one; the old value stays on the Tape, which is never rewritten,
  so change that password or key at the speech server.
- **Off works with nothing installed.** The default engine is `off`; `GET /api/speech/status`
  says how to turn dictation on.

## Layout

| File | Holds |
|---|---|
| `__init__.py` | The routes and `transcribe(core, ...)`, the function other modules call |
| `service.py` | The per-process service: settings, limits, the hosted rule, receipts |
| `engines.py` | `LocalEngine` (faster-whisper), `ParakeetEngine` (onnx-asr), `EndpointEngine` (OpenAI-style API), `ModelHolder` |
| `audio.py` | Turning a recording into plain 16 kHz samples, for Parakeet |
| `livedoor.py` | `LiveDoor`, the serving core for a wearable's short chunks |
| `transcript.py` | `Transcript`, `Segment`, `Word`, `SpeechError` |
| `kinds.py`, `settings.py` | Event kinds and the Voice settings |

## Engines

An engine has `name`, `hosted`, `problem()` (cheap: why it cannot be used, or `None`) and
`transcribe(audio, content_type, language, want_words) -> Transcript`.

- **local** runs faster-whisper from the optional `speech` extra (`uv sync --extra speech`).
  It is imported only when a recording arrives. There is one model per process, loaded on
  first use and kept warm in a `ModelHolder`, whose lock means the model never runs twice
  at once. A second recording waits `speech.busy_wait_seconds`, then is answered 503. The
  web app keeps the recording and puts a Retry button on that toast.
  The web and worker processes each hold their own model.
- **parakeet** runs NVIDIA's Parakeet TDT 0.6B v3 on the processor, from the optional
  `speech-parakeet` extra. See [Parakeet](#parakeet) below.

There is one model in memory per process, whichever engine it belongs to: the
`ModelHolder` lets the Whisper model go before Parakeet's loads, and the other way round.
- **endpoint** posts to `{speech.endpoint_url}/audio/transcriptions` as multipart with
  `response_format=verbose_json` (and `timestamp_granularities[]` when words are wanted).
  The key is the secret `speech.endpoint.api_key`. The address counts as hosted unless it
  is this machine, a non-public address, a single-label name or a home-network style name
  (an address written as one bare number counts as hosted);
  `speech.endpoint_location` overrides that. Redirects are not followed, and the server's
  own error text is never passed on.

## Parakeet

Parakeet is a second engine that runs on this computer. It is NVIDIA's
[parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), run on the
processor through [onnx-asr](https://github.com/istupakov/onnx-asr) and ONNX Runtime, with no
PyTorch or NeMo. Your audio is not uploaded: only the model is downloaded, once.

**Install.** On the JimmyHQ computer, in the JimmyHQ folder, then restart JimmyHQ:

```bash
uv sync --extra speech-parakeet
```

`uv sync` removes extras it is not given, so to keep Whisper installed as well use
`uv sync --extra speech --extra speech-parakeet`. The extra installs onnx-asr (MIT licence)
with ONNX Runtime and the Hugging Face downloader, and PyAV, which carries its own FFmpeg.
Nothing is imported until Parakeet is chosen and a recording arrives, or the owner presses
**Download the model now** under Settings, Voice.

**What is downloaded, from where.** onnx-asr fetches an ONNX conversion of the model from
huggingface.co (the repository `istupakov/parakeet-tdt-0.6b-v3-onnx`) into the usual
Hugging Face cache (`HF_HOME` moves it), plus the Silero voice activity detector from
`istupakov/silero-vad-onnx`, about 2 MB, which cuts long recordings at pauses.

| `speech.parakeet_variant` | Files | About |
|---|---|---|
| `int8` (default) | `encoder-model.int8.onnx`, `decoder_joint-model.int8.onnx`, `vocab.txt`, `config.json` | 670 MB |
| `full` | `encoder-model.onnx` and `.onnx.data`, `decoder_joint-model.onnx`, `vocab.txt`, `config.json` | 2.5 GB |

Nothing is fetched until Parakeet is the chosen engine and either a recording arrives or the
owner presses the button. The status (`download` in `GET /api/speech/status`) says whether
the files are already in the cache by reading the cache alone, without loading anything. The
first recording waits for the whole download, so the button is the better way to start it:
`POST /api/speech/prepare` downloads and loads the model on a thread of its own, and a
recording that arrives meanwhile is told the model is still being downloaded.

**Settings.** `speech.parakeet_variant` (the size above) and `speech.parakeet_threads` (the
processor threads ONNX Runtime may use; 0 means one per core). The model runs on the
processor only (`CPUExecutionProvider`), even where ONNX Runtime offers Core ML or a
graphics card.

**What comes back.** The same `Transcript`. A recording up to 20 seconds is heard whole and
answers one segment; a longer one is cut at pauses by Silero, and each piece is a segment
(onnx-asr says most models handle at most 20 to 30 seconds at once). Words, when asked for,
are built from the model's word pieces: onnx-asr says when each piece starts but not when it
ends, so a word ends where the next begins and the last word where its segment ends. Those
end times are approximate. `language` is always empty: Parakeet works out the language
itself and does not say which it heard.

**Languages.** 25 European ones, per the model card: bg, hr, cs, da, nl, en, et, fi, fr, de,
el, hu, it, lv, lt, mt, pl, pt, ro, sk, sl, es, sv, ru, uk. It cannot be told which to
expect. A spoken language outside that list (in the setting or a request) is refused with a
sentence that points to Whisper.

**Formats.** The dictate button records `audio/webm` (Opus) where the browser can, else
`audio/mp4` (AAC, Safari), else `audio/ogg` (Opus), as listed in
`web/src/modules/speech/lib/problems.ts`. onnx-asr itself reads only PCM WAV or an array of
samples, so `audio.py` decodes first: a PCM WAV at a rate onnx-asr accepts is read with the
standard library, and everything else (webm, ogg, mp4, m4a, mp3, flac, other WAVs) goes
through PyAV to 16 kHz mono. Decoding happens in memory, before waiting for the model, and
stops as soon as the audio passes `speech.max_seconds`. What FFmpeg cannot read is refused
as unreadable (422), and a recording under a tenth of a second answers empty text without
loading the model.

**The live door.** Parakeet fits it: the model stays warm in the holder, a 5-second chunk is
heard whole, and chunks run one at a time. Until the model is downloaded and loaded the
first chunks are answered `deferred`, so press the download button before a device is
pointed at it.

**Whisper or Parakeet.** What is known: the model card reports an average word error rate
of 6.34% on the Hugging Face Open ASR leaderboard (English) and 11.97% on FLEURS across its
languages, and onnx-asr's own benchmark puts it at 36 times faster than real time on a
recent desktop processor (and about real time on a small Arm board). What is not known:
how it compares with the Whisper model you would otherwise use, on your voice and your
computer, and how much accuracy the int8 copy gives up. One small measurement exists, and it is no more than that: on an Apple-silicon Mac mini, a
24-second recording of a computer voice reading mixed prose and numbers took about 0.6
seconds with Parakeet (int8), 1.3 seconds with Whisper `base.en` and 2.9 seconds with Whisper
`small.en`, once each model was loaded. All three wrote the long passage correctly; all
three stumbled on one short clipped sentence. A real voice, a noisy room and other
languages were not tried.
Choose Parakeet for European languages on a computer with no graphics card; keep Whisper for
any other language, or when you need to tell it which language to expect.

**Licence.** The model is released under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), "ready for commercial/
non-commercial use" according to its model card, and the ONNX conversion carries the same
licence. JimmyHQ does not ship the model; the owner's computer downloads it. Anyone who
passes the model on must credit it:

> Parakeet TDT 0.6B v3 by NVIDIA, licensed under CC BY 4.0
> (https://creativecommons.org/licenses/by/4.0/). ONNX conversion:
> huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx.

## Dictation for an app's page

An app's page asks with `hq.speech.dictate()` and the shell records with the same recorder
as its own dictate button, then posts to the same route with one more form field, `app`
(see [Apps, Dictation](apps.md#dictation)). With `app` set, `jimmyhq.apps.dictate.check`
decides first: the app must be installed, switched on and granted `speech.dictate`; the
caller becomes `app.<id>`; the longest recording is `apps.dictate_max_s` or this module's own
limit, whichever is shorter; and a private app is refused unless `own_machines` in the
status is true. `own_machines` is stricter than "local": true for Whisper and Parakeet, and
for a speech server only when `speech.endpoint_location` is `local` or the address is this
computer (`localhost` or a loopback address). Without `app` the route behaves as before.

## Calling it from another module

```python
from jimmyhq import speech

result = await speech.transcribe(core, audio_bytes, "audio/webm", caller="recorder",
                                 language=None, want_words=False, actor=who.actor)
result.text, result.language, result.duration, result.segments
```

It raises `speech.SpeechError` with `status` and `detail`. Code already on a worker thread
can call `speech.service(core).transcribe_now(...)` with the same arguments.

## The live door

`LiveDoor(transcribe, LiveLimits.from_settings(core.settings), on_text=...)` is for a device
that posts a few seconds of audio at a time and resends anything not answered quickly.
`submit(chunk)` returns a `LiveAnswer(text, status)` within the deadline:

| status | Means |
|---|---|
| `answered` | Transcribed in time; `text` holds it |
| `deferred` | Not ready in time; answered empty, finished behind, text goes to `on_text` |
| `duplicate` | The same bytes were already accepted within the window |
| `busy` | The backlog is full; the chunk was dropped |
| `failed` | The engine could not transcribe it; the chunk is forgotten, so a resend is tried again |

It has one worker thread of its own and transcribes strictly in arrival order. It has no
route yet: the route's shape depends on the device. `submit` blocks, so call it from a
thread. The queue is in memory by design; `on_text` is where the text is kept.

## Testing

Tests never load a model or use a network. Set `speech.service(core).models.loader` to a
function returning a fake model, and `speech.service(core).transport` to an
`httpx.MockTransport`. Parakeet's tests (`tests/test_speech_parakeet.py`) put small fakes of
onnx-asr, ONNX Runtime, the Hugging Face downloader and PyAV in `sys.modules`, so the
engine's real imports and calls run against them.
