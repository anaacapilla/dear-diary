"""Diary UI startup and read-only queries; no model or Telegram dependency."""
import sqlite3
from contextlib import contextmanager

from src import database


class DiarySchemaError(ValueError):
    """A schema mismatch that requires an actionable message in the web UI."""


def schema_error(version):
    if version > database.SCHEMA_VERSION:
        return DiarySchemaError(
            "El diario usa una versión más reciente que la cargada por esta web "
            f"(base: {version}; web: {database.SCHEMA_VERSION}). "
            "Detén Streamlit con Ctrl+C y vuelve a iniciarlo desde la misma carpeta "
            "del proyecto que el bot. Refrescar el navegador no reinicia Streamlit."
        )
    return DiarySchemaError(
        "El esquema del diario necesita actualizarse. Reinicia Streamlit desde "
        "la misma carpeta del proyecto que el bot para completar la actualización."
    )


def prepare_database():
    """Migrate an existing diary without silently creating one at a wrong path."""
    uri = database.DB_PATH.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='diary_entries'"
        ).fetchone()
        if not exists:
            raise ValueError("La base de datos no contiene un diario.")
        if version > database.SCHEMA_VERSION:
            raise schema_error(version)
    finally:
        conn.close()
    if version < database.SCHEMA_VERSION:
        database.inicializar_db()


@contextmanager
def read_connection():
    # A missing database must not silently create an empty diary.
    uri = database.DB_PATH.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.create_function("diary_date", 1, database._obtener_fecha_desde_iso)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != database.SCHEMA_VERSION:
            raise schema_error(version)
        yield conn
    finally:
        conn.close()


def entries(start, end, query="", *, person_id=None):
    """Inclusive local dates. Text matching is literal, case-insensitive Unicode."""
    with read_connection() as conn:
        rows = conn.execute("""
            SELECT *, COALESCE(entry_date, diary_date(COALESCE(recorded_at,received_at,created_at))) AS day
            FROM diary_entries
            WHERE COALESCE(entry_date, diary_date(COALESCE(recorded_at,received_at,created_at))) BETWEEN ? AND ?
            AND (? IS NULL OR EXISTS (
                SELECT 1 FROM person_mentions pm
                WHERE pm.entry_id=diary_entries.id AND pm.person_id=?))
            ORDER BY COALESCE(recorded_at,received_at,created_at) DESC, id
        """, (start.isoformat(), end.isoformat(), person_id, person_id)).fetchall()
        names = {}
        for item in conn.execute("""SELECT DISTINCT pm.entry_id, e.entity
                FROM person_mentions pm JOIN people p ON p.entity_id=pm.person_id
                JOIN entities e ON e.id=p.entity_id
                UNION SELECT DISTINCT pm.entry_id, a.alias
                FROM person_mentions pm JOIN person_aliases a ON a.person_id=pm.person_id"""):
            names.setdefault(item["entry_id"], []).append(item["entity"])
    needle = query.strip().casefold()
    return [dict(row) for row in rows if not needle or needle in " ".join(
        [str(row[field] or "") for field in ("transcription", "summary", "events")]
        + names.get(row["id"], [])
    ).casefold()]


def details(entry_id):
    with read_connection() as conn:
        row = conn.execute("SELECT * FROM diary_entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["entities"] = [dict(r) for r in conn.execute("""
            SELECT DISTINCT CASE en.entity_type WHEN 'person_mention' THEN 'person'
                ELSE en.entity_type END AS entity_type, en.entity FROM entities en
            JOIN entry_entities ee ON ee.entity_id=en.id WHERE ee.entry_id=?
            ORDER BY entity_type, en.entity
        """, (entry_id,))]
        result["emotions"] = [r[0] for r in conn.execute("""
            SELECT e.emotion FROM emotions e JOIN entry_emotions ee ON ee.emotion_id=e.id
            WHERE ee.entry_id=? ORDER BY e.emotion
        """, (entry_id,))]
        return result
