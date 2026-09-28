# PHASE 4.5 - League Identity Recovery Final Runtime Verification Report

Verification date: 2026-09-22  
Scope: read-only-first runtime audit with controlled temporary-data verification. No public synchronization endpoint was called and no existing production League was modified.

## 1. Executive Summary

The recovery implementation is **not fully verified in the real Docker runtime**.

Controlled host/PostgreSQL verification previously proved the core workflow:

```text
missing provider_id
 -> League Recovery
 -> exact name/country match
 -> provider_id persistence
 -> durable recovery state
 -> FixtureSync re-resolution
 -> fixture provider request
 -> Team resolution
 -> Match persistence
 -> repeat recovery no-op
```

The current Docker runtime was rebuilt and restarted. API, Worker, PostgreSQL, and Redis are healthy, and Worker logs show the recovery scheduler job registered. However, Docker PostgreSQL remains at migration `20260915_missing_lineup_identity` and does not contain `league_identity_recovery`. Applying Alembic inside the container is blocked by the image missing `libpq.so.5` for the synchronous PostgreSQL driver.

Because the Docker recovery table is absent, live scheduler retry, Worker restart persistence, Docker lock contention, and Docker end-to-end recovery cannot be proven. Final classification: **B. IMPLEMENTATION PARTIALLY VERIFIED**.

## 2. Previous Verification Status

The prior report classified Phase 4.5 as **B. IMPLEMENTATION PARTIALLY VERIFIED**.

Previously verified with temporary PostgreSQL data:

- identity candidate matching;
- identity persistence;
- recovery state and retry metadata;
- FixtureSync re-resolution;
- fixture provider continuation;
- Team resolution and Match insertion;
- repeat recovery idempotency;
- focused HTTP 409 mapping.

Previously unverified:

- live Scheduler retry;
- Worker restart recovery;
- two-process lock contention;
- full post-fix regression;
- repeated runtime FixtureSync upsert.

## 3. Current Runtime Environment

### Docker services

`docker compose ps` showed:

| Service | Runtime state |
|---|---|
| API | Up, healthy |
| Worker | Up, healthy |
| PostgreSQL | Up, healthy |
| Redis | Up, healthy |

API readiness returned:

```json
{"status":"ready","postgres":true,"redis":true}
```

The rebuilt Worker successfully imported the recovery module and reported:

```text
max_attempts=5
retry_delays=(15, 30, 60, 120, 240)
```

Worker startup logs showed:

```text
Added job "Recover League Identities"
Scheduler started
SCHEDULER_STARTED jobs=[..., 'recover_league_identities', ...]
```

### Docker PostgreSQL state

Read-only Docker PostgreSQL query returned:

```text
alembic_version = 20260915_missing_lineup_identity
leagues = 1245
league_seasons = 47
allowed_leagues = 3
matches = 1270
```

`to_regclass('public.league_identity_recovery')` returned null. The recovery migration has not been applied to the Docker database.

## 4. Scheduler Retry Verification

**PARTIALLY VERIFIED**

### Verified

- Recovery Scheduler job is registered in the rebuilt Worker.
- Job interval is 15 minutes and `max_instances=1`.
- Source and focused tests verify durable retry candidate selection and League-domain invocation.
- Host controlled PostgreSQL test verified a zero-candidate failure persisted:

```text
state = IDENTITY_RESOLUTION_RETRY
attempt_count = 1
next_retry_at = now + 15 minutes
last_error_code = NO_CANDIDATE
```

### Not verified

- Live Docker Scheduler discovery of a retry record.
- Live Docker second recovery attempt.
- Backoff timing observed across scheduler cycles.
- Success clearing retry state in the running Worker.

Reason: Docker PostgreSQL lacks the recovery table, and the migration could not be applied inside the image because Alembic raised:

```text
ImportError: libpq.so.5: cannot open shared object file
```

## 5. Worker Restart Verification

**NOT VERIFIED**

Worker restart was not used to claim persistence because the Docker recovery table is absent. The database model is durable in the host-controlled migration/database verification, but the actual Docker Worker/PostgreSQL deployment has not been migrated.

Verified only:

- Worker starts successfully with the recovery code.
- Scheduler registers the recovery job after restart/recreate.

Not verified:

- retry state surviving a Worker restart in Docker;
- attempt count continuing from PostgreSQL;
- pending recovery rediscovery after restart;
- successful recovery clearing pending state after restart.

## 6. Advisory Lock Contention Verification

**PARTIALLY VERIFIED**

### Verified

- Recovery uses the existing PostgreSQL transaction-scoped resource lock:

```text
fover:sync:league_identity:<league_id>
```

- Lock release is protected by the helper’s `finally` path.
- Focused tests and source checks cover the lock integration.

### Not verified

- Two independent Docker Worker/process contexts contending on the same League.
- A real second process skipping while the first holds the lock.
- Same-League API/Scheduler contention in Docker.
- Different-League parallel recovery in Docker.

The absent Docker recovery table prevented a safe real recovery contention test.

## 7. Full Regression Results

### Focused recovery regression

```text
6 passed, 3 existing Pydantic deprecation warnings
```

### Full repository suite

Earlier full-suite run:

```text
34 failed, 667 passed, 19 warnings
```

Failure groups were primarily unrelated Odds, Lineup, Team, and existing fixture-lock expectations. The directly affected scheduler registration expectation was updated for the approved recovery job and passes in the focused suite.

### Alembic consistency

Recovery-specific ORM metadata drift was corrected. Current `alembic check` still fails because of unrelated existing differences:

```text
match_lineups index rename/drift
matches provider_fixture_id index drift
```

Recovery-specific migration alignment is no longer among the reported drift items.

Classification: **PARTIALLY VERIFIED; PRE-EXISTING UNRELATED FAILURES REMAIN**.

## 8. Repeat FixtureSync Verification

**PARTIALLY VERIFIED**

Host-controlled temporary PostgreSQL verification proved one recovery followed by FixtureSync:

```text
fixture provider call: league=987654, season=2026
FixtureSync result: inserted=1, updated=0, total=1, failed=0
Match count: 1
```

Repeated recovery returned:

```json
{
  "success": true,
  "state": "IDENTITY_RESOLVED",
  "already_resolved": true,
  "attempt_count": 1
}
```

This proved no duplicate recovery state/provider lookup on repeat recovery.

Not verified in this phase:

- a second full runtime FixtureSync call through the Docker deployment;
- database Match upsert comparison across two complete FixtureSync executions;
- timestamp/duplicate/cache comparison after the second full sync.

## 9. End-to-End Recovery Verification

**PARTIALLY VERIFIED**

The complete flow was proven in a controlled host PostgreSQL test using temporary data:

| Stage | Result |
|---|---|
| Missing provider ID | VERIFIED |
| Recovery triggered | VERIFIED |
| Provider candidate exact name/country validation | VERIFIED |
| Provider ID persisted | VERIFIED |
| Recovery state success | VERIFIED |
| League re-resolution | VERIFIED, including cross-session refresh fix |
| Fixture Provider request | VERIFIED with recovered ID `987654` |
| Team resolution | VERIFIED |
| Match persistence | VERIFIED, one temporary Match |
| Repeat recovery | VERIFIED as no-op |
| Docker runtime repetition | NOT VERIFIED |

The public sync endpoint was not called, per safety requirements.

## 10. Database Integrity Verification

### Host controlled database

Previously verified:

- recovery state unique per League;
- recovery FK to League;
- retry index;
- Match provider fixture uniqueness;
- LeagueSeason uniqueness and FK behavior;
- temporary rows cleaned in FK order.

### Docker database

Read-only baseline captured:

```text
Leagues: 1245
LeagueSeasons: 47
AllowedLeagues: 3
Matches: 1270
Recovery table: absent
```

Not verified in Docker:

- recovery-state consistency;
- orphan recovery records;
- recovery table uniqueness/indexes;
- Docker post-recovery League/Season/AllowedLeague/Team/Match integrity.

## 11. Runtime Health Verification

**PARTIALLY VERIFIED**

Verified:

- API readiness: PostgreSQL and Redis true;
- API container healthy;
- Worker container healthy;
- PostgreSQL healthy;
- Redis healthy;
- Worker scheduler starts and registers recovery job;
- rebuilt Worker imports recovery implementation.

Current logs did not show recovery-specific exceptions, but the recovery job cannot complete database work while its table is absent. PostgreSQL logs showed an earlier unclean shutdown with automatic recovery; PostgreSQL subsequently reported healthy.

Not verified:

- Docker recovery execution;
- Docker recovery transaction errors after migration;
- current live lock behavior;
- current live duplicate-key behavior during recovery.

## 12. Evidence Matrix

| Verification | Evidence | Status |
|---|---|---|
| Docker services | `docker compose ps` | VERIFIED |
| API readiness | `GET /health/ready` | VERIFIED |
| Worker recovery code | Docker import check | VERIFIED |
| Worker scheduler registration | Worker logs | VERIFIED |
| Docker migration | `alembic_version=20260915_missing_lineup_identity`; recovery table absent | FAILED / BLOCKED |
| Provider discovery transport | Read-only `/leagues?name=Premier League`, 35 candidates | VERIFIED transport only |
| Exact candidate validation | Controlled provider payload + focused tests | VERIFIED |
| Identity persistence | Host temporary PostgreSQL | VERIFIED |
| Retry persistence | Host temporary PostgreSQL | VERIFIED |
| Scheduler live retry | No migrated Docker table | NOT VERIFIED |
| Worker restart persistence | No migrated Docker table | NOT VERIFIED |
| Multi-process lock | No safe Docker recovery record | NOT VERIFIED |
| Fixture continuation | Host temporary PostgreSQL | VERIFIED |
| Match persistence | Host temporary PostgreSQL | VERIFIED |
| Repeat recovery | Host temporary PostgreSQL | VERIFIED |
| Repeat full FixtureSync | Not executed twice in runtime | NOT VERIFIED |
| Full regression | 34 unrelated failures remain | PARTIALLY VERIFIED |
| Alembic check | Recovery drift fixed; unrelated drift remains | PARTIALLY VERIFIED |
| Runtime health | API/Worker/PostgreSQL/Redis healthy | VERIFIED |

## 13. Current Defects

### Direct deployment defect

The Docker image cannot run Alembic because the synchronous PostgreSQL driver requires `libpq.so.5`, which is absent from the image:

```text
ImportError: libpq.so.5: cannot open shared object file
```

Consequently, Docker PostgreSQL remains at the pre-Phase-4.5 migration and lacks `league_identity_recovery`.

### Verification consequence

The Worker is running the new scheduler code against a database that does not have the required recovery schema. This is a production-readiness blocker for runtime recovery, not evidence that the recovery workflow itself is incorrect.

### Unrelated drift

`alembic check` still reports existing MatchLineup/Match index differences. These were not changed during this verification because they are outside League Identity Recovery scope.

## 14. Remaining Limitations

1. Docker recovery migration is not applied.
2. Docker Scheduler retry is not verified.
3. Worker restart persistence is not verified in the migrated runtime.
4. Two-process advisory-lock contention is not verified in Docker.
5. Full FixtureSync repeat/idempotency is not verified twice in the runtime.
6. Full repository regression remains red due to unrelated existing failures.
7. Alembic consistency remains red due to unrelated index drift.
8. No public sync endpoint was called, so API behavior was verified through focused handler tests and readiness checks rather than a live authenticated mutation request.

## 15. Final Phase 4.5 Classification

**B. IMPLEMENTATION PARTIALLY VERIFIED**

Do not upgrade to A. The core workflow works in controlled host PostgreSQL verification, but the actual Docker deployment is not migration-ready and the required runtime retry/restart/concurrency evidence is missing.

## 16. Production Readiness

**NOT READY FOR PRODUCTION PROMOTION**

Required blockers before promotion:

1. Fix the Docker image/Alembic PostgreSQL driver dependency so migrations run in the deployment image.
2. Apply and verify `20260922_league_identity_recovery` and `20260922_align_recovery_indexes` in the Docker PostgreSQL environment.
3. Re-run Scheduler retry with a controlled temporary recovery record.
4. Restart Worker and prove retry-state rediscovery.
5. Run two-process same-League lock contention.
6. Run repeat FixtureSync and compare database state.
7. Re-run the full relevant regression suite and separate unrelated failures.
8. Resolve or explicitly baseline remaining Alembic drift before release.

## 17. Next Phase Recommendation

The next action is a deployment-image migration-readiness fix and verification phase, not a recovery-architecture redesign:

1. Add the required PostgreSQL client runtime dependency to the Docker image or make Alembic use an already-supported async-compatible migration path.
2. Rebuild/restart the controlled Docker stack.
3. Apply migrations through Alembic in-container.
4. Verify recovery table/index/FK state.
5. Execute controlled Scheduler retry, Worker restart, two-process lock, and repeat FixtureSync tests.
6. Reclassify only after all required runtime evidence is captured.

Final answer to the required question:

> Is League Identity Recovery now proven to work correctly across normal execution, retry, Worker restart, concurrent execution, and repeat FixtureSync in the real runtime?

**NO.** Normal controlled recovery and FixtureSync continuation are proven, but Docker retry, restart, concurrent execution, and complete repeat-sync runtime evidence are not yet proven.
