import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from telegram.error import NetworkError

from src import bot


def update(chat_type="private", user_id=12345, message_id=10):
    audio = NS(file_id="FILE", file_size=100, duration=3, file_name="audio.mp3")
    message = NS(
        reply_text=AsyncMock(), voice=None, audio=audio,
        chat_id=12345, message_id=message_id, date=datetime.now(timezone.utc),
    )
    return NS(effective_user=NS(id=user_id),
              effective_chat=NS(type=chat_type, id=12345), message=message)


def test_authorization_is_private_only():
    assert bot.usuario_autorizado(update())
    assert not bot.usuario_autorizado(update("group"))
    assert not bot.usuario_autorizado(update(user_id=999))


def test_help_does_not_depend_on_real_user_id():
    u = update()
    asyncio.run(bot.help_command(u, None))
    assert "/report YYYY-MM-DD" in u.message.reply_text.await_args.args[0]


def test_long_emoji_messages_split_without_loss():
    text = "Ana 😀" * 3000
    chunks = list(bot.dividir_mensaje(text))
    assert "".join(chunks) == text
    assert all(len(c.encode("utf-16-le")) // 2 <= 3500 for c in chunks)


def test_report_includes_long_lists_and_is_sent_in_safe_chunks(analysis):
    analysis.people = ["Persona" * 100] * 20
    text = bot.formatear_informe("", analysis, 1, 2)
    message = NS(reply_text=AsyncMock())
    asyncio.run(bot.enviar_texto(message, text))
    assert message.reply_text.await_count > 1
    assert all(len(c.args[0].encode("utf-16-le")) // 2 <= 3500
               for c in message.reply_text.await_args_list)


def test_failed_notification_does_not_lose_processing(db, tmp_path, monkeypatch, analysis):
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    monkeypatch.setattr("src.maintenance.AUDIO_DIR", tmp_path)
    monkeypatch.setattr(bot, "transcribir", Mock(return_value="Texto privado"))
    extractor = Mock()
    extractor.extract.return_value = analysis
    path = tmp_path / "audio.audio"
    path.write_bytes(b"test")
    entry_id = db.guardar_entrada("", audio_path=str(path), status="pending")
    message = NS(reply_text=AsyncMock(side_effect=NetworkError("private")))
    asyncio.run(bot.procesar_entrada(db.obtener_entrada(entry_id), None, message, extractor))
    assert db.obtener_entrada(entry_id)["status"] == "completed"


def test_failed_llm_preserves_text_retry_skips_whisper(db, tmp_path, monkeypatch, analysis, caplog):
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    monkeypatch.setattr("src.maintenance.AUDIO_DIR", tmp_path)
    path = tmp_path / "test.audio"
    path.write_bytes(b"test")
    transcribe = Mock(return_value="SECRETO")
    monkeypatch.setattr(bot, "transcribir", transcribe)
    extractor = Mock()
    extractor.extract.side_effect = ValueError("SECRETO")
    entry_id = db.guardar_entrada("", status="pending", audio_path=str(path))
    message = NS(reply_text=AsyncMock())
    asyncio.run(bot.procesar_entrada(db.obtener_entrada(entry_id), None, message, extractor))
    assert db.obtener_entrada(entry_id)["transcription"] == "SECRETO"
    assert db.obtener_entrada(entry_id)["status"] == "failed"
    extractor.extract.side_effect = None
    extractor.extract.return_value = analysis
    asyncio.run(bot.procesar_entrada(db.obtener_entrada(entry_id), None, message, extractor))
    assert transcribe.call_count == 1
    assert db.obtener_entrada(entry_id)["status"] == "completed"
    assert "SECRETO" not in caplog.text


def test_same_filename_uses_distinct_paths(db, tmp_path, monkeypatch):
    from src.processing_queue import ProcessingQueue
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    context = NS(bot=None, application=NS(bot_data={"processing_queue": ProcessingQueue()}))
    async def run():
        await bot.recibir_audio(update(message_id=1), context)
        await bot.recibir_audio(update(message_id=2), context)
    asyncio.run(run())
    paths = [entry["audio_path"] for entry in db.entradas_pendientes()]
    assert len(paths) == 2 and len(set(paths)) == 2
    assert all("audio.mp3" not in path for path in paths)


def test_strict_date_argument():
    import pytest
    for args in [[], ["20260830"], ["2026-08-30T12:00:00"], ["2026-02-30"], ["2026-08-30", "extra"]]:
        with pytest.raises(ValueError):
            bot.fecha_argumento(args)
    assert bot.fecha_argumento(["2026-08-30"]) == "2026-08-30"
