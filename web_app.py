"""Launch from the project root: uv run --group web streamlit run web_app.py."""
import calendar
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date, datetime

import streamlit as st

from src.config import DIARY_TZ
from src import database
from src.web_data import details, entries

st.set_page_config(page_title="Dear Diary", page_icon="📖", layout="wide")

STATUS = {"pending": "Pendiente", "transcribed": "Transcrita", "completed": "Analizada",
          "failed": "Error", "empty": "Sin voz"}
MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
          "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def change_month(offset):
    total = st.session_state["calendar_year"] * 12 + st.session_state["calendar_month"] - 1 + offset
    year, month = divmod(total, 12)
    if 1900 <= year <= 2100:
        st.session_state["calendar_year"] = year
        st.session_state["calendar_month"] = month + 1
        st.session_state["diary_day"] = date(year, month + 1, 1)


def edit_entry(item):
    entry_id = item["id"]
    key = "edit_" + entry_id
    if item["status"] not in ("completed", "failed", "empty"):
        st.caption("Podrás editar la transcripción cuando termine el procesamiento.")
        return
    if st.button("Editar entrada", key="open_" + entry_id):
        st.session_state[key] = hashlib.sha256(item["transcription"].encode()).hexdigest()
        st.session_state["text_" + entry_id] = item["transcription"]
    if key not in st.session_state:
        return
    st.caption("Edita la transcripción completa. Se conservará el original y se regenerará el análisis.")
    with st.form("form_" + entry_id):
        text = st.text_area("Transcripción editable", key="text_" + entry_id, height=250, max_chars=12000)
        save = st.form_submit_button("Guardar cambios", type="primary")
        cancel = st.form_submit_button("Cancelar")
    if cancel:
        del st.session_state[key]
        st.rerun()
    if save:
        try:
            database.corregir_transcripcion(entry_id, text, st.session_state[key])
        except ValueError:
            st.error("No se pudo guardar: el texto está vacío o la entrada ha cambiado. Vuelve a abrir la edición.")
            return
        except sqlite3.Error:
            st.error("No se pudo guardar en la base de datos. Puedes volver a intentarlo.")
            return
        del st.session_state[key]
        st.session_state["edit_notice"] = "Cambios guardados. El bot regenerará el análisis; si está apagado, lo hará al arrancar."
        st.rerun()


def show_entry(row, editable=False):
    with st.expander(f"{row['day']} · {STATUS.get(row['status'], row['status'])} · {row['id'][:8]}", expanded=True):
        item = details(row["id"])
        if item is None:
            st.info("Esta entrada ya no está disponible.")
            return
        if item["summary"]:
            st.write(item["summary"])
        st.caption("Sentimiento: " + (item["sentiment"] or "Sin análisis"))
        st.caption("Emociones: " + (", ".join(item["emotions"]) or "—"))
        for kind, label in (("person", "Personas"), ("place", "Lugares")):
            st.caption(label + ": " + (", ".join(e["entity"] for e in item["entities"] if e["entity_type"] == kind) or "—"))
        events = json.loads(item["events"] or "[]")
        if events:
            st.write("Acontecimientos")
            for event in events:
                st.text("• " + event)
        st.write("Transcripción")
        st.text(item["transcription"] or "Todavía no hay transcripción.")
        from src.people_ui import edit_entry_people
        edit_entry_people(item)
        if editable:
            edit_entry(item)
        if item.get("original_transcription") and item.get("transcription_edited_at"):
            with st.expander("Transcripción original"):
                st.text(item["original_transcription"])
        with st.expander("Datos de procesamiento"):
            st.caption("Estado y tiempos de preparación de esta entrada")
            st.write("**Estado:** " + STATUS.get(item["status"], "Desconocido"))
            for field, label in (("received_at", "Recibida"), ("recorded_at", "Enviada")):
                value = item.get(field)
                if value:
                    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=DIARY_TZ)
                    st.write(f"**{label}:** {timestamp.astimezone(DIARY_TZ).strftime('%d/%m/%Y a las %H:%M')}")
            first, second = st.columns(2)
            for column, field, label in (
                (first, "transcription_seconds", "Tiempo de transcripción"),
                (second, "analysis_seconds", "Tiempo de análisis"),
            ):
                seconds = item.get(field)
                column.metric(label, f"{seconds:.1f} s" if seconds is not None else "Pendiente")
            for field, label in (("transcription_model", "Modelo de transcripción"), ("llm_model", "Modelo de análisis")):
                if item.get(field):
                    st.caption(f"{label}: {item[field]}")
            if item.get("error_code"):
                stage = {"download": "descarga del audio", "transcription": "transcripción",
                         "analysis": "análisis", "storage": "guardado"}.get(item["error_code"], "procesamiento")
                st.warning(f"No se pudo completar la etapa de {stage}. Puedes reintentar desde Telegram.")


def show_results(rows, prefix):
    if not rows:
        st.info("No hay entradas para esta selección.")
        return
    st.caption(f"{len(rows)} entradas")
    pages = (len(rows) + 9) // 10
    page = st.selectbox("Página", range(1, pages + 1), key=prefix + "_page")
    for row in rows[(page - 1) * 10:page * 10]:
        show_entry(row, editable=prefix == "entries")


def main():
    today = datetime.now(DIARY_TZ).date()
    st.sidebar.title("📖 Dear Diary")
    st.sidebar.caption("Tu espacio personal")
    st.markdown("""<style>
    .stApp { background-color: #FFF8FB; color: #503746; }
    [data-testid="stSidebar"] { background-color: #F7E6EF; }
    [data-testid="stSidebar"] button[kind="primary"] {
        background: #A84970; border-color: #A84970; color: white;
    }
    [data-testid="stSidebar"] button[kind="tertiary"] { justify-content: flex-start; }
    [data-testid="stExpander"] { background: #FFFCFE; border-color: #EAC9D9; border-radius: 14px; }
    button[kind="secondary"] { border-color: #E4BBD0; background: #FFF4F9; color: #703C57; }
    button[kind="secondary"]:hover { border-color: #A84970; background: #F7E0ED; color: #703C57; }
    .calendar-heading { text-align: center; margin: 0; line-height: 2.5rem; font-size: 1.45rem; font-weight: 650; color: #85425F; }
    </style>""", unsafe_allow_html=True)
    section = st.session_state.setdefault("section", "Entradas")
    for label in ("Entradas", "Buscador", "Estadísticas", "Mis personas"):
        if st.sidebar.button(label, key="nav_" + label, use_container_width=True,
                             type="primary" if section == label else "tertiary"):
            st.session_state["section"] = label
            st.rerun()
    if st.sidebar.button("Actualizar datos", help="Recarga los datos guardados y descarta los cambios sin guardar de Mis personas."):
        for key in ("people_baseline", "people_editor", "people_proposals", "people_review"):
            st.session_state.pop(key, None)
    st.sidebar.caption("Telegram recibe tus audios. Aquí puedes revisar tu diario.")
    st.title(section)
    if section == "Mis personas":
        from src.people_ui import show_people
        show_people()
        return
    if "edit_notice" in st.session_state:
        st.success(st.session_state.pop("edit_notice"))
    if section == "Entradas":
        left, right = st.columns(2)
        st.session_state.setdefault("calendar_year", today.year)
        st.session_state.setdefault("calendar_month", today.month)
        year = left.number_input("Año", min_value=1900, max_value=2100, step=1, key="calendar_year")
        month = right.selectbox("Mes", range(1, 13), key="calendar_month", format_func=lambda m: MONTHS[m - 1])
        previous, heading, following = st.columns([1, 5, 1])
        previous.button("←", help="Mes anterior", on_click=change_month, args=(-1,),
                        disabled=(year, month) == (1900, 1), use_container_width=True)
        heading.markdown(f'<h3 class="calendar-heading">{MONTHS[month - 1]} {year}</h3>', unsafe_allow_html=True)
        following.button("→", help="Mes siguiente", on_click=change_month, args=(1,),
                         disabled=(year, month) == (2100, 12), use_container_width=True)
        rows = entries(date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))
        counts = Counter(row["day"] for row in rows)
        st.caption("● indica un día con entradas. Las fechas corresponden al envío del audio.")
        for column, label in zip(st.columns(7), ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]):
            column.write(label)
        for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
            for column, day in zip(st.columns(7), week):
                if day:
                    selected = date(year, month, day)
                    count = counts[selected.isoformat()]
                    if column.button(f"{day}" + (f" ● {count}" if count else ""), key=selected.isoformat(), use_container_width=True):
                        st.session_state["diary_day"] = selected
        selected = st.session_state.get("diary_day", today)
        if (selected.year, selected.month) != (year, month):
            selected = date(year, month, 1)
        st.subheader(selected.strftime("%d/%m/%Y"))
        show_results([r for r in rows if r["day"] == selected.isoformat()], "entries")
    else:
        left, right = st.columns(2)
        start = left.date_input("Desde", value=today.replace(day=1))
        end = right.date_input("Hasta", value=today)
        if start > end:
            st.warning("La fecha inicial debe ser anterior o igual a la final.")
            return
        if section == "Buscador":
            st.info("Primera versión: búsqueda literal en transcripciones, resúmenes y acontecimientos. Las preguntas en lenguaje natural se incorporarán en la siguiente fase.")
            query = st.text_input("Buscar en el diario", placeholder="Por ejemplo: Marta, oficina, paseo…")
            if query.strip():
                show_results(entries(start, end, query), "search")
        else:
            rows = entries(start, end)
            a, b, c = st.columns(3)
            a.metric("Entradas", len(rows))
            b.metric("Días con entradas", len({r["day"] for r in rows}))
            c.metric("Analizadas", sum(r["status"] == "completed" for r in rows))
            if rows:
                st.subheader("Entradas por día")
                counts = Counter(r["day"] for r in rows)
                st.bar_chart(dict(sorted(counts.items())))
                st.subheader("Estado del procesamiento")
                st.dataframe([{"Estado": STATUS.get(k, k), "Entradas": v}
                              for k, v in Counter(r["status"] for r in rows).items()], hide_index=True)
                st.subheader("Sentimiento de las entradas analizadas")
                st.dataframe([{"Sentimiento": k, "Entradas": v} for k, v in Counter(
                    r["sentiment"] for r in rows if r["status"] == "completed" and r["sentiment"]
                ).items()], hide_index=True)
                st.caption("Estas cifras describen entradas registradas, no acontecimientos únicos ni una evaluación clínica.")
            else:
                st.info("No hay entradas en este periodo.")


try:
    from src.web_data import prepare_database
    prepare_database()
    main()
except (sqlite3.Error, ValueError):
    st.error("No se puede abrir o actualizar el diario. Comprueba DATABASE_PATH y los permisos del archivo. Si todavía no existe un diario, arranca el bot una vez para crearlo.")
