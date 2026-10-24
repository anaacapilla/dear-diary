"""SQLite storage. Every connection closes and enforces foreign keys."""
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime

from src.config import DATABASE_PATH, DIARY_TZ

DB_PATH = DATABASE_PATH
SCHEMA_VERSION = 12


def ahora():
    return datetime.now(DIARY_TZ).isoformat()


def validar_fecha_entrada(value):
    """Explicit diary dates are civil dates, independent of upload timestamps."""
    try:
        day = date.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(day, date) or isinstance(day, datetime):
            raise ValueError
        if day < date(1900, 1, 1) or day > datetime.now(DIARY_TZ).date():
            raise ValueError
        return day.isoformat()
    except (TypeError, ValueError):
        raise ValueError("Elige una fecha válida entre 01/01/1900 y hoy.") from None


def _obtener_fecha_desde_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DIARY_TZ)
        return dt.astimezone(DIARY_TZ).date().isoformat()
    except (ValueError, TypeError):
        return None


@contextmanager
def conectar():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("diary_date", 1, _obtener_fecha_desde_iso)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def backup_database(destination):
    """SQLite's backup API includes committed WAL data, unlike copying the file."""
    from pathlib import Path
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with conectar() as source:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    return destination


def inicializar_db():
    from pathlib import Path
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with conectar() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='diary_entries'"
            ).fetchone()
        if version > SCHEMA_VERSION:
            raise RuntimeError("La base de datos pertenece a una versión más reciente")
        if exists and version < SCHEMA_VERSION:
            stamp = datetime.now(DIARY_TZ).strftime("%Y%m%d-%H%M%S-%f")
            backup_database(path.parent / "backups" / f"migration-{stamp}.sqlite3")
    with conectar() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
            raise RuntimeError("La base de datos pertenece a una versión más reciente")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_entries (
                id TEXT PRIMARY KEY, hash TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL, received_at TEXT, recorded_at TEXT,
                audio_path TEXT, transcription TEXT NOT NULL,
                summary TEXT, sentiment TEXT, events TEXT,
                transcription_model TEXT, llm_model TEXT, llm_prompt_version TEXT
            )
        """)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(diary_entries)")}
        additions = {
            "telegram_chat_id": "INTEGER", "telegram_message_id": "INTEGER",
            "telegram_file_id": "TEXT", "content_hash": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'completed'",
            "error_code": "TEXT",
            "telegram_progress_message_id": "INTEGER",
            "original_transcription": "TEXT",
            "transcription_edited_at": "TEXT",
            "transcription_seconds": "REAL",
            "analysis_seconds": "REAL",
            "audio_seconds": "REAL",
            "entry_date": "TEXT",
            "source": "TEXT NOT NULL DEFAULT 'legacy'",
            "processing": "INTEGER NOT NULL DEFAULT 0",
            "web_request_id": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE diary_entries ADD COLUMN {name} {definition}")
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS telegram_message_unique
            ON diary_entries(telegram_chat_id, telegram_message_id)
            WHERE telegram_chat_id IS NOT NULL AND telegram_message_id IS NOT NULL
        """)
        conn.execute("""CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity TEXT NOT NULL,
            UNIQUE(entity_type, entity))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS entry_entities (
            entry_id TEXT NOT NULL REFERENCES diary_entries(id),
            entity_id TEXT NOT NULL REFERENCES entities(id),
            PRIMARY KEY(entry_id, entity_id))""")
        from src.people import create_schema
        create_schema(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS emotions (
            id TEXT PRIMARY KEY, emotion TEXT NOT NULL UNIQUE)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS entry_emotions (
            entry_id TEXT NOT NULL REFERENCES diary_entries(id),
            emotion_id TEXT NOT NULL REFERENCES emotions(id),
            PRIMARY KEY(entry_id, emotion_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS daily_reports (
            report_date TEXT PRIMARY KEY, entries_count INTEGER NOT NULL DEFAULT 0,
            summary TEXT, sentiment TEXT, emotions TEXT, people TEXT, places TEXT,
            generated_at TEXT NOT NULL, llm_model TEXT, llm_prompt_version TEXT,
            source_mode TEXT NOT NULL DEFAULT 'auto')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS entry_edit_requests (
            entry_id TEXT PRIMARY KEY REFERENCES diary_entries(id) ON DELETE CASCADE,
            prompt_message_id INTEGER NOT NULL UNIQUE, expected_hash TEXT NOT NULL,
            requested_at REAL NOT NULL, progress_message_id INTEGER NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS telegram_entry_dates (
            chat_id INTEGER PRIMARY KEY, command_message_id INTEGER NOT NULL,
            entry_date TEXT)""")
        conn.execute("CREATE TABLE IF NOT EXISTS audio_deletions (path TEXT PRIMARY KEY)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS web_request_unique ON diary_entries(web_request_id) WHERE web_request_id IS NOT NULL")
        conn.execute("""CREATE TABLE IF NOT EXISTS deleted_telegram_messages (
            chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
            PRIMARY KEY(chat_id, message_id))""")
        conn.execute("CREATE TABLE IF NOT EXISTS deleted_web_requests (request_id TEXT PRIMARY KEY)")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            if version < 12:
                from src.people import normalize_stored_aliases
                normalize_stored_aliases(conn)
            if version < 9:
                from src.people import repair_overlapping_mentions
                repair_overlapping_mentions(conn)
            conn.execute("""UPDATE diary_entries SET entry_date=
                COALESCE(entry_date, diary_date(COALESCE(recorded_at, received_at, created_at)))
                WHERE entry_date IS NULL""")
            conn.execute("UPDATE diary_entries SET source='telegram' WHERE telegram_chat_id IS NOT NULL AND source='legacy'")
            for row in conn.execute("SELECT id, transcription FROM diary_entries WHERE content_hash IS NULL").fetchall():
                conn.execute("UPDATE diary_entries SET content_hash=? WHERE id=?", (hashlib.sha256(row["transcription"].encode()).hexdigest(), row["id"]))
            conn.execute("""UPDATE diary_entries SET original_transcription=transcription
                            WHERE original_transcription IS NULL AND transcription<>''""")
            conn.execute("DELETE FROM daily_reports")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def obtener_entrada(entry_id):
    with conectar() as conn:
        row = conn.execute("SELECT * FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
    return dict(row) if row else None


def obtener_entrada_telegram(chat_id, message_id):
    with conectar() as conn:
        row = conn.execute(
            "SELECT * FROM diary_entries WHERE telegram_chat_id=? AND telegram_message_id=?",
            (chat_id, message_id),
        ).fetchone()
    return dict(row) if row else None


def entrada_telegram_borrada(chat_id, message_id):
    with conectar() as conn:
        return conn.execute("SELECT 1 FROM deleted_telegram_messages WHERE chat_id=? AND message_id=?", (chat_id, message_id)).fetchone() is not None


def guardar_entrada(
    transcription, *, received_at=None, recorded_at=None, audio_path=None,
    summary=None, sentiment=None, events=None, transcription_model=None,
    llm_model=None, llm_prompt_version=None, entities=None, emotions=None,
    telegram_chat_id=None, telegram_message_id=None, telegram_file_id=None,
    status="completed", audio_seconds=None, entry_date=None, source=None, web_request_id=None,
):
    if (telegram_chat_id is None) != (telegram_message_id is None):
        raise ValueError("Se necesitan tanto chat_id como message_id")
    entrada_id = str(uuid.uuid4())
    identity = (f"telegram:{telegram_chat_id}:{telegram_message_id}"
                if telegram_chat_id is not None else f"entry:{entrada_id}")
    identity_hash = hashlib.sha256(identity.encode()).hexdigest()
    created_at = ahora()
    day = validar_fecha_entrada(entry_date) if entry_date is not None else _obtener_fecha_desde_iso(recorded_at or received_at or created_at)
    source = source or ("telegram" if telegram_chat_id is not None else "legacy")
    if source not in ("telegram", "web", "legacy"):
        raise ValueError("Origen de entrada desconocido")
    with conectar() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if web_request_id:
            if conn.execute("SELECT 1 FROM deleted_web_requests WHERE request_id=?", (web_request_id,)).fetchone():
                raise ValueError("Esta entrada ya se borró. Abre Añadir entrada para crear otra.")
            existing = conn.execute("SELECT id, transcription, entry_date FROM diary_entries WHERE web_request_id=?", (web_request_id,)).fetchone()
            if existing:
                if existing["transcription"] != transcription or existing["entry_date"] != day:
                    raise ValueError("Esta solicitud ya se guardó. Abre una entrada nueva.")
                return existing["id"]
        if telegram_chat_id is not None:
            if conn.execute("SELECT 1 FROM deleted_telegram_messages WHERE chat_id=? AND message_id=?", (telegram_chat_id, telegram_message_id)).fetchone():
                raise ValueError("Esta entrada ya se borró")
            existing = conn.execute(
                "SELECT id FROM diary_entries WHERE telegram_chat_id=? AND telegram_message_id=?",
                (telegram_chat_id, telegram_message_id),
            ).fetchone()
            if existing:
                return existing["id"]
            intent = conn.execute("SELECT * FROM telegram_entry_dates WHERE chat_id=?", (telegram_chat_id,)).fetchone()
            if intent and intent["entry_date"] and telegram_message_id > intent["command_message_id"]:
                day = intent["entry_date"]
                conn.execute("UPDATE telegram_entry_dates SET entry_date=NULL WHERE chat_id=?", (telegram_chat_id,))
        conn.execute("""
            INSERT INTO diary_entries (
                id, hash, created_at, received_at, recorded_at, audio_path,
                transcription, summary, sentiment, events, transcription_model,
                llm_model, llm_prompt_version, telegram_chat_id, telegram_message_id,
                telegram_file_id, content_hash, status, audio_seconds, entry_date, source, web_request_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            entrada_id, identity_hash, created_at, received_at, recorded_at, audio_path,
            transcription, summary, sentiment, json.dumps(events or [], ensure_ascii=False),
            transcription_model, llm_model, llm_prompt_version, telegram_chat_id,
            telegram_message_id, telegram_file_id,
            hashlib.sha256(transcription.encode()).hexdigest(), status, audio_seconds, day, source, web_request_id,
        ))
        _guardar_relaciones(conn, entrada_id, entities or [], emotions or [])
        conn.execute("DELETE FROM daily_reports")
    return entrada_id


def _guardar_relaciones(conn, entry_id, entities, emotions):
    from src.people import record_mentions
    conn.execute("DELETE FROM entry_entities WHERE entry_id=?", (entry_id,))
    conn.execute("DELETE FROM entry_emotions WHERE entry_id=?", (entry_id,))
    for entity_type, value in set(entities):
        if entity_type == "person":
            # Raw mentions have their own namespace; only accepted identities use person.
            entity_type = "person_mention"
        value = value.strip()
        if not value:
            continue
        conn.execute(
            "INSERT INTO entities VALUES(?,?,?) ON CONFLICT(entity_type,entity) DO NOTHING",
            (str(uuid.uuid4()), entity_type, value),
        )
        entity_id = conn.execute(
            "SELECT id FROM entities WHERE entity_type=? AND entity=?",
            (entity_type, value),
        ).fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO entry_entities VALUES(?,?)", (entry_id, entity_id))
    record_mentions(conn, entry_id, entities)
    for emotion in set(emotions):
        conn.execute("INSERT INTO emotions VALUES(?,?) ON CONFLICT(emotion) DO NOTHING",
                     (str(uuid.uuid4()), emotion))
        emotion_id = conn.execute(
            "SELECT id FROM emotions WHERE emotion=?", (emotion,),
        ).fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO entry_emotions VALUES(?,?)", (entry_id, emotion_id))


def guardar_transcripcion(entry_id, text, model, *, seconds=None):
    from src.people import record_mentions
    with conectar() as conn:
        conn.execute("""
            UPDATE diary_entries SET transcription=?, content_hash=?,
            transcription_model=?, status=?, error_code=NULL,
            original_transcription=COALESCE(original_transcription, ?),
            transcription_seconds=COALESCE(?, transcription_seconds) WHERE id=?
        """, (text, hashlib.sha256(text.encode()).hexdigest(), model,
              "transcribed" if text else "empty", text, seconds, entry_id))
        record_mentions(conn, entry_id, [])
        conn.execute("DELETE FROM daily_reports")


def completar_entrada(entry_id, analysis, model, prompt_version, *, seconds=None):
    with conectar() as conn:
        conn.execute("""
            UPDATE diary_entries SET summary=?, sentiment=?, events=?, llm_model=?,
            llm_prompt_version=?, status='completed', error_code=NULL,
            analysis_seconds=COALESCE(?, analysis_seconds) WHERE id=?
        """, (analysis.summary, analysis.sentiment,
              json.dumps(analysis.events, ensure_ascii=False), model, prompt_version, seconds, entry_id))
        _guardar_relaciones(
            conn, entry_id,
            [("person", p) for p in analysis.people] + [("place", p) for p in analysis.places],
            analysis.emotions,
        )
        conn.execute("DELETE FROM daily_reports")


def marcar_error(entry_id, stage):
    with conectar() as conn:
        conn.execute(
            "UPDATE diary_entries SET status='failed', error_code=? WHERE id=?", (stage, entry_id),
        )
        conn.execute("DELETE FROM daily_reports")


def preparar_reintento(entry_id):
    with conectar() as conn:
        conn.execute(
            "UPDATE diary_entries SET status='pending', error_code=NULL WHERE id=? AND status!='empty'",
            (entry_id,),
        )
        conn.execute("DELETE FROM daily_reports")


def entradas_pendientes():
    with conectar() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM diary_entries WHERE status NOT IN ('completed','empty') ORDER BY created_at"
        )]


def indicar_fecha_telegram(chat_id, message_id, day):
    day = validar_fecha_entrada(day) if day is not None else None
    with conectar() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT command_message_id FROM telegram_entry_dates WHERE chat_id=?", (chat_id,)).fetchone()
        if current and current[0] >= message_id:
            return False
        conn.execute("INSERT INTO telegram_entry_dates VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET command_message_id=excluded.command_message_id, entry_date=excluded.entry_date", (chat_id, message_id, day))
    return True


def reclamar_entrada(entry_id):
    with conectar() as conn:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute("UPDATE diary_entries SET processing=1 WHERE id=? AND processing=0 AND status IN ('pending','transcribed')", (entry_id,)).rowcount
        row = conn.execute("SELECT * FROM diary_entries WHERE id=?", (entry_id,)).fetchone() if changed else None
    return dict(row) if row else None


def liberar_entrada(entry_id):
    with conectar() as conn:
        conn.execute("UPDATE diary_entries SET processing=0 WHERE id=?", (entry_id,))


def recuperar_procesamiento():
    # One bot instance owns the durable queue; a restart releases interrupted work.
    with conectar() as conn:
        conn.execute("UPDATE diary_entries SET processing=0")


def entradas_del_dia(fecha):
    date.fromisoformat(fecha)
    with conectar() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT * FROM diary_entries
            WHERE COALESCE(entry_date, diary_date(COALESCE(recorded_at, received_at, created_at)))=?
            ORDER BY COALESCE(recorded_at, received_at, created_at), id
        """, (fecha,))]


def _obtener_sentimiento_global(sentimientos):
    values = {s for s in sentimientos if s}
    return next(iter(values)) if len(values) == 1 else ("mezclado" if values else "neutral")


def actualizar_reporte_diario(fecha, *, llm_model=None, llm_prompt_version=None, summarize=None):
    entries_all = entradas_del_dia(fecha)
    entries = [e for e in entries_all if e["status"] == "completed"]
    with conectar() as conn:
        entities = conn.execute("""
            SELECT DISTINCT CASE en.entity_type WHEN 'person_mention' THEN 'person'
                ELSE en.entity_type END AS entity_type, en.entity FROM entities en
            JOIN entry_entities ee ON ee.entity_id=en.id
            JOIN diary_entries d ON d.id=ee.entry_id
            WHERE COALESCE(d.entry_date, diary_date(COALESCE(d.recorded_at,d.received_at,d.created_at)))=?
            AND d.status='completed' ORDER BY en.entity
        """, (fecha,)).fetchall()
        emotions = [r[0] for r in conn.execute("""
            SELECT DISTINCT e.emotion FROM emotions e
            JOIN entry_emotions ee ON ee.emotion_id=e.id
            JOIN diary_entries d ON d.id=ee.entry_id
            WHERE COALESCE(d.entry_date, diary_date(COALESCE(d.recorded_at,d.received_at,d.created_at)))=?
            AND d.status='completed' ORDER BY e.emotion
        """, (fecha,))]
        cached = conn.execute("SELECT * FROM daily_reports WHERE report_date=?", (fecha,)).fetchone()
    summaries = [e["summary"] for e in entries if e["summary"]]
    source_mode = "empty" if not entries else ("single_entry" if len(entries) == 1 else "multi_entry")
    summary = " ".join(summaries) or ("Sin resumen disponible." if entries else "No hay entradas analizadas.")
    if len(entries) > 1 and summarize:
        if cached and cached["source_mode"] == "llm" and cached["llm_model"] == llm_model and cached["llm_prompt_version"] == llm_prompt_version:
            summary = cached["summary"]
        else:
            summary = summarize(summaries)
        source_mode = "llm"
    report = {
        "report_date": fecha, "entries_count": len(entries),
        "pending_count": sum(e["status"] not in ("completed", "empty") for e in entries_all),
        "summary": summary, "sentiment": _obtener_sentimiento_global([e["sentiment"] for e in entries]),
        "emotions": emotions,
        "people": [r["entity"] for r in entities if r["entity_type"] == "person"],
        "places": [r["entity"] for r in entities if r["entity_type"] == "place"],
        "generated_at": ahora(), "source_mode": source_mode,
    }
    # Never replace a successful LLM synthesis with a fallback aggregation.
    if source_mode == "llm" or not cached or cached["source_mode"] != "llm":
        with conectar() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_reports (
                    report_date, entries_count, summary, sentiment, emotions, people,
                    places, generated_at, llm_model, llm_prompt_version, source_mode
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (fecha, len(entries), summary, report["sentiment"],
                  json.dumps(emotions, ensure_ascii=False),
                  json.dumps(report["people"], ensure_ascii=False),
                  json.dumps(report["places"], ensure_ascii=False),
                  report["generated_at"], llm_model, llm_prompt_version, source_mode))
    return report


def obtener_reporte_diario(fecha, **kwargs):
    return actualizar_reporte_diario(fecha, **kwargs)


def obtener_dias_con_entradas_del_mes(year, month):
    date(year, month, 1)
    prefix = f"{year:04d}-{month:02d}"
    with conectar() as conn:
        return [r[0] for r in conn.execute("""
            SELECT DISTINCT COALESCE(entry_date, diary_date(COALESCE(recorded_at,received_at,created_at))) AS day
            FROM diary_entries WHERE substr(COALESCE(entry_date, diary_date(COALESCE(recorded_at,received_at,created_at))),1,7)=?
            ORDER BY day
        """, (prefix,))]


def borrar_entrada(entry_id, *, expected_hash=None):
    with conectar() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            return None
        if row["processing"]:
            raise ValueError("La entrada se está procesando. Espera a que termine para borrarla.")
        if expected_hash is not None and row["content_hash"] != expected_hash:
            raise ValueError("La entrada ha cambiado. Vuelve a abrir la confirmación.")
        if row["telegram_chat_id"] is not None:
            conn.execute("INSERT OR IGNORE INTO deleted_telegram_messages VALUES(?,?)", (row["telegram_chat_id"], row["telegram_message_id"]))
        if row["web_request_id"]:
            conn.execute("INSERT OR IGNORE INTO deleted_web_requests VALUES(?)", (row["web_request_id"],))
        conn.execute("DELETE FROM entry_entities WHERE entry_id=?", (entry_id,))
        conn.execute("DELETE FROM entry_emotions WHERE entry_id=?", (entry_id,))
        conn.execute("DELETE FROM diary_entries WHERE id=?", (entry_id,))
        conn.execute("DELETE FROM entities WHERE id NOT IN (SELECT entity_id FROM entry_entities) AND id NOT IN (SELECT entity_id FROM people)")
        conn.execute("DELETE FROM emotions WHERE id NOT IN (SELECT emotion_id FROM entry_emotions)")
        conn.execute("DELETE FROM daily_reports")
        if row["audio_path"] and not conn.execute("SELECT 1 FROM diary_entries WHERE audio_path=?", (row["audio_path"],)).fetchone():
            conn.execute("INSERT OR IGNORE INTO audio_deletions VALUES(?)", (row["audio_path"],))
    return {"audio_path": row["audio_path"]}


def limpiar_referencia_audio(path):
    with conectar() as conn:
        conn.execute("UPDATE diary_entries SET audio_path=NULL WHERE audio_path=?", (str(path),))


def rutas_audio():
    with conectar() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT audio_path, received_at, created_at FROM diary_entries WHERE audio_path IS NOT NULL"
        )]


def guardar_mensaje_progreso(entry_id, message_id):
    with conectar() as conn:
        conn.execute(
            "UPDATE diary_entries SET telegram_progress_message_id=? WHERE id=?",
            (message_id, entry_id),
        )


def obtener_detalles(entry_id):
    with conectar() as conn:
        row = conn.execute("SELECT * FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            return None
        entry = dict(row)
        entities = conn.execute(
            """SELECT DISTINCT CASE en.entity_type WHEN 'person_mention' THEN 'person'
                   ELSE en.entity_type END AS entity_type, en.entity FROM entities en
               JOIN entry_entities ee ON ee.entity_id=en.id
               WHERE ee.entry_id=? ORDER BY en.entity""", (entry_id,),
        ).fetchall()
        entry["people"] = [r["entity"] for r in entities if r["entity_type"] == "person"]
        entry["places"] = [r["entity"] for r in entities if r["entity_type"] == "place"]
        entry["emotions"] = [r[0] for r in conn.execute(
            """SELECT e.emotion FROM emotions e JOIN entry_emotions ee ON ee.emotion_id=e.id
               WHERE ee.entry_id=? ORDER BY e.emotion""", (entry_id,),
        )]
    return entry


def corregir_transcripcion(entry_id, text, expected_hash):
    text = text.strip()
    if not text or len(text) > 12000:
        raise ValueError("La transcripción debe contener entre 1 y 12000 caracteres")
    with conectar() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise ValueError("La entrada ya no existe")
        current_hash = hashlib.sha256(row["transcription"].encode()).hexdigest()
        if current_hash != expected_hash or row["processing"] or row["status"] not in ("completed", "failed", "empty"):
            raise ValueError("La entrada ha cambiado")
        conn.execute("""
            UPDATE diary_entries SET original_transcription=COALESCE(original_transcription, transcription),
            transcription=?, content_hash=?, transcription_edited_at=?, status='pending', error_code=NULL,
            summary=NULL, sentiment=NULL, events='[]', llm_model=NULL, llm_prompt_version=NULL,
            analysis_seconds=NULL WHERE id=?
        """, (text, hashlib.sha256(text.encode()).hexdigest(), ahora(), entry_id))
        _guardar_relaciones(conn, entry_id, [], [])
        conn.execute("DELETE FROM entry_edit_requests WHERE entry_id=?", (entry_id,))
        conn.execute("DELETE FROM daily_reports")


def obtener_solicitud_edicion(prompt_id=None):
    with conectar() as conn:
        if prompt_id is None:
            row = conn.execute("SELECT * FROM entry_edit_requests ORDER BY requested_at DESC LIMIT 1").fetchone()
        else:
            row = conn.execute("SELECT * FROM entry_edit_requests WHERE prompt_message_id=?", (prompt_id,)).fetchone()
    return dict(row) if row else None


def guardar_solicitud_edicion(entry_id, prompt_id, expected_hash, requested_at, progress_id):
    with conectar() as conn:
        conn.execute("DELETE FROM entry_edit_requests")
        conn.execute("INSERT INTO entry_edit_requests VALUES(?,?,?,?,?)",
                     (entry_id, prompt_id, expected_hash, requested_at, progress_id))


def cancelar_edicion(entry_id=None):
    with conectar() as conn:
        if entry_id is None:
            conn.execute("DELETE FROM entry_edit_requests")
        else:
            conn.execute("DELETE FROM entry_edit_requests WHERE entry_id=?", (entry_id,))
