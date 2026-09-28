# Odds Auto Sync — Real Runtime Verification Report

## 1. Scheduler runtime status

- Running: Yes
- Scheduler owner: dedicated worker process started by `worker.py`
- Runtime evidence: the live worker process was started and logged:
  - `Worker dependency health: postgres=True redis=True`
  - `Starting dedicated scheduler service`
  - `Added job "Refresh Odds Snapshots" to job store "default"`
  - `Scheduler started`
  - `SCHEDULER_STARTED jobs=['sync_live_matches', 'reconcile_recent_non_terminal', 'sync_daily_fixtures', 'repair_daily_matches', 'refresh_standings', 'refresh_odds', 'refresh_lineups', 'refresh_events', 'refresh_statistics']`
- Process evidence: multiple live `python.exe` worker processes were present, including:
  - `D:\fover_backend\.venv\Scripts\python.exe worker.py`
- Conclusion: the scheduler is running in the real process environment and is not merely present in source code.

## 2. Odds job name

- Job name: `Refresh Odds Snapshots`
- Job ID: `refresh_odds`
- Registered by: `LiveUpdateScheduler.start()` in `app/services/scheduler.py`

## 3. Odds job interval

- Current interval: `IntervalTrigger(minutes=360)`
- Effective cadence: every 6 hours
- This is the configured schedule for the odds refresh job in `app/services/scheduler.py`.

## 4. Eligible match used

Live database query found eligible odds-refresh candidates under the existing rules:

- `match_id`: `1570386`
- `provider_fixture_id`: `1570386`
- `status`: `NS`
- `kickoff`: `2026-09-17 17:00:00+00:00`
- `league`: `La Liga`
- `season`: `2026`
- `current Odds row count`: `0`

Additional candidates also existed, e.g. match IDs `1589030`, `1589034`, `1589036`, `1589041`, and `1589042`, with at least one rows count set to `1` or higher. The selected verification candidate was a real, live record that satisfied the scheduler’s eligible-match query conditions.

## 5. Pre-sync DB state

Pre-sync snapshot for the eligible match:

- `local_match_id`: `1570386`
- `provider_fixture_id`: `1570386`
- `status`: `NS`
- `match_time`: `2026-09-17 17:00:00+00:00`
- `league_name`: `La Liga`
- `season`: `2026`
- `odds_count`: `0`
- `latest_odds_ts`: `NULL`

This was measured directly from PostgreSQL before any scheduled odds job was observed to run.

## 6. Scheduled execution evidence

- Observed runtime scheduler startup: Yes
- Observed scheduled odds execution: No
- Reason: the configured odds refresh cadence is 6 hours, and the verification window is much shorter than the interval.
- No `ODDS_REFRESH_START`, `ODDS_REFRESH_SYNCED`, `ODDS_REFRESH_COMPLETE`, or provider-call logs were observed during this live verification window.
- This means the code path is configured and running, but no actual scheduled execution was seen in the current session.

## 7. Provider request evidence

- Provider request observed during this session: No
- Reason: the scheduler was running but the job had not reached its configured due time.
- Therefore, no live odds provider HTTP request, response payload, bookmaker data, or market payload was captured during the verification window.

## 8. Post-sync DB state

- Post-sync database change observed: No
- Because no scheduled odds execution occurred, there was no database change attributable to the live scheduler run.

## 9. Duplicate check

- Duplicate canonical odds rows at runtime: Not observed
- Reason: no scheduled execution occurred, so no canonical odds insert/update path ran in the live environment during this session.
- The canonical identity is still the project-defined business key of:
  - `local_match_id`
  - `bookmaker`
  - `market`
  - `selection`
  - `handicap / point`

## 10. Automatic refresh evidence

- Observed: No
- Reason: the odds job interval is 6 hours; verification did not span that duration.
- The implementation does include the logic for refresh cadence and age gating, but runtime proof was not available within the current session.

## 11. Automatic stop behavior

- Verified at runtime: No
- Source/runtime evidence available: the scheduling logic in `app/services/scheduler.py` stops when match status enters the stop set (`LIVE`, `HT`, `FT`, `AET`, `PEN`, `CANC`, `ABD`, `AWD`, `WO`) and also when the match is outside the allowed time window.
- But no live scheduled job fired, so runtime confirmation of that stop behavior was not possible during this verification window.

## 12. Failure behavior

- No destructive experiments or provider outages were performed against production.
- No partial-write failure at runtime was observed.
- The existing implementation is designed to classify provider failures and avoid partial persistence in the normal service flow; however, no live provider-failure event was observed in the active runtime.

## 13. Tests

Focused scheduler/lock tests passed:

- `tests/test_scheduler_runtime.py`
- `tests/test_odds_lock_alignment.py`
- `tests/test_lineup_lock_alignment.py`

Command used:

```powershell
Set-Location 'd:\fover_backend'; .\.venv\Scripts\python.exe -m pytest tests/test_scheduler_runtime.py tests/test_odds_lock_alignment.py tests/test_lineup_lock_alignment.py -q
```

Result:

- 7 passed
- 0 failed
- 3 warnings

These tests prove the scheduler contract and lock ordering are correct in code, but they do not prove a live scheduled odds execution occurred.

## 14. Code changes

- None

No production code change was required, because the implementation is already present and verification is blocked by the scheduler cadence rather than a code defect.

## 15. Database changes

- None

No odds rows were created or updated during this verification window because no scheduled odds execution was observed.

## 16. Final classification

Final classification: `BLOCKED`

Reason:

- The scheduler is running in the real worker process.
- The odds job is registered and configured.
- A live eligible match exists in PostgreSQL.
- However, no scheduled odds execution was observed during this session because the configured refresh interval is 6 hours, which is longer than the available verification window.

This does not satisfy the higher standard required for `WORKING`: a live scheduled execution, provider request, and DB mutation must all be observed.

## Final decision

Odds Auto Sync is not proven to be actively running at runtime in this verification window. The current status is therefore `BLOCKED`, not `WORKING`, because the scheduler is configured and live, but the execution interval prevents direct runtime proof within the short observation period.
