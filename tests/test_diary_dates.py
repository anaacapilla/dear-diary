import asyncio
import hashlib
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from src import bot, database, maintenance, people, web_data, web_entries
from src.processing_queue import ProcessingQueue
from src.statistics import emotion_counts


DAY = date(2026, 1, 12)


def test_partial_v10_database_is_completed_before_web_creation(db):
    """A running web may have migrated the intermediate v10 during development."""
    old_entry = db.guardar_entrada("Texto anterior", entry_date=DAY)
    with db.conectar() as conn:
        conn.execute("DROP INDEX web_request_unique")
        conn.execute("ALTER TABLE diary_entries DROP COLUMN web_request_id")
        conn.execute("ALTER TABLE diary_entries DROP COLUMN processing")
        for table in ("deleted_web_requests", "deleted_telegram_messages", "audio_deletions", "telegram_entry_dates"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("PRAGMA user_version=10")
    web_data.prepare_database()
    new_entry = web_entries.create_entry(DAY, "Nueva entrada", "v10-recovery")
    assert db.obtener_entrada(new_entry)["status"] == "pending"
    assert db.obtener_entrada(old_entry)["transcription"] == "Texto anterior"
    with db.conectar() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    backups = list((db.DB_PATH.parent / "backups").glob("migration-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10


def message(number, text=None):
    msg = NS(chat_id=12345, message_id=number, text=text, date=datetime.now(timezone.utc),
             reply_text=AsyncMock(return_value=NS(message_id=number + 1000)),
             voice=NS(file_id="FILE", file_size=20, duration=3), audio=None)
    return NS(message=msg, effective_user=NS(id=12345), effective_chat=NS(id=12345, type="private"))


def context():
    client = NS(send_message=AsyncMock(return_value=NS(message_id=9000)), edit_message_text=AsyncMock())
    return NS(bot=client, application=NS(bot=client, bot_data={"processing_queue": ProcessingQueue()}))


def test_diary_date_controls_reports_search_and_statistics_without_changing_upload_time(db, analysis):
    uploaded = "2026-10-24T08:00:00+02:00"
    entry = db.guardar_entrada("Vi a Ana", entry_date=DAY, recorded_at=uploaded, received_at=uploaded,
                               entities=[("person", "Ana")], emotions=["alegria"])
    item = db.obtener_entrada(entry)
    assert item["entry_date"] == DAY.isoformat()
    assert item["recorded_at"] == item["received_at"] == uploaded
    assert [r["id"] for r in web_data.entries(DAY, DAY)] == [entry]
    assert not web_data.entries(date(2026, 10, 24), date(2026, 10, 24))
    assert db.obtener_reporte_diario(DAY.isoformat())["people"] == ["Ana"]
    assert db.obtener_dias_con_entradas_del_mes(2026, 1) == [DAY.isoformat()]
    assert emotion_counts(DAY, DAY) == [{"emotion": "alegria", "count": 1}]


def test_v9_migration_backfills_local_dates_and_preserves_people_and_timestamps(db):
    people.save_people([{"name": "Ana García", "aliases": ["Ana"]}], people.snapshot([]))
    entry = db.guardar_entrada("Ana", recorded_at="2026-01-11T23:30:00+00:00", telegram_chat_id=12345, telegram_message_id=5)
    before = db.obtener_detalles(entry)
    with db.conectar() as conn:
        for column in ("entry_date", "source", "processing", "web_request_id"):
            conn.execute(f"ALTER TABLE diary_entries DROP COLUMN {column}" if column != "web_request_id" else "DROP INDEX web_request_unique")
        conn.execute("ALTER TABLE diary_entries DROP COLUMN web_request_id")
        conn.execute("PRAGMA user_version=9")
    db.inicializar_db()
    after = db.obtener_detalles(entry)
    assert after["entry_date"] == DAY.isoformat()
    assert after["source"] == "telegram"
    assert after["people"] == before["people"] == ["Ana García"]
    assert after["recorded_at"] == before["recorded_at"]
    backups = list((db.DB_PATH.parent / "backups").glob("migration-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9


@pytest.mark.parametrize("day", ["2026-02-30", "2099-01-01", "1899-12-31", "tomorrow", "2026-01-12T12:00:00"])
def test_invalid_or_future_dates_are_rejected(db, day):
    with pytest.raises(ValueError):
        web_entries.create_entry(day, "Texto", "request")
    with db.conectar() as conn:
        assert conn.execute("SELECT count(*) FROM diary_entries").fetchone()[0] == 0


def test_telegram_date_is_consumed_atomically_only_by_next_new_audio(db, monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    ctx = context()
    async def scenario():
        await bot.orientar_usuario(message(10, "fecha 2026-01-12"), ctx)
        await bot.recibir_audio(message(11), ctx)
        first = db.obtener_entrada_telegram(12345, 11)
        assert first["entry_date"] == DAY.isoformat()
        await bot.orientar_usuario(message(10, "fecha 2026-01-12"), ctx)  # Redelivery after consumption.
        await bot.recibir_audio(message(11), ctx)  # Duplicate does not consume a future intent.
        await bot.recibir_audio(message(12), ctx)
        second = db.obtener_entrada_telegram(12345, 12)
        assert second["entry_date"] == database._obtener_fecha_desde_iso(second["recorded_at"])
        assert first["recorded_at"] != DAY.isoformat()
    asyncio.run(scenario())


def test_duplicate_or_older_audio_does_not_consume_new_date(db, monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    ctx = context()
    async def scenario():
        await bot.recibir_audio(message(1), ctx)
        await bot.orientar_usuario(message(3, "fecha 12/01/2026"), ctx)
        await bot.recibir_audio(message(1), ctx)
        await bot.recibir_audio(message(2), ctx)
        await bot.recibir_audio(message(4), ctx)
        assert db.obtener_entrada_telegram(12345, 4)["entry_date"] == DAY.isoformat()
    asyncio.run(scenario())


def test_rejected_audio_keeps_the_date_and_fecha_hoy_cancels_it(db, monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    ctx = context()
    async def scenario():
        await bot.orientar_usuario(message(1, "fecha: 2026-01-12"), ctx)
        big = message(2)
        big.message.voice.duration = bot.MAX_AUDIO_SECONDS + 1
        await bot.recibir_audio(big, ctx)
        with db.conectar() as conn:
            assert conn.execute("SELECT entry_date FROM telegram_entry_dates").fetchone()[0] == DAY.isoformat()
        await bot.orientar_usuario(message(3, "fecha hoy"), ctx)
        await bot.recibir_audio(message(4), ctx)
        assert db.obtener_entrada_telegram(12345, 4)["entry_date"] != DAY.isoformat()
    asyncio.run(scenario())


def test_invalid_and_unauthorized_date_commands_do_not_change_selection(db):
    ctx = context()
    async def scenario():
        await bot.orientar_usuario(message(1, "fecha 2026-01-12"), ctx)
        await bot.orientar_usuario(message(2, "fecha 2099-01-01"), ctx)
        unauthorized = message(3, "fecha hoy")
        unauthorized.effective_user.id = 999
        await bot.orientar_usuario(unauthorized, ctx)
    asyncio.run(scenario())
    with db.conectar() as conn:
        assert conn.execute("SELECT entry_date FROM telegram_entry_dates").fetchone()[0] == DAY.isoformat()


def test_web_submission_is_idempotent_and_validation_keeps_database_empty(db):
    with pytest.raises(ValueError):
        web_entries.create_entry(DAY, "   ", "one")
    entry = web_entries.create_entry(DAY, " Texto ", "one")
    assert web_entries.create_entry(DAY, " Texto ", "one") == entry
    assert db.obtener_entrada(entry)["source"] == "web"
    with pytest.raises(ValueError):
        web_entries.create_entry(DAY, "Otro texto", "one")
    assert len(web_data.entries(DAY, DAY)) == 1
    assert web_entries.delete_entry(entry, db.obtener_entrada(entry)["content_hash"])
    with pytest.raises(ValueError, match="borró"):
        web_entries.create_entry(DAY, "Texto", "one")


def test_audio_retention_uses_reception_not_the_retrospective_day(db, monkeypatch, tmp_path):
    from datetime import timedelta
    monkeypatch.setattr(maintenance, "AUDIO_DIR", tmp_path)
    audio = tmp_path / "recent.ogg"
    audio.write_bytes(b"test")
    now = datetime.now(timezone.utc)
    db.guardar_entrada("Ana", entry_date=DAY, received_at=(now - timedelta(hours=1)).isoformat(), audio_path=str(audio))
    assert maintenance.purgar_audios(now) == 0
    assert audio.exists()


def test_date_selection_survives_restart_and_database_write_failure(db):
    db.indicar_fecha_telegram(12345, 1, DAY)
    db.inicializar_db()
    # A failure after reserving the date must roll back the reservation as well.
    with db.conectar() as conn:
        conn.execute("""CREATE TRIGGER reject_entry BEFORE INSERT ON diary_entries
            BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        db.guardar_entrada("", telegram_chat_id=12345, telegram_message_id=2, status="pending")
    with db.conectar() as conn:
        assert conn.execute("SELECT entry_date FROM telegram_entry_dates").fetchone()[0] == DAY.isoformat()
        conn.execute("DROP TRIGGER reject_entry")
    entry = db.guardar_entrada("", telegram_chat_id=12345, telegram_message_id=2, status="pending")
    assert db.obtener_entrada(entry)["entry_date"] == DAY.isoformat()


def test_web_entry_processing_notifies_telegram_without_using_whisper(db, monkeypatch, analysis):
    entry = web_entries.create_entry(DAY, "Paseo con Ana en Madrid", "one")
    transcribe = Mock(side_effect=AssertionError("Web text must not invoke Whisper"))
    monkeypatch.setattr(bot, "transcribir", transcribe)
    monkeypatch.setattr(bot, "DiaryExtractor", lambda: NS(extract=Mock(return_value=analysis)))
    ctx = context()
    async def run():
        queue = ctx.application.bot_data["processing_queue"]
        assert await bot.recuperar_cola(ctx.application) == 1
        queue.start()
        await queue.join()
        await queue.stop()
    asyncio.run(run())
    assert db.obtener_entrada(entry)["status"] == "completed"
    assert not db.obtener_entrada(entry)["processing"]
    ctx.bot.send_message.assert_awaited_once()
    sent = ctx.bot.send_message.await_args.kwargs
    assert sent["chat_id"] == 12345
    assert "12/01/2026" in sent["text"] and "desde la web" in sent["text"]
    assert "Procesando tu entrada" in sent["text"]
    assert "reply_parameters" not in sent
    assert "Entrada guardada" in ctx.bot.edit_message_text.await_args.kwargs["text"]
    assert db.obtener_entrada(entry)["telegram_progress_message_id"] == 9000
    transcribe.assert_not_called()
    assert db.obtener_detalles(entry)["people"] == ["Ana"]
    original = db.obtener_entrada(entry)["transcription"]
    db.corregir_transcripcion(entry, "Vi a Ana en Madrid", hashlib.sha256(original.encode()).hexdigest())
    assert db.obtener_entrada(entry)["entry_date"] == DAY.isoformat()


def test_delete_removes_audio_relations_and_prevents_telegram_resurrection(db, tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "AUDIO_DIR", tmp_path)
    audio = tmp_path / "one.ogg"
    audio.write_bytes(b"test")
    partial = tmp_path / "one.ogg.part"
    partial.write_bytes(b"partial")
    entry = db.guardar_entrada("Ana", audio_path=str(audio), telegram_chat_id=12345, telegram_message_id=1,
                              entities=[("person", "Ana")], emotions=["alegria"], entry_date=DAY)
    assert web_entries.delete_entry(entry, db.obtener_entrada(entry)["content_hash"])
    assert not audio.exists() and not partial.exists()
    assert not web_data.entries(DAY, DAY)
    assert db.obtener_entrada(entry) is None
    assert db.entrada_telegram_borrada(12345, 1)
    asyncio.run(bot.recibir_audio(message(1), context()))
    assert db.obtener_entrada_telegram(12345, 1) is None
    with db.conectar() as conn:
        assert not conn.execute("SELECT * FROM person_mentions").fetchall()
        assert not conn.execute("SELECT * FROM entry_emotions").fetchall()


def test_web_progress_reuses_persisted_message_and_keeps_processing_after_notification_failure(db, analysis):
    from telegram.error import NetworkError
    from src.entry_ui import ProgressMessage
    entry = web_entries.create_entry(DAY, "Vi a Ana", "notification")
    ctx = context()
    db.guardar_mensaje_progreso(entry, 8000)
    ctx.bot.edit_message_text.side_effect = NetworkError("offline")
    extractor = NS(extract=Mock(return_value=analysis))
    asyncio.run(bot.procesar_entrada(db.obtener_entrada(entry), ctx.bot,
                                    ProgressMessage.from_entry(db.obtener_entrada(entry), ctx.bot), extractor))
    assert db.obtener_entrada(entry)["status"] == "completed"
    ctx.bot.send_message.assert_not_awaited()
    assert all(call.kwargs["message_id"] == 8000 for call in ctx.bot.edit_message_text.await_args_list)


def test_web_failure_retry_button_is_owned_by_the_authorized_chat(db):
    from src import entry_actions
    entry = web_entries.create_entry(DAY, "Vi a Ana", "retry-notification")
    db.marcar_error(entry, "analysis")
    db.guardar_mensaje_progreso(entry, 8000)
    update = message(12)
    update.callback_query = NS(data="dd:retry:" + entry, message=NS(message_id=8000), answer=AsyncMock())
    ctx = context()
    enqueue = AsyncMock()
    asyncio.run(entry_actions.handle_callback(update, ctx, enqueue=enqueue))
    enqueue.assert_awaited_once()
    enqueue.reset_mock()
    update.effective_user.id = 999
    asyncio.run(entry_actions.handle_callback(update, ctx, enqueue=enqueue))
    enqueue.assert_not_awaited()


def test_telegram_progress_includes_diary_day_and_origin(db):
    from src.entry_ui import ProgressMessage
    entry = db.guardar_entrada("", entry_date=DAY, telegram_chat_id=12345, telegram_message_id=1, status="pending")
    ctx = context()
    progress = ProgressMessage.from_entry(db.obtener_entrada(entry), ctx.bot)
    asyncio.run(progress.show("Procesando tu audio…"))
    text = ctx.bot.send_message.await_args.kwargs["text"]
    assert "12/01/2026" in text and "desde Telegram" in text and "Procesando tu audio" in text


def test_active_processing_blocks_delete_and_edits_but_waiting_entry_can_be_deleted(db, analysis):
    entry = web_entries.create_entry(DAY, "Ana", "one")
    current = db.reclamar_entrada(entry)
    assert current and not db.reclamar_entrada(entry)
    with pytest.raises(ValueError, match="procesando"):
        web_entries.delete_entry(entry, current["content_hash"])
    with pytest.raises(ValueError):
        db.corregir_transcripcion(entry, "Otro", current["content_hash"])
    db.recuperar_procesamiento()
    assert web_entries.delete_entry(entry, current["content_hash"])
    assert db.reclamar_entrada(entry) is None


def test_stale_delete_confirmation_and_external_audio_path_do_not_remove_entry(db, tmp_path):
    entry = web_entries.create_entry(DAY, "Ana", "one")
    with pytest.raises(ValueError, match="cambiado"):
        web_entries.delete_entry(entry, "obsolete")
    outside = tmp_path / "outside.ogg"
    outside.write_bytes(b"keep")
    other = db.guardar_entrada("Ana", audio_path=str(outside))
    with pytest.raises(ValueError):
        web_entries.delete_entry(other, db.obtener_entrada(other)["content_hash"])
    assert outside.exists() and db.obtener_entrada(other)


def test_failed_audio_cleanup_is_durable_and_retried(db, tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "AUDIO_DIR", tmp_path)
    audio = tmp_path / "one.ogg"
    audio.write_bytes(b"test")
    entry = db.guardar_entrada("Ana", audio_path=str(audio))
    original_unlink = Path.unlink
    def deny_audio(path, *args, **kwargs):
        if path == audio:
            raise PermissionError("locked")
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as restricted:
        restricted.setattr(Path, "unlink", deny_audio)
        assert not web_entries.delete_entry(entry, db.obtener_entrada(entry)["content_hash"])
    assert db.obtener_entrada(entry) is None and audio.exists()
    assert maintenance.limpiar_audios_borrados() == 0
    assert not audio.exists()


def test_calendar_creates_in_selected_day_and_delete_requires_confirmation(db):
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py", default_timeout=20)
    app.session_state["diary_day"] = DAY
    app.session_state["calendar_year"] = DAY.year
    app.session_state["calendar_month"] = DAY.month
    app.run()
    app.button(key="add_entry").click().run()
    next(t for t in app.text_area if t.label == "Escribe tu entrada").input("Vi a Ana")
    next(b for b in app.button if b.label == "Guardar entrada").click().run()
    assert not app.exception
    rows = web_data.entries(DAY, DAY)
    assert len(rows) == 1 and rows[0]["transcription"] == "Vi a Ana"
    entry = rows[0]["id"]
    app.button(key="open_delete_" + entry).click().run()
    assert db.obtener_entrada(entry)
    next(b for b in app.button if b.label == "Cancelar borrado").click().run()
    assert db.obtener_entrada(entry)
    app.button(key="open_delete_" + entry).click().run()
    next(b for b in app.button if b.label == "Sí, borrar entrada").click().run()
    assert not app.exception and db.obtener_entrada(entry) is None
