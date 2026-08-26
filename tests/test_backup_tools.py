import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops.backup import backup


def test_encrypt_decrypt_round_trip(tmp_path, monkeypatch):
    key = b"0123456789abcdef0123456789abcdef"
    source = tmp_path / "source"
    encrypted = tmp_path / "backup.enc"
    restored = tmp_path / "restored"
    source.write_bytes(b"postgres custom dump")

    backup.encrypt(source, encrypted, key)
    backup.decrypt(encrypted, restored, key)

    assert restored.read_bytes() == source.read_bytes()
    with pytest.raises(Exception):
        backup.decrypt(encrypted, tmp_path / "bad", b"abcdef0123456789")


def test_retention_keeps_newest_success_and_recent_success(tmp_path):
    now = datetime.now(timezone.utc)
    newest = tmp_path / "newest.json"
    recent = tmp_path / "recent.json"
    old = tmp_path / "old.json"
    failed = tmp_path / "failed.json"
    for path, status, created in (
        (newest, "SUCCESS", now),
        (recent, "SUCCESS", now - timedelta(days=2)),
        (old, "SUCCESS", now - timedelta(days=31)),
        (failed, "FAILED", now),
    ):
        path.write_text(json.dumps({"backup_id": path.stem, "status": status, "created_at": created.isoformat()}), encoding="utf-8")

    backup.retain(tmp_path, 30, newest)

    assert newest.exists()
    assert recent.exists()
    assert not old.exists()
    assert not failed.exists()


def test_stale_check_fails_without_recent_success(tmp_path, monkeypatch):
    metadata = tmp_path / "old.json"
    metadata.write_text(json.dumps({
        "backup_id": "old",
        "status": "SUCCESS",
        "completed_at": (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(),
    }), encoding="utf-8")
    monkeypatch.setenv("BACKUP_LOCAL_DIR", str(tmp_path))
    monkeypatch.setenv("BACKUP_STALE_HOURS", "26")

    assert backup.stale_check() == 1


def test_stale_check_accepts_recent_success(tmp_path, monkeypatch):
    metadata = tmp_path / "recent.json"
    metadata.write_text(json.dumps({
        "backup_id": "recent",
        "status": "SUCCESS",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    monkeypatch.setenv("BACKUP_LOCAL_DIR", str(tmp_path))

    assert backup.stale_check() == 0


def test_create_backup_fails_closed_when_pg_dump_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@example.invalid/fover_db")
    monkeypatch.setenv("BACKUP_GCS_URI", "gs://bucket/fover")
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY_B64", base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode())
    monkeypatch.setenv("BACKUP_LOCAL_DIR", str(tmp_path))

    def fail(*args, **kwargs):
        raise RuntimeError("pg_dump failed")

    monkeypatch.setattr(backup.subprocess, "run", fail)

    assert backup.create_backup() == 1
    records = list(tmp_path.glob("*.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text(encoding="utf-8"))["status"] == "FAILED"