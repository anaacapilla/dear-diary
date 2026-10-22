import asyncio
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import Update
from telegram.ext import ExtBot
from telegram.error import BadRequest, NetworkError

from src import bot, database
from src.entry_ui import ProgressMessage, brief_card, entry_keyboard
from src.processing_queue import ProcessingQueue


@pytest.fixture(autouse=True)
def fresh_lock(monkeypatch):
    monkeypatch.setattr(bot, "_processing_lock", asyncio.Lock())


def context():
    tasks = []
    def create_task(coroutine):
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        return task
    return NS(
        bot=NS(id=999, edit_message_text=AsyncMock(),
               send_message=AsyncMock(return_value=NS(message_id=900)), get_file=AsyncMock()),
        application=NS(bot_data={"processing_queue": ProcessingQueue()}, create_task=create_task),
        user_data={}, args=[], tasks=tasks,
    )


def save(db, analysis, text="Texto original", source_id=1, status="completed", audio_path=None):
    entry_id = db.guardar_entrada(
        text, telegram_chat_id=12345, telegram_message_id=source_id, telegram_file_id="FILE",
        recorded_at="2026-10-22T12:00:00+02:00", status=status, audio_path=audio_path,
        summary=analysis.summary, sentiment=analysis.sentiment,
        entities=[("person", p) for p in analysis.people] + [("place", p) for p in analysis.places],
        emotions=analysis.emotions, events=analysis.events,
    )
    db.guardar_mensaje_progreso(entry_id, 500 + source_id)
    return entry_id


def callback(entry_id, action, *, message_id=501, user_id=12345, chat_type="private"):
    message = NS(message_id=message_id, chat_id=12345,
                 reply_text=AsyncMock(return_value=NS(message_id=700)))
    return NS(
        callback_query=NS(data=f"dd:{action}:{entry_id}", message=message, answer=AsyncMock()),
        effective_user=NS(id=user_id), effective_chat=NS(id=12345, type=chat_type),
        effective_message=message, message=None,
    )


def test_pipeline_edits_one_card_and_stores_its_id_and_metrics(db, tmp_path, monkeypatch, analysis):
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    monkeypatch.setattr("src.maintenance.AUDIO_DIR", tmp_path)
    monkeypatch.setattr(bot, "transcribir", Mock(return_value="Transcripción original"))
    monkeypatch.setattr(bot, "DiaryExtractor", lambda: NS(extract=Mock(return_value=analysis)))

    async def scenario():
        ctx = context()
        message = NS(
            voice=NS(file_id="FILE", file_size=5, duration=1), audio=None,
            chat_id=12345, message_id=1, date=datetime.now(timezone.utc),
            reply_text=AsyncMock(return_value=NS(message_id=501)),
        )
        update = NS(message=message, effective_user=NS(id=12345),
                    effective_chat=NS(id=12345, type="private"))

        async def download(path):
            assert message.reply_text.await_count == 1
            path.write_bytes(b"audio")
        ctx.bot.get_file.return_value = NS(file_size=5, download_to_drive=download)
        queue = ctx.application.bot_data["processing_queue"]
        queue.start()
        try:
            await bot.recibir_audio(update, ctx)
            await asyncio.wait_for(queue.join(), 4)
            message.reply_text.assert_awaited_once()
            ctx.bot.send_message.assert_not_awaited()
            edits = ctx.bot.edit_message_text.await_args_list
            assert len(edits) >= 4
            assert {c.kwargs["message_id"] for c in edits} == {501}
            assert "Entrada guardada" in edits[-1].kwargs["text"]
            assert len(edits[-1].kwargs["text"]) < 700
            assert "ID:" not in edits[-1].kwargs["text"]
            labels = [b.text for row in edits[-1].kwargs["reply_markup"].inline_keyboard for b in row]
            assert labels == []
            entry = db.obtener_entrada_telegram(12345, 1)
            assert entry["telegram_progress_message_id"] == 501
            assert entry["original_transcription"] == "Transcripción original"
            assert entry["transcription_seconds"] >= 0
            assert entry["analysis_seconds"] >= 0
        finally:
            await queue.stop()
    asyncio.run(scenario())


def test_recovered_entry_reuses_persisted_card(db, monkeypatch, analysis):
    entry_id = save(db, analysis, status="transcribed")
    monkeypatch.setattr(bot, "DiaryExtractor", lambda: NS(extract=Mock(return_value=analysis)))
    async def scenario():
        ctx = context()
        await bot.trabajo_entrada(db.obtener_entrada(entry_id), ctx.bot)()
        ctx.bot.send_message.assert_not_awaited()
        assert all(c.kwargs["message_id"] == 501 for c in ctx.bot.edit_message_text.await_args_list)
        assert db.obtener_entrada(entry_id)["status"] == "completed"
    asyncio.run(scenario())


def test_transient_edit_failure_does_not_create_duplicate(db, analysis, caplog):
    entry_id = save(db, analysis)
    async def scenario():
        ctx = context()
        ctx.bot.edit_message_text.side_effect = NetworkError("PRIVATE TEXT")
        progress = ProgressMessage.from_entry(db.obtener_entrada(entry_id), ctx.bot)
        assert not await progress.show("Analizando")
        ctx.bot.send_message.assert_not_awaited()
        assert progress.message_id == 501
        assert "PRIVATE TEXT" not in caplog.text
    asyncio.run(scenario())


def test_missing_card_gets_one_replacement_and_new_id(db, analysis):
    entry_id = save(db, analysis)
    async def scenario():
        ctx = context()
        ctx.bot.edit_message_text.side_effect = [BadRequest("Message to edit not found"), None]
        progress = ProgressMessage.from_entry(db.obtener_entrada(entry_id), ctx.bot)
        assert await progress.show("Analizando")
        assert await progress.show("Terminada")
        ctx.bot.send_message.assert_awaited_once()
        assert progress.message_id == 900
        assert db.obtener_entrada(entry_id)["telegram_progress_message_id"] == 900
        assert ctx.bot.edit_message_text.await_args.kwargs["message_id"] == 900
    asyncio.run(scenario())


def test_unchanged_card_is_not_an_error(db, analysis):
    entry_id = save(db, analysis)
    async def scenario():
        ctx = context()
        ctx.bot.edit_message_text.side_effect = BadRequest("Message is not modified")
        assert await ProgressMessage.from_entry(db.obtener_entrada(entry_id), ctx.bot).show("Igual")
        ctx.bot.send_message.assert_not_awaited()
    asyncio.run(scenario())


def test_long_operation_updates_same_card_and_cancels_notice(db, analysis):
    entry_id = save(db, analysis)
    async def scenario():
        ctx, release, noticed = context(), asyncio.Event(), asyncio.Event()
        async def edit(**kwargs):
            if "Sigo trabajando" in kwargs["text"]:
                noticed.set()
        ctx.bot.edit_message_text.side_effect = edit
        progress = ProgressMessage.from_entry(db.obtener_entrada(entry_id), ctx.bot)
        task = asyncio.create_task(progress.wait(release.wait(), "Analizando", notice_after=0))
        try:
            await asyncio.wait_for(noticed.wait(), 2)
            release.set()
            result, seconds = await task
            assert result is True and seconds >= 0
            assert {c.kwargs["message_id"] for c in ctx.bot.edit_message_text.await_args_list} == {501}
            assert ctx.bot.edit_message_text.await_count == 2
        finally:
            release.set()
            await task
    asyncio.run(scenario())


@pytest.mark.parametrize("user_id,chat_type,message_id", [
    (999, "private", 501), (12345, "group", 501), (12345, "private", 999),
])
def test_buttons_cannot_read_or_mutate_other_context(db, analysis, user_id, chat_type, message_id):
    entry_id = save(db, analysis)
    async def scenario():
        ctx = context()
        update = callback(entry_id, "delete", user_id=user_id, chat_type=chat_type, message_id=message_id)
        await bot.entry_callback(update, ctx)
        ctx.bot.edit_message_text.assert_not_awaited()
        assert ctx.application.bot_data.get("delete_confirmations", {}) == {}
        assert db.obtener_entrada(entry_id) is not None
    asyncio.run(scenario())


def test_failure_button_retries_in_original_progress_card(db, analysis, monkeypatch):
    entry_id = save(db, analysis, status="failed")
    monkeypatch.setattr(bot, "DiaryExtractor", lambda: NS(extract=Mock(return_value=analysis)))
    async def scenario():
        ctx = context()
        await bot.entry_callback(callback(entry_id, "retry"), ctx)
        queue = ctx.application.bot_data["processing_queue"]
        assert queue.pending_count == 1
        queue.start()
        try:
            await asyncio.wait_for(queue.join(), 3)
            assert db.obtener_entrada(entry_id)["status"] == "completed"
            ctx.bot.send_message.assert_not_awaited()
            assert {c.kwargs["message_id"] for c in ctx.bot.edit_message_text.await_args_list} == {501}
        finally:
            await queue.stop()
    asyncio.run(scenario())


def test_buttons_fit_telegram_callback_limit(db, analysis):
    entry = db.obtener_detalles(save(db, analysis, status="failed"))
    for row in entry_keyboard(entry).inline_keyboard:
        for b in row:
            assert len(b.callback_data.encode()) <= 64
    assert len(brief_card(entry)) < 700


def test_migration_from_v2_preserves_text_and_adds_ui_fields(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "v2.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE diary_entries (
        id TEXT PRIMARY KEY, hash TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
        received_at TEXT, recorded_at TEXT, audio_path TEXT, transcription TEXT NOT NULL,
        summary TEXT, sentiment TEXT, events TEXT, transcription_model TEXT,
        llm_model TEXT, llm_prompt_version TEXT)""")
    conn.execute("""INSERT INTO diary_entries(id,hash,created_at,transcription,summary)
                    VALUES('old','hash','2026-10-22T12:00:00+02:00','Texto conservado','Resumen conservado')""")
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()
    monkeypatch.setattr(database, "DB_PATH", path)
    database.inicializar_db()
    row = database.obtener_entrada("old")
    assert row["transcription"] == row["original_transcription"] == "Texto conservado"
    assert row["summary"] == "Resumen conservado"
    assert row["telegram_progress_message_id"] is None
    assert database.obtener_solicitud_edicion() is None
    assert len(list((tmp_path / "backups").glob("migration-*.sqlite3"))) == 1
    with database.conectar() as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        assert c.execute("PRAGMA user_version").fetchone()[0] == 3


def test_deleting_entry_cascades_to_edit_request(db, analysis):
    entry_id = save(db, analysis)
    db.guardar_solicitud_edicion(entry_id, 700, hashlib.sha256(b"Texto original").hexdigest(), 0, 501)
    db.borrar_entrada(entry_id)
    assert db.obtener_solicitud_edicion() is None


def test_successful_correction_clears_request_atomically(db, analysis):
    entry_id = save(db, analysis)
    expected = hashlib.sha256(b"Texto original").hexdigest()
    db.guardar_solicitud_edicion(entry_id, 700, expected, 0, 501)
    db.corregir_transcripcion(entry_id, "Corregido", expected)
    assert db.obtener_solicitud_edicion() is None


def test_processing_explicitly_removes_old_action_buttons(db, analysis):
    entry_id = save(db, analysis)
    async def scenario():
        ctx = context()
        progress = ProgressMessage.from_entry(db.obtener_entrada(entry_id), ctx.bot)
        await progress.show("En cola")
        assert ctx.bot.edit_message_text.await_args.kwargs["reply_markup"].to_dict() == {"inline_keyboard": []}
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["retry", "edit", "delete", "delok.12345678", "tr.0", "det.0"])
def test_real_callback_dispatch_only_allows_retry(db, analysis, monkeypatch, action):
    entry_id = save(db, analysis, status="failed")
    answer, edit, send = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(ExtBot, "answer_callback_query", answer)
    monkeypatch.setattr(ExtBot, "edit_message_text", edit)
    monkeypatch.setattr(ExtBot, "send_message", send)
    async def scenario():
        app = bot.crear_aplicacion()
        app._initialized = True
        queue = ProcessingQueue()
        app.bot_data["processing_queue"] = queue
        update = Update.de_json({
            "update_id": 100,
            "callback_query": {
                "id": "press", "chat_instance": "chat",
                "from": {"id": 12345, "is_bot": False, "first_name": "Test"},
                "message": {"message_id": 501, "date": 1792663200,
                            "chat": {"id": 12345, "type": "private"}, "text": "Card"},
                "data": f"dd:{action}:{entry_id}",
            },
        }, app.bot)
        await app.process_update(update)
        answer.assert_awaited_once()
        edit.assert_awaited_once()
        send.assert_not_awaited()
        entry = db.obtener_entrada(entry_id)
        assert entry is not None
        assert entry["transcription"] == "Texto original"
        if action == "retry":
            assert queue.pending_count == 1
            assert entry["status"] == "pending"
            await app.process_update(update)
            assert queue.pending_count == 1
        else:
            assert queue.pending_count == 0
            assert entry["status"] == "failed"
            markup = edit.await_args.kwargs["reply_markup"]
            assert [b.text for row in markup.inline_keyboard for b in row] == ["Reintentar"]
            assert db.obtener_solicitud_edicion() is None
        await queue.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["completed", "pending", "empty", "transcribed"])
def test_only_failed_entries_have_buttons(status):
    entry = {"id": "id", "status": status, "transcription": "text"}
    assert entry_keyboard(entry) is None
    text = brief_card(entry)
    assert "Corregir" not in text and "Emociones" not in text
