"""Private Telegram entry cards: the only action is retrying a failed entry."""
import asyncio
import logging
import uuid

from telegram.error import TelegramError

from src import database
from src.config import TELEGRAM_USER_ID
from src.entry_ui import ProgressMessage, brief_card, entry_keyboard

logger = logging.getLogger(__name__)


def authorized(update):
    user, chat = update.effective_user, update.effective_chat
    message = getattr(update, "effective_message", None) or getattr(update, "message", None)
    return bool(user and chat and message and user.id == TELEGRAM_USER_ID
                and chat.type == "private" and chat.id == TELEGRAM_USER_ID)


async def answer(query, text=None, *, alert=False):
    try:
        await query.answer(text=text, show_alert=alert)
    except TelegramError as error:
        logger.warning("No se pudo responder al botón: %s", type(error).__name__)


async def show_entry(entry_id, context, origin=None):
    entry = await asyncio.to_thread(database.obtener_detalles, entry_id)
    if entry is not None:
        progress = ProgressMessage.from_entry(entry, context.bot, origin)
        await progress.show(brief_card(entry), entry_keyboard(entry))
    return entry


async def handle_callback(update, context, *, enqueue):
    query = update.callback_query
    if not authorized(update) or query is None:
        if query:
            await answer(query, "Esta acción no está disponible.", alert=True)
        return
    try:
        prefix, action, entry_id = query.data.split(":", 2)
        if prefix != "dd" or str(uuid.UUID(entry_id)) != entry_id:
            raise ValueError
    except (ValueError, AttributeError):
        await answer(query, "Botón no válido.", alert=True)
        return
    entry = await asyncio.to_thread(database.obtener_detalles, entry_id)
    if entry is None:
        await answer(query, "Esta entrada ya no existe.", alert=True)
        return
    web_entry = entry.get("source") == "web" and entry.get("telegram_chat_id") is None
    if ((entry.get("telegram_chat_id") != TELEGRAM_USER_ID and not web_entry)
            or entry.get("telegram_progress_message_id") != query.message.message_id):
        await answer(query, "Esta vista ha caducado. Usa el último mensaje de la entrada.", alert=True)
        return
    queue = context.application.bot_data["processing_queue"]
    if queue.contains(entry_id):
        await answer(query, "Esta entrada ya está en cola o se está procesando.")
        return
    if action != "retry":
        await answer(query, "Consulta y gestiona tus entradas desde la web.")
        await show_entry(entry_id, context)
        return
    if entry["status"] != "failed":
        await answer(query, "Esta entrada no necesita reintentarse.")
        await show_entry(entry_id, context)
        return
    await answer(query)
    await enqueue(entry, context, query.message, retry=True)
