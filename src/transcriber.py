import os

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import logging

import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
from transformers import pipeline


logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURACIÓN
# ============================================================

MODEL_NAME = "openai/whisper-base"

if torch.cuda.is_available():
    DEVICE = "cuda:0"
    DTYPE = torch.float16
    logger.info("GPU CUDA detectada. Usando GPU.")

else:
    DEVICE = "cpu"
    DTYPE = torch.float32
    logger.info("No se ha detectado GPU CUDA. Usando CPU.")


# ============================================================
# MODELO
# ============================================================

logger.info("Cargando modelo Whisper: %s", MODEL_NAME)

model = AutoModelForSpeechSeq2Seq.from_pretrained(
    MODEL_NAME,
    dtype=DTYPE,
    low_cpu_mem_usage=True,
    use_safetensors=True,
)

model.to(DEVICE)

processor = AutoProcessor.from_pretrained(MODEL_NAME)

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
