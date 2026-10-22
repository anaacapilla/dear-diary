"""Lazy, serialized faster-whisper inference; importing this module is offline."""
import logging
from functools import lru_cache
from threading import Lock

from src.config import WHISPER_COMPUTE_TYPE, WHISPER_DEVICE, WHISPER_MODEL

logger = logging.getLogger(__name__)
_lock = Lock()


@lru_cache(maxsize=1)
def obtener_modelo():
    import ctranslate2
    from faster_whisper import WhisperModel

    device = WHISPER_DEVICE
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
    compute = WHISPER_COMPUTE_TYPE
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    logger.info("Cargando Whisper: modelo=%s dispositivo=%s", WHISPER_MODEL, device)
    return WhisperModel(WHISPER_MODEL, device=device, compute_type=compute)


def transcribir(ruta):
    with _lock:
        segmentos, _ = obtener_modelo().transcribe(
            str(ruta), language="es", task="transcribe", vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segmentos).strip()
