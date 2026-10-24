from datetime import date
import sqlite3
from pathlib import Path

import pytest

from src import database, web_data


def test_read_only_search_dates_and_literal_text(db):
    entry_id = db.guardar_entrada("MARTA: 100% feliz", recorded_at="2026-10-21T23:30:00+00:00")
    assert not web_data.entries(date(2026, 10, 21), date(2026, 10, 21))
    rows = web_data.entries(date(2026, 10, 22), date(2026, 10, 22), "marta")
    assert [r["id"] for r in rows] == [entry_id]
    assert len(web_data.entries(date(2026, 10, 22), date(2026, 10, 22), "%")) == 1
    assert not web_data.entries(date(2026, 10, 22), date(2026, 10, 22), "_")
    with web_data.read_connection() as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM diary_entries")


def test_details_include_analysis(db, analysis):
    entry_id = db.guardar_entrada("Paseo", entities=[("person", "Ana")], emotions=["alegria"])
    assert web_data.details(entry_id)["entities"] == [{"entity_type": "person", "entity": "Ana"}]
    assert web_data.details(entry_id)["emotions"] == ["alegria"]
    assert web_data.details("missing") is None


def test_web_navigation(db):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py", default_timeout=15).run()
    assert not app.exception
    for section in ("Buscador", "Estadísticas", "Entradas"):
        app.sidebar.button(key="nav_" + section).click().run()
        assert not app.exception


def test_missing_database_is_not_created(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "missing.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    with pytest.raises(sqlite3.OperationalError):
        web_data.entries(date(2026, 1, 1), date(2026, 12, 31))
    assert not path.exists()


def test_web_startup_migrates_existing_diary_without_bot(db):
    entry = db.guardar_entrada("Marta", entities=[("person", "Marta")])
    with db.conectar() as conn:
        for table in ("person_mentions", "person_groups", "person_aliases", "people"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("PRAGMA user_version=3")
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py", default_timeout=20).run()
    assert not app.exception
    assert not app.error
    with db.conectar() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert conn.execute("SELECT mention FROM person_mentions WHERE entry_id=?", (entry,)).fetchone()[0] == "Marta"
    assert len(list((db.DB_PATH.parent / "backups").glob("migration-*.sqlite3"))) == 1
    web_data.prepare_database()
    assert len(list((db.DB_PATH.parent / "backups").glob("migration-*.sqlite3"))) == 1


def test_web_startup_does_not_create_missing_diary(tmp_path, monkeypatch):
    path = tmp_path / "missing.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    with pytest.raises(sqlite3.OperationalError):
        web_data.prepare_database()
    assert not path.exists()


def test_web_startup_rejects_newer_database(db):
    with db.conectar() as conn:
        conn.execute(f"PRAGMA user_version={db.SCHEMA_VERSION + 1}")
    with pytest.raises(ValueError, match="más reciente"):
        web_data.prepare_database()


def test_running_web_with_old_schema_shows_restart_instructions(db, monkeypatch):
    # Simulate Streamlit keeping the imported module while the bot has migrated SQLite.
    entry_id = db.guardar_entrada("Texto conservado")
    monkeypatch.setattr(database, "SCHEMA_VERSION", db.SCHEMA_VERSION - 1)
    with pytest.raises(web_data.DiarySchemaError, match="Ctrl.C"):
        web_data.prepare_database()
    with pytest.raises(web_data.DiarySchemaError, match="Refrescar el navegador"):
        web_data.entries(date(2026, 1, 1), date(2026, 12, 31))
    assert db.obtener_entrada(entry_id)["transcription"] == "Texto conservado"


def test_schema_error_is_visible_in_streamlit(db, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(database, "SCHEMA_VERSION", db.SCHEMA_VERSION - 1)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py").run()
    assert not app.exception
    assert len(app.error) == 1
    assert "Ctrl+C" in app.error[0].value
    assert "permisos" not in app.error[0].value


def test_calendar_arrows_and_edit(db):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    entry_id = db.guardar_entrada("Texto original", recorded_at="2026-12-01T12:00:00+01:00")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py", default_timeout=15)
    app.session_state["calendar_year"] = 2026
    app.session_state["calendar_month"] = 12
    app.session_state["diary_day"] = date(2026, 12, 1)
    app.run()
    next(b for b in app.button if b.label == "→").click().run()
    assert app.number_input[0].value == 2027
    assert app.selectbox(key="calendar_month").value == 1
    next(b for b in app.button if b.label == "←").click().run()
    assert app.number_input[0].value == 2026
    assert app.selectbox(key="calendar_month").value == 12
    app.button(key="open_" + entry_id).click().run()
    app.text_area[0].input("Texto corregido desde la web")
    next(b for b in app.button if b.label == "Guardar cambios").click().run()
    assert not app.exception
    saved = db.obtener_entrada(entry_id)
    assert saved["transcription"] == "Texto corregido desde la web"
    assert saved["original_transcription"] == "Texto original"
    assert saved["status"] == "pending"


def test_web_edit_detects_conflicting_change(db):
    import hashlib

    entry_id = db.guardar_entrada("Original")
    expected = hashlib.sha256(b"Original").hexdigest()
    db.corregir_transcripcion(entry_id, "Primera corrección", expected)
    with pytest.raises(ValueError, match="cambiado"):
        db.corregir_transcripcion(entry_id, "Edición obsoleta", expected)
    assert db.obtener_entrada(entry_id)["transcription"] == "Primera corrección"
