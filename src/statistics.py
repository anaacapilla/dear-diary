"""Statistics over diary dates; no UI or model dependencies."""
from collections import Counter, defaultdict
from datetime import date, timedelta
import math

from src.web_data import read_connection
from src import database


def aggregate(rows, start, end, today):
    days = sorted({date.fromisoformat(r['day']) for r in rows})
    counts = Counter(r['day'] for r in rows)
    span = (end - start).days + 1
    best = run = 0
    previous = None
    for day in days:
        run = run + 1 if previous == day - timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    anchor = min(end, today)
    active = set(days)
    if anchor == today and anchor not in active:
        anchor -= timedelta(days=1)
    current = 0
    while anchor in active:
        current += 1
        anchor -= timedelta(days=1)
    durations = [r['audio_seconds'] for r in rows
                 if r.get('audio_seconds') is not None and math.isfinite(r['audio_seconds'])
                 and r['audio_seconds'] >= 0]
    return dict(total=len(rows), active=len(days), coverage=100 * len(days) / span,
                daily=len(rows) / span, per_active=len(rows) / len(days) if days else 0,
                best=best, current=current, durations=durations, counts=counts)


def day_sentiments(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['day']].append(row)
    result = {}
    for day, items in grouped.items():
        votes = Counter(r['sentiment'] for r in items
                        if r['status'] == 'completed' and r['sentiment'])
        winners = [k for k, v in votes.items() if v == max(votes.values())] if votes else []
        state = winners[0] if len(winners) == 1 else 'empate' if winners else 'pendiente'
        result[day] = (state, len(items), votes)
    return result


def emotion_counts(start, end):
    with read_connection() as conn:
        return [dict(r) for r in conn.execute('''
            SELECT e.emotion AS emotion, COUNT(DISTINCT d.id) AS count
            FROM diary_entries d JOIN entry_emotions ee ON ee.entry_id=d.id
            JOIN emotions e ON e.id=ee.emotion_id
            WHERE d.status='completed'
            AND COALESCE(d.entry_date, diary_date(COALESCE(d.recorded_at,d.received_at,d.created_at))) BETWEEN ? AND ?
            GROUP BY e.emotion ORDER BY count DESC, e.emotion
        ''', (start.isoformat(), end.isoformat()))]


def recover_durations():
    """Read retained audio locally. Missing/invalid files remain unknown."""
    import av
    from src.maintenance import audio_seguro
    with read_connection() as conn:
        rows = conn.execute('SELECT id, audio_path FROM diary_entries WHERE audio_seconds IS NULL AND audio_path IS NOT NULL').fetchall()
    recovered = 0
    for row in rows:
        try:
            path = audio_seguro(row['audio_path'])
            with av.open(str(path)) as container:
                stream = next(iter(container.streams.audio), None)
                seconds = (float(stream.duration * stream.time_base)
                           if stream is not None and stream.duration is not None
                           else container.duration / av.time_base if container.duration is not None else None)
            if seconds is None or not math.isfinite(seconds) or seconds < 0:
                continue
        except (OSError, ValueError, av.error.FFmpegError):
            continue
        with database.conectar() as conn:
            cursor = conn.execute('UPDATE diary_entries SET audio_seconds=? WHERE id=? AND audio_seconds IS NULL', (seconds, row['id']))
            recovered += cursor.rowcount
    return recovered
