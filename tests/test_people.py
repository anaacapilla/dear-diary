import pytest

from src import people


def register(*rows):
    people.save_people(list(rows), people.snapshot(people.list_people()))
    return people.list_people()


def test_unique_aliases_and_word_boundaries(db):
    person = register({"name": "Marta García", "aliases": ["Marta", "mi prima Marta"]})[0]
    entry = db.guardar_entrada("Hablé con MI prima Marta y Martab.")
    assert db.obtener_detalles(entry)["people"] == ["Marta García"]
    with db.conectar() as conn:
        assert dict(conn.execute("SELECT mention, person_id FROM person_mentions WHERE entry_id=?", (entry,))) == {"MI prima Marta": person["entity_id"]}
    assert not people.candidates()


def test_shared_alias_stays_unresolved(db):
    register({"name": "Marta García", "aliases": ["la prima"]},
             {"name": "Ana García", "aliases": ["LA PRIMA"]})
    entry = db.guardar_entrada("Vi a la prima.")
    assert db.obtener_detalles(entry)["people"] == ["la prima"]
    assert people.candidates() == [{"mention": "la prima", "entries_count": 1}]
    assert people.historical_proposals() == []


def test_history_requires_explicit_acceptance_and_keeps_original(db):
    entry = db.guardar_entrada("Vi a mi prima Marta.", entities=[("person", "mi prima Marta")])
    register({"name": "Marta García", "aliases": ["mi prima Marta"]})
    assert db.obtener_detalles(entry)["people"] == ["mi prima Marta"]
    proposals = people.historical_proposals()
    assert len(proposals) == 1
    people.accept_proposals(proposals)
    assert db.obtener_detalles(entry)["people"] == ["Marta García"]
    assert db.obtener_entrada(entry)["transcription"] == "Vi a mi prima Marta."
    assert not people.candidates()
    assert not people.historical_proposals()
    db.inicializar_db()
    assert not people.candidates()


def test_stale_proposals_are_rejected_atomically(db):
    db.guardar_entrada("Marta")
    register({"name": "Marta García", "aliases": ["Marta"]})
    proposals = people.historical_proposals()
    register(*people.list_people(), {"name": "Marta López", "aliases": ["Marta"]})
    with pytest.raises(ValueError):
        people.accept_proposals(proposals)


def test_registry_survives_entry_deletion_and_groups_are_normalized(db):
    register({"name": "Marta García", "groups": ["Universidad", " universidad "]},
             {"name": "Lorena García", "groups": ["UNIVERSIDAD"]})
    assert all(p["groups"] == ["Universidad"] for p in people.list_people())
    entry = db.guardar_entrada("Marta García")
    db.borrar_entrada(entry)
    assert len(people.list_people()) == 2


def test_registry_validation_rolls_back_and_detects_concurrent_edit(db):
    initial = people.snapshot([])
    with pytest.raises(ValueError):
        people.save_people([{"name": "Marta García"}, {"name": ""}], initial)
    assert people.list_people() == []
    register({"name": "Marta García"})
    with pytest.raises(ValueError):
        people.save_people([], initial)
    with pytest.raises(ValueError):
        people.save_people([], people.snapshot(people.list_people()))


def test_unknown_extracted_people_are_candidates_and_reanalysis_replaces_mentions(db, analysis):
    entry = db.guardar_entrada("Ana", entities=[("person", "Ana")])
    assert people.candidates()[0]["mention"] == "Ana"
    register({"name": "Ana López", "aliases": ["Ana"]})
    db.completar_entrada(entry, analysis, "test", "test")
    assert db.obtener_detalles(entry)["people"] == ["Ana López"]
    assert not people.candidates()


def test_people_page_navigation_and_candidates(db):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    db.guardar_entrada("Marta", entities=[("person", "Marta")])
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py", default_timeout=20).run()
    app.sidebar.button(key="nav_Mis personas").click().run()
    assert not app.exception
    assert [t.label for t in app.tabs] == ["Mis personas", "Por organizar", "Propuestas del histórico"]
    next(t for t in app.text_input if t.label == "Nombre completo de la nueva persona").input("Marta García")
    next(b for b in app.button if b.label == "Guardar persona o alias").click().run()
    assert not app.exception
    assert people.list_people()[0]["aliases"] == ["Marta"]
    app.button(key="find_people_proposals").click().run()
    assert not app.exception
    assert len(app.session_state["people_proposals"]) == 1
    app.session_state["people_review"] = {"edited_rows": {0: {"Aceptar": True}}, "added_rows": [], "deleted_rows": []}
    next(b for b in app.button if b.label == "Aceptar seleccionadas").click().run()
    assert not app.exception
    assert not people.candidates()
    assert not people.historical_proposals()


def test_archived_alias_is_not_assigned_or_reused(db):
    register({"name": "Marta García", "aliases": ["Marta"], "archived": True},
             {"name": "Marta López", "aliases": ["Marta"]})
    entry = db.guardar_entrada("Marta")
    assert db.obtener_detalles(entry)["people"] == ["Marta"]
    assert people.historical_proposals() == []


def test_v3_migration_seeds_candidates_and_backups_original_database(db):
    entry = db.guardar_entrada("Mi prima", entities=[("person", "Mi prima")])
    with db.conectar() as conn:
        conn.execute("UPDATE entities SET entity_type='person' WHERE entity_type='person_mention'")
        for table in ("person_mentions", "person_groups", "person_aliases", "people"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("PRAGMA user_version=3")
    db.inicializar_db()
    assert people.candidates() == [{"mention": "Mi prima", "entries_count": 1}]
    assert db.obtener_entrada(entry)["transcription"] == "Mi prima"
    assert len(list((db.DB_PATH.parent / "backups").glob("migration-*.sqlite3"))) == 1
    with db.conectar() as conn:
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_registry_editor_adds_person_and_saves_fields(db):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py", default_timeout=20)
    app.session_state["section"] = "Mis personas"
    app.run()
    app.session_state["people_editor"] = {"edited_rows": {}, "deleted_rows": [], "added_rows": [
        {"name": "Lorena García", "aliases": "Lore; Lorena", "groups": ["Universidad", "Cole"],
         "relationship": "Amistad", "known_from": "Universidad", "residence": "Madrid", "origin": "Sevilla"}]}
    next(b for b in app.button if b.label == "Guardar cambios").click().run()
    assert not app.exception
    person = people.list_people()[0]
    assert person["name"] == "Lorena García"
    assert person["relationship"] == "Amistad"
    assert person["known_from"] == "Universidad"
    assert person["groups"] == ["Cole", "Universidad"]
    assert person["aliases"] == ["Lore", "Lorena"]
