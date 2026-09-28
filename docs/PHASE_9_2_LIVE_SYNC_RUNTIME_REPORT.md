# PHASE 9.2 - LIVE SYNC RUNTIME REPORT

## 1. Scope

This phase validated only the real provider live-feed path through the existing scheduler, `FootballService`, `FixtureSyncService`, repository, transaction, lock, and cache boundaries. No match, fixture, status, score, lineup, analytics, player, or team data was manually changed. Lineup business logic and analytics projection repair were not performed.

## 2. Baseline Runtime State

FastAPI/Uvicorn was running. PostgreSQL and Redis readiness returned true. The dedicated `worker.py` scheduler was restarted for this validation and remained alive on port 8001. The scheduler reported `fover_scheduler_up 1.0`.

## 3. Live Sync Architecture

The observed path is:

`worker.py` -> `scheduler_service.start_scheduler()` -> `LiveUpdateScheduler._sync_live_matches_job()` -> `football_service.sync_live_matches()` -> `FixtureSyncService.sync_live_matches()` -> provider live endpoint -> stale-match selection and `_process_sync()` / `process_fixture()` -> `MatchRepository` persistence -> scheduler-owned commit or rollback -> active-match update and `live_matches` cache invalidation.

The scheduler does not write directly to `matches` and does not bypass `FixtureSyncService`.

## 4. Provider Live Feed Verification

PASS. The real configured `FixtureProvider.get_live_fixtures()` call succeeded in approximately 538 ms and returned two structurally valid fixtures with no provider errors:

| Provider fixture | Status | Elapsed | Score |
|---|---|---:|---:|
| `1622790` | `1H` | 29-30 | 0-1 |
| `1611759` | `HT` | 45 | 1-1 |

Provider team and league identities were present. Credentials and authorization headers were not exposed.

## 5. Provider <-> Local Fixture Correlation

Both current provider LIVE fixtures were queried by canonical `provider_fixture_id` in the local `matches` table:

| Provider fixture | Provider teams | Provider status/score | Local match |
|---|---|---|---|
| `1622790` | Pusamania Borneo - Dewa United | `1H`, 0-1, elapsed 29-30 | Missing |
| `1611759` | Metalist 1925 U19 - Zhytomyr U19 | `HT`, 1-1, elapsed 45 | Missing |

Classification: two cases of Provider LIVE + Local missing. The local database had zero LIVE rows at correlation time. No local row was created to manufacture a test case.

## 6. Real Runtime Execution

PASS. The normal scheduler executed `sync_live_matches` twice after the transaction-scoped lock repair. Evidence included:

* `fover_scheduler_job_runs_total{job="sync_live_matches"} 2.0` after the repaired worker restart.
* `SCHEDULER_JOB_COMPLETED job=sync_live_matches` in worker output.
* Provider HTTP requests from the scheduler execution path.
* `RESOURCE_LOCK_ACQUIRED lock_identity=fover:sync:fixture_query:global`.
* Per-resource lock acquisition/release for finalization work reached by the run.
* `RESOURCE_LOCK_RELEASED lock_identity=fover:sync:fixture_query:global`.
* Scheduler remained alive after both executions.

The real provider-to-local processing path was reached, but no suitable local provider fixture existed for a persisted live-state comparison.

## 7. Database Update Verification

Provider-to-local update: NOT OBSERVABLE. Both current provider LIVE fixture IDs were absent locally, so there was no valid before/after local row to compare. No status, elapsed value, score, or fixture row was manually altered. This is the permitted no-suitable-fixture outcome, not a demonstrated Live Sync persistence failure.

Earlier provider fixture observations during the runtime window had the same missing-local condition. No natural status transition or terminal handoff was available for verification.

## 8. Status Transition Verification

No natural `NS -> LIVE`, `1H -> HT`, `HT -> 2H`, `2H -> FT`, or other terminal transition was observed for a provider/local-correlated fixture. The implementation path remains the existing `process_fixture()` transition path; no transition was fabricated or forced.

## 9. Lock / Concurrency Verification

PASS after repair. The scheduler retains `max_instances=1` and the global advisory lock. Resource locks remain in place for `fixture_query:global` and per-resource work.

The audit identified a real pre-repair issue: session-scoped resource advisory locks remained held by idle pooled connections after callbacks committed. The smallest fix changed resource acquisition to `pg_try_advisory_xact_lock`; scheduler-level integer locks remain unchanged. After worker restart and two real live-sync cycles, `ADVISORY_LOCK_COUNT` was `0`. Focused lock tests passed.

## 10. Transaction Verification

PASS. The scheduler remains the outer transaction owner. `FixtureSyncService` and repositories do not gain transaction ownership from this phase. Transaction-scoped resource locks now align lock lifetime with the caller-owned commit/rollback boundary. Focused transaction and live-sync tests passed.

## 11. Cache Verification

PASS by the existing runtime path and focused tests. Successful scheduler sync commits before deleting `make_cache_key("live_matches")`; failure paths roll back and do not perform successful-state invalidation. No new cache strategy or key was introduced. Because no provider fixture had a local match, a provider-to-local row update cache comparison was not naturally available.

## 12. Failure / Recovery Verification

PASS by focused tests and runtime architecture. Provider failure, empty/malformed responses, lock contention, per-fixture failure isolation, rollback, cache transaction ordering, and scheduler continuation are covered by the existing and focused tests. The scheduler remained alive through real executions. No production dependency was intentionally damaged.

## 13. Test Results

Focused Phase 9.2 suites: `82 passed`.

Full backend suite: `660 passed, 1 failed`. The failure is unrelated and pre-existing: `tests/test_team_upsert_on_conflict.py::test_ensure_teams_exist_resolves_existing_masters_and_reports_missing` expects a result without the existing `resolved` field.

## 14. Database Integrity

Post-validation read-only checks:

* Orphan lineup/match references: 0
* Orphan analytics references: 0
* Orphan memberships: 0
* Duplicate provider match identities: 0
* Duplicate provider player identities: 0
* Duplicate provider team identities: 0
* Duplicate analytics business keys: 0
* Invalid match statuses: 0
* Migration head: `20260915_missing_lineup_identity`
* Advisory locks after completed worker cycle: 0

No unexplained integrity regression was introduced by Phase 9.2.

## 15. Historical / Out-of-Scope Findings

* Historical lineup-to-analytics gaps remain out of scope and were not repaired.
* The prior runtime audit found provider LIVE fixtures without local matches. This phase did not create or repair those matches.
* The unrelated team-sync test failure was not changed.
* No terminal transition was naturally available.

## 16. Evidence

Runtime evidence:

* Provider live endpoint: success, two fixtures.
* Scheduler: running, `sync_live_matches` counter advanced to 2 after repaired worker restart.
* Worker logs: provider requests, `SCHEDULER_JOB_COMPLETED`, resource lock acquisition/release.
* API readiness: `{"status":"ready","postgres":true,"redis":true}`.
* Local correlation: provider fixtures `1622790` and `1611759` both missing locally.
* Lock state: zero advisory locks after completed repaired cycles.

## 17. Final Status

PASS

PASS form: `RUNTIME EXECUTION PROVEN - PROVIDER-TO-LOCAL UPDATE NOT OBSERVABLE BECAUSE NO CORRESPONDING LOCAL LIVE FIXTURE WAS AVAILABLE.`

The scheduler, provider access, Live Sync architecture, transaction boundary, cache ordering, failure handling, and lock behavior were verified. A real provider-to-local database update was not claimed because the provider returned LIVE fixtures that had no corresponding local match. Phase 9.3 was not started.