# PHASE 4.1 - FIXTURE SYNC RUNTIME DISCREPANCY ROOT-CAUSE AUDIT

**Date:** 2026-09-19  
**Scope:** explain why direct Provider `(league=1, season=2026)` returns fixtures while normal HTTP sync with local `league_id=1274` returns `No fixtures found`.  
**Safety:** no source code, configuration, migration, database structure, fixture rows, or Match rows were modified.

## 1. Objective

Trace both runtime paths and identify the exact first point where the HTTP path differs from the direct Provider path.

The selected identity was:

```text
local league_id = 1274
provider = api-football
provider_id = 1
season = 2026
```

## 2. Test League / Provider Identity

Real PostgreSQL state before this audit confirmed:

| Field | Value |
|---|---|
| Local `league_id` | `1274` |
| Provider | `api-football` |
| Provider ID | `1` |
| Name | `World Cup` |
| Country ID | `2105094199` |
| Country | `World` |
| Allowed | Yes |

The exact `(api-football, '1')` identity resolved to exactly one League row. The identity itself is valid and is not the cause of the discrepancy.

## 3. Direct Provider Path

The direct audit script called:

```python
await football_service.fixture_provider.get_fixtures(
    league=1,
    season=2026,
)
```

`FixtureProvider.get_fixtures()` calls the API client with:

```python
client.get("/fixtures", params={"league": league, "season": season})
```

Observed real Provider result:

| Field | Value |
|---|---|
| Provider request | `/fixtures?league=1&season=2026` |
| Response object | Present |
| Provider errors | `[]` |
| Response fixture count | `104` |
| Paging | `current=1, total=1` |
| Provider League IDs in fixtures | `1` |
| Season in fixture payloads | `2026` |

The direct Provider path receives real fixtures.

## 4. HTTP Sync Path

The normal route is:

```text
POST /api/matches/sync/season?league_id=1274&season=2026
  -> app/api/matches.py:sync_full_season()
  -> football_service.sync_full_season(db, league=1274, season=2026)
  -> FootballAPIService.sync_full_season()
  -> FixtureSyncService.sync_full_season()
  -> FixtureProvider.get_fixtures(league=1274, season=2026)
```

The route checks whether request value `1274` is in `allowed_leagues`, but it does not resolve that local ID through `LeagueRepository.find_by_provider_identity()` and does not read the League Master to obtain provider ID `1`.

The loaded implementation of `FixtureSyncService.sync_full_season()` is:

```python
allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
if league not in allowed_ids:
    return {"success": True, ...}

result = await self.fixture_provider.get_fixtures(league=league, season=season)
...
fixtures = result.get("response", [])
if not fixtures:
    return {"success": False, "message": "No fixtures found"}
```

There is no local-to-provider identity conversion in this path.

## 5. Provider Request Comparison

| Parameter | Direct Provider | HTTP Sync | Difference |
|---|---:|---:|---|
| Provider | API-Football | API-Football | None |
| Endpoint | `/fixtures` | `/fixtures` | None |
| `league` | `1` | `1274` | **Critical mismatch** |
| `season` | `2026` | `2026` | None |
| `page` | omitted | omitted | None |
| status filter | omitted | omitted | None |
| date filter | omitted | omitted | None |
| pagination | Provider default | Provider default | None |

The HTTP path sends the canonical local League ID as the external Provider League ID.

## 6. Provider Response Comparison

### Direct request

```text
request: league=1, season=2026
errors: []
response count: 104
paging: current=1, total=1
```

### HTTP-equivalent service request

A standalone read-only wrapper around the live service method recorded the actual Provider arguments and delegated to the real Provider client:

```json
{
  "request_to_service": {
    "local_league_id": 1274,
    "season": 2026
  },
  "provider_calls": [
    {
      "league": 1274,
      "season": 2026,
      "response_count": 0,
      "errors": [],
      "paging": {"current": 1, "total": 1}
    }
  ],
  "service_result": {
    "success": false,
    "message": "No fixtures found",
    "final_lineup_candidates": []
  }
}
```

A direct real Provider call with `league=1274, season=2026` independently returned the same zero-response shape. Thus the Provider is behaving consistently for the two different League parameters.

## 7. Fixture Filtering Trace

The data-loss trace is:

```text
Provider request league=1274, season=2026
  -> Provider response: 0 fixtures
  -> FixtureSyncService receives empty response list
  -> returns No fixtures found
  -> no fixture filtering
  -> no provider identity resolution per fixture
  -> no Team resolution
  -> no Match persistence
```

The HTTP path does not reach `_process_sync_with_candidates()` for this result. Therefore:

| Stage | Count |
|---|---:|
| Provider fixtures for correct provider ID `1` | 104 |
| Provider fixtures requested by HTTP path with `1274` | 0 |
| Fixtures entering FixtureSync filtering | 0 |
| Fixtures after identity filtering | 0 |
| Fixtures reaching Team resolution | 0 |
| Fixtures reaching Match persistence | 0 |
| Inserted/updated Matches | 0 |

The first reduction occurs inside the Provider response retrieval caused by the wrong `league` query parameter.

## 8. Identity Resolution Trace

The League identity itself is correct in PostgreSQL:

```text
(api-football, provider_id=1)
  -> League Master local league_id=1274
```

However, `FixtureSyncService.sync_full_season()` uses its input integer directly for the Provider request. It treats the caller's `league` argument as if it were already a Provider League ID.

The HTTP route caller supplies the local ID:

```text
request league_id=1274
  -> service league=1274
  -> Provider league=1274
```

The required generic conversion is absent:

```text
local league_id=1274
  -> League Master lookup
  -> provider_id=1
  -> Provider league=1
```

The per-fixture resolver in `_process_sync_with_candidates()` cannot correct this because it only runs after fixtures have been returned.

## 9. Runtime Process Verification

The running Uvicorn process was inspected read-only:

| Field | Value |
|---|---|
| Process | Uvicorn / Python |
| Executable | `D:\fover_backend\venv\Scripts\python.exe` |
| Command | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| Startup | `2026-09-19 00:47:43` |
| Workspace | `D:\fover_backend` |

The direct verification scripts used `D:\fover_backend\.venv\Scripts\python.exe`. Both environments loaded the same current `FixtureProvider.get_fixtures()` and `FixtureSyncService.sync_full_season()` source implementation. The discrepancy is not caused by a source-code mismatch between the inspected workspace and the running process.

The process environment details were not fully enumerable from the PowerShell process listing, but the actual HTTP-equivalent in-process service trace and direct Provider comparisons prove the parameter mismatch independently of environment variables.

## 10. Cache/State Verification

The full-season path does not read a fixture-result cache before calling the Provider. Its cache interactions are for post-processing/live or lineup invalidation after fixture handling. No cache lookup can transform `league=1274` into `league=1` or explain the Provider response difference.

No cache was deleted or invalidated manually during this audit.

The database identity and allow-list state were stable during both HTTP attempts. The repeated HTTP call returned the same `No fixtures found` result, so the discrepancy is deterministic for the wrong Provider parameter rather than a transient cache state.

## 11. Database Before/After

Before the HTTP attempts:

| Check | Value |
|---|---:|
| League row for local 1274 | 1 |
| Provider ID | 1 |
| Country ID | 2105094199 |
| Allowed row | 1 |
| Matches for local 1274 | 0 |
| Matches for local 1274 / season 2026 | 0 |

After two identical HTTP attempts:

| Check | Value |
|---|---:|
| League row for local 1274 | 1 |
| Provider ID | 1 |
| Country ID | 2105094199 |
| Allowed row | 1 |
| Matches for local 1274 | 0 |
| Matches for local 1274 / season 2026 | 0 |
| Database change from HTTP sync | No |

The route returned HTTP 200 with `success=false`; this was not a database write success.

## 12. Exact Failure Point

The exact runtime sequence is:

```text
Provider has 104 fixtures for league=1, season=2026
        ↓
HTTP route accepts local league_id=1274
        ↓
FixtureSyncService.sync_full_season(league=1274, season=2026)
        ↓
FixtureProvider.get_fixtures(league=1274, season=2026)
        ↓
Provider returns 0 fixtures
        ↓
FixtureSyncService returns "No fixtures found"
        ↓
0 fixtures enter filtering
        ↓
0 fixtures resolve identity
        ↓
0 fixtures reach Match persistence
        ↓
0 inserted, 0 updated
```

The first data disappearance is at Provider retrieval because the HTTP path sends `1274` instead of the League Master’s `provider_id=1`.

## 13. Root Cause

**ROOT CAUSE:** `FixtureSyncService.sync_full_season()` uses its `league` argument directly as the Provider League query parameter. The normal HTTP route receives the canonical local `league_id`, but no League Master lookup converts it to the Provider identity. Consequently, the Provider request is made with `league=1274` instead of `league=1`.

This is a generic local-ID/provider-ID boundary defect. It is not caused by League 389, Country, AllowedLeague, Team identity, Match persistence, cache, pagination, or Provider instability.

## 14. Generic Impact

The defect affects any newly onboarded or existing League where:

```text
local league_id != provider_id
```

Such a League can be correctly registered, Country-linked, and allowed, yet full-season Fixture Sync asks the Provider for the wrong external League ID and receives no fixtures or an unrelated Provider response.

The defect may remain hidden when local and provider IDs happen to be numerically equal, which explains why some existing League tests and runtime cases can pass without proving the generic contract.

## 15. Evidence

1. Real Provider catalog and details response selected provider League `1`, World Cup, World, season 2026.
2. PostgreSQL contained exactly one identity row: `(api-football, '1') -> local league_id 1274`.
3. Direct Provider call `league=1, season=2026` returned 104 fixtures.
4. Direct Provider call `league=1274, season=2026` returned 0 fixtures.
5. Source trace shows the HTTP route passes `league_id` unchanged into `football_service.sync_full_season()`.
6. Loaded source trace shows `FixtureSyncService.sync_full_season()` calls `get_fixtures(league=league, season=season)` without League Master resolution.
7. In-process wrapper recorded the normal service request as `league=1274, season=2026` and response count `0`.
8. Two real HTTP attempts both returned HTTP 200 with `success=false` and `No fixtures found`.
9. PostgreSQL Match count remained zero before and after.
10. No source code or database structure was modified during this audit.

## 16. Limitations

- Raw Uvicorn stdout was not independently captured; the exact request parameter was proven through the same loaded service implementation and an in-process wrapper using the real Provider client.
- The HTTP path was not allowed to be fixed in this phase, so no corrected end-to-end Match persistence run was attempted.
- No manual Match or fixture data was inserted.
- Existing legacy null identity rows were not changed.

## 17. Final Classification

# ROOT CAUSE IDENTIFIED

The discrepancy is proven. The direct path sends Provider League ID `1`; the normal HTTP full-season path sends the local League ID `1274` directly to the Provider. The Provider returns fixtures for `1` and zero fixtures for `1274`, so the HTTP path loses all data before FixtureSync filtering and Match persistence.

No fix was implemented in Phase 4.1.
