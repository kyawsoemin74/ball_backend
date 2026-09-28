# PHASE 7 - DOWNSTREAM VERIFICATION REPORT

**Verification date:** 2026-09-19  
**Mode:** read-only runtime, API, Provider, and PostgreSQL verification  
**Safety:** no source code, migrations, database rows, cache entries, or downstream records were modified.

## 1. Objective

Verify that the generic Provider League identity remains correct after Fixture Sync and across Match, Standing, Odds, and H2H boundaries.

## 2. Verified League Identity

The previously verified unequal-ID League was used:

| Field | Value |
|---|---|
| Local `league_id` | `1274` |
| Provider | `api-football` |
| Provider ID | `1` |
| Country ID | `2105094199` (`World`) |
| Allowed | Yes |
| Season | `2026` |

The canonical mapping remained exactly one row:

```text
(api-football, 1) -> local league_id=1274
```

## 3. Match Verification

Post-sync PostgreSQL state contained 104 Matches for local League 1274 and season 2026.

| Check | Result |
|---|---|
| `Match.provider` | `api-football` for all 104 |
| `Match.provider_fixture_id` | 104 distinct Provider fixture IDs |
| `Match.league_id` | `1274` for all selected rows |
| `Match.season` | `2026` |
| Home/Away Team references | Valid local Team IDs; 0 orphans |
| Duplicate Provider Match identities | 0 |
| Orphan Match -> League | 0 |
| Orphan Match -> Team | 0 |

The canonical Match identity is `(provider, provider_fixture_id)`. Provider ID `1` was not stored as the local League foreign key.

### API read-back

```text
GET /api/matches/1589105
HTTP 200
```

Returned identity fields included:

```text
match_id=1589105
league_id=1274
season=2026
home_team_id=25623
away_team_id=25624
has_standings=false
has_odds=false
has_h2h=false
```

**Match classification: PASS**

## 4. Standing Verification

### Database

| Check | Result |
|---|---:|
| Standing rows for `league_id=1274`, season `2026` | 0 |
| Duplicate `(league_id, season, team_id)` rows | 0 globally |
| Orphan Standing -> League | 0 |
| Orphan Standing -> Team | 0 |

### Provider and API

A direct real Provider request using external `league=1, season=2026` returned one response group with no Provider errors. No Standing sync was triggered because it would write data and this phase is verification-only.

```text
GET /api/leagues/1274/standing/2026
HTTP 404
{"detail":"Standings not found"}
```

**Standing classification: NOT VERIFIABLE**

Real Provider data exists, but no local Standing rows exist and the write-producing sync was not allowed.

## 5. Odds Verification

### Database

- Odds rows for Matches in League 1274 / season 2026: `0`;
- Matches with Odds rows: `0`;
- orphan Odds -> Match rows: `0`;
- duplicate Odds identity groups: `0`.

### Provider and API

For sample local Match `1589105`, stored Provider fixture ID `1489369` was used. The real Provider returned HTTP status 200 with `results=0` and an empty response.

```text
GET /api/matches/1589105/odds
HTTP 200
{"source":"database","odds":[],"cached":true,"match_started":true}
```

This is an empty database snapshot, not proof of successful Odds synchronization.

**Odds classification: NOT VERIFIABLE**

Reason: no real Provider Odds data was available for the sample fixture and no local Odds rows exist.

## 6. H2H Verification

The sample Match used these local and Provider Team identities:

| Role | Local Team ID | Provider Team ID |
|---|---:|---:|
| Home | 25623 | 16 |
| Away | 25624 | 1531 |

The real Provider H2H request used external Team IDs `16-1531` and returned two records with no Provider errors.

The H2H service resolves local Teams to Provider Team IDs for external requests and uses the local Team pair for persisted/read keys.

### Database and API

- H2H rows for local pair `25623-25624`: `0`;
- malformed H2H keys: `0`;
- H2H keys referencing missing local Teams: `0` globally;
- selected H2H API read-back:

```text
GET /api/matches/h2h/1589105/25623/25624
HTTP 404
{"detail":"H2H data not found"}
```

No H2H refresh was triggered because it would persist H2H and analytics data.

**H2H classification: NOT VERIFIABLE**

## 7. API Read-back Summary

| Endpoint | HTTP | Returned data | Database comparison | Result |
|---|---:|---|---|---|
| `GET /api/matches/1589105` | 200 | Match with local League/Team IDs | 104 valid Matches | PASS |
| `GET /api/leagues/1274/standing/2026` | 404 | No standings | 0 Standing rows | NOT VERIFIABLE |
| `GET /api/matches/1589105/odds` | 200 | Empty database Odds snapshot | 0 Odds rows | NOT VERIFIABLE |
| `GET /api/matches/h2h/1589105/25623/25624` | 404 | No H2H data | 0 selected H2H rows | NOT VERIFIABLE |

HTTP status alone was not treated as proof of correctness.

## 8. Database Integrity Audit

Read-only checks returned:

| Integrity check | Result |
|---|---:|
| Duplicate League Provider identity groups | 0 |
| Duplicate Match Provider identity groups | 0 |
| Duplicate Standing identity groups | 0 |
| Duplicate Odds identity groups | 0 |
| Orphan Match -> League | 0 |
| Orphan Match -> Home Team | 0 |
| Orphan Match -> Away Team | 0 |
| Orphan Standing -> League | 0 |
| Orphan Standing -> Team | 0 |
| Orphan Odds -> Match | 0 |
| H2H malformed keys | 0 |
| H2H keys missing local Teams | 0 |
| Selected Match rows mapped to another local League | 0 |
| Selected Match provider values other than `api-football` | 0 |

The absence of Standing, Odds, and selected H2H rows means those relationship checks are clean but do not prove persistence flows.

## 9. Cross-Domain Identity Trace

Verified chain:

```text
(api-football, 1)
      -> leagues.league_id=1274
      -> matches.league_id=1274
      -> matches.home_team_id / away_team_id
      -> teams.team_id=25623 / 25624
```

Current downstream database state:

```text
Standing.league_id: no rows for 1274 / 2026
Odds.fixture_id: no rows for selected Matches
H2H local team-pair key 25623-25624: no row
```

No observed downstream row replaced local League ID `1274` with Provider ID `1`.

## 10. Identity Conversion Audit

Verified correct behavior:

- Match persistence retains local `league_id=1274`;
- Provider fixture identity remains `provider_fixture_id`;
- Team read-back uses local Team IDs while Provider calls use Provider Team IDs;
- Odds reads use local Match identity and stored Provider fixture identity;
- H2H resolves local Team IDs to Provider Team IDs for external requests and localizes its persistence key.

No downstream database row stored Provider League ID `1` as `Match.league_id`.

A source-level observation is recorded without changing it: the Standing sync method currently passes its `league_id` argument directly to its Provider call. It was not runtime-executed here because that would write Standing rows.

## 11. Failure / Limitation Evidence

- Match: real data exists and local League/Team relationships pass.
- Standing: Provider data exists, but no local rows and no write-producing sync was allowed.
- Odds: Provider returned zero records for the sample fixture; no local rows exist.
- H2H: Provider returned two records, but no local row exists and no write-producing refresh was allowed.

No invalid or orphan downstream records were created by prior Fixture Sync or by Phase 7 read-backs.

## 12. Final Verification Matrix

| Domain | Provider data | Runtime/API data | DB relationship | Identity correct | Result |
|---|---|---|---|---|---|
| League | YES | YES | YES | YES | PASS |
| Match | YES, 104 fixtures | YES, 104 Matches | YES | YES | PASS |
| Standing | YES, one response group | No persisted sync/read-back | No rows, no orphans | Not proven | NOT VERIFIABLE |
| Odds | NO records for sample fixture | Empty DB snapshot | No rows, no orphans | Not proven | NOT VERIFIABLE |
| H2H | YES, two Provider records | No persisted H2H row | No selected row, no invalid keys | Not proven | NOT VERIFIABLE |

## 13. Findings

1. The canonical League identity survives Fixture Sync into all 104 Match rows.
2. Match-to-League and Match-to-Team relationships are valid and use local IDs.
3. No duplicate League, Match, Standing, or Odds identities were found.
4. No orphan Match, Standing, Odds, or structurally invalid H2H relationships were found.
5. Standing, Odds, and H2H persistence cannot be verified from the current real database state without triggering write-producing sync operations.
6. The Standing sync source path should receive a separate implementation audit because it accepts local League ID while directly calling the Provider; no fix was made in Phase 7.

## 14. Limitations

- Verification-only safety rules prevented running Standing or H2H refresh operations that would create records.
- The Provider returned no Odds records for the sample fixture.
- Raw Provider query parameters were not emitted by application logs.
- Only one real unequal-ID League was available for complete downstream verification.
- No database data was inserted, updated, deleted, or manually repaired during Phase 7.

## 15. Final Classification

# PASS WITH LIMITATIONS

The core downstream identity chain through Match passes with real PostgreSQL and API evidence:

```text
(api-football, 1)
  -> local league_id=1274
  -> 104 Match rows with league_id=1274
  -> valid local Team relationships
```

Standing, Odds, and H2H are `NOT VERIFIABLE` because the selected database has no persisted downstream records and executing their sync paths would modify data. No downstream identity corruption, duplicate canonical record, or orphan relationship was observed.
