# VPS Worker Production Runtime Check Report

## Production Runtime Owner

Current runtime inspection shows this is not a Docker Compose / systemd / Kubernetes deployment in the active environment.

Evidence:

- `docker` is not available in this runtime (`docker: The term 'docker' is not recognized...`)
- no systemd service manager is present in the current environment
- no Kubernetes control plane / pod manager is active
- the active process model is a local Windows Python runtime, not a production container supervisor

Therefore the effective production runtime owner is:

```
Production Runtime Owner: unknown / local manual runtime
```

## Worker Status

Actual process inspection:

- `uvicorn.exe` is running
- several `python.exe` processes are running
- none of the active command lines correspond to the dedicated worker entrypoint `python worker.py`
- no active `refresh_odds` scheduler registration was found

Observed state:

```
Worker: NOT RUNNING
worker.py: NOT ACTIVE
Scheduler: NOT RUNNING
refresh_odds: NOT REGISTERED
```

## Runtime Evidence

Process check showed active Python and uvicorn processes, but the command line for the worker entry point was not active.

This is the evidence that the expected production worker chain was never started:

```
worker.py
  -> scheduler_service.start_scheduler()
  -> live_scheduler.start()
  -> refresh_odds job registered
```

Instead, the runtime shows only the API process:

```
uvicorn.exe app.main:app ...
```

and no running worker path.

## Why the Worker Is Not Running

The root cause is not Odds logic, scheduler logic, or database/data state.

The exact root cause is:

```
The production Worker runtime owner is absent in the current environment.
No active process manager or worker service is running the dedicated worker entrypoint,
so `worker.py` never starts, `live_scheduler.start()` never runs, and `refresh_odds` is never registered.
```

This is confirmed by:

- no active `python worker.py` process
- no active scheduler job list
- no `refresh_odds` entry in the runtime scheduler
- no Docker/systemd/Kubernetes process manager active here

## Auto-Start Configuration

The repository configuration indicates that the project expects a dedicated worker service in [docker-compose.yml](../docker-compose.yml), with:

```
worker:
  command: python worker.py
  restart: unless-stopped
```

and the API service explicitly sets:

```
SCHEDULER_ENABLED=false
```

That means the scheduler is expected to be owned by the dedicated worker runtime, not by the API service.

In the current active environment, that configured auto-start mechanism is not running.

## Final Result

```
Production Runtime Owner: unknown / local manual runtime
Worker: NOT RUNNING
Auto-start: NO
Auto-restart: NO
Scheduler: NOT RUNNING
refresh_odds: NOT REGISTERED

Root Cause:
The production worker entrypoint is not active in the current runtime, and no Docker/systemd/Kubernetes process manager is present to start it. Because the Worker never starts, the scheduler never begins and refresh_odds is never registered.

Final Status: BLOCKED
```
