import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest


def save(db, text="texto", **kwargs):
    return db.guardar_entrada(text, recorded_at="2026-08-30T12:00:00+02:00", **kwargs)


def test_entities_and_emotions_are_saved(db):
    entry = save(db, text="Paseé con Ana en Madrid", entities=[("person", "Ana"), ("place", "Madrid"), ("person", "Ana")],
                 emotions=["alegria", "alegria"])
    with db.conectar() as c:
        assert c.execute("SELECT count(*) FROM entry_entities WHERE entry_id=?", (entry,)).fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM entry_emotions WHERE entry_id=?", (entry,)).fetchone()[0] == 1
    report = db.obtener_reporte_diario("2026-08-30")
    assert report["people"] == ["Ana"]
    assert report["places"] == ["Madrid"]
    assert report["emotions"] == ["alegria"]


def test_same_text_different_messages_creates_two_entries(db):
    a = save(db, telegram_chat_id=1, telegram_message_id=1)
    b = save(db, telegram_chat_id=1, telegram_message_id=2)
    assert a != b
    assert db.obtener_reporte_diario("2026-08-30")["entries_count"] == 2


def test_same_text_without_message_identity_is_not_lost(db):
    assert save(db) != save(db)


def test_duplicate_message_is_idempotent(db):
    a = save(db, telegram_chat_id=1, telegram_message_id=2)
    b = save(db, text="otro texto", telegram_chat_id=1, telegram_message_id=2)
    assert a == b
    assert db.obtener_entrada(a)["transcription"] == "texto"


@pytest.mark.parametrize("timestamp, day", [
    ("2026-08-30T00:30:00+02:00", "2026-08-30"),
    ("2026-08-29T22:30:00+00:00", "2026-08-30"),
    ("2026-01-01T00:30:00+01:00", "2026-01-01"),
    ("2026-10-25T00:30:00+00:00", "2026-10-25"),
    ("2026-10-25T01:30:00+00:00", "2026-10-25"),
    ("2026-08-30T00:30:00", "2026-08-30"),
])
def test_reports_use_madrid_including_midnight_and_dst(db, timestamp, day):
    db.guardar_entrada("texto", recorded_at=timestamp)
    assert db.obtener_reporte_diario(day)["entries_count"] == 1
    year, month, _ = map(int, day.split("-"))
    assert db.obtener_dias_con_entradas_del_mes(year, month) == [day]


def test_pending_transcription_survives_llm_failure_and_retry(db, analysis):
    entry = save(db, text="", status="pending")
    db.guardar_transcripcion(entry, "Paseo con Ana en Madrid", "base")
    db.marcar_error(entry, "analysis")
    assert db.obtener_entrada(entry)["transcription"] == "Paseo con Ana en Madrid"
    assert db.obtener_reporte_diario("2026-08-30")["pending_count"] == 1
    db.completar_entrada(entry, analysis, "test", "2")
    assert db.obtener_entrada(entry)["status"] == "completed"
    assert db.entradas_pendientes() == []
    assert db.obtener_reporte_diario("2026-08-30")["people"] == ["Ana"]


def test_every_connection_enforces_foreign_keys_and_rolls_back(db):
    with pytest.raises(sqlite3.IntegrityError):
        with db.conectar() as c:
            c.execute("INSERT INTO entry_entities VALUES('missing','missing')")
    assert save(db)


def test_backup_can_be_restored(db, tmp_path):
    entry = save(db)
    copy = db.backup_database(tmp_path / "copy.sqlite3")
    c = sqlite3.connect(copy)
    try:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("SELECT id FROM diary_entries").fetchone()[0] == entry
    finally:
        c.close()


def test_delete_removes_relations_and_invalidates_reports(db):
    entry = save(db, entities=[("person", "Ana")], emotions=["alegria"])
    db.obtener_reporte_diario("2026-08-30")
    assert db.borrar_entrada(entry) is not None
    assert db.obtener_entrada(entry) is None
    assert db.obtener_reporte_diario("2026-08-30")["entries_count"] == 0
    with db.conectar() as c:
        assert c.execute("SELECT count(*) FROM entities").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM emotions").fetchone()[0] == 0


def test_day_synthesis_is_cached_and_invalidated_on_new_entry(db):
    save(db, text="uno", summary="Mañana")
    save(db, text="dos", summary="Tarde")
    synth = Mock(return_value="Síntesis conjunta")
    assert db.obtener_reporte_diario("2026-08-30", summarize=synth)["summary"] == "Síntesis conjunta"
    db.obtener_reporte_diario("2026-08-30", summarize=synth)
    assert synth.call_count == 1
    save(db, text="tres", summary="Noche")
    db.obtener_reporte_diario("2026-08-30", summarize=synth)
    assert synth.call_count == 2


def test_migration_preserves_legacy_entry_and_backs_up(tmp_path, monkeypatch):
    from src import database as db
    path = tmp_path / "legacy.db"
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE diary_entries (
        id TEXT PRIMARY KEY, hash TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
        received_at TEXT, recorded_at TEXT, audio_path TEXT, transcription TEXT NOT NULL,
        summary TEXT, sentiment TEXT, events TEXT, transcription_model TEXT,
        llm_model TEXT, llm_prompt_version TEXT)""")
    c.execute("""INSERT INTO diary_entries(id,hash,created_at,recorded_at,transcription)
                 VALUES('legacy','oldhash','2026-08-30T00:30:00+02:00',
                        '2026-08-30T00:30:00+02:00','Texto anterior')""")
    c.commit()
    c.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.inicializar_db()
    assert db.obtener_entrada("legacy")["transcription"] == "Texto anterior"
    assert db.obtener_reporte_diario("2026-08-30")["entries_count"] == 1
    assert len(list((tmp_path / "backups").glob("migration-*.sqlite3"))) == 1
    db.inicializar_db()
    assert len(list((tmp_path / "backups").glob("migration-*.sqlite3"))) == 1
