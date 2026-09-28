# PHASE 2 — LEAGUE IDENTITY ARCHITECTURE DESIGN & FREEZE

## 1. Executive Summary

This document freezes the target League Identity Architecture for the Fover backend based strictly on the Phase 1 audit findings.

The key fact is simple: the current implementation does not provide a guaranteed canonical mapping from a Provider League to one Local League before Fixture Sync can safely process fixtures. The target architecture must therefore enforce canonical identity in a deterministic, design-first way.

This phase is design-only. It does not modify source code, database schema, database data, scheduler behavior, sync behavior, or configuration.

The target rule is:

- Provider League identity is canonical externally.
- Local League identity is canonical internally.
- A valid Provider → Local League mapping must exist before a league is allowed to participate in fixture synchronization.
- Country identity must be valid when required by league metadata contracts.
- AllowedLeague must represent local eligibility, not act as a provider identity substitute.
- FixtureSyncService must consume a resolved local `league_id` and never infer identity from name alone.

This design is intentionally built to be robust for existing rows, newly added leagues, restored data, and repeated synchronization.

---

## 2. Phase 1 Findings Used

The Phase 1 audit established the following facts that drive the target design:

1. Local League identity is `league_id`.
2. Provider League identity is intended to be `(provider, provider_id)`.
3. The runtime fixture logic resolves provider fixtures using `find_by_provider_identity(db, "api-football", provider_id)`.
4. `League.provider_id` is nullable in the current schema.
5. `League.country_id` is nullable in the current schema.
6. `allowed_leagues` is a separate table keyed by `league_id` only.
7. `FixtureSyncService` skips fixtures when the provider identity is unresolved.
8. A route can return HTTP 200 even when inserted/updated/total are all zero.
9. A local League can exist and even be allowed while still being unresolvable by provider identity.
10. The current architecture is not safe for new or existing League onboarding without explicit identity rules.

These findings are the reason the target architecture must separate provider identity, local master identity, country identity, and allowed eligibility into clear ownership boundaries.

---

## 3. Current Identity Flow

### 3.1 Actual current lifecycle

The current system is observed to work as follows:

```text
Provider
  → Provider League Data (API-Football payload)
  → Country Resolution
  → League Resolution / Creation
  → AllowedLeague
  → Fixture Provider
  → FixtureSyncService
  → Match
```

### 3.2 Actual code path and ownership today

#### Provider data entry
- Provider league payloads enter through the provider client and service layer.
- The relevant code surfaces are in:
  - `app/services/league_service.py`
  - `app/services/league_sync_service.py`
  - `app/services/fixture_sync_service.py`
  - `app/services/country_sync_service.py`

#### Where `provider` is assigned
- `League.provider` is assigned in `LeagueRepository.attach_provider_identity(...)` and `LeagueRepository.update_provider_identity(...)`.
- It is also set as part of registration and upsert logic when a local row is created.
- The model is defined in `app/models/league.py`:

```python
provider = Column(String(50), nullable=False, server_default='api-football')
```

#### Where `provider_id` is assigned
- `League.provider_id` is set by `attach_provider_identity(...)`, `update_provider_identity(...)`, and by league creation payloads.
- The key field is nullable in the schema, so the assignment is not mandatory.

#### Where local `league_id` is generated
- Local `league_id` is the database primary key and acts as the canonical internal identifier.
- In many cases it is implicitly assigned by the database or set directly when a league row is created.
- In the registration path, a local row can be created with `league_id = provider_id` if the code chooses that shape.

#### Where `country_id` is assigned
- `CountrySyncService.sync_country(...)` creates or upserts a country master.
- `League.country_id` is then set on the local League via country sync or by row creation if the country result is available.
- The current code supports an absent country case and does not require `country_id` to be populated before the League can continue in some flows.

#### Where League is created
- `LeagueService.register_league(...)` creates a League row when a provider league is registered.
- `LeagueSyncService._upsert_league(...)` resolves an existing provider identity and may carry the local League forward.
- `LeagueRepository.create_registered(...)` commits a new row.

#### Where League is updated
- `LeagueRepository.update_provider_identity(...)` updates provider identity.
- `LeagueRepository.update_country_id(...)` updates country linkage.
- `LeagueSyncService.upsert_league(...)` resolves the master and then updates metadata if needed.

#### How an existing League is found
- The central lookup is:

```python
await self.league_repository.find_by_provider_identity(db, "api-football", provider_id)
```

This is the exact provider → local resolution path used before fixture sync proceeds.

#### How AllowedLeague is resolved
- `AllowedLeagueRepository.get_allowed_ids(db)` returns the set of local `league_id` values in the allow-list.
- The allow-list is separate and contains only `league_id` entries.
- It does not directly validate Provider identity.

#### How FixtureSync resolves Provider League → Local League
- `FixtureSyncService._process_sync_with_candidates(...)` does:
  - read provider league `id`
  - call `find_by_provider_identity(db, "api-football", provider_id)`
  - skip if no local row exists
  - skip if row is not allowed
  - accept only if mapping resolves successfully

#### What happens on identity failure
- The system logs a warning and skips the fixture.
- If no fixtures remain after filtering, the service returns a result containing zero inserts/updates while still reporting `success=True`.
- The route then commits the empty result and returns HTTP 200.

This is the direct root of the current failure pattern observed in Phase 1.

---

## 4. Canonical Identity Definitions

### 4.1 Provider League Identity

Canonical external identity:

```text
(provider, provider_id)
```

This is the correct external identity because it distinguishes provider identity from local canonical master identity and supports exact matching across provider payloads and local league rows.

This must be treated as the authoritative identity for provider-based Mapping.

### 4.2 Local League Identity

Canonical local identity:

```text
league_id
```

This is the stable internal identity that downstream tables and synchronization modules should use after provider identity has been resolved.

It is responsible for:

- references in `matches`
- links to `allowed_leagues`
- local foreign-key relationships
- downstream standings, odds, h2h, and season dependencies

`league_id` is not a substitute for provider identity. It is the local canonical key after the provider mapping has been validated.

### 4.3 Country Identity

Canonical country identity should be:

```text
country_id
```

with provider-derived country metadata normalized into the `countries` master.

Rules:

- Country is a master entity.
- Country identity may be provider-derived but should be canonically represented in local master tables by `country_id`.
- Country should be resolved through a stable normalized mapping, not by using League name as the primary key.

### 4.4 AllowedLeague Identity

AllowedLeague must represent:

```text
Local League membership / eligibility
```

It must not act as a provider identity resolver and must not be treated as a substitute for missing `(provider, provider_id)` data.

AllowedLeague must be a local eligibility table only.

---

## 5. Provider → League Mapping

### Target canonical mapping

```text
Provider
  provider_id
      ↓
League Master
  league_id
```

### Required rules

1. Match provider league to local league using exact `(provider, provider_id)`.
2. Do not resolve identity by League name.
3. Do not treat `allowed_leagues` as identity evidence.
4. A new local League must be created only if the provider mapping is valid and the row does not already exist.
5. If a provider league resolves to more than one local League, that is an integrity violation and must be blocked.
6. If the provider identity is missing or invalid, sync must stop before fixture processing begins.
7. If a League exists without provider identity, it must be treated as uninitialized and ineligible for provider-based fixture sync.
8. Provider identity changes must be treated as a repairable integrity event, not a silent overwrite with no validation.

### Forbidden behavior

The following must be forbidden in the target design:

- name-based league matching as a primary resolution rule
- allowed-list membership as a provider identity replacement
- fixture processing when `(provider, provider_id)` is missing
- creating multiple local League rows for the same provider identity
- implicit creation of a local League from a provider fixture without validation

---

## 6. Country → League Relationship

### Target relationship

```text
Provider Country
      ↓
Country Master
      ↓
League.country_id
```

### Design decisions

- Country is a required master relationship for League metadata when the provider provides a valid country value.
- If country metadata is missing or cannot be normalized, the League may remain in a retryable or incomplete state but must not become fully sync-eligible without a valid country decision.
- `League.country_id = NULL` is allowed only in a controlled, explicit incomplete state and must never be treated as fully synchronized/ready.
- Fixture sync must not rely on the absence of country as a success condition.

### Classification

| State | Classification | Rule |
| --- | --- | --- |
| Country exists and resolves cleanly | REQUIRED | Continue |
| Country data absent but league is otherwise valid | RETRYABLE / INCOMPLETE | Do not mark fully ready |
| Country value invalid or malformed | FAILURE | Block or repair |
| League.country_id is invalid or orphaned | FAILURE | Block |
| Fixture Sync proceeds with missing country | NOT ALLOWED | Must not proceed |

The design must distinguish between:

- REQUIRED: must exist for full sync readiness
- OPTIONAL: acceptable for incomplete metadata but not full sync eligibility
- RETRYABLE: can be reattempted later
- FATAL: blocks processing and requires repair or explicit exception

---

## 7. New League Onboarding Workflow

The target onboarding flow for a brand-new Provider League is:

```text
Provider returns League X
        ↓
Resolve Provider Country
        ↓
Resolve/Create Country Master
        ↓
Resolve League by (provider, provider_id)
        ↓
League exists?
   ┌────┴────┐
  YES       NO
   ↓         ↓
Validate   Create
Identity   League Master
   ↓         ↓
   └────┬────┘
        ↓
Validate Country relationship
        ↓
AllowedLeague decision
        ↓
Fixture Sync eligibility
        ↓
Provider Fixtures
        ↓
Match persistence
```

### Required decision points

1. Determine provider identity exactly: `(provider, provider_id)`.
2. If the provider id is missing or invalid, stop.
3. Resolve or create the Country Master only after provider country data is verified.
4. Resolve or create the League Master only through canonical provider identity.
5. If the League exists but has an invalid provider mapping, stop and mark it as a repair-required identity record.
6. If the League is not in `allowed_leagues`, keep it out of fixture sync.
7. Only when all identity checks and eligibility checks pass is the League allowed to participate in fixture sync.

This flow must be deterministic and consistent for all new leagues, not special-cased for any one record.

---

## 8. Failure / Recovery Matrix

| Condition | Classification | Action |
| --- | --- | --- |
| Provider League identity valid | SUCCESS | Continue |
| Provider identity missing | FAILURE | Stop / Repair |
| Local League not found | CREATE | Create League Master |
| Duplicate `(provider, provider_id)` | FAILURE | Block / Repair |
| Country identity missing | RETRYABLE / FAILURE | Defer or block depending on contract |
| `country_id` invalid | FAILURE | Block |
| League not Allowed | SKIP | Do not sync |
| Allowed League but Provider identity missing | FAILURE | Do not sync |
| Provider League cannot resolve | FAILURE | Do not sync |
| Match references unknown League | FAILURE | Do not create invalid Match |
| Repeat sync | SUCCESS | Idempotent update |

### Notes

- A missing provider identity is always a failure condition for fixture sync eligibility.
- An allowed local league lacking valid provider identity is not sync-ready.
- Country issues may be retryable only if the system explicitly distinguishes incomplete metadata from hard identity failure.
- All duplicate identity cases must be blocked rather than repaired by silent overwrites.

---

## 9. Fixture Sync Contract

### Target Fixture Sync contract

```text
Provider League
      ↓
Canonical Provider Identity
      ↓
Canonical Local League
      ↓
Allowed League
      ↓
Fixture accepted
```

### Frozen rules

1. Fixture Sync must never process a fixture when `provider_id` is NULL.
2. Fixture Sync must never create a new League by itself as an implicit side effect of fixture processing.
3. Fixture Sync must never resolve a local league by name.
4. Fixture Sync must never bypass the League Master.
5. Fixture Sync must stop on unresolved identity, not silently skip while returning success.
6. If a provider league cannot resolve to a canonical local League, the correct state is failure or skip; it is not a successful sync.
7. If a local league is allowed but provider identity is invalid, the correct behavior is: do not sync.

### Required decision

- `Can FixtureSync process a fixture when provider_id is NULL?` → No.
- `Can FixtureSync create a new League by itself?` → No.
- `Can FixtureSync use League name to resolve identity?` → No.
- `Can FixtureSync bypass Country/League Master?` → No.
- `What should happen when identity resolution fails?` → Fail fast, skip with explicit reporting, never return false success.

---

## 10. Ownership Boundaries

### Ownership definitions

```text
Country Master
    owns Country identity

League Master
    owns League identity

AllowedLeague
    owns League eligibility

FixtureSyncService
    consumes resolved League identity
    does NOT become League Master
```

### Required responsibilities

| Domain | Responsible Service / Entity | Responsibility |
| --- | --- | --- |
| Country master | CountrySyncService / CountryRepository | Create / resolve / validate country master |
| League master | LeagueService / LeagueRepository / LeagueSyncService | Create / resolve / register / update canonical local league |
| AllowedLeague | AllowedLeagueService / AllowedLeagueRepository | Maintain local eligibility only |
| Fixture sync | FixtureSyncService | Consume a resolved local league and process fixtures |
| Repair / data cleanup | Separate repair process | Handle legacy or bad identity state outside normal sync |

### Critical rule

No service should independently create a canonical League master without passing through the canonical identity contract.

---

## 11. Database Invariants

The target architecture requires the following invariants:

### 11.1 Provider identity invariant

```text
(provider, provider_id) → unique League
```

This is the canonical external mapping invariant.

### 11.2 Local league identity invariant

```text
league_id → unique local entity
```

This prevents duplicate canonical local ids.

### 11.3 Country invariant

```text
League.country_id → valid Country.country_id
```

This must be enforced by the design and by any future implementation step.

### 11.4 Null handling

- `provider_id` must be non-null for sync-eligible leagues.
- `country_id` must be valid for a fully synchronized league.
- Nulls may exist only in explicitly incomplete or repair-required rows, not in the ready-for-sync state.

### 11.5 Duplicate prevention

- No duplicate `(provider, provider_id)` allowed in the canonical local population.
- No duplicate `league_id` allowed.
- No duplicate provider league row without explicit repair or reconciliation.

### 11.6 Legacy rows

Existing null or broken rows are not to be silently treated as valid. They must be classified as repair-required or manual-review items.

---

## 12. Idempotency Rules

### Required behavior

Repeated provider synchronization must be idempotent.

```text
First sync
→ Create

Second sync
→ Resolve existing League

Third sync
→ Update existing League

Repeated sync
→ No duplicate League
```

### Idempotency invariants

1. Re-running the same provider league must not create duplicate canonical League records.
2. Re-run of the same fixture set must not duplicate matches.
3. Identity resolution must use the canonical matching rule, not name heuristics.
4. Only fully validated local league records should be accepted into the fixture sync path.

---

## 13. Legacy Data Strategy

The Phase 1 findings require the design to classify existing problematic records without modifying them.

| Problem | Classification | Repair Workflow |
| --- | --- | --- |
| `provider_id = NULL` | REPAIR REQUIRED | Treat as unresolved identity; exclude from fixture sync |
| `country_id = NULL` | REPAIR REQUIRED / INCOMPLETE | Require explicit metadata repair or explicit incomplete state |
| duplicate provider identity | MIGRATION REQUIRED | Block in implementation; reconcile legacy rows |
| incorrect provider | MANUAL REVIEW | Do not silently overwrite |
| incorrect country reference | REPAIR REQUIRED | Repair via canonical country resolution |
| AllowedLeague without resolvable Provider identity | REPAIR REQUIRED | Remove or quarantine from sync eligibility |
| League with no valid country and no valid provider identity | MANUAL REVIEW | Do not allow sync |

### Design rule

Legacy rows are not to be silently normalized in Phase 2. They must be recognized as a repair boundary outside the normal sync contract.

---

## 14. Target Architecture Diagram

```text
                    PROVIDER
                       │
                       ▼
              Provider League
                       │
                       ▼
              Provider Identity
            (provider, provider_id)
                       │
                       ▼
                League Master
                  league_id
                       │
              ┌────────┴────────┐
              ▼                 ▼
        Country Master      AllowedLeague
          country_id          eligibility
              │                 │
              └────────┬────────┘
                       ▼
                Fixture Sync
                       │
                       ▼
                  Match DB
```

### Responsibility of each layer

- Provider: emits provider league payloads.
- Provider identity: canonical external identity tuple `(provider, provider_id)`.
- Country master: canonicalizes country metadata and provides `country_id`.
- League master: stores canonical local `league_id` and a validated provider mapping.
- AllowedLeague: records eligibility of a canonical local league.
- FixtureSyncService: consumes the resolved local `league_id`, not a guessed or name-based identity.
- Match DB: persists match data only for fixtures tied to verified local league masters.

---

## 15. Frozen Architectural Rules

## FROZEN ARCHITECTURAL RULES

1. Canonical Provider League identity is `(provider, provider_id)`.
2. Canonical Local League identity is `league_id`.
3. Provider → Local League resolution must use exact provider identity matching.
4. League name must never be used as the primary resolution key.
5. Country must resolve to a Country Master before a League is considered fully ready for canonical operation.
6. `League.country_id` may be NULL only in an explicitly incomplete or repair-required state, not in the ready-for-sync state.
7. AllowedLeague ownership is local eligibility only; it must not substitute for provider identity validation.
8. FixtureSyncService must consume a resolved canonical local `league_id` and may not infer identity by name or local row drift.
9. If `(provider, provider_id)` is missing or invalid, fixture sync must not proceed.
10. If a local League cannot resolve to a valid provider identity, it must be treated as unresolved and ineligible.
11. Duplicate provider identity must be blocked; silent overwrites are not allowed.
12. Repeated synchronization must be idempotent and must not create duplicate master records.
13. Legacy bad rows are outside the normal sync contract and must require a designated repair workflow.
14. No special-case treatment is allowed for any single League such as League 389.
15. Ownership boundaries must be respected: Country master owns country identity; League master owns local identity; AllowedLeague owns eligibility; FixtureSync consumes the result.

---

## 16. Current vs Target Gap Analysis

| Area | Current Behavior | Target Behavior | Gap | Implementation Required |
| --- | --- | --- | --- | --- |
| Provider identity | Nullable / partially enforced | Canonical `(provider, provider_id)` required | High | Required |
| Local League identity | `league_id` exists but can be unresolved | `league_id` always tied to valid provider mapping | High | Required |
| Country linkage | `country_id` may be null | Country must resolve or be explicitly incomplete | High | Required |
| AllowedLeague | Local allow-list only | Local eligibility, not identity | Medium | Required |
| FixtureSync | Skips unresolved provider leagues | Fails fast or blocks before fixture processing | Critical | Required |
| New League onboarding | Can exist with incomplete identity | Must validate identity before sync eligibility | Critical | Required |
| Duplicate provider identity | Not fully protected in operational state | Must be rejected | Critical | Required |
| Legacy repair | Hidden in data | Explicit repair workflow and classification | High | Required |
| Idempotency | Repeated sync may silently produce no-op/skip outcomes | Deterministic no-duplicate canonical flow | High | Required |
| Ownership boundaries | Mixed responsibilities across services | Clear separation of master, eligibility, consumption | Medium | Required |

---

## 17. Phase 3 Implementation Requirements

Phase 3 implementation may proceed only if it follows the design freeze above.

### Required implementation constraints

- Enforce canonical provider identity before fixture sync eligibility.
- Enforce canonical local league mapping before sync processing.
- Validate Country before League is treated as fully ready.
- Require explicit identity repair for legacy rows with missing provider ids.
- Keep AllowedLeague as a local eligibility gate only.
- Keep FixtureSyncService as a consumer of the resolved canonical League master, not as the owner of identity rules.
- Reject duplicate provider identity without silent overwrite.
- Make idempotency explicit and deterministic.
- Require explicit handling for unresolved provider identity, orphan country, and invalid local master state.

This is the implementation contract that must be used in the next phase.

---

## 18. Final Classification

ARCHITECTURE FROZEN — READY FOR IMPLEMENTATION
