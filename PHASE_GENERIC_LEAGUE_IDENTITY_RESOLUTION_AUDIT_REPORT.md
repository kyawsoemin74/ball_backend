# Generic League Provider Identity Resolution Audit

**Audit date:** 2026-09-19  
**Scope:** generic Provider League -> canonical local League -> Allowed League -> Fixture Sync flow  
**Modification policy:** audit only. No source code, migrations, database rows, allow-list rows, or League 389 data were modified.

## 1. Current Implementation Flow

The real implementation is:

```text
Provider payload
  -> LeagueService / LeagueSyncService
  -> LeagueRepository.find_by_provider_identity(provider, provider_id)
  -> leagues.league_id
  -> AllowedLeagueRepository.get_allowed_ids()
  -> FixtureSyncService._process_sync_with_candidates()
  -> parse_fixture_to_match(league_id=local league_id)
  -> team identity resolution
  -> Match upsert with Match.league_id
```

Relevant implementation surfaces:

| Stage | File | Actual symbol/query |
|---|---|---|
| Provider league registration | [app/services/league_service.py](app/services/league_service.py) | `LeagueService.register_league()` |
| Provider league discovery sync | [app/services/league_sync_service.py](app/services/league_sync_service.py) | `LeagueSyncService.sync_all_leagues()`, `upsert_league()` |
| Provider identity lookup | [app/repositories/league_repository.py](app/repositories/league_repository.py) | `find_by_provider_identity()` |
| Country resolution | [app/services/country_sync_service.py](app/services/country_sync_service.py) | `sync_country()`, `sync_from_league_payload()` |
| League master | [app/models/league.py](app/models/league.py) | `League` / `leagues` |
| Allow-list | [app/services/allowed_league_service.py](app/services/allowed_league_service.py), [app/repositories/allowed_league_repository.py](app/repositories/allowed_league_repository.py) | `add_allowed_league()`, `get_allowed_ids()` |
| Fixture filtering and persistence | [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py) | `_process_sync_with_candidates()`, `process_fixture()`, `sync_full_season()` |
| Match persistence | [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py) | Match repository lookup/upsert path |
| HTTP entry point | [app/api/matches.py](app/api/matches.py) | season fixture sync route |

The system does not use league name matching in the fixture resolver. It does not use `local league_id == provider_id` as a lookup fallback. It resolves the provider fixture's `league.id` by exact provider identity.

## 2. Canonical Identity Definition

The intended and implemented provider identity key is:

```text
(provider, provider_id)
```

`LeagueRepository.find_by_provider_identity()` executes the equivalent of:

```sql
SELECT *
FROM leagues
WHERE provider = :provider
  AND provider_id = :provider_id_as_string;
```

It returns no row for a null input, returns the one matching League row, and raises `ValueError` if more than one row is returned. The `League` model also declares a unique constraint named `uq_leagues_provider_provider_id`.

The canonical local identity is `leagues.league_id`, the primary key. Fixture processing passes the resolved `master.league_id` into `parse_fixture_to_match()`, so the persisted `Match.league_id` is local, not provider-derived.

Downstream identity usage:

- `LeagueSeason.league_id` is a non-null foreign key to `leagues.league_id`.
- `Match.league_id` is a non-null foreign key to `leagues.league_id`.
- `Standings.league_id` is a non-null foreign key to `leagues.league_id`.
- `Team` has provider identity `(provider, provider_id)`, but `current_league_id` is nullable and has no foreign-key constraint to `leagues`.
- `Odds` attaches to a local Match through `fixture_id`; it has no direct League identity.
- `MatchH2H` attaches to a normalized team pair; it has no direct League identity.

Therefore the canonical contract is correctly represented for LeagueSeason, Match, and Standing, but provider identity completeness is not enforced by the League schema.

## 3. League Onboarding Flow

### Provider discovery sync

`LeagueSyncService.sync_all_leagues()` fetches provider leagues, obtains the current allow-list, and for each payload calls:

```python
find_by_provider_identity(db, "api-football", provider_id)
```

If no master is found, it logs `Skipping unresolved provider League` and discards the payload. It does **not** create a League Master or provider mapping for a newly discovered provider League.

For a resolved master, `LeagueSyncService._upsert_league()` resolves/synchronizes country and updates `master.country_id`, then synchronizes seasons and optionally teams. This is an update path, not a generic onboarding path.

### Manual registration

`LeagueService.register_league()` is a separate onboarding path. It:

1. accepts only `api-football`;
2. validates a positive integer provider ID;
3. checks the exact provider identity for an existing row;
4. fetches provider league details;
5. verifies the returned provider ID matches;
6. synchronizes country when country data is present;
7. creates a League row containing `provider`, `provider_id`, and `country_id` when available.

The row is created with `league_id = provider_id`. That is a registration convention, not a generic identity resolution requirement. It also checks only the provider identity before creation; country resolution can return no country and the row can still be created with `country_id = NULL`.

### Allow-list

`AllowedLeagueService.add_allowed_league()` verifies only that a local `League` row exists, then inserts `allowed_leagues.league_id`. It does not require non-null provider identity, valid country relationship, or a ready/complete League Master.

### Onboarding conclusion

A newly discovered provider League is not automatically transformed into a canonical local League. It must already be registered by another path. A local League can also be allowed while its provider identity and country relationship are incomplete. The required guarantee is therefore absent.

## 4. Fixture Resolution Flow

For each provider fixture, `_process_sync_with_candidates()`:

1. reads `fixture.league.id`;
2. rejects the fixture if that ID is missing;
3. performs exact lookup `(provider='api-football', provider_id=fixture.league.id)`;
4. rejects the fixture if no League Master is found;
5. checks the resolved local `master.league_id` against `allowed_leagues`;
6. calls `parse_fixture_to_match(fixture, league_id=master.league_id)`;
7. resolves provider teams;
8. creates or updates the Match using the local `league_id`.

Branch behavior:

| Condition | Actual behavior |
|---|---|
| Valid provider and provider ID, one League row | Resolves to that local `league_id`; continues only if allowed |
| Missing `provider_id` in payload | Skips fixture and counts it unresolved/failed |
| Missing identity in database | Skips fixture; no League Master is created |
| Wrong provider | Exact lookup returns no row; fixture is skipped |
| Same provider ID under another provider | Exact provider predicate does not resolve it |
| Duplicate identity rows | Repository raises `ValueError`; request path can fail rather than choose ambiguously |
| Provider ID null in League row | Cannot match; row is invisible to fixture resolution |
| Local League not allowed | Fixture is filtered out without persistence |
| Allowed local League with missing provider identity | Still fails provider lookup; allow-list does not repair identity |
| Empty filtered set with unresolved fixtures | Current code returns `success=False`, `failed=<unresolved count>` from `_process_sync_with_candidates()` |
| Empty filtered set without unresolved fixtures | Returns a successful no-op with zero writes |

`sync_full_season()` first checks the requested local `league` against the allow-list, then fetches provider fixtures using that request parameter. That initial check does not replace the per-fixture provider identity lookup.

## 5. League 389 Evidence

League 389 was used only as an evidence case. No special code or database correction was applied.

Target: local `league_id=389`, season `2026`.

Read-only database state:

| Field | Value |
|---|---|
| Local League row | Present |
| `provider` | `api-football` |
| `provider_id` | `NULL` |
| `name` | `Premier League` |
| `country_id` | `NULL` |
| Allowed row | Present |
| Exact `(api-football, '389')` rows | 0 |
| LeagueSeason rows for 2026 | 0 |
| Match rows for 2026 | 0 |

Existing runtime evidence for `POST /api/matches/sync/season?league_id=389&season=2026`:

- Provider returned 240 fixtures.
- Provider fixtures carried League ID 389 and season 2026.
- HTTP result was `200`.
- Sync result was `success=false`, `inserted=0`, `updated=0`, `total=0`, `failed=240`.
- The service logged unresolved provider League warnings and aborted before team resolution or Match persistence.
- Database before/after evidence remained unchanged: zero LeagueSeason rows and zero Match rows for the target.

The first failing stage was exact provider identity resolution. The local row's primary key and allow-list membership did not help because the resolver intentionally does not fall back from `league_id` to provider identity.

## 6. Known-Working League Comparison

The real working evidence is Provider League 1236 / Como Cup, season 2026.

| Field | Working 1236 | Unresolved 389 |
|---|---:|---:|
| Provider | `api-football` | `api-football` |
| Provider ID | `1236` | `NULL` in League row; payload sends `389` |
| Local `league_id` | `1236` | `389` |
| Country ID | `2105094199` (World) | `NULL` |
| Allowed | Yes | Yes |
| Exact provider lookup | One row | Zero rows |
| League Master | Complete identity row | Identity-incomplete row |
| Provider fixture payload | Resolves to 1236 | Carries 389 but cannot resolve |
| Match sync | 9 inserted, then 9 updated on repeat | 0 inserted, 0 updated, 240 failed |
| Duplicate League identity | None | None; absence is the problem |

Prior runtime evidence for 1236 proves that the generic exact lookup and local League ID propagation work when the identity row is registered. It does not prove that discovery automatically registers arbitrary new provider leagues.

## 7. Database Integrity Results

Read-only live inventory:

- `leagues`: 1,259 rows.
- `countries`: 59 rows.
- `allowed_leagues`: 11 rows.
- `leagues.provider IS NULL`: 0 rows in the current database.
- `leagues.provider_id IS NULL`: 1,227 rows.
- `leagues.country_id IS NULL`: 1,255 rows.
- Duplicate non-null `(provider, provider_id)` identities: 0 groups.
- Allowed rows whose League has null provider identity: 2.
- Orphaned AllowedLeague -> League references: 0.
- Orphaned LeagueSeason -> League references: 0.
- Orphaned Match -> League references: 0.
- Orphaned Standing -> League references: 0.
- Orphaned non-null League -> Country references: 0.

The actual PostgreSQL schema confirms `leagues.provider` is non-null, but `leagues.provider_id` and `leagues.country_id` are nullable. Existing foreign-key references are clean; the major integrity failure is incomplete identity data, not broken foreign-key pointers.

The current database also contains valid mapped League rows with no country, so country integrity is structurally possible but not guaranteed for every League. `Team.current_league_id` is not a foreign key, so its relationship to the canonical League is not database-enforced.

## 8. Runtime Evidence

The reviewed runtime evidence includes two materially different outcomes:

### Unresolved identity

League 389 returned HTTP 200 but the sync result explicitly reported 240 failures and zero writes. The request was not a successful synchronization. Raw Uvicorn stdout was not independently available in the reviewed runtime session, so log correlation is based on the captured audit evidence and the active source branch.

### Working identity

League 1236 returned HTTP 200 with 9 inserted matches. A repeated request returned 9 updates and zero inserts. Database evidence showed one canonical League identity row and nine persisted matches, demonstrating that the exact resolver and downstream local League ID propagation work for a registered identity.

HTTP 200 alone is not a success criterion. The relevant fields are provider identity, resolved local League ID, inserted, updated, failed, and database before/after state.

## 9. Generic Failure Matrix

| Condition | Expected behavior | Actual implementation | Result |
|---|---|---|---|
| Valid `(provider, provider_id)` | Resolve one local League | Exact lookup; ambiguity raises | Satisfied when data exists |
| New valid Provider League | Create/register League Master | Discovery sync skips if absent; manual registration can create | **Not satisfied generically** |
| Missing `provider_id` | Fail closed or onboarding path | Lookup returns none; fixture/league payload is skipped | Partially satisfied |
| Wrong provider | Do not resolve | Provider is part of predicate | Satisfied |
| Unknown provider ID | Fail closed or onboarding path | No row; skipped | Satisfied fail-closed, no onboarding |
| Duplicate identity | Reject/prevent ambiguity | DB unique constraint declared; repository raises if duplicates are visible | Mostly satisfied, legacy/schema enforcement should be monitored |
| Country unresolved | Do not create invalid League | Registration can create with null country; sync can skip country update | **Not satisfied** |
| League not Allowed/Ready | Do not process fixtures | Allow-list gates processing, but does not verify identity readiness | Partially satisfied |
| Valid identity + allowed League | Fixture sync may continue | Resolves local ID and persists matches | Satisfied; proven by 1236 |
| Provider identity null on allowed League | Do not process | Allowed row remains valid to allow-list service; fixture lookup fails later | Fail-closed processing, weak onboarding gate |
| Repeated discovery | No duplicate League | Existing exact identity is reused; absent identity is skipped | Existing rows safe; new discovery not onboarded |

## 10. Root Cause

**ROOT CAUSE**  
League Master creation/registration is separated from provider discovery and allow-listing. The generic discovery path requires an existing `(provider, provider_id)` mapping instead of creating or routing a new Provider League through a canonical onboarding transaction. The database permits `provider_id` and `country_id` to remain null, and the allow-list service validates only local row existence.

**AFFECTED COMPONENTS**  
`LeagueSyncService`, `LeagueService.register_league()`, `AllowedLeagueService`, `League` schema, and `FixtureSyncService`.

**WHY THE GENERIC FLOW FAILS**  
A provider payload can be discovered while no canonical provider identity row exists. Fixture Sync then correctly applies its exact lookup, finds zero rows, and rejects every fixture. An allow-list row does not establish provider identity. League 389 is a representative instance of this generic state, not a League-specific defect.

**REQUIRED ARCHITECTURAL FIX**  
Introduce one provider-neutral onboarding/registration path that atomically validates provider identity, resolves a valid Country, creates or reuses exactly one League Master, retains the generated local `league_id`, and only then permits Allowed/Ready status. Discovery must either invoke that path or explicitly emit an onboarding state instead of silently treating a newly discovered League as an existing master. Fixture Sync should continue to resolve only by exact provider identity and persist only the canonical local `league_id`.

No fix was implemented in this audit.

## 11. Architecture Check

The active generic fixture resolver does **not** depend on:

- League name matching;
- local `league_id == provider_id` fallback;
- hard-coded League IDs;
- League 389-specific mappings;
- fixture-specific exceptions; or
- manual database corrections during fixture processing.

The manual registration implementation does choose `league_id = provider_id`, but that is a creation convention and creates a risk that the system's nominally independent local identity becomes coupled to one provider. The fixture resolver itself does not rely on that equality.

The intended generic rule is therefore present in the resolution read path:

```text
Provider
  -> (provider, provider_id)
  -> League Master
  -> local league_id
  -> Fixture Sync
```

It is not guaranteed by the onboarding and readiness paths.

## 12. Final Classification

# LEAGUE_IDENTITY_FAILURE

The classification is based on actual code, live database evidence, and runtime evidence. The exact resolver works for a registered Provider League, but the current system cannot guarantee that any newly discovered Provider League will be registered with one valid canonical local identity before it becomes eligible for synchronization. The real 389 execution demonstrates the resulting generic failure: valid provider fixtures, an allowed local row, no exact provider identity, and zero persisted fixtures.

## 13. Required Fix Direction

A separate implementation phase should:

1. define a single canonical League onboarding contract for every provider;
2. require verified `(provider, provider_id)` and successful Country resolution before Ready/Allowed state;
3. create or reuse one local League Master and return its generated `league_id`;
4. enforce identity uniqueness and prevent ambiguous/null-ready League records;
5. make discovery use that onboarding path or produce an explicit pending-onboarding result;
6. retain Fixture Sync's exact provider identity lookup and local `league_id` persistence;
7. add tests for null identity, wrong provider, unknown ID, duplicate identity, unresolved country, allowed-but-not-ready, first discovery, repeat discovery, and working fixture persistence.

No League 389 repair, mapping, allow-list change, migration, or source change was performed.
