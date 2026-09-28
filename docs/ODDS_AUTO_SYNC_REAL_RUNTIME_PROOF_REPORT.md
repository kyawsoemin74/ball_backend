# Odds Auto Sync Real Runtime Proof Report

## Runtime Verification

### 1. Real 72-hour NS match

Executed against the live local PostgreSQL database using the production eligibility rule:

- status = NS
- match_time >= NOW()
- match_time <= NOW() + 72 hours
- provider_fixture_id IS NOT NULL

Observed real eligible match samples:

- match_id: 1570386
  - provider_fixture_id: 1570386
  - status: NS
  - match_time: 2026-09-17T17:00:00+00:00
  - league: La Liga
  - current_odds_count: 0

- match_id: 1570390
  - provider_fixture_id: 1570390
  - status: NS
  - match_time: 2026-09-17T19:30:00+00:00
  - league: La Liga
  - current_odds_count: 0

- match_id: 1589030
  - provider_fixture_id: 20001
  - status: NS
  - match_time: 2026-09-18T07:05:02.893832+00:00
  - league: Phase3 League 20001
  - current_odds_count: 1

Result:

```
Eligible 72h NS Match: YES
```

### 2. Worker startup

Started the existing local worker using the project entry point:

```
.\.venv\Scripts\python.exe worker.py
```

Observed runtime output:

```
Worker dependency health: postgres=True redis=True
Scheduler started
SCHEDULER_STARTED jobs=['sync_live_matches', 'reconcile_recent_non_terminal', 'sync_daily_fixtures', 'repair_daily_matches', 'refresh_standings', 'refresh_odds', 'refresh_lineups', 'refresh_events', 'refresh_statistics']
Background worker started
```

Result:

```
Worker: RUNNING
Scheduler: RUNNING
```

### 3. refresh_odds registration

The scheduler registration log includes:

```
Refresh Odds Snapshots
refresh_odds
```

The active scheduler contains the job id:

```
refresh_odds
```

This was observed during startup and confirms that the job is registered automatically by the existing worker.

### 4. Natural scheduled execution

The project scheduler interval for the odds job is 360 minutes (6 hours), as defined in [app/services/scheduler.py](../app/services/scheduler.py).

The verification window did not include a full 6-hour wait, so the scheduler’s natural odds execution was not observed during the available runtime window.

Observed result:

```
Automatic refresh_odds Execution: NOT_OBSERVED
```

### 5. API-Football fetch / DB persistence

No natural execution was observed during the active verification window. Because the scheduler did not naturally execute `refresh_odds()` during the available observation period, there was no live API-Football fetch or persistence event to confirm end-to-end.

Result:

```
API-Football Odds Fetch: NOT_OBSERVED
Odds Sync: NOT_OBSERVED
Odds DB Persistence: NOT_OBSERVED
```

## Final Summary

```
Eligible 72h NS Match: YES

Worker:
RUNNING

Scheduler:
RUNNING

refresh_odds Registered:
YES

Automatic refresh_odds Execution:
NOT_OBSERVED

API-Football Odds Fetch:
NOT_OBSERVED

Odds Sync:
NOT_OBSERVED

Odds DB Persistence:
NOT_OBSERVED

FINAL STATUS:
BLOCKED
```

## Final Answer

When a real NS match exists within the next 72 hours, the existing local Worker and Scheduler are running and the `refresh_odds` job is registered, but the automatic odds refresh was not observed during the available runtime window because the scheduler interval is 6 hours. Therefore, the complete end-to-end runtime proof is currently BLOCKED rather than PASS.
