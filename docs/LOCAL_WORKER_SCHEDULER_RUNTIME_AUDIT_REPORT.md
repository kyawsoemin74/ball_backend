# Local Worker / Scheduler Runtime Audit Report

## 1. Worker Exists

The project contains the worker entry point at [worker.py](../worker.py).

Startup chain:

```
worker.py
  -> run_worker()
  -> start_worker_metrics_server(8001)
  -> refresh_dependency_health()
  -> start_scheduler()
  -> scheduler_service.start_scheduler()
  -> live_scheduler.start()
  -> APScheduler job registration
  -> scheduler.start()
```

Worker entry point and startup logic are present in [worker.py](../worker.py), [scheduler_service.py](../scheduler_service.py), and [app/services/scheduler.py](../app/services/scheduler.py).

## 2. Scheduler Ownership

The code shows the Worker owns the Scheduler.

- [worker.py](../worker.py) explicitly calls `start_scheduler()`.
- [scheduler_service.py](../scheduler_service.py) calls `live_scheduler.start()`.
- [app/services/scheduler.py](../app/services/scheduler.py) creates the scheduler and registers jobs.
- The API process is not the owner of the scheduler in this project path.

Observed project configuration:

- API runtime is separate from the worker runtime.
- The worker is the owned startup path for the scheduler.

Result:

```
Worker owns Scheduler: YES
API owns Scheduler: NO
Expected startup process: python worker.py
```

## 3. Local Runtime Check

Current Windows runtime evidence:

- `worker.py` exists.
- Local Python virtual environment exists.
- `uvicorn.exe` is active.
- A dedicated `python worker.py` process was started in the current session and executed successfully.

Observed runtime while starting the worker:

```
2026-09-17 23:24:55,496 INFO  [__main__] Worker dependency health: postgres=True redis=True
2026-09-17 23:24:55,496 INFO  [scheduler_service] Starting dedicated scheduler service
2026-09-17 23:24:55,499 INFO  [apscheduler.scheduler] Scheduler started
2026-09-17 23:24:55,499 INFO  [app.services.scheduler] SCHEDULER_STARTED jobs=['sync_live_matches', 'reconcile_recent_non_terminal', 'sync_daily_fixtures', 'repair_daily_matches', 'refresh_standings', 'refresh_odds', 'refresh_lineups', 'refresh_events', 'refresh_statistics']
2026-09-17 23:24:55,499 INFO  [__main__] Background worker started
```

Therefore:

```
Worker: RUNNING
Scheduler: RUNNING
```

## 4. Worker Startup Dependencies

The local worker was started successfully with the project virtual environment using:

```
.\.venv\Scripts\python.exe worker.py
```

Dependency checks during startup reported:

- postgres=True
- redis=True

This confirms the required local runtime dependencies for the worker startup path were available.

## 5. Safe Local Startup Test

The local worker startup was performed without invoking `refresh_odds()` or manually triggering scheduler jobs. It completed startup normally.

### Startup result

```
Worker starts: PASS
Scheduler starts: PASS
Jobs register: PASS
refresh_odds registered: YES
```

## 6. Odds Job Registration

During the worker startup, APScheduler registered the jobs including:

```
refresh_odds
```

with the runtime scheduler status logged as:

```
SCHEDULER_STARTED jobs=['sync_live_matches', 'reconcile_recent_non_terminal', 'sync_daily_fixtures', 'repair_daily_matches', 'refresh_standings', 'refresh_odds', 'refresh_lineups', 'refresh_events', 'refresh_statistics']
```

## Final Summary

```
Local Worker File: EXISTS
Worker Startup: PASS
Worker Runtime: RUNNING
Scheduler Startup: PASS
Scheduler Runtime: RUNNING
refresh_odds Registered: YES

Root Cause:
There is no local runtime problem preventing the worker from starting. The project contains the worker entry point, the dependency checks pass, and the local worker successfully starts the scheduler and registers refresh_odds when run via the project’s intended command.

Final Status: WORKING
```
