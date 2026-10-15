# Odds Pre-Implementation Decisions — Bookmaker, Markets, Myanmar Odds, and Retries

**Audit status:** Source and test inspection; approved design decisions supersede the recommendations below
**Audit date:** 2026-10-09
**Decision status:** Approved by user 2026-10-09; documentation only, no implementation authorized
**Runtime/provider evidence:** Not collected in this task. No provider request, sync, endpoint, scheduler, database, or cache operation was run.

## Approved Decisions (Authoritative; 2026-10-09)

The following decisions are approved and supersede any earlier “recommended”, “requires approval”, or proposed numeric retry values in the original audit:

1. **Bookmaker:** bookmaker ID 11 / 1xBet is the intended primary Odds source. Align Analytics Projection with that source; do not substitute Bet365. Verify actual provider identity from approved available evidence before runtime acceptance.
2. **Myanmar eligibility:** IDs 4 (Asian Handicap) and 5 (Goals Over/Under) require Myanmar Odds. IDs 1 (Match Winner) and 45 (Corners) do not; NULL is legitimate for those two IDs. Use verified market ID, not raw name, to determine eligibility. Missing inputs or failed conversion on IDs 4/5 rejects the candidate before replacement.
3. **Market mapping:** normalize verified provider market names/identities to explicit canonical categories. Do not guess unknown aliases. Preserve unknown/unmapped market ID and raw name as diagnostic evidence.
4. **Scheduler/retries:** three-hour prematch cadence; timeout and HTTP 5xx defer to a later scheduled attempt with no immediate repeat. HTTP 429 honors `Retry-After` when present and enforces provider cooldown. Persist per-match failure count and next eligible retry time. No unsupported retry limit or cooldown duration may be invented.
5. **Snapshot/cache safety:** validate the full candidate before replacement; empty, invalid, incomplete, or unusable candidates preserve the valid snapshot. Commit before cache invalidation. Verify exact persisted state after ambiguous commit before retry or invalidation. Keep no-data, provider failure, and validation failure distinct.

Approved design preserves Provider → Service → SyncService → Repository → Database ownership. Analytics keeps ownership of its projection; the API/scheduler retain transaction commit/rollback and post-commit cache invalidation. Failure/retry metadata is recorded separately after rolling back the Odds snapshot transaction. This audit's original evidence findings remain historical evidence, not a claim that the approved target is implemented.

## 1. Scope and Evidence Standard

This document resolves the outstanding pre-implementation questions using the active source call paths and existing test fixtures. Source behavior is distinguished from test examples and from product/provider facts that cannot be established without live or independently persisted provider evidence.

Classifications:

- **VERIFIED** — directly established by the inspected active source, or by a test fixture as a fact about that fixture only.
- **INFERRED** — a likely intent or recommendation derived from source/test patterns, not established as a product requirement.
- **UNVERIFIED** — this repository cannot establish the fact.

No tests were executed. Some Odds tests perform database writes, and the audit does not need test execution. Existing test code is cited only as static evidence of fixtures and assertions; it is not treated as proof of deployed behavior or actual API-Football payloads.

## 2. Active Call-Path Summary

```text
Admin POST /api/matches/sync/{local_match_id}/odds
  -> football_service.odds_sync_service.refresh_odds(
       provider_fixture_id, local_match_id=local_match_id)
  -> OddsProvider.get_match_odds(provider_fixture_id)
  -> FootballAPIClient.get_with_metadata("/odds", fixture=provider_fixture_id)
  -> OddsSyncService parses response envelope and fixture identity
  -> OddsService._get_1xbet_bookmaker(id == 11)
  -> OddsService._filter_main_lines(bet IDs 1, 4, 5, 45)
  -> OddsRepository.replace_fixture_odds(local_match_id, rows)
  -> AnalyticsProjectionService.project_odds(...)
  -> API transaction commit
  -> cache deletion

Scheduler:
  -> _refresh_odds_job (registered every 360 minutes)
  -> future NS/TBD/PST Match candidates, 72-hour horizon
  -> skip if latest Odds.last_updated is less than 12 hours old
  -> same OddsSyncService path
  -> commit_odds_refresh
  -> invalidate_odds_cache_after_commit
```

The public `GET /api/matches/{match_id}/odds` reads cached data or serializes stored Odds rows. It does not issue the provider request or recalculate Myanmar Odds. Exact implementation evidence is listed below.

## 3. Bookmaker Verification

| Question / finding | Classification | Evidence and actual behavior |
|---|---|---|
| Which provider bookmaker ID is selected by the active normalizer? | **VERIFIED** | `OddsService._get_1xbet_bookmaker()` returns the first bookmaker whose `id == 11`; `_filter_main_lines()` applies only to that returned bookmaker. [app/services/odds_service.py](../app/services/odds_service.py#L162-L168) |
| Which bookmaker name is persisted? | **VERIFIED** for source behavior; actual provider value **UNVERIFIED** | `_filter_main_lines()` writes `bookmaker_data.get("name", "1xBet")` to `bookmaker_name`. Thus a name supplied by the response is persisted as-is; `"1xBet"` is only a fallback when the name key is absent. [app/services/odds_service.py](../app/services/odds_service.py#L193-L216) |
| Do repository and GET paths retain/filter bookmaker identity? | **VERIFIED** | Repository natural identity includes `bookmaker_name`, market, and selection; replacement operates on all rows for the local fixture. GET serializes each row's stored bookmaker name without a bookmaker filter. [app/repositories/odds_repository.py](../app/repositories/odds_repository.py#L15-L30), [app/repositories/odds_repository.py](../app/repositories/odds_repository.py#L34-L60), [app/services/odds_service.py](../app/services/odds_service.py#L222-L254) |
| Do existing fixtures identify ID 11 as 1xBet? | **VERIFIED only for fixtures** | Existing tests include provider fixtures with bookmaker `id: 11`, `name: "1xBet"` and a stored 1xBet row. These examples establish test assumptions, not the live API-Football mapping or the current production response. [tests/test_odds_service_myanmar_sync.py](../tests/test_odds_service_myanmar_sync.py#L95-L125), [tests/test_odds_phase3_hardening.py](../tests/test_odds_phase3_hardening.py#L200-L235) |
| Is 1xBet the product-intended source for persisted Odds? | **APPROVED** | User approved bookmaker ID 11 / 1xBet as the intended primary Odds source. Actual provider identity/name remains to be verified before runtime acceptance. |
| Which bookmakers does Analytics accept? | **VERIFIED** | `project_odds()` has `approved_bookmakers = {"bet365"}` and filters rows by this lowercased name. It does not accept 1xBet or a multi-bookmaker allowlist. [app/services/analytics_projection_service.py](../app/services/analytics_projection_service.py#L249-L268) |
| Can bookmaker filtering leave Analytics stale while Odds succeeds? | **VERIFIED current defect; target decision APPROVED** | Active Odds rows are sourced from ID 11. Analytics filters that input to Bet365; when nothing remains it reads/preserves existing Analytics rows and returns a zero-source projection result. With zero source and no rejection counters, `_result()` marks that result successful. The active sync service only raises when `projection["success"]` is false. Approved target aligns projection with 1xBet and canonical rows; zero accepted/reconciliation failure fails the shared outer transaction. [app/services/odds_sync_service.py](../app/services/odds_sync_service.py#L106-L120), [app/services/analytics_projection_service.py](../app/services/analytics_projection_service.py#L40-L52), [app/services/analytics_projection_service.py](../app/services/analytics_projection_service.py#L249-L274) |
| Is a Bet365 provider payload available to verify? | **UNVERIFIED** | No live provider call was made, and repository test fixtures inspected for the active sync use ID 11/1xBet rather than demonstrating an actual Bet365 response. |

### Bookmaker decision options

| Option | Compatibility / consequence |
|---|---|
| **A. Retain bookmaker ID 11 / 1xBet as primary and align Analytics Projection to it. APPROVED.** | Preserves current provider selection and meets the explicit product decision. Actual provider payload identity still requires verification. Projection remains Analytics-owned and participates in the same outer transaction. |
| B. Select a different single provider bookmaker. | Requires the product owner to identify the exact provider bookmaker ID and expected persisted name; neither is established here. |
| C. Support an explicit bookmaker allowlist or multiple bookmaker snapshots. | Requires a product decision on response shape, natural-key coexistence, UI selection behavior, and Analytics source support. Current normalizer selects only one bookmaker. |

**Approved:** Option A. Do not use Bet365 as a silent fallback. Before runtime acceptance, verify that the actual provider payload identifies bookmaker ID 11 as 1xBet; the code's fallback display name and test fixtures alone do not prove provider identity.

## 4. Market Names and Myanmar Odds

### 4.1 Active market selection and persisted names

| Provider bet ID | Active selection behavior | Raw market name behavior | Candidate canonical category |
|---:|---|---|---|
| 1 | Selects Home/Draw/Away only when all three labels exist. Also uses Home/Away prices to set the favorite override; equal prices currently choose `"AWAY"`. | Persists `bet["name"]` unchanged. A test fixture calls it `"Match Winner"`. | `MATCH_WINNER` |
| 4 | Forms Home/Away pairs by signed handicap key; selects the complete pair whose prices are closest to 2.00. | Persists `bet["name"]` unchanged. Tests use `"Asian Handicap"`. | `ASIAN_HANDICAP` |
| 5 | Groups Over/Under by line; selects the complete pair closest to 2.00. | Persists `bet["name"]` unchanged. Tests use `"Goals Over/Under"`. | `GOALS_OVER_UNDER` |
| 45 | Uses the same Over/Under line selection as ID 5. | Persists `bet["name"]` unchanged. A test fixture uses `"Corners Over/Under"`. | `CORNERS_OVER_UNDER` |

The supported ID set is `{1, 4, 5, 45}`. Names are not normalized to canonical values in the active Odds parser: `market_name` is taken from `bet.get("name", "")` and copied to the persistence row. [app/services/odds_service.py](../app/services/odds_service.py#L123-L160), [app/services/odds_service.py](../app/services/odds_service.py#L162-L216)

The canonical names shown above are accepted by the Analytics projection, which permits `MATCH_WINNER`, `ASIAN_HANDICAP`, `GOALS_OVER_UNDER`, and `CORNERS_OVER_UNDER`. This is not evidence that the active Odds service maps raw names to them; it does not. [app/services/analytics_projection_service.py](../app/services/analytics_projection_service.py#L249-L268)

### 4.2 Names and alias evidence

| Observed string | Evidence level | What it proves / does not prove |
|---|---|---|
| `Match Winner` | **VERIFIED in test fixture only** | A test sample uses this string for bet ID 1. It is not a verified API-Football production name. [tests/test_odds_service_myanmar_sync.py](../tests/test_odds_service_myanmar_sync.py#L100-L115) |
| `Asian Handicap` | **VERIFIED in test fixture and active Myanmar name gate** | Tests use it for ID 4; the active conversion helper requires the exact trimmed spelling. This does not establish that all provider responses use it. [app/services/odds_service.py](../app/services/odds_service.py#L73-L92), [tests/test_odds_service_myanmar_sync.py](../tests/test_odds_service_myanmar_sync.py#L130-L143) |
| `Goals Over/Under` | **VERIFIED in test fixture and active Myanmar name gate** | Tests use it for ID 5; the active conversion helper requires the exact trimmed spelling. [app/services/odds_service.py](../app/services/odds_service.py#L73-L92), [tests/test_odds_service_myanmar_sync.py](../tests/test_odds_service_myanmar_sync.py#L146-L157) |
| `Corners Over/Under` | **VERIFIED in test fixture only** | A test uses this for ID 45 and asserts Myanmar values are NULL. It is not live provider evidence. [tests/test_odds_service_myanmar_sync.py](../tests/test_odds_service_myanmar_sync.py#L250-L278) |
| Other spelling variants or provider aliases | **UNVERIFIED** | No actual response evidence in the inspected active source/tests establishes additional alias strings. |

**Approved normalization decision:** Normalize verified provider bet IDs to stable canonical categories and retain the original raw market ID/name as diagnostic evidence. Do not guess aliases or categories for unknown values. Provider display-name variants that are not established by approved evidence remain unmapped and must be reported diagnostically. Public API compatibility for canonical `market` values remains an implementation evidence gap; coordinate/version any externally visible change before rollout.

### 4.3 Myanmar eligibility and current failure behavior

| Market | Current active conversion behavior | Approved target eligibility | NULL interpretation |
|---|---|---|---|
| ID 1 / Match Winner | No conversion call; `myanmar_odd` remains absent and is persisted as NULL. | Not eligible. | NULL is legitimate. |
| ID 4 / Asian Handicap | Conversion is called only when raw name is exactly `"Asian Handicap"`; selected Home/Away pair and first selection's handicap are supplied. | Myanmar Odds required by verified ID, independent of raw name. | NULL on an accepted ID 4 pair is validation failure. |
| ID 5 / Goals Over/Under | Conversion is called only when raw name is exactly `"Goals Over/Under"`; selected Over/Under pair and line are supplied. | Myanmar Odds required by verified ID, independent of raw name. | NULL on an accepted ID 5 pair is validation failure. |
| ID 45 / Corners Over/Under | ID 45 is selected, but the ordinary tested name `"Corners Over/Under"` fails the conversion helper's exact-name gate, leaving NULL. | Not eligible for Myanmar conversion by approved decision. | NULL is legitimate. |

The active name gate accepts only `"Asian Handicap"` and `"Goals Over/Under"` and then checks IDs `{4, 5, 45}`. Consequently ID alone does not guarantee conversion. ID 45 may enter the helper but its typical test name is not an allowed conversion name. Conversely, an unexpected ID/name combination can reach the converter if both independent checks happen to pass. [app/services/odds_service.py](../app/services/odds_service.py#L73-L103)

Conversion inputs and behavior:

- `OddsService._filter_main_lines()` derives the optional favorite override from ID 1 Home/Away odds; equal prices select `"AWAY"`. That value is passed to the Asian Handicap conversion, but not to Goals Over/Under. [app/services/odds_service.py](../app/services/odds_service.py#L168-L190), [app/services/odds_service.py](../app/services/odds_service.py#L88-L119)
- Handicap selection parsing recognizes the first numeric substring and takes its absolute value. The conversion engine supports quarter increments through 25.00; unsupported values raise. [myanmar_odds/utils/handicap_parser.py](../myanmar_odds/utils/handicap_parser.py#L1-L25), [myanmar_odds/constants/base_index_map.py](../myanmar_odds/constants/base_index_map.py#L8-L44)
- Conversion can return `status="OUT_OF_RANGE"`, but the active Odds helper reads and attaches `market_label` without checking the status. The returned label can therefore be persisted rather than rejecting the candidate. [myanmar_odds/services/myanmar_odds_service.py](../myanmar_odds/services/myanmar_odds_service.py#L54-L126), [app/services/odds_service.py](../app/services/odds_service.py#L92-L120)
- Malformed decimal odds that fail `float()` parsing are skipped. There is no explicit finite/positive check: values such as zero, negative numbers, `NaN`, or infinity are not rejected by a dedicated validator at this boundary. A market missing its required selection pair is omitted; if another market yielded rows, those surviving rows can still be used for full fixture replacement. [app/services/odds_service.py](../app/services/odds_service.py#L123-L160), [app/services/odds_service.py](../app/services/odds_service.py#L193-L216), [app/services/odds_sync_service.py](../app/services/odds_sync_service.py#L91-L110)
- A parse/conversion exception occurs before the repository replacement call and propagates to the API/scheduler transaction owner; ordinary malformed values or incomplete pairs are generally skipped rather than raised. An `OUT_OF_RANGE` result is not an exception and is not currently rejected.

### 4.4 Approved market/eligibility matrix

| Bet ID | Canonical category | Evidence-backed sample name(s), not production-verified | Approved Myanmar treatment |
|---:|---|---|---|
| 1 | `MATCH_WINNER` | `Match Winner` (test fixture only) | No conversion; NULL allowed. |
| 4 | `ASIAN_HANDICAP` | `Asian Handicap` (test fixture and current gate) | Required conversion; selected Home/Away pair must produce valid nonempty labels. |
| 5 | `GOALS_OVER_UNDER` | `Goals Over/Under` (test fixture and current gate) | Required conversion; selected Over/Under pair must produce valid nonempty labels. |
| 45 | `CORNERS_OVER_UNDER` | `Corners Over/Under` (test fixture only) | No conversion; NULL allowed. |

**Approved by ID:** Myanmar is required for IDs 4 and 5 only. IDs 1 and 45 allow NULL. The strings above are source/test evidence, not verified live provider aliases; verify actual provider values before runtime acceptance. Do not gate eligibility on these raw strings.

## 5. Retry, Failure, Transaction, and Cache Audit

### 5.1 Existing behavior

| Area | Verified current behavior | Evidence |
|---|---|---|
| Provider call and timeout | OddsProvider uses `GET /odds` with `fixture=<provider_fixture_id>` via `get_with_metadata`. The metadata client defaults to 30 seconds and 2 retries. | [app/providers/odds_provider.py](../app/providers/odds_provider.py#L6-L15), [app/services/base/football_client.py](../app/services/base/football_client.py#L96-L166) |
| Timeout / transport exception retries | Up to two immediate retries (three attempts total); there is no delay/backoff in the inspected loop. | [app/services/base/football_client.py](../app/services/base/football_client.py#L113-L157) |
| HTTP 5xx | Retried immediately up to two times; after that returned as provider error. | [app/services/base/football_client.py](../app/services/base/football_client.py#L126-L144) |
| HTTP 429 / rate limit | Returned without retry by `request_with_metadata`; it is categorized as `rate_limit`. No `Retry-After` handling appears in the method. | [app/services/base/football_client.py](../app/services/base/football_client.py#L126-L142) |
| Other HTTP errors / provider envelope errors | Returned to `OddsSyncService`, which returns an `error` result. The active API route rolls back and returns 502; scheduler treats it as failed. | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py#L55-L73), [app/api/matches.py](../app/api/matches.py#L381-L438), [app/services/scheduler.py](../app/services/scheduler.py#L867-L876) |
| Empty response | Returns a no-data result without an `error` or `status`. Scheduler then proceeds to commit and cache invalidation; it can increment refreshed metrics. Admin API rejects it because `status != SUCCESS`, rolls back, and returns 502. | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py#L75-L77), [app/services/scheduler.py](../app/services/scheduler.py#L867-L923), [app/api/matches.py](../app/api/matches.py#L392-L417) |
| Incomplete/malformed candidate | Some malformed values/lines are skipped. A nonempty remainder reaches `replace_fixture_odds()`, which deletes the existing fixture snapshot before inserting the new rows. Empty replacement rows would also delete all rows if called directly, though the active sync path guards the repository call with `if odds_to_upsert`. | [app/services/odds_service.py](../app/services/odds_service.py#L123-L216), [app/services/odds_sync_service.py](../app/services/odds_sync_service.py#L91-L110), [app/repositories/odds_repository.py](../app/repositories/odds_repository.py#L34-L60) |
| Retry on later scheduler run | Scheduler is currently registered every 360 minutes, considers matches in a 72-hour future window, and skips a match when the latest Odds row is less than 12 hours old. It has no Odds-specific persisted attempt counter or retry queue. A recent old snapshot may suppress another provider attempt even after a failed refresh. | [app/services/scheduler.py](../app/services/scheduler.py#L46-L49), [app/services/scheduler.py](../app/services/scheduler.py#L194-L253), [app/services/scheduler.py](../app/services/scheduler.py#L797-L865) |
| Failure classification methods | Scheduler calls `classify_retry_state`, and commit handling calls `is_commit_ambiguous` and `verify_commit_unknown`; none is defined on the active `OddsSyncService`. The current service defines only `COMMIT_UNKNOWN`, `COMMIT_CONFIRMED`, `CACHE_INVALIDATION_FAILED`, `SUCCESS`, and `refresh_odds` among its state/classification surface. | [app/services/scheduler.py](../app/services/scheduler.py#L64-L120), [app/services/scheduler.py](../app/services/scheduler.py#L909-L917), [app/services/odds_sync_service.py](../app/services/odds_sync_service.py#L17-L140) |
| Existing tests vs active classifier source | A hardening test imports `IDENTITY_BOUNDARY_VIOLATION`, `RETRYABLE`, `TERMINAL`, and calls `classify_failure_state`; these names/method are absent from the inspected active OddsSyncService source. The test also has database-writing cases. Tests were not run. | [tests/test_odds_phase3_hardening.py](../tests/test_odds_phase3_hardening.py#L13-L21), [tests/test_odds_phase3_hardening.py](../tests/test_odds_phase3_hardening.py#L188-L198), [app/services/odds_sync_service.py](../app/services/odds_sync_service.py#L17-L140) |
| Existing test market expectation vs active parser | The hardening E2E test queries for canonical `MATCH_WINNER`, while active `_filter_main_lines()` persists `bet["name"]` unchanged (the fixture supplies `Match Winner`). This is a static source/test contract mismatch; no test was executed. | [tests/test_odds_phase3_hardening.py](../tests/test_odds_phase3_hardening.py#L262-L297), [app/services/odds_service.py](../app/services/odds_service.py#L193-L216) |
| Database write and transaction owner | Sync service replaces/flushes but does not commit. API and scheduler own commit/rollback. | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py#L91-L114), [app/api/matches.py](../app/api/matches.py#L392-L438), [app/services/scheduler.py](../app/services/scheduler.py#L64-L120) |
| Definite/ambiguous commit | On normal scheduler commit return, helper reports refreshed. If commit raises, it calls missing `is_commit_ambiguous`; the resulting attribute error escapes that helper and reaches the per-match exception handler. It cannot execute the intended fresh-session verification call to missing `verify_commit_unknown`. Its `refresh_result.get("_transaction_context", {})` is empty for active refresh results because `refresh_odds()` does not attach that field. The repository's `snapshot_matches_rows()` checks only natural keys, not odd values or Myanmar labels. | [app/services/scheduler.py](../app/services/scheduler.py#L64-L120), [app/services/odds_sync_service.py](../app/services/odds_sync_service.py#L91-L128), [app/repositories/odds_repository.py](../app/repositories/odds_repository.py#L63-L74) |
| Admin commit failure | API calls `db.commit()` and then cache deletion. Its exception handler rolls back and re-raises; there is no ambiguous outcome verification in this path. | [app/api/matches.py](../app/api/matches.py#L400-L428) |
| Cache invalidation failure | Admin catches deletion exceptions and returns `cache_invalidated: false`; scheduler catches deletion failure and adds the local match to an in-memory pending set for another deletion attempt on a later job run. Neither path rolls back already committed Odds. | [app/api/matches.py](../app/api/matches.py#L406-L418), [app/services/scheduler.py](../app/services/scheduler.py#L122-L145), [app/services/scheduler.py](../app/services/scheduler.py#L839-L857) |
| Cache invalidation ordering | In normal nonempty flow, scheduler invalidates after the commit helper reports refreshed, and API invalidates after `db.commit()`. Scheduler's empty-result case also falls through to commit/invalidation because empty result has no `error`; this violates the desired success-only semantics. | [app/services/scheduler.py](../app/services/scheduler.py#L867-L893), [app/api/matches.py](../app/api/matches.py#L392-L418) |
| No eligible Myanmar markets | Rows with no attached Myanmar value are persisted with `record.get("myanmar_odd")` -> NULL. There is no explicit validator distinguishing an allowed NULL market from a missing required conversion. | [app/services/odds_sync_service.py](../app/services/odds_sync_service.py#L95-L107) |

### 5.2 Recommended explicit policy

This is the approved target policy. It supersedes the earlier tentative numerical suggestions; no unsupported retry limit or cooldown duration is set here.

| Outcome | Approved classification | Approved handling |
|---|---|---|
| Timeout / connection failure | `PROVIDER_FAILED: TIMEOUT` | No immediate repeat. Preserve the previous snapshot and defer to a later eligible scheduled attempt. Persist failure count and next eligible time. |
| HTTP 5xx | `PROVIDER_FAILED: SERVER_ERROR` | No immediate repeat. Preserve the previous snapshot and defer to a later eligible scheduled attempt. |
| HTTP 429 | `PROVIDER_FAILED: RATE_LIMITED` | Honor `Retry-After` when present and enforce provider cooldown. Persist per-match failure count and next eligible time. Numeric fallback cooldown and retry budget require evidence/justification and remain unset. |
| Empty response / missing configured bookmaker | `NO_DATA` | Preserve old snapshot and do not invalidate cache. Record distinctly from provider and validation failures; persist next eligible attempt. Numeric cooldown remains unset. |
| Invalid/incomplete data or required Myanmar conversion failure | `VALIDATION_FAILED` | Reject the whole candidate and preserve old snapshot. Do not repeat the same response immediately; record outcome and defer to an eligible later attempt. |
| Valid candidate with no eligible Myanmar markets | `UPDATED` if all other checks pass | Persist NULL for IDs 1/45; this is not a conversion error. |
| DB write/flush failure before commit | `PERSISTENCE_FAILED` | Roll back snapshot transaction; preserve prior committed data; no cache invalidation. Retry only after a clean transaction and due eligibility. |
| Definite commit failure | `COMMIT_FAILED` | Do not report success or invalidate. Retry only after transaction state is known and retry eligibility permits it. |
| Ambiguous commit | `COMMIT_UNKNOWN` | Fresh-session verification must match exact expected business keys, values, Myanmar labels, and completeness. No retry or invalidation until resolved. |
| Cache invalidation failure after confirmed commit | `CACHE_INVALIDATION_FAILED` separate from persistence | Keep committed DB data; retry deletion, not provider fetch. |
| Repeated failures | Persisted per-match retry state | Persist failure count and next eligible time. No maximum attempts, cooldown duration, backoff, or terminal threshold is approved without evidence. |

The approved policy sets no numeric retry limit or cooldown duration. The existing client currently makes two immediate retries for transport/5xx; the approved Odds behavior must not inherit those immediate retries. Any numeric retry/cooldown/fallback value must be justified by provider documentation, quota evidence, or an explicit product/SRE decision. Failure-state storage must be durable across process restarts and workers.

## 6. Decision Status and Remaining Implementation Questions

| Decision / question | Status | Evidence gap or implementation requirement |
|---|---|---|
| Primary bookmaker | APPROVED: ID 11 / 1xBet. No Bet365 substitution. | Verify actual provider ID/name from available evidence before runtime acceptance. |
| Analytics source | APPROVED: align projection with 1xBet and canonical rows. | Keep projection owned by Analytics; zero accepted/reconciliation failure must fail the shared snapshot transaction. |
| Market normalization | APPROVED: canonical categories by verified IDs; unknown evidence retained, no guessed aliases. | Real provider raw-name aliases and public client compatibility remain unverified. |
| Myanmar-required markets | APPROVED: IDs 4/5 required; IDs 1/45 NULL allowed. | Implementation must key eligibility on verified ID and validate every required conversion result. |
| Snapshot validation | APPROVED: reject entire invalid/incomplete/unusable candidate before replacement. | Define structural completeness rules and unknown-market diagnostic retention without dropping evidence. |
| Scheduler and retry | APPROVED: 3-hour prematch cadence; timeout/5xx deferred, no immediate repeat; 429 honors Retry-After and cooldown. | Numeric cooldown, retry limits, due-time rules, and durable state design are unapproved implementation parameters. |
| Failure state | APPROVED: persist per-match failure count and next eligible time. | No Odds-specific persistent state is present in inspected source; determine repository/storage owner and whether schema approval is needed. |
| Ambiguous commit | APPROVED: exact fresh-session snapshot verification before retry or cache invalidation. | Current hooks are missing, result lacks expected snapshot context, and repository helper compares natural keys only. |

## 7. Final Assessment

**Design decisions approved; implementation not authorized by this audit.** The outstanding product decisions in bookmaker intent, Myanmar eligibility, Analytics alignment, canonicalization approach, and broad retry behavior are resolved. Implementation readiness still depends on verifying actual provider identity/market evidence, API compatibility, retry numeric parameters, durable retry-state storage, and exact commit verification.

Highest-risk confirmed mismatches:

1. The active Odds path selects bookmaker ID 11; Analytics accepts only `bet365`, so active source rows are filtered out of Analytics.
2. Raw provider market names are persisted without canonicalization, while Analytics expects canonical market names.
3. The active Myanmar conversion is gated by both ID and exact name; it does not validate conversion status, and malformed/incomplete lines may be silently dropped before partial replacement.
4. Empty scheduler results can be treated as refreshed and cause cache invalidation.
5. Scheduler retry/ambiguous-commit methods referenced by the active job are absent from `OddsSyncService`; exact ambiguous-commit verification is not wired.
6. Existing hardening tests reference failure-classification names/methods that are absent from the active service source. No tests were run because database-writing test cases exist.

## 8. Documentation-Only Change Confirmation

This approval update is limited to the two Odds design Markdown documents. No application source, tests, configuration, database schema/data, scheduler state, provider, Redis/cache, or runtime state was changed. No API-Football request, synchronization endpoint, scheduler job, database query, or test suite was executed.
