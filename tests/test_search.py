import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src import people, search


TODAY = date(2026, 10, 24)


def test_search_import_and_request_with_config_cached_before_search_settings(db, monkeypatch):
    import importlib
    import ollama
    from src import config

    client = Mock()
    client.chat.return_value = response({"period": {"kind": "previous", "unit": "day"}})
    factory = Mock(return_value=client)
    with monkeypatch.context() as patch:
        patch.delattr(config, "SEARCH_MODEL")
        patch.delattr(config, "SEARCH_TIMEOUT_SECONDS")
        patch.setenv("SEARCH_MODEL", "test-search-model")
        patch.setenv("SEARCH_TIMEOUT_SECONDS", "7")
        patch.setattr(ollama, "Client", factory)
        importlib.reload(search)
        # Use a free-form question to exercise the model path, not the grammar.
        plan = search.interpret("las entradas del día anterior a hoy", TODAY)
        assert plan.period.kind == "previous"
        factory.assert_called_once_with(timeout=7.0)
        assert client.chat.call_args.kwargs["model"] == "test-search-model"


def test_standard_office_question_works_without_model(db, monkeypatch):
    import ollama
    db.guardar_entrada("Oficina", entry_date="2026-10-23", entities=[("place", "oficina")])
    client = Mock(side_effect=AssertionError("This common query must not load a model"))
    monkeypatch.setattr(ollama, "Client", client)
    plan = search.interpret("días que he ido a la ofi este último mes", TODAY)
    assert search.resolve_period(plan.period, TODAY) == (date(2026, 9, 24), TODAY)
    assert plan.conditions[0].values == ["oficina"]
    assert plan.view == "days"
    assert len(search.execute(plan, TODAY)) == 1
    client.assert_not_called()
    # Additional words/negations may not be silently ignored by the grammar.
    assert search.standard_plan("días que no he ido a la ofi este último mes", search.catalog(), TODAY) is None
    assert search.standard_plan("días que he ido a la ofi con Ana este último mes", search.catalog(), TODAY) is None


@pytest.mark.parametrize("kind,unit,count,today,expected", [
    ("rolling", "month", 1, TODAY, (date(2026, 9, 24), TODAY)),
    ("previous", "month", 1, TODAY, (date(2026, 9, 1), date(2026, 9, 30))),
    ("current", "month", 1, TODAY, (date(2026, 10, 1), TODAY)),
    ("rolling", "month", 1, date(2024, 3, 31), (date(2024, 2, 29), date(2024, 3, 31))),
    ("rolling", "year", 1, date(2024, 2, 29), (date(2023, 2, 28), date(2024, 2, 29))),
    ("previous", "week", 1, TODAY, (date(2026, 10, 12), date(2026, 10, 18))),
    ("previous", "day", 1, TODAY, (date(2026, 10, 23), date(2026, 10, 23))),
    ("rolling", "day", 30, TODAY, (date(2026, 9, 25), TODAY)),
])
def test_relative_dates(kind, unit, count, today, expected):
    assert search.resolve_period(search.DateWindow(kind=kind, unit=unit, count=count), today) == expected


def response(plan):
    return SimpleNamespace(message=SimpleNamespace(content=json.dumps(plan)))


def test_question_clock_and_catalog_are_sent_but_not_diary_content(db):
    db.guardar_entrada("SECRETO PRIVADO", entry_date="2026-10-01", entities=[("place", "oficina")])
    client = Mock()
    client.chat.return_value = response({"period": {"kind": "rolling", "unit": "month", "count": 1},
        "conditions": [{"field": "entity:place", "values": ["ofi"]}], "view": "days"})
    plan = search.interpret("días que he ido a la ofi este último mes", TODAY, client=client)
    assert plan.conditions[0].values == ["oficina"]
    assert search.resolve_period(plan.period, TODAY) == (date(2026, 9, 24), TODAY)
    content = json.loads(client.chat.call_args.kwargs["messages"][1]["content"])
    assert content["current_date"] == "2026-10-24"
    assert content["timezone"] == "Europe/Madrid"
    assert "SECRETO" not in json.dumps(client.chat.call_args.kwargs)


def test_execute_combination_and_or_exclusion_weekdays(db):
    target = db.guardar_entrada("Paseo", entry_date="2026-10-23", entities=[("place", "oficina")], emotions=["alegria"])
    db.guardar_entrada("Otro", entry_date="2026-10-24", entities=[("place", "Madrid")], emotions=["alegria"])
    db.guardar_entrada("Otro", entry_date="2026-10-23", entities=[("place", "oficina")], emotions=["tristeza"])
    plan = search.SearchPlan(period=search.DateWindow(kind="rolling", unit="month"), weekdays=[4],
        conditions=[search.Condition(field="entity:place", values=["oficina", "Madrid"]),
                    search.Condition(field="emotion", values=["tristeza"], exclude=True)])
    assert [r["id"] for r in search.execute(plan, TODAY)] == [target]


def test_people_aliases_include_history_and_respect_manual_decisions(db):
    historical = db.guardar_entrada("Mami llamó", entry_date="2024-01-02")
    people.save_people([{"name": "Ana García", "aliases": ["mami"]}], people.snapshot(people.list_people()))
    recent = db.guardar_entrada("Mami llamó", entry_date="2026-10-23")
    state = people.entry_people(recent)
    people.correct_entry_people(recent, {"Mami": None}, people.snapshot(state))
    plan = search.SearchPlan(period=search.DateWindow(kind="all"),
        conditions=[search.Condition(field="person", values=["mami"])])
    assert {r["id"] for r in search.execute(plan, TODAY)} == {historical}


def test_ambiguous_person_never_silently_selects_one(db):
    people.save_people([{"name": "Ana García", "aliases": ["Ana"]},
                        {"name": "Ana López", "aliases": ["Ana"]}], people.snapshot(people.list_people()))
    plan = search.SearchPlan(period=search.DateWindow(kind="all"),
        conditions=[search.Condition(field="person", values=["Ana"])])
    with pytest.raises(search.SearchError, match="única"):
        search.execute(plan, TODAY)


@pytest.mark.parametrize("payload", ["invalid json", '{"sql":"DELETE FROM diary_entries"}'])
def test_invalid_output_fails_without_logging_private_data(db, payload, caplog):
    client = Mock()
    client.chat.return_value = SimpleNamespace(message=SimpleNamespace(content=payload))
    with pytest.raises(search.SearchError, match="interpretar"):
        search.interpret("MI PREGUNTA PRIVADA", TODAY, client=client)
    assert "MI PREGUNTA" not in caplog.text
    assert payload not in caplog.text


def test_model_failure_and_clarification_do_not_search(db):
    client = Mock()
    client.chat.side_effect = ConnectionError("private response")
    with pytest.raises(search.SearchError, match="Ollama"):
        search.interpret("ayer", TODAY, client=client)
    client.chat.side_effect = None
    client.chat.return_value = response({"period": {"kind": "all"}, "clarification": "¿Qué Ana?"})
    with pytest.raises(search.SearchError, match="Qué Ana"):
        search.interpret("con Ana", TODAY, client=client)


def test_unknown_value_is_not_dropped_and_text_is_literal(db):
    target = db.guardar_entrada("100% agradable", entry_date="2026-10-23")
    plan = search.SearchPlan(period=search.DateWindow(kind="all"),
        conditions=[search.Condition(field="text", values=["%", "_"])])
    assert [r["id"] for r in search.execute(plan, TODAY)] == [target]
    plan.conditions = [search.Condition(field="entity:place", values=["inventado"])]
    with pytest.raises(search.SearchError):
        search.execute(plan, TODAY)


def test_future_dates_and_unknown_fields_rejected(db):
    plan = search.SearchPlan(period=search.DateWindow(kind="absolute", start=TODAY, end=date(2027, 1, 1)))
    with pytest.raises(search.SearchError):
        search.execute(plan, TODAY)
    plan.period = search.DateWindow(kind="all")
    plan.conditions = [search.Condition(field="SQL", values=["DROP TABLE"])]
    with pytest.raises(search.SearchError):
        search.execute(plan, TODAY)


def test_multiple_people_and_same_entry_semantics(db):
    people.save_people([{"name": "Ana"}, {"name": "Luis"}], people.snapshot(people.list_people()))
    both = db.guardar_entrada("Ana y Luis", entry_date="2026-10-23")
    db.guardar_entrada("Ana", entry_date="2026-10-23")
    db.guardar_entrada("Luis", entry_date="2026-10-23")
    plan = search.SearchPlan(period=search.DateWindow(kind="all"),
        conditions=[search.Condition(field="person", values=["Ana", "Luis"], match="all")])
    assert {r["id"] for r in search.execute(plan, TODAY)} == {both}


def test_natural_ui_submits_once_and_can_edit_without_model(db, monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    db.guardar_entrada("Fui a la oficina", entry_date="2026-10-23", entities=[("place", "oficina")])
    interpreter = Mock(return_value=search.SearchPlan(
        period=search.DateWindow(kind="rolling", unit="month"), view="days",
        conditions=[search.Condition(field="entity:place", values=["oficina"])]))
    monkeypatch.setattr(search, "interpret", interpreter)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "web_app.py", default_timeout=20)
    app.session_state["section"] = "Buscador"
    app.run()
    interpreter.assert_not_called()
    assert not app.exception
    assert len(app.expander) == 1
    app.text_input(key="search_question").input("días de ofi este último mes")
    next(b for b in app.button if b.label == "Buscar").click().run()
    assert not app.exception
    assert any("Lugar: oficina" in m.value for m in app.markdown)
    assert any("1 días con coincidencias" in c.value for c in app.caption)
    next(b for b in app.button if b.label == "Aplicar interpretación").click().run()
    assert not app.exception
    assert interpreter.call_count == 1
    interpreter.side_effect = search.SearchError("¿Qué oficina?")
    next(b for b in app.button if b.label == "Buscar").click().run()
    assert not app.exception
    assert any("Qué oficina" in item.value for item in app.info)
    assert not any("Lugar: oficina" in m.value for m in app.markdown)
