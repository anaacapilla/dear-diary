"""Private Telegram diary. Importing the module never loads models or opens a DB."""
import asyncio
import logging
import re
import time
import uuid
from contextlib import suppress
from datetime import date
from dataclasses import dataclass
from pathlib import Path

from telegram import BotCommand, ReplyParameters, Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from src import database
from src.config import (
    AUDIO_DIR, LOG_LEVEL, MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, OLLAMA_MODEL,
    TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, WHISPER_MODEL, validar_telegram,
)
from src.llm.extractor import DiaryExtractor
from src.llm.prompts import PROMPT_VERSION
from src.maintenance import audio_seguro, borrar_audio, crear_backup, mantenimiento
from src.transcriber import transcribir
from src.processing_queue import ProcessingQueue

logger = logging.getLogger(__name__)
MAX_TELEGRAM_MESSAGE_CHARS = 800
_processing_lock = asyncio.Lock()

MENSAJE_BIENVENIDA = (
    "¡Hola! Soy tu dear-diary bot. Envíame una nota de voz en este chat privado.\n"
    "Conservo el audio durante siete días y la transcripción y el análisis hasta que los borres.\n"
    "Usa /help para consultar los comandos."
)


def truncar_texto(texto, max_chars=MAX_TELEGRAM_MESSAGE_CHARS):
    texto = (texto or "").strip()
    if len(texto) <= max_chars:
        return texto
    if max_chars <= 3:
        return texto[:max_chars]
    return texto[:max_chars - 3].rstrip() + "..."


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


@dataclass(frozen=True)
class EntryReplyTarget:
    """Keep replies attached to the source audio, including after a restart."""
    bot: object
    chat_id: int
    message_id: int

    async def reply_text(self, text, **kwargs):
        return await self.bot.send_message(
            chat_id=self.chat_id, text=text,
            reply_parameters=ReplyParameters(
                message_id=self.message_id, allow_sending_without_reply=True,
            ),
            **kwargs,
        )


def trabajo_entrada(entry, telegram_bot, fallback_message=None):
    entry_id = entry["id"]
    if entry["telegram_chat_id"] is not None and entry["telegram_message_id"] is not None:
        target = EntryReplyTarget(
            telegram_bot, entry["telegram_chat_id"], entry["telegram_message_id"],
        )
    else:
        target = fallback_message

    async def run():
        # Read again under the operation lock: a waiting entry may have been deleted.
        async with _processing_lock:
            current = await asyncio.to_thread(database.obtener_entrada, entry_id)
            if current is not None:
                await procesar_entrada(current, telegram_bot, target)
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
    text = "Entrada preparada para reintentar." if retry else "🎙️ Audio recibido."
    if ahead:
        unit = "entrada" if ahead == 1 else "entradas"
        text += f" Hay {ahead} {unit} delante. Te avisaré cuando termine."
    else:
        text += " Lo procesaré a continuación."
    # Acknowledge before waking the worker. Failed Telegram delivery must not lose the job.
    await enviar_texto(message, text)
    return queue.enqueue(key, trabajo_entrada(entry, context.bot, message))


async def recuperar_cola(application):
    queue = application.bot_data["processing_queue"]
    recovered = 0
    for entry in await asyncio.to_thread(database.entradas_pendientes):
        if (entry["status"] in ("pending", "transcribed")
                and entry["telegram_chat_id"] == TELEGRAM_USER_ID
                and entry["telegram_message_id"] is not None):
            recovered += queue.enqueue(entry["id"], trabajo_entrada(entry, application.bot))
    return recovered


def formatear_informe(transcription, analysis, transcription_time, llm_time):
    return (
        "📔 Nueva entrada guardada\n\n"
        f"✨ Resumen\n{truncar_texto(analysis.summary)}\n\n"
        f"💭 Sentimiento\n{analysis.sentiment}\n\n"
        f"🎨 Emociones\n{', '.join(analysis.emotions) or 'Ninguna'}\n\n"
        f"📍 Lugares\n{', '.join(analysis.places) or 'Ninguno'}\n\n"
        f"👥 Personas\n{', '.join(analysis.people) or 'Ninguna'}\n\n"
        f"⏱️ Transcripción: {transcription_time:.2f} s\n"
        f"Análisis LLM: {llm_time:.2f} s\n"
        f"Total: {transcription_time + llm_time:.2f} s"
    )


def formatear_reporte_diario(report):
    return (
        f"📅 Reporte del día {report['report_date']}\n"
        f"Entradas analizadas: {report['entries_count']}\n"
        f"Pendientes o fallidas: {report.get('pending_count', 0)}\n\n"
        f"✨ Resumen\n{report['summary']}\n\n"
        f"💭 Sentimiento\n{report['sentiment']}\n\n"
        f"🎨 Emociones\n{', '.join(report['emotions']) or 'Ninguna'}\n\n"
        f"📍 Lugares\n{', '.join(report['places']) or 'Ninguno'}\n\n"
        f"👥 Personas\n{', '.join(report['people']) or 'Ninguna'}"
    )


def usuario_autorizado(update: Update):
    user, chat = update.effective_user, update.effective_chat
    return bool(
        user and chat and user.id == TELEGRAM_USER_ID
        and chat.type == "private" and chat.id == TELEGRAM_USER_ID and update.message
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if usuario_autorizado(update):
        await enviar_texto(update.message, MENSAJE_BIENVENIDA)


async def help_command(update, context):
    if usuario_autorizado(update):
        await enviar_texto(update.message, (
            "Comandos disponibles:\n"
            "/start — Bienvenida\n/help — Ayuda\n"
            "/report YYYY-MM-DD — Síntesis del día\n"
            "/entries YYYY-MM-DD — Identificadores y estados de las entradas\n"
            "/retry [ID] — Reintentar una entrada o hasta diez pendientes\n"
            "/delete ID — Borrar una entrada y su audio\n"
            "/backup — Crear una copia de SQLite en el equipo del bot\n\n"
            "Zona del diario: Europe/Madrid. Audios: siete días. "
            "Las copias de seguridad se conservan siete días y pueden contener entradas borradas."
        ))


def fecha_argumento(args):
    if len(args) != 1 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args[0]):
        raise ValueError("Formato inválido")
    return date.fromisoformat(args[0]).isoformat()


async def report_command(update, context):
    if not usuario_autorizado(update):
        return
    try:
        fecha = fecha_argumento(context.args)
    except ValueError:
        await enviar_texto(update.message, "Uso: /report YYYY-MM-DD")
        return
    await enviar_texto(update.message, "Reporte solicitado. Puedes seguir enviando audios mientras lo preparo.")
    async with _processing_lock:
        try:
            report = await asyncio.to_thread(
                database.obtener_reporte_diario, fecha,
                summarize=DiaryExtractor().summarize_day,
                llm_model=OLLAMA_MODEL, llm_prompt_version=PROMPT_VERSION,
            )
        except Exception as error:
            logger.warning("Síntesis diaria no disponible: %s", type(error).__name__)
            report = await asyncio.to_thread(database.obtener_reporte_diario, fecha)
            await enviar_texto(update.message, "No se pudo generar la síntesis LLM. Muestro los resúmenes guardados.")
    await enviar_texto(update.message, formatear_reporte_diario(report))


async def entries_command(update, context):
    if not usuario_autorizado(update):
        return
    try:
        fecha = fecha_argumento(context.args)
    except ValueError:
        await enviar_texto(update.message, "Uso: /entries YYYY-MM-DD")
        return
    entries = await asyncio.to_thread(database.entradas_del_dia, fecha)
    text = "\n".join(f"{e['id']} — {e['status']}" for e in entries) or "No hay entradas."
    await enviar_texto(update.message, text)


async def procesar_entrada(entry, bot, message, extractor=None):
    """Persist each completed stage. Retrying reuses an existing transcription."""
    entry_id = entry["id"]
    stage = "download"
    transcription_time = 0.0
    try:
        text = entry["transcription"]
        if not text:
            ruta = audio_seguro(entry["audio_path"])
            if ruta is None:
                # A failed transcription may be retried after audio retention expired.
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
            await enviar_texto(message, "✍️ Transcribiendo tu entrada...")
            stage = "transcription"
            started = time.perf_counter()
            text = await asyncio.to_thread(transcribir, ruta)
            transcription_time = time.perf_counter() - started
            await asyncio.to_thread(database.guardar_transcripcion, entry_id, text, WHISPER_MODEL)
            if not text:
                await enviar_texto(message, f"No he detectado voz en el audio. Entrada: {entry_id}")
                return
        stage = "analysis"
        await enviar_texto(message, "🧠 Analizando la entrada...")
        started = time.perf_counter()
        analysis = await asyncio.to_thread((extractor or DiaryExtractor()).extract, text)
        llm_time = time.perf_counter() - started
        stage = "storage"
        await asyncio.to_thread(database.completar_entrada, entry_id, analysis, OLLAMA_MODEL, PROMPT_VERSION)
    except Exception as error:
        logger.warning("Procesamiento fallido: etapa=%s tipo=%s", stage, type(error).__name__)
        await asyncio.to_thread(database.marcar_error, entry_id, stage)
        await enviar_texto(message, (
            f"No se pudo completar la entrada {entry_id} (etapa: {stage}). "
            f"Puedes reintentar con /retry {entry_id}. "
            "Si la transcripción ya terminó, se conserva."
        ))
        return
    await enviar_texto(message, (
        formatear_informe(text, analysis, transcription_time, llm_time)
        + f"\n\nID: {entry_id}"
    ))


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
    if existing and existing["status"] in ("completed", "empty"):
        await enviar_texto(message, f"Este mensaje ya está registrado. ID: {existing['id']}")
        return
    if existing and existing["status"] == "failed":
        await enviar_texto(message, f"Esta entrada no pudo completarse. Usa /retry {existing['id']}.")
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


async def retry_command(update, context):
    if not usuario_autorizado(update):
        return
    if len(context.args) > 1:
        await enviar_texto(update.message, "Uso: /retry [ID]")
        return
    if context.args:
        entry = await asyncio.to_thread(database.obtener_entrada, context.args[0])
        entries = [entry] if entry and entry["status"] != "empty" else []
    else:
        queue = context.application.bot_data["processing_queue"]
        entries = [
            entry for entry in await asyncio.to_thread(database.entradas_pendientes)
            if not queue.contains(entry["id"])
        ][:10]
    if not entries:
        await enviar_texto(update.message, "No hay entradas pendientes para reintentar.")
    for entry in entries:
        await encolar_entrada(entry, context, update.message, retry=True)


async def delete_command(update, context):
    if not usuario_autorizado(update):
        return
    if len(context.args) != 1:
        await enviar_texto(update.message, "Uso: /delete ID")
        return
    async with _processing_lock:
        entry = await asyncio.to_thread(database.obtener_entrada, context.args[0])
        if not entry:
            await enviar_texto(update.message, "No se encontró esa entrada.")
            return
        if entry["audio_path"]:
            await asyncio.to_thread(borrar_audio, entry["audio_path"])
        await asyncio.to_thread(database.borrar_entrada, entry["id"])
    await enviar_texto(update.message, "Entrada y audio borrados. Las copias anteriores caducan en siete días.")


async def backup_command(update, context):
    if usuario_autorizado(update):
        async with _processing_lock:
            await asyncio.to_thread(crear_backup, force=True)
        await enviar_texto(update.message, "Copia de SQLite creada en el equipo del bot.")


async def maintenance_loop(application):
    while True:
        await asyncio.sleep(3600)
        try:
            async with _processing_lock:
                await asyncio.to_thread(mantenimiento)
        except Exception as error:
            logger.error("Mantenimiento fallido: %s", type(error).__name__)


async def post_init(application):
    await asyncio.to_thread(database.inicializar_db)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(mantenimiento)
    await application.bot.set_my_commands([
        BotCommand(name, description) for name, description in [
            ("start", "Bienvenida"), ("help", "Ayuda"), ("report", "Reporte del día"),
            ("entries", "Ver entradas"), ("retry", "Reintentar pendientes"),
            ("delete", "Borrar entrada"), ("backup", "Copia de seguridad local"),
        ]
    ])
    application.bot_data["processing_queue"] = ProcessingQueue()
    recovered = await recuperar_cola(application)
    pending = len(await asyncio.to_thread(database.entradas_pendientes)) - recovered
    recovery_notice = f"\nRetomaré {recovered} entradas que quedaron interrumpidas." if recovered else ""
    try:
        await application.bot.send_message(
            chat_id=TELEGRAM_USER_ID,
            text=MENSAJE_BIENVENIDA + recovery_notice + (f"\nHay {pending} entradas que necesitan /retry." if pending else ""),
        )
    except TelegramError as error:
        logger.warning("Bienvenida no enviada: %s", type(error).__name__)
    application.bot_data["processing_queue"].start()
    application.bot_data["maintenance_task"] = asyncio.create_task(maintenance_loop(application))


async def post_stop(application):
    # run_polling calls this before closing the Telegram client.
    # Finish active inference; leave queued entries in SQLite for next startup.
    task = application.bot_data.get("maintenance_task")
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    queue = application.bot_data.get("processing_queue")
    if queue:
        await queue.stop()


async def post_shutdown(application):
    # Idempotent fallback if startup failed before Application.start().
    await post_stop(application)


async def on_error(update, context):
    logger.error("Error del bot: %s", type(context.error).__name__)
    if isinstance(update, Update) and usuario_autorizado(update):
        await enviar_texto(update.message, "No se pudo completar la operación. Consulta /entries o vuelve a intentarlo.")


def crear_aplicacion():
    validar_telegram()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.post_init = post_init
    app.post_stop = post_stop
    app.post_shutdown = post_shutdown
    for name, handler in [
        ("start", start), ("help", help_command), ("report", report_command),
        ("entries", entries_command), ("retry", retry_command),
        ("delete", delete_command), ("backup", backup_command),
    ]:
        app.add_handler(CommandHandler(name, handler, block=name not in {"report", "delete", "backup"}))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.VOICE | filters.AUDIO), recibir_audio))
    app.add_error_handler(on_error)
    return app


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    )
    for name in ("httpx", "httpcore", "telegram", "ollama"):
        logging.getLogger(name).setLevel(logging.WARNING)
    crear_aplicacion().run_polling(drop_pending_updates=False)
