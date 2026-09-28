# PHASE 4.5 - Step 1 Scheduler Retry Final Runtime Verification Report

Verification date: 2026-09-23
Scope: only the League Identity Recovery Scheduler Retry path. Steps 2, 3, and 4 were not rerun.

## 1. Objective

Verify that the real Docker Worker starts APScheduler, registers the `recover_league_identities` job, executes it against one due temporary recovery record, and persists the expected League identity and recovery transition in PostgreSQL.

## 2. Runtime Environment

Pre-checks passed:

```text
api       running healthy
postgres  running healthy
redis     running healthy
worker    running healthy
```

Worker runtime checks also passed:

```text
psycopg2=2.9.12 (dt dec pq3 ext lo64)
libpq=True
imports=ok
```

The live PostgreSQL schema check returned:

```text
league_identity_recovery
13
```

meaning the recovery table exists with 13 columns. No schema or unrelated Alembic drift was changed.

## 3. Temporary Test Data

Exactly one temporary League and one recovery record were staged:

```text
League ID: 1286
Recovery ID: 4
League name: Intercontinental Cup U20
Country: World
Expected provider_id: 1246
```

The provider candidate was selected by a read-only provider scan because the exact name/country and provider identity were not already present locally. No production League, Team, or Match was reused.

Before scheduler execution, PostgreSQL showed:

```text
4|1286|IDENTITY_RESOLUTION_RETRY|1|2026-09-23 05:08:03.193777+00|
1286|api-football||Intercontinental Cup U20|World|
```

Fields represent:

```text
recovery_id | league_id | state | attempt_count | next_retry_at | resolved_provider_id
league_id | provider | provider_id | name | country | country_code
```

The retry timestamp was already due.

## 4. Actual APScheduler Registration Evidence

The live Worker log emitted during application startup:

```text
2026-09-23 04:50:53,428 INFO [app.services.scheduler] SCHEDULER_STARTED
jobs=['sync_live_matches', 'reconcile_recent_non_terminal',
'recover_league_identities', 'sync_daily_fixtures',
'repair_daily_matches', 'refresh_standings', 'refresh_odds',
'refresh_lineups', 'refresh_events', 'refresh_statistics']
```

This is runtime evidence from the actual Worker process that:

- APScheduler started;
- `recover_league_identities` was registered in the live scheduler job set;
- the job appeared exactly once in the logged job list.

## 5. Trigger Configuration

The live Worker execution occurred on the observed 15-minute scheduler cadence. The recovery job started at `05:20:53 UTC`, following the preceding recovery cycle at `05:05:53 UTC`.

The application’s registered job configuration is the existing frozen configuration:

```text
job id: recover_league_identities
job name: Recover League Identities
trigger: IntervalTrigger(minutes=15)
max_instances: 1
```

However, the live Worker log does not print the APScheduler object’s `trigger`, `max_instances`, or `next_run_time` fields. No scheduler introspection endpoint exists, and no scheduler code was modified to add one. Therefore these specific fields are source/configuration evidence rather than direct live-object introspection.

Missing live-object fields:

- trigger object and interval representation;
- `max_instances` value from the running job object;
- `next_run_time` from the running job object;
- direct scheduler `running` property dump.

This is the only remaining Step 1 evidence gap.

## 6. Actual Scheduler Execution Evidence

The actual running Worker log emitted:

```text
2026-09-23 05:20:53,478 INFO [app.services.scheduler] SCHEDULER_JOB_STARTED
job=recover_league_identities

2026-09-23 05:20:53,883 INFO [app.services.scheduler]
LEAGUE_IDENTITY_RECOVERY_JOB_COMPLETE metrics={'selected': 1, 'resolved': 1,
'retryable': 0, 'failed': 0}

2026-09-23 05:20:53,883 INFO [app.services.scheduler] SCHEDULER_JOB_COMPLETED
job=recover_league_identities result={}
```

This proves the execution was scheduler-driven rather than a direct call to the recovery service or job function:

```text
Docker Worker
  -> APScheduler running
  -> recover_league_identities registered
  -> scheduled trigger fired at 05:20:53 UTC
  -> recovery job selected Recovery ID 4
  -> recovery transitioned the temporary League
```

No live Match was required.

## 7. Before/After Recovery State

Before:

```text
recovery_id=4
league_id=1286
state=IDENTITY_RESOLUTION_RETRY
attempt_count=1
next_retry_at=due
resolved_provider_id=NULL
```

After the real scheduler execution, PostgreSQL returned:

```text
4|1286|IDENTITY_RESOLVED|2||1246
```

Expected and actual:

| Field | Expected | Actual | Result |
|---|---|---|---|
| State | `IDENTITY_RESOLVED` | `IDENTITY_RESOLVED` | PASS |
| Attempt count | `1 -> 2` | `2` | PASS |
| Next retry | No longer pending | `NULL` | PASS |
| Resolved provider ID | `1246` | `1246` | PASS |

## 8. League Provider Identity Persistence

After the scheduler execution, PostgreSQL returned:

```text
1286|api-football|1246|Intercontinental Cup U20|World|
```

Therefore `League.provider_id` was persisted as `1246` by the scheduler-triggered recovery lifecycle.

## 9. Duplicate and Idempotency Evidence

Before cleanup, PostgreSQL duplicate checks returned:

```text
recovery_rows=1
provider_id_rows=1
name_country_rows=1
```

Interpretation:

- exactly one recovery row existed for League `1286`;
- exactly one League had provider ID `1246`;
- exactly one League matched the temporary name/country identity;
- no duplicate recovery or provider identity row was created.

## 10. Cleanup Evidence

Cleanup was executed only after post-state capture, in FK-safe order:

```text
DELETE FROM matches WHERE league_id = 1286;       DELETE 0
DELETE FROM league_seasons WHERE league_id = 1286; DELETE 0
DELETE FROM allowed_leagues WHERE league_id = 1286; DELETE 0
DELETE FROM league_identity_recovery WHERE recovery_id = 4; DELETE 1
DELETE FROM leagues WHERE league_id = 1286;        DELETE 1
```

Final verification returned:

```text
league_1286=0
recovery_4=0
provider_1246=0
name_country=0
```

No production business data was modified.

## 11. Acceptance Criteria Audit

| Criterion | Result | Evidence |
|---|---|---|
| Actual Docker Worker scheduler running | PASS | `SCHEDULER_STARTED` and recurring job logs |
| `recover_league_identities` registered | PASS | Live startup job list contains it exactly once |
| Actual APScheduler trigger fires | PASS | Live `SCHEDULER_JOB_STARTED` at `05:20:53 UTC` |
| Worker logs prove scheduler execution | PASS | Started, recovery metrics, completed logs |
| Recovery ID 4 selected | PASS | `selected=1`; post-state is Recovery ID 4 |
| RETRY -> RESOLVED | PASS | PostgreSQL state transition |
| Attempt count 1 -> 2 | PASS | PostgreSQL `2` |
| Resolved provider ID becomes 1246 | PASS | PostgreSQL `resolved_provider_id=1246` |
| League 1286 provider ID becomes 1246 | PASS | PostgreSQL League row |
| No duplicate recovery record | PASS | `recovery_rows=1` |
| No duplicate provider identity | PASS | `provider_id_rows=1` |
| Temporary cleanup succeeds | PASS | All final counts zero |
| No production data modified | PASS | Isolated IDs and cleanup evidence |
| Live trigger/max_instances/next_run_time object fields | NOT CAPTURED | No live introspection surface; source config only |

## 12. Failed or Unverified Checks

The scheduler execution lifecycle is proven, but the following exact fields were not emitted by the actual running Worker and were not introspected through a live endpoint:

- running job object trigger representation;
- running job object `max_instances`;
- running job object `next_run_time`;
- direct running scheduler property dump.

Capturing those fields would require adding temporary observability or executing code inside the Worker’s existing process context, which was outside the no-code-change and no-direct-invocation constraints. The existing source/configuration states `IntervalTrigger(minutes=15)` and `max_instances=1`, and the observed 15-minute log cadence is consistent with it.

## 13. Final Classification

**B. PARTIALLY VERIFIED**

The real Docker Worker APScheduler registered and executed the recovery job, and the due recovery record transitioned successfully to `IDENTITY_RESOLVED` with provider ID `1246`. The strict Step 1 acceptance list is not fully satisfied because live-object trigger, `max_instances`, `next_run_time`, and scheduler-running-property introspection were not captured.

Step 1 must not be promoted to A. VERIFIED based on source configuration and cadence inference alone.

## 14. Phase Handoff

Step 1 — Scheduler Retry: **PARTIALLY VERIFIED**.

Do not proceed to Phase 4.5 closure as FULLY VERIFIED until the missing live APScheduler object fields are independently captured, or the acceptance criteria are formally relaxed.
