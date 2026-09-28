# PHASE 6.2 — FAILURE / STATE MATRIX FREEZE

## Lineup Partial Acceptance + Missing Identity Retry

**Status:** PASS  
**Failure / State Matrix:** FROZEN  
**Next phase:** PHASE 6.3 — IMPLEMENTATION

This document is authoritative with [PHASE_6_1_ARCHITECTURE_FREEZE.md](PHASE_6_1_ARCHITECTURE_FREEZE.md). It defines the complete state and failure contract for partial lineups and missing provider Player identities. It does not implement the behavior.

No production code, schema, migration, database row, retry counter, finalization record, historical record, or runtime behavior was changed.

## 1. Executive Summary

The current implementation has these verified gaps:

* `LineupSyncService` rejects the complete lineup when one Player identity is missing.
* `PlayerIdentityResolutionService` currently classifies missing `player.id` as a whole-player terminal readiness failure.
* `PARTIAL` is not a persisted database state.
* No dedicated missing-lineup-identity tracking structure exists.
* `AnalyticsProjectionService` requires a canonical local `player_id` for every projected row and cannot publish a partial lineup as complete.
* Existing finalization persistence supports `REQUIRED`, `RUNNING`, `RETRYABLE`, `TERMINAL`, and `SUCCESS`; it does not support a literal `PARTIAL` status.
* Retry discovery is based on `REQUIRED` and `RETRYABLE` records.
* One locked finalization attempt increments `attempt_count` once at `mark_attempt_started`.
* Lock contention does not increment `attempt_count`.
* Event synchronization is independent.

The frozen contract is therefore:

```text
Domain State:      PARTIAL
Persistence State: RETRYABLE
```

A partial lineup is retryable, never successful, and never publishes Analytics rows until all lineup Player identities are complete.

## 2. Current State / Failure Audit

### 2.1 Current runtime flow

```text
Provider LineupProvider
  -> LineupSyncService
  -> PlayerIdentityResolutionService
  -> PlayerSyncService / PlayerRepository for CREATE_NEW
  -> match_lineups.data JSON
  -> AnalyticsProjectionService.project_lineup
  -> AnalyticsLineupRepository.replace_by_match
  -> outer commit
  -> post-commit cache invalidation
```

### 2.2 Current finalization flow

```text
FixtureSyncService._process_sync_with_candidates
  -> create_required() on terminal Match transition
  -> final_lineup_candidates

FixtureSyncService.finalize_pending_lineups
  -> get_retry_candidates()
  -> lineup:{match_id} advisory lock
  -> mark_attempt_started(): RUNNING and attempt_count += 1
  -> FootballAPIService.sync_match_lineup()
  -> LineupSyncService
  -> Analytics Projection
  -> mark_success() or failure handling
  -> outer commit/rollback
  -> post-commit cache invalidation
```

### 2.3 Current implementation versus frozen requirement

| Area | Current implementation | Frozen requirement | Classification |
|---|---|---|---|
| Missing `player.id` | Whole-lineup non-ready failure | Skip only that Player and track it | Implementation gap |
| Valid Players beside missing identity | Not processed because lineup returns early | Continue valid Player processing | Implementation gap |
| Missing identity tracking | No dedicated structure | Lineup/finalization-owned tracking | Implementation gap |
| `PARTIAL` state | Not persisted | Domain state mapped to `RETRYABLE` | Implementation gap |
| Partial Analytics | Current projection requires canonical IDs | Withhold Analytics until complete | Implementation gap |
| Resolver `CREATE_NEW` | Uses PlayerSyncService path | Preserve | Aligned |
| Player Master identity | Shared resolver boundary | Preserve | Aligned |
| Finalization states | `REQUIRED`, `RUNNING`, `RETRYABLE`, `TERMINAL`, `SUCCESS` | Domain `PARTIAL` maps to `RETRYABLE` | Aligned after implementation |
| Retry discovery | Finds `REQUIRED` / `RETRYABLE` terminal matches | Rediscover partial work | Aligned after implementation |
| Attempt counting | Increment once on locked attempt start | One per lineup attempt | Aligned |
| Lock | `lineup:{match_id}` advisory lock | Preserve | Aligned |
| Transaction ownership | Outer caller owns commit/rollback | Preserve | Aligned |
| Event independence | Separate `events:{match_id}` path | Preserve | Aligned |

## 3. Canonical State Model

The logical domain state model is:

```text
REQUIRED
   ↓
RUNNING
   ↓
PARTIAL
   ↓
RETRY
   ↓
RUNNING
   ↓
SUCCESS
```

Persistence mapping:

| Domain state | Persistence state | Classification | Retryable | Terminal |
|---|---|---|---:|---:|
| `REQUIRED` | `REQUIRED` | IN_PROGRESS | Yes | No |
| `RUNNING` | `RUNNING` | IN_PROGRESS | Not independently | No |
| `PARTIAL` | `RETRYABLE` | PARTIAL | Yes | No |
| `RETRY` | `RETRYABLE` | RETRYABLE | Yes | No |
| `SUCCESS` | `SUCCESS` | SUCCESS | No | No |
| terminal failure | `TERMINAL` | TERMINAL | No | Yes |

`PARTIAL` is a domain state, not a new database enum/status in this phase. A retryable finalization record must carry partial/missing-identity diagnostics so the state is distinguishable from an ordinary provider retry.

## 4. State Transition Matrix

| Current State | Trigger | Condition | Next State | Retryable? | Attempt Count Change | Write Allowed? | Rollback Required? | Analytics Allowed? | Cache Action | Terminal? | Reason / Category |
|---|---|---|---|---:|---:|---:|---:|---:|---|---:|---|
| `REQUIRED` | retry discovery | candidate is eligible | `RUNNING` | Yes | `+1` when attempt starts | Yes | No before work | After complete validation only | No pre-commit invalidation | No | normal attempt |
| `RUNNING` | complete valid lineup | all identities resolve, lineup and Analytics succeed | `SUCCESS` | No | 0 | Yes | No | Yes | Invalidate after commit | No | success |
| `RUNNING` | missing Player identities | lineup structure valid, one or more identities missing | `PARTIAL` domain / `RETRYABLE` persistence | Yes | 0 beyond attempt start | Valid entries/tracking only | Commit retryable tracking transaction; no invalid rows | No | No success invalidation | No | missing identity |
| `RUNNING` | retryable provider failure | timeout, connection, temporary provider error | `RETRYABLE` | Yes | 0 beyond attempt start | No partial source data | Yes for lineup work; persist failure metadata through owner | No | No success invalidation | No | `PROVIDER_FAILURE` |
| `RUNNING` | invalid provider response | structurally unusable response | `RETRYABLE` or terminal per category | Contract-defined | 0 beyond attempt start | No invalid lineup | Rollback lineup/Analytics work | No | No success invalidation | No unless exhausted | `INVALID_RESPONSE` |
| `RUNNING` | terminal identity failure | conflict, ambiguity, unsafe identity | `TERMINAL` | No | 0 beyond attempt start | No unsafe identity rows | Rollback invalid lineup/Analytics work | No | No success invalidation | Yes | `MASTER_RESOLUTION_FAILURE` |
| `RUNNING` | structural validation failure | invalid match/team/sections | `RETRYABLE` if provider-transient, otherwise `TERMINAL` | Matrix-dependent | 0 beyond attempt start | No invalid lineup | Yes | No | No success invalidation | Matrix-dependent | `LINEUP_VALIDATION_FAILED` |
| `RUNNING` | Analytics failure | complete lineup but projection/persistence fails | `RETRYABLE` | Yes unless exhausted | 0 beyond attempt start | No committed partial Analytics | Yes | No | No success invalidation | No unless exhausted | `ANALYTICS_PROJECTION_FAILURE` |
| `RUNNING` | DB/transaction failure | flush or transaction failure | `RETRYABLE` | Yes unless exhausted | 0 beyond attempt start | No partial commit | Yes | No | No success invalidation | No unless exhausted | `TRANSACTION_FAILURE` |
| `RUNNING` | commit ambiguity | commit outcome unknown | verification path | Yes until verified | Attempt already counted | No assumption of success | Verify before retry | Unknown until verified | No success invalidation until verified | If confirmed failure/exhausted | `COMMIT_AMBIGUITY` |
| `PARTIAL` | retry discovery | `RETRYABLE` record with missing identities | `RUNNING` | Yes | `+1` when attempt starts | Yes | No before work | Only after complete | No pre-commit invalidation | No | partial retry |
| `PARTIAL` | retry still missing identities | one or more identities remain unusable | `PARTIAL` / `RETRYABLE` | Yes | One attempt only | Update tracking only | No invalid rows | No | No success invalidation | No | missing identity |
| `PARTIAL` | all identities become valid | complete resolver success and Analytics success | `SUCCESS` | No | One attempt only | Yes | No | Yes | Invalidate after commit | No | completion |
| `PARTIAL` | terminal identity result | conflict/ambiguity remains unsafe | `TERMINAL` | No | One attempt only | No unsafe rows | Yes | No | No success invalidation | Yes | identity terminal |
| `RETRYABLE` | retry discovery | candidate selected | `RUNNING` | Yes | `+1` when attempt starts | Yes | No before work | After complete validation only | No pre-commit invalidation | No | retry |
| `RETRYABLE` | complete valid retry | all identities resolve and projection succeeds | `SUCCESS` | No | One attempt only | Yes | No | Yes | Invalidate after commit | No | success |
| `RETRYABLE` | missing identities remain | structurally valid but incomplete | `PARTIAL` / `RETRYABLE` | Yes | One attempt only | Valid tracking only | No invalid rows | No | No success invalidation | No | partial |
| `RETRYABLE` | terminal identity failure | unsafe identity cannot be resolved | `TERMINAL` | No | One attempt only | No unsafe rows | Yes | No | No success invalidation | Yes | terminal identity |
| `RETRYABLE` | maximum attempts exhausted | attempt count reaches limit | `TERMINAL` | No | Current attempt counted once | Preserve valid evidence/tracking only | Transaction rules apply | No incomplete Analytics | No success invalidation | Yes | `MAX_RETRY_ATTEMPTS_EXCEEDED` |
| `RUNNING` | duplicate `RUNNING` request | same locked match already owned | unchanged / lock rejection | Yes later | `0` | No | No operation transaction | No | None | No | `LOCK_FAILURE` |

Undefined transitions are prohibited. A successful state cannot transition back to retryable through ordinary processing.

## 5. Player-Level Missing Identity Matrix

| Question | Frozen answer |
|---|---|
| Does one missing Player ID fail the whole lineup? | No |
| Does valid-player processing continue? | Yes |
| Does the missing Player create a Player Master row? | No |
| Does the missing Player enter Analytics? | No |
| Is the missing identity tracked? | Yes |
| Is the lineup `SUCCESS`? | No |
| Can the lineup retry? | Yes |
| Does missing identity increment attempts per Player? | No |
| Is name-only matching allowed? | No |
| Is a placeholder ID allowed? | No |

Required flow:

```text
valid Player
  -> resolver
  -> canonical local player_id
  -> process normally

missing provider Player ID
  -> skip that Player
  -> track evidence
  -> continue valid Players
  -> PARTIAL domain state / RETRYABLE persistence
```

## 6. Multiple Missing Players

| Input | Valid processed | Missing tracked | Domain result | Persistence | Analytics |
|---|---:|---:|---|---|---|
| 45 valid IDs | 45 | 0 | complete | success path | allowed after projection |
| 43 valid, 2 missing | 43 | 2 | `PARTIAL` | `RETRYABLE` | withheld until complete |
| 35 valid, 10 missing | 35 | 10 | `PARTIAL` | `RETRYABLE` | withheld until complete |
| 0 valid, 45 missing | 0 | 45 | `PARTIAL` only if structure/team/match are valid and the lineup is retryable | `RETRYABLE` | withheld |
| malformed sections/teams | 0 or partial source parsing is irrelevant | N/A | lineup-level failure | retryable/terminal by matrix | withheld |

An all-missing lineup is not automatically accepted merely because it is partial. It is `PARTIAL` only when the lineup structure, match, and teams are valid and the provider response is otherwise usable. If the provider response itself is unusable, it is a lineup-level invalid response.

## 7. Resolver Failure Matrix

| Resolver result | Scope | Domain result | Persistence | Retryable | Analytics |
|---|---|---|---|---:|---|
| `RESOLVED_EXISTING` | Player-level success | continue | valid Player entry | N/A | eligible after complete lineup |
| `CREATE_NEW` | Player-level success | PlayerSyncService then continue | canonical Player only | N/A | eligible after complete lineup |
| `AMBIGUOUS` | Unsafe identity | terminal identity failure | no unsafe Player/lineup identity | No | withheld |
| `CONFLICT` | Ownership conflict | terminal identity failure | no overwrite or duplicate | No | withheld |
| `INVALID` with missing/empty provider ID | Player-level incomplete identity | `PARTIAL` | track missing identity only | Yes | withheld |
| `INVALID` with malformed provider namespace or contradictory payload | Identity/domain failure | terminal unless provider-transient cause is proven | no unsafe identity | Usually No | withheld |
| repository lookup unavailable | Infrastructure | retryable transaction/provider failure | rollback | Yes | withheld |

## 8. Provider Failure Matrix

| Provider condition | Scope | State | Retry? | Attempt increment | Partial data persisted? | Rollback | Analytics |
|---|---|---|---:|---:|---:|---:|---:|
| timeout | lineup-level | `RETRYABLE` | Yes | One attempt | No | Yes | No |
| connection failure | lineup-level | `RETRYABLE` | Yes | One attempt | No | Yes | No |
| HTTP temporary failure | lineup-level | `RETRYABLE` | Yes | One attempt | No | Yes | No |
| API error response | lineup-level | `RETRYABLE` unless permanent | Contract-defined | One attempt | No | Yes | No |
| empty response | lineup-level | `RETRYABLE` or terminal if provider proves no data | Contract-defined | One attempt | No | Yes | No |
| malformed lineup structure | lineup-level | `RETRYABLE` if provider-transient, otherwise terminal | Matrix-dependent | One attempt | No | Yes | No |
| missing team object/identity | lineup-level | `TEAM_IDENTITY_RESOLUTION_FAILED` | Usually retryable if provider-transient | One attempt | No | Yes | No |
| missing `startXI` / `substitutes` array | lineup-level | `LINEUP_VALIDATION_FAILED` | Provider-contract dependent | One attempt | No | Yes | No |
| one missing `player.id` | player-level | `PARTIAL` / `RETRYABLE` | Yes | One attempt | Valid entries/tracking only | No invalid rows | No |
| duplicate provider Player IDs | lineup/player-level | validation failure | Retry only if provider-transient | One attempt | No duplicate identity rows | Yes | No |
| invalid present provider Player ID | player-level identity failure | terminal unless provider correction is explicitly retryable | Matrix-dependent | One attempt | No unsafe identity | Yes | No |

A missing Player ID is never collapsed into malformed whole-lineup response when the remaining lineup structure is usable.

## 9. Lineup Validation Matrix

### Lineup-level validation

| Condition | Result |
|---|---|
| Missing/invalid match identity | lineup-level failure; no persistence; retry/terminal by cause |
| Missing/invalid provider team | team identity failure; no persistence |
| Malformed response object | invalid provider response; no persistence |
| Missing required lineup section | lineup validation failure; no persistence |
| Provider response cannot be safely interpreted | invalid response; no persistence |

### Player-level validation

| Condition | Result |
|---|---|
| Valid positive provider ID | resolver and normal processing |
| Missing/null provider ID | skip, track, continue, `PARTIAL` |
| Empty provider ID | skip, track, continue, `PARTIAL` |
| Invalid present provider ID | identity failure; no fake identity |
| Resolver ambiguity/conflict | no processing for that Player; terminal identity policy |
| Duplicate provider identity in source | reject duplicate source; no silent duplicate |

## 10. Analytics Failure Matrix

| Lineup condition | Analytics behavior | Finalization |
|---|---|---|
| Complete lineup | project all canonical local Player IDs; replace match scope; verify flush | `SUCCESS` after commit |
| `PARTIAL` lineup | withhold Analytics publication; no missing Player rows; retain tracking | `RETRYABLE` |
| Partial valid rows already present from a prior implementation | replace/remove only through the authoritative complete or rollback contract; never imply complete state | retryable until complete |
| Missing local Player FK | reject before repository replacement | rollback; retryable/terminal by identity category |
| Missing team/match FK | reject before repository replacement | rollback |
| Duplicate source key | reject deterministic source | rollback/contract failure |
| Analytics repository failure | no committed Analytics or lineup mutation | `RETRYABLE` |
| Analytics flush/reconciliation failure | outer rollback | `RETRYABLE` unless exhausted |
| Analytics commit ambiguity | verify transaction outcome before state decision | `COMMIT_AMBIGUITY` |

Analytics never resolves provider identity and never creates Player Master records.

## 11. Transaction Failure Matrix

| Failure | State | Rollback | Retry | Attempt count | Cache | Data safety |
|---|---|---:|---:|---:|---|---|
| PlayerSync/Player write failure | `RETRYABLE` or terminal identity | Yes | Yes if infrastructure | One attempt | No success invalidation | no partial unsafe Player |
| Lineup write failure | `RETRYABLE` | Yes | Yes | One attempt | No invalidation | no partial lineup |
| Missing identity tracking write failure | `RETRYABLE` / transaction failure | Yes | Yes | One attempt | No invalidation | no untracked partial success |
| Analytics write failure | `RETRYABLE` | Yes | Yes | One attempt | No invalidation | no partial Analytics |
| flush failure | `RETRYABLE` | Yes | Yes | One attempt | No invalidation | no published success |
| commit failure known | `RETRYABLE` or terminal after limit | Rollback if possible | Yes until exhausted | Attempt already counted | No success invalidation | verify state |
| commit ambiguity | verification state | Verify, do not assume | Yes if not committed | Attempt already counted | No success invalidation until verified | no false SUCCESS |
| rollback failure | transaction failure/escalation | Escalate and verify DB state | Contract-dependent | Attempt already counted | No success invalidation | explicit operational alert |

## 12. Retry Attempt Contract

`attempt_count` represents locked lineup/finalization attempts, not Players or missing identities.

Frozen rules:

* An attempt begins after `lineup:{match_id}` is acquired and `mark_attempt_started()` changes the record to `RUNNING`.
* `attempt_count` increments exactly once at attempt start.
* Provider preflight performed before orchestration does not increment it.
* Retry discovery does not increment it.
* Lock rejection does not increment it.
* One missing Player, two missing Players, and ten missing Players each consume at most one attempt for that lineup run.
* A `PARTIAL` result consumes the current attempt and remains retryable.
* Commit ambiguity does not increment a second time; the existing attempt must be verified.
* Successful completion does not add an extra increment beyond the attempt that produced it.
* Failure metadata persistence must not increment the attempt a second time for the same locked attempt.

## 13. PARTIAL → SUCCESS Contract

```text
PARTIAL / RETRYABLE
  -> retry provider lineup
  -> validate all previously missing identities
```

If two identities were missing and one becomes valid:

```text
1 resolved + 1 missing
  -> process newly valid identity
  -> update missing tracking
  -> remain PARTIAL / RETRYABLE
  -> no SUCCESS
```

If all missing identities become valid:

```text
all identities valid
  -> shared resolver for every provider Player
  -> complete canonical local IDs
  -> persist complete lineup
  -> replace complete Analytics scope
  -> mark SUCCESS
  -> commit
  -> post-commit cache invalidation
```

## 14. Maximum Retry Contract

```text
PARTIAL / RETRYABLE
  -> attempt count reaches FINAL_LINEUP_MAX_ATTEMPTS
  -> TERMINAL
  -> MAX_RETRY_ATTEMPTS_EXCEEDED
```

At exhaustion:

* no further automatic retry occurs
* valid evidence and missing-identity tracking remain preserved according to the transaction contract
* incomplete Analytics is absent/withheld
* no fake Player or Analytics row is created
* future manual reprocessing remains an explicit operational action through the authoritative pipeline
* historical records outside the selected lineup remain untouched

## 15. Lock / Concurrency Matrix

| Actor | Lock result | Attempt count | Transaction | Final state |
|---|---|---:|---|---|
| Request A acquires `lineup:{match_id}` | proceeds | increments once at start | owns current attempt | normal result |
| Request B simultaneous | rejected/skipped | unchanged | no lineup transaction | Request A owns result |
| Scheduler simultaneous | rejected/skipped | unchanged | no lineup transaction | retry later |
| Retry worker simultaneous | rejected/skipped | unchanged | no lineup transaction | retry later |
| Finalization request simultaneous | rejected/skipped | unchanged | no lineup transaction | retry later |
| lock owner fails | released in `finally` | current attempt remains recorded | rollback/metadata path | retryable/terminal by failure |
| retry after release | may acquire | increments once when started | new attempt | normal result |

## 16. Cache Matrix

| Outcome | Before commit | After commit | Cache action |
|---|---|---|---|
| `SUCCESS` | no success invalidation | invalidate lineup cache | allowed |
| `PARTIAL` | no success invalidation | invalidate only if the persisted partial contract requires it; never advertise completeness | explicit partial policy |
| retryable provider failure | no invalidation | no success invalidation | none |
| terminal failure | no invalidation | no success invalidation | none |
| rollback | no invalidation | no invalidation | none |
| commit ambiguity | no invalidation | wait for verification | invalidate only after confirmed commit |

No cache invalidation may expose uncommitted state.

## 17. Event Independence

| Lineup state | Event behavior |
|---|---|
| `PARTIAL` | no EventSync trigger or mutation |
| `RETRYABLE` | no EventSync trigger or mutation |
| `SUCCESS` | no EventSync dependency; Events remain separately synchronized |
| `TERMINAL` | no EventSync trigger or mutation |

Event transactions, locks, cleanup, and cache invalidation remain independent.

## 18. Failure Category Contract

| Category | Scope | Retryable | Terminal | State | Attempt increment | Analytics impact |
|---|---|---:|---:|---|---:|---|
| `PROVIDER_FAILURE` | lineup-level | Yes | No until exhausted | `RETRYABLE` | one attempt | withheld |
| `INVALID_RESPONSE` | lineup-level | Contract-dependent | On permanent/exhaustion | `RETRYABLE` or `TERMINAL` | one attempt | withheld |
| `LINEUP_VALIDATION_FAILED` | lineup-level | Provider-cause dependent | If permanent | retryable/terminal | one attempt | withheld |
| `PLAYER_IDENTITY_RESOLUTION_FAILED` | player-level identity | Missing case Yes; unsafe case No | Depends on subtype | `PARTIAL` or `TERMINAL` | one attempt | missing Player withheld |
| `MASTER_RESOLUTION_FAILURE` | identity safety | No for ambiguity/conflict | Yes | `TERMINAL` | one attempt | withheld |
| `IDENTITY_BOUNDARY_VIOLATION` | canonical ID contract | No | Yes | `TERMINAL` | one attempt | withheld |
| `ANALYTICS_PROJECTION_FAILURE` | Analytics | Yes until exhausted | On exhaustion | `RETRYABLE`/`TERMINAL` | one attempt | no committed Analytics |
| `LINEUP_PERSISTENCE_FAILURE` | DB/domain | Yes | On exhaustion | `RETRYABLE`/`TERMINAL` | one attempt | rollback |
| `TRANSACTION_FAILURE` | outer transaction | Yes | On exhaustion | `RETRYABLE`/`TERMINAL` | one attempt | rollback |
| `COMMIT_AMBIGUITY` | commit outcome | Verification-dependent | If confirmed failure/exhausted | verification state | already counted | no assumption |
| `MAX_RETRY_ATTEMPTS_EXCEEDED` | finalization | No | Yes | `TERMINAL` | current attempt only | incomplete Analytics withheld |

## 19. Required Example Scenarios

### Case 1: 45 valid IDs

```text
45 valid IDs
-> 45 resolver outcomes
-> complete lineup
-> Analytics projection
-> SUCCESS
```

### Case 2: 43 valid IDs, 2 missing

```text
43 processed
2 tracked
-> PARTIAL domain state
-> RETRYABLE persistence
-> no Analytics publication
```

### Case 3: one of two missing IDs resolves

```text
1 resolved
1 still missing
-> PARTIAL
-> RETRYABLE
-> no SUCCESS
```

### Case 4: all missing IDs resolve

```text
all identities valid
-> complete lineup
-> complete Analytics replacement
-> SUCCESS
```

### Case 5: provider timeout

```text
provider timeout
-> PROVIDER_FAILURE
-> RETRYABLE
-> rollback
```

### Case 6: resolver conflict

```text
resolver conflict
-> MASTER_RESOLUTION_FAILURE
-> TERMINAL
-> no overwrite or duplicate
```

### Case 7: Analytics projection failure

```text
Analytics failure
-> rollback lineup/Analytics transaction
-> ANALYTICS_PROJECTION_FAILURE
-> RETRYABLE until exhausted
```

### Case 8: concurrent request

```text
Worker A -> lock acquired
Worker B -> rejected/skipped
attempt count consumed only by A
```

### Case 9: maximum retries exhausted

```text
retryable attempt reaches limit
-> MAX_RETRY_ATTEMPTS_EXCEEDED
-> TERMINAL
```

## 20. Frozen Invariants

```text
INV-S01 SUCCESS requires zero unresolved lineup Player identities.
INV-S02 PARTIAL never equals SUCCESS.
INV-S03 Missing Player identity never creates Player Master.
INV-S04 Missing Player identity never enters Analytics.
INV-S05 Valid Players continue processing when other Players are missing.
INV-S06 Missing identities remain explicitly trackable.
INV-S07 Retry operates at lineup/finalization level, not per-Player attempt count.
INV-S08 Lock rejection does not consume retry attempts.
INV-S09 Analytics only receives canonical local player_id.
INV-S10 EventSync remains independent.
INV-S11 No cache invalidation occurs for uncommitted state.
INV-S12 Historical data is not modified by this implementation phase.
INV-S13 No fixture-specific state handling is allowed.
INV-S14 No provider-specific Player workaround is allowed.
INV-S15 CREATE_NEW always uses PlayerSyncService and PlayerRepository.
INV-S16 A missing identity is never replaced by zero, negative, fake, name-derived, or local Player ID.
INV-S17 A partial lineup never publishes complete Analytics state.
INV-S18 Complete retry replaces the full match projection deterministically and idempotently.
INV-S19 One locked finalization attempt increments attempt_count once.
INV-S20 Terminal records are not automatically retried.
INV-S21 Event failure cannot mutate lineup state through this contract.
INV-S22 Lineup failure cannot mutate Event state through this contract.
```

## 21. Implementation Constraints for PHASE 6.3

PHASE 6.3 must:

* implement the matrix exactly as written
* preserve the shared Player Resolver boundary
* normalize valid provider Player data before Player persistence
* skip and track missing provider identities without creating Player Master rows
* process valid Players independently of missing Players
* persist partial evidence through the lineup/finalization-owned tracking contract
* map domain `PARTIAL` to existing `RETRYABLE` persistence unless a separately approved schema decision changes that mapping
* withhold Analytics until the lineup is complete
* use the existing `lineup:{match_id}` lock
* increment attempts once per locked finalization attempt
* preserve outer transaction ownership
* preserve Event independence
* preserve post-commit cache invalidation
* avoid historical repair, fixture-specific logic, and manual data mutation

PHASE 6.3 must not introduce new state semantics. If implementation discovers a contradiction with this matrix, implementation must stop and return to architecture review.

## 22. Final Decision

Every relevant state is defined. Every relevant failure is classified. Player-level missing identity is separated from lineup-level invalidity. Partial semantics, retry semantics, attempt-count semantics, Analytics behavior, transaction behavior, locking, cache behavior, Event independence, and maximum retry behavior are frozen.

No production code, schema, migration, tests, database data, historical finalization record, retry counter, or runtime behavior was changed.

```text
PHASE 6.2 = PASS
FAILURE / STATE MATRIX = FROZEN
NEXT PHASE = PHASE 6.3 — IMPLEMENTATION
```
