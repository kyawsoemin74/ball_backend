# PHASE 4 - REAL DB / REAL PROVIDER VERIFICATION REPORT

**Verification date:** 2026-09-19  
**Scope:** real Provider League onboarding against the configured API-Football integration and real PostgreSQL database  
**Source-code safety:** no Python source, model, migration, route, provider, service, scheduler, or database repair change was made during Phase 4.

## 1. Objective

Verify the Phase 3 generic identity flow with real external Provider data and the real PostgreSQL database:

```text
Provider
  -> (provider, provider_id)
  -> Country Master
  -> League Master
  -> local league_id
  -> Allowed/Ready
  -> Fixture Sync
  -> Match persistence
```

League 389 was not selected for this verification.

## 2. Frozen Architecture Reference

The verification follows [PHASE_2_LEAGUE_IDENTITY_ARCHITECTURE_DESIGN_FREEZE.md](PHASE_2_LEAGUE_IDENTITY_ARCHITECTURE_DESIGN_FREEZE.md) and the implementation documented in [PHASE_3_GENERIC_LEAGUE_IDENTITY_IMPLEMENTATION_REPORT.md](PHASE_3_GENERIC_LEAGUE_IDENTITY_IMPLEMENTATION_REPORT.md).

The tested contract was exact Provider identity `(provider, provider_id)` mapped to an independent local `league_id`. No name matching, provider-ID-as-local-ID assumption, or League-specific mapping was used.

## 3. Selected Real Provider League

The real provider catalog request returned 1,245 league records. The selected record was chosen from that response because it was not already registered locally, contained Country metadata, and had a current 2026 season.

| Field | Real value |
|---|---|
| Provider | `api-football` |
| Provider League ID | `1` |
| Provider League name | `World Cup` |
| Provider League type | `Cup` |
| Provider country | `World` |
| Provider country code | `NULL` |
| Selected season | `2026` |
| Current season | `true` |

The detailed provider request `/leagues?id=1` returned one response, no provider errors, and the same identity and country values.

## 4. Provider Identity Evidence

The real Provider response contained:

```json
{
  "league": {"id": 1, "name": "World Cup", "type": "Cup"},
  "country": {"name": "World", "code": null},
  "seasons": [{"year": 2026, "current": true}]
}
```

The existing `LeagueService.register_league()` consumed this real response. It validated `provider='api-football'` and `provider_id=1`, then routed the payload through the generic onboarding service.

## 5. Country Resolution Evidence

Before onboarding, PostgreSQL already contained exactly one Country Master row for `World`:

| Field | Value |
|---|---:|
| `country_id` | `2105094199` |
| `name` | `World` |
| `code` | `NULL` |
| Duplicate Country created | No |

After onboarding, the new League pointed to `country_id=2105094199`, and the joined Country name was `World`. The Country relationship was non-null and valid.

## 6. League Master Evidence

### Before onboarding

Read-only baseline for `(api-football, '1')`:

- matching League rows: `0`;
- allowed rows: `0`;
- Match rows: `0`;
- LeagueSeason rows: `0`;
- duplicate identity groups: `0`.

The broader database baseline was:

- total League rows: `1,259`;
- `provider_id IS NULL`: `1,227`;
- `country_id IS NULL`: `1,259`;
- `provider IS NULL`: `0`.

Those legacy null rows were not modified by this verification.

### After onboarding

The existing registration service committed this new League Master:

| Field | Value |
|---|---:|
| Local `league_id` | `1274` |
| `provider` | `api-football` |
| `provider_id` | `1` stored as text `'1'` |
| `name` | `World Cup` |
| `country_id` | `2105094199` |
| Country name | `World` |
| Allowed | Initially no, then yes |
| Match count | `0` before fixture attempt |

The exact identity query `(provider='api-football', provider_id='1')` returned exactly one row.

## 7. Local ID Independence Evidence

The generated local identity was `league_id=1274`, while the Provider identity was `provider_id=1`.

```text
(provider='api-football', provider_id='1') -> local league_id=1274
```

This proves the tested path did not require or assume `local league_id == provider_id`.

## 8. Allowed/Ready Evidence

The existing `AllowedLeagueService.add_allowed_league()` was invoked with the generated local ID `1274`.

Result:

- Allowed row created: yes;
- Allowed row value: `league_id=1274`;
- provider identity valid: yes;
- Country relationship valid: yes;
- League Master valid: yes;
- incomplete identity rejected by the readiness gate: not exercised for this selected complete record, but enforced by the implementation and covered by focused tests.

A repeat registration call returned:

```text
created = false
local league_id = 1274
identity row count before = 1
identity row count after = 1
```

This is real PostgreSQL idempotency evidence.

## 9. Fixture Sync Request

The normal HTTP endpoint was invoked with the canonical local ID, not the Provider ID:

```text
POST http://127.0.0.1:8000/api/matches/sync/season?league_id=1274&season=2026
```

Authentication used the existing active admin database user and the normal JWT authorization path.

No source code or manual SQL mapping was used.

## 10. HTTP Response

### Attempt 1

```text
HTTP status: 200
{
  "success": false,
  "message": "No fixtures found",
  "final_lineup_candidates": []
}
```

### Attempt 2

The same normal endpoint was retried once:

```text
HTTP status: 200
{
  "success": false,
  "message": "No fixtures found",
  "final_lineup_candidates": []
}
```

There were no `inserted`, `updated`, `failed`, or `total` fields in this no-fixtures response.

This is not a synchronization success. HTTP 200 only indicates that the API request completed without an HTTP transport exception.

## 11. Runtime Logs

The HTTP response is the captured application-level result. The running Uvicorn terminal did not expose an independently captured line-by-line stdout stream for these requests.

The important runtime discrepancy is:

- direct real Provider call through `football_service.fixture_provider.get_fixtures(league=1, season=2026)` returned a real fixture response containing 2026 World Cup fixtures;
- the running HTTP route returned `No fixtures found` on both attempts;
- therefore the HTTP process did not expose the same Provider fixture result to the route during this verification.

This prevents claiming that the route reached League resolution, Team resolution, or Match persistence.

## 12. Database BEFORE State

For `(api-football, provider_id='1')` before onboarding:

| Check | Before |
|---|---:|
| League identity rows | 0 |
| Allowed rows | 0 |
| Match rows | 0 |
| LeagueSeason rows | 0 |
| Country `World` rows | 1 |
| Duplicate selected identity groups | 0 |

No data was changed during the baseline audit.

## 13. Database AFTER State

After registration, allow-listing, two HTTP sync attempts, and read-only verification:

| Check | After |
|---|---:|
| Canonical League rows for `api-football/1` | 1 |
| Local League ID | 1274 |
| Allowed rows for local 1274 | 1 |
| League country ID | 2105094199 |
| Country `World` rows | 1 |
| Matches for local 1274 | 0 |
| Matches for local 1274 / season 2026 | 0 |
| LeagueSeason rows for local 1274 | 0 |
| Duplicate selected identity groups | 0 |
| All duplicate non-null identity groups | 0 |

The League identity and Country relationship remained unchanged after both HTTP requests.

## 14. Match Persistence Evidence

No Match rows were inserted or updated by the HTTP route because both route calls returned `No fixtures found` before fixture processing.

Therefore the following cannot be claimed from this run:

- Provider fixture identity resolved through the HTTP route;
- `Match.league_id=1274` was persisted;
- Team references were created or resolved for this selected League;
- Match idempotency was exercised.

Direct Provider evidence proves that the external service can return fixtures, but it does not prove that the running HTTP process persisted them.

## 15. Identity Integrity Checks

Post-verification checks:

- selected `(provider, provider_id)` identity rows: exactly 1;
- all duplicate non-null provider identity groups: 0;
- orphan Match -> League rows: 0;
- orphan Match -> home Team rows: 0;
- orphan Match -> away Team rows: 0;
- orphan LeagueSeason -> League rows: 0;
- orphan Standing -> League rows: 0;
- orphan non-null League -> Country rows: 0;
- selected League provider identity unchanged: yes;
- selected League country relationship unchanged and valid: yes.

The database was not corrupted by the controlled onboarding or failed/no-fixture sync attempts.

## 16. Failure Behavior

The observed failure was a runtime fixture acquisition discrepancy, not an identity-resolution failure:

```text
Provider identity: PASS
Country resolution: PASS
League Master creation: PASS
Local ID independence: PASS
Allowed/Ready: PASS
HTTP fixture acquisition: FAIL / inconsistent with direct Provider call
Fixture identity resolution through route: NOT REACHED / NOT PROVEN
Match database write: NOT REACHED
```

The route did not report false synchronization success: it returned `success=false`. No invalid Match rows were created.

## 17. Duplicate Checks

The selected identity was checked before and after onboarding:

```text
(api-football, '1') -> exactly one League row
```

A repeat registration call returned the same local ID `1274` with `created=false`. No duplicate League was created.

The broader database identity check found zero duplicate non-null `(provider, provider_id)` groups after verification.

## 18. Orphan Checks

All completed orphan checks returned zero:

- orphan Matches: 0;
- orphan Match home Teams: 0;
- orphan Match away Teams: 0;
- orphan LeagueSeasons: 0;
- orphan Standings: 0;
- orphan League Country references: 0.

No Match rows existed for the selected League, so Match relationship validity was vacuously clean rather than persistence-proven.

## 19. Source-Code Modification Check

No source code was changed during Phase 4. No migration was created or applied. No manual League mapping was inserted. No League 389 condition was added.

The repository already contained unrelated pre-existing worktree changes from earlier work; those were not reverted or modified. The only new artifact from this phase is this report.

## 20. Limitations

1. The core real-provider-to-PostgreSQL onboarding flow was proven, including Country reuse, independent local ID generation, exact identity uniqueness, allow-list readiness, and repeat idempotency.
2. The required real fixture-to-Match persistence path was not proven because the running HTTP route returned no fixtures twice, despite a direct real Provider call returning 2026 fixtures for the same Provider League and season.
3. Raw Uvicorn stdout was not independently captured for the two HTTP requests.
4. No production synchronization or manual repair was attempted.
5. Existing database rows with null `provider_id` and `country_id` remain legacy incomplete data and were not modified.
6. The selected League was created and allowed as a controlled verification record. Removing it would be a database modification, so no cleanup was performed under the strict no-manual-data-change rule.

## 21. Final Classification

# PASS WITH LIMITATIONS

The real Provider -> Country Master -> League Master -> independent local `league_id` -> Allowed/Ready flow passed against PostgreSQL for Provider League `api-football/1`.

The full classification cannot be `PASS` because the required HTTP Fixture Sync -> Match persistence stage was not verified. The running API returned `success=false` and `No fixtures found` twice, while a direct real Provider call returned fixtures for the same league and season. No invalid downstream data was created, and the application correctly did not claim synchronization success.

```text
Provider Identity: PASS
Country Resolution: PASS
League Master: PASS
Local ID Independence: PASS
Allowed/Ready: PASS
Fixture Integration: NOT PROVEN / RUNTIME DISCREPANCY
Match Persistence: NOT PROVEN
Identity Integrity: PASS
Runtime Verification: PARTIAL
Final Classification: PASS WITH LIMITATIONS
```
