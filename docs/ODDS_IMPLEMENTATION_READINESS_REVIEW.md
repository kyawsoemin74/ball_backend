# Odds Implementation Readiness Review

**Review date:** 2026-10-09
**Scope:** Read-only Phase 2.5 approval gate
**Verdict:** **NOT READY**
**Phase 3 authorization:** Not granted

This is a historical readiness assessment. The later approved Final Design supersedes its unknown-market ambiguity: IDs 1, 4, and 5 are required, ID 45 is optional, and other IDs are ignored before normalization. Other readiness findings are not re-assessed by that decision.

## 1. Executive Verdict

The approved Final Design and Implementation Plan agree on the intended bookmaker, market categories, Myanmar eligibility, prematch refresh policy, snapshot preservation, and API/Scheduler transaction ownership. The plan identifies most necessary changes and a useful acceptance matrix.

Phase 3 should **not** begin yet. Transaction recovery is not sufficiently specified to prevent a false commit confirmation: the plan does not define a durable transaction/snapshot identity or require exact verification of both Odds and Analytics state, while the current scheduler's recovery helper calls methods absent from the active sync service and the manual API route commits directly. A persisted Odds row set can also predate an ambiguous attempt and therefore cannot by itself prove that the corresponding Analytics projection committed.

Additional blockers are:

1. Odds-owned retry/cooldown storage has not been approved, and numeric Retry-After fallback/cooldown/backoff semantics are unresolved.
2. The plan does not explicitly require the manual admin sync route to enforce the same due-time and provider-cooldown gates as the scheduler.
3. The plan has inconsistent handling of unknown market IDs: one implementation phase says to fail closed on unknown IDs, while its recovery matrix allows known valid markets to proceed with unknowns recorded diagnostically.
4. Current checked-in Odds test modules statically import an absent `app.services.odds_identity` module and constants absent from `OddsSyncService`. Tests were not run, so runtime collection failure is not asserted, but the test baseline is not established.

The bookmaker and market IDs/names in source fixtures establish test assumptions, not actual API-Football identity. That evidence gap blocks runtime acceptance, but not by itself the implementation of strict identity validation. Flutter/client source is absent; the plan's recommendation to preserve the existing GET contract is the safe target and should be mandatory unless separate compatibility approval is obtained.

## 2. Git Baseline and Review Scope

- Read-only Git inspection found no tracked or staged diff. The working tree contains untracked Odds audit/design documents, including the three required documents; they were preserved. The review document is the only file created for this phase.
- Reviewed:
  - `docs/ODDS_FINAL_DESIGN_FREEZE.md` (revision 2.0; approved decisions and recovery matrix).
  - `docs/ODDS_PRE_IMPLEMENTATION_DECISIONS.md` (approved decisions and evidence classifications).
  - `docs/ODDS_IMPLEMENTATION_PLAN.md` (call paths, proposed file changes, phases, failure matrix, tests and migration proposal).
  - Active Odds provider/service/sync/repository, API, scheduler, cache, Analytics projection, shared provider client and monitoring code.
  - Odds/Match/Analytics models and retry-state models/migrations.
  - Odds, Analytics and scheduler test modules, statically only.
- No Flutter/Dart files or Odds-specific JSON payload fixture were found in the current repository. Available Odds examples are constructed in tests. Existing temporary logs searched for Odds/provider terms contained no matching evidence.
- No runtime database connection was attempted: there is no approved, unambiguous read-only connection/procedure identified for this review.
- No tests, provider calls, syncs, endpoints, migrations, database/cache operations, or scheduler jobs were executed.

## 3. Approved Design Consistency Matrix

The consistency assessment compares the Final Design as the target against the plan, not against the current implementation.

| Requirement | Assessment | Evidence and readiness note |
|---|---|---|
| Bookmaker ID 11 / 1xBet; no Bet365 substitution | **PASS** | The approved decision is explicit in the Final Design and pre-implementation report. The plan calls for strict identity checking and a narrow Analytics alignment. Current source selects the first ID 11 bookmaker but does not validate its name; Analytics currently allows only Bet365. See `OddsService._get_1xbet_bookmaker` and `AnalyticsProjectionService.project_odds` below. |
| Actual provider identity verified before runtime acceptance | **UNVERIFIED — runtime blocker** | No captured response or approved persisted source identity was found. Test payloads are not provider evidence. |
| IDs 1/4/5/45 map to the four approved canonical categories | **PASS with evidence gap** | The documents agree on the mapping and using IDs as identity. Real provider ID/name correspondence remains unverified. |
| Unknown markets receive no guessed alias and retain raw diagnostic identity | **RESOLVED by later approved Final Design** | Filter to IDs `{1, 4, 5, 45}` before normalization; require valid IDs 1/4/5, keep 45 optional, and ignore other IDs without affecting completeness. |
| Myanmar conversion required for IDs 4/5; NULL allowed for IDs 1/45 | **PASS** | The plan and approved documents agree on ID-based eligibility, pre-persistence conversion, and whole-candidate rejection on required conversion failure. Current service still gates by exact names and includes ID 45 in its helper guard. |
| Three-hour schedule; prematch only; retain 72-hour horizon and 12-hour freshness baseline | **PASS** | Plan targets three hours and preserves the horizon/freshness baseline. It correctly requires a valid complete snapshot for freshness skips. Current schedule is 360 minutes and currently skips on latest-row timestamp alone. |
| Whole candidate validated before snapshot replacement | **PASS as target** | Plan requires full validation before repository mutation. Current parser skips malformed/incomplete subsets and repository replacement deletes all rows before upsert when given nonempty input. |
| Timeout/5xx deferred; 429 honors Retry-After and shared cooldown | **PASS as target; policy blocker** | Plan preserves non-Odds shared client defaults and proposes per-call Odds policy. The header contract, cooldown scope, invalid/missing-header behavior and any maximum/fallback duration still need approval. |
| Durable per-match failure count and next eligible time | **PASS as target; schema blocker** | Plan proposes Odds-owned persistence and correctly rejects reuse of unrelated retry workflows. No such Odds state exists now. |
| Distinct no-data/provider/validation/database/commit/cache outcomes | **PASS as target** | Plan's outcome matrix separates them; current `refresh_odds` response shapes and `observe_sync` do not reliably do so. |
| Cache invalidation only after confirmed commit | **PASS as target; transaction blocker** | Plan states the rule and proposes durable pending invalidation. Current scheduler uses an in-memory set; API commit is direct; ambiguous verification is not operationally complete. |
| Architecture and transaction ownership | **PASS in principle; implementation safety unresolved** | The documents keep Provider transport, Odds domain validation, SyncService orchestration, repository SQL, Analytics-owned projection and API/Scheduler-owned transactions. Exact ambiguous-commit proof and the route-level recovery wiring remain unresolved and block readiness. |
| Public GET contract | **PASS if plan's conservative choice is binding** | Plan preserves fields and provider display-name semantics. Any change to `market` requires separate compatibility approval; no client evidence exists. |

The design document's conditional exclusion of schema work is not a contradiction: it allows migration work if review demonstrates it is needed. Durable per-match state and a shared cooldown are required by the approved behavior and are not currently represented, so a dedicated additive migration is the likely result.

## 4. Bookmaker and Market Evidence

Evidence levels used here:

- **VERIFIED:** direct repository or approved runtime evidence proves the claim.
- **SOURCE_ONLY:** code proves what the implementation selects or expects, not what the provider actually returned.
- **INFERRED:** plausible interpretation, not directly established.
- **UNVERIFIED:** evidence is absent or inadequate.

| Claim | Classification | Evidence and limitation | Blocks |
|---|---|---|---|
| The active Odds selector chooses bookmaker ID 11 | **SOURCE_ONLY** | `OddsService._get_1xbet_bookmaker` returns the first entry whose `id == 11` (`app/services/odds_service.py:165-166`). | No; this is current code behavior, not provider identity proof. |
| ID 11 is actually named 1xBet in API-Football responses | **UNVERIFIED** | The normalizer persists the response name or defaults a missing name to `"1xBet"` (`app/services/odds_service.py:193-216`). Existing test payloads use ID 11/name 1xBet (`tests/test_odds_service_myanmar_sync.py:95-125`, `tests/test_odds_identity_resolution_bridge.py:29-50`); they are fixtures, not captured responses. Odds rows store only `bookmaker_name`, not provider bookmaker ID (`app/models/odds.py:8-25`). No relevant captured payload or approved runtime evidence was found. | Blocks runtime acceptance. Implementation must reject missing/mismatched identity rather than rely on the fallback. |
| Duplicate ID 11 entries are unambiguous | **UNVERIFIED** | `_get_1xbet_bookmaker` selects the first match; no duplicate rejection occurs in the active helper. No live duplicate response evidence exists. | Blocks choosing a deterministic duplicate policy; implementation should reject ambiguity unless approved evidence supports a rule. |
| Analytics currently accepts 1xBet | **SOURCE_ONLY — disproved by source** | `AnalyticsProjectionService.project_odds` currently filters on `{"bet365"}` and canonical market strings (`app/services/analytics_projection_service.py:249-274`). | Must be changed in the explicitly approved Odds projection scope before end-to-end acceptance. |
| Provider market IDs 1, 4, 5, 45 mean the approved categories in actual provider responses | **SOURCE_ONLY / UNVERIFIED at provider level** | Active selection code targets those IDs (`app/services/odds_service.py:123-190`); test payloads use the associated sample names. No actual captured provider payload was found. | Blocks runtime acceptance of identity/aliases; implementation may encode only the user-approved ID mapping and retain unknown evidence. |
| `Match Winner`, `Asian Handicap`, `Goals Over/Under`, and `Corners Over/Under` are exhaustive provider names | **UNVERIFIED** | These spellings occur in current code/test fixtures. No evidence establishes provider spelling variants or exhaustiveness. | Blocks inventing aliases; unknown names must remain diagnosable. |
| Public GET returns raw stored market display names | **SOURCE_ONLY** | `GET /api/matches/{match_id}/odds` calls `get_cached_odds` (`app/api/matches.py:301-312`); serialization uses `Odds.market_name` as `market` (`app/services/odds_service.py:222-254`). | No if preserved. |
| Flutter relies on current values or handles NULL exactly as intended | **UNVERIFIED** | No Flutter/Dart source was found in the workspace. | Blocks public `market` value changes, not internal canonical normalization. |

**Safe compatibility decision:** preserve current GET path, response fields/types, raw display `market` value, and stored Myanmar value. Carry canonical identity separately for validation and Analytics. Do not change public semantics or return a canonical category in `market` without explicit version/compatibility approval.

## 5. Retry and Cooldown Policy Review

### Current source behavior

- `OddsProvider.get_match_odds` calls `get_with_metadata("/odds", params={"fixture": match_id})` (`app/providers/odds_provider.py:6-15`).
- The concrete `FootballAPIClient.request_with_metadata` defaults to two retries, immediately retries transport exceptions and HTTP 5xx, and has no inter-attempt delay (`app/services/base/football_client.py:96-157`). `FootballAPIResponse` has no response-header/Retry-After field (`:30-36`). The provider protocol exposes only `get`/`post`; it does not express this metadata/retry interface (`app/providers/base/football_api_client.py:1-12`).
- A per-request retry option can preserve unrelated consumers' defaults, but the active provider adapter currently has no way to pass it. Any implementation must test all client call paths and leave shared defaults unchanged for unrelated modules.
- Monitoring currently reports generic sync outcomes. `observe_sync` treats a returned dict without `error` or `success == False` as success (`app/monitoring.py:240-276`). The current no-data result lacks those failure indicators (`app/services/odds_sync_service.py:81-83`), so it may be counted as success.

### Decisions required before safe implementation

| Question | Review finding / required decision |
|---|---|
| Avoid immediate Odds retries without changing other clients? | **Technically feasible, not yet wired.** Add an explicit per-call policy through `OddsProvider` to the concrete client. Keep current client defaults for callers that do not opt in. The provider protocol/type contract must accurately represent optional metadata and retry policy. |
| Parse and bound `Retry-After` safely? | **Not yet specified.** Preserve raw header metadata only at transport boundary; parse valid delta-seconds or HTTP-date to a UTC due time; reject malformed/negative values safely. An upper bound or treatment of extreme future dates is a numeric policy and requires provider/SRE justification or owner approval. Do not invent a cap. |
| Where does provider-wide cooldown live? | **Schema/ownership blocker.** Persist in an Odds-owned provider/account-scope state row, with an approved non-secret quota-scope key and atomic concurrent updates. A process-local value is insufficient across workers/restarts. |
| Where does per-match state live and survive restart? | **Schema blocker.** Use an Odds-owned state model/repository keyed by local `Match.local_match_id`, with failure count, next eligible time, outcome/category, sanitized diagnostics and durable cache-pending state. Existing `MatchFinalization` and `LeagueIdentityRecovery` tables belong to unrelated workflows and must not be reused. |
| What if retry-state persistence fails? | **Fail-closed behavior needs a precise operational contract.** The plan says to surface failure and fail closed for cooldown safety. Specify that no further affected provider request proceeds until required cooldown/retry state is durable or explicitly reconciled; otherwise a 429 could trigger more calls after restart. |
| How does due-time interact with freshness? | **Plan target is sound for Scheduler:** an eligible due retry overrides the 12-hour freshness skip, while active provider cooldown blocks calls. Implement the same preflight check on the manual admin route; the plan does not explicitly state whether that route can bypass due time/cooldown. |
| How to avoid starvation without arbitrary retry caps? | Persist an explicit due time and use bounded/ordered batches with stable ordering only if justified by expected volume. Do not set an attempt cap/backoff absent evidence. Define how overdue work is fairly selected and how an unavailable state store affects the queue. |

Three-hour scheduler cadence is an approved scheduling target, not a complete backoff/cooldown policy. It can serve as the next retry opportunity only if that interpretation is explicitly selected; it does not define 429 fallback behavior or manual-route behavior.

## 6. Database and Migration Recommendation

### Current schema evidence

- `Odds` stores local fixture identity, nullable bookmaker display name, market display name, selection, decimal text, optional Myanmar label and timestamp; it has no provider bookmaker/market IDs or retry state (`app/models/odds.py:7-28`).
- `Match.local_match_id` is the local primary key, while `provider_fixture_id` is separately unique within provider (`app/models/match.py:8-20`). `Odds.fixture_id` references `matches.local_match_id` (`app/models/odds.py:11`).
- No Odds-owned failure counter, next eligible timestamp, provider cooldown or durable cache-pending store was found in active models/migrations. Scheduler cache recovery is `pending_odds_cache_invalidations: set[int]` (`app/services/scheduler.py:168`).
- Existing `MatchFinalization` and `LeagueIdentityRecovery` models/migrations show durable retry-state patterns, but belong to their own workflows (`app/models/match_finalization.py:7-41`, `app/models/league_identity_recovery.py:7-35`; migrations `alembic/versions/20261005_create_match_finalization.py` and `alembic/versions/20260922_create_league_identity_recovery.py`). Neither is an appropriate Odds extension point.

### Proposal for separate approval — not implemented

1. Add an Odds-owned `odds_refresh_state` keyed by local `match_id`, FK to `matches.local_match_id` with documented delete behavior. Include nonnegative failure count, nullable timezone-aware `next_eligible_at`, last outcome/category and attempted time, bounded sanitized diagnostic data, timestamps, and `cache_invalidation_pending`.
2. Add provider/account-scope cooldown persistence (or another explicitly approved durable representation) with `cooldown_until`, last status/category, and timestamps. The key must reflect shared quota ownership without storing credentials.
3. Index due retry state and pending cache invalidations; define uniqueness, transaction/row-locking/upsert semantics, diagnostic retention and privacy limits.
4. Set cache-pending atomically with successful Odds/Analytics persistence before main commit; clear it after successful cache deletion in a separate transaction. A failed clear must cause idempotent cache deletion later, not provider refetch.
5. Record failed-refresh state in a fresh transaction after the snapshot transaction has rolled back/closed and the transaction-scoped Match lock has been released; reacquire or otherwise serialize per Match before writing.
6. Apply an additive Alembic migration before deploying dependent code. No historical Odds backfill is necessary for empty state rows. Downgrade loses pending retries/cooldowns/cache work and must be operationally coordinated.

**Approval required:** schema owner/DBA review of table shape, provider-scope key, constraints/indexes, locking, failure-state retention, migration rollout and downgrade behavior. Production data risk is low for additive empty tables; concurrency, provider-call amplification, and lost pending recovery state are material operational risks.

## 7. Transaction and Cache Recovery Review

### What is correctly defined in the plan

- Provider/normalization/write responsibilities follow the frozen architecture.
- Odds and Analytics projection are intended to share the API/Scheduler-owned transaction; projection remains Analytics-owned.
- Invalid/no-data candidates do not replace rows; definite write/projection failure rolls back.
- Cache deletion is post-commit only, and cache failure is separate from committed persistence.
- Failure-state writes occur after rollback in a separate transaction, with lock reacquisition; cache-pending work is proposed as durable.

### Blocking gaps in the current code and plan

1. **Recovery helper is not operationally complete.** `commit_odds_refresh` exists in `app/services/scheduler.py:64-120`, but invokes `OddsSyncService.is_commit_ambiguous`, `verify_commit_unknown`, and `classify_retry_state`; none is defined in the active `app/services/odds_sync_service.py` (which defines only the current outcome constants and `refresh_odds`, `:17-140`). The active refresh result also does not include `_transaction_context` or expected persistence rows (`:45-140`).
2. **Manual API path bypasses the helper.** `sync_match_odds_route` commits directly and handles general exceptions with rollback (`app/api/matches.py:381-438`). It has no equivalent ambiguous-commit verification before deciding the outcome/cache action.
3. **Current verifier is too weak.** `OddsRepository.snapshot_matches_rows` compares only sets of `(bookmaker_name, market_name, selection)` keys (`app/repositories/odds_repository.py:52-66`); it does not compare odds, Myanmar values, timestamps/version, or Analytics projection.
4. **Plan verification evidence is not sufficient for all ambiguous cases.** Exact Odds keys and values are necessary but not sufficient if the same snapshot existed before the attempted transaction. Require a fresh-session check of both the expected Odds snapshot and its Analytics projection, or an approved transaction/version/digest marker written atomically with both. Define comparison semantics for timestamp and identical-content snapshots. No commit retry or cache invalidation is safe until verification is conclusive.
5. **Lock behavior must be explicit across state transactions.** `run_with_resource_lock` uses `pg_try_advisory_xact_lock` (`app/services/resource_lock.py:10-13,35-56`), so rollback releases the transaction-scoped lock. Failure-state persistence must happen after rollback and reacquire/serialize; do not rely on the original lock.
6. **Cache recovery is process-local today.** Scheduler keeps failed invalidations in a `set[int]` and attempts deletion on the next pass (`app/services/scheduler.py:168,839-856`). A restart loses the pending set.
7. **Success is currently reported before outer commit.** `refresh_odds` returns `status: SUCCESS` after flush/projection but before the caller commits (`app/services/odds_sync_service.py:109-140`). The outer owner must distinguish candidate-ready from committed success.
8. **Analytics can currently accept zero rows as success.** The projection filters for Bet365 and canonical markets; with zero projected rows it preserves existing data and returns its result (`app/services/analytics_projection_service.py:249-274`). The SyncService only raises on a false `success` result (`app/services/odds_sync_service.py:109-111`).

These gaps leave commit certainty, prior-identical-snapshot handling, and Analytics consistency under ambiguous commit unresolved. This is the principal reason for **NOT READY**, rather than READY WITH EXPLICIT BLOCKERS.

## 8. API and Client Compatibility

- Current endpoint is `GET /api/matches/{match_id}/odds` (`app/api/matches.py:301-312`).
- The read service returns top-level `source`, `odds`, `cached`, and `match_started`; each row contains `bookmaker`, `market`, `selection`, `odd`, `myanmar_odd`, and `updated_at` (`app/services/odds_service.py:222-254`).
- The `market` value is presently `Odds.market_name`, which is populated from raw provider market name by the active sync path (`app/services/odds_service.py:193-216`).
- No Flutter/Dart consumer source is available, so comparisons of exact market strings, bookmaker assumptions, NULL handling and caching are **UNVERIFIED**.

**Recommendation:** preserve the existing route, method, fields, types and display-name semantics; normalize by ID internally and give Analytics canonical categories through its internal projection input. Do not persist a canonical string in the sole public display field unless raw display data can be preserved. If the public `market` value must change, require explicit API/client compatibility and versioning approval first.

## 9. Test and Rollout Readiness

### Coverage in the plan

The plan has measurable acceptance criteria for full snapshot validation/replacement, empty/incomplete preservation, market-ID mapping, unknown diagnostics, Myanmar-required versus NULL markets, provider timeout/5xx/429, durable state, Analytics consistency, rollback, ambiguous commit, post-commit cache handling, scheduler cadence/freshness and GET compatibility. It correctly requires mocked provider transport and a disposable database for DB-backed coverage.

### Existing test baseline concern

- The plan lists several useful suites but omits `tests/test_odds_identity_mapping.py` and `tests/test_odds_identity_resolution_bridge.py`.
- `tests/test_odds_identity_mapping.py:3-8` imports `app.services.odds_identity`, but no such active module exists under `app/services`; only the test refers to those normalization functions.
- `tests/test_odds_identity_resolution_bridge.py:8-27` imports many constants/methods from `OddsSyncService` that are absent from the active service. `tests/test_odds_phase3_hardening.py:13-20` also imports absent state constants. This static mismatch makes collection/compatibility suspect; tests were not run, so no claim is made that collection definitely fails.
- The test suite includes DB-writing tests (`tests/test_odds_phase3_hardening.py:55-117` and subsequent integration cases); future execution requires an explicitly isolated disposable database and mocked provider. No test was executed in this review.

### Required test-plan additions before Phase 3 approval

1. Reconcile test modules/imports with the active implementation and document whether the identity-mapping module is deleted, legacy, or intended to become active. Do not silently revive an alternative implementation.
2. Add end-to-end tests proving both admin and scheduler entry points enforce retry eligibility/cooldown and transaction/cache rules.
3. Add ambiguous-commit tests where the candidate equals pre-existing Odds data but Analytics is stale; assert no false confirmation absent exact transaction-consistent verification.
4. Test Retry-After parsing (delta seconds/date, malformed, negative, extreme future) without choosing an unsupported maximum.
5. Test retry-state persistence failure, especially failure to persist a 429 provider cooldown, and verify no further provider call proceeds unsafely.
6. Pin provider call counts: one Odds transport attempt for timeout/5xx, no cross-module default retry regression, no request while cooldown is active.
7. Test cache pending survives a simulated process restart and can be cleared without OddsProvider calls.
8. Run migration upgrade/downgrade only on a disposable test database in a separately authorized implementation phase.

## 10. Blocking Issues vs Nonblocking Evidence Gaps

| Priority | Issue | Owner / decision | Exact clearing condition |
|---|---|---|---|
| **Blocking — transaction safety** | Ambiguous commit confirmation can mistake a pre-existing identical Odds snapshot for the attempted commit; plan does not require exact Analytics verification or an atomic version marker. API manual path has no ambiguous-commit handler; scheduler calls missing methods. | Odds/API/Scheduler implementer and DB/transaction reviewer | Define a proof contract covering Odds and Analytics (or an atomic snapshot version/digest), include expected verification context in refresh result, wire both entry points, and test present/absent/inconclusive and identical-prestate cases. |
| **Blocking — test baseline** | Relevant tests statically reference a missing module and missing active-service symbols; current test plan omits two Odds test files. | Test owner / Odds implementer | Reconcile/remove/adapt tests against the actual active architecture, then establish targeted tests in isolated environments during Phase 3. |
| **Blocking — durable state schema** | No Odds-owned retry/cooldown/cache-recovery state exists. | Schema owner / DBA / service owner | Approve Odds-owned model/table/migration and rollout/downgrade, state semantics, lock/upsert behavior, provider quota-scope key and retention. |
| **Blocking — retry numeric semantics** | Retry-After without header, upper-bound handling, cooldown fallback and repeated-failure due-time policy lack justified numeric rules. | Product/SRE/provider-quota owner | Supply provider guidance or explicit documented numeric policy; do not invent values. Specify fail-closed behavior if header/state is unusable. |
| **Resolved — unknown market policy** | The later approved Final Design closes the earlier plan ambiguity. | Approved Final Design | Ignore IDs outside `{1, 4, 5, 45}`; require IDs 1/4/5; make ID 45 optional; reject missing/invalid required data and preserve old data. |
| **Blocking — manual sync eligibility** | The plan does not explicitly say whether admin-triggered sync may bypass per-match next eligible time or provider-wide cooldown. | API/product/SRE owner | Require the same gates for manual and scheduled calls, or explicitly approve a privileged override with audit and quota protections. |
| **Runtime acceptance only** | Actual bookmaker ID/name and provider market IDs/names/aliases have no captured evidence. | Provider evidence owner | Provide approved captured payload/persisted metadata with provenance, or defer runtime acceptance; no live call as part of this review. |
| **Public contract change only** | Flutter/client source is unavailable. | API/client owner | Keep current response unchanged, or obtain client contract evidence and separate version/compatibility approval before changing `market`. |

## 11. Phase 3 Authorization Checklist

- [ ] Resolve the ambiguous-commit proof contract for both Odds and Analytics, including identical pre-existing snapshots.
- [ ] Implement the same commit verification and cache-after-confirmation behavior for scheduler and manual API entry points.
- [ ] Reconcile existing Odds test imports/legacy test modules and establish an isolated test baseline.
- [ ] Approve an Odds-owned retry/cooldown/cache-state schema and migration.
- [ ] Approve Retry-After parsing, missing/invalid header behavior, cooldown scope, numeric fallback/upper bound and retry due-time policy.
- [ ] Clarify unknown market coexistence behavior while preserving raw diagnostics and never guessing aliases.
- [ ] Require manual admin sync to honor cooldown/next-eligible state or approve a narrowly scoped override.
- [ ] Preserve public GET contract unless separate client compatibility/version approval is granted.
- [ ] Obtain actual provider identity evidence before runtime acceptance; do not call API-Football to close this gap without authorization.
- [ ] Confirm all changes remain within Provider → Service → SyncService → Repository → Database, Analytics-owned projection and API/Scheduler-owned transactions.
- [ ] Obtain separate explicit authorization for Phase 3 implementation.

## 12. Final Verdict

**NOT READY.** The broad implementation direction is understandable, but the current plan does not yet establish a safe, unambiguous commit-verification contract for both transactional projections and both entry points. The active scheduler recovery path is incomplete, and the test baseline is statically inconsistent with the active source. These are not merely runtime-evidence gaps.

This review does **not** authorize Phase 3. No source code, existing tests, migrations, database, Redis, scheduler, provider, cache, or runtime state was changed. No provider calls, syncs, mutating operations, database connections, or tests were run. Only this required review document was created.
