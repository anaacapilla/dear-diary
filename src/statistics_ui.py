"""Statistics page and accessible year-in-pixels calendar."""
import calendar
from datetime import date
from html import escape

import streamlit as st

from src.statistics import aggregate, day_sentiments, emotion_counts, recover_durations
from src.web_data import entries, read_connection

MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
COLORS = {'positivo': '#77bc9a', 'negativo': '#c58cae', 'neutral': '#e2cf93',
          'mezclado': '#95b3d5', 'empate': '#b2a1cf', 'pendiente': '#d3d3d3',
          'sin entradas': '#faf5f7', 'futuro': '#ffffff'}


def duration(seconds):
    if seconds is None:
        return 'Sin datos'
    seconds = round(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours} h {minutes:02d} min' if hours else f'{minutes} min {seconds:02d} s'


def pixels_html(year, rows, today, mode):
    states = day_sentiments(rows)
    html = ['<div style="width:100%;box-sizing:border-box;padding:0 clamp(12px,3vw,48px);overflow-x:auto"><table style="width:100%;table-layout:fixed;border-collapse:separate;border-spacing:3px"><thead><tr><th style="width:28px;padding:0;text-align:center">Día</th>']
    html.extend(f'<th style="font-size:12px;padding:0;text-align:center">{month}</th>' for month in MONTHS)
    html.append('</tr></thead><tbody>')
    for day in range(1, 32):
        html.append(f'<tr><th style="font-size:10px;padding:0;text-align:center">{day}</th>')
        for month in range(1, 13):
            if day > calendar.monthrange(year, month)[1]:
                html.append('<td></td>')
                continue
            dt = date(year, month, day)
            state, count, votes = states.get(dt.isoformat(), ('futuro' if dt > today else 'sin entradas', 0, {}))
            color = COLORS.get(state, COLORS['pendiente'])
            if mode == 'Número de entradas' and count:
                color = '#e1bbcf' if count == 1 else '#ba7599' if count <= 3 else '#85425f'
            background = f'repeating-linear-gradient(45deg,{color},{color} 3px,#faf5f7 3px,#faf5f7 5px)' if state == 'empate' and mode == 'Sentimiento' else color
            title = escape(f'{dt:%d/%m/%Y} · {count} entradas · {state} · ' + ', '.join(f'{k}: {v}' for k, v in votes.items()), quote=True)
            html.append(f'<td style="padding:0"><a target="_self" href="?stats_day={dt.isoformat()}" title="{title}" aria-label="{title}" style="display:block;width:min(100%,32px);aspect-ratio:1 / 1;margin:0 auto;box-sizing:border-box;border:1px solid #ddced5;border-radius:3px;background:{background}"></a></td>')
        html.append('</tr>')
    html.append('</tbody></table></div>')
    return ''.join(html)


def show_statistics(today, show_results):
    with read_connection() as conn:
        first = conn.execute('SELECT MIN(diary_date(COALESCE(recorded_at,received_at,created_at))) FROM diary_entries').fetchone()[0]
    period = st.selectbox('Periodo', ['Este mes', 'Este año', 'Todo', 'Personalizado'], key='stats_period')
    start, end = (today.replace(day=1), today)
    if period == 'Este año':
        start = today.replace(month=1, day=1)
    elif period == 'Todo':
        start = min(date.fromisoformat(first), today) if first else today
    elif period == 'Personalizado':
        left, right = st.columns(2)
        start = left.date_input('Desde', value=start, max_value=today, key='stats_start')
        end = right.date_input('Hasta', value=today, max_value=today, key='stats_end')
    if start > end:
        st.warning('La fecha inicial debe ser anterior o igual a la final.')
        return
    rows = entries(start, end)
    data = aggregate(rows, start, end, today)
    st.caption(f'{start:%d/%m/%Y} — {end:%d/%m/%Y} · Fechas de envío en la zona horaria del diario.')
    for col, label, value in zip(st.columns(3), ['Entradas', 'Días con entradas', 'Días activos'], [data['total'], data['active'], f"{data['coverage']:.1f} %"]):
        col.metric(label, value)
    for col, label, value in zip(st.columns(4), ['Entradas / día', 'Entradas / día activo', 'Racha actual', 'Mejor racha'], [f"{data['daily']:.2f}", f"{data['per_active']:.2f}", f"{data['current']} días", f"{data['best']} días"]):
        col.metric(label, value)
    st.caption('Las medias incluyen todos los días del periodo o solo los días con entradas, respectivamente. Las rachas se limitan al periodo; la actual termina hoy o ayer, o en el último día si es histórico.')
    if not rows:
        st.info('No hay entradas en este periodo.')
    st.subheader('Tus audios')
    values = data['durations']
    for col, label, value in zip(st.columns(3), ['Tiempo grabado conocido', 'Duración media', 'Audio más largo'], [sum(values) if values else None, sum(values)/len(values) if values else None, max(values) if values else None]):
        col.metric(label, duration(value))
    st.caption(f'Duración conocida de {len(values)} de {len(rows)} entradas. Las duraciones desconocidas no cuentan como cero.')
    if st.button('Recuperar duraciones antiguas', help='Lee los audios conservados en este equipo. Los archivos borrados o ilegibles quedan sin dato.'):
        with st.spinner('Leyendo los audios conservados…'):
            recovered = recover_durations()
        st.session_state['duration_notice'] = f'Duraciones recuperadas: {recovered}.'
        st.rerun()
    if 'duration_notice' in st.session_state:
        st.success(st.session_state.pop('duration_notice'))
    if rows:
        st.subheader('Frecuencia')
        st.bar_chart([{'Fecha': date.fromordinal(n).isoformat(), 'Entradas': data['counts'][date.fromordinal(n).isoformat()]} for n in range(start.toordinal(), end.toordinal()+1)], x='Fecha', y='Entradas')
    st.subheader('Emociones frecuentes')
    emotions = emotion_counts(start, end)
    analyzed = sum(r['status'] == 'completed' for r in rows)
    if emotions:
        st.bar_chart([{'Emoción': e['emotion'], 'Entradas': e['count']} for e in emotions], x='Emoción', y='Entradas', horizontal=True)
        st.dataframe([{'Emoción': e['emotion'], 'Entradas': e['count'], '% de analizadas': round(100*e['count']/analyzed, 1)} for e in emotions], hide_index=True)
    else:
        st.info('Todavía no hay emociones analizadas en este periodo.')
    st.caption('Una entrada puede tener varias emociones. Los sentimientos y emociones describen el contenido analizado, no una evaluación clínica.')


def show_year_pixels(today, show_results):
    left, right = st.columns(2)
    year = left.number_input('Año', min_value=1900, max_value=2100, value=today.year, key='stats_year')
    mode = right.selectbox('Colorear por', ['Sentimiento', 'Número de entradas'])
    year_rows = entries(date(year, 1, 1), date(year, 12, 31))
    st.markdown(pixels_html(year, year_rows, today, mode), unsafe_allow_html=True)
    legend = COLORS.items() if mode == 'Sentimiento' else [('1 entrada', '#e1bbcf'), ('2–3 entradas', '#ba7599'), ('4 o más', '#85425f'), ('sin entradas', '#faf5f7'), ('futuro', '#ffffff')]
    st.markdown(' · '.join(f'<span style="color:{color}">■</span> {label.capitalize()}' for label, color in legend), unsafe_allow_html=True)
    st.caption('Calendario del año completo. Cada audio analizado aporta un voto; los empates llevan trama. Pasa el cursor para ver el detalle o pulsa un día para ver sus entradas.')
    selected = st.query_params.get('stats_day')
    if selected:
        try:
            selected = date.fromisoformat(selected)
        except ValueError:
            selected = None
        if selected:
            st.subheader(f'Entradas del {selected:%d/%m/%Y}')
            show_results(entries(selected, selected), 'stats')
