import ast
import importlib.util
from pathlib import Path

import src.database as database

BOT_FILE = Path(__file__).resolve().parents[1] / "src" / "bot.py"


class DummyAnalysis:
    def __init__(self):
        self.summary = "Resumen breve"
        self.sentiment = "positivo"
        self.emotions = ["felicidad", "calma"]
        self.places = ["casa"]
        self.people = ["Ana"]


def _has_asyncio_run_call(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "run":
            if isinstance(func.value, ast.Name) and func.value.id == "asyncio":
                return True
    return False


def _load_bot_module():
    spec = importlib.util.spec_from_file_location("dear_diary_bot_under_test", BOT_FILE)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_bot_uses_post_init_instead_of_asyncio_run():
    source = BOT_FILE.read_text(encoding="utf-8")

    assert not _has_asyncio_run_call(source), (
        "No debe llamarse a asyncio.run() antes de app.run_polling() porque "
        "cierra el bucle de eventos y rompe la inicialización del bot."
    )
    assert "app.post_init" in source, (
        "La bienvenida inicial debe registrarse con post_init para ejecutar "
        "en el mismo loop que usa Telegram."
    )


def test_formatear_informe_omits_raw_transcription_for_telegram_limit():
    bot_module = _load_bot_module()
    long_transcription = "A " * 5000
    dummy_analysis = DummyAnalysis()
    dummy_analysis.summary = "A " * 5000
    result = bot_module.formatear_informe(
        long_transcription,
        dummy_analysis,
        1.5,
        2.2,
    )

    assert "Transcripción" not in result
    assert "Resumen" in result
    assert "Sentimiento" in result
    assert "..." in result
    assert len(result) < 4096


def test_guardar_entrada_ignores_duplicate_transcription_hash(tmp_path, monkeypatch):
    db_path = tmp_path / "diary.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)

    database.inicializar_db()

    first = database.guardar_entrada(
        "mismo texto",
        received_at="2026-08-30T11:00:00+00:00",
        recorded_at="2026-08-30T11:00:00+00:00",
        summary="Resumen",
        sentiment="positivo",
        events=["evento"],
    )

    second = database.guardar_entrada(
        "mismo texto",
        received_at="2026-08-30T11:05:00+00:00",
        recorded_at="2026-08-30T11:05:00+00:00",
        summary="Resumen nuevo",
        sentiment="positivo",
        events=["evento"],
    )

    assert first == second
