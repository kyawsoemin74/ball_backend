# PHASE 9.1 - SCHEDULER / WORKER RUNTIME REPORT

## 1. Scope

This phase audited and repaired only scheduler/worker runtime ownership, startup, job registration, execution, lifecycle, observability, locks, transaction boundaries, and runtime health. Lineup business logic and lineup-to-analytics repair were not changed.

## 2. Baseline Runtime State

Before repair, FastAPI/Uvicorn was running and `/health/ready` returned PostgreSQL and Redis ready. No `worker.py` process existed, port 8001 was closed, and no scheduler execution counters or worker logs were available. The live provider was reachable, but scheduler execution was not proven.

## 3. Existing Scheduler Architecture

The existing architecture is a dedicated worker process:

`worker.py` -> `scheduler_service.start_scheduler()` -> global `LiveUpdateScheduler` -> APScheduler jobs -> `football_service` -> `FixtureSyncService` / repositories.

The API deployment explicitly sets `SCHEDULER_ENABLED=false`; the worker deployment runs `python worker.py` with scheduling enabled. This avoids embedding a scheduler in multi-worker Uvicorn.

## 4. Runtime Ownership

Ownership is dedicated-worker, not FastAPI/Uvicorn. Compose and Kubernetes both define one worker deployment separate from the API. The observed failure was that this worker was not started in the current runtime. Uvicorn did not own or silently start a scheduler, and no duplicate scheduler instance was observed after the worker was restored.

## 5. Root Cause

The scheduler was not executing because the intended dedicated `worker.py` runtime process was absent. The scheduler code and deployment definitions already contained the worker startup path; the current shell had only the API process and dependencies running.

## 6. Evidence

Baseline:

* Uvicorn process present.
* No `worker.py` process.
* TCP port 8001 refused.
* No scheduler execution counters.

After repair:

* Worker dependency health logged `postgres=True redis=True`.
* `SCHEDULER_STARTED` logged all nine expected job IDs.
* Port 8001 returned HTTP 200.
* `fover_scheduler_up 1.0`.
* `fover_scheduler_job_runs_total{job="sync_live_matches"} 2.0` after two real scheduled intervals.
* Worker logs recorded a real `sync_live_matches` execution, resource-lock acquisition/release, provider requests, and `SCHEDULER_JOB_COMPLETED`.
* One worker process tree owned the scheduler; no second worker tree was present.

## 7. Implementation Changes

* Added an APScheduler event listener in `app/services/scheduler.py` for structured `SCHEDULER_JOB_STARTED`, `SCHEDULER_JOB_COMPLETED`, `SCHEDULER_JOB_MISSED`, and `SCHEDULER_JOB_FAILED` events.
* Added explicit `SCHEDULER_STARTED` and `SCHEDULER_STOPPED` lifecycle logs with the expected job inventory.
* Added `tests/test_scheduler_runtime.py` covering startup, running state, job registration, next-run times, and structured lifecycle events.
* Restored the existing dedicated worker runtime with `python worker.py`. No second scheduler architecture was introduced.

No Match, fixture, lineup, analytics, or historical production data was manually changed.

## 8. Scheduler Job Inventory

| Job ID | Trigger | Overlap protection |
|---|---|---|
| `sync_live_matches` | 60-second interval | `max_instances=1`, live advisory lock, `fixture_query:global` resource lock |
| `reconcile_recent_non_terminal` | 5-minute interval | `max_instances=1`, advisory lock, resource lock |
| `sync_daily_fixtures` | 00:01 Myanmar time | `max_instances=1`, advisory/resource locks |
| `repair_daily_matches` | 02:00 Myanmar time | `max_instances=1`, advisory/resource locks |
| `refresh_standings` | 6-hour interval | `max_instances=1`, resource locks |
| `refresh_odds` | 6-hour interval | `max_instances=1`, resource locks |
| `refresh_lineups` | 15-minute interval | `max_instances=1`, `lineup:{match_id}` resource lock |
| `refresh_events` | 600-second interval | `max_instances=1`, event resource locks |
| `refresh_statistics` | 600-second interval | `max_instances=1`, statistics resource locks |

All expected jobs registered with non-null next-run times in the focused startup test. The live sync job executed twice in the runtime verification window.

## 9. Lock / Concurrency Verification

PASS. APScheduler uses `max_instances=1`; PostgreSQL advisory/resource locks remain in place. Runtime logs showed `fover:sync:fixture_query:global` acquired and released. Focused resource-lock, lineup-lock, and scheduler tests passed. The deployment has one dedicated worker replica, preventing uncontrolled duplicate scheduler owners.

## 10. Transaction Verification

PASS. The repair did not move transaction ownership. Scheduler jobs continue to own outer commit/rollback around existing service calls. `FixtureSyncService`, repositories, lineup services, and analytics services retain their existing boundaries. Focused transaction and cache-transaction tests passed.

## 11. Failure / Recovery Verification

PASS by implementation and focused tests. Individual live/reconciliation/refresh jobs catch failures, increment scheduler error metrics, log exceptions, and allow APScheduler to continue. The event listener records job errors/missed runs without changing job behavior. APScheduler overlap protection and advisory locks prevent concurrent duplicate work.

No provider or database fault was intentionally injected into production.

## 12. Test Results

Focused Phase 9.1 suites: `145 passed`.

Full backend suite: `660 passed, 1 failed`. The remaining failure is unrelated and pre-existing: `tests/test_team_upsert_on_conflict.py::test_ensure_teams_exist_resolves_existing_masters_and_reports_missing` expects no `resolved` field, while the current implementation returns it.

## 13. Real Runtime Verification

PASS for scheduler runtime. The final worker started at 15:24:27, registered all nine jobs, and remained alive on port 8001. Earlier live verification recorded two actual scheduled `sync_live_matches` executions and `SCHEDULER_JOB_COMPLETED`. PostgreSQL and Redis remained ready, and the API readiness endpoint continued returning ready.

The real provider-to-local Live Sync state transition was not observed: provider fixture `1639452` was live earlier in the audit, but no corresponding local match row existed. No fixture or local status was fabricated or manually repaired. This is reported as an unobserved data transition, not a scheduler-runtime failure.

## 14. Database Integrity Verification

Post-repair read-only checks:

* Orphan lineup/match references: 0
* Orphan analytics references: 0
* Orphan memberships: 0
* Duplicate provider player identities: 0
* Duplicate provider team identities: 0
* Duplicate analytics business keys: 0
* Invalid match statuses: 0
* Migration head: `20260915_missing_lineup_identity`

No new integrity problem was caused by the scheduler repair.

## 15. Known Pre-existing Issues

* One unrelated full-suite team-sync test fails because of an existing response-contract mismatch involving the `resolved` field.
* The earlier runtime audit found lineup-to-analytics consistency gaps. Those are explicitly out of scope for Phase 9.1 and were not modified.
* The current environment does not provide a persistent service manager record; the worker was restored in the active runtime shell. Deployment operators must ensure the existing Compose/Kubernetes worker definition is actually started and kept at one replica.

## 16. Out-of-Scope Items

Player/team master behavior, lineup business logic, analytics projection repair, historical lineup repair, event architecture, manual status repair, and fixture-specific patches were not performed.

## 17. Final Status

PASS

The Phase 9.1 scheduler/worker acceptance criteria are satisfied: root cause was identified, the intended dedicated worker was restored, jobs registered and executed in the real runtime, the scheduler remained alive, structured lifecycle logs and metrics were produced, locks and transaction ownership were preserved, focused tests passed, and database integrity remained clean. The unrelated full-suite failure and out-of-scope lineup findings remain documented separately.