# PHASE 4.5 - Team Resolution Failure Audit and Targeted Fix Report

Verification date: 2026-09-23
Scope: audit of the Step 4 temporary Team-resolution failure. No production code or business data was modified.

## Final classification

**B. PARTIALLY VERIFIED - root cause identified but live verification incomplete**

The available evidence identifies a temporary test setup defect. The required Docker re-verification could not be completed because Docker Desktop's Linux engine was unavailable. Therefore this report does not classify Team Resolution as ready for Step 4 re-verification yet.

## Confirmed root cause

The Step 4 test used synthetic provider Team IDs:

- Home: `9902001`
- Away: `9902002`

It patched only `FixtureProvider.get_fixtures()`. It did not:

- pre-create Team Master rows for those provider IDs; or
- patch `TeamProvider.get_team_details()` for those IDs.

Consequently, FixtureSync correctly treated both Teams as unresolved and invoked the existing Team Master synchronization path. That path queried the real provider for `/teams?id=9902001` and `/teams?id=9902002`. The test had no valid provider Team responses for those invented IDs.

This is classified as **A. Test-data/setup defect**, with runtime completion still pending.

## Exact resolution path

The live FixtureSync path is:

```text
FixtureSyncService._process_sync_with_candidates()
  -> TeamService.resolve_provider_teams()
  -> TeamSyncService.resolve_provider_teams()
  -> TeamRepository.find_by_provider_identity()
  -> unresolved provider IDs
  -> TeamSyncService.ensure_teams_exist()
  -> TeamProvider.get_team_details(provider_id)
  -> TeamSyncService.upsert_team()
  -> TeamRepository.upsert_by_provider_identity()
  -> TeamRepository.find_by_provider_identity()
```

Relevant source behavior:

1. FixtureSync extracts `match.home_team_id` and `match.away_team_id` from the fixture payload.
2. Existing Team identity is checked by `(provider='api-football', provider_id)`.
3. Missing identities are passed to `ensure_teams_exist()`.
4. Missing Team payloads are fetched through `TeamProvider.get_team_details()`.
5. `upsert_team()` requires a provider response list containing exactly one dictionary.

## Exact failing function and condition

Failing function:

```text
TeamSyncService.upsert_team()
```

Failing condition:

```python
if len(response) != 1 or not isinstance(response[0], dict):
    raise ValueError("Team provider response must contain exactly one Team")
```

The error is therefore not raised by League Identity Recovery, Match upsert, or FixtureSync's provider-League filtering.

## Inputs used by the failed test

```text
league provider ID: 9901001
home provider Team ID: 9902001
away provider Team ID: 9902002
fixture provider ID: 1234567
season: 2026
```

The temporary fixture payload contained the required home and away IDs and was returned by a patched fixture provider. The temporary League was allowed and resolved by provider identity.

## Provider request and response evidence

Provider request implied by the code:

```text
GET /teams?id=9902001
GET /teams?id=9902002
```

The earlier run captured only the final application error, not the raw provider response. The Docker engine became unavailable before the raw response could be queried:

```text
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
```

Therefore the exact raw response shape is **UNKNOWN**. It must not be guessed. The failure is nevertheless explained by the setup: synthetic IDs were used without corresponding Team Master rows or mocked provider detail responses.

## Updated runtime prerequisites

Subsequent terminal evidence showed that the Docker environment was healthy during a later verification window:

- API, Worker, PostgreSQL, and Redis were healthy;
- `libpq.so.5` was present in the Worker image;
- the live recovery migration was `20260922_align_recovery_indexes`;
- `public.league_identity_recovery` was present with 13 columns;
- Worker restart persistence resolved the staged retry row after restart.

These facts remove the earlier runtime-library and migration blockers for a future attempt. They do not prove Team Resolution because the corrected Team-detail setup has not yet executed in the live Worker. Unrelated Alembic index drift remains outside this audit.

Expected response for each missing Team:

```json
{
  "response": [
    {
      "team": {
        "id": 9902001,
        "name": "Runtime Home Team",
        "country": "Runtime Country",
        "logo": null
      },
      "venue": {}
    }
  ]
}
```

Actual raw response: **NOT CAPTURED**. The observed application-level result was that `upsert_team()` received a response that did not satisfy the exactly-one-Team contract.

## Runtime evidence

The two repeated FixtureSync calls produced:

```text
Fixture ID 1234567 failed during sync: Team provider response must contain exactly one Team
Fixture ID 1234567 failed during sync: Team provider response must contain exactly one Team
```

Both calls returned `failed=1`, `inserted=0`, and `updated=0`. No Match was persisted. Temporary rows were subsequently cleaned up; the cleanup query returned zero temporary Leagues, Matches, and Teams.

## Architecture assessment

The existing responsibility boundary is intact:

```text
LeagueSync / TeamSyncService -> Team Master
FixtureSync -> resolve existing Team identity
```

FixtureSync did not create arbitrary Team identities. It called `TeamService.resolve_provider_teams()`, then delegated missing Team identities to `TeamSyncService.ensure_teams_exist()`. The Team provider identity uniqueness and Team Master ownership rules were not weakened or bypassed.

No evidence currently supports a production defect in:

- provider-ID lookup;
- Team repository identity lookup;
- Team name or country filtering;
- league context handling;
- duplicate provider result handling;
- FixtureSync integration.

The provider's raw response for the invented IDs remains unobserved, so provider-response details are explicitly marked UNKNOWN.

## Targeted fix decision

No production application fix is approved.

The necessary correction is isolated test setup only:

1. Use temporary provider Team IDs with mocked `TeamProvider.get_team_details()` responses, or
2. pre-create temporary Team Master rows with those provider identities before running FixtureSync.

The preferred verification setup is to patch the existing provider method with one valid response per Team, preserving the real `TeamSyncService`, `TeamRepository`, persistence, re-resolution, and FixtureSync paths.

Files changed for this audit:

- `PHASE_4.5_TEAM_RESOLUTION_FAILURE_AUDIT_AND_FIX_REPORT.md`

Production files changed: none.

Frozen modules not changed:

- League Identity Recovery service, repository, model, scheduler, and resource lock;
- FixtureSync implementation;
- TeamService;
- TeamSyncService;
- TeamRepository;
- database schema and indexes.

## Required controlled verification after Docker recovery

Run inside the real Worker container with temporary data only:

1. Create a temporary allowed League with provider identity `9901001`.
2. Patch fixture retrieval to return the synthetic fixture.
3. Patch `TeamProvider.get_team_details()` to return exactly one valid response for each temporary Team ID.
4. Run Team resolution independently and assert both IDs resolve to one local Team each.
5. Commit and re-query both provider identities.
6. Re-run resolution and assert the same local Team IDs are returned.
7. Assert one Team row per provider identity.
8. Run `sync_full_season()` once and assert one Match is persisted.
9. Run the identical operation again and assert `updated=1`, `inserted=0`, one Match row, and unchanged Team-row counts.
10. Clean up Match, Team, Allowed League, and League rows and verify zero temporary rows remain.

Only after those checks pass may the statement be made:

```text
Team Resolution is READY for Step 4 re-verification.
```

This report does not claim that statement yet, and it does not claim Step 4 or Phase 4.5 verified.
