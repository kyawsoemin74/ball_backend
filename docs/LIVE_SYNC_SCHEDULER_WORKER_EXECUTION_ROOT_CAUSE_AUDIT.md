# LIVE SYNC SCHEDULER / WORKER EXECUTION ROOT CAUSE AUDIT

## 1. Objective

Determine why local PostgreSQL Matches `1570397` and `1589091` are not receiving current provider live updates, specifically by auditing the Scheduler -> Worker -> Live Sync -> Provider -> target Match path. This was read-only. No sync was triggered and no code, database row, cache value, lock, or scheduler setting was changed.

Observation date: 2026-09-19 local time. Latest exact-provider observation: `2026-09-18T20:22:38.700580+00:00`.

## 2. Strict Scope

Only these existing local Matches were audited:

- local Match `1570397` -> provider fixture `1570397`
- local Match `1589091` -> provider fixture `1557408`

The other provider fixtures absent from PostgreSQL were not used as evidence for this root cause.

## 3. Target Match Records

| local_match_id | provider | provider_fixture_id | league_id | home_team_id | away_team_id | status | elapsed | home_score | away_score | match_time | created_at | updated_at |
|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---|---|---|
| 1570397 | api-football | 1570397 | 140 | 25492 | 25486 | 1H | 41 | 0 | 2 | 2026-09-19 01:30:00+06:30 | 2026-08-29 12:36:13.662083+06:30 | NULL |
| 1589091 | api-football | 1557408 | 39 | 25602 | 25599 | 1H | 34 | 0 | 0 | 2026-09-19 01:30:00+06:30 | 2026-09-18 17:28:42.967445+06:30 | NULL |

## 4. Current Runtime Environment

- Uvicorn API processes were running on port 8000.
- No `worker.py` process was present.
- Port 8001, where the worker metrics server is started, was not listening.
- The API process start time was approximately 2026-09-19 02:03:12 local time.
- PostgreSQL and Redis were reachable.
- No runtime log files containing scheduler or Live Sync execution records were present in the workspace.

Worker classification at audit time: **WORKER_NOT_RUNNING**.

This proves the worker was absent during the audit observation. It does not prove whether a historical worker executed before the observation window.

## 5. Scheduler Ownership

The current code ownership is:

`worker.py` -> `scheduler_service.start_scheduler()` -> `LiveUpdateScheduler.start()` -> `_sync_live_matches_job()` -> `football_service.sync_live_matches()` -> `FixtureSyncService.sync_live_matches()` -> `FixtureProvider.get_live_fixtures()` -> `_process_sync()` -> `MatchRepository.get_by_provider_fixture_id()` / PostgreSQL upsert.

Responsibility boundaries:

- `worker.py` starts the metrics server, checks dependencies, and starts the scheduler.
- `scheduler_service.py` delegates scheduler start/stop to the global `LiveUpdateScheduler`.
- `LiveUpdateScheduler` registers and invokes the Live Sync job, owns the outer commit/rollback, and invalidates the live-match cache after commit.
- `FixtureSyncService` fetches provider fixtures, resolves league/team identities, parses fields, resolves the existing Match, flushes the upsert, and returns the aggregate result.
- `MatchRepository` resolves existing Matches by `(provider, provider_fixture_id)` and supplies live-query helpers.
- `FixtureProvider` calls the provider `/fixtures` endpoint.

## 6. Worker Runtime Evidence

`worker.py` would execute, in order:

1. `start_worker_metrics_server(8001)`.
2. PostgreSQL/Redis dependency health check.
3. `start_scheduler()`.
4. Wait for shutdown.

None of the worker process or port 8001 was present during this audit. Therefore:

**WORKER_RUNTIME = WORKER_NOT_RUNNING**

Historical worker execution for the period in which the provider values changed: **NOT VERIFIABLE**.

## 7. Scheduler Registration

The source registration is present in `LiveUpdateScheduler.start()`:

| Job ID | Function | Trigger | Interval | max_instances |
|---|---|---|---|---:|
| `sync_live_matches` | `_sync_live_matches_job` | interval | 60 seconds | 1 |
| `reconcile_recent_non_terminal` | `_reconcile_recent_non_terminal_job` | interval | 5 minutes | 1 |

The Live Sync job is therefore **REGISTERED in source**, but actual runtime registration, next run, last run, and misfire state were unavailable because the worker was not running. Runtime scheduler classification: **NOT_VERIFIABLE**.

## 8. Job Execution Evidence

The scheduler source would emit `SCHEDULER_JOB_STARTED`, `SCHEDULER_JOB_COMPLETED`, `SCHEDULER_JOB_MISSED`, or `SCHEDULER_JOB_FAILED`. The Live Sync job would also emit skip/error/completion messages. No worker logs or metrics were available, and no worker process was running.

Execution timeline:

| Time | Evidence |
|---|---|
| Audit observation | API process present; worker absent |
| Audit observation | Port 8001 unavailable |
| Audit observation | No scheduler log file available |
| Audit observation | No target-specific execution/exception record available |

**EXECUTION = NOT VERIFIABLE** for historical execution. The current first runtime stop is the absent Worker before Scheduler registration/execution.

## 9. Provider Fetch Evidence

An exact provider fixture-detail request was made for observation only, outside the scheduler. It returned two fixtures and no errors at `2026-09-18T20:22:38.700580+00:00` UTC:

| Provider fixture | Status | Elapsed | Score |
|---:|---|---:|---:|
| 1570397 | 2H | 56 | 0-2 |
| 1557408 | 2H | 65 | 1-0 |

This proves current provider data is available. It does **not** prove that the Live Sync Worker received this response, because the request was not initiated by the scheduler.

Scheduler -> provider fetch: **NOT VERIFIABLE**.

## 10. Target Fixture Discovery

The core Live Sync code calls `FixtureProvider.get_live_fixtures()`, which requests `/fixtures?live=all`. It does not first select the two local rows by local status. The scheduler gate only checks whether any Match exists within a broad +/-24-hour match-time window; both target rows fall within that window.

The provider response collection and target-specific processing list from an actual scheduler run were unavailable. Therefore:

| Target | Discovery result |
|---:|---|
| 1570397 | NOT VERIFIABLE |
| 1557408 | NOT VERIFIABLE |

No source-level local-status gate was found that would reject provider `2H` because the stored status is `1H`. The active-match Redis registry is used by event/statistics refresh jobs, not as the core Live Sync provider input.

## 11. Match Identity Resolution

Read-only PostgreSQL identity checks found exactly the expected mappings:

- `api-football, 1570397` -> local Match `1570397`.
- `api-football, 1557408` -> local Match `1589091`.

Both provider leagues were allowed, and both home/away team identities resolved. No duplicate provider identity existed.

| Target | Resolution |
|---:|---|
| 1570397 | MATCH_RESOLVED |
| 1589091 / provider 1557408 | MATCH_RESOLVED |

## 12. Match Update Path

The source update path is:

`FixtureSyncService._process_sync_with_candidates()` -> `parse_fixture_to_match()` -> league filter -> team resolution -> `MatchRepository.get_by_provider_fixture_id()` -> PostgreSQL `ON CONFLICT DO UPDATE`.

The upsert update set includes `status`, `elapsed`, `home_score`, and `away_score`. A `db.flush()` follows. No target-specific runtime evidence proves that either fixture reached this function during a scheduler execution.

| Target | Update path reached | Update function called | Repository called |
|---:|---|---|---|
| 1570397 | UNKNOWN | UNKNOWN | UNKNOWN |
| 1589091 | UNKNOWN | UNKNOWN | UNKNOWN |

## 13. Repository / Transaction

The repository lookup uses the exact canonical provider identity. The scheduler closure owns the transaction:

- successful aggregate result -> `db.commit()`;
- unsuccessful result -> `db.rollback()`;
- exception -> `db.rollback()` and outer job error handling;
- successful commit -> live-match cache deletion.

For both target Matches, no runtime update, flush, commit, rollback, or exception record was available. Transaction result: **UNKNOWN**.

## 14. PostgreSQL Result

At the audit observation, PostgreSQL still contained:

| Local Match | DB status | DB elapsed | DB score | DB updated_at |
|---:|---|---:|---:|---|
| 1570397 | 1H | 41 | 0-2 | NULL |
| 1589091 | 1H | 34 | 0-0 | NULL |

Compared with the exact provider observation:

- Match 1570397: provider `2H/56/0-2`, DB `1H/41/0-2`.
- Match 1589091: provider `2H/65/1-0`, DB `1H/34/0-0`.

PostgreSQL latest state: **FAIL** for both.

## 15. API Read-back

The API returned HTTP 200 for both target local Match IDs:

| Local Match | Provider | PostgreSQL | API |
|---:|---|---|---|
| 1570397 | 2H / 56 / 0-2 | 1H / 41 / 0-2 | 1H / 41 / 0-2 |
| 1589091 | 2H / 65 / 1-0 | 1H / 34 / 0-0 | 1H / 34 / 0-0 |

The API matches PostgreSQL, not the provider. The first proven data divergence is before PostgreSQL persistence.

## 16. Cache Evidence

Read-only Redis checks found:

- `fover:live_matches`: absent, TTL `-2`.
- `fover:active_match:1570397`: absent.
- `fover:active_match:1589091`: absent.

The API therefore was not returning an old `live_matches` cache value at inspection time. The active registry is not the core Live Sync input. Cache did not explain why the two Match rows were not updated.

Cache classification: **CACHE_NOT_INVOLVED** in the observed stale API response.

## 17. Lock Evidence

PostgreSQL reported no advisory, tuple, or transaction locks at inspection time. No target-specific lock rejection or overlapping sync log was available.

Lock classification: **NO_LOCK_EVIDENCE**.

## 18. Exception Evidence

No local runtime logs were available containing either provider fixture ID (`1570397`, `1557408`) or either local Match ID (`1570397`, `1589091`). Consequently, there is no evidence of a raised, caught, swallowed, or converted target-specific exception.

Exception classification: **UNKNOWN**, not `EXCEPTION_SWALLOWED`.

## 19. Per-Match Execution Matrix

| Stage | Match 1570397 | Match 1589091 |
|---|---|---|
| Provider data available | PASS | PASS |
| Scheduler executed | NOT VERIFIABLE | NOT VERIFIABLE |
| Live Sync job executed | NOT VERIFIABLE | NOT VERIFIABLE |
| Provider live fetch by scheduler | NOT VERIFIABLE | NOT VERIFIABLE |
| Target fixture discovered | NOT VERIFIABLE | NOT VERIFIABLE |
| League resolved | PASS | PASS |
| Team resolved | PASS | PASS |
| Local Match resolved | PASS | PASS |
| Update path reached | UNKNOWN | UNKNOWN |
| Repository called | UNKNOWN | UNKNOWN |
| Transaction committed | UNKNOWN | UNKNOWN |
| DB updated | FAIL | FAIL |
| API updated | FAIL | FAIL |

## 20. First Failure Boundary

### Current runtime

The first currently proven stop is:

`Worker process` -> **WORKER_NOT_RUNNING** -> Scheduler cannot initialize/register/execute.

This is the first verified runtime failure boundary in the observed environment.

### Historical provider-change window

Whether a worker executed while the provider values changed is **NOT VERIFIABLE**. No historical scheduler timeline exists. Therefore it is not proven that the worker was absent for the entire period during which either provider fixture changed.

Downstream stages, including target discovery, update, repository call, flush, and commit, are consequently not proven to have run for either target.

## 21. Root Cause Classification

**WORKER_NOT_RUNNING** for the current runtime state.

Historical execution classification: **UNKNOWN**.

The audit does not classify this as provider failure, target filtering, identity failure, mapping failure, transaction failure, cache failure, lock failure, or swallowed exception because no direct target-specific evidence supports any of those alternatives.

## 22. Root Cause

What is proven:

- The API worker is running, but the dedicated `worker.py` Live Sync process is not running.
- Without `worker.py`, the scheduler metrics server on 8001 and scheduler registration/execution do not exist in the current runtime.
- The exact provider fixtures are current and available.
- The target identities, leagues, teams, parser mappings, and upsert fields are compatible.
- PostgreSQL was not updated, and the API returns the unchanged PostgreSQL values.

What is not proven:

- Whether a worker ran historically after the provider values advanced.
- Whether either target was present in a scheduler live response.
- Whether either target reached the update/upsert code.
- Whether any transaction attempted for either target committed or rolled back.

Therefore, the exact current runtime root cause is **WORKER_NOT_RUNNING**. The historical reason the rows remained stale cannot be narrowed beyond **UNKNOWN** without worker execution evidence.

## 23. Impact

Both existing local LIVE Matches expose stale status and elapsed values through PostgreSQL and the API. Match `1589091` also has a provider score of `1-0` while the database/API remain `0-0`. No evidence shows a cache or lock masking a successful database update.

## 24. Limitations

- The worker was absent during this audit, so no live scheduler execution could be observed.
- No worker log file, 8001 metrics output, next-run/last-run state, provider request trace, or target-specific exception trace was available.
- The provider calls in this audit were direct observation requests, not manual Live Sync triggers.
- No service was restarted and no synchronization was initiated.
- Current process absence cannot prove historical absence during the exact time provider data changed.

## 25. Final Conclusion

For both existing Matches, the provider data is available, identity resolution is correct, and the source update path contains the required field mappings. The current runtime, however, has no `worker.py` process and no scheduler metrics listener, so the Live Sync job is not currently executing.

The first **proven current** failure boundary is:

`Worker not running` -> `Scheduler not initialized` -> `Live Sync job not executed`.

As a result, target fixture discovery, update-path entry, repository execution, flush, and commit are not verifiable for either Match. PostgreSQL remains stale, and the API faithfully returns those stale values. The current classification is **WORKER_NOT_RUNNING**; the historical execution cause remains **UNKNOWN**, not manufactured from unavailable evidence.