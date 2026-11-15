# LIVE → FINAL Handoff Design Freeze

## 1. Objective and scope

This document freezes the approved design for the existing Normal LIVE SYNC → Final Live Sync handoff. It preserves the current architecture and reuses the existing finalization queue and final-match synchronization path.

**In scope**

- Select recently kicked-off local LIVE matches absent from the current API-Football live feed.
- Durably retain candidates in the existing `MatchFinalization` queue.
- Limit missing-feed provider fixture-by-ID verification to 10 requests per scheduler run.
- Retry valid nonterminal and temporary-failure outcomes through the existing queue policy.
- Persist final data only after provider confirmation of `FT`, `AET`, or `PEN`.

**Out of scope**

- EXHAUSTED recovery or changes to its current semantics.
- Reconciliation of arbitrary terminal matches without a queue record.
- Historical match scans, schema changes, new tables, services, queues, workers, or schedulers.
- Changes to frozen modules, frontend behavior, or unrelated synchronization flows.

## 2. Source verification and approved flow

The following are **verified from source**, not runtime observations:

1. `LiveUpdateScheduler.start()` registers `sync_live_matches` every 60 seconds with `max_instances=1`.
2. `_sync_live_matches_job()` acquires the scheduler advisory lock and calls the live-sync facade when the existing live-sync gate allows it.
3. `FixtureSyncService.sync_live_matches()` calls `FixtureProvider.get_live_fixtures()`, which requests `/fixtures` with `live=all`.
4. The live response is used to build a provider fixture ID set. Local LIVE rows absent from that set are currently passed to `ensure_pending()`.
5. The scheduler commits successful live-sync work before it passes the returned finalization candidates to `_finalize_match_sync_candidates()`.
6. `FinalMatchSyncService.sync_final_match()` resolves the local match and provider fixture ID, calls `get_fixtures_by_ids()`, and verifies the returned status before result persistence.
7. The finalizer accepts only `FT`, `AET`, and `PEN` as final statuses.
8. The queue is unique by local match ID. Existing `SUCCESS` and `EXHAUSTED` records are not revived by `ensure_pending()`.

**Frozen end-to-end flow**

1. Run the existing scheduler job every 60 seconds and retain its scheduler advisory lock.
2. When the live-sync gate permits a poll, fetch `/fixtures?live=all` once.
3. Reuse that response to compare provider fixture IDs against local LIVE matches.
4. Select only missing-feed matches satisfying the six-hour eligibility rules below.
5. Call `ensure_pending()` for every eligible candidate before applying the per-run verification cap; commit the candidate records.
6. Retrieve queue work and classify missing-feed work separately from other due finalization work using the current response and local match status. Already queued work is not removed when the six-hour candidate-selection window expires.
7. Process ordinary due finalization work first. Then process at most 10 missing-feed fixture-by-ID verifications in this scheduler run.
8. Pass each selected local match ID through the existing `FinalMatchSyncService.sync_final_match()` path.
9. Persist final results only when provider status is `FT`, `AET`, or `PEN`, and finish the existing event synchronization before marking the queue record `SUCCESS`.
10. Do not process a `SUCCESS` record again.

If the live poll is gated off, existing due queue work still runs. If the live response is unavailable or invalid, do not infer that any match has finished or create new missing-feed candidates; continue processing already-due queue work without waiting for a second live-feed request.

## 3. Six-hour candidate eligibility

All conditions must hold before creating a new missing-feed queue record:

- `Match.provider` identifies API-Football.
- Local `Match.status` is in the existing `LIVE_STATUSES`.
- `provider_fixture_id` is a valid positive integer.
- The kickoff timestamp is timezone-aware and, converted to UTC, satisfies:
  `now_utc - 6 hours <= kickoff_utc <= now_utc`.
- The fixture ID is absent from the valid current live-feed response.
- There is no existing finalization record in `SUCCESS`.

Use one UTC `now` value for the selection pass. Convert timezone-aware `match_time` values with `astimezone(timezone.utc)`; do not interpret a naive timestamp using the host's local timezone. A naive or otherwise invalid kickoff timestamp is not eligible and must be surfaced in logs/metrics rather than silently assigned a timezone.

The six-hour bound applies only to **new candidate selection**. If a candidate already has a durable queue record, queue eligibility and retry time govern its processing; expiry of the kickoff window must not delete, suppress, or reset that record.

**Source conflict:** the current missing-feed loop reads all rows returned by `get_live_matches()` and has no kickoff-time bound. `MatchRepository.get_live_stale()` accepts a threshold but is not used by this handoff. Implementation must add the approved six-hour predicate without changing the existing status definitions.

## 4. Ten-request-per-run limit

The limit applies to fixture-by-ID verification for matches classified as missing from the current live feed. It does not count the one live-feed request or unrelated due finalization requests.

**Application point and ordering**

- Apply the cap in `LiveUpdateScheduler` when selecting work to pass to `_finalize_match_sync_candidates()`, after durable queue creation and before fixture-by-ID requests.
- Use the queue's existing deterministic order (`next_retry_at`, then local match ID) for due candidates. For newly enqueued records with equivalent retry times, local match ID breaks ties. This gives older scheduled work priority without inventing a new priority field.
- Process non-missing due finalization work first. Then select no more than 10 due matches that are currently local-LIVE and absent from the current live-feed ID set.
- The cap is on selected missing-match verifications, not the number of records enqueued. Enqueue all eligible matches first; unselected queue records remain durable and can be selected in later scheduler runs.
- Missing candidates already included in the due work are handled in that pass and must not also be submitted through a second candidate list. Deduplicate all work by `local_match_id` and keep a per-run processed/selected ID set.
- If a lock conflict or state check prevents a selected ID from reaching the provider call, do not retry that ID through another path in the same scheduler run. Its queue record remains available for a later run.

Existing due work remains higher priority than newly detected candidates and is not blocked by the missing-candidate cap. When a candidate remains local-LIVE and absent from the feed on a later run, it is still classifiable as missing work even if its original six-hour selection window has expired, because its queue record is already durable.

`FixtureProvider.get_fixtures_by_ids()` accepts a sequence, but `FinalMatchSyncService` currently calls it with one ID. This design does not assume or introduce batching; count one provider request per selected match.

## 5. Unknown provider status and response handling

Only `FT`, `AET`, and `PEN` may trigger final result synchronization. Status comparison is normalized by trimming and uppercasing the provider status.

| Provider result | Required behavior |
|---|---|
| Valid terminal status `FT`, `AET`, or `PEN` | Validate final state, persist via the existing final result path, sync events, then mark `SUCCESS` only after successful completion. |
| Known active/waiting status in the existing wait set, including `ET` and `P` | Return `WAITING`; persist as `PENDING` with the existing waiting retry schedule, unless the attempt limit is reached. Never persist final result fields as a final result. |
| Structurally valid response with an unrecognized status | Return retryable `FAILED` with a distinct unknown-status category and diagnostic status value. Use the existing FAILED schedule; do not classify it as terminal or immediately `EXHAUSTED`. At the existing limit, normal policy may exhaust it. |
| Valid response with no matching fixture | Retryable `FAILED`, distinct missing-fixture category. |
| Temporary HTTP/transport failure, timeout, or no response | Retryable `FAILED`, distinct provider-failure category. |
| Malformed response structure or duplicate fixture identity | Preserve the existing non-retryable invalid-response handling (`EXHAUSTED`); do not relabel it as unknown status. |
| Terminal status with invalid elapsed/score fields | Preserve existing invalid-final-state handling (`EXHAUSTED`); no final result persistence. |
| Invalid/missing local match identity | Preserve the existing identity failure handling; do not issue provider verification. |
| Event sync fails after final result fields were staged | Retryable `FAILED` under the existing behavior; do not mark `SUCCESS`. A transaction may commit terminal result fields together with the failed queue state, so retries must remain idempotent and use the authoritative finalizer. |

**Source conflict:** current `sync_final_match()` treats statuses outside `FINAL_MATCH_TERMINAL_STATUSES` and `FINAL_MATCH_WAIT_STATUSES` as non-retryable `EXHAUSTED`. Implementation must distinguish an otherwise valid unknown status and return retryable `FAILED`, without broadening the terminal set.

## 6. Existing retry schedule and state transitions

These are **verified source facts** from `MatchFinalizationRepository`:

- Maximum attempts: 5; `mark_running()` increments `attempt_count` before each execution.
- Retryable FAILED delays for attempts 1–4: 15, 30, 60, and 120 minutes.
- WAITING delays for attempts 1–4: 5, 10, 15, and 30 minutes.
- A retryable outcome at attempt 5, any non-retryable failure, or a waiting outcome at attempt 5 becomes `EXHAUSTED`.
- Stale `RUNNING` threshold: 5 minutes. Due selection includes stale `RUNNING`; scheduler recovery marks it retryable `FAILED` and defers the next attempt.
- Due selection includes due `PENDING`, due `FAILED`, and stale `RUNNING`; it excludes `SUCCESS` and `EXHAUSTED`.
- `WAITING` is a service outcome, not a persisted model state. `mark_waiting()` stores `PENDING` or `EXHAUSTED`.
- `mark_success()` stores `SUCCESS`, clears retry time and failure metadata, and sets `completed_at`.

| State/outcome | Current/design behavior |
|---|---|
| `PENDING` | Due when `next_retry_at` is null or not later than now. |
| `RUNNING` | Attempt claimed and committed before provider work; stale work is made retryable after 5 minutes. |
| `WAITING` | Transient outcome converted to `PENDING` using the waiting schedule, or `EXHAUSTED` at the limit. |
| `FAILED` | Retryable outcomes use the FAILED schedule; non-retryable outcomes or attempt 5 become `EXHAUSTED`. Unknown valid statuses use this retryable path under the frozen design. |
| `EXHAUSTED` | Existing terminal-for-this-queue state. Not reactivated or redesigned here. |
| `SUCCESS` | Completed; excluded from further processing. |

## 7. Duplicate prevention, locks, and transaction safety

**Verified source facts**

- `MatchFinalization.match_id` is the primary key and references `Match.local_match_id`.
- `ensure_pending()` uses conflict-safe insertion and returns an existing record without resetting it.
- The scheduler uses its existing advisory lock (`LIVE_MATCH_SYNC_LOCK_KEY`) and `max_instances=1`.
- Live result writes and final result persistence use `MATCH_RESULT_LOCK_RESOURCE` keyed by local match ID.
- The live and final sync orchestration also use the existing shared `live_sync:global` resource lock.
- Resource locks are PostgreSQL transaction-scoped advisory locks. The transaction, not the lock wrapper's exit, releases them.
- Live-sync queue insertion is committed before finalization is called. A process interruption after that commit leaves the queue record available for a later due pass.

**Frozen requirements**

- Keep all lock identities and existing transaction boundaries.
- Deduplicate work by local match ID across due and newly detected candidate lists.
- Do not call `ensure_pending()` in a way that resets `FAILED`, `RUNNING`, `EXHAUSTED`, or `SUCCESS` records.
- `SUCCESS` is terminal for this handoff.
- A final result is not successful queue completion until event synchronization succeeds.

## 8. Failure-handling matrix

| Situation | Queue/result behavior |
|---|---|
| Existing `PENDING` | Process only when due; otherwise retain unchanged. |
| Existing fresh `RUNNING` | Skip; do not duplicate provider request. |
| Existing stale `RUNNING` | Use existing stale recovery, then retry according to the existing schedule. |
| Existing `FAILED` | Process only when due; retain scheduled retry time. |
| Existing `EXHAUSTED` | Do not reactivate or process in this design. |
| Existing `SUCCESS` | Stop processing; do not enqueue duplicate work. |
| Duplicate `ensure_pending()` | Return existing queue row; unique key prevents a second row. |
| Scheduler interruption after queue commit | Existing due selection can resume the durable record. |
| Scheduler interruption before queue commit | No durable new record; a still-eligible local LIVE/missing match can be selected on a later poll. |
| Provider timeout or temporary API error | Retryable `FAILED`, existing retry schedule and cap. |
| Fixture absent from by-ID response | Retryable `FAILED`, existing retry schedule and cap. |
| Known active/nonterminal status | `WAITING` outcome, then `PENDING` on waiting schedule. |
| Valid unknown status | Retryable `FAILED` under the frozen design; distinct from malformed data. |
| Malformed response or invalid terminal data | Existing non-retryable handling; no final result write. |
| Terminal result and event sync succeed | Persist final state and mark `SUCCESS`; stop. |
| Event sync fails | Retryable failure; not `SUCCESS`; existing retry cap applies. |

## 9. Existing files and functions to reuse

- `app/services/scheduler.py`
  - `LiveUpdateScheduler.start()`
  - `_sync_live_matches_job()`
  - `_finalize_match_sync_candidates()`
  - scheduler advisory lock helpers
- `app/services/fixture_sync_service.py`
  - `FixtureSyncService.sync_live_matches()`
- `app/repositories/match_repository.py`
  - `get_live_matches()`
  - `get_live_stale()` (currently unused by this handoff; requires the approved threshold and provider filtering)
  - `get_by_provider_fixture_id()`
  - `update_live_state()`
- `app/repositories/match_finalization_repository.py`
  - `ensure_pending()`
  - `get_due_match_ids()`
  - existing state-transition methods
- `app/providers/fixture_provider.py`
  - `get_live_fixtures()`
  - `get_fixtures_by_ids()`
- `app/services/final_match_sync_service.py`
  - `sync_final_match()`
  - `_validated_state()`
- `app/services/resource_lock.py`
  - `run_with_resource_lock()`
- Existing `Match` and `MatchFinalization` models; no schema change is required by this design.

## 10. Minimum implementation changes

1. Add the UTC-aware six-hour kickoff filter to new missing-feed candidate selection. Exclude invalid/naive timestamps with explicit logging/metrics. Preserve already-enqueued queue work regardless of kickoff age.
2. Ensure every eligible missing-feed candidate is durably `ensure_pending()`-ed before limiting provider work.
3. Update scheduler orchestration to classify due work against the already-fetched live response, process ordinary due finalization work first, and then select at most 10 missing-feed verifications. If live-feed retrieval fails, still process already-due queue work.
4. Deduplicate due and newly detected IDs by `local_match_id`; maintain per-run selection/processed IDs so a match is not submitted twice.
5. Use deterministic ordering from existing queue order (`next_retry_at`, local match ID); leave unselected candidates durable and eligible for subsequent due processing.
6. Change only the unknown-status branch in `FinalMatchSyncService` to retryable `FAILED` with a distinct category. Keep terminal statuses, known waiting statuses, malformed response handling, missing fixture handling, and invalid final-data handling distinct.
7. Add focused tests for time-window boundaries/timezones, queue durability outside the window, the 10-request cap and subsequent eligibility, due-work priority, no duplicate IDs, unknown-status retries, and terminal-only persistence.

No new table, queue, service, scheduler, or result-persistence path is needed.

## 11. Required tests and verification plan

**Automated tests**

- Include a kickoff exactly at `now_utc - 6 hours`, exactly at `now_utc`, just outside either boundary, and in the future.
- Confirm timezone-aware non-UTC kickoff conversion and rejection/reporting of naive timestamps.
- Confirm only API-Football rows with valid IDs and existing LIVE statuses are selected, and only when absent from the same feed response.
- Confirm all eligible candidates are durably queued before the per-run cap; more than 10 candidates leave the remainder queued and eligible later.
- Confirm ordinary due finalizations are processed before missing-feed candidates and are not charged against the missing-feed limit.
- Confirm duplicate due/new candidate IDs result in at most one finalizer submission per scheduler run.
- Confirm a queued candidate remains due after the six-hour selection window ends.
- Confirm `P`, `ET`, and known wait statuses remain nonterminal; unknown valid status becomes retryable `FAILED`; malformed responses and invalid terminal data retain distinct failure categories.
- Confirm only `FT`, `AET`, and `PEN` can reach final result persistence and `SUCCESS`.
- Confirm repeated `ensure_pending()` does not reset existing state and `SUCCESS` is not rescheduled.

**Runtime verification after implementation approval**

- In a safe non-production environment, verify advisory lock behavior, commit/restart recovery, SQL query plans, and that fixture-by-ID requests per scheduler run stay within the cap for missing-feed work.
- Verify provider status/error payloads and monitor actual API usage.
- Do not claim production verification from source inspection or unit tests alone.

## 12. Explicit out-of-scope items

- Reactivating or changing `EXHAUSTED` records.
- Arbitrary terminal-match reconciliation without a finalization record.
- Scanning historical matches outside the six-hour new-candidate window.
- New schema, migration, queue, service, worker, scheduler, frontend feature, or final-result write path.
- Changing retry delays, maximum attempts, terminal protection, lock identities, or unrelated sync behavior.

## 13. Final Design Freeze summary

The approved design is to use the existing live-feed comparison and finalization queue, with a six-hour UTC kickoff window for **new** missing-feed candidates, durable queueing before limiting work, at most 10 missing-feed fixture-by-ID verifications per scheduler run, and retryable handling for otherwise valid unknown provider statuses. Existing due work retains priority; successful finalization stops processing.

The current source already contains the core missing-feed detection, queue, provider-by-ID verification, terminal validation, and lock mechanisms. It does **not** contain the six-hour candidate bound, the 10-request selection limit, or the required retryable unknown-status behavior. Those are the minimum implementation changes. EXHAUSTED remains outside this design.

This is a source-verified design only. No runtime or production database verification is claimed. No application code, database schema, or database data was modified in this design phase.
