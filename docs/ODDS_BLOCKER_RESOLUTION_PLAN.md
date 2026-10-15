# Odds Blocker Resolution Plan

**Document status:** design/planning only; no implementation authorized
**Review basis:** repository evidence, approved design documents, and prior readiness review
**Final readiness verdict:** **CONDITIONALLY READY — EXPLICIT APPROVAL REQUIRED**

## 1. Executive Decision

The prior readiness review concluded that Phase 3 is not ready because the plan did not yet define a safe ambiguous-commit verification contract, a durable retry-state design, a consistent unknown-market rule, and a validated baseline for the Odds tests. Those points are resolvable at the planning/design level without changing application code.

This document resolves the blockers to the extent possible without implementation. The recommended outcome is: Phase 3 remains blocked until a final set of design decisions receives explicit approval and the future implementation is validated against isolated test coverage. In other words, the repository evidence supports a clear implementation direction, but the final production and schema approvals remain explicit gates.

This is a documentation-only plan. It does not authorize migration, code changes, provider calls, scheduler execution, or database mutation.

## 2. Scope and Safety Constraints

- Preserve the frozen architecture: `Provider → Service → SyncService → Repository → Database`.
- Provider remains transport-only.
- Odds Service owns normalization, domain validation, and Myanmar Odds conversion.
- OddsSyncService owns orchestration and status classification.
- OddsRepository owns Odds SQL and persistence.
- Analytics remains the owner of Analytics projection logic and persistence.
- API/Scheduler own transaction commit/rollback, retry-state writes after rollback, and post-commit cache invalidation.
- Do not modify application code or runtime state while preparing this plan.
- Do not run provider calls, scheduler jobs, mutating endpoints, or tests.
- Preserve all approved design decisions and not silently reinterpret them.

## 3. Evidence Reviewed

### Core source files and evidence consulted

- `docs/ODDS_FINAL_DESIGN_FREEZE.md`
- `docs/ODDS_PRE_IMPLEMENTATION_DECISIONS.md`
- `docs/ODDS_IMPLEMENTATION_PLAN.md`
- `docs/ODDS_IMPLEMENTATION_READINESS_REVIEW.md`
- `app/services/odds_service.py`
- `app/services/odds_sync_service.py`
- `app/repositories/odds_repository.py`
- `app/services/scheduler.py`
- `app/services/base/football_client.py`
- `app/providers/odds_provider.py`
- `app/services/analytics_projection_service.py`
- `app/api/matches.py`
- `app/models/odds.py`
- `app/models/match.py`
- `app/models/analytics.py`
- `app/models/match_finalization.py`
- `app/models/league_identity_recovery.py`
- `alembic/versions/20261005_create_match_finalization.py`
- `alembic/versions/20260922_create_league_identity_recovery.py`
- `tests/test_odds_service_myanmar_sync.py`
- `tests/test_odds_phase3_hardening.py`
- `tests/test_odds_lock_alignment.py`
- `tests/test_scheduler_runtime.py`
- `tests/test_odds_identity_mapping.py`
- `tests/test_odds_identity_resolution_bridge.py`

### Verified facts from source

- `OddsService._get_1xbet_bookmaker()` looks for `bookmaker.id == 11` and persists the response `name` or a fallback string when absent (`app/services/odds_service.py`).
- `OddsSyncService.refresh_odds()` calls the provider, filters `bookmakers` for ID 11, only keeps supported bet IDs `1`, `4`, `5`, and `45`, and persists rows after selecting lines (`app/services/odds_sync_service.py`).
- `OddsRepository.replace_fixture_odds()` deletes all rows for a fixture and then upserts the new rows when rows exist (`app/repositories/odds_repository.py`).
- `OddsRepository.snapshot_matches_rows()` compares only raw business-key sets and not actual values (`app/repositories/odds_repository.py`).
- `LiveUpdateScheduler._refresh_odds_job()` checks `Match.status in {'NS','TBD','PST'}`, resolves future prematch candidates within 72 hours, and currently skips if `latest_update` is younger than 12 hours (`app/services/scheduler.py`).
- `FootballAPIClient.request_with_metadata()` defaults to 30s timeout and 2 retries, retries transport errors and HTTP 5xx immediately, and does not inspect `Retry-After` (`app/services/base/football_client.py`).
- `AnalyticsProjectionService.project_odds()` accepts only `bet365` and canonical market names; it can return a filtered-to-zero result and preserve prior data (`app/services/analytics_projection_service.py`).
- `GET /api/matches/{match_id}/odds` reads cached/stored odds and does not call the provider (`app/api/matches.py`).
- `MatchFinalization` and `LeagueIdentityRecovery` are durable retry-ish patterns for unrelated workflows and are not a valid replacement for Odds-owned state (`app/models/match_finalization.py`, `app/models/league_identity_recovery.py`, migration files).
- No Odds-specific retry-state table or durable provider cooldown state exists in current repository schema (`app/models/odds.py`, `alembic/versions/*`).

### Evidence limitations

- There is no confirmed production or captured API-Football response in the repo; tests provide sample payloads only.
- There is no Flutter/Dart client source in the workspace.
- The current test modules include static imports of symbols that are not present in the active service implementation; these modules are not yet a trustworthy baseline.

## 4. Blocker Register

| Blocker | Current status | Evidence | Risk |
|---|---|---|---|
| A. Ambiguous commit recovery | Unresolved at current design level | Scheduler helper calls missing methods; manual route commits directly; repository snapshot verifier compares only business keys | False success, incorrect cache invalidation, stale or partially committed state |
| B. Retry policy and cooldown | Unresolved | Shared client retries on transport/5xx immediately; no `Retry-After` parsing or durable cooldown state | Provider quota abuse, repeated immediate calls, inconsistent state |
| C. Durable retry-state storage | Unresolved | Existing retry patterns are unrelated; no Odds-owned state exists | Retry eligibility lost across restarts and workers |
| D. Manual sync and scheduler parity | Unresolved | Separate entry points with separate logic risk | Divergent behavior and inconsistent failure responses |
| E. Unknown/unmapped markets | Partially resolved by approved decisions, but rules need explicit implementation contract | Source uses raw names and partial filters; unknown IDs/names remain diagnostic | Partial snapshot replacement, wrong canonical market assignment |
| F. Analytics projection alignment | Approved target exists but exact execution contract is not fully defined | Current projection filters only Bet365 and canonical strings, while source rows use 1xBet raw names | Analytics can remain stale or silently report success |
| G. Existing test baseline | Unresolved | Static import mismatches and DB-writing tests require isolation | Failing or misleading tests, unsafe execution |
| H. Provider identity and public API compatibility | Runtime acceptance gate remains open | No verified provider payload; no client source found | Wrong bookmaker or market treatment in production; public contract risk |

## 5. Resolution Decision for Each Blocker

### Blocker A — Ambiguous Commit Recovery

#### Evidence and current behaviour

- `commit_odds_refresh()` in `app/services/scheduler.py` calls `is_commit_ambiguous`, `verify_commit_unknown`, and `classify_retry_state`; these methods are not defined in the active `OddsSyncService` (`app/services/odds_sync_service.py`).
- The manual sync route commits directly and invalidates cache after commit without a fresh-session verification step (`app/api/matches.py`).
- `snapshot_matches_rows()` checks only `(bookmaker_name, market_name, selection)` business keys and does not compare odds values, Myanmar values, or projection outcome (`app/repositories/odds_repository.py`).
- Current `refresh_odds()` marks a result as `SUCCESS` before the outer transaction commits (`app/services/odds_sync_service.py`).

#### Risk

A “committed” result can be accepted even when no transaction was successfully committed, or when only a previously persisted snapshot remained in place.

#### Recommended resolution

Implement a deterministic verification contract:

1. Every refresh result carries expected snapshot context: local match ID, provider fixture ID, expected row count, expected set of `(bookmaker_name, market_name, selection)`, and expected canonical values for `odd_value` and `myanmar_odd` where required.
2. The API/Scheduler outer transaction commits as usual; if commit raises an ambiguous error, open a fresh DB session and verify the exact persisted Odds snapshot, not only row keys.
3. Verification must also confirm the Analytics projection row set, using the same canonical row identity and expected values.
4. The verification result resolves to one of:
   - `COMMIT_CONFIRMED`: exact committed Odds and Analytics states match the expected candidate.
   - `COMMIT_NOT_APPLIED`: evidence proves the candidate was not committed.
   - `COMMIT_UNKNOWN`: evidence is incomplete or inconsistent.
5. Only `COMMIT_CONFIRMED` allows cache invalidation.
6. `COMMIT_NOT_APPLIED` may permit a later retry only if the approved retry policy allows it.
7. `COMMIT_UNKNOWN` requires a recoverable unknown state and a no-blind-retry rule.

#### Alternatives rejected

- “Only check row counts” — rejected because it can falsely confirm a previous snapshot.
- “Invalidate cache after any successful commit call” — rejected because a commit may be ambiguous or unconfirmed.
- “Treat `SUCCESS` as committed” — rejected because the current source proves that `refresh_odds` returns success before the commit boundary.

#### Required future implementation changes

- Add expected snapshot context to the refresh result and commit-verification path.
- Strengthen repository verification beyond natural-key membership.
- Add exact verification for Analytics state as part of the same contract.
- Apply the same pattern to scheduler and manual sync routes.

#### Verification method

Fresh-session comparison of the exact expected snapshot and its matching Analytics rows, using canonical values and stable business identity.

#### Acceptance criteria

- `COMMIT_CONFIRMED` is only set when exact expected row content and Analytics content match the database.
- `COMMIT_NOT_APPLIED` is returned only when fresh verification proves absence of the intended candidate.
- `COMMIT_UNKNOWN` is returned on incomplete or contradictory evidence.
- No cache invalidation is allowed for `COMMIT_NOT_APPLIED` or `COMMIT_UNKNOWN`.

#### Approval required

- Explicit approval that the verification contract will compare complete content, not merely row counts.

### Blocker B — Retry Policy and Cooldown

#### Evidence and current behaviour

- `FootballAPIClient.request_with_metadata()` retries transport exceptions and HTTP 5xx immediately up to two retries (`app/services/base/football_client.py`).
- No `Retry-After` parsing or provider cooldown state exists in the current Odds implementation.
- Scheduler jobs currently rely on a 12-hour freshness timestamp and in-memory cache invalidation state, not a durable retry policy (`app/services/scheduler.py`).

#### Risk

Provider overload, duplicate requests, false retry eligibility, and scheduler starvation.

#### Recommended resolution

- Timeout and HTTP 5xx: do not reissue the provider request immediately; defer to the next eligible scheduled attempt.
- HTTP 429: parse valid `Retry-After` values and enforce a provider-wide cooldown at least until the due time.
- Provider-wide cooldown and per-match retry state are separate concerns; provider cooldown may affect all matches sharing the same provider/account quota, while per-match failure state is local to the match.
- Retry eligibility checks happen before any provider call.
- Freshness skip must not suppress a retry whose next eligible time has arrived.
- Any invalid or incomplete candidate is recorded as `VALIDATION_FAILED`; no replacement occurs.
- No global HTTP client retry change is allowed for all modules; the fix must be Odds-specific unless a broader design change is explicitly approved.
- Distinguish `NO_DATA`, `PROVIDER_FAILED`, `VALIDATION_FAILED`, `DB_WRITE_FAILED`, `COMMIT_UNKNOWN`, `CACHE_INVALIDATION_FAILED`, `SUCCESS`.

#### Numeric policy rule

Do not invent numeric retry limits, cooldown durations, or fallback values without evidence. If `Retry-After` is absent or invalid, the safest conservative default is to defer the provider call until the regular scheduler cadence and record the failure with an explicit fallback policy mark. Any numeric fallback beyond that requires provider guidance or explicit product approval.

#### Alternatives rejected

- Immediate provider retry on timeout or 5xx — rejected by approved design.
- Global HTTP client retry change — rejected because it alters unrelated modules.
- Hard-coded backoff or max-attempt count — rejected until justified.

#### Required future implementation changes

- Add provider-specific request policy in Odds path only.
- Add durable provider cooldown state and per-match retry state.
- Ensure retry-state storage failure is explicit and fail-closed.

#### Verification method

Static check on provider response classification, due-time comparison, and scheduler eligibility logic; runtime verification only after approval.

#### Acceptance criteria

- A 429 with valid `Retry-After` blocks provider calls until due time.
- Timeout/5xx result in deferred retry; no immediate repeated call.
- A freshness skip does not suppress a due retry.
- Failure state is persisted and reset only on success.

#### Approval required

- Explicit approval of the fallback policy when `Retry-After` is absent or invalid.

### Blocker C — Durable Retry-State Storage

#### Evidence and current behaviour

Existing retry-state tables are for other workflows (`MatchFinalization`, `LeagueIdentityRecovery`), not Odds (`app/models/match_finalization.py`, `app/models/league_identity_recovery.py`, migration files). No Odds-owned durable retry-state table exists.

#### Risk

Retry eligibility is lost across restarts, failure count may be reset, provider cooldown is not enforced reliably, and scheduled retries may re-trigger immediately.

#### Recommended resolution

Create a dedicated Odds retry-state table and a separate provider-cooldown state if needed. The minimum design is:

- local match identity (FK to `matches.local_match_id`)
- failure count (`>= 0`)
- next eligible retry timestamp (`nullable`)
- last failure classification (`string`)
- last error and diagnostics metadata (bounded, sanitized)
- created/updated timestamps
- index on `next_eligible_at` and perhaps `failure_count`
- uniqueness on `match_id`

Provider-wide cooldown must be stored separately if the product requires a global quota guard rather than per-match gating.

#### Alternatives rejected

- Reusing `MatchFinalization` or `LeagueIdentityRecovery` — rejected because they belong to unrelated workflows and may represent different semantics.
- Storing retry state in a non-durable in-memory set — rejected because process restarts lose it.
- Duplicating all provider cooldown data in every match row — rejected because it mixes concerns and is harder to reconcile.

#### Required future implementation changes

- Add migration after separate approval.
- Add repository methods for state reads/writes and provider cooldown checks.
- Add fail-safe handling when retry-state persistence fails.

#### Verification method

Static schema review plus isolated DB-backed acceptance tests in a future authorized implementation phase.

#### Acceptance criteria

- Retry count and next eligible time survive process restart.
- A scheduled or manual refresh consults the state before a provider call.
- Failure-state write failure is observable and blocks false retry claims.

#### Approval required

- DBA/schema approval and migration review.

### Blocker D — Manual Sync and Scheduler Parity

#### Evidence and current behaviour

The admin route and scheduler route both call `refresh_odds()`, but they are not documented as sharing identical preflight checks or result-state semantics (`app/api/matches.py`, `app/services/scheduler.py`). The scheduler also uses a different resource-lock and cache-recovery path than the API route.

#### Risk

Two paths with slightly different retry/cooldown semantics cause inconsistent production results.

#### Recommended resolution

Both the scheduler and manual route should use the same exact validation, retry-state, provider-cooldown, commit-verification, and cache-invalidation logic. The outer transaction and lock remain with the API/Scheduler owner; the route does not move those responsibilities into the service/repository.

Therefore:

- A manual sync must not bypass retry eligibility or provider cooldown.
- A scheduler-triggered job and a manual sync share the same sync status vocabulary.
- Either caller may report equivalent states to the client/logs but must not invent a stronger claim than the verification contract supports.

#### Alternatives rejected

- “Manual sync can bypass retry state because it is user-driven.” — rejected by approved design.
- “Scheduler path and API route each define different validation logic.” — rejected because it creates inconsistency.

#### Required future implementation changes

- Centralize all preflight checks in a common Odds sync orchestration contract used by both paths.
- Make both entry points call the same outcome classification and cache invalidation logic.

#### Verification method

Static parity review and isolated tests on both paths.

#### Acceptance criteria

- Manual sync and scheduler route share the same provider-eligibility checks.
- Both paths classify `NO_DATA`, `VALIDATION_FAILED`, `PROVIDER_FAILED`, `COMMIT_UNKNOWN`, `CACHE_INVALIDATION_FAILED` the same way.
- Result states are comparable and observable.

#### Approval required

- API behavior approval for equivalent manual and scheduled paths.

### Blocker E — Unknown and Unmapped Markets

#### Evidence and current behaviour

The active service accepts raw market names and partial selection sets; it does not treat unknown market IDs or raw names as diagnostic-only evidence without a mapping decision. The approved design says to preserve unknown raw data and never guess aliases.

#### Risk

A malformed or unsupported market may be silently treated as valid and replace a good snapshot.

#### Recommended resolution

Use the fail-safe rule:

- Unknown market ID or raw provider name is not mapped to a canonical category.
- Retain the raw market ID and name in structured diagnostics.
- Unknown/unmapped markets do not qualify as supported rows for persistence.
- Filter raw markets to the exact allowlist `{1, 4, 5, 45}` before normalization. Require complete, valid markets 1, 4, and 5; market 45 is optional but must validate if present.
- Ignore IDs outside the allowlist; their presence does not affect completeness. If required markets are missing or invalid, preserve the old snapshot.
- For IDs `1` and `45`, `myanmar_odd = NULL` is legitimate. For IDs `4` and `5`, absence or failed conversion is a validation failure.

#### Alternatives rejected

- Guessing a category from raw market names — rejected by approved decisions.
- Dropping unknown entries and proceeding without all required markets 1, 4, and 5 — rejected. Ignoring unknown entries when all required markets validate is required by the approved final design.

#### Required future implementation changes

- Add exact allowlist filtering and required-market validation for IDs 1, 4, and 5; keep ID 45 optional.
- Keep raw provider ID/name in diagnostics.
- Reject candidates missing/invalid required markets before replacement; an unknown-only payload has no admissible candidate and preserves the existing snapshot.

#### Verification method

Static validation rules and isolated unit tests covering known, unknown, mixed and incomplete candidate sets.

#### Acceptance criteria

- Unknown market IDs never map to canonical categories.
- Unknown-only candidates preserve old data. A mixed payload may replace the snapshot only when IDs 1, 4, and 5 validate and ID 45 validates if present.
- IDs 4 and 5 require successful Myanmar conversion.
- IDs 1 and 45 allow legitimate NULL values.

### Blocker F — Analytics Projection Alignment

#### Evidence and current behaviour

The current projection only accepts `bet365` and canonical market names, while the Odds source path chooses bookmaker ID 11 (`app/services/analytics_projection_service.py`, `app/services/odds_service.py`).

#### Risk

Analytics can remain stale, or a false success can be returned while no projection was accepted.

#### Recommended resolution

- Keep Analytics projection logic owned by Analytics.
- Align the approved Analytics projection to the selected primary bookmaker and canonical row vocabulary.
- Validate the same candidate before persistence and projection; zero accepted rows is a failure condition for the overall refresh in the outer transaction.
- The same transaction must include the valid Odds replacement and the Analytics projection. Neither can be considered successful if the other is rejected or missing.

#### Alternatives rejected

- Move Analytics SQL into OddsRepository — rejected because it violates architecture and ownership.
- Treat zero accepted rows as a success — rejected because it masks a stale projection.

#### Required future implementation changes

- Align the Analytics projection to the approved bookmaker and canonical categories.
- Return a distinct status when zero accepted rows or mismatch occurs.
- Prevent a false successful commit when projection verification fails.

#### Verification method

Fresh database checks and isolated tests for zero-acceptance/reconciliation mismatch handling.

#### Acceptance criteria

- A valid Odds snapshot plus Analytics success yields a single commit result.
- Zero accepted or rejected projection rows do not report success.
- A projection error causes rollback and no cache invalidation.

#### Approval required

- Approval that the Analytics change is limited to the approved projection alignment and remains within Analytics ownership.

### Blocker G — Existing Test Baseline

#### Evidence and current behaviour

The repository contains test modules that import `app.services.odds_identity` and missing constants from `OddsSyncService` and `app.services.odds_sync_service`. These are static inconsistencies in the currently checked-in baseline.

#### Risk

The future implementation will not have a trustworthy regression baseline if these tests are not reconciled before Phase 3.

#### Recommended resolution

Treat the current test baseline as untrustworthy until reconciled with the actual active code. Future implementation should:

- inspect all relevant Odds tests statically,
- isolate DB-writing tests in a disposable environment,
- repair or remove stale tests before asserting outcomes,
- add the missing acceptance tests for the approved decisions.

#### Alternatives rejected

- Calling the tests “good enough” without reconciling the imports — rejected.
- Executing DB-writing tests in a shared environment — rejected.

#### Required future implementation changes

- identify obsolete legacy tests and decide whether to remove, repair, or skip them
- add coverage for the final design decisions and failure outcomes

#### Verification method

Repository inspection plus isolated test execution in a disposable database after explicit authorization.

#### Acceptance criteria

- The test suite imports only actual active modules and symbols.
- DB-writing tests run only in isolated test infrastructure.
- A coverage matrix exists for the final design decisions before implementation.

### Blocker H — Provider Identity and Public API Compatibility

#### Evidence and current behaviour

No capturedAPI-Football payload or provider evidence is present in the repository, and no Flutter/Dart client source was found. The current API returns raw stored `market` names from the database, and the provider selection logic is based on bookmaker ID 11 and approved category IDs.

#### Risk

Unsafe runtime acceptance of the wrong bookmaker or API schema mismatch with the client.

#### Recommended resolution

- Treat lack of production provider identity evidence as a runtime acceptance gate, not as a reason to bypass the approved source constraints.
- Keep the public GET contract stable unless separate compatibility approval is granted.
- Preserve display value and `NULL` semantics unless there is a verified client contract that requires a change.
- Do not call the provider during this planning phase to “close” the gap.

#### Alternatives rejected

- Silently substituting Bet365 or inventing a mapping from raw names — rejected.
- Changing the public `market` field to canonical names without client evidence — rejected.

#### Required future implementation changes

- Verify provider identity using approved evidence before runtime acceptance.
- Maintain API compatibility with the current GET contract unless separate approval is granted.

#### Verification method

Static review of preserved source contract and evidence collection in a separately authorized verification phase.

#### Acceptance criteria

- The public contract remains backwards compatible unless explicitly approved otherwise.
- Provider identity is verified through approved evidence before runtime acceptance.

## 6. Recommended Retry and Cooldown Policy

### Policy decisions

- Timeout and HTTP 5xx: no immediate repeated Odds provider call; defer to the next eligible scheduled audit or retry slot.
- HTTP 429: honor valid `Retry-After` when present and enforce provider cooldown.
- No immediate repeated request may occur after timeout/5xx or 429 without a verified due-time check.
- Retry eligibility must be checked before any provider request.
- Per-match retry state must persist at least: failure count, next eligible retry time, last failure reason/category, last timestamp, and durable state if needed.
- Provider-wide cooldown must be represented separately from per-match retry state if the product requires a global quota guard.
- A stale but valid snapshot must not suppress a retry whose due time has arrived.
- The same logic must apply to manual refresh and scheduled refresh.

### Numeric policy guidance

- No numeric retry cap, fallback cooldown, or backoff value is justified by the current repo evidence.
- If `Retry-After` is absent or invalid, the conservative default is to defer to the next standard scheduler slot and mark the provider failure as deferred, not retried immediately.
- Any value beyond that requires owner approval and supporting evidence.

### Operational semantics

- Retry-state writes must happen after transaction rollback and outside the active Odds snapshot transaction.
- Retry-state persistence failure must be treated as a fail-closed state; the system must not claim it safely deferred a request when persistence failed.
- Cache invalidation may proceed only when `COMMIT_CONFIRMED` is proven.

## 7. Commit-Recovery Verification Contract

### Verification algorithm

1. Build the expected candidate snapshot from the validated provider response and ensure the result contains only supported canonical rows and required Myanmar values.
2. Capture the expected set of normalized rows and expected values for the target local match and provider fixture.
3. On ambiguous commit failure, open a fresh DB session and verify exact persisted Odds rows by:
   - local match identity,
   - bookmaker identity,
   - canonical market identity,
   - selection identity,
   - exact `odd_value`,
   - `myanmar_odd` where required,
   - absence of unexpected rows,
   - presence of all expected rows,
   - no partial replacement.
4. Verify the corresponding Analytics projection rows using the same exact input set and canonical row identity.
5. Resolve the outcome as one of `COMMIT_CONFIRMED`, `COMMIT_NOT_APPLIED`, or `COMMIT_UNKNOWN`.
6. Only `COMMIT_CONFIRMED` may invalidate cache and report refresh success.

### Evidence required for each outcome

- `COMMIT_CONFIRMED`: exact match of expected Odds and Analytics rows and values in a fresh session.
- `COMMIT_NOT_APPLIED`: exact mismatch proving the intended state was not persisted.
- `COMMIT_UNKNOWN`: insufficient or conflicting evidence, including identical old snapshot plus no reliable transaction marker.

### Missing capability assessment

The current schema does not provide a durable transaction marker or snapshot version for both Odds and Analytics. The implementation may still proceed with a fresh-session exact comparison if it is documented as the verification contract, but if the schema cannot support reliable proof, then the implementation must treat ambiguous commit as requiring a separate evidence gate rather than silent retry.

## 8. Unknown-Market Decision

The later approved Final Design resolves this policy: the exact allowlist is `{1, 4, 5, 45}`, IDs 1/4/5 are required, ID 45 is optional, and all other IDs are ignored before normalization. This supersedes the earlier unresolved recommendation below.

### Recommended fail-safe rule

- Unknown market IDs are not mapped to a canonical category.
- Unknown raw names remain in structured diagnostics.
- Unknown market entries are not considered valid persisted rows.
- A candidate missing any required allowlisted market is invalid, even when other allowlisted markets are valid.
- Unknown IDs do not create completeness uncertainty; they are ignored. Preserve the existing snapshot when any required market is absent or invalid.

### Approved IDs

- `1` -> `MATCH_WINNER` - `myanmar_odd = NULL` is allowed.
- `4` -> `ASIAN_HANDICAP` - Myanmar conversion required.
- `5` -> `GOALS_OVER_UNDER` - Myanmar conversion required.
- `45` -> `CORNERS_OVER_UNDER` - `myanmar_odd = NULL` is allowed.

### Required future enforcement

- Market eligibility and Myanmar conversion are keyed by verified market ID, not raw name.
- Unsupported or unknown market entries are ignored and cannot independently cause rejection; they are never persisted or allowed to bypass required-market validation.

## 9. Manual/Scheduler Consistency Contract

The future implementation must use a shared orchestration contract for both entry points:

- same candidate selection logic,
- same validation and normalization,
- same provider-cooldown and retry eligibility checks,
- same failure-state classification,
- same commit-verification contract,
- same cache invalidation rule.

The API/Scheduler outer transaction ownership remains with the route or scheduler; Service/Repository remain read/write internals and do not own commit or rollback.

Manual calls must not bypass the same provider cooldown or retry due time. If a privileged override is approved, it must be documented and auditable.

## 10. Analytics Atomicity Contract

- Analytics retains ownership of projection logic and persistence.
- Odds and Analytics updates must be consistent within the same outer transaction.
- A projection that filters all rows to zero is not a successful refresh.
- A database write or projection failure prevents any success result.
- Cache invalidation only follows a confirmed commit.

## 11. Test Baseline and Future Test Matrix

### Future test baseline actions

- Reconcile stale imports and outdated test references to the active implementation before use.
- Isolate DB-write test cases in a disposable test database.
- Add missing acceptance tests for the approved design decisions.
- Do not execute DB-writing or provider-related tests in this planning phase.

### Minimum required test matrix

- canonical mapping by verified market ID
- unknown market diagnostics without guessed aliasing
- ID 4/5 Myanmar required; ID 1/45 NULL allowed
- full-candidate validation before replace
- empty/incomplete candidate preserves old snapshot
- no immediate retry on timeout/5xx
- 429 with and without `Retry-After`
- provider-wide cooldown and per-match retry eligibility
- commit ambiguous / confirmed / not applied
- Analytics failure prevents a success result
- cache invalidation only after confirmed commit
- scheduler and manual sync parity
- public GET contract compatibility

## 12. Provider Identity and Public API Compatibility Gates

### Provider identity gate

The repository contains examples and code assumptions for bookmaker ID 11 / 1xBet, but no independent approved provider payload or runtime evidence proves the actual provider response shape. This remains a runtime acceptance gate.

### Public API gate

- Preserve the existing public GET endpoint and fields unless separate compatibility approval is granted.
- No change to `market` semantics should be treated as acceptable without explicit client evidence and approval.
- Flutter/Dart source remains unavailable in the current workspace.

## 13. Dependency-Ordered Implementation Plan

1. Confirm and lock the unresolved design decisions and evidence gaps.
2. Finalize the exact commit-verification contract for Odds + Analytics.
3. Finalize retry-state ownership, schema, and numeric fallback policy.
4. Finalize candidate completeness and unknown-market rules.
5. Finalize manual/scheduler parity and result-state definitions.
6. Finalize Analytics projection consistency and transaction flow.
7. Repair or replace the static test baseline and isolate DB-write tests.
8. Phase 3 implementation with the approved design and the migration/state plan.
9. Execute isolated regression tests in disposable database and mocked-provider setup.
10. Perform separately authorized production-like validation only after the above gates close.

For each step, the future implementation must state prerequisites, affected files, acceptance evidence, and dependencies. No file modification is performed here.

## 14. Acceptance Checklist

- [ ] Ambiguous commit verification is deterministic and exact.
- [ ] Retry and cooldown policies are explicit and documented.
- [ ] Durable retry-state storage is reviewed and approved.
- [ ] Unknown market policy is fail-safe and nonguessing.
- [ ] Manual sync and scheduler parity is enforced.
- [ ] Analytics alignment remains within the approved scope.
- [ ] Test baseline is repaired and isolated.
- [ ] Provider identity and client compatibility remain open until evidence is approved.
- [ ] No code or database mutation is performed in this planning phase.

## 15. Remaining Unresolved Decisions

1. Whether a future implementation uses a separate provider-wide cooldown state or a match-scoped cooldown + per-match retry state only.
2. Whether `Retry-After` fallback values are provider-driven or must default to the scheduler cadence.
3. Whether artificial numeric caps must be avoided entirely until explicit evidence exists.
4. **Resolved by the approved Final Design:** mixed supported/unsupported payloads are accepted only when required IDs 1, 4, and 5 validate; ID 45 is optional and other IDs are ignored.
5. Whether manual sync may ever bypass retry eligibility under a privileged override.
6. Whether the final API `market` contract changes are acceptable after explicit client compatibility approval.
7. Whether the current schema may support exact commit verification without a new marker or additional snapshot metadata.

## 16. Final Readiness Verdict

**CONDITIONALLY READY — EXPLICIT APPROVAL REQUIRED**

The design and planning issues are now resolved to the point where a future implementation could proceed safely, but only after the remaining approvals and explicit evidence gates are resolved. The document keeps the frozen design intact, records the blocker resolutions, and defines the exact future acceptance criteria.

It does not authorize implementation. The actual source code, tests, migrations, schema, runtime state, scheduler, Redis, provider, and database remain unchanged.
