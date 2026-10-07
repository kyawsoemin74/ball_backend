# Phase 3C — Historical Event Sync Policy Design Freeze

**Project:** Fover Backend
**Mode:** Design audit and policy freeze only
**Verdict:** PARTIAL

## 1. Executive Summary

The repository distinguishes ordinary live Event refresh from terminal Event
refresh by Match status and caller. The Event sync service itself has no
scheduled-time or status eligibility check. Routine scheduler discovery is
limited to Redis-registered active Matches, and it ordinarily refreshes live
statuses; terminal statuses are handled as a special recovery case. An
admin-only POST endpoint also invokes the same snapshot-replacement operation
without a status or age guard.

The source does **not** establish whether a first Event snapshot for an old
fixture is permanently authorized, or whether an administrator may replace
already-existing historical Event rows through the generic sync endpoint. The
Phase 2 identity freeze excludes historical backfill and repair from that
implementation phase, but does not define a later standing policy. Match 476
was therefore operationally accepted as a first snapshot for a past fixture,
but the evidence does not prove that this operation was an approved historical
backfill policy.

Terminal Event synchronization is represented in code, but the normal daily
and season fixture-sync entry points explicitly disable the transition hook,
and the broad terminal helper has no production callsite. The scheduler has a
terminal recovery branch, but active Match registration removes terminal
Matches. This leaves the ordinary terminal-finalization wiring unclear.

No code, tests, database, Redis, provider state, or historical rows were
changed. Event Team ID canonical identity remains frozen and untouched.

## 2. Scope

Inspected the required Phase 2/3 design and audit reports, Event provider,
sync service, service, repository, Match/status handling, scheduler, active
Match registry, admin and read API routes, terminal fixture/finalization
paths, resource locking, Player identity resolution, and focused Event tests.
Compared the status-gating approach with adjacent Lineup and Statistics paths.

This report decides only what the available project evidence can support. It
does not implement or authorize an operational backfill, repair, or cleanup.

## 3. Evidence Reviewed

- `docs/EVENT_TEAM_ID_CANONICAL_IDENTITY_DESIGN_FREEZE.md`
- `docs/PHASE_3_EVENT_TEAM_ID_CANONICAL_IDENTITY_IMPLEMENTATION_REPORT.md`
- `docs/PHASE_3A_POST_IMPLEMENTATION_AUDIT_REPORT.md`
- `docs/PHASE_3B_TEST_FAILURE_ROOT_CAUSE_AUDIT_REPORT.md`
- `docs/PHASE_3B1_UNRESOLVED_TEST_CONTRACT_DECISION.md`
- `app/providers/event_provider.py`
- `app/services/event_sync_service.py`
- `app/services/event_service.py`
- `app/repositories/event_repository.py`
- `app/services/fixture_sync_service.py`
- `app/services/scheduler.py`
- `app/services/active_match_service.py`
- `app/services/resource_lock.py`
- `app/services/player_identity_resolution_service.py`
- `app/services/player_sync_service.py`
- `app/api/matches.py`
- `app/models/match.py` and `app/models/match_finalization.py`
- `tests/test_events_lock_alignment.py`
- `tests/test_event_service_freshness.py`
- `tests/test_event_team_identity.py`

No provider sync, database query/write, Redis query/write, migration, or test
execution was performed for this phase.

### Source Evidence

- `EventSyncService` validates the provider response and canonical Team
  identity, resolves Event player identities, and delegates snapshot
  replacement; it has no Match status or time eligibility guard.
- `EventService` delegates by the Match's provider fixture ID and leaves
  commit/cache invalidation to callers. Its cached read path does not refresh.
- The admin endpoint is admin-only, locked, and transactional, but has no
  status/time eligibility guard. The scheduler is active-registry-based and
  has distinct live and terminal-recovery status sets.
- Daily and season fixture sync disable terminal transition handling. The
  broad terminal Event helper has no production callsite.

### Test Evidence

- `test_events_lock_alignment.py` asserts scheduler lock-before-sync,
  commit-before-cache-invalidation, lock-conflict skip, and that cache failure
  does not roll back a committed sync.
- `test_event_service_freshness.py` asserts that EventService delegates without
  owning cache invalidation and that reads do not trigger refresh.
- `test_event_team_identity.py` asserts canonical Team ID resolution and
  replacement validation.
- The inspected tests do not assert policy for a Match past scheduled time,
  historical first-snapshot backfill, historical repair, or final Event sync
  eligibility. Tests therefore do not resolve those authorization questions.

### Architecture Evidence

- `EVENT_TEAM_ID_CANONICAL_IDENTITY_DESIGN_FREEZE.md` explicitly excludes
  historical Event rewrite/backfill/repair from that implementation and
  requires caller-owned rollback, locking, commit, and cache invalidation.
- The Phase 3 implementation report classifies Match 476's 18 Events as a
  new snapshot where there were previously no rows, not repair; it records
  four Player Master creations as well.
- The Phase 3A and 3B reports leave the general historical policy outside
  their decisions. Phase 3B.1 concerns Standings only and adds no Event
  historical policy.

## 4. Current Event Sync Lifecycle

1. `EventProvider.get_match_events` is transport-only. It requests
   `/fixtures/events` by provider fixture ID and makes no Match age/status
   decision (`app/providers/event_provider.py`).
2. `EventService.sync_match_events` loads the local Match and forwards its
   `provider_fixture_id` to `EventSyncService`. It does not commit, invalidate
   cache, or check status/time (`app/services/event_service.py`).
3. `EventSyncService.refresh_match_events` validates a non-empty provider
   response, loads Match-side canonical Team IDs, resolves each provider Team
   ID through Team Master, checks side membership, resolves Player identities,
   and calls the repository. It has no status or scheduled-time guard
   (`app/services/event_sync_service.py`).
4. `EventRepository.replace_match_events` validates all canonical Team IDs
   before deleting the previous snapshot, then inserts the replacement rows
   and leaves transaction completion to its caller
   (`app/repositories/event_repository.py`).
5. `EventService.get_cached_match_events` reads cache/database only. With no
   DB Events it returns an empty list; it does not trigger a provider refresh.
   The GET route returns 404 for an empty result (`app/services/event_service.py`,
   `app/api/matches.py`).

The sync operation is therefore a full-snapshot replacement primitive, not a
temporal policy boundary. Eligibility is currently a caller concern.

## 5. Scheduler Eligibility

The ordinary Event scheduler gets candidate IDs from the Redis active-Match
registry, reads current Match status, and applies an Event refresh interval.
Its explicit live set is `1H`, `HT`, `2H`, and `LIVE`. It skips the configured
blocked statuses except for a separate terminal-recovery set:
`FT`, `AET`, `PEN`, `PST`, `CANC`, `ABD`, `AWD`, and `WO`
(`app/services/scheduler.py`).

For those terminal-recovery statuses the interval is bypassed. This is a
recovery path, not a historical sweep: the scheduler starts from active
registry membership and does not enumerate old Matches. Fixture status
registration adds live statuses and removes terminal statuses
(`app/services/fixture_sync_service.py`,
`app/services/active_match_service.py`). A stale active key can nevertheless
make the terminal recovery branch reachable.

The scheduler takes the per-Match `events` resource lock before calling the
sync service, commits on success, rolls back on failure, and invalidates the
Event cache after commit. Its focused tests assert this order and behavior
(`tests/test_events_lock_alignment.py`).

The scheduler gate uses stored status, not elapsed scheduled time. It does not
select an arbitrary Match solely because its scheduled time is past.

## 6. Final Event Sync Behavior

`FixtureSyncService.handle_terminal_transition` contains a finalization
sequence for a nonterminal-to-`FT`/`AET`/`PEN` transition. It performs a final
fixture sync, then Event and Statistics sync under resource locks, and
registers lineup finalization work. A failure in Event sync fails that
transition operation (`app/services/fixture_sync_service.py`).

However, `sync_daily_fixtures` and `sync_full_season` set
`_allow_terminal_transition` to `False` while processing fixtures;
`final_live_sync` also passes `allow_terminal_transition=False`. The
`_finalize_terminal_match_events` helper supports a broader terminal set but
has no production callsite in the repository. Thus the code expresses a
terminal-final-sync design, but the inspected normal fixture paths do not
establish that the transition hook runs in routine operation. The scheduler's
separate terminal-recovery branch is conditional on the Match still being in
the active registry.

The final-match verification service is a separate Match finalization
mechanism; the inspected Event calls are in the fixture transition helper and
the Event scheduler recovery branch, not in the generic final-match
verification method.

## 7. Manual/Admin Sync Behavior

`POST /sync/{match_id}/events` is protected by `current_active_admin` and
documented as fetching Events for a finalized Match. It takes the Event
resource lock, calls the generic sync operation, commits or rolls back, and
attempts Event cache invalidation after commit (`app/api/matches.py`).

It does **not** load/check the Match status or scheduled time before syncing.
Consequently the route currently permits an explicit admin request for a
past, nonterminal, or terminal Match, provided the Match lookup and sync
otherwise succeed. The route's description and authorization establish an
admin-triggered sync capability, but do not define separate approval or
replacement rules for historical first snapshots versus historical repairs.

The API read route is not a manual refresh mechanism; it only reads cached or
persisted Events.

## 8. Historical Sync Definition

For this policy audit, **historical Event sync** means invoking the provider
snapshot operation for a Match whose scheduled match time has passed. A past
scheduled time does not prove that the Match actually completed: Match 476
had a past time while its stored status was `NS`.

The repository contains no universal age threshold, status reconciliation
rule, or documented permission rule that turns elapsed scheduled time alone
into approval or prohibition. The source establishes present technical
capability, not an unambiguous historical authorization contract.

## 9. Historical Backfill Definition

**Historical backfill** means creating the first persisted Event snapshot for
an old Match that has no Event rows. It is distinct from live refresh and from
the final snapshot taken at completion.

Match 476 had zero Event rows before the controlled sync and received its
first snapshot. That operation is a historical first-snapshot write by this
definition, regardless of whether the stored `NS` status was correct.

The Phase 2 freeze says historical Event rows are not rewritten, backfilled,
deleted, or otherwise repaired *as part of that change*. It does not specify
whether a future, separately approved first-snapshot backfill is allowed.

## 10. Historical Repair Definition

**Historical repair** means changing, replacing, reconciling, or deleting
already-persisted Event rows for an old Match. The generic sync operation
replaces the entire snapshot; it cannot distinguish a first insert from
replacement of existing historical rows.

The Phase 2 freeze requires historical identity repair to be separately
audited and separately approved. No dedicated historical repair workflow,
approval contract, or protective check was found. Therefore a generic admin
sync must not be treated as evidence that historical repair is approved.

## 11. Player Master Side-Effect Analysis

For Event player/assist references, `EventSyncService` calls the Player
identity resolver. When it returns `CREATE_NEW`, the service normalizes the
provider payload and calls `PlayerSyncService.upsert_player`. The controlled
Match 476 audit attributed four new Player Master rows to that sync, at the
same creation timestamp as the Event rows.

This is an expected possible consequence of the current Event identity
resolution path, not an isolated Event-table-only operation. The writes share
the caller's transaction boundary. Any future historical workflow must
disclose and account for both Event replacement/insertion and possible Player
Master creation. Whether that side effect requires additional business
approval is not specified by the inspected documentation.

## 12. Transaction / Lock / Cache Preservation

- `EventSyncService` and `EventService` do not commit or roll back.
- The admin endpoint and scheduler own transaction completion.
- Admin, scheduler, and terminal-transition call paths use the existing
  transaction-scoped `events` resource lock.
- Successful admin/scheduler paths invalidate Event cache after commit; the
  read path does not refresh provider data.
- The terminal-transition helper invokes Event sync under the Event lock
  inside its fixture transaction, but does not itself invalidate the Event
  cache. The helper is not enabled by the inspected routine fixture entry
  points. If terminal finalization is later wired, its caller must preserve
  the established caller-owned commit/rollback/lock/cache boundaries and
  explicitly verify cache invalidation after commit.
- The repository's full-snapshot replacement and validation-before-delete
  strategy must remain unchanged.

This audit identifies the terminal-path cache boundary as a follow-up
verification item; it does not change it.

## 13. Match 476 Case Analysis

The Phase 3A audit records Match 476 as having a past scheduled time, stored
status `NS`, and zero Event rows before the controlled sync. The sync used the
existing Event lock and generic Event sync service. Neither that service nor
the admin route has a time/status guard, so it was technically accepted; the
ordinary scheduler's live status gate would not accept `NS`.

The operation inserted 18 canonical-Team-ID Events and created four Player
Master rows. It was a first snapshot for an old fixture, not repair of
pre-existing Event rows. The record is an audit example, not evidence of a
general permission rule. Existing rows and Player rows must remain untouched
by this phase; no cleanup or rerun is authorized here.

## 14. Policy Decision Matrix

Labels describe the policy boundary that can be supported by current evidence.
Where authorization or wiring is not established, the cell says so explicitly;
current technical capability is not treated as approval.

| Operation | LIVE Match | Terminal Match | Old Historical Match |
|------------|------------|-----------------|----------------------|
| Scheduler Event Refresh | **ALLOWED** — active-registry candidate, live status, and refresh interval permit ordinary refresh. | **ALLOWED WITH EXPLICIT WORKFLOW** — only terminal-recovery statuses take the recovery branch; active registration normally removes terminal Matches, so this is not a general terminal sweep. | **NOT ALLOWED** — no historical Match enumeration exists in the scheduler. |
| Final Event Sync | **NOT ALLOWED** — final sync is tied to a terminal transition, not an ordinary live refresh. | **ALLOWED WITH EXPLICIT WORKFLOW** — terminal transition/recovery code exists, but routine fixture entry points disable the transition hook; reliable normal wiring is not established. | **ALLOWED WITH EXPLICIT WORKFLOW** — only for an identified pending finalization/recovery case; the repository does not define a rule to create a new finalization operation solely because time has passed. |
| Manual/Admin Event Sync | **ALLOWED WITH EXPLICIT WORKFLOW** — existing explicit admin POST, Event lock, commit/rollback; endpoint currently has no status gate. | **ALLOWED WITH EXPLICIT WORKFLOW** — endpoint is described for finalized Matches and requires admin authorization/lock. | **ALLOWED WITH EXPLICIT WORKFLOW** — the admin endpoint technically accepts it, but whether this constitutes approved historical backfill or repair is **UNKNOWN**. |
| Historical Backfill | **NOT ALLOWED** — the Match is not historical under the definition above. | **NOT ALLOWED** — a newly terminal Match belongs to final Event sync, not historical backfill. | **ALLOWED WITH EXPLICIT WORKFLOW** — an explicit workflow is the only defensible candidate boundary, but no approval criteria or intended permission are documented; final authorization remains **UNKNOWN**. |
| Historical Repair | **NOT ALLOWED** — not a historical repair case. | **NOT ALLOWED** — ordinary final snapshot replacement is not evidence for historical repair permission. | **ALLOWED WITH EXPLICIT WORKFLOW** — separate audit and approval are required by the identity freeze; no such dedicated workflow or its safeguards are specified, so operation is not currently authorized through generic sync. |

## 15. Canonical Historical Event Sync Policy

The repository evidence supports these boundaries, but not a complete
historical authorization policy:

- Ordinary Event refresh is status/active-registry based and is not a
  scheduled-time-based historical sweep.
- Terminal Event synchronization is a distinct finalization/recovery operation,
  not ordinary live refresh. Its normal transition wiring must be verified
  before it is relied upon.
- A first snapshot for a past fixture is historical backfill, even when the
  local status remains `NS`.
- Replacing existing historical Event rows is historical repair, not merely a
  routine sync by virtue of using the existing admin endpoint.
- `EventSyncService` remains a snapshot/identity service with no time/status
  policy; callers own eligibility, transaction, locking, and cache invalidation.
- Historical backfill authorization, age/status criteria, and repair approval
  workflow remain **UNKNOWN**. Do not infer those permissions from the route's
  current technical capability.

## 16. Allowed vs Prohibited Operations

**Supported by current evidence**

- Routine scheduler refresh of registered live Matches under its configured
  status/interval gate.
- Explicit terminal recovery for the listed terminal statuses when reached
  through the scheduler's active-registry recovery path.
- Admin-triggered sync capability under the existing admin authorization and
  Event lock; its accepted Match statuses are currently unrestricted.
- Creation of Player Master identities when the Event identity resolver
  selects `CREATE_NEW`, within the sync caller's transaction.

**Not supported as an implicit policy**

- A periodic sweep of all old Matches.
- Treating past scheduled time by itself as proof that a Match is terminal.
- Treating every call to the generic admin sync as authorized historical
  repair.
- Rewriting or cleaning up Match 476 or any other historical Event/Player rows
  as part of this design phase.

**Requires explicit future decision/workflow**

- Whether first-snapshot historical backfill is allowed, for which statuses
  and age range, and who approves it.
- Whether historical repair is ever allowed, and its audit, validation,
  rollback, and approval process.
- How a successfully committed terminal final sync invalidates Event cache.

## 17. Deferred Implementation Items

No implementation is authorized by this report. If separately approved:

1. Resolve the historical backfill authorization contract, including the
   treatment of past-time Matches still marked `NS`.
2. Define a distinct, auditable historical repair workflow; do not silently
   reuse unrestricted full-snapshot replacement for repair.
3. Verify/wire the intended terminal final Event sync path. The normal fixture
   sync entry points currently disable the transition hook, while the broad
   terminal helper has no production callsite.
4. Verify post-commit Event cache invalidation for any terminal finalization
   caller that is enabled.
5. Ensure operator-facing reporting accounts for possible Player Master
   creation during historical work.

## 18. Risks

- An admin can currently replace an old Match's entire Event snapshot without
  the route distinguishing first-snapshot backfill from repair.
- A past Match still marked `NS` can be manually synced, as Match 476
  demonstrates; status/time inconsistency is not resolved by Event sync.
- A terminal final-sync code path exists but appears disabled from normal
  fixture sync entry points; relying on it without clarifying its callers can
  leave a final snapshot missing.
- Terminal recovery relies on active-registry membership even though terminal
  registration removes the Match; its practical recovery reachability is
  therefore conditional.
- Historical Event work may create Player Master rows in addition to Event
  rows.
- Enabling/wiring final sync without preserving caller-owned transaction,
  lock, and cache boundaries could change frozen behavior.

## 19. Final Design Freeze

The source-backed freeze is limited to the following:

1. Event snapshot synchronization itself is policy-neutral about Match age and
   status. Eligibility belongs to callers.
2. Ordinary scheduled Event refresh is not a historical backfill mechanism.
3. Terminal Event synchronization is conceptually distinct and may be
   performed only in a terminal finalization/recovery workflow.
4. A first snapshot for a past fixture is historical backfill; replacing
   existing historical rows is historical repair.
5. Historical repair requires a separate audit and approval, consistent with
   the existing identity freeze; the generic sync endpoint is not sufficient
   evidence of approval.
6. Historical first-snapshot backfill permission and its eligibility criteria
   are not determined by current evidence.
7. Preserve the frozen Event Team identity flow:
   provider `event.team.id` → Team Master lookup → canonical `Team.team_id` →
   `match_events.team_id`.
8. Preserve full-snapshot replacement, per-Match Event lock, caller-owned
   transaction, and post-commit cache-invalidation ownership. Do not alter
   Event or Player data in this phase.

## 20. Verification Checklist

- [x] Required earlier phase documents reviewed.
- [x] Event provider → service → repository → DB/read API path traced.
- [x] Scheduler active Match discovery, status gate, terminal recovery, lock,
  transaction, and cache path inspected.
- [x] Terminal transition and finalization callsites checked; normal fixture
  entry points disable the transition hook, and broad helper has no callsite.
- [x] Admin Event sync authorization, lock, transaction, and lack of
  status/time guard confirmed.
- [x] Player Master creation path and Match 476 side effects documented.
- [x] Match 476 treated only as an audit example; no rows changed.
- [x] No code, tests, database, Redis, provider state, migration, Event Team ID
  implementation, or historical data changed.
- [ ] Historical backfill authorization and criteria established — **UNKNOWN**.
- [ ] Historical repair workflow and safeguards established — **UNKNOWN**.
- [ ] Normal terminal final Event sync wiring established — **UNKNOWN**.

## Final Verdict

**PARTIAL**

The live-refresh boundary, snapshot behavior, admin capability, Player Master
side effect, and historical repair distinction are established. A permanent
authorization contract for first-snapshot historical backfill is absent, and
the normal terminal-final-sync wiring is not established by current callers.
Those gaps prevent a fully evidence-backed historical policy freeze.
