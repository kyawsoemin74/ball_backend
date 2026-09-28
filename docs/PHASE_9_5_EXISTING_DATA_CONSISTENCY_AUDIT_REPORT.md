# PHASE 9.5 - EXISTING DATA CONSISTENCY AUDIT REPORT

## 1. Executive Summary

This was a read-only, system-wide consistency audit after Phase 9.4. No database mutation, repair, migration, synchronization job, provider write, or application behavior change was performed.

Audit status: **PASS**. All required audit queries and tests completed.

Database consistency status: **NOT CLEAN**. Master identities and foreign-key relationships are intact, but the database contains historical lineup-to-analytics gaps, missing canonical Player references in finalized lineups, terminal finalization records without lineups, negative event elapsed values, and repeated event signatures.

## 2. Audit Scope

Audited Players, Teams, Matches, Leagues, League Seasons, memberships, Match Lineups, Analytics Lineups, Match Events, finalization records, provider identities, statuses, cross-table orphans, duplicates, lifecycle matrices, runtime readiness, scheduler ownership, locks, and Phase 9.4 blocker dependencies.

## 3. Architecture Reference

The audit used the frozen architecture:

`Provider -> SyncService -> Repository -> Database`

For lineups and analytics:

`Provider -> LineupSyncService -> PlayerIdentityResolutionService -> canonical player_id -> match_lineups -> AnalyticsProjectionService -> AnalyticsLineupRepository -> analytics_match_lineups`.

No architecture was changed.

## 4. Read-Only Guarantee

All database inspection used SELECT, COUNT, JOIN, GROUP BY, EXISTS, and information-schema queries. No INSERT, UPDATE, DELETE, UPSERT, migration, repair service, synchronization service, or provider write was executed.

## 5. Database Baseline

| Table | Rows | Primary-key range |
|---|---:|---|
| players | 7,735 | 7,675..15,409 |
| teams | 258 | 1..25,620 |
| matches | 1,820 | 1,490,120..1,589,023 |
| match_lineups | 71 | 1,490,466..1,588,898 |
| analytics_match_lineups | 697 | 1,978..2,674 |
| match_events | 8,766 | 1,937..30,726 |
| player_team_memberships | 7,818 | 5,909..13,726 |
| match_lineup_finalization | 126 | 1,490,427..1,588,912 |
| leagues | 1,235 | 1..1,235 |
| league_seasons | 128 | 1..2,436 |

Migration head: `20260915_missing_lineup_identity`.

## 6. Player Master Audit

PASS for master-table identity integrity:

* NULL/blank provider IDs: 0
* Invalid provider values: 0
* Duplicate `(provider, provider_id)`: 0
* Duplicate provider ID: 0
* Membership orphan Player references: 0
* Event orphan Player references: 0
* Event orphan assist references: 0
* Analytics orphan Player references: 0

Historical blocker: 40 finalized-success lineup payloads reference local player IDs absent from `players`. Their provider player IDs are present in the stored lineup payloads, but resolving or creating those Players is outside Phase 9.5 and Phase 9.4 scope.

## 7. Team Master Audit

PASS for Team Master identity and references:

* NULL/blank provider IDs: 0
* Invalid provider values: 0
* Duplicate `(provider, provider_id)`: 0
* Membership orphan Team references: 0
* Match orphan home-team references: 0
* Match orphan away-team references: 0
* Event orphan Team references: 0
* Analytics orphan Team references: 0

No Team Master changes were made.

## 8. Match Master Audit

PASS for Match Master structural integrity:

* NULL provider fixture IDs: 0
* Duplicate `(provider, provider_fixture_id)`: 0
* Duplicate Match business keys: 0
* Orphan home-team references: 0
* Orphan away-team references: 0
* Orphan league references: 0
* NULL match times: 0
* Invalid status values: 0

No Match rows were modified.

## 9. Match Status Audit

Observed status groups:

| Status | Matches | Lineups | Analytics | Events | Finalizations |
|---|---:|---:|---:|---:|---:|
| FT | 948 | 70 | 16 | 8,712 | 125 |
| NS | 864 | 0 | 0 | 0 | 0 |
| PEN | 3 | 1 | 0 | 54 | 1 |
| PST | 5 | 0 | 0 | 0 | 0 |

No current LIVE, 1H, HT, 2H, ET, or P rows existed at audit time. Invalid statuses: 0. Status-specific impossible-state rules were not invented beyond the backend’s current allowed status set.

## 10. Lineup Consistency Audit

* Total lineups: 71
* Duplicate lineup match keys: 0
* Orphan lineup Match references: 0
* Non-array payloads: 0
* Invalid team blocks/sections: 0
* Duplicate player within a lineup: 0
* Lineups containing missing canonical player IDs: 1
* Lineups without Analytics: 55

The one structurally incomplete lineup is not repaired. The 14 retryable partial finalization lineups remain unprojected by design.

## 11. Analytics Consistency Audit

* Total analytics rows: 697
* Analytics orphans to Match: 0
* Analytics orphan Player references: 0
* Analytics orphan Team references: 0
* Duplicate analytics business keys: 0
* Lineups with Analytics: 16
* Lineups without Analytics: 55
* Finalized-success lineups without Analytics: 40

The 40 finalized-success gaps are historical identity-chain failures, not Analytics-created orphan rows.

## 12. Player -> Lineup -> Analytics Chain

The dominant broken chain is:

`stored provider player identity + stored local player_id -> Player Master row missing -> Analytics canonical validation rejects -> analytics projection absent`.

Counts:

* Player Master identity integrity failures: 0 at the Player table level.
* Canonical lineup payloads referencing missing Players: 40 matches.
* Finalized-success lineups blocked by those missing Players: 40.
* Analytics rows with missing Players: 0.
* Analytics rows with invalid provider/local identity mismatch: 0 detected by FK/provider checks.

This is historical/stale reference residue or an earlier Player Master lifecycle inconsistency. It cannot be safely resolved in this audit without Player Master changes.

## 13. Membership Audit

* Orphan membership Player references: 0
* Orphan membership Team references: 0
* Duplicate current membership identities: 0
* Duplicate `(provider, player_id, team_id, valid_from)`: 0

No assumption was made that every historical lineup player must have a current membership.

## 14. Match Events Audit

* Total events: 8,766
* Orphan Match references: 0
* Orphan Player references: 0
* Orphan assist references: 0
* Orphan Team references: 0
* `player_id IS NULL`: 8,766
* `assist_id IS NULL`: 8,766
* `team_id IS NULL`: 0
* Negative elapsed values: 48

NULL player and assist IDs are legitimate for the stored event forms where the provider event has no player/assist identity, but negative elapsed values are a data-quality finding. Representative negative values were `-5` on Yellow Card events. No Events were modified.

The duplicate-signature query found 27 repeated logical signatures, predominantly NULL-player Yellow Card rows. This is reported as a medium historical/event identity finding, not treated as a database primary-key violation.

## 15. Finalization Audit

State distribution:

* `SUCCESS`: 55
* `RETRYABLE / LINEUP_PARTIAL`: 14
* `TERMINAL / MASTER_RESOLUTION_FAILURE`: 54
* `TERMINAL / MAX_RETRY_ATTEMPTS_EXCEEDED`: 3
* `RUNNING`: 0

Findings:

* SUCCESS without canonical lineup: 0
* SUCCESS without Analytics: 40
* SUCCESS with missing canonical Players: 40
* RETRYABLE without lineup: 0
* TERMINAL without lineup: 56

The terminal-without-lineup count is a historical finalization/data-state inconsistency and requires a later scoped investigation. No state was modified.

## 16. Final Live Sync Consistency

Current terminal Match rows are structurally valid and provider fixture identities are unique. The audit found no evidence that historical rows passed through Final Live Sync beyond available current logs; no historical runtime claim was fabricated.

Terminal state groups are FT and PEN in the current database; no AET rows were present. This audit did not trigger synchronization or finalization work.

## 17. Cross-Table Orphan Matrix

| Child table/reference | Parent | Orphans | Severity |
|---|---|---:|---|
| memberships.player_id | players.player_id | 0 | Informational |
| memberships.team_id | teams.team_id | 0 | Informational |
| matches.home_team_id | teams.team_id | 0 | Informational |
| matches.away_team_id | teams.team_id | 0 | Informational |
| matches.league_id | leagues.league_id | 0 | Informational |
| match_lineups.local_match_id | matches.local_match_id | 0 | Informational |
| analytics_match_lineups.match_id | matches.local_match_id | 0 | Informational |
| analytics_match_lineups.player_id | players.player_id | 0 | Informational |
| analytics_match_lineups.team_id | teams.team_id | 0 | Informational |
| match_events.match_id | matches.local_match_id | 0 | Informational |
| match_events.player_id | players.player_id | 0 | Informational |
| match_events.assist_id | players.player_id | 0 | Informational |
| match_events.team_id | teams.team_id | 0 | Informational |

## 18. Duplicate Business Identity Audit

* Players `(provider, provider_id)`: 0 duplicates
* Teams `(provider, provider_id)`: 0 duplicates
* Matches `(provider, provider_fixture_id)`: 0 duplicates
* Current memberships `(provider, player_id, team_id)`: 0 duplicates
* Full membership identity: 0 duplicates
* Analytics business key: 0 duplicates
* Lineup business key: 0 duplicates
* Event logical signatures: 27 repeated signatures, mostly NULL-player Yellow Card rows; no primary-key duplicates

## 19. Status/Data Matrix

* FT: 948 matches, 70 lineups, 16 analytics-bearing matches, 8,712 events, 125 finalizations. Historical analytics gaps remain.
* PEN: 3 matches, 1 lineup, 0 analytics, 54 events, 1 finalization. Analytics is missing for the one PEN lineup.
* NS: 864 matches, no lineups, analytics, events, or finalizations. This is expected lifecycle state.
* PST: 5 matches, no lineups, analytics, events, or finalizations. This is expected postponed state.

## 20. Severity Classification

### CRITICAL

0. No duplicate canonical provider identities or orphan FK graph was found.

### HIGH

0 confirmed canonical identity corruption. The 40 missing Player references are significant but are historical/out-of-scope dependencies rather than duplicated or orphaned canonical rows.

### MEDIUM

* 40 finalized-success lineups without Analytics because canonical Players are missing.
* 56 terminal finalization records without canonical lineups.
* 48 negative event elapsed values.
* 27 repeated event logical signatures.

### LOW

* One lineup payload contains missing canonical player identity.
* One lineup without finalization has zero canonical player IDs.

### INFORMATIONAL

* 8,766 NULL event player IDs and assist IDs are legitimate for event types without resolved player/assist identity under the current schema.
* No current LIVE rows existed during the audit window.

## 21. Root-Cause Classification

* Master/FK integrity: EXPECTED/healthy, based on zero orphan and duplicate identity results.
* 40 finalized lineup projection gaps: HISTORICAL DATA RESIDUE or stale historical references; direct provider/local evidence is stored in payloads, but Player Master rows are absent.
* 14 partial lineup gaps: EXPECTED RETRYABLE state under the frozen identity boundary.
* 56 terminal finalizations without lineups: HISTORICAL DATA RESIDUE or legacy finalization state; exact origin requires a later targeted investigation.
* Negative event elapsed values: HISTORICAL DATA RESIDUE or provider normalization residue; no current writer was invoked.
* Repeated event signatures: HISTORICAL DATA RESIDUE or provider duplicate-event residue; primary keys remain unique.
* Full-suite team-sync failure: PRE-EXISTING UNRELATED FAILURE.

## 22. Phase 9.4 Blocker Analysis

The Phase 9.4 blocker remains current:

1. Affected finalized-success lineups: 40.
2. Missing local Player rows: present for every affected match.
3. Provider identities: stored provider player IDs exist in the canonical lineup JSON payloads.
4. Analytics rows: absent for those matches; no orphan analytics rows were created.
5. Safe resolution through Analytics: impossible because Analytics must not create or resolve Players.
6. Required dependency: a future Player Master/data-consistency phase.
7. Current-runtime impact: historical data consistency and finalization completeness; no current LIVE rows were present during this audit.

No Player Master repair was performed.

## 23. Runtime Health

* API readiness: HTTP 200, PostgreSQL and Redis ready.
* Worker: present and serving metrics.
* Scheduler: `fover_scheduler_up=1`; live, reconciliation, lineup, event, and statistics counters were present.
* Scheduler ownership: dedicated worker remains the sole scheduler owner.
* Advisory locks: active scheduler-level locks were observed during scheduled work; no transaction-scoped lineup resource lock leak was identified.

## 24. Regression Tests

Focused consistency-related tests: `93 passed`.

Full suite: `669 passed, 1 failed`.

The one failure is a pre-existing unrelated team-sync test expecting a result without the existing `resolved` field. No test was changed during this audit.

## 25. Remaining Issues

1. 40 finalized-success lineups lack Analytics because canonical Players are missing.
2. 14 retryable partial lineups lack Analytics by design.
3. 56 terminal finalization records lack canonical lineups.
4. 48 Match Events have negative elapsed values.
5. 27 repeated event logical signatures exist.
6. One unfinalized lineup lacks canonical player IDs.
7. One unrelated pre-existing full-suite test failure remains.

## 26. Recommended Next Actions

* Plan the Player Master/data-consistency investigation required for the 40 missing canonical Players.
* Separately investigate terminal finalization records without lineups.
* Audit event elapsed normalization and duplicate-event provenance.
* Do not repair any of these findings in Phase 9.5.

## 27. Files Changed

Only this report was added:

`docs/PHASE_9_5_EXISTING_DATA_CONSISTENCY_AUDIT_REPORT.md`

No application, test, migration, or data file was changed.

## 28. Explicit No-Repair Confirmation

No database mutation, data repair, Player creation, Team change, Match change, Lineup change, Analytics change, Event change, Finalization change, migration, synchronization job, or service restart was performed.

## 29. Final Status

### Audit Status

PASS. The required read-only audit completed successfully.

### Database Consistency Status

NOT CLEAN. Findings are documented above; no critical canonical identity duplicates or FK orphans were found.

### Critical Findings

0 critical findings. Significant medium findings remain.

### Outstanding Repair Dependencies

The 40 finalized-success lineup projection gaps depend on missing Player Master rows and cannot be safely resolved by Analytics or this audit.

### Final Audit Classification

**PASS**

This PASS describes completion and reliability of the audit, not a claim that the database is perfect.