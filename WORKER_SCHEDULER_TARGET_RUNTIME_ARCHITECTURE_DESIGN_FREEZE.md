# FOVER Worker / Scheduler Target Runtime Architecture Design Freeze

Document status: Phase 2 design-only freeze
Date: 2026-09-19
Scope: Target runtime architecture, ownership, deployment, recovery, observability, security, and Phase 3 requirements.

No code, configuration, database schema, database row, service process, scheduler, provider, or deployment was changed or started for this phase.

## 1. Objective

This document freezes the target runtime architecture for the Fover Backend after the Phase 1 audit.

The target separates API responsibilities from scheduled background work:

- API runtime is owned by `app.main:app` under Uvicorn on port 8000.
- Worker runtime is owned by `worker.py` as a separate process.
- Scheduler lifecycle and background job registration are owned by the Worker.
- Live Sync remains in the existing scheduler -> service -> repository -> PostgreSQL path.
- Docker Compose is the unified deployment orchestration layer for API, Worker, PostgreSQL, and Redis.
- Worker failure is isolated from API failure.
- No domain or Live Sync business logic is redesigned in Phase 2.

## 2. Current Phase 1 Findings

Phase 1 established the following evidence:

| Component | Phase 1 result |
|---|---|
| API `app.main:app` | Running on port 8000 |
| PostgreSQL | Reachable and ready |
| Redis | Reachable and ready |
| `worker.py` process | Not running |
| Worker metrics port 8001 | Unavailable |
| Scheduler | Not running in the observed runtime |
| Scheduler gauge | `fover_scheduler_up 0.0` |
| Current scheduler job execution | Not evidenced |
| Docker runtime in audit environment | Docker command unavailable |
| Target Match 1570397 | DB/API read-back: provider fixture 1570397, current status FT |
| Target Match 1589091 | DB/API read-back: provider fixture 1557408, current status FT |

The failure was runtime activation of the dedicated Worker, not a proven defect in the source ownership model or Live Sync business path.

The existing source path is:

```text
worker.py
  -> scheduler_service.start_scheduler()
  -> LiveUpdateScheduler.start()
  -> sync_live_matches job
  -> football_service.sync_live_matches()
  -> FixtureSyncService.sync_live_matches()
  -> FixtureProvider
  -> MatchRepository / Match upsert
  -> PostgreSQL
```

Phase 1 also verified that `app.main:app` does not import `worker.py`, instantiate the scheduler, or call `start_scheduler()`.

## 3. Target Architecture

```text
                         FOVER BACKEND
                              |
                    Docker Compose / Runtime
                              |
             +----------------+----------------+
             |                                 |
             v                                 v
        API SERVICE                       WORKER SERVICE
             |                                 |
             |                                 v
             v                            worker.py
     app.main:app                         Scheduler
             |                                 |
     Uvicorn :8000                 +-----------+-----------+
                                   v           v           v
                              Live Sync    Event Sync   Other Jobs
                                   |
                                   v
                         PostgreSQL / Redis
```

The target consists of two application services and shared infrastructure:

```text
api       -> uvicorn app.main:app --host 0.0.0.0 --port 8000
worker    -> python worker.py -> Scheduler -> background jobs
postgres  -> shared database
redis     -> shared cache/coordination infrastructure
```

The API and Worker are separate runtime processes, even though Docker Compose manages them as one deployment unit.

## 4. API Ownership

The frozen API startup contract is:

```text
API
  -> app.main:app
  -> Uvicorn
  -> :8000
```

The API owns:

- HTTP API routes.
- WebSocket handling.
- API lifecycle startup and shutdown.
- API-side notification and websocket application responsibilities already present in the codebase.
- API liveness and readiness endpoints.

The API does not own:

- Scheduler startup.
- Scheduler lifecycle.
- Background job registration.
- Live Sync scheduling.
- Worker process supervision.

The API must not import Worker startup code or embed APScheduler. Multiple Uvicorn workers must not create multiple scheduler owners.

## 5. Worker Ownership

The frozen Worker startup contract is:

```text
Worker service
  -> python worker.py
  -> worker metrics server :8001
  -> dependency health check
  -> start_scheduler()
  -> Scheduler lifecycle
  -> background jobs
```

The Worker owns:

- Scheduler startup and shutdown.
- Scheduler job registration.
- `sync_live_matches` scheduling.
- Event, statistics, standings, odds, lineup, fixture, reconciliation, and other scheduled jobs already defined by the codebase.
- Worker-specific runtime metrics and lifecycle logs.

The Worker does not serve the public API. It must not replace or embed the FastAPI application.

The Worker is independent of API startup. It requires shared PostgreSQL and Redis configuration, but it does not require an API process to be alive before its scheduler can start.

## 6. Scheduler Ownership

Only `worker.py` may start the Scheduler.

Frozen startup flow:

```text
worker.py
  -> start_scheduler()
  -> global LiveUpdateScheduler initialized
  -> jobs registered
  -> scheduler.start()
  -> scheduler running state exposed
```

The scheduler is trigger-only. It does not own domain persistence or reimplement synchronization logic.

The existing job contract is preserved unless a later phase explicitly authorizes a separate change:

| Job | Current trigger | Concurrency contract |
|---|---|---|
| `sync_live_matches` | Every 60 seconds | `max_instances=1` plus existing locks |
| `reconcile_recent_non_terminal` | Every 5 minutes | `max_instances=1` plus existing locks |
| `sync_daily_fixtures` | 00:01 Myanmar time | `max_instances=1` plus existing locks |
| `repair_daily_matches` | 02:00 Myanmar time | `max_instances=1` plus existing locks |
| `refresh_standings` | Every 6 hours | `max_instances=1` plus existing locks |
| `refresh_odds` | Every 6 hours | `max_instances=1` plus existing locks |
| `refresh_lineups` | Every 15 minutes | `max_instances=1` plus existing locks |
| `refresh_events` | Every 600 seconds | `max_instances=1` plus existing locks |
| `refresh_statistics` | Every 600 seconds | `max_instances=1` plus existing locks |

The scheduler must not automatically start merely because an API process is running.

## 7. Live Sync Ownership

The frozen Live Sync runtime path is:

```text
Worker
  -> Scheduler
  -> sync_live_matches
  -> Provider
  -> FixtureSyncService
  -> Repository
  -> PostgreSQL
```

Ownership is fixed as follows:

| Layer | Frozen responsibility |
|---|---|
| Worker | Owns the process that contains the Scheduler |
| Scheduler | Triggers the job and owns the outer job transaction boundary |
| `sync_live_matches` job | Applies existing locks, gate checks, invokes the service, and handles existing completion/error behavior |
| Provider | Retrieves provider fixture data |
| `FixtureSyncService` | Owns synchronization orchestration, normalization, identity flow, and service-level result handling |
| Repository | Resolves existing identities and performs persistence access/upsert operations |
| PostgreSQL | Stores committed Match and related data |
| Redis | Provides existing cache and coordination functionality |

The Scheduler must not write Match records directly. The API must not become an alternate Live Sync scheduler.

The existing Live Sync business logic, provider behavior, identity resolution, lock behavior, transaction ordering, cache invalidation, and repository contracts are frozen and out of scope for Phase 2.

## 8. Process Separation

Two application processes are mandatory:

```text
Process 1: API
  command: uvicorn app.main:app --host 0.0.0.0 --port 8000
  role: public API and WebSocket runtime
  port: 8000

Process 2: Worker
  command: python worker.py
  role: Scheduler and background jobs
  metrics: internal port 8001
```

The following are prohibited by this design:

- Starting the Scheduler from `app.main`.
- Importing `worker.py` from the API process.
- Running `worker.py` as a Uvicorn worker.
- Running API routes from the Worker process.
- Running more than one uncoordinated Scheduler owner.

## 9. Docker Compose Architecture

Docker Compose is the frozen application deployment orchestration layer.

Required application services:

```text
api
  -> app.main:app under Uvicorn
  -> port 8000

worker
  -> python worker.py
  -> internal metrics port 8001
```

Required shared infrastructure:

```text
postgres
redis
```

The target deployment unit must allow one command to start the complete backend:

```bash
docker compose up -d
```

The Compose service relationships are frozen as follows:

- API depends on healthy PostgreSQL and Redis.
- Worker depends on healthy PostgreSQL and Redis.
- API and Worker use the same Compose network and service-name-based dependency URLs.
- API publishes port 8000 for application traffic.
- Worker does not publish port 8001 publicly; its metrics endpoint is internal/admin-only.
- API and Worker have independent restart policies.
- The Worker has exactly one active scheduler replica in the default deployment.
- PostgreSQL and Redis remain infrastructure services and are not application-process responsibilities.

The current repository already contains separate `api` and `worker` Compose services. Phase 3 must implement or validate the frozen contract without changing domain code.

## 10. Restart / Recovery Design

### Worker crash

```text
Worker crash
  -> Compose detects process exit
  -> Worker container restarts according to restart policy
  -> python worker.py
  -> dependency check
  -> scheduler initialization
  -> jobs re-register
  -> scheduler resumes future executions
```

A Worker crash must not restart or terminate the API container. The API remains independently supervised.

### API crash

```text
API crash
  -> Compose restarts API according to API restart policy
  -> app.main:app starts
  -> API health checks recover
```

An API crash must not be required to restart the Worker. The Worker remains independently supervised.

### Scheduler restart semantics

The scheduler uses in-memory runtime state. On Worker restart, jobs are registered again from source and the interval schedule begins from the new Worker lifecycle. Persistent scheduler job state is not part of this freeze.

### Unhealthy process behavior

Process-exit recovery and health detection are separate concerns. Phase 3 must verify that a failed Worker process is restarted and that an unhealthy Worker is observable. Any policy that converts health failure into a controlled Worker restart must not invoke Live Sync business logic directly or create a second scheduler owner.

## 11. Server Reboot Design

The frozen Linux reboot behavior is:

```text
Linux server reboot
  -> Docker engine starts on boot
  -> existing Compose containers are eligible for restart
  -> API container starts
  -> Worker container starts
  -> Worker runs python worker.py
  -> Scheduler registers jobs
  -> background scheduling becomes available
```

To make this behavior reliable, the deployment environment must satisfy all of the following:

- Docker Engine is enabled to start on boot.
- The Compose project and its containers have been created before reboot.
- API and Worker use restart policies that survive normal Docker daemon recovery.
- PostgreSQL and Redis become healthy before API and Worker are considered ready.
- Worker readiness requires its metrics endpoint and scheduler state to be observable, not merely a container PID.
- A later production verification must confirm the behavior on Linux; the Windows audit environment cannot prove it.

The frozen target does not require a manual Worker command after an ordinary server reboot.

## 12. Health / Observability Design

Worker health must be established from multiple signals. A process existing is insufficient.

Required Worker observability:

```text
Worker process
  + Worker metrics endpoint reachable
  + PostgreSQL dependency state
  + Redis dependency state
  + Scheduler running state
  + Expected job registration
  + Job execution counters
  + Job error counters
  + Last successful execution evidence
```

The existing Worker metrics endpoint is `:8001/metrics`. The existing scheduler metrics include:

- `fover_scheduler_up`.
- `fover_scheduler_job_runs_total{job=...}`.
- `fover_scheduler_job_errors_total{job=...}`.
- Sync and provider counters already defined by the application.

The existing scheduler lifecycle logs include job started, completed, missed, and failed events. Phase 3 must make these signals available from the Worker runtime and must not rely on API-process metrics to represent Worker state.

For `sync_live_matches`, the minimum operational evidence is:

```text
Scheduler up
  -> job registered
  -> job started
  -> provider/service path reached
  -> job completed or failed
  -> last successful execution recorded
```

The current source has cumulative run/error counters and lifecycle logs. If an explicit last-success timestamp is required by the deployment health contract, Phase 3 may add observability-only instrumentation or derive it from a controlled metrics/logging mechanism. It must not alter Live Sync business behavior or transaction ownership.

API health remains separate:

- `/health` and `/health/live` represent API process health.
- `/health/ready` represents API-side PostgreSQL and Redis readiness.
- API readiness does not prove Worker or Scheduler readiness.

## 13. Security Design

The Worker is not a public HTTP API:

```text
Worker != Public API
```

Security requirements:

- Do not publish Worker port 8001 to the public internet.
- Keep Worker metrics and health on the internal Compose network or protected administrative access path.
- Expose only API port 8000 through the public ingress/reverse proxy.
- Use the existing secret injection mechanism for database, Redis, provider, and application secrets.
- Do not place secret values in this design document, logs, reports, or public metrics.
- Run API and Worker with the existing non-root container policy where supported.
- Keep PostgreSQL and Redis private to the deployment network.
- Ensure Prometheus/Grafana access is restricted according to the operational environment; dashboards must not disclose credentials.
- Do not add public Worker endpoints as a substitute for internal observability.

The Worker may expose metrics internally for Prometheus and administrative health checks, but this endpoint is not an API client surface.

## 14. Frozen Business Logic

Phase 2 freezes runtime architecture only. The following domain behavior is explicitly protected:

```text
League Identity
Country Master
League Master
League Season
Team Master
Player Master
Fixture Sync
Standing Sync
Event Sync
Lineup Sync
Odds Sync
H2H
Match Identity
```

The following are also frozen for this migration:

- Provider selection and provider identity mapping.
- Fixture normalization and Match identity resolution.
- Live status and score handling.
- Repository persistence semantics.
- Scheduler job business logic.
- Transaction ownership and commit/rollback behavior.
- Cache invalidation semantics.
- Lock and concurrency behavior.
- Database schema and migrations.

Any required business change must be proposed and approved as a separate task.

## 15. Phase 3 Implementation Requirements

Phase 3 may implement only the frozen runtime/deployment contract:

1. Keep API and Worker as separate services.
2. Use `uvicorn app.main:app --host 0.0.0.0 --port 8000` for the API service contract.
3. Use `python worker.py` for the Worker service contract.
4. Ensure one `worker` service is active for the default Compose deployment.
5. Ensure PostgreSQL and Redis are included or correctly referenced as shared infrastructure.
6. Preserve dependency health ordering for API and Worker.
7. Preserve restart policies for API, Worker, PostgreSQL, and Redis according to the approved deployment environment.
8. Keep Worker metrics internal and observable.
9. Align Prometheus scrape configuration with the actual API/Worker metrics exposure.
10. Verify exact environment variable names before wiring service configuration.
11. Do not guess secret values or copy them into source/configuration.
12. Do not modify `app/main.py`, `worker.py`, scheduler code, `sync_live_matches`, `FixtureSyncService`, repositories, schema, or domain logic for unrelated reasons.
13. Do not add a second scheduler startup path.
14. Do not trigger manual synchronization as part of deployment implementation.

Verified configuration names for Phase 3 wiring are:

```text
DATABASE_URL
REDIS_URL
FOOTBALL_API_KEY
SCHEDULER_ENABLED
APP_ENV
JWT_SECRET_KEY
API_KEY
GOOGLE_CLIENT_ID
```

`DATABASE_URL`, `REDIS_URL`, and `FOOTBALL_API_KEY` are required by the application/runtime contract. `JWT_SECRET_KEY`, `GOOGLE_CLIENT_ID`, and any required application authentication settings remain secret-managed API configuration. `SCHEDULER_ENABLED` is retained as an explicit service configuration value, but process command ownership remains authoritative: API is not a scheduler owner and Worker is the scheduler owner.

The actual secret values must be supplied by the deployment environment and must not be recorded in this design document.

## 16. Architecture Decisions

The following decisions are frozen:

### Decision A: Separate services

API and Worker are separate runtime services.

### Decision B: API ownership

`app.main:app` owns API runtime only.

### Decision C: Worker ownership

`worker.py` owns Scheduler runtime and scheduled background jobs.

### Decision D: Scheduler ownership

Only the Worker starts the Scheduler. The API does not automatically start it.

### Decision E: Live Sync ownership

Scheduler is trigger-only; `FixtureSyncService` owns synchronization orchestration; repositories own persistence access; PostgreSQL stores committed data.

### Decision F: Deployment orchestration

Docker Compose is the default unified deployment orchestration layer.

### Decision G: One deployment unit

API, Worker, PostgreSQL, and Redis can be started through one Compose deployment command.

### Decision H: Failure isolation

Worker failure must not require API restart. API failure must not require Worker restart.

### Decision I: Recovery

Container/process restart policy automatically recovers the Worker after crash and re-registers jobs through `worker.py`.

### Decision J: Server reboot

Docker startup plus service restart policy must restore API and Worker without a manual Worker command.

### Decision K: Observability

Worker health requires process, metrics endpoint, scheduler state, job registration, execution evidence, and last-success evidence.

### Decision L: Security

Worker metrics are internal/admin-only; Worker is not a public API.

### Decision M: Business protection

Live Sync and all listed domain logic remain unchanged.

### Decision N: Evidence standard

Source configuration proves capability. Runtime execution claims require actual Worker metrics, logs, process, and database/API evidence.

## 17. Risks

- Multiple Worker replicas could create duplicate scheduler execution. Default deployment must remain one Worker replica unless leader election is separately designed.
- A healthy API can conceal a failed Worker. Monitoring must scrape and alert on Worker metrics independently.
- `fover_scheduler_up` from an API process is not proof of the separate Worker scheduler state.
- A source-registered job is not proof that the job executed.
- In-memory scheduler state resets on Worker restart; next-run timing must be understood during verification.
- Docker healthchecks may detect an unhealthy container without independently proving that the deployment manager will restart it. Phase 3/6 must verify the selected recovery mechanism.
- Docker Engine not starting on Linux reboot would prevent the Compose deployment from recovering automatically.
- Public exposure of port 8001 would expand the attack surface and violate the Worker-not-public contract.
- Misconfigured or missing environment variables can prevent Worker startup even when API configuration is valid.
- Changing scheduler intervals, locks, transaction ownership, or service behavior during runtime migration could create business regressions and is prohibited.
- Target Matches 1570397 and 1589091 must not be used for manual sync during Phase 2.

## 18. Non-Goals

Phase 2 does not:

- Modify application code.
- Modify `app/main.py` or `worker.py`.
- Modify scheduler registration or job logic.
- Modify Live Sync or FixtureSyncService business logic.
- Modify repositories or Match identity behavior.
- Modify PostgreSQL schema or migrations.
- Modify Docker Compose, Dockerfile, `.env`, Kubernetes, or production deployment files.
- Start or restart API, Worker, Scheduler, Docker, or Kubernetes services.
- Trigger provider requests or manual Live Sync.
- Write to PostgreSQL or Redis.
- Repair target Match rows.
- Verify actual scheduled execution.
- Claim that the current Windows runtime is already compliant with the target.
- Redesign league, country, team, player, fixture, standing, event, lineup, odds, H2H, or Match business logic.

## 19. Verification Strategy

Phase 2 performs no runtime verification. The following later-phase sequence is frozen:

```text
PHASE 3
Docker Compose Runtime Implementation
        |
PHASE 4
Local Runtime Verification
        |
PHASE 5
Scheduler / Live Sync Verification
        |
PHASE 6
Restart / Recovery Verification
        |
PHASE 7
Linux Production Verification
        |
PHASE 8
Final Runtime Freeze
```

### Phase 3: implementation checks

- Build the image without changing domain code.
- Confirm Compose service commands and dependencies.
- Confirm API uses `app.main:app`.
- Confirm Worker uses `python worker.py`.
- Confirm only the Worker owns the Scheduler.
- Confirm exact environment names and secret injection.
- Confirm Worker port 8001 is not publicly published.

### Phase 4: local runtime checks

- Run the approved Compose command only after Phase 3 implementation.
- Confirm API, Worker, PostgreSQL, and Redis containers exist and are healthy.
- Confirm API port 8000 responds.
- Confirm Worker port 8001 responds only through the intended internal/admin path.
- Confirm `fover_scheduler_up=1` from Worker metrics.
- Confirm expected job IDs are registered.
- Confirm no second Worker/Scheduler owner exists.

### Phase 5: Scheduler / Live Sync checks

Use only the approved runtime verification procedure. Do not manually invoke the job.

Required evidence chain:

```text
Worker process
  -> Scheduler running
  -> sync_live_matches registered
  -> sync_live_matches started
  -> Provider request from scheduled execution
  -> FixtureSyncService path reached
  -> Match identity resolved
  -> repository persistence result
  -> PostgreSQL/API read-back
  -> scheduler completion or failure evidence
```

The designated target path is:

```text
Worker
  -> Scheduler
  -> sync_live_matches
  -> Provider fixture
  -> Match 1570397 / 1589091
  -> PostgreSQL
```

Do not manually sync either target. If a target is not naturally eligible or no provider/local correlation exists, record `UNKNOWN` rather than fabricating a transition.

### Phase 6: restart/recovery checks

- Observe Worker crash/restart isolation from API.
- Confirm jobs re-register after Worker restart.
- Confirm API remains available during Worker recovery.
- Confirm no duplicate scheduler owner appears.
- Confirm execution evidence resumes after recovery.

### Phase 7: Linux production checks

- Confirm Docker Engine starts at boot.
- Confirm Compose services recover after server reboot.
- Confirm API and Worker health independently.
- Confirm Worker remains one scheduler owner.
- Confirm internal metrics access and alerting.

### Phase 8: final freeze

- Record final process, container, scheduler, job, metrics, logs, provider, database, and API evidence.
- Confirm no business logic or schema changes were introduced.
- Freeze the verified runtime architecture and operational runbook.

## 20. Final Design Freeze

### Freeze criteria

| Criterion | Decision |
|---|---|
| API ownership | DEFINED |
| Worker ownership | DEFINED |
| Scheduler ownership | DEFINED |
| Live Sync ownership | DEFINED |
| Process separation | DEFINED |
| Docker Compose model | DEFINED |
| Restart/recovery | DEFINED |
| Server reboot behavior | DEFINED |
| Health/observability | DEFINED |
| Security | DEFINED |
| Business logic protection | DEFINED |
| Phase 3 requirements | DEFINED |
| Environment variable names | DEFINED; secret values remain deployment-managed |
| Current runtime compliance | NOT CLAIMED; Phase 1 found Worker absent |

All design criteria are defined. The current runtime failure does not invalidate the target design; it identifies the implementation and verification work required in later phases.

## PHASE 2 RESULT

Target Runtime Architecture:

**FROZEN**

Decision:

**PROCEED TO PHASE 3**

Reason:

The Phase 1 evidence establishes clear API, Worker, Scheduler, Live Sync, process, deployment, recovery, observability, security, and business-logic boundaries. Existing configuration provides the required service commands and environment variable names. The current Worker-not-running state is recorded as a baseline runtime gap and is intentionally deferred to Phase 3 implementation and Phases 4-8 verification. No implementation, restart, manual sync, deployment, or database change was performed in Phase 2.
