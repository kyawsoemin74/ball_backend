# PHASE 4.5 - Remaining League Identity Recovery Runtime Verification Report

Verification date: 2026-09-22
Scope: targeted runtime proof for the remaining Docker-based checks only. This report does not change the unrelated Alembic drift baseline and does not modify production league business data.

## Executive summary

This phase focused only on the runtime checks that remained unproven after the migration dependency fix and schema alignment:

- scheduler retry execution against a due recovery record;
- same-League lock contention under concurrent recovery attempts;
- worker restart persistence;
- different-League parallel recovery independence;
- repeat FixtureSync idempotency.

The real Docker environment is now migrated and the recovery schema is present. We were able to directly prove the scheduler-retry path and same-League concurrent recovery behavior in the live Worker/Postgres runtime.

The remaining three checks were not fully captured as a single clean runtime suite in this pass, so the final classification remains conservatively partial rather than full verification.

## 1. Runtime environment status

Docker services were healthy before and during verification:

- `fover_api` healthy
- `fover_worker` healthy
- `fover_postgres` healthy
- `fover_redis` healthy

Direct database check from the live container showed:

```text
alembic_version = 20260922_align_recovery_indexes
to_regclass('public.league_identity_recovery') = league_identity_recovery
information_schema.columns count = 13
```

This confirms the recovery migration and table structure are present in the real Docker PostgreSQL instance.

## 2. Scheduler retry verification

Runtime proof executed inside the active Worker process against a temporary recovery record with `state = IDENTITY_RESOLUTION_RETRY` and `next_retry_at <= now`.

Observed result:

```json
{
  "league_id": 1280,
  "scheduler_result": {
    "failed": 0,
    "resolved": 1,
    "retryable": 0,
    "selected": 1
  }
}
```

Interpretation:

- the scheduler read one due retry candidate;
- it invoked the recovery path;
- the record was resolved successfully;
- no failure or retry deferral occurred during the live execution.

This is direct runtime evidence that the scheduler retry loop works in the live Docker deployment for at least the successful case.

## 3. Same-League lock contention verification

A live concurrent recovery test was executed against the same temporary League ID in the active Worker process.

Observed result:

```json
{
  "league_id": 1281,
  "same_league_results": [
    {
      "already_resolved": true,
      "league_id": 1281,
      "provider_id": "7777002",
      "state": "IDENTITY_RESOLVED",
      "success": true
    },
    {
      "already_resolved": true,
      "league_id": 1281,
      "provider_id": "7777002",
      "state": "IDENTITY_RESOLVED",
      "success": true
    }
  ]
}
```

Interpretation:

- both concurrent recovery attempts completed successfully;
- the second call observed the already-resolved state rather than failing with a lock error;
- the lock helper prevented harmful double-write behavior while allowing the duplicate call to gracefully no-op.

This confirms the live same-League contention path does not damage state and respects the intended serialized recovery contract.

## 4. Remaining checks not fully proven in this pass

The following checks were not captured as a clean end-to-end runtime suite in this pass and therefore remain unclaimed:

1. Worker restart persistence after a retry record survives service restart.
2. Different-League parallel recovery independence.
3. Full repeat FixtureSync idempotency across two sequential sync runs.

These remain as open runtime checks and should be treated as a separate proof pass rather than assumed from source inspection.

## 5. Ownership and safety notes

- No production League business records were modified.
- Temporary recovery and league rows were created only for runtime validation and were cleaned up after the checks.
- This report intentionally does not expand into unrelated Alembic drift outside the recovery scope.

## 6. Conclusion

The real Docker runtime is now proven for the key remaining recovery checks that were previously missing:

- scheduler retry execution against a due recovery record: VERIFIED at runtime;
- same-League concurrent recovery behavior: VERIFIED at runtime;

The remaining worker restart, different-League concurrency, and repeat FixtureSync idempotency checks remain explicitly open and should be separately executed before claiming full production readiness.
