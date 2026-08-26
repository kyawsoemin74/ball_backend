import base64

import pytest

from ops.backup import asset_backup


def test_asset_backup_round_trip_and_representative(tmp_path, monkeypatch):
    source = tmp_path / "uploads" / "news"
    source.mkdir(parents=True)
    (source / "representative.png").write_bytes(b"\x89PNG\r\n\x1a\nasset")
    backup_dir = tmp_path / "backup"
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY_B64", base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode())

    metadata = asset_backup.create_asset_backup(source, backup_dir, "test-recovery", "fover_test_db")
    artifact = backup_dir / f"{metadata['backup_id']}.tar.gz.enc"
    restored = tmp_path / "restored"
    asset_backup.restore_asset_backup(artifact, restored, metadata, "representative.png")
    asset_backup.validate_restored_asset_tree(restored, metadata, "representative.png")

    assert (restored / "representative.png").read_bytes().endswith(b"asset")
    assert metadata["encrypted_checksum"] == metadata["checksum"]
    assert metadata["database_backup_id"] == "fover_test_db"

    (restored / "representative.png").unlink()
    with pytest.raises(RuntimeError, match="restored asset tree checksum mismatch"):
        asset_backup.validate_restored_asset_tree(restored, metadata, "representative.png")


def test_asset_backup_detects_missing_representative(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "real.jpg").write_bytes(b"image")
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY_B64", base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode())
    metadata = asset_backup.create_asset_backup(source, tmp_path / "backup", "test-recovery")

    with pytest.raises(RuntimeError, match="representative asset is missing"):
        asset_backup.restore_asset_backup(tmp_path / "backup" / f"{metadata['backup_id']}.tar.gz.enc", tmp_path / "restored", metadata, "missing.jpg")


def test_asset_backup_refuses_overwrite(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "real.jpg").write_bytes(b"image")
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY_B64", base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode())
    metadata = asset_backup.create_asset_backup(source, tmp_path / "backup", "test-recovery")

    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        asset_backup.copy_to_separate_storage(tmp_path / "backup" / f"{metadata['backup_id']}.tar.gz.enc", tmp_path / "backup", metadata)


def test_database_reference_validation_detects_missing_file(tmp_path, monkeypatch):
    class Result:
        stdout = "https://recovery.example/uploads/news/missing.jpg\n"

    monkeypatch.setattr(asset_backup.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(RuntimeError, match="database references missing local asset"):
        asset_backup.validate_database_references(
            "postgresql://disposable.invalid/db", tmp_path, "https://recovery.example/uploads/news/"
        )