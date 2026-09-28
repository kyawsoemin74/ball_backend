# PHASE 3 - GENERIC LEAGUE IDENTITY IMPLEMENTATION REPORT

**Date:** 2026-09-19  
**Scope:** generic Provider League identity onboarding and readiness integration  
**Database safety:** no production or runtime database data was modified; no synchronization endpoint was triggered.

## 1. Frozen Design Reference

Implementation follows [PHASE_2_LEAGUE_IDENTITY_ARCHITECTURE_DESIGN_FREEZE.md](PHASE_2_LEAGUE_IDENTITY_ARCHITECTURE_DESIGN_FREEZE.md).

The preserved rules are:

- Provider identity is exact `(provider, provider_id)`.
- Internal identity is an independently generated local `league_id`.
- Country is resolved through Country Master before a League is ready.
- AllowedLeague is local eligibility, not identity resolution.
- FixtureSyncService remains resolution-only and consumes the canonical local League ID.
- Missing or ambiguous identity fails closed.
- Legacy incomplete rows remain outside normal onboarding and require repair.
- No League-specific mapping or League 389 special case is allowed.

## 2. Current Implementation Gap

Before this change:

- discovery sync skipped a provider League when its exact master identity was missing;
- manual registration assigned `league_id = provider_id`, coupling local and provider identity;
- Country resolution could return no `country_id` while still creating a League;
- AllowedLeague validated only that a local League row existed;
- unresolved fixture identity was already rejected by Fixture Sync, but onboarding did not guarantee that a new provider League reached a canonical master first.

The implementation closes those gaps without changing downstream Match, Standing, Odds, H2H, Team, Event, or Scheduler ownership.

## 3. Files Changed

- [app/repositories/league_repository.py](../app/repositories/league_repository.py): `create_registered()` now ignores caller-supplied `league_id`, allowing the database sequence to generate local identity.
- [app/services/league_service.py](../app/services/league_service.py): provider identity validation and generic Country-first `onboard_provider_league()` path; registration now uses it.
- [app/services/league_sync_service.py](../app/services/league_sync_service.py): discovery now onboards valid provider Leagues before allow-list filtering; incomplete Country/legacy repository states are skipped explicitly.
- [app/services/allowed_league_service.py](../app/services/allowed_league_service.py): allow-list insertion requires provider identity and Country relationship readiness.
- [tests/test_admin_league_registration.py](../tests/test_admin_league_registration.py): independent local ID and Country test double.
- [tests/test_league_sync.py](../tests/test_league_sync.py): generic onboarding, idempotency, missing identity/Country, and discovery tests.
- [tests/test_allowed_leagues.py](../tests/test_allowed_leagues.py): readiness and fail-closed fixture expectations.
- [tests/test_league_season_foundation.py](../tests/test_league_season_foundation.py): ready League fixture includes Country relationship.

## 4. Generic Identity Flow

The implemented flow is:

```text
Provider League payload
  -> validate provider and provider_id
  -> exact lookup (provider, provider_id)
  -> resolve/create Country Master
  -> create or reuse League Master
  -> database-generated local league_id
  -> validate readiness before AllowedLeague insertion
  -> FixtureSync exact lookup
  -> Match.league_id = resolved local league_id
```

No name matching, local-ID fallback, or provider-ID-as-local-ID assumption is used by the generic resolver.

## 5. League Onboarding Flow

`LeagueService.onboard_provider_league()` and the equivalent discovery path in `LeagueSyncService`:

1. validate the known provider and positive provider ID;
2. search by exact `(provider, provider_id)`;
3. return the existing local League if found;
4. resolve Country through `CountrySyncService`;
5. fail closed when Country cannot produce a canonical `country_id`;
6. create a new League without supplying `league_id` so PostgreSQL generates it;
7. persist provider, provider ID, name, and country relationship;
8. return the canonical local League row.

Repeated onboarding returns the same existing row and does not create a duplicate.

Existing incomplete legacy rows are not silently repaired as part of normal discovery. If their Country remains unresolved, they are logged as incomplete and excluded from the ready path.

## 6. Country Resolution Flow

Country data is passed to `CountrySyncService.sync_from_league_payload()`, which normalizes and upserts through `CountryRepository`. The resulting canonical `country_id` is stored on `League.country_id`.

New onboarding fails closed when Country is absent, invalid, or cannot resolve to a Country Master. Existing rows with missing Country remain incomplete and are not eligible for normal discovery synchronization.

## 7. Allowed/Ready Gate

`AllowedLeagueService.add_allowed_league()` now requires:

- a non-empty provider;
- a non-empty provider ID;
- a non-null Country relationship.

It still stores only local `league_id`, preserving AllowedLeague ownership as local eligibility. It no longer allows an identity-incomplete League to become eligible.

## 8. Fixture Sync Integration

Fixture Sync remains resolution-only. It reads the provider fixture League ID and calls the exact repository lookup with `provider='api-football'` and that provider ID. On success it passes `master.league_id` to fixture parsing and Match persistence.

On unresolved identity, the service returns an explicit failure result with failed fixture counts and performs no Match write. No fixture-specific onboarding or arbitrary League creation was added.

## 9. Transaction Behavior

No production synchronization was run. Code-level behavior is:

- new League onboarding uses the caller's existing transaction/session;
- database-generated local identity is flushed by `create_registered()`;
- the admin registration route commits only after successful onboarding;
- Country or provider identity failure occurs before League creation;
- unresolved Fixture identity remains a failed sync result rather than a successful zero-write result;
- existing transaction ownership and downstream savepoints were not changed.

HTTP success is not treated as proof of synchronization success.

## 10. Idempotency Behavior

The canonical identity lookup occurs before creation. Therefore:

```text
first (provider, provider_id) onboarding -> one League Master
repeat onboarding -> same local league_id
```

Local `league_id` is generated by the database sequence and is independent of provider ID. The existing database unique constraint on `(provider, provider_id)` remains the duplicate-prevention invariant.

## 11. Error/Failure Matrix

| Condition | Implemented behavior |
|---|---|
| Valid Provider identity | Continue to exact lookup |
| Missing provider | Reject unsupported provider |
| Missing provider_id | Reject before creation or fixture processing |
| Invalid provider identity | Fail closed |
| Existing League identity | Return existing local `league_id` |
| New valid League | Resolve Country, create League Master, return generated local ID |
| Country exists | Reuse/upsert Country Master and store `country_id` |
| Country missing/unresolved | Fail closed for onboarding; discovery marks incomplete and excludes it |
| Duplicate Provider identity | Unique constraint/repository ambiguity check prevents selection |
| League not Allowed/Ready | Exclude from Fixture Sync |
| Fixture identity unresolved | Explicit sync failure; no Match write |
| Valid identity and ready League | Continue to Match persistence |

## 12. Tests Added

Focused tests cover:

- existing provider identity resolves to the same local League;
- new provider League resolves Country and creates a League with a distinct local ID;
- repeat onboarding is idempotent;
- missing provider ID fails closed;
- unresolved Country prevents creation;
- discovery onboards before allow-list filtering;
- incomplete League cannot be added to AllowedLeague;
- unresolved fixture identity fails with no fixture writes.

## 13. Tests Executed

Focused identity slice:

```text
50 passed, 11 warnings
```

Broader identity-adjacent regression slice:

```text
60 passed, 5 warnings
```

The regression slice included Country repository, League Season, League priority, Team identity, Standing, Match upsert, H2H, and scheduler hardening tests.

The warnings are existing framework/deprecation warnings; no test failure remained in either executed slice.

The full repository suite was also executed: 661 passed and 34 failed. Those failures are outside the changed League identity surfaces and are concentrated in pre-existing Odds, Team, scheduler, and lineup worktree changes. The relevant League identity suites remained green.

## 14. Regression Results

PASS for the executed focused and identity-adjacent suites. Existing Fixture Sync behavior remains fail-closed for unresolved provider League identity. Downstream Match, Team, Standing, Odds, H2H, Event, and Scheduler architecture was not redesigned or bypassed.

A full repository-wide suite was not claimed as executed in this report.

## 15. Hard-coded League Check

Static application-code search found no:

- `league_id = provider_id` creation shortcut;
- `provider_id == league_id` resolver shortcut;
- provider ID 389 condition;
- local League 389 mapping;
- manual provider League mapping;
- provider League fallback by name.

League 389 does not appear in the changed application implementation.

## 16. Database Safety Check

No database data was changed and no migration was applied. The existing database schema already contains:

- primary key `leagues.league_id` with a PostgreSQL sequence;
- unique constraint `uq_leagues_provider_provider_id`;
- foreign key `leagues.country_id -> countries.country_id`.

The implementation now uses the existing local ID sequence rather than provider ID. Existing legacy null identities remain repair-required data and are not silently modified.

A future migration may be required to strengthen physical readiness constraints after legacy rows are reconciled. That migration is intentionally not part of this implementation phase.

## 17. Remaining Limitations

- Real Provider + real database onboarding verification was not executed, as required by the phase boundary. It belongs to the next verification phase.
- Existing legacy rows with null `provider_id` or `country_id` remain unresolved and require a separate repair/reconciliation workflow.
- The current provider validator supports the existing `api-football` provider contract; additional providers require explicit provider-specific validation rules.
- The database unique constraint exists, but nullable legacy `provider_id` rows remain possible until data repair and a future readiness constraint are handled.
- The full repository-wide test suite was not used as the completion criterion; the focused and relevant regression suites passed.

## 18. Exact Next Phase Recommendation

Run the following verification phase without changing source or production data:

1. use a real newly discovered Provider League with a provider ID not equal to its expected local ID;
2. capture Country Master creation/reuse and the generated local `league_id`;
3. verify repeat onboarding returns the same local League row;
4. add or verify AllowedLeague readiness through the admin path;
5. run controlled fixture processing and record provider identity, resolved local ID, inserted, updated, failed, and database before/after values;
6. verify Match, LeagueSeason, Team, and downstream foreign-key integrity;
7. separately classify existing League 389 as legacy repair-required evidence, without a special-case mapping.

## Final Status

```text
IMPLEMENTATION STATUS: PASS WITH LIMITATIONS
GENERIC IDENTITY: PASS
COUNTRY RESOLUTION: PASS
LEAGUE MASTER: PASS
ALLOWED/READY: PASS
FIXTURE INTEGRATION: PASS
IDEMPOTENCY: PASS
HARD-CODE CHECK: PASS
REGRESSION: PASS (focused and identity-adjacent suites)
RUNTIME VERIFICATION: NOT EXECUTED; deferred to next phase
```
