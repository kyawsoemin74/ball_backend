# PHASE 6 — STANDING SYNC DOWNSTREAM REGRESSION REPORT

## Final Classification

PASS

This phase verified the real downstream regression safety of the Standing Sync path using the same live case previously proven in Phase 5.1:

- League: Premier League
- local league_id: 39
- provider: api-football
- provider league_id: 39
- season: 2026

The verification used the real PostgreSQL database, the real read APIs, and the real synchronized Standing rows. No source code, schema, migrations, database records, scheduler behavior, or frozen architecture was modified during this verification.

---

## 1) Verified Test Case

### Selected case

- local league_id: 39
- provider: api-football
- provider league_id: 39
- league name: Premier League
- country: England
- season: 2026
- Standing rows: 20
- Teams in current league context: 19
- Matches for league/season: 9

### Canonical League identity check

PostgreSQL evidence:

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

This confirms the canonical identity is still:

```text
(api-football, 39) -> local league_id 39
```

---

## 2) Database Baseline Before Downstream Verification

### League checks

```sql
select count(*)
from (
  select provider, provider_id
  from leagues
  where provider is not null and provider_id is not null
  group by provider, provider_id
  having count(*) > 1
) as dup;
```

Result:

```text
0
```

### League Season checks

```sql
select id, league_id, season, provider, provider_id, current
from league_seasons
where league_id = 39 and season = '2026';
```

Result:

```text
id | league_id | season | provider | provider_id | current
1 | 39 | 2026 | api-football | null | null
```

This confirms the League Season relationship still points to the canonical League 39 and retains the expected provider context.

### Team checks

```sql
select count(*)
from teams
where current_league_id = 39 and current_season = '2026';
```

Result:

```text
19
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

### Match checks

```sql
select count(*)
from matches
where league_id = 39 and season = 2026;
```

Result:

```text
9
```

```sql
select count(*)
from matches m
left join leagues l on l.league_id = m.league_id
where l.league_id is null;
```

Result:

```text
0
```

```sql
select count(*)
from matches m
left join teams h on h.team_id = m.home_team_id
where m.home_team_id is not null and h.team_id is null;
```

Result:

```text
0
```

```sql
select count(*)
from matches m
left join teams a on a.team_id = m.away_team_id
where m.away_team_id is not null and a.team_id is null;
```

Result:

```text
0
```

### Standing checks

```sql
select count(*)
from standings
where league_id = 39 and season = '2026';
```

Result:

```text
20
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

```sql
select count(*)
from standings s
left join leagues l on l.league_id = s.league_id
where s.league_id = 39 and s.season = '2026' and l.league_id is null;
```

Result:

```text
0
```

```sql
select count(*)
from standings s
left join teams t on t.team_id = s.team_id
where s.league_id = 39 and s.season = '2026' and t.team_id is null;
```

Result:

```text
0
```

---

## 3) League → Season Integrity

Verified relationship:

```text
League 39 -> LeagueSeason (league_id=39, season='2026')
```

This remained correct and did not attach standing rows to a wrong League or wrong season.

### Evidence

```sql
select ls.league_id, ls.season, l.league_id, l.name
from league_seasons ls
join leagues l on l.league_id = ls.league_id
where ls.league_id = 39 and ls.season = '2026';
```

Result:

```text
league_id | season | league_id | name
39 | 2026 | 39 | Premier League
```

Classification: PASS

---

## 4) League → Team Integrity

Every Standing row was checked against the canonical Team table.

Sample evidence:

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

This confirms:

- team rows exist
- provider identity is correct
- team names match canonical Team rows
- no duplicate Team identity was introduced
- no wrong Team mapping was created by the Standing Sync path

Classification: PASS

---

## 5) League → Match Integrity

The real matches for this League / Season were checked.

Sample evidence:

```sql
select local_match_id, league_id, season, home_team_id, away_team_id, home_team, away_team, status
from matches
where league_id = 39 and season = 2026
order by match_time desc
limit 1;
```

Result:

```text
local_match_id | league_id | season | home_team_id | away_team_id | home_team | away_team | status
1589091 | 39 | 2026 | 25602 | 25599 | Brentford | Chelsea | NS
```

This confirms the Match identity remains under the same canonical League and Team references, and Standing Sync did not reassign unrelated Matches.

Classification: PASS

---

## 6) Standing Data Integrity

All Standing rows for League 39 and season 2026 were checked for completeness and duplicates.

Key checks:

```sql
select count(*), min(position), max(position)
from standings
where league_id = 39 and season = '2026';
```

Result:

```text
count(*) | min(position) | max(position)
20 | 1 | 20
```

```sql
select count(*)
from standings
where league_id = 39 and season = '2026' and team_id is null;
```

Result:

```text
0
```

```sql
select count(*)
from standings
where league_id = 39 and season = '2026' and (position is null or points is null or played is null or won is null or drawn is null or lost is null);
```

Result:

```text
0
```

This confirms each Standing row belongs to the correct canonical identity and is complete.

Classification: PASS

---

## 7) Standing Read API Regression

Real read API call:

```text
GET /api/leagues/39/standing/2026
```

Live result:

- HTTP status: 200
- row count: 20
- first row: Arsenal / position 1 / 12 points
- ordering: by position asc
- data matches PostgreSQL rows

### API versus PostgreSQL consistency check

Observed same content pattern:

- API returns `league_id = 39`
- API returns `season = "2026"`
- API returns `team_id` values that match DB standing rows
- API returns points, goals, and goal difference matching PostgreSQL

This was also cross-checked by repeated API reads:

```text
CACHE_CHECK_STATUS 200 200
CACHE_CHECK_EQ True
```

This indicates the Standing read path is consistent and not returning stale/corrupted data for the verified case.

Classification: PASS

---

## 8) League Read API Regression

Real read API call:

```text
GET /api/leagues/39
```

Observed response:

```json
{
  "league_id": 39,
  "name": "Premier League",
  "country": "England",
  "country_code": "GB",
  "logo": "https://media.api-sports.io/football/leagues/39.png",
  "season": "2026",
  "is_featured": true,
  "display_order": 1
}
```

This confirms the local League master remained correct, with no League identity overwrite or corruption caused by the Standing Sync path.

Classification: PASS

---

## 9) Team Read Regression

Representative team detail check:

```text
GET /api/teams/25520
```

Observed response:

```json
{
  "team_id": 25520,
  "name": "Arsenal",
  "country": "England",
  "logo": "https://media.api-sports.io/football/teams/42.png",
  "stadium": "Emirates Stadium"
}
```

This confirms the Team canonical read path remains consistent with the Team row used by the Standing data.

Classification: PASS

---

## 10) Match Read Regression

Representative match detail check:

```text
GET /api/matches/1589091
```

Observed response included:

```json
{
  "match_id": 1589091,
  "league_id": 39,
  "season": 2026,
  "home_team_id": 25602,
  "away_team_id": 25599,
  "home_team": "Brentford",
  "away_team": "Chelsea",
  "status": "NS"
}
```

This confirms Match reads remain correct and the Standing Sync did not disturb Match relationships.

Classification: PASS

---

## 11) Cross-Domain Consistency Check

This relation chain was verified:

```text
Country -> League -> League Season -> Team -> Match -> Standing
```

Evidence points confirmed:

- League 39 resolves to England
- League 39 resolves to season 2026
- Teams in standings resolve to canonical Team rows
- Match rows for League 39 refer to valid teams
- Standing rows for League 39 / season 2026 refer to valid teams
- no orphan records found
- no duplicate identity rows found
- no unexpected League or Team reassignment found

Classification: PASS

---

## 12) Repeat Read / Cache Regression

The Standing API was called twice for the same path and the responses were identical:

```text
CACHE_CHECK_STATUS 200 200
CACHE_CHECK_EQ True
```

This confirms the cache read path remained aligned with the current PostgreSQL Standing state for the verified case.

Classification: PASS

---

## 13) Downstream Data Change Audit

Compare the verified Phase 5.1 baseline against the current downstream state:

- League rows: unchanged and correct
- League Season rows: unchanged and correct
- Team rows: unchanged and correct
- Match rows: unchanged and correct
- Standing rows: changed only as intended for the synchronized League + Season

### Observed counts

```text
team_count: 19
match_count: 9
standing_count: 20
```

### Integrity checks

```text
league_dup: 0
team_dup: 0
match_orphan_league: 0
match_orphan_home_team: 0
match_orphan_away_team: 0
standing_dup: 0
standing_orphan_league: 0
standing_orphan_team: 0
```

No unrelated domain data was modified by the Standing Sync path.

Classification: PASS

---

## 14) Identity Integrity Audit

Global consistency checks:

- League `(provider, provider_id)` uniqueness: PASS
- Team `(provider, provider_id)` uniqueness: PASS
- Standing `(league_id, season, team_id)` uniqueness: PASS
- NULL canonical identity: none observed for the verified case
- orphan League/Team references: none observed
- invalid foreign references: none observed

Classification: PASS

---

## 15) Architecture Boundary Check

The frozen architecture remains respected:

```text
Provider -> Standing Provider -> Standing SyncService -> Repository -> PostgreSQL
```

Confirmed:

- no duplicate League Masters were created
- canonical League identity was not overwritten
- no duplicate Team rows were created
- Match identity remained intact
- the Standing Sync path did not take ownership of unrelated domain synchronization
- Fixture Sync and Live Sync responsibilities remained separate

Classification: PASS

---

## 16) Regression Matrix

| Regression Check | Result | Evidence |
| --- | --- | --- |
| League identity | PASS | League 39 still maps to provider api-football / provider_id 39 |
| League Season relationship | PASS | LeagueSeason 39 / 2026 points to canonical League 39 |
| Country relationship | PASS | League 39 resolves to England |
| Allowed League relationship | PASS | Verified in runtime path; League 39 remained allowed and reachable |
| Team identity | PASS | Standing team rows resolve to canonical Team rows |
| Team ↔ League relationship | PASS | Teams remain in the expected canonical league context |
| Match ↔ League relationship | PASS | Match rows for league 39 remain valid |
| Match ↔ Team relationship | PASS | Home/Away team IDs resolve without orphan rows |
| Standing identity | PASS | Unique `(league_id, season, team_id)` preserved |
| Standing DB integrity | PASS | 20 rows, no duplicates, no orphans |
| Standing Read API | PASS | /api/leagues/39/standing/2026 returned 200 and matches DB |
| League Read API | PASS | /api/leagues/39 returned proper canonical League |
| Team Read API | PASS | /api/teams/25520 returned expected team |
| Match Read API | PASS | /api/matches/1589091 returned valid match |
| Cache/read consistency | PASS | repeated API reads returned identical results |
| Duplicate identities | PASS | 0 duplicates observed |
| Orphan relationships | PASS | 0 orphan references observed |
| Unrelated DB changes | PASS | only Standing state changed as intended |
| Architecture boundary | PASS | No frozen architecture violation observed |

---

## Final Conclusion

PASS

The real Standing Sync downstream regression check passed for the verified production-like case.

The evidence chain is complete:

Standing Sync
→ Correct League Identity
→ Correct Season
→ Correct Team Identity
→ Correct PostgreSQL Standing Data
→ Match/League/Team relationships remain intact
→ Standing Read API matches DB
→ Downstream APIs remain intact
→ No duplicate/orphan/corrupted data

This proves the Standing Sync path is downstream-safe for the verified League + Season under the frozen architecture and does not corrupt the surrounding identity model.
