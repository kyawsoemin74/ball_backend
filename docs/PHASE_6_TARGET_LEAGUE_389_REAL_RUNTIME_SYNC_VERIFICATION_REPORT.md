# PHASE 6 — TARGET LEAGUE 389 REAL RUNTIME SYNC VERIFICATION REPORT

## 1. Objective

Verify whether the exact runtime workflow can process and persist fixtures for:

```text
POST /api/matches/sync/season?league_id=389&season=2026
```

The required chain was evaluated as:

```text
Provider -> League Master -> Provider Identity -> Country -> Allowed League -> Fixture Sync -> Team Identity -> Match Persistence -> PostgreSQL
```

## 2. Verification-Only / No-Code-Change Declaration

No Python source, service, repository, provider, scheduler, API, model, schema, migration, test, configuration, environment variable, sync behavior, transaction behavior, lock behavior, or cache behavior was modified.

The only state-changing operation was the requested application API call itself. No database rows were manually inserted, updated, repaired, or deleted.

Existing unrelated worktree changes were left untouched.

## 3. Environment

- Runtime date: 2026-09-18
- Application: local FastAPI service
- Database: live PostgreSQL through the application SQLAlchemy session
- Provider: API-Football through the existing fixture provider
- Authentication: active admin user `admin`, role `admin`
- Target: local League 389, season 2026

## 4. League 389 Baseline

Fresh PostgreSQL read-only evidence:

```text
league_id: 389
provider: api-football
provider_id: NULL
name: Premier League
country_id: NULL
```

The local League Master row exists, but it has no provider identity and no country relationship.

### Duplicate Provider Identity

Exact lookup:

```text
(provider='api-football', provider_id='389') -> 0 rows
```

No duplicate identity exists. The problem is absence of the canonical provider mapping, not duplicate ambiguity.

## 5. Provider Identity Evidence

The provider was queried through the existing fixture service before the target API call.

```text
provider request: fixtures for league=389, season=2026
provider response rows: 240
provider errors: []
provider League IDs: ['389']
provider seasons: ['2026']
first fixture ID: 1528100
provider League name: Premier League
```

The provider identity is therefore:

```text
(provider='api-football', provider_id='389')
```

The existing League Master lookup requires that exact identity. The current database has no matching row because local League 389 has `provider_id = NULL`.

## 6. Country Evidence

Fresh read-only evidence:

```text
league.country_id: NULL
referenced Country row: none
```

Classification: FAIL for the target League country relationship.

Country resolution is not reached as a usable downstream stage because the League row itself has no `country_id`.

## 7. Allowed League Evidence

Fresh read-only evidence:

```text
allowed_leagues.league_id = 389: present
```

The local League is allowed. This proves Allowed League membership is not the cause of the failure.

Classification: PASS for allowed-list membership, but it does not compensate for missing canonical provider identity.

## 8. Before-Sync Database State

Read-only baseline for `league_id=389`, `season=2026`:

```text
LeagueSeason rows: 0
Match count: 0
Distinct provider fixture identities: 0
Duplicate provider fixture identities: 0
Orphan League references: 0
Orphan home Team references: 0
Orphan away Team references: 0
Distinct Match Team references: 0
Match status groups: []
Exact provider League identity rows: 0
```

League 389 had no existing season row or match rows before the target call.

## 9. Exact API Request

```text
POST http://127.0.0.1:8000/api/matches/sync/season?league_id=389&season=2026
```

The request used the existing active-admin Bearer authentication path.

## 10. HTTP Response

```text
HTTP status: 200
```

Complete response body:

```json
{
  "success": false,
  "inserted": 0,
  "updated": 0,
  "total": 0,
  "failed": 240,
  "message": "Fixture sync aborted because provider League identity is unresolved for the payload.",
  "final_lineup_candidates": []
}
```

This is not a successful synchronization. HTTP 200 only indicates that the request completed through the API route without an unhandled transport or server exception.

## 11. Runtime Log Correlation

The target request returned an explicit unresolved-identity failure for all 240 provider fixtures.

The active fixture-sync implementation emits the following runtime events for this branch:

```text
Skipping fixture with unresolved provider League: provider_id=389
Fixture sync aborted: provider League identity is unresolved for 240 fixture(s); no local league mapping was available.
```

The route's normal failure-result path then rolls back the request session rather than committing fixture work.

The active terminal interface did not expose the separate long-running Uvicorn stdout stream for direct line-by-line capture. Therefore, raw live stdout correlation is NOT VERIFIABLE independently in this report. The API response, provider payload, database before/after evidence, and active runtime control flow all agree on the same failure branch.

No evidence indicates that Team Identity resolution, Match INSERT/UPDATE, cache invalidation for persisted matches, or fixture transaction commit was reached.

## 12. Provider → League → Fixture Execution Chain

| Stage | Result | Evidence |
| --- | --- | --- |
| Provider request | PASS | 240 provider fixtures returned, no provider errors |
| Provider League identity | PASS | Every inspected provider fixture carried League ID 389 and season 2026 |
| Local League resolution | FAIL | `(api-football, 389)` lookup returned 0 rows |
| Country resolution | FAIL / NOT EXECUTED | League 389 has `country_id = NULL`; no Country row could resolve |
| Allowed League validation | PASS independently | `allowed_leagues.league_id=389` exists |
| Fixture filtering | FAIL | All 240 fixtures were rejected at unresolved League identity |
| Team identity resolution | NOT EXECUTED | No fixture reached team resolution |
| Match INSERT/UPDATE | NOT EXECUTED | No fixture reached Match persistence |
| Transaction commit of fixture data | NOT EXECUTED | Failure result caused rollback; no fixture writes existed |
| Database persistence | FAIL | Match count remained 0 |

## 13. After-Sync Database State

Fresh PostgreSQL verification after the API call:

```text
League 389 provider_id: NULL
League 389 country_id: NULL
Allowed League 389: present
LeagueSeason rows for 389 / 2026: 0
Match count for 389 / 2026: 0
Distinct provider fixture identities: 0
Duplicate provider fixture identities: 0
Orphan League references: 0
Orphan home Team references: 0
Orphan away Team references: 0
Rows for sampled provider fixture IDs 1528100-1528103: 0
Match status groups: []
```

Before/after comparison:

```text
matches for league 389 / season 2026: 0 -> 0
LeagueSeason rows: 0 -> 0
```

No useful database change occurred.

## 14. Match Persistence Evidence

The response reported:

```text
inserted: 0
updated: 0
total: 0
failed: 240
```

PostgreSQL independently confirmed:

```text
matches where league_id=389 and season=2026: 0
sample provider fixture rows: 0
```

Classification: Match persistence was not reached because League identity resolution failed first.

## 15. Team Identity Evidence

No fixture reached Team Identity resolution. This is proven by:

- all 240 fixtures failing at provider League resolution
- `total=0` in the API response
- zero Match rows for the target League and season
- zero Match Team references in the baseline and after-state

Team identity was therefore NOT EXECUTED for this target sync. No Team rows were created or modified by this request.

## 16. Integrity Checks

Post-sync checks:

```text
Duplicate provider fixture identities: 0
Orphan Match -> League references: 0
Orphan Match -> home Team references: 0
Orphan Match -> away Team references: 0
Duplicate League provider identity for (api-football, 389): 0
```

These are clean because no target Match rows were written. They do not mean the requested fixtures were synchronized.

## 17. Repeat-Sync / Idempotency Evidence

The first target sync persisted no fixtures:

```text
inserted=0
updated=0
total=0
failed=240
```

Under the requested rule, a repeat sync was not run as a meaningful idempotency test. There is no persisted Match set against which update-versus-duplicate behavior could be evaluated.

Idempotency classification: NOT VERIFIED / NOT MEANINGFUL because the first run did not persist data.

## 18. First Failing Stage

The first blocking failure is:

```text
Local League Resolution / Provider Identity Resolution
```

Exact cause:

```text
provider payload identity: (api-football, 389)
League Master lookup: provider='api-football' AND provider_id='389'
lookup result: 0 rows
local league_id=389 row: exists, but provider_id=NULL
```

All later fixture stages were blocked because the sync is fail-closed when the provider League cannot be mapped to a canonical Local League.

## 19. Root-Cause Analysis

The provider returned valid fixture data. The target League was also present in the Allowed League table. Neither condition is sufficient for fixture persistence.

The controlling failure is that local League 389 is not canonically connected to the provider identity. The generic resolver intentionally uses `(provider, provider_id)` and does not infer identity from `local league_id`.

The current behavior is now explicit rather than misleading:

```text
HTTP 200
!= sync success
!= database change
```

In this run:

```text
HTTP request completed: YES
Provider fixtures retrieved: YES
Fixture synchronization succeeded: NO
Database Match change: NO
```

## 20. Architecture-Boundary Verification

The runtime path respected the generic architecture boundary:

```text
Provider -> FixtureProvider -> FixtureSyncService -> LeagueRepository identity lookup -> fail closed
```

Confirmed:

- no League 389 special-case logic was required
- no canonical League fallback was used
- Allowed League membership did not bypass League Master identity resolution
- no direct Match insertion occurred
- Team Master resolution was not bypassed
- scheduler ownership was not changed
- transaction ownership was not changed
- no manual data repair was performed

The failure is generic and would apply to any League whose provider identity is missing or unresolved.

## 21. Final Classification

# FAIL — LEAGUE IDENTITY

The target workflow cannot currently process League 389 fixtures because the first blocking stage is canonical provider League identity resolution.

The provider returned 240 valid fixtures, but all 240 were rejected before Team resolution and Match persistence. The API returned HTTP 200 with an explicit `success=false` response, and PostgreSQL remained unchanged for the target League and season.

## 22. Evidence Limitations

- The separate long-running Uvicorn terminal's raw stdout was not directly available through the active terminal-selection interface, so individual live log lines were not independently copied into this report.
- The runtime response, fresh provider payload, source-controlled active logging branch, and before/after PostgreSQL evidence are mutually consistent.
- Country relationship is not valid for League 389 because `country_id` is NULL; no repair was attempted.
- Repeat-sync idempotency is not meaningfully testable because the first run persisted zero fixtures.

## 23. Exact Next Recommended Phase

Do not repair during this verification phase.

The next phase should be a separately authorized League Master identity-data investigation and implementation phase that:

1. confirms the intended canonical mapping for local League 389;
2. establishes the correct Country relationship;
3. attaches `(provider='api-football', provider_id='389')` to exactly one canonical local League;
4. creates or verifies the correct LeagueSeason relationship for season 2026;
5. reruns this exact target sync only after explicit authorization; and
6. verifies Match persistence and repeat-sync idempotency from a fresh baseline.

No such repair was performed here.

## Final Answer to the Required Question

Can `POST /api/matches/sync/season?league_id=389&season=2026` currently process Provider fixtures and persist correct Match data through the generic League Identity architecture?

```text
No.
```

The provider request succeeds, but the generic League identity lookup fails because local League 389 has no provider identity. The current fail-closed runtime rejects all 240 fixtures, persists zero Matches, and returns an explicit failure body despite HTTP 200.
