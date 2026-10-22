import os
from datetime import datetime, timedelta, timezone

import pytest

from src import maintenance


def test_retention_deletes_old_audio_but_keeps_entries(db, tmp_path, monkeypatch):
    root = tmp_path / "audios"
    root.mkdir()
    monkeypatch.setattr(maintenance, "AUDIO_DIR", root)
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    old, new = root / "old.ogg", root / "new.ogg"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    entry = db.guardar_entrada("old", audio_path=str(old),
                              received_at=(now - timedelta(days=7)).isoformat())
    db.guardar_entrada("new", audio_path=str(new),
                       received_at=(now - timedelta(days=6)).isoformat())
    assert maintenance.purgar_audios(now) == 1
    assert not old.exists() and new.exists()
    assert db.obtener_entrada(entry)["transcription"] == "old"
    assert db.obtener_entrada(entry)["audio_path"] is None


def test_old_orphan_files_are_purged(db, tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "AUDIO_DIR", tmp_path)
    file = tmp_path / "orphan.ogg"
    file.write_bytes(b"old")
    stamp = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
    os.utime(file, (stamp, stamp))
    maintenance.purgar_audios()
    assert not file.exists()
    assert db.DB_PATH.exists()


def test_no_deletion_outside_audio_directory(tmp_path, monkeypatch):
    root = tmp_path / "audios"
    root.mkdir()
    monkeypatch.setattr(maintenance, "AUDIO_DIR", root)
    outside = tmp_path / "private.txt"
    outside.write_text("keep")
    with pytest.raises(ValueError):
        maintenance.audio_seguro(outside)
    assert outside.read_text() == "keep"


def test_maintenance_creates_restorable_backup_and_expires_old_one(db, tmp_path, monkeypatch):
    root = tmp_path / "audios"
    root.mkdir()
    backups = tmp_path / "copies"
    backups.mkdir()
    monkeypatch.setattr(maintenance, "AUDIO_DIR", root)
    monkeypatch.setattr(maintenance, "BACKUP_DIR", backups)
    expired = backups / "diary-old.sqlite3"
    expired.write_bytes(b"old")
    stamp = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
    os.utime(expired, (stamp, stamp))
    maintenance.mantenimiento()
    assert not expired.exists()
    assert len(list(backups.glob("diary-*.sqlite3"))) == 1
