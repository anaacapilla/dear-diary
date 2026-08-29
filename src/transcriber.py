import logging
import os

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
from transformers import pipeline

from src.config import WHISPER_MODEL

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURACIÓN
# ============================================================

MODEL_NAME = WHISPER_MODEL

logger.info("Comprobando disponibilidad de CUDA")
if torch.cuda.is_available():
    DEVICE = "cuda:0"
    DTYPE = torch.float16
    logger.info(
        "GPU CUDA detectada: %s. Usando GPU.",
        torch.cuda.get_device_name(0),
    )

else:
    DEVICE = "cpu"
    DTYPE = torch.float32
    logger.info("No se ha detectado GPU CUDA. Usando CPU.")


# ============================================================
# MODELO
# ============================================================

logger.info("Cargando modelo Whisper: %s", MODEL_NAME)
logger.info("Descargando o leyendo desde caché los pesos de Whisper")

model = AutoModelForSpeechSeq2Seq.from_pretrained(
    MODEL_NAME,
    dtype=DTYPE,
    low_cpu_mem_usage=True,
    use_safetensors=True,
)
logger.info("Pesos de Whisper cargados; moviendo modelo a %s", DEVICE)

model.to(DEVICE)

logger.info("Cargando procesador Whisper")
processor = AutoProcessor.from_pretrained(MODEL_NAME)

logger.info("Creando pipeline de transcripción")
transcriber = pipeline(
    "automatic-speech-recognition",
    model=model,
    tokenizer=processor.tokenizer,
    feature_extractor=processor.feature_extractor,
    device=DEVICE,
    dtype=DTYPE,
)

logger.info("Whisper listo 🚀")


# ============================================================
# TRANSCRIPCIÓN
# ============================================================


def transcribir(ruta):

    logger.info("Transcribiendo: %s", ruta)

    resultado = transcriber(
        str(ruta),
        chunk_length_s=30,
        stride_length_s=5,
        return_timestamps=True,
        generate_kwargs={
            "language": "es",
            "task": "transcribe",
        },
    )

    texto = resultado["text"].strip()

    logger.info(
        "Transcripción completada. Caracteres: %d",
        len(texto),
    )

    return texto
