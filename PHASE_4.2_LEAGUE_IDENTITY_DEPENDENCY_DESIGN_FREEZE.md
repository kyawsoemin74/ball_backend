# PHASE 4.2 - League Identity Dependency Design Freeze

Status: design-only audit. No source code, database data, schema, migrations, sync endpoint, provider synchronization, League Sync, or Team Sync was executed or modified.

## 1. Objective

Freeze the architecture for League provider identity as a dependency of Fixture Sync.

The design applies to every provider-backed League. League 389 is only the observed trigger for the audit, not the design subject.

The central decision is:

> FixtureSync may call the fixture Provider only after the League Master has supplied a validated provider identity. FixtureSync may defensively reject an invalid dependency, but it does not own League identity creation or repair.

## 2. Current Architecture

Evidence: [app/api/matches.py](app/api/matches.py#L469-L500), [app/services/football.py](app/services/football.py#L164-L168), [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L1060-L1115).

```text
API season route
  -> FootballAPIService.sync_full_season
  -> FixtureSyncService.sync_full_season
  -> AllowedLeagueRepository.get_allowed_ids
  -> LeagueRepository.get_by_id(local league_id)
  -> validate League.provider + League.provider_id
  -> FixtureProvider.get_fixtures(provider_id, season)
  -> _process_sync_with_candidates
  -> Team resolve -> ensure -> resolve again
  -> LeagueSeason/Venue/Referee/Team context
  -> Match upsert
  -> API commit
  -> active-match cache and live-list cache
  -> optional lineup finalization
```

| Stage | Current owner | Input/output | Dependency | Failure behavior | Transaction |
|---|---|---|---|---|---|
| API | `app/api/matches.py::sync_full_season` | Local ID/season in; result dict out | Admin, valid query, lock | Validation/auth/409 or returned service result | Owns outer commit/rollback |
| Facade | `FootballAPIService` | DB/local ID/season in; delegates out | `FixtureSyncService` | Propagates result | Same session |
| League eligibility | `AllowedLeagueRepository` | DB in; local allowed IDs out | `allowed_leagues` | Unallowed request returns success/no-op | Read only |
| League identity | `LeagueRepository` plus `FixtureSyncService` guard | Local ID in; provider/provider ID read | `leagues` row | Missing/invalid identity returns failure before provider | Read only |
| Fixture provider | `FixtureProvider.get_fixtures` | Provider ID/season in; fixture payload out | Valid provider identity | Client retries then returns API error | No DB write |
| Fixture orchestration | `FixtureSyncService._process_sync_with_candidates` | Fixtures in; metrics/DB work out | Fixture League mapping, Team identity | Fixture-level skips or fatal DB exception | Nested fixture savepoints inside outer session |
| Team identity | `TeamService` and `TeamSyncService` | Provider Team IDs in; local Team IDs out | Team Master/provider | Ensure missing Teams, retry resolution, skip unresolved fixture | Same outer session |
| Master metadata | Season/Venue/Referee/Coach services | Provider fixture fields in; local IDs/rows out | Related Masters | Exception or fixture failure | Same outer session |
| Match persistence | Direct PostgreSQL upsert plus `MatchRepository` lookup | Normalized fixture in; Match row out | Match identity/FKs | SQL error propagates | Same outer session |
| Cache/finalization | API plus `FixtureSyncService` | Result state in; Redis side effects out | Successful commit | Cache failures logged; finalization uses separate sessions | After main commit |

## 3. Current Failure Boundary

The exact current guard is in `FixtureSyncService.sync_full_season`:

```python
master = await self.league_repository.get_by_id(db, league, allowed_ids=allowed_ids)
provider = str(getattr(master, "provider", "") or "").strip() if master is not None else ""
provider_id = str(getattr(master, "provider_id", "") or "").strip() if master is not None else ""
if provider != "api-football" or not provider_id:
    return {
        "success": False,
        "inserted": 0,
        "updated": 0,
        "total": 0,
        "failed": 0,
        "message": "Fixture sync aborted because local League provider identity is unresolved.",
        "final_lineup_candidates": [],
    }
```

Evidence: [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L1065-L1089).

Current behavior is:

- Allowed IDs are read first.
- The local League is read second.
- Provider namespace and provider ID are validated third.
- `FixtureProvider.get_fixtures` is not called when validation fails.
- No LeagueSeason lookup occurs before this guard.
- No Team resolution, fixture parsing, Match persistence, cache invalidation, or finalization occurs.
- The API rolls back and returns HTTP 200 with the service dictionary.

This is a **defensive consumer guard**, but currently also the first effective enforcement point for a Master-data invariant.

## 4. League Identity Contract

The contract is frozen as follows.

### Canonical identity

`League.league_id` is the local Fover canonical primary key. It is used by local foreign keys such as `matches.league_id` and `league_seasons.league_id`.

### Provider identity

`(League.provider, League.provider_id)` is the external identity. For the current fixture Provider, a Provider-resolvable League must satisfy:

```text
provider == "api-football"
provider_id is non-null
provider_id is non-blank
provider_id is valid for that provider
```

The local ID must never be sent to the Provider as a substitute for `provider_id` unless the provider identity contract explicitly proves they are the same, which this codebase does not.

### Display data

League name, country, logo, type, national flag, featured status, and display order are metadata. They do not establish Provider identity and must not be used as identity fallback.

### Country relationship

Country is a required completeness relationship for the current AllowedLeague creation path, but it is not the external League identity. The League provider identity contract must remain valid even when country metadata is incomplete.

### Frozen invariant

```text
ProviderResolvableLeague
  := canonical League exists
     AND provider == "api-football"
     AND provider_id is non-blank
     AND provider_id is unique within provider namespace
```

The `League` Master lifecycle owns making this invariant true or explicitly marking the League non-provider-resolvable. FixtureSync consumes the invariant and retains a defensive check.

## 5. League Master Responsibility

League Master and its League-domain synchronization services own:

- creation of canonical League rows;
- registration of `(provider, provider_id)`;
- validation that provider identity belongs to the returned provider League;
- conflict detection and identity attachment/repair workflows;
- reporting unresolved identity states before a League is authorized for sync.

Evidence: `LeagueService.register_league` validates provider/provider ID and onboards provider metadata; `LeagueSyncService.onboard_provider_league` creates provider-bearing rows. See [app/services/league_service.py](app/services/league_service.py#L216-L310) and [app/services/league_sync_service.py](app/services/league_sync_service.py#L118-L184).

### Can FixtureSync repair/create League identity?

**Frozen answer: No.**

FixtureSync must not infer identity from League name, local ID, LeagueSeason, or AllowedLeague. It must not create a League as a side effect of fixture retrieval. This preserves the Master-domain ownership already used for provider League registration and avoids allowing a fixture payload to silently establish canonical League identity.

FixtureSync may request an upstream League-domain recovery operation only through an explicit orchestration contract in a future implementation. The recovery operation remains owned by League Master/LeagueSyncService, not by FixtureSync's repository or Provider adapter.

## 6. LeagueSeason Contract

`LeagueSeason` is the canonical local relationship between a League and a season. It owns season-level metadata such as season value, provider season ID when available, dates, and current flag.

Frozen contract:

```text
Valid LeagueSeason
  := valid local League exists
     AND season is a valid positive normalized value
     AND the relationship points to that League
```

For provider-backed season operations, the parent League must also be Provider-resolvable before the Provider request. A LeagueSeason row cannot substitute for a missing parent League provider ID.

The current code writes LeagueSeason after fixture parsing and passes `provider="api-football"` without a provider-season ID in the fixture path. Evidence: [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L613-L620) and [app/services/league_season_sync_service.py](app/services/league_season_sync_service.py#L145-L164).

Frozen responsibility:

- LeagueSeason service owns season row normalization and persistence.
- FixtureSync may persist a season observed in a valid fixture payload as a projection, subject to the LeagueSeason contract.
- FixtureSync must not use LeagueSeason to repair parent League identity.
- Whether a pre-existing LeagueSeason is mandatory before season fixture retrieval is **UNRESOLVED — REQUIRES EVIDENCE/PRODUCT DECISION**. Current source proves it is not a precondition today.

## 7. AllowedLeague Contract

`AllowedLeague` means:

> This local League is authorized to participate in provider-backed synchronization.

It is an authorization relationship, not a provider identity table and not a LeagueSeason table.

Frozen separation:

```text
League Master       -> who the League is
Provider identity   -> how the external provider addresses it
LeagueSeason        -> which local season belongs to it
AllowedLeague       -> whether synchronization is authorized
```

An AllowedLeague row must not be treated as proof that provider identity is valid. The current creation path checks identity, but the repository returns raw IDs and does not continuously revalidate existing rows. Evidence: [app/services/allowed_league_service.py](app/services/allowed_league_service.py#L17-L48) and [app/repositories/allowed_league_repository.py](app/repositories/allowed_league_repository.py#L8-L11).

Target contract: a League should be both Provider-resolvable and Allowed before FixtureSync calls the Provider. Invalid existing authorization must be reported as an authorization/data-integrity state, not silently treated as valid.

## 8. FixtureSyncService Contract

FixtureSyncService is frozen as an orchestration consumer, not a League Master repair service.

### It may assume

- a local canonical League has been selected;
- the League is authorized for the operation;
- the League provider identity has been validated by the League-domain boundary;
- the requested season is valid;
- the Provider request can be formed without guessing.

### It must still validate defensively

FixtureSync must re-check provider namespace and provider ID immediately before Provider access. This protects against stale, imported, or directly modified data even after upstream validation.

### It must not own

- League creation;
- provider identity inference;
- provider identity repair;
- League onboarding through a fixture payload.

### Frozen option selection

| Option | Decision | Reason |
|---|---|---|
| A. Trust invariant without checking | Reject | Runtime data can be stale/incomplete; current guard is valuable. |
| B. Resolve missing identity inside FixtureSync | Reject | Violates Master ownership and creates ambiguous repair authority. |
| C. Request upstream League synchronization | Conditional handoff only | Allowed as an explicit League-domain recovery workflow, not direct FixtureSync ownership. |
| D. Fail only affected League | Freeze for batch-capable operations | Prevents unrelated valid work from being represented as failed. |
| E. Abort whole operation | Freeze only for the single League/season request when its required dependency is unresolved | The requested operation has no valid provider unit to execute; this is not a rule for unrelated batch units. |

## 9. League Identity State Machine

The required states are:

```text
LEAGUE_IDENTITY_MISSING
  -> recovery requested
LEAGUE_IDENTITY_RECOVERY_PENDING
  -> League-domain worker/service owns attempt
LEAGUE_IDENTITY_RECOVERY_RUNNING
  -> provider identity validated and attached
LEAGUE_IDENTITY_RECOVERED
  -> LEAGUE_IDENTITY_VALID

LEAGUE_IDENTITY_RECOVERY_RUNNING
  -> failed/conflicting/invalid result
LEAGUE_IDENTITY_UNRESOLVED

Existing identity with wrong namespace or contradictory provider mapping
  -> LEAGUE_IDENTITY_INVALID
```

State definitions:

| State | Required meaning |
|---|---|
| `LEAGUE_IDENTITY_VALID` | Provider namespace and provider ID pass the frozen invariant |
| `LEAGUE_IDENTITY_MISSING` | Required provider or provider ID is null/blank |
| `LEAGUE_IDENTITY_INVALID` | Values exist but namespace, format, uniqueness, or provider response identity is invalid |
| `LEAGUE_IDENTITY_RECOVERY_PENDING` | A recovery request is recorded but not running |
| `LEAGUE_IDENTITY_RECOVERY_RUNNING` | One owned recovery attempt is active |
| `LEAGUE_IDENTITY_RECOVERED` | Recovery succeeded; transition to valid is verified |
| `LEAGUE_IDENTITY_UNRESOLVED` | Recovery failed, conflicted, exhausted, or cannot safely determine identity |

All states are justified by either a distinct operational action or a distinct failure/retry decision. The current code does not persist this state machine; this is a design contract for the implementation phase.

## 10. Recovery Workflow

The existing Team pattern is:

```text
TEAM_IDENTITY_MISSING
  -> TeamSyncService.ensure_teams_exist
  -> resolve_provider_teams again
  -> process fixture or isolate fixture failure
```

Evidence: [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L589-L610) and [app/services/team_sync_service.py](app/services/team_sync_service.py#L276-L365).

League recovery should follow the same **state-machine principle**, but not necessarily the same inline implementation:

```text
LEAGUE_IDENTITY_MISSING/INVALID
  -> League Master recovery request
  -> LeagueSyncService or approved LeagueService registration path
  -> provider response identity verification
  -> attach/verify provider identity
  -> re-read League Master
  -> retry FixtureSync provider request
```

Frozen ownership:

- League-domain service owns recovery.
- FixtureSync does not directly create/attach identity.
- Recovery and Fixture persistence use separate transaction units/sessions.
- FixtureSync may retry only after the recovery result is committed and re-read.

The exact trigger mechanism (synchronous request, queued job, or operator workflow) is **UNRESOLVED — REQUIRES EVIDENCE/OPERATIONS DECISION**. The ownership and retry-before-provider boundary are frozen.

## 11. Failure Boundary

The boundary is determined by sync scope:

### Single League/season endpoint

```text
Requested League invalid
  -> no provider request for that unit
  -> recovery handoff or unresolved result
```

The endpoint has one League/season request, so it cannot continue that same requested unit without an identity. That is an operation-level failure, not evidence that all Leagues must abort.

### Multi-League/batch orchestration

```text
League A valid      -> provider request -> continue
League B unresolved -> recovery/failure state -> isolate B
League C valid      -> provider request -> continue
```

Frozen rule: one unresolved League must not abort unrelated valid League units when the orchestration has independent provider-request and transaction boundaries. The affected unit is skipped or failed with explicit status; valid units may commit according to the batch policy.

The current season endpoint is not such a batch: it receives one League ID. The current date path can process many fixtures but uses one outer transaction and can return `success:false`, causing rollback. That current behavior is documented as an implementation limitation, not frozen as the preferred batch design.

## 12. Transaction Ownership

Frozen transaction ownership:

| Layer | Responsibility |
|---|---|
| API | Request/session boundary, lock, final commit/rollback, HTTP outcome mapping |
| Scheduler | Creates its session, invokes orchestration, commits/rolls back, records job outcome |
| SyncService | Performs orchestration and flush/savepoint work; does not commit the caller's main transaction |
| Repository | Executes reads/writes/flush contracts; never commits business operations |
| Database | Enforces local uniqueness/FK/check constraints once defined |

League identity recovery and Fixture persistence must use separate transactions/sessions:

1. Recovery must commit only after provider identity has been verified and conflict-checked.
2. Fixture Sync must re-read the committed League identity before calling the fixture Provider.
3. A failed fixture persistence transaction must not partially commit a League identity recovery that was not independently approved.
4. A failed recovery must not create a partial Match transaction.

The current API owns the outer transaction, while per-fixture savepoints are internal to FixtureSync. This ownership remains compatible with the frozen architecture.

## 13. Provider Boundary

The boundary is frozen as:

```text
local league_id
  -> League Master lookup
  -> validated provider + provider_id
  -> FixtureProvider
  -> GET /fixtures?league=<provider_id>&season=<season>
```

Provider access must occur **after** identity validation, never inside a Provider adapter that guesses or repairs local identity. The Provider receives only the external provider identity and season parameters required by its endpoint. It does not receive unresolved local League records as a substitute.

The current implementation follows this order for Season Sync: [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L1065-L1099).

## 14. Team Dependency

The frozen dependency chain is:

```text
League Master identity       REQUIRED before provider request
  -> LeagueSeason context     REQUIRED for valid season persistence; pre-fetch row currently optional
  -> AllowedLeague             REQUIRED authorization for this sync path
  -> FixtureProvider           REQUIRED external fetch boundary
  -> Team identity             REQUIRED before Match persistence when provider Team IDs exist
  -> Match persistence         FINAL fixture write
```

Classification:

| Dependency | Classification |
|---|---|
| League provider identity | Required, upstream-owned, recoverable through League domain |
| LeagueSeason | Local season relationship; current pre-fetch requirement unresolved; persistence contract required |
| AllowedLeague | Required authorization, not identity |
| Team identity | Downstream, recoverable via TeamSyncService before Match write |
| Match identity | Required for idempotent persistence |

Team recovery is fixture-level because the provider fixture has already been fetched and independent fixture records can be processed. League recovery is provider-request-level because the provider request itself is keyed by League provider identity.

## 15. API Failure Contract

The current `HTTP 200 + success:false` body is not frozen as the target contract. It is current behavior caused by a normal return dictionary and fixed route status. The target contract must distinguish:

| Outcome | Required semantic distinction |
|---|---|
| Invalid request | Query validation/auth/authorization failure |
| Missing/invalid Master identity | Dependency failure before provider access |
| Recovery pending/running | Accepted or deferred dependency recovery, if asynchronous |
| Provider failure | External request/retry failure after valid identity |
| Empty provider result | Valid request, no fixtures returned |
| Partial sync | Some independent units committed, some isolated/failed |
| Complete sync failure | No durable work committed for the unit |

The implementation phase must choose exact HTTP statuses and a stable machine-readable error code. Existing source is insufficient to freeze the numeric status taxonomy safely. **UNRESOLVED — REQUIRES API CONTRACT DECISION.**

What is frozen now:

- Do not represent all business failures as indistinguishable HTTP 200 success responses.
- Do not use `failed:0` for a failed precondition without a separate failure classification.
- Preserve a body that includes operation scope, failure state, recovery state, and durable counts.

## 16. Scheduler Behavior

Scheduled Fixture Sync must not retry an unresolved League at uncontrolled provider frequency.

Frozen scheduler behavior:

- Unresolved identity is a Master-domain dependency state, not a generic provider timeout.
- Scheduler records `LEAGUE_IDENTITY_MISSING`, `LEAGUE_IDENTITY_INVALID`, or `LEAGUE_IDENTITY_UNRESOLVED` with canonical League ID and season.
- Retry ownership belongs to the League identity recovery workflow, not repeated FixtureSync polling.
- Fixture Sync may be retried only after recovery reports committed/verified identity.
- Recovery attempts require bounded retry/backoff and a terminal/manual-review state after exhaustion.
- A failed League must not prevent unrelated valid League units from being scheduled.
- Logs and metrics must distinguish skipped due to unresolved identity from provider failures and database failures.

Exact retry interval, queue technology, and maximum attempt count are **UNRESOLVED — REQUIRES OPERATIONS EVIDENCE**. The ownership and bounded-retry rule are frozen.

## 17. Idempotency Requirements

Repeated identity recovery and Fixture Sync must converge without duplicates.

Required constraints and service checks:

- Local League primary key remains canonical `league_id`.
- External League identity is unique per `(provider, provider_id)`.
- Identity attachment must verify provider response identity and detect conflicts before mutation.
- Repeated recovery for an already valid identity is a no-op.
- `LeagueSeason` is unique per `(league_id, season)`.
- `AllowedLeague` is unique by `league_id`.
- Match is unique by `(provider, provider_fixture_id)` through `uq_matches_provider_fixture_id`.
- Recovery must not create a second local League merely because the first row is unresolved.
- Fixture Sync must re-read identity after recovery and use normal Match upsert idempotency.
- A recovery commit and fixture commit must be independently retryable.

These requirements align with existing repository constraints for LeagueSeason, AllowedLeague primary key, and Match provider fixture identity. The current nullable identity schema does not by itself guarantee the full operational invariant.

## 18. Observability Requirements

Every identity failure/recovery event must include, without secrets:

- local canonical `league_id`;
- provider namespace;
- provider ID, if present, otherwise an explicit null/blank marker;
- requested season;
- identity state;
- recovery attempt number/state/result;
- sync scope and operation ID;
- provider request attempted: yes/no;
- FixtureSync outcome;
- inserted/updated/failed/isolated counts;
- transaction outcome;
- retry/deferred/manual-review state.

Logs must not include API keys or authorization tokens. Metrics should separate identity failures from provider HTTP failures, Team identity failures, DB failures, and lock contention.

## 19. Master Architecture Compatibility

The frozen design is consistent with the existing Master-domain philosophy:

| Master | Existing/current philosophy | Compatibility |
|---|---|---|
| Country | Canonical local master synchronized by a domain service | League identity remains League-owned; country is supporting metadata |
| League | Provider-bearing Master onboarding and identity lookup | Directly compatible; identity guarantee must be made explicit |
| LeagueSeason | Relationship/season metadata with local League FK | Compatible; cannot substitute for parent League identity |
| Team | Provider identity resolution with ensure/re-resolve recovery | Provides the recovery pattern, but League recovery remains League-owned |
| Player | Provider identity resolution through dedicated services | Supports the rule that identity resolution belongs to the Master domain, not Fixture persistence |

No Provider or Repository is promoted to own business identity. No API layer becomes the canonical Master authority. The design strengthens an existing boundary rather than silently changing the frozen architecture.

## 20. Architectural Weakness

### Current weakness

> FixtureSyncService currently treats unresolved League provider identity as an immediate whole-operation abort.

That behavior is understandable for a single League/season provider request, but it exposes several upstream weaknesses:

1. League provider identity is nullable and not universally guaranteed.
2. AllowedLeague authorization can exist as historical state without a valid provider identity.
3. LeagueSeason has a local FK but does not guarantee parent provider resolvability.
4. League identity recovery is not part of the current FixtureSync recovery pattern.
5. The API reports the precondition failure as HTTP 200 with `success:false` and `failed:0`.
6. Batch/date failure boundaries do not guarantee durable isolation of valid work when the aggregate result becomes unsuccessful.

The design weakness is a combination of missing upstream invariant, incomplete responsibility boundary, missing League recovery handoff, and weak failure semantics. It is not evidence that FixtureSync should guess or repair League identity itself.

## 21. Target Architecture

```text
League Master / LeagueSyncService
    |
    +-- Canonical League Identity
    |       +-- local league_id
    |       +-- provider = api-football
    |       +-- provider_id
    |       +-- validated uniqueness/conflict state
    |
    +-- Identity state / recovery ownership
            |
            +-- VALID ------------------------------+
            |                                        |
            v                                        v
      LeagueSeason --------------------------> AllowedLeague
      valid local relationship                 sync authorization
                                                     |
                                                     v
                                           FixtureSyncService
                                                     |
                                      defensive identity re-check
                                                     |
                                  +------------------+------------------+
                                  |                                     |
                         Identity valid                      Identity missing/invalid
                                  |                                     |
                                  v                                     v
                         FixtureProvider                 League recovery handoff/state
                                  |                                     |
                                  v                                     v
                         Team resolve/ensure              resolve and retry after commit
                                  |                                     |
                                  v                                     v
                         Match persistence                 isolated failure if unresolved
                                  |
                                  v
                         API/Scheduler commit
                                  |
                         Cache + finalization
```

Corrected dependency meaning:

- League Master owns identity.
- LeagueSeason does not repair identity.
- AllowedLeague authorizes but does not establish identity.
- FixtureSync calls the Provider only after identity validation.
- Team identity is downstream and recoverable after fixture fetch.
- Unresolved identity is isolated to its operation unit; it must not poison unrelated valid batch units.

## 22. Decision Matrix

| Decision | Current behavior | Frozen target |
|---|---|---|
| Canonical League identity | Local `league_id` | Keep local `league_id` as canonical |
| External identity | Nullable provider/provider ID | Validated `(provider, provider_id)` owned by League Master |
| Identity owner | Split lifecycle; guard in FixtureSync | League Master/LeagueSyncService |
| FixtureSync repair | None | No direct repair; explicit League-domain recovery handoff only |
| Provider access | After current guard | Only after validated identity |
| LeagueSeason pre-fetch | Not read | Precondition status unresolved; parent identity always required for provider-backed work |
| AllowedLeague | Raw local authorization IDs | Authorization only; must not imply identity |
| Team recovery | Ensure and re-resolve | Preserve pattern |
| Single unresolved League | Whole requested unit fails | Single unit fails/defers; unrelated units continue |
| Multi-unit unresolved League | Aggregate can roll back | Isolate transaction/provider unit where batch behavior is offered |
| Transaction owner | API/scheduler outer session | Keep API/scheduler ownership; recovery separate session/transaction |
| Error representation | HTTP 200 failure dictionary | Stable typed failure state; exact HTTP mapping unresolved |
| Scheduler retry | General job retry behavior | Bounded League-domain recovery, then FixtureSync retry |

## 23. Implementation Constraints

The implementation phase must not violate these constraints:

1. Do not infer provider ID from local League ID, name, country, or season.
2. Do not create/repair League identity inside Match/Fixture persistence code.
3. Do not call the fixture Provider before provider identity validation.
4. Do not allow an AllowedLeague row alone to prove resolvability.
5. Do not use LeagueSeason to bypass parent League identity validation.
6. Preserve `(provider, provider_id)` conflict/idempotency checks.
7. Keep League recovery and Fixture persistence transactionally separate.
8. Preserve Team ensure/re-resolve semantics as a downstream pattern.
9. Keep API and scheduler as outer transaction owners.
10. Do not make cache invalidation precede the durable transaction outcome.
11. Distinguish unresolved identity from provider failure, empty data, Team failure, and DB failure.
12. Do not make this design or implementation specific to League 389.

## 24. Verification Plan

The implementation phase should verify the frozen contract without using production data:

1. Read-only inventory of all League provider states and AllowedLeague relationships.
2. Unit tests for valid, missing, blank, unsupported, conflicting, and recovered provider identities.
3. Assert no fixture Provider call occurs for invalid identity.
4. Assert League recovery is owned by League service and commits before FixtureSync retry.
5. Assert repeated recovery creates no duplicate League/provider identity.
6. Assert LeagueSeason uniqueness and parent identity behavior.
7. Assert AllowedLeague authorization does not bypass identity validation.
8. Assert valid Team recovery preserves resolve -> ensure -> resolve behavior.
9. Test single-League failure and multi-unit isolation separately.
10. Test transaction rollback when recovery or fixture persistence fails.
11. Test API/scheduler result classification for identity failure, provider failure, empty result, partial success, and complete failure.
12. Verify cache and finalization occur only after the relevant commit.
13. Verify logs contain the required identity/recovery fields without secrets.

## 25. FINAL DESIGN FREEZE

**B. DESIGN FROZEN WITH LIMITATIONS**

The following decisions are frozen:

1. **Who owns League Provider Identity?** League Master and its League-domain synchronization/registration services.
2. **Who validates it?** League-domain lifecycle validates it upstream; FixtureSync performs a final defensive validation before Provider access.
3. **Who may repair it?** Only an explicit League-domain recovery workflow; not FixtureSync, TeamSync, Provider adapters, or repositories acting alone.
4. **When may FixtureSync call the Provider?** Only after `provider="api-football"` and a validated non-blank provider ID are resolved from the canonical League Master.
5. **Should one unresolved League abort unrelated valid work?** No for independent batch units; the single-League/season operation itself cannot proceed without its required identity.
6. **Where is the transaction boundary?** API/scheduler owns the outer Fixture transaction; League identity recovery uses a separate committed transaction/session before retry.
7. **What is recovery?** League-domain recovery, verified re-read, then FixtureSync retry; Team-style ensure/re-resolve is the pattern, but ownership remains League-domain.
8. **What happens when recovery fails?** Mark the League identity unresolved, do not call the fixture Provider, isolate that unit, and hand off to bounded retry/manual review according to scheduler policy.
9. **How is partial success represented?** By explicit per-unit state and durable counts; the exact HTTP status/error-code taxonomy is **UNRESOLVED — REQUIRES API CONTRACT DECISION**.
10. **How does this remain consistent with Team recovery?** Both use explicit identity states and re-resolution, but Team recovery occurs downstream after fixture discovery while League recovery must occur upstream before the fixture Provider request.

Limitations requiring explicit implementation-phase evidence/decisions:

- exact synchronous versus asynchronous League recovery trigger;
- exact LeagueSeason pre-fetch requirement;
- exact HTTP status and machine-readable error-code taxonomy;
- exact scheduler retry interval and maximum attempts;
- exact database enforcement mechanism for the invariant.

These limitations do not block the core architecture freeze. They are deliberately marked unresolved rather than guessed.
