import os
import asyncio
from dotenv import load_dotenv
from pathlib import Path
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.transcriber import transcribir
from src.database import inicializar_db, guardar_entrada
# ============================================================
# CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError(
        "No se ha encontrado la variable de entorno TELEGRAM_BOT_TOKEN"
    )

AUDIO_DIR = PROJECT_ROOT / "audios"
AUDIO_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)

# ============================================================
# COMANDOS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Al iniciar/reiniciar, desactivamos la recepción de audios
    context.user_data["recibiendo_audio"] = False

    await update.message.reply_text(
        "¡Hola! Soy tu dear-diary bot 🤖\n\n"
        "Estoy preparado para ayudarte a registrar tus "
        "pensamientos y emociones.\n\n"
        "Comienza activando el modo de recepción de audios "
        "para empezar a registrar tus ideas."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Estos son los comandos disponibles:\n\n"
        "/start - Iniciar el bot\n"
        "/help - Mostrar esta ayuda\n"
        "/activar_audio - Activar recepción de audios\n"
        "/detener_audio - Dejar de recibir audios"
    )


async def activar_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["recibiendo_audio"] = True

    await update.message.reply_text(
        "🎙️ Recepción de audio activada.\n"
        "Envíame un audio y lo transcribiré."
    )


async def detener_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["recibiendo_audio"] = False

    await update.message.reply_text(
        "🎙️ Recepción de audio desactivada."
    )


# ============================================================
# RECEPCIÓN DE AUDIO
# ============================================================

async def recibir_audio(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    # Solo procesar audios si el usuario ha activado el modo
    if not context.user_data.get("recibiendo_audio", False):
        return

    logger.info("📥 Audio recibido")

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
        texto = await asyncio.to_thread(transcribir, ruta)

        logger.info("📝 ¡Transcripción completada!")

        if texto:
            hash_entrada = guardar_entrada(
                texto=texto,
                # user_id=update.effective_user.id,
                tipo="audio",
            )

            logger.info(
                "Entrada guardada: %s",
                hash_entrada
            )

            await update.message.reply_text(
                f"📝 {texto}"
    )
        else:
            await update.message.reply_text(
                "No he podido detectar texto en el audio."
            )

    except Exception as e:
        logger.exception("❌ Error:", exc_info=True)

        await update.message.reply_text(
            "❌ Ha ocurrido un error al procesar el audio."
        )

    finally:
        # Borrar el audio después de procesarlo
        if ruta.exists():
            ruta.unlink()
            logger.info(f"🗑️ Eliminado: {ruta}")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    logger.info("Inicializando base de datos...")
    inicializar_db()

    logger.info("Base de datos inicializada.")

    logger.info("Iniciando bot...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(
        CommandHandler("activar_audio", activar_audio)
    )
    app.add_handler(
        CommandHandler("detener_audio", detener_audio)
    )

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
