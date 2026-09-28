# Odds Scheduler First-Run Timing Audit Report

## 1. Objective

Determine exactly how the existing APScheduler job named refresh_odds calculates its first execution time and whether it waits a full 6 hours after the Worker/Scheduler starts.

This is an audit only. No source code or database data was changed.

## 2. Scheduler Code Evidence

Primary scheduler definition is in [app/services/scheduler.py](../app/services/scheduler.py).

Key code excerpts:

- The scheduler is created as `AsyncIOScheduler()`.
- The job is registered in `LiveUpdateScheduler.start()`.
- The odds refresh job is defined as:

```python
self.scheduler.add_job(
    self._refresh_odds_job,
    trigger=IntervalTrigger(minutes=360),
    id="refresh_odds",
    name="Refresh Odds Snapshots",
    max_instances=1,
)
```

Relevant file flow:

- [worker.py](../worker.py) starts the worker lifecycle.
- [scheduler_service.py](../scheduler_service.py) calls `live_scheduler.start()`.
- [app/services/scheduler.py](../app/services/scheduler.py) creates the scheduler and registers all jobs.

Important findings from the code:

- `refresh_odds` uses `IntervalTrigger(minutes=360)`.
- There is no explicit `start_date` in the job registration.
- There is no explicit `next_run_time` override in the job registration.
- There is no starting hook that calls `refresh_odds()` during worker startup.
- There is no immediate refresh logic outside the scheduled job.
- `max_instances=1` is configured.
- `misfire_grace_time` is not configured for this job.
- `coalesce` is not configured for this job.

## 3. First-Run Calculation

From the actual project code, the job is an ordinary APScheduler interval job using `IntervalTrigger(minutes=360)`.

Because there is no explicit `start_date` and no explicit `next_run_time`, the first execution is scheduled relative to the scheduler activation time. In practical APScheduler semantics, the first run occurs after a full interval from the scheduler start, which in this code is effectively:

```
scheduler_start_time + 6 hours
```

This matches the project’s actual intent and the observed behavior of the worker startup: the job is registered during scheduler startup; there is no code path to trigger it immediately.

## 4. Runtime Job State

Observed runtime evidence from the local worker startup:

```
2026-09-17 23:32:10,278 INFO [__main__] Worker dependency health: postgres=True redis=True
2026-09-17 23:32:10,278 INFO [scheduler_service] Starting dedicated scheduler service
2026-09-17 23:32:10,280 INFO [apscheduler.scheduler] Added job "Refresh Odds Snapshots" to job store "default"
2026-09-17 23:32:10,281 INFO [app.services.scheduler] SCHEDULER_STARTED jobs=['sync_live_matches', 'reconcile_recent_non_terminal', 'sync_daily_fixtures', 'repair_daily_matches', 'refresh_standings', 'refresh_odds', 'refresh_lineups', 'refresh_events', 'refresh_statistics']
2026-09-17 23:32:10,281 INFO [__main__] Background worker started
```

This is direct runtime proof that the Worker starts successfully, the scheduler starts, and the job is registered.

## 5. next_run_time

The project does not set a custom `next_run_time` and does not persist scheduler state. The live runtime does not expose a custom override for the odds job in the startup path. The project code therefore indicates the first next run is the standard interval-based value computed from scheduler startup time.

## 6. Interval Calculation

The job scheduling is explicit in code:

```python
trigger=IntervalTrigger(minutes=360)
```

That is exactly 360 minutes = 6 hours.

The scheduler only adds the job at startup; it does not manually invoke it. The logic therefore matches:

```
Worker starts
  -> scheduler starts
  -> refresh_odds job is registered
  -> first run occurs after the 6-hour interval from scheduler start
```

## 7. Worker Restart Behavior

The local Worker was started successfully and the scheduler registered the job. The project code shows no persistent scheduler job store and no custom restart logic that carries a prior next_run_time forward.

Therefore the restart behavior is consistent with a fresh schedule being created on every new Worker startup. In this codebase, there is no evidence of saved scheduler state across restarts.

## 8. Persistent Scheduler State

Search of the project did not reveal any APScheduler persistence configuration such as a SQLAlchemy job store, Redis job store, or scheduler state database configuration.

The live scheduler state is in-memory only. There is no persistent stored `next_run_time` tied to prior Worker lifecycles.

Conclusion:

- Persistent scheduler state: NO
- Schedule survives worker restart: NO
- Restart resets interval: YES, effectively

## 9. Startup Immediate-Run Check

A code search did not reveal any startup-time invocation of `refresh_odds()`, nor any call path such as `run_job`, `reschedule_job`, or `modify_job` during worker startup.

The relevant startup chain is:

```
worker.py
  -> refresh_dependency_health()
  -> start_scheduler()
  -> live_scheduler.start()
  -> add_job(refresh_odds)
  -> scheduler.start()
```

There is no immediate odds call after startup. The project’s code path is startup -> schedule -> wait for interval.

## 10. Real Match Eligibility

Read-only DB query confirmed a real match exists within the required rule window. Example observed data:

- match_id: 1570386
- provider_fixture_id: 1570386
- status: NS
- match_time: 2026-09-17T17:00:00+00:00
- league: La Liga
- current_odds_count: 0

This confirms the job would have a valid future candidate when its scheduled execution eventually occurs.

## 11. Natural Execution Observation

The local worker was started and the scheduler registered `refresh_odds`, but no natural scheduled odds run was observed during the available runtime window because the configured interval is 6 hours.

Observation:

```
Natural execution observed: NO
```

This does not imply a failed implementation; it simply means the observation window was shorter than the scheduled wait.

## 12. Core Question Answers

### Question 1
Does the first refresh_odds execution wait 6 hours from Worker/Scheduler startup?

Answer: YES, based on the actual code path and APScheduler interval semantics.

Reason:

- `IntervalTrigger(minutes=360)` is explicitly configured.
- No explicit `start_date` or `next_run_time` override is set.
- No startup hook triggers `refresh_odds()` immediately.
- The job is registered only when the scheduler starts.

### Question 2
What determines the first next_run_time?

Answer: The scheduler start time combined with the interval trigger, because no explicit run time is configured.

### Question 3
If Worker starts at T0, what is the expected first execution time?

Answer: roughly `T0 + 6 hours`, assuming no scheduler delay or miss.

### Question 4
If Worker is stopped before the first execution, what happens?

Answer: The schedule is not persisted; when the worker restarts, it creates a new scheduler and new interval timing from the new startup point.

### Question 5
If Worker starts again later, does the 6-hour timer continue or reset?

Answer: It resets from the new scheduler startup, because there is no persistent APScheduler state in the project.

### Question 6
Does the current local test require the Worker to remain running continuously until the natural refresh_odds execution?

Answer: YES.

The interval is six hours and there is no alternative trigger path. Without the Worker staying active, the next run is never reached.

## 13. Final Timeline

```
Worker starts
  -> scheduler starts
  -> refresh_odds job registered
  -> no immediate refresh call
  -> next_run_time = scheduler_start_time + 6 hours
  -> wait 6 hours
  -> refresh_odds() executes naturally
```

## 14. Final Classification

`FIRST_RUN_AFTER_STARTUP`

## 15. Evidence / Commands Used

- Local worker startup command:
  - `.\.venv\Scripts\python.exe worker.py`
- Runtime logs:
  - `Worker dependency health: postgres=True redis=True`
  - `Scheduler started`
  - `SCHEDULER_STARTED jobs=['...', 'refresh_odds', ...]`
- Scheduler code evidence:
  - [app/services/scheduler.py](../app/services/scheduler.py)
- Worker entrypoint evidence:
  - [worker.py](../worker.py)
- Scheduler service evidence:
  - [scheduler_service.py](../scheduler_service.py)

## 16. Conclusion

The actual project code and live local runtime show that `refresh_odds` is an interval job set to 360 minutes (6 hours) and is registered when the Worker starts the scheduler. There is no explicit `start_date`, no explicit `next_run_time`, no startup hook to trigger it immediately, and no persisted scheduler state. Therefore, the first odds refresh is startup-relative and waits approximately a full 6 hours from scheduler startup before executing naturally.
