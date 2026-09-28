# Worker / Scheduler Runtime Root-Cause Audit Report

## Current Runtime

- Worker: NOT RUNNING
- Scheduler: NOT RUNNING
- refresh_odds registered: NO

## Startup Chain

Worker entry path:

```
worker.py
  -> run_worker()
  -> start_worker_metrics_server(8001)
  -> refresh_dependency_health()
  -> start_scheduler()
  -> live_scheduler.start()
  -> scheduler.add_job(..., id="refresh_odds", ...)
  -> scheduler.start()
```

Observed result in current runtime:

- [worker.py](../worker.py) is the actual worker entry point and is designed to start the scheduler.
- [scheduler_service.py](../scheduler_service.py) calls the global scheduler start wrapper.
- [app/services/scheduler.py](../app/services/scheduler.py) registers the jobs in `LiveUpdateScheduler.start()`.
- The real runtime evidence shows the scheduler object exists but is not started and contains no jobs.

## Runtime Evidence

From the running environment:

- `live_scheduler.is_running = False`
- `live_scheduler.scheduler.get_jobs() = []`
- `refresh_odds` job is absent

This is direct runtime proof that the startup chain stopped before the scheduler could reach the running state.

## Root Cause

The exact root cause is not a code bug in the odds logic or the scheduler registration logic. The root cause is that the intended runtime owner for the scheduler is not active in the current environment.

The worker entry point is present and correct, but no actual process is running it in the current machine/runtime, and no active process-manager/compose runtime is starting it. As a result:

- `worker.py` is not running
- `start_scheduler()` is never called in the current runtime
- `live_scheduler.start()` never executes
- `refresh_odds` is never registered
- the scheduler never reaches the running state

## Startup Status

- Worker entry: PASS (the code path exists and is valid)
- Scheduler initialization: PASS (the scheduler object is created)
- Scheduler start: FAIL (not invoked in the active runtime)
- Job registration: FAIL (no jobs exist in the current runtime)
- Scheduler running: FAIL

## Process / Ownership Findings

The runtime owner expected by the project is the production Worker service defined in [docker-compose.yml](../docker-compose.yml):

```
worker:
  command: python worker.py
  restart: unless-stopped
```

This means the scheduler is supposed to be owned by the dedicated Worker container/service, not by the API service. The API service explicitly sets `SCHEDULER_ENABLED=false` in [docker-compose.yml](../docker-compose.yml), and the dedicated worker service has `SCHEDULER_ENABLED=true`.

Current environment evidence:

- `docker compose` is not available in this runtime (`docker: The term 'docker' is not recognized...`)
- no active `worker.py` process was found in the current runtime
- no live scheduler job set was found

Therefore, the exact component preventing startup is the missing active Worker runtime owner / process manager, not the odds system or scheduler code.

## Code Changes

NONE

No code fix was required because the investigation proves the issue is environmental/runtime ownership, not a scheduler logic bug.

## Final Status

NO_CODE_PROBLEM

The scheduler is not running because the production Worker that should start it is not active in the current runtime environment. The missing runtime owner prevents `start_scheduler()` from ever being reached, which prevents `refresh_odds` from being registered and running.
