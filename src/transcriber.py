import logging
from faster_whisper import WhisperModel

from src.config import WHISPER_MODEL

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURACIÓN
# ============================================================

MODEL_NAME = WHISPER_MODEL

logger.info("Comprobando disponibilidad de CUDA")
try:
    import torch

    CUDA_AVAILABLE = torch.cuda.is_available()
except ImportError:
    CUDA_AVAILABLE = False

if CUDA_AVAILABLE:
    DEVICE = "cuda"
    COMPUTE_TYPE = "float16"
    logger.info("GPU CUDA detectada. Usando GPU.")
else:
    DEVICE = "cpu"
    COMPUTE_TYPE = "int8"
    logger.info("No se ha detectado GPU CUDA. Usando CPU.")


# ============================================================
# MODELO
# ============================================================

logger.info("Cargando modelo Whisper: %s", MODEL_NAME)
logger.info("Descargando o leyendo desde caché los pesos de Whisper")

model = WhisperModel(
    MODEL_NAME,
    device=DEVICE,
    compute_type=COMPUTE_TYPE,
)

logger.info("Whisper listo 🚀")


# ============================================================
# TRANSCRIPCIÓN
# ============================================================


def transcribir(ruta):

    logger.info("Transcribiendo: %s", ruta)

    segmentos, _ = model.transcribe(
        str(ruta),
        language="es",
        task="transcribe",
    )

    texto = " ".join(segmento.text.strip() for segmento in segmentos).strip()

    logger.info(
        "Transcripción completada. Caracteres: %d",
        len(texto),
    )

    return texto
