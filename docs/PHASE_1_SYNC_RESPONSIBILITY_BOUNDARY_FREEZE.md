# PHASE 1 — SYNC RESPONSIBILITY & BOUNDARY FREEZE

## 1. Executive Summary

This phase freezes the intended service boundaries for the Fover sync system before any implementation work begins.

The target architecture is intentionally strict:

- Fixture Sync is a date/repair fixture responsibility only.
- Live Sync is an independent scheduler-driven lifecycle for active matches.
- Final Live Sync is a terminal-state finalization lifecycle that follows a terminal transition.
- Date Fixture Sync must not trigger Live Sync or Final Live Sync.
- Live Sync must detect terminal transitions and hand off to Final Live Sync.
- Shared infrastructure is allowed, but cross-lifecycle orchestration is not.

This document freezes the responsibility contract and documents the current gaps that will be implemented in a later phase without modifying the current code, database, scheduler, or lock logic during Phase 1.

### Phase 1 decision

Classification: BOUNDARY FROZEN WITH DOCUMENTED GAPS

This is a contract freeze, not an implementation change.

---

## 2. Phase 0 Findings Referenced

Phase 0 confirmed the following facts:

1. `POST /api/matches/sync/{date_val}` currently uses the Fixture Sync path.
2. Fixture Sync and Live Sync currently share the same transaction-scoped `fixture_query:global` advisory lock.
3. The shared `FixtureSyncService` processing path reaches terminal-transition handling.
4. The date-sync path can therefore indirectly invoke Final Live Sync.
5. The exact runtime owner of a historical 409 conflict was not proven.
6. Phase 0 was audit-only and no implementation changes were made.

The target architecture is therefore defined as follows:

```text
                         FOVER SYNC SYSTEM
                                │
              ┌─────────────────┴─────────────────┐
              │                                   │
       FIXTURE SYNC                         LIVE SYNC
              │                                   │
   POST /sync/{date}                    Scheduler: 60s
   Daily Fixture Job                    Provider Live Feed
   Repair Fixture Job                           │
              │                                 ▼
              ▼                           LIVE / HT / ET
       FixtureSyncService                       │
              │                                 │
              │                         Terminal detected
              │                                 │
              │                                 ▼
              │                         FINAL LIVE SYNC
              │                                 │
              │                         Fresh Provider Fetch
              │                                 │
              └──────────────┬──────────────────┘
                             ▼
                       Match / Fixture DB
```

---

## 3. Frozen Fixture Sync Responsibility

### Definition

Fixture Sync is defined as:

```text
FIXTURE SYNC
│
├── POST /api/matches/sync/{date_val}
├── Daily Fixture Job
└── Repair Fixture Job
```

### Responsibility

The responsibility of Fixture Sync is limited to:

```text
Provider Fixture Data
        ↓
FixtureSyncService
        ↓
Fixture / Match normalization
        ↓
League / Team / Venue identity resolution
        ↓
Match persistence
        ↓
Commit
```

### Allowed work

Fixture Sync may:

- fetch fixture data
- normalize fixture data
- resolve master identities
- create/update Match records
- update fixture metadata
- persist fixture/master-related fields

### Forbidden work

Fixture Sync must not:

```text
❌ invoke sync_live_matches()
❌ invoke Final Live Sync
❌ perform terminal finalization
❌ perform live-specific provider refresh
❌ own Event Sync
❌ own Lineup Sync
❌ own Odds Sync
❌ own Statistics Sync
```

### Freeze decision

Decision 1: Is `POST /api/matches/sync/{date_val}` Fixture Sync ONLY?

Target answer: YES

---

## 4. Frozen Live Sync Responsibility

### Definition

Live Sync is defined as an independent lifecycle triggered by the Scheduler.

Target:

```text
Scheduler
    ↓
sync_live_matches()
    ↓
API-Football Live Feed
    ↓
process active fixtures
    ↓
Match / Fixture state update
```

### Responsibility

Live Sync is responsible for:

```text
LIVE
HT
1H
2H
ET
P
```

and other currently active provider statuses that are part of the existing canonical status model in the repository.

### Current status model found in code

The repository already defines and uses these active/live status values:

- `1H`
- `2H`
- `HT`
- `ET`
- `LIVE`
- `BT`
- `P`

Additionally, terminal and finalized values are treated as follows:

- terminal finalization values: `FT`, `AET`, `PEN`
- finished/terminal values: `FT`, `AET`, `PEN`, `CANC`, `ABD`, `AWD`, `WO`

These values are used in the live-sync and finalization logic in the sync service and terminal-transition boundary.

### Freeze rule

Live Sync must be triggered independently by the Scheduler and must not depend on `POST /api/matches/sync/{date_val}` in normal operation.

### Freeze decisions

Decision 2: Can Date Fixture Sync invoke Live Sync?

Target answer: NO

Decision 4: Can Live Sync operate independently of Date Fixture Sync?

Target answer: YES

---

## 5. Frozen Terminal Transition Responsibility

### Definition

Terminal transition is the lifecycle boundary between Live Sync and Final Live Sync.

Target:

```text
LIVE
  ↓
FT / AET / PEN
  ↓
TERMINAL DETECTED
  ↓
FINAL LIVE SYNC
```

### Architectural rule

Terminal transition handling belongs to the Live lifecycle, not to the Date Fixture Sync lifecycle.

### Target ownership

```text
Live Sync
    ↓
detect terminal transition
    ↓
request Final Live Sync
```

### Freeze decision

Decision 5: Should terminal transition detection belong to the Live lifecycle?

Target answer: YES

### Rule

Date Fixture Sync must not perform this terminal transition handoff.

---

## 6. Frozen Final Live Sync Responsibility

### Definition

Final Live Sync is a separate finalization operation.

Target:

```text
Terminal Transition
        ↓
FINAL LIVE SYNC
        ↓
Fresh Provider Fetch
        ↓
Validate FT / AET / PEN
        ↓
Persist Final Fixture State
        ↓
Commit
        ↓
Post-commit Cache Invalidation
        ↓
FINALIZED
```

### Responsibility

Final Live Sync is responsible for:

- terminal-state confirmation
- fresh provider fetch
- final fixture state
- final score/state confirmation
- final persistence
- finalization retry/recovery

### Freeze rule

Final Live Sync must not be triggered by `POST /api/matches/sync/{date_val}` unless a separate future architecture explicitly changes that contract.

### Freeze decisions

Decision 3: Can Date Fixture Sync invoke Final Live Sync?

Target answer: NO

Decision 6: Should Final Live Sync perform a fresh provider fetch?

Target answer: YES

---

## 7. Shared Service Boundary

The project already uses shared low-level infrastructure:

```text
Provider
→ Service
→ SyncService
→ Repository
→ Database
```

This phase does not require separate classes for every responsibility. Shared infrastructure is allowed when it is safe.

### Allowed shared components

The following may remain shared if they are not used for cross-lifecycle orchestration:

- shared Match repository access
- shared normalization helpers
- shared identity resolution helpers
- shared provider wrapper logic
- shared persistence components

### Not allowed

The following orchestration patterns are not acceptable for the target architecture:

```text
Fixture Sync
    ↓
Terminal Handler
    ↓
Final Live Sync
```

This is exactly the boundary that must be separated.

### Freeze principle

Shared infrastructure is allowed. Cross-lifecycle orchestration is not.

---

## 8. Lock Boundary

### Target design

```text
Fixture Sync
└── fixture_query lock

Live Sync
└── live-sync lock

Final Live Sync
└── fixture:{match_id} lock
```

The exact lock names are not blindly frozen yet; the architectural rule is fixed:

```text
Fixture Sync ≠ Live Sync lock
Live Sync ≠ Final Live Sync lock
```

### Required lock separation

The purpose is to prevent an unrelated live operation from blocking a date fixture sync simply because both use the same global lock.

### Freeze decisions

Decision 7: Should Fixture Sync and Live Sync use separate operation-level locks?

Target answer: YES

Decision 8: Should same-match Final Live Sync requests serialize?

Target answer: YES

### Current observed pattern

The current code uses the same global identity for fixture query operations, including daily and live sync work. That is intentionally identified as a boundary gap that must be resolved by a future implementation phase.

---

## 9. Transaction Boundary

### Frozen rule

The project continues to use the existing ownership principle:

```text
API / Scheduler
        ↓
SyncService
        ↓
Repository
        ↓
Database
        ↓
API / Scheduler
        ↓
COMMIT / ROLLBACK
```

### Freeze rules

1. SyncService does not own the outer transaction.
2. Repository does not commit.
3. API / Scheduler owns commit and rollback.
4. Post-commit cache invalidation occurs only after successful commit.

### Current implementation check

The current code path in the API and scheduler layers does follow this pattern in general:

- the outer route or scheduler job owns the transaction
- `db.commit()` and `db.rollback()` are performed by the caller
- service functions do not commit directly

This rule is therefore consistent with the target architecture.

### Freeze decision

Decision 9: Should SyncService own commit/rollback?

Target answer: NO

---

## 10. Cache Boundary

### Target principle

```text
DB COMMIT
    ↓
Post-commit Cache Invalidation
```

### Frozen cache expectations

- Fixture Sync cache invalidation belongs to fixture-query operations.
- Live Sync cache invalidation belongs to live-match update operations.
- Final Live Sync cache invalidation belongs to final-state confirmation and terminal finalization.

### Freeze rule

The same cache key must not be used to represent two different lifecycle meanings without a clear invalidation contract.

### Current check

The API and scheduler paths currently invalidate `live_matches` after a successful commit. This is consistent with the target principle, but the full cache ownership model should still be clarified by lifecycle boundaries in the next implementation phase.

### Freeze decision

Decision 10: Should cache invalidation occur after commit?

Target answer: YES

---

## 11. 409 Conflict Boundary

### Frozen rule

The historical 409 conflict must not be classified without runtime evidence.

The exact historical lock owner was not proven in Phase 0, so the following must remain true:

> A Live Sync operation must not block Date Fixture Sync merely because both use a shared global lock.

### Required distinction

The boundary contract distinguishes between:

```text
Expected Fixture/Fixture conflict
```

vs.

```text
Unexpected Fixture/Live conflict
```

vs.

```text
Expected FinalLive/FinalLive same-match serialization
```

### Architectural rule

If two Fixture Sync requests are intentionally serialized at the same global fixture-query boundary, a 409 may remain appropriate.

If a Live Sync operation is blocked by a Fixture Sync operation because they share the same global identity, that is an architectural violation.

### Freeze principle

This phase does not declare the historical 409 as caused by live sync or by fixture sync. It freezes the architectural requirement that the two lifecycles must not share a global operation lock.

---

## 12. Target Call Graphs

### 12.1 Fixture Sync target call graph

```text
POST /api/matches/sync/{date}
        ↓
sync_daily_matches()
        ↓
FixtureSyncService
        ↓
Provider Fixture Query
        ↓
Fixture / Match Processing
        ↓
Repository
        ↓
DB
        ↓
Commit
        ↓
Cache Invalidation
        ↓
DONE
```

There must be no `Live Sync` or `Final Live Sync` below this graph.

### 12.2 Live Sync target call graph

```text
Scheduler
        ↓
sync_live_matches()
        ↓
Provider Live Feed
        ↓
Fixture / Match Processing
        ↓
Terminal Transition Detection
        ↓
if active:
    persist live state
        ↓
if terminal:
    request Final Live Sync
```

### 12.3 Final Live Sync target call graph

```text
Terminal Transition
        ↓
Final Live Sync
        ↓
Fresh Provider Fetch
        ↓
Terminal Validation
        ↓
Final Fixture / Match Persistence
        ↓
Commit
        ↓
Cache Invalidation
        ↓
FINALIZED
```

---

## 13. Responsibility Matrix

| Responsibility | Fixture Sync | Live Sync | Final Live Sync |
|---|---:|---:|---:|
| Date fixture fetch | YES | NO | NO |
| Daily fixture job | YES | NO | NO |
| Repair fixture job | YES | NO | NO |
| Live provider feed | NO | YES | NO |
| Active match updates | NO | YES | NO |
| Terminal transition detection | NO | YES | NO |
| Fresh final provider fetch | NO | NO | YES |
| FT / AET / PEN validation | NO | Detect | YES |
| Final match state | NO | NO | YES |
| Finalization retry | NO | NO | YES |
| Shared Match repository | MAY | MAY | MAY |
| Shared low-level infrastructure | MAY | MAY | MAY |

---

## 14. Current → Target Gap List

### Gap 1: Shared global fixture lock for date sync and live sync

- Current: both paths can share `fixture_query:global`
- Target: Fixture Sync lock must be separate from Live Sync lock
- Why it matters: unrelated live work can block fixture work unintentionally

### Gap 2: Date-sync terminal handoff through shared processing

- Current: `FixtureSyncService` can reach terminal-transition handling and finalization
- Target: terminal detection belongs to Live Sync, not Date Fixture Sync
- Why it matters: Date Fixture Sync has hidden cross-lifecycle responsibility

### Gap 3: Final Live Sync triggered via shared processing path

- Current: `handle_terminal_transition()` is reached through the same sync processing pipeline
- Target: terminal detection should orchestrate a distinct Final Live Sync lifecycle
- Why it matters: the lifecycle boundary is not explicit enough for clean separation

### Gap 4: unclear runtime ownership of historical 409

- Current: no exact owner was captured during the live conflict
- Target: lock conflict classification must be based on runtime evidence
- Why it matters: the root cause cannot be assigned without direct observation

### Gap 5: same lifecycle path used for fixture and live updates

- Current: same resource lock and same shared processing service
- Target: separate operation-level lock and independent orchestration boundaries
- Why it matters: operations should not be serialized simply because they share a global fixture key

---

## 15. Exact Next-Phase Implementation Plan

This section documents the required implementation changes for the next phase, without making them now.

### 15.1 `app/api/matches.py`

- File: `app/api/matches.py`
- Current behavior: the date-sync route wraps the function in `run_with_resource_lock(db, "fixture_query", "global", sync)` and raises a 409 on conflict.
- Target behavior: the route remains a Fixture Sync-only endpoint and does not trigger terminal handling or live finalization.
- Why required: this enforces the date fixture boundary.
- Dependency: existing route contract and admin auth checks.
- Risk: API behavior change may affect manual sync semantics if not carefully isolated.

### 15.2 `app/services/fixture_sync_service.py`

- File: `app/services/fixture_sync_service.py`
- Current behavior: `handle_terminal_transition()` is reachable from the shared sync pipeline and can call `final_live_sync()`.
- Target behavior: terminal transition handling should move out of the date fixture flow; date fixture processing should not be able to invoke final live sync.
- Why required: this is the primary cross-lifecycle orchestration violation.
- Dependency: shared persistence and match-processing logic.
- Risk: moderate; terminal statuses are business-critical and must be audited carefully.

### 15.3 `app/services/scheduler.py`

- File: `app/services/scheduler.py`
- Current behavior: live job uses the same `fixture_query:global` lock and same shared sync path.
- Target behavior: Live Sync must be assigned an independent scheduler lifecycle and a separate operation-level lock from Fixture Sync.
- Why required: Live Sync must run independently of Date Fixture Sync.
- Dependency: scheduler registration, job orchestration, and lock acquisition.
- Risk: moderate; must preserve job scheduling and active-status processing.

### 15.4 `app/services/resource_lock.py`

- File: `app/services/resource_lock.py`
- Current behavior: the canonical lock identity is generated from resource type and identity strings; the current global fixture lock is reused across live and daily code paths.
- Target behavior: separate lock identities for Fixture Sync, Live Sync, and Final Live Sync should be used according to the new boundary freeze.
- Why required: lock separation is central to the architecture contract.
- Dependency: backend PostgreSQL advisory locks.
- Risk: medium; lock naming and scope must be kept consistent across the app.

### 15.5 `app/services/football.py`

- File: `app/services/football.py`
- Current behavior: service entry points tunnel to the same fixture sync service and can expose the combined lifecycle.
- Target behavior: service methods should respect lifecycle boundaries and route to the correct sync responsibility.
- Why required: the orchestration contract should be explicit at the service layer.
- Dependency: sync service entry points and external provider access.
- Risk: low-to-medium; service layer refactor is broad but conceptually safe.

### 15.6 `app/services/cache_service.py` and cache invalidation sites

- File: `app/services/cache_service.py` and relevant call sites in the API/scheduler/service flow
- Current behavior: invalidation occurs after commit in some paths, while the lifecycle boundary is not separated by responsibility.
- Target behavior: each lifecycle invalidates only the cache keys relevant to that lifecycle and only after commit.
- Why required: cache boundaries must match lifecycle responsibilities.
- Dependency: live match cache keys and post-commit invalidation timing.
- Risk: low if the invalidation key set is preserved carefully.

### 15.7 `app/repositories/final_lineup_finalization_repository.py`

- File: `app/repositories/final_lineup_finalization_repository.py`
- Current behavior: finalization is reached from the terminal-handling flow.
- Target behavior: finalization remains part of Final Live Sync responsibility; not triggered from Fixture Sync.
- Why required: finalization logic must follow the live-to-final bridge.
- Dependency: finalization repository and surrounding status validation.
- Risk: medium; finalization workflows are sensitive and should be preserved.

---

## 16. Frozen Rules

The following rules are now frozen as the authoritative contract for the next implementation phase.

1. `POST /api/matches/sync/{date_val}` is Fixture Sync ONLY.
2. Date Fixture Sync cannot invoke Live Sync.
3. Date Fixture Sync cannot invoke Final Live Sync.
4. Live Sync operates independently of Date Fixture Sync.
5. Terminal transition detection belongs to the Live lifecycle.
6. Final Live Sync performs a fresh provider fetch.
7. Fixture Sync and Live Sync use separate operation-level locks.
8. Same-match Final Live Sync requests serialize.
9. SyncService does not own commit or rollback.
10. Cache invalidation occurs after commit.
11. A 409 may be expected for intentionally serialized Fixture Sync operations, but not for a Live Sync operation that is blocked only because it shares a global fixture lock.
12. Shared infrastructure is allowed; cross-lifecycle orchestration is not.

---

## 17. Phase 1 Completion Decision

### Final classification

BOUNDARY FROZEN WITH DOCUMENTED GAPS

### Reason

The required responsibilities and lifecycles are explicitly frozen in this document, but the current implementation still contains the following documented gaps:

- shared `fixture_query:global` lock between Fixture Sync and Live Sync
- terminal transition handling reachable from the Fixture Sync path
- cross-lifecycle orchestration exists in the shared service path
- historical 409 ownership remains unproven without runtime capture

This is the correct Phase 1 outcome: design freeze complete, implementation deferred.

---

## 18. Completion Criteria Check

PHASE 1 is complete only if all of the following are explicitly frozen:

- [x] Fixture Sync responsibility
- [x] Live Sync responsibility
- [x] Final Live Sync responsibility
- [x] Terminal transition ownership
- [x] Fixture → Live boundary
- [x] Fixture → Final Live boundary
- [x] Live → Final Live handoff
- [x] Lock separation
- [x] Transaction ownership
- [x] Cache invalidation boundary
- [x] 409 conflict boundary
- [x] Target call graphs
- [x] Implementation gap list

---

## Final Statement

This document establishes the authoritative responsibility and boundary contract that Phase 2 must implement. No source code, database state, scheduler configuration, migration, or runtime behavior has been modified during Phase 1.
