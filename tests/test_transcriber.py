import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

from src import transcriber


def test_vad_and_spanish_transcription_without_loading_weights(monkeypatch):
    model = Mock()
    model.transcribe.return_value = (iter([NS(text=" hola "), NS(text=" mundo ")]), None)
    monkeypatch.setattr(transcriber, "obtener_modelo", lambda: model)
    assert transcriber.transcribir("fake.ogg") == "hola mundo"
    kwargs = model.transcribe.call_args.kwargs
    assert kwargs["vad_filter"] is True
    assert kwargs["language"] == "es"


def test_cpu_model_is_configured_without_torch(monkeypatch):
    fake = Mock()
    monkeypatch.setitem(sys.modules, "faster_whisper", NS(WhisperModel=fake))
    monkeypatch.setitem(sys.modules, "ctranslate2", NS(get_cuda_device_count=lambda: 0))
    monkeypatch.setattr(transcriber, "WHISPER_DEVICE", "auto")
    monkeypatch.setattr(transcriber, "WHISPER_COMPUTE_TYPE", "auto")
    transcriber.obtener_modelo.cache_clear()
    try:
        transcriber.obtener_modelo()
        assert fake.call_args.kwargs == {"device": "cpu", "compute_type": "int8"}
    finally:
        transcriber.obtener_modelo.cache_clear()
