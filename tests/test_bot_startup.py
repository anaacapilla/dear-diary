import ast
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

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


def test_daily_report_uses_single_summary_for_one_entry(tmp_path, monkeypatch):
    db_path = tmp_path / "diary.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)

    database.inicializar_db()

    database.guardar_entrada(
        "texto de una entrada",
        received_at="2026-08-30T11:00:00+00:00",
        recorded_at="2026-08-30T11:00:00+00:00",
        summary="Resumen único del día",
        sentiment="positivo",
        events=["evento único"],
    )

    report = database.obtener_reporte_diario("2026-08-30")

    assert report["entries_count"] == 1
    assert report["summary"] == "Resumen único del día"


def test_daily_report_aggregates_summaries_for_multiple_entries(tmp_path, monkeypatch):
    db_path = tmp_path / "diary.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)

    database.inicializar_db()

    database.guardar_entrada(
        "texto uno",
        received_at="2026-08-30T09:00:00+00:00",
        recorded_at="2026-08-30T09:00:00+00:00",
        summary="Resumen de la mañana",
        sentiment="positivo",
        events=["evento uno"],
    )

    database.guardar_entrada(
        "texto dos",
        received_at="2026-08-30T18:00:00+00:00",
        recorded_at="2026-08-30T18:00:00+00:00",
        summary="Resumen de la tarde",
        sentiment="negativo",
        events=["evento dos"],
    )

    report = database.obtener_reporte_diario("2026-08-30")

    assert report["entries_count"] == 2
    assert "Resumen de la mañana" in report["summary"]
    assert "Resumen de la tarde" in report["summary"]


def test_obtener_dias_con_entradas_del_mes(tmp_path, monkeypatch):
    db_path = tmp_path / "diary.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)

    database.inicializar_db()

    database.guardar_entrada(
        "texto uno",
        received_at="2026-08-10T09:00:00+00:00",
        recorded_at="2026-08-10T09:00:00+00:00",
        summary="Resumen 10",
        sentiment="positivo",
        events=["evento uno"],
    )

    database.guardar_entrada(
        "texto dos",
        received_at="2026-08-12T18:00:00+00:00",
        recorded_at="2026-08-12T18:00:00+00:00",
        summary="Resumen 12",
        sentiment="negativo",
        events=["evento dos"],
    )

    days = database.obtener_dias_con_entradas_del_mes(2026, 8)

    assert "2026-08-10" in days
    assert "2026-08-12" in days
    assert len(days) == 2


def test_help_command_mentions_only_report_command():
    bot_module = _load_bot_module()

    update = type(
        "DummyUpdate",
        (),
        {
            "message": type("DummyMessage", (), {"reply_text": AsyncMock()})(),
            "effective_user": type("DummyUser", (), {"id": 8028882})(),
        },
    )()

    import asyncio
    asyncio.run(bot_module.help_command(update, None))

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "/report YYYY-MM-DD" in text
    assert "/ping" not in text
