"""Local retention and SQLite backups. Does not send personal data anywhere."""
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import database
from src.config import (
    AUDIO_DIR, AUDIO_RETENTION_DAYS, BACKUP_DIR, BACKUP_RETENTION_DAYS,
)

logger = logging.getLogger(__name__)


def audio_seguro(path):
    if not path:
        return None
    root = AUDIO_DIR.resolve()
    resolved = Path(path).resolve()
    if resolved.parent != root:
        raise ValueError("La ruta del audio no está dentro del directorio configurado")
    return resolved


def borrar_audio(path):
    resolved = audio_seguro(path)
    if resolved:
        resolved.unlink(missing_ok=True)
        database.limpiar_referencia_audio(path)


def purgar_audios(now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=AUDIO_RETENTION_DAYS)
    references = {}
    for row in database.rutas_audio():
        path = audio_seguro(row["audio_path"])
        stamp = datetime.fromisoformat(row["received_at"] or row["created_at"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=database.DIARY_TZ)
        references.setdefault(path, []).append(stamp)
    candidates = set(references)
    if AUDIO_DIR.exists():
        audio_extensions = {".ogg", ".mp3", ".wav", ".m4a", ".flac", ".opus", ".aac", ".audio", ".part"}
        candidates.update(p.resolve() for p in AUDIO_DIR.iterdir()
                          if p.is_file() and not p.is_symlink()
                          and p.suffix.lower() in audio_extensions)
    removed = 0
    for path in candidates:
        # Old database references are removed even when a file has already disappeared.
        if not path.exists():
            database.limpiar_referencia_audio(str(path))
            continue
        stamps = references.get(path)
        stamp = min(stamps) if stamps else datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if stamp <= cutoff:
            borrar_audio(path)
            removed += 1
    return removed


def crear_backup(now=None, force=False):
    now = now or datetime.now(timezone.utc)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    today = now.strftime("%Y%m%d")
    if force:
        name = f"diary-{now.strftime('%Y%m%d-%H%M%S-%f')}.sqlite3"
    else:
        name = f"diary-{today}.sqlite3"
    path = BACKUP_DIR / name
    if force or not path.exists():
        database.backup_database(path)
    return path


def mantenimiento(now=None):
    now = now or datetime.now(timezone.utc)
    limpiar_audios_borrados()
    removed = purgar_audios(now)
    crear_backup(now)
    cutoff = (now - timedelta(days=BACKUP_RETENTION_DAYS)).timestamp()
    # Migration backups are next to the database, including with custom BACKUP_DIR.
    roots = {BACKUP_DIR.resolve(), (Path(database.DB_PATH).parent / "backups").resolve()}
    for root in roots:
        if root.exists():
            for pattern in ("diary-*.sqlite3", "migration-*.sqlite3"):
                for path in root.glob(pattern):
                    if not path.is_symlink() and path.stat().st_mtime <= cutoff:
                        path.unlink()
    logger.info("Mantenimiento completado: audios eliminados=%d", removed)


def limpiar_audios_borrados():
    """Retry file deletion after the diary transaction, including after a restart."""
    with database.conectar() as conn:
        paths = [r[0] for r in conn.execute("SELECT path FROM audio_deletions")]
    failures = 0
    for path in paths:
        try:
            with database.conectar() as conn:
                # Legacy diaries may share a file between entries.
                if conn.execute("SELECT 1 FROM diary_entries WHERE audio_path=?", (path,)).fetchone():
                    continue
            resolved = audio_seguro(path)
            if resolved:
                resolved.unlink(missing_ok=True)
                resolved.with_suffix(resolved.suffix + ".part").unlink(missing_ok=True)
            with database.conectar() as conn:
                conn.execute("DELETE FROM audio_deletions WHERE path=?", (path,))
        except (OSError, ValueError):
            failures += 1
            logger.warning("Limpieza de audio borrado pendiente")
    return failures
