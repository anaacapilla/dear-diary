import asyncio
import logging
import time
from pathlib import Path
from datetime import datetime

from src.config import (
    AUDIO_DIR,
    LOG_LEVEL,
    OLLAMA_MODEL,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_USER_ID,
    WHISPER_MODEL,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
)

logger = logging.getLogger(__name__)
logger.info("Iniciando configuración de dear-diary")

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.database import inicializar_db, guardar_entrada
from src.llm.extractor import DiaryExtractor
from src.llm.prompts import PROMPT_VERSION
from src.transcriber import transcribir

# ============================================================
# CONFIGURACIÓN
# ============================================================

AUDIO_DIR.mkdir(exist_ok=True)

logger.info("Directorio de audios preparado: %s", AUDIO_DIR)
logger.info("Inicializando extractor LLM con modelo %s", OLLAMA_MODEL)
diary_extractor = DiaryExtractor()
logger.info("Extractor LLM preparado")

logging.getLogger("httpx").setLevel(logging.WARNING)


def formatear_informe(
    transcription,
    analysis,
    transcription_time,
    llm_time,
):
    emotions = ", ".join(analysis.emotions) or "Ninguna"
    places = ", ".join(analysis.places) or "Ninguno"
    people = ", ".join(analysis.people) or "Ninguna"
    total_time = transcription_time + llm_time

    return (
        "📔 Nueva entrada guardada\n\n"
        "📝 Transcripción\n"
        f"{transcription}\n\n"
        "✨ Resumen\n"
        f"{analysis.summary}\n\n"
        "💭 Sentimiento\n"
        f"{analysis.sentiment}\n\n"
        "🎨 Emociones\n"
        f"{emotions}\n\n"
        "📍 Lugares\n"
        f"{places}\n\n"
        "👥 Personas\n"
        f"{people}\n\n"
        "⏱️ Tiempo de procesamiento\n"
        f"Transcripción: {transcription_time:.2f} s\n"
        f"Análisis LLM: {llm_time:.2f} s\n"
        f"Total: {total_time:.2f} s"
    )


def usuario_autorizado(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == TELEGRAM_USER_ID

# ============================================================
# COMANDOS
# ============================================================

MENSAJE_BIENVENIDA = (
    "¡Hola! Soy tu dear-diary bot 🤖\n\n"
    "Estoy preparado para ayudarte a registrar tus "
    "pensamientos y emociones.\n\n"
    "La recepción de audio está activada automáticamente y "
    "ya estoy escuchando para guardar tus ideas."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not usuario_autorizado(update):
        return

    context.user_data["recibiendo_audio"] = True
    await update.message.reply_text(MENSAJE_BIENVENIDA)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not usuario_autorizado(update):
        return

    await update.message.reply_text(
        "Estos son los comandos disponibles:\n\n"
        "/start - Iniciar el bot\n"
        "/help - Mostrar esta ayuda"
    )


# ============================================================
# RECEPCIÓN DE AUDIO
# ============================================================

async def recibir_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not usuario_autorizado(update):
        return

    if context.user_data.get("recibiendo_audio", True) is False:
        return

    logger.info("📥 Audio recibido")
    received_at = datetime.now().astimezone().isoformat()

    # Nota de voz de Telegram
    if update.message.voice:
        archivo_telegram = update.message.voice
        nombre = f"{archivo_telegram.file_id}.ogg"

    # Audio enviado como archivo de música
    elif update.message.audio:
        archivo_telegram = update.message.audio
        nombre = (
            Path(
                archivo_telegram.file_name
                or f"{archivo_telegram.file_id}.mp3"
            ).name
        )

    else:
        return

    ruta = AUDIO_DIR / nombre

    try:
        # Descargar desde Telegram
        archivo = await context.bot.get_file(
            archivo_telegram.file_id
        )

        await archivo.download_to_drive(ruta)

        logger.info(f"💾 Guardado: {ruta}")

        # Avisar al usuario
        await update.message.reply_text(
            "🎙️ Audio recibido. Transcribiendo..."
        )

        # Transcribir
        transcription_start = time.perf_counter()
        texto = await asyncio.to_thread(transcribir, ruta)
        transcription_time = time.perf_counter() - transcription_start

        logger.info("📝 ¡Transcripción completada!")

        if texto:
            await update.message.reply_text(
                "🧠 Analizando la entrada..."
            )

            llm_start = time.perf_counter()
            analysis = await asyncio.to_thread(
                diary_extractor.extract,
                texto,
            )
            llm_time = time.perf_counter() - llm_start

            recorded_at = update.message.date.astimezone().isoformat()
            entity_values = [
                ("person", value) for value in analysis.people
            ] + [
                ("place", value) for value in analysis.places
            ]

            entry_id = guardar_entrada(
                transcription=texto,
                received_at=received_at,
                recorded_at=recorded_at,
                audio_path=str(ruta),
                summary=analysis.summary,
                sentiment=analysis.sentiment,
                events=analysis.events,
                transcription_model=WHISPER_MODEL,
                llm_model=OLLAMA_MODEL,
                llm_prompt_version=PROMPT_VERSION,
                entities=entity_values,
                emotions=analysis.emotions,
            )

            logger.info(
                "Entrada guardada: %s",
                entry_id,
            )

            informe = formatear_informe(
                transcription=texto,
                analysis=analysis,
                transcription_time=transcription_time,
                llm_time=llm_time,
            )
            print(informe)
            await update.message.reply_text(informe)
        else:
            await update.message.reply_text(
                "No he podido detectar texto en el audio."
            )

    except Exception as e:
        logger.exception("❌ Error:", exc_info=True)

        await update.message.reply_text(
            "❌ Ha ocurrido un error al procesar el audio."
        )

    # finally:
    #     # Borrar el audio después de procesarlo
    #     if ruta.exists():
    #         ruta.unlink()
    #         logger.info(f"🗑️ Eliminado: {ruta}")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    logger.info("Inicializando base de datos...")
    inicializar_db()

    logger.info("Base de datos inicializada.")

    logger.info("Iniciando bot...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    async def enviar_bienvenida_inicial(application: Application) -> None:
        try:
            await application.bot.send_message(
                chat_id=TELEGRAM_USER_ID,
                text=MENSAJE_BIENVENIDA,
            )
            logger.info("✅ Mensaje de bienvenida enviado al arrancar")
        except Exception:
            logger.exception("❌ No se pudo enviar el mensaje de bienvenida inicial")

    app.post_init = enviar_bienvenida_inicial

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    # Audios
    app.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO,
            recibir_audio
        )
    )

    logger.info("Bot configurado 🤖")
    logger.info("🤖 Bot iniciado y escuchando...")

    app.run_polling()
