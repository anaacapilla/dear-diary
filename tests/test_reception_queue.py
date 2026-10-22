import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.ext import CommandHandler

from src import bot
from src.processing_queue import ProcessingQueue


@pytest.fixture(autouse=True)
def fresh_operation_lock(monkeypatch):
    # Production has one event loop; each synchronous test here uses asyncio.run.
    monkeypatch.setattr(bot, "_processing_lock", asyncio.Lock())


def make_update(message_id=1):
    return NS(
        effective_user=NS(id=12345), effective_chat=NS(id=12345, type="private"),
        message=NS(
            voice=NS(file_id=f"FILE-{message_id}", file_size=20, duration=2),
            audio=None, chat_id=12345, message_id=message_id,
            date=datetime.now(timezone.utc), reply_text=AsyncMock(return_value=NS(message_id=1000 + message_id)),
        ),
    )


def make_context(queue):
    telegram_bot = NS(send_message=AsyncMock(return_value=NS(message_id=2000)),
                      edit_message_text=AsyncMock(), get_file=AsyncMock())
    return NS(application=NS(bot_data={"processing_queue": queue}),
              bot=telegram_bot, args=[])


def save_entry(db, message_id, **kwargs):
    return db.guardar_entrada(
        "", telegram_chat_id=12345, telegram_message_id=message_id,
        telegram_file_id=f"FILE-{message_id}", status="pending", **kwargs,
    )


def test_receives_three_audios_and_start_while_worker_is_busy(db, tmp_path, monkeypatch, analysis):
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    async def scenario():
        queue = ProcessingQueue()
        context = make_context(queue)
        release, started = asyncio.Event(), asyncio.Event()
        updates = [make_update(i) for i in (1, 2, 3)]
        order, active, peak = [], 0, 0

        async def process(entry, telegram_bot, target):
            nonlocal active, peak
            message_id = entry["telegram_message_id"]
            assert updates[message_id - 1].message.reply_text.await_count == 1
            active += 1
            peak = max(peak, active)
            order.append(message_id)
            started.set()
            await release.wait()
            await asyncio.to_thread(db.completar_entrada, entry["id"], analysis, "test", "5")
            active -= 1

        monkeypatch.setattr(bot, "procesar_entrada", process)
        queue.start()
        try:
            await asyncio.wait_for(bot.recibir_audio(updates[0], context), 3)
            await asyncio.wait_for(started.wait(), 3)
            await asyncio.wait_for(bot.recibir_audio(updates[1], context), 3)
            await asyncio.wait_for(bot.recibir_audio(updates[2], context), 3)
            help_update = make_update(4)
            await asyncio.wait_for(bot.start(help_update, context), 1)
            assert help_update.message.reply_text.await_count == 1
            assert queue.pending_count == 3
            assert "Hay 1 entrada delante" in updates[1].message.reply_text.await_args.args[0]
            assert "Hay 2 entradas delante" in updates[2].message.reply_text.await_args.args[0]
            assert len(db.entradas_pendientes()) == 3
            release.set()
            await asyncio.wait_for(queue.join(), 5)
            assert order == [1, 2, 3]
            assert peak == 1
            assert queue.pending_count == 0
            assert db.entradas_pendientes() == []
        finally:
            release.set()
            await queue.stop()
    asyncio.run(scenario())


def test_acknowledges_persisted_entry_before_downloading(db, tmp_path, monkeypatch, analysis):
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    monkeypatch.setattr("src.maintenance.AUDIO_DIR", tmp_path)
    monkeypatch.setattr(bot, "transcribir", Mock(return_value="Texto"))
    monkeypatch.setattr(bot, "DiaryExtractor", lambda: NS(extract=Mock(return_value=analysis)))

    async def scenario():
        queue = ProcessingQueue()
        context, update = make_context(queue), make_update()
        acknowledged = asyncio.Event()

        async def receipt(*args, **kwargs):
            assert db.obtener_entrada_telegram(12345, 1)["status"] == "pending"
            acknowledged.set()
            return NS(message_id=1001)

        async def download(path):
            assert acknowledged.is_set()
            path.write_bytes(b"audio")

        async def get_file(file_id):
            assert acknowledged.is_set()
            return NS(file_size=5, download_to_drive=download)

        update.message.reply_text.side_effect = receipt
        context.bot.get_file.side_effect = get_file
        queue.start()
        try:
            await bot.recibir_audio(update, context)
            await asyncio.wait_for(queue.join(), 5)
            assert db.obtener_entrada_telegram(12345, 1)["status"] == "completed"
            context.bot.get_file.assert_awaited_once()
            update.message.reply_text.assert_awaited_once()
            receipt_args = update.message.reply_text.await_args.kwargs
            assert receipt_args["reply_parameters"].message_id == 1
            assert receipt_args["reply_parameters"].allow_sending_without_reply is True
            assert context.bot.edit_message_text.await_count >= 3
            assert all(c.kwargs["message_id"] == 1001
                       for c in context.bot.edit_message_text.await_args_list)
        finally:
            await queue.stop()
    asyncio.run(scenario())


def test_duplicate_audio_and_retry_do_not_enqueue_active_entry_twice(db, tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    async def scenario():
        queue, started, release = ProcessingQueue(), asyncio.Event(), asyncio.Event()
        context, update = make_context(queue), make_update()
        calls = []

        async def process(entry, *args):
            calls.append(entry["id"])
            await asyncio.to_thread(db.guardar_transcripcion, entry["id"], "Texto", "base")
            started.set()
            await release.wait()

        monkeypatch.setattr(bot, "procesar_entrada", process)
        queue.start()
        try:
            await bot.recibir_audio(update, context)
            await asyncio.wait_for(started.wait(), 3)
            await bot.recibir_audio(update, context)
            entry = db.obtener_entrada_telegram(12345, 1)
            context.args = [entry["id"]]
            await bot.encolar_entrada(db.obtener_entrada(entry if isinstance(entry, str) else entry["id"]), context, update.message, retry=True)
            assert queue.pending_count == 1
            assert len(calls) == 1
            assert db.obtener_entrada(entry["id"])["status"] == "transcribed"
            assert "ya está en cola" in update.message.reply_text.await_args.args[0]
        finally:
            release.set()
            await queue.stop()
    asyncio.run(scenario())


def test_recovery_only_resumes_interrupted_authorized_entries(db, monkeypatch):
    pending = save_entry(db, 1)
    transcribed = save_entry(db, 2)
    db.guardar_transcripcion(transcribed, "Texto guardado", "base")
    failed = save_entry(db, 3)
    db.marcar_error(failed, "analysis")
    db.guardar_entrada("", status="pending", telegram_chat_id=999,
                       telegram_message_id=1, telegram_file_id="OTHER")
    db.guardar_entrada("", status="pending")  # no Telegram destination
    process = AsyncMock()
    monkeypatch.setattr(bot, "procesar_entrada", process)

    async def scenario():
        queue = ProcessingQueue()
        context = make_context(queue)
        context.application.bot = context.bot
        assert await bot.recuperar_cola(context.application) == 2
        assert await bot.recuperar_cola(context.application) == 0
        queue.start()
        try:
            await asyncio.wait_for(queue.join(), 3)
            assert [c.args[0]["id"] for c in process.await_args_list] == [pending, transcribed]
            assert db.obtener_entrada(failed)["status"] == "failed"
        finally:
            await queue.stop()
    asyncio.run(scenario())


def test_shutdown_finishes_active_entry_and_recovers_waiting_one(db, monkeypatch, analysis):
    first, second = save_entry(db, 1), save_entry(db, 2)
    async def scenario():
        queue, started, release = ProcessingQueue(), asyncio.Event(), asyncio.Event()
        context = make_context(queue)
        order = []

        async def process(entry, *args):
            order.append(entry["id"])
            if entry["id"] == first:
                started.set()
                await release.wait()
            await asyncio.to_thread(db.completar_entrada, entry["id"], analysis, "test", "5")

        monkeypatch.setattr(bot, "procesar_entrada", process)
        queue.enqueue(first, bot.trabajo_entrada(db.obtener_entrada(first), context.bot))
        queue.enqueue(second, bot.trabajo_entrada(db.obtener_entrada(second), context.bot))
        queue.start()
        stopping = None
        try:
            await asyncio.wait_for(started.wait(), 3)
            stopping = asyncio.create_task(bot.post_stop(context.application))
            await asyncio.sleep(0)
            assert not stopping.done()
            assert queue.pending_count == 1
            release.set()
            await asyncio.wait_for(stopping, 3)
            assert order == [first]
            assert db.obtener_entrada(second)["status"] == "pending"
            await bot.post_shutdown(context.application)  # idempotent

            next_queue = ProcessingQueue()
            context.application.bot_data["processing_queue"] = next_queue
            context.application.bot = context.bot
            assert await bot.recuperar_cola(context.application) == 1
            next_queue.start()
            try:
                await asyncio.wait_for(next_queue.join(), 3)
                assert order == [first, second]
            finally:
                await next_queue.stop()
        finally:
            release.set()
            await queue.stop()
            if stopping:
                await stopping
    asyncio.run(scenario())


def test_worker_survives_unexpected_error_and_does_not_log_content(caplog):
    async def scenario():
        queue = ProcessingQueue()
        success = AsyncMock()

        async def fail():
            raise ValueError("PRIVATE-DIARY")

        queue.enqueue("one", fail)
        queue.enqueue("two", success)
        queue.start()
        try:
            await asyncio.wait_for(queue.join(), 2)
            success.assert_awaited_once()
            assert queue.pending_count == 0
            assert "PRIVATE-DIARY" not in caplog.text
        finally:
            await asyncio.wait_for(queue.stop(), 2)
    asyncio.run(scenario())


def test_deleted_waiting_entry_is_skipped(db, monkeypatch):
    entry = save_entry(db, 1)
    process = AsyncMock()
    monkeypatch.setattr(bot, "procesar_entrada", process)
    async def scenario():
        queue = ProcessingQueue()
        queue.enqueue(entry, bot.trabajo_entrada(db.obtener_entrada(entry), None))
        db.borrar_entrada(entry)
        queue.start()
        try:
            await asyncio.wait_for(queue.join(), 2)
            process.assert_not_awaited()
        finally:
            await queue.stop()
    asyncio.run(scenario())


def test_failed_receipt_still_queues_entry(db, tmp_path, monkeypatch):
    from telegram.error import NetworkError
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    process = AsyncMock()
    monkeypatch.setattr(bot, "procesar_entrada", process)
    async def scenario():
        queue, update = ProcessingQueue(), make_update()
        context = make_context(queue)
        update.message.reply_text.side_effect = NetworkError("private")
        await bot.recibir_audio(update, context)
        assert queue.pending_count == 1
        queue.start()
        try:
            await asyncio.wait_for(queue.join(), 2)
            process.assert_awaited_once()
        finally:
            await queue.stop()
    asyncio.run(scenario())


def test_failed_analysis_does_not_block_the_next_audio(db, monkeypatch, analysis):
    first, second = save_entry(db, 1), save_entry(db, 2)
    db.guardar_transcripcion(first, "Texto uno", "base")
    db.guardar_transcripcion(second, "Texto dos", "base")
    extractor = Mock()
    extractor.extract.side_effect = [RuntimeError("PRIVATE"), analysis]
    monkeypatch.setattr(bot, "DiaryExtractor", lambda: extractor)
    async def scenario():
        queue, telegram_bot = ProcessingQueue(), NS(send_message=AsyncMock(return_value=NS(message_id=2000)),
                                                       edit_message_text=AsyncMock())
        for entry_id in (first, second):
            queue.enqueue(entry_id, bot.trabajo_entrada(db.obtener_entrada(entry_id), telegram_bot))
        queue.start()
        try:
            await asyncio.wait_for(queue.join(), 3)
            assert db.obtener_entrada(first)["status"] == "failed"
            assert db.obtener_entrada(first)["transcription"] == "Texto uno"
            assert db.obtener_entrada(second)["status"] == "completed"
        finally:
            await queue.stop()
    asyncio.run(scenario())


def test_retry_marks_failed_entry_pending_and_is_persisted(db):
    entry = save_entry(db, 1)
    db.guardar_transcripcion(entry, "Texto guardado", "base")
    db.marcar_error(entry, "analysis")
    async def scenario():
        queue = ProcessingQueue()
        context, update = make_context(queue), make_update()
        context.args = [entry]
        await bot.encolar_entrada(db.obtener_entrada(entry if isinstance(entry, str) else entry["id"]), context, update.message, retry=True)
        assert db.obtener_entrada(entry)["status"] == "pending"
        assert db.obtener_entrada(entry)["transcription"] == "Texto guardado"
        assert queue.pending_count == 1
        await queue.stop()  # intentionally unstarted: recovery will use SQLite
        assert db.obtener_entrada(entry)["status"] == "pending"
    asyncio.run(scenario())


def test_slow_commands_do_not_block_reception():
    app = bot.crear_aplicacion()
    handlers = {command: handler for handler in app.handlers[0]
                if isinstance(handler, CommandHandler) for command in handler.commands}
    assert set(handlers) == {"start"}
    assert app.post_stop is bot.post_stop
    assert app.concurrent_updates == 1


def test_oversized_and_unauthorized_audio_are_not_queued(db, tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    async def scenario():
        queue, context = ProcessingQueue(), None
        context = make_context(queue)
        large = make_update(1)
        large.message.voice.file_size = bot.MAX_AUDIO_BYTES + 1
        await bot.recibir_audio(large, context)
        other = make_update(2)
        other.effective_user.id = 999
        await bot.recibir_audio(other, context)
        assert queue.pending_count == 0
        assert db.entradas_pendientes() == []
        await queue.stop()
    asyncio.run(scenario())


def test_redelivered_failed_message_requires_explicit_retry(db):
    entry = save_entry(db, 1)
    db.marcar_error(entry, "analysis")
    async def scenario():
        queue = ProcessingQueue()
        context, update = make_context(queue), make_update()
        await bot.recibir_audio(update, context)
        assert queue.pending_count == 0
        assert db.obtener_entrada(entry)["status"] == "failed"
        markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        assert any(b.callback_data == f"dd:retry:{entry}" for row in markup.inline_keyboard for b in row)
        await queue.stop()
    asyncio.run(scenario())
