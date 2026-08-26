#!/usr/bin/env python3
"""Create, verify, upload, and retain encrypted PostgreSQL backups."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import shutil
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from google.cloud import storage
except ImportError:  # pragma: no cover - reported when the command runs
    storage = None


LOGGER = logging.getLogger("fover-backup")
UTC = timezone.utc


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def database_name(database_url: str) -> str:
    name = urlparse(database_url).path.rsplit("/", 1)[-1]
    if not name:
        raise RuntimeError("DATABASE_URL does not contain a database name")
    return name


def encryption_key() -> bytes:
    try:
        key = base64.urlsafe_b64decode(required("BACKUP_ENCRYPTION_KEY_B64"))
    except Exception as exc:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY_B64 must be valid base64") from exc
    if len(key) not in (16, 24, 32):
        raise RuntimeError("BACKUP_ENCRYPTION_KEY_B64 must decode to 16, 24, or 32 bytes")
    return key


def encrypt(source: Path, destination: Path, key: bytes) -> None:
    nonce = os.urandom(12)
    plaintext = source.read_bytes()
    destination.write_bytes(b"FOVER1" + nonce + AESGCM(key).encrypt(nonce, plaintext, None))


def decrypt(source: Path, destination: Path, key: bytes) -> None:
    payload = source.read_bytes()
    if not payload.startswith(b"FOVER1"):
        raise RuntimeError("backup encryption header is invalid")
    nonce = payload[6:18]
    destination.write_bytes(AESGCM(key).decrypt(nonce, payload[18:], None))


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def storage_client():
    if storage is None:
        raise RuntimeError("google-cloud-storage is required for offsite backups")
    return storage.Client()


def remote_parts() -> tuple[str, str]:
    location = required("BACKUP_GCS_URI")
    parsed = urlparse(location)
    if parsed.scheme != "gs" or not parsed.netloc:
        raise RuntimeError("BACKUP_GCS_URI must use gs://bucket/prefix")
    return parsed.netloc, parsed.path.strip("/")


def local_storage_path() -> Path:
    path = Path(required("BACKUP_STORAGE_PATH")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def upload(path: Path, metadata: dict) -> None:
    if os.getenv("BACKUP_STORAGE_PATH", "").strip():
        destination = local_storage_path() / path.name
        shutil.copy2(path, destination)
        if destination.stat().st_size != path.stat().st_size or checksum(destination) != metadata["checksum"]:
            raise RuntimeError("separate backup storage verification failed")
        metadata["storage_location"] = str(destination)
        return
    bucket_name, prefix = remote_parts()
    object_name = "/".join(part for part in (prefix, path.name) if part)
    blob = storage_client().bucket(bucket_name).blob(object_name)
    blob.metadata = {"sha256": metadata["checksum"], "backup_id": metadata["backup_id"]}
    blob.upload_from_filename(str(path), content_type="application/octet-stream")
    blob.reload()
    if blob.size != path.stat().st_size:
        raise RuntimeError("remote backup size does not match local backup")
    if (blob.metadata or {}).get("sha256") != metadata["checksum"]:
        raise RuntimeError("remote backup checksum metadata does not match")
    metadata["storage_location"] = f"gs://{bucket_name}/{object_name}"


def upload_metadata(path: Path, metadata: dict) -> None:
    if os.getenv("BACKUP_STORAGE_PATH", "").strip():
        destination = local_storage_path() / path.name
        shutil.copy2(path, destination)
        if destination.stat().st_size != path.stat().st_size:
            raise RuntimeError("separate metadata storage verification failed")
        return
    bucket_name, prefix = remote_parts()
    object_name = "/".join(part for part in (prefix, path.name) if part)
    blob = storage_client().bucket(bucket_name).blob(object_name)
    blob.upload_from_filename(str(path), content_type="application/json")
    blob.reload()
    if blob.size != path.stat().st_size:
        raise RuntimeError("remote metadata size does not match local metadata")


def retain_remote(keep_days: int, newest_backup_id: str) -> None:
    if os.getenv("BACKUP_STORAGE_PATH", "").strip():
        directory = local_storage_path()
        cutoff = datetime.now(UTC) - timedelta(days=keep_days)
        candidates = list(directory.glob("*.dump.enc"))
        newest = max(candidates, key=lambda item: item.stat().st_mtime, default=None)
        for path in candidates:
            if path == newest or datetime.fromtimestamp(path.stat().st_mtime, UTC) >= cutoff:
                continue
            path.unlink()
            metadata_path = path.with_suffix("").with_suffix(".json")
            if metadata_path.exists():
                metadata_path.unlink()
        return
    bucket_name, prefix = remote_parts()
    client = storage_client()
    object_prefix = "/".join(part for part in (prefix, "") if part)
    blobs = list(client.list_blobs(bucket_name, prefix=object_prefix))
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    candidates = [blob for blob in blobs if blob.name.endswith(".dump.enc")]
    newest = max(candidates, key=lambda blob: blob.updated or datetime.min.replace(tzinfo=UTC), default=None)
    for blob in candidates:
        updated = blob.updated or datetime.min.replace(tzinfo=UTC)
        if blob is newest or updated >= cutoff:
            continue
        blob.delete()


def write_metadata(metadata: dict, directory: Path) -> Path:
    path = directory / f"{metadata['backup_id']}.json"
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def alert(metadata: dict, reason: str) -> None:
    LOGGER.error("BACKUP_FAILED backup_id=%s stage=%s reason=%s", metadata.get("backup_id"), metadata.get("failure_stage"), reason)
    webhook = os.getenv("BACKUP_ALERT_WEBHOOK_URL", "").strip()
    if webhook:
        body = json.dumps({"backup_id": metadata.get("backup_id"), "timestamp": metadata.get("created_at"), "failure_stage": metadata.get("failure_stage"), "reason": reason}).encode()
        request = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(request, timeout=10).read()
        except Exception:
            LOGGER.exception("backup alert delivery failed")


def retain(directory: Path, keep_days: int, newest: Path) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    for path in directory.glob("*.json"):
        if path == newest:
            continue
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
            created = datetime.fromisoformat(metadata["created_at"])
            if metadata.get("status") == "SUCCESS" and created >= cutoff:
                continue
            if metadata.get("status") != "SUCCESS" or created < cutoff:
                path.unlink()
        except (OSError, KeyError, TypeError, ValueError):
            LOGGER.warning("retention skipped malformed metadata: %s", path)


def create_backup() -> int:
    metadata = {"backup_id": datetime.now(UTC).strftime("fover_%Y%m%dT%H%M%SZ"), "created_at": datetime.now(UTC).isoformat(), "status": "FAILED"}
    output_dir = Path(os.getenv("BACKUP_LOCAL_DIR", "./backups"))
    output_dir.mkdir(parents=True, exist_ok=True)
    encrypted = output_dir / f"{metadata['backup_id']}.dump.enc"
    metadata_path = output_dir / f"{metadata['backup_id']}.json"
    try:
        database_url = required("DATABASE_URL")
        key = encryption_key()
        metadata["database"] = database_name(database_url)
        if os.getenv("BACKUP_STORAGE_PATH", "").strip():
            metadata["storage_destination"] = required("BACKUP_STORAGE_PATH")
        else:
            metadata["storage_destination"] = required("BACKUP_GCS_URI")
        metadata["postgresql_version"] = os.getenv("BACKUP_POSTGRES_VERSION", "unknown")
        with tempfile.TemporaryDirectory() as temp_dir:
            raw = Path(temp_dir) / "database.dump"
            metadata["failure_stage"] = "pg_dump"
            subprocess.run(["pg_dump", "--format=custom", "--file", str(raw), database_url], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            metadata["failure_stage"] = "encryption"
            encrypt(raw, encrypted, key)
        metadata["size"] = encrypted.stat().st_size
        metadata["failure_stage"] = "checksum"
        metadata["checksum"] = checksum(encrypted)
        metadata["failure_stage"] = "upload"
        upload(encrypted, metadata)
        metadata["failure_stage"] = "metadata"
        metadata["completed_at"] = datetime.now(UTC).isoformat()
        metadata["status"] = "SUCCESS"
        metadata_path = write_metadata(metadata, output_dir)
        upload_metadata(metadata_path, metadata)
        retain(output_dir, int(os.getenv("BACKUP_RETENTION_DAYS", "30")), metadata_path)
        retain_remote(int(os.getenv("BACKUP_RETENTION_DAYS", "30")), metadata["backup_id"])
        LOGGER.info("BACKUP_SUCCESS backup_id=%s size=%s checksum=%s", metadata["backup_id"], metadata["size"], metadata["checksum"])
        return 0
    except Exception as exc:
        metadata["completed_at"] = datetime.now(UTC).isoformat()
        metadata["reason"] = str(exc)
        try:
            write_metadata(metadata, output_dir)
            if encrypted.exists():
                encrypted.unlink()
        finally:
            alert(metadata, str(exc))
        return 1


def stale_check() -> int:
    directory = Path(os.getenv("BACKUP_LOCAL_DIR", "./backups"))
    threshold = int(os.getenv("BACKUP_STALE_HOURS", "26"))
    successful = []
    for path in directory.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if item.get("status") == "SUCCESS":
                successful.append(datetime.fromisoformat(item["completed_at"]))
        except (OSError, KeyError, TypeError, ValueError):
            continue
    if not successful or datetime.now(UTC) - max(successful) > timedelta(hours=threshold):
        metadata = {"backup_id": "stale-check", "failure_stage": "stale-check", "created_at": datetime.now(UTC).isoformat()}
        alert(metadata, "no successful backup within configured threshold")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "stale-check"), nargs="?", default="run")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("BACKUP_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    return create_backup() if args.command == "run" else stale_check()


if __name__ == "__main__":
    sys.exit(main())