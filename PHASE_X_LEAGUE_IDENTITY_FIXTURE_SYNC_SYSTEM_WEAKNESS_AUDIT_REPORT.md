# Phase X: League Identity / Fixture Sync System Weakness Audit

Audit target: `POST /api/matches/sync/season`  
Audit mode: source inspection plus read-only PostgreSQL `SELECT` queries. No source code, schema, or database data was modified. No sync endpoint or provider synchronization was executed.

## 1. Objective

Determine why Fixture Sync can reach the state:

```text
Fixture sync aborted because local League provider identity is unresolved.
```

The analysis uses League 389 only as one observed database example. The conclusion is about the League pipeline generally.

## 2. Runtime Failure Observed

The season endpoint accepts a **local** `league_id` and a `season`. The actual season service first reads the local `leagues` row. It then requires:

```text
League.provider == "api-football"
League.provider_id is non-empty
```

If either condition fails, `FixtureSyncService.sync_full_season` returns:

```json
{
  "success": false,
  "inserted": 0,
  "updated": 0,
  "total": 0,
  "failed": 0,
  "message": "Fixture sync aborted because local League provider identity is unresolved.",
  "final_lineup_candidates": []
}
```

The API then rolls back and returns that dictionary with HTTP 200. This is not a provider fixture failure: the provider request has not yet been made.

## 3. Complete Fixture Sync Flow

Evidence: [app/api/matches.py](app/api/matches.py#L438-L480), [app/services/football.py](app/services/football.py#L164-L168), [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L1060-L1100).

```text
POST /api/matches/sync/season?league_id=<local_id>&season=<year>
  -> app.api.matches.sync_full_season
  -> admin authentication and AsyncSession dependency
  -> resource lock: fixture_query / global
  -> football_service.sync_full_season(db, league=<local_id>, season=<year>)
  -> FootballAPIService.sync_full_season
  -> FixtureSyncService.sync_full_season
  -> AllowedLeagueRepository.get_allowed_ids
  -> LeagueRepository.get_by_id(local league_id, allowed_ids)
  -> read League.provider and League.provider_id
  -> FixtureProvider.get_fixtures(provider_id, season)
  -> GET /fixtures?league=<provider_id>&season=<year>
  -> FixtureSyncService._process_sync_with_candidates
  -> provider League identity read for every returned fixture
  -> Team identity resolution / optional Team creation
  -> LeagueSeason upsert for each parsed fixture with a season
  -> Venue and Referee sync
  -> Match upsert
  -> optional standings prewarm is NOT part of sync_full_season
  -> commit by API
  -> active-match Redis updates and fover:live_matches deletion
  -> optional pending lineup finalization
```

| Stage | Actual class/function | Required condition | Failure behavior |
|---|---|---|---|
| Router/auth | `app/api/matches.py::sync_full_season` | Active admin and valid query types | Auth/validation errors; no service call |
| Lock | `run_with_resource_lock(db, "fixture_query", "global", sync)` | PostgreSQL advisory transaction lock available | HTTP 409 if not acquired |
| Allow-list read | `AllowedLeagueRepository.get_allowed_ids` | `local league_id` must be in `allowed_leagues` | Returns success/no-op if not allowed |
| Local League read | `LeagueRepository.get_by_id(db, league, allowed_ids)` | Existing League row in allow-list | Unresolved identity failure if provider/provider_id invalid |
| Provider identity | `master.provider`, `master.provider_id` | Provider exactly `api-football`; provider ID non-empty | Returns `success:false`; provider not called |
| Fixture provider | `FixtureProvider.get_fixtures` | Valid provider identity and provider response | API error/no-fixtures result; pagination is supported here |
| Fixture filter | `_process_sync_with_candidates` | Each returned fixture has a provider League identity mapping and allowed local League | Unresolved fixture League can abort the payload; disallowed League is skipped |
| Parser | `parse_fixture_to_match` | Fixture date, ID, League, Team/goal/status shape parse successfully | Fixture counted failed/skipped |
| Team identity | `TeamService.resolve_provider_teams` | Existing or creatable provider Team identities | Missing Team creation attempted once; still unresolved fixture is skipped |
| Related metadata | Season, Venue, Referee, Coach/Team context services | Valid payload and repository operations | Fixture failure or transaction exception depending on error |
| Match persistence | `pg_insert(Match).on_conflict_do_update` | Provider fixture identity and FK constraints | Normal duplicate updates; SQLAlchemy error aborts transaction |
| Commit | Router `sync` closure | Result `success` true | Commit; otherwise rollback |
| Cache/finalization | Router after commit | Successful commit | Active keys/live list updated; optional lineup finalization in separate sessions |

### Important actual behavior

`sync_full_season` does **not** resolve or read `LeagueSeason` before the provider request. The `season` query parameter is passed directly to the provider. `LeagueSeason` is created/updated later from returned fixture payloads. Therefore the advertised League -> LeagueSeason -> AllowedLeague precondition chain is not actually enforced as a complete read-time chain.

## 4. League Identity Contract

Fixture Sync correctly distinguishes local identity from provider identity at the first season-sync boundary:

```text
incoming league_id
  -> local leagues.league_id
  -> local League row
  -> require provider = "api-football"
  -> require provider_id
  -> provider request uses League.provider_id, not local league_id
```

Evidence: [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L1065-L1090).

The contract is therefore:

| Field | Required meaning |
|---|---|
| `League.league_id` | Fover local canonical identity and FK target |
| `League.provider` | Source namespace; this path requires `api-football` |
| `League.provider_id` | API-Football league identifier sent as `/fixtures?league=...` |
| `LeagueSeason.league_id` | FK to local League; not used to resolve the season-sync provider request |
| `LeagueSeason.season` | Local season record; written during fixture processing, not required before season fetch |
| `AllowedLeague.league_id` | Authorization/eligibility list; does not contain provider identity |

The system does **not** equate `local league_id` with provider identity. The failure is a deliberate guard against making that unsafe assumption. The weakness is that the database/model/population pipeline permits rows that cannot satisfy the downstream contract.

## 5. League -> LeagueSeason -> AllowedLeague Chain

The implemented chain is actually:

```text
AllowedLeague.league_id
  -> allowed local League.league_id
  -> League.provider/provider_id validation
  -> provider season request using League.provider_id + request season
  -> returned fixture league/season
  -> LeagueSeason upsert using local League ID + fixture season
```

It is not:

```text
League -> resolve LeagueSeason -> validate AllowedLeague -> request fixtures
```

Specific findings:

- `AllowedLeague` has only `league_id`; it does not store or validate provider identity itself.
- `AllowedLeagueService.add_allowed_league` currently checks that the referenced League exists, has non-empty provider/provider_id, and has a country relationship. Evidence: [app/services/allowed_league_service.py](app/services/allowed_league_service.py#L17-L48).
- Existing invalid AllowedLeague rows are not repaired or revalidated by `AllowedLeagueRepository.get_allowed_ids`; it returns IDs only. Evidence: [app/repositories/allowed_league_repository.py](app/repositories/allowed_league_repository.py#L8-L11).
- `LeagueSeasonSyncService.upsert_season` accepts `league_id`, `season`, provider, and provider ID values without independently resolving the League provider identity. Evidence: [app/services/league_season_sync_service.py](app/services/league_season_sync_service.py#L145-L164).
- Fixture Sync writes a `LeagueSeason` with `provider="api-football"` but does not pass a season `provider_id`. Evidence: [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L613-L620).
- `LeagueSeason` has a foreign key to local League, but no database constraint guarantees that the parent League has a valid provider/provider_id pair.

## 6. Why Unresolved Identity Can Exist

The architecture allows this state because the League model treats provider identity as nullable data, while downstream Fixture Sync treats it as a mandatory operational invariant.

### Source-level population paths

1. **Current provider onboarding path:** `LeagueService.register_league` validates a positive provider ID, fetches provider details, and creates a row with `provider` and `provider_id`. Evidence: [app/services/league_service.py](app/services/league_service.py#L200-L251), [app/services/league_service.py](app/services/league_service.py#L263-L310).
2. **League sync path:** `LeagueSyncService.onboard_provider_league` creates provider-bearing rows and existing provider-bearing rows are looked up by provider identity. Evidence: [app/services/league_sync_service.py](app/services/league_sync_service.py#L105-L184).
3. **AllowedLeague path:** new authorization is blocked when identity is incomplete, but this validates only at authorization time. It does not repair or continuously enforce old rows. Evidence: [app/services/allowed_league_service.py](app/services/allowed_league_service.py#L17-L48).
4. **Model/schema path:** `League.provider_id` is explicitly `nullable=True`; the public `LeagueCreate` schema contains local/display fields but no provider/provider_id fields. Evidence: [app/models/league.py](app/models/league.py#L9-L30), [app/schemas/league.py](app/schemas/league.py#L5-L24).
5. **Repository/compatibility path:** `LeagueRepository` supports `create_registered`, `attach_provider_identity`, `update_provider_identity`, and an `upsert_one`; these are separate operations and do not impose a universal non-null identity invariant. Evidence: [app/repositories/league_repository.py](app/repositories/league_repository.py#L25-L67), [app/repositories/league_repository.py](app/repositories/league_repository.py#L155-L220).
6. **Legacy/import/restore path:** the source model and schema permit provider-null rows, and the live database contains a large legacy-style population of such rows. The repository contains no evidence that restored/imported rows are automatically registered or identity-repaired before authorization/sync.
7. **Partial lifecycle path:** League row creation/update and provider identity attachment are separate operations in the repository. A row can exist before identity attachment, and the model permits it to remain in that state. A transaction rollback protects individual transactions, but it does not create a cross-workflow invariant for pre-existing rows.

### Classification of the identity guarantee

| Question | Finding |
|---|---|
| Guaranteed at every League row creation? | **No**; model permits nullable provider_id and legacy/display schemas omit it. |
| Guaranteed by current provider onboarding? | **Yes for that path**, assuming the transaction commits. |
| Guaranteed after League sync? | **Yes for provider rows returned and successfully onboarded**, not for unrelated legacy rows. |
| Optional in persisted schema? | **Yes**; `provider_id` is nullable. |
| Repairable later? | **Yes in repository API** through identity attachment/update, but no general automatic repair path is shown. |
| Allowed to remain unresolved? | **Yes at database/model level**, and the live data proves that state exists. |

## 7. Failure Propagation Analysis

The message is both a valid defensive domain guard and evidence of an upstream invariant/ownership weakness.

The season service does not attempt to infer a provider ID from local ID, League name, LeagueSeason, or AllowedLeague. That prevents accidental requests for the wrong provider league. However, the failure is returned before the provider call and before any fixture can be processed.

For the season endpoint, one requested local League is the entire operation. Therefore an unresolved requested League necessarily blocks that season request. It does not mean unrelated leagues are processed, because the endpoint accepts only one `league_id` per call.

For the date endpoint, the behavior differs: `_process_sync_with_candidates` examines each returned fixture. Unresolved provider League identities are counted, and if no valid filtered fixtures remain with unresolved entries, it returns a failed result. Thus one unresolved League in a date payload can make the date operation fail even when other valid fixtures were present, depending on whether valid filtered fixtures remain; valid fixtures may have been processed before the final result is returned, but the API rolls back a `success:false` result.

The failure is therefore:

- **Expected defensive validation** at the Fixture Sync boundary.
- **Architectural coupling** because Fixture Sync is the first guaranteed enforcement point for an invariant that League/AllowedLeague creation does not enforce at schema/database level.
- **Potentially excessive failure propagation** because the date path can turn unresolved identity into a failed request and rollback rather than a structured per-League skip.

## 8. HTTP/API Error Semantics

The season route declares HTTP 200 and returns the service dictionary. When `FixtureSyncService.sync_full_season` detects incomplete local identity, it returns `success:false`; the router executes `await db.rollback()` and returns the dictionary unchanged.

This response correctly conveys a domain failure in the body but incorrectly/ambiguously represents it at the HTTP contract level:

| State | Body | HTTP behavior |
|---|---|---|
| Invalid/unallowed local League | `success:true`, zero work, message for not allowed | HTTP 200 |
| Missing provider identity | `success:false`, zero work, unresolved identity message | HTTP 200 |
| Provider response missing/error | `success:false`, API error | HTTP 200 |
| No fixtures | `success:false`, no fixtures found | HTTP 200 |
| Partial fixture processing | Usually `success:true` with `failed` count, unless a fatal path changes success | HTTP 200 |
| Lock contention | No result body from service | HTTP 409 |
| Unexpected exception | Exception after rollback | Usually HTTP 500 |

Callers cannot reliably classify validation failure, provider failure, and system failure from HTTP status alone. They must inspect free-form `message` and `success`, while partial failure depends on additional counters. This is an API contract weakness independent of the League data weakness.

## 9. Database Consistency Audit

The database has structural relationships but not the operational identity invariant:

```text
LeagueSeason.league_id -> leagues.league_id       # enforced FK
AllowedLeague.league_id -> leagues.league_id      # model primary-key relationship
League.provider_id nullable                        # no non-null invariant
League.provider/provider_id pair validity          # no DB check constraint shown
```

The chain can become incomplete at these points:

- A local League can exist with `provider_id IS NULL`.
- An AllowedLeague can point to such a row if it was created before current validation, restored/imported, inserted outside the current service, or retained after an identity became incomplete.
- A LeagueSeason can exist for a local League while that League has no provider identity; the FK proves only local parent existence.
- LeagueSeason provider identity is nullable and is not used to repair the parent League.
- Fixture Sync requires the parent League provider identity only at request time.

### Failure matrix

| Condition | Can it happen? | Why | Detector | Result | Sync stops? | Unrelated data can sync? |
|---|---:|---|---|---|---:|---:|
| Local League missing | Yes | Invalid local ID or deleted/isolated row | `get_by_id` returns none | Not allowed/no-op result | Requested sync does not fetch | Other requests yes |
| League provider missing | Yes | Nullable model; legacy/import/partial lifecycle | `sync_full_season` | `success:false`, rollback, no provider call | Yes for requested season | Other league requests yes |
| League provider unsupported | Yes by schema | `provider` is not DB-constrained to `api-football` | `sync_full_season` | Same unresolved result | Yes | Other league requests yes |
| League provider ID blank | Yes | Nullable/free text field | `sync_full_season` | Same unresolved result | Yes | Other league requests yes |
| League not Allowed | Yes | Allow-list is separate authorization | `sync_full_season` before identity validation | `success:true`, zero work | Yes for requested call | Other allowed calls yes |
| Allowed row points to missing League | Structurally prevented by FK in current model; can exist only if constraint absent/legacy anomaly | Separate authorization table and historical state | `get_allowed_ids` does not validate | ID is later rejected by League lookup | Requested call no-op/failure path | Other calls yes |
| LeagueSeason parent missing | Structurally prevented by declared FK; not observed in query | FK | Database | Insert/update rejected | Transaction-dependent | Other data potentially no |
| LeagueSeason parent identity invalid | Yes | FK does not validate provider fields | Not checked by season request | Season request still fails at League identity | Yes | Other league requests yes |
| Fixture payload provider League unresolved | Yes | Provider fixture ID has no local master mapping | `_process_sync_with_candidates` | Fixture skipped; possibly overall failure/rollback | Date result can fail | Valid fixtures may be processed before rollback, but commit may be prevented |
| Team identity missing | Yes | Team master separate from fixture data | Team resolver | Create attempt; then fixture skip if unresolved | Fixture only, except DB errors | Yes, subject to result semantics |

## 10. All-League Identity Audit

A read-only query was executed against the configured local PostgreSQL database on 2026-09-22. No write statements were issued.

### Counts

| Measure | Count |
|---|---:|
| `leagues` rows | 1,264 |
| `league_seasons` rows | 130 |
| `allowed_leagues` rows | 12 |
| League rows with invalid/missing provider namespace | 0 |
| League rows with missing/blank `provider_id` | 1,227 |
| Orphan `allowed_leagues` rows | 0 |
| Allowed rows whose League identity is invalid | 2 |
| Orphan `league_seasons` rows | 0 |
| LeagueSeason rows whose parent League identity is invalid | 0 |

Representative unresolved League rows include local IDs 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, and 13; all have `provider='api-football'` and `provider_id=NULL` in the query result. This is broad persisted state, not a League-389-only condition.

The two invalid AllowedLeague rows observed were:

| Local League ID | Name | Provider | Provider ID |
|---:|---|---|---|
| 98 | J1 League | `api-football` | NULL |
| 389 | Premier League | `api-football` | NULL |

The database snapshot contains no invalid-parent LeagueSeason rows, but that does not prove the parent identity invariant: a valid FK parent can still have a null provider ID. The dominant issue is that most League Master rows are not provider-addressable even though the model default/provider label says `api-football`.

## 11. Responsibility / Ownership Audit

| Component | Actual responsibility | Finding |
|---|---|---|
| Country Master | Resolves country records and FKs | Not the owner of League provider identity |
| League model | Stores local identity and provider/provider_id | Allows incomplete identity because provider_id is nullable |
| LeagueService | Registration/onboarding and compatibility facade | Correctly creates provider-bearing rows in current registration path; not a universal invariant enforcer |
| LeagueSyncService | Provider League onboarding and metadata sync | Owns provider-sourced League creation; does not migrate/repair all legacy rows |
| LeagueSeason | Stores local League-season relationship | Does not guarantee parent provider identity; provider fields nullable |
| AllowedLeagueService | Authorizes a local League for synchronization | Current create path validates identity; existing rows are not continuously revalidated |
| FixtureSyncService | Consumes provider identity and rejects unresolved League | Correct downstream guard, but it is enforcing an upstream invariant late |
| Repository | Reads/updates identity and persists rows | Offers attach/update operations but does not centralize mandatory identity guarantees |
| API layer | Exposes registration and allow-list operations | Current admin endpoint blocks new invalid allow-list entries; it cannot explain/normalize all historical invalid rows |

Ownership is therefore **distributed and incomplete**. League Master/LeagueSyncService should be the source of provider identity, AllowedLeague is the authorization gate, and FixtureSyncService is only the consumer/defensive validator. In practice, the database permits the invalid state and no single lifecycle process audits all existing rows before they become eligible for sync.

## 12. Frozen Architecture Compliance

The main provider-to-database direction is broadly compliant:

```text
Provider -> SyncService -> Repository/SQL -> Service facade -> API
```

Actual findings:

- Provider classes are transport-oriented.
- LeagueSyncService and FixtureSyncService own orchestration.
- Repositories perform League identity reads and persistence operations.
- API owns request transaction commit/rollback and locking.
- FixtureSyncService does direct PostgreSQL `Match` upsert rather than delegating the Match write to a dedicated repository method, a local layering inconsistency.
- The identity invariant is enforced downstream in FixtureSyncService rather than guaranteed centrally by League Master/database lifecycle.
- AllowedLeague validation is duplicated conceptually: the creation service validates identity, while runtime sync validates it again. This protects execution but allows historical drift.

No evidence shows that a Repository is performing the provider HTTP call for this flow. The primary weakness is invariant ownership, not a reversal of provider/repository responsibilities.

## 13. Systemic Weakness Classification

**Primary classification: G. MULTIPLE SYSTEMIC WEAKNESSES**

The evidence supports all of the following:

- **C. Missing upstream invariant:** `League.provider_id` is nullable and 1,227 of 1,264 current rows lack it.
- **D. Broken ownership boundary:** League identity is created/updated through several paths, but FixtureSyncService is the first mandatory consumer guard.
- **E. Incorrect dependency between League and Fixture Sync:** a League row can exist and appear in the allow-list while it is not provider-addressable.
- **F. Error-handling/API contract weakness:** the endpoint returns HTTP 200 for unresolved identity and other business/provider failures.
- **B. Data-quality issue:** the current database demonstrably contains broad incomplete identity data.

This is not merely normal defensive validation and not merely a single bad League row. The defensive guard is correct; the system design permits the guarded-invalid state to persist and exposes it through a weak error contract.

## 14. Blast Radius

### Direct impact

- Season Fixture Sync for any requested local League with missing/invalid provider identity.
- Any fixture/date sync fixture whose provider League cannot map to a local provider identity.
- Match creation/update for those rejected fixtures.
- Team resolution and downstream Match metadata for fixtures that never reach processing.

### Indirect impact

- LeagueSeason creation/update for fixtures not processed.
- Standings prewarm when the fixture/season processing never establishes candidates.
- Active-match registration and live list cache updates for skipped Matches.
- Match detail APIs that depend on a Match row that was never inserted.
- Lineup/event/statistics/odds/H2H workflows for a Match that does not exist or is not active/eligible.
- Scheduler jobs that select allowed/local Match rows can have incomplete coverage; separate jobs may still operate on existing valid Matches.

### No direct impact proven

- Country Master operations in general.
- Existing Matches for unrelated valid Leagues.
- Explicit Event, Lineup, Statistics, Odds, or H2H sync endpoints when called directly for an existing valid Match; those are separate flows.
- Provider API calls for unresolved season identity: the request is blocked before provider access.

## 15. Root Cause

The direct question is:

> Why can Fixture Sync reach a state where the League exists locally but its provider identity is unresolved, and why does that condition abort the entire sync?

**Why the state can exist:** the League table and model allow a League row with `provider_id=NULL`; the legacy/display schema omits provider identity; League identity attachment is a separate repository operation; current onboarding guarantees identity only for rows passing that specific provider onboarding path; and historical/imported/restored rows are not globally reconciled before allow-list/runtime use. The database confirms this is widespread: 1,227 of 1,264 League rows have no provider ID, and two currently allowed rows are incomplete.

**Why the sync aborts:** season fixture retrieval requires the provider League ID. The service deliberately refuses to substitute local `league_id` because local and provider identities are distinct. Without a provider ID it cannot form a trustworthy `/fixtures?league=<provider_id>&season=<season>` request, so it returns a failed result before provider access. The API then rolls back and returns HTTP 200 with `success:false`.

Final conclusion:

```text
DATA PROBLEM              = proven: broad null provider_id population
ARCHITECTURAL PROBLEM    = proven: nullable identity and missing centralized invariant
API CONTRACT PROBLEM     = proven: business failure represented as HTTP 200
OVERALL                  = combination of all three
```

## 16. Evidence Matrix

| Finding | Source/database evidence |
|---|---|
| Season route and transaction | [app/api/matches.py](app/api/matches.py#L438-L480) |
| Facade delegation | [app/services/football.py](app/services/football.py#L164-L168) |
| Local League -> provider identity contract | [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L1065-L1090) |
| Provider fixture request | [app/providers/fixture_provider.py](app/providers/fixture_provider.py#L9-L38) |
| Allow-list IDs are raw local IDs | [app/repositories/allowed_league_repository.py](app/repositories/allowed_league_repository.py#L8-L11) |
| Per-fixture League identity filtering | [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L515-L588) |
| League model permits nullable provider ID | [app/models/league.py](app/models/league.py#L9-L30) |
| Current provider registration validates identity | [app/services/league_service.py](app/services/league_service.py#L200-L310) |
| Provider League onboarding writes identity | [app/services/league_sync_service.py](app/services/league_sync_service.py#L105-L184) |
| New AllowedLeague validation | [app/services/allowed_league_service.py](app/services/allowed_league_service.py#L17-L48) |
| LeagueSeason does not resolve parent provider identity | [app/services/league_season_sync_service.py](app/services/league_season_sync_service.py#L145-L164) |
| Fixture path writes LeagueSeason after fixture parsing | [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L613-L620) |
| Read-only DB counts | PostgreSQL SELECT audit executed 2026-09-22; counts recorded in Section 10 |
| Invalid allowed examples | Read-only query returned local IDs 98 and 389 with null provider IDs |
| HTTP 200 body semantics | [app/api/matches.py](app/api/matches.py#L438-L480) |
| Cache/finalization after commit | [app/api/matches.py](app/api/matches.py#L451-L473) |

## 17. Risks

- Most League Master rows are not addressable through the provider despite carrying `provider='api-football'`, creating a misleading partial identity state.
- Allow-list eligibility is not a database invariant; historical invalid rows can remain eligible until runtime failure.
- A requested season cannot proceed partially because the endpoint is scoped to one local League and fails before provider retrieval.
- Date fixture processing can discover unresolved provider League identities after fetching a mixed payload; the failure result can prevent commit of otherwise valid work.
- HTTP 200 business failures can cause monitoring, clients, and job wrappers to treat a failed sync as transport success.
- LeagueSeason existence may give the appearance of readiness while the parent League still lacks provider identity.
- Provider identity repair is available as repository capability but is not a single audited lifecycle operation with completeness reporting.

## 18. What Must Be Decided Before Fixing

No fix was implemented. Before a future implementation phase, the project must decide:

1. Whether every League row must be provider-addressable, or whether local-only/legacy Leagues are valid and must be explicitly excluded from AllowedLeague.
2. Whether provider identity is immutable after registration, attachable through an approved repair workflow, or replaceable with conflict checks.
3. Whether LeagueSeason should be a prerequisite for fixture sync or merely a projection created from fixture payloads.
4. Whether AllowedLeague creation is the only authorization gate or whether runtime eligibility must revalidate provider identity.
5. Whether a mixed date payload should commit valid fixtures while reporting invalid League fixtures, or fail atomically.
6. Which HTTP status and machine-readable error code represent unresolved identity, provider failure, no data, and partial success.
7. How restored/imported/legacy rows are measured and classified before any invariant is strengthened.

## 19. Recommended Next Phase

The next phase should be a design and evidence phase, not an ad hoc League repair:

- Inventory all League rows and classify provider identity states by source/lifecycle.
- Trace migration/import/restore history for the null-provider population.
- Define the canonical League identity invariant and AllowedLeague eligibility rule.
- Define LeagueSeason's role and whether provider season identity is required.
- Specify failure isolation and response semantics for season/date sync.
- Create a controlled, read-only validation query set and a separately approved remediation plan.
- Only after those decisions, implement and test invariant enforcement, repair workflow, and sync error semantics.

## 20. Final Status

**AUDIT COMPLETE - SYSTEMIC WEAKNESS CONFIRMED**

The failure is not specific to League 389. Fixture Sync reaches it because local League existence, provider identity, LeagueSeason state, and AllowedLeague authorization are represented and maintained by separate paths with no database-level or universal lifecycle invariant requiring a valid provider identity. Fixture Sync correctly refuses to guess the provider identity, but it is forced to discover the inconsistency downstream and aborts the requested season sync. The final finding is a **combination of data problem, architectural invariant/ownership problem, and API contract problem**.
