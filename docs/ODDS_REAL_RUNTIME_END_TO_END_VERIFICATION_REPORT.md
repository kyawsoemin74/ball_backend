# Odds Real Runtime End-to-End Verification Report

## Objective

Verify the actual production runtime flow for odds refresh using one real in-window NS match:

```
72h NS match
  ↓
Scheduler
  ↓
refresh_odds()
  ↓
API-Football Odds
  ↓
Odds DB
```

This was done without modifying source code, without changing DB data, without manually triggering the scheduler, and without manually calling refresh_odds().

## Step 1 — Find a Real Eligible Match

Live query executed against the current database with the rule:

- status = NS
- match_time >= NOW()
- match_time <= NOW() + 72 hours
- provider_fixture_id IS NOT NULL

Result:

- Eligible matches found: 10
- Real example records:
  - match_id: 1570386, provider_fixture_id: 1570386, status: NS, match_time: 2026-09-17T17:00:00+00:00, league: La Liga, current_odds_count: 0
  - match_id: 1570390, provider_fixture_id: 1570390, status: NS, match_time: 2026-09-17T19:30:00+00:00, league: La Liga, current_odds_count: 0
  - match_id: 1589030, provider_fixture_id: 20001, status: NS, match_time: 2026-09-18T07:05:02.893832+00:00, league: Phase3 League 20001, current_odds_count: 1

This satisfies the required real-match eligibility gate.

## Step 2 — Verify Scheduler

Verified scheduler state from the actual runtime scheduler object in [app/services/scheduler.py](../app/services/scheduler.py):

- live_scheduler.is_running = False
- scheduler job IDs = []
- refresh_odds registered = False
- next run time = None

This confirms the production scheduler is not currently running in the active runtime environment.

## Step 3 — Wait for Natural Scheduled Execution

No natural scheduled execution was observed because the scheduler is not active. The scheduled odds job was not started by the runtime scheduler, and there was no automatic run to observe.

Observed evidence:

- Worker process exists
- uvicorn process exists
- scheduler runtime object is not started
- refresh_odds job is absent from the running scheduler

## Step 4 — Verify API-Football Odds

Not observed because the scheduler never executed the odds refresh job.

Result:

- Provider request = NOT_OBSERVED
- HTTP response = NOT_OBSERVED
- Odds data returned = NOT_OBSERVED

## Step 5 — Verify Odds DB Persistence

Not observed because no automatic odds refresh ran under the live scheduler.

Result:

- odds_count_before = not measured because no scheduled execution occurred
- odds_count_after = not measured because no natural execution occurred
- Odds Sync pipeline = NOT_OBSERVED
- PostgreSQL odds persistence = NOT_OBSERVED

## Final Decision

### Summary

- Eligible 72h NS Match: PASS
- Scheduler Running: FAIL
- Automatic refresh_odds(): NOT_OBSERVED
- API-Football Odds Fetch: NOT_OBSERVED
- Odds Sync: NOT_OBSERVED
- Odds DB Persistence: NOT_OBSERVED

### Final Status

BLOCKED

Reason:

A real eligible NS match exists within 72 hours, but the actual runtime scheduler is not running and the refresh_odds job is not registered/executing in the live environment. Therefore the production automatic odds refresh path could not be proven end-to-end during the current runtime window.

## Final Report Summary

```
Eligible 72h NS Match: PASS
Scheduler Running: FAIL
Automatic refresh_odds(): NOT_OBSERVED
API-Football Odds Fetch: NOT_OBSERVED
Odds Sync: NOT_OBSERVED
Odds DB Persistence: NOT_OBSERVED

FINAL STATUS: BLOCKED
```
