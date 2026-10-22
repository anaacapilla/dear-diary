"""Private Telegram diary. Importing the module never loads models or opens a DB."""
import asyncio
import logging
import uuid
from contextlib import suppress

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from src import database, entry_actions
from src.config import (
    AUDIO_DIR, LOG_LEVEL, MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, OLLAMA_MODEL,
    TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, WHISPER_MODEL, validar_telegram,
)
from src.llm.extractor import DiaryExtractor
from src.llm.prompts import PROMPT_VERSION
from src.maintenance import audio_seguro, mantenimiento
from src.transcriber import transcribir
from src.processing_queue import ProcessingQueue
from src.entry_ui import ProgressMessage, brief_card, entry_keyboard

logger = logging.getLogger(__name__)
_processing_lock = asyncio.Lock()

MENSAJE_BIENVENIDA = "Envíame un audio para añadir una entrada a tu diario."


def dividir_mensaje(text, limit=3500):
    """Telegram counts UTF-16 code units; never split a surrogate pair."""
    chunk, units = [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > limit:
            yield "".join(chunk)
            chunk, units = [], 0
        chunk.append(char)
        units += width
    if chunk:
        yield "".join(chunk)


async def enviar_texto(message, text):
    try:
        for chunk in dividir_mensaje(text):
            await message.reply_text(chunk, parse_mode=None)
        return True
    except TelegramError as error:
        # Telegram error details can include requests or user content.
        logger.warning("No se pudo enviar la respuesta: %s", type(error).__name__)
        return False


def trabajo_entrada(entry, telegram_bot, fallback_message=None, *, progress=None):
    entry_id = entry["id"]
    progress = progress or ProgressMessage.from_entry(entry, telegram_bot, fallback_message)

    async def run():
        async with _processing_lock:
            current = await asyncio.to_thread(database.obtener_entrada, entry_id)
            if current is not None:
                await procesar_entrada(current, telegram_bot, progress)
    return run


async def encolar_entrada(entry, context, message, *, retry=False):
    queue = context.application.bot_data["processing_queue"]
    key = entry["id"]
    if queue.contains(key):
        await enviar_texto(message, "Esta entrada ya está en cola o se está procesando.")
        return False
    if retry:
        await asyncio.to_thread(database.preparar_reintento, key)
    ahead = queue.pending_count
    text = "⏳ Entrada preparada para reintentar." if retry else "🎙️ Audio recibido. En cola."
    if entry.get("transcription_edited_at"):
        text = "✏️ Texto corregido. Preparando el análisis."
    if ahead:
        unit = "entrada" if ahead == 1 else "entradas"
        text += f" Hay {ahead} {unit} delante. Te avisaré cuando termine."
    else:
        text += " Lo procesaré a continuación."
    # Acknowledge before waking the worker. Failed Telegram delivery must not lose the job.
    progress = ProgressMessage.from_entry(entry, context.bot, message)
    await progress.show(text)
    return queue.enqueue(key, trabajo_entrada(entry, context.bot, message, progress=progress))


async def recuperar_cola(application):
    queue = application.bot_data["processing_queue"]
    recovered = 0
    for entry in await asyncio.to_thread(database.entradas_pendientes):
        if (entry["status"] in ("pending", "transcribed")
                and entry["telegram_chat_id"] == TELEGRAM_USER_ID
                and entry["telegram_message_id"] is not None):
            recovered += queue.enqueue(entry["id"], trabajo_entrada(entry, application.bot))
    return recovered


def usuario_autorizado(update: Update):
    return entry_actions.authorized(update)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if usuario_autorizado(update):
        await enviar_texto(update.message, MENSAJE_BIENVENIDA)


async def procesar_entrada(entry, bot, message, extractor=None):
    """Update one progress card; persist successful stages independently of Telegram."""
    entry_id = entry["id"]
    progress = message if isinstance(message, ProgressMessage) else ProgressMessage.from_entry(entry, bot, message)
    stage = "download"
    text = entry["transcription"]
    try:
        if not text:
            await progress.show("📥 Descargando tu audio…")
            ruta = audio_seguro(entry["audio_path"])
            if ruta is None:
                ruta = AUDIO_DIR / f"{entry_id}.audio"
                with database.conectar() as conn:
                    conn.execute("UPDATE diary_entries SET audio_path=? WHERE id=?", (str(ruta), entry_id))
            if not ruta.exists():
                archivo = await bot.get_file(entry["telegram_file_id"])
                if archivo.file_size and archivo.file_size > MAX_AUDIO_BYTES:
                    raise ValueError("Archivo demasiado grande")
                partial = ruta.with_suffix(ruta.suffix + ".part")
                try:
                    await archivo.download_to_drive(partial)
                    if partial.stat().st_size > MAX_AUDIO_BYTES:
                        raise ValueError("Archivo demasiado grande")
                    partial.replace(ruta)
                finally:
                    partial.unlink(missing_ok=True)
            stage = "transcription"
            text, transcription_time = await progress.wait(asyncio.to_thread(transcribir, ruta), "✍️ Transcribiendo tu entrada…")
            await asyncio.to_thread(database.guardar_transcripcion, entry_id, text, WHISPER_MODEL,
                                    seconds=transcription_time)
            if not text:
                empty = {**entry, "status": "empty", "transcription": ""}
                await progress.show(brief_card(empty), entry_keyboard(empty))
                return
        stage = "analysis"
        analysis, llm_time = await progress.wait(
            asyncio.to_thread((extractor or DiaryExtractor()).extract, text),
            "🧠 Preparando el resumen y el análisis…",
        )
        stage = "storage"
        await asyncio.to_thread(database.completar_entrada, entry_id, analysis, OLLAMA_MODEL,
                                PROMPT_VERSION, seconds=llm_time)
    except Exception as error:
        logger.warning("Procesamiento fallido: etapa=%s tipo=%s", stage, type(error).__name__)
        await asyncio.to_thread(database.marcar_error, entry_id, stage)
        failed = {**entry, "status": "failed", "transcription": text}
        await progress.show(brief_card(failed), entry_keyboard(failed))
        return
    completed = {**entry, "status": "completed", "summary": analysis.summary, "emotions": analysis.emotions,
                 "transcription": text}
    await progress.show(brief_card(completed), entry_keyboard(completed))


async def recibir_audio(update, context):
    if not usuario_autorizado(update):
        return
    message = update.message
    audio = message.voice or message.audio
    if not audio:
        return
    duration = audio.duration
    if hasattr(duration, "total_seconds"):
        duration = duration.total_seconds()
    if (audio.file_size or 0) > MAX_AUDIO_BYTES or (duration or 0) > MAX_AUDIO_SECONDS:
        await enviar_texto(message, f"Audio demasiado grande. Máximo {MAX_AUDIO_BYTES // 1000000} MB y {MAX_AUDIO_SECONDS // 60} minutos.")
        return
    existing = await asyncio.to_thread(
        database.obtener_entrada_telegram, message.chat_id, message.message_id,
    )
    if existing and existing["status"] in ("completed", "empty", "failed"):
        await entry_actions.show_entry(existing["id"], context, message)
        return
    if not existing:
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        suffix = ".ogg" if message.voice else ".audio"
        path = AUDIO_DIR / f"{uuid.uuid4()}{suffix}"
        entry_id = await asyncio.to_thread(
            database.guardar_entrada, "", received_at=database.ahora(),
            recorded_at=message.date.isoformat(), audio_path=str(path),
            telegram_chat_id=message.chat_id, telegram_message_id=message.message_id,
            telegram_file_id=audio.file_id, status="pending",
        )
        existing = await asyncio.to_thread(database.obtener_entrada, entry_id)
    await encolar_entrada(existing, context, message)


async def entry_callback(update, context):
    await entry_actions.handle_callback(update, context, enqueue=encolar_entrada)


async def orientar_usuario(update, context):
    if usuario_autorizado(update):
        await enviar_texto(update.message, MENSAJE_BIENVENIDA)


async def maintenance_loop(application):
    while True:
        await asyncio.sleep(3600)
        try:
            async with _processing_lock:
                await asyncio.to_thread(mantenimiento)
        except Exception as error:
            logger.error("Mantenimiento fallido: %s", type(error).__name__)


async def web_edits_loop(application):
    """Pick up persisted corrections submitted by the web in another process."""
    while True:
        await asyncio.sleep(5)
        try:
            await recuperar_cola(application)
        except Exception as error:
            logger.error("Recuperación de correcciones fallida: %s", type(error).__name__)


async def post_init(application):
    await asyncio.to_thread(database.inicializar_db)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(mantenimiento)
    await application.bot.delete_my_commands()
    application.bot_data["processing_queue"] = ProcessingQueue()
    recovered = await recuperar_cola(application)
    application.bot_data["processing_queue"].start()
    application.bot_data["maintenance_task"] = asyncio.create_task(maintenance_loop(application))
    application.bot_data["web_edits_task"] = asyncio.create_task(web_edits_loop(application))
    application.bot_data["lifecycle_running"] = True
    text = "👋 Bot en marcha. Puedes enviarme audios."
    if recovered:
        text += f"\nRetomaré {recovered} entradas que quedaron interrumpidas."
    try:
        await application.bot.send_message(chat_id=TELEGRAM_USER_ID, text=text)
    except TelegramError as error:
        logger.warning("Aviso de inicio no enviado: %s", type(error).__name__)


async def post_stop(application):
    # run_polling calls this before closing the Telegram client.
    # Finish active inference; leave queued entries in SQLite for next startup.
    for name in ("maintenance_task", "web_edits_task"):
        task = application.bot_data.get(name)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    queue = application.bot_data.get("processing_queue")
    if queue:
        await queue.stop()
    if application.bot_data.pop("lifecycle_running", False):
        try:
            await application.bot.send_message(
                chat_id=TELEGRAM_USER_ID,
                text="⏹ Bot detenido. No procesaré audios hasta que vuelva a arrancar.",
            )
        except TelegramError as error:
            logger.warning("Aviso de apagado no enviado: %s", type(error).__name__)


async def post_shutdown(application):
    # Idempotent fallback if startup failed before Application.start().
    await post_stop(application)


async def on_error(update, context):
    logger.error("Error del bot: %s", type(context.error).__name__)
    if isinstance(update, Update) and usuario_autorizado(update):
        await enviar_texto(update.effective_message, "No se pudo completar la operación. Vuelve a intentarlo.")


def crear_aplicacion():
    validar_telegram()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.post_init = post_init
    app.post_stop = post_stop
    app.post_shutdown = post_shutdown
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(entry_callback, pattern=r"^dd:"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.VOICE | filters.AUDIO), recibir_audio))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, orientar_usuario))
    app.add_error_handler(on_error)
    return app


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    )
    for name in ("httpx", "httpcore", "telegram", "ollama"):
        logging.getLogger(name).setLevel(logging.WARNING)
    crear_aplicacion().run_polling(
        drop_pending_updates=False,
        allowed_updates=["message", "callback_query"],
    )
