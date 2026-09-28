# FINAL LIVE SYNC - AUDIT REPORT

## 1. Scope

This was an audit-only review of terminal handling in Live Sync, provider refetch behavior, final Match persistence, locks, transactions, cache invalidation, scheduler interaction, retries, tests, and naturally available runtime evidence. Lineup, Analytics, Player Master, Team Master, Event Sync, Odds Sync, Standings Sync, and historical repair were excluded.

No production data or implementation code was modified by this audit.

## 2. Audit Question

Does the current Fover backend have a dedicated Final Live Sync step that performs a fresh authoritative provider fixture re-fetch when a match reaches FT, AET, or PEN?

Answer: **MISSING**.

The code has terminal-state persistence and finalization handoff, but it does not have a distinct final provider re-fetch/update responsibility.

## 3. Current Live Sync Architecture

The normal path is:

`worker.py` -> `LiveUpdateScheduler._sync_live_matches_job()` -> `football_service.sync_live_matches()` -> `FixtureSyncService.sync_live_matches()` -> `FixtureProvider.get_live_fixtures()` -> `FixtureSyncService._process_sync()` -> `process_fixture()`-equivalent shared processing -> Match upsert -> scheduler commit/rollback -> post-commit live cache invalidation.

`FixtureSyncService.sync_live_matches()` fetches `/fixtures?live=all` once. It builds the provider ID set, fetches details for locally live fixtures missing from that set through `get_fixtures_by_ids()`, and then passes the resulting payload list into the shared processing pipeline.

There is no branch in this path that says terminal state detected, then performs a second authoritative final-state fetch.

## 4. Terminal Transition Architecture

Within `_process_sync_with_candidates()`:

1. `parse_fixture_to_match()` normalizes the already-received provider payload, including status, elapsed, home score, away score, match time, teams, and league fields.
2. The existing Match is resolved by provider identity through `MatchRepository.get_by_provider_fixture_id()`.
3. The Match is upserted using the normalized payload.
4. `handle_terminal_transition()` receives `previous_status`, `normalized_status`, and the current result.

`handle_terminal_transition()` only checks whether the old status is non-terminal and the new status is one of `FT`, `AET`, or `PEN`, then calls `final_lineup_finalization_repository.create_required()`. It logs `FINAL_LINEUP_REQUIRED` and appends a finalization candidate. It does not call `FixtureProvider`, `get_fixtures_by_ids()`, `get_live_fixtures()`, or any final-sync method.

Therefore `Finalization REQUIRED` is a downstream lineup workflow handoff, not Final Live Sync.

## 5. Recent Terminal Reconciliation Architecture

`FixtureSyncService.reconcile_recent_non_terminal()` selects bounded local non-terminal matches, collects their provider fixture IDs, and calls `FixtureProvider.get_fixtures_by_ids()` in batches. Each returned fixture is passed directly to `process_fixture()`.

The actual path is:

`local non-terminal` -> `/fixtures?ids=...` provider re-check -> `process_fixture()` -> Match upsert and terminal detection -> finalization candidate.

This is a provider re-check for reconciliation. It is not a dedicated Final Live Sync step because there is no separate final-state operation after the provider response is found terminal and no second final provider request specifically owned by terminal finalization.

## 6. Dedicated Final Live Sync Discovery

Search of the backend found no `final_live_sync`, `final_live`, `finalize_live_fixture`, `sync_fixture_final_state`, or equivalent dedicated method.

Concrete negative evidence:

* `handle_terminal_transition()` creates finalization work only.
* `process_fixture()` delegates to `_process_sync_with_candidates()` and does not refetch.
* `sync_live_matches()` fetches the live feed and optional stale fixture IDs, then processes those payloads once.
* `reconcile_recent_non_terminal()` fetches by fixture IDs and processes those payloads once.
* No terminal handler calls a provider endpoint after terminal detection.

Classification: **MISSING**.

## 7. Provider Fetch Analysis

| Call | Endpoint and parameters | Role | Final-specific refetch? |
|---|---|---|---|
| `FixtureProvider.get_live_fixtures()` | `GET /fixtures`, `live=all` | Normal live feed | No |
| `FixtureProvider.get_fixtures_by_ids()` from live sync | `GET /fixtures`, `ids=<joined IDs>` | Re-fetch locally live matches absent from current live feed | No; it occurs before processing, not after terminal detection |
| `FixtureProvider.get_fixtures_by_ids()` from reconciliation | `GET /fixtures`, `ids=<joined IDs>` | Recent non-terminal reconciliation | No dedicated final responsibility |

The provider fixture-detail response contains the status, elapsed value, score, match date, league, team, and fixture metadata consumed by `parse_fixture_to_match()`. Those fields are persisted when that payload is processed, but there is no second fetch specifically establishing final authoritative state.

## 8. Final State Persistence

The existing normal Match upsert persists `status`, `home_score`, `away_score`, `elapsed`, `match_time`, team and league fields, venue, and referee fields from the already-fetched payload. The outer scheduler owns commit/rollback, and successful live/reconciliation jobs invalidate the `live_matches` cache after commit.

This persistence path is real, but it is not a Final Live Sync persistence path because no dedicated final fetch supplies the payload.

## 9. FT / AET / PEN Behavior

* `FT`, `AET`, and `PEN` are included in `FINISHED_STATUSES` and active-match terminal handling.
* `handle_terminal_transition()` specifically treats `FT`, `AET`, and `PEN` as finalization-triggering statuses.
* The status value is persisted as received by the normal Match upsert.
* The regular provider score fields are persisted as `home_score` and `away_score`.
* `elapsed` is persisted from the provider status payload.
* No dedicated final fetch exists for any of the three statuses.
* No separate Final Live Sync semantics distinguish the final authoritative fetch for AET or PEN from FT.

The audit found no evidence that penalty-shootout or extra-time final-state data receives a dedicated final-sync treatment beyond the normal payload normalization and persistence.

## 10. Lock / Concurrency Analysis

Normal live sync is protected by the scheduler's `max_instances=1`, the live scheduler advisory lock, and the shared `fixture_query:global` resource lock. Reconciliation uses its own scheduler advisory lock and `fixture_query:recent_reconciliation` resource lock.

No dedicated Final Live Sync lock exists because no dedicated Final Live Sync operation exists. Any terminal processing performed inside normal sync or reconciliation is covered by the enclosing fixture-query lock and the transaction-scoped resource lock implementation.

Runtime evidence showed the scheduler healthy and no advisory locks held after completed work. This protects the existing operations but does not establish a separate final-sync lock scope.

## 11. Transaction Analysis

The scheduler owns the outer transaction. `FixtureSyncService` and repositories flush/upsert within that transaction and do not perform a separate final provider transaction. On successful live or reconciliation processing, the scheduler commits; on failure it rolls back. Per-fixture savepoints isolate fixture processing failures.

Because the terminal payload is processed in the existing transaction, any terminal Match update and finalization candidate creation are committed together. There is no separate final-fetch transaction boundary to audit.

## 12. Cache Analysis

After successful normal live sync or reconciliation commit, the scheduler invalidates `make_cache_key("live_matches")`. Failed transactions roll back and do not perform successful-state invalidation. No final-state-specific cache key or final-sync invalidation event exists.

The current cache behavior is correct for the existing sync path, but it does not prove Final Live Sync because the dedicated operation is absent.

## 13. Failure / Retry Analysis

Provider failure in normal live sync returns an unsuccessful result or raises, causing scheduler rollback and scheduler error handling. Per-fixture failures are isolated by savepoints where applicable. The scheduler remains eligible for the next interval.

Reconciliation can retry local non-terminal records on later scheduled runs because it selects them again. A terminal response processed during reconciliation creates finalization work; it does not enter a dedicated final-live retry state.

There is no final provider fetch failure category, final-live retry record, final-live retry counter, or final-live completion event. Recovery relies on normal subsequent live/reconciliation processing rather than a dedicated final-sync retry mechanism.

## 14. Scheduler Trigger Analysis

Actual scheduler jobs are:

* `sync_live_matches`: every 60 seconds.
* `reconcile_recent_non_terminal`: every 5 minutes.

Neither job triggers a distinct Final Live Sync. They trigger the existing provider fetch and shared processing path. Terminal detection then hands off to finalization work, which is unrelated to final authoritative Match synchronization.

Actual architecture classification: **D. No dedicated Final Live Sync exists.**

## 15. Test Coverage

Focused audit suites passed: `67 passed`.

They cover live sync selection, stale-match behavior, scheduler execution, locks, transaction ownership, cache transaction ordering, and recovery. The tests do not prove a dedicated final provider re-fetch because the production code has no such responsibility and no test asserts a second provider call after terminal detection.

Existing terminal assertions around status/finalization are evidence of terminal detection and finalization handoff only. They do not qualify as proof of Final Live Sync under this audit definition.

## 16. Real Runtime Verification

Runtime scheduler and provider evidence were available:

* `fover_scheduler_up=1`.
* `sync_live_matches` had executed repeatedly; the observed counter was 19.
* Recent reconciliation had executed twice.
* The provider live endpoint returned eight current LIVE fixtures during the audit.
* No current provider LIVE fixture had a corresponding local Match row.
* Local terminal samples existed, but no natural provider-terminal/local-transition pair was available.

Runtime classification: **IMPLEMENTATION AUDITED - NATURAL RUNTIME FINAL-SYNC TRANSITION NOT OBSERVED.** This does not change the source classification: the dedicated Final Live Sync responsibility is missing.

## 17. Database Integrity

Read-only checks:

* Duplicate provider fixture identities: 0
* Orphan home-team references: 0
* Orphan away-team references: 0
* Invalid Match statuses: 0
* Duplicate Match business keys: 0
* Migration head: `20260915_missing_lineup_identity`

No production data was modified, and no integrity regression was introduced by this audit.

## 18. Gap Analysis

Current implementation is **MISSING** a dedicated Final Live Sync step.

Why the gap matters:

* A terminal status may be persisted from a normal live or reconciliation payload without a terminal-specific authoritative re-fetch.
* FT, AET, and PEN have no distinct final-state fetch semantics.
* Final score and elapsed fields are only as authoritative as the payload that happened to trigger terminal detection.
* Finalization work can be created even though final Match state has not passed through a dedicated final-sync responsibility.
* There is no final-sync-specific logging, retry state, lock, metric, or completion evidence.

This is not a claim that the existing normal upsert is broken. It is a precise architectural gap relative to the requested Final Live Sync definition.

## 19. Recommended Future Architecture

Do not implement in this audit phase. A future implementation phase should introduce a clearly separated operation:

`Normal Live Sync` -> `terminal detected` -> `Dedicated Final Live Sync` -> `fresh authoritative provider fetch by fixture ID` -> `normalize FT/AET/PEN state` -> `FixtureSyncService/Match persistence` -> `outer transaction commit` -> `post-commit live cache invalidation` -> `Final Live Sync complete`.

The operation should define its own lock/retry/logging contract while reusing the existing FixtureSyncService and transaction owner. Only after Final Live Sync succeeds should downstream finalization workflow be triggered:

`Final Live Sync SUCCESS` -> `future downstream Finalization workflow`.

Lineup and Analytics changes are not part of this recommendation.

## 20. Out-of-Scope Items

No Lineup, Analytics, Player Master, Team Master, Event, Odds, Standings, historical repair, status repair, fixture-specific patch, or implementation change was performed.

## 21. Final Classification

**MISSING**

Fover currently does not have a dedicated Final Live Sync step. It has normal live synchronization, recent terminal reconciliation, terminal detection, Match persistence, and finalization handoff. None of those performs a distinct fresh authoritative provider re-fetch after terminal detection.