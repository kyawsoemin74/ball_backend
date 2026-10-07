# Phase 3 Event Team ID Canonical Identity Implementation Report

**Status:** PARTIAL  
**Date:** 2026-10-07  
**Scope:** New Event sync identity handling; no schema migration

## 1. Implementation Summary

Event synchronization now treats provider `event.team.id` only as an external
API-Football identity. `EventSyncService` resolves it using Team Master, checks
that the resolved local Team is one of the Match sides, then passes an explicit
`canonical_team_id` to `EventRepository`.

The repository validates that every replacement Event contains a positive
integer canonical ID before deleting the previous snapshot, and persists only
that value as `match_events.team_id`. The sync result also replaces the
provider-shaped `team.id` with the canonical value so it cannot echo the raw
provider ID as if it were local.

No model, schema, migration, Event read-service, API route, scheduler, lock, or
cache policy was changed.

## 2. Files Changed

Implementation and test files:

* [`app/services/event_sync_service.py`](../app/services/event_sync_service.py)
* [`app/repositories/event_repository.py`](../app/repositories/event_repository.py)
* [`tests/test_event_team_identity.py`](../tests/test_event_team_identity.py)
* [`tests/test_event_service_freshness.py`](../tests/test_event_service_freshness.py)

Required Phase 3 report:

* [`docs/PHASE_3_EVENT_TEAM_ID_CANONICAL_IDENTITY_IMPLEMENTATION_REPORT.md`](./PHASE_3_EVENT_TEAM_ID_CANONICAL_IDENTITY_IMPLEMENTATION_REPORT.md)

The Phase 2 freeze document
[`docs/EVENT_TEAM_ID_CANONICAL_IDENTITY_DESIGN_FREEZE.md`](./EVENT_TEAM_ID_CANONICAL_IDENTITY_DESIGN_FREEZE.md)
was already present as an untracked file before Phase 3 edits; it was not
changed in this phase.

No other production files were changed.

## 3. Before/After Data Flow

**Before**

```text
provider event.team.id
        ↓ unchanged
EventRepository reads event.team.id
        ↓
match_events.team_id
        ↓ unchanged
EventService/API
```

**After**

```text
provider event.team.id
        ↓
TeamRepository.find_by_provider_identity(
    db, "api-football", provider_team_id
)
        ↓
Team.team_id (canonical)
        ↓ Match-side validation
EventSyncService canonical_team_id
        ↓
EventRepository
        ↓
match_events.team_id
        ↓ unchanged
EventService/API
```

## 4. Team Master Resolution

`EventSyncService` receives an optional existing `TeamRepository` dependency
for testing/injection and defaults to `TeamRepository()`. Each validated event
uses:

```python
find_by_provider_identity(db, "api-football", provider_team_id)
```

No second Team resolver, mapping table, hard-coded mapping, or API-layer
correction was added. The Match sides are used only for consistency validation,
not as a replacement for Team Master identity resolution.

## 5. Failure Handling

If Team Master returns no Team, sync logs `TEAM_IDENTITY_MISSING` with the
Match and provider ID and returns:

```json
{"success": false, "message": "TEAM_IDENTITY_MISSING"}
```

If Team Master resolves a Team that is not either side of the Match, sync logs
`EVENT_TEAM_IDENTITY_MISMATCH` with canonical and Match-side IDs and returns
that failure reason.

Both checks happen before `replace_match_events`; the full snapshot fails and
the existing snapshot is not replaced. No provider ID fallback is used.

## 6. Match Ownership Validation

The service reads the Match by `local_match_id` and requires the resolved
canonical Team ID to equal `home_team_id` or `away_team_id`. The same rule is
applied to all validated Event types; there is no type-specific identity path.

## 7. Transaction Safety

`EventSyncService` still flushes after repository replacement and does not
commit or rollback. Existing callers retain transaction ownership. Missing or
mismatched Team identity returns before replacement, allowing the current
caller failure/rollback behavior to remain in effect.

## 8. Lock Preservation

No lock implementation or call site changed. Admin, scheduler, and
fixture/finalization flows retain the existing per-match `"events"` resource
lock. The controlled provider sync below also used that existing lock.

## 9. Cache Preservation

No cache key, read behavior, cache owner, or invalidation policy changed.
Existing callers continue invalidating the per-match Event cache after a
successful commit. The controlled sync invalidated
`fover:match:476:events`, then verified the EventService API-facing read result.

## 10. Test Results

Focused Event tests:

```text
39 passed, 3 warnings
```

Command:

```text
python -m pytest tests/test_event_team_identity.py
    tests/test_event_service_freshness.py
    tests/test_events_lock_alignment.py -q
```

Coverage includes provider-ID resolution, canonical repository persistence,
home/away teams, multiple Events, unresolved and mismatched IDs, Goal/Card/
Substitution/VAR/Penalty types, replacement, EventService serialization,
locking, cache invalidation, and transaction ownership.

Broader suite:

* Initial collection failed in four unrelated modules:
  `test_live_active_registry_phase4.py` (missing `patch` import),
  `test_odds_identity_mapping.py` (missing module),
  `test_odds_identity_resolution_bridge.py` (missing constant), and
  `test_odds_phase3_hardening.py` (missing constant).
* Excluding those four collection failures, the suite reported **670 passed
  and 24 failed**. The failures were in unrelated allowed-league, odds,
  standings, team-profile, and H2H test areas; no Event Team Identity test
  appears among the failures.

The broad backend test suite is therefore not green.

## 11. Real DB Verification

Read-only DB checks succeeded using local configuration without displaying
credential values. Before the controlled sync, local Match `476` had:

| Field | Value |
|---|---:|
| Provider fixture ID | `1528952` |
| `matches.home_team_id` | `25666` |
| `matches.away_team_id` | `25647` |
| Home provider Team ID | `3` |
| Away provider Team ID | `9` |
| Existing Event rows for this Match | `0` |

Team Master returned:

```text
provider Team ID 3 → teams.team_id 25666
provider Team ID 9 → teams.team_id 25647
```

After sync, all 18 persisted `match_events.team_id` values were in
`{25666, 25647}`. The EventService API-facing read returned 18 Events and the
same two canonical IDs.

## 12. Real Provider Sync Evidence

A read-only API-Football fetch for provider fixture `1528952` returned 18
Events, including provider Team IDs `3` (Croatia) and `9` (Spain). A controlled
sync then ran under the existing Event resource lock for local Match `476`.

Observed result:

```text
Provider Team ID 3 → canonical Team ID 25666
Provider Team ID 9 → canonical Team ID 25647
Provider Events received: 18
Persisted Events: 18
Persisted team IDs: 25647, 25666
EventService read team IDs: 25647, 25666
Cache invalidation: succeeded
```

The provider and canonical IDs differed, and neither provider ID was persisted
as `match_events.team_id`.

## 13. Historical Data Status

No pre-existing Event rows were updated, backfilled, or deleted. The selected
fixture had zero Event rows before sync. The requested controlled provider sync
inserted a new 18-Event snapshot for that fixture (whose stored Match status
was `NS`, despite the provider returning Events). This was a new sync write,
not a repair of existing historical rows.

Accordingly, historical Event **repair** was not performed, but the DB gained
new rows for a fixture whose match time had passed. This side effect is
disclosed because it is relevant to the “no historical data modified”
acceptance criterion.

## 14. Regression Results

* The focused Event sync, freshness, replacement, lock, cache, and transaction
  tests pass.
* The selected real provider sync succeeded and persisted canonical Team IDs.
* The broader suite has unrelated collection and runtime failures detailed
  above; it is not fully green.
* No Event scheduler, finalization, API route, model, schema, migration, or
  Team Master implementation was changed.

## 15. Known Limitations

* The controlled real sync inserted Events for a past fixture that had no
  existing Event rows. No pre-existing Event row was repaired, but this means
  the database was not wholly untouched historically.
* The broad backend suite remains non-green for unrelated failures.
* The database has no Team foreign key on `match_events.team_id`; runtime
  identity and membership validation enforce the frozen contract.

## 16. Final Verdict

**PARTIAL.**

The implementation, focused Event tests, read-only identity mapping check, and
controlled provider sync all verify the canonical Team identity contract.
However, the broader suite is not green, and the controlled sync inserted a
new snapshot for a past fixture. Therefore the strict acceptance condition
that no historical data be modified cannot be claimed as fully satisfied.
