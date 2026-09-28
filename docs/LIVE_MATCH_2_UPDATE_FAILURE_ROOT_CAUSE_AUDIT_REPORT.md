# LIVE MATCH 2 - UPDATE FAILURE ROOT CAUSE AUDIT REPORT

## 1. Objective

Determine why the only two existing local PostgreSQL LIVE Match records do not contain the current provider status and elapsed values. This was a read-only audit. No code, database row, scheduler setting, lock, or cache value was modified.

Observation date: 2026-09-19. Provider observation timestamp: `2026-09-18T20:16:19.981587+00:00`.

## 2. Scope - ONLY the 2 Existing Local LIVE Matches

The audit target was restricted to the two rows returned by the local LIVE status query. The 48 provider fixtures with no local Match were excluded from the root-cause analysis.

## 3. Target Match Records

| local_match_id | provider | provider_fixture_id | league_id | home_team_id | away_team_id | status | elapsed | home_score | away_score | match_time | created_at | updated_at |
|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---|---|---|
| 1570397 | api-football | 1570397 | 140 | 25492 | 25486 | 1H | 41 | 0 | 2 | 2026-09-19 01:30:00+06:30 | 2026-08-29 12:36:13.662083+06:30 | NULL |
| 1589091 | api-football | 1557408 | 39 | 25602 | 25599 | 1H | 34 | 0 | 0 | 2026-09-19 01:30:00+06:30 | 2026-09-18 17:28:42.967445+06:30 | NULL |

## 4. Current Provider Evidence

An exact fixture-detail request was made for provider IDs `1570397` and `1557408`. The provider returned HTTP-success data, two fixture records, and no errors.

| Provider fixture | Provider league | Provider teams | Provider status | Provider elapsed | Provider score | Provider match date |
|---:|---:|---|---|---:|---:|---|
| 1570397 | 140 | 540 / 797 | 2H | 50 | 0-2 | 2026-09-18T19:00:00+00:00 |
| 1557408 | 39 | 55 / 49 | 2H | 59 | 0-0 | 2026-09-18T19:00:00+00:00 |

Provider data availability: **PASS** for both targets.

## 5. Provider vs DB Comparison

| Local match | Provider status / elapsed / score | DB status / elapsed / score | Difference |
|---:|---|---|---|
| 1570397 | 2H / 50 / 0-2 | 1H / 41 / 0-2 | status and elapsed stale; score equal |
| 1589091 | 2H / 59 / 0-0 | 1H / 34 / 0-0 | status and elapsed stale; score equal |

The provider has newer state for both existing local matches. This is Problem B from the requested separation: the local Match exists but is not current.

## 6. Match Identity Resolution

Database identity checks found exactly one row for each canonical identity:

- `api-football, 1570397` -> local Match `1570397`.
- `api-football, 1557408` -> local Match `1589091`.

Both local leagues are allowed and correctly map to provider identities:

- Provider league 140 -> local league 140, La Liga.
- Provider league 39 -> local league 39, Premier League.

Both home and away provider team identities resolve to the local team rows. No duplicate Match identity was found.

**MATCH IDENTITY = PASS** for both targets.

This is not a League Identity failure.

## 7. Live Fixture Discovery

The core live path is `sync_live_matches()` -> `get_live_fixtures()` -> `_process_sync()`. It processes the provider live response and does not first query local LIVE rows by status. The scheduler gate only checks whether any Match exists within a broad UTC +/-24-hour match-time window.

The separate `active_match_service` Redis registry is used by event/statistics refresh jobs, not as the input to the core provider live fixture fetch. Neither target had an active-registry key at observation time, but that does not prove the core Live Sync skipped either target.

Because no `worker.py` process or port 8001 metrics endpoint was running, there was no runtime processing list or job log proving whether either target entered a live-sync execution.

Per-target discovery: **UNKNOWN**.

No evidence shows that local status `1H` causes a fixture to be rejected. The incoming provider status is parsed independently.

## 8. Live Sync Update Path

The traced path is:

`sync_live_matches()` -> provider `/fixtures?live=all` -> league identity filter -> team identity resolution -> `get_by_provider_fixture_id("api-football", provider_fixture_id)` -> PostgreSQL upsert on `uq_matches_provider_fixture_id` -> `flush()` -> scheduler commit -> delete `fover:live_matches` after commit.

The source path has no local-status gate equivalent to `if match.status not in active_statuses: skip`. `1H` therefore should not block an incoming `2H` payload.

Actual per-fixture execution: **NOT VERIFIABLE**. There was no active scheduler runtime or target-specific sync log.

## 9. Field Mapping

The parser maps provider fields as follows:

- provider `fixture.status.short` -> `Match.status`
- provider `fixture.status.elapsed` -> `Match.elapsed`
- provider `goals.home` -> `Match.home_score`
- provider `goals.away` -> `Match.away_score`

The upsert update set includes `status`, `elapsed`, `home_score`, and `away_score`. The mapping is therefore source-capable for both target records: **PASS by path inspection, runtime application NOT VERIFIABLE**.

## 10. Repository Update

The implementation resolves an existing row through `MatchRepository.get_by_provider_fixture_id()` and then uses PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` on `uq_matches_provider_fixture_id`. The update set includes the four target fields. There is no condition that preserves the old `1H` value.

For both targets:

- MATCH FOUND: **YES** in the database.
- UPDATE CALLED: **UNKNOWN** at runtime.
- UPDATE APPLIED: **NO** in the observed database state.

The stale values do not prove a repository failure because no update attempt was observed.

## 11. Transaction / Commit

The scheduler owns the outer transaction. On aggregate success it calls `db.commit()`, and on failure it rolls back. The fixture service flushes but does not own the outer commit. Cache invalidation is placed after commit.

No worker execution, commit log, rollback log, exception, or transaction trace for either target was available. Therefore:

- Transaction committed for target update: **UNKNOWN**.
- Transaction failure: **NOT PROVEN**.
- Database latest state: **NO** for both targets.

## 12. Cache / API Read-back

The exact Redis key `fover:live_matches` did not exist (`EXISTS=0`, `TTL=-2`) at inspection time. The API returned the same stale values held by PostgreSQL for both target matches:

| Local match | Provider | DB | API |
|---:|---|---|---|
| 1570397 | 2H / 50 / 0-2 | 1H / 41 / 0-2 | 1H / 41 / 0-2 |
| 1589091 | 2H / 59 / 0-0 | 1H / 34 / 0-0 | 1H / 34 / 0-0 |

The first loss of current data is therefore before PostgreSQL persistence. This is not a cache-staleness root cause. The API is returning stale database data.

## 13. Scheduler Evidence

The scheduler source registers `sync_live_matches` every 60 seconds with `max_instances=1`, and `reconcile_recent_non_terminal` every 5 minutes. The scheduler gate would permit execution when any Match exists within its +/-24-hour window; the target rows meet that time window.

Runtime result for these two fixtures:

**SCHEDULER EXECUTION = NOT VERIFIABLE**

No worker process was present, port 8001 refused connections, and no target-specific scheduler logs or metrics were available. Scheduler absence is not asserted as the root cause; it is the principal missing runtime evidence.

## 14. Lock / Concurrency Evidence

At inspection time PostgreSQL reported no advisory, tuple, or transaction locks. No target-specific lock was observed. No overlapping sync or lock rejection log was available.

Lock/concurrency failure: **NOT PROVEN**.

## 15. Per-Match Failure Matrix

| Check | Match 1570397 | Match 1589091 |
|---|---|---|
| Provider data available | YES | YES |
| Provider identity resolves | YES | YES |
| Local Match resolves | YES | YES |
| Live Sync discovered it | UNKNOWN | UNKNOWN |
| Update path reached | UNKNOWN | UNKNOWN |
| Update attempted | UNKNOWN | UNKNOWN |
| Update persisted | NO | NO |
| Transaction committed | UNKNOWN | UNKNOWN |
| DB contains latest data | NO | NO |
| API contains latest data | NO | NO |
| First verified failure | Runtime execution/update attempt unavailable | Runtime execution/update attempt unavailable |

## 16. First Failure Boundary

The earliest **verified** break is not a provider, league, team, parser, identity, repository-condition, or cache mismatch. All those boundaries are compatible with the incoming target data, and identity resolution is proven PASS.

The earliest boundary that cannot be crossed with available runtime evidence is:

`Live Sync scheduler execution -> target fixture processing`

This boundary is **NOT VERIFIABLE** because the worker was not running and no target-specific execution evidence exists. The database proves that no successful update was persisted, but it cannot distinguish “never attempted” from “attempted then rolled back” without runtime logs.

## 17. Root Cause

### Root-cause classification

**UNKNOWN**

The exact root cause cannot be proven from the available runtime evidence. The strongest supported conclusion is:

> The two existing local Matches are not stale because of identity resolution, local-status filtering, field mapping, cache read-back, or a demonstrated lock conflict. They remain stale because no successful target update reached PostgreSQL during the observed period. Whether that is because the scheduler did not execute, the fixtures were skipped before processing, or an unobserved transaction failed is not distinguishable without a running worker and its target-specific logs.

The classification is deliberately not `SCHEDULER_EXECUTION_FAILURE`, `LIVE_FIXTURE_SKIPPED`, or `MATCH_REPOSITORY_UPDATE_FAILURE`, because none is directly evidenced for these two fixtures.

## 18. Impact

Both existing local LIVE Matches expose stale status and elapsed values to API consumers. Scores happen to match the provider at the observation time. PostgreSQL itself is stale, and the API faithfully returns that stale database state.

## 19. Limitations

- No scheduler worker was running during the audit.
- No live-sync execution counter, provider-request log, per-fixture skip log, commit log, rollback log, or exception log was available.
- No sync was triggered intentionally, and no target row was modified to test the path.
- A fresh provider request was made only for observation, not through the scheduler.
- The Redis active-match registry was empty for both targets, but core Live Sync does not use that registry as its provider fixture input.

## 20. Final Conclusion

### What is working

- Current provider data is available for both exact fixture IDs.
- Provider-to-local Match identity is correct and unique.
- League mappings are allowed and correct.
- Home and away team identities resolve.
- The parser and upsert path include status, elapsed, and score fields.
- The API reads the current PostgreSQL rows without an active `live_matches` cache entry.

### What is not working

- Neither PostgreSQL row contains the provider's current status or elapsed value.
- The API returns the same stale values.
- No successful update persistence is present for either target.
- Actual scheduler execution and target processing cannot be demonstrated.

### Where the update stops and why

The first runtime boundary that cannot be verified is scheduler execution into target fixture processing. The available evidence does not prove whether the update stops before the update path, during transaction handling, or because the scheduler did not execute. Therefore the exact required root-cause classification is **UNKNOWN**, with the proven operational symptom being **no successful database write for either existing local LIVE Match**.