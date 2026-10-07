# Event Sync `operation` Requirement — Root-Cause Audit

**Mode:** Read-only source/history audit  
**Audit date:** 2026-10-07  
**Verdict:** PARTIAL

## 1. Audit Objective

Trace why `POST /api/matches/sync/{match_id}/events` currently requires
`operation=HISTORICAL_BACKFILL`, identify where and when this requirement
appeared, compare it with the earlier Event architecture, and determine what
would be affected if the parameter were removed.

No production code, tests, database, Redis, API endpoint, provider, migration,
or documentation other than this requested audit report was changed or
invoked.

## 2. Current Endpoint Behavior

### Route and parameter

The route is declared in `app/api/matches.py`, function
`sync_match_events`. `app.main` mounts the matches router with `/api`, so the
HTTP route is:

```text
POST /api/matches/sync/{match_id}/events
```

Its handler parameters include:

```python
match_id: int = Path(..., gt=0)
operation: Literal["HISTORICAL_BACKFILL"] = Query(...)
db: AsyncSession = Depends(get_db)
```

There is no separate request-schema class for `operation`. FastAPI derives
the query parameter schema from the `Literal` annotation and required
`Query(...)` declaration.

- Missing `operation`: framework parameter validation rejects the request
  with HTTP 422.
- Any value other than `HISTORICAL_BACKFILL`: `Literal` validation rejects
  the request with HTTP 422.
- The route also has the existing `current_active_admin` dependency.
  Unauthenticated or insufficiently authorized callers are rejected by that
  dependency; this authorization is independent of the operation parameter.

After request validation, the handler takes the existing `events` resource
lock. Within the lock it checks that the Match exists, its status is `FT`,
`AET`, or `PEN`, its timezone-aware scheduled time is in the past, and it
has no Event rows. If those guards pass, it invokes the Event sync service,
commits on success, invalidates that Match's Event cache, and returns the
operation label in the response.

The `operation` variable is not used to choose a code path. It is returned as
metadata in both the unsuccessful sync result and successful result. The
first-snapshot/status/time checks, not the value of `operation`, implement
the data-safety behavior.

## 3. Exact Origin of `operation`

The parameter is present in the current uncommitted Phase 3D implementation
diff in `app/api/matches.py`. `git blame` reports its line as
`Not Committed Yet`; it has no committed introducing SHA and therefore no
commit-level author/message rationale.

The change first appears in the Phase 3D implementation work:

- It changes the prior admin Event sync endpoint into a first-snapshot
  historical backfill handler.
- It introduces `Literal["HISTORICAL_BACKFILL"] = Query(...)`.
- The Phase 3D implementation report says the endpoint “requires the explicit
  query operation” and documents the query-string URL.
- The Phase 3C freeze requires a historical operation to use an explicit
  workflow, but does not specify a parameter name, a query parameter, or
  `HISTORICAL_BACKFILL` as an API contract.

No earlier committed implementation of this endpoint contains the
`operation` parameter.

## 4. Files / Functions Involved

| File | Function or section | Relevance |
|---|---|---|
| `app/api/matches.py` | `sync_match_events` | Declares the required `Literal` query parameter, performs historical first-snapshot guards, delegates sync, and echoes the label. |
| `app/main.py` | matches router mounting | Adds the `/api` prefix to the route. |
| `app/services/football.py` | `FootballAPIService.sync_match_events` | Compatibility facade; delegates to `EventService`, accepts no `operation`. |
| `app/services/event_service.py` | `EventService.sync_match_events` | Loads the Match and delegates to `EventSyncService`, accepts no `operation`. |
| `app/services/event_sync_service.py` | `EventSyncService.refresh_match_events` | Fetches/validates provider Events, resolves Team and Player identities, and delegates replacement; accepts no `operation`. |
| `app/providers/event_provider.py` | `EventProvider.get_match_events` | Provider transport by fixture ID; accepts no `operation`. |
| `app/repositories/event_repository.py` | `replace_match_events` | Replaces the snapshot; accepts no `operation`. |
| `app/services/scheduler.py` | `_refresh_events_job` | Calls the facade directly; does not call the HTTP route and supplies no operation. |
| `app/services/final_match_sync_service.py` | `sync_final_match` | Calls `EventService` directly during finalization; supplies no operation. |
| `tests/test_phase3d_event_sync.py` | backfill route tests | Calls the handler directly with the literal and tests its guards/label. |
| `docs/PHASE_3D_HISTORICAL_EVENT_SYNC_IMPLEMENTATION_REPORT.md` | §6 and summary | Documents the implementation's query parameter as an explicit backfill operation. |

## 5. Git History Evidence

- The route itself predates this parameter. `git blame` attributes its
  declaration and original handler to committed history, including commit
  `c184a6f9` (2026-05-26); a later handler body is present in commit
  `8c7b6b78` (2026-08-27).
- Reading the committed `8c7b6b7:app/api/matches.py` version shows the same
  route without an `operation` parameter. It allowed the admin request to
  call `football_service.sync_match_events`, then commit and invalidate
  cache, under the existing Event lock.
- Current `git blame` identifies the `operation` declaration and new
  historical guards as `Not Committed Yet`.
- The checked-in Git history has no commit containing the parameter. The
  Phase 3C/3D documents are untracked in this worktree; Git cannot provide
  committed-document history for their wording.

Therefore the exact introduction can be attributed to the current Phase 3D
implementation diff, but not to a committed change or commit message.

## 6. Design / Frozen Architecture Evidence

### Original Event Team ID freeze

`EVENT_TEAM_ID_CANONICAL_IDENTITY_DESIGN_FREEZE.md` specifies the canonical
meaning of Event `team_id`, the admin sync response's canonical identity
representation, and protection of historical Event rows during that phase.
It does not specify an `operation` query parameter or the literal
`HISTORICAL_BACKFILL`.

### Phase 3 implementation and audits

- The Phase 3 implementation report concerns Team Master resolution and
  canonical Event Team persistence; it identifies no `operation` parameter.
- Phase 3A discusses the controlled first snapshot for Match 476 and the
  unresolved historical-data policy.
- Phase 3B and 3B.1 explicitly leave historical Event policy outside their
  scope; 3B.1 addresses Standings only.

### Phase 3C freeze

Phase 3C differentiates live refresh, final Event sync, historical backfill,
and historical repair. It says historical work must be an explicit workflow
and that the normal scheduler must not be the historical mechanism. It
explicitly leaves backfill authorization/criteria unresolved. It does not
require a query parameter, name that parameter, define an operation enum, or
state that a pre-existing admin-only Event endpoint must reject calls without
such a parameter.

### Phase 3D implementation report

The Phase 3D report records the chosen implementation as requiring
`operation=HISTORICAL_BACKFILL`, but this is documentation of the
implementation decision itself, not evidence that the earlier architecture
required this exact API shape.

**Finding:** The exact requirement
`operation=HISTORICAL_BACKFILL` is **NOT ESTABLISHED BY SOURCE** as part of
the original Event Sync architecture or the frozen Phase 3C design. The
Phase 3C requirement for an explicit workflow is established; the exact
query-parameter representation is not.

## 7. Caller Analysis

| Caller | Classification | Call path | Supplies `operation`? |
|---|---|---|---|
| Active Event scheduler | A — Scheduler | `_refresh_events_job` → `football_service.sync_match_events` → EventService → EventSyncService → EventProvider / EventRepository | No. It does not call HTTP. |
| Terminal finalization | B — Terminal Finalization | `FinalMatchSyncService.sync_final_match` → Event lock → EventService → EventSyncService → EventProvider / EventRepository | No. It does not call HTTP. |
| Admin/manual route | C — Admin/manual API | HTTP route → `sync_match_events` handler → `football_service.sync_match_events` → EventService → EventSyncService → EventProvider / EventRepository | Yes. Required by FastAPI route validation and echoed in response. |
| Focused Phase 3D tests | D — Test | Direct handler call plus service/finalization fakes | Tests passing the parameter call the handler directly; this is not an internal service contract. |
| Other service/finalization tests | D — Test | Tests stub `football_service.sync_match_events` or EventService/finalizer dependencies | No operation argument because they do not invoke the HTTP handler. |
| External HTTP clients | F — Unknown | Not present in the repository search | No in-repository caller evidence. |

The operation parameter is confined to the API handler boundary. It is not
propagated into the service, sync service, provider, or repository.

## 8. Test Dependency Analysis

The repository search found `HISTORICAL_BACKFILL` in the new focused test
module `tests/test_phase3d_event_sync.py` and in Phase 3D documentation.
Tests that invoke `matches_api.sync_match_events` directly pass
`operation="HISTORICAL_BACKFILL"`; they also check the response label and
independently exercise terminal-status and pre-existing-Event guards.

Other Event identity, EventService freshness, scheduler-lock, and
finalization tests exercise service-level APIs without an `operation`
argument.

No earlier Event test or service contract requires this literal. It is
required by tests only to match the current handler signature, not by the
underlying Event sync architecture.

Classification among the supplied choices: the exact parameter is **required
only by the current implementation and tests coupled to that implementation**.
There is no explicit design freeze requiring that exact query parameter.

## 9. Why `HISTORICAL_BACKFILL` Exists

| Candidate rationale | Evidence | File / function / logic | Confidence |
|---|---|---|---|
| Distinguish the admin request as historical backfill | The Phase 3D implementation changes the handler docstring to first-snapshot backfill and returns `operation` in the result. | `app/api/matches.py`, `sync_match_events`; Phase 3D implementation report §6 | High |
| Require explicit opt-in instead of accepting the old generic admin call | A required query parameter causes missing/other values to fail request validation before the handler performs sync. | `app/api/matches.py`, `operation: Literal[...] = Query(...)` | High as actual effect; medium as intended rationale |
| Prevent replacing existing Event rows | The handler separately queries `EventRepository.get_by_match_id` and rejects non-empty results before sync. | `app/api/matches.py`, existing-Events guard | High |
| Prevent arbitrary nonterminal/future Match sync | The handler separately validates `FT`/`AET`/`PEN` and past timezone-aware `match_time`. | `app/api/matches.py`, status/time guards | High |
| Separate scheduler and finalization callers from historical operation | Scheduler and finalization call services directly, not the HTTP route, and have their own lifecycle gates. | `app/services/scheduler.py`; `app/services/final_match_sync_service.py` | High |
| Enforce Team identity or provider validation | The parameter is not read by EventSyncService and does not affect Team mapping/provider response checks. | `app/services/event_sync_service.py`, `refresh_match_events` | High |
| Fulfil Phase 3C's exact API requirement | Phase 3C requires an explicit workflow but does not specify this parameter or value. | Phase 3C report §15–§16 | High that exact requirement is absent |

The most supportable reading is that the parameter was introduced as an
explicit API-level marker/control when Phase 3D implemented the historical
first-snapshot workflow. The exact intent beyond that implementation choice
is not recorded in a commit message or earlier design freeze.

## 10. Whether It Is Architecturally Required

The existing architecture already separates:

- live refresh by the status-gated Event scheduler;
- terminal final Event sync by the finalization lifecycle;
- manual requests through an admin-only HTTP route;
- historical first-snapshot backfill through route-level Match status, past
  time, and zero-existing-Events checks.

All internal scheduler/finalization callers bypass the HTTP route and invoke
the shared service, so they cannot supply or depend on this query parameter.
The sync service already uses Match existence and canonical identity checks;
it does not use an operation mode.

The endpoint route itself is already a manual/admin boundary. Its
first-snapshot guards determine whether a backfill can proceed. The
`operation` parameter adds a required request marker and response label, but
does not change which service operation executes after validation.

Thus:

- Required by original architecture: **No evidence; not established.**
- Required by Phase 3C frozen policy: **No, not as this exact parameter.**
- Required by current route implementation/API contract: **Yes.**
- Required for the current first-snapshot and no-replacement guards to work:
  **No; those checks are separate from the parameter.**
- Whether external consumers treat it as a published API contract:
  **Unknown**; no in-repository client was found and current OpenAPI runtime
  output was not available to inspect in this source audit.

## 11. Scope-Contamination Analysis

The original Event Team ID problem concerned mapping provider
`event.team.id` through Team Master to canonical `Team.team_id` for
`match_events.team_id`, including the admin endpoint's returned Event shape.
The original freeze does not mention `operation=HISTORICAL_BACKFILL`.

The historical Event issue emerged in later audits around the controlled
past-fixture snapshot and was explicitly considered in Phase 3C. Phase 3D
then implemented a historical backfill flow and added the parameter in the
current uncommitted implementation diff.

**Answer:** The parameter did **not** originate from the original Event Team
ID canonical-identity requirement. Its introduction is later than that
scope, associated with the Historical Event Sync / Phase 3D backfill work.
This conclusion is supported by the earlier committed route lacking the
parameter, the Phase 2/3 identity freeze omitting it, and the current
Phase 3D diff adding it.

## 12. Impact If Removed

This is impact analysis only; no removal is proposed or implemented here.

### Directly affected

- The HTTP route's FastAPI request contract would no longer require the
  query value. Clients could call the endpoint without that value.
- Invalid/missing-operation 422 validation specifically attributable to this
  parameter would disappear.
- If the response currently echoes `"operation": "HISTORICAL_BACKFILL"`,
  removing the parameter also requires removing or otherwise resolving that
  response reference; otherwise the handler would refer to an unavailable
  local variable. The response shape would change if the label were omitted.
- Direct unit tests that call the handler with `operation=...` would need
  adjustment to match a changed handler signature; tests asserting the
  response label would also be affected.
- The Phase 3D implementation report's endpoint example and statements that
  the operation is required would become stale. The runtime verification
  report also describes the parameter as present.
- FastAPI's generated query schema, if enabled, would change. A runtime
  OpenAPI schema was not available during the prior runtime verification.

### Not affected by removing only this parameter

- The `current_active_admin` dependency.
- The Event resource lock.
- Match existence, terminal status, timezone-aware past-time, and
  no-existing-Events guards.
- The call to `football_service.sync_match_events`.
- Service, EventSyncService, provider, repository, Event Team identity,
  transaction, or cache behavior.
- Scheduler and terminal-finalization call paths; both bypass the HTTP route
  and never pass `operation`.
- The automatic historical-sync prevention provided by the scheduler's
  active/live discovery and status gate.

### Safety distinction

Removing the parameter would remove the requirement for an explicit
`HISTORICAL_BACKFILL` marker at the API boundary. It would **not**, by itself,
remove the current code's first-snapshot-only, past-time, terminal-status, or
admin-only checks. The query literal currently acts as request-level
acknowledgement/labeling, not as the implementation of those data guards.

## 13. Root-Cause Classification

**D. Historical backfill implementation requirement**

The exact parameter appears in the Phase 3D historical first-snapshot
backfill implementation, after the Historical Event Sync discussion. It
does not appear in the earlier Event Team ID architecture or committed
endpoint. Its exact query-parameter form is not mandated by Phase 3C.

## 14. Evidence Gaps

- The relevant Phase 3D source changes are uncommitted, so Git has no
  introducing commit message or author rationale for the parameter.
- The Phase 3C/3D documents are untracked in the current worktree; their
  historical revisions cannot be established from Git.
- No external client/source was found in this repository, so adoption of
  this query parameter by deployed clients is unknown.
- A live OpenAPI document was not available to determine the currently
  published schema outside static FastAPI route metadata.
- The source does not state whether `operation` was intended as an audit
  trail, user confirmation, API version discriminator, or only a literal
  label. Only its validation and response-label behavior are directly
  established.

## 15. Final Verdict

**PARTIAL**

The origin is clear: the parameter was added in the current Phase 3D
historical backfill implementation and was not part of the earlier
Event Team ID architecture or committed endpoint. The current route's
validation and effects are also clear. However, no source establishes that
this exact query parameter is architecturally necessary; Phase 3C requires
an explicit historical workflow, not this particular API representation.
External-client dependence and the original author's exact rationale are
unknown.

**Answer to the root-cause question:** The endpoint requires
`HISTORICAL_BACKFILL` because the Phase 3D implementation made the admin
endpoint's historical first-snapshot operation explicit through a required
query literal. This requirement was introduced later than the original Event
Team ID work. The repository establishes it as part of the current
implementation, but does **not** establish that the exact parameter was part
of intended Fover Event Sync architecture.
