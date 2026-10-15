# Odds Final Design Freeze — Prematch Odds + Myanmar Odds

**Status:** DESIGN FROZEN — approved decisions recorded 2026-10-09; implementation subsequently authorized
**Audit basis:** Current repository source inspection on 2026-10-09
**Implementation status:** Target market filtering and required-market validation are implemented in the current worktree; no provider-backed sync has been run
**Design revision:** 2.0 — approved bookmaker, market, retry, and recovery decisions

## 1. Design Objective and Scope

Freeze the smallest compatible design for refreshing **prematch** Odds, validating an entire candidate snapshot (including required Myanmar labels), preserving the last valid snapshot when refresh validation fails, and invalidating cache only after persistence is confirmed committed.

Included:

- Existing API-Football `/odds` provider request.
- Scheduler-owned three-hour cadence and prematch candidate selection.
- Current Odds sync service and repository write path.
- Required Myanmar conversion in the active pre-persistence normalization path.
- Transaction, commit outcome, cache invalidation and refresh-result behavior.
- Alignment of the Analytics Odds projection with the approved primary bookmaker and canonical market vocabulary.
- Existing public Odds GET serializer.

Excluded:

- Live Odds.
- Database schema/migration work, unless a later implementation review proves the current schema cannot represent the frozen contract.
- Flutter/UI changes or guarantees; Flutter source is not in this repository.
- Unrelated changes to the frozen Analytics module or other frozen modules. The explicitly approved Odds bookmaker/market alignment in the Analytics Odds projection is in scope and must retain Analytics ownership of projection logic.
- Provider requests, database mutations, or implementation in this phase.

The design preserves current service ownership: Provider transports, Odds service normalizes domain values, OddsSyncService orchestrates the write, OddsRepository owns Odds-table SQL, and the API/scheduler own transactions and post-commit cache invalidation. The approved Analytics alignment changes the projection's accepted bookmaker/market vocabulary only; it does not move Analytics persistence into the Odds repository or transfer transaction ownership.

## 2. Frozen Final Workflow

```text
LiveUpdateScheduler.start
  -> register refresh_odds every 3 hours

_refresh_odds_job
  -> select future prematch Match rows
       status NS or TBD
       (PST only if explicitly treated as rescheduled prematch
        and its match_time is still in the future)
       within the existing 72-hour horizon
  -> acquire existing per-local-Match PostgreSQL advisory transaction lock
  -> inspect persisted retry state and provider cooldown:
       if a retry is due and provider is not in cooldown: continue
       otherwise inspect existing Odds snapshot:
         if structurally valid AND updated within the existing 12-hour freshness window:
           SKIP without provider call
         otherwise continue
  -> OddsSyncService.refresh_odds
       -> OddsProvider GET /odds?fixture=<provider_fixture_id>
       -> validate provider envelope and fixture identity
       -> normalize verified market IDs to canonical categories
       -> retain unknown/unmapped provider market ID/name as diagnostic evidence;
          never guess a canonical category
       -> validate numeric odds, market completeness and snapshot usability
       -> calculate Myanmar labels for IDs 4 and 5 only;
          IDs 1 and 45 permit NULL
       -> reject entire candidate on any required validation failure
  -> on invalid / empty / no-data / conversion failure:
       no Odds repository replacement
       roll back/close the outer transaction to release the transaction-scoped lock;
       no Odds-cache invalidation
       in a separate retry-state transaction, persist the per-match outcome,
       failure count, and next eligible retry time; retain raw unknown-market
       evidence for diagnosis without assigning a guessed category
  -> on validated candidate:
       OddsRepository replaces the complete Odds snapshot inside outer transaction
       project the same canonical 1xBet snapshot through the aligned Analytics Projection
       within the same outer transaction; projection failure prevents commit
       outer scheduler commits
  -> only after commit is confirmed:
       invalidate fover:match:<local_match_id>:odds
       report UPDATED / success
  -> on cache invalidation failure:
       preserve committed DB result, record CACHE_INVALIDATION_FAILED,
       retain recoverable cache invalidation work; do not refetch provider data to repair cache
```

The three-hour job cadence is not a mandate to fetch every match every three hours. The existing 12-hour freshness window and 72-hour prematch horizon are retained as the current policy baseline; freshness is evaluated against a **valid complete local snapshot**, not merely the latest arbitrary Odds row. A persisted retry that has become eligible takes precedence over freshness suppression, subject to provider-wide cooldown, so an earlier failure is not hidden by the timestamp of the preserved snapshot. Timeout and HTTP 5xx failures do not cause immediate provider retries; they become eligible at a later scheduled attempt. HTTP 429 handling observes `Retry-After` when present and records/enforces provider cooldown. Persist the failure outcome, count, and next eligible time only after the odds transaction has been rolled back/closed, in a separate retry-state transaction owned by the existing entry point and protected against per-match races. Retry-state storage must not be confused with the Odds snapshot transaction. Numeric retry/cooldown parameters remain unapproved and must not be invented.

## 3. Current Implementation Flow — Verified Source

### Scheduler and eligibility

- `LiveUpdateScheduler.start()` registers `_refresh_odds_job` with `IntervalTrigger(minutes=360)` and `max_instances=1`: [app/services/scheduler.py](../app/services/scheduler.py), lines 194-253. This is currently six hours, not the target three.
- `_refresh_odds_job()` selects local Match ID, provider fixture ID, status, and kickoff time. Current eligible statuses are `NS`, `TBD`, and `PST`; query horizon is now through 72 hours; latest Odds younger than 12 hours is skipped: [app/services/scheduler.py](../app/services/scheduler.py), lines 46-49 and 797-874.
- Its freshness test is based on the newest `Odds.last_updated`, without validating that the entire stored snapshot is valid or complete: [app/services/scheduler.py](../app/services/scheduler.py), lines 858-865.
- The API admin entry point is a separate on-demand path and is not constrained by the scheduler's upcoming-match window: [app/api/matches.py](../app/api/matches.py), lines 380-438. It remains available but must obey the same snapshot validation/write contract.

### Provider, parser, persistence

- `OddsProvider.get_match_odds()` makes `GET /odds` with `fixture=<provider fixture ID>`: [app/providers/odds_provider.py](../app/providers/odds_provider.py), lines 6-15.
- The concrete HTTP client returns `FootballAPIResponse` through `get_with_metadata`, with a 30-second timeout and default two retries; the Odds sync service handles metadata errors and requires a dict containing `response`: [app/services/base/football_client.py](../app/services/base/football_client.py), lines 96-166; [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 55-73.
- `OddsSyncService.refresh_odds()` is reached by both the scheduler and admin route. It compares provider `fixture.id`, selects bookmaker ID 11 through `OddsService`, filters bet IDs 1/4/5/45, maps `fixture_id` to local Match ID, then calls the repository for nonempty output: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 45-120; [app/services/odds_service.py](../app/services/odds_service.py), lines 162-216.
- `OddsRepository.replace_fixture_odds()` deletes all rows for the local Match and then upserts the new rows. Called only if the active normalizer produced at least one row. The active sync service does not call it on empty output: [app/repositories/odds_repository.py](../app/repositories/odds_repository.py), lines 34-60; [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 91-120.
- Existing Odds values are strings; active parsing converts to float for selection but retains original text in persistence rows. Model: [app/models/odds.py](../app/models/odds.py), lines 8-28; parser: [app/services/odds_service.py](../app/services/odds_service.py), lines 168-216.

### Transactions and cache

- The sync service flushes but does not commit. The admin route and scheduler own commit/rollback: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 91-114; [app/api/matches.py](../app/api/matches.py), lines 400-428; [app/services/scheduler.py](../app/services/scheduler.py), lines 64-120 and 874-893.
- Both entry paths acquire a per-local-Match advisory transaction lock: API lines 431-438; scheduler lines 895-905; lock implementation: [app/services/resource_lock.py](../app/services/resource_lock.py), lines 10-64.
- Cache key is built from prefix, `"match"`, local Match ID, and `"odds"`: [app/cache.py](../app/cache.py), lines 21-24. Admin invalidates after commit at [app/api/matches.py](../app/api/matches.py), lines 406-418. Scheduler invalidates after `commit_odds_refresh` reports `refreshed`: [app/services/scheduler.py](../app/services/scheduler.py), lines 122-140 and 886-893.
- Current scheduler ambiguous-commit code calls `is_commit_ambiguous` and `verify_commit_unknown`; neither method is implemented in active [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 17-140. It also expects `_transaction_context`, which current refresh results do not populate. Current scheduler provider-error reporting calls absent `classify_retry_state` at lines 909-917.

### Analytics projection interaction

- Active sync passes the same 1xBet/provider-name rows to `AnalyticsProjectionService.project_odds`: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 106-114.
- Projection currently accepts bookmaker `"bet365"` and canonical market values only, then returns the existing Analytics snapshot with `filtered_to_zero_preserved` when no rows remain. That zero-source result can report success: [app/services/analytics_projection_service.py](../app/services/analytics_projection_service.py), lines 124-130 and 249-274.
- Thus the active pipeline has no explicit mapping bridge from the Odds producer's vocabulary into Analytics' accepted vocabulary. **Approved target:** align Analytics Projection's accepted bookmaker to 1xBet and feed it canonical market names from the shared validated snapshot. Projection is part of the same outer transaction: zero accepted rows or reconciliation failure is not success and prevents commit of either projection or replacement snapshot. Keep projection SQL/persistence in Analytics-owned components; do not move it into OddsRepository.

### Myanmar conversion and read

- Active call chain: `refresh_odds` → `_filter_main_lines` → `_select_main_line_by_id` → `_attach_myanmar_odd_labels` → `MyanmarOddsService.convert_to_myanmar_odds`, before `persistence_rows` are made: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 81-107; [app/services/odds_service.py](../app/services/odds_service.py), lines 73-120 and 123-216.
- Existing converter uses the passed pair's decimal prices, a parsed absolute handicap, and optional favorite-side override; output carries `market_label` and `status`, including `OUT_OF_RANGE`: [myanmar_odds/services/myanmar_odds_service.py](../myanmar_odds/services/myanmar_odds_service.py), lines 56-126; [myanmar_odds/models/myanmar_odds_result.py](../myanmar_odds/models/myanmar_odds_result.py), lines 5-16.
- Current helper gates on exact names `"Asian Handicap"` and `"Goals Over/Under"` as well as IDs `{4,5,45}`; therefore usual Corners name `"Corners Over Under"` skips conversion: [app/services/odds_service.py](../app/services/odds_service.py), lines 73-87 and 136-159.
- Current read endpoint is `GET /api/matches/{match_id}/odds`; it calls `get_cached_odds` and returns its serializer without recalculating Myanmar values: [app/api/matches.py](../app/api/matches.py), lines 301-312; [app/services/football.py](../app/services/football.py), lines 248-250; [app/services/odds_service.py](../app/services/odds_service.py), lines 222-254.

## 4. Compatibility Matrix (Current Source vs Approved Target)

| Requirement | Classification | Current evidence (file/function/lines) | Consequence for target |
|---|---|---|---|
| Three-hour scheduler | FAIL | `LiveUpdateScheduler.start`, interval is 360 minutes: [scheduler.py](../app/services/scheduler.py), lines 194-253. | Target cadence is not implemented; scheduler registration must change to three hours. |
| Prematch-only candidates | INCOMPLETE | Eligible `NS`, `TBD`, `PST`, 72-hour future window; stop statuses include live/finished: [scheduler.py](../app/services/scheduler.py), lines 46-49 and 808-830. | Live is excluded, but postponed eligibility is implicit and should be frozen explicitly. |
| Do not request every match on every job run | PASS, with guard qualification | Newest Odds under 12 hours is skipped: [scheduler.py](../app/services/scheduler.py), lines 858-865. | Keep the 12-hour gate under the three-hour cadence, but only skip when the existing snapshot passes frozen validity checks. |
| Empty/no-usable response never replaces valid rows | PASS for current repository call; surrounding behavior FAIL | Sync skips repository replacement for empty/no-usable rows: [odds_sync_service.py](../app/services/odds_sync_service.py), lines 75-120. | Existing rows remain, but scheduler can still commit, invalidate cache, and count refreshed. Preserve rows and make the result non-success/no-data. |
| Invalid/incomplete incoming snapshot cannot replace old snapshot | INCOMPLETE | Parser skips malformed individual odds and incomplete lines; any surviving nonempty rows replace the whole Match snapshot: [odds_service.py](../app/services/odds_service.py), lines 123-216; [odds_sync_service.py](../app/services/odds_sync_service.py), lines 91-110; [odds_repository.py](../app/repositories/odds_repository.py), lines 52-60. | An incomplete response can become a partial replacement. Validate the complete candidate before calling repository replacement. |
| Validate odds as finite and positive | INCOMPLETE | `float()` parsing is used, but no explicit `isfinite`/positive guard in active normalizer; Analytics has a Decimal positive check only after its independent filter: [odds_service.py](../app/services/odds_service.py), lines 199-207; [analytics_projection_service.py](../app/services/analytics_projection_service.py), lines 255-263. | Invalid values could reach main Odds string persistence if they pass the selector; add source-level validation before snapshot acceptance. |
| Normalize market names | INCOMPLETE | Main Odds stores provider `bet.name`; Analytics requires canonical enum-like names: [odds_service.py](../app/services/odds_service.py), lines 193-216; [analytics_projection_service.py](../app/services/analytics_projection_service.py), lines 249-253. | Design requires an explicit stable mapping by provider market ID; API consumers of current human-readable names need compatibility review. |
| Validate complete/usable snapshot | INCOMPLETE | Selector emits complete pairs/1X2 but drops incomplete inputs; sync only checks whether any normalized rows exist: [odds_service.py](../app/services/odds_service.py), lines 123-160; [odds_sync_service.py](../app/services/odds_sync_service.py), lines 75-120. | No whole-snapshot completeness decision prevents partial replacement. |
| Myanmar calculation happens before persistence | PASS for current gated pairs | Active selector invokes conversion before `persistence_rows` are constructed: [odds_service.py](../app/services/odds_service.py), lines 73-120 and 136-159; [odds_sync_service.py](../app/services/odds_sync_service.py), lines 93-107. | The connection exists, but result status/output is not validated as a persistence gate. |
| Required Myanmar result is validated | INCOMPLETE | Caller consumes `result.market_label`, but does not check `result.status`, `OUT_OF_RANGE`, or nonempty value: [odds_service.py](../app/services/odds_service.py), lines 92-120. | An unsupported/out-of-range result could be persisted as a label instead of rejecting the candidate. |
| Eligible markets and noneligible NULL are explicit | INCOMPLETE | Converter caller checks IDs `{4,5,45}` but exact names only permit AH and Goals OU; 1X2 receives no Myanmar label: [odds_service.py](../app/services/odds_service.py), lines 81-87 and 123-163. | Apply approved ID-based rule: IDs 4/5 require conversion; IDs 1/45 allow NULL. Raw names must not control eligibility. |
| Persist normalized values with labels | PASS, conditional on nonempty selector output | Main rows include `myanmar_odd`; repository upsert updates it: [odds_sync_service.py](../app/services/odds_sync_service.py), lines 93-107; [odds_repository.py](../app/repositories/odds_repository.py), lines 37-50. | Values are persisted when included, but validation must occur first to prevent null/stale labels on required markets. |
| Snapshot replacement is atomic in outer transaction | PASS for primary Odds table transaction boundary | Delete/upsert is followed by flush; caller owns commit/rollback: [odds_repository.py](../app/repositories/odds_repository.py), lines 52-60; [odds_sync_service.py](../app/services/odds_sync_service.py), lines 109-114; [api/matches.py](../app/api/matches.py), lines 400-428; [scheduler.py](../app/services/scheduler.py), lines 64-120. | Validate before repository call; any persistence/projection exception before commit must rollback. |
| Analytics projection is aligned with approved Odds source | FAIL in current source | Producer selects ID 11 and raw provider market name, while projection allows Bet365 and canonical markets; zero accepted rows preserve old Analytics and can report success: [odds_service.py](../app/services/odds_service.py), lines 162-216; [analytics_projection_service.py](../app/services/analytics_projection_service.py), lines 249-274. | Align projection to 1xBet plus the canonical snapshot vocabulary. Keep it transactionally coupled; zero acceptance/reconciliation failure must fail the snapshot transaction. |
| Commit failure recovery methods exist | FAIL | Scheduler calls missing `is_commit_ambiguous`, `verify_commit_unknown`, and `classify_retry_state`: [scheduler.py](../app/services/scheduler.py), lines 64-120 and 909-917; no definitions in [odds_sync_service.py](../app/services/odds_sync_service.py), lines 17-140. | Cannot safely classify uncertain commit, report provider failure, or authorize cache invalidation/retry. |
| Cache invalidated only after confirmed commit | INCOMPLETE | Cache invalidation is called after commit helper returns refreshed; helper lacks its referenced methods and no-data result also reaches it: [scheduler.py](../app/services/scheduler.py), lines 64-120 and 874-893. | Successful nonempty normal commit is ordered correctly; current false-refreshed/no-data and broken commit-error paths violate the intended guarantee. |
| Timeout/5xx immediate retry behavior matches approved policy | FAIL | Metadata client retries transport exceptions and HTTP 5xx twice immediately: [app/services/base/football_client.py](../app/services/base/football_client.py), lines 113-157. | For Odds requests, defer after the current request attempt; do not spend extra immediate provider calls on timeout/5xx. |
| HTTP 429 cooldown and persisted per-match retry state | INCOMPLETE | 429 is returned without Retry-After handling; scheduler has no Odds-specific persisted failure counter/next eligible time: [app/services/base/football_client.py](../app/services/base/football_client.py), lines 126-142; [app/services/scheduler.py](../app/services/scheduler.py), lines 797-930. | Honor Retry-After, enforce provider cooldown, and persist per-match count and next eligibility. Numeric values remain implementation parameters requiring evidence/approval. |
| Canonical mapping based on verified market identity | INCOMPLETE | Active parser persists raw bet names; Analytics requires canonical names: [app/services/odds_service.py](../app/services/odds_service.py), lines 193-216; [app/services/analytics_projection_service.py](../app/services/analytics_projection_service.py), lines 249-268. | Map only verified market IDs; preserve raw unknown ID/name diagnostics; never guess aliases. |
| Failure is not reported as success | FAIL | Empty results omit `error` and `status: SUCCESS`; scheduler treats absence of error as commit path and increments `refreshed_matches`; decorator records success unless `error`/`success:false`: [odds_sync_service.py](../app/services/odds_sync_service.py), lines 75-120; [scheduler.py](../app/services/scheduler.py), lines 874-923; [monitoring.py](../app/monitoring.py), lines 249-277. | Define explicit `UPDATED`, `SKIPPED_FRESH`, `NO_DATA`, `FAILED`, `COMMIT_UNKNOWN`, and `CACHE_INVALIDATION_FAILED` outcomes. |
| GET returns stored Myanmar field without recalculation | PASS | Serializer returns `o.myanmar_odd`; GET calls cached read service: [odds_service.py](../app/services/odds_service.py), lines 222-254; [api/matches.py](../app/api/matches.py), lines 301-312. | Stable persisted field path exists; no conversion on read is needed. |

## 5. Myanmar Odds Calculation Rules and Eligible Markets

### Frozen eligibility

For this Prematch design:

| Provider market ID | Canonical market name | Myanmar treatment |
|---:|---|---|
| 1 | `MATCH_WINNER` | Not eligible; `myanmar_odd = NULL` is legitimate. Requires complete Home/Draw/Away selections to qualify as a complete 1X2 market. |
| 4 | `ASIAN_HANDICAP` | Eligible; requires a complete Home/Away pair for one selected handicap line and a valid Myanmar result for both selections. |
| 5 | `GOALS_OVER_UNDER` | Eligible; requires a complete Over/Under pair for one selected line and a valid Myanmar result for both selections. |
| 45 | `CORNERS_OVER_UNDER` | Not eligible by approved decision; `myanmar_odd = NULL` is legitimate. The current code's ID/name checks are inconsistent with the ID-based target. |

Myanmar eligibility is determined by the verified market ID, not a raw display name. ID 45 appearing in part of the current helper does not override the approved noneligible/NULL rule.

### Calculation behavior preserved from current domain service

- Handicap parser extracts a number and takes its absolute value (`myanmar_odds/utils/handicap_parser.py:7-22`).
- Base index supports quarter-goal increments from 0.00 through 25.00; unsupported values raise (`myanmar_odds/constants/base_index_map.py:8-44`).
- Difference is rounded `abs(home_odds - away_odds) * 100`; the lower decimal price determines side unless a valid favorite override is provided (`myanmar_odds/services/myanmar_odds_service.py:18-29,56-74`).
- In the current active Odds parser, the favorite override is derived from the 1X2 Home/Away prices and equal prices currently fall through to `"AWAY"` (`app/services/odds_service.py:168-190`). **Frozen adjustment:** provide an override only when the 1X2 pair is valid and unequal; otherwise allow the converter to derive the side from the eligible market pair. Do not arbitrarily treat a tie as Away.
- Nonzero handicap maps `base_index` plus/minus the difference according to the engine's favorite-price rule, then resolves a ladder label. An out-of-range result returns `status="OUT_OF_RANGE"` (`:91-126`).
- Zero handicap returns `D` for equal prices or side-oriented signed difference label; the current code marks these `OK` (`:75-89`).
- For Asian Handicap, use selected Home/Away prices, the selected handicap line, and the existing 1X2-derived favorite override when available. For Goals Over/Under, use the selected Over/Under prices and line with no team favorite override, matching current caller behavior (`app/services/odds_service.py:73-120,136-159`).
- On an eligible market, conversion passes only if both required labels are present and neither result is `OUT_OF_RANGE` or otherwise non-OK. A conversion exception or invalid result rejects the entire incoming candidate snapshot.
- On noneligible markets, store `myanmar_odd = NULL` explicitly; this is not a conversion failure.

## 6. Snapshot Validation and Old-Data Preservation Rules

### Frozen candidate acceptance rule

The candidate is validated fully in memory before `OddsRepository.replace_fixture_odds` is called.

1. Provider result must have a valid response envelope and no provider/API error.
2. Each accepted fixture item must match the requested provider fixture ID. Unexpected structure or identity mismatch rejects the candidate; it is not silently treated as absence.
3. Resolve only the configured bookmaker (current source: API-Football ID 11 / 1xBet). A missing bookmaker is `NO_DATA`, not a valid replacement.
4. Normalize supported provider bet IDs to stable canonical market names and preserve the selection line as a canonical selection value.
5. Each persisted odd must parse as a finite decimal greater than zero. Do not store `NaN`, infinity, zero, negative, empty or malformed values.
6. Market completeness:
   - Markets 1, 4, and 5 are required: 1X2 = Home/Draw/Away; Asian Handicap = Home/Away at the same selected line; Goals Over/Under = Over/Under at the same selected line.
   - Market 45 is optional. If present in the accepted candidate, it must have a complete Over/Under pair at the same line.
   - Unknown provider market IDs outside `{1, 4, 5, 45}` are ignored before supported-market normalization. They neither establish nor invalidate completeness and are never persisted as Odds or Analytics markets.
   - Missing required markets or malformed/incomplete advertised supported markets reject the whole candidate rather than silently dropping supported data and replacing with a partial subset.
7. For each AH and Goals O/U market accepted, calculate and validate the required Myanmar label for every persisted selection. For 1X2 and Corners O/U, null is valid by frozen scope.
8. Only after all checks pass may the sync service call repository replacement.

### Preservation guarantee

- Any provider error, empty response, missing bookmaker, invalid odd, incomplete advertised market, no usable market, or required Myanmar conversion failure must leave existing Odds rows untouched.
- The caller still completes the transaction by rolling it back/closing it to release the transaction-scoped advisory lock; this does not roll back prior committed Odds rows.
- No delete, upsert, or cache invalidation occurs for those outcomes.
- Validation must happen before repository calls. If a write/flush/analytics-independent error occurs after replacement begins but before commit, the outer owner rolls back the same transaction.
- A freshness skip is permitted only when an existing persisted snapshot has passed the frozen completeness/value/Myanmar checks and its latest snapshot timestamp is younger than 12 hours.
- Main Odds snapshot replacement remains a transactionally atomic delete-and-insert/upsert operation in the existing repository. No schema change is currently required for that atomicity.

The current source partially preserves data (it avoids calling replacement for an empty normalized list) but does not enforce the whole-candidate validation rule. A nonempty partial set can replace the previous full snapshot.

## 7. Transaction, Commit/Rollback, and Cache Ownership

### Responsibilities (frozen)

| Responsibility | Owner |
|---|---|
| HTTP transport and provider envelope transport result | `FootballAPIClient` / `OddsProvider` |
| Market normalization, odds validation, Myanmar conversion and candidate validation | Existing Odds domain/read service helpers, invoked by `OddsSyncService`; no Provider or Repository business logic |
| Write orchestration and mapping local Match identity | `OddsSyncService` |
| Odds table select/delete/upsert | `OddsRepository` only |
| Per-match failure count / next eligible retry persistence | A dedicated Odds retry-state repository (or separately approved suitable existing repository), called by the API/scheduler transaction owner in a separate transaction after snapshot rollback; storage choice remains unresolved |
| Transaction-scoped per-Match lock, commit/rollback | API route for manual sync; scheduler job/helper for scheduled sync |
| Cache invalidation after commit | Existing API/scheduler transaction owner |
| Public read and serialization | `OddsService.get_cached_odds` via `FootballAPIService` and API route |

### Commit and ambiguous outcome rule

- `refresh_odds` returns a validated candidate/result and flushes as needed; it never commits.
- API/scheduler start a transaction under the existing per-local-Match advisory lock.
- On definite write/projection failure before commit, rollback and report `FAILED`; do not invalidate.
- On successful commit return, invalidate key `fover:match:<local_match_id>:odds`. Only then report `UPDATED`.
- If `commit()` raises with a possibly ambiguous outcome, classify it explicitly and verify from a fresh read session that the committed snapshot exactly matches the expected complete business keys and values, including Myanmar labels.
- If verification confirms exact committed state, report commit-confirmed and invalidate cache.
- If verification establishes no commit, report failure; do not claim success or invalidate. A future retry can occur only after lock reacquisition and fresh-state inspection.
- If verification is unavailable or inconclusive, report `COMMIT_UNKNOWN`; do not retry automatically, report success, or invalidate cache until state is resolved.

The current `commit_odds_refresh` attempts verification but calls methods missing from the active service; the frozen rule is target behavior, not current implementation.

Provider, validation, and persistence failures must not be recorded in the rolled-back snapshot transaction. The entry point first rolls back/closes that transaction to release its transaction-scoped advisory lock, then persists the outcome in a separate transaction. The retry-state write must serialize by local Match identity and must not modify Odds rows or Odds cache. Whether the current lock helper can safely protect both transactions, or the retry write requires a new lock acquisition, must be decided during implementation without moving commit ownership into `OddsSyncService`.

### Cache failure

After a confirmed commit, cache deletion failure does not roll back valid database data. Report the committed update with a separate `CACHE_INVALIDATION_FAILED` state and preserve the existing scheduler's in-memory retry mechanism. Cache invalidation failure is not a provider/data validation failure and must not be reported as “not persisted.”

## 8. Versioned Failure and Recovery Matrix

**Matrix version 1.0 — approved design, 2026-10-09.** This matrix is target behavior, not a statement that the current source implements it. Numeric retry, cooldown, and scheduling parameters not supported by verified evidence remain unset.

| Condition | Outcome | Snapshot / Analytics data | Transaction and cache | Retry/recovery |
|---|---|---|---|---|
| Timeout / connection failure | `PROVIDER_FAILED: TIMEOUT` | Preserve existing valid snapshot; no projection write | Outer owner rolls back/closes transaction; no cache invalidation | No immediate retry. Persist failure count and next eligible attempt; defer to later scheduled eligibility. |
| HTTP 5xx | `PROVIDER_FAILED: SERVER_ERROR` | Preserve existing valid snapshot; no projection write | Roll back/close; no invalidation | No immediate retry. Defer to later scheduled eligibility. |
| HTTP 429 | `PROVIDER_FAILED: RATE_LIMITED` | Preserve existing valid snapshot | No snapshot commit or cache invalidation | Honor `Retry-After` when present and set/enforce shared provider cooldown. Persist per-match count and next eligible time. Numeric fallback cooldown is TBD and requires justification. |
| Other permanent provider/API error or malformed envelope | `PROVIDER_FAILED` with reason | Preserve old snapshot | Roll back/close; no invalidation | No automatic same-payload retry; record and surface. Retry eligibility is determined by classified error and approved provider policy. |
| Empty response or configured bookmaker absent | `NO_DATA` | Preserve old Odds and Analytics snapshots | No snapshot commit; no cache invalidation | Distinct from provider failure and validation failure. Record outcome and next eligible attempt; numeric cooldown is TBD. |
| Unknown/unmapped market ID/name | Ignore outside allowlist; may retain bounded diagnostic evidence | Never normalize or persist unknown rows. Validate required IDs 1/4/5 and optional ID 45 independently. | No cache invalidation unless the complete supported snapshot is committed | Do not retry solely to infer a mapping. Add mappings only for explicitly approved IDs. |
| Invalid/non-finite/nonpositive odds, malformed required selection, or incomplete advertised supported market | `VALIDATION_FAILED` | Preserve prior snapshot and Analytics projection | Roll back/close; no invalidation | No immediate retry of the same response. Record failure and defer to an eligible later provider refresh. |
| Missing required Myanmar inputs/label, unsupported conversion, exception, or `OUT_OF_RANGE` for ID 4/5 | `VALIDATION_FAILED: MYANMAR` | Preserve prior snapshot and Analytics projection | Roll back/close; no invalidation | No retry of the same candidate; later refresh may be attempted subject to persisted eligibility. |
| No eligible Myanmar markets in a valid snapshot (IDs 1 and/or 45 only) | `UPDATED` if all other snapshot requirements pass | Persist NULL for IDs 1 and 45; update aligned Analytics projection | Commit first; then invalidate Odds cache | NULL is legitimate and is not a conversion failure. |
| Valid candidate; Analytics rejects, accepts zero rows, or reconciliation fails | `PERSISTENCE_FAILED: ANALYTICS_PROJECTION` | Roll back both Analytics and Odds replacements; preserve prior snapshots | No cache invalidation | Diagnose mapping/identity/constraint cause; do not report primary Odds success. |
| Database write/flush failure | `PERSISTENCE_FAILED` | Roll back to preserve prior committed snapshots | No cache invalidation | Defer retry until the session/transaction is known clean and persisted eligibility permits it. |
| Definite commit failure | `COMMIT_FAILED` | Verify/rely on known rollback state; preserve prior snapshot | No cache invalidation | Retry only after transaction/session is known clean and eligibility is rechecked. |
| Ambiguous commit | `COMMIT_UNKNOWN` until resolved | Compare exact expected snapshot (business keys, values, Myanmar labels, completeness) in a fresh session | Do not invalidate before commit is confirmed | Do not retry until verification establishes no commit. If verification is inconclusive, surface unresolved state and do not blind-retry. |
| Confirmed commit | `UPDATED` | New complete Odds and aligned Analytics snapshots committed atomically | Invalidate Odds cache only after commit confirmation | No provider retry. |
| Cache invalidation failure after confirmed commit | `UPDATED` plus `CACHE_INVALIDATION_FAILED` | Keep committed DB state | Do not roll back; preserve recoverable invalidation work | Retry cache deletion, not provider refresh. Whether recovery state is durable is an implementation decision; no cross-process guarantee is assumed from the current in-memory set. |
| Repeated failures | Preserve last valid snapshot | No replacement while failures persist | No cache invalidation absent confirmed commit | Persist per-match failure count and next eligible time. Maximum count, backoff/cooldown duration, reset rules, and terminal/suppression behavior are implementation parameters requiring explicit numeric justification; none is invented by this freeze. |

## 9. Final Architectural Decisions

1. **Keep the 3-hour Prematch-only scope.** Register the Odds job every three hours. Do not add live statuses or a live polling path.
2. **Preserve non-eager provider usage.** Keep the 72-hour future-match candidate horizon and the 12-hour freshness threshold, but skip only if the currently stored snapshot itself passes validity checks.
3. **Prematch status policy.** `NS` and `TBD` are eligible. `PST` is eligible only when its effective Match time remains in the future and it represents a rescheduled prematch fixture; it must not qualify merely by status if time is missing/past. Live/terminal statuses remain excluded.
4. **Keep API/admin entry point but enforce the same validator.** It may request an explicit refresh for an allowed Match, but a failed/no-data candidate must preserve current data and not invalidate cache.
5. **Use bookmaker ID 11 / 1xBet as the primary Odds source**, matching the current active selector. This design does not silently switch to Bet365.
6. **Canonicalize only provider IDs `{1, 4, 5, 45}`** to `MATCH_WINNER`, `ASIAN_HANDICAP`, `GOALS_OVER_UNDER`, and `CORNERS_OVER_UNDER`. Do not infer market identity from a raw name. Ignore all other IDs; they cannot be assigned a guessed category or persisted.
7. **Markets 1, 4, and 5 are required; 45 is optional.** ID 1 requires Home/Draw/Away and permits Myanmar NULL. IDs 4 and 5 require their complete selected line pairs and successful Myanmar conversion. ID 45, if included, must have a valid complete Corners Over/Under pair and permits Myanmar NULL.
8. **Whole-snapshot validation precedes replacement.** The candidate must contain valid IDs 1, 4, and 5; ID 45 is optional but must validate if present. Unknown IDs do not affect supported-market completeness. No partial candidate may cause delete-and-replace.
9. **Retain API/scheduler transaction ownership and existing repository ownership.** No commit is moved into `OddsSyncService`; no SQL is moved out of `OddsRepository`.
10. **Align Analytics Projection with the approved 1xBet source.** It consumes the same normalized canonical snapshot as primary Odds persistence and remains owned by Analytics components. Both writes share the API/scheduler outer transaction; zero acceptance or reconciliation failure rolls back both snapshots rather than silently leaving stale Analytics while reporting primary success.
11. **Explicit result states replace success-shaped empty results.** At minimum: `UPDATED`, `SKIPPED_FRESH`, `NO_DATA`, `FAILED`, `COMMIT_UNKNOWN`, and `CACHE_INVALIDATION_FAILED`. Only `UPDATED` follows confirmed commit; skipped/no-data/failure do not invalidate Odds cache.
12. **Resolve commit outcomes before cache operations.** Implemented helper methods and expected snapshot context must be part of the eventual sync/transaction contract. No blind retry or cache invalidation on unknown commit.
13. **Preserve the cache key and coordinate canonical public market values with clients.** Current GET returns the stored market string, so switching from provider display names to canonical categories is a response-contract change. Flutter/client source is unavailable; client compatibility remains an evidence gap and must be verified or handled through a separately approved API compatibility/versioning decision.

## 10. Remaining Evidence Gaps and Implementation Parameters

### Explicit design assumptions

- IDs 1, 4, and 5 must each be present and complete for a usable candidate; ID 45 may be absent. Any required market that is missing, or any supported market included but malformed/incomplete, rejects the entire candidate.
- Unknown IDs outside `{1, 4, 5, 45}` are ignored and cannot make a complete supported candidate incomplete.
- Bookmaker ID 11 / 1xBet as the intended primary source is approved. The actual provider identity/name in live payloads must still be verified from approved available evidence before runtime acceptance.
- Myanmar eligibility is approved by market ID: 4 and 5 required; IDs 1 and 45 allow NULL. Raw names do not determine eligibility.
- Existing 12-hour refresh age is retained while schedule cadence becomes three hours. This limits repeated API calls and is the current freshness policy; a different freshness window needs explicit product/API-credit approval.
- `PST` is not intrinsically prematch; it qualifies only for a future rescheduled fixture.

### Unresolved before implementation

- Actual provider bookmaker identity, provider market IDs, and raw-name aliases must be verified from approved existing evidence before runtime acceptance; no provider request was made for this design freeze.
- Public client compatibility with canonical `market` values is unverified because Flutter/client source is unavailable. Preserve or version/change that API contract before rollout.
- Whether provider payloads contain multiple fixture entries or duplicate ID 11 bookmaker entries is unverified; define deterministic rejection/selection based on provider evidence.
- Retry-After formats and provider quota guidance are not verified from local source. Respect the header when present; select bounded wait, provider cooldown duration, and maximum deferral from documented provider/product limits. Do not invent numeric values.
- Persisting per-match failure count and next eligible retry time is approved, but no Odds-specific persisted retry state exists in the inspected path. Reuse an existing store only if a suitable owner/contract is demonstrated; if a schema migration is needed, obtain separate implementation approval.
- Provider-wide 429 cooldown must be shared across workers/restarts; current source provides no durable provider cooldown state. The storage owner and any schema changes require a separate implementation decision.
- Persisted retry eligibility must override the retained 12-hour freshness skip when due, subject to provider cooldown, so recent but stale/invalid data does not suppress an approved retry.
- Unknown/unmapped market diagnostic retention mechanism and retention policy must preserve raw ID/name evidence without exposing sensitive data.

## 11. Final Verdict

**COMPATIBLE WITH REQUIRED ADJUSTMENTS.**

The approved target retains Provider → Service → SyncService → Repository → Database ownership, the local/provider Match identity split, per-Match lock, repository replacement transaction, cache key, and public read service. It requires focused implementation changes to scheduler cadence/result handling, whole-snapshot validation, canonical market mapping, Myanmar validation, Analytics projection alignment, deferred/cooldown retry state, and commit recovery. Analytics remains responsible for its projection writes; API/scheduler remain responsible for commit/rollback and post-commit cache invalidation. Design approval is not implementation authorization.

## 12. Freeze Status and Implementation Readiness Checklist

### Freeze status

**FROZEN, REVISION 2.0:** Three-hour Prematch-only scheduler; existing 72-hour horizon and 12-hour freshness baseline retained subject to valid-snapshot checks; bookmaker ID 11 / 1xBet primary source (actual provider identity must be verified before runtime acceptance); canonical mapping by verified market ID only; unknown-market evidence retained without guessed aliases; Myanmar required for IDs 4/5 and NULL allowed for IDs 1/45; whole-candidate validation before replacement; Analytics Projection aligned to 1xBet and canonical rows in the same outer transaction; timeout/5xx deferred without immediate retry; HTTP 429 honors Retry-After and enforces provider cooldown; durable per-match failure count and next eligibility; commit confirmation before cache invalidation. This freeze authorizes documentation only.

### Acceptance and implementation-readiness checklist

- [x] Approve bookmaker ID 11 / 1xBet as intended primary source; still verify actual provider identity before runtime acceptance.
- [x] Approve Myanmar eligibility by ID: 4/5 required, 1/45 NULL allowed.
- [x] Approve canonical market mapping and forbid guesses for unknown aliases.
- [x] Approve timeout/5xx deferral, 429 Retry-After/cooldown, and persisted per-match retry state.
- [ ] Verify actual provider bookmaker identity, provider market IDs, and raw-name aliases from available approved evidence.
- [ ] Resolve public `market` API compatibility for canonical names; client source is unavailable here.
- [ ] Define/implement one shared candidate validator before repository replacement.
- [ ] Make current freshness skip depend on a valid complete stored snapshot.
- [ ] Change scheduler interval to three hours while preserving prematch candidate gating.
- [ ] Make timeout/5xx defer to later scheduled attempts; honor 429 Retry-After and enforce an approved provider cooldown.
- [ ] Select justified numeric retry/cooldown/freshness parameters without inventing unsupported limits.
- [ ] Persist per-match failure count and next eligible time; obtain separate schema approval if no suitable existing store exists.
- [ ] Persist/enforce provider-wide 429 cooldown across workers; document and justify any fallback duration.
- [ ] Write failure/retry metadata in a separate transaction after snapshot rollback, with per-Match race protection and existing API/scheduler commit ownership.
- [ ] Make every scheduler result branch use explicit outcomes and persisted retry eligibility.
- [ ] Align Analytics Projection with 1xBet and canonical rows while preserving Analytics ownership and outer transaction atomicity.
- [ ] Implement definite/ambiguous commit classification and fresh-session expected-snapshot verification.
- [ ] Ensure no-data/invalid/failed/unknown states do not delete old rows or invalidate cache.
- [ ] Invalidate only after confirmed database commit; keep cache failure separate from persistence result.
- [ ] Retain unknown/unmapped market IDs and raw names as structured diagnostic evidence without guessing aliases.
- [ ] Add targeted tests for validation, preservation, Myanmar required-vs-NULL markets, transaction outcomes, and result/counter behavior before rollout.
- [ ] Verify API/client compatibility and deployment/runtime behavior in a separately authorized implementation/verification phase.

**Implementation authorization:** Not granted by this design freeze.
