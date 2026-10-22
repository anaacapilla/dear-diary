"""Rendering and delivery for one editable Telegram message per diary entry."""
import asyncio
import logging
import sqlite3
import time
from contextlib import suppress
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters
from telegram.error import BadRequest, TelegramError

from src import database
from src.config import DIARY_TZ, TELEGRAM_USER_ID

logger = logging.getLogger(__name__)


def button(label, action, entry_id):
    return InlineKeyboardButton(label, callback_data=f"dd:{action}:{entry_id}")


def entry_keyboard(entry):
    if entry["status"] == "failed":
        return InlineKeyboardMarkup([[button("Reintentar", "retry", entry["id"])]])
    return None


def brief_card(entry):
    status = entry["status"]
    if status == "failed":
        if entry.get("transcription"):
            return "No he podido terminar el análisis. Tu transcripción está guardada. Puedes reintentar."
        return "No he podido procesar el audio. Pulsa Reintentar."
    if status == "empty":
        return "No he detectado voz en el audio. Envía otro audio."
    if status != "completed":
        return "⏳ Entrada en cola. Te avisaré cuando termine."
    stamp = entry.get("recorded_at") or entry.get("received_at") or entry.get("created_at")
    label = ""
    if stamp:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        dt = dt.replace(tzinfo=DIARY_TZ) if dt.tzinfo is None else dt.astimezone(DIARY_TZ)
        label = " · " + dt.strftime("%d/%m · %H:%M")
    summary = (entry.get("summary") or "Sin resumen disponible.").strip()
    if len(summary) > 450:
        summary = summary[:447].rstrip() + "..."
    text = f"✅ Entrada guardada{label}\n\n{summary}"
    return text


class ProgressMessage:
    def __init__(self, bot, entry_id, chat_id, source_id=None, message_id=None, origin=None):
        self.bot, self.entry_id, self.chat_id = bot, entry_id, chat_id
        self.source_id, self.origin = source_id, origin
        self.message_id = message_id if isinstance(message_id, int) else None
        self._lock = asyncio.Lock()

    @classmethod
    def from_entry(cls, entry, bot, origin=None):
        return cls(
            bot, entry["id"],
            entry.get("telegram_chat_id") or getattr(origin, "chat_id", None) or TELEGRAM_USER_ID,
            entry.get("telegram_message_id") or getattr(origin, "message_id", None),
            entry.get("telegram_progress_message_id"), origin,
        )

    async def show(self, text, markup=None):
        async with self._lock:
            if self.message_id is not None and self.bot is not None:
                try:
                    await self.bot.edit_message_text(
                        chat_id=self.chat_id, message_id=self.message_id, text=text,
                        parse_mode=None,
                        # Keep an explicit empty keyboard: PTB otherwise drops empty fields.
                        reply_markup=markup if markup is not None else InlineKeyboardMarkup(
                            [], api_kwargs={"inline_keyboard": []},
                        ),
                    )
                    return True
                except BadRequest as error:
                    reason = str(error).casefold()
                    if "message is not modified" in reason:
                        return True
                    missing = ("message to edit not found", "message can't be edited",
                               "message can not be edited", "message_id_invalid")
                    if not any(item in reason for item in missing):
                        logger.warning("No se pudo editar el progreso: %s", type(error).__name__)
                        return False
                    # Explicitly unavailable message: create one replacement and persist its ID.
                    self.message_id = None
                except TelegramError as error:
                    logger.warning("No se pudo editar el progreso: %s", type(error).__name__)
                    return False
            try:
                kwargs = {"parse_mode": None, "reply_markup": markup}
                if self.source_id is not None:
                    kwargs["reply_parameters"] = ReplyParameters(
                        message_id=self.source_id, allow_sending_without_reply=True,
                    )
                if self.origin is not None:
                    sent = await self.origin.reply_text(text, **kwargs)
                else:
                    sent = await self.bot.send_message(chat_id=self.chat_id, text=text, **kwargs)
            except TelegramError as error:
                logger.warning("No se pudo enviar el progreso: %s", type(error).__name__)
                return False
            message_id = getattr(sent, "message_id", None)
            if isinstance(message_id, int):
                self.message_id = message_id
                try:
                    await asyncio.to_thread(database.guardar_mensaje_progreso, self.entry_id, message_id)
                except sqlite3.Error as error:
                    logger.warning("No se pudo registrar el mensaje de progreso: %s", type(error).__name__)
            return True

    async def wait(self, operation, text, *, notice_after=30):
        await self.show(text)
        async def notice():
            await asyncio.sleep(notice_after)
            await self.show(text + "\nSigo trabajando en tu entrada.")
        task = asyncio.create_task(notice())
        started = time.perf_counter()
        try:
            result = await operation
            return result, time.perf_counter() - started
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
