#!/usr/bin/env python3
"""Create and verify encrypted application-asset backups for recovery drills."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

try:
    from .backup import checksum, decrypt, encrypt, encryption_key
except ImportError:  # pragma: no cover - used when invoked as a script
    from backup import checksum, decrypt, encrypt, encryption_key

UTC = timezone.utc


def asset_encryption_key() -> bytes:
    """Prefer the dedicated asset key while retaining legacy test compatibility."""
    value = os.getenv("ASSET_BACKUP_ENCRYPTION_KEY", "").strip()
    if value:
        try:
            key = base64.urlsafe_b64decode(value)
        except Exception as exc:
            raise RuntimeError("ASSET_BACKUP_ENCRYPTION_KEY must be valid base64") from exc
        if len(key) not in (16, 24, 32):
            raise RuntimeError("ASSET_BACKUP_ENCRYPTION_KEY must decode to 16, 24, or 32 bytes")
        return key
    return encryption_key()


def tree_checksum(source: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        relative = path.relative_to(source).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError("asset archive contains an unsafe path")
    return members


def create_asset_backup(
    source: Path,
    destination_dir: Path,
    environment: str,
    database_backup_id: str | None = None,
    key_id: str | None = None,
    secret_reference: str | None = None,
) -> dict:
    source = source.resolve()
    destination_dir = destination_dir.resolve()
    if not source.is_dir():
        raise RuntimeError(f"asset source directory does not exist: {source}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    recovery_id = f"recovery_{started.strftime('%Y%m%dT%H%M%SZ')}"
    backup_id = f"assets_{started.strftime('%Y%m%dT%H%M%SZ')}"
    encrypted = destination_dir / f"{backup_id}.tar.gz.enc"
    metadata_path = destination_dir / f"{backup_id}.json"
    if encrypted.exists() or metadata_path.exists():
        raise RuntimeError("asset backup destination already contains this backup id")

    source_checksum = tree_checksum(source)
    with tempfile.TemporaryDirectory() as temp_dir:
        archive = Path(temp_dir) / f"{backup_id}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(source, arcname=".", recursive=True)
        archive_checksum = checksum(archive)
        encrypt(archive, encrypted, asset_encryption_key())

    encrypted_checksum = checksum(encrypted)
    metadata = {
        "backup_id": backup_id,
        "recovery_id": recovery_id,
        "created_at": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "environment": environment,
        "source_path": str(source),
        "destination_path": str(encrypted),
        "size": encrypted.stat().st_size,
        "checksum": encrypted_checksum,
        "encrypted_checksum": encrypted_checksum,
        "archive_checksum": archive_checksum,
        "source_tree_checksum": source_checksum,
        "encryption_algorithm": "AES-256-GCM",
        "status": "SUCCESS",
    }
    if database_backup_id:
        metadata["database_backup_id"] = database_backup_id
    if key_id:
        metadata["key_id"] = key_id
    if secret_reference:
        metadata["secret_reference"] = secret_reference
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metadata


def copy_to_separate_storage(artifact: Path, destination_dir: Path, metadata: dict) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / artifact.name
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite asset backup: {destination}")
    shutil.copy2(artifact, destination)
    if checksum(destination) != metadata["checksum"]:
        raise RuntimeError("separate asset storage checksum verification failed")
    return destination


def restore_asset_backup(artifact: Path, restore_dir: Path, metadata: dict, representative: str | None = None) -> str:
    if checksum(artifact) != metadata["checksum"]:
        raise RuntimeError("asset backup checksum mismatch")
    restore_dir = restore_dir.resolve()
    restore_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        archive = Path(temp_dir) / "assets.tar.gz"
        decrypt(artifact, archive, asset_encryption_key())
        if checksum(archive) != metadata["archive_checksum"]:
            raise RuntimeError("asset archive checksum mismatch")
        with tarfile.open(archive, "r:gz") as tar:
            members = _safe_members(tar)
            tar.extractall(restore_dir, members=members)
    validate_restored_asset_tree(restore_dir, metadata, representative)
    return str(restore_dir)


def validate_restored_asset_tree(restore_dir: Path, metadata: dict, representative: str | None = None) -> None:
    if tree_checksum(restore_dir) != metadata["source_tree_checksum"]:
        raise RuntimeError("restored asset tree checksum mismatch")
    if representative:
        representative_path = restore_dir / Path(representative)
        if not representative_path.is_file():
            raise RuntimeError(f"representative asset is missing: {representative}")


def validate_database_references(database_url: str, restore_dir: Path, public_base_url: str) -> int:
    """Check local News image URLs without exposing database credentials or values."""
    result = subprocess.run(
        ["psql", database_url, "-v", "ON_ERROR_STOP=1", "-tA", "-c", "SELECT image_url FROM news WHERE image_url IS NOT NULL"],
        check=True,
        capture_output=True,
        text=True,
    )
    base = public_base_url.rstrip("/") + "/"
    checked = 0
    for value in result.stdout.splitlines():
        if not value.startswith(base):
            continue
        relative = unquote(urlparse(value).path.removeprefix(urlparse(base).path).lstrip("/"))
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RuntimeError("database asset reference contains an unsafe path")
        if not (restore_dir / relative_path).is_file():
            raise RuntimeError(f"database references missing local asset: {relative}")
        checked += 1
    return checked


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("source", type=Path)
    create.add_argument("destination", type=Path)
    create.add_argument("--environment", default="disposable-recovery")
    create.add_argument("--database-backup-id")
    create.add_argument("--key-id")
    create.add_argument("--secret-reference")
    restore = subparsers.add_parser("restore")
    restore.add_argument("artifact", type=Path)
    restore.add_argument("metadata", type=Path)
    restore.add_argument("destination", type=Path)
    restore.add_argument("--representative")
    args = parser.parse_args()
    if args.command == "create":
        print(json.dumps(create_asset_backup(args.source, args.destination, args.environment, args.database_backup_id, args.key_id, args.secret_reference), indent=2, sort_keys=True))
    else:
        metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
        print(restore_asset_backup(args.artifact, args.destination, metadata, args.representative))
    return 0


if __name__ == "__main__":
    sys.exit(main())