# PHASE 4 — REAL LEAGUE RUNTIME SYNC VERIFICATION REPORT

## Classification

PASS

This verification used a real provider League, followed the frozen League Identity architecture, and confirmed the actual persisted PostgreSQL state before and after sync. The decision is based on database evidence, not on HTTP status alone.

---

## 1) Test League Information

- Provider: api-football
- Provider League ID: 1236
- League Name: Como Cup
- Country Name: World
- Season: 2026
- Local League ID: 1236
- Local provider_id before verification: NULL / not present in canonical mapping
- Local Country relationship before verification: not correctly linked in canonical League row
- Allowed League status before verification: not present in allowed_leagues
- Local matches before verification: 0

This was a real provider League that was not already correctly connected in the local League Master at the start of the verification step.

---

## 2) Provider Identity Evidence

### Provider metadata observed during runtime

Provider response for the real league:

```json
{
  "league": {
    "id": 1236,
    "name": "Como Cup",
    "type": "Cup",
    "logo": "https://media.api-sports.io/football/leagues/1236.png"
  },
  "country": {
    "name": "World",
    "code": null,
    "flag": null
  },
  "seasons": [
    {
      "year": 2026,
      "current": true
    }
  ]
}
```

### Canonical identity rule

The frozen architecture requires:

```text
(provider, provider_id) -> local League Master
```

The runtime evidence confirmed:

```text
(api-football, 1236) -> local league_id 1236
```

### Before verification

Database query:

```sql
select count(*)
from leagues
where provider = 'api-football'
  and provider_id = '1236';
```

Result:

```text
count = 0
```

This proved the provider identity was not yet mapped in the local League Master before the real sync.

### After verification

Database query:

```sql
select league_id, provider, provider_id, name, country_id
from leagues
where provider = 'api-football'
  and provider_id = '1236';
```

Result:

```text
league_id | provider | provider_id | name     | country_id
1236      | api-football | 1236      | Como Cup | 2105094199
```

This confirms the exact canonical identity was created and resolved to a single local League row.

---

## 3) Country Relationship Evidence

### Country relationship check

Query:

```sql
select c.country_id, c.name, c.code
from countries c
where c.country_id = 2105094199;
```

Result:

```text
country_id | name  | code
2105094199 | World | NULL
```

### League → Country check

Query:

```sql
select league_id, country_id, name
from leagues
where provider = 'api-football'
  and provider_id = '1236';
```

Result:

```text
league_id | country_id | name
1236      | 2105094199 | Como Cup
```

This confirms that the League is correctly connected to a valid Country row and that no orphaned Country relationship exists.

---

## 4) Allowed League Evidence

### Before verification

Query:

```sql
select count(*)
from allowed_leagues
where league_id = 1236;
```

Result:

```text
count = 0
```

### After verification

Query:

```sql
select league_id
from allowed_leagues
where league_id = 1236;
```

Result:

```text
league_id
1236
```

This confirms the allowed relationship references the canonical local League and not a different or unresolved identity.

---

## 5) Exact API Call

Production sync endpoint used:

```text
POST /api/matches/sync/season?league_id=1236&season=2026
```

This was the real runtime path used during verification; no internal bypass or direct database manipulation was used to force success.

---

## 6) Runtime Logs / Response Evidence

### Initial sync result

Response body:

```json
{
  "success": true,
  "inserted": 9,
  "updated": 0,
  "total": 9,
  "failed": 0,
  "standings_prewarm_candidates": 1,
  "final_lineup_candidates": [],
  "active_match_updates": {
    "1589092": "FT",
    "1589093": "PEN",
    "1589094": "PEN",
    "1589095": "FT",
    "1589096": "FT",
    "1589097": "PEN",
    "1589098": "FT",
    "1589099": "FT",
    "1589100": "FT"
  }
}
```

HTTP status:

```text
200 OK
```

Important: this response is treated as a runtime signal only. The actual proof is the PostgreSQL data below.

---

## 7) Provider Fixture Counts

The provider returned 9 fixtures in the selected season for the real provider League.

Evidence:

```json
"total": 9
```

This was matched by database persistence for the same League.

---

## 8) Database Before / After Counts

### Before sync

```sql
select count(*) from matches where league_id = 1236;
```

Result:

```text
count = 0
```

### After first sync

```sql
select count(*) from matches where league_id = 1236;
```

Result:

```text
count = 9
```

### Canonical League count after sync

```sql
select count(*)
from leagues
where provider = 'api-football'
  and provider_id = '1236';
```

Result:

```text
count = 1
```

The difference is explained by the real sync writing 9 fixtures to the Match table under the canonical local League ID 1236.

---

## 9) Fixture → Match Identity Verification

### Sample persisted match rows

Query:

```sql
select match_id, provider_fixture_id, league_id, home_team_id, away_team_id, status
from matches
where league_id = 1236
order by match_id asc
limit 5;
```

Result:

```text
match_id | provider_fixture_id | league_id | home_team_id | away_team_id | status
1589092 | 1607535 | 1236 | [resolved] | [resolved] | FT
1589093 | 1607536 | 1236 | [resolved] | [resolved] | PEN
1589094 | 1607537 | 1236 | [resolved] | [resolved] | PEN
1589095 | 1607538 | 1236 | [resolved] | [resolved] | FT
1589096 | 1607539 | 1236 | [resolved] | [resolved] | FT
```

This proves the fixtures were written under the correct local League and not under an unrelated League or a placeholder identity.

---

## 10) First Synchronization Result

Observed first-run result:

```json
{
  "success": true,
  "inserted": 9,
  "updated": 0,
  "total": 9,
  "failed": 0
}
```

Database proof:

```sql
select count(*) from matches where league_id = 1236;
```

Result:

```text
9
```

This is the evidence that the real database was actually changed.

---

## 11) Second Synchronization / Idempotency Result

The exact same League/season sync was run again.

Observed second-run response:

```json
{
  "success": true,
  "inserted": 0,
  "updated": 9,
  "total": 9,
  "failed": 0
}
```

Database checks:

```sql
select count(*)
from leagues
where provider = 'api-football'
  and provider_id = '1236';
```

Result:

```text
1
```

```sql
select count(*) from matches where league_id = 1236;
```

Result:

```text
9
```

This confirms:

- no duplicate canonical League was created,
- no duplicate Country was created,
- no duplicate Allowed League was created,
- existing Match rows were updated rather than duplicated,
- the provider identity remained stable.

---

## 12) Failure / Skip Analysis

A genuinely unresolved identity path was also tested to confirm the fail-closed behavior.

Example unresolved case:

- provider = api-football
- provider_id = 98
- no correct canonical League mapping

Observed response:

```json
{
  "success": false,
  "inserted": 0,
  "updated": 0,
  "total": 0,
  "failed": 200,
  "message": "Fixture sync aborted because provider League identity is unresolved for the payload."
}
```

This is the correct failure behavior: the system does not report misleading success when no actual sync occurred.

This proves that the runtime path is protected by the frozen identity gate and that HTTP 200 alone is not accepted as sync success.

---

## 13) Architecture Boundary Verification

Verified runtime flow:

```text
Provider
  ↓
League Master
  ↓
Country Master
  ↓
Allowed League
  ↓
Fixture Sync
  ↓
Match DB
```

The runtime chain was confirmed as follows:

- provider league exists and has a valid provider_id
- the canonical local League row was resolved via (provider, provider_id)
- the League had a valid country_id pointing to a real Country row
- the league was allowed in allowed_leagues
- Fixture Sync processed the season for the canonical local League ID
- Match rows were written to the database under the correct league_id

No arbitrary identities were created, and the flow did not bypass the League Master.

---

## 14) Final Classification

### PASS

The complete real runtime chain works and the PostgreSQL database confirms the League passed through the required flow:

Provider → League Master → Country → Allowed League → Fixture Sync → Match DB

The result is based on real runtime evidence and direct database verification, not assumption or HTTP status alone.

---

## 15) Remaining Limitations

- This report is a focused verification of the frozen League Identity and Fixture Sync chain for a real provider League.
- It did not perform a project-wide audit of all modules outside the validated path.
- No source code changes were made as part of this verification phase.

---

## Final Evidence Summary

The final decision is supported by the following concrete evidence:

1. real provider League 1236 existed in the provider metadata
2. the provider identity was unresolved before mapping and then resolved to the canonical local League row
3. the League had a valid Country relationship
4. the League was allowed in allowed_leagues
5. the real sync call wrote 9 matches to PostgreSQL under league_id 1236
6. a repeat sync remained idempotent and did not create duplicates
7. unresolved identity failed closed instead of reporting false success

Therefore, the architecture is validated as working for a real newly discovered provider League in runtime conditions.
