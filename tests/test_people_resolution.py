"""Evidence-based identity resolution and search regressions."""
from datetime import date
from pathlib import Path

import pytest

from src import people, web_data


def registry(*rows):
    people.save_people(list(rows), people.snapshot(people.list_people()))
    return {p["name"]: p["entity_id"] for p in people.list_people()}


def test_specific_alias_wins_over_partial_llm_mention(db):
    registry({"name": "Marta amiga", "aliases": ["Marta"]},
             {"name": "Marta prima", "aliases": ["mi prima Marta"]})
    entry = db.guardar_entrada("Hoy vi a mi prima Marta", entities=[("person", "Marta")])
    assert db.obtener_detalles(entry)["people"] == ["Marta prima"]
    assert len(people.entry_people(entry)["mentions"]) == 1


def test_partial_name_is_kept_when_it_also_occurs_separately(db):
    registry({"name": "Marta amiga", "aliases": ["Marta"]},
             {"name": "Marta prima", "aliases": ["mi prima Marta"]})
    entry = db.guardar_entrada("Vi a mi prima Marta y después llamé a Marta", entities=[("person", "Marta")])
    assert db.obtener_detalles(entry)["people"] == ["Marta amiga", "Marta prima"]


def test_context_does_not_override_a_more_specific_registered_name(db):
    registry({"name": "Marta López", "aliases": ["Marta"], "relationship": "prima"},
             {"name": "Marta García"})
    entry = db.guardar_entrada("Hoy vi a mi prima Marta García", entities=[("person", "Marta")])
    assert db.obtener_detalles(entry)["people"] == ["Marta García"]


@pytest.mark.parametrize("text,field,value", [
    ("Vi a mi prima Marta", "relationship", "prima"),
    ("Marta, mi prima, llamó", "relationship", "prima"),
    ("Hablé con Marta que vive en Valencia", "residence", "Valencia"),
    ("Marta, que es de Toledo, llamó", "origin", "Toledo"),
])
def test_explicit_unique_profile_clue_resolves_shared_alias(db, text, field, value):
    registry({"name": "Marta García", "aliases": ["Marta"], field: value},
             {"name": "Marta López", "aliases": ["Marta"]})
    entry = db.guardar_entrada(text, entities=[("person", "Marta")])
    assert db.obtener_detalles(entry)["people"] == ["Marta García"]


@pytest.mark.parametrize("text", ["Vi a Marta en Valencia", "Marta no vive en Valencia", "Quedé con Marta. Mi prima vive en Valencia", "No sé si Marta vive en Valencia", "¿Marta vive en Valencia?"])
def test_nearby_or_negated_clues_do_not_identify_a_shared_alias(db, text):
    registry({"name": "Marta García", "aliases": ["Marta"], "relationship": "prima", "residence": "Valencia"},
             {"name": "Marta López", "aliases": ["Marta"]})
    entry = db.guardar_entrada(text)
    assert all(m["person_id"] is None for m in people.entry_people(entry)["mentions"])


def test_conflicting_profile_clues_require_review(db):
    registry({"name": "Marta García", "aliases": ["Marta"], "relationship": "prima"},
             {"name": "Marta López", "aliases": ["Marta"], "residence": "Valencia"})
    entry = db.guardar_entrada("Vi a mi prima Marta, que vive en Valencia")
    assert all(m["person_id"] is None for m in people.entry_people(entry)["mentions"])


def test_non_unique_or_archived_profile_clue_is_not_assigned(db):
    registry({"name": "Marta García", "aliases": ["Marta"], "relationship": "prima", "archived": True},
             {"name": "Marta López", "aliases": ["Marta"], "relationship": "prima"})
    entry = db.guardar_entrada("Mi prima Marta llamó")
    assert all(m["person_id"] is None for m in people.entry_people(entry)["mentions"])


def test_manual_override_wins_over_longer_alias_and_survives_reanalysis(db, analysis):
    ids = registry({"name": "Marta amiga", "aliases": ["Marta"]},
                   {"name": "Marta prima", "aliases": ["mi prima Marta"]})
    entry = db.guardar_entrada("Mi prima Marta llamó")
    state = people.entry_people(entry)
    people.correct_entry_people(entry, {state["mentions"][0]["mention"]: ids["Marta prima"], "Marta": ids["Marta amiga"]}, people.snapshot(state))
    assert db.obtener_detalles(entry)["people"] == ["Marta amiga"]
    db.completar_entrada(entry, analysis.model_copy(update={"people": ["Marta"]}), "test", "test")
    assert db.obtener_detalles(entry)["people"] == ["Marta amiga"]


def test_llm_mentions_without_literal_evidence_are_not_linked(db):
    registry({"name": "Ana García", "aliases": ["Ana"]})
    entry = db.guardar_entrada("Hoy paseé sola", entities=[("person", "Ana")])
    assert db.obtener_detalles(entry)["people"] == []


def test_v6_migration_repairs_overlaps_without_reassigning_unreviewed_history(db):
    ids = registry({"name": "Marta amiga", "aliases": ["Marta"]},
                   {"name": "Marta prima", "aliases": ["mi prima Marta"]})
    entry = db.guardar_entrada("Vi a mi prima Marta")
    unreviewed = db.guardar_entrada("Marta", entities=[("person", "Marta")])
    with db.conectar() as conn:
        conn.execute("INSERT INTO person_mentions VALUES(?,?,?)", (entry, "Marta", ids["Marta amiga"]))
        conn.execute("UPDATE person_mentions SET person_id=NULL WHERE entry_id=?", (unreviewed,))
        people.sync_entry_people(conn, entry)
        conn.execute("PRAGMA user_version=6")
    web_data.prepare_database()
    assert db.obtener_detalles(entry)["people"] == ["Marta prima"]
    assert people.entry_people(unreviewed)["mentions"][0]["person_id"] is None
    with db.conectar() as conn:
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    assert list((db.DB_PATH.parent / "backups").glob("migration-*.sqlite3"))


def test_profile_history_proposals_require_review_and_revalidation(db):
    entry = db.guardar_entrada("Mi prima Marta llamó", entities=[("person", "Marta")])
    registry({"name": "Marta García", "aliases": ["Marta"], "relationship": "prima"},
             {"name": "Marta López", "aliases": ["Marta"]})
    proposals = people.historical_proposals()
    assert len(proposals) == 1
    assert people.entry_people(entry)["mentions"][0]["person_id"] is None
    people.accept_proposals(proposals)
    assert db.obtener_detalles(entry)["people"] == ["Marta García"]
    assert not people.historical_proposals()


def test_profile_history_proposal_is_rejected_if_the_clue_becomes_ambiguous(db):
    db.guardar_entrada("Mi prima Marta llamó")
    registry({"name": "Marta García", "aliases": ["Marta"], "relationship": "prima"},
             {"name": "Marta López", "aliases": ["Marta"]})
    proposals = people.historical_proposals()
    rows = people.list_people()
    for row in rows:
        row["relationship"] = "prima"
    registry(*rows)
    with pytest.raises(ValueError, match="cambiado"):
        people.accept_proposals(proposals)
    assert all(m["person_id"] is None for m in people.entry_people(proposals[0]["entry_id"])["mentions"])


def test_migration_preserves_manual_override_over_specific_alias(db):
    ids = registry({"name": "Marta amiga", "aliases": ["Marta"]},
                   {"name": "Marta prima", "aliases": ["mi prima Marta"]})
    entry = db.guardar_entrada("Vi a mi prima Marta")
    with db.conectar() as conn:
        conn.execute("INSERT INTO person_mentions VALUES(?,?,?)", (entry, "Marta", ids["Marta amiga"]))
        conn.execute("INSERT INTO person_overrides VALUES(?,?,?)", (entry, "Marta", ids["Marta amiga"]))
        conn.execute("PRAGMA user_version=6")
    db.inicializar_db()
    assert db.obtener_detalles(entry)["people"] == ["Marta amiga"]


def test_identity_filter_preserves_archived_history_and_follows_rename_and_delete(db):
    ids = registry({"name": "Marta García", "aliases": ["la prima"]})
    entry = db.guardar_entrada("Vi a la prima", recorded_at="2026-10-23T12:00:00+02:00")
    start, end = date(2026, 10, 1), date(2026, 10, 31)
    person = people.list_people()[0]
    registry({**person, "name": "Marta López", "archived": True})
    assert [r["id"] for r in web_data.entries(start, end, "Marta López", person_id=ids["Marta García"])] == [entry]
    assert not web_data.entries(date(2026, 10, 24), end, person_id=ids["Marta García"])
    db.borrar_entrada(entry)
    assert not web_data.entries(start, end, person_id=ids["Marta García"])


def test_identity_filter_and_canonical_search_follow_manual_corrections(db):
    ids = registry({"name": "Marta García", "aliases": ["la prima", "Marti"]}, {"name": "Ana López"})
    entry = db.guardar_entrada("Vi a la prima", recorded_at="2026-10-23T12:00:00+02:00")
    start, end = date(2026, 10, 1), date(2026, 10, 31)
    assert [r["id"] for r in web_data.entries(start, end, "Marta García")] == [entry]
    assert [r["id"] for r in web_data.entries(start, end, "Marti")] == [entry]
    assert [r["id"] for r in web_data.entries(start, end, person_id=ids["Marta García"])] == [entry]
    assert not web_data.entries(start, end, "oficina", person_id=ids["Marta García"])
    people.correct_entry_people(entry, {"la prima": ids["Ana López"]}, people.snapshot(people.entry_people(entry)))
    assert not web_data.entries(start, end, person_id=ids["Marta García"])
    assert [r["id"] for r in web_data.entries(start, end, person_id=ids["Ana López"])] == [entry]


def test_search_ui_can_filter_without_typing_a_query(db):
    from streamlit.testing.v1 import AppTest
    ids = registry({"name": "Marta García", "aliases": ["la prima"]})
    db.guardar_entrada("Vi a la prima", recorded_at="2026-10-23T12:00:00+02:00")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py", default_timeout=20)
    app.session_state["section"] = "Buscador"
    app.run()
    app.checkbox(key="search_all_history").uncheck().run()
    app.date_input[0].set_value(date(2026, 10, 1))
    app.date_input[1].set_value(date(2026, 10, 31))
    app.selectbox(key="search_person").select(ids["Marta García"]).run()
    assert not app.exception
    assert len(app.expander) >= 1
    assert any("Vi a la prima" in element.value for element in app.text)
