# Odds Module End-to-End Audit

**Audit date:** 2026-10-09  
**Scope:** Read-only source, schema/migration, test, and configuration inspection.  
**Result:** Backend source flow verified; runtime data, provider activity, and Flutter integration were not verified.

## 1. Executive Summary

The production Odds path is an API-Football fixture-scoped snapshot flow:

1. An admin sync endpoint or the six-hour scheduler selects a local Match and obtains its provider fixture ID.
2. `OddsSyncService.refresh_odds` calls `OddsProvider` once per logical refresh.
3. The provider issues `GET /odds?fixture=<provider_fixture_id>`.
4. `OddsService` selects API-Football bookmaker ID `11` (treated by this code as 1xBet), retains only complete main lines for bet IDs `1`, `4`, `5`, and `45`, and optionally computes Myanmar labels.
5. `OddsRepository` replaces the local match's Odds snapshot; the outer API/scheduler owns commit/rollback and post-commit cache invalidation.
6. The public GET serves Redis if a value already exists, otherwise reads PostgreSQL. A cache miss does not call API-Football and does not write the database result into Redis.

The core provider → service/sync → repository → database boundary is recognizable, and both sync entry points pass the local Fover ID separately from the provider fixture ID. Important deviations and risks remain:

- A provider response with no usable odds leaves existing Odds rows untouched. The scheduler nevertheless treats several such no-data results as successful commits and invalidates the cache; the admin route returns 502 because those results lack `status: SUCCESS`.
- Live Odds normalize to 1xBet plus raw provider market names, while the analytics projection accepts Bet365 plus canonical market names. Its zero-filter path preserves any prior analytics snapshot instead of projecting the newly fetched rows.
- `commit_odds_refresh` calls commit-classification/verification methods absent from the current `OddsSyncService`; an ambiguous scheduler commit therefore cannot follow the apparent verification path.
- Odds are stored as strings without a database numeric/positive-value check, source market ID, provider fixture ID, or pre-match/live field.
- Scheduler cadence and HTTP retries can multiply provider requests. Per-match locks prevent concurrent refreshes sharing the same local ID, not repeated sequential refreshes or admin-triggered refreshes.
- The repository contains an offline Bet365 CSV filter and three `phase5_docker` Odds copies, but they are not wired into the production app path.

No source, database, cache, migration, environment, or runtime state was changed by audit actions. No API-Football call, sync endpoint, scheduler, or database-backed test was run.

## 2. Scope and Safety Confirmation

- Inspected the current application implementation, selected Odds-related tests, model/migrations, scheduler registration, API wiring, and configuration structure.
- Did not read credentials from `.env` or print credential values.
- Did not connect to PostgreSQL/Redis or query existing Odds data. Current-row integrity is therefore **NOT VERIFIED**.
- Did not inspect live logs or claim observed provider calls. Runtime behavior/activity is **NOT VERIFIED**.
- Did not run any sync route, provider method, scheduler job, migration, or database-backed test.
- Ran only `myanmar_odds/tests/test_myanmar_odds.py` and `tests/test_odds_service_myanmar_sync.py`; both use pure logic, fakes, and mocked clients/DB/cache. Result: **95 passed**, with three existing Pydantic deprecation warnings.
- At initial inspection, untracked `app/filter_odds.py` and `app/E0.csv` were present. At final inspection, both paths were absent. No audit action edited or deleted either path, and the cause of their disappearance is unknown. Per user direction, they remain absent. The audit report is the only file created by this task.

## 3. Odds Component Inventory

Paths below are relative to this report. “Called by” describes source wiring, not observed runtime execution.

| Component | Actual file | Main responsibility | Called by |
|---|---|---|---|
| Main Odds ORM model | [app/models/odds.py](../app/models/odds.py), lines 8-28 | `Odds` table mapping, local Match FK, business unique key | Odds repository, Odds API availability check, scheduler freshness query |
| Provider protocol | [app/providers/base/football_api_client.py](../app/providers/base/football_api_client.py), lines 1-10 | Async HTTP client protocol | `OddsProvider` |
| HTTP client | [app/services/base/football_client.py](../app/services/base/football_client.py), lines 32-169 | API-Football URL/header, HTTP request, metadata/error handling, retry and metrics | `OddsProvider`; constructed by the football service |
| Odds provider | [app/providers/odds_provider.py](../app/providers/odds_provider.py), lines 1-15 | Transport-only `/odds` request with fixture query | `OddsSyncService.refresh_odds` |
| Read/normalization service | [app/services/odds_service.py](../app/services/odds_service.py), lines 17-254 | Selects bookmaker/markets/lines, Myanmar labels, cache/DB read serializer | `OddsSyncService`; `FootballAPIService`; GET route via facade |
| Write orchestration | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 27-140 | Provider result handling, row preparation, repository replacement, analytics projection | Admin sync route and scheduler job |
| Persistence repository | [app/repositories/odds_repository.py](../app/repositories/odds_repository.py), lines 8-67 | Odds selects, delete, PostgreSQL upsert, snapshot replacement | `OddsSyncService`; read service |
| API routes | [app/api/matches.py](../app/api/matches.py), lines 301-312 and 380-438 | Public read route and admin sync route; outer transaction owner | Router mounted by `app/main.py` |
| API router mount | [app/main.py](../app/main.py), lines 88-89 | Mounts matches routes under `/api` | FastAPI startup |
| Scheduler | [app/services/scheduler.py](../app/services/scheduler.py), lines 46-49, 194-284, 797-933 | Registers six-hour Odds job, selects candidates, locks, commits, invalidates cache | `worker.py` through `scheduler_service.py` |
| Scheduler process entry | [worker.py](../worker.py), lines 1-34; [scheduler_service.py](../scheduler_service.py), lines 1-20 | Starts/stops the scheduler process | Container/process command |
| Shared cache functions | [app/cache.py](../app/cache.py), lines 21-24, 100-142; [app/services/cache_service.py](../app/services/cache_service.py), lines 7-28 | Redis key construction, get/set/delete, exception fallback | Odds service, API and scheduler |
| Resource lock | [app/services/resource_lock.py](../app/services/resource_lock.py), lines 10-64 | Transaction-scoped PostgreSQL advisory lock | Admin route and scheduler |
| Analytics Odds model/repository | [app/models/analytics.py](../app/models/analytics.py), lines 65-80; [app/repositories/analytics_repository.py](../app/repositories/analytics_repository.py), lines 37-48 | Separate numeric analytics snapshot | `AnalyticsProjectionService.project_odds` |
| Analytics projection | [app/services/analytics_projection_service.py](../app/services/analytics_projection_service.py), lines 249-274 | Filters and replaces analytics snapshots | `OddsSyncService` |
| Myanmar odds engine | [myanmar_odds/services/myanmar_odds_service.py](../myanmar_odds/services/myanmar_odds_service.py), lines 1-126; [myanmar_odds/models/myanmar_odds_result.py](../myanmar_odds/models/myanmar_odds_result.py), lines 1-16 | Pure odds-to-ladder conversion | `OddsService._attach_myanmar_odd_labels` |
| Myanmar mappings/parser | [myanmar_odds/constants/base_index_map.py](../myanmar_odds/constants/base_index_map.py), [myanmar_odds/constants/ladder_map.py](../myanmar_odds/constants/ladder_map.py), [myanmar_odds/utils/handicap_parser.py](../myanmar_odds/utils/handicap_parser.py) | Handicap parsing and ladder labels | Myanmar odds service |
| Offline CSV helper | [app/filter_odds.py](../app/filter_odds.py), lines 1-112 | Reads `E0.csv`, selects Bet365 CSV columns, writes a JSON file | Only its `__main__` block; not referenced by production application code |
| Standalone phase copies | [odds_identity_phase5_docker.py](../odds_identity_phase5_docker.py), [odds_service_phase5_docker.py](../odds_service_phase5_docker.py), [odds_sync_service_phase5_docker.py](../odds_sync_service_phase5_docker.py) | Separate root-level Odds implementations/helpers | No production imports found; not the active `app.*` flow |

There is no Odds-specific Pydantic response schema on the GET route. No Flutter/Dart files were found in this repository.

## 4. Actual Architecture Diagram

```text
External API-Football
        |
        | GET /odds?fixture=<provider_fixture_id>
        v
FootballAPIClient -> OddsProvider
                           |
                           v
                   OddsSyncService
                    |           |
         normalization via       +--> AnalyticsProjectionService
          OddsService                   -> AnalyticsOddsRepository
                    |
                    v
              OddsRepository
                    |
                    v
 PostgreSQL: odds (fixture_id = local_match_id)

Read:
GET /api/matches/{local_match_id}/odds
  -> _assert_match_allowed
  -> FootballAPIService.get_cached_odds
  -> OddsService.get_cached_odds
  -> Redis read; on miss -> PostgreSQL Odds rows
  -> response dict

Commit/lock/cache invalidation:
  admin API route OR scheduler owns the outer transaction and lock lifecycle.
```

**Assessment:** The provider is transport-oriented and the repository contains SQL operations. The main write path passes through `OddsSyncService`, while the outer API/scheduler owns commit and rollback. Market normalization is implemented on the read/domain `OddsService` and called by the sync orchestrator. The additional analytics snapshot write path is inconsistent with the actual source rows, and commit-unknown recovery in the scheduler is not implemented by the current sync service.

## 5. End-to-End Data Flow

### 5.1 Match selection and identity handoff

- **Admin path:** `POST /api/matches/sync/{match_id}/odds` calls `_assert_match_allowed` using the local ID, obtains `match.provider_fixture_id`, then calls `refresh_odds(..., provider_fixture_id, ..., local_match_id=match_id)`. Evidence: [app/api/matches.py](../app/api/matches.py), lines 380-398.
- **Scheduled path:** `_refresh_odds_job` selects `Match.match_id`, `Match.provider_fixture_id`, status, and time, then passes the two identities separately to `refresh_odds`. Evidence: [app/services/scheduler.py](../app/services/scheduler.py), lines 813-821 and 867-873.
- **Provider call:** `OddsProvider.get_match_odds` passes `{"fixture": match_id}` to the HTTP client at `/odds`. The `match_id` parameter at this boundary is the provider fixture ID. Evidence: [app/providers/odds_provider.py](../app/providers/odds_provider.py), lines 8-15.
- **Normalization/persistence:** `OddsSyncService` compares each returned `fixture.id` to the requested provider ID, maps accepted rows to `local_match_id`, then calls `replace_fixture_odds` with that local ID. Evidence: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 45-113.
- **Read path:** `GET /api/matches/{match_id}/odds` checks that the local Match is allowed, calls the read facade/service and returns its dict directly. Evidence: [app/api/matches.py](../app/api/matches.py), lines 301-312; [app/services/football.py](../app/services/football.py), lines 248-250.

### 5.2 Inputs, transformations, and side effects

| Transition | Input/output and transformation | Side effects/errors |
|---|---|---|
| Match → provider request | Local ID is resolved to `Match.provider_fixture_id` at the API/scheduler entry; provider expects the provider fixture ID. No team-name/date matching is used. | There is no provider-ID lookup or local Match resolution inside `OddsSyncService`; its optional `local_match_id` defaults to `fixture_id` if omitted. |
| Provider → normalizer | Client returns `FootballAPIResponse` on the production path; sync checks status/error fields and requires a dict containing `response`. It then filters fixture ID and bookmaker ID 11. | HTTP errors are classified as request/API errors; malformed row shapes can raise during `.get`/iteration and are handled by outer transaction owners. |
| Normalizer → main rows | Raw odds text is parsed as `float` for selection, then original text, provider bet name and provider selection text are kept. Incomplete 1X2/line pairs are discarded; a single main OU/AH line is selected. | No schema-level numeric validation; no raw provider payload is stored. |
| Main rows → persistence | Each accepted row gets local `fixture_id` and current UTC `last_updated`. Repository deletes existing rows for that local match, then upserts the new set. | `flush` is inside sync service; commit/rollback is outside it. |
| Main rows → analytics | `project_odds` filters source rows independently and may replace a second table. | Its allowed bookmaker and market identifiers do not match the main Odds normalizer output; see sections 9 and 18. |
| Database → API | On Redis miss, reads all Odds rows by local `fixture_id`; serializes bookmaker, market, selection, odd string, Myanmar label and timestamp. | Empty rows return HTTP 200 with an empty `odds` array. Missing/unauthorized Match returns 404. |

## 6. Provider and API-Football Integration

- **Base URL:** default is `https://v3.football.api-sports.io` in [app/core/config.py](../app/core/config.py), line 19; environment may override it. Credentials are not reproduced here.
- **Endpoint/method/query:** `GET /odds` with one query parameter, `fixture=<provider_fixture_id>`. There is no bookmaker or bet filter, pagination, date filter, batch request, or live flag in the source call. Evidence: [app/providers/odds_provider.py](../app/providers/odds_provider.py), lines 12-15.
- **Metadata path:** The production `FootballAPIClient` has `get_with_metadata`, so `OddsProvider` selects that method via `hasattr`; the protocol type only declares `get` and `post`. Evidence: [app/providers/base/football_api_client.py](../app/providers/base/football_api_client.py), lines 1-10; [app/services/base/football_client.py](../app/services/base/football_client.py), lines 96-166.
- **Timeout/retry:** default timeout is 30 seconds and `retries=2` means at most three HTTP attempts per logical call. Metadata requests retry exceptions/timeouts and 5xx responses; 4xx responses (including 429) return without retry. There is no backoff. Evidence: [app/services/base/football_client.py](../app/services/base/football_client.py), lines 57-93 and 96-158.
- **Provider response validation:** Sync checks `FootballAPIResponse` status/error fields, requires a dict with `response`, checks that the response is nonempty, and compares nested fixture IDs. It does not validate the complete response shape or validate all odds before processing. Empty response produces `reason: no_data`; a mismatched fixture is skipped rather than reported as a distinct identity error. Evidence: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 55-90.
- **Call triggers in source:** There is one production call site to `OddsProvider.get_match_odds`, inside `OddsSyncService.refresh_odds`. Two entry paths can reach it: the admin POST route and `_refresh_odds_job`. The public GET does not call it. Evidence: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), line 55; [app/api/matches.py](../app/api/matches.py), lines 380-398; [app/services/scheduler.py](../app/services/scheduler.py), lines 797-873.
- **Other Odds-related paths:** `FootballAPIService.get_match_odds` delegates to `OddsService.get_match_odds`, but that method returns `{"error": "Read-only odds access"}` and makes no provider request. It is not the public GET implementation. Evidence: [app/services/odds_service.py](../app/services/odds_service.py), lines 219-220; [app/services/football.py](../app/services/football.py), lines 237-238.

No provider request was made; endpoint behavior, actual provider response shape, quotas, and live API error behavior remain source-level only.

## 7. Match Identity Mapping

- `Match.local_match_id` is the local primary key; `Match.provider_fixture_id` is a separate integer; `(provider, provider_fixture_id)` is unique. Legacy synonyms `match_id` and `fixture_id` map to local ID and provider ID respectively. Evidence: [app/models/match.py](../app/models/match.py), lines 8-20.
- `Odds.fixture_id` references `matches.local_match_id`, not the provider fixture ID, in the current model. Evidence: [app/models/odds.py](../app/models/odds.py), line 11.
- The initial migration linked `odds.fixture_id` to old `matches.fixture_id`. Match-identity migration later separated local/provider identity, and the Odds FK repair migration checks for orphan Odds rows before creating the FK to `matches.local_match_id`. Evidence: [alembic/versions/0001_initial.py](../alembic/versions/0001_initial.py), lines 112-125; [alembic/versions/20260910_separate_match_identity.py](../alembic/versions/20260910_separate_match_identity.py), lines 20-66; [alembic/versions/20260911_repair_odds_match_fk.py](../alembic/versions/20260911_repair_odds_match_fk.py), lines 16-68.
- Entry paths obtain the local Match from the local ID and pass its provider fixture ID to the provider. There is no odds identity association by team names/date or Match Master table in this flow.
- `OddsSyncService` itself does not fetch/resolve the Match or validate the local ID. Its default `local_match_id = fixture_id` is a risky fallback for any direct caller that omits the keyword; both currently found production callers pass it explicitly. A fixture mismatch in returned payload rows is silently skipped.
- Missing local Match: read route’s `_assert_match_allowed` returns 404. Scheduler selects existing Match rows. A direct sync-service call has no corresponding local Match guard; the FK only catches an invalid local ID at flush/commit.
- Current deployed FK/row integrity was not queried. Orphan presence is **NOT VERIFIED**.

## 8. Database Schema and Constraints

### 8.1 Main `odds` table

Schema evidence: [app/models/odds.py](../app/models/odds.py), lines 8-28; initial and follow-up migrations listed above.

| Column | Type/nullability | Meaning |
|---|---|---|
| `id` | Integer, primary key, non-null | Row identity |
| `fixture_id` | Integer, non-null, indexed; FK to `matches.local_match_id` in current model | Canonical local match ID |
| `bookmaker_name` | `VARCHAR(255)`, nullable | Provider bookmaker display name |
| `market_name` | `VARCHAR(255)`, non-null, indexed | Raw provider bet name |
| `selection` | `VARCHAR(255)`, non-null, indexed | Raw/trimmed selection text |
| `odd_value` | `VARCHAR(50)`, non-null | Original odd string |
| `myanmar_odd` | `VARCHAR(20)`, nullable | Optional Myanmar ladder label; added by a later migration |
| `last_updated` | timezone-aware timestamp, non-null, server default now/on update now | Local persistence/update time |

The business unique constraint is `(fixture_id, bookmaker_name, market_name, selection)`; indexes exist on fixture, market and selection. There is no numeric/check constraint for odds, no line/handicap column, no provider fixture ID/source column, no provider market ID, no bookmaker ID, no odds-source timestamp, and no pre-match/live discriminator. Since `bookmaker_name` is nullable, PostgreSQL's normal unique semantics do not prevent multiple rows where that key component is `NULL`; the current normalizer usually supplies a name, but does not reject an empty name.

### 8.2 Separate analytics table

`analytics_match_odds_snapshots` is a second table with BigInteger identity, local match FK, non-null bookmaker/market/selection, `NUMERIC(12,6)` odd, optional Myanmar text, source provider and timestamps; it has a unique business key and match/market indexes. Evidence: [app/models/analytics.py](../app/models/analytics.py), lines 65-80, and [alembic/versions/20260824_create_analytics_v1.py](../alembic/versions/20260824_create_analytics_v1.py), lines 77-87. It is not the table read by the public Odds endpoint.

### 8.3 Schema-based example (hypothetical, not database evidence)

One main-table record could be:

```text
odds(fixture_id=<local_match_id>,
     bookmaker_name="1xBet",
     market_name="Match Winner",
     selection="Home",
     odd_value="1.80",
     myanmar_odd=NULL,
     last_updated=<database timestamp>)
```

This illustrates the source/schema representation only; no current database row was read.

## 9. Supported Markets and Bookmakers

The production normalizer explicitly selects bookmaker ID `11`, and the code treats this as 1xBet. It retains the provider bookmaker `name` (default text `"1xBet"` only if the key is absent). No bookmaker ID is stored. Evidence: [app/services/odds_service.py](../app/services/odds_service.py), lines 162-168 and 210-216.

| Market | Current source behavior | Status |
|---|---|---|
| 1X2 / Match Winner | Provider bet ID 1; requires exact normalized `home`, `draw`, and `away` outcome labels; emits all three. | VERIFIED |
| Asian Handicap | Bet ID 4; selection text must parse as `Home <line>` and `Away <line>`; requires a matched pair for the same canonicalized signed line and chooses the pair whose average price is closest to 2.0. The line is embedded in `selection`. | VERIFIED |
| Goals Over/Under | Bet ID 5; requires a matched over/under pair for a line and selects the pair whose average price is closest to 2.0. | VERIFIED |
| Corners Over/Under | Bet ID 45 is selected with the same line-pair algorithm, but the Myanmar-label helper only accepts exact names `"Asian Handicap"` and `"Goals Over/Under"`. | PARTIALLY VERIFIED |
| Other markets | Not selected by `_is_target_market`. | NOT IMPLEMENTED |
| Provider/market identity normalization | Main table stores provider market name and selection strings, not IDs/canonical market enums. | VERIFIED |

Odds are parsed to float for filtering/ranking but stored as the provider's original text string. No explicit positive/finite check exists in the active parser. The 1X2 favorite used for Myanmar orientation is inferred from the lower home/away prices; equal prices fall through to `"AWAY"` in that derivation. Evidence: [app/services/odds_service.py](../app/services/odds_service.py), lines 123-216.

The analytics projection accepts only bookmaker `"bet365"` and canonical market strings `MATCH_WINNER`, `ASIAN_HANDICAP`, `GOALS_OVER_UNDER`, `CORNERS_OVER_UNDER`. Those differ from the active 1xBet/raw-name output, so current incoming rows normally filter to zero and the projection returns/preserves existing snapshots. Evidence: [app/services/analytics_projection_service.py](../app/services/analytics_projection_service.py), lines 249-274.

## 10. Synchronization Lifecycle

```text
Admin POST or six-hour scheduler
  -> obtain (local_match_id, provider_fixture_id)
  -> acquire "odds:<local_match_id>" PostgreSQL transaction advisory lock
  -> GET /odds?fixture=<provider_fixture_id>
  -> classify client/API errors
  -> select fixture response, bookmaker ID 11 and supported main lines
  -> [if rows] delete main Odds snapshot + upsert selected rows
  -> flush main Odds rows
  -> analytics projection attempt
  -> outer owner commits or rolls back
  -> after commit, delete fover:match:<local_match_id>:odds
```

### Scheduler selection/cadence

- Job ID `refresh_odds` is registered every 360 minutes (`max_instances=1`). The scheduler is started by `worker.py`; docker-compose config sets API `SCHEDULER_ENABLED=false` and worker `SCHEDULER_ENABLED=true`, but actual process/runtime status was not checked. Evidence: [app/services/scheduler.py](../app/services/scheduler.py), lines 194-284; [docker-compose.yml](../docker-compose.yml), lines 1-55; [worker.py](../worker.py), lines 21-34.
- Candidate status is `NS`, `TBD`, or `PST`; matches must be from now through 72 hours ahead. Live/finished/terminal stop statuses are excluded. Per-match Odds newer than 12 hours is skipped. Evidence: [app/services/scheduler.py](../app/services/scheduler.py), lines 46-49 and 813-865.
- The manual admin endpoint has no corresponding upcoming-only or status eligibility check; the scheduler’s 72-hour rule does not apply to it.
- Scheduler `max_instances=1` is process-local. Both manual and scheduled refreshes additionally use a transaction-scoped PostgreSQL advisory lock keyed by local match ID. Evidence: [app/services/resource_lock.py](../app/services/resource_lock.py), lines 12-64; API lines 431-438; scheduler lines 895-905.

### Write and empty/partial behavior

- With at least one selected row, all existing main-table rows for the local match are deleted and the new list is upserted. Thus incomplete/missing markets disappear on a partial response if at least one other market survives normalization.
- If the provider returns no `response`, no matching fixture, no bookmaker 11, or no complete supported lines, `replace_fixture_odds` is not called; existing main-table rows are retained.
- `OddsRepository.replace_fixture_odds(..., rows=[])` would delete all rows, but the current sync path does not call it with an empty list. Evidence: [app/repositories/odds_repository.py](../app/repositories/odds_repository.py), lines 34-60; [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 75-120.
- No durable retry queue/state exists. Scheduler failures are counted/logged and become eligible for another attempt on a later job cycle if match and age rules still permit it. Manual failures are returned to the caller; no automatic retry is implemented there beyond HTTP client retries.
- Odds sync writes the main Odds table and analytics Odds snapshot only; it does not update Match or other Master data.

## 11. Transactions, Locks, and Failure Recovery

| Case | Current behavior |
|---|---|
| Normal admin sync with `status: SUCCESS` | Route commits; then tries cache deletion. Cache invalidation failure is logged and returned as `cache_invalidated: false`; no durable retry state is added by this route. |
| Admin provider/API error or result without `status: SUCCESS` | Route rolls back and returns HTTP 502. |
| Scheduler provider error (`"error"` key) | Scheduler rolls back and counts failure. |
| Scheduler empty/no usable result without `"error"` | It proceeds to commit and post-commit invalidation; because result lacks `"error"` it is counted as refreshed even though no main Odds rows were replaced. |
| Database flush/projection exception | Propagates to outer owner; admin rolls back, scheduler exception handler rolls back. |
| Concurrent lock conflict | Admin returns 409; scheduler skips the match. Lock acquisition errors are logged and treated as lock unavailable. |
| Scheduler cache invalidation failure | In-memory `pending_odds_cache_invalidations` retains local match ID and scheduler retries deletion when it next processes that match. It is not durable across process restart. |
| Ambiguous scheduler commit | `commit_odds_refresh` calls `odds_sync_service.is_commit_ambiguous()` and `verify_commit_unknown()`, but neither method exists in the current [app/services/odds_sync_service.py](../app/services/odds_sync_service.py) (entire implementation ends after `refresh_odds`). The helper's intended verification cannot execute; source does not establish safe ambiguous-commit recovery. |
| Admin ambiguous commit | Direct route commit has no corresponding snapshot verification; generic exception path attempts rollback and re-raises. |

`OddsSyncService` itself only flushes and returns a result; it does not commit. The route and scheduler own the transaction boundary, in line with the frozen responsibility pattern. Scheduler `commit_odds_refresh` references `_transaction_context`, but current `refresh_odds` does not populate it. This is an additional mismatch in the intended commit-verification path.

## 12. Cache and Read API

- **GET endpoint:** `GET /api/matches/{match_id}/odds`; this is local Fover match ID. It first calls `_assert_match_allowed`, then reads via `FootballAPIService.get_cached_odds` → `OddsService.get_cached_odds`. Evidence: [app/main.py](../app/main.py), lines 88-89; [app/api/matches.py](../app/api/matches.py), lines 301-312; [app/services/football.py](../app/services/football.py), lines 248-250.
- **Cache key:** `fover:match:<local_match_id>:odds` with default prefix, because `make_cache_key` prepends `REDIS_CACHE_PREFIX`. Evidence: [app/core/config.py](../app/core/config.py), line 36; [app/cache.py](../app/cache.py), lines 21-24.
- **Cache hit:** returns cached JSON immediately, before checking the Match exists or reading PostgreSQL.
- **Cache miss:** checks Match by local ID, reads rows from `odds`, returns serialized records. It does not set Redis after this read. `REDIS_TTL_ODDS=3000` is defined but not used by this read path; the `pre_match_ttl` refresh parameter is also unused.
- **Cache invalidation:** admin route invalidates after commit; scheduler invalidates after commit and has process-memory recovery state. Cache get failures are converted to misses and the DB read proceeds. Evidence: [app/cache.py](../app/cache.py), lines 100-142; [app/services/odds_service.py](../app/services/odds_service.py), lines 222-254; [app/services/scheduler.py](../app/services/scheduler.py), lines 122-140.
- **Provider on miss:** none. Public GET is read-only with respect to provider and database.
- **Staleness:** no Odds read-through/write-through TTL is applied here. If an existing key was written by another path or an older version, it may be returned until expiry or successful invalidation. Failed admin invalidation has no local retry queue.
- **Missing data:** existing Match with no Odds rows returns HTTP 200 and an empty list; absent/not-allowed Match returns 404.

### Hypothetical response shape (not a live API response)

```json
{
  "source": "database",
  "odds": [
    {
      "bookmaker": "1xBet",
      "market": "Match Winner",
      "selection": "Home",
      "odd": "1.80",
      "myanmar_odd": null,
      "updated_at": "<timestamp>"
    }
  ],
  "cached": true,
  "match_started": false
}
```

The shape follows the serializer at [app/services/odds_service.py](../app/services/odds_service.py), lines 236-254. Values are illustrative, not observed database data. There is no explicit `match_id` in this GET response.

## 13. Flutter Integration

**BACKEND-ONLY AUDIT.** No `.dart` files or Flutter package were found in this repository, so endpoint use, JSON model fields, repository/state-management flow, UI behavior, and bookmaker/market selection on the Flutter side are **NOT VERIFIED**.

Evidence required for a follow-up: Flutter source repository/workspace; API client and odds model; match detail screen/provider code; and a redacted response captured from an already available test environment (without triggering provider sync).

## 14. Myanmar Odds Conversion

Conversion is implemented in the backend, not observed in a frontend implementation. `OddsService` applies it only to paired Asian Handicap ID 4 and Goals Over/Under IDs 5/45 when the raw provider market name exactly matches `"Asian Handicap"` or `"Goals Over/Under"`. It writes the resulting string label in nullable `odds.myanmar_odd`; raw decimal odds remain in `odd_value`. The conversion uses a handicap base-index map, difference in hundredths, and ladder labels; out-of-range labels are represented as `"OUT_OF_RANGE"`. Evidence: [app/services/odds_service.py](../app/services/odds_service.py), lines 73-120 and 136-159; [myanmar_odds/services/myanmar_odds_service.py](../myanmar_odds/services/myanmar_odds_service.py), lines 13-126.

This is a derived display label, not a replacement for raw odds. There is no Myanmar conversion for 1X2; corners over/under is partially supported in the selector but does not pass the current exact-name gate for Myanmar labeling.

## 15. Existing Test Coverage

### Executed safely

- `myanmar_odds/tests/test_myanmar_odds.py` and `tests/test_odds_service_myanmar_sync.py`: **95 passed**, three Pydantic deprecation warnings. The Odds service tests use fake database/cache/client objects; no real provider, Redis, or DB was called. Covered conversion, line selection, and mocked read-path behavior.
- No other test suite was run.

### Inspected but not run

- `tests/test_odds_lock_alignment.py`: mocks provider/DB/lock/cache for scheduler lock ordering and admin transaction behavior.
- `tests/test_odds_phase3_hardening.py`: includes DB-backed tests that create Match/Odds records and transaction state; intentionally not run under this audit's no-database-mutation rule.
- `tests/test_odds_identity_mapping.py` imports `app.services.odds_identity`, which is absent from the active `app/services` tree. Similar normalization code exists in root-level `odds_identity_phase5_docker.py`, not imported by the application.
- `tests/test_odds_identity_resolution_bridge.py` imports failure-state constants and methods not present in current `app/services/odds_sync_service.py`; its asserted stricter empty/partial snapshot, ID validation, and commit recovery behavior is not evidence of current runtime behavior.
- `tests/test_api.py` has route/cache-related tests, but this broad suite was not run.

### Coverage matrix

| Behavior | Existing evidence |
|---|---|
| Myanmar conversion and odds-line selection | Executed mocked/pure tests |
| Provider response parsing and strict malformed-data validation | Partial/stale tests; current active code lacks deep validation; dedicated identity test imports missing module |
| Match identity resolution | Some test intent exists, but bridge suite targets absent/currently different interfaces; production caller handoff verified from source |
| Insert/update and duplicate prevention | DB-backed hardening test exists but not run; DB unique constraint and repository upsert verified from source |
| Transaction rollback/cache invalidation | Mocked lock tests exist; DB-backed tests not run |
| Concurrent synchronization | Lock alignment has mocks; no concurrency execution performed |
| API serialization/missing Odds | Mocked read-path tests exist; no HTTP request was made |
| Bookmaker/market mapping | Unit tests cover selector behavior; production mapping verified from source |
| Myanmar odds conversion | Executed pure tests |
| Current production database integrity | Not verified |

## 16. Data-Integrity Findings

**Current data verification: NOT VERIFIED.** No read-only database connection was established. No duplicate, orphan, stale-timestamp, or invalid-value query was run.

Source-level protections and risks:

- Main Odds rows have a composite unique key and local Match FK in the current model; repository also deletes/replaces and upserts. These are source/schema facts, not confirmation of deployed constraints.
- Nullable `bookmaker_name` weakens duplicate protection for rows with null bookmaker under PostgreSQL unique semantics. Active mapping normally obtains a provider name, but does not validate it as nonempty.
- Odd values are `VARCHAR(50)` and the normalizer checks parseability for line calculations but does not reject nonpositive/nonfinite values before persistence. No DB check constraint exists in inspected migrations/model.
- `selection` contains line identity as free text; the unique key depends on exact string normalization and market-name spelling.
- A partial nonempty provider snapshot deletes the complete previous Odds snapshot before persisting only selected lines. This is intentional replace behavior in code, but an incomplete response can remove previously available markets.
- An empty/unusable response retains old rows and does not update `last_updated`; stale rows may therefore remain visible, with no source timestamp to distinguish them.
- `20260911_repair_odds_match_fk` checks orphan count before creating the FK, but no evidence confirms that migration ran against the current database.

## 17. API Credit Consumption Findings

### A. Calls verified in source

One logical provider call occurs at `OddsSyncService.refresh_odds` → `OddsProvider.get_match_odds` → HTTP `GET /odds?fixture=<provider_fixture_id>`. It is reachable through:

1. `POST /api/matches/sync/{match_id}/odds` (admin-protected).
2. Scheduler job `refresh_odds`.

The public Odds GET, Match synchronization method, and offline CSV helper do not make this Odds endpoint call in the inspected application flow.

### B. Calls observed in existing runtime logs

**NOT VERIFIED.** No runtime logs or API-Football dashboard evidence were inspected; no actual calls or credit usage are claimed.

### C. Frequency estimate from source configuration

- Scheduler interval: 6 hours (four job runs per 24 hours maximum by cadence).
- Target: future matches within 72 hours with status NS/TBD/PST.
- Existing Odds younger than 12 hours are skipped. In a stable success case this tends toward about two refreshes per match per day; if requests keep failing and the row timestamp remains old, the job may retry the same eligible match each six-hour cycle (up to four logical calls/day while it remains eligible).
- Each logical request can make up to three HTTP attempts due to two retries for timeout/exception/5xx; 429 is returned without retry on the metadata path.
- Admin calls can add further sequential requests; the lock only prevents overlapping work for the same local match ID. No source-level rate limiter, cross-request dedupe, or provider response cache suppresses those calls.

These are source/config-based upper-bound scenarios, not measured usage. The scheduler being configured does not prove it was running at audit time.

### D. Not verified

Actual scheduler uptime, eligible-match counts, real retries, provider response volume, quota/credit consumption, and API-Football account plan remain **NOT VERIFIED**.

## 18. Architecture Deviations

1. **Mostly follows the frozen core.** Provider handles transport, sync service orchestrates writes, repository owns SQL, and API/scheduler own transactions. Local/provider identities are separated at production entry points.
2. **Normalization sits on the read/domain service.** `OddsSyncService` delegates market parsing and Myanmar labels to `OddsService`; this is workable but means a service named for read/cache logic also owns provider-market normalization.
3. **Parallel analytics write path disagrees with Odds input.** Analytics projection is invoked from Odds sync but recognizes Bet365/canonical names while primary normalization produces 1xBet/raw names. This can preserve old analytics data while main Odds rows change.
4. **Commit verification is out of sync.** Scheduler's ambiguous commit handler calls absent methods and expects transaction context not produced by the current sync result.
5. **Repository has non-runtime alternatives.** Root phase copies and the CSV helper are not wired into application imports. They should not be treated as the active architecture or proof of live behavior.

## 19. Known Limitations and Unverified Areas

- No live PostgreSQL schema/data inspection; actual constraints, row validity, orphan count and freshness unknown.
- No live Redis inspection; existing key population, TTLs, source writers, and stale cache contents unknown.
- No runtime logs or provider account evidence; actual API calls/credits unknown.
- No provider request was executed; real API response/version behavior unknown.
- No Flutter source; endpoint consumption and display behavior unknown.
- Full API, lock, and DB-backed tests not run.
- The current implementation and some checked-in identity/bridge tests have interface/behavior drift; source—not stale test expectations—was used as the implementation evidence.

## 20. Findings Prioritized by Severity

| Priority | Finding | Evidence | Impact | Confidence |
|---|---|---|---|---|
| HIGH | Empty/no-usable provider data leaves prior Odds rows intact, while scheduled path can count the result as refreshed; admin instead maps the no-status result to 502. | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 75-120 and [app/services/scheduler.py](../app/services/scheduler.py), lines 867-923; [app/api/matches.py](../app/api/matches.py), lines 394-404 | Consumers may see stale odds as current; scheduler metrics/cache handling can imply a successful refresh when no Odds snapshot changed. | VERIFIED |
| HIGH | Analytics projection filters incoming live Odds by Bet365/canonical keys, but active sync emits bookmaker ID 11/1xBet and provider market names. Zero-match projection preserves prior analytics rows. | [app/services/odds_service.py](../app/services/odds_service.py), lines 162-216; [app/services/analytics_projection_service.py](../app/services/analytics_projection_service.py), lines 249-274 | Analytics snapshots may not track the main Odds table and may remain stale without an explicit projection failure. | VERIFIED |
| HIGH | Scheduler ambiguous-commit recovery references missing `OddsSyncService.is_commit_ambiguous` and `verify_commit_unknown`; expected `_transaction_context` is also absent from refresh result. | [app/services/scheduler.py](../app/services/scheduler.py), lines 64-120; [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 45-140 | Commit outcome may remain uncertain; intended verification cannot run and cache invalidation may not follow safely. | VERIFIED |
| MEDIUM | Provider call load can reach four scheduled logical requests per eligible match/day on repeated failures, each with up to three HTTP attempts; admin can initiate more. | [app/services/scheduler.py](../app/services/scheduler.py), lines 46-49, 245-251, 813-873; [app/services/base/football_client.py](../app/services/base/football_client.py), lines 96-158 | Potential elevated API-Football credit consumption; actual use unmeasured. | VERIFIED (source); runtime NOT VERIFIED |
| MEDIUM | Main Odds has no numeric/check constraint and active normalizer does not explicitly reject zero, negative, NaN, or infinite numeric strings. | [app/services/odds_service.py](../app/services/odds_service.py), lines 199-207; [app/models/odds.py](../app/models/odds.py), lines 11-17 | Invalid/unexpected odds may be persisted as strings and served unchanged. | VERIFIED |
| MEDIUM | Nonempty but partial normalized response replaces the entire previous match snapshot; empty/unusable result retains all old rows without refreshing timestamps. | [app/repositories/odds_repository.py](../app/repositories/odds_repository.py), lines 52-60; [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 75-120 | Partial data can remove valid prior markets; empty data can preserve stale rows without a freshness marker change. | VERIFIED |
| LOW | Odds cache TTL setting and `pre_match_ttl` parameter are unused by the current GET/read code; DB reads are not cached. | [app/core/config.py](../app/core/config.py), line 37; [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 45-53; [app/services/odds_service.py](../app/services/odds_service.py), lines 222-254 | Cache behavior may differ from configuration names or older tests/docs; only externally present values can be cache hits. | VERIFIED |
| LOW | Optional local ID fallback equals provider fixture ID if a caller omits `local_match_id`. | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 45-55 | A future/direct caller could persist provider identity into a local-ID column; current two production call paths pass the local ID. | VERIFIED |
| INFO | Live DB integrity, Redis state, provider logs/credits, and Flutter screen behavior were not inspected. | Scope and limitations above | These acceptance areas remain unverified; no runtime claims should be inferred from source alone. | NOT VERIFIED |

## 21. Recommended Next Audit Phase

Without changing implementation, the next phase should use an explicitly read-only database role/transaction to inspect deployed schema constraints and aggregate integrity counts; inspect Redis key metadata without changing values; review already-retained, redacted worker/API logs and provider quota records; and trace the Flutter client in its source repository. Resolve the active-vs-stale test/source interface discrepancy by identifying the intended checked-in tests before relying on them as acceptance evidence. Any such runtime inspection should preserve the no-sync/no-provider-call boundary.

## Final Verdict

- **AUDIT STATUS:** PARTIAL.
- **ARCHITECTURE STATUS:** DEVIATIONS FOUND.
- **DATA INTEGRITY:** NOT VERIFIED.
- **PROVIDER CALL CONTROL:** RISKS FOUND.
- **FRONTEND INTEGRATION:** NOT VERIFIED.
- **IMPLEMENTATION CHANGES:** NONE.
