# Fover Backup and Recovery

The PostgreSQL database is the source of truth. Redis is a rebuildable cache.

## Backup

`backup.py run` performs one fail-closed PostgreSQL backup. For the current GCS-deferred staging drill, set `BACKUP_STORAGE_PATH` to the verified separate backup volume. Do not configure GCS in this phase.

1. Runs `pg_dump --format=custom`.
2. Encrypts the dump with AES-256-GCM.
3. Calculates SHA-256 over the encrypted artifact.
4. Copies the encrypted artifact to the configured separate storage path (or uses the future GCS path only after explicit approval).
5. Verifies destination size and checksum metadata.
6. Writes JSON metadata, uploads metadata, and applies local and offsite retention.

Required environment references:

```text
DATABASE_URL
BACKUP_GCS_URI=gs://bucket/prefix
BACKUP_ENCRYPTION_KEY_B64=<base64 key from secret manager>
```

Local staging storage:

```text
BACKUP_STORAGE_PATH=D:/FoverBackup
BACKUP_POSTGRES_VERSION=17.9
```

Optional settings:

```text
BACKUP_LOCAL_DIR=/var/backups/fover
BACKUP_RETENTION_DAYS=30
BACKUP_STALE_HOURS=26
BACKUP_ALERT_WEBHOOK_URL=<secret-managed alert endpoint>
```

The encryption key must be held in the approved secret manager and must not be stored beside artifacts or in Git. Losing it makes encrypted backups unrecoverable. The example file contains placeholders only.

### Asset backup

News uploads are local filesystem objects, not PostgreSQL data. `News.image_url` stores the public URL generated from `NEWS_UPLOAD_PUBLIC_URL`; the file itself is under `NEWS_UPLOAD_DIR` (normally `/var/www/fover/uploads/news`). Create the encrypted asset artifact directly in the separate backup volume:

```bash
python3 ops/backup/asset_backup.py create "$NEWS_UPLOAD_DIR" D:/FoverBackup --environment disposable-recovery
```

The JSON record beside the artifact contains `backup_id`, `recovery_id`, UTC timestamp, environment, source and destination paths, size, encrypted checksum, archive checksum, source-tree checksum, encryption algorithm, and status. Never overwrite an existing artifact. Supply the dedicated `ASSET_BACKUP_ENCRYPTION_KEY` from the approved secret manager; it is not printed or written to metadata. The database backup uses `BACKUP_ENCRYPTION_KEY_B64`; the asset backup uses the separate asset key and target.

Install `fover-backup.service` and `fover-backup.timer` on the backup host, then enable the timer. The scheduled execution time is 02:30 UTC daily. Automation must be enabled only after staging validation and explicit production approval.

## Restore verification

Never restore to production during a drill. Provision disposable PostgreSQL using the same major version used to create the backup, set `RECOVERY_DATABASE_URL`, and run:

```bash
python3 ops/backup/restore_verify.py /var/backups/fover/fover_<id>.dump.enc --expected-sha256 <sha256> --timing-report /var/backups/fover/recovery_<id>.json
```

For a full disposable check, add the asset artifact, its metadata, an empty recovery directory, and one known relative file:

```bash
python3 ops/backup/restore_verify.py <database-artifact> --database-url "$RECOVERY_DATABASE_URL" --asset-artifact D:/FoverBackup/assets_<id>.tar.gz.enc --asset-metadata D:/FoverBackup/assets_<id>.json --asset-restore-dir <recovery-filesystem>/uploads/news --asset-representative <known-file>
```

The tool decrypts the artifact, runs `pg_restore --list`, restores into the disposable database with `--no-owner`, and verifies the critical public tables and `alembic_version` are present. It writes T0-T4 timestamps to the optional timing report. The operator must additionally validate critical row counts, constraints, indexes, sequences, JSON data, timestamps, and representative read-only API calls.

Record restore start, database-ready, application-ready, and total duration in the recovery log. The timing report covers artifact validation, decryption, restore, and database validation; the operator adds Redis, backend, health, and API timestamps. No RTO is claimed until a timed drill succeeds.

## Recovery runbook

1. Identify the newest metadata record with `status=SUCCESS`.
2. Confirm its checksum and remote object existence.
3. Provision disposable PostgreSQL with the supported major version.
4. Create disposable roles and apply the reviewed grants in `recovery-roles.example.yaml`; use `pg_restore --no-owner` and assign restored objects to `fover_user`.
5. Restore the database using `restore_verify.py`, then validate schemas, owners, tables, constraints, indexes, sequences, migrations, and application read/write access.
	Validate the application role and grants with `python3 ops/backup/role_verify.py "$RECOVERY_DATABASE_URL" fover_user leagues teams players matches news`.
6. Restore assets using `asset_backup.py restore` or the verifier options; validate encrypted, archive, and restored-tree checksums, one representative file, and every local `News.image_url` reference.
7. Inject values from `recovery.env.example`: database and Redis endpoints from recovery provisioning; required secrets from the approved secret manager; storage and domain values from the recovery environment. Never copy production passwords.
8. Start Redis empty, then start the backend with scheduler and worker disabled for read-only validation.
9. Run health checks and representative read-only API checks. Confirm missing-role, missing-grant, missing-config, missing-asset, and checksum failure drills fail clearly without exposing secret values.
10. Record `recovery_id`, timestamps, artifact metadata, failures, and hardened RTO. Clean up only disposable resources and preserve evidence.
11. Promotion or reconnect requires explicit approval after the complete validation record is reviewed.

On any failure, stop promotion, preserve the failed metadata and logs, keep the current system isolated, and escalate to the database/infrastructure operator. Do not delete the last valid backup.

## Recovery classifications

The non-secret configuration manifest is `recovery.env.example`; the unified manifest is `recovery-manifest.yaml`; role and grant design is `recovery-roles.example.yaml`. `DATABASE_URL` and `REDIS_URL` are reproducible from disposable service provisioning. `NEWS_UPLOAD_DIR`, `NEWS_UPLOAD_PUBLIC_URL`, retention, and scheduler-disabled settings are reproducible configuration. `JWT_SECRET_KEY`, `FOOTBALL_API_KEY`, `GOOGLE_CLIENT_ID`, and `BACKUP_ENCRYPTION_KEY_B64` are external secret-manager values. TLS/domain credentials and any alert webhook are external secret-manager or infrastructure records. No secret values belong in these files.

The application role is not a superuser. `fover_migration` is elevated only for an explicitly approved disposable migration test; `fover_backup` receives reviewed read access; `fover_scheduler` is disabled and need not be created for this phase. A missing role or grant is a validation failure, never a reason to grant superuser access.

GCS remains deferred. The active separate storage for this phase is `D:/FoverBackup`; verify capacity before creating an asset archive and preserve existing artifacts.