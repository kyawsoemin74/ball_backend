# PHASE 4.4 - League Identity Recovery Final Specification

Status: design-only final specification. No source code, schema, database data, synchronization endpoint, provider mutation, or identity repair was executed.

## 1. Current Implementation Evidence

### Already implemented

- `League.league_id` is the local canonical primary key.
- `League.provider` and nullable `League.provider_id` store provider identity.
- `LeagueRepository` can look up by provider identity, read by local ID, and attach/update provider identity.
- `LeagueService.register_league` validates an explicit provider ID and verifies the provider response ID.
- `LeagueSyncService` onboards provider-bearing League rows and synchronizes League metadata/seasons.
- `AllowedLeagueService` validates identity when a new AllowedLeague is added.
- `FixtureSyncService.sync_full_season` validates provider identity before requesting fixtures.
- Team processing already uses resolve -> ensure/create -> resolve again.
- API/Scheduler own outer transaction commit/rollback.
- `run_with_resource_lock` provides PostgreSQL transaction-scoped advisory locks.
- League sync queues League cache invalidation for `after_commit` and clears it on rollback.

### Partially implemented

- Provider identity attachment exists, but there is no recovery state, attempt count, next retry time, or conflict-safe recovery orchestration.
- `LeagueProvider.get_leagues_by_name()` exists, but no authoritative candidate-selection workflow uses name plus country and ambiguity rules.
- FixtureSync has a defensive identity guard, but no League-domain recovery handoff or post-recovery re-resolution.
- AllowedLeague creation validates identity, but runtime reads return raw IDs and do not revalidate historical rows.
- LeagueSeason has a local FK and unique `(league_id, season)`, but is not resolved before fixture provider access.
- Scheduler has sync jobs and locks, but no League identity recovery job.

### Missing

- Durable League identity recovery state and retry metadata.
- An authoritative provider discovery contract for a League with no `provider_id`.
- League-domain recovery orchestration.
- Scheduler discovery/retry of recovery records.
- Typed API outcomes for unresolved identity versus provider/database failure.
- FixtureSync handoff, committed recovery re-read, and controlled retry.

### Incorrect or outdated wiring

- `FixtureSyncService` is currently the first effective enforcement point for a League Master invariant.
- The season route returns `HTTP 200` with `success:false`, zero counts, and no stable error code for an unresolved identity.
- A local League may be Allowed while its provider identity is incomplete because existing authorization rows are not continuously revalidated.

Evidence: [app/models/league.py](app/models/league.py#L9-L30), [app/services/league_service.py](app/services/league_service.py#L216-L310), [app/services/league_sync_service.py](app/services/league_sync_service.py#L118-L350), [app/repositories/league_repository.py](app/repositories/league_repository.py#L8-L220), [app/services/fixture_sync_service.py](app/services/fixture_sync_service.py#L1060-L1115), [app/api/matches.py](app/api/matches.py#L469-L500).

## 2. Approved Target Architecture

```text
League Master
    |
    | provider_id missing/invalid
    v
League Identity Recovery (League domain)
    |
    +-- Provider Lookup
    |       |
    |       +-- SUCCESS -> verify candidate -> save provider_id -> commit
    |       |                                      |
    |       |                                      v
    |       |                              re-resolve League
    |       |
    |       +-- FAIL -> durable retry state -> Scheduler
    |
    v
LeagueSeason
    v
AllowedLeague
    v
FixtureSync
    v
Team -> Match
```

Frozen boundaries:

- League Master owns identity.
- League Recovery owns discovery, verification, and identity persistence.
- Provider adapters perform HTTP only.
- FixtureSync validates and consumes identity; it never creates or repairs League identity.
- Recovery commits before FixtureSync re-resolves and calls the fixture Provider.
- Team recovery remains downstream and uses the existing ensure/re-resolve pattern.

## 3. Recovery Trigger Policy

### Authoritative triggers

1. **Season FixtureSync:** when the requested local League is Allowed but `provider` is unsupported/blank or `provider_id` is null/blank/invalid.
2. **Daily FixtureSync:** when a returned fixture's provider League cannot resolve to a local valid League identity. Daily sync must isolate the affected League/fixture and continue independent valid units according to its batch transaction policy.
3. **Scheduler recovery job:** discovers durable retry records and invokes League Recovery.

### Non-triggers

- **League Sync:** does not repair arbitrary local-only rows. It already consumes provider League payloads and onboards provider-bearing rows; its existing behavior remains unchanged.
- **Startup/background reconciliation:** not a trigger until the recovery scheduler is implemented. Startup must not make unbounded provider calls.
- **Manual API:** current registration requires an explicit provider ID and remains the supported manual onboarding path. A separate identity-recovery API is not required for Phase 4.4; it may be added later only as an approved admin workflow.

### Exact trigger condition

Recovery is required when the canonical League exists but:

```text
provider is null/blank OR provider != "api-football"
OR provider_id is null/blank OR provider_id is invalid
OR provider identity lookup returns zero/multiple/conflicting local rows
```

Recovery occurs **before fixture Provider access** because `/fixtures?league=...` cannot be safely formed without a verified external League ID. No local ID, name, country, season, or LeagueSeason value may be substituted.

## 4. Provider ID Discovery Contract

### Existing capability

`LeagueProvider.get_league_details(league_id)` calls `GET /leagues?id=<id>`. `LeagueProvider.get_leagues_by_name(name)` calls `GET /leagues?name=<name>`. There is no current recovery method that accepts a local League with no provider ID. Evidence: [app/providers/league_provider.py](app/providers/league_provider.py#L1-L25).

### Frozen discovery workflow

For an unresolved local League:

1. Read local League name and country/country code.
2. Require a non-empty local country relationship for automatic discovery. If country is missing, mark recovery unresolved/manual review; do not search by name alone.
3. Call the existing provider search capability `GET /leagues?name=<local name>`. A future provider method may add an explicitly supported country parameter, but this is not assumed by current source.
4. Parse the provider response into candidate League records.
5. Retain only candidates whose normalized provider League name exactly matches the local League name and whose provider country/country code exactly matches the local country identity.
6. Require exactly one candidate with a positive provider League ID.
7. Verify the selected response's provider ID and metadata before persistence.
8. If zero candidates remain, classify `IDENTITY_RESOLUTION_RETRY` or manual review according to failure class.
9. If multiple candidates remain, classify `IDENTITY_RESOLUTION_FAILED` with reason `AMBIGUOUS_PROVIDER_MATCH`; never select the first result.
10. If provider response identity conflicts with the search/request context, reject it and persist no identity.

This is not name-only guessing: name is a search key, while exact name plus country verification and exactly-one-candidate enforcement are mandatory. If country is absent or ambiguous, automatic recovery is prohibited.

### Provider failure classes

| Result | State | Retry |
|---|---|---|
| One verified candidate | `IDENTITY_RESOLVED` | No further recovery retry |
| Zero candidates | `IDENTITY_RESOLUTION_RETRY` | Yes, bounded |
| Multiple candidates | `IDENTITY_RESOLUTION_FAILED` | No automatic retry unless source data changes/manual review |
| Timeout/5xx/transport failure | `IDENTITY_RESOLUTION_RETRY` | Yes, exponential backoff |
| 4xx/auth/configuration failure | `IDENTITY_RESOLUTION_FAILED` | No automatic retry until configuration/operator action |
| Invalid provider payload | `IDENTITY_RESOLUTION_FAILED` | Manual review |

## 5. Durable Recovery State Model

Current `leagues` has no recovery state, attempt counter, or `next_retry_at`. Therefore a separate durable recovery record is required; process memory is insufficient for restart-safe retry. This is an implementation schema requirement, not a change made in this phase.

### Target persistence

Target record: `league_identity_recovery`, keyed uniquely by local `league_id`, with at least:

```text
league_id                 FK -> leagues.league_id, primary/unique
state                     enum/text
attempt_count             integer
next_retry_at             timestamp nullable
last_attempted_at         timestamp nullable
last_error_code           text nullable
last_error_message        text nullable
provider                  text nullable
resolved_provider_id      text nullable
created_at / updated_at   timestamps
```

The record is recovery metadata, not a second League identity. `League.provider_id` remains the only persisted canonical provider identity.

### States

| State | Meaning | Owner | Transition | Retry eligibility | Terminal? |
|---|---|---|---|---|---|
| `IDENTITY_RESOLUTION_REQUIRED` | League needs recovery; no active attempt | FixtureSync/League domain | Validation detects missing/invalid identity | Yes | No |
| `IDENTITY_RESOLUTION_RUNNING` | One recovery attempt owns the League lock | League Recovery | Scheduler/API acquires lock and starts attempt | No concurrent retry | No |
| `IDENTITY_RESOLVED` | Provider identity saved, committed, and re-read successfully | League Recovery | Verified persistence | No | Success |
| `IDENTITY_RESOLUTION_RETRY` | Transient/zero-result failure with future retry time | Scheduler/League Recovery | Retryable failure recorded | Yes when `next_retry_at <= now` | No |
| `IDENTITY_RESOLUTION_FAILED` | Ambiguous, invalid, configuration, or exhausted failure | League Recovery/operator | Non-retryable or max-attempt failure | No automatic retry | Yes until explicit requeue |

`IDENTITY_RESOLVED` is retained as audit state but the League Master remains authoritative. If the League later loses identity, a new required record is created/updated.

## 6. Retry Policy

The retry policy is frozen for implementation:

- Initial retry delay: 15 minutes.
- Exponential backoff: 15 minutes, 30 minutes, 60 minutes, 120 minutes, then 240 minutes.
- Maximum automatic attempts: 5.
- Maximum delay: 24 hours.
- Retryable: timeout, provider 5xx, transport failure, rate limit, and zero provider candidates when local metadata remains eligible.
- Non-retryable: ambiguous candidates, invalid response identity, unsupported provider, missing local country/name, provider authentication/configuration failure, and identity conflict.
- At attempt 5, transition to `IDENTITY_RESOLUTION_FAILED` with manual-review reason.
- `next_retry_at` and `attempt_count` survive Worker/API restart.
- Scheduler restart rereads durable `IDENTITY_RESOLUTION_RETRY` records; it does not rely on in-memory queues.

The provider HTTP client already retries each HTTP request up to three total attempts with a 30-second timeout. Recovery-level backoff is separate and must not create an unbounded nested loop. Evidence: [app/services/base/football_client.py](app/services/base/football_client.py#L46-L100).

## 7. Locking Model

Use the existing PostgreSQL transaction-scoped resource lock:

```text
resource_type     = "league_identity"
resource_identity = local league_id
lock identity     = fover:sync:league_identity:<league_id>
```

Rules:

- Only one recovery attempt for a local League can run at once.
- Manual/API and Scheduler use the same resource lock.
- Different Leagues use different lock identities and can recover independently.
- Lock acquisition occurs before changing `IDENTITY_RESOLUTION_RUNNING`.
- Lock release is handled by `run_with_resource_lock` in `finally` and is transaction-scoped through PostgreSQL advisory xact locking.
- A rollback or process/database failure releases the transaction-scoped lock; no permanent lock record is created.
- A lock loser rereads the League/recovery record. If the winner committed `IDENTITY_RESOLVED`, the loser returns idempotent success; otherwise it leaves retry ownership to the scheduler.

Existing fixture lock `fover:sync:fixture_query:global` remains unchanged. Evidence: [app/services/resource_lock.py](app/services/resource_lock.py#L20-L95).

## 8. Transaction Ownership

### A. Provider lookup

Provider lookup runs inside the League Recovery orchestration attempt but performs no DB identity mutation until a candidate is verified.

### B. League identity persistence

League Recovery owns a separate `AsyncSession`/transaction:

```text
begin recovery transaction
  -> lock League
  -> read League/recovery record
  -> provider lookup
  -> verify candidate
  -> repository attach/update provider identity
  -> flush and reread
  -> commit
```

Failure rolls back identity changes and records retry metadata in a controlled transaction. The recovery service, not the Repository, owns commit/rollback.

### C. LeagueSeason / AllowedLeague

After identity is committed and re-resolved, normal LeagueSeason and AllowedLeague validation/handling occurs. AllowedLeague remains authorization, not identity persistence. Existing fixture/LeagueSeason writes remain under the FixtureSync caller's outer transaction.

### D. FixtureSync

API or Scheduler owns the outer FixtureSync transaction. FixtureSync performs orchestration, flushes/savepoints, and never commits the caller's transaction. It calls the fixture Provider only after the committed recovery reread succeeds.

Recovery and FixtureSync transactions are separate to prevent a Match failure from leaving an ambiguous League identity mutation and to ensure a Provider request never relies on uncommitted identity.

## 9. LeagueSeason Dependency

Frozen rule:

```text
League identity recovery can happen independently.
After identity is valid, LeagueSeason is validated/created as needed.
AllowedLeague is then validated as synchronization authorization.
FixtureSync proceeds only after identity and authorization checks pass.
```

A pre-existing LeagueSeason is **not required** for identity recovery because the current architecture creates/upserts LeagueSeason after provider fixture data is received. The parent League must exist and have valid identity; LeagueSeason does not repair or substitute for it. `LeagueSeason` remains unique by `(league_id, season)` and is persisted by its existing service/repository path.

## 10. FixtureSync Handoff

Frozen flow:

```text
identity missing
  -> create/update recovery record
  -> execute one League-domain recovery attempt
  -> recovery commits identity in separate transaction
  -> FixtureSync rereads League
  -> validates provider + provider_id
  -> validates AllowedLeague and season
  -> starts/restarts fixture operation
  -> FixtureProvider request
  -> normal Team and Match flow
```

- The failed precondition operation is retried once automatically only after successful committed recovery.
- FixtureSync restarts the provider portion from the beginning; it does not resume a partially started fixture operation.
- The recovery and fixture transactions never share a session/transaction.
- Duplicate provider fixture requests are avoided by making no fixture request before identity validation and by one recovery retry per operation.
- Match idempotency remains protected by `(provider, provider_fixture_id)` upsert.
- If recovery fails, FixtureSync returns a typed unresolved/retry result and does not call the fixture Provider.

## 11. Failure Isolation

The failure unit is `League + season` for the season endpoint and `League/fixture` for independently processed date/batch units.

| Failure | Unit result | Other valid units |
|---|---|---|
| One League unresolved | `IDENTITY_RESOLUTION_RETRY` or `FAILED`; no fixture request | Continue |
| One provider lookup timeout | Recovery unit retry | Continue |
| Ambiguous provider result | Recovery unit terminal/manual review | Continue |
| Recovery DB failure | Recovery transaction rollback | Continue |
| Lock contention | Unit deferred; no mutation | Continue |
| Scheduler restart | Durable retry state reread | Continue |
| Fixture Match DB failure | Current fixture transaction behavior applies | Independent units continue only where their transaction boundary is independent |

The current season endpoint is one League/season, so it cannot process unrelated Leagues inside that request. A future scheduler/batch must use one transaction/provider-request unit per League/season so A failure cannot roll back B/C.

## 12. API Contract

The current route returns HTTP 200 with a failure dictionary. The final contract is:

| Outcome | HTTP status | Error code/state |
|---|---:|---|
| Invalid query/auth | Existing FastAPI 422/401/403 behavior | `INVALID_REQUEST` / auth code |
| Identity unresolved and recovery failed/deferred | 409 Conflict | `LEAGUE_IDENTITY_UNRESOLVED` / `IDENTITY_RESOLUTION_RETRY` or `FAILED` |
| Provider unavailable after valid identity | 503 Service Unavailable | `LEAGUE_PROVIDER_UNAVAILABLE` |
| Empty valid provider result | 200 | `NO_FIXTURES_FOUND` |
| Database failure | 500 | `FIXTURE_SYNC_DATABASE_FAILURE` |
| Successful sync | 200 | `FIXTURE_SYNC_COMPLETED` |

Response body for identity failure:

```json
{
  "success": false,
  "operation": "fixture_season_sync",
  "league_id": 389,
  "season": 2026,
  "state": "IDENTITY_RESOLUTION_RETRY",
  "error_code": "LEAGUE_IDENTITY_UNRESOLVED",
  "message": "League provider identity is unresolved; fixture provider was not called.",
  "provider_request_attempted": false,
  "inserted": 0,
  "updated": 0,
  "failed": 0,
  "recovery": {
    "attempt_count": 1,
    "next_retry_at": "<timestamp>"
  }
}
```

`success:false` is allowed as an explicit failure body, but it must not be returned with HTTP 200 for unresolved identity. The body must contain a stable code/state and provider-request flag. Lock contention remains HTTP 409 with the existing lock message or a typed lock code; the implementation must distinguish lock conflict from identity conflict in the body.

## 13. Scheduler Contract

Scheduler responsibilities:

1. Query durable recovery records where state is retryable and `next_retry_at <= now`.
2. Acquire `fover:sync:league_identity:<league_id>`.
3. Invoke League-domain Recovery.
4. Persist `IDENTITY_RESOLVED`, `IDENTITY_RESOLUTION_RETRY`, or `IDENTITY_RESOLUTION_FAILED` through the owning service.
5. Commit state/identity results through the recovery transaction.
6. Schedule `next_retry_at` using the frozen backoff.
7. Emit metrics/logs and continue to the next candidate.

Scheduler must not:

- update `League.provider_id` directly;
- call repository identity writes without League Recovery;
- guess provider IDs;
- execute FixtureSync itself as part of identity recovery;
- allow one candidate failure to abort other candidates.

## 14. Responsibility Matrix

| Component | Responsibility | Must NOT do |
|---|---|---|
| League Master | Canonical local identity and provider identity ownership | Use display data as external identity |
| LeagueService | Public League-domain recovery/registration orchestration | Let FixtureSync write identity |
| LeagueSyncService | Provider-to-League onboarding, candidate verification, recovery state coordination | Bypass repository or silently overwrite conflicts |
| League Recovery | Lookup, verify, persist, retry-state transition, commit/re-read | Persist Match or invoke FixtureSync directly |
| LeagueProvider | HTTP lookup/search transport | Persist identity or choose ambiguous candidate |
| TeamProvider | Team HTTP transport | Own League identity |
| FixtureSyncService | Validate/re-resolve League, fetch fixtures after validation, orchestrate Team/Match | Create/repair League or guess provider ID |
| LeagueSeason | Local League-season relationship and metadata | Repair parent League identity |
| AllowedLeague | Synchronization authorization | Prove provider identity by row existence alone |
| Scheduler | Discover retry records, lock, invoke recovery, schedule retry | Direct provider-ID writes or fixture orchestration |
| API | Auth, request validation, outer transaction, typed response mapping | Bypass service/repository ownership |
| Repository | SQL reads/writes, conflict-safe identity persistence | Call external Provider or commit business workflow |
| Database | FKs, uniqueness, durable recovery state | Infer semantic provider identity |

## 15. Cache and Observability Contract

Recovery queues existing League cache keys `fover:league:<id>` and `fover:leagues_grouped` for deletion after successful commit. Rollback clears queued invalidation. No FixtureSync cache invalidation occurs before recovery commit. Existing `fover:live_matches` invalidation remains after successful FixtureSync commit.

Required structured events:

```text
LEAGUE_IDENTITY_VALIDATION_STARTED
LEAGUE_IDENTITY_UNRESOLVED
LEAGUE_IDENTITY_RECOVERY_REQUESTED
LEAGUE_IDENTITY_RECOVERY_STARTED
LEAGUE_IDENTITY_PROVIDER_LOOKUP
LEAGUE_IDENTITY_RECOVERED
LEAGUE_IDENTITY_RECOVERY_FAILED
LEAGUE_IDENTITY_RERESOLUTION_SUCCEEDED
LEAGUE_IDENTITY_RERESOLUTION_FAILED
FIXTURE_SYNC_RESUMED
FIXTURE_SYNC_SKIPPED
LEAGUE_IDENTITY_SCHEDULER_RETRY
LEAGUE_IDENTITY_LOCK_CONTENTION
```

Each event includes local `league_id`, provider, provider ID/null, season where applicable, state, attempt, operation ID, lock result, provider-request attempted, result class, and transaction outcome. Secrets are excluded.

## 16. Implementation File Scope

### MUST MODIFY

| File | Required change |
|---|---|
| `app/services/league_service.py` | Expose League-owned recovery orchestration and typed result |
| `app/services/league_sync_service.py` | Verify provider candidate, coordinate state transitions, reuse onboarding rules |
| `app/repositories/league_repository.py` | Add atomic identity verification/attach behavior if existing operations are insufficient |
| `app/providers/league_provider.py` | Add/standardize provider search contract around existing `/leagues?name=` capability |
| `app/services/fixture_sync_service.py` | Trigger recovery handoff, re-resolve after commit, preserve no-guess guard |
| `app/services/scheduler.py` | Add durable recovery retry job and per-League lock invocation |
| `app/api/matches.py` | Map typed unresolved/provider/database outcomes to final API contract |

### MAY MODIFY, only if verification proves necessary

| File | Condition |
|---|---|
| `app/models/league.py` / new recovery model registration | Only for approved durable state representation |
| `app/models/__init__.py` | Only if a recovery model is added |
| `alembic/versions/...` | Only after explicit schema approval for recovery state/constraints |
| `app/services/football.py` | Only if the facade must forward a new typed contract |
| `app/monitoring.py` | Only if existing metrics cannot represent recovery lifecycle |
| `app/api/admin_leagues.py` | Only if an explicit admin recovery endpoint is approved |

### MUST NOT MODIFY

- Team Master/provider/repository/service behavior.
- Match model and provider-fixture uniqueness/upsert behavior.
- LeagueSeason behavior unless the approved dependency verification finds a direct defect.
- Standing, Odds, Event, Lineup, Statistics, H2H, and analytics modules.
- Existing fixture Provider endpoint behavior and shared HTTP retry policy.
- Existing active/live cache implementation.
- Existing daily/live/repair scheduler jobs except shared lock/helper integration proven necessary.
- Unrelated migrations, auth, users, countries, and cleanup modules.

## 17. Acceptance Tests

| # | Input | Expected state | Expected DB result | Expected API/Scheduler result |
|---:|---|---|---|---|
| 1 | League has valid provider ID | `IDENTITY_RESOLVED`/valid | No recovery mutation; fixtures persist normally | HTTP 200 success |
| 2 | League provider ID missing | Required/running | Recovery record created/updated; no Match write before recovery | Typed unresolved/recovery result; no fixture Provider call |
| 3 | Provider lookup yields one exact name+country candidate | Resolved | League provider ID committed once | FixtureSync re-resolves and proceeds |
| 4 | Provider lookup yields zero candidates | Retry | No League identity mutation; next retry scheduled | `IDENTITY_RESOLUTION_RETRY`, no fixture call |
| 5 | Provider lookup yields multiple exact candidates | Failed/manual review | No identity mutation | `IDENTITY_RESOLUTION_FAILED`, no automatic retry |
| 6 | Provider timeout/5xx | Retry | No partial identity; attempt metadata committed | Scheduler backoff state |
| 7 | Retry due | Running then retry/resolved | Attempt incremented; identity only on verified success | Scheduler continues other records |
| 8 | Worker restarts during retry state | Retry reread | Durable state/attempt/next time retained | Next worker resumes safely |
| 9 | Concurrent API and scheduler recovery | One running owner | One identity update at most | Loser re-reads/no duplicate |
| 10 | Valid recovery with no LeagueSeason | Identity resolved | League identity commits; season created later by normal flow | FixtureSync allowed after auth/season handling |
| 11 | AllowedLeague absent | Authorization failure | No fixture Match write | Typed not-authorized/unallowed result |
| 12 | Recovery succeeds then re-read fails | Unresolved | No fixture transaction starts | No fixture Provider call; retry/manual state |
| 13 | A fails, B/C valid | A isolated | B/C independent commits | Partial/batch result records A failure |
| 14 | Same recovery repeated | Resolved/no-op | No duplicate League identity | Stable success/no-op |
| 15 | API unresolved identity | Retry/failed | No fixture writes | HTTP 409 and stable error code |
| 16 | Scheduler retry candidate | Running/retry/resolved | State transaction committed | Retry metrics/logs emitted |
| 17 | Final valid fixture processing | Completed | LeagueSeason/Teams/Matches persist with normal idempotency | HTTP 200 success; cache after commit |

## 18. Final Frozen Workflow

```text
League Master
    |
    | provider_id NULL / invalid / unresolved
    v
League Identity Recovery  [League domain owns this boundary]
    |
    v
Provider Lookup           [HTTP only; no persistence]
    |
    +---------------- SUCCESS ----------------+
    |                                         |
    v                                         v
verify exact candidate                    durable recovery failure
    |                                         |
save provider_id                            next_retry_at/attempt
    |                                         |
recovery transaction COMMIT                 Scheduler retry
    |                                         |
re-resolve League                           League Recovery
    |                                         |
LeagueSeason validation/creation             [same per-League lock]
    |
AllowedLeague authorization
    |
FixtureSync [Provider access only after valid identity]
    |
Team resolve -> ensure/create -> resolve again
    |
Match persistence -> API/Scheduler commit
```

Frozen transaction boundaries:

```text
Recovery session: Provider lookup -> verify -> League identity write -> commit
Fixture session:  re-resolve -> fixture Provider -> Team/Season/Match -> commit
Cache:            after the relevant commit only
```

## 19. Final Readiness Classification

**B. DESIGN FROZEN WITH MINOR LIMITATIONS**

The required architecture is now explicit and implementation-ready at the contract level:

- provider discovery is fixed to provider search plus exact local name/country verification and ambiguity rejection;
- recovery state and restart-safe retry metadata are defined;
- retry interval/backoff/max attempts are defined;
- locking and transaction ownership are defined;
- LeagueSeason independence and post-recovery ordering are defined;
- FixtureSync handoff/re-resolution is defined;
- failure isolation and API outcomes are defined;
- file scope and acceptance tests are defined.

Limitations requiring implementation-phase confirmation:

1. API-Football's deployed `/leagues?name=` response must expose sufficient country metadata for exact matching; otherwise an explicitly supported country-aware provider query is required before implementation can pass.
2. Durable recovery-state schema requires an approved migration; no schema change was made in this phase.
3. Exact provider payload normalization for names/countries must be tested against controlled responses before production rollout.

These are verification gates, not unresolved ownership or workflow decisions. No implementation begins until the provider response contract and durable-state migration are approved.
