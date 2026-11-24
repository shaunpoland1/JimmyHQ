"""Turning a recording into plain samples, for an engine that reads only those.

Whisper decodes a browser's recording itself. Parakeet (through onnx-asr) reads
only plain PCM: a WAV file or an array of numbers. So this does the decoding:

- A plain PCM WAV at a rate onnx-asr accepts is read with the standard library.
- Anything else (webm and ogg with Opus, mp4 and m4a with AAC, mp3, flac, and a
  WAV the standard library cannot read) goes through PyAV, which the
  ``speech-parakeet`` extra installs and which carries its own FFmpeg. It is
  turned into 16 kHz mono, which is what the model hears anyway.

The recording is decoded from memory and never written to disk. Decoding stops
as soon as the audio is longer than the limit, so an over-long recording costs
little and a small file that unpacks to hours of sound cannot fill the memory.
"""

from __future__ import annotations

import io
import wave
from typing import Any

import numpy as np

from jimmyhq.speech.transcript import SpeechError

RATE = 16_000  # what PyAV is asked for: the rate the model works at

# The rates onnx-asr accepts for an array (its ``SampleRates``); it resamples the rest itself.
SAMPLE_RATES = (8_000, 11_025, 16_000, 22_050, 24_000, 32_000, 44_100, 48_000)

UNREADABLE = ("That recording could not be read as audio. Record it again, or send a "
              "different format.")


def unreadable() -> SpeechError:
    return SpeechError(422, UNREADABLE, "unreadable")


def too_long(max_seconds: float) -> SpeechError:
    return SpeechError(413, f"That recording is longer than the limit of {max_seconds:g} "
                            "seconds. Record it in shorter pieces, or raise the limit under "
                            "Settings, Voice.", "too_long")


def decode(audio: bytes, content_type: str, max_seconds: float) -> tuple[Any, int]:
    """Mono float32 samples between -1 and 1, and their rate.

    Raises ``SpeechError``: 413 when the audio is longer than ``max_seconds``,
    422 when it cannot be read as audio at all.
    """
    if content_type == "audio/wav":
        plain = read_plain_wav(audio, max_seconds)
        if plain is not None:
            return np.asarray(plain[0], dtype=np.float32), plain[1]
    return np.asarray(decode_with_av(audio, max_seconds), dtype=np.float32), RATE


def read_plain_wav(audio: bytes, max_seconds: float) -> tuple[Any, int] | None:
    """A PCM WAV read with the standard library, or None when it needs PyAV instead.

    None covers a WAV of floating-point samples, an unusual rate, or one that is
    not a WAV at all; PyAV then has its go and says whether it is audio.
    """
    try:
        with wave.open(io.BytesIO(audio)) as handle:
            width, channels = handle.getsampwidth(), handle.getnchannels()
            rate, frames = handle.getframerate(), handle.getnframes()
            if rate not in SAMPLE_RATES or width not in (1, 2, 3, 4) or channels < 1:
                return None
            if frames / rate > max_seconds:
                # The header says how long it is, so nothing needs reading.
                raise too_long(max_seconds)
            data = handle.readframes(frames)
    except (wave.Error, EOFError, ValueError):
        return None
    usable = len(data) - len(data) % (width * channels)
    return _pcm_to_float(data[:usable], width).reshape(-1, channels).mean(axis=1,
                                                                          dtype=np.float32), rate


def _pcm_to_float(data: bytes, width: int) -> Any:
    if width == 1:  # unsigned, resting at 128
        return (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    if width == 3:  # three bytes, little-endian, signed
        raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        whole = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        whole = np.where(whole & 0x800000, whole - 0x1000000, whole)
        return whole.astype(np.float32) / float(2 ** 23)
    kind = "<i2" if width == 2 else "<i4"
    return np.frombuffer(data, dtype=kind).astype(np.float32) / float(2 ** (8 * width - 1))


def decode_with_av(audio: bytes, max_seconds: float) -> Any:
    """Any recording FFmpeg can read, as 16 kHz mono float32 samples."""
    import av  # the optional "speech-parakeet" extra; imported only when a recording arrives

    limit = max_seconds * RATE
    pieces: list[Any] = []
    total = 0

    def keep(frames: Any) -> None:
        nonlocal total
        for frame in frames:
            samples = frame.to_ndarray().reshape(-1)
            total += samples.size
            if total > limit:
                raise too_long(max_seconds)
            pieces.append(samples)

    try:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        with av.open(io.BytesIO(audio), mode="r") as container:
            for frame in _skip_damaged(container.decode(audio=0), av):
                frame.pts = None  # a browser's timestamps can jump; only the samples matter
                keep(resampler.resample(frame))
            keep(resampler.resample(None))  # what the resampler still holds
    except SpeechError:
        raise
    except Exception:  # noqa: BLE001 - FFmpeg's message may describe the audio; keep it out
        raise unreadable() from None
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(pieces).astype(np.float32) / 32768.0


def _skip_damaged(frames: Any, av: Any) -> Any:
    """A damaged packet in the middle of a recording is skipped, not fatal."""
    iterator = iter(frames)
    while True:
        try:
            yield next(iterator)
        except StopIteration:
            return
        except av.error.InvalidDataError:
            continue
