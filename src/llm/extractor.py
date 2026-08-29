import logging

from ollama import chat

from src.config import OLLAMA_MODEL

from .models import DiaryAnalysis
from .prompts import SYSTEM_PROMPT, build_prompt

logger = logging.getLogger(__name__)


class DiaryExtractor:

    def __init__(self, model: str | None = None):
        self.model = model or OLLAMA_MODEL
        self._response_format = DiaryAnalysis.model_json_schema()

        logger.info(
            "DiaryExtractor inicializado con modelo '%s'",
            self.model,
        )

    def extract(self, transcription: str) -> DiaryAnalysis:
        if not transcription or not transcription.strip():
            raise ValueError("La transcripción no puede estar vacía.")

        clean_transcription = transcription.strip()

        logger.info(
            "Iniciando extracción con modelo '%s' (%d caracteres)",
            self.model,
            len(clean_transcription),
        )

        try:
            response = chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": build_prompt(clean_transcription),
                    },
                ],
                format=self._response_format,
                options={
                    "temperature": 0.0,
                },
            )

            logger.info("Respuesta recibida de Ollama; validando esquema JSON.")
            result = DiaryAnalysis.model_validate_json(response.message.content)

            logger.info(
                "Extracción completada. Resumen: %s",
                result.summary[:80] if result.summary else "",
            )
            return result

        except Exception:
            logger.exception(
                "Error durante la extracción con el modelo '%s'",
                self.model,
            )
            raise
