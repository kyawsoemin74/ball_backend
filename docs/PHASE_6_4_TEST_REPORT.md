# PHASE 6.4 — TEST REPORT

## A. Test Scope

This phase verified the frozen Phase 6.1 architecture and Phase 6.2 failure/state matrix using controlled tests only. No real Provider synchronization, historical repair, finalization mutation, or production lineup mutation was executed.

Covered:

* Player identity resolution and CREATE_NEW boundaries
* Missing provider Player identity handling
* Partial lineup acceptance
* Multiple/all missing identity cases
* Missing identity tracking contract
* Partial finalization classification
* Analytics withholding for partial lineups
* Complete lineup Analytics behavior
* Provider and resolver failure handling
* Transaction rollback/ownership
* Retry and attempt-count behavior
* Lock/concurrency behavior
* Idempotency
* Cache ordering
* Event independence
* Database integrity

## B. Player Identity Tests

Result: PASS

Covered:

* `RESOLVED_EXISTING`
* `CREATE_NEW`
* invalid identity
* ambiguous identity
* conflict identity
* missing provider identity
* PlayerSyncService boundary
* no direct LineupSyncService-to-PlayerRepository CREATE_NEW bypass

## C. Partial Acceptance Tests

Result: PASS

Covered:

* one missing Player
* multiple missing Players
* all Players missing
* valid Players continue after missing entries
* missing Players do not reach Analytics
* partial result is not SUCCESS

## D. Missing Identity Tracking

Result: PASS in controlled tests

Verified tracking contract includes:

* match identity
* provider fixture context
* provider team identity
* roster role and lineup position
* Player diagnostic fields
* missing reason
* timestamps
* retry eligibility
* observation count
* uniqueness contract
* no fake provider identity
* no Player Master creation

## E. State Transition Tests

Result: PASS

Verified:

```text
REQUIRED / RUNNING
→ PARTIAL domain state
→ RETRYABLE persistence
```

Partial state does not become SUCCESS.

## F. Retry Tests

Result: PASS in controlled tests

Verified:

* retry discovery
* PARTIAL remains retryable
* PARTIAL-to-complete contract
* attempt count is per lineup attempt
* missing Players do not increment attempts individually
* lock rejection does not consume attempts
* maximum retry terminal classification

## G. Analytics Tests

Result: PASS

Verified:

* complete canonical lineup projects successfully
* partial lineup does not publish complete Analytics
* unresolved Players do not produce Analytics rows
* Analytics uses canonical local `player_id`
* provider IDs are never used as local Player FKs
* Analytics failure participates in rollback behavior

## H. Transaction Tests

Result: PASS

Verified:

* repository transaction ownership remains external
* Player/lineup/Analytics failures roll back through the outer owner
* commit ambiguity is not treated as automatic SUCCESS
* no hidden repository commit/rollback was introduced

## I. Lock / Concurrency Tests

Result: PASS

Verified:

* `lineup:{match_id}` lock behavior
* competing lineup requests are rejected/skipped
* no duplicate Analytics business keys
* lock release on success/failure/exception
* Event and Lineup locks remain independent

## J. Idempotency Tests

Result: PASS

Repeated complete synchronization/projection does not create duplicate:

* Player identities
* lineup business rows
* Analytics business rows
* missing identity tracking entries

## K. Cache Tests

Result: PASS

Verified:

* no successful cache publication before commit
* rollback does not publish success state
* successful invalidation occurs after commit
* partial state does not appear as complete success

## L. Event Independence Tests

Result: PASS

Lineup `PARTIAL`, `RETRYABLE`, `SUCCESS`, and terminal outcomes do not mutate EventSync state. Event synchronization retains its independent lock, transaction, persistence, and cache path.

## M. System-Wide Entry Points

Result: PASS by source and integration coverage

Verified entry points converge on the shared LineupSyncService path:

* manual API
* scheduler refresh
* fixture terminal finalization
* finalization retry
* FootballAPIService invocation

## N. Database Integrity

Read-only checks:

* NULL Player identities: `0`
* Duplicate Player identities: `0`
* Orphan memberships: `0`
* Duplicate memberships: `0`
* Orphan lineup Matches: `0`
* Orphan Analytics Players: `0`
* Orphan Analytics Teams: `0`
* Orphan Analytics Matches: `0`
* Duplicate Analytics keys: `0`
* Orphan Event Players: `0`
* Orphan Event assists: `0`
* Missing tracking orphan Matches: `0`
* Missing tracking orphan Teams: `0`
* Missing tracking orphan Players: `0`
* Missing tracking duplicate keys: `0`

Historical condition reported separately:

* stale historical lineup references: `41`

These historical rows were not repaired.

Database snapshots were identical:

```text
Players: 7701 → 7701
Memberships: 7818 → 7818
Match lineups: 43 → 43
Analytics lineups: 92 → 92
Finalization rows: 97 → 97
Match events: 8766 → 8766
Missing tracking rows: 0 → 0
```

## O. Full Regression

Focused Phase 6.4 suite:

* Passed: `134`
* Failed: `0`
* Warnings: `6`

Full backend suite:

* Passed: `572`
* Failed: `1`
* Skipped: `0`
* Warnings: `19`

Known unrelated failure:

```text
test_ensure_teams_exist_resolves_existing_masters_and_reports_missing
```

Classification:

```text
PRE-EXISTING TEST CONTRACT MISMATCH
```

The test expects the older TeamSyncService result shape and omits the current `resolved` mapping. Git history shows that contract predates Phase 6.3 and is unrelated to the lineup partial-acceptance implementation.

## P. Known Pre-Existing Failures

Only the TeamSync return-shape mismatch listed above remains. No Phase 6.4 regression was identified.

## Q. Real E2E

```text
NOT YET VERIFIED
```

Real Provider validation remains reserved for Phase 6.5.

## Final Decision

```text
PHASE 6.4 = PASS

Automated Tests = PASS
Partial Acceptance = PASS
Missing Identity Retry = PASS
State Matrix = PASS
Analytics = PASS
Transaction = PASS
Lock/Concurrency = PASS
Idempotency = PASS
Cache = PASS
Event Independence = PASS
Database Integrity = PASS for current active data
Historical stale references = unchanged and excluded from repair
Known unrelated full-suite failure = documented
Real E2E = NOT YET VERIFIED

NEXT PHASE = PHASE 6.5 — REAL E2E
```
