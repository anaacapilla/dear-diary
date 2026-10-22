"""Configuration without loading models or requiring Telegram for utilities."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_env(name: str, default: str | None = None, *, required=False):
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"Falta la variable de entorno '{name}'")
    return value


def project_path(name: str, default: str) -> Path:
    path = Path(get_env(name, default) or default).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_USER_ID = int(get_env("TELEGRAM_USER_ID", "0") or "0")
OLLAMA_MODEL = get_env("OLLAMA_MODEL", "qwen3:1.7b") or "qwen3:1.7b"
WHISPER_MODEL = get_env("WHISPER_MODEL", "base") or "base"
# Accept old .env values, loading faster-whisper's converted weights.
if WHISPER_MODEL.startswith("openai/whisper-"):
    WHISPER_MODEL = WHISPER_MODEL.removeprefix("openai/whisper-")
WHISPER_DEVICE = get_env("WHISPER_DEVICE", "auto")
WHISPER_COMPUTE_TYPE = get_env("WHISPER_COMPUTE_TYPE", "auto")
LOG_LEVEL = get_env("LOG_LEVEL", "INFO") or "INFO"
DIARY_TIMEZONE = get_env("DIARY_TIMEZONE", "Europe/Madrid") or "Europe/Madrid"
DIARY_TZ = ZoneInfo(DIARY_TIMEZONE)
AUDIO_DIR = project_path("AUDIO_DIR", "audios")
DATABASE_PATH = project_path("DATABASE_PATH", "dear_diary.db")
BACKUP_DIR = project_path("BACKUP_DIR", "backups")
AUDIO_RETENTION_DAYS = int(get_env("AUDIO_RETENTION_DAYS", "7"))
BACKUP_RETENTION_DAYS = int(get_env("BACKUP_RETENTION_DAYS", "7"))
OLLAMA_TIMEOUT_SECONDS = float(get_env("OLLAMA_TIMEOUT_SECONDS", "120"))
MAX_AUDIO_BYTES = int(get_env("MAX_AUDIO_BYTES", "20000000"))
MAX_AUDIO_SECONDS = int(get_env("MAX_AUDIO_SECONDS", "1800"))
MAX_LLM_CHARS = int(get_env("MAX_LLM_CHARS", "12000"))
if min(AUDIO_RETENTION_DAYS, BACKUP_RETENTION_DAYS, MAX_AUDIO_BYTES,
       MAX_AUDIO_SECONDS, MAX_LLM_CHARS, OLLAMA_TIMEOUT_SECONDS) <= 0:
    raise ValueError("Los límites y periodos de retención deben ser positivos")


def validar_telegram():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_USER_ID <= 0:
        raise ValueError("Configura TELEGRAM_BOT_TOKEN y TELEGRAM_USER_ID antes de arrancar")

if not 4000 <= MAX_LLM_CHARS <= 12000:
    raise ValueError("MAX_LLM_CHARS debe estar entre 4000 y 12000")
