"""The real decoders, when the speech add-ons are installed: a recording is turned into
samples by the libraries themselves, with no model loaded and nothing downloaded.

The engines' own tests use fakes, which is how a release went out in which faster-whisper
and PyAV could not work together and every recording was refused. These run wherever an
add-on is installed (one CI job installs them) and are skipped elsewhere.
"""

from __future__ import annotations

import io
import math
import struct
import wave

import pytest


def a_second_of_tone() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 10)))
                               for i in range(16000)))
    return out.getvalue()


def test_whispers_own_decoder_reads_a_recording_with_the_pyav_that_is_installed():
    pytest.importorskip("av")
    audio = pytest.importorskip("faster_whisper.audio")
    samples = audio.decode_audio(io.BytesIO(a_second_of_tone()), sampling_rate=16000)
    assert 15000 <= len(samples) <= 17000


def test_parakeets_decoder_reads_a_recording_with_the_pyav_that_is_installed():
    pytest.importorskip("av")
    pytest.importorskip("numpy")
    from jimmyhq.speech import audio

    samples = audio.decode_with_av(a_second_of_tone(), 600.0)
    assert 15000 <= len(samples) <= 17000
