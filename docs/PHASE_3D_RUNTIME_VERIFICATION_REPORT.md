# Phase 3D — Runtime Verification Report

**Project:** Fover Backend  
**Module:** Event  
**Mode:** Runtime verification only  
**Verification date:** 2026-10-07  
**Verdict:** PARTIAL

## 1. Executive Summary

The local Docker Compose runtime is available and healthy. The API and worker
connect to the Compose PostgreSQL database `fover_db` and Redis DB 0; the
worker scheduler reports healthy and its startup log registers the Event
refresh job. Runtime source inspection confirms the containers include the
Phase 3D live-status filter, finalization Event lock/sync, and historical
backfill guards.

No safe live Match, live registry entry, or naturally transitioning
`FT`/`AET`/`PEN` fixture was available. No provider sync, backfill, or failure
injection was performed.

During a read-only inspection and a GET of an existing finished Match, an
unexpected legacy data condition was observed: Match `1366` has canonical
Match-side Team IDs `98` and `97`, whose Team Master provider IDs are `774`
and `5`. Its two persisted Events instead have `team_id` values `774` and `5`.
The Event API returned those same values. The Events therefore do not match
the Match's canonical Team IDs in this runtime database. Consistent with the
safety rules, verification stopped at discovery. No sync, repair, or
historical-data change was attempted.

The GET populated only the intended per-Match Event cache key; before the GET
the key was absent, and afterward it existed with a 21,600-second TTL. No
database rows were changed by this verification.

## 2. Environment Verification

| Component | Runtime evidence |
|---|---|
| API | Compose container `fover_api`, healthy; `/health/ready` returned `{"status":"ready","postgres":true,"redis":true}`. |
| Worker | Compose container `fover_worker`, healthy. |
| PostgreSQL | Compose service/container `fover_postgres`; actual API and worker SQLAlchemy URLs resolve to host `postgres`, database `fover_db`. |
| Redis | Compose service/container `fover_redis`; actual API and worker settings resolve to host `redis`, DB `/0`. |
| API scheduler setting | `SCHEDULER_ENABLED=false`; scheduler ownership is in the worker. |
| Worker scheduler setting | `SCHEDULER_ENABLED=true`. |
| Worker health metric | `fover_scheduler_up 1.0`. |
| Scheduler startup log | `SCHEDULER_STARTED` listed `refresh_events` among the running jobs. |
| Scheduler cadence | Source configures active Event refresh every 600 seconds. No natural Event-refresh execution log was available during this observation window. |
| Git commit | `097260faede4fbaecd1b397caaa6ebcc17701aca`. |
| Worktree | Dirty with Phase 3D implementation files, pre-existing Phase 3 Event identity changes/documents, and tests. No source/test files were modified during this runtime-verification phase. |
| Running image | API image `sha256:1e454657a6535e08f6bc7d430792c37dc2982d4390f3809b5a4889db8b5c897f`; worker image `sha256:cdfac6d6df548797e7e418c7f23be153ec8cc470164eb4382f3b8c76568ede7e`. Both containers were created at approximately 2026-10-07 06:43 UTC. |
| Runtime code check | Both API and worker imports showed the live status gate, final Event sync call, and backfill existing-row guard are present. |
| Alembic revision | `20261006_auth_sessions`. |

## 3. Database Verification

The database was queried through the running Compose PostgreSQL service. Each
inspection used `BEGIN TRANSACTION READ ONLY` and ended with `ROLLBACK`.
`current_database()`, `current_user`, and
`current_setting('transaction_read_only')` returned `fover_db`, `user`, and
`on`. The `matches`, `match_events`, `match_finalization`, and
`alembic_version` tables exist.

Observed aggregate state:

| Data | Read-only result |
|---|---:|
| Match rows | 1,156 |
| `FT` Matches | 600 |
| `AET` Matches | 0 |
| `PEN` Matches | 0 |
| `1H`/`HT`/`2H`/`LIVE` Matches | 0 |
| Past scheduled times | 613 |
| Event rows | 463 |
| Finalization rows | 9, all `SUCCESS` |
| Match `476` in this runtime database | 0 rows |
| Event rows for Match `476` in this runtime database | 0 rows |

Match `476` is absent from this Compose database, so its Phase 3A historical
snapshot cannot be compared with a baseline here. This does not establish
that the Phase 3A database and this runtime database are the same database.

## 4. Redis Verification

- Redis is the instance configured for both API and worker: `redis:6379/0`.
- A read-only scan found no `fover:active_match:*` keys and no existing
  `fover:match:*:events` keys at the time of inspection.
- Before the Event API GET for Match `1366`, the exact key
  `fover:match:1366:events` did not exist (`EXISTS=0`, `TTL=-2`).
- After the GET, it existed (`EXISTS=1`) with TTL `21600`.
- No Redis flush, key deletion, or broad cache operation was performed.

## 5. Live Event Refresh

**Result: NOT VERIFIED — NO SAFE NATURAL FIXTURE**

The database had no Match in the configured live Event status set, and the
active Match registry was empty. The worker was healthy and the Event refresh
job was registered, but there was no eligible Match for the scheduler to
discover. No Event provider call was made, and no Event rows were inserted or
replaced.

## 6. FT Final Event Sync

**Result: NOT VERIFIED — NO SAFE NATURAL FIXTURE**

The database had `FT` Matches but no live-to-`FT` transition was observed
during this verification. Existing terminal rows are not safe substitutes
for a natural transition. No final Event sync was triggered.

## 7. AET Final Event Sync

**Result: NOT VERIFIED — NO SAFE NATURAL FIXTURE**

No `AET` Match existed in the runtime database, and no natural transition was
observed. No provider call or state change was made.

## 8. PEN Final Event Sync

**Result: NOT VERIFIED — NO SAFE NATURAL FIXTURE**

No `PEN` Match existed in the runtime database, and no natural transition was
observed. No provider call or state change was made.

## 9. Finalization Failure Safety

**Runtime result: NOT VERIFIED. Test-level result: VERIFIED.**

No safe runtime failure injection was performed. The prior focused
implementation test run recorded in the Phase 3D implementation report
verified Event failure/exception responses remain retryable and do not mark
the finalization `SUCCESS`. No live finalization record or cache was changed
for this test.

## 10. Historical Auto-Sync Prevention

**Result: NOT VERIFIED — NO SAFE NATURAL FIXTURE**

There were no active registry keys and no live-status Matches, while the
database contained old finished Matches. Runtime source inspection confirms
the worker code rejects statuses outside the live Event allowlist. However,
no natural `refresh_events` job execution was logged during the observation
window, and no safe historical Match was selected by an actual scheduler
execution. No historical Event provider call was made.

## 11. Admin Historical Backfill

**Result: NOT VERIFIED — NO SAFE NATURAL FIXTURE**

No completed Match with zero Event rows was used to make an authenticated
admin backfill request. The running route contains the
`HISTORICAL_BACKFILL` operation parameter and existing-Event guard.

An unauthenticated POST to the endpoint returned **HTTP 401**, confirming
that the admin-authenticated route rejects unauthenticated access before
backfill execution. A non-admin authenticated request and a successful Admin
Backfill were not performed. Existing-row repair was not attempted.

## 12. Event Team Identity

**Result: FAIL for the inspected existing Match `1366` snapshot.**

Read-only Team Master and Event data:

| Identity | Match/Team Master value | Existing Event value |
|---|---:|---:|
| Home side canonical `Team.team_id` | 98 | — |
| Home side provider identity | 774 | Event `team_id=774` |
| Away side canonical `Team.team_id` | 97 | — |
| Away side provider identity | 5 | Event `team_id=5` |

The Match has two persisted Events. Their `team_id` values are `774` and `5`,
which are the Match sides' provider IDs, not canonical Match Team IDs `98`
and `97`. Joining the Event `team_id` values to `teams.team_id` found no Team
Master rows for those Event IDs. The API returned the same Event IDs.

This is evidence of a pre-existing or legacy snapshot in the inspected
runtime database; it is not evidence that the current EventSyncService wrote
those rows. No provider Event sync or repair was attempted. As required, the
verification stopped after this unexpected data condition was identified.

Missing-Team and wrong-Match-side error behavior is **TEST-LEVEL VERIFIED**
by the prior focused Phase 3 Event identity tests; **RUNTIME NOT VERIFIED**.

## 13. Cache Verification

The existing read path was exercised with a GET for Match `1366`. The response
contained two Events with Team IDs `5` and `774`. Before the GET, the Event
cache key was absent; afterward, the intended key existed with a six-hour
TTL. This verifies a DB-backed read can populate the per-Match Event cache.

Post-sync invalidation was not runtime-tested because no Event sync was run.
The implementation tests cover mocked cache invalidation after successful
commit.

## 14. Lock Verification

**Runtime result: NOT VERIFIED. Test-level result: VERIFIED.**

No provider sync was run and no competing runtime requests were started.
Runtime code inspection confirms terminal finalization and Admin Backfill
callers use the existing per-Match `events` resource lock. Existing focused
tests cover lock-before-provider and lock-conflict behavior. Real lock
contention was not induced.

## 15. Transaction Verification

**Runtime result: NOT VERIFIED. Test-level result: VERIFIED.**

No Event replacement was run. Database inspection used explicit read-only
transactions and rolled each back. The GET only read the persisted Event
snapshot and populated its cache key. No live sync failure was injected.
Prior focused tests verify caller-owned commit/rollback behavior with fakes.

## 16. Historical Data Safety

No provider sync, database write, historical repair, migration, or cleanup
was performed. All PostgreSQL inspection transactions were read-only.
Match `476` is absent from this runtime database and was not used as a test
target.

The historical Event snapshot for Match `1366` was not modified. The API GET
populated the per-Match Redis cache key; it did not update Event, Match, or
Team database rows.

The discovered `Match 1366` Event identity mismatch is left untouched. It
must not be automatically repaired as part of this verification.

## 17. API Verification

The read-only Event API endpoint for Match `1366` returned two Events. The
flat `team_id` values were `5` and `774`. Comparison with actual Team Master
and Match rows shows these are provider IDs and not canonical Match-side IDs
`97` and `98`; thus the observed historical response is not canonical.

The backfill route rejected an unauthenticated POST with HTTP 401. The
OpenAPI schema endpoint was unavailable (HTTP 404), so the HTTP-exposed query
parameter schema was not independently inspected. No authenticated backfill
or provider operation was performed.

## 18. Evidence Table

| Verification | Result | Evidence |
|---|---|---|
| Live Event Refresh | NOT VERIFIED — NO SAFE NATURAL FIXTURE | Zero live-status Matches and empty active registry; no Event refresh execution log in the observation window. |
| FT Final Event Sync | NOT VERIFIED — NO SAFE NATURAL FIXTURE | No natural live-to-FT transition observed; no final Event sync run. |
| AET Final Event Sync | NOT VERIFIED — NO SAFE NATURAL FIXTURE | No AET Match in runtime DB; no sync run. |
| PEN Final Event Sync | NOT VERIFIED — NO SAFE NATURAL FIXTURE | No PEN Match in runtime DB; no sync run. |
| Finalization Failure Safety | RUNTIME NOT VERIFIED; TEST-LEVEL VERIFIED | Focused tests documented in Phase 3D report; no runtime failure injection. |
| Historical Auto-Sync Block | NOT VERIFIED — NO SAFE NATURAL FIXTURE | No active registry entries and runtime code has live-status gate; no natural Event job execution observed. |
| Admin Historical Backfill | NOT VERIFIED — NO SAFE NATURAL FIXTURE | Runtime code guard present; no safe zero-Event fixture used. |
| Non-Admin Rejection | PARTIALLY VERIFIED | Unauthenticated POST returned 401; authenticated non-admin request not made. |
| Event Team Canonical ID | FAIL for existing Match 1366 snapshot | Match canonical IDs 98/97; provider IDs 774/5; stored/API Event team IDs are 774/5. |
| Cache Invalidation | NOT VERIFIED after sync | GET populated only `fover:match:1366:events`; post-sync behavior only test-level. |
| Event Lock | RUNTIME NOT VERIFIED; TEST-LEVEL VERIFIED | Runtime source and focused lock tests; no live contention. |
| Transaction Safety | RUNTIME NOT VERIFIED; TEST-LEVEL VERIFIED | Read-only DB transactions rolled back; no sync/failure path exercised. |
| Historical Data Safety | PASS for this verification's DB operations | No DB writes/sync/repair; Match 476 absent from this DB. No prior baseline for all historical rows. |
| API Response | FAIL for canonical identity on Match 1366 | API returned two Events with IDs 5 and 774, which differ from canonical Match-side IDs. |

## 19. Failures / Limitations

- The database had no live Event-refresh candidates or active registry
  entries.
- No natural `AET` or `PEN` Match or live-to-terminal transition was available.
- The `refresh_events` interval is 600 seconds; the running worker had not
  produced a natural Event-refresh execution log during the observation
  window.
- A successful Admin Backfill, authenticated non-admin denial, runtime Event
  provider failure, real lock contention, and successful-sync cache
  invalidation were not safely exercised.
- The runtime database does not contain Match `476`, preventing comparison
  with its earlier audit baseline.
- Existing Match `1366` Event rows and API output have provider-ID-shaped
  `team_id` values rather than canonical Match Team IDs. The cause and age of
  those rows were not established in this phase.
- The verification was stopped after that identity discrepancy was
  discovered. No corrective action was performed.

## 20. Final Verdict

**PARTIAL**

The runtime infrastructure and Phase 3D code presence are confirmed, and the
read-only API/cache path was observed. Required natural live and terminal
flows could not be safely exercised. In addition, an existing persisted
snapshot/API response failed the canonical identity comparison. It was left
untouched as required. No runtime PASS is claimed.
