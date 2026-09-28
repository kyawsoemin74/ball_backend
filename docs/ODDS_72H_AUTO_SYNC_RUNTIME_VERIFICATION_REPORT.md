# ODDS 72-HOUR AUTO SYNC — RUNTIME VERIFICATION REPORT

## 1. Objective

Verify whether the existing Odds scheduler automatically fetches and persists odds for a real eligible NS match within the next 72 hours, without manually invoking refresh_odds() or altering the database.

Scope was limited to the live DB + existing scheduler path:

- DB NS match
- within 72 hours
- existing Odds scheduler
- automatic refresh_odds()
- API-Football odds fetch
- PostgreSQL odds persistence

## 2. Eligible NS Match Evidence

A live database query was executed against PostgreSQL using the same eligibility logic the scheduler relies on:

- status = NS
- match_time >= NOW()
- match_time <= NOW() + INTERVAL '72 hours'
- provider_fixture_id IS NOT NULL

Live result:

- Eligible NS matches: 20

Sample rows:

- match_id: 1570386
  - provider_fixture_id: 1570386
  - league: La Liga
  - season: 2026
  - match_time: 2026-09-17 17:00:00+00:00
  - status: NS
  - current_odds_count: 0

- match_id: 1570390
  - provider_fixture_id: 1570390
  - league: La Liga
  - season: 2026
  - match_time: 2026-09-17 19:30:00+00:00
  - status: NS
  - current_odds_count: 0

- match_id: 1589030
  - provider_fixture_id: 20001
  - league: Phase3 League 20001
  - season: 2026
  - match_time: 2026-09-18 07:05:02.893832+00:00
  - status: NS
  - current_odds_count: 1

This confirms there are real eligible NS matches inside the 72-hour window, and some of them have zero existing odds rows.

## 3. Scheduler Evidence

The live worker process was started via the project worker path:

- command: `.\.venv\Scripts\python.exe worker.py`

Observed live runtime logs:

- `Worker dependency health: postgres=True redis=True`
- `Starting dedicated scheduler service`
- `Added job "Refresh Odds Snapshots" to job store "default"`
- `Scheduler started`
- `SCHEDULER_STARTED jobs=['sync_live_matches', 'reconcile_recent_non_terminal', 'sync_daily_fixtures', 'repair_daily_matches', 'refresh_standings', 'refresh_odds', 'refresh_lineups', 'refresh_events', 'refresh_statistics']`

This proves the scheduler is running in the actual worker process and the odds job is registered.

Scheduler summary:

- Odds Scheduler Job: registered
- Job ID: refresh_odds
- Job name: Refresh Odds Snapshots
- Interval: IntervalTrigger(minutes=360) = 6 hours
- Worker: running
- Scheduler: running

## 4. Automatic Execution Evidence

The scheduler was running, but the configured odds refresh interval is 6 hours.

Observation window used for verification:

- under 30 minutes

No scheduled odds execution was observed during that window.

Evidence:

- no `ODDS_REFRESH_START` log emitted
- no `ODDS_REFRESH_SYNCED` log emitted
- no provider request log emitted
- no odds persistence change observed in PostgreSQL

Therefore:

- Automatic Odds Job Execution: NOT_OBSERVED

## 5. Provider Request Evidence

No live provider request from the scheduler was observed during this verification window.

This means:

- Provider Odds Fetch: NOT_OBSERVED

No HTTP request to the provider endpoint could be tied to the real scheduler execution in this session, so there is no runtime proof that:

- local match_id -> provider_fixture_id -> API-Football odds endpoint was called
- a usable odds payload was returned
- bookmaker/market rows were received from the provider

## 6. Database Before/After Evidence

Before the observed window, a real eligible NS match existed with zero odds rows, for example:

- match_id: 1570386
- current_odds_count: 0

No scheduled execution was observed, so there was no after-state attributable to the scheduler.

Database evidence summary:

- odds_count_before for candidate: 0
- odds_count_after for same candidate: unchanged at 0 during the verification window

No automatic database persistence was observed.

## 7. Automatic Sync Result

The runtime verification did not observe the expected end-to-end automatic flow:

- DB eligible NS match exists
- scheduler is running
- odds job is registered
- scheduled odds execution did not fire within the current observation window
- no provider fetch
- no odds persistence

This means the automatic sync is not proven to be working in the live runtime under the current observation window.

## 8. Idempotency Result

No second scheduled odds execution was observed, so no live idempotency event occurred during the verification period.

No duplicate canonical odds rows were observed, but this is because the scheduled sync path never executed in the live session.

## 9. Final Classification

Final Classification: BLOCKED

### Reason

The scheduler is running and the odds job is registered, and real 72-hour eligible NS matches exist in PostgreSQL. However, the configured interval is 6 hours and the verification window was too short to observe a live scheduled execution. No automatic provider fetch, odds sync, or DB persistence was observed in the real runtime during this verification window.

This satisfies the "BLOCKED" condition:

- eligible NS match exists
- scheduler is running
- scheduled execution could not be observed within the available window

## 10. Evidence Summary

- 72h NS Candidate: PASS
- Scheduler Running: PASS
- Automatic Odds Job Execution: NOT_OBSERVED
- Provider Odds Fetch: NOT_OBSERVED
- Odds DB Persistence: NOT_OBSERVED
- Automatic End-to-End Sync: BLOCKED

## Final decision

The existing Odds auto-sync path is configured and live in the scheduler, but it is not proven to have executed automatically during this runtime verification window. The current status is BLOCKED, not PASS.
