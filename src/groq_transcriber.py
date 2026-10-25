"""Groq transcription with bounded PCM chunks; no audio or credentials in logs."""
import math
import tempfile
import time
import wave
from pathlib import Path

import av
import httpx

from src.config import GROQ_API_KEY, GROQ_MODEL

ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
SAMPLE_RATE = 16000
CHUNK_SECONDS = 600  # 19.2 MB WAV, below the free plan's 25 MB.
MAX_RETRIES = 3
MAX_RETRY_WAIT = 60


class GroqTranscriptionError(RuntimeError):
    """Safe error message: never includes server response bodies or request data."""


def _write_wav(path, pcm):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm)


def _chunks(source, directory):
    """Decode incrementally, including the resampler tail, without dropping samples."""
    chunk_bytes = CHUNK_SECONDS * SAMPLE_RATE * 2
    pending = bytearray()
    index = 0
    with av.open(str(source)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)

        def frames():
            for frame in container.decode(audio=0):
                yield from resampler.resample(frame)
            yield from resampler.resample(None)

        for frame in frames():
            pending.extend(frame.to_ndarray().tobytes())
            while len(pending) >= chunk_bytes:
                path = directory / f"chunk-{index}.wav"
                _write_wav(path, pending[:chunk_bytes])
                del pending[:chunk_bytes]
                yield path
                index += 1
        if pending:
            path = directory / f"chunk-{index}.wav"
            _write_wav(path, pending)
            yield path


def _request(client, path):
    for attempt in range(MAX_RETRIES + 1):
        response = None
        try:
            with path.open("rb") as audio:
                response = client.post(
                    ENDPOINT,
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    files={"file": ("audio.wav", audio, "audio/wav")},
                    data={"model": GROQ_MODEL, "language": "es", "temperature": "0",
                          "response_format": "json"},
                )
        except httpx.TransportError:
            if attempt == MAX_RETRIES:
                raise GroqTranscriptionError("No se pudo conectar con Groq") from None
        else:
            if response.status_code == 413:
                return _split_request(client, path)
            if response.is_success:
                try:
                    text = response.json()["text"]
                    if not isinstance(text, str):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise GroqTranscriptionError("Respuesta de Groq no válida") from None
                return text.strip()
            if response.status_code != 429 and response.status_code < 500:
                raise GroqTranscriptionError(f"Groq rechazó la transcripción (HTTP {response.status_code})")
            if attempt == MAX_RETRIES:
                raise GroqTranscriptionError("Groq no disponible o cuota agotada; reintenta más tarde")
        delay = 2 ** attempt
        if response is not None:
            try:
                delay = float(response.headers.get("retry-after", delay))
            except ValueError:
                pass
        if not math.isfinite(delay) or delay > MAX_RETRY_WAIT:
            raise GroqTranscriptionError("Cuota de Groq agotada; reintenta más tarde")
        time.sleep(max(0, delay))
    raise AssertionError("unreachable")


def _split_request(client, path):
    with wave.open(str(path), "rb") as audio:
        count = audio.getnframes()
        if count < SAMPLE_RATE * 2:
            raise GroqTranscriptionError("Groq rechaza incluso un fragmento mínimo")
        pcm = audio.readframes(count)
    middle = (count // 2) * 2
    texts = []
    for suffix, data in (("a", pcm[:middle]), ("b", pcm[middle:])):
        child = path.with_name(path.stem + suffix + ".wav")
        try:
            _write_wav(child, data)
            texts.append(_request(client, child))
        finally:
            child.unlink(missing_ok=True)
    return " ".join(text for text in texts if text)


def transcribir_groq(source):
    if not GROQ_API_KEY.strip():
        raise GroqTranscriptionError("Configura GROQ_API_KEY")
    texts = []
    with tempfile.TemporaryDirectory(prefix="diary-groq-") as directory:
        with httpx.Client(timeout=httpx.Timeout(180, connect=15), follow_redirects=False) as client:
            for path in _chunks(source, Path(directory)):
                try:
                    texts.append(_request(client, path))
                finally:
                    path.unlink(missing_ok=True)
    return " ".join(text for text in texts if text)
