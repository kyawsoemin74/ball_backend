# PHASE 0 - ODDS ARCHITECTURE & SCOPE FREEZE

## Status

**Status:** FROZEN
**Phase objective:** Audit-only architecture freeze for the Odds system and scheduler lifecycle.
**Implementation rule:** No production code, schema, migration, data insertion, update, deletion, or runtime change is allowed in this phase.
**Next gate:** Await explicit approval before any implementation work begins.

---

## 1. Purpose of this document

This document freezes the current architecture and scope of the Odds system as it exists in source code, configuration, and observed runtime evidence. It is intentionally a design and evidence freeze, not an implementation plan.

The purpose is to ensure all future changes begin from one shared baseline:

- what is implemented in code
- what is configured in deployment
- what is actually running in the active environment
- what remains not proven or not active
- what is deliberately out of scope for this phase

---

## 2. Evidence-driven current state

### 2.1 Runtime ownership model

The active deployment model is explicitly split between API and background worker.

Verified from source:

- [docker-compose.yml](../docker-compose.yml): API runs `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4` with `SCHEDULER_ENABLED=false`
- [docker-compose.yml](../docker-compose.yml): dedicated worker runs `python worker.py` with `SCHEDULER_ENABLED=true`
- [worker.py](../worker.py): worker starts metrics server, validates dependency health, starts the scheduler, then waits for shutdown

Verified runtime evidence:

- API health endpoint is healthy and ready
- Worker metrics endpoint is not reachable
- No active `worker.py` process was observed in the current runtime
- No scheduler-owned process was observed as the active owner of background jobs

Conclusion:

The app is currently API-running only. The scheduler owner is not active in the current runtime. Therefore, automatic Odds refresh is not proven to be running in this environment.

### 2.2 Scheduler architecture

The scheduler is implemented in [app/services/scheduler.py](../app/services/scheduler.py) and is owned by the dedicated worker entrypoint in [worker.py](../worker.py).

The scheduler registers these jobs:

- `sync_live_matches`
- `reconcile_recent_non_terminal`
- `sync_daily_fixtures`
- `repair_daily_matches`
- `refresh_standings`
- `refresh_odds`
- `refresh_lineups`
- `refresh_events`
- `refresh_statistics`

The relevant Odds job definition is:

- job id: `refresh_odds`
- function: `LiveUpdateScheduler._refresh_odds_job()`
- trigger: APScheduler interval-based schedule
- status gating: `NS`, `TBD`, `PST` are eligible; terminal/live states are excluded

This is source-proven and code-proven. It is not runtime-proven in the current active environment because the scheduler owner process is absent.

### 2.3 Odds system schema

The current odds persistence model is defined in [app/models/odds.py](../app/models/odds.py).

Key attributes:

- `fixture_id` references `matches.local_match_id`
- `bookmaker_name` nullable
- `market_name` non-null and indexed
- `selection` non-null and indexed
- `odd_value` non-null string
- `myanmar_odd` optional
- `last_updated` server timestamp

Business uniqueness is enforced by:

- `(fixture_id, bookmaker_name, market_name, selection)`

This means the canonical sync unit is a per-fixture snapshot replacement keyed by selection identity and bookmaker/market tuple.

### 2.4 Odds data state

The observed database state is:

- `odds` table row count: 0
- no odds snapshots were persisted in the current runtime
- no automatic synchronization execution was observed

This is consistent with the evidence that the worker and scheduler are not active.

### 2.5 Provider behavior

The provider layer is implemented and capable of producing real read-only responses. Verified requests against eligible fixture IDs returned HTTP 200 and valid bookmaker payloads.

Important distinction:

- provider reachability is proven
- automatic scheduler-to-provider refresh is not runtime-proven
- persisted odds rows remain zero

### 2.6 Matching eligibility logic

From the scheduler implementation:

- eligible match statuses: `NS`, `TBD`, `PST`
- ineligible statuses include `LIVE`, `HT`, `FT`, `AET`, `PEN`, `CANC`, `ABD`, `AWD`, `WO`
- refresh window remains bounded and pre-match oriented
- the scheduler is not a live odds subscriber

The logic is code-proven, but the execution path was not active in the current runtime.

---

## 3. Architecture freeze boundary

### 3.1 In scope for this phase

This phase is limited to:

- code audit of the Odds path
- deployment and runtime audit
- scheduler ownership verification
- evidence collection for the system boundary
- architecture freeze documentation
- explicit scope and implementation limits

### 3.2 Out of scope for this phase

This phase explicitly excludes:

- changing code in [app/services/scheduler.py](../app/services/scheduler.py)
- changing [worker.py](../worker.py)
- changing [app/services/odds_sync_service.py](../app/services/odds_sync_service.py)
- changing [app/repositories/odds_repository.py](../app/repositories/odds_repository.py)
- changing any model, migration, or repository layer
- any database insert/update/delete to production data
- any runtime process start/stop outside audited environment validation
- refactors, migrations, feature additions, or recovery tooling beyond audit documentation

### 3.3 No hidden implementation

No implementation work is considered part of this phase unless it is purely documentation and evidence-preservation. This includes:

- no SQL migration scripts
- no schema changes
- no app logic modifications
- no worker lifecycle reconfiguration
- no deployment edits

---

## 4. Frozen architecture baseline

### 4.1 Baseline flow

The frozen baseline architecture is:

```text
API runtime
  -> health/ready endpoint
  -> database + redis readiness
  -> no scheduler ownership

Dedicated worker runtime
  -> worker.py entrypoint
  -> dependency health checks
  -> scheduler_service.start_scheduler()
  -> AsyncIOScheduler
  -> jobs including refresh_odds
  -> background execution

Odds refresh job
  -> matches in eligible statuses/time windows
  -> resource lock acquisition
  -> provider odds fetch
  -> validation and normalization
  -> repository replace/snapshot persistence
  -> commit verification
  -> cache invalidation
```

### 4.2 Key ownership fact

The scheduler is not owned by the API process. The scheduler is owned by the dedicated worker process. The current runtime does not have a live worker process, so the scheduler is not active.

### 4.3 Key evidence fact

The Odds pipeline is source-implemented and test-covered, but not active in the current observed runtime. In practical terms:

- configured correctly in source
- deployed as a dedicated worker pattern
- not executed in the current environment
- database contains zero odds rows

---

## 5. Frozen decisions

### Decision 1: Worker lifecycle is a prerequisite

The background worker is not optional for the Odds auto-sync architecture. The app relies on the dedicated worker to own scheduler execution.

### Decision 2: API-only readiness is not equal to scheduler readiness

A healthy API and ready database/redis does not prove the Odds auto-sync runtime is functioning.

### Decision 3: Implementation must wait for active runtime proof

Any actual Odds auto-sync implementation or production recovery must begin only after the worker runtime has been activated and proved in the deployed environment.

### Decision 4: This phase ends at architecture freeze

The repository is frozen here until explicit approval is granted to proceed to the next phase.

---

## 6. Current verified blockers

The following blockers are explicitly recognized as part of the frozen baseline:

1. No live worker process was observed in the current environment.
2. No scheduler-owned runtime was available for the Odds refresh job.
3. The worker metrics endpoint was unavailable.
4. The `odds` table contains zero rows despite the presence of eligible matches and provider connectivity.
5. There is no runtime proof that the auto-sync loop executed successfully.

These blockers remain architectural and operational facts. They do not by themselves justify production code changes during this phase.

---

## 7. Scope statement for the next implementation phase

The next allowed phase, if specifically approved, will include:

- worker runtime validation in the real deployment environment
- deployment activation or verification of the dedicated worker
- scheduler runtime confidence checks
- only then, Odds sync implementation or recovery work

No work beyond that boundary is in scope without explicit approval.

---

## 8. Final freeze statement

This repository state is hereby frozen for the Odds architecture and worker lifecycle boundary.

The system is recognized as:

- source-configured for dedicated worker ownership
- runtime-active only at the API layer
- not proven to be running the scheduler and odds auto-sync loop
- not safe to modify until the worker runtime and operational ownership are explicitly validated and approved

This document is the official Phase 0 freeze record.
