"""Aliases must identify and find the same person across voice transcriptions."""
from datetime import date
from pathlib import Path

import pytest

from src import people, web_data


START, END = date(2020, 1, 1), date(2026, 10, 24)
MOTHER = "Rosa María Pérez García"


def register(*rows):
    people.save_people(list(rows), people.snapshot(people.list_people()))
    return {p["name"]: p["entity_id"] for p in people.list_people()}


def ids(query, **kwargs):
    return {r["id"] for r in web_data.entries(START, END, query, **kwargs)}


@pytest.mark.parametrize("expression", ["mamá", "mama", "MAMÁ", "mami", "mi madre"])
def test_comma_aliases_identify_without_llm_extraction(db, expression):
    register({"name": MOTHER, "aliases": ["Mamá, mami, mama, mi madre"]})
    entry = db.guardar_entrada(f"Hoy he hablado con {expression}")
    assert db.obtener_detalles(entry)["people"] == [MOTHER]
    assert len(people.list_people()[0]["aliases"]) == 4


def test_alias_matching_keeps_original_accents_and_accepts_voice_punctuation(db):
    register({"name": "Marta García", "aliases": ["mi prima Marta"]})
    entry = db.guardar_entrada("Hablé con mi prima, Marta")
    assert db.obtener_detalles(entry)["people"] == ["Marta García"]
    assert people.entry_people(entry)["mentions"][0]["mention"] == "mi prima, Marta"


def test_ignoring_accents_does_not_hide_collisions_or_confuse_enye(db):
    register({"name": "José García", "aliases": ["José"]},
             {"name": "Jose López", "aliases": ["Jose"]},
             {"name": "Begoña García", "aliases": ["Begoña"]})
    entry = db.guardar_entrada("Hablé con Jose y Begona")
    assert all(m["person_id"] is None for m in people.entry_people(entry)["mentions"])
    assert len(people.entry_people(entry)["mentions"]) == 1


def test_explicit_context_uses_the_same_accent_normalization(db):
    register({"name": "José García", "aliases": ["José"], "relationship": "primo"},
             {"name": "Jose López", "aliases": ["Jose"]})
    entry = db.guardar_entrada("Vi a mi primo Jose")
    assert db.obtener_detalles(entry)["people"] == ["José García"]


@pytest.mark.parametrize("uncertainty", ["Quizá", "Quizás", "Quiza", "Quizas"])
def test_accent_normalization_does_not_turn_uncertain_context_into_identity(db, uncertainty):
    register({"name": "José García", "aliases": ["José"], "relationship": "primo"},
             {"name": "Jose López", "aliases": ["Jose"]})
    entry = db.guardar_entrada(f"{uncertainty} mi primo Jose llamó")
    assert all(m["person_id"] is None for m in people.entry_people(entry)["mentions"])


def test_v11_migration_splits_old_comma_lists_with_backup_and_preserves_history(db):
    person_id = register({"name": MOTHER})[MOTHER]
    entry = db.guardar_entrada("Hablé con mamá", recorded_at="2025-06-01T12:00:00+02:00")
    with db.conectar() as conn:
        raw = "Mamá, mami, mama, Rosa, la mamá, mi madre"
        conn.execute("INSERT INTO person_aliases VALUES(?,?,?)", (person_id, raw, people.normalize(raw)))
        conn.execute("PRAGMA user_version=11")
    web_data.prepare_database()
    assert "mami" in people.list_people()[0]["aliases"]
    assert len(people.list_people()[0]["aliases"]) == 6
    assert people.entry_people(entry)["mentions"] == []
    assert ids(MOTHER) == ids("mamá") == ids("mami") == {entry}
    people.accept_proposals(people.historical_proposals())
    assert db.obtener_detalles(entry)["people"] == [MOTHER]
    assert len(list((db.DB_PATH.parent / "backups").glob("migration-*.sqlite3"))) == 1


def test_canonical_and_alias_queries_have_identical_results_across_history(db):
    old = db.guardar_entrada("Hoy comí con mi madre", recorded_at="2024-01-02T12:00:00+01:00")
    register({"name": MOTHER, "aliases": ["mamá; mami, mi madre"]})
    first = db.guardar_entrada("Hablé con mama")
    second = db.guardar_entrada("Mami me llamó")
    third = db.guardar_entrada(f"Vi a {MOTHER}")
    for query in (MOTHER, "Rosa Maria Perez Garcia", "mamá", "mama", "mami", "mi madre"):
        assert ids(query) == {old, first, second, third}


def test_search_respects_manual_corrections_including_unidentified(db):
    registered = register({"name": MOTHER, "aliases": ["mamá", "mami"]}, {"name": "Ana López"})
    entry = db.guardar_entrada("Mamá me llamó")
    people.correct_entry_people(entry, {"Mamá": registered["Ana López"]}, people.snapshot(people.entry_people(entry)))
    assert not ids(MOTHER)
    assert not ids("mami")
    assert not ids("mamá")
    assert ids("Ana López") == {entry}
    people.correct_entry_people(entry, {"Mamá": None}, people.snapshot(people.entry_people(entry)))
    assert not ids(MOTHER)
    assert not ids("mamá")


def test_equivalent_accented_mentions_have_one_manual_choice(db):
    registered = register({"name": MOTHER, "aliases": ["mamá"]}, {"name": "Ana López"})
    entry = db.guardar_entrada("Mamá llamó y después vi a mama")
    state = people.entry_people(entry)
    assert len(state["mentions"]) == 1
    people.correct_entry_people(entry, {state["mentions"][0]["mention"]: registered["Ana López"]}, people.snapshot(state))
    assert ids("Ana López") == {entry}
    assert not ids(MOTHER)
    assert not ids("mamá")


def test_shared_alias_does_not_infer_a_historical_identity(db):
    entry = db.guardar_entrada("Mi madre llamó")
    register({"name": MOTHER, "aliases": ["mi madre"]},
             {"name": "Ana López", "aliases": ["mi madre"]})
    assert not ids(MOTHER)
    assert not ids("Ana López")
    assert ids("madre") == {entry}  # Ordinary text search remains available.


def test_detection_is_saved_before_analysis_and_survives_llm_failure(db):
    register({"name": MOTHER, "aliases": ["mamá"]})
    entry = db.guardar_entrada("", status="pending")
    db.guardar_transcripcion(entry, "Mamá llamó", "test")
    db.marcar_error(entry, "analysis")
    assert db.obtener_detalles(entry)["people"] == [MOTHER]
    assert ids("mamá") == ids(MOTHER) == {entry}


def test_search_dates_and_confirmed_person_filter_remain_explicit(db):
    old = db.guardar_entrada("Vi a mamá", recorded_at="2024-01-02T12:00:00+01:00")
    person_id = register({"name": MOTHER, "aliases": ["mamá"]})[MOTHER]
    new = db.guardar_entrada("Mamá llamó", recorded_at="2026-10-23T12:00:00+02:00")
    assert ids(MOTHER) == {old, new}
    assert ids(MOTHER, person_id=person_id) == {new}
    assert {r["id"] for r in web_data.entries(date(2026, 10, 1), END, "mamá")} == {new}


def test_search_ui_defaults_to_all_dates_and_finds_canonical_name_from_alias(db):
    from streamlit.testing.v1 import AppTest

    db.guardar_entrada("Mami llamó", recorded_at="2024-01-02T12:00:00+01:00")
    register({"name": MOTHER, "aliases": ["mamá", "mami"]})
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py", default_timeout=20)
    app.session_state["section"] = "Buscador"
    app.run()
    next(t for t in app.text_input if t.label == "Buscar en el diario").input(MOTHER)
    next(b for b in app.button if b.label == "Buscar como texto literal").click().run()
    assert not app.exception
    assert app.checkbox(key="search_all_history").value is True
    assert any("Mami llamó" in element.value for element in app.text)
