# PHASE 6 - REPEAT SYNC / IDEMPOTENCY VERIFICATION REPORT

**Verification date:** 2026-09-19  
**Scope:** repeated real API synchronization through generic local-League-to-Provider identity resolution  
**Safety:** no source code, architecture, migration, manual Match row, or manual identity change was made.

## 1. Objective

Verify that repeated synchronization preserves:

```text
local League identity
  -> Provider identity
  -> Provider fixtures
  -> canonical Match identity
  -> local Match.league_id
```

The normal HTTP endpoint and real PostgreSQL database were used.

## 2. Real League Selected

| Field | Value |
|---|---|
| Local `league_id` | `1274` |
| Provider | `api-football` |
| Provider `provider_id` | `1` |
| Country ID | `2105094199` |
| Allowed/Ready | Yes |
| Season | `2026` |

This is an unequal identity relationship:

```text
(api-football, provider_id=1) -> local league_id=1274
```

No League-specific logic was added or used.

## 3. Provider Identity

The League Master remained unchanged:

```text
provider = api-football
provider_id = 1
local league_id = 1274
country_id = 2105094199
```

Fixture Sync resolves the local ID through League Master before requesting Provider fixtures. Match persistence continues to use local `league_id=1274`.

The canonical Match identity is the schema unique pair:

```text
(provider, provider_fixture_id)
```

## 4. Database Baseline

Read-only PostgreSQL state immediately before the two Phase 6 requests:

| Check | Baseline |
|---|---:|
| League 1274 rows | 1 |
| Provider ID | `1` |
| Country ID | `2105094199` |
| Allowed row | 1 |
| Matches for League 1274 | 104 |
| Matches for League 1274 / season 2026 | 104 |
| Distinct selected Provider fixture IDs | 104 |
| Duplicate League identity groups | 0 |
| Duplicate Match identity groups | 0 |
| Orphan Match -> League rows | 0 |
| Orphan home Team references | 0 |
| Orphan away Team references | 0 |
| Invalid League -> Country references | 0 |
| Distinct local League IDs for selected fixtures | 1 |

The 104 baseline Matches were created by the successful Phase 4.2 runtime verification. Phase 6 therefore measured repeated update behavior without deleting existing data.

## 5. First Sync Result

Exact request:

```text
POST /api/matches/sync/season?league_id=1274&season=2026
```

HTTP status: `200`

```json
{
  "success": true,
  "inserted": 0,
  "updated": 104,
  "total": 104,
  "failed": 0,
  "standings_prewarm_candidates": 1
}
```

All 104 Provider fixtures were recognized as existing canonical Matches and updated. No new Match identity was inserted.

## 6. First Sync Database Evidence

After the first Phase 6 request:

- Match count remained `104`;
- distinct Provider fixture IDs remained `104`;
- every selected Match retained `league_id=1274`;
- the League identity remained one row;
- no duplicate or orphan relationship appeared.

The fresh insert transition for this same real identity was proven in Phase 4.2:

```text
Phase 4.2 first sync: inserted=104, updated=0
Phase 4.2 second sync: inserted=0, updated=104
```

No data was deleted to recreate an empty first-run state.

## 7. Second Sync Result

The exact same request was executed again without modifying the database:

```text
POST /api/matches/sync/season?league_id=1274&season=2026
```

HTTP status: `200`

```json
{
  "success": true,
  "inserted": 0,
  "updated": 104,
  "total": 104,
  "failed": 0,
  "standings_prewarm_candidates": 1
}
```

## 8. Second Sync Database Evidence

After the second request:

- Match count remained `104`;
- distinct Provider fixture IDs remained `104`;
- duplicate Match identity groups remained `0`;
- all selected fixtures still mapped to one local League ID, `1274`;
- no orphan League or Team references were created.

## 9. Before/After Comparison

| Measure | Before Phase 6 | After Sync #1 | After Sync #2 |
|---|---:|---:|---:|
| League identity rows | 1 | 1 | 1 |
| Allowed row | 1 | 1 | 1 |
| Matches for 1274 / 2026 | 104 | 104 | 104 |
| Distinct Provider fixture IDs | 104 | 104 | 104 |
| Duplicate Match identities | 0 | 0 | 0 |
| Duplicate League identities | 0 | 0 | 0 |
| Orphan Match -> League | 0 | 0 | 0 |
| Orphan home Team references | 0 | 0 | 0 |
| Orphan away Team references | 0 | 0 | 0 |

Identity values remained stable:

```text
provider = api-football
provider_id = 1
local league_id = 1274
country_id = 2105094199
```

## 10. Duplicate Checks

Final PostgreSQL results:

- duplicate League provider identity groups: `0`;
- duplicate Match provider identity groups: `0`;
- selected Match rows: `104`;
- distinct selected Provider fixture IDs: `104`;
- selected fixtures mapped to distinct local League IDs: `1`.

The canonical identities are therefore one-to-one for this synchronized set.

## 11. Identity Stability

Repeated synchronization did not change:

- local `league_id`;
- Provider name;
- Provider ID;
- Country ID;
- Allowed state.

The stable mapping remained:

```text
(api-football, 1) -> 1274
```

Provider identity was used externally, while local identity was preserved in every Match row.

## 12. Failure-Safety Result

A focused regression test safely verified the unresolved-identity branch without modifying real data:

- an allowed local League with `provider_id=None` was supplied to FixtureSync;
- Provider call count remained zero;
- result was `success=false`;
- an explicit unresolved-identity message was returned;
- no Match persistence path was entered.

No real database row was corrupted to perform this check.

## 13. Genericity Assessment

The real runtime evidence exercises an unequal local/provider identity. The implementation dynamically reads `League.provider_id` from the League Master and continues to use `League.league_id` for local Match relationships.

Focused tests additionally verify a generic relationship `local league_id=42 -> provider_id=7`, without League-specific constants.

The evidence supports this generic conclusion:

> The Fixture Sync flow preserves Provider Identity, Local League Identity, and Match identity across repeated synchronization when the League Master contains a valid Provider identity and the League is allowed.

## 14. Limitations

- Phase 6 began with 104 existing Matches, so its first request measured repeat-update behavior rather than a fresh insert transition.
- The fresh insert transition was not recreated by deleting data; it is supported by Phase 4.2 evidence of 104 inserts followed by 104 updates.
- Only one real unequal-ID League was available for this complete runtime repeat test.
- Raw Provider query parameters are not emitted by application logs; Provider identity resolution was proven by Phase 4.2 runtime evidence and implementation tests.
- A third sync was not necessary because the second identical request already reproduced stable `0 inserted / 104 updated` behavior.

## 15. Final Classification

# PASS WITH LIMITATIONS

The repeated real API synchronization is idempotent and preserves canonical identities and relationships:

```text
SYNC #1: 0 inserted, 104 updated
SYNC #2: 0 inserted, 104 updated
Matches: 104 -> 104 -> 104
Duplicates: 0
Orphans: 0
Identity corruption: none
```

The repeat-sync behavior passes. The limitation is that the Phase 6 baseline already contained the real 104-match set, so a fresh first-run insert was not repeated in this phase; that transition was proven in Phase 4.2 without destructive reset.
