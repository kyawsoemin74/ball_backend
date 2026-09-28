# Phase: Season Fixture Sync Runtime Root Cause Audit

## Final Classification

**FAIL - LEAGUE RESOLUTION**

Fixtures were returned by the provider, but every fixture was rejected because the local League Master identity for provider league `389` is incomplete.

## 1. Exact API Request

```text
POST /api/matches/sync/season?league_id=389&season=2026
Authorization: Bearer <application-generated admin access token>
Host: 127.0.0.1:8000
```

Runtime was the existing application started from `app.main:app` with the scheduler disabled for this audit. No source code or database data was changed.

## 2. Runtime Timestamp and HTTP Result

- Read-only database baseline: `2026-09-18 19:08:21.646574+06:30`.
- Request response: `2026-09-18` during the same runtime window.
- Total request time: `6.119460` seconds.
- HTTP status: `200 OK`.
- Response body:

```json
{"success":true,"inserted":0,"updated":0,"total":0,"failed":0,"final_lineup_candidates":[]}
```

Authentication passed. The route dependency is `get_current_active_admin`, which validates the Bearer JWT, loads the user, checks `is_active`, and requires `role == "admin"`. The authenticated database user was `admin`, active, with role `admin`.

## 3. Complete Execution Path

1. `app/api/matches.py`, `sync_full_season`: declares the POST route and admin dependency.
2. The route calls `run_with_resource_lock(db, "fixture_query", "global", sync)`.
3. `app/services/resource_lock.py` constructs `fover:sync:fixture_query:global` and calls PostgreSQL `pg_try_advisory_xact_lock`.
4. The route operation calls `football_service.sync_full_season(db, league=389, season=2026)`.
5. `app/services/fixture_sync_service.py`, `FixtureSyncService.sync_full_season`: loads allowed canonical league IDs, fetches the provider fixtures, and passes the response to `_process_sync`.
6. `_process_sync_with_candidates`: resolves each payload league with `LeagueRepository.find_by_provider_identity(db, "api-football", provider_id)`.
7. A missing identity logs the unresolved-League warning and skips that fixture. With no filtered fixtures, it returns `success=True` and zero counts.
8. The route sees `success=True`, calls `await db.commit()`, applies empty post-sync actions, and returns the result.
9. The resource-lock wrapper logs release and the monitoring middleware logs request completion.

Relevant source decision points:

- [app/api/matches.py](../app/api/matches.py): route, rollback/commit, and HTTP response contract.
- [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py): provider fetch, league resolution, allowed gate, and zero-work result.
- [app/repositories/league_repository.py](../app/repositories/league_repository.py): exact provider identity query.
- [app/repositories/allowed_league_repository.py](../app/repositories/allowed_league_repository.py): allowed IDs are canonical `allowed_leagues.league_id` values.
- [app/services/resource_lock.py](../app/services/resource_lock.py): advisory lock identity and release.
- [app/core/security.py](../app/core/security.py): authentication and admin authorization.

## 4. Provider Payload Evidence

The existing `FixtureProvider.get_fixtures(league=389, season=2026)` was executed directly against API-Football.

- Payload was a dictionary.
- Provider response count: **240 fixtures**.
- Paging: `current=1`, `total=1`.
- Provider errors: `{}`.
- All inspected fixture records carried `league.id=389`.
- All inspected fixture records carried `league.season=2026`.
- Example fixture IDs included `1528105`, `1528101`, and `1528107`.
- The provider therefore returned valid, relevant fixtures; the response was not empty or malformed.

## 5. League 389 Resolution Evidence

Read-only PostgreSQL queries immediately before and after the request showed:

```text
leagues.league_id       = 389
leagues.provider        = api-football
leagues.provider_id     = '' / empty
leagues.name            = Premier League
leagues.is_featured     = true

allowed_leagues.league_id = 389  (present)
```

Additional identity checks:

- Exact row `(provider='api-football', provider_id='389')`: **0 rows**.
- Any row with `provider_id='389'`: **0 rows**.
- Canonical `league_id=389`: **1 row**.
- Canonical `league_id=389` in `allowed_leagues`: **present**.
- Duplicate/conflicting provider identity for `389`: **none**.
- Teams with `provider_id='389'`: **0**. Team resolution was never reached.

The exact repository query is equivalent to:

```python
select(League).where(
    (League.provider == "api-football") &
    (League.provider_id == "389")
)
```

It does not fall back from provider identity to canonical `league_id`.

## 6. Failure Classification

**Selected classification: `PROVIDER_ID_MISMATCH`**

The canonical row exists and is allowed, but its provider identity column is empty. The provider sends `389`; the resolver requires `provider='api-football'` and `provider_id='389'`; that exact identity does not exist. This is not `LEAGUE_RECORD_MISSING`, `ALLOWED_LEAGUE_FAILURE`, `QUERY_FILTER_FAILURE`, or a transaction visibility issue.

The requested `league_id=389` does pass the initial allowed-league check in `sync_full_season`. The failure occurs later, while resolving the provider league ID on each returned fixture.

## 7. Allowed League Gate and Fixture Processing

The implementation has two relevant gates:

1. `sync_full_season` checks whether the requested canonical `league_id` (`389`) is in `allowed_leagues`. It is, so provider fetch executes.
2. `_process_sync_with_candidates` resolves each payload provider league and then checks the resolved canonical ID against the allowed ID set. Because resolution returns `None`, the fixture is skipped before the allowed-ID comparison can pass.

All 240 returned fixtures followed this path:

```text
provider league 389
  -> lookup (api-football, 389)
  -> no League row
  -> fixture skipped
```

The fixture is not queued for retry, inserted, updated, or aborted. It is skipped. Since the filtered fixture list is empty, the entire season synchronization work is skipped after provider fetch.

## 8. Database Before/After

| Measurement | Before | After | Result |
|---|---:|---:|---|
| `matches` for canonical league 389, season 2026 | 0 | 0 | unchanged |
| `matches` for canonical league 389, all seasons | 0 | 0 | unchanged |
| `leagues` row for canonical 389 | 1 | 1 | unchanged |
| `allowed_leagues` row for canonical 389 | 1 | 1 | unchanged |
| `teams` with provider ID 389 | 0 | 0 | unchanged |
| `league_seasons` rows for league 389 | 0 | 0 | unchanged |
| All `matches` with season 2026 | 1844 | 1844 | unchanged |

Counts:

- Inserted: **0**
- Updated: **0**
- Persisted: **0**
- Skipped: **240**
- Failed: **0** in the returned result

## 9. Transaction and Commit Evidence

No fixture reached parsing, team resolution, match upsert, or fixture savepoint processing. Therefore no fixture database write was attempted.

The route source commits when the service result has `success=True`. The zero-work result from `_process_sync_with_candidates` has `success=True`, so the route executes `await db.commit()` even though there are no writes. The runtime log contains `sync_completed`; there is no dedicated SQL `COMMIT` log line in the application output. A rollback would occur only for a false result or an exception; neither occurred.

Cache invalidation and final-lineup work received empty result data. No cache or match persistence change was observed.

## 10. Resource Lock Evidence

The request used the exact lock identity:

```text
fover:sync:fixture_query:global
```

Runtime evidence shows the lock lifecycle completed and the lock was released:

```text
INFO:app.services.resource_lock:RESOURCE_LOCK_RELEASED
lock_identity=fover:sync:fixture_query:global
```

The request was not rejected with HTTP 409, so lock conflict did not affect this request. The source lock implementation uses a non-blocking PostgreSQL advisory transaction lock and logs acquisition/conflict; the observed request proceeded into provider fetch and fixture processing.

## 11. Correlated Runtime Timeline

| Stage | Status | Evidence |
|---|---|---|
| Request | PASS | Exact POST returned from local API |
| Authentication/authorization | PASS | Active admin Bearer token accepted |
| Resource lock | PASS | Request proceeded; release logged for `fover:sync:fixture_query:global` |
| Provider fetch | PASS | 240 fixtures, one page, no provider errors |
| League resolution | FAIL | 240 unresolved `provider_id=389` warnings |
| Allowed-league validation | SKIPPED for individual fixtures | No canonical master was resolved |
| Fixture processing | SKIPPED | Filtered fixture list empty |
| Team resolution | NOT EXECUTED | No fixture passed the league filter |
| Match upsert | NOT EXECUTED | No fixture passed the league filter |
| Transaction commit | PASS with zero writes | Route commits successful zero-work result; no SQL commit log emitted |
| Cache/finalization | SKIPPED/empty | No active updates or lineup candidates |
| Monitoring | PASS | `sync_completed` logged |
| Response | PASS as request handling | HTTP 200 with zero-work result |

Observed log sequence included:

```text
WARNING:app.services.fixture_sync_service:Skipping fixture with unresolved provider League: provider_id=389
INFO:app.services.fixture_sync_service:No allowed leagues were present in the fixture payload; skipping fixture synchronization.
INFO:app.monitoring:sync_completed
INFO:app.services.resource_lock:RESOURCE_LOCK_RELEASED lock_identity=fover:sync:fixture_query:global
INFO:app.monitoring:http_request_completed
POST /api/matches/sync/season?league_id=389&season=2026 HTTP/1.1" 200 OK
```

## 12. Exact Root Cause

```text
ROOT CAUSE:

The provider returned 240 valid Premier League fixtures for provider league 389 and season 2026, but the local canonical League row 389 has an empty provider_id. The fixture resolver requires the exact identity (provider='api-football', provider_id='389'), finds no row, and skips every fixture before team or match persistence.

Failure point:

FixtureSyncService._process_sync_with_candidates in app/services/fixture_sync_service.py, at the provider League lookup and `master is None` skip branch.

Provider evidence:

240 fixtures returned; all carry provider league ID 389 and season 2026; provider errors are empty.

Database evidence:

League 389 exists with provider='api-football' but empty provider_id; it is allowed; exact provider identity 389 has zero rows.

Runtime evidence:

Repeated unresolved provider League warnings for provider_id=389, followed by the no-allowed-leagues message and sync_completed.

Persistence result:

0 inserted, 0 updated, 240 skipped; matches for league 389 and season 2026 remained 0 before and after.

HTTP result:

200 OK

Why 200 was returned:

The zero-work branch returns success=True with zero counts. The API route treats success=True as a commit-and-return condition, commits an empty transaction, and returns the service dictionary under a route declared with status_code=200. Thus 200 means the request was processed successfully, not that any fixture was persisted.
```

## 13. Conclusion

This audit proves **FAIL - LEAGUE RESOLUTION**. The provider payload is valid and contains 240 requested-season fixtures. The synchronization produces no useful database change because the canonical league row for 389 is missing its provider identity value, and the current resolver has no canonical-ID fallback. No source code or database repair was performed.