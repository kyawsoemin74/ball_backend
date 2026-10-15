# Odds Backend Source Code Path & Flow Audit

**Audit type:** Static source inspection only  
**Scope:** Active Odds provider, synchronization, persistence, read/API, transaction, and Myanmar conversion paths  
**Runtime/provider/database execution:** None

## 1. Actual Architecture

The active implementation uses these modules:

| Layer | Actual code | Role in active Odds flow |
|---|---|---|
| HTTP transport | `app/services/base/football_client.py:57-166` | Sends the API-Football request and returns metadata response in production. |
| Provider | `app/providers/odds_provider.py:6-15` | Calls `/odds` with provider fixture query. |
| Domain selection/read service | `app/services/odds_service.py:17-254` | Filters bookmaker/markets/main lines, computes eligible Myanmar labels, and reads stored Odds. |
| Sync orchestration | `app/services/odds_sync_service.py:27-140` | Parses provider envelope, maps local identity, invokes persistence and analytics projection. |
| Persistence | `app/repositories/odds_repository.py:8-67` | Selects, deletes, upserts and replaces rows in `odds`. |
| Entry points/transaction owners | `app/api/matches.py:380-438`; `app/services/scheduler.py:797-933` | Starts refresh, holds lock, commits/rolls back, invalidates cache. |
| Read facade/API | `app/services/football.py:248-250`; `app/api/matches.py:301-312` | Reads stored/cached snapshot and returns it. |
| Additional projection | `app/services/analytics_projection_service.py:249-274` | Writes a separate Analytics snapshot from the same sync operation. |

The provider and repository boundaries exist. Market normalization is implemented in `OddsService` and directly invoked by `OddsSyncService`; the sync service is the active write orchestrator. PostgreSQL transaction ownership is outside the sync service, in the API route and scheduler.

## 2. Entry Point Map

### 2.1 Admin/API-triggered sync

```text
POST /api/matches/sync/{match_id}/odds
  app/api/matches.py::sync_match_odds_route
    -> _assert_match_allowed(local match ID)
    -> read Match.provider_fixture_id
    -> run_with_resource_lock(db, "odds", local match ID, sync)
    -> football_service.odds_sync_service.refresh_odds(
           db, provider_fixture_id, ..., local_match_id=local match ID)
    -> commit on status SUCCESS; rollback otherwise
    -> CacheService.delete(cache key) after commit
```

Evidence:

- Router prefix is `/matches` at `app/api/matches.py:30`; `app/main.py:88` mounts it under `/api`.
- Local Match and provider ID handoff: `app/api/matches.py:381-398`.
- Lock, commit, failure and cache invalidation: `app/api/matches.py:400-438`.
- Match guard uses `MatchRepository.get_by_id` and returns 404 if not found: `app/api/matches.py:202-207`.

### 2.2 Scheduler-triggered sync

```text
LiveUpdateScheduler.start
  -> registers _refresh_odds_job every 360 minutes (max_instances=1)

_refresh_odds_job
  -> select eligible local Match ID + provider_fixture_id
  -> skip if newest Odds < 12 hours old
  -> per-local-match advisory lock
  -> football_service.odds_sync_service.refresh_odds(
         provider_fixture_id, local_match_id=local ID)
  -> commit_odds_refresh
  -> invalidate cache after reported commit
  -> increments refreshed/failed/skipped metric
```

Evidence: `app/services/scheduler.py:194-253` and `797-933`. Candidate statuses and 72-hour query are at lines 46-49 and 808-821.

The APScheduler `max_instances=1` is scheduler-process level. `run_with_resource_lock` also acquires a PostgreSQL transaction advisory lock keyed by local Match identity (`app/services/resource_lock.py:10-64`).

### 2.3 Read API

```text
GET /api/matches/{match_id}/odds
  app/api/matches.py::get_match_odds
    -> _assert_match_allowed
    -> football_service.get_cached_odds
    -> OddsService.get_cached_odds
    -> Redis value if present
       otherwise Match lookup + SELECT Odds by local fixture_id
    -> dict returned directly
```

Evidence: `app/api/matches.py:301-312`; `app/services/football.py:248-250`; `app/services/odds_service.py:222-254`.

The GET does not invoke the provider or `OddsSyncService`.

## 3. Write Flow

### 3.1 Provider request

`FootballAPIService.__init__` constructs one `OddsProvider` and wires it into `OddsService` and `OddsSyncService` (`app/services/football.py:112-115`). `OddsProvider.get_match_odds` calls:

```python
client.get_with_metadata("/odds", params={"fixture": match_id})
```

when metadata support exists; otherwise it calls `client.get` (`app/providers/odds_provider.py:12-15`). In the production client, metadata support exists. The HTTP client builds the endpoint from its configured base URL and uses GET with the given query (`app/services/base/football_client.py:96-166`). The provider parameter is the external fixture ID, not the local Fover Match ID.

The `FootballAPIClient` protocol declares only `get` and `post` returning optional dicts (`app/providers/base/football_api_client.py:1-10`), while the concrete client provides `get_with_metadata` returning `FootballAPIResponse`. The Provider return annotation says `Optional[dict]`, though its active production branch returns that metadata object. `OddsSyncService` handles both shapes at runtime. **INCOMPLETE: static typing contract does not describe the active metadata return shape.**

### 3.2 Response parsing and mapping

`OddsSyncService.refresh_odds` performs, in order (`app/services/odds_sync_service.py:45-120`):

1. Defaults `local_match_id` to the supplied provider `fixture_id` if caller omitted the keyword (`:54`). Both active callers pass `local_match_id` explicitly (`app/api/matches.py:392-398`; `app/services/scheduler.py:867-873`).
2. Calls `OddsProvider.get_match_odds(fixture_id)` (`:55`).
3. Handles `FootballAPIResponse` errors by returning an error/reason before persistence (`:56-69`).
4. Requires a dict with a `"response"` key (`:71-73`).
5. Returns a `"no_data"` result for an empty response (`:75-77`).
6. Iterates provider items, compares nested `fixture.id` to the requested provider ID, selects bookmaker ID 11 through `OddsService._get_1xbet_bookmaker`, and calls `_filter_main_lines` (`:81-90`).
7. Sets each normalized row’s `fixture_id` to the local Match ID (`:88-90`).
8. Copies only `fixture_id`, bookmaker name, provider market name, selection text, original odd string, Myanmar label, and local timestamp into persistence rows (`:93-107`).

No provider fixture ID or provider market/bookmaker numeric ID is persisted in main Odds rows. Odds remain strings; `odd_float` is an intermediate selector field and is dropped before persistence.

### 3.3 Active selection and transformations

`OddsService._get_1xbet_bookmaker` selects bookmaker ID `11` (`app/services/odds_service.py:165-166`). `_filter_main_lines` selects bet IDs `1`, `4`, `5`, `45` (`:162-163`, `:168-216`):

- Bet 1 requires Home, Draw, Away; it uses Home/Away odds to choose a favorite for Myanmar orientation.
- Bet 4 pairs Home/Away handicap selections by parsed signed line.
- Bets 5 and 45 pair Over/Under by line.
- For paired lines it selects the pair whose odds average is closest to 2.0 (`:123-160`).
- Selection text is trimmed. Odds are converted to `float` for line selection but original odd text is saved.
- Failed `float` parsing skips that value; no explicit finite/positive validation is present at this point.

Only one main line per supported paired market is retained; other available lines and unsupported bet IDs are dropped.

### 3.4 Persistence and analytics projection

When at least one row exists, `refresh_odds`:

1. Replaces main Odds rows for the local Match via `OddsRepository.replace_fixture_odds`.
2. Flushes the outer SQLAlchemy session.
3. Calls `AnalyticsProjectionService.project_odds` with the same persistence rows.
4. Raises if projection returns `success=False`.
5. Returns `status: SUCCESS` with response-shaped row values if processing completes.

When there are no normalized rows, it flushes, calls Analytics projection with an empty list, then returns a no-odds reason without `status: SUCCESS`.

`replace_fixture_odds` deletes every existing `odds` row for that Match, then `upsert_many` executes PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` for the unique constraint (`app/repositories/odds_repository.py:34-60`). Because deletion occurs before upsert, this is snapshot replacement rather than update-in-place for the existing rows. The unique constraint and upsert provide a second duplicate guard. Empty rows passed directly to the repository would delete all rows, but the active sync service does not call replacement when its normalized list is empty.

### 3.5 Analytics projection has no mapping from the active row vocabulary — INCOMPLETE

The active sync output uses bookmaker name from API-Football ID 11 (default `"1xBet"`) and raw market names such as `"Match Winner"` and `"Asian Handicap"` (`app/services/odds_service.py:165-216`). It passes those values unchanged to `project_odds` (`app/services/odds_sync_service.py:93-111`).

`project_odds` accepts bookmaker `"bet365"` and only canonical market names (`app/services/analytics_projection_service.py:249-253`). There is no mapping from provider bookmaker/market IDs or provider labels into those accepted values. Consequently, rows using the selected provider name and human-readable provider market names are filtered out. With no projected rows, it returns the existing analytics snapshot with reason `filtered_to_zero_preserved` (`:268-270`). `_result` computes `success` as `accepted == source_count` (`:124-130`); the zero-projected-row case passes `0 == 0`, so it succeeds and does not cause `OddsSyncService` to raise. The main Odds replacement remains eligible for outer commit, while Analytics is left unchanged. Whether every actual provider response has these names cannot be established without a provider response; the missing normalization bridge is directly visible in source.

**Classification: INCOMPLETE.** The write path does not map/validate its emitted values against the projection's accepted contract; common provider labels are filtered to zero and silently treated as a successful no-op. Exact projection counts depend on the provider response and are not established by source inspection alone.

## 4. Read Flow

### 4.1 Service read behavior

`OddsService.get_cached_odds(db, fixture_id)` (`app/services/odds_service.py:222-254`):

1. Builds key `fover:match:<local ID>:odds` by default prefix (`:223`; `app/cache.py:21-24`).
2. Returns any non-`None` cached JSON immediately (`:224-227`).
3. On miss, selects `Match` by local `match_id` (`:229-231`).
4. Returns `{"error": "Match not found"}` if absent (`:230-231`).
5. Selects `Odds` rows where `Odds.fixture_id == fixture_id` (`:234`).
6. Maps fields to `bookmaker`, `market`, `selection`, `odd`, `myanmar_odd`, and `updated_at` (`:236-247`).
7. Returns `{source: database, odds: [...], cached: true, match_started: ...}` (`:249-254`).

On a DB miss, the method does not set Redis. Cache failures in shared `cache_get_json` are converted to a miss, after which DB read continues (`app/cache.py:100-116`). No bookmaker/market filtering is applied on read. Match identity in the API path is the local Fover Match ID.

### 4.2 API route

`GET /api/matches/{match_id}/odds` is mounted by `app/main.py:88`, with local router prefix at `app/api/matches.py:30`. It checks that the Match is allowed, then returns the service dict unless it contains `"error"`, in which case it raises 404 (`app/api/matches.py:301-312`).

The returned structure includes no explicit `match_id`. The source comment at `app/api/matches.py:305` says “Fallback to Service logic (DB check -> API fetch if needed),” but the actual call chain is the read-only `get_cached_odds` described above and does not fetch API-Football. **PASS for code behavior; stale/misleading comment.**

## 5. Execution Flow

### 5.1 Provider retries and exceptions

The concrete metadata HTTP client uses 30-second timeout and two retries by default (`app/services/base/football_client.py:96-102`). It retries exceptions/timeouts and 5xx status responses, but returns 4xx (including 429) without retry (`:127-158`). The Odds sync service maps metadata errors to result dictionaries (`app/services/odds_sync_service.py:56-73`).

Malformed nested structures are not comprehensively validated. For example, each response item and nested `fixture`/`bookmakers` is assumed to have dictionary-like shape (`:81-85`); unexpected types can raise. Such an exception propagates out of `refresh_odds`:

- API route catches generic exceptions, attempts rollback, logs, and re-raises (`app/api/matches.py:420-428`).
- Scheduler catches exceptions around each locked refresh, rolls back, increments failed metric, and logs (`app/services/scheduler.py:925-927`).

### 5.2 Empty/no-usable result reporting — FAIL in scheduler path

Empty provider response returns `reason: no_data`; nonempty response with no bookmaker or no selected main lines returns `reason: 1xbet_data_not_found` or `filtered_no_odds`. Neither result has `"error"` or `status: SUCCESS` (`app/services/odds_sync_service.py:75-77`, `:116-120`).

- **Admin route:** checks `status != SUCCESS`, rolls back and returns HTTP 502 (`app/api/matches.py:400-404`).
- **Scheduler:** treats only a result containing `"error"` as failure (`app/services/scheduler.py:874-876`). Empty/no-usable result therefore goes to `commit_odds_refresh`; after its `"refreshed"` return, scheduler invalidates cache and increments `refreshed_matches` (`:878-893`, `:907-923`). No main Odds replacement occurred for this result.
- `@observe_sync("odds")` classifies a dict as failure only when `success is False` or `"error"` exists. These empty/no-usable returns are recorded as sync success, not `no_data` failure (`app/monitoring.py:249-277`).

**Classification: FAIL.** The scheduler and metrics path report no-data as refreshed/success even though no main Odds data was replaced. This is a direct code-path contradiction; no runtime execution was performed.

### 5.3 Commit/rollback and ambiguous commit — FAIL on commit-error path

`OddsSyncService.refresh_odds` flushes but never commits. The API route commits after `SUCCESS` and rolls back for non-success/exception (`app/api/matches.py:400-428`). Scheduler uses `commit_odds_refresh` as the transaction owner (`app/services/scheduler.py:64-120`, call at `:878-883`).

On **any** `db.commit()` exception, `commit_odds_refresh` immediately calls:

```python
if not odds_sync_service.is_commit_ambiguous(exc):
```

It then calls `verify_commit_unknown` in the ambiguous case. Neither method exists in `app/services/odds_sync_service.py`; that module defines only four constants, `OddsTransactionFailure`, constructor, and `refresh_odds` (`app/services/odds_sync_service.py:17-140`). Therefore a commit exception makes the handler itself raise `AttributeError` before it can classify the failure or invoke verification. The scheduler outer exception handler later rolls back and records failure (`app/services/scheduler.py:925-927`), but cannot determine whether an ambiguous commit actually committed. The refresh result also does not populate `_transaction_context`, which the helper reads (`app/services/scheduler.py:66`; `app/services/odds_sync_service.py:128-133`).

**Classification: FAIL.** Concrete method-reference break in the commit-error path. No commit exception was induced or observed; actual DB outcome is unverified.

The `OddsTransactionFailure` class is declared and caught by the API route, but no active code in `OddsSyncService.refresh_odds` raises it. Its route exception branch is disconnected from the shown sync implementation.

### 5.4 Provider-failure classification also references a missing method — FAIL

When the scheduler receives an Odds result with `"error"`, it rolls back and returns `{"state": "failed", ...}` (`app/services/scheduler.py:874-876`). The caller then enters the `state == "failed"` branch and calls `football_service.odds_sync_service.classify_retry_state(reason)` (`:909-917`). No such method exists in `app/services/odds_sync_service.py:17-140`. Thus this error-reporting branch raises `AttributeError` while formatting the log. Since `failed_matches` is incremented before that expression and again in the surrounding exception handler at `:925-927`, the metric can be incremented twice for that one failed match, and the intended error log is replaced by the exception log.

**Classification: FAIL.** Direct unresolved method call in the active scheduler provider-error path. No provider failure was induced.

### 5.5 Cache invalidation

- Admin route deletes its odds key after successful commit; cache-delete exceptions are logged and response returns `cache_invalidated: false` (`app/api/matches.py:406-418`).
- Scheduler invalidates after commit through `invalidate_odds_cache_after_commit`; on failure it records local Match ID in in-memory `pending_odds_cache_invalidations` for a later retry (`app/services/scheduler.py:122-140`, `:839-858`, `:886-893`).
- Failed/empty admin sync does not invalidate because commit is not reached.

These cache calls are connected in the successful paths. Scheduler commit helper failure prevents the post-commit invalidation call.

## 6. Myanmar Odds Active Path

This conversion is connected to the active parser; it is not merely a standalone legacy helper:

```text
OddsSyncService.refresh_odds
  -> OddsService._filter_main_lines
  -> OddsService._select_main_line_by_id
  -> OddsService._attach_myanmar_odd_labels
  -> MyanmarOddsService.convert_to_myanmar_odds
  -> row["myanmar_odd"]
  -> persistence_rows["myanmar_odd"]
  -> OddsRepository upsert
  -> OddsService.get_cached_odds serializer
  -> GET response field "myanmar_odd"
```

Evidence: `app/services/odds_sync_service.py:85-107`; `app/services/odds_service.py:73-120`, `:136-159`, `:168-216`, `:236-247`; `myanmar_odds/services/myanmar_odds_service.py:56-126`.

The helper only attaches labels when exact provider market name is `"Asian Handicap"` or `"Goals Over/Under"` and market ID is in `{4,5,45}` (`app/services/odds_service.py:81-87`). Therefore:

- Asian Handicap ID 4 with exact expected provider name: conversion is called for a complete selected Home/Away line pair.
- Goals Over/Under ID 5 with exact expected provider name: conversion is called for a complete selected Over/Under pair.
- Corners Over/Under ID 45: selector can select the pair, but typical provider name `"Corners Over Under"` does not match the exact name allowlist, so conversion is skipped.
- Incomplete pairs and 1X2 do not enter conversion.

Conversion exceptions are not caught in the normalizer and therefore propagate to the outer API/scheduler rollback/error handling. If successful, `myanmar_odd` is copied into main Odds persistence and read serialization. Whether a given runtime sync has executed conversion is not established by source inspection.

## 7. Findings

| ID | Classification | Finding and direct caller → callee evidence | Expected vs actual |
|---|---|---|---|
| F1 | PASS | Local identity handoff: `sync_match_odds_route` and `_refresh_odds_job` read local Match ID and `provider_fixture_id`, then call `refresh_odds(..., local_match_id=local ID)`. `odds.fixture_id` is assigned local ID before repository replacement. `app/api/matches.py:387-398`; `app/services/scheduler.py:813-821,867-873`; `app/services/odds_sync_service.py:54-55,88-109`. | Provider request uses external identity; persistence uses local identity, as required by current Match model split. |
| F2 | PASS | Provider chain is wired: `OddsSyncService.refresh_odds` → `OddsProvider.get_match_odds` → concrete `FootballAPIClient.get_with_metadata("/odds", params={"fixture": ...})`. `app/services/odds_sync_service.py:55`; `app/providers/odds_provider.py:12-15`; `app/services/base/football_client.py:160-166`. | Active sync invokes provider endpoint with provider fixture ID. |
| F3 | INCOMPLETE | Provider return annotations declare optional dict, while production `get_with_metadata` returns `FootballAPIResponse`; sync handles that type but protocol omits method. `app/providers/base/football_api_client.py:1-10`; `app/providers/odds_provider.py:12-15`; `app/services/base/football_client.py:160-166`; `app/services/odds_sync_service.py:56-69`. | Runtime code has a compatibility branch; static interface contract is incomplete. |
| F4 | PASS | Active market selector retains bookmaker ID 11 and supported bet IDs 1/4/5/45, stores provider names and trimmed selections, and maps local ID before write. `app/services/odds_service.py:162-216`; `app/services/odds_sync_service.py:81-107`. | Selected rows reach primary Odds persistence in declared fields. |
| F5 | INCOMPLETE | Parser assumes nested objects are dict/list shapes and only guards odd float conversion; it does not explicitly validate finite/positive odds or fixture mismatch as a distinct failure. `app/services/odds_sync_service.py:71-90`; `app/services/odds_service.py:193-207`. | Malformed input can be skipped inconsistently or raise; invalid numeric edge cases are not fully rejected. |
| F6 | PASS | Nonempty snapshot path deletes existing match rows then PostgreSQL-upserts normalized rows; outer API/scheduler owns commit. `app/repositories/odds_repository.py:34-60`; `app/services/odds_sync_service.py:91-114`; `app/api/matches.py:400-406`; `app/services/scheduler.py:878-883`. | Persistence method is called in the active nonempty path. |
| F7 | INCOMPLETE | `project_odds` accepts Bet365/canonical values, but caller passes provider bookmaker name and raw provider market name without canonicalization; incompatible values are filtered to zero and zero counts report success. `app/services/odds_service.py:165-166,210-216`; `app/services/odds_sync_service.py:106-114`; `app/services/analytics_projection_service.py:124-130,249-270`. | Analytics write has no source-level mapping bridge; common human-readable values become a successful no-op. Exact provider-returned labels are not known from source. |
| F8 | FAIL | Scheduler calls commit helper for any no-error result and increments refreshed count; empty/no-usable result has no error or success status and skips main replacement. Decorator likewise treats it as success. `app/services/odds_sync_service.py:75-77,91-120`; `app/services/scheduler.py:874-893,907-923`; `app/monitoring.py:249-277`. | No-data should not be represented as newly refreshed; actual code reports success/refreshed in scheduler path. |
| F9 | FAIL | Commit helper calls nonexistent `is_commit_ambiguous` and `verify_commit_unknown` methods; result lacks expected `_transaction_context`. `app/services/scheduler.py:64-120`; `app/services/odds_sync_service.py:17-140`. | Commit exceptions cannot follow the implemented recovery branch; ambiguous outcome cannot be verified through this helper. |
| F10 | PASS | Read path GET → facade → OddsService cache/DB serializer returns selected stored rows and includes Myanmar field; GET does not call provider. `app/api/matches.py:301-312`; `app/services/football.py:248-250`; `app/services/odds_service.py:222-254`. | Read path is connected to database/cache snapshot and uses local ID. |
| F11 | PASS, with stale comment | Myanmar conversion helper is called for eligible selected pairs; `myanmar_odd` is carried through persistence and serialization. `app/services/odds_service.py:73-120,136-159,210-216,236-247`; `app/services/odds_sync_service.py:93-107`. | Backend conversion output is connected to stored/read fields for eligible exact market names. |
| F12 | INCOMPLETE | Corners ID 45 can be selected but its common market name does not satisfy `_attach_myanmar_odd_labels` exact allowlist. `app/services/odds_service.py:81-87,136-153`. | Corners selection is supported; Myanmar conversion is not connected for the `"Corners Over Under"` name. |
| F13 | INCOMPLETE | `FootballAPIService.get_match_odds` delegates to `OddsService.get_match_odds`, which returns a read-only error object; no application call sites use this facade method. The actual GET calls `get_cached_odds`. `app/services/football.py:237-238`; `app/services/odds_service.py:219-220`; `app/api/matches.py:301-312`. | A secondary named “get odds” path is disconnected from useful reads; current public route correctly uses the separate cached-read method. |
| F14 | FAIL | Scheduler provider-error branch calls absent `classify_retry_state` after incrementing `failed_matches`; its surrounding exception handler catches the resulting error and increments the same counter again. `app/services/scheduler.py:909-927`; method absent from `app/services/odds_sync_service.py:17-140`. | Provider failures can produce an `AttributeError`, suppress intended classification logging, and double-count failed matches. |

### Unreferenced repository methods

`OddsRepository.get_by_match`, `find_by_business_identity`, and `snapshot_matches_rows` have definitions but no call sites under `app/`. The active `OddsService.get_cached_odds` issues its own `select(Odds)` (`app/services/odds_service.py:234`) rather than calling `get_fixture_odds` or `get_by_match`. The active write path calls `replace_fixture_odds`, which calls `delete_fixture_odds` and `upsert_many`. These other repository methods are disconnected from the current public read/sync path.

## 8. Flow Diagram

```text
API-Football
    |
    | GET /odds?fixture=<provider_fixture_id>
    v
FootballAPIClient.get_with_metadata
    v
OddsProvider.get_match_odds
    v
OddsSyncService.refresh_odds
    |  validates envelope, compares response fixture ID
    |  maps provider fixture ID -> passed local_match_id
    v
OddsService._get_1xbet_bookmaker
    -> OddsService._filter_main_lines
       -> IDs 1/4/5/45; selects main/complete lines
       -> Myanmar conversion on eligible exact-name pairs
    v
OddsRepository.replace_fixture_odds
    -> delete existing local Match rows
    -> PostgreSQL insert/upsert
    -> flush
    |
    +--> AnalyticsProjectionService.project_odds
           [INCOMPLETE VOCABULARY CONNECTION:
            raw provider values have no canonicalization bridge to the accepted
            Bet365/canonical allowlist; incompatible rows filter to zero,
            existing Analytics snapshot is preserved and result can still succeed]
    |
    +--> outer API or scheduler commit/rollback
           [COMMIT ERROR PATH BROKEN:
            referenced is_commit_ambiguous / verify_commit_unknown methods absent]

Scheduler no-data branch:
  refresh returns no "error"/no SUCCESS -> scheduler commits + invalidates
  -> counts refreshed despite no primary snapshot replacement [FAIL]

Scheduler provider-error branch:
  state="failed" -> absent classify_retry_state call
  -> AttributeError -> outer handler catches and increments failed metric again [FAIL]

GET /api/matches/{local_id}/odds
    -> _assert_match_allowed
    -> FootballAPIService.get_cached_odds
    -> OddsService.get_cached_odds
       -> Redis hit OR database SELECT by local Odds.fixture_id
       -> serializer includes myanmar_odd
    -> API returns dict
```

## 9. Unverified Points

- No provider request or sync endpoint was executed; no runtime provider payload or execution outcome was inspected.
- No database or Redis state was queried for this source-only audit.
- No commit exception was induced. The missing-method defect is established by direct source references; its actual transaction outcome is unverified.
- No Flutter source path is in scope or established here; frontend rendering is not determined.
- Historical test expectations and previous runtime reports are not used as proof in this source audit.

## 10. Final Verdict

**SOURCE-CODE FLOW: PARTIALLY CORRECT.**

Provider-to-main-Odds and database-to-API paths are connected for normal nonempty provider payloads; the API and scheduler pass the local Match identity separately from provider fixture identity; the public GET is read-only; and Myanmar labels are connected for the eligible exact-name markets.

The source contains concrete broken/incomplete paths: scheduler no-data results can be counted as successful refreshes; the Analytics projection has no bridge from provider bookmaker/market values to its accepted canonical contract and can silently preserve the old Analytics snapshot; the scheduler commit-error handler calls methods absent from active `OddsSyncService`; and the scheduler provider-error branch calls an absent retry-classification method. The Corners Myanmar name gate and provider typing contract are also incomplete; several repository read/verification helpers are unreferenced.
