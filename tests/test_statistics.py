from datetime import date

from src.statistics import aggregate, day_sentiments, emotion_counts, recover_durations
from src.statistics_ui import pixels_html


def test_means_streaks_missing_durations_and_votes():
    rows = [dict(day=day, status=status, sentiment=sentiment, audio_seconds=seconds)
            for day, status, sentiment, seconds in [
                ('2026-10-20', 'completed', 'positivo', 60),
                ('2026-10-20', 'completed', 'negativo', None),
                ('2026-10-21', 'pending', None, 120),
                ('2026-10-23', 'completed', 'mezclado', None)]]
    data = aggregate(rows, date(2026, 10, 20), date(2026, 10, 24), date(2026, 10, 24))
    assert data['daily'] == .8
    assert data['per_active'] == 4 / 3
    assert data['coverage'] == 60
    assert data['best'] == 2
    assert data['current'] == 1
    assert data['durations'] == [60, 120]
    states = day_sentiments(rows)
    assert states['2026-10-20'][0] == 'empate'
    assert states['2026-10-21'][0] == 'pendiente'
    assert states['2026-10-23'][0] == 'mezclado'
    assert aggregate([], date(2026, 1, 1), date(2026, 1, 1), date(2026, 10, 24))['current'] == 0


def test_duration_storage_emotions_and_retained_audio(db, tmp_path, monkeypatch):
    import wave
    from src import maintenance
    monkeypatch.setattr(maintenance, 'AUDIO_DIR', tmp_path)
    path = tmp_path / 'voice.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\0\0' * 16000)
    saved = db.guardar_entrada('Hola', recorded_at='2026-10-20T12:00:00+02:00',
                               audio_path=str(path), emotions=['alegria', 'gratitud'])
    known = db.guardar_entrada('Otro', audio_seconds=90)
    missing = db.guardar_entrada('Sin archivo', audio_path=str(tmp_path / 'missing.ogg'))
    assert recover_durations() == 1
    assert db.obtener_entrada(saved)['audio_seconds'] == 2
    assert db.obtener_entrada(known)['audio_seconds'] == 90
    assert db.obtener_entrada(missing)['audio_seconds'] is None
    assert recover_durations() == 0
    assert emotion_counts(date(2026, 10, 20), date(2026, 10, 20)) == [
        {'emotion': 'alegria', 'count': 1}, {'emotion': 'gratitud', 'count': 1}]


def test_pixels_leap_year_and_clickable_days():
    html = pixels_html(2024, [], date(2024, 2, 1), 'Sentimiento')
    assert html.count('href=') == 366
    assert '?stats_day=2024-02-29' in html
    assert 'futuro' in html


def test_statistics_ui_populated_and_day_selection(db):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    db.guardar_entrada('Hola', recorded_at='2026-10-20T12:00:00+02:00',
                      audio_seconds=60, sentiment='positivo', emotions=['alegria'])
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / 'web_app.py', default_timeout=20)
    app.query_params['stats_day'] = '2026-10-20'
    app.run()
    assert not app.exception
    assert app.title[0].value == 'Tu año en píxeles'
    assert any('20/10/2026' in heading.value for heading in app.subheader)
    app.sidebar.button(key='nav_Estadísticas').click().run()
    app.selectbox(key='stats_period').select('Todo').run()
    assert not app.exception
    assert app.metric[0].value == '1'
    assert not any('píxeles' in heading.value for heading in app.subheader)
