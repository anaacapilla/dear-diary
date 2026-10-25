"""Diary UI startup and read-only queries; no model or Telegram dependency."""
import sqlite3
from contextlib import contextmanager

from src import database
from src import people


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


def search_options():
    """Offer only values attached to existing entries, including new entity types."""
    with read_connection() as conn:
        entities = {}
        for row in conn.execute("""SELECT DISTINCT en.entity_type, en.entity
                FROM entities en JOIN entry_entities ee ON ee.entity_id=en.id
                WHERE en.entity_type NOT IN ('person', 'person_mention')
                ORDER BY en.entity_type, en.entity COLLATE NOCASE"""):
            entities.setdefault(row["entity_type"], []).append(row["entity"])
        options = {field: [r[0] for r in conn.execute(
            f"SELECT DISTINCT {field} FROM diary_entries WHERE {field} IS NOT NULL AND {field}<>'' ORDER BY {field}"
        )] for field in ("sentiment", "source", "status")}
        options["entities"] = entities
        options["emotions"] = [r[0] for r in conn.execute("""SELECT DISTINCT e.emotion
            FROM emotions e JOIN entry_emotions ee ON ee.emotion_id=e.id ORDER BY e.emotion""")]
        return options


def entries(start, end, query="", *, person_id=None, entity_filters=None,
            emotion=None, sentiment=None, source=None, status=None):
    """Search text or a person's interchangeable names, without modifying history."""
    with read_connection() as conn:
        clauses, parameters = [], []
        for kind, value in (entity_filters or {}).items():
            clauses.append("""EXISTS (SELECT 1 FROM entry_entities ee
                JOIN entities en ON en.id=ee.entity_id
                WHERE ee.entry_id=diary_entries.id AND en.entity_type=? AND en.entity=?)""")
            parameters.extend((kind, value))
        if emotion is not None:
            clauses.append("""EXISTS (SELECT 1 FROM entry_emotions ee
                JOIN emotions e ON e.id=ee.emotion_id
                WHERE ee.entry_id=diary_entries.id AND e.emotion=?)""")
            parameters.append(emotion)
        for field, value in (("sentiment", sentiment), ("source", source), ("status", status)):
            if value is not None:
                clauses.append(f"{field}=?")
                parameters.append(value)
        extra = " AND " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute("""
            SELECT *, COALESCE(entry_date, diary_date(COALESCE(recorded_at,received_at,created_at))) AS day
            FROM diary_entries
            WHERE COALESCE(entry_date, diary_date(COALESCE(recorded_at,received_at,created_at))) BETWEEN ? AND ?
            AND (? IS NULL OR EXISTS (
                SELECT 1 FROM person_mentions pm
                WHERE pm.entry_id=diary_entries.id AND pm.person_id=?))
        """ + extra + " ORDER BY COALESCE(recorded_at,received_at,created_at) DESC, id",
            (start.isoformat(), end.isoformat(), person_id, person_id, *parameters)).fetchall()
        if not rows or not query.strip():
            return [dict(row) for row in rows]
        registry = people.list_people(conn)
        query_key = people.alias_key(query)
        identities = {p["entity_id"] for p in registry
                      if query_key in {people.alias_key(name) for name in [p["name"], *p["aliases"]]}}
        selected_ids = {row["id"] for row in rows}
        saved, manual = {}, {}
        for table, target in (("person_mentions", saved), ("person_overrides", manual)):
            for item in conn.execute(f"SELECT entry_id, mention, person_id FROM {table}"):
                if item["entry_id"] in selected_ids:
                    target.setdefault(item["entry_id"], []).append(dict(item))
        registry_names = {p["entity_id"]: [p["name"], *p["aliases"]] for p in registry}
        index = people.alias_index(conn) if identities else {}
    needle = people.alias_text(people.normalize(query))
    results = []
    for row in rows:
        mentions = saved.get(row["id"], [])
        if identities:
            # Search can find an unconfirmed unique alias, but a saved/manual choice wins.
            spans = [(*m, 0) for m in people.literal_spans(row["transcription"], index)]
            for priority, decisions in ((1, mentions), (2, manual.get(row["id"], []))):
                for decision in decisions:
                    if priority == 1 and decision["person_id"] is None:
                        continue
                    spans.extend((*m, priority) for m in people.literal_spans(
                        row["transcription"], {decision["mention"]: {decision["person_id"]}}))
            found = {identity for _, identity in people.select_mentions(spans) if identity is not None}
            matches = bool(identities & found)
        else:
            text = " ".join([str(row[field] or "") for field in ("transcription", "summary", "events")]
                            + [name for m in mentions for name in registry_names.get(m["person_id"], [])])
            matches = needle in people.alias_text(people.normalize(text))
        if matches:
            results.append(dict(row))
    return results


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
