# Coach Sync Implementation and Phase 4 Runtime Report

## Implementation Summary

The current Coach synchronization implementation uses `Coach.updated_at`
with a 24-hour TTL, checks freshness before requesting Coach data, and shares
an execution-scoped Team decision memo across fixture processing. The provider
boundary is `CoachProvider.get_team_coach_response()`, called from
`TeamSyncService.sync_team_coach()`. The local API and worker loaded the
current implementation during Phase 4.

No source code was changed during Phase 4. Runtime test database sessions
were explicitly rolled back; Redis was read only.

## PHASE 4 — LOCAL RUNTIME VERIFICATION

### 1. Environment

- API base URL: `http://127.0.0.1:8000`; `/health/ready` returned HTTP 200
  with PostgreSQL and Redis ready.
- API `DATABASE_URL` target: `postgres:5432/fover_db`, resolving to local
  container `fover_postgres`.
- Redis target: `redis:6379/0`, resolving to local container `fover_redis`.
- The worker ran in `fover_worker`; scheduler enabled in worker and disabled
  in API.
- At the first inspection, running containers had stale code. On the
  subsequent inspection, both API and worker loaded
  `/usr/src/app/app/services/team_sync_service.py` with
  `COACH_TTL = 1 day, 0:00:00`; its SHA-256 matched the workspace file.
  This verification did not restart any service.
- No request was sent to `https://kyawsoemin.com`, no production endpoint
  was accessed, and no scheduler/API synchronization endpoint was triggered.
- Two controlled calls went to the configured API-Football provider: one for
  an expired Coach and one for a missing Coach. Both database sessions were
  rolled back.

### 2. Database Baseline

Read-only queries ran in explicit `BEGIN READ ONLY` transactions against
local PostgreSQL. The pre-test snapshot at `2026-10-03 04:52:17 UTC` showed
61 Coaches, 61 distinct non-null `(provider, provider_id)` identities, 61
Coach rows with `updated_at`, 10 Teams with `coach_id`, no broken
associations, and no duplicate provider identity groups. The `updated_at`
range was `2026-09-25 06:31:50 UTC` through `2026-10-03 04:47:25 UTC`.

The post-test snapshot at `2026-10-03 04:54:38 UTC` returned the same Coach,
identity, and association counts and the same timestamp range. Teams 77, 81,
91, and 112 retained their original associations. The expired Coach refresh
was rolled back.

### 3. Redis Baseline

Read-only Redis `SCAN` using `*coach*` returned no keys. The scan was repeated
after testing and still returned no matching keys. No Redis data was changed.

### 4. Provider Metric Baseline

The existing metric name was inspected: `fover_provider_requests_total`,
with `method`, `path`, and `status` labels. The local worker metrics endpoint
and Prometheus query returned no `/coachs` sample series after the updated
containers started, so a numeric counter baseline was unavailable. Controlled
request counts below are based on the live provider-method wrapper and
matching `httpx` request logs; no metric name was invented.

### 5. Fresh Coach Test

Ran the live `TeamSyncService.sync_team_coach()` against local Team **112**
(provider Team **1101**), whose existing Coach is **105**. At decision time,
the Coach age was about **415 seconds**, within the 24-hour TTL.

- Decision: `COACH_SKIP_FRESH`
- Provider method calls: **0**
- `/coachs` HTTP requests: **0**

### 6. Expired Coach Test

Ran against local Team **81** (provider Team **775**), with existing Coach
**74**. The Coach age at decision time was **88,665.5 seconds**, greater than
24 hours.

- Decision: `COACH_REFRESH`
- HTTP request: exactly one `GET /coachs?team=775`, HTTP 200
- The response resolved to the existing canonical Coach **74**.
- The database session was rolled back after observing the refresh.

### 7. Missing Coach Test

Ran against local Team **77** (provider Team **14**), which had no Coach
association.

- Decision: `COACH_FETCH`
- HTTP request: exactly one `GET /coachs?team=14`, HTTP 200
- The response was ambiguous. The service classified it as
  `COACH_FETCH_FAILED`, did not assign or create a Coach, and the session was
  rolled back.

This verifies the fetch attempt; it does not establish successful Coach
resolution for that Team.

### 8. Batch Deduplication Test

Local Team **81** was processed twice in one live batch. The first appearance
refreshed provider Team **775** and made one HTTP request. The second returned
`COACH_SKIP_BATCH_DUPLICATE` without a provider call. Total requests for that
Team in the execution: **one**.

### 9. Terminal Reprocessing Test

The live fixture-processing batch wrapper was run with nested terminal-style
reprocessing for fresh Team **112**:

- Initial result: `COACH_SKIP_FRESH`
- Nested result: `COACH_SKIP_BATCH_DUPLICATE`
- Provider calls: **zero**

No real terminal fixture was fetched or persisted.

### 10. Refresh Failure Test

Ran against expired local Team **91** (provider Team **1110**), Coach **84**
(provider Coach ID **181**), using a controlled fake provider that raised
`TimeoutError`.

- Result: `COACH_FETCH_FAILED`
- Team association remained Coach **84**
- Existing Coach **84** and provider identity **181** remained present
- No real HTTP request was made; the database session was rolled back

### 11. Provider Request Evidence

Live service output recorded decision categories and matching HTTP client
logs:

| Scenario | Decision/result | Provider calls | HTTP `/coachs` requests |
|---|---|---:|---:|
| Fresh Team 112 | `COACH_SKIP_FRESH` | 0 | 0 |
| Expired Team 81 | `COACH_REFRESH`, success | 1 | 1 |
| Same Team twice in batch | Refresh then `COACH_SKIP_BATCH_DUPLICATE` | 1 total | 1 total |
| Missing Team 77 | `COACH_FETCH`, then ambiguous response / `COACH_FETCH_FAILED` | 1 | 1 |
| Terminal-style nested Team 112 | Fresh then batch duplicate | 0 | 0 |
| Controlled refresh failure, Team 91 | `COACH_FETCH_FAILED` | Fake only | 0 |

### 12. Before/After Call Behavior

The controlled local runtime demonstrated zero requests for a fresh Coach,
one request for an expired Coach, and no more than one request for a repeated
Team in one execution. Historical old-runtime logs showed repeated requests
for provider Team IDs, but they are not a matched before/after workload and
do not support a numeric reduction.

**Call reduction behavior verified by controlled local runtime; production
percentage reduction not measured.**

### 13. Static Bypass Audit

The current application source contains one HTTP `/coachs` request in
`CoachProvider.get_team_coach_response()`. Its only application caller is
`TeamSyncService.sync_team_coach()`, after the shared freshness/batch
decision. No direct alternate `/coachs` call was found. The runtime code hash
matched the current workspace source during these tests.

### 14. Post-Run Database Verification

After controlled calls, read-only SQL confirmed 61 Coaches, 61 distinct
provider identities, 10 associated Teams, zero broken associations, and
zero duplicate provider identity groups. The selected Teams retained their
pre-test Coach associations. No controlled refresh data was committed.
Redis still had no keys matching `*coach*`.

### 15. Final Acceptance Checklist

- [x] Local API, PostgreSQL, and Redis targets confirmed.
- [x] Running application code matched current source.
- [x] Production was not accessed; no sync endpoint or service restart was
  initiated.
- [x] Read-only database baseline captured before and after.
- [x] Redis Coach-key scans found no keys.
- [x] Existing provider metric name inspected; unavailable counter sample
  explicitly recorded.
- [x] Fresh Coach produced zero `/coachs` requests.
- [x] Expired Coach produced one successful refresh request.
- [x] Missing Coach produced one fetch request; ambiguous response remained
  unresolved without creating an association.
- [x] Same Team repeated in one execution produced at most one request.
- [x] Nested terminal-style processing reused the same scoped gate.
- [x] Controlled refresh failure preserved the existing Coach and
  association.
- [x] No duplicate Coach identities or unexpected association changes.
- [x] No Coach Redis cache keys observed.
- [x] Static bypass audit found no hidden application `/coachs` call.
- [x] Decision logs and HTTP request evidence agreed for controlled cases.
- [x] Runtime database sessions were rolled back; Redis was not modified.
- [x] `git diff --check` passed. Implementation changes already staged in
  the worktree were left untouched.

## Phase 4 Final Status

**PASS — LOCAL RUNTIME VERIFIED**

Controlled local runtime calls verified fresh skip, expired refresh,
missing-Coach fetch, batch deduplication, nested terminal-style gate reuse,
and preservation of an existing Coach on refresh failure. The missing-Team
provider response was ambiguous, so no Coach was assigned. A numeric
Prometheus before/after counter and production percentage reduction were not
available or claimed.
