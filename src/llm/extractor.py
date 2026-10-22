"""Bounded Ollama requests with validation, retry and hierarchical synthesis."""
import json
import logging
import re
import unicodedata

from ollama import Client

from src.config import MAX_LLM_CHARS, OLLAMA_MODEL, OLLAMA_TIMEOUT_SECONDS
from .models import DailySummary, DiaryAnalysis
from .prompts import DAILY_SYSTEM_PROMPT, SYSTEM_PROMPT, build_prompt

logger = logging.getLogger(__name__)
INFERENCE_OPTIONS = {"temperature": 0.0, "num_ctx": 8192, "num_predict": 2048}


def dividir_texto(text, limit=MAX_LLM_CHARS):
    """Keep every character; prefer a word boundary when possible."""
    while len(text) > limit:
        cut = text.rfind(" ", limit // 2, limit)
        cut = cut if cut > 0 else limit
        yield text[:cut]
        text = text[cut:]
    if text.strip():
        yield text


def menciones_literales(values, text, people=False):
    source = unicodedata.normalize("NFC", " ".join(text.split()))
    self_labels = {"yo", "narrador", "narradora", "el narrador", "la narradora",
                   "usuario", "usuaria", "el usuario", "la usuaria"}
    grounded = []
    for value in values:
        value = unicodedata.normalize("NFC", " ".join(value.split()))
        if not value or (people and value.casefold() in self_labels):
            continue
        match = re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", source, re.IGNORECASE)
        if match and match.group() not in grounded:
            grounded.append(match.group())
    return grounded


class DiaryExtractor:
    def __init__(self, model=None, client=None):
        self.model = model or OLLAMA_MODEL
        self.client = client or Client(timeout=OLLAMA_TIMEOUT_SECONDS)

    def _request(self, system, content, schema):
        last_type = "UnknownError"
        for _ in range(2):
            try:
                response = self.client.chat(
                    model=self.model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": content}],
                    format=schema.model_json_schema(),
                    options=INFERENCE_OPTIONS.copy(),
                    think=False,
                )
                return schema.model_validate_json(response.message.content)
            except Exception as error:
                # Validation exceptions may contain diary text: never log their message.
                last_type = type(error).__name__
                logger.warning("Respuesta LLM fallida: tipo=%s", last_type)
        raise RuntimeError(f"No se pudo obtener un análisis válido ({last_type})") from None

    def extract(self, transcription):
        if not transcription or not transcription.strip():
            raise ValueError("La transcripción no puede estar vacía")
        parts = []
        for chunk in dividir_texto(transcription.strip()):
            result = self._request(SYSTEM_PROMPT, build_prompt(chunk), DiaryAnalysis)
            parts.append(result.model_copy(update={
                "people": menciones_literales(result.people, chunk, people=True),
                "places": menciones_literales(result.places, chunk),
            }))
        if len(parts) == 1:
            return parts[0]
        sentiments = {p.sentiment for p in parts}
        # Preserve all extracted mentions; output truncation is only a Telegram concern.
        # model_construct is intentional: merged lists can exceed each request's schema limit.
        return DiaryAnalysis.model_construct(
            summary=self.summarize_day([p.summary for p in parts]),
            sentiment=next(iter(sentiments)) if len(sentiments) == 1 else "mezclado",
            emotions=sorted({e for p in parts for e in p.emotions}),
            people=sorted({e for p in parts for e in p.people}),
            places=sorted({e for p in parts for e in p.places}),
            events=sorted({e for p in parts for e in p.events}),
        )

    def summarize_day(self, summaries):
        if not summaries:
            return "Sin resumen disponible."
        text = "\n".join(summaries)
        # Each intermediate result is <=2000 characters, so this loop reduces long inputs.
        while len(text) > MAX_LLM_CHARS:
            text = "\n".join(
                self._request(DAILY_SYSTEM_PROMPT,
                              json.dumps({"summaries": chunk}, ensure_ascii=False),
                              DailySummary).summary
                for chunk in dividir_texto(text)
            )
        return self._request(
            DAILY_SYSTEM_PROMPT,
            json.dumps({"summaries": text}, ensure_ascii=False), DailySummary,
        ).summary
