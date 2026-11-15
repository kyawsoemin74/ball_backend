# Normal `live_sync` Design Freeze

**Approval date:** 2026-11-13  
**Scope:** Approved target design for a future implementation task  
**Current task:** Design approval and freeze only

## 1. Design Approval Statement

The Normal `live_sync` Target Design documented in the reference audit is approved as the baseline for a separate future implementation phase. Implementation must follow the requirements in this freeze and preserve the existing Fover backend architecture:

`Provider → Service (Read) → SyncService (Write Orchestration) → Repository (Persistence) → Database`

This approval does not authorize implementation as part of this task. No application code, database schema, migrations, scheduler configuration, resource-lock implementation, or production data may be changed under this approval-only task.

## 2. Reference Audit Report

The approved source of design findings and recommendations is:

- [`docs/NORMAL_LIVE_SYNC_TARGET_DESIGN_AUDIT.md`](./NORMAL_LIVE_SYNC_TARGET_DESIGN_AUDIT.md)

Its verdict is **READY AFTER DESIGN ADJUSTMENT**. The audit confirms that terminal-result regression is possible and that protection must cover both Normal Live Sync and other existing fixture update paths, including daily/repair and season fixture upserts.

## 3. Approved Target Architecture

The existing architecture and module responsibilities remain in force:

1. **Provider:** retrieve provider data; no new provider or polling architecture.
2. **Service (Read):** preserve provider access and fixture retrieval responsibilities.
3. **SyncService (Write Orchestration):** coordinate the existing live, finalization, daily/repair, and fixture sync workflows.
4. **Repository (Persistence):** enforce result-field integrity atomically at the database write boundary.
5. **Database:** reuse the existing schema and PostgreSQL transaction/locking capabilities.

Do not introduce a duplicate synchronization service or framework. Keep provider reads and writes in their existing layers and use the existing persistence and resource-lock mechanisms where appropriate.

## 4. Approved Functional Requirements

- Protect finalized match results from stale non-terminal provider responses.
- Apply protection consistently to all existing workflows that write match result data, not only Normal Live Sync.
- Coordinate competing match-result writes using one consistent per-match lock identity through the existing resource-lock mechanism where appropriate.
- Enforce data integrity atomically in the database write path; a service-only precheck is not sufficient.
- Preserve the distinction between `FT`, `AET`, and `PEN`.
- Keep `P` non-terminal; never infer or convert `P` to `PEN`.
- Permit only explicitly approved non-result metadata updates after finalization.
- Reuse the current database schema unless implementation investigation proves a schema change technically necessary. Any migration or table proposal requires explanation and explicit approval before proceeding.

## 5. Terminal Status Protection Rules

The protected finalized statuses are exactly:

- `FT` — finished in regular time.
- `AET` — finished after extra time.
- `PEN` — finished after a penalty shootout.

The approved transition rules are:

1. A local `FT`, `AET`, or `PEN` match must not be reverted by a stale non-terminal provider status.
2. `P` remains a non-terminal status and must not be treated as final.
3. The system must never convert `P` to `PEN` without provider confirmation.
4. The distinction between `FT`, `AET`, and `PEN` must be preserved; do not collapse them into one generic terminal status.
5. A valid provider terminal transition from a non-terminal local status must remain persistable.
6. Finalization state such as `PENDING`, `RUNNING`, or `SUCCESS` is not a substitute for protecting the Match result row atomically.

Any separate policy for terminal-to-terminal corrections must be made explicit during implementation; it must not weaken the approved protection against stale non-terminal regression.

## 6. Finalized Result Data Protection Rules

After a match is finalized, protect the complete result data, including:

- Match status.
- Home and away scores.
- Elapsed time.
- Penalty scores where the existing persistence path stores them.
- Any other existing fields that represent the finalized match result.

Treat related result fields as one coherent result snapshot. Protecting only the status while allowing stale scores or elapsed time to overwrite would not satisfy this freeze.

Do not block all Match updates by default. Non-result metadata may be updated only when it is explicitly approved and independently validated. Identity and scheduling metadata—including provider/local match identity, league/team identity, season, and fixture time—must not be blindly refreshed from a stale payload. If a persistence operation updates both metadata and result fields, it must preserve the finalized result while applying only approved metadata changes.

Penalty scores are subject to protection wherever the existing implementation persists them; this freeze does not authorize adding new canonical Match columns.

## 7. Synchronization Writer Coordination

Protection applies to every existing workflow that can modify result data, including:

- Normal Live Sync.
- Finalization Sync.
- Daily Fixture Sync.
- Daily Repair.
- Full Season / Fixture Upsert.
- Any other existing writer identified during implementation investigation.

Do not protect only the Normal Live Sync repository update while leaving fixture upsert or another existing path able to replace finalized results. A status check performed before persistence may support orchestration or reporting, but is not an adequate concurrency or integrity guard by itself.

## 8. Shared Lock Strategy

- Reuse the existing resource-lock mechanism; do not introduce a new locking system.
- Establish one consistent per-match lock identity for relevant synchronization writers, based on the canonical local Match identity.
- Ensure competing writers use the same lock identity and hold the transaction-scoped lock through the protected database write and its commit/rollback.
- Keep existing scheduler-level locks for their job-coalescing role; they do not replace per-match coordination across workflows with different job scopes.
- Provider network reads should remain outside a per-match persistence lock where feasible, to avoid holding that lock during provider latency.
- A lock conflict must not be treated as a successful write. The affected work must be skipped/deferred observably and remain eligible for a subsequent attempt.

## 9. Database Integrity Strategy

- Apply terminal-result protection atomically at the existing repository/persistence boundary.
- Ensure both direct result updates and fixture conflict-upsert writes follow the same approved rule.
- Do not rely solely on a service-layer read of local status; concurrent writers may change the row after that read.
- Use the existing transaction architecture and PostgreSQL locking capabilities.
- A separate row-lock approach may be considered only if all competing writers consistently acquire it before reading/writing and retain it through commit. The preferred baseline is an atomic database write condition combined with the shared existing per-match resource lock.
- Reuse the existing schema. Do not add a table or Alembic migration unless implementation investigation proves it technically necessary and explicit approval is obtained first.

## 10. Explicitly Excluded Changes

This design freeze does not approve:

- Replacing or redesigning the synchronization architecture.
- Adding a duplicate live synchronization service, framework, queue, worker, or table.
- Adding a database migration or changing the schema without explicit approval.
- Changing provider polling frequency, endpoint usage, or retry policy as an unrelated enhancement.
- Changing scheduler configuration or resource-lock implementation beyond the approved use of existing locks in a future implementation task.
- Modifying unrelated or frozen modules.
- Blocking all post-finalization Match metadata updates without field-level review.
- Inferring provider status from elapsed time or converting `P` into `PEN`.
- Assuming provider timestamps or versions establish snapshot ordering unless verified.

## 11. Implementation Constraints

For any future implementation:

1. Treat this document as the approved baseline; do not silently change its requirements or scope.
2. Preserve existing behavior for valid nonterminal updates and valid `LIVE → FT/AET/PEN` transitions.
3. Protect result fields as a coherent unit, while permitting only explicitly approved metadata updates.
4. Apply the same integrity rule across every relevant existing result writer.
5. Reuse shared per-match locking and ensure lock lifetime is aligned with the protected transaction.
6. Make stale/protected updates distinguishable from missing rows so stale input is not published as the current active-match state.
7. Add focused regression and concurrency tests before implementation is considered complete.
8. If an implementation blocker appears—especially a need for schema changes or a conflict with a frozen module—stop and request explicit approval before changing the design.
9. Do not add unrelated improvements or expand the approved scope.

## 12. Future Implementation Scope

A future implementation task may:

1. Confirm any unresolved terminal-to-terminal correction and metadata allowlist policies without weakening this freeze.
2. Update the existing repository/persistence paths to enforce atomic finalized-result protection.
3. Apply the same persistence policy to Normal Live Sync, finalization, daily/repair, fixture upsert, and any other relevant existing writer.
4. Wire those workflows to the same per-match resource-lock identity.
5. Preserve scheduler-level job locks and current architecture.
6. Add focused tests for stale terminal regression, result-field preservation, valid terminal transitions, writer coordination, and lock behavior.

No implementation work is included in this design approval task.

**DESIGN STATUS: APPROVED & FROZEN**

**IMPLEMENTATION STATUS: NOT STARTED**

**REFERENCE AUDIT: READY AFTER DESIGN ADJUSTMENT**
