# LIVE SYNC - REAL RUNTIME OPERATIONAL AUDIT REPORT

## 1. Audit Objective

This read-only audit verified the running Live Sync chain against the real API-Football provider, the running PostgreSQL database, and API read-back. No source code, schema, scheduler configuration, or database rows were modified.

Audit time: 2026-09-19, local database time UTC+06:30.

## 2. Runtime Environment

- PostgreSQL was reachable on localhost:5432 as `fover_user`.
- Redis was reachable on localhost:6379.
- Uvicorn was serving the API on port 8000.
- API readiness returned HTTP 200: `{"status":"ready","postgres":true,"redis":true}` at the successful read-back check.
- No `worker.py` process was present and port 8001 refused connections. Therefore scheduler execution counters and worker logs were not available during this audit window.
- Migration head: `20260915_missing_lineup_identity`.

## 3. Scheduler Status

The registered scheduler configuration was inspected. `sync_live_matches` is configured for every 60 seconds; `reconcile_recent_non_terminal` is configured for every 5 minutes. The scheduler also registers separate odds, lineup, event, and statistics refresh jobs.

Runtime classification: **FAIL** for operational verification. No worker process or 8001 metrics endpoint was running, so repeated execution, last successful execution, missed executions, swallowed exceptions, and scheduler job counters could not be demonstrated. No active advisory locks were present in PostgreSQL at query time (`0`).

## 4. Live Provider Payload

The configured provider was called directly using the application provider client and API key. The live endpoint returned HTTP 200, no provider errors, and 49 fixtures in one sample; an immediately preceding provider-client sample returned 50 fixtures in 0.717 seconds. The payload contained live statuses including `1H`, `HT`, and `2H`, elapsed minutes, scores, provider fixture IDs, provider league IDs, and provider team IDs.

Representative live payloads:

| Provider fixture | League | Home/Away provider teams | Status | Elapsed | Score |
|---|---:|---|---|---:|---:|
| 1570397 | 140 | 540 / 797 | HT | 45 | 0-2 |
| 1557408 | 39 | 55 / 49 | 2H | 49 | 0-0 |

Provider discovery itself: **PASS**. Provider-to-backend processing: **FAIL**, because only 2 of 50 sampled provider fixtures had local rows; 48 were absent.

## 5. League Identity Resolution

For the two provider/local-correlated samples, provider league IDs mapped correctly to local leagues:

- Provider 140 -> local league 140, La Liga, allowed.
- Provider 39 -> local league 39, Premier League, allowed.

The Live Sync code explicitly skips unresolved provider leagues and disallowed leagues, but scheduler logs for those decisions were unavailable because the worker was not running. For the correlated samples, league resolution: **PASS**. Overall discovery-to-persistence: **FAIL** due to 48 missing local fixtures; the exact reason for each missing fixture cannot be assigned without worker execution logs.

## 6. Team Identity Resolution

Both correlated samples had non-null local home and away team references and provider team identities consistent with the provider payload:

- Fixture 1570397: local teams 25492 / 25486, corresponding provider team IDs 540 / 797.
- Fixture 1557408: local teams 25602 / 25599, corresponding provider team IDs 55 / 49.

Database checks found zero orphan home-team or away-team references. Sampled team resolution: **PASS**. Team resolution for the 48 provider fixtures with no local row: **N.V.** because there was no runtime processing trace identifying whether they were filtered, unresolved, or never processed.

## 7. Match Update Verification

Direct provider versus PostgreSQL comparison found stale local state for both correlated fixtures:

| Fixture | Provider state | PostgreSQL state | API state |
|---|---|---|---|
| 1570397 | HT, elapsed 45, 0-2 | 1H, elapsed 41, 0-2 | 1H, elapsed 41, 0-2 |
| 1557408 | 2H, elapsed 49, 0-0 | 1H, elapsed 34, 0-0 | 1H, elapsed 34, 0-0 |

The database had 2 live-like rows, both stale by more than six hours using `coalesce(updated_at, created_at)`. All 2,332 `matches` rows had `updated_at IS NULL`, so the timestamp cannot establish update progression. Match live update: **FAIL**.

## 8. Live -> Terminal Lifecycle

The database contained 1,090 `FT`, 5 `AET`, and 10 `PEN` rows. No natural live-to-terminal transition was observed during this window, and the worker was not active. No runtime evidence was available for `FINAL_LIVE_SYNC_REQUIRED`, `FINAL_LIVE_SYNC_STARTED`, provider terminal refetch, final persistence, or downstream finalization.

The source path includes a final provider fetch after terminal detection, but this audit does not treat source code as runtime proof. Terminal fixtures were not incorrectly marked live in the database query. Lifecycle verification: **N.V.**.

## 9. Event Sync

The scheduler has a separate `refresh_events` job. The database contained 8,767 event rows across 499 matches, with zero orphan event-match references. A read-only natural-key check found 2 duplicate event groups. No current live fixture had persisted events (`0` active event rows), and no provider event response was sampled in this audit. Event sync: **N.V.**, with a duplicate-event integrity finding.

## 10. Statistics Sync

The scheduler has a separate `refresh_statistics` job. Only 1 `match_statistics` row existed, and zero current live fixtures had statistics. No live provider statistics response was sampled. Statistics sync: **N.V.**.

## 11. Lineup Sync

The scheduler has a separate `refresh_lineups` job. The database contained 71 lineup rows, with zero current live fixtures having a lineup row. No live lineup provider response was sampled. Lineup sync: **N.V.**.

## 12. Odds Sync

Odds are handled by a separate `refresh_odds` scheduler job, not by the core live fixture upsert operation. The database contained 44 odds rows and zero orphan odds references. No current live odds response or live odds update was sampled. Core Live Sync ownership of live odds is therefore not demonstrated; odds sync: **N.V.**.

## 13. Transaction Ownership

The inspected runtime path gives the scheduler/API caller ownership of commit and rollback. Live Sync commits only when its aggregate result reports success and rolls back on failure; cache invalidation occurs after commit. Because no worker execution was available, no real live transaction, rollback, commit confirmation, or failure propagation was observed. Transaction design: **PASS WITH LIMITATIONS**; runtime execution evidence: **N.V.**.

## 14. Cache Behavior

The API returned exactly the stale PostgreSQL values for both sampled matches, so there was no separate cache disagreement in those responses. The API's `live_all` response included both stale rows. Post-commit cache invalidation was not directly observed because the worker was absent. Cache read-back: **PASS WITH LIMITATIONS**; successful live-write invalidation: **N.V.**.

## 15. Lock / Concurrency

No advisory locks were held at query time. No worker logs were available for lock acquisition, rejection, release, overlap, starvation, or deadlock. The source path uses a `live_sync:global` resource lock and fixture-level locks for final live sync, but this was not runtime-demonstrated. Lock state snapshot: **PASS WITH LIMITATIONS**; scheduler concurrency behavior: **N.V.**.

## 16. Database Integrity

Read-only PostgreSQL checks:

- Duplicate `(provider, provider_fixture_id)` Match identities: 0.
- Orphan Match -> League: 0.
- Orphan Match -> home Team: 0.
- Orphan Match -> away Team: 0.
- Null Match provider fixture IDs: 0.
- Invalid Match statuses: 0.
- Orphan event, statistics, lineup, and odds references: 0.
- Duplicate event natural-key groups: 2.
- Stale live-like rows over six hours: 2.
- Matches with non-null `updated_at`: 0 of 2,332.

Database integrity: **FAIL** due to stale active rows, absent update timestamps, and duplicate event groups, even though foreign-key and Match-identity checks passed.

## 17. API Read-back

`GET /api/matches/1570397`, `GET /api/matches/1589091`, and `GET /api/matches/live_all` returned HTTP 200. For both sampled fixtures, API status and elapsed matched PostgreSQL, but differed from the live provider. Therefore provider = database = API was false for both status and elapsed. API read-back: **FAIL**.

## 18. Error Classification

| Classification | Evidence | Affected fixtures / impact |
|---|---|---|
| FIXTURE_DISCOVERY_FAILURE | 48 of 50 provider-live fixture IDs had no local Match row | 48 provider fixtures; absent from DB and API |
| STALE_ACTIVE_FIXTURE | Provider advanced status/elapsed while local rows remained 1H/old elapsed | 1570397 and 1557408; stale DB and API |
| CACHE_STALENESS | Not isolated; API matched stale DB values | No independent cache divergence proven |
| EVENT_SYNC_FAILURE | 2 duplicate event natural-key groups | Affected match IDs not resolved to a current live sample |
| SCHEDULER / LOCK / FINALIZATION | Worker absent, no runtime logs or job metrics | Runtime behavior not verifiable, not assigned as proven root cause |

## 19. Root Cause(s)

The proven failure boundary is between the real provider live feed and persisted Match state: current provider fixtures are not fully represented locally, and the two represented live fixtures were not updated to the provider's current status and elapsed values. The exact cause cannot be distinguished between scheduler absence, gate/filtering, unresolved identity, or an earlier failed execution because no worker runtime logs were available.

The database also lacks `updated_at` values for every Match, preventing reliable freshness auditing. Duplicate event natural-key groups indicate a downstream data-integrity issue, but this audit did not establish whether it came from live sync, event refresh, or historical ingestion.

## 20. Impact

Current users receive an incomplete and stale live fixture list. For the two correlated fixtures, API status and elapsed values lag the provider. Forty-eight current provider fixtures cannot be read back as local matches. Event, statistics, lineup, and odds correctness for current live fixtures cannot be established.

## 21. Limitations

- The scheduler worker was not running during the audit; no job execution logs, counters, or lock lifecycle logs were available.
- No live-to-terminal transition occurred naturally during the observation window.
- Provider event, statistics, lineup, and odds payloads were not sampled.
- The 48 missing fixtures cannot be attributed to a specific identity or allowed-league reason without worker logs or a live sync execution.
- No data was inserted, updated, deleted, repaired, or manually re-synchronized.

## 22. Final Flow Matrix

| Stage | Result | Evidence |
|---|---|---|
| Provider | PASS | HTTP 200; 49-50 live fixtures; no provider errors |
| Live Fixture Discovery | FAIL | 48 of 50 provider fixtures absent locally |
| League Identity | PASS WITH LIMITATIONS | Both correlated samples resolved; missing-fixture reasons unavailable |
| Team Identity | PASS WITH LIMITATIONS | Both correlated samples resolved; missing-fixture reasons unavailable |
| Match Upsert | FAIL | Both correlated local rows stale; 48 absent |
| Event Sync | N.V. | No current live payload/read-back; duplicate groups found |
| Statistics Sync | N.V. | No current live rows or sampled payload |
| Lineup Sync | N.V. | No current live rows or sampled payload |
| Odds Sync | N.V. | Separate job/boundary; no live odds payload sampled |
| Transaction | PASS WITH LIMITATIONS | Source ownership and DB state inspected; worker transaction not observed |
| Cache | PASS WITH LIMITATIONS | API matched DB; post-commit invalidation not observed |
| Terminal Finalization | N.V. | No natural transition and no worker |
| API Read-back | FAIL | API matched stale DB, not current provider |

## 23. Final Classification

**FAIL**

Live Sync is not currently demonstrated to work end-to-end for current real live fixtures. Real provider data showed 49-50 live fixtures, while only 2 had local Match rows; both local rows were stale (`1H` versus provider `HT`/`2H`) and the API returned the same stale state. The scheduler lifecycle, terminal finalization, and downstream live data components remain unverified because the worker was not running, but the provider-to-database-to-API mismatch alone is sufficient for a FAIL classification.