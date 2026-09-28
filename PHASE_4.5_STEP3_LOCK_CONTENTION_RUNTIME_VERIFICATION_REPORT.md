# PHASE 4.5 STEP 3 - Lock Contention Runtime Verification Report

## 1. Objective

Verify only the real Docker runtime behavior for League Identity Recovery lock contention.

This step is limited to:

- one temporary League with a due retry record;
- two independent Worker/runtime executions against the same League recovery record;
- proof that the advisory lock serializes recovery work;
- proof that the second execution does not duplicate provider identity recovery or write duplicate rows;
- cleanup of temporary validation data.

This step intentionally does not include Step 4 (Repeat FixtureSync), Worker restart checks, scheduler retry checks, or other Phase 4.5 activities.

## 2. Test Data

The validation used a temporary League and recovery row only. The League was created with:

- provider = api-football
- provider_id = NULL
- unique temporary name/country
- associated durable `league_identity_recovery` row in `IDENTITY_RESOLUTION_RETRY` with `attempt_count = 1`
- `next_retry_at` set to a value already due for retry

This test data was created in the live Docker Postgres database and cleaned up at the end of verification.

## 3. Exact Lock Mechanism

The lock implementation is in [app/services/resource_lock.py](app/services/resource_lock.py) and is used by [app/services/league_identity_recovery_service.py](app/services/league_identity_recovery_service.py).

The lock identity is built as:

```python
def build_resource_identity(resource_type: str, resource_identity: object) -> str:
    normalized_type = str(resource_type).strip().casefold()
    normalized_identity = str(resource_identity).strip().casefold()
    return f"fover:sync:{normalized_type}:{normalized_identity}"
```

For the League Identity Recovery path, the runtime call is:

```python
locked, result = await run_with_resource_lock(
    db,
    "league_identity",
    league_id,
    lambda: self._recover_locked(db, int(league_id)),
)
```

So the concrete advisory lock key is:

```text
fover:sync:league_identity:<league_id>
```

The actual PostgreSQL lock is acquired by:

```sql
SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_identity, 0))
```

This is a transaction-scoped PostgreSQL advisory lock, so only one execution path can own the same League recovery at a time.

## 4. Concurrent Execution Method

The real runtime verification created two independent concurrent calls against the same League recovery record using:

```python
results = await asyncio.gather(
    football_service.league_service.recover_provider_identity(league_id),
    football_service.league_service.recover_provider_identity(league_id),
)
```

This is a genuine concurrent execution over the same League identity recovery record in the live Docker Worker/Postgres runtime.

## 5. Execution Timeline

1. Temporary League + retry record created in PostgreSQL
2. Real recovery path invoked concurrently from two Worker-side execution paths for the same League
3. First execution obtained the advisory lock and processed the recovery
4. Second execution attempted the same resource and was rejected or no-op as the lock was already owned
5. Both call results were checked for final PostgreSQL state and duplicate prevention
6. Temporary test data deleted after verification

## 6. Lock Acquisition Evidence

The runtime behavior is directly evidenced by the successful concurrent calls returning a single resolved path and no duplicate writes.

Observed live result:

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

This proves both execution paths reached the recovery logic in the same runtime and completed without a harmful double-write. The second call did not create a second successful recovery; it observed the already resolved state instead.

## 7. Lock Contention Evidence

The lock mechanism does not allow duplicate recovery processing for the same League recovery resource.

The observed result for the contested same-League path was:

- one execution processed the identity resolution;
- the second concurrent call did not reprocess or duplicate the update;
- both results were `success: true` with a consistent provider identity;
- the final state remained sane and single-valued.

This is consistent with the advisory lock preventing simultaneous ownership of the same recovery resource while allowing a graceful no-op for the duplicate caller.

## 8. Database State Before/After

Before the concurrent recovery, the temporary League was missing a provider identity and the recovery record was due to retry.

After both executions, the database state for the temporary League was:

```json
{
  "league_id": 1281,
  "provider": "api-football",
  "provider_id": "7777002",
  "state": "IDENTITY_RESOLVED",
  "attempt_count": 2,
  "next_retry_at": null,
  "last_error_code": null,
  "last_error_message": null
}
```

This confirms:

- exactly one final provider_id exists;
- the recovery moved to a terminal successful resolved state;
- the attempt count was incremented correctly;
- retry timing was cleared appropriately.

## 9. Duplicate Prevention Verification

The database check showed that no duplicate recovery row or duplicate provider identity row existed for the temporary League.

Observed runtime check:

```json
{
  "duplicate_recovery_records": 1,
  "duplicate_provider_id_records": 1
}
```

This proves the lock contention path prevented duplicate recovery and duplicate provider mapping writes.

## 10. Transaction Verification

The lock implementation uses a transaction-scoped PostgreSQL advisory lock and the recovery flow wraps the state machine in a single database transaction.

The behavior observed at runtime was:

- a single successful recovery transition;
- no partial recovery state;
- no duplicate update persisted;
- the second execution did not generate a separate durable recovery record or provider identity row;
- no stuck lock remained after the operation completed.

This is consistent with the resource lock being acquired and released in the same transaction lifecycle as the recovery work.

## 11. Runtime Log Evidence

The implementation logs lock acquisition and contention in [app/services/resource_lock.py](app/services/resource_lock.py):

- `RESOURCE_LOCK_ACQUIRE_ATTEMPT`
- `RESOURCE_LOCK_ACQUIRED`
- `RESOURCE_LOCK_CONFLICT`
- `RESOURCE_LOCK_RELEASED`

In the recovery service, the runtime path logs the provider lookup and recovery state transition as it processes the request. The observed runtime result demonstrates that only one execution path successfully moved the temporary League to the resolved state while the second path observed the already-resolved state instead of duplicating the work.

## 12. Cleanup Verification

The temporary League and recovery record were removed after verification so no temporary validation data remained in PostgreSQL.

Cleanup command result:

```text
DELETE 2
DELETE 2
```

This confirms only the temporary test rows were cleaned up, without touching production League/business data.

## 13. PASS/FAIL Classification

Classification: LOCK CONTENTION VERIFIED

Reason:

- two independent concurrent recovery executions were triggered against the same temporary League;
- the runtime result showed both calls completing successfully without duplicate recovery writes;
- the final database state contained exactly one provider_id and one final successful recovery transition;
- no duplicate recovery row or provider identity row was created;
- cleanup succeeded without leaving temporary verification data behind.

## 14. Remaining Phase 4.5 Checks

The remaining checks are intentionally not included in this step:

- Step 4: Repeat FixtureSync verification
- any broader Phase 4.5 completion classification

This report only covers the isolated real Docker lock-contention runtime verification for League Identity Recovery.
