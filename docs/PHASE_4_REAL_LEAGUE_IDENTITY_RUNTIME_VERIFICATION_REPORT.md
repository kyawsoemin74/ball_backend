# PHASE 4 — REAL LEAGUE IDENTITY & FIXTURE SYNC RUNTIME VERIFICATION

## A. Test League

- Provider: api-football
- Provider ID: 1236
- Provider League: Como Cup
- Provider Country: World
- Local League ID: 1236

## B. Before Sync State

### League baseline

Before the real runtime verification, the local master did not contain the canonical provider identity for provider=api-football and provider_id=1236.

SQL evidence:

```sql
select count(*)
from leagues
where provider = 'api-football'
  and provider_id = '1236';
```

Result:

- count = 0

### Allowed League baseline

Before authorization, the selected league was not present in the allowed-league list.

SQL evidence:

```sql
select count(*)
from allowed_leagues
where league_id = 1236;
```

Result:

- count = 0

### Existing fixtures baseline

Before the sync, there were no matches for this local league.

SQL evidence:

```sql
select count(*)
from matches
where league_id = 1236;
```

Result:

- count = 0

### Summary

- before_league_count = 0
- before_allowed_league_state = NOT PRESENT
- before_fixture_count = 0

## C. Runtime Sync Evidence

### Real provider response

Provider metadata was queried through the real API-Football runtime path:

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

### HTTP result

The production sync endpoint was invoked using the actual app route:

```text
POST /api/matches/sync/season?league_id=1236&season=2026
```

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

- HTTP 200

This is not treated as proof of success by itself. It is only evidence that the path returned a successful response. The actual database evidence below confirms the persistence.

### Identity resolution proof

The canonical source of truth is provider + provider_id.

Observed runtime mapping:

```text
Provider:
api-football + 1236

        ↓

Local:
league_id = 1236
```

Evidence from PostgreSQL after sync:

```sql
select league_id, provider, provider_id, name, country_id
from leagues
where provider = 'api-football'
  and provider_id = '1236';
```

Result:

- league_id = 1236
- provider = api-football
- provider_id = 1236
- name = Como Cup
- country_id = 2105094199

### Logs

The runtime proof includes the actual sync output above and the database row above. No manual League insertion or direct repository bypass was used for the positive path.

## D. After Sync Database Evidence

### League master

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

Canonical row count:

- canonical_league_count = 1

### Country relationship

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

This confirms the resolved local league points to a real country row, and the provider country evidence was World.

### Allowed League relationship

```sql
select league_id
from allowed_leagues
where league_id = 1236;
```

Result:

- league_id = 1236

### Fixtures

```sql
select count(*)
from matches
where league_id = 1236;
```

Result:

- count = 9

Sample rows:

```text
match_id | provider_fixture_id | league_id | status
1589092 | 1607535 | 1236 | FT
1589093 | 1607536 | 1236 | PEN
1589094 | 1607537 | 1236 | PEN
...
```

This proves that the provider fixtures were persisted with the correct local league_id = 1236 and not assigned elsewhere.

## E. Idempotency Result

The exact same sync was run again against the same provider identity.

Observed result:

```json
{
  "success": true,
  "inserted": 0,
  "updated": 9,
  "total": 9,
  "failed": 0
}
```

Database verification:

```sql
select count(*)
from leagues
where provider = 'api-football'
  and provider_id = '1236';
```

Result:

- canonical_league_count_after_repeat = 1

```sql
select count(*)
from matches
where league_id = 1236;
```

Result:

- fixture_count_before_repeat = 9
- fixture_count_after_repeat = 9

This confirms idempotent behavior for the same provider+provider_id mapping.

## F. Failure Behavior

To verify the fail-closed path, a genuinely unresolved provider identity was also exercised without special-casing any league.

Example:

- provider = api-football
- provider_id = 98
- local row existed but provider_id was NULL

Real runtime sync response:

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

This satisfies the Phase 3 contract: it does not report misleading success with zero writes.

## G. Regression Result

Relevant verification performed:

1. Focused regression suite:
   - pytest tests/test_league_sync.py -q
   - Result: 9 passed, 0 failed
2. Real provider league identity verification:
   - provider + provider_id resolved to one canonical league_id = 1236
3. Real sync verification:
   - fixture count changed from 0 to 9 for the selected league
4. Repeat sync verification:
   - same provider + provider_id remained mapped to the same local league_id
5. Fail-closed verification:
   - unresolved provider identity responded with success=false and zero DB writes

No unrelated modules were modified during this verification phase.

## H. Final Classification

RUNTIME VERIFICATION PASS

## Final Evidence Summary

Stage-by-stage classification:

- Provider response: PASS
- Provider identity extraction: PASS
- League Master resolution: PASS
- Country resolution: PASS
- Allowed League resolution: PASS
- Fixture processing: PASS
- Database persistence: PASS
- Repeat sync / idempotency: PASS
- Failure behavior for unresolved identity: PASS

## Frozen Design Compliance

This runtime verification was checked against the frozen rules from the Phase 1 and Phase 2 documents. The implementation followed the design contract:

- exact provider identity resolution by provider + provider_id
- one canonical local league_id for the same provider identity
- local allow-list was treated as eligibility, not identity
- unresolved provider identity did not silently pass as success
- fixture processing only proceeded when the canonical mapping existed

The repository did not contain a standalone Phase 3 report file at runtime, so the implementation was validated directly against the frozen design and the live production execution path.
