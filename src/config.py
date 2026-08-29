from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise ValueError(f"No se ha encontrado la variable de entorno '{name}'")
    return value


TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN", required=True)
TELEGRAM_USER_ID = int(get_env("TELEGRAM_USER_ID", required=True))
OLLAMA_MODEL = get_env("OLLAMA_MODEL", "qwen3:1.7b") or "qwen3:1.7b"
WHISPER_MODEL = get_env("WHISPER_MODEL", "openai/whisper-base") or "openai/whisper-base"
LOG_LEVEL = get_env("LOG_LEVEL", "INFO") or "INFO"
AUDIO_DIR = PROJECT_ROOT / "audios"
DATABASE_PATH = Path(
    get_env("DATABASE_PATH", str(PROJECT_ROOT / "dear_diary.db"))
    or PROJECT_ROOT / "dear_diary.db"
)
