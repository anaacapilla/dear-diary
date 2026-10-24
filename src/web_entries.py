"""Creation and permanent deletion from the web, independent of Telegram."""
from src import database
from src.maintenance import audio_seguro, limpiar_audios_borrados


def create_entry(day, text, request_id):
    day = database.validar_fecha_entrada(day)
    text = text.strip()
    if not text or len(text) > 12000:
        raise ValueError("Escribe una entrada de entre 1 y 12000 caracteres.")
    if not request_id:
        raise ValueError("Vuelve a abrir Añadir entrada.")
    return database.guardar_entrada(text, entry_date=day, source="web", status="pending", web_request_id=request_id)


def delete_entry(entry_id, expected_hash):
    entry = database.obtener_entrada(entry_id)
    if entry is None:
        return True
    # Validate the path before changing SQLite. Never delete outside AUDIO_DIR.
    audio_seguro(entry["audio_path"])
    result = database.borrar_entrada(entry_id, expected_hash=expected_hash)
    if result is None:
        return True
    limpiar_audios_borrados()
    with database.conectar() as conn:
        pending = conn.execute("SELECT 1 FROM audio_deletions WHERE path=?", (result["audio_path"],)).fetchone()
    return not bool(pending)


def retry_entry(entry_id):
    with database.conectar() as conn:
        changed = conn.execute("""UPDATE diary_entries SET status='pending', error_code=NULL
            WHERE id=? AND status='failed' AND processing=0""", (entry_id,)).rowcount
        if not changed:
            raise ValueError("La entrada ha cambiado o ya está en proceso.")
        conn.execute("DELETE FROM daily_reports")
