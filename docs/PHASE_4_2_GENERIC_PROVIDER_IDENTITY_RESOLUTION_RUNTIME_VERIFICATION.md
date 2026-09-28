# PHASE 4.2 - GENERIC PROVIDER IDENTITY RESOLUTION RUNTIME VERIFICATION

**Date:** 2026-09-19  
**Scope:** fix and verify the generic local League ID -> Provider League ID boundary  
**Safety:** no League-specific mapping, migration, manual fixture insert, or manual Match repair was used.

## 1. Root Cause Reference

Phase 4.1 proved that the normal season-sync path passed local `league_id` directly to the Provider. For the real test League:

```text
local league_id=1274
provider_id=1
```

The old path requested `league=1274`, while the Provider required `league=1`.

## 2. Implementation Change

`FixtureSyncService.sync_full_season()` now:

1. checks the requested local `league_id` against the AllowedLeague set;
2. resolves the League Master using `LeagueRepository.get_by_id()`;
3. validates non-empty `provider` and `provider_id`;
4. fails closed without calling the Provider when identity is incomplete;
5. calls `FixtureProvider.get_fixtures(league=master.provider_id, season=season)`;
6. leaves fixture persistence on the canonical local `league_id` path.

The compatibility `MatchService` behavior was left unchanged after regression validation; the production API delegates through `FootballAPIService` to `FixtureSyncService`.

## 3. Frozen Architecture Compliance

The implementation preserves the frozen contract:

```text
local league_id
  -> League Master
  -> (provider, provider_id)
  -> FixtureProvider
  -> FixtureSync
  -> Match.league_id = local league_id
```

No name matching, `league_id == provider_id` assumption, League 389 condition, League 1274 condition, or second Provider client was added.

## 4. Provider Identity Resolution

Focused tests cover:

- local League ID `42` with Provider ID `7` sends `7` to the Provider;
- missing Provider ID does not call the Provider and returns explicit failure;
- existing unresolved identity behavior remains fail closed.

The production database identity used for real verification was:

```text
(api-football, provider_id=1) -> local league_id=1274
```

## 5. Local Identity Preservation

The Provider request used external ID `1`. Match persistence used local League ID `1274`.

Post-sync PostgreSQL evidence:

- `matches.league_id = 1274` for all 104 selected Matches;
- Provider fixture IDs remained in `matches.provider_fixture_id`;
- no Match used provider ID `1` as its local League foreign key.

## 6. Regression Tests

Focused tests added/updated:

- valid identity resolves local League to Provider ID;
- local ID unequal to Provider ID;
- missing Provider identity fails closed;
- Provider is not called for missing identity;
- existing League and fixture-sync identity regressions remain covered.

## 7. Real League Selected

The existing real PostgreSQL League created during Phase 4 was used. No new manual mapping was created in Phase 4.2.

| Field | Value |
|---|---|
| Provider | `api-football` |
| Provider League ID | `1` |
| Local League ID | `1274` |
| Name | `World Cup` |
| Country ID | `2105094199` (`World`) |
| Allowed | Yes |
| Season | `2026` |

A second suitable existing League with a different local/provider ID was not available in the current verified database population, so real runtime genericity was fully exercised for one unequal-ID League and covered generically by focused tests.

## 8. Actual Provider Request

The API input was:

```text
POST /api/matches/sync/season?league_id=1274&season=2026
```

The League Master mapping was:

```text
local league_id=1274
provider=api-football
provider_id=1
```

The updated service boundary sends:

```text
FixtureProvider.get_fixtures(league="1", season=2026)
```

Runtime-equivalent Provider evidence after the implementation returned:

```text
league=1, season=2026 -> 104 fixtures
league=1274, season=2026 -> 0 fixtures
```

The first value is the request used by the corrected service path. The Provider client does not emit query parameters in application logs, so the exact argument is proven by the loaded service call path and focused request-capture test, while the live HTTP result independently confirms the corrected result.

## 9. Provider Response

For the corrected real HTTP sync:

- response contained 104 fixtures;
- Provider League ID in the fixture payloads was `1`;
- fixture season was `2026`;
- no unresolved Provider errors were reported;
- pagination was completed by the existing Provider abstraction.

The same Provider data had previously been returned by direct `league=1, season=2026` verification.

The final focused implementation/regression command passed `100` tests with `11` existing deprecation warnings.


## 10. Runtime Logs
After the final provider-validation edit, Uvicorn was restarted from the workspace source and a repeat API call returned `updated=104`, `failed=0`, with the PostgreSQL selected Match count stable at `104 -> 104`.



The reloaded Uvicorn process started from the workspace virtual environment and loaded the updated code. Relevant logs for both HTTP calls included:

```text
RESOURCE_LOCK_ACQUIRE_ATTEMPT lock_identity=fover:sync:fixture_query:global
RESOURCE_LOCK_ACQUIRED lock_identity=fover:sync:fixture_query:global
sync_completed
RESOURCE_LOCK_RELEASED lock_identity=fover:sync:fixture_query:global
http_request_completed
POST /api/matches/sync/season?league_id=1274&season=2026 HTTP/1.1 200 OK
```

The first request completed with 104 inserts; the second completed with 104 updates. The Provider client does not currently log request parameter dictionaries, so no raw parameter line was available from Uvicorn stdout.

## 11. Database Before State

Immediately before the real HTTP verification:

| Check | Before |
|---|---:|
| League 1274 row | 1 |
| Provider ID | `1` |
| Country ID | `2105094199` |
| Allowed row | 1 |
| Matches for League 1274 | 0 |
| Matches for League 1274 / season 2026 | 0 |
| Identity duplicate groups | 0 |
| Orphan Matches | 0 |
| Orphan Team references | 0 |

## 12. Database After State

After two normal HTTP sync requests:

| Check | After |
|---|---:|
| League 1274 row | 1 |
| Provider ID | `1` unchanged |
| Country ID | `2105094199` unchanged |
| Allowed row | 1 |
| Matches for League 1274 | 104 |
| Matches for League 1274 / season 2026 | 104 |
| Identity duplicate groups | 0 |
| Orphan Matches | 0 |
| Orphan home Team references | 0 |
| Orphan away Team references | 0 |

## 13. Match Persistence

### First sync

```json
{
  "success": true,
  "inserted": 104,
  "updated": 0,
  "total": 104,
  "failed": 0
}
```

HTTP status: `200`.

### Second sync

```json
{
  "success": true,
  "inserted": 0,
  "updated": 104,
  "total": 104,
  "failed": 0
}
```

HTTP status: `200`.

PostgreSQL confirmed 104 Match rows tied to `league_id=1274`, with no duplicate provider fixture identities observed and no orphan League or Team references.

## 14. Repeat Sync Result

The repeat run demonstrated idempotency:

```text
first sync:  inserted=104, updated=0
second sync: inserted=0,   updated=104
```

The canonical League identity remained one row throughout:

```text
(api-football, '1') -> league_id=1274
```

No duplicate League identity or duplicate Match set was created.

## 15. Duplicate Checks

Post-verification checks returned:

- selected `(api-football, '1')` identity rows: `1`;
- all duplicate non-null provider identity groups: `0`;
- duplicate provider fixture identities for the selected League: `0`;
- League 1274 identity changed: no;
- League 1274 Country relationship changed: no.

## 16. Failure-Closed Verification

The missing-identity focused test used an allowed local League with `provider_id=None` and verified:

- Provider call count: `0`;
- sync result: `success=false`;
- explicit unresolved-identity message;
- no Match persistence path entered.

This prevents the service from sending a local ID to the Provider when the canonical external identity is unavailable.

## 17. Genericity Verification

The implementation is generic because it:

- queries the League Master by the caller-supplied local ID;
- reads the stored Provider identity dynamically;
- sends the stored `provider_id`, not a literal ID;
- preserves the local ID for Match persistence;
- rejects missing identity without Provider access.

The unequal-ID real runtime proof used one real League (`1274 -> 1`). A second unequal-ID real League was not available in the current database population, so additional genericity evidence comes from the focused tests using a different synthetic relationship (`42 -> 7`) without any League-specific constants.

## 18. Limitations

- The Provider client does not currently log request parameter dictionaries, so raw Uvicorn output does not contain a literal `league=1` line. The request was directly captured in the focused test and proven by the real HTTP result and database writes.
- Only one real unequal local/provider League was available for a complete runtime sync. The second relationship was verified through a generic focused test.
- The real runtime created no new League in Phase 4.2; it used the already controlled and allowed Phase 4 League 1274.
- Existing unrelated full-suite failures in Odds, Team, scheduler, and lineup worktree changes were not part of this implementation slice.

## 19. Final Classification

# PASS

The corrected generic flow is proven:

```text
local league_id=1274
  -> League Master provider_id=1
  -> Provider request league=1
  -> 104 real fixtures
  -> Fixture Sync
  -> 104 Match rows with league_id=1274
  -> repeat sync updates 104 without duplicates
```

The implementation also fails closed for missing Provider identity and does not alter local Match League identity.
