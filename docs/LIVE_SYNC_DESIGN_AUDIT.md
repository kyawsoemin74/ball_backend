# LIVE_SYNC Design and Execution Flow Audit

**Audit type:** Read-only source/test inspection  
**Scope:** Normal scheduled live fixture synchronization and its interaction with finalization  
**Verdict:** **DESIGN GAP FOUND**  
**Runtime verification:** Not performed

## 1. Executive Summary

Normal `live_sync` is triggered by the dedicated worker's APScheduler job every 60 seconds, subject to a database eligibility gate. It requests the provider's all-live fixture endpoint, maps each returned fixture to an existing local row by `(provider, provider_fixture_id)`, and updates only status, elapsed time, and home/away goals. It does not create missing local matches.

The source treats `P` as live and `PEN`/`FT`/`AET` as finished for candidate/finalization purposes, but normal live sync does not validate state transitions before writing. `MatchRepository.update_live_state` unconditionally sets status, elapsed, and scores by local match ID. As a result, a stale `P`, `2H`, or `LIVE` response can overwrite an already terminal match, including after finalization has marked it `SUCCESS`. The finalization state does not protect the `matches` row.

The scheduled live job performs due finalizations **before** its normal live-feed update, then finalizes candidates discovered by that update. This ordering makes the regression path concrete: a fresh exact-ID request can finalize a match, followed in the same scheduler job by a stale all-live response that reopens its canonical status. The live scheduler and finalizer share global lock scope with each other, but Daily Repair uses a different lock namespace and can overlap.

Other verified characteristics:

- The provider URL is `/fixtures` with `live=all`; no league/season filter or live-response pagination is used.
- `FootballAPIClient.get` retries failed HTTP requests twice (three HTTP attempts total) without delay/backoff; no response cache exists in the provider/client path.
- An empty `response` list is treated as a successful empty live feed; local rows still marked live are checked for missing-feed finalization candidates.
- A returned fixture with no local row is skipped, not inserted.
- Existing live rows absent from the provider feed are not immediately changed; they are registered for exact-ID finalization through the durable path.
- Worker manifests specify one replica, and scheduler/advisory locks coordinate normal live jobs, but deployed worker count/runtime lock behavior was not verified.

**Impact:** Normal live sync is not safe to coexist unmodified with the agreed finalization design. The existing finalizer itself requires provider confirmation and does not write `P`; the normal live update path can still regress finalized data. The smallest required protection is an atomic terminal-to-nonterminal update guard at the repository boundary, paired with focused interaction tests. No architecture replacement is needed.

No provider request, synchronization job, database query, API mutation, service restart, or code change was performed. Findings about deployed runtime, provider credit quota, and live database metadata are **NOT VERIFIED**.

## 2. Current Live Sync Architecture

The normal path follows the existing architecture:

| Layer | Component | Responsibility |
|---|---|---|
| Worker entry | [`worker.py`](../worker.py), `run_worker` | Starts the dedicated scheduler after dependency health checks. |
| Scheduler startup | [`scheduler_service.py`](../scheduler_service.py), `start_scheduler` | Starts the singleton `LiveUpdateScheduler`. |
| Job orchestration | [`app/services/scheduler.py`](../app/services/scheduler.py), `LiveUpdateScheduler` | Runs the eligibility gate, locks, finalization passes, DB transactions, active-match update, and cache invalidation. |
| Service read/provider | [`app/providers/fixture_provider.py`](../app/providers/fixture_provider.py), `FixtureProvider.get_live_fixtures` | Calls the all-live fixture endpoint. |
| HTTP transport | [`app/services/base/football_client.py`](../app/services/base/football_client.py), `FootballAPIClient.get/request` | Sends the request, instruments provider calls, retries failed HTTP attempts, returns the payload or `None`. |
| Sync orchestration | [`app/services/football.py`](../app/services/football.py), `FootballAPIService.sync_live_matches` | Facade to `FixtureSyncService.sync_live_matches`. |
| Sync service | [`app/services/fixture_sync_service.py`](../app/services/fixture_sync_service.py), `FixtureSyncService.sync_live_matches` | Validates response shape, maps provider fixture IDs to local matches, updates live fields, and registers finalization candidates. |
| Persistence | [`app/repositories/match_repository.py`](../app/repositories/match_repository.py), `update_live_state` | Writes status/elapsed/home score/away score. |
| Finalization persistence | [`app/repositories/match_finalization_repository.py`](../app/repositories/match_finalization_repository.py) | Persists due finalization state when live transitions or missing live records are found. |
| Database | `matches`, `match_finalization` | Canonical match state and durable finalization work. Runtime schema was not queried. |

## 3. Complete Execution Flow

1. `worker.py:run_worker` calls `start_scheduler`.
2. `LiveUpdateScheduler.start` registers `_sync_live_matches_job` with `IntervalTrigger(seconds=60)`, job ID `sync_live_matches`, and `max_instances=1` (`scheduler.py`, lines 503–514).
3. `_sync_live_matches_job` opens an `AsyncSession` and acquires the scheduler-wide session advisory lock. If unavailable, it skips the cycle.
4. It obtains due `match_finalization` IDs (up to the repository default limit of 100) and invokes `_finalize_match_sync_candidates` before consulting the normal live-sync gate.
5. `_should_sync_live_matches` queries all API-Football matches. Any status in `LIVE_STATUSES` is eligible regardless of match date. `NS` rows are eligible only in the configured pre/post-kickoff window. Other statuses do not make the gate true.
6. If eligible, the scheduler takes the transaction-scoped `live_sync:global` resource lock and calls `football_service.sync_live_matches`.
7. `FootballAPIService.sync_live_matches` delegates to `FixtureSyncService.sync_live_matches`.
8. The provider call returns a dict containing a list at `response`; malformed/absent data returns `success=False`. A list (including an empty list) is processed.
9. The service builds a set of valid provider fixture IDs, then iterates the original provider list. For each structurally usable fixture it converts fixture ID, elapsed, and goals, looks up the existing `api-football` row, and writes status/elapsed/scores.
10. When the prior local status is live and the provider status is terminal (`FT`, `AET`, `PEN`), the service ensures a durable finalization row and returns a candidate unless the row is `SUCCESS` or `EXHAUSTED`.
11. It then loads all locally live matches, including `P`, that were absent from the provider response. For each API-Football row it ensures a finalization row and returns a candidate unless state is `SUCCESS` or `EXHAUSTED`.
12. On successful live sync, the scheduler commits DB writes, applies active-match registry updates, and invalidates the `live_matches` cache. On an unsuccessful result or exception it rolls back. The lock is released in `finally`.
13. The scheduler calls `_finalize_match_sync_candidates` for candidates produced by this live sync, then releases the job-wide advisory lock.

### Persisted fields in normal Live Sync

`sync_live_matches` calls `MatchRepository.update_live_state`; it updates only `status`, `elapsed`, `home_score`, and `away_score`. It does not update `match_time`, team/league metadata, or insert new matches. The full fixture upsert path used by daily/full-season sync is separate.

## 4. Provider Retrieval Audit

### Endpoint and request behavior

- `FixtureProvider.get_live_fixtures` calls `client.get("/fixtures", params={"live": "all"})`.
- The request is not restricted by league or season.
- That method makes one provider-level call and does not inspect `paging` or fetch additional pages. Full-season fixture retrieval has separate pagination logic; live retrieval does not.
- The provider/client path does not cache fixture responses. Cache operations in the scheduler invalidate the application `live_matches` result cache after writes; that is not provider-response caching.
- `FootballAPIClient.get` calls `request` with the default `retries=2`. `request` makes up to three HTTP attempts, catches request/status/JSON exceptions, returns `None` after the last failure, and increments retry metrics between attempts. It has no sleep/backoff and does not honor `Retry-After` in this code path.
- Missing API key raises before the retry loop. API-level JSON `errors` in a successful HTTP response are not independently validated by `sync_live_matches`; only the `response` list shape is checked.
- No request was made during this audit. Actual provider pagination/omission behavior and credit cost are **NOT VERIFIED**.

### Can the provider omit a previously live match?

**CONFIRMED as a code-path possibility.** The service explicitly identifies locally live provider matches missing from the current `live=all` fixture IDs and registers them as finalization candidates. It does not change their canonical status based only on absence. The durable worker then requests that fixture ID separately.

An empty list follows the same missing-fixture candidate path and is considered a successful empty response. If the provider temporarily omits a match and the exact-ID response is also missing, the finalization path can retry while its durable state remains eligible; retry exhaustion is covered in the earlier design audit, not duplicated here.

## 5. Match Selection and Persistence Audit

### Local match selection

- Provider fixture IDs are parsed as positive integers for the missing-feed set.
- Returned payloads are looked up with `get_by_provider_fixture_id(db, "api-football", provider_fixture_id)`.
- This maps provider fixture identity to the local row; local `local_match_id` is not assumed to equal the provider ID.
- The Match model has a unique constraint on `(provider, provider_fixture_id)`, preventing duplicate canonical identities.
- If an otherwise valid live fixture has no local row, normal Live Sync increments `skipped` and does not insert it. Daily/full-season fixture sync is responsible for normal fixture creation and league/team identity resolution.
- The normal live service does not re-resolve team or league identities; it relies on the existing local match identity.

### Provider payload processing

For each fixture, the service requires a fixture dict, status dict, and goals dict. It converts:

- `fixture.id` to integer;
- status to `str(status.short or "NS")`;
- elapsed to `int(elapsed or 0)`;
- home/away goals to `int(goals.side or 0)`.

Malformed payloads are logged and skipped. No monotonicity check is made for status, elapsed, or score. Missing/falsey elapsed or goals can be coerced to zero. The stored status is the provider's short string; it is not translated to a separate internal enum.

### Duplicate payloads

`provider_fixture_ids` is a set used to compare provider IDs against local live matches missing from the feed. The actual processing loop iterates the original fixture list and does not deduplicate repeated fixture payloads. A duplicate provider fixture can therefore update the same local row more than once in one cycle; the model's unique constraint prevents creating a second canonical row, but does not prevent duplicate processing or last-payload-wins behavior.

### Match date/time and other statuses

Normal Live Sync does not update match date/time, teams, league, venue, or other fixture metadata. It persists any supplied status short string; it does not whitelist only live statuses. The code defines `PST`, cancellations, abandonment, awarded, and walkover as terminal for active-match registration/normal scheduler gating, while final-match finalization only accepts `FT`, `AET`, and `PEN`. Postponed/cancelled/abandoned status handling in the normal live feed is not a robust terminal-finalization path.

## 6. Status Mapping Audit

There is **no provider-to-local translation table** in normal Live Sync. The status short code is copied to the Match row. Sets determine scheduling/candidate behavior:

| Provider code | Source classification | Normal Live Sync behavior |
|---|---|---|
| `NS` | Not included in live set; scheduler may admit near kickoff. | Written as raw `NS` if returned; not a live local candidate afterward. |
| `1H`, `HT`, `2H`, `ET`, `BT`, `LIVE`, `P` | Included in `LIVE_STATUSES`; `P` is also in active-match registration. | Written as received and considered live by subsequent local selection. |
| `PEN`, `FT`, `AET` | In `FINISHED_STATUSES` / `FINAL_MATCH_TERMINAL_STATUSES`; finalization terminal set. | Written as received; if previous status was live, candidate is queued for exact-ID finalization. |
| `CANC`, `ABD`, `AWD`, `WO` | In `FINISHED_STATUSES`; not in final-match result terminal allowlist. | Written if returned; removed from live selection; no `FT/AET/PEN` finalization candidate by this branch. |
| `PST` | Added to active-match terminal/stop set. | Written if returned; removed from local live sweep. |
| Unknown status | Not explicitly classified or rejected in this method. | Can still be written to a matched row. |

`P` remains live. `PEN`, `FT`, and `AET` are terminal for finalization. However, the fact a status is in a set does not protect the write: normal Live Sync calls the repository update before evaluating the transition candidate.

## 7. Terminal Status Protection Audit

### Finding: no protection exists

`MatchRepository.update_live_state` executes an unconditional update by `local_match_id`, setting status, elapsed, and both scores. Its `WHERE` clause has no terminal-status predicate, version/timestamp check, or compare-and-set condition.

`FixtureSyncService.sync_live_matches` reads `previous_status`, but only uses it after the update to determine whether to create a finalization candidate. It does not reject a stale status or compare elapsed/scores before persistence. `match_finalization.state == "SUCCESS"` is not checked before the update.

### Consequences

- A terminal `PEN`, `FT`, or `AET` row can be overwritten by `P`, `2H`, `LIVE`, or another status.
- Elapsed time and scores can also regress because they are assigned unconditionally.
- A previously `SUCCESS` finalization row does not prevent regression. After the row is changed back to `P`, the missing-live sweep finds it, but sees `SUCCESS` and skips re-finalization.
- Duplicate provider payloads in the same response can also overwrite the same local row sequentially.

**Severity: CRITICAL.** This can make finalized match status/scores incorrect and leave finalization marked successful.

## 8. Finalization Interaction Audit

### Actual scheduler order

```text
sync_live_matches job (60s)
  ├─ acquire scheduler session advisory lock
  ├─ load due match_finalization rows
  │    └─ FinalMatchSyncService exact-ID fetch → terminal validation → Match update/events → SUCCESS
  ├─ eligibility gate
  ├─ FixtureProvider GET /fixtures?live=all
  │    └─ FixtureSyncService updates matches and returns terminal/missing-live candidates
  ├─ commit normal live writes; update active registry; invalidate live_matches cache
  ├─ finalize candidates discovered in live feed
  └─ release scheduler session advisory lock
```

### Interaction findings

- Normal Live Sync and finalization update the same `matches.status`, `elapsed`, `home_score`, and `away_score` fields.
- Finalization also changes `match_finalization`; normal Live Sync may create/retrieve that row through `ensure_pending`, but does not use it as a guard on match writes.
- Due finalization is called before the live feed. A stale live response can overwrite the newly finalized Match later in that same job.
- If the live feed itself shows a transition to terminal, the job first persists it and then does a separate exact-ID provider read for finalization. This is intentionally authoritative but is an additional request for that match.
- If the provider omits a locally live match, the second local-live sweep creates/reuses finalization eligibility. This prevents a match from being silently dropped solely because `/live=all` omitted it, unless its finalization state is `SUCCESS` or `EXHAUSTED`.
- The daily repair job uses a different session advisory lock (`REPAIR_DAILY_MATCHES_LOCK_KEY`) and `fixture_query:global` resource lock, not the live job's lock keys. Its `_process_sync` path can upsert the same Match data. It can therefore overlap live sync at the database level; whichever transaction writes last can determine the row values.
- The normal live job and its due finalizer are sequential under one scheduler job lock, not concurrent with each other in that path. That serialization does not prevent stale-response ordering regression.

## 9. Scheduler, Worker, and Concurrency Audit

- `LiveUpdateScheduler.start` registers `sync_live_matches` every 60 seconds with `max_instances=1`.
- The scheduler instance is a module singleton started through `scheduler_service.start_scheduler`, called by `worker.py`.
- `_acquire_advisory_lock` obtains a PostgreSQL session-level advisory lock on a dedicated pinned connection and releases it on the same connection in `finally`. `LIVE_MATCH_SYNC_LOCK_KEY` coordinates the live job across scheduler processes using the same database.
- The job also uses `live_sync:global` transaction-scoped resource locks around finalization and live sync operations.
- Daily Repair has separate advisory/resource lock identities (`REPAIR_DAILY_MATCHES_LOCK_KEY` and `fixture_query:global`). Those do not mutually exclude the live sync lock.
- Kubernetes worker deployment and HPA both specify one replica/max replica one. This is configuration evidence only, not a check of the deployed cluster.
- `coalesce` and `misfire_grace_time` are not explicitly supplied when the scheduler is constructed or the job is added. Effective APScheduler defaults were not runtime-inspected.
- A scheduler job exception is logged; the next interval can retry. Database exceptions in the normal live operation trigger rollback. Abrupt process death releases a PostgreSQL session lock when its connection is closed, but live crash behavior was not runtime-tested.

**Assessment:** Normal live jobs are protected against overlapping instances and cross-process duplicate live-job execution by source-level locks. Live Sync versus Daily Repair is not covered by one shared lock, and no lock prevents a stale but serialized write from regressing terminal data.

## 10. API Credit and Performance Audit

### Source-confirmed call count shape

- When the eligibility gate passes, normal Live Sync calls `GET /fixtures?live=all` once per scheduler cycle.
- The HTTP client can make up to three HTTP attempts for that call when the request fails; retries have no delay/backoff in the inspected `request` method.
- Finalization candidates cause separate exact-ID fixture requests. Due finalization is run before the live endpoint; newly discovered terminal candidates are finalized after the live endpoint.
- A successful finalization also requests events, which is an additional provider operation outside the normal live fixture endpoint.
- Live responses are not paginated by `get_live_fixtures`.
- Repeated fixture IDs in the provider response are not deduplicated before processing.
- The due finalization query has a default `limit=100`, but live-feed candidate processing has no equivalent global request cap.
- The scheduler does not call the live provider endpoint when the database eligibility gate has no live or near-start match. Due finalization is processed before this gate.

### Quota conclusion

**NOT VERIFIED:** No provider plan, credit cost per endpoint/attempt, production call metrics, or observed live fixture volume was inspected. Do not assign a credit value from source alone.

**Potential avoidable usage — MEDIUM:** One finalization exact-ID request can follow an all-live response that already included that same terminal fixture. This is consistent with authoritative verification, but it is a second fixture lookup. Retries can multiply failed HTTP attempts up to three per logical client call. A global finalization request budget is not evident in the live sync candidate pass.

## 11. Failure and Missed-Match Scenarios

| Scenario | Actual execution path | Outcome / recovery | Evidence and severity |
|---|---|---|---|
| **A. Provider returns live fixture; local match absent** | Lookup by provider and fixture ID returns `None`; increments `skipped`; no insert. | No local match is created by live sync. Daily/full-season fixture sync must establish identity; next live cycle can update after insertion. A terminal transition may be missed meanwhile. | `fixture_sync_service.py`, `sync_live_matches`, approx. 1224–1233; **MEDIUM**. |
| **B. Provider temporarily omits previously live fixture** | It is absent from provider loop; local `get_live_matches` sweep sees it and ensures finalization state/candidate. | Canonical status is not changed on absence. Exact-ID finalization can recover while its state is eligible; `SUCCESS`/`EXHAUSTED` rows are skipped. | `fixture_sync_service.py`, lines 1263–1289; `match_finalization_repository.py`, due query. **NO GAP** for omission before exhaustion; existing exhaustion gap remains. |
| **C. Provider returns empty list** | Dict with `response: []` passes shape check; result starts successful. Service still scans local live matches and can queue candidates. | No direct match updates; existing local live rows may be exact-ID finalized. Scheduler commits and invalidates live cache. | `fixture_sync_service.py`, lines 1160–1187, 1263–1289; **NO GAP** for safe empty-list handling. |
| **D. API request fails/times out** | Client retries up to 3 HTTP attempts, then normally returns `None`; service reports invalid response/`success=False`; scheduler rolls back and logs. A missing API key raises and is caught by scheduler. | Next scheduler cycle retries; no durable live-sync request queue. Client-level retry is immediate and does not honor `Retry-After`. | `football_client.py`, lines 58–95; `fixture_sync_service.py`, lines 1160–1171; `scheduler.py`, lines 879–953; **MEDIUM**. |
| **E. Provider returns stale status** | Existing row is looked up, then repository overwrites status/elapsed/scores with no state/timestamp guard. | Status, elapsed, and scores can regress. The next live cycle repeats unless provider corrects it. | `fixture_sync_service.py`, lines 1235–1251; `match_repository.py`, lines 21–49; **CRITICAL**. |
| **F. Match changes `P` → `PEN` while live sync/finalization run** | If finalization is due, it runs first and may persist provider-confirmed `PEN`/`SUCCESS`; normal live fetch then runs. A stale live response `P` can overwrite it. If live feed reports `PEN`, normal path updates then exact-ID finalizes after the feed. | No simultaneous execution within the normal job, but sequential stale overwrite is possible; separate Daily Repair may overlap with different lock keys. | `scheduler.py`, lines 889–953; `fixture_sync_service.py`, lines 1235–1258; **CRITICAL**. |
| **G. Local row is terminal; live feed returns non-terminal** | Unconditional update writes the non-terminal data. Candidate condition checks previous status only after update; the missing-live pass can create/return a row, but skips `SUCCESS`/`EXHAUSTED`. | Terminal data can be reopened and left inconsistent with finalization state. | `fixture_sync_service.py`, lines 1235–1289; `match_repository.py`, lines 21–49; **CRITICAL**. |
| **H. Two workers process same fixture** | Same live scheduler job is protected by `max_instances=1` and PostgreSQL session advisory lock; global resource lock also serializes live/finalization operations. Daily Repair uses different locks. | Duplicate live job is prevented if all processes share PostgreSQL. Live vs Daily Repair may overlap. Actual cluster behavior not verified. | `scheduler.py`, lines 503–514, 1582–1663, 1026–1094; **MEDIUM** for cross-job overlap, no same-job gap found. |
| **I. Application restarts during Live Sync** | Scheduler DB transaction rolls back on caught failure; session advisory lock is tied to pinned connection. Candidate row and match writes are in the live transaction; successful commit occurs before active registry/cache actions. | On restart, existing live rows can be rediscovered and due finalizations are persistent. If process dies after commit but before cache/registry work, those derived states can be stale until a later refresh. Not runtime tested. | `scheduler.py`, lines 879–953; `match_finalization_repository.py`; **MEDIUM** operational caveat. |
| **J. DB persistence fails after provider retrieval** | Exception propagates to scheduler wrapper, which rolls back; cache invalidation and registry publish do not occur on failed transaction. | Next scheduler cycle re-fetches provider data and retries. Update is by existing local ID, so no duplicate Match insert in live path. Ambiguous commit is not explicitly reread/verified in normal live sync. | `scheduler.py`, lines 915–953; `match_repository.py`, lines 21–49; **MEDIUM**. |

## 12. Test Coverage

### Existing relevant coverage

- `tests/test_live_sync_phase6.py` verifies scheduler reaches provider boundary, provider/local identity mapping, persisted status reporting for active and terminal tokens, active-status gate behavior, cache/registry ordering, rollback, lock contention, and scheduler retry on the next job run.
- `tests/test_final_match_sync_phase3.py` verifies missing-feed finalization candidate creation and exact fixture-ID verification, terminal statuses, `P` waiting, retry timing/exhaustion, stale `RUNNING` recovery, and mocked duplicate concurrent finalization behavior.
- `tests/test_scheduler_runtime.py` verifies scheduler job registration and advisory lock release on the pinned connection.
- `tests/test_live_identity_phase3.py` covers provider fixture identity and sync boundary behavior.

### Missing or incomplete coverage

- No normal Live Sync regression test where a terminal local Match receives stale `P`, `2H`, or `LIVE`.
- No assertion that elapsed time or scores cannot regress.
- No duplicate provider fixture ID test demonstrating current repeated processing.
- No test for unknown/missing status short code being written as `NS` or unknown strings being stored.
- No local-match-missing test asserting `skipped`/no insertion in the live service.
- No test for `errors` in a successful HTTP JSON body with an empty response.
- No pagination test for the live endpoint; implementation does not paginate it.
- No direct `FootballAPIClient.get` retry/backoff test was found in the test search.
- No integration test for Live Sync racing Daily Repair on the same match.
- No real PostgreSQL multi-process concurrency test or deployed-worker-count verification.
- No runtime crash test during provider fetch/DB commit/cache invalidation.

## 13. Confirmed Design Gaps

| Finding | Evidence | Impact | Severity |
|---|---|---|---|
| Unconditional live-state update has no terminal guard. | `MatchRepository.update_live_state`, [`app/repositories/match_repository.py`](../app/repositories/match_repository.py#L21), updates by local ID only; caller `FixtureSyncService.sync_live_matches`, lines 1235–1251. | Stale non-terminal status, elapsed, or scores can replace terminal match data, including after finalization success. | **CRITICAL** |
| Finalization state does not protect the Match row. | `sync_live_matches` updates before candidate-state filtering; it skips candidates only after updating when state is `SUCCESS`/`EXHAUSTED` (fixture service lines 1235–1289). | A successful finalization may remain marked `SUCCESS` after its data is regressed. | **CRITICAL** |
| Live and Daily Repair do not share lock scope. | Live uses `LIVE_MATCH_SYNC_LOCK_KEY`/`live_sync:global`; repair uses `REPAIR_DAILY_MATCHES_LOCK_KEY`/`fixture_query:global` (`scheduler.py`, lines 79–87, 936, 1026, 1093, 1582–1663). | Both can update overlapping Match fields; last DB writer can prevail. | **HIGH** |
| Duplicate payloads are not deduplicated before processing. | `provider_fixture_ids` is a set, but the update loop iterates `fixtures` directly (fixture service lines 1188–1200). | Repeated updates and response-order-dependent last write. | **MEDIUM** |
| Live response is not paginated/filtered/response-error validated. | `FixtureProvider.get_live_fixtures` makes one `live=all` call; service checks only dict/list shape (provider lines 49–53; fixture service 1160–1171). | If provider truncates/omits fixtures or returns errors with a list, service may treat incomplete data as a valid empty/partial snapshot. Provider behavior is not verified. | **MEDIUM** |
| Immediate transport retries can add unpaced provider attempts. | `FootballAPIClient.request` defaults retries to 2, loops 3 times without delay and does not honor `Retry-After` in `get` path (`football_client.py`, lines 58–95). | Potential provider burst on outages/rate limiting; quota impact is unknown. | **MEDIUM** |
| Unknown/missing status values can be persisted. | Live-state construction defaults missing short status to `NS`; no status allowlist before `update_live_state` (fixture service lines 1200–1236). | A malformed/stale response can overwrite active or terminal status. | **HIGH** |

## 14. Minimal Recommended Fixes

1. **Protect terminal state at the repository write boundary.** Make non-terminal live updates conditional on the row not already being terminal. Return a result that lets the service log/report a stale update rather than presenting it as an ordinary update. Keep terminal statuses distinct; do not infer a terminal status.
2. **Coordinate Live Sync and Daily Repair writes.** Reuse a common match-level/global lock or ensure the terminal-update guard is atomic so distinct scheduler jobs cannot overwrite finalized data. Avoid introducing a new worker.
3. **Deduplicate provider fixture IDs before the persistence loop.** Preserve deterministic handling and report duplicate provider identities.
4. **Validate provider payload status and errors before persistence.** Distinguish invalid response from a valid empty feed; do not default malformed status into a status regression.
5. **Use paced retry/backoff for transport failures and respect provider retry metadata where available.** Preserve the existing 60-second scheduler cadence while avoiding immediate repeated HTTP attempts on 429/5xx/network failures.
6. **Keep the existing candidate/finalization lifecycle.** Missing local live matches should still be handled by the existing fixture ingestion path; local omitted-live matches should continue using exact-ID finalization.
7. **Add focused tests** for terminal status regression, score/elapsed regression, Live Sync/Daily Repair overlap behavior, duplicate provider payloads, malformed/empty responses, and provider transport retry pacing.

These are targeted protections within existing modules, not a replacement synchronization design.

## 15. Final Audit Verdict

**DESIGN GAP FOUND — Specific Live Sync weaknesses must be addressed or accounted for before finalization implementation.**

1. **Is normal `live_sync` correctly retrieving live fixtures?**  
   It correctly calls the configured all-live endpoint when the database gate passes. No runtime/provider completeness or pagination behavior was verified.
2. **Can normal Live Sync miss fixtures or status transitions?**  
   Yes. It skips fixtures without local rows; it relies on the provider live response and the local-live missing-feed sweep; it does not paginate the live response. The missing-feed sweep helps recover omitted local-live matches but does not override `SUCCESS`/`EXHAUSTED`.
3. **Can normal Live Sync overwrite terminal match data?**  
   **Yes.** It unconditionally writes status, elapsed, and scores from the live response.
4. **Can normal Live Sync interfere with finalization?**  
   **Yes.** It shares fields with the active finalizer and runs after due finalization in the same job. It can overwrite the finalizer's result. The finalization row does not guard the Match update.
5. **Are scheduler execution and concurrency adequately protected?**  
   Same live scheduler job is guarded by `max_instances=1`, PostgreSQL advisory lock, and resource locks. Live Sync and Daily Repair have different lock identities and can overlap. Production deployment/runtime behavior is **NOT VERIFIED**.
6. **Is API request usage reasonably controlled?**  
   The job is gated and runs every 60 seconds; one logical live endpoint call can make up to three immediate HTTP attempts. Finalization makes separate exact-ID requests and live candidates have no shared global cap. Actual quota/cost is **NOT VERIFIED**.
7. **What are the confirmed weaknesses?**  
   No terminal write guard; no elapsed/score monotonicity protection; potential live/Daily Repair overlap; duplicate payload processing; no live endpoint pagination/response error validation; unpaced HTTP retry.
8. **What is the minimum change required, if any?**  
   Add an atomic terminal-to-nonterminal write guard in `MatchRepository.update_live_state` and a regression test covering a finalized row receiving stale `P`. Coordinate Daily Repair or rely on the same repository guard to close that race.
9. **Is normal Live Sync compatible with the agreed Finalization Design?**  
   **Not without the terminal-state protection.** The candidate and finalization mechanisms are reusable, but ordinary live updates can undo final data.
10. **Should normal Live Sync be modified before implementing finalization improvements?**  
    **Yes, at minimum the terminal status/score regression guard must be implemented or explicitly included in the same change set before rollout.** Provider pacing and overlap coordination should also be resolved before claiming production safety.

**Source/runtime distinction:** Source behavior and checked-in worker configuration are **CONFIRMED**. Deployed replica count, database metadata, provider omission/pagination behavior, API credit cost, production lock execution, and real transaction outcomes are **NOT VERIFIED**.
