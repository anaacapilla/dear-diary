from pathlib import Path
from unittest.mock import Mock
import wave

import httpx
import pytest

from src import groq_transcriber as groq, transcriber


def test_chunking_preserves_every_sample(tmp_path, monkeypatch):
    source = tmp_path / "original.audio"
    pcm = b"\x01\x00" * (groq.SAMPLE_RATE * 3 + 17)
    groq._write_wav(source, pcm)
    monkeypatch.setattr(groq, "CHUNK_SECONDS", 1)
    restored = bytearray()
    paths = list(groq._chunks(source, tmp_path))
    assert len(paths) == 4
    for path in paths:
        with wave.open(str(path)) as audio:
            restored.extend(audio.readframes(audio.getnframes()))
    assert restored == pcm


def test_413_splits_and_joins_in_order(tmp_path):
    path = tmp_path / "audio.wav"
    groq._write_wav(path, b"\0\0" * groq.SAMPLE_RATE * 4)
    client = Mock()
    client.post.side_effect = [httpx.Response(413), httpx.Response(200, json={"text": "primero"}),
                               httpx.Response(200, json={"text": "segundo"})]
    assert groq._request(client, path) == "primero segundo"
    assert list(tmp_path.iterdir()) == [path]
    assert client.post.call_args.kwargs["data"]["language"] == "es"


def test_rate_limit_respects_retry_after(tmp_path, monkeypatch):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"audio")
    client = Mock()
    client.post.side_effect = [httpx.Response(429, headers={"retry-after": "3"}),
                               httpx.Response(200, json={"text": "hola"})]
    sleep = Mock()
    monkeypatch.setattr(groq.time, "sleep", sleep)
    assert groq._request(client, path) == "hola"
    sleep.assert_called_once_with(3)


@pytest.mark.parametrize("status", [400, 401, 403])
def test_permanent_errors_are_not_retried_or_leaked(tmp_path, status):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"audio")
    client = Mock()
    client.post.return_value = httpx.Response(status, text="PRIVATE CONTENT API KEY")
    with pytest.raises(groq.GroqTranscriptionError) as error:
        groq._request(client, path)
    assert "PRIVATE" not in str(error.value)
    assert client.post.call_count == 1


def test_daily_quota_does_not_block_worker(tmp_path, monkeypatch):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"audio")
    client = Mock()
    client.post.return_value = httpx.Response(429, headers={"retry-after": "3600"})
    sleep = Mock()
    monkeypatch.setattr(groq.time, "sleep", sleep)
    with pytest.raises(groq.GroqTranscriptionError):
        groq._request(client, path)
    sleep.assert_not_called()


def test_transient_failures_stop_after_bounded_retries(tmp_path, monkeypatch):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"audio")
    client = Mock()
    client.post.return_value = httpx.Response(503, text="private")
    monkeypatch.setattr(groq.time, "sleep", Mock())
    with pytest.raises(groq.GroqTranscriptionError):
        groq._request(client, path)
    assert client.post.call_count == 4


def test_invalid_response_is_not_saved_as_transcription(tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"audio")
    client = Mock()
    client.post.return_value = httpx.Response(200, json={"text": None})
    with pytest.raises(groq.GroqTranscriptionError):
        groq._request(client, path)


def test_groq_routing_never_loads_local_model(monkeypatch):
    monkeypatch.setattr(transcriber, "TRANSCRIPTION_PROVIDER", "groq")
    monkeypatch.setattr(transcriber, "validar_transcripcion", lambda: None)
    remote = Mock(return_value="texto")
    monkeypatch.setattr(groq, "transcribir_groq", remote)
    monkeypatch.setattr(transcriber, "obtener_modelo", Mock(side_effect=AssertionError))
    assert transcriber.transcribir("audio.ogg") == "texto"


def test_temporary_files_cleaned_on_failure(tmp_path, monkeypatch):
    source = tmp_path / "original.wav"
    groq._write_wav(source, b"\0\0" * groq.SAMPLE_RATE)
    monkeypatch.setattr(groq, "GROQ_API_KEY", "test")
    seen = []
    def fail(client, path):
        seen.append(path)
        raise groq.GroqTranscriptionError("fallo")
    monkeypatch.setattr(groq, "_request", fail)
    with pytest.raises(groq.GroqTranscriptionError):
        groq.transcribir_groq(source)
    assert source.exists()
    assert seen and not seen[0].parent.exists()
