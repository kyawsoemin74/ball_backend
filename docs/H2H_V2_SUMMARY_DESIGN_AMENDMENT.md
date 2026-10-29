# H2H v2 Summary Design Amendment

**Status: APPROVED — IMPLEMENTED**
**Scope:** Additive amendment to the H2H v2 response contract
**Related design:** [H2H Local Team ID Consistency Design Freeze](./H2H_LOCAL_TEAM_ID_CONSISTENCY_DESIGN_FREEZE.md)

## 1. Purpose

This amendment adds six additional summary values inside the existing
H2H v2 `summary` object. The values make the current match's head-to-head
totals directly accessible while preserving the existing nested team
summaries and all other response fields.

The user approved this amendment for implementation. Its backend service,
response schema, and focused tests have been updated accordingly. No database,
runtime configuration, or frontend changes were made.

## 2. Proposed Summary Contract

The contract adds these required integer fields to the existing
`H2HSummaryV2` object:

| Field | Meaning |
|---|---|
| `total_matches` | Count of eligible historical meetings included in the summary |
| `home_win` | Eligible meetings won by the current match's home team |
| `draw` | Eligible drawn meetings between the current match's two teams |
| `away_win` | Eligible meetings won by the current match's away team |
| `home_goals` | Goals scored by the current match's home team across eligible meetings |
| `away_goals` | Goals scored by the current match's away team across eligible meetings |

The six values are added alongside, not instead of, the existing
`summary.home`, `summary.away`, and `summary.included_meeting_count` fields.
Existing response-level fields, meeting objects, availability states, and
error behavior remain unchanged.

Illustrative response shape (values are placeholders):

```json
{
  "contract_version": 2,
  "match_id": 1285,
  "home_team_id": 97,
  "away_team_id": 98,
  "availability": "AVAILABLE",
  "available": true,
  "summary": {
    "home": {
      "team_id": 97,
      "team_name": "Current Home Team",
      "wins": 10,
      "draws": 6,
      "losses": 7,
      "goals_for": 30,
      "goals_against": 25
    },
    "away": {
      "team_id": 98,
      "team_name": "Current Away Team",
      "wins": 7,
      "draws": 6,
      "losses": 10,
      "goals_for": 25,
      "goals_against": 30
    },
    "included_meeting_count": 23,
    "total_matches": 23,
    "home_win": 10,
    "draw": 6,
    "away_win": 7,
    "home_goals": 30,
    "away_goals": 25
  },
  "meetings": [],
  "excluded_meeting_count": 0
}
```

The example's summary totals are illustrative only.

## 3. Calculation and Orientation Rules

Summary values must reuse the eligible-meeting counters already calculated
by `H2HService._normalize_h2h_data()` in
[`app/services/h2h_service.py`](../app/services/h2h_service.py). Do not
recalculate totals from raw fixtures or independently reinterpret meeting
eligibility.

The proposed derivation is:

- `total_matches` = existing `included_meeting_count`
- `home_win` = existing wins counter for the current local home team
- `draw` = eligible draw count for either current team
- `away_win` = existing wins counter for the current local away team
- `home_goals` = existing goals-for counter for the current local home team
- `away_goals` = existing goals-for counter for the current local away team

“Home” and “away” in these fields always refer to the current match's
`home_team_id` and `away_team_id`. They do not refer to the raw home/away
positions of any particular historical fixture. Historical fixture sides
must continue to be mapped to local team IDs before the existing counters
are updated. A reversed historical orientation therefore contributes the
result and goals to the correct current team.

All current eligibility, identity mapping, duplicate fixture, score
validation, and failure-handling rules remain authoritative. Only meetings
already marked eligible and included in the existing summary counters
contribute to the new fields.

## 4. Required Invariants

For every response:

1. `total_matches == included_meeting_count`.
2. `total_matches == home_win + draw + away_win`.
3. `summary.home.draws == summary.away.draws == draw`.
4. `home_goals == summary.home.goals_for`.
5. `away_goals == summary.away.goals_for`.
6. All six fields use exactly the same eligible meetings as the existing
   nested counters.
7. `total_matches` need not equal `len(meetings)`: the response may retain
   meetings that are incomplete, non-final, unmappable, or unrelated for
   display and diagnostics, while excluding them from summary totals.

## 5. Compatibility and Boundaries

- Preserve all existing H2H v2 response and nested summary fields.
- Add the six fields only to the existing v2 `summary` object.
- Keep the legacy v1 endpoint's raw-list response unchanged.
- Keep calculation in the backend read path.
- Do not persist derived summary values.
- No database schema change or migration is required or proposed.
- No provider call, synchronization, queue operation, or additional database
  or cache write is required or proposed.
- No Flutter or other frontend change is included.
- Do not refactor unrelated service or repository architecture.
- Preserve existing availability statuses, HTTP errors, cache behavior, and
  H2H data eligibility rules.

## 6. Proposed File Responsibilities

Implementation responsibilities:

| File | Proposed responsibility |
|---|---|
| [`app/services/h2h_service.py`](../app/services/h2h_service.py) | Populate the six fields from the existing normalized counters in `_normalize_h2h_data()` |
| [`app/schemas/h2h_v2.py`](../app/schemas/h2h_v2.py) | Declare the six fields as required integers on `H2HSummaryV2`, retaining all existing fields |
| [`tests/test_h2h_v2.py`](../tests/test_h2h_v2.py) | Assert aliases, invariants, both historical orientations, eligible/ineligible mixtures, and zero/empty cases |
| [`docs/H2H_LOCAL_TEAM_ID_CONSISTENCY_DESIGN_FREEZE.md`](./H2H_LOCAL_TEAM_ID_CONSISTENCY_DESIGN_FREEZE.md) | Record the approved amendment and implementation status |

## 7. Acceptance Criteria

The implementation satisfies the design when:

- The v2 JSON includes all six required integer fields and retains every
  existing v2 summary and response field.
- Each new field equals the corresponding existing normalized counter.
- All seven invariants in Section 4 hold.
- Reversed historical home/away orientation produces the same current-team
  attribution as direct orientation.
- Incomplete or invalid scores, non-final results, duplicate fixtures,
  unmappable identities, and unrelated pairs do not contribute.
- Empty and missing H2H source behavior and existing availability/error
  semantics remain unchanged.
- The legacy v1 endpoint remains a raw list.
- No persisted summary, migration, provider/sync/queue operation, or
  frontend change is introduced.

## 8. Unresolved Design Questions

No calculation or field-meaning question remains within the scope of this
amendment. The additive contract is approved and implemented. This amendment
does not change other decisions recorded in the existing Design Freeze.

## 9. Implementation Status

The six aliases are emitted by `H2HService._normalize_h2h_data()` from the
existing eligible-meeting counters and declared as required integer fields in
`H2HSummaryV2`. Focused H2H v2 tests and adjacent H2H/provider/sync/scheduler
regressions passed. No database persistence, migration, provider/sync/queue
operation, cache-contract change, or frontend change was introduced.
