"""A single search box, an inspectable interpretation and optional manual controls."""
from datetime import date
import hashlib

import streamlit as st

from src import people, search, web_data


def show_search(today, show_results):
    st.caption("Encuentra recuerdos con tus palabras.")
    with st.form("natural_search"):
        question = st.text_input("Buscar en el diario", max_chars=1000,
            placeholder="Días que he ido a la ofi este último mes", key="search_question")
        natural = st.form_submit_button("Buscar", type="primary")
        literal = st.form_submit_button("Buscar como texto literal")
    st.caption('Prueba «días con mamá el mes pasado» o «días con alegría este año».')

    if natural or literal:
        st.session_state.pop("search_interpretation", None)
        st.session_state.pop("search_literal", None)
        st.session_state.pop("search_notice", None)
        if literal:
            if question.strip():
                st.session_state["search_literal"] = question.strip()
            else:
                st.session_state["search_notice"] = "Escribe el texto que quieres encontrar."
        else:
            try:
                with st.spinner("Interpretando tu búsqueda…"):
                    plan = search.interpret(question, today)
                st.session_state["search_interpretation"] = plan.model_dump(mode="json")
                st.session_state["search_reference_day"] = today.isoformat()
            except search.SearchError as error:
                st.session_state["search_notice"] = str(error)
    if st.session_state.get("search_notice"):
        st.info(st.session_state["search_notice"])
    if "search_literal" in st.session_state:
        st.caption("Texto literal · Todo el diario · Los nombres y alias únicos incluyen sus otras menciones.")
        show_results(web_data.entries(date(1900, 1, 1), today, st.session_state["search_literal"]), "search")
    if "search_interpretation" in st.session_state:
        plan = search.SearchPlan.model_validate(st.session_state["search_interpretation"])
        # Keep the submitted clock fixed while paging or inspecting entries.
        reference = date.fromisoformat(st.session_state["search_reference_day"])
        try:
            plan = search.validate_plan(plan, search.catalog(), reference)
            st.markdown("**He entendido:** " + " · ".join(search.describe(plan, reference)))
            edit_interpretation(plan, reference)
            st.caption("Coincidencias en los datos guardados. Mencionar un lugar no confirma una visita; "
                       "las emociones son las del análisis de la entrada. Todos los criterios se aplican a la misma entrada.")
            rows = search.execute(plan, reference)
            if plan.view == "days":
                days = sorted({row["day"] for row in rows}, reverse=True)
                st.caption(f"{len(days)} días con coincidencias · {len(rows)} entradas")
                if not days:
                    st.info("No hay días para esta búsqueda.")
                else:
                    page = st.selectbox("Página de días", range(1, (len(days) + 9) // 10 + 1), key="search_days_page")
                    for day in days[(page - 1) * 10:page * 10]:
                        st.subheader(date.fromisoformat(day).strftime("%d/%m/%Y"))
                        show_results([r for r in rows if r["day"] == day], "search_day_" + day)
            else:
                show_results(rows, "search")
        except search.SearchError as error:
            st.info(str(error))

    with st.expander("Búsqueda con filtros"):
        manual_search(today, show_results)


def edit_interpretation(plan, reference):
    """Corrections execute directly; there is no second model request."""
    signature = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()[:12]
    start, end = search.resolve_period(plan.period, reference)
    vocabulary = search.catalog()
    with st.expander("Editar interpretación"):
        with st.form("edit_search_" + signature):
            left, right = st.columns(2)
            start = left.date_input("Desde", value=start, min_value=date(1900, 1, 1), max_value=reference,
                                    key="edit_start_" + signature)
            end = right.date_input("Hasta", value=end, min_value=date(1900, 1, 1), max_value=reference,
                                   key="edit_end_" + signature)
            conditions = []
            for i, condition in enumerate(plan.conditions):
                label = search.LABELS.get(condition.field, condition.field)
                key = signature + "_" + str(i)
                if condition.field == "text":
                    values = [v.strip() for v in st.text_area(label + " (una expresión por línea)",
                        value="\n".join(condition.values), key="edit_values_" + key).splitlines() if v.strip()]
                else:
                    values = st.multiselect(label, vocabulary["fields"][condition.field],
                        default=condition.values, key="edit_values_" + key)
                mode = st.selectbox("Coincidencia de " + label, ["any", "all"],
                    index=0 if condition.match == "any" else 1,
                    format_func=lambda v: "Cualquiera" if v == "any" else "Todas", key="edit_match_" + key)
                exclude = st.checkbox("Excluir " + label, value=condition.exclude, key="edit_exclude_" + key)
                if values:
                    conditions.append(search.Condition(field=condition.field, values=values, match=mode, exclude=exclude))
            weekdays = st.multiselect("Días de la semana", range(7), default=plan.weekdays,
                format_func=lambda d: ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][d],
                key="edit_weekdays_" + signature)
            if st.form_submit_button("Aplicar interpretación"):
                if start > end:
                    st.warning("La fecha inicial debe ser anterior o igual a la final.")
                else:
                    edited = plan.model_copy(update={"period": search.DateWindow(kind="absolute", start=start, end=end),
                                                     "conditions": conditions, "weekdays": weekdays})
                    st.session_state["search_interpretation"] = edited.model_dump(mode="json")
                    st.rerun()


def manual_search(today, show_results):
    """A model-independent fallback that stays out of the primary search flow."""
    options = web_data.search_options()
    registry = people.list_people()
    names = {p["entity_id"]: p["name"] + (" (archivada)" if p["archived"] else "") for p in registry}
    fields = {"search_person": list(names), "search_emotion": options["emotions"],
              "search_sentiment": options["sentiment"], "search_source": options["source"],
              "search_status": options["status"],
              **{"search_entity_" + kind: values for kind, values in options["entities"].items()}}
    for key, values in fields.items():
        if st.session_state.get(key) not in values:
            st.session_state.pop(key, None)

    with st.form("manual_search"):
        all_history = st.checkbox("Buscar en todo el diario", value=True, key="search_all_history")
        left, right = st.columns(2)
        start = left.date_input("Desde", value=today.replace(day=1), min_value=date(1900, 1, 1), max_value=today)
        end = right.date_input("Hasta", value=today, min_value=date(1900, 1, 1), max_value=today)
        person_id = st.selectbox("Persona", [None, *names], key="search_person",
            format_func=lambda value: "Sin filtro" if value is None else names[value])
        st.caption("Persona filtra por asociaciones guardadas. La búsqueda natural y literal incluyen también alias únicos del histórico.")

        def choose(label, values, key, labels=None):
            return st.selectbox(label, [None, *values], key=key,
                format_func=lambda value: "Sin filtro" if value is None else (labels or {}).get(value, value))

        entities = {}
        for kind, values in options["entities"].items():
            value = choose("Lugar" if kind == "place" else kind.replace("_", " ").capitalize(),
                           values, "search_entity_" + kind)
            if value is not None:
                entities[kind] = value
        emotion = choose("Emoción", options["emotions"], "search_emotion")
        sentiment = choose("Sentimiento", options["sentiment"], "search_sentiment")
        source = choose("Origen", options["source"], "search_source",
                        {"telegram": "Telegram", "web": "Web", "legacy": "Entrada anterior"})
        status = choose("Estado", options["status"], "search_status",
                        {"pending": "Pendiente", "transcribed": "Transcrita", "completed": "Analizada",
                         "failed": "Error", "empty": "Sin voz"})
        query = st.text_input("Texto adicional", key="manual_search_text")
        submitted = st.form_submit_button("Aplicar filtros")
    if submitted:
        st.session_state.pop("manual_search_criteria", None)
        if all_history:
            start, end = date(1900, 1, 1), today
        if start > end:
            st.warning("La fecha inicial debe ser anterior o igual a la final.")
        else:
            st.session_state["manual_search_criteria"] = dict(start=start, end=end, query=query,
                person_id=person_id, entity_filters=entities, emotion=emotion, sentiment=sentiment,
                source=source, status=status)
    if "manual_search_criteria" in st.session_state:
        show_results(web_data.entries(**st.session_state["manual_search_criteria"]), "manual_search")
