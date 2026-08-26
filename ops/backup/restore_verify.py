#!/usr/bin/env python3
"""Verify an encrypted custom-format backup in a disposable PostgreSQL database."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from backup import checksum, decrypt, encryption_key
from asset_backup import restore_asset_backup, validate_database_references


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--database-url", default=os.getenv("RECOVERY_DATABASE_URL"))
    parser.add_argument("--expected-sha256")
    parser.add_argument("--timing-report", type=Path)
    parser.add_argument("--asset-artifact", type=Path)
    parser.add_argument("--asset-metadata", type=Path)
    parser.add_argument("--asset-restore-dir", type=Path)
    parser.add_argument("--asset-representative")
    parser.add_argument("--asset-public-url", default=os.getenv("NEWS_UPLOAD_PUBLIC_URL", "https://kyawsoemin.com/uploads/news/"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or RECOVERY_DATABASE_URL is required")
    started = datetime.now(timezone.utc)
    timing = {"recovery_id": f"recovery_{started.strftime('%Y%m%dT%H%M%SZ')}", "backup_id": args.artifact.stem.removesuffix(".dump"), "t0": started.isoformat()}
    metadata_path = args.artifact.with_name(args.artifact.name.removesuffix(".dump.enc") + ".json")
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("status") != "SUCCESS":
            raise SystemExit("backup metadata is not successful")
        if args.expected_sha256 is None:
            args.expected_sha256 = metadata.get("checksum")
    if args.expected_sha256 and checksum(args.artifact) != args.expected_sha256:
        raise SystemExit("checksum mismatch")
    timing["t1_backup_validated"] = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory() as temp_dir:
        dump = Path(temp_dir) / "database.dump"
        decrypt(args.artifact, dump, encryption_key())
        timing["t2_decryption_complete"] = datetime.now(timezone.utc).isoformat()
        subprocess.run(["pg_restore", "--list", str(dump)], check=True)
        subprocess.run(["pg_restore", "--clean", "--if-exists", "--exit-on-error", "--no-owner", "--dbname", args.database_url, str(dump)], check=True)
        timing["t3_restore_complete"] = datetime.now(timezone.utc).isoformat()
        checks = """
        SELECT CASE WHEN count(*) = 12 THEN 'OK' ELSE 'MISSING_CRITICAL_TABLES' END
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN ('leagues', 'teams', 'players', 'matches', 'match_events',
            'match_lineups', 'standings', 'odds', 'match_statistics', 'match_h2h',
            'match_lineup_finalization', 'alembic_version');
        """
        result = subprocess.run(
            ["psql", args.database_url, "-v", "ON_ERROR_STOP=1", "-tA", "-c", checks],
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip() != "OK":
            raise RuntimeError("critical restore tables are missing")
        timing["t4_database_validation_complete"] = datetime.now(timezone.utc).isoformat()
        if any((args.asset_artifact, args.asset_metadata, args.asset_restore_dir)) and not all((args.asset_artifact, args.asset_metadata, args.asset_restore_dir)):
            raise RuntimeError("asset artifact, metadata, and restore directory must be provided together")
        if args.asset_artifact:
            metadata = json.loads(args.asset_metadata.read_text(encoding="utf-8"))
            restore_asset_backup(args.asset_artifact, args.asset_restore_dir, metadata, args.asset_representative)
            timing["asset_db_references_checked"] = validate_database_references(args.database_url, args.asset_restore_dir, args.asset_public_url)
            timing["t5_assets_validated"] = datetime.now(timezone.utc).isoformat()
    timing["result"] = "PASS"
    if args.timing_report:
        args.timing_report.parent.mkdir(parents=True, exist_ok=True)
        args.timing_report.write_text(json.dumps(timing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("restore verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())