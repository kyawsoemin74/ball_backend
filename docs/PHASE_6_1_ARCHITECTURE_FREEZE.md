# PHASE 6.1 - ARCHITECTURE FREEZE

## Partial Lineup Acceptance + Missing Identity Retry

**Status:** PASS  
**Architecture:** FROZEN  
**Next phase:** PHASE 6.2 - FAILURE / STATE MATRIX FREEZE

This document defines the architecture for provider lineup players whose provider identity is incomplete. It is an architecture decision record only. No production code, schema, migration, database row, retry counter, finalization record, or runtime behavior is changed by this phase.

## A. Current Architecture Audit

### A.1 Verified runtime flow

```text
Provider LineupProvider
  -> LineupSyncService
  -> PlayerIdentityResolutionService
  -> PlayerSyncService / PlayerRepository when CREATE_NEW
  -> match_lineups.data JSON
  -> AnalyticsProjectionService.project_lineup
  -> AnalyticsLineupRepository.replace_by_match
  -> outer transaction commit
  -> post-commit cache invalidation
```

The current lineup sync validates the provider response, resolves provider teams, resolves every lineup Player, persists the canonical lineup, and invokes Analytics projection in the same outer transaction. Analytics consumes local `player_id` values and does not resolve or create Players.

### A.2 Current production entry points

1. Manual API lineup synchronization:

```text
POST /sync/{match_id}/lineup
  -> run_with_resource_lock(db, "lineup", match_id, ...)
  -> FootballAPIService.sync_match_lineup
  -> LineupSyncService.sync_lineup
  -> outer commit
  -> lineup cache invalidation
```

2. Scheduler lineup refresh:

```text
LiveUpdateScheduler._refresh_lineups_job
  -> cooldown check
  -> run_with_resource_lock(db, "lineup", match_id, ...)
  -> FootballAPIService.sync_match_lineup
  -> LineupSyncService
  -> Analytics projection
  -> commit
  -> cache invalidation
```

3. Fixture terminal transition:

```text
FixtureSyncService._process_sync_with_candidates
  -> Match status transition to FT/AET/PEN
  -> FinalLineupFinalizationRepository.create_required
  -> final_lineup_candidates
```

4. Finalization retry:

```text
FixtureSyncService.finalize_pending_lineups
  -> FinalLineupFinalizationRepository.get_retry_candidates
  -> run_with_resource_lock(db, "lineup", match_id, ...)
  -> _run_final_lineup_attempt
  -> FootballAPIService.sync_match_lineup
  -> LineupSyncService
  -> Analytics projection
  -> mark_success
  -> commit
  -> post-commit cache invalidation
```

5. Direct service invocation used by production facades:

```text
FootballAPIService.sync_match_lineup
  -> LineupSyncService.sync_lineup
```

No alternate provider-backed lineup writer was found.

### A.3 Current identity behavior

`PlayerIdentityResolutionService.resolve_lineup` currently expects a provider Player ID. Missing or invalid identity produces a non-ready result and `LineupSyncService` rejects the entire lineup before persistence. A valid identity uses the shared resolver. `CREATE_NEW` is routed through `PlayerSyncService`, which normalizes the Player model payload before calling `PlayerRepository`.

### A.4 Current finalization behavior

The implemented database states are:

```text
REQUIRED, RUNNING, RETRYABLE, TERMINAL, SUCCESS
```

`PARTIAL` is not currently an implemented database state. `MASTER_RESOLUTION_FAILURE`, `IDENTITY_BOUNDARY_VIOLATION`, and maximum-attempt exhaustion are terminal under the current orchestration. Provider and invalid-response failures can remain retryable.

### A.5 Current transaction, lock, and cache behavior

* The outer API, scheduler, or finalization orchestration owns commit and rollback.
* Player and Analytics repositories do not own global transactions.
* The resolver does not commit or roll back.
* `lineup:{match_id}` is implemented through the shared PostgreSQL advisory-lock helper.
* Lock release occurs in a `finally` block.
* Cache invalidation occurs after successful commit in the owning caller.
* Event synchronization uses an independent `events:{match_id}` lock and transaction.

## B. Problem Definition

A provider lineup can be structurally usable while one or more Player entries lack a valid provider identity. The current implementation treats that Player-level condition as a whole-lineup non-ready result. This prevents valid Players from being processed and does not preserve enough structured information to make the missing identities retryable.

The architecture must distinguish:

```text
PLAYER-LEVEL MISSING IDENTITY
```

from:

```text
LINEUP-LEVEL INVALID RESPONSE
```

A missing provider Player ID must never become a guessed, fake, local, or placeholder identity.

## C. New Architecture

The frozen target flow is:

```text
Provider lineup
  -> structural validation
  -> team and match identity validation
  -> per-player provider identity validation
  -> valid players: shared Player Identity Resolver
  -> valid players: canonical local player_id
  -> missing players: skip and track Missing Lineup Identity
  -> canonical partial lineup persistence
  -> PARTIAL / retryable finalization when missing identities remain
  -> Analytics projection of valid canonical rows only
  -> later provider retry
  -> all identities complete
  -> full canonical lineup replacement
  -> full Analytics replacement
  -> SUCCESS after commit
```

The existing service/repository/transaction/lock architecture remains the ownership model. The new behavior is a refinement of per-player acceptance and finalization state, not a redesign of the system boundary.

## D. Identity Boundary

### D.1 Valid provider identity

A provider Player ID is valid only when it is present, non-empty, and a positive provider identity according to the provider contract.

```text
(provider, provider_player_id)
  -> PlayerIdentityResolutionService
  -> RESOLVED_EXISTING or CREATE_NEW
  -> local player_id
```

Resolver outcomes remain:

```text
RESOLVED_EXISTING
CREATE_NEW
AMBIGUOUS
CONFLICT
INVALID
```

Valid outcomes are handled as follows:

* `RESOLVED_EXISTING`: reuse the returned local `player_id`.
* `CREATE_NEW`: call `PlayerSyncService`, then `PlayerRepository`; never call the repository directly from lineup orchestration.
* `AMBIGUOUS`: do not process that Player; track an unresolved identity and keep the lineup retryable/partial.
* `CONFLICT`: do not process that Player; track the conflict according to the finalization failure matrix.
* `INVALID`: do not process that Player; classify invalid identity explicitly.

### D.2 Missing provider identity

When `player.id` is null, empty, non-positive, or otherwise unusable:

```text
Provider Player
  -> Missing Identity Validation
  -> SKIP this Player
  -> Track Missing Lineup Identity
  -> continue valid Player processing
```

The missing Player must not:

* enter the normal resolver
* be matched by name
* receive a fake ID
* receive `0` or a negative placeholder
* receive a local Player ID as provider ID
* create a Player Master row
* create an Analytics row
* be silently discarded

## E. Partial Acceptance Contract

A structurally valid lineup is accepted as `PARTIAL` when:

* match identity is valid
* every provider team identity is valid
* lineup sections are structurally valid
* at least one Player entry is structurally processable
* valid Player entries resolve to canonical local IDs
* one or more Player entries have missing or retryable identity state
* every missing/unresolved entry is explicitly tracked

Example:

```text
45 provider lineup entries
43 valid provider IDs -> resolver -> local player_id
2 missing provider IDs -> skipped and tracked

lineup state = PARTIAL
```

Structural invalidity is not partial acceptance. The following remain lineup-level failures:

* missing or invalid match identity
* invalid team identity
* malformed response or lineup section
* invalid provider team relationship
* database or transaction failure
* analytics persistence failure

### E.1 Partial persistence

The canonical source lineup may persist valid resolved entries plus explicit missing-identity metadata. The exact storage representation is a Phase 6.2 implementation decision and must preserve the following rules:

* valid entries retain provider metadata and local `player_id`
* missing entries retain enough non-identity evidence for retry tracking
* missing entries never contain a fake local or provider identity
* a partial source lineup is never presented as complete

### E.2 Partial Analytics

Analytics remains canonical-ID-only. During `PARTIAL`, Analytics may contain only valid resolved lineup Players if and only if the projection contract can represent the source as partial without implying completeness. The preferred frozen rule is:

* projection is replace-based
* only valid canonical rows are projected
* missing entries are not projected
* projection metadata/state must indicate partial completeness
* later complete retry replaces the partial Analytics scope with the complete canonical lineup
* `PARTIAL` never becomes `SUCCESS`

If the existing Analytics schema cannot represent partial completeness without ambiguity, Analytics publication for `PARTIAL` must be withheld while the valid canonical lineup and missing-identity tracking remain retryable. Phase 6.2 must choose one of these two explicit options before implementation.

## F. Missing Identity Tracking Contract

Missing-identity tracking belongs to the **Lineup Finalization / Lineup Sync domain**, not Player Master and not Analytics.

A tracking record must retain:

* local match ID
* provider fixture ID where available
* local team ID where available
* provider team ID
* lineup side/team context
* roster role
* lineup position
* Player name where available
* shirt number where available
* position where available
* grid where available
* provider namespace
* raw identity state (`missing`, `empty`, `invalid`)
* first observed timestamp
* last observed timestamp
* observation count
* retry eligibility
* completion condition
* current resolution state

The preferred ownership is a dedicated missing-lineup-identity collection owned by the lineup/finalization domain. A new persistence structure is **PROPOSED only** and must not be created in Phase 6.1.

The finalization record remains the retry-discovery anchor. Missing-identity details must be linked to that match/finalization identity and must not be encoded as a fake Player or Analytics row.

## G. Lineup State Machine

The frozen logical state machine is:

```text
REQUIRED
   -> RUNNING
   -> PARTIAL
   -> RETRY
   -> RUNNING
   -> SUCCESS
```

The existing physical database state `RETRYABLE` represents retry eligibility. Phase 6.2 must decide whether `PARTIAL` is:

1. a persisted finalization state requiring a schema/state constraint update, or
2. a domain result/failure category mapped to existing `RETRYABLE` while partial details are stored separately.

No new state is implemented here.

### G.1 State classification

* `REQUIRED`: normal finalization work is required.
* `RUNNING`: one locked finalization attempt is in progress.
* `PARTIAL`: structurally valid lineup with one or more missing Player identities; retryable, never successful.
* `RETRY`: logical retry eligibility; represented by the existing retryable mechanism until Phase 6.2 freezes its storage mapping.
* `SUCCESS`: complete lineup, complete identity resolution, successful Analytics projection, successful commit.
* `TERMINAL`: no automatic retry, including identity ambiguity/conflict according to the final failure matrix or maximum attempts.

## H. Success Condition

`SUCCESS` is permitted only when all conditions hold:

1. provider lineup structure is valid
2. local match identity is valid
3. provider and local team identities are valid
4. every Player entry has a valid provider identity
5. every provider Player resolves to a canonical local `player_id`
6. no missing or unresolved Player identity remains
7. canonical lineup persistence succeeds
8. Analytics projection succeeds when required
9. database constraints pass
10. outer transaction commits successfully
11. commit outcome is verified where ambiguity exists
12. post-commit cache invalidation follows the existing contract

```text
PARTIAL != SUCCESS
```

## I. Retry Completion Contract

On retry:

```text
PARTIAL / RETRYABLE
  -> provider lineup fetch
  -> validate every previously missing identity
```

If any identity remains incomplete:

```text
PARTIAL / RETRYABLE
  -> update tracking evidence
  -> remain retryable
  -> no SUCCESS
```

If all identities become valid:

```text
PARTIAL / RETRYABLE
  -> resolver for newly valid identities
  -> complete canonical lineup
  -> replace complete Analytics projection
  -> finalization SUCCESS
  -> commit
  -> cache invalidation
```

Retry is rediscovered through the existing finalization retry mechanism. No manual database status mutation is allowed.

## J. Player Master Protection

```text
Missing provider Player ID
  -> no Player Master record
```

```text
Valid provider Player ID
  -> shared resolver
  -> existing Player or PlayerSyncService CREATE_NEW
```

The Player Master remains the canonical identity boundary. No placeholder identity is permitted.

## K. Analytics Contract

Analytics consumes only canonical local `player_id` values.

Analytics must never:

* create Player Master records
* resolve provider identities
* infer missing identities
* use provider IDs as local FKs
* create orphan rows

For `PARTIAL`:

* missing Players are excluded from Analytics
* valid canonical Players may be projected only if partial completeness is explicitly represented by the frozen Phase 6.2 contract
* projection remains replace-based and idempotent
* a later complete retry replaces the partial projection with the complete projection
* no Analytics row is created for a missing identity

## L. Locking Contract

The existing lock remains:

```text
lineup:{match_id}
```

The lock protects:

* partial processing
* duplicate manual requests
* scheduler retries
* finalization retries
* partial-to-complete transitions
* Analytics replacement

Concurrent partial retries, duplicate requests, scheduler requests, and finalization retries use the same lock. Only one owner may mutate lineup/finalization/Analytics state at a time. Lock contention does not increment the finalization attempt count.

## M. Transaction Contract

The preserved boundary is:

```text
Provider read
  -> validation
  -> per-player identity resolution
  -> missing identity tracking
  -> canonical lineup write
  -> Analytics projection or explicitly withheld partial publication
  -> finalization state update
  -> flush
  -> outer commit
  -> post-commit cache invalidation
```

Rollback is owned by the outer orchestration layer:

* provider failure: rollback or retry metadata transaction according to finalization contract
* identity resolution failure: no invalid Player/Analytics FK; partial tracking may remain retryable
* lineup persistence failure: rollback
* Analytics failure: rollback lineup/Analytics transaction
* transaction failure: rollback
* commit ambiguity: verification path; never assume SUCCESS

Resolver, repositories, and Analytics projection do not own global commit/rollback.

## N. Event Independence

Event synchronization remains independent:

```text
Event Provider
  -> EventSyncService
  -> match_events
  -> events:{match_id} lock
  -> event transaction
  -> event cache invalidation
```

Lineup `PARTIAL` does not trigger or require EventSync. Event failures do not change lineup state, and lineup failures do not corrupt event data.

## O. Historical Data Protection

Phase 6.1 performs no historical repair.

It does not:

* rebuild old lineups
* rewrite old finalization records
* reset attempts
* delete historical data
* mark records successful
* reassign Players
* insert Analytics rows

Any historical reprocessing is a separate authoritative Provider -> Resolver -> Lineup -> Analytics phase.

## P. Failure Ownership

| Condition | Owner | Classification | Partial allowed? |
|---|---|---|---|
| Missing Player provider ID | Lineup identity boundary | missing identity, retryable partial | Yes |
| Empty/invalid Player provider ID | Lineup identity boundary | invalid identity, retryable or terminal per matrix | Only if explicitly tracked |
| Resolver ambiguity | Player identity boundary | `AMBIGUOUS` / terminal policy | No silent success |
| Resolver conflict | Player identity boundary | `CONFLICT` / terminal policy | No silent success |
| Invalid lineup structure | Lineup validation | invalid provider response | No |
| Missing team identity | Team identity boundary | team resolution failure | No |
| Provider unavailable | Provider/LineupSync | provider failure | No persisted success |
| Analytics failure | Analytics projection | analytics failure | No SUCCESS |
| Database failure | Outer transaction | database failure | Rollback |
| Lock contention | Resource lock | lock conflict | No attempt increment |
| Maximum attempts | Finalization | terminal exhaustion | No further retry |

## Q. Invariants

```text
INV-01 Every persisted Player Master record has a valid provider identity.
INV-02 Missing provider Player identity never creates a Player Master record.
INV-03 Every Analytics Player reference is a canonical local player_id.
INV-04 LineupSyncService never bypasses PlayerSyncService for CREATE_NEW.
INV-05 PARTIAL is never treated as SUCCESS.
INV-06 SUCCESS requires zero unresolved Player identities.
INV-07 Valid Players continue processing when other entries are missing identity.
INV-08 Missing identities are explicitly trackable and retryable.
INV-09 Retry does not require manual database mutation.
INV-10 Lineup processing remains protected by lineup:{match_id}.
INV-11 EventSync remains independent.
INV-12 Historical records are not modified by this architecture phase.
INV-13 No fixture-specific workaround is introduced.
INV-14 No provider-specific hard-coded Player IDs are introduced.
INV-15 Existing transaction ownership remains intact.
INV-16 Missing identity is never represented by zero, negative, fake, or local Player ID.
INV-17 A partial Analytics projection never implies lineup completeness.
INV-18 A complete retry replaces partial scope deterministically and idempotently.
INV-19 Lock contention does not increment finalization attempt count.
INV-20 Cache invalidation is never published as successful state before commit.
```

## R. Frozen Decisions for Phase 6.2

The following decisions are frozen for implementation:

1. `PARTIAL` is a domain state mapped to the existing persisted `RETRYABLE` finalization state. No new database status is required by this architecture freeze.
2. Partial lineups do not publish Analytics rows. Analytics publication is withheld until every lineup Player has a canonical local `player_id`; the first complete retry replaces the full match scope.
3. Missing-identity details belong in a dedicated lineup/finalization tracking structure proposed for Phase 6.2. The finalization record remains the retry-discovery anchor; the tracking structure owns per-entry evidence and completion state.
4. A finalization attempt increments exactly once when the locked attempt enters `RUNNING`. Discovery and lock contention do not increment attempts. A partial result consumes the current attempt and remains retryable.
5. Missing provider identity is partial/retryable. `AMBIGUOUS`, `CONFLICT`, and invalid present identities remain terminal identity failures unless the Phase 6.2 matrix explicitly proves a provider-retryable classification for a distinct provider condition.

These choices remove the remaining architectural ambiguity without implementing the state or schema changes in Phase 6.1.

## S. Phase 6.1 Decision

The current architecture has been audited. The partial acceptance boundary, missing identity behavior, tracking ownership, state model, retry completion rule, Player Master protection, Analytics contract, lock behavior, transaction ownership, Event independence, historical protection, invariants, and Phase 6.2 implementation decisions are defined.

No production code, database schema, migration, existing data, retry counter, finalization record, or runtime behavior was modified.

```text
PHASE 6.1 = PASS
ARCHITECTURE = FROZEN
NEXT PHASE = PHASE 6.2 — FAILURE / STATE MATRIX FREEZE
```
