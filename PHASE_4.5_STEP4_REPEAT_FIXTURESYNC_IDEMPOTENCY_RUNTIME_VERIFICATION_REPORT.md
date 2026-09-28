# PHASE 4.5 - Step 4 Runtime Verification: Repeat FixtureSync Idempotency

Verification date: 2026-09-23
Scope: isolated live Docker Worker/PostgreSQL runtime only. No production League, Team, or Match business data was modified.

## Final status

**A. VERIFIED**

All Step 4 acceptance criteria passed in the real Docker runtime using isolated temporary data. This report does not classify the wider Phase 4.5 as fully verified.

## Runtime health

`docker compose ps` confirmed:

```text
api       running healthy
postgres  running healthy
redis     running healthy
worker    running healthy
```

The recovery table and unrelated migration baseline were not changed during this Step 4 test.

## Temporary League 1282 cleanup

Cleanup was executed before the test. The command deleted zero rows because no League 1282 data remained. The follow-up verification returned:

```text
0|0|0|0
```

for League, Allowed League, Recovery, and Match rows associated with League 1282.

## Test setup

The test used a new temporary League and synthetic provider identities:

```text
fixture_id: 1234567
league_provider_id: 9901001
home_team_provider_id: 9902001
away_team_provider_id: 9902002
```

The fixture provider was patched to return one fixed fixture. The Team provider was patched to return exactly one valid response for each synthetic Team ID. The following production paths remained active and unchanged:

```text
FixtureSyncService.sync_full_season()
  -> TeamService.resolve_provider_teams()
  -> TeamSyncService.resolve_provider_teams()
  -> TeamSyncService.ensure_teams_exist()
  -> TeamSyncService.upsert_team()
  -> TeamRepository.upsert_by_provider_identity()
  -> TeamRepository.find_by_provider_identity()
  -> Match provider-identity upsert
```

Only the unrelated coach side effect was stubbed to prevent external coach-provider calls during this focused test.

## First FixtureSync execution

Runtime result:

```json
{
  "inserted": 1,
  "updated": 0,
  "failed": 0,
  "success": true,
  "total": 1,
  "first_match_count": 1
}
```

Team snapshot after the first execution:

```json
{
  "count": 2,
  "provider_ids": ["9902001", "9902002"],
  "team_ids": [75, 76],
  "rows": [
    {"team_id": 75, "provider": "api-football", "provider_id": "9902001", "name": "Runtime Home Team"},
    {"team_id": 76, "provider": "api-football", "provider_id": "9902002", "name": "Runtime Away Team"}
  ]
}
```

This proves that exactly one Home Team and one Away Team were resolved and persisted.

## Second identical FixtureSync execution

The exact same League, season, fixture, and Team provider responses were used again.

Runtime result:

```json
{
  "inserted": 0,
  "updated": 1,
  "failed": 0,
  "success": true,
  "total": 1,
  "second_match_count": 1,
  "total_matches_for_league": 1
}
```

Team snapshot after the second execution was unchanged:

```json
{
  "count": 2,
  "provider_ids": ["9902001", "9902002"],
  "team_ids": [75, 76]
}
```

## Acceptance criteria

| Criterion | Result |
|---|---|
| Docker API/Worker/PostgreSQL healthy | PASS |
| League 1282 completely removed and verified | PASS |
| Isolated temporary League and Team data only | PASS |
| Exactly one valid provider response per synthetic Team | PASS |
| Team resolution succeeds | PASS |
| Exactly one Home and Away Team resolved | PASS |
| Team provider identities remain unchanged | PASS |
| No duplicate Team rows created | PASS |
| Match persisted after first sync | PASS, `inserted=1` |
| Second sync creates no duplicate Match | PASS, `inserted=0`, `updated=1` |
| Final Match count remains exactly 1 | PASS |
| Temporary data cleaned and verified | PASS |

## Cleanup verification

The test cleanup removed Matches, LeagueSeasons, AllowedLeagues, Teams, and the temporary League. The final PostgreSQL verification returned:

```text
0|0|0|0|0|0
```

for League, Allowed League, Recovery, LeagueSeason, Match, and synthetic Team rows respectively.

## Scope statement

- No League Identity Recovery architecture was modified.
- No FixtureSync production logic was modified.
- No unrelated Alembic drift or indexes were modified.
- No Scheduler, Worker Restart, or Lock Contention tests were rerun.
- Step 4 is verified only for this isolated runtime scenario.
- Phase 4.5 is not classified as fully verified by this report alone.
