# FOVER Worker / Scheduler Runtime Architecture Audit Report

Audit date: 2026-09-19
Audit time: 15:27:03 +06:30
Scope: Phase 1 audit only. No application code, scheduler logic, sync logic, repository, schema, Docker configuration, service, or runtime process was modified or restarted. No manual sync was triggered.

## 1. Executive Summary

The repository implements a dedicated worker architecture:

```text
API process
  -> uvicorn app.main:app
  -> FastAPI routes, health, API-side notification/websocket tasks

Worker process
  -> python worker.py
  -> worker metrics on :8001
  -> dependency checks
  -> scheduler_service.start_scheduler()
  -> LiveUpdateScheduler
  -> scheduled background jobs
```

`app.main:app` does not import `worker.py`, instantiate the scheduler, or call `start_scheduler()`. `worker.py` owns scheduler startup. The Live Sync business path is separated correctly in source: scheduler trigger -> `football_service.sync_live_matches()` -> `FixtureSyncService` -> provider -> match repository/upsert -> scheduler-owned transaction completion.

The current local runtime is not executing the target architecture. Uvicorn is serving port 8000 and PostgreSQL/Redis are reachable, but no process command contains `worker.py`, port 8001 refuses connections, and API metrics report `fover_scheduler_up 0.0` with no scheduler job run counters. Therefore current Worker and Scheduler operation are not running, despite the source and deployment definitions providing the path.

Final classification: **FAIL**

This classification is based on current runtime evidence, not intended architecture. The first proven runtime failure boundary is the Worker startup/runtime boundary.

## 2. Current Architecture

### Actual implementation

- API ownership: `app.main:app`, served by Uvicorn.
- Worker ownership: `worker.py`, executed as a separate process.
- Scheduler object: global `live_scheduler` in `app.services.scheduler`.
- Scheduler wrapper: `scheduler_service.start_scheduler()` and `stop_scheduler()`.
- Live Sync trigger: APScheduler job `sync_live_matches`.
- Sync orchestration: `FixtureSyncService.sync_live_matches()`.
- Provider transport: `FixtureProvider.get_live_fixtures()` and stale fixture detail requests.
- Persistence: `MatchRepository.get_by_provider_fixture_id()` for identity resolution plus the `Match` PostgreSQL upsert in `FixtureSyncService`.
- Commit/rollback: owned by the scheduler job around the service result.

### Intended design versus actual runtime

The repository documentation and manifests describe API plus dedicated Worker operation. The active Windows runtime currently has API plus database/cache processes, but no active Worker. The deployment definition is present; its activation is not proven in the current runtime.

## 3. API Startup Chain

Actual source chain:

```text
uvicorn app.main:app
  -> import app.main
  -> construct FastAPI app
  -> lifespan starts notification_worker.start()
  -> startup event starts Redis websocket broker
  -> serve HTTP on the configured Uvicorn port
```

Evidence in [app/main.py](app/main.py):

- `app = FastAPI(...)` defines the API application.
- The lifespan starts only `notification_worker` and stops it during shutdown.
- Startup/shutdown handlers manage the websocket Redis broker.
- No import of `worker.py` exists.
- No call to `start_scheduler()` exists.
- No scheduler object is constructed or started.

Current observed API command:

```text
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Current API evidence:

- `GET /health`: HTTP 200, `{"status":"alive"}`.
- `GET /health/live`: HTTP 200.
- `GET /health/ready`: HTTP 200, PostgreSQL and Redis both true.
- Three Uvicorn-related processes were observed in the local process tree, representing one Uvicorn process tree rather than a `worker.py` process.

Conclusion: API startup ownership is **PASS**. API does not own the Scheduler or Live Sync background job.

## 4. Worker Startup Chain

Actual source chain in [worker.py](worker.py) and [scheduler_service.py](scheduler_service.py):

```text
python worker.py
  -> asyncio.run(run_worker())
  -> install SIGINT/SIGTERM handlers
  -> start_worker_metrics_server(8001)
  -> refresh_dependency_health()
  -> start_scheduler()
  -> live_scheduler.start()
  -> wait for shutdown event
  -> stop_scheduler() in finally
```

The Worker is a separate process and does not depend on API startup. It shares PostgreSQL and Redis configuration, but its scheduler lifecycle is independent of Uvicorn.

Current runtime evidence:

- No active process command contained `worker.py`.
- Port 8001 was not listening; `GET http://127.0.0.1:8001/metrics` was refused.
- No worker metrics or worker health endpoint was available.

Conclusion:

- Worker entry point in source: **PASS**.
- Worker startup path in source: **PASS**.
- Worker currently running: **FAIL**.
- Worker dependency health in the Worker process: **UNKNOWN**. API readiness proves the dependencies are reachable from the API process, not that Worker startup completed.

## 5. Scheduler Ownership

Scheduler instantiation occurs in `LiveUpdateScheduler.__init__()` in [app/services/scheduler.py](app/services/scheduler.py):

```text
self.scheduler = AsyncIOScheduler()
```

The scheduler is started only through:

```text
worker.py
  -> scheduler_service.start_scheduler()
  -> live_scheduler.start()
```

`LiveUpdateScheduler.start()` registers jobs and then calls `self.scheduler.start()`. `scheduler_service.start_scheduler()` sets `fover_scheduler_up` to `1`; `stop_scheduler()` sets it to `0`.

### Registered jobs in source

| Job ID | Trigger | Interval/schedule | max_instances | Timezone |
|---|---|---|---:|---|
| `sync_live_matches` | interval | 60 seconds | 1 | scheduler default timezone |
| `reconcile_recent_non_terminal` | interval | 5 minutes | 1 | scheduler default timezone |
| `sync_daily_fixtures` | cron | 00:01 daily | 1 | UTC+06:30 Myanmar offset |
| `repair_daily_matches` | cron | 02:00 daily | 1 | UTC+06:30 Myanmar offset |
| `refresh_standings` | interval | 6 hours | 1 | scheduler default timezone |
| `refresh_odds` | interval | 360 minutes / 6 hours | 1 | scheduler default timezone |
| `refresh_lineups` | interval | 15 minutes | 1 | scheduler default timezone |
| `refresh_events` | interval | 600 seconds | 1 | scheduler default timezone |
| `refresh_statistics` | interval | 600 seconds | 1 | scheduler default timezone |

Concurrency controls include APScheduler `max_instances=1` and job-specific database/resource lock logic. Job failures are caught by individual job paths and recorded through scheduler error metrics; APScheduler lifecycle events are logged as started, completed, missed, or failed.

### Runtime scheduler evidence

API metrics showed:

```text
fover_scheduler_up 0.0
```

No `fover_scheduler_job_runs_total{...}` samples were present. Port 8001 was unavailable, so the Worker metrics endpoint could not provide a second source of evidence.

Conclusion:

- Scheduler ownership by Worker in source: **PASS**.
- `sync_live_matches` registration in source: **PASS**.
- Scheduler running now: **FAIL**.
- Live Sync registration in the current runtime: **UNKNOWN** as an independent runtime fact, with `fover_scheduler_up 0.0` and no Worker strongly indicating it is not registered.

## 6. Live Sync Execution Chain

The actual source chain is:

```text
worker.py
  -> scheduler_service.start_scheduler()
  -> LiveUpdateScheduler.start()
  -> add_job(_sync_live_matches_job, id="sync_live_matches", interval=60s)
  -> _sync_live_matches_job()
  -> advisory/resource lock acquisition
  -> 24-hour past/future Match candidate gate
  -> football_service.sync_live_matches(db)
  -> FixtureSyncService.sync_live_matches(db)
  -> FixtureProvider.get_live_fixtures()
  -> stale local live Match lookup
  -> optional FixtureProvider.get_fixtures_by_ids(...)
  -> FixtureSyncService._process_sync_with_candidates()
  -> provider league identity and allowed-league checks
  -> provider team identity resolution
  -> MatchRepository.get_by_provider_fixture_id()
  -> PostgreSQL Match upsert with status/elapsed/scores
  -> db.flush()
  -> scheduler commit or rollback
  -> active-match update and live-match cache invalidation after commit
```

Responsibility ownership:

| Responsibility | Owner | Classification |
|---|---|---|
| Trigger and interval | `LiveUpdateScheduler` | PASS in source |
| Synchronization orchestration | `FixtureSyncService` | PASS in source |
| Provider live fixture retrieval | `FixtureProvider` | PASS in source |
| League/team identity resolution | `FixtureSyncService` and repositories/services | PASS in source |
| Match identity resolution | `MatchRepository.get_by_provider_fixture_id()` | PASS in source |
| Match persistence | `FixtureSyncService` / PostgreSQL upsert | PASS in source |
| Outer transaction | Scheduler job | PASS in source |
| API ownership of Live Sync | None found | PASS: API does not own it |

The Scheduler is trigger-only with respect to domain synchronization. It delegates business work to `football_service` and `FixtureSyncService`; it does not directly write Match rows.

Current runtime execution of this chain: **UNKNOWN**, with the first prerequisite Worker boundary proven failed.

## 7. Docker / Deployment Architecture

### Docker Compose source configuration

[docker-compose.yml](docker-compose.yml) defines one deployment unit containing:

- `api`: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4`.
- `worker`: `python worker.py`.
- `postgres`: PostgreSQL 15.
- `redis`: Redis 7 with AOF enabled.
- Observability services: PostgreSQL exporter, Redis exporter, cAdvisor, Prometheus, and Grafana.

API configuration sets `SCHEDULER_ENABLED=false`; Worker configuration sets `SCHEDULER_ENABLED=true`. The source code currently does not use this setting to start or stop the scheduler; actual ownership is established by the process command. Compose assigns the API to port 8000 and the Worker metrics server to port 8001.

Both API and Worker use `restart: unless-stopped`. API and Worker depend on healthy PostgreSQL and Redis. API health checks `/health/ready`; Worker health checks `http://localhost:8001/metrics`. PostgreSQL and Redis each have their own health checks. All services use the Compose network by service name for database/cache connections.

The configured complete deployment command is:

```text
docker compose up -d
```

The CI workflow [docker-compose-up.yml](.github/workflows/docker-compose-up.yml) builds with `docker compose build` and deploys with `docker compose up -d`, then runs `docker compose ps`.

### Dockerfile

[Dockerfile](Dockerfile) builds one image, installs requirements, copies the repository, runs as a non-root `app` user, and exposes 8000 and 8001. Its default command is the API Uvicorn command; Compose and Kubernetes override the command for the Worker.

### Kubernetes configuration

Kubernetes separately defines:

- API Deployment `fover-backend`, replicas 2, `SCHEDULER_ENABLED=false`, port 8000.
- Worker Deployment `fover-backend-worker`, replicas 1, command `python worker.py`, `SCHEDULER_ENABLED=true`, port 8001.
- Worker HPA constrained to min/max 1 replica.
- Worker metrics ClusterIP Service.
- API Service, PostgreSQL StatefulSet, Redis StatefulSet, ingress, and related resources.

### Current deployment determination

The active Windows runtime is not proven to be Docker Compose or Kubernetes:

- `docker` was not available as a command.
- No Docker Compose containers were inspected or started.
- Current observed processes are local Windows Python/Uvicorn processes.

Current deployment state: **API plus local PostgreSQL/Redis reachable; Worker absent**.

## 8. Current Runtime Evidence

Read-only checks performed at 2026-09-19 15:27 +06:30:

| Check | Evidence | Result |
|---|---|---|
| API process | Uvicorn process tree for `app.main:app` | PASS |
| API port | TCP 8000 listening | PASS |
| API liveness | `/health/live` HTTP 200 | PASS |
| API readiness | `/health/ready` HTTP 200, postgres=true, redis=true | PASS |
| PostgreSQL port | TCP 5432 listening | PASS |
| Redis port | TCP 6379 listening | PASS |
| Worker process | No `worker.py` command present | FAIL |
| Worker metrics port | TCP 8001 refused | FAIL |
| Scheduler gauge | API metrics `fover_scheduler_up 0.0` | FAIL for current runtime |
| Scheduler job counters | No scheduler job run samples | UNKNOWN / no evidence |
| Worker logs | No active Worker output or Worker endpoint | UNKNOWN |
| Docker runtime | Docker command unavailable | UNKNOWN / not applicable to local process |

No service was started, stopped, or restarted during these checks.

## 9. Target Match Runtime Evidence

The requested target local Match IDs were inspected without manual synchronization:

| Local Match | Provider | Provider fixture ID | League | Current DB status | Current DB score | API read-back |
|---:|---|---:|---:|---|---|---|
| 1570397 | api-football | 1570397 | 140 | FT, elapsed 90 | 1-3 | HTTP 200, FT, 1-3 |
| 1589091 | api-football | 1557408 | 39 | FT, elapsed 90 | 3-0 | HTTP 200, FT, 3-0 |

The provider identity and state values above came from a read-only SQLAlchemy query against the existing database. API read-back was performed through `GET /api/matches/{match_id}`. The API response schema does not expose provider identity fields, so provider identity was verified from the database query and the remaining state was independently read through the API.

Execution evidence for these targets:

- Worker execution: **FAIL / not running at audit time**.
- Scheduler registration in the active Worker: **UNKNOWN**, with no Worker and `fover_scheduler_up=0.0`.
- Scheduler execution for either target: **UNKNOWN**.
- Provider fetch initiated by the scheduler for either target: **UNKNOWN**.
- Current target rows are final (`FT`), so this snapshot does not demonstrate a current live transition.

Source capability exists to process a provider fixture when the Worker is running and the fixture is returned by the live/stale selection path. That is capability evidence, not execution evidence.

## 10. Boundary-by-Boundary PASS / FAIL / UNKNOWN

| Boundary | Classification | Evidence |
|---|---|---|
| API startup | PASS | Uvicorn serves `app.main:app`; health endpoints return successfully. |
| Worker startup/runtime | FAIL | No `worker.py` process is running. |
| Scheduler startup | FAIL | Worker is absent; API metrics report `fover_scheduler_up 0.0`. |
| Job registration | PASS in source; UNKNOWN at runtime | `sync_live_matches` is added by `LiveUpdateScheduler.start()`, but no active Worker exists to register it. |
| Job execution | UNKNOWN | No Worker metrics/logs or job counter prove a current run. |
| Provider fetch from Scheduler | UNKNOWN | Existing provider capability is source-proven, but no scheduler-triggered fetch is evidenced. |
| FixtureSyncService orchestration | PASS in source; UNKNOWN at runtime | Service path and transaction contract are present; current Worker is absent. |
| Repository identity/persistence | PASS in source; UNKNOWN for current scheduler run | Target rows resolve in read-only DB inspection, but no current scheduler write was observed. |
| PostgreSQL dependency | PASS | API readiness and port 5432 show current reachability. Worker-specific health remains unproven. |
| Redis dependency | PASS | API readiness and port 6379 show current reachability. Worker-specific health remains unproven. |

## 11. First Proven Failure Boundary

The first proven failure is:

```text
API PASS
  -> Worker FAIL: dedicated worker process is absent
  -> Scheduler FAIL / not started
```

This is a runtime ownership/activation failure, not a proven defect in Live Sync business logic, provider identity resolution, repository persistence, or database schema.

The source path after Worker startup is not classified as failed merely because it was not executed in this runtime window.

## 12. Architecture Gap Analysis

Target architecture requires:

```text
API service      -> app.main:app -> Uvicorn :8000
Worker service   -> worker.py    -> Scheduler -> background jobs
PostgreSQL and Redis shared by both
```

Current source and deployment gap comparison:

- API ownership already matches the target.
- Worker entry point and separate Worker command already match the target.
- Scheduler ownership already matches the target.
- Compose already defines API, Worker, PostgreSQL, and Redis in one deployment unit.
- Kubernetes already defines separate API and one-replica Worker deployments.
- The active local runtime does not activate the Worker, so the deployed/declared architecture is not the current runtime architecture.
- Current runtime observability cannot prove Worker state because port 8001 is unavailable.
- The API metrics endpoint exposes the scheduler gauge in the local configuration, but it reports the API process's in-memory gauge and is not a substitute for Worker metrics.
- The Compose Prometheus configuration scrapes `api:8000` and `worker:8001`, while Compose disables API metrics; this should be resolved during design freeze so observability expectations are explicit.
- `SCHEDULER_ENABLED` is present in configuration and manifests but is not the mechanism that owns scheduler startup in the inspected source path.

## 13. Required Changes

These are Phase 2/3 design requirements only. They were not implemented in this audit:

1. Ensure the selected deployment mechanism actually starts both `api` and `worker` as separate long-lived services.
2. Preserve exactly one active scheduler owner. Compose should run one Worker service; Kubernetes should keep the Worker at one replica unless leader election is added.
3. Make the Worker health contract explicit: process liveness, dependency readiness, scheduler-up gauge, and job counters should be observable from the Worker metrics endpoint.
4. Define whether `SCHEDULER_ENABLED` is authoritative configuration or remove its ambiguity; it must not imply API-owned scheduling.
5. Align Prometheus scrape targets and API metrics settings with the chosen observability contract.
6. Verify restart behavior through the deployment manager in a later phase; this audit did not restart anything.
7. Use `docker compose up -d` as the single complete local deployment command only in the implementation phase, after the design is frozen.

No change is required to the Live Sync business logic to establish process ownership. Any future implementation must leave the provider, FixtureSyncService, repository, identity, transaction, cache, event, lineup, odds, standings, and database schema contracts unchanged unless separately authorized.

## 14. Risks

- Starting multiple Worker replicas without singleton control can create duplicate scheduler execution. Kubernetes currently constrains the Worker to one replica; this must remain enforced.
- Running a scheduler inside multi-worker Uvicorn would create duplicate or uncontrolled scheduler owners; source currently avoids this.
- A healthy API does not prove a healthy Worker. The Worker has a separate metrics port and needs independent monitoring.
- `fover_scheduler_up` exposed by an API process is not reliable evidence of the separate Worker scheduler state.
- A Worker may start while PostgreSQL or Redis is unavailable; startup currently logs dependency health but proceeds to `start_scheduler()` rather than failing closed.
- In-memory APScheduler state is recreated on Worker restart; scheduled next-run state is not persisted.
- A source-registered job is not evidence that the job has executed. Runtime counters/logs are required.
- The target rows' current `FT` state cannot be used as proof of a current live-sync transition.
- Docker Compose and Kubernetes definitions exist, but the current Windows runtime has no Docker command and no active Kubernetes evidence.

## 15. Proposed Target Architecture

```text
                    FOVER BACKEND
                         |
              +----------+----------+
              |                     |
          API SERVICE          WORKER SERVICE
              |                     |
      app.main:app              worker.py
      Uvicorn :8000                 |
                                    v
                                Scheduler
                                    |
                    +---------------+---------------+
                    |               |               |
                Live Sync      Event Sync      Other jobs
                    |
                    v
             FixtureSyncService
                    |
             PostgreSQL + Redis
```

Operational target:

```text
docker compose up -d
  -> api       -> app.main:app -> :8000
  -> worker    -> python worker.py -> scheduler -> :8001 metrics
  -> postgres
  -> redis
```

The Scheduler remains trigger-only. `FixtureSyncService` remains responsible for synchronization orchestration. Repositories remain responsible for persistence access. API requests remain separate from scheduled background execution.

## 16. Phase 2 Design-Freeze Requirements

Before implementation, freeze these decisions:

- API startup owner is `app.main:app` under Uvicorn.
- Worker startup owner is `worker.py` under a separate process/service.
- Scheduler startup occurs only in the Worker process.
- API must not import Worker startup or start APScheduler.
- Worker replica count is one unless an explicit leader-election design is approved.
- Live Sync registration remains `sync_live_matches` at 60 seconds with `max_instances=1`, subject to explicit approval before any change.
- Worker health is independently observable on port 8001.
- API and Worker share PostgreSQL and Redis but do not share process ownership.
- `docker compose up -d` must start API, Worker, PostgreSQL, and Redis in the implementation phase.
- Restart and recovery semantics must be tested without changing Live Sync business logic.
- No changes are authorized to league identity, fixture sync business logic, Match identity, standings, events, lineups, odds, H2H, or database schema.

## Final Classification

**FAIL**

Reason: the current runtime has a healthy API and reachable PostgreSQL/Redis, but the dedicated Worker is absent, port 8001 is unavailable, the Scheduler gauge is zero, and no current scheduler execution evidence exists. The source and deployment architecture are largely aligned with the target, but the Phase 1 exit condition requires knowing whether the current runtime actually has Worker/Scheduler; current evidence proves that it does not.
