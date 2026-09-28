# Phase 3 Async DB / Lock Lifecycle Fix Report

## Objective

Fix the async database session and transaction-scoped advisory lock lifecycle issue causing the runtime crash: "Event loop is closed" inside the resource lock acquisition path, without redesigning the scheduler or odds sync flow.

## Root Cause

The failure was caused by loop-bound async database resources being reused across different asyncio event loops. In practice, the engine/session infrastructure could survive a previous loop shutdown and later be accessed from a new loop, leading to asyncpg attempting to operate on a connection or session still bound to a closed event loop.

This occurred before business logic progression, so the odds auto-sync path could not acquire the PostgreSQL transaction-scoped advisory lock and failed during DB access rather than during odds matching logic.

## Fix Applied

The fix is contained to the async DB lifecycle layer in [app/db/__init__.py](../app/db/__init__.py).

Key changes:

- Engine creation is now loop-aware.
- The active engine is recreated when the current running loop changes.
- Session creation uses the current loop-bound engine rather than reusing a stale loop-bound engine from a previous run.
- The lock path remains transaction-scoped and unchanged in intent: PostgreSQL advisory locks are still acquired within the active transaction and released automatically on commit or rollback.

This preserves the existing odds auto-sync scheduler flow while preventing stale connections from a closed loop from being reused.

## Scope Guardrail

This fix intentionally does not expand into broader odds or scheduler redesign.

The objective was limited to:

- making the async DB/session lifecycle safe and loop-stable
- keeping PostgreSQL advisory lock semantics intact
- allowing the existing odds auto-sync path to run normally again

## Verification

Verified with the focused regression suite:

- Command: `.\.venv\Scripts\python.exe -m pytest tests/test_odds_phase3_hardening.py -q`
- Result: 7 passed in 1.39s

This includes the key protections for:

- valid-loop lock acquisition
- transaction-scoped lock release on commit/rollback
- cross-loop safety across separate asyncio.run() executions
- rollback-safe odds state behavior
- odds sync persistence and idempotency checks

## Status

Status: PASS for the async DB/session lifecycle blocker.

Important note: the repository still contains unrelated pre-existing failures outside this scope; they are not caused by the event-loop lifecycle fix and were intentionally not expanded into this task.
