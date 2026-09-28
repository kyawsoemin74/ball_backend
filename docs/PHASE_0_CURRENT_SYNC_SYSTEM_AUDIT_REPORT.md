# PHASE 0 CURRENT SYNC SYSTEM AUDIT REPORT

## 1. Executive Summary

Status: PARTIALLY ALIGNED

The current backend is not a clean three-way separation of Fixture Sync, Live Sync, and Final Live Sync. The code clearly shows a shared sync engine and a shared global transaction-scoped resource lock, and the date-sync endpoint does not remain isolated from terminal finalization logic.

Evidence summary:

- The API date-sync route in [app/api/matches.py](../app/api/matches.py) calls `football_service.sync_daily_fixtures()` inside a `run_with_resource_lock(..., "fixture_query", "global", ...)` wrapper.
- The scheduler live job in [app/services/scheduler.py](../app/services/scheduler.py) also calls `run_with_resource_lock(..., "fixture_query", "global", ...)` around `football_service.sync_live_matches()`.
- The shared processing layer in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py) contains both `_process_sync_with_candidates()` and `handle_terminal_transition()`. When a terminal status is detected, it calls `final_live_sync()`, which does a fresh provider fetch by `provider_fixture_id` and validates a terminal final response.
- Therefore, Fixture Sync can indirectly trigger Final Live Sync during a terminal transition, and the system uses the same global lock for multiple sync classes.

This is evidence of partial alignment, not clean separation.

## 2. Fixture Sync Audit

### 2.1 Entry point

The date fixture route is defined in [app/api/matches.py](../app/api/matches.py):

```python
@router.post("/sync/{date_val}", status_code=status.HTTP_200_OK, dependencies=[Depends(current_active_admin)])
async def sync_daily_matches(
    date_val: date = Path(..., description="The date to sync (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
```

The handler calls:

```python
result = await football_service.sync_daily_fixtures(db=db, target_date=date_val.isoformat())
```

### 2.2 Service path

The service method is in [app/services/football.py](../app/services/football.py):

```python
async def sync_daily_fixtures(self, db: AsyncSession, target_date: str) -> dict:
    return await self.fixture_sync_service.sync_daily_fixtures(db, target_date)
```

The actual implementation is in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py):

```python
@observe_sync("fixture")
async def sync_daily_fixtures(self, db: AsyncSession, target_date: str) -> dict:
    result = await self.fixture_provider.get_fixtures_by_date(target_date=target_date)
    ...
    sync_result, prewarm_candidates = await self._process_sync_with_candidates(db, fixtures)
```

### 2.3 Provider call

This implementation uses the provider API date endpoint:

```python
result = await self.fixture_provider.get_fixtures_by_date(target_date=target_date)
```

### 2.4 Identity resolution and persistence

The date fixture sync passes provider fixtures through the shared sync path in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py):

```python
sync_result, prewarm_candidates = await self._process_sync_with_candidates(db, fixtures)
```

Inside `_process_sync_with_candidates()`, it:

- filters leagues
- resolves provider teams
- creates or updates `Match` records with an upsert
- calls `handle_terminal_transition()` when a status transition is detected
- calls `active_match_service` registration updates
- optionally does `finalize_pending_lineups()` after the transaction completes in the outer route and scheduler

### 2.5 Transaction owner

The outer transaction owner in the API route is the route transaction itself, not `FixtureSyncService`. The route code in [app/api/matches.py](../app/api/matches.py) does:

```python
if not result.get("success"):
    await db.rollback()
    return result
await db.commit()
```

Similarly, the scheduler jobs in [app/services/scheduler.py](../app/services/scheduler.py) own the transaction and commit or rollback around sync work.

### 2.6 Lock

The route wraps the sync body with the global resource lock:

```python
locked, result = await run_with_resource_lock(db, "fixture_query", "global", sync)
```

The implementation is in [app/services/resource_lock.py](../app/services/resource_lock.py):

```python
_ADVISORY_LOCK_SQL = "SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_identity, 0))"
```

### 2.7 Cache behavior

After a successful commit, the route invalidates live-match cache:

```python
await CacheService().delete(make_cache_key("live_matches"))
```

and can finalize pending lineups:

```python
if "final_lineup_candidates" in result:
    await football_service.finalize_pending_lineups(result.get("final_lineup_candidates", []))
```

This is after the sync and commit path in the route.

### 2.8 Downstream operations

The date-sync route does not directly call `sync_live_matches()` or `final_live_sync()`. However, during the shared fixture processing path, terminal transitions can trigger finalization logic. That is the key boundary concern.

## 3. Live Sync Audit

### 3.1 Scheduler registration

The scheduler and job registrations are in [app/services/scheduler.py](../app/services/scheduler.py):

```python
self.scheduler.add_job(
    self._sync_live_matches_job,
    trigger=IntervalTrigger(seconds=60),
    id="sync_live_matches",
    name="Sync Live Matches",
    max_instances=1,
)
```

### 3.2 Live sync service path

The live sync job calls:

```python
result = await football_service.sync_live_matches(db)
```

and the service method is in [app/services/football.py](../app/services/football.py):

```python
async def sync_live_matches(self, db: AsyncSession) -> dict:
    return await self.fixture_sync_service.sync_live_matches(db)
```

The implementation in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py):

```python
@observe_sync("fixture")
async def sync_live_matches(self, db: AsyncSession) -> dict:
    result = await self.fixture_provider.get_live_fixtures()
    ...
    return await self._process_sync(db, fixtures)
```

### 3.3 Provider endpoint

Live sync uses the provider live feed:

```python
result = await self.fixture_provider.get_live_fixtures()
```

It also fetches stale live matches by provider IDs:

```python
stale_resp = await self.fixture_provider.get_fixtures_by_ids(chunk)
```

### 3.4 Database writes

This path writes to the same `Match` persistence through the shared processing path `_process_sync()` -> `_process_sync_with_candidates()`.

### 3.5 Lock and transaction

The live sync job wraps the whole live sync work in a `run_with_resource_lock(..., "fixture_query", "global", sync_live)` call in [app/services/scheduler.py](../app/services/scheduler.py):

```python
resource_locked, result = await run_with_resource_lock(
    db, "fixture_query", "global", sync_live
)
```

This is the same global lock identity used by the API date sync route.

### 3.6 Cache invalidation

Within the live job, after commit:

```python
await self.cache_service.delete(make_cache_key("live_matches"))
```

The live sync path is therefore operating through the same database write and cache pattern as the date fixture sync, not through a separate live-only boundary.

## 4. Final Live Sync Audit

### 4.1 Where it is triggered

The terminal transition logic is in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py):

```python
async def handle_terminal_transition(
    self,
    db: AsyncSession,
    local_match_id: int,
    provider_fixture_id: int,
    previous_status: str | None,
    normalized_status: str,
    result: dict,
) -> None:
    if previous_status not in NON_TERMINAL_STATUSES:
        return
    if normalized_status not in FINAL_LINEUP_TERMINAL_STATUSES:
        return

    final_sync = await self.final_live_sync(
        db,
        int(provider_fixture_id),
        trigger_status=normalized_status,
        local_match_id=local_match_id,
    )
```

### 4.2 Fresh provider fetch

The `final_live_sync()` method is in the same file and does the fresh fetch:

```python
response = await self.fixture_provider.get_fixtures_by_ids([str(provider_fixture_id)])
```

and validates `final_status` against terminal statuses:

```python
if final_status not in FINAL_LINEUP_TERMINAL_STATUSES:
    metrics.update({"reason": "fresh_response_non_terminal", "failure_category": "NON_TERMINAL_FINAL_RESPONSE"})
    return metrics
```

### 4.3 Persistence

After the fresh fetch validates as terminal, it reuses `_process_sync_with_candidates()` to persist the final state.

```python
sync_result, _ = await self._process_sync_with_candidates(db, [final_fixture])
```

### 4.4 Lock

`final_live_sync()` acquires a different lock identity than the global fixture lock:

```python
locked, outcome = await run_with_resource_lock(
    db,
    "fixture",
    provider_fixture_id,
    sync_final_fixture,
)
```

The identity is a per-match resource lock, not the global `fixture_query:global` lock.

### 4.5 Transaction boundary

The fresh final provider fetch occurs inside the same existing transaction context before the outer route or scheduler commit. The function does not create a totally separate transaction boundary. It relies on the surrounding caller transaction and commit logic.

## 5. Sync Boundary Audit

### Confirmed behavior

The current system does not fully separate the three responsibilities by architecture.

- Fixture Sync: date/season sync path through `FixtureSyncService.sync_daily_fixtures()` / `sync_full_season()`
- Live Sync: scheduler `sync_live_matches()` -> `FixtureSyncService.sync_live_matches()`
- Final Live Sync: terminal transition detection -> `FixtureSyncService.final_live_sync()`

These paths are not isolated into three completely independent subsystems. They share:

1. the same `FixtureSyncService` processing engine
2. the same `Match` persistence flow
3. the same global `fixture_query:global` lock
4. the same terminal-transition logic used within shared processing

### Direct / indirect call evidence

The route `/sync/{date}` does not directly call `sync_live_matches()`, but within `_process_sync_with_candidates()` it may call `handle_terminal_transition()`, which calls `final_live_sync()`. This is an indirect Final Live Sync trigger from the date-sync path.

This means the route is not strictly “Fixture Sync only.”

## 6. Lock Audit

### Observed actual implementation

The core shared lock implementation is in [app/services/resource_lock.py](../app/services/resource_lock.py):

```python
_RESOURCE_PREFIX = "fover:sync"
_ADVISORY_LOCK_SQL = "SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_identity, 0))"
```

The identity is created by:

```python
return f"{_RESOURCE_PREFIX}:{normalized_type}:{normalized_identity}"
```

For the global fixture sync path:

```python
run_with_resource_lock(db, "fixture_query", "global", sync)
```

This becomes:

`fover:sync:fixture_query:global`

### Actual lock separation

Observed actual lock identities are:

- `fixture_query:global` for date/season/live/repair scheduler operations
- `fixture:<provider_fixture_id>` for `final_live_sync()`
- other resource-specific locks such as `lineup`, `events`, `statistics`, and `h2h`

This is not the target architecture of distinct `Fixture Sync / Live Sync / Final Live Sync` lock identities.

### Concurrency effect

Because the same `fixture_query:global` lock is used by API date-sync and scheduler live/daily/repair jobs, these operations cannot safely run concurrently. The code is intentionally designed to serialize them through the same lock.

## 7. Transaction Audit

### API route behavior

The API route performs the transaction itself:

```python
if not result.get("success"):
    await db.rollback()
    return result
await db.commit()
```

### Scheduler behavior

The scheduler jobs also perform outer transaction ownership in the same pattern.

### Service behavior

`FixtureSyncService` does not own the outer commit itself in the main route or scheduler flow. It performs repository execs and flushes but the route/scheduler commit remains the boundary.

This is aligned with a shared transaction ownership model, but not with a clean separation between sync classes.

## 8. Cache Audit

### Fixture Sync

Route and scheduler both invalidate `live_matches` after commit. This is done after `await db.commit()`.

### Live Sync

The live scheduler job invalidates `live_matches` after commit as well.

### Final Live Sync

`final_live_sync()` does not explicitly invalidate a dedicated final-live cache in the code excerpt; it persists final state and returns success. It relies on the surrounding outer path and downstream `finalize_pending_lineups()` calls.

This is partially aligned with post-commit invalidation, but it is not a strict clean separation across sync classes.

## 9. Scheduler Audit

### Scheduler owner

The scheduler is created and started in [app/services/scheduler.py](../app/services/scheduler.py) and started via [scheduler_service.py](../scheduler_service.py) and [worker.py](../worker.py).

Key registration:

```python
self.scheduler.add_job(self._sync_live_matches_job, trigger=IntervalTrigger(seconds=60), id="sync_live_matches", max_instances=1)
self.scheduler.add_job(self._sync_daily_fixtures_job, trigger=CronTrigger(hour=0, minute=1, timezone=MM_TZ), id="sync_daily_fixtures", max_instances=1)
self.scheduler.add_job(self._repair_daily_matches_job, trigger=CronTrigger(hour=2, minute=0, timezone=MM_TZ), id="repair_daily_matches", max_instances=1)
```

### Overlap prevention

Jobs are configured with `max_instances=1`, but they still share the same global resource lock identity for fixture sync operations, so they serialize through the same transaction-scoped advisory lock even when job overlap prevention is technically enforced.

### Potential overlap

A scheduled live sync can overlap conceptually with manual API date sync because they both use the same global lock identity. The scheduler and API are not separated by different lock identities.

## 10. 409 Conflict Audit

### Exact response source

The 409 is raised in [app/api/matches.py](../app/api/matches.py):

```python
locked, result = await run_with_resource_lock(db, "fixture_query", "global", sync)
if not locked:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Fixture sync already in progress")
```

### What it means in practice

This code path reports a conflict when another caller previously acquired the same `fixture_query:global` transaction-scoped advisory lock. The system therefore treats date sync, live sync, and repair sync as one global fixture-sync class.

### Classification

Evidence supports:

`SCHEDULER/API CONCURRENCY`

or, if exact owner is not proven at runtime:

`ROOT CAUSE NOT PROVEN`

The code proves same-lock contention is possible across API and scheduler callers. The exact owner at a particular 409 event is not proven by code alone.

## 11. Current Call Graphs

### Fixture Sync call graph

```text
POST /api/matches/sync/{date}
        ↓
app.api.matches.sync_daily_matches()
        ↓
football_service.sync_daily_fixtures()
        ↓
FixtureSyncService.sync_daily_fixtures()
        ↓
FixtureProvider.get_fixtures_by_date()
        ↓
FixtureSyncService._process_sync_with_candidates()
        ↓
Match upsert / repository writes
        ↓
DB commit / rollback
        ↓
live_matches cache invalidation
```

### Live Sync call graph

```text
Scheduler: sync_live_matches
        ↓
app.services.scheduler._sync_live_matches_job()
        ↓
football_service.sync_live_matches()
        ↓
FixtureSyncService.sync_live_matches()
        ↓
FixtureProvider.get_live_fixtures()
        ↓
FixtureSyncService._process_sync_with_candidates()
        ↓
Match upsert / repository writes
        ↓
DB commit / rollback
        ↓
live_matches cache invalidation
```

### Final Live Sync call graph

```text
Terminal status transition detected in FixtureSyncService._process_sync_with_candidates()
        ↓
handle_terminal_transition()
        ↓
final_live_sync()
        ↓
FixtureProvider.get_fixtures_by_ids([provider_fixture_id])
        ↓
validate FT / AET / PEN status
        ↓
_process_sync_with_candidates() again
        ↓
Match persistence
        ↓
outer transaction commit / rollback
```

## 12. Target vs Current Comparison

| Area | Current Implementation | Target Design | Status |
|---|---|---|---|
| Fixture Sync | API date/season path and scheduler daily/repair path | Date fixture sync only | PARTIALLY ALIGNED |
| Live Sync | scheduler live job and same processing engine | live-match sync only | PARTIALLY ALIGNED |
| Final Live Sync | terminal transition inside shared processing | terminal-only final provider fetch | PARTIALLY ALIGNED |
| Fixture → Live separation | same global `fixture_query:global` lock and same processing path | separate execution boundary | NOT ALIGNED |
| Live → Fixture separation | same shared engine and lock | separate execution boundary | NOT ALIGNED |
| Terminal transition | handled directly in `_process_sync_with_candidates()` | explicit final-live pipeline | PARTIALLY ALIGNED |
| Fresh final provider fetch | yes, in `final_live_sync()` | yes | ALIGNED |
| Lock separation | not separated; same global lock for date/live/repair | separate locks | NOT ALIGNED |
| Transaction ownership | outer API/scheduler commit/rollback | outer API/scheduler commit/rollback | ALIGNED |
| Cache invalidation | post-commit invalidation in route/job | post-commit invalidation | ALIGNED |
| Scheduler ownership | worker + APScheduler by code | dedicated worker scheduler expected | PARTIALLY ALIGNED |
| 409 behavior | same identity conflict across callers | same identity conflict only when legitimate concurrency exists | PARTIALLY ALIGNED |

## 13. Confirmed Gaps

### CONFIRMED

- The same global `fixture_query:global` lock is used by the API date sync route and the scheduler live/daily/repair jobs.
- The same shared `FixtureSyncService` processing engine is used by date sync, live sync, and final terminal logic.
- The date-sync route can indirectly invoke `final_live_sync()` through terminal status detection.
- There is no clean lock separation between Fixture Sync and Live Sync.

### UNKNOWN

- The exact owner of a specific runtime 409 cannot be proven from code alone without a live conflict capture.
- Whether a given 409 was caused by scheduler overlap versus API overlap at the exact timestamp cannot be proven without runtime lock owner evidence.

### NOT OBSERVED

- A distinct Live Sync lock identity separate from `fixture_query:global`
- A distinct Final Live Sync lock identity separate from per-match `fixture:<id>`
- A fully isolated service boundary where Fixture Sync never enters the terminal transition/final provider flow

## 14. Recommended Next Phase

Recommended next phase: implement a dedicated runtime lock/workflow audit that captures the exact owner of `fover:sync:fixture_query:global` during a natural conflict, then separate the live-sync and final-live-sync lock identities and execution boundaries without changing the current runtime behavior in Phase 0.

---

## Final Decision

### Q1
Does `POST /api/matches/sync/{date_val}` perform Fixture Sync only?

Answer: NO.

Reason: the shared processing path can invoke terminal transition handling and `final_live_sync()` from the same data sync flow.

### Q2
Does Fixture Sync call Live Sync directly or indirectly?

Answer: INDIRECTLY, via shared `FixtureSyncService` processing and terminal transition logic; not via a direct method call from the route itself.

### Q3
Does Live Sync operate independently from Date Fixture Sync?

Answer: NOT FULLY.

Reason: the scheduler live job and the API date sync both share the same `fixture_query:global` lock and same `FixtureSyncService` processing path.

### Q4
When a terminal transition occurs, does Final Live Sync perform a fresh provider fetch?

Answer: YES.

Reason: `final_live_sync()` explicitly calls `get_fixtures_by_ids([provider_fixture_id])` and validates the response status before persisting final state.

### Q5
Are Fixture Sync, Live Sync and Final Live Sync using appropriately separated locks?

Answer: NO.

Reason: `Fixture Sync` and scheduler `Live Sync` both use the same `fixture_query:global` lock, and final live sync uses a per-match `fixture:<id>` lock instead of a dedicated final-sync class lock.

### Q6
Can Fixture Sync and Live Sync safely execute concurrently?

Answer: NO.

Reason: same global resource identity and same transaction-scoped advisory lock make them mutually exclusive by design.

### Q7
What exactly causes the observed `409 Fixture sync already in progress`?

Answer: ROOT CAUSE NOT PROVEN.

Reason: the code proves that the route and scheduler jobs share the same global lock identity, but the exact runtime owner at a specific 409 event cannot be proven from code or a post-request snapshot alone without a live conflict capture.

### Q8
What exact implementation changes, if any, are required to reach the target architecture?

Answer: This phase did not implement change. The next implementation phase should separate the sync classes by lock identity and call boundary, enforce final-live-sync as a distinct terminal transition pipeline, and keep date fixture sync isolated from live and terminal finalization work.
