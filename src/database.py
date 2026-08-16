import sqlite3
import uuid
import hashlib
from datetime import datetime


DB_PATH = "dear_diary.db"


def inicializar_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entradas (
                id TEXT PRIMARY KEY,
                hash TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                texto TEXT NOT NULL,
                tipo TEXT
            )
        """)

        conn.commit()


def guardar_entrada(texto, tipo):
    entrada_id = str(uuid.uuid4())

    created_at = datetime.now().astimezone().isoformat()

    hash_entrada = hashlib.sha256(
        texto.encode("utf-8")
    ).hexdigest()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO entradas (
                id,
                hash,
                created_at,
                texto,
                tipo
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            entrada_id,
            hash_entrada,
            created_at,
            texto,
            tipo,
        ))

        conn.commit()

    return entrada_id
