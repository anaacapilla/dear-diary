import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

import ast
from pathlib import Path

from src import bot, transcriber


def test_import_does_not_load_model():
    assert transcriber.obtener_modelo.cache_info().currsize == 0


def test_polling_preserves_pending_updates():
    source = Path(bot.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "run_polling"]
    assert len(calls) == 1
    assert any(k.arg == "drop_pending_updates" and k.value.value is False
               for k in calls[0].keywords)
    allowed = next(k.value for k in calls[0].keywords if k.arg == "allowed_updates")
    assert set(ast.literal_eval(allowed)) == {"message", "callback_query"}


def test_application_has_lifecycle_and_error_handler():
    app = bot.crear_aplicacion()
    assert app.post_init is bot.post_init
    assert app.post_shutdown is bot.post_shutdown
    assert bot.on_error in app.error_handlers


@pytest.mark.parametrize("recovered", [0, 2])
def test_startup_and_shutdown_notify_once(db, tmp_path, monkeypatch, recovered):
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    monkeypatch.setattr(bot, "mantenimiento", lambda: None)
    monkeypatch.setattr(bot, "recuperar_cola", AsyncMock(return_value=recovered))
    async def scenario():
        app = NS(bot_data={}, bot=NS(delete_my_commands=AsyncMock(), send_message=AsyncMock()))
        await bot.post_init(app)
        try:
            app.bot.delete_my_commands.assert_awaited_once()
            app.bot.send_message.assert_awaited_once()
            text = app.bot.send_message.await_args.kwargs["text"]
            assert "Bot en marcha" in text
            assert app.bot.send_message.await_args.kwargs["chat_id"] == 12345
            if recovered:
                assert "Retomaré 2" in text
            else:
                assert "Retomaré" not in text
        finally:
            await bot.post_stop(app)
        assert app.bot.send_message.await_count == 2
        assert "Bot detenido" in app.bot.send_message.await_args.kwargs["text"]
        await bot.post_shutdown(app)
        assert app.bot.send_message.await_count == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["help", "entries", "report", "delete", "backup", "retry", "cancelar"])
def test_removed_commands_only_offer_audio_guidance(monkeypatch, command):
    from telegram import Update, User
    from telegram.ext import ExtBot
    reply = AsyncMock()
    monkeypatch.setattr(ExtBot, "send_message", reply)
    async def scenario():
        app = bot.crear_aplicacion()
        app._initialized = True
        app.bot._bot_user = User(999, "Diary", True, username="diary_test_bot")
        update = Update.de_json({
            "update_id": 101,
            "message": {"message_id": 10, "date": 1792663200,
                        "from": {"id": 12345, "is_bot": False, "first_name": "Test"},
                        "chat": {"id": 12345, "type": "private"},
                        "text": f"/{command}",
                        "entities": [{"type": "bot_command", "offset": 0, "length": len(command)+1}]},
        }, app.bot)
        await app.process_update(update)
        reply.assert_awaited_once()
        assert reply.await_args.kwargs["text"] == bot.MENSAJE_BIENVENIDA
    asyncio.run(scenario())


def test_shutdown_notice_waits_for_worker():
    async def scenario():
        queue = NS(stop=AsyncMock())
        async def send(**kwargs):
            queue.stop.assert_awaited_once()
        app = NS(bot_data={"processing_queue": queue, "lifecycle_running": True},
                 bot=NS(send_message=AsyncMock(side_effect=send)))
        await bot.post_stop(app)
        app.bot.send_message.assert_awaited_once()
    asyncio.run(scenario())


def test_notification_failure_does_not_interrupt_lifecycle(db, tmp_path, monkeypatch, caplog):
    from telegram.error import NetworkError
    monkeypatch.setattr(bot, "AUDIO_DIR", tmp_path)
    monkeypatch.setattr(bot, "mantenimiento", lambda: None)
    monkeypatch.setattr(bot, "recuperar_cola", AsyncMock(return_value=0))
    async def scenario():
        app = NS(bot_data={}, bot=NS(delete_my_commands=AsyncMock(),
                 send_message=AsyncMock(side_effect=NetworkError("PRIVATE_REQUEST"))))
        await bot.post_init(app)
        assert app.bot_data["lifecycle_running"]
        await bot.post_stop(app)
        await bot.post_shutdown(app)
        assert app.bot.send_message.await_count == 2
        assert not app.bot_data.get("lifecycle_running")
    asyncio.run(scenario())
    assert "PRIVATE_REQUEST" not in caplog.text
