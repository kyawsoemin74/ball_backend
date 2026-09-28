# Season Fixture Sync League 389 Runtime Audit

## 1. API Execution Summary

| Field | Evidence |
|---|---|
| Endpoint | `POST /api/matches/sync/season?league_id=389&season=2026` |
| `league_id` | `389` |
| `season` | `2026` |
| HTTP status | `200 OK` |
| Response time | `6.119460` seconds |
| Response body | `{"success":true,"inserted":0,"updated":0,"total":0,"failed":0,"final_lineup_candidates":[]}` |
| Runtime date | `2026-09-18` |

The request was authenticated with the application JWT path as an active admin. No source code or database data was modified during this audit.

### Exact call chain

1. `app/api/matches.py:sync_full_season` receives the request and requires `current_active_admin`.
2. The route calls `run_with_resource_lock(db, "fixture_query", "global", sync)`.
3. `app/services/resource_lock.py` builds `fover:sync:fixture_query:global` and obtains the PostgreSQL advisory transaction lock.
4. The route calls `football_service.sync_full_season(db, league=389, season=2026)`.
5. `FixtureSyncService.sync_full_season` checks the requested canonical ID against `allowed_leagues`, then calls `FixtureProvider.get_fixtures(league=389, season=2026)`.
6. `_process_sync_with_candidates` resolves each payload League with `LeagueRepository.find_by_provider_identity(db, "api-football", provider_id)`.
7. Unresolved fixtures are skipped; an empty filtered list returns `success=True` with zero counters.
8. The route commits the result, performs empty post-sync actions, and returns HTTP 200.

Relevant implementation surfaces: [app/api/matches.py](../app/api/matches.py), [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py), [app/repositories/league_repository.py](../app/repositories/league_repository.py), [app/repositories/allowed_league_repository.py](../app/repositories/allowed_league_repository.py), and [app/services/resource_lock.py](../app/services/resource_lock.py).

## 2. Provider Evidence

| Field | Runtime result |
|---|---|
| Provider called | **YES** |
| Provider | API-Football, through the existing `FixtureProvider` |
| Request | `/fixtures?league=389&season=2026` |
| Provider response | Dictionary with `response` list, `paging.current=1`, `paging.total=1`, `errors={}` |
| Fixture count | **240** |
| Provider League ID | `389` on all returned fixtures |
| Provider League name | `Premier League` |
| Provider season | `2026` on all returned fixtures |
| Example fixture IDs | `1528105`, `1528101`, `1528107` |

The provider response was present, relevant, and not malformed. Provider response failure is ruled out.

## 3. League Identity Evidence

Read-only PostgreSQL evidence for the canonical row:

| Field | Value |
|---|---|
| Local `league_id` | `389` |
| `provider` | `api-football` |
| `provider_id` | SQL `NULL` |
| League name | `Premier League` |
| `country_id` | SQL `NULL` |
| Active status | No `active`, `is_active`, or `status` column exists on this `leagues` table; `is_featured=true` is present but is not used by the sync resolver |
| Allowed status | **Allowed**: `allowed_leagues.league_id=389` exists |
| Exact identity duplicates | **0** rows for `(provider='api-football', provider_id='389')` |

Resolution result:

```text
payload provider identity: (api-football, 389)
local lookup: League.provider == 'api-football'
              AND League.provider_id == '389'
lookup result: no row
canonical league_id=389: exists, but provider_id is NULL
```

Exact failure reason: the canonical League record exists and is allowed, but its provider identity is not mapped. The resolver does not fall back from provider ID to canonical `league_id`.

**Classification of the identity failure: `PROVIDER_ID_MISMATCH`**

The final approved audit classification is given in Section 9 as `LEAGUE_IDENTITY_FAILURE`.

## 4. Fixture Processing

| Counter | Result |
|---|---:|
| Provider fixtures | 240 |
| Accepted fixtures | 0 |
| Skipped fixtures | 240 |
| Rejected fixtures | 0 |

Every fixture followed this path:

```text
provider League ID 389
  -> find_by_provider_identity('api-football', '389')
  -> no local master row
  -> log unresolved provider League
  -> skip fixture
```

Runtime logs contained repeated:

```text
Skipping fixture with unresolved provider League: provider_id=389
```

After all fixtures were skipped, the service logged:

```text
No allowed leagues were present in the fixture payload; skipping fixture synchronization.
```

That message was the direct reason no fixture entered team resolution or Match upsert. The service returned `success=True`, `inserted=0`, `updated=0`, `total=0`, and `failed=0`.

## 5. Database Evidence

Read-only counts immediately before and after the exact request:

| Query | Before | After |
|---|---:|---:|
| `matches` where `league_id=389 AND season=2026` | 0 | 0 |
| `matches` where `league_id=389`, all seasons | 0 | 0 |
| `leagues` row `league_id=389` | 1 | 1 |
| `allowed_leagues` row `league_id=389` | 1 | 1 |
| `teams` with `provider_id='389'` | 0 | 0 |
| `league_seasons` rows for league 389 | 0 | 0 |
| All matches with season 2026 | 1844 | 1844 |

For the returned provider fixture IDs, no Match rows were created because no fixture passed League resolution. Therefore:

- Existing matching records: **0**.
- Inserted: **0**.
- Updated: **0**.
- Unchanged: **0** matching records; the pre-existing database state was unchanged.
- Missing: **240** provider fixtures have no corresponding persisted Match through this execution.
- `created_at`, `updated_at`, `provider_fixture_id`, `league_id`, `season`, `home_team_id`, and `away_team_id`: no new or updated rows to inspect.

**Did this API execution actually change the database? `NO`.**

Evidence: the before/after counts are identical, the response counters are all zero, and the fixture write loop was never entered.

## 6. Transaction Evidence

| Question | Result | Evidence |
|---|---|---|
| Transaction/session started | **YES** | The route received an `AsyncSession`; the advisory lock was acquired through the database session |
| Writes attempted | **NO** | All 240 fixtures were rejected before parsing, team resolution, or Match upsert |
| Commit reached | **YES** | The route executes `await db.commit()` whenever the service result has `success=True`; the zero-work result had `success=True` |
| Rollback triggered | **NO** | No false result or exception occurred; the request completed normally |
| Operation completed normally | **YES** | `sync_completed`, lock release, request completion, and HTTP 200 were logged |

There is no dedicated SQL `COMMIT` log line in the captured application output. Commit is proven by the route’s source-controlled branch and the normal completion path; no data was written before that empty commit.

This was **not a successful sync**. It was a **successful request with zero fixtures synchronized**.

## 7. HTTP Response Analysis

The service branch for an empty filtered fixture list returns:

```python
{
    "success": True,
    "inserted": 0,
    "updated": 0,
    "total": 0,
    "failed": 0,
    "final_lineup_candidates": [],
}
```

The route treats `success=True` as permission to commit and return the dictionary. The route itself is declared with `status_code=200`. It does not require `inserted > 0`, `updated > 0`, or `total > 0` before returning 200.

Therefore:

```text
HTTP success != synchronization success != database change
```

Here, `200 OK` means the request was authenticated, processed without an exception, and returned a successful service result. It does not mean fixtures were accepted or persisted.

## 8. Root Cause

The provider returned 240 valid fixtures for provider League `389`, season `2026`. Local canonical League `389` exists and is allowed, but `leagues.provider_id` is SQL `NULL`. The provider identity lookup for `(api-football, 389)` returned no row. Consequently, `FixtureSyncService._process_sync_with_candidates` skipped all 240 fixtures at the unresolved-provider-League branch. The empty filtered list then caused the service to skip all fixture writes while still returning `success=True`.

The gate preventing writes was therefore **provider League identity resolution**, before team resolution and Match upsert, not provider fetch, the allowed-list membership of canonical 389, the resource lock, or transaction failure.

## 9. Final Classification

**LEAGUE_IDENTITY_FAILURE**

## 10. Recommended Next Step

The smallest next investigation/fix is to inspect the League Master identity-mapping workflow and determine why canonical `league_id=389` was created or retained with `provider_id=NULL`. Any repair should be handled in a separate implementation phase after confirming the intended canonical mapping and its migration/data-change approval. No repair was performed in this audit.