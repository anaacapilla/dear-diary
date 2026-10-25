from datetime import date
from pathlib import Path

import pytest

from src import people


def register_people():
    people.save_people([
        {"name": "Marta García", "aliases": ["Marta", "la prima"]},
        {"name": "Ana García", "aliases": ["la prima"]},
    ], people.snapshot([]))
    return {p["name"]: p["entity_id"] for p in people.list_people()}


def correct(entry_id, assignments):
    people.correct_entry_people(entry_id, assignments, people.snapshot(people.entry_people(entry_id)))


def test_manual_ambiguity_resolution_is_local_and_updates_reports(db):
    ids = register_people()
    first = db.guardar_entrada("Vi a la prima.", recorded_at="2026-10-23T12:00:00+02:00")
    second = db.guardar_entrada("Vi a la prima.", recorded_at="2026-10-22T12:00:00+02:00")
    correct(first, {"la prima": ids["Ana García"]})
    assert db.obtener_detalles(first)["people"] == ["Ana García"]
    assert db.obtener_detalles(second)["people"] == ["la prima"]
    assert db.obtener_reporte_diario("2026-10-23")["people"] == ["Ana García"]
    assert people.candidates() == [{"mention": "la prima", "entries_count": 1}]


def test_correction_removes_previous_identity_and_survives_reanalysis(db, analysis):
    ids = register_people()
    entry = db.guardar_entrada("Marta", entities=[("person", "Marta")])
    correct(entry, {"Marta": ids["Ana García"]})
    assert db.obtener_detalles(entry)["people"] == ["Ana García"]
    db.completar_entrada(entry, analysis.model_copy(update={"people": ["Marta"]}), "test", "test")
    assert db.obtener_detalles(entry)["people"] == ["Ana García"]
    correct(entry, {"Marta": None})
    assert db.obtener_detalles(entry)["people"] == ["Marta"]
    assert not people.historical_proposals()
    db.completar_entrada(entry, analysis.model_copy(update={"people": ["Marta"]}), "test", "test")
    assert people.entry_people(entry)["mentions"][0]["person_id"] is None


def test_correction_keeps_person_when_another_mention_still_refers_to_them(db):
    ids = register_people()
    entry = db.guardar_entrada("Marta y la prima")
    correct(entry, {"Marta": ids["Marta García"], "la prima": ids["Marta García"]})
    correct(entry, {"Marta": ids["Marta García"], "la prima": ids["Ana García"]})
    assert db.obtener_detalles(entry)["people"] == ["Ana García", "Marta García"]


def test_manual_addition_requires_literal_mention_and_preserves_places(db):
    ids = register_people()
    entry = db.guardar_entrada("Vi a mi amiga en Madrid", entities=[("place", "Madrid")])
    correct(entry, {"mi amiga": ids["Ana García"]})
    assert db.obtener_detalles(entry)["places"] == ["Madrid"]
    with pytest.raises(ValueError, match="literalmente"):
        correct(entry, {"mi amiga": ids["Ana García"], "inventada": ids["Ana García"]})
    assert db.obtener_detalles(entry)["people"] == ["Ana García"]


def test_concurrent_edits_and_processing_are_rejected(db):
    ids = register_people()
    entry = db.guardar_entrada("Marta")
    expected = people.snapshot(people.entry_people(entry))
    correct(entry, {"Marta": ids["Ana García"]})
    with pytest.raises(ValueError, match="cambiado"):
        people.correct_entry_people(entry, {"Marta": None}, expected)
    db.preparar_reintento(entry)
    with pytest.raises(ValueError, match="procesando"):
        correct(entry, {"Marta": None})


def test_registry_errors_identify_empty_or_duplicate_row(db):
    with pytest.raises(ValueError, match="fila 1"):
        people.save_people([{"name": ""}], people.snapshot([]))
    with pytest.raises(ValueError, match="repetido.*fila 2"):
        people.save_people([{"name": "Ana García"}, {"name": " ANA  GARCÍA "}], people.snapshot([]))


def test_edit_people_from_calendar_and_search(db):
    from streamlit.testing.v1 import AppTest

    register_people()
    entry = db.guardar_entrada("Vi a la prima", recorded_at="2026-10-23T12:00:00+02:00")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py", default_timeout=20)
    app.session_state["diary_day"] = date(2026, 10, 23)
    app.session_state["calendar_year"] = 2026
    app.session_state["calendar_month"] = 10
    app.run()
    app.button(key="open_people_" + entry).click().run()
    next(s for s in app.selectbox if s.label == "«la prima»").select(people.list_people()[0]["entity_id"])
    next(b for b in app.button if b.label == "Guardar personas").click().run()
    assert not app.exception
    assert db.obtener_detalles(entry)["people"] == ["Ana García"]
    app.sidebar.button(key="nav_Buscador").click().run()
    next(t for t in app.text_input if t.label == "Buscar en el diario").input("prima")
    next(b for b in app.button if b.label == "Buscar como texto literal").click().run()
    assert not app.exception
    app.button(key="open_people_" + entry).click().run()
    assert not app.exception
