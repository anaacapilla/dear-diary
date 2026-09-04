import hashlib
import json
import sqlite3
import uuid
from collections import Counter
from datetime import datetime

from src.config import DATABASE_PATH

DB_PATH = DATABASE_PATH


def inicializar_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS diary_entries (
                id TEXT PRIMARY KEY,
                hash TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                received_at TEXT,
                recorded_at TEXT,
                audio_path TEXT,
                transcription TEXT NOT NULL,
                summary TEXT,
                sentiment TEXT,
                events TEXT,
                transcription_model TEXT,
                llm_model TEXT,
                llm_prompt_version TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity TEXT NOT NULL,
                UNIQUE(entity_type, entity)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS entry_entities (
                entry_id TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                PRIMARY KEY (entry_id, entity_id),
                FOREIGN KEY (entry_id) REFERENCES diary_entries(id),
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS emotions (
                id TEXT PRIMARY KEY,
                emotion TEXT NOT NULL UNIQUE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS entry_emotions (
                entry_id TEXT NOT NULL,
                emotion_id TEXT NOT NULL,
                PRIMARY KEY (entry_id, emotion_id),
                FOREIGN KEY (entry_id) REFERENCES diary_entries(id),
                FOREIGN KEY (emotion_id) REFERENCES emotions(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_reports (
                report_date TEXT PRIMARY KEY,
                entries_count INTEGER NOT NULL DEFAULT 0,
                summary TEXT,
                sentiment TEXT,
                emotions TEXT,
                people TEXT,
                places TEXT,
                generated_at TEXT NOT NULL,
                llm_model TEXT,
                llm_prompt_version TEXT,
                source_mode TEXT NOT NULL DEFAULT 'auto'
            )
        """)

        conn.commit()


def _obtener_sentimiento_global(sentimientos):
    sentimientos = [s for s in sentimientos if s]
    if not sentimientos:
        return "neutral"

    conteo = Counter(sentimientos)
    valor_mas_comun, frecuencia = conteo.most_common(1)[0]

    if frecuencia == len(sentimientos):
        return valor_mas_comun

    return "mezclado"


def _obtener_fecha_desde_iso(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _obtener_entradas_del_dia(fecha):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                summary,
                sentiment,
                recorded_at,
                created_at
            FROM diary_entries
            WHERE date(COALESCE(recorded_at, created_at)) = ?
            ORDER BY COALESCE(recorded_at, created_at) ASC
            """,
            (fecha,),
        ).fetchall()

    return [
        {
            "id": row[0],
            "summary": row[1],
            "sentiment": row[2],
            "recorded_at": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]


def _obtener_agregados_del_dia(fecha):
    with sqlite3.connect(DB_PATH) as conn:
        emociones = conn.execute(
            """
            SELECT e.emotion
            FROM diary_entries d
            JOIN entry_emotions ee ON ee.entry_id = d.id
            JOIN emotions e ON e.id = ee.emotion_id
            WHERE date(COALESCE(d.recorded_at, d.created_at)) = ?
            """,
            (fecha,),
        ).fetchall()

        entidades = conn.execute(
            """
            SELECT en.entity_type, en.entity
            FROM diary_entries d
            JOIN entry_entities ee ON ee.entry_id = d.id
            JOIN entities en ON en.id = ee.entity_id
            WHERE date(COALESCE(d.recorded_at, d.created_at)) = ?
            """,
            (fecha,),
        ).fetchall()

    emociones = [row[0] for row in emociones]
    personas = sorted({row[1] for row in entidades if row[0] == "person"})
    lugares = sorted({row[1] for row in entidades if row[0] == "place"})

    return {
        "emotions": emociones,
        "people": personas,
        "places": lugares,
    }


def actualizar_reporte_diario(fecha, *, llm_model=None, llm_prompt_version=None):
    fecha = _obtener_fecha_desde_iso(fecha) or fecha
    entries = _obtener_entradas_del_dia(fecha)
    agregados = _obtener_agregados_del_dia(fecha)

    if not entries:
        summary = "No hay entradas registradas para este día."
        sentiment = "neutral"
        entries_count = 0
        source_mode = "empty"
    elif len(entries) == 1:
        summary = entries[0]["summary"] or "Sin resumen disponible."
        sentiment = entries[0]["sentiment"] or "neutral"
        entries_count = 1
        source_mode = "single_entry"
    else:
        summaries = [entry["summary"] for entry in entries if entry["summary"]]
        summary = " ".join(summaries) if summaries else "Sin resumen disponible."
        sentiment = _obtener_sentimiento_global(
            [entry["sentiment"] for entry in entries if entry["sentiment"]]
        )
        entries_count = len(entries)
        source_mode = "multi_entry"

    generated_at = datetime.now().astimezone().isoformat()
    payload = {
        "report_date": fecha,
        "entries_count": entries_count,
        "summary": summary,
        "sentiment": sentiment,
        "emotions": json.dumps(agregados["emotions"], ensure_ascii=False),
        "people": json.dumps(agregados["people"], ensure_ascii=False),
        "places": json.dumps(agregados["places"], ensure_ascii=False),
        "generated_at": generated_at,
        "llm_model": llm_model,
        "llm_prompt_version": llm_prompt_version,
        "source_mode": source_mode,
    }

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO daily_reports (
                report_date,
                entries_count,
                summary,
                sentiment,
                emotions,
                people,
                places,
                generated_at,
                llm_model,
                llm_prompt_version,
                source_mode
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_date)
            DO UPDATE SET
                entries_count = excluded.entries_count,
                summary = excluded.summary,
                sentiment = excluded.sentiment,
                emotions = excluded.emotions,
                people = excluded.people,
                places = excluded.places,
                generated_at = excluded.generated_at,
                llm_model = excluded.llm_model,
                llm_prompt_version = excluded.llm_prompt_version,
                source_mode = excluded.source_mode
            """,
            (
                payload["report_date"],
                payload["entries_count"],
                payload["summary"],
                payload["sentiment"],
                payload["emotions"],
                payload["people"],
                payload["places"],
                payload["generated_at"],
                payload["llm_model"],
                payload["llm_prompt_version"],
                payload["source_mode"],
            ),
        )
        conn.commit()

    return {
        "report_date": payload["report_date"],
        "entries_count": payload["entries_count"],
        "summary": payload["summary"],
        "sentiment": payload["sentiment"],
        "emotions": agregados["emotions"],
        "people": agregados["people"],
        "places": agregados["places"],
        "generated_at": payload["generated_at"],
        "source_mode": payload["source_mode"],
    }


def obtener_reporte_diario(fecha):
    fecha = _obtener_fecha_desde_iso(fecha) or fecha

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT report_date, entries_count, summary, sentiment, emotions, people, places, generated_at, source_mode FROM daily_reports WHERE report_date = ?",
            (fecha,),
        ).fetchone()

    if row is None:
        report = actualizar_reporte_diario(fecha)
        return report

    return {
        "report_date": row[0],
        "entries_count": row[1],
        "summary": row[2],
        "sentiment": row[3],
        "emotions": json.loads(row[4] or "[]"),
        "people": json.loads(row[5] or "[]"),
        "places": json.loads(row[6] or "[]"),
        "generated_at": row[7],
        "source_mode": row[8],
    }


def obtener_dias_con_entradas_del_mes(year, month):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT date(COALESCE(recorded_at, created_at))
            FROM diary_entries
            WHERE CAST(strftime('%Y', COALESCE(recorded_at, created_at)) AS INTEGER) = ?
              AND CAST(strftime('%m', COALESCE(recorded_at, created_at)) AS INTEGER) = ?
            ORDER BY date(COALESCE(recorded_at, created_at)) ASC
            """,
            (year, month),
        ).fetchall()

    return [row[0] for row in rows]


def guardar_entrada(
    transcription,
    *,
    received_at=None,
    recorded_at=None,
    audio_path=None,
    summary=None,
    sentiment=None,
    events=None,
    transcription_model=None,
    llm_model=None,
    llm_prompt_version=None,
    entities=None,
    emotions=None,
):
    entrada_id = str(uuid.uuid4())

    created_at = datetime.now().astimezone().isoformat()

    hash_entrada = hashlib.sha256(
        transcription.encode("utf-8")
    ).hexdigest()

    events_json = json.dumps(events or [], ensure_ascii=False)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO diary_entries (
                id,
                hash,
                created_at,
                received_at,
                recorded_at,
                audio_path,
                transcription,
                summary,
                sentiment,
                events,
                transcription_model,
                llm_model,
                llm_prompt_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entrada_id,
            hash_entrada,
            created_at,
            received_at,
            recorded_at,
            audio_path,
            transcription,
            summary,
            sentiment,
            events_json,
            transcription_model,
            llm_model,
            llm_prompt_version,
        ))

        existing_entry = conn.execute(
            "SELECT id FROM diary_entries WHERE hash = ?",
            (hash_entrada,),
        ).fetchone()

        if existing_entry is not None:
            conn.commit()
            report_date = _obtener_fecha_desde_iso(recorded_at) or _obtener_fecha_desde_iso(received_at) or datetime.now().astimezone().date().isoformat()
            actualizar_reporte_diario(report_date)
            return existing_entry[0]

        for entity_type, entity_value in entities or []:
            entity_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO entities (id, entity_type, entity)
                VALUES (?, ?, ?)
                ON CONFLICT(entity_type, entity) DO NOTHING
            """, (entity_id, entity_type, entity_value))

            entity_row = conn.execute("""
                SELECT id FROM entities
                WHERE entity_type = ? AND entity = ?
            """, (entity_type, entity_value)).fetchone()

            conn.execute("""
                INSERT OR IGNORE INTO entry_entities (entry_id, entity_id)
                VALUES (?, ?)
            """, (entrada_id, entity_row[0]))

        for emotion in emotions or []:
            emotion_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO emotions (id, emotion)
                VALUES (?, ?)
                ON CONFLICT(emotion) DO NOTHING
            """, (emotion_id, emotion))

            emotion_row = conn.execute("""
                SELECT id FROM emotions WHERE emotion = ?
            """, (emotion,)).fetchone()

            conn.execute("""
                INSERT OR IGNORE INTO entry_emotions (entry_id, emotion_id)
                VALUES (?, ?)
            """, (entrada_id, emotion_row[0]))

        conn.commit()

    report_date = _obtener_fecha_desde_iso(recorded_at) or _obtener_fecha_desde_iso(received_at) or datetime.now().astimezone().date().isoformat()
    actualizar_reporte_diario(report_date)

    return entrada_id
