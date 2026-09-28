# PHASE 4.5 - League Identity Recovery Implementation Report

## 1. Objective

Implement the approved League-owned provider identity recovery workflow generically for all Leagues, without guessing provider IDs or moving identity ownership into FixtureSync.

## 2. Phase 4.4 Contract

Implemented contract:

```text
League Master
  -> missing/invalid provider identity
  -> League Identity Recovery
  -> GET /leagues?name=<league_name>
  -> exact name + country verification
  -> save provider identity in League Master
  -> recovery commit
  -> re-resolve League
  -> FixtureSync
  -> Team -> Match
```

Frozen behavior preserved:

- FixtureSync does not directly write League identity.
- Provider access occurs only after identity validation.
- Ambiguous/zero/malformed candidates are not guessed.
- Recovery uses `fover:sync:league_identity:<league_id>` resource locking.
- Recovery state is durable and retryable.
- Fixture and recovery transactions are separate.
- Team and Match behavior remains on the existing path.

## 3. Pre-Implementation Audit

The source matched the Phase 4.4 assumptions:

- `League.provider_id` remains nullable.
- `LeagueProvider.get_leagues_by_name()` already calls `/leagues?name=...`.
- `LeagueRepository` already supports provider identity lookup and attachment.
- No durable recovery state existed.
- FixtureSync was the first effective identity guard.
- The scheduler had no identity recovery job.
- The season API returned service failure dictionaries with HTTP 200.

## 4. Files Modified

Implementation files added or changed for this phase:

- `app/models/league_identity_recovery.py`
- `app/repositories/league_identity_recovery_repository.py`
- `app/services/league_identity_recovery_service.py`
- `app/models/__init__.py`
- `alembic/env.py`
- `alembic/versions/20260922_create_league_identity_recovery.py`
- `app/services/league_service.py`
- `app/services/football.py`
- `app/services/fixture_sync_service.py`
- `app/api/matches.py`
- `app/services/scheduler.py`
- `tests/test_league_identity_recovery.py`
- `tests/test_scheduler_runtime.py`

Other modified workspace files predated this phase and were not reverted.

## 5. Database Migration

Migration: `20260922_league_identity_recovery`

Applied successfully with Alembic. PostgreSQL verification confirmed:

- Alembic version: `20260922_league_identity_recovery`
- Table: `league_identity_recovery`
- Unique League relationship: `uq_league_identity_recovery_league_id`
- Foreign key: `league_id -> leagues.league_id ON DELETE CASCADE`
- Retry index: `(state, next_retry_at)`
- Durable fields: state, attempt count, retry time, last attempt, error code/message, provider, resolved provider ID, resolved timestamp, timestamps

No existing League row or provider ID was modified by the migration.

## 6. Provider Discovery Implementation

`LeagueIdentityRecoveryService` uses the existing `LeagueProvider.get_leagues_by_name()` capability:

```text
GET /leagues?name=<local League name>
```

Candidate processing:

1. Require local League name and country metadata.
2. Normalize whitespace/case.
3. Require exact normalized provider League name match.
4. Require exact country name or country-code match.
5. Require exactly one unique positive numeric provider ID.
6. Reject zero candidates, malformed payloads, invalid IDs, and ambiguity.
7. Reject provider identity conflicts with another local League.

The first provider result is never selected arbitrarily.

## 7. Recovery State Implementation

Durable states implemented:

- `IDENTITY_RESOLUTION_REQUIRED`
- `IDENTITY_RESOLUTION_RUNNING`
- `IDENTITY_RESOLVED`
- `IDENTITY_RESOLUTION_RETRY`
- `IDENTITY_RESOLUTION_FAILED`

The state is stored per local League with a unique relationship. Recovery success stores the verified provider and provider ID plus `resolved_at`. Failures store error code/message and retry metadata.

## 8. Retry Implementation

Implemented retry policy:

- delays: 15, 30, 60, 120, 240 minutes;
- maximum attempts: 5;
- retryable: zero candidates, timeout, provider unavailability, provider error;
- terminal: ambiguity, invalid response/ID, metadata insufficiency, identity conflict, database failure, or exhausted attempts;
- retry state survives process restart through PostgreSQL.

The existing HTTP client’s three-attempt request retry remains separate from recovery-level backoff.

## 9. Lock Implementation

Recovery uses the existing `run_with_resource_lock` helper with:

```text
resource_type = league_identity
resource_identity = local league_id
```

This produces a per-League PostgreSQL transaction-scoped advisory lock. API and Scheduler recovery use the same lock identity; different Leagues can run independently. Lock release remains in the helper’s `finally` path.

## 10. Transaction Boundaries

Recovery transaction:

```text
read League
-> provider lookup
-> candidate verification
-> repository identity attach
-> reread identity
-> recovery state success
-> commit
```

Fixture transaction:

```text
re-resolve committed League
-> fixture provider request
-> Team/LeagueSeason/Match processing
-> caller commit
```

Recovery uses its own session through `async_session`. FixtureSync retains API/Scheduler outer transaction ownership. Recovery cache invalidation occurs after recovery commit.

## 11. FixtureSync Integration

`FixtureSyncService.sync_full_season` now:

- validates the local League first;
- invokes LeagueService recovery when identity is unresolved;
- rereads the League after successful recovery;
- calls the fixture Provider only after valid provider identity exists;
- returns a typed unresolved result if recovery fails or reread fails;
- leaves Team resolution and Match persistence unchanged.

FixtureSync does not directly call the League repository to write provider identity.

## 12. LeagueSeason / AllowedLeague Integration

League identity recovery does not require a pre-existing LeagueSeason row. After recovery, normal FixtureSync validates the AllowedLeague gate and creates/upserts LeagueSeason from fixture data through the existing path.

AllowedLeague remains authorization only. It does not discover or repair provider identity.

## 13. API Contract

The season API maps identity recovery failures to HTTP 409 with a structured detail body containing state, stable error code, League ID, retryability, and provider-call outcome.

Example error code:

```text
LEAGUE_IDENTITY_UNRESOLVED
```

The prior unresolved-identity behavior of HTTP 200 with only `success:false` is no longer used for this dependency failure. Existing invalid request/auth and lock behavior remains separate.

## 14. Scheduler Integration

A new `recover_league_identities` job runs every 15 minutes with `max_instances=1`.

The job:

1. reads durable retry candidates where `state=IDENTITY_RESOLUTION_RETRY`, `next_retry_at <= now`, and attempts remain;
2. invokes LeagueService recovery per candidate;
3. isolates candidate failures;
4. records aggregate scheduler metrics/logs.

The Scheduler does not write provider IDs, perform candidate matching, or execute FixtureSync.

## 15. Failure Isolation

Recovery is isolated per League. A timeout, ambiguous provider response, lock contention, or database failure for one League does not stop recovery attempts for other candidates.

The existing season endpoint remains one League/season per request. Independent multi-League scheduler candidates are processed independently.

## 16. Unit Test Results

Focused tests passed:

```text
6 passed
```

Covered:

- exact name/country candidate selection;
- country mismatch;
- name mismatch;
- zero candidates;
- malformed response;
- ambiguous candidates;
- bounded exponential backoff;
- retryable versus terminal classification;
- HTTP 409 identity failure;
- scheduler recovery job registration.

## 17. Real DB Verification

Completed:

- Alembic migration applied to PostgreSQL.
- Migration head verified.
- Recovery table columns verified.
- Unique index and retry index verified.
- League foreign key verified.
- No existing League provider IDs were populated.

Not performed:

- No controlled recovery record was inserted for an existing League.
- No real identity update was performed.
- No FixtureSync endpoint was executed.

## 18. Real Provider Verification

A read-only provider discovery request was performed through the existing provider/client path:

```text
GET /leagues?name=Premier League
```

Result:

- provider request succeeded;
- response contained 35 candidates;
- no candidate was selected because no local League recovery transaction was executed;
- no provider ID was persisted;
- no credentials were exposed.

The result demonstrates provider transport availability, not successful exact local name/country recovery for a controlled test League.

## 19. Scheduler Runtime Verification

Completed:

- Scheduler job registration test passes with `recover_league_identities`.
- Durable retry candidate query and per-League recovery invocation are implemented.

Not fully verified:

- live scheduler retry against a controlled retryable recovery record;
- Worker restart during a running retry;
- concurrent API/Scheduler recovery against a disposable League.

## 20. End-to-End Verification

Not executed. The required end-to-end flow would mutate a controlled League identity and then run FixtureSync. No suitable disposable unresolved League fixture was provisioned, and no existing League was repaired or used for this mutation test.

## 21. Idempotency Verification

Source/test coverage confirms:

- unique recovery state per League;
- existing valid identity returns a no-op result;
- provider identity conflict is rejected;
- Match and LeagueSeason existing uniqueness constraints remain in place;
- repeated recovery logic does not create a second League row.

A real repeated recovery transaction was not executed, so runtime idempotency remains a verification gap.

## 22. Regression Verification

Focused recovery/scheduler suite:

```text
6 passed
```

Full repository suite:

```text
34 failed, 667 passed, 19 warnings
```

The failures are predominantly pre-existing unrelated Odds, Lineup, Team, and fixture-lock expectations. One scheduler registration expectation was directly updated for the new approved recovery job. No unrelated failures were repaired or masked.

## 23. Remaining Limitations

1. Provider discovery was tested read-only, but exact recovery persistence was not run against a disposable unresolved League.
2. The provider search returned multiple candidates for the test name; exact country filtering against a controlled local League was covered by unit tests, not a real recovery transaction.
3. Scheduler restart and concurrent API/Scheduler recovery were not runtime-tested.
4. The full repository suite remains red because of unrelated pre-existing failures.
5. Existing workspace modifications were not reverted.
6. No live FixtureSync, Team Sync, or Match persistence operation was executed.

## 24. Final Status

**B. IMPLEMENTATION PARTIALLY VERIFIED**

The durable state migration, provider candidate normalization, retry policy, League-domain recovery service, advisory lock integration, FixtureSync handoff, Scheduler trigger, API 409 mapping, and focused tests are implemented and validated at source/schema/unit level.

Classification A is not claimed because real identity persistence, scheduler retry execution, concurrent recovery, and end-to-end FixtureSync were not safely verified with a disposable controlled League.

## 25. Next Phase Recommendation

Provision a disposable controlled League fixture/database dataset, then verify:

1. one exact provider candidate resolves and persists;
2. recovery state reaches `IDENTITY_RESOLVED`;
3. FixtureSync re-resolves and proceeds;
4. retryable failure survives Worker restart;
5. API and Scheduler concurrent recovery produce one identity update;
6. repeated recovery and FixtureSync remain idempotent;
7. existing Team/Match/Live/Scheduler paths remain regression-safe.
