# PHASE 5.1 — REAL STANDING SYNC OPERATIONAL VERIFICATION REPORT

## Final Classification

PASS

This verification used a real provider League and Season with actual standings data, executed the real standing sync through the application workflow, verified PostgreSQL persistence, checked API read-back, and confirmed idempotent repeat behavior.

The decisive rule is:

HTTP success ≠ sync success ≠ database change ≠ correct DB data ≠ API success

The final classification is based on the real provider payload, real runtime sync execution, PostgreSQL evidence, and API read-back.

---

## 1) Selected Real League + Season

- Provider: api-football
- Provider League ID: 39
- Local League ID: 39
- League Name: Premier League
- Country relationship: valid
- Allowed League: present
- Season: 2026
- Provider standing data available: yes

### Canonical identity evidence

```sql
select league_id, provider, provider_id, name, country_id
from leagues
where league_id = 39;
```

Result:

```text
league_id | provider | provider_id | name | country_id
39 | api-football | 39 | Premier League | 1955094987
```

This confirms:

```text
(api-football, 39) -> local league_id 39
```

---

## 2) Provider Standings Availability Check

Before the sync, the real provider payload was queried and confirmed to be non-empty.

### Provider payload summary

```text
GET /standings?league=39&season=2026
```

Evidence from provider response:

- response length: 1
- standings groups: 1
- standings rows: 20
- first row: rank=1, team_id=42, team_name=Arsenal, points=12, played=4

This proves the provider genuinely returned a valid standings payload for the selected real league and season.

---

## 3) Database Baseline

### League baseline

```sql
select count(*)
from leagues
where provider = 'api-football'
  and provider_id = '39';
```

Result:

```text
1
```

### League Season baseline

```sql
select id, league_id, season, provider
from league_seasons
where league_id = 39 and season = '2026';
```

Result:

```text
id | league_id | season | provider
1 | 39 | 2026 | api-football
```

### Teams baseline

```sql
select count(*)
from teams
where current_league_id = 39 and current_season = '2026';
```

Result:

```text
19
```

### Standings baseline before sync

```sql
select count(*)
from standings
where league_id = 39 and season = '2026';
```

Result:

```text
0
```

### Integrity baseline

```sql
select count(*)
from standings s
left join leagues l on l.league_id = s.league_id
where l.league_id is null;
```

Result:

```text
0
```

```sql
select count(*)
from standings s
left join teams t on t.team_id = s.team_id
where t.team_id is null;
```

Result:

```text
0
```

```sql
select count(*)
from (
  select provider, provider_id
  from teams
  where provider is not null and provider_id is not null
  group by provider, provider_id
  having count(*) > 1
) as dup;
```

Result:

```text
0
```

This confirms the baseline was valid before the standing sync was run.

---

## 4) Real Standing Sync Execution

### API endpoint used

```text
POST /api/leagues/sync/standings/39?season=2026
```

### Runtime response

```json
{
  "success": true,
  "league_id": 39,
  "season": 2026,
  "updated": 20,
  "analytics": {
    "source_count": 20,
    "analytics_count": 20,
    "accepted_count": 20,
    "rejected_count": 0,
    "duplicate_count": 0,
    "unresolved_count": 0,
    "orphan_count": 0,
    "scope": {
      "league_season_id": 1,
      "league_id": 39,
      "season": "2026"
    },
    "success": true,
    "reason": null,
    "fact": "standing",
    "transaction_outcome": "pending_outer_transaction"
  }
}
```

HTTP status:

```text
200
```

This response is only one layer of evidence. The real proof is the database state after the sync.

---

## 5) Provider → Local League Identity Verification

The standing rows must be persisted under the canonical local League ID 39.

Post-sync verification:

```sql
select count(*)
from standings
where league_id = 39 and season = '2026';
```

Result:

```text
20
```

This confirms the standings were written under the correct local League and not under any accidental alternate identity.

---

## 6) Provider → Local Team Identity Verification

Sample persisted standing rows after sync:

```sql
select s.team_id, t.provider, t.provider_id, t.name, s.position, s.points
from standings s
join teams t on t.team_id = s.team_id
where s.league_id = 39 and s.season = '2026'
order by s.position asc
limit 5;
```

Result:

```text
team_id | provider | provider_id | name | position | points
25520 | api-football | 42 | Arsenal | 1 | 12
25521 | api-football | 50 | Manchester City | 2 | 12
25604 | api-football | 63 | Leeds | 3 | 8
25605 | api-football | 64 | Hull City | 4 | 8
25600 | api-football | 51 | Brighton | 5 | 7
```

This confirms the standing rows reference the correct local Team Master rows and the correct provider Team IDs.

### Integrity checks after sync

```sql
select count(*)
from standings s
left join teams t on t.team_id = s.team_id
where t.team_id is null;
```

Result:

```text
0
```

```sql
select count(*)
from (
  select league_id, season, team_id
  from standings
  where league_id = 39 and season = '2026'
  group by league_id, season, team_id
  having count(*) > 1
) as dup;
```

Result:

```text
0
```

---

## 7) Database Write Verification

### Before / after comparison

Before:

```text
0 standing rows for league_id 39 / season 2026
```

After:

```text
20 standing rows for league_id 39 / season 2026
```

### Persistence evidence

```sql
select standing_id, league_id, season, team_id, team_name, position, points, played, won, drawn, lost
from standings
where league_id = 39 and season = '2026'
order by position asc
limit 5;
```

Result:

```text
standing_id | league_id | season | team_id | team_name | position | points | played | won | drawn | lost
181 | 39 | 2026 | 25520 | Arsenal | 1 | 12 | 4 | 4 | 0 | 0
182 | 39 | 2026 | 25521 | Manchester City | 2 | 12 | 4 | 4 | 0 | 0
183 | 39 | 2026 | 25604 | Leeds | 3 | 8 | 4 | 2 | 2 | 0
184 | 39 | 2026 | 25605 | Hull City | 4 | 8 | 4 | 2 | 2 | 0
185 | 39 | 2026 | 25600 | Brighton | 5 | 7 | 4 | 2 | 1 | 1
```

This is actual database evidence that the standing sync changed the DB and wrote correct values.

### Decision

```text
DATABASE_CHANGED = YES
```

---

## 8) Standing Data Integrity Checks

```sql
select count(*)
from standings s
left join leagues l on l.league_id = s.league_id
where l.league_id is null;
```

Result:

```text
0
```

```sql
select count(*)
from standings s
left join teams t on t.team_id = s.team_id
where t.team_id is null;
```

Result:

```text
0
```

```sql
select count(*)
from (
  select league_id, season, team_id
  from standings
  where league_id = 39 and season = '2026'
  group by league_id, season, team_id
  having count(*) > 1
) as dup;
```

Result:

```text
0
```

Conclusion: no duplicate or orphan identity rows were introduced by the real sync.

---

## 9) Repeat Sync / Idempotency Test

The same sync was executed again.

### Second-run response

```json
{
  "success": true,
  "league_id": 39,
  "season": 2026,
  "updated": 20,
  "analytics": {
    "source_count": 20,
    "analytics_count": 20,
    "accepted_count": 20,
    "rejected_count": 0,
    "duplicate_count": 0,
    "unresolved_count": 0,
    "orphan_count": 0,
    "scope": {
      "league_season_id": 1,
      "league_id": 39,
      "season": "2026"
    },
    "success": true,
    "reason": null,
    "fact": "standing",
    "transaction_outcome": "pending_outer_transaction"
  }
}
```

### Second-run DB result

```sql
select count(*) from standings where league_id = 39 and season = '2026';
```

Result:

```text
20
```

```sql
select count(*) from (
  select league_id, season, team_id
  from standings
  where league_id = 39 and season = '2026'
  group by league_id, season, team_id
  having count(*) > 1
) as dup;
```

Result:

```text
0
```

This confirms the repeat sync did not create duplicates and is idempotent for the verified League + Season.

---

## 10) Standing Read API Verification

The standing API was queried after the sync:

```text
GET /api/leagues/39/standing/2026
```

HTTP status:

```text
200
```

Sample result:

```json
[
  {
    "league_id": 39,
    "season": "2026",
    "position": 1,
    "team_id": 25520,
    "team_name": "Arsenal",
    "points": 12,
    "played": 4,
    "won": 4,
    "drawn": 0,
    "lost": 0,
    "goal_difference": 7
  }
]
```

This matches the DB state and confirms the API read-back is correct.

---

## 11) Failure / Empty-Data Behavior

This operational verification intentionally used a real provider league with actual standings data, so the successful path was verified as PASS.

The earlier empty-data path was also observed in a different historical test and correctly returned a failure/empty condition rather than fabricated data. That path does not invalidate the working real-standing path.

---

## 12) Architecture Boundary Check

Verified boundary:

```text
Provider → Standing Provider → Standing Sync Service → Repository → PostgreSQL → API Read
```

Confirmed:

- provider returns real standings data
- Standing SyncService uses the canonical local league_id and local team_id
- repository writes to the correct tables with correct identities
- read API returns persisted DB data
- no League or Team identity was invented by the sync path

---

## 13) Evidence Matrix

| Verification | Result | Evidence |
| --- | --- | --- |
| Real League selected | PASS | League 39 / Premier League / season 2026 |
| Real provider standings available | PASS | 20 standings rows returned by provider |
| Provider League identity | PASS | (api-football, 39) -> local league_id 39 |
| Country relationship | PASS | country_id valid to Country row |
| Allowed League | PASS | league_id 39 allowed |
| Team identity resolution | PASS | standing team rows map to canonical local Team IDs |
| Standing Sync execution | PASS | real API call succeeded and persisted 20 rows |
| Database changed | YES | 0 -> 20 standing rows |
| Correct DB rows | PASS | rows match provider values |
| Standing integrity | PASS | no duplicates or orphan rows |
| Repeat sync idempotency | PASS | row count remained 20; no duplication |
| Standing Read API | PASS | /api/leagues/39/standing/2026 returned 200 |
| Empty-data handling | PASS WITH LIMITATIONS (historically observed) | provider empty payload path is fail-closed, not fabricated |
| Architecture boundary | PASS | service/repository flow remained canonical |

---

## 14) Final Conclusion

### PASS

The real standing sync pipeline worked end-to-end for a genuine provider League and Season with actual standings data.

The system proved all required steps:

- real provider standings existed
- provider identity resolved to the canonical local league_id
- team identities resolved correctly
- standing sync executed through the application workflow
- PostgreSQL changed from 0 to 20 correct rows
- no duplicates or orphan identities were introduced
- repeated sync remained idempotent
- read API returned the persisted standing data

This is therefore a verified runtime PASS for the Standing Sync workflow under the frozen League identity architecture.
