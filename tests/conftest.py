import os
from types import SimpleNamespace

# Never use real Telegram credentials or the project's database in tests.
os.environ["TELEGRAM_BOT_TOKEN"] = "12345:TEST"
os.environ["TELEGRAM_USER_ID"] = "12345"
os.environ["DIARY_TIMEZONE"] = "Europe/Madrid"

import pytest
from src import database
from src.llm.models import DiaryAnalysis


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "diary.db")
    database.inicializar_db()
    return database


@pytest.fixture
def analysis():
    return DiaryAnalysis(summary="Paseo agradable con Ana en Madrid.",
                         sentiment="positivo", emotions=["alegria"],
                         people=["Ana"], places=["Madrid"], events=["paseo"])
