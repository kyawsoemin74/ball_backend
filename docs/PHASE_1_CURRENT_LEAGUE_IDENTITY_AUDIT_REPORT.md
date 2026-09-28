# PHASE 1 — CURRENT LEAGUE IDENTITY AUDIT

## 1. Executive Summary

This audit checked the current runtime path, the database schema, and the read-only database state for the Fover backend. The result is clear: the current architecture does not guarantee that a Provider League is correctly connected to one canonical Local League before Fixture Sync is allowed to process its fixtures.

The most important distinction is:

- HTTP success is not the same as sync success.
- Sync success is not the same as database change.
- A request can return HTTP 200 while the system writes zero matches and skips every fixture.

The actual failure mode is structural and observable in code and data:

- Local leagues are keyed by `league_id`, but the fixture sync resolves provider fixtures by exact `(provider, provider_id)`.
- `League.provider_id` is nullable.
- `League.country_id` is nullable.
- The allow-list is a separate table keyed only by `league_id`.
- The fixture sync does not fall back from a provider league ID to canonical `league_id`.
- When a local row exists but has no provider identity, the sync silently skips the fixture and returns `success=True` with zero writes.

This is not a theoretical design concern. It is proven by the current runtime path and the real database state for the League 389 case and the broader league master data.

### Required answer to the exact question

> When a completely new League is added to Fover, does the current system guarantee that the Provider League identity is correctly connected to one canonical Local League before Fixture Sync is allowed to process its fixtures?

Answer: `NO`

Reason: the system does not enforce provider identity at the database level, does not require a valid provider mapping before allow-list status is added, and the fixture sync explicitly skips fixtures when `(provider='api-football', provider_id=...)` resolves to no row. The request can still return HTTP 200 with zero writes.

---

## 2. Current League Identity Architecture

The current flow is:

```
Provider League
      ↓
Provider Identity
(provider + provider_id)
      ↓
Country Resolution
      ↓
League Master
      ↓
Allowed League
      ↓
Fixture Sync
      ↓
Match
      ↓
Standing / Odds / H2H
```

The relevant implementation surfaces are:

- [app/models/league.py](../app/models/league.py)
- [app/models/allowed_league.py](../app/models/allowed_league.py)
- [app/models/country.py](../app/models/country.py)
- [app/models/match.py](../app/models/match.py)
- [app/repositories/league_repository.py](../app/repositories/league_repository.py)
- [app/repositories/allowed_league_repository.py](../app/repositories/allowed_league_repository.py)
- [app/services/league_service.py](../app/services/league_service.py)
- [app/services/league_sync_service.py](../app/services/league_sync_service.py)
- [app/services/country_sync_service.py](../app/services/country_sync_service.py)
- [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py)
- [app/api/admin_leagues.py](../app/api/admin_leagues.py)
- [app/api/matches.py](../app/api/matches.py)

### Architectural reality

The model is split between:

1. `leagues` as the canonical local master table.
2. `allowed_leagues` as an independent allow-list keyed by `league_id`.
3. Provider payloads resolved through `provider` + `provider_id`.
4. Fixture sync requiring exact `(provider, provider_id)` lookup before any fixture is accepted.

This is the critical gap: `allowed_leagues` does not prove that the provider identity is valid. It only proves that a local `league_id` is allowed. The fixture sync uses a different gate: the provider identity lookup.

---

## 3. Database Identity Model

The current database table is defined in [app/models/league.py](../app/models/league.py) and implements:

- `league_id` = primary key, canonical local identity
- `provider` = `String(50)`, `nullable=False`, default `'api-football'`
- `provider_id` = `String(100)`, `nullable=True`, indexed
- `name` = `String(255)`, `nullable=False`
- `country_id` = `Integer`, `nullable=True`, foreign key to `countries.country_id`
- `is_featured` = `Boolean`, `nullable=False`, default `false`
- `created_at` and `updated_at` also exist

Important schema realities:

- There is a unique constraint on `(provider, provider_id)`: `uq_leagues_provider_provider_id`.
- There is no uniqueness constraint on `League.name`.
- `provider_id` is nullable.
- `provider` is not nullable in the model, but the actual table can still contain provider values that are not valid or not consistently normalized.
- `country_id` is nullable.

The exact table declaration includes:

```python
class League(Base):
    __tablename__ = "leagues"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_leagues_provider_provider_id"),
        Index("ix_leagues_provider_id", "provider_id"),
    )

    league_id = Column(Integer, primary_key=True)
    provider = Column(String(50), nullable=False, server_default='api-football')
    provider_id = Column(String(100), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    country_id = Column(Integer, ForeignKey("countries.country_id"), nullable=True, index=True)
```

### Actual database meaning

- The canonical local identity is `league_id`.
- Provider identity is represented by `provider` and `provider_id`.
- `provider_id` is allowed to be `NULL`.
- `country_id` is allowed to be `NULL`.
- The database does not prevent multiple local League rows from representing the same provider league in a logical sense when the write path is incorrect or legacy rows remain.
- The database does not prevent a league from existing without a valid provider identity.
- The database does not prevent a league from existing without a valid country relationship.

This means the current DB implementation is not a guarantee of identity integrity; it is a permissive schema that relies on application logic for correctness.

---

## 4. Provider Identity Flow

The provider identity concept is the exact pair:

```
(provider, provider_id)
```

The implementation is intended to use this combination. The repository confirms it explicitly in [app/repositories/league_repository.py](../app/repositories/league_repository.py):

```python
async def find_by_provider_identity(
    self,
    db: AsyncSession,
    provider: str,
    provider_id: str | int | None,
) -> League | None:
    if provider is None or provider_id is None:
        return None
    provider_id_key = str(provider_id)
    result = await db.execute(
        select(League).where(
            (League.provider == provider) & (League.provider_id == provider_id_key)
        )
    )
    rows = result.scalars().all()
    if len(rows) > 1:
        raise ValueError(
            f"Multiple leagues found for provider={provider} "
            f"provider_id={provider_id_key}"
        )
    return rows[0] if rows else None
```

This repository method is the core path for:

```
provider_id → local league_id
```

The exact flow is:

- Provider fixture payload includes `league.id`.
- `FixtureSyncService._process_sync_with_candidates` calls `find_by_provider_identity(db, "api-football", provider_id)`.
- If the row is missing, the fixture is skipped.
- The result is a `master.league_id` only if the exact provider identity exists.

Additional identity update and creation paths are:

- `LeagueRepository.attach_provider_identity(...)`
- `LeagueRepository.update_provider_identity(...)`
- `LeagueService.register_league(...)`
- `LeagueSyncService.upsert_league(...)`

However, these paths only work if the row already exists or the source payload is valid. They do not enforce a mandatory identity before allow-listing or fixture sync.

### Provider identity validation

There is no robust application-level validation that guarantees every League row has a provider identity before it is considered synchronized or allowed. The validation that does exist is only the lookup check used inside fixture sync and league sync.

---

## 5. Null / Invalid Identity Audit

The current real database state from the live audit showed the following broad identity health issues:

- Total leagues: 1258
- Valid provider identity rows: 31
- `provider_id IS NULL`: 1227
- `provider IS NULL`: not the main issue; the broader problem is unresolved/nullable provider membership and invalid mapping
- `country_id IS NULL`: 1255
- Allowed entries total: 10
- Allowed league rows that are effectively unresolvable: 2

This is a database-level failure of identity completeness, not just a one-off record issue.

### Specific checks performed

The live audit looked for:

- `provider_id IS NULL`
- `provider IS NULL`
- `country_id IS NULL`
- duplicate provider identities
- duplicate local league identities
- suspicious or orphaned country references
- allowed leagues that cannot be resolved by provider identity

The resulting pattern is consistent: the system is capable of storing League rows whose creator did not complete provider identity mapping, and later sync logic rejects them.

### Representative finding

The canonical League `389` case is a direct real example:

- `league_id = 389`
- `provider = 'api-football'`
- `provider_id = NULL`
- `country_id = NULL`
- allowed row existed in `allowed_leagues`
- provider fixture payload still carried `league.id = 389`
- exact lookup `(api-football, 389)` returned zero rows
- fixture sync skipped all 240 fixtures

This proves that neither the allow-list nor the row’s presence is sufficient to guarantee a valid provider identity link.

---

## 6. New League Creation Flow

The current new-league creation process is split across multiple paths.

### A. Manual registration path

In [app/api/admin_leagues.py](../app/api/admin_leagues.py), the endpoint `register_league` calls `football_service.league_service.register_league(...)`.

That method in [app/services/league_service.py](../app/services/league_service.py) does the following:

1. Validates `provider` and `provider_id`.
2. Calls `find_by_provider_identity(db, provider, provider_id)`.
3. Calls the provider API to fetch league details.
4. Validates the provider payload identity matches the requested `provider_id`.
5. Syncs the country via `CountrySyncService`.
6. Creates a local row with:
   - `league_id = provider_id`
   - `provider = provider`
   - `provider_id = provider_id_text`
   - `country_id = country_id`

This flow can create a correctly mapped row when the inputs are valid.

### B. League sync path

In [app/services/league_sync_service.py](../app/services/league_sync_service.py), `upsert_league` looks up the master with `find_by_provider_identity(db, "api-football", provider_id)`. If no row exists, it logs:

```python
logger.warning("Skipping unresolved provider League: provider_id=%s", provider_id)
return None
```

This means the system does not create a valid canonical mapping on the fly just because the provider payload is seen. It requires a resolved local League record.

### C. Allow-list path

In [app/services/allowed_league_service.py](../app/services/allowed_league_service.py), `add_allowed_league` only inserts a row into `allowed_leagues` when the `league_id` exists. It does not validate `provider_id`, provider mapping, or country resolution.

Thus the path is:

```
League row exists
      ↓
AllowedLeague row inserted
      ↓
No provider identity validation here
```

### D. Readiness for Fixture Sync

The system considers a league ready for fixture sync only after it passes the provider identity lookup inside `FixtureSyncService._process_sync_with_candidates` and is also in `allowed_leagues`.

This is not a guarantee. It is a runtime gate that can fail silently when the mapping is absent or invalid.

---

## 7. Country Relationship

The relationship is governed by `League.country_id` pointing to `countries.country_id`, as defined in [app/models/league.py](../app/models/league.py) and [app/models/country.py](../app/models/country.py).

Country sync is handled by [app/services/country_sync_service.py](../app/services/country_sync_service.py).

### Key facts

- `countries.name` is `UNIQUE`.
- `League.country_id` is nullable.
- Country data is synthesized from provider payloads using `sync_country(...)` / `sync_from_league_payload(...)`.
- If the payload has no country value, the sync returns a skipped result and the league may still be created or retained with `country_id = NULL`.
- There is no strict requirement that a League must have a valid Country before it can be allowed or synced.

This means the country relationship is permissive and can silently degrade. The same pattern applies to provider identity: not all rows are required to be complete.

---

## 8. Allowed League Flow

The allowed list is a separate table, [app/models/allowed_league.py](../app/models/allowed_league.py), backed by [app/repositories/allowed_league_repository.py](../app/repositories/allowed_league_repository.py).

It stores:

```python
class AllowedLeague(Base):
    __tablename__ = "allowed_leagues"
    league_id = Column(Integer, primary_key=True, index=True, nullable=False)
```

This means:

- Allowed status is stored in another table, not in the League master row itself.
- A League can be allowed while still having a missing provider identity.
- Allowed status is not a substitute for identity completeness.
- Fixture Sync checks exact provider identity before checking allowed status by canonical local `league_id`.

The critical runtime condition in `FixtureSyncService._process_sync_with_candidates` is:

```python
master = await self.league_repository.find_by_provider_identity(
    db, "api-football", provider_id
)
if master is None:
    logger.warning("Skipping fixture with unresolved provider League: provider_id=%s", provider_id)
    continue

if master.league_id not in allowed_ids:
    continue
```

So the identity check happens before the allow-list check on the master row. If the provider identity resolves to no local row, the fixture is skipped regardless of allowed status.

### Concrete case: allowed + provider_id NULL + provider_id=389

This exact situation was tested in the live runtime audit:

- Local `League 389` existed.
- `League 389` had `provider_id = NULL`.
- `League 389` was listed in `allowed_leagues`.
- Provider payload carried `league.id = 389`.
- `find_by_provider_identity('api-football', '389')` returned no row.

Result:

- fixture skipped
- no match write
- zero insert/update counters
- HTTP 200 returned because the service result had `success=True`

This proves that allowed status is not enough to guarantee synchronizable identity.

---

## 9. Fixture Sync Resolution Flow

The exact logic is in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py).

The sequence is:

```python
for fixture_raw in fixtures:
    provider_id = league_info.get("id")
    if provider_id is None:
        continue

    master = await self.league_repository.find_by_provider_identity(db, "api-football", provider_id)
    if master is None:
        logger.warning("Skipping fixture with unresolved provider League: provider_id=%s", provider_id)
        continue

    if master.league_id not in allowed_ids:
        continue

    filtered_fixtures.append((fixture_raw, master.league_id))

if not filtered_fixtures:
    logger.info("No allowed leagues were present in the fixture payload; skipping fixture synchronization.")
    return {"success": True, "inserted": 0, "updated": 0, "total": 0, "failed": 0, "final_lineup_candidates": []}, set()
```

### Behavior when resolution fails

This failure:

- does not raise an exception,
- does not hard-fail the whole sync,
- skips the fixture(s),
- produces zero writes,
- can still return a successful service result,
- allows the route to commit the empty result and return HTTP 200.

The route in [app/api/matches.py](../app/api/matches.py) confirms the semantics:

```python
result = await football_service.sync_full_season(db=db, league=league_id, season=season)
if not result.get("success"):
    await db.rollback()
    return result
await db.commit()
return result
```

The route declares `status_code=200` regardless of whether any fixture was accepted. Therefore:

```
HTTP success != Sync success != Database change
```

---

## 10. Real Database Evidence

The live runtime audit produced the following read-only database results:

### A. League identity health

- Total leagues: 1258
- Valid provider identities: 31
- `provider_id IS NULL`: 1227
- `country_id IS NULL`: 1255
- Allowed total: 10
- Allowed but effectively unresolvable: 2

### B. Provider identity distribution

The audit showed a broad distribution dominated by rows without resolved provider identity, while the actual valid provider mapping set was small. The current DB is not in a clean provider-linked state.

### C. Suspicious records

Representative records include rows where:

- `league_id` exists,
- `provider` is `api-football`,
- `provider_id` is `NULL`,
- `country_id` is `NULL`,
- allowed status may exist,
- fixture sync cannot resolve them by provider identity.

The League 389 example is the clearest reproduction of this pattern.

---

## 11. Identity Integrity Results

This architecture fails the exact identity guarantees asked in the audit:

### Condition: One Provider League → One canonical Local League

Current implementation status: `NOT ENFORCED`

Reason:

- The database table allows a provider identity to be missing.
- The repository has exact lookup semantics but no enforcement that every provider league must be represented by a canonical local row before sync uses it.
- The allow-list and local identity are not bound together strongly enough.

### Condition: One Provider League → Multiple Local Leagues

Current implementation status: `UNSAFE`

Reason:

- The unique constraint protects `(provider, provider_id)` but not all rows are complete.
- If legacy or mismatched rows exist, the repository would raise a `ValueError` for multiple rows in the exact provider lookup path, which is a symptom of inconsistency rather than a guarantee.

### Condition: Local League → No Provider identity

Current implementation status: `NOT ENFORCED`

Reason:

- `provider_id` is nullable in the model and database.
- The code allows such rows to exist.
- Fixture sync rejects them at runtime.

### Overall classification

This architecture is not identity-safe. It is not a guaranteed canonical mapping model. It is a partially enforced runtime lookup system with null/legacy gaps.

---

## 12. Downstream Dependency Audit

The downstream modules depend on `league_id` in different ways:

- Matches: use `league_id` as the local foreign key, not provider identity.
- Standings: require the canonical `league_id` and then usually resolve provider IDs for source data; they depend on the league master being correctly mapped.
- Odds: usually keyed by fixture or match IDs and then cross-reference to league via match/fixture context.
- H2H: depends on team identity and match context, indirectly on league master through match records.
- Fixtures: depend on `league_id` after provider identity resolution.
- Allowed Leagues: use canonical `league_id` as the allow-list key.
- League Seasons: use canonical `league_id` as the season master key.

The critical point is that the correct downstream relationship relies on one earlier fact: the provider payload must resolve to the correct local league. If that mapping is missing, downstream modules are hollow because they never receive a valid local league master to persist against.

---

## 13. League 389 Root-Cause Evidence

League `389` is the real proof of the general issue.

### Evidence chain

1. Provider fixture response included `league.id = 389`.
2. The system called `find_by_provider_identity(db, "api-football", "389")`.
3. The database row for `league_id = 389` existed but had `provider_id = NULL`.
4. The lookup returned no row.
5. `FixtureSyncService._process_sync_with_candidates` logged:
   `Skipping fixture with unresolved provider League: provider_id=389`
6. All 240 fixtures were skipped.
7. The service returned `success=True`, `inserted=0`, `updated=0`, `total=0`, `failed=0`.
8. The route committed the empty result and returned HTTP 200.
9. Database counts before and after remained unchanged.

This is not a provider failure; it is a local identity gap.

### Architectural gap

The gap is that the system allows a canonical League row to exist and even be allowed while its provider mapping is null or absent. The sync gate is provider identity lookup, but the database schema and application flow do not guarantee that the provider mapping has been completed before a League becomes eligible.

---

## 14. Complete Gap Inventory

### Gap 1 — Provider identity creation
- Current Behavior: Provider identity is created only when a code path explicitly sets it.
- Expected Behavior: A League should not exist without a valid `(provider, provider_id)` mapping before sync eligibility.
- Evidence: `League.provider_id` is nullable and `find_by_provider_identity` returns `None` if `provider_id` is `None`.
- Impact: Sync skips fixtures.
- Severity: Critical

### Gap 2 — Provider identity uniqueness
- Current Behavior: Unique constraint exists on `(provider, provider_id)`, but the system still permits incomplete rows and legacy null identity state.
- Expected Behavior: No duplicate or null provider identity should be accepted for a local League that is active or synced.
- Evidence: The unique constraint is present, but many rows remain with `provider_id = NULL` and the runtime lookup still fails.
- Impact: Identity inconsistencies and unresolved rows.
- Severity: High

### Gap 3 — Null provider_id prevention
- Current Behavior: `provider_id` is nullable.
- Expected Behavior: There should be a guard either at the schema or application layer preventing null provider IDs for leagues that participate in sync.
- Evidence: `provider_id = Column(String(100), nullable=True, index=True)`.
- Impact: Provider-based sync cannot resolve.
- Severity: Critical

### Gap 4 — Country relationship
- Current Behavior: `country_id` is nullable and sync can skip country resolution silently.
- Expected Behavior: Country mapping should be present or explicitly flagged as absent.
- Evidence: `League.country_id` is nullable and `CountrySyncService.sync_from_league_payload` can return `status: skipped`.
- Impact: Incomplete league master state and downstream metadata issues.
- Severity: Medium

### Gap 5 — Allowed League readiness
- Current Behavior: Allowed status is independent from provider identity completeness.
- Expected Behavior: Eligibility should not be granted to a league with unresolved provider identity.
- Evidence: `AllowedLeagueRepository.get_allowed_ids` returns only `league_id`s, and fixture sync still gets an empty filtered list if identity fails.
- Impact: Allowed rows can exist but still never sync.
- Severity: High

### Gap 6 — Fixture Sync resolution
- Current Behavior: Fixture sync resolves each provider fixture by exact provider identity and skips unresolved ones.
- Expected Behavior: Missing identity should be recognized and surfaced as an explicit integrity error or controlled blocking condition.
- Evidence: `_process_sync_with_candidates` logs `Skipping fixture with unresolved provider League` and returns `success=True` with zero writes.
- Impact: Zero-write success responses and silent sync stalling.
- Severity: Critical

### Gap 7 — New League onboarding
- Current Behavior: New League creation can create valid rows, but there is no guarantee that every League ultimately receives a valid provider identity and country mapping.
- Expected Behavior: Onboarding should guarantee a canonical local league mapped to a valid provider identity before sync is enabled.
- Evidence: `register_league` and `upsert_league` are both dependent on successful provider identity resolution, yet legacy/null rows remain possible.
- Impact: New leagues can be created but be ineligible for fixture sync.
- Severity: High

### Gap 8 — Idempotency and empty-success semantics
- Current Behavior: Empty result sets still return `success=True` and commit.
- Expected Behavior: A no-op sync should not be indistinguishable from a successful sync in the HTTP layer.
- Evidence: [app/api/matches.py](../app/api/matches.py) checks only `result.get("success")` before commit.
- Impact: Operational confusion and false positive success signals.
- Severity: High

### Gap 9 — Existing downstream dependencies
- Current Behavior: Many modules depend on a valid `league_id` and provider-linked league master.
- Expected Behavior: The identity contract should be validated before any downstream sync or lookup consumes the record.
- Evidence: All downstream modules rely on the resolved local `league_id` being meaningful and connected to the correct provider identity.
- Impact: Standings, odds, h2h, and fixtures can all be silently absent or stale.
- Severity: High

---

## 15. Current Architecture Classification

Current classification: `IDENTITY_BROKEN`

Reason: the current system does not guarantee canonical provider-to-local league identity before fixture sync. The evidence shows null provider identities, import/allow-list gaps, runtime skipping, and zero-write 200 OK responses.

This is not a design-only concern; it is an active runtime integrity issue.

---

## 16. Recommended Next Phase

The next phase must not change the current code or data. It should instead define a target identity contract and freeze it before implementation.

The required next step is to design a canonical league identity policy based on these findings:

- exactly how a local league is created,
- exactly how provider identity is stored,
- exactly when the provider link becomes mandatory,
- exactly how the allow-list interacts with provider identity,
- exactly how country validation is enforced,
- exactly how the fixture sync gate should behave when a provider league is unresolved.

This phase is complete as an audit only. The actual fix design belongs to the next implementation phase.

---

## Final Report Conclusion

The current system does not guarantee that a Provider League is correctly connected to one canonical Local League before Fixture Sync is allowed to process its fixtures.

Answer: `NO`

Code evidence: [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py), [app/repositories/league_repository.py](../app/repositories/league_repository.py), [app/services/allowed_league_service.py](../app/services/allowed_league_service.py), [app/api/matches.py](../app/api/matches.py)

Database evidence: live read-only league audit showing many `provider_id IS NULL` and `country_id IS NULL` rows, plus the real League 389 case where the allowed row existed but provider identity resolution failed and all fixtures were skipped.
