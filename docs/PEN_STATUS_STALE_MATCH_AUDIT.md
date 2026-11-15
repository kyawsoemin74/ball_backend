# Stale API-Football `P` Match Audit

**Audit date:** 2026-11-12  
**Scope:** Read-only code review and read-only SQL against the database configured in this workspace. No provider API requests were made. No source, configuration, scheduler, or database changes were made.

## 1. Executive Summary

**Incident conclusion: NOT VERIFIED.** The workspace-configured database had no match with status `P` at audit time, so there are no incident records to identify or compare with current provider state.

The code correctly treats `P` as live/non-terminal and `PEN` as terminal. The principal code-level recovery gap is in final-match verification: a fixture-by-ID response of `P` is classified as `WAITING`, but only five attempts are allowed. The fifth wait changes the durable finalization record to `EXHAUSTED`; subsequent due queries omit it, and candidate processing explicitly skips it. A local `P` match can then remain in the database without further by-ID verification. The daily repair job only requests yesterday and today, so it cannot repair a match after it falls outside those dates.

This is a **confirmed code-path failure mode**, not a confirmed explanation for the user's reported rows: the live finalization table was missing from the workspace-configured database, so its per-match retry state could not be inspected. In that same database, if used by the running worker, the missing table would make the live-sync job fail at its initial due-finalization query before it calls the provider.

The existing persistence path accepts and writes `PEN` when it is actually fetched. There is no evidence here that the provider returned `PEN` for any reported match and that persistence then failed. No current provider state was requested.

## 2. Existing Match Status Lifecycle

1. `FixtureProvider` requests the provider's `fixtures?live=all`, date-filtered fixtures, or fixtures by ID. The transport methods are in [app/providers/fixture_provider.py](../app/providers/fixture_provider.py).
2. `FixtureSyncService.parse_fixture_to_match` copies `fixture.status.short` directly into the local match status; it does not translate `P` or `PEN`. See [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L241) and [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L266).
3. Live sync updates a found local fixture's status, elapsed time, and scores, then considers any local matches still in the live-status set that were absent from the provider's live response as final-match verification candidates. See [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L1160) and [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L1263).
4. Final-match verification fetches the provider fixture by ID. `P` waits; `FT`, `AET`, or `PEN` are accepted terminal states. On a valid terminal response, it persists status/scores and runs final event sync. See [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L44), [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L144), and [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L186).
5. After five `WAITING` attempts, the finalization state becomes `EXHAUSTED`; exhausted records are not selected as due work and are skipped by the scheduler candidate processor. See [app/repositories/match_finalization_repository.py](../app/repositories/match_finalization_repository.py#L140) and [app/services/scheduler.py](../app/services/scheduler.py#L664).
6. Daily fixture repair requests only the prior day and current day. An older match is outside that repair window. See [app/services/scheduler.py](../app/services/scheduler.py#L1039).

## 3. Provider-to-Database Status Mapping

### Verified code facts

- `P` is present in `LIVE_STATUSES`, `ACTIVE_MATCH_REGISTER_STATUSES`, and `NON_TERMINAL_STATUSES` in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L38). It is also present in the facade's `LIVE_STATUSES` in [app/services/football.py](../app/services/football.py#L40).
- `PEN` is present in `FINISHED_STATUSES` and the final-lineup terminal set in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L38) and [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L42). The final-match service independently recognizes `FT`, `AET`, and `PEN` as terminal in [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L13).
- The final-match service classifies `P` as wait/non-terminal in [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L14).
- The model stores status as a `String(10)` in [app/models/match.py](../app/models/match.py#L36); there is no status enum or alias table in this path.
- `P` is therefore not unknown and is not mapped to `LIVE`, `FT`, or `PEN`. `PEN` is not conflated with `P` or `FT`.
- When a terminal fixture payload is processed through the daily upsert, `status` is included in the update fields in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L266) and the existing-match upsert path. When final-match verification returns a valid terminal response, it calls `update_live_state` with the returned terminal status in [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L186).

**Finding — Provider Status Mapping Defect: not found.** Actual behavior matches the requested meanings: `P` remains live; `PEN`, `AET`, and `FT` are terminal. This is established from code, not from a live provider response.

## 4. Live Sync Trace

- [app/providers/fixture_provider.py](../app/providers/fixture_provider.py#L49) requests fixtures with `live=all`. The API response's `status.short` is copied into the live-state dict without mapping in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L1211).
- Existing local fixtures found by provider fixture ID are updated through `MatchRepository.update_live_state` in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L1235). Thus, if the live response includes a fixture with status `PEN`, the status write occurs before finalization is considered.
- A `P`-to-`PEN` response qualifies for finalization because prior `P` is in the live set and `PEN` is in `FINAL_MATCH_TERMINAL_STATUSES`; the candidate handling is in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L1252).
- If an existing local live match is absent from the current `live=all` fixture IDs, `get_live_matches` still selects it because that repository query filters by `LIVE_STATUSES` and has no age bound in [app/repositories/match_repository.py](../app/repositories/match_repository.py#L100). `P` is included in that set, so this fallback does not initially lose a stale `P` match.
- That fallback calls `ensure_pending`, but appends a finalization candidate only when the record is neither `SUCCESS` nor `EXHAUSTED`, in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L1276). Once it is `EXHAUSTED`, later cycles keep seeing the local row but do not perform another by-ID verification.
- The scheduler's live decision gate counts `P` as live, without a date cutoff, in [app/services/scheduler.py](../app/services/scheduler.py#L832). Its 60-second job is registered in [app/services/scheduler.py](../app/services/scheduler.py#L503). These code paths do not independently exclude old `P` records.

**Finding — Live Sync Filtering Defect: not found in the `P` status filter.** `P` is included and stale local `P` rows remain eligible for the missing-fixture fallback. The effective loss occurs later, when the finalization record is exhausted.

## 5. Final Live Sync / Final Event Sync Trace

- Final-match status handling in [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L144) returns `WAITING` for `P` and accepts `FT`, `AET`, and `PEN` as terminal. It does not finalize `P`.
- After recognizing a terminal status, the service validates `elapsed` and both goal values before calling the repository. If `PEN` is returned with missing/non-integer elapsed or scores, it returns `INVALID_FINAL_MATCH_STATE` with `retryable: False` before writing the terminal status in [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L159) and [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L250). The scheduler marks non-retryable failures exhausted. No provider payload was obtained to determine whether this condition occurred.
- On accepted terminal status, the service persists the provider status and scores, then synchronizes events before marking finalization successful in [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L186).
- `MatchFinalizationRepository.get_due_match_ids` selects `PENDING`, `FAILED`, and expired `RUNNING` records, not `EXHAUSTED`, in [app/repositories/match_finalization_repository.py](../app/repositories/match_finalization_repository.py#L67).
- When the provider continues returning `P`, `mark_waiting` allows attempts 1–4 to be retried after delays of 5, 10, 15, and 30 minutes. At attempt 5 (`FINALIZATION_MAX_ATTEMPTS = 5`), it sets the state to `EXHAUSTED` and clears `next_retry_at` in [app/repositories/match_finalization_repository.py](../app/repositories/match_finalization_repository.py#L10), [app/repositories/match_finalization_repository.py](../app/repositories/match_finalization_repository.py#L17), and [app/repositories/match_finalization_repository.py](../app/repositories/match_finalization_repository.py#L140).
- The scheduler skips `EXHAUSTED` records in [app/services/scheduler.py](../app/services/scheduler.py#L664); its subsequent due query also does not select them. There is no code in the reviewed lifecycle that reopens an exhausted finalization record merely because the associated local match remains `P`.
- A separate `handle_terminal_transition` path can invoke `final_live_sync` and final statistics/lineup work, but it requires terminal status and an enabled transition flag in [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L384). The daily sync explicitly disables that transition flag in its call path. The final-match service is the relevant recovery path for local `P` rows absent from the live response.

**Finding — Missing Recovery Path (confirmed code behavior).** A `P` status remains non-terminal, but the durable retry record becomes terminal `EXHAUSTED` after five waits. The system can then stop checking for the later `PEN`. This is a plausible explanation for persistent old `P` rows, but no such row or finalization state was present in the database snapshot.

## 6. Daily Fixtures / Repair Sync Trace

- `sync_daily_fixtures(db, target_date)` requests fixtures for exactly the supplied date using `FixtureProvider.get_fixtures_by_date`; it does not independently search older dates. See [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L1112) and [app/providers/fixture_provider.py](../app/providers/fixture_provider.py#L46).
- The daily scheduled job requests today's date in Myanmar time at 00:01 in [app/services/scheduler.py](../app/services/scheduler.py#L503).
- The repair job calls daily sync for `yesterday_str` and `today_str` only in [app/services/scheduler.py](../app/services/scheduler.py#L1043), [app/services/scheduler.py](../app/services/scheduler.py#L1058), and [app/services/scheduler.py](../app/services/scheduler.py#L1074).
- Therefore, once the original fixture date is older than yesterday, these date-based jobs do not fetch that fixture. There is no status-specific older-date `P` query in this daily-repair path.
- If an old fixture is returned by one of these date calls or by ID with status `PEN`, the code can persist `PEN`; the date window is the limitation, not a `PEN` status whitelist in the upsert.
- The repair job derives its dates from `datetime.now().date()` while the daily job uses `MM_TZ`. If the worker host timezone differs from Myanmar time, the repair dates can be offset near midnight. Host timezone/runtime behavior was not verified; this is a risk, not a confirmed cause.

**Finding — Daily Repair Date-Window Defect (confirmed design limit).** Old fixtures beyond yesterday are not covered by the daily repair dates. This compounds the finalization retry gap after an old `P` record becomes `EXHAUSTED`.

## 7. Scheduler and Worker Trace

- The worker entry point calls `start_scheduler()` in [worker.py](../worker.py#L16) and [worker.py](../worker.py#L29); the scheduler service starts the shared scheduler in [scheduler_service.py](../scheduler_service.py#L13).
- Live sync is registered every 60 seconds; daily fixtures and daily repair are also registered in [app/services/scheduler.py](../app/services/scheduler.py#L503).
- `_sync_live_matches_job` tries due finalizations before deciding whether to call live fixture sync in [app/services/scheduler.py](../app/services/scheduler.py#L879) and [app/services/scheduler.py](../app/services/scheduler.py#L889). The job uses advisory locks and releases the live lock in its `finally` path.
- Exceptions are logged and counted by the job's outer handler in [app/services/scheduler.py](../app/services/scheduler.py#L955); errors are not silently converted to a successful live-sync result. No production worker logs were available in this audit, so actual job execution, lock contention, and error frequency cannot be verified.

### Workspace-configured database schema finding

The read-only database check found:

- Database date/schema: `2026-11-12`, `public`.
- Match statuses present: `FT=50`, `NS=512`; **zero** rows with status `P`.
- `public.match_finalization` does not exist (`to_regclass` returned `NULL`).
- `alembic_version` reports `20261009_odds_retry_state`.
- The existing migration [alembic/versions/20261005_create_match_finalization.py](../alembic/versions/20261005_create_match_finalization.py#L19) creates `match_finalization`; the model expects it in [app/models/match_finalization.py](../app/models/match_finalization.py#L8).

If this is the same database used by the worker, the first `get_due_match_ids` query in live sync references the missing `match_finalization` table and raises before provider live sync is reached. The worker's outer handler would record a job error and try again on a later interval. This is a **confirmed schema mismatch in the database reachable from this workspace**, but it is not proof of the schema in another deployed environment or of the specific reported matches. The existing migration was not run or modified.

## 8. Database Evidence

The only available database was queried using a transaction explicitly set read-only. Query condition: `matches.status = 'P' AND matches.match_time < CURRENT_DATE`.

**Result:** zero rows. The all-`P` query also returned zero rows. Consequently, there are no local match IDs, provider fixture IDs, league IDs, dates, teams, timestamps, finalization states, or related sync records to report. No records are fabricated.

The `matches` model has `updated_at`, but no separate last-sync timestamp is defined in [app/models/match.py](../app/models/match.py#L67). `MatchRepository.update_live_state` uses raw SQL and sets only status, elapsed, and scores in [app/repositories/match_repository.py](../app/repositories/match_repository.py#L21); it does not update `updated_at`. The checked database also reported zero triggers on `matches`. Thus even if a stale row were found, that column would not reliably identify the last live-state synchronization in this database.

The `match_finalization` table was absent from the checked database, so per-match attempt counts, finalization states, and retry logs could not be queried. No provider status was requested; current `P`/`PEN` status evidence is unavailable by design.

## 9. Confirmed Root Cause(s)

### A. Bounded finalization retries permanently stop rechecking `P`

- **Classification:** Missing Recovery Path.
- **Files/functions:** [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L144), [app/repositories/match_finalization_repository.py](../app/repositories/match_finalization_repository.py#L140), [app/services/scheduler.py](../app/services/scheduler.py#L664), [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L1276).
- **Condition:** A by-ID provider response remains `P` through five finalization attempts; the waiting state becomes `EXHAUSTED`, and future scheduling excludes it.
- **Actual behavior:** No additional by-ID provider call is scheduled by this finalization record. The local match may remain `P` if no other sync path fetches its fixture.
- **Expected behavior:** A match still locally `P` should remain eligible for paced status re-verification until the provider returns a terminal state or an operator-visible terminal failure is recorded.
- **Evidence:** Explicit status sets, attempt cap, due-state filter, skip condition, and local-database fallback are described above.
- **Incident linkage:** Code behavior is verified; actual occurrence for the user's reported rows is **not verified** because the checked database contains no `P` rows and has no finalization table.

### B. Finalization schema missing in the checked database

- **Classification:** Scheduler / Worker Execution Defect.
- **Files/functions:** [app/services/scheduler.py](../app/services/scheduler.py#L889), [app/repositories/match_finalization_repository.py](../app/repositories/match_finalization_repository.py#L67), [app/models/match_finalization.py](../app/models/match_finalization.py#L8), [alembic/versions/20261005_create_match_finalization.py](../alembic/versions/20261005_create_match_finalization.py#L19).
- **Condition:** Worker runs this code against the workspace-configured database, where `public.match_finalization` is absent.
- **Actual behavior:** The due-finalization query fails before live fixture sync. The scheduler records a job error; it does not proceed to the `live=all` request in that cycle.
- **Expected behavior:** The runtime schema should include the table required by the deployed finalization code.
- **Evidence:** Read-only `to_regclass` returned `NULL`; recorded Alembic revision is later than the migration that creates the table.
- **Incident linkage:** Confirmed for the checked database only; **not verified** for the database that contains the user's reported matches.

## 10. Potential Risks

- **Classification: Daily Repair Date-Window Defect.** Once a match is older than yesterday, daily repair no longer requests its date. If finalization is exhausted, this path will not recover it.
- **Classification: Insufficient Evidence.** The API-Football provider might return `PEN` on a by-ID request, or the match may still be `P`. The no-API-call restriction means neither can be established.
- **Classification: Repository / Persistence Defect (conditional risk).** A `PEN` response is not persisted by final-match verification if `elapsed` or either score fails strict integer validation; the service returns a non-retryable invalid-state result before `update_live_state`, and the scheduler exhausts it. This condition is visible in code, but no provider payload shows it happened.
- **Classification: Repository / Persistence observability risk.** The raw state update does not set `matches.updated_at`; that field cannot be relied upon as last-sync evidence.
- **Classification: Scheduler / Worker Execution risk.** The checked database's missing finalization table could block all live sync cycles if it is the target runtime database. Deployment/runtime identity is unverified.
- **Classification: Insufficient Evidence.** The repair job uses host-local `datetime.now().date()` while the daily job uses Myanmar timezone. If host timezone differs, repair dates may be offset; the worker timezone is not known.
- No confirmed provider status-mapping, `P` live-filtering, `PEN` terminal-status, or `PEN` persistence defect was found.

## 11. Exact Affected Files and Line Numbers

- [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L38): live, finished, and non-terminal status sets.
- [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L241): fixture parsing; [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L266): raw provider status assignment.
- [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L1112): date-scoped daily sync; [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L1160): live sync; [app/services/fixture_sync_service.py](../app/services/fixture_sync_service.py#L1276): stale-local-live candidate and exhausted-state exclusion.
- [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L13): terminal set; [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L14): wait set; [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L144): provider response classification; [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L186): persistence and final event path.
- [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L159): terminal-state validation can reject a response before persistence; [app/services/final_match_sync_service.py](../app/services/final_match_sync_service.py#L250): elapsed/score validation.
- [app/repositories/match_finalization_repository.py](../app/repositories/match_finalization_repository.py#L10): five-attempt cap; [app/repositories/match_finalization_repository.py](../app/repositories/match_finalization_repository.py#L67): due-state filter; [app/repositories/match_finalization_repository.py](../app/repositories/match_finalization_repository.py#L140): wait/retry exhaustion.
- [app/services/scheduler.py](../app/services/scheduler.py#L503): job registration; [app/services/scheduler.py](../app/services/scheduler.py#L664): skip exhausted; [app/services/scheduler.py](../app/services/scheduler.py#L832): live-sync eligibility; [app/services/scheduler.py](../app/services/scheduler.py#L889): due-finalization query before live sync; [app/services/scheduler.py](../app/services/scheduler.py#L1039): daily repair; [app/services/scheduler.py](../app/services/scheduler.py#L1058): yesterday request; [app/services/scheduler.py](../app/services/scheduler.py#L1074): today request.
- [app/repositories/match_repository.py](../app/repositories/match_repository.py#L21): raw live-state persistence; [app/repositories/match_repository.py](../app/repositories/match_repository.py#L100): unbounded-by-date live-status query.
- [app/models/match.py](../app/models/match.py#L36): local status field; [app/models/match.py](../app/models/match.py#L67): `updated_at`.
- [app/models/match_finalization.py](../app/models/match_finalization.py#L8): required finalization table mapping.
- [worker.py](../worker.py#L29): worker starts the scheduler.
- [alembic/versions/20261005_create_match_finalization.py](../alembic/versions/20261005_create_match_finalization.py#L19): migration that creates the table missing from the checked database.

## 12. Recommended Minimal Fix — DESIGN ONLY

1. **For the code-level recovery gap:** Preserve periodic, rate-limited provider-by-ID verification for a local match whose status remains `P`, even after the initial short retry budget. Use the existing finalization state/retry mechanism and locks; do not make `P` terminal. Once the provider returns `PEN`, continue the existing terminal persistence and final-event path. Do not rely on the daily date window for stale `P` recovery.
2. **For the workspace database discrepancy:** Before treating this database's worker as operational, reconcile its schema with the already-existing `20261005_match_finalization` migration. No migration or database operation was performed during this audit.
3. Do not expand recovery to arbitrary old `LIVE` statuses; scope recovery specifically to persisted `P` records and make it observable through existing error/metrics mechanisms.

## 13. Regression Tests Required

- Provider `P` remains unchanged through fixture parsing and is included in every live/non-terminal set; `PEN` remains terminal and distinct from `P` and `FT`.
- A local `P` match missing from a `live=all` response is selected for by-ID verification regardless of fixture age.
- Repeated by-ID `P` responses do not permanently remove that still-`P` match from paced verification after the short retry budget.
- When a later by-ID response changes from `P` to `PEN`, the existing repository persists `PEN`, scores, final events, and successful finalization state.
- If a `PEN` response has missing or non-integer elapsed/score fields, verify the status is not silently left `P` with a permanent exhausted state; the response should remain recoverable or persist terminal status separately from optional fields.
- If the live endpoint itself returns `PEN`, verify the status write succeeds even if a prior finalization record was exhausted; verify final-event handling has an explicit, observable outcome.
- Daily repair's yesterday/today requests are not treated as recovery coverage for dates older than yesterday.
- A deployment/schema validation catches a missing `match_finalization` table before the live worker repeatedly fails its initial due-finalization query.
- Verify `updated_at` is not presented as last-sync evidence unless the persistence path actually maintains it.

## Final Conclusion

1. **Why might `P` matches remain unfinished?** The code stops by-ID verification after five responses still report `P`; the resulting `EXHAUSTED` state is never selected again. Daily repair only fetches yesterday and today, so an older match is outside that mechanism.
2. **Where does the lifecycle fail?** At `MatchFinalizationRepository.mark_waiting` when attempt count reaches five, followed by scheduler/repository exclusion of `EXHAUSTED`.
3. **Did the provider return `PEN` but persistence fail?** No such provider response was obtained or available in local evidence. The code normally accepts and persists `PEN`; however, final-match verification rejects the terminal write if elapsed or scores fail strict validation, after which the failure is non-retryable. Whether that condition occurred is not verified.
4. **Can backend stop fetching before status changes?** Yes. After the five wait attempts, it stops fetching the fixture by ID through finalization. A live response containing the fixture could still update it, but that is not guaranteed by the local code evidence.
5. **Status mapping, dates, finalization, or scheduler?** No mapping defect found. The primary code-level defect is the exhausted retry/recovery path, compounded by the repair date window. Separately, the database reachable from this workspace lacks the finalization table, which would prevent live sync from reaching the provider if it is the same runtime database.
6. **Smallest design-only fix?** Keep stale local `P` records eligible for paced by-ID verification beyond the initial retry cap; persist `PEN` through the existing finalization path. Confirm the runtime DB has the existing finalization schema. **Neither fix has been implemented.**

**Overall status: NOT VERIFIED for the reported production incident.** The code-level recovery gap and the workspace-database schema mismatch are evidenced; no stale `P` rows, provider response, finalization state, or production worker logs were available to attribute the reported matches conclusively.
