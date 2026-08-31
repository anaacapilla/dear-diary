import hashlib
import json
import sqlite3
import uuid
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

        conn.commit()


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

    return entrada_id
