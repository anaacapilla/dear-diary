"""Streamlit views for the people registry and historical review."""
import hashlib
import sqlite3

import pandas as pd
import streamlit as st

from src import people

LABELS = {"name": "Nombre canónico (completo)", "aliases": "Alias (separados por coma o ;)",
          "origin": "De dónde es", "residence": "Dónde vive", "relationship": "Relación",
          "known_from": "De qué la conozco", "groups": "Grupos", "archived": "Archivada"}


def refresh(message):
    st.session_state.pop("people_baseline", None)
    st.session_state.pop("people_editor", None)
    st.session_state.pop("people_proposals", None)
    st.session_state.pop("people_review", None)
    st.session_state["people_notice"] = message
    st.rerun()


def show_people():
    if "people_notice" in st.session_state:
        st.success(st.session_state.pop("people_notice"))
    if "people_baseline" not in st.session_state:
        st.session_state["people_baseline"] = people.list_people()
    baseline = st.session_state["people_baseline"]
    registry, organize, history = st.tabs(["Mis personas", "Por organizar", "Propuestas del histórico"])
    with registry:
        st.caption("Añade filas al final de la tabla. Solo el nombre completo es obligatorio. Separa los alias con comas o punto y coma: mamá, mami, mi madre. Archivar conserva los vínculos y los alias.")
        query = st.text_input("Buscar personas", placeholder="Nombre, alias, relación, lugar o grupo")
        groups = sorted({g for p in baseline for g in p["groups"]})
        group = st.selectbox("Filtrar por grupo", ["Todos", *groups])
        with st.form("people_registry_form"):
            rows = [{"entity_id": p["entity_id"], **{k: "; ".join(p[k]) if k == "aliases" else p[k] for k in LABELS}} for p in baseline]
            # A typed empty frame keeps all editor columns available on first use.
            frame = pd.DataFrame(rows, columns=["entity_id", *LABELS])
            frame["archived"] = frame["archived"].astype(bool)
            edited = st.data_editor(frame, key="people_editor", hide_index=True,
                num_rows="dynamic", width="stretch",
                column_config={"entity_id": None, **{k: st.column_config.TextColumn(label) for k, label in LABELS.items() if k not in ("groups", "archived")},
                    "groups": st.column_config.MultiselectColumn("Grupos", options=groups, accept_new_options=True),
                    "archived": st.column_config.CheckboxColumn("Archivada", default=False)},
                disabled=["entity_id"])
            save = st.form_submit_button("Guardar cambios", type="primary")
            discard = st.form_submit_button("Descartar cambios")
        if query.strip() or group != "Todos":
            st.caption("Resultados de los filtros (datos guardados). La tabla editable superior conserva todas las filas para guardar sin pérdidas.")
            filtered = [p for p in baseline if (group == "Todos" or group in p["groups"])
                        and (not query.strip() or people.normalize(query) in people.normalize(str(p)))]
            st.dataframe([{LABELS[k]: "; ".join(p[k]) if isinstance(p[k], list) else p[k] for k in LABELS} for p in filtered], hide_index=True)
        if discard:
            refresh("Cambios descartados. Se han recargado los datos guardados.")
        if save:
            try:
                records = edited.where(edited.notna(), None).to_dict("records")
                for row in records:
                    row["aliases"] = people.split_aliases(row["aliases"] or "")
                    row["groups"] = list(row["groups"] or [])
                people.save_people(records, people.snapshot(baseline))
            except (ValueError, sqlite3.IntegrityError) as error:
                st.error(str(error))
            else:
                refresh("Personas guardadas. Puedes buscar propuestas para las entradas anteriores.")
    with organize:
        st.caption("Menciones sin identificar. Si no hay una pista explícita y única, puedes elegir a quién se refieren desde la propia entrada, con Editar personas.")
        candidates = people.candidates()
        if not candidates:
            st.info("No hay menciones por organizar.")
        else:
            st.dataframe(candidates, column_config={"mention": "Mención", "entries_count": "Entradas"}, hide_index=True)
            mention = st.selectbox("Mención que quieres organizar", [c["mention"] for c in candidates])
            targets = {p["entity_id"]: p["name"] for p in baseline}
            with st.form("organize_person"):
                target = st.selectbox("Asociar como alias a", ["new", *targets], format_func=lambda value: "Crear una persona" if value == "new" else targets[value])
                name = st.text_input("Nombre completo de la nueva persona")
                submit = st.form_submit_button("Guardar persona o alias")
            if submit:
                rows = [{**p, "aliases": list(p["aliases"])} for p in baseline]
                if target == "new":
                    rows.append({"name": name, "aliases": [mention], "groups": []})
                else:
                    next(p for p in rows if p["entity_id"] == target)["aliases"].append(mention)
                try:
                    people.save_people(rows, people.snapshot(baseline))
                except (ValueError, sqlite3.IntegrityError) as error:
                    st.error(str(error))
                else:
                    refresh("Alias guardado. Revisa y acepta las asociaciones en Propuestas del histórico.")
    with history:
        st.caption("Busca nombres, alias y pistas explícitas de las fichas en las transcripciones anteriores. Selecciona las asociaciones que quieras aceptar. No se regeneran los audios ni los análisis.")
        if st.button("Buscar propuestas", key="find_people_proposals"):
            st.session_state.pop("people_review", None)
            st.session_state["people_proposals"] = people.historical_proposals()
        proposals = st.session_state.get("people_proposals")
        if proposals is not None:
            if not proposals:
                st.info("No hay asociaciones únicas nuevas que proponer.")
            else:
                with st.form("accept_people_proposals"):
                    review = st.data_editor([{"Aceptar": False, "Mención": p["mention"], "Persona": p["name"], "Entrada": p["entry_id"], "Transcripción": p["text"]} for p in proposals],
                        hide_index=True, disabled=["Mención", "Persona", "Entrada", "Transcripción"], key="people_review")
                    accept = st.form_submit_button("Aceptar seleccionadas", type="primary")
                if accept:
                    selected = [p for p, row in zip(proposals, review) if row["Aceptar"]]
                    if not selected:
                        st.warning("Selecciona al menos una propuesta.")
                    else:
                        try:
                            people.accept_proposals(selected)
                        except ValueError as error:
                            st.error(str(error))
                        else:
                            refresh(f"{len(selected)} asociaciones aceptadas.")


def edit_entry_people(item):
    entry_id = item["id"]
    key = "entry_people_" + entry_id
    if item.get("processing") or item["status"] not in ("completed", "failed", "empty"):
        return
    if st.button("Editar personas", key="open_people_" + entry_id):
        st.session_state[key] = people.entry_people(entry_id)
    if key not in st.session_state:
        return
    state = st.session_state[key]
    version = hashlib.sha256(people.snapshot(state).encode()).hexdigest()[:16]
    registry = people.list_people()
    names = {p["entity_id"]: p["name"] + (" (archivada)" if p["archived"] else "") for p in registry}
    options = [None, *names]
    st.caption("Elige la persona para cada mención. La selección se aplica a todas las apariciones de esa expresión en esta entrada y no crea alias globales. Puedes dejar una mención sin identificar.")
    with st.form("entry_people_form_" + entry_id):
        assignments = {}
        for number, mention in enumerate(state["mentions"]):
            current = mention["person_id"]
            assignments[mention["mention"]] = st.selectbox(
                f'«{mention["mention"]}»', options, index=options.index(current) if current in options else 0,
                format_func=lambda value: "Sin identificar" if value is None else names[value],
                key=f"{key}_{number}_{version}")
        extra = st.text_input("Añadir una mención que no se detectó", placeholder="Copia el nombre o expresión de la transcripción", key=key + "_extra_" + version)
        extra_person = st.selectbox("Persona de la mención añadida", options,
            format_func=lambda value: "Sin identificar" if value is None else names[value], key=key + "_extra_person_" + version)
        save = st.form_submit_button("Guardar personas", type="primary")
        cancel = st.form_submit_button("Cancelar edición de personas")
    if cancel:
        del st.session_state[key]
        st.rerun()
    if save:
        if extra.strip():
            assignments[extra.strip()] = extra_person
        try:
            people.correct_entry_people(entry_id, assignments, people.snapshot(state))
        except (ValueError, sqlite3.Error) as error:
            st.error(str(error))
        else:
            del st.session_state[key]
            st.session_state.pop("people_proposals", None)
            st.session_state["edit_notice"] = "Personas de la entrada actualizadas. La transcripción y los alias globales se conservan."
            st.rerun()
