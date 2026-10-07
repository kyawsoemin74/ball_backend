# Phase 3A — Post-Implementation Audit Report

**Audit date:** 2026-10-07  
**Mode:** Audit-only; no source, test, database, cache, or historical-row edits

## 1. Audit Objective

This audit addresses only:

1. The current full-suite test failures and collection errors.
2. The current database effects of the controlled Event sync for Match `476`.

No tests were edited or skipped to obtain the audit results. No provider sync
was rerun. All database inspection used a transaction with
`SET TRANSACTION READ ONLY`; Redis checks used read-only GET/TTL operations.

The final `git status --short` contains the pre-existing Phase 3 source/test
changes and Phase 2/Phase 3 documents, plus this Phase 3A report. This audit
added only this report; it made no production-code or test changes.

## 2. Phase 3 Current Status

The canonical Event Team identity code remains in place:

```text
provider event.team.id
    → Team Master provider identity lookup
    → canonical teams.team_id
    → match_events.team_id
```

The audit found no full-suite failure in Event Team Identity, EventSyncService,
EventRepository, EventService, MatchEvent, Event cache/locking, or Match/Event
Team consistency. Match `476` currently has 18 canonical Event rows, all
associated with one of its canonical Match sides.

The historical policy issue that drove the earlier PARTIAL result remains a
contract interpretation question. Match `476` had zero Event rows before the
controlled sync, so these rows were newly inserted, not used to repair or
overwrite existing Event rows. The Phase 2 freeze bars historical Event
backfill as well as repair, but does not define whether a newly fetched Event
snapshot for a past fixture is that prohibited backfill or ordinary Event
synchronization. The sync also created four Player Master rows through the
existing Event player-resolution path; those writes are disclosed below.

This audit does not change the Phase 3 implementation verdict by itself. The
broader test suite remains non-green and one unrelated scheduler-query failure
has a code-versus-test-contract ambiguity.

## 3. Full Suite Test Summary

Run with the project virtual environment:

```text
python -m pytest tests -q --continue-on-collection-errors
```

Observed result:

| Result | Count |
|---|---:|
| Tests collected from successfully collected modules (all executed) | 695 |
| Passed | 671 |
| Failed | 24 |
| Skipped | 0 |
| Collection errors | 4 modules |
| Warnings | 21 |

The four modules with collection errors did not contribute executable tests
to the 695-item count. The number of individual tests in those four modules
cannot be established from this run because collection stopped at their
import-time errors.

Collection errors, separate from the 24 failed test items:

| Test module | Exact collection error |
|---|---|
| `tests/test_live_active_registry_phase4.py` | `ImportError: cannot import name 'patch' from 'app.core.config'` |
| `tests/test_odds_identity_mapping.py` | `ModuleNotFoundError: No module named 'app.services.odds_identity'` |
| `tests/test_odds_identity_resolution_bridge.py` | `ImportError: cannot import name 'COMMIT_NOT_CONFIRMED' from 'app.services.odds_sync_service'` |
| `tests/test_odds_phase3_hardening.py` | `ImportError: cannot import name 'IDENTITY_BOUNDARY_VIOLATION' from 'app.services.odds_sync_service'` |

**Historical baseline:** Pre-Phase-3 baseline unavailable. The present run
establishes current failures, not when they first appeared.

## 4. Detailed 24 Failure Matrix

All file/line evidence below refers to the first assertion or application
traceback location in the reproduced run. “Event regression?” is **No** for
each row: the failing tests do not exercise an Event path or any of the Phase 3
Event changes.

| # | Test | Error | Module / production code | Category | Event regression? | Evidence |
|---:|---|---|---|---|---|---|
| 1 | `test_standing_service_sync_standings_skips_unallowed_league` | `AssertionError: assert False is True`; service returns `reason="league_not_allowed"` for league `999` | `tests/test_allowed_leagues.py`; `StandingSyncService.sync_standings` | D | No | Test line 959 expects success; the service has an explicit not-allowed failure response at `app/services/standing_sync_service.py:245-251`. |
| 2 | `test_standing_service_sync_standings_multi_group_flattened_rows` | `AssertionError: assert False is True`; service returns `reason="league_identity_unresolved"` because its League lookup returns no League | `tests/test_allowed_leagues.py`; `StandingSyncService.sync_standings` / `LeagueRepository` | D | No | Test line 1055; reproduced the exact fake setup and observed `Local League identity was not found`. The test does not seed/stub a local League identity. |
| 3 | `test_standing_service_persists_standing_metadata_fields` | `AssertionError: assert False is True`; service returns `reason="league_identity_unresolved"` | `tests/test_allowed_leagues.py`; `StandingSyncService.sync_standings` / `LeagueRepository` | D | No | Test line 1101; exact fake setup returned `Local League identity was not found` before metadata persistence. |
| 4 | `test_refresh_odds_continues_after_match_exception` | `AssertionError: assert 0 == 3`; captured cause `ValueError: not enough values to unpack (expected 4, got 3)` | `tests/test_league_scheduler_hardening.py`; `app/services/scheduler.py:811` | D | No | The scheduler iterates `(match_id, provider_fixture_id, status, match_time)`; the fake query returns 3-element tuples. |
| 5 | `test_refresh_odds_skips_fresh_snapshot` | `AssertionError: assert 0 == 1`; captured cause `ValueError: not enough values to unpack (expected 4, got 3)` | `tests/test_league_scheduler_hardening.py`; `app/services/scheduler.py:811` | D | No | Same four-column production unpack versus three-column fake result. |
| 6 | `test_resolver_uses_match_season_before_league_season` | `AssertionError`; expected SQL text `standings.season = '2028'`, actual SQL scopes `league_seasons.season = '2028'` | `tests/test_league_structure_resolver.py`; `app/services/league_structure_resolver.py` | E | No | Test line 96; source uses the joined LeagueSeason table. This run cannot establish whether match-season preference is required or the assertion is stale. |
| 7 | `test_resolver_falls_back_to_league_season_and_logs` | `AssertionError`; expected SQL text `standings.season = '2026'`, actual SQL scopes `league_seasons.season = '2026'` | `tests/test_league_structure_resolver.py`; `app/services/league_structure_resolver.py` | E | No | Test line 125; source uses the joined LeagueSeason table. This run cannot establish whether fallback behavior is required or the assertion is stale. |
| 8 | `test_odds_scheduler_uses_fixture_lock_before_provider` | `AssertionError: assert 0 == 1`; captured `ValueError: not enough values to unpack (expected 4, got 3)` | `tests/test_odds_lock_alignment.py`; `app/services/scheduler.py:811` | D | No | Test line 86; the fake eligible-match rows omit `provider_fixture_id`. |
| 9 | `test_odds_scheduler_does_not_call_provider_when_lock_conflicts` | `AssertionError: assert 0 == 1`; captured `ValueError: not enough values to unpack (expected 4, got 3)` before lock attempt | `tests/test_odds_lock_alignment.py`; `app/services/scheduler.py:811` | D | No | Test line 111; same fake tuple shape mismatch. |
| 10 | `test_odds_cache_failure_does_not_rollback_after_commit` | `AssertionError: assert 0 == 1`; captured `ValueError: not enough values to unpack (expected 4, got 3)` | `tests/test_odds_lock_alignment.py`; `app/services/scheduler.py:811` | D | No | Test line 135; scheduler fails while reading fake eligible-match rows, before cache behavior. |
| 11 | `test_manual_odds_route_uses_local_match_lock_and_invalidates_after_commit` | `AttributeError: 'object' object has no attribute 'provider_fixture_id'` | `tests/test_odds_lock_alignment.py`; `app/api/matches.py:387` | D | No | The route reads `match.provider_fixture_id`; the fake `_assert_match_allowed` returns bare `object()`. |
| 12 | `test_manual_odds_route_rolls_back_failed_sync_without_cache_invalidation` | `AttributeError: 'object' object has no attribute 'provider_fixture_id'` | `tests/test_odds_lock_alignment.py`; `app/api/matches.py:387` | D | No | Same test-double mismatch, before the mocked sync/failure behavior. |
| 13 | `test_h2h_unresolved_team_identity_fails_before_repository_write` | `AssertionError` from `pytest.raises`; actual `ValueError: invalid local_match_id for H2H refresh` | `tests/test_phase6_sync_reliability.py`; H2H sync service | D | No | Test line 61 expects an unresolved Team error, but its fake input lacks a valid local Match identity, so the service rejects earlier. This is H2H identity, not Event Team identity. |
| 14 | `test_get_cached_standings_redis_miss_reads_postgres_and_populates_cache` | `AttributeError: 'object' object has no attribute 'execute'` | `tests/test_standings_hardening.py`; `StandingService` → `LeagueSeasonRepository.get_by_league_and_season`, line 15 | D | No | Test line 123 supplies `object()` where the current code executes a LeagueSeason lookup. |
| 15 | `test_get_cached_standings_postgres_miss_returns_none_without_api_call` | `AttributeError: 'object' object has no attribute 'execute'` | `tests/test_standings_hardening.py`; same `LeagueSeasonRepository` lookup | D | No | Test line 138 uses the same non-session fake. |
| 16 | `test_upsert_standings_deduplicates_duplicate_team_rows` | `AttributeError: 'FakeStandingWriteDB' object has no attribute 'execute'` | `tests/test_standings_hardening.py`; `StandingSyncService.upsert_standings` → `LeagueSeasonRepository`, line 15 | D | No | Test line 171 provides only `flush`; current upsert first queries LeagueSeason identity. |
| 17 | `test_standing_repository_upsert_uses_real_conflict_constraint` | `TypeError: StandingRepository.upsert_for_league_season() takes 4 positional arguments but 5 were given` | `tests/test_standings_hardening.py`; `app/repositories/standing_repository.py:31` | D | No | Test line 226 calls the old `(db, league_id, season, rows)` form; current method takes `(db, league_season_id, rows)`. |
| 18 | `test_standing_repository_upsert_cleans_stale_rows_by_team_scope` | Same `TypeError` signature mismatch | `tests/test_standings_hardening.py`; `app/repositories/standing_repository.py:31` | D | No | Test line 258 uses the same obsolete argument form. |
| 19 | `test_refresh_pair_query_uses_match_season_and_distinct_pairs` | `AssertionError: "DISTINCT" not in ...`; actual SQL selects `league_seasons.league_id, league_seasons.season` joined to allowed leagues | `tests/test_standings_hardening.py`; `app/services/scheduler.py:_get_allowed_standings_pairs` | E | No | Test line 334 requires `DISTINCT` and `matches.season`; current SQL uses LeagueSeason. Whether test or current scheduler contract is wrong cannot be settled from this run. |
| 20 | `test_refresh_job_isolates_failures_and_continues` | Expected 3 processed pairs, got 0; captured `AttributeError: 'FakeSchedulerDB' object has no attribute 'info'` | `tests/test_standings_hardening.py`; `app/services/scheduler.py:1042` | D | No | Test line 402; advisory-lock code expects SQLAlchemy session `.info`, absent from the fake DB. |
| 21 | `test_standings_model_declares_index_and_unique_constraint` | `AssertionError`; expected index `ix_standings_league_id_season_position`, actual index is `ix_standings_league_season_position` | `tests/test_standings_hardening.py`; `app/models/standing.py:10` | E | No | Test line 471; model/test index names differ. This run cannot establish whether the test expectation or model/migration contract is wrong. |
| 22 | `test_team_profile_standings_service_uses_cache_on_hit` | `AttributeError: 'object' object has no attribute 'execute'` | `tests/test_team_profile_standings.py`; `StandingService` → `LeagueSeasonRepository.get_by_league_and_season`, line 15 | D | No | Test line 87 uses bare `object()` despite the current service doing a LeagueSeason DB lookup before cache access. |
| 23 | `test_team_profile_standings_service_reads_db_and_populates_cache_on_miss` | Same `AttributeError: 'object' object has no attribute 'execute'` | `tests/test_team_profile_standings.py`; same LeagueSeason repository lookup | D | No | Test line 99 uses the same non-session fake. |
| 24 | `test_ensure_teams_exist_resolves_existing_masters_and_reports_missing` | `AssertionError`; actual result has an additional key: `resolved: {10: 101}` | `tests/test_team_upsert_on_conflict.py`; `TeamSyncService.ensure_teams_exist`, line 391 | D | No | Test line 256 compares exact dict shape but omits the current resolver result field. This is Team Master testing, not Event Team resolution. |

### Failure classification

The labels below classify the reproduced 24 test failures, not the four
separate collection errors:

| Category | Count | Interpretation |
|---|---:|---|
| A — Direct Event Team Identity regression | 0 | No failure in the modified Event identity path. |
| B — Indirect Event implementation regression | 0 | No failure has an evidenced dependency on the Event changes. |
| C — Proven pre-existing unrelated failure | 0 | No historical baseline exists to prove a failure predated Phase 3. |
| D — Test fixture/assertion compatibility issue | 20 | The observed mismatch is explained by fake shape/capability, an old method signature, or a test expectation that conflicts with an explicit current guard/result. |
| E — Cause/contract cannot be determined from this run | 4 | Two season-resolution SQL expectations, one standings refresh-pair SQL expectation, and one model index-name expectation differ from current code; this audit cannot establish which contracts should prevail. |

Pre-Phase-3 baseline unavailable; therefore none of the 24 is represented as
proven to have existed before Phase 3. The failures are all outside the Event
Team identity code paths based on their stack locations and current source.

## 5. Event Regression Assessment

**No full-suite failure was identified as an Event Team Identity regression.**

None of the 24 failing test names or first relevant production frames involves:

* `EventSyncService`;
* `EventRepository`;
* Event read/API serialization;
* `MatchEvent`;
* Event cache invalidation;
* Event lock execution;
* provider-to-Team Master Event identity resolution; or
* Match/Event Team-side validation.

The H2H test touches provider Team resolution in the H2H flow, but fails first
because its fake request lacks the required local Match ID. It does not exercise
the Event identity implementation.

## 6. Database Target Verification

Before querying Match data, the configured target was inspected without
printing credentials:

* `.env` database host: `localhost`, port `5432`, database `fover_db`.
* Database server reported `::1`, PostgreSQL `17.9`, Windows build.
* A host-local listener exists on port 5432.
* `docker-compose.yml` configures the API/worker to use the Compose service
  hostname `postgres`; the Compose Postgres service does not publish a host
  port.
* `.env` Redis target is `localhost:6379/0`; Compose configures its own
  services to use hostname `redis`.

**Classification: local development DB/cache target**, distinct from the
Compose-internal service endpoint. No production endpoint was queried. All SQL
inspection transactions used `SET TRANSACTION READ ONLY`.

## 7. Match 476 Data Audit

Current DB values:

| Field | Current value |
|---|---|
| `matches.local_match_id` | `476` |
| `matches.provider_fixture_id` | `1528952` |
| `matches.status` | `NS` |
| `matches.match_time` | `2026-10-06 18:45:00+00` |
| `matches.home_team_id` | `25666` |
| Home Team | Croatia; Team Master provider ID `3` |
| `matches.away_team_id` | `25647` |
| Away Team | Spain; Team Master provider ID `9` |
| Match `created_at` | `2026-09-24 07:42:48.831080+00` |
| Match `updated_at` | `NULL` |

The Match status/time and side IDs were also observed in the pre-sync candidate
query. EventSyncService reads Match side IDs but does not update the Match row.

## 8. 18 Event Row Impact Analysis

The current DB contains 18 rows for Match `476`. All satisfy:

```text
match_events.team_id IN (25666, 25647)
```

There are zero rows with provider Team IDs `3` or `9`, and zero rows whose
Team ID is outside the Match sides. Every current row has `canonical_side =
true`.

All Event rows have `created_at = 2026-10-07 05:45:56.459726+00`. Their
`updated_at` values span `05:45:57.104539+00` through `05:45:57.105015+00`.

| Event ID | Time (elapsed+extra) | `team_id` | Team | Type | Detail | `updated_at` UTC |
|---:|---:|---:|---|---|---|---|
| 1 | 14 | 25666 | Croatia | Card | Yellow Card | 05:45:57.104539 |
| 2 | 17 | 25666 | Croatia | Goal | Normal Goal | 05:45:57.104640 |
| 3 | 22 | 25666 | Croatia | Card | Yellow Card | 05:45:57.104683 |
| 4 | 45 | 25647 | Spain | subst | Substitution 1 | 05:45:57.104712 |
| 5 | 61 | 25647 | Spain | Goal | Normal Goal | 05:45:57.104737 |
| 6 | 65 | 25666 | Croatia | Card | Yellow Card | 05:45:57.104762 |
| 7 | 65 | 25666 | Croatia | subst | Substitution 1 | 05:45:57.104787 |
| 8 | 66 | 25666 | Croatia | subst | Substitution 2 | 05:45:57.104808 |
| 9 | 66 | 25647 | Spain | Card | Yellow Card | 05:45:57.104829 |
| 10 | 71 | 25647 | Spain | subst | Substitution 2 | 05:45:57.104849 |
| 11 | 71 | 25647 | Spain | subst | Substitution 3 | 05:45:57.104869 |
| 12 | 71 | 25666 | Croatia | subst | Substitution 3 | 05:45:57.104890 |
| 13 | 78 | 25647 | Spain | subst | Substitution 4 | 05:45:57.104913 |
| 14 | 79 | 25666 | Croatia | Card | Red Card | 05:45:57.104934 |
| 15 | 87 | 25647 | Spain | subst | Substitution 5 | 05:45:57.104955 |
| 16 | 88 | 25666 | Croatia | subst | Substitution 4 | 05:45:57.104975 |
| 17 | 88 | 25647 | Spain | Goal | Normal Goal | 05:45:57.104995 |
| 18 | 90+2 | 25647 | Spain | Card | Yellow Card | 05:45:57.105015 |

The provider Event payload fetched during Phase 3 contained 18 Events with
provider Team IDs `3` (Croatia) and `9` (Spain). Team Master mappings were
`3 → 25666` and `9 → 25647`. The persisted data matches those mappings and
the Match sides.

## 9. Historical Data Policy Interpretation

The Phase 2 freeze states that Phase 3 correctness applies to newly
synchronized Event snapshots and that historical `match_events` rows are not
rewritten, backfilled, deleted, or otherwise repaired as part of the change.
It requires existing rows containing provider Team IDs to be handled in a
separately approved repair phase. It does not explicitly define whether
inserting the first provider Event snapshot for a past fixture is a prohibited
historical backfill or an allowed new Event sync.

For Match `476`, the pre-sync Event count was `0`. The controlled operation
created a new snapshot; it did not overwrite an existing Event snapshot or
translate old provider-ID Event rows. Therefore this is:

* **Not** repair or rewrite of pre-existing Event rows: there were none.
* **Yes** creation of a new Event snapshot for a historical fixture.
* **Unclear** whether this new historical-fixture snapshot is permitted under
  the freeze's separate “not backfilled” rule. That boundary needs a design
  decision; the audit does not infer permission or violation.

The fixture match time was in the past and its stored status remained `NS`.
The Event scheduler blocks `NS` for routine refresh; terminal statuses are
included in final Event recovery, and the admin sync route is described as a
finalized-match route but contains no status guard. The Phase 3 controlled sync
called the existing sync service under the existing Event lock; it was not a
scheduler-triggered `NS` refresh. The current source therefore supports
terminal/historical Event synchronization, but does not establish that an
`NS` fixture with a past time is a normal scheduled case. That Match status
discrepancy is separate from the canonical Event Team IDs.

## 10. Controlled Sync Side Effects

The sync can write both Event rows and Player Master rows. The current
EventSyncService resolves Players and, when the existing Player identity
resolver returns `CREATE_NEW`, calls `PlayerSyncService.upsert_player`.
Read-only inspection found 19 distinct Player rows referenced by the Events:

* 15 have `created_at` and `updated_at` at `2026-09-15 09:45:47.623072+00`.
* 4 were created at exactly `2026-10-07 05:45:56.459726+00`, matching the Event
  insertion timestamp:
  * player `15412`, provider player ID `754`, Luka Modrić;
  * player `15413`, provider player ID `202696`, Igor Matanovic;
  * player `15414`, provider player ID `842`, Nikola Vlašić;
  * player `15415`, provider player ID `2763`, Mario Pašalić.

This timestamp/source evidence identifies four Player Master creations during
the controlled sync. No referenced pre-existing Player row has an update
timestamp in the sync window. No prior Player snapshot was captured, so the
timestamp is strong attribution evidence, not a transaction audit log.

Current cache inspection found `fover:match:476:events` present with 18 Events,
canonical Team IDs `[25647, 25666]`, and TTL `21156` seconds. Phase 3 reported
that the sync caller invalidated the key after commit, then the API-facing read
repopulated it; pre-sync cache contents were not captured.

The database has no `match_finalization` table. The existing
`match_lineup_finalization` table has no row for Match `476`. No finalization
write is made by the Event sync service.

## 11. Data Impact Matrix

| Object | Before | After / current | Changed? | Expected? | Evidence |
|---|---|---|---|---|---|
| Match `476` | ID `476`, fixture `1528952`, sides `25666/25647`, status `NS`, 0 Events | Same Match IDs/sides/status; `updated_at` remains NULL; now has 18 Events | Match row: no evidence of change | Yes; EventSync reads Match for validation | Pre-sync candidate query, current read-only Match query, service source |
| `match_events` for `476` | 0 rows | 18 new rows; all Team IDs `25666` or `25647` | Yes: inserted | Yes: controlled snapshot creation; no previous snapshot overwritten | Read-only row/aggregate query; all `canonical_side=true` |
| Teams `25666`, `25647` | Existing Team Master rows, provider IDs `3`, `9` | Same Team IDs/provider IDs; created `2026-09-18 19:06:19.563770+00`, updated `2026-09-24 07:42:48.831080+00` | No evidence of change | Yes; identity lookup is SELECT-only | Read-only Team query shows existing timestamps; source lookup |
| Player Master | 19 Event participant identities available/linked after sync | Four new Player rows at sync timestamp; 15 referenced rows retain older timestamps | Yes: four new rows | Expected side effect of existing `CREATE_NEW` Player resolution path | Player timestamps and Event foreign-key joins; source `_resolve_provider_player` |
| Event cache | Pre-sync value not captured; sync report says invalidated after commit | Key present, 18 Events, canonical IDs only | Yes, invalidated then repopulated; prior state unknown | Yes under existing cache ownership | Read-only Redis GET/TTL and Phase 3 operation record |
| `match_finalization` | Relation not present in current DB schema | Relation remains absent | No | Not applicable | `information_schema` catalog query |
| `match_lineup_finalization` | Pre-sync row not captured | No row for Match `476` | No write evidenced | Yes; Event sync does not own this state | Read-only current-row query and source inspection |
| Other application tables | No complete pre-sync snapshot available | No additional writes indicated by EventSyncService source; player and Event writes identified above | Cannot prove all-table before/after equality | No additional writes expected by this service | Source inspection; no transaction/audit log was captured |

## 12. Data Safety Assessment

* The 18 Event rows are structurally consistent with the provider Event
  payload, valid elapsed/type fields, and both Match sides.
* Their `team_id` values are canonical Team Master IDs, not provider IDs `3`
  or `9`.
* No Event snapshot was overwritten because the pre-sync count was zero.
* The Match and Team identity records remain consistent; the Match still
  reports `NS` despite a past match time and provider Events.
* Four new Player Master records were also created by the established Event
  player-resolution path.
* No evidence indicates unsafe Team identity assignment or Event corruption.
* Full before/after auditing of all tables and pre-sync cache values is not
  possible because no database transaction audit log or complete before
  snapshot was captured.

## 13. Recommended Handling

**Choose Option 3: leave the rows unchanged pending a separate Historical Event
Data Review/Repair phase or explicit policy decision.**

Evidence supports that they are valid canonical Events created from a real
provider fixture payload where no Event rows existed. They are not historical
row repairs, but whether their creation is historical backfill is not settled
by the freeze. The four Player Master rows are also expected output of current
player resolution behavior and are referenced by the new Event snapshot.

Do not delete or restore any rows based on this audit. Separately decide
whether the freeze bars new Event snapshots for past-time Matches and whether
an `NS` Match with a past match time is eligible for manual sync.

## 14. Required Follow-up

1. Keep this Phase 3A audit record with the implementation report.
2. Handle the 20 diagnosed test fixture/assertion mismatches separately from
   Event Team identity work.
3. Resolve the four test-versus-code contract discrepancies: season resolution,
   standings refresh-pair SQL, and the standings index name. Do not label them
   production defects or stale assertions without confirming the intended
   contracts.
4. Decide explicitly whether first-time Event synchronization for past
   fixtures is prohibited historical backfill or allowed new snapshot sync.
5. If operational policy requires it, separately decide whether a past-time
   `NS` Match may receive an admin/manual Event sync.
6. No Event code change or Event data cleanup is indicated by this audit.

## 15. Final Audit Verdict

**PARTIAL.**

The audit found **zero Event Team Identity regressions** among the 24 failed
tests. The controlled sync’s 18 Event rows are canonical and match-side
consistent; they are new rows, not repairs or overwrites of old Event rows.
Whether creating that snapshot for a past fixture is prohibited historical
backfill remains unclear under the freeze. The operation also created four
Player Master rows through the existing Event player-resolution behavior. No
evidence supports deleting the Event or Player rows.

The test suite remains non-green (24 failures and 4 collection errors), and one
standings query failure remains unresolved as a code-versus-test-contract
question. A historical test baseline and complete pre-sync all-table/cache
snapshot are unavailable. These limitations and the unresolved historical-sync
policy prevent a PASS verdict, but the available evidence does not identify
unsafe Event Team data.
