"""Regression coverage for registry identities, manual edits and upgrades."""
import hashlib
import sqlite3

import pytest

from src import people, web_data


def save_registry(rows):
    people.save_people(rows, people.snapshot(people.list_people()))
    return people.list_people()


def edit(entry_id, assignments):
    people.correct_entry_people(entry_id, assignments, people.snapshot(people.entry_people(entry_id)))


def test_registering_name_does_not_identify_historical_mentions_before_acceptance(db):
    entry = db.guardar_entrada("Marta García", entities=[("person", "Marta García")])
    person = save_registry([{"name": "Marta García"}])[0]
    with db.conectar() as conn:
        assert conn.execute("SELECT 1 FROM entry_entities WHERE entry_id=? AND entity_id=?", (entry, person["entity_id"])).fetchone() is None
    assert people.entry_people(entry)["mentions"][0]["person_id"] is None
    people.accept_proposals(people.historical_proposals())
    assert people.entry_people(entry)["mentions"][0]["person_id"] == person["entity_id"]


def test_rename_can_use_existing_candidate_name_without_changing_unresolved_mentions(db):
    entry = db.guardar_entrada("Marta García", entities=[("person", "Marta García")])
    person = save_registry([{"name": "Marta Garrcía"}])[0]
    save_registry([{**person, "name": "Marta García"}])
    assert db.obtener_detalles(entry)["people"] == ["Marta García"]
    assert people.entry_people(entry)["mentions"][0]["person_id"] is None
    save_registry([{**people.list_people()[0], "name": "Marta García López"}])
    assert db.obtener_detalles(entry)["people"] == ["Marta García"]


def test_manual_identity_survives_casing_and_spacing_changes(db, analysis):
    persons = save_registry([{"name": "Marta García", "aliases": ["mi prima"]},
                             {"name": "Ana García"}])
    ana = next(p["entity_id"] for p in persons if p["name"] == "Ana García")
    entry = db.guardar_entrada("Vi a mi prima")
    edit(entry, {"mi prima": ana})
    db.corregir_transcripcion(entry, "Vi a MI   PRIMA", hashlib.sha256("Vi a mi prima".encode()).hexdigest())
    db.completar_entrada(entry, analysis.model_copy(update={"people": ["MI PRIMA"]}), "test", "test")
    assert db.obtener_detalles(entry)["people"] == ["Ana García"]
    assert len(people.entry_people(entry)["mentions"]) == 1


def test_untouched_mentions_are_still_eligible_for_historical_proposals(db):
    person = save_registry([{"name": "Ana García"}])[0]
    entry = db.guardar_entrada("La prima y Marta", entities=[("person", "La prima"), ("person", "Marta")])
    edit(entry, {"La prima": person["entity_id"], "Marta": None})
    save_registry([*people.list_people(), {"name": "Marta García", "aliases": ["Marta"]}])
    assert [p["mention"] for p in people.historical_proposals()] == ["Marta"]


def test_manual_unidentified_choice_cannot_be_bypassed_by_capitalization(db, analysis):
    save_registry([{"name": "Marta García", "aliases": ["Marta"]}])
    entry = db.guardar_entrada("Marta")
    edit(entry, {"Marta": None})
    db.corregir_transcripcion(entry, "MARTA", hashlib.sha256("Marta".encode()).hexdigest())
    db.completar_entrada(entry, analysis.model_copy(update={"people": ["MARTA"]}), "test", "test")
    assert all(m["person_id"] is None for m in people.entry_people(entry)["mentions"])
    assert people.historical_proposals() == []


def test_missing_manual_mention_is_removed_after_transcription_correction(db):
    person = save_registry([{"name": "Marta García"}])[0]
    entry = db.guardar_entrada("Vi a mi prima")
    edit(entry, {"mi prima": person["entity_id"]})
    db.corregir_transcripcion(entry, "Paseé sola", hashlib.sha256("Vi a mi prima".encode()).hexdigest())
    assert people.entry_people(entry)["mentions"] == []
    with db.conectar() as conn:
        assert not conn.execute("SELECT * FROM person_overrides WHERE entry_id=?", (entry,)).fetchall()


def test_v5_upgrade_separates_unidentified_mentions_from_registered_identities(db):
    # Reproduce the old shared namespace without touching any real diary.
    person = save_registry([{"name": "Marta García"}])[0]
    entry = db.guardar_entrada("Marta García")
    with db.conectar() as conn:
        conn.execute("UPDATE person_mentions SET person_id=NULL WHERE entry_id=?", (entry,))
        conn.execute("PRAGMA user_version=5")
    web_data.prepare_database()
    assert people.list_people()[0]["entity_id"] == person["entity_id"]
    with db.conectar() as conn:
        assert not conn.execute("SELECT * FROM entry_entities WHERE entry_id=? AND entity_id=?", (entry, person["entity_id"])).fetchall()
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    backups = list((db.DB_PATH.parent / "backups").glob("migration-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_v4_upgrade_adds_manual_edits_without_losing_person_fields(db):
    person = save_registry([{"name": "Ana García", "aliases": ["Ana"],
                             "groups": ["Universidad"], "relationship": "Amistad"}])[0]
    entry = db.guardar_entrada("Ana")
    with db.conectar() as conn:
        conn.execute("DROP TABLE person_overrides")
        conn.execute("PRAGMA user_version=4")
    web_data.prepare_database()
    assert people.list_people() == [person]
    edit(entry, {"Ana": None})
    assert people.entry_people(entry)["mentions"][0]["person_id"] is None
    assert not people.historical_proposals()


def test_migration_rolls_back_schema_changes_on_failure(db, monkeypatch):
    with db.conectar() as conn:
        conn.execute("PRAGMA user_version=5")
    def fail(conn):
        conn.execute("CREATE TABLE migration_probe (id TEXT)")
        raise ValueError("Migration failed")
    monkeypatch.setattr(people, "create_schema", fail)
    with pytest.raises(ValueError, match="Migration failed"):
        db.inicializar_db()
    with db.conectar() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='migration_probe'").fetchone() is None


def test_sidebar_refresh_reloads_people_changed_in_another_session(db):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py", default_timeout=20)
    app.session_state["section"] = "Mis personas"
    app.run()
    assert app.session_state["people_baseline"] == []
    save_registry([{"name": "Ana García"}])
    next(b for b in app.sidebar.button if b.label == "Actualizar datos").click().run()
    assert not app.exception
    assert app.session_state["people_baseline"][0]["name"] == "Ana García"
