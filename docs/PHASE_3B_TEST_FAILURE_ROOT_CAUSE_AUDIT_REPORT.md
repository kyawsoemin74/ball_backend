# Phase 3B — Test Failure Root-Cause Audit

**Audit date:** 2026-10-07  
**Mode:** Read-only audit; no source, test, fixture, configuration, database, or Redis edits

## 1. Objective

Determine the evidence-backed root cause and impact of the 24 full-suite
failures and 4 collection errors reproduced in Phase 3A, without treating a
failing test as proof of a production defect.

## 2. Scope

This audit covers only:

* the 24 failed test items;
* the 4 collection errors;
* the test-runner discrepancy observed when invoking the standalone pytest
  console script; and
* whether any of the failures implicate Phase 3 Event Team identity code.

Historical Event Sync policy and Match `476` data are explicitly out of scope
and are not reconsidered here.

## 3. Read-Only Constraints

No production code, tests, fixtures, configuration, environment variables,
database rows, or Redis keys were changed. No migrations or provider/API calls
were run. Pytest was run as authorized. No dependency was installed.

The environment was inspected only. Initial `git diff --check` reported no
whitespace errors.

## 4. Test Baseline

At audit start, `git status --short` showed the existing Phase 3 changes:

* Modified production files: `app/repositories/event_repository.py`,
  `app/services/event_sync_service.py`.
* Added/changed Phase 3 test files:
  `tests/test_event_service_freshness.py`,
  `tests/test_event_team_identity.py`.
* Existing untracked phase documents: the Phase 2 design freeze, Phase 3
  implementation report, and Phase 3A audit report.

The tracked diff summary at audit start was 978 insertions and 1 deletion
across the two modified production files and two Phase 3 test files. The Phase
3 implementation report independently identifies those same four code/test
files and states no other production files were changed. Phase 3B adds only
this report.

**Pre-Phase-3 baseline unavailable.** Nothing in the current evidence
establishes when the 24 failures or collection errors first appeared. They are
not classified as proven pre-existing failures.

## 5. Full Test Result

The project-environment invocation was run:

```text
.venv\Scripts\python.exe -m pytest tests -q --continue-on-collection-errors
```

Result:

| Measure | Result |
|---|---:|
| Tests executed from successfully collected modules | 695 |
| Passed | 671 |
| Failed | 24 |
| Skipped | 0 |
| Warnings | 21 |
| Collection errors | 4 modules |

The exact 24 failures and 4 import-time collection errors from Phase 3A were
reproduced. Since collection stopped for four modules, the number of individual
test functions inside those modules is not included in the 695 executed
items; the total number of repository test functions is therefore unknown.

### Standalone pytest invocation note

`pytest` itself is not available on PATH. Invoking the project venv's console
script directly,
`.venv\Scripts\pytest.exe tests -q --continue-on-collection-errors`, did **not**
reproduce the project-environment run: it reported `2 failed, 6 passed, 79
errors`. The collection errors and both failures were `ModuleNotFoundError:
No module named 'app'`. The same interpreter with `python -m pytest` imports
the project correctly and reproduces the 24+4 baseline.

This is a console-entrypoint/import-path tooling discrepancy, not evidence that
the suite's result changed or that application modules are missing. Do not
count those 79 errors as the suite's root-cause inventory.

## 6. Failure Inventory

The complete per-test inventory is in §8. For each item it records the exact
observed error, test and production source locations, module, root-cause
evidence, one primary classification, impact, confidence, and whether a later
fix is required. No failed test was omitted.

## 7. Collection-Error Inventory

All four are in-repository import/collection contract failures, not missing
third-party packages. The complete traceback entry points and classification
appear in §8. No install or source change was attempted.

## 8. Root-Cause Matrix

Classification definitions:

* **A** production-code regression
* **B** stale/outdated test expectation
* **C** test fixture/mock/contract mismatch
* **D** environment/infrastructure/tooling issue
* **E** dependency/import/collection issue
* **F** unrelated/pre-existing failure
* **G** test implementation defect
* **H** unknown/insufficient evidence

“Fix required?” means a later test/contract or production follow-up is needed
to remove the failure or determine the intended contract. No fixes were made
in Phase 3B.

| # | Test/Error | Module | Root Cause | Classification | Impact | Evidence | Fix Required? |
|---:|---|---|---|---|---|---|---|
| 1 | `test_standing_service_sync_standings_skips_unallowed_league` — `AssertionError: assert False is True`; actual reason `league_not_allowed` | Standings | The test expects a successful no-op and an older message for an unallowed league; current sync returns `success=False`, `reason="league_not_allowed"` before provider work. This proves the contract mismatch, but evidence does not establish whether “skip” should be success or failure. | H | LOW — test/CI and caller-response semantics only; no sync occurs | Test `tests/test_allowed_leagues.py:950-959`; guard `app/services/standing_sync_service.py:243-251`. Confidence 10/10 for the mismatch; 5/10 for which side is correct. | Yes — confirm intended skip result and align implementation/test. |
| 2 | `test_standing_service_sync_standings_multi_group_flattened_rows` — `AssertionError: assert False is True`; actual `league_identity_unresolved` | Standings | Fake supplies an allowed provider league ID but does not supply a local League Master result. Sync stops at League identity resolution before it calls the fake provider or tests group flattening. | C | NONE — test setup only | Test `tests/test_allowed_leagues.py:1019-1055`; resolver `app/services/standing_sync_service.py:254-262`. Confidence 10/10. | Yes — mock/seed the local League identity for this test. |
| 3 | `test_standing_service_persists_standing_metadata_fields` — `AssertionError: assert False is True`; actual `league_identity_unresolved` | Standings | Same missing local League Master fake as #2; execution never reaches metadata persistence. | C | NONE — test setup only | Test `tests/test_allowed_leagues.py:1062-1101`; resolver `app/services/standing_sync_service.py:254-262`. Confidence 10/10. | Yes — mock/seed the local League identity. |
| 4 | `test_refresh_odds_continues_after_match_exception` — expected 3 processed, got 0; logged `ValueError: not enough values to unpack (expected 4, got 3)` | Odds scheduler | Test fake supplies three-column eligible-match tuples; the production query and loop use four columns (`match_id`, `provider_fixture_id`, `status`, `match_time`). | C | NONE — fake query shape only | Test `tests/test_league_scheduler_hardening.py:56` and its fake rows; `app/services/scheduler.py:803,811`. Confidence 10/10. | Yes — update the fake to match the selected row shape. |
| 5 | `test_refresh_odds_skips_fresh_snapshot` — expected 1 processed, got 0; same tuple-unpack `ValueError` | Odds scheduler | Same three-versus-four-column fake mismatch; freshness assertion is never reached. | C | NONE — fake query shape only | Test `tests/test_league_scheduler_hardening.py:146-183`; `app/services/scheduler.py:803,811`. Confidence 10/10. | Yes — update the fake row shape. |
| 6 | `test_resolver_uses_match_season_before_league_season` — assertion expects `standings.season = '2028'`; actual SQL filters `league_seasons.season = '2028'` | League structure | Test asserts a removed physical Standings season column. The resolver selects Match season and filters the normalized LeagueSeason relation; result, metric, and log assertions demonstrate the intended Match-season precedence. | B | NONE — SQL-text assertion only | Test `tests/test_league_structure_resolver.py:76-96`; implementation `app/services/league_structure_resolver.py:59-67,89-95`; identity migration drops `standings.season` in `alembic/versions/20260926_standing_ls_identity.py`. Confidence 10/10. | Yes — assert the LeagueSeason predicate rather than the removed column. |
| 7 | `test_resolver_falls_back_to_league_season_and_logs` — expects `standings.season = '2026'`; actual SQL filters `league_seasons.season = '2026'` | League structure | Same stale physical-column expectation; fallback value, metric, and log are correct in the observed run. | B | NONE — SQL-text assertion only | Test `tests/test_league_structure_resolver.py:106-125`; implementation `app/services/league_structure_resolver.py:69-77,89-95`; same migration removes the old Standings season column. Confidence 10/10. | Yes — update the SQL assertion to the normalized relation. |
| 8 | `test_odds_scheduler_uses_fixture_lock_before_provider` — expected one refresh, got zero; captured tuple-unpack `ValueError` | Odds scheduler | Local `DB.execute()` fake returns `(match_id, status, match_time)` instead of the four values selected by production. Lock/provider assertions never run. | C | NONE — fake query shape only | Test `tests/test_odds_lock_alignment.py:25-30,65-86`; `app/services/scheduler.py:803,811`. Confidence 10/10. | Yes — supply all four selected values. |
| 9 | `test_odds_scheduler_does_not_call_provider_when_lock_conflicts` — expected one skipped match, got zero; same `ValueError` | Odds scheduler | Same test DB tuple mismatch; lock conflict path is never reached. | C | NONE — fake query shape only | Test `tests/test_odds_lock_alignment.py:25-30,92-111`; `app/services/scheduler.py:803,811`. Confidence 10/10. | Yes — supply all four selected values. |
| 10 | `test_odds_cache_failure_does_not_rollback_after_commit` — expected one refresh, got zero; same `ValueError` | Odds scheduler | Same test DB tuple mismatch; cache/commit behavior is never reached. | C | NONE — fake query shape only | Test `tests/test_odds_lock_alignment.py:25-30,116-135`; `app/services/scheduler.py:803,811`. Confidence 10/10. | Yes — supply all four selected values. |
| 11 | `test_manual_odds_route_uses_local_match_lock_and_invalidates_after_commit` — `AttributeError: 'object' object has no attribute 'provider_fixture_id'` | Odds API | `_assert_match_allowed` mock returns bare `object()`, while the route needs the allowed Match's provider fixture ID before lock/sync. | C | NONE — incomplete mock; route is not exercised past setup | Test `tests/test_odds_lock_alignment.py:140-170`; `app/api/matches.py:383-388`. Confidence 10/10. | Yes — return a Match-shaped fake with `provider_fixture_id`. |
| 12 | `test_manual_odds_route_rolls_back_failed_sync_without_cache_invalidation` — same `AttributeError` | Odds API | Same incomplete Match fake; the expected error-sync/rollback branch is never reached. | C | NONE — incomplete mock | Test `tests/test_odds_lock_alignment.py:176-200`; `app/api/matches.py:383-388`. Confidence 10/10. | Yes — return a Match-shaped fake. |
| 13 | `test_h2h_unresolved_team_identity_fails_before_repository_write` — `pytest.raises` regex mismatch; actual `ValueError: invalid local_match_id for H2H refresh` | H2H sync | Test calls `refresh_h2h(object(), "10-20")`; current API requires a valid local match ID and rejects input before fetching/resolving provider Team identities. | C | NONE — test input/contract only | Test `tests/test_phase6_sync_reliability.py:53-61`; validation `app/services/h2h_sync_service.py:39-45`. Confidence 10/10. | Yes — construct the required local Match input or test the correct API layer. |
| 14 | `test_get_cached_standings_redis_miss_reads_postgres_and_populates_cache` — `AttributeError: 'object' object has no attribute 'execute'` | Standings service/repository | `get_cached_standings` first resolves canonical LeagueSeason identity; bare `object()` is not an AsyncSession and cannot support `LeagueSeasonRepository.get_by_league_and_season`. | C | NONE — invalid DB fake | Test `tests/test_standings_hardening.py:117-123`; `app/services/standing_service.py:113-123`; `app/repositories/league_season_repository.py:12-17`. Confidence 10/10. | Yes — provide a DB fake supporting the LeagueSeason query. |
| 15 | `test_get_cached_standings_postgres_miss_returns_none_without_api_call` — same `AttributeError` | Standings service/repository | Same incomplete DB fake; the standing-cache miss path is not reached. | C | NONE — invalid DB fake | Test `tests/test_standings_hardening.py:132-138`; `app/services/standing_service.py:113-123`; `app/repositories/league_season_repository.py:12-17`. Confidence 10/10. | Yes — provide a DB fake supporting the LeagueSeason query. |
| 16 | `test_upsert_standings_deduplicates_duplicate_team_rows` — `AttributeError: 'FakeStandingWriteDB' object has no attribute 'execute'` | Standings sync/repository | The fake implements write/flush behavior but omits `execute`; sync resolves LeagueSeason identity before Team resolution or repository upsert. | C | NONE — invalid DB fake | Test `tests/test_standings_hardening.py:146-171`; `app/services/standing_sync_service.py:150-164`; `app/repositories/league_season_repository.py:12-17`. Confidence 10/10. | Yes — add a read-query response to the fake. |
| 17 | `test_standing_repository_upsert_uses_real_conflict_constraint` — `TypeError: upsert_for_league_season() takes 4 positional arguments but 5 were given` | Standing repository | Test uses the old `(db, league_id, season, rows)` repository signature and old row key contract. Current repository takes `(db, league_season_id, rows)` and writes by LeagueSeason identity. | B | NONE — stale direct-call contract | Test `tests/test_standings_hardening.py:201-226`; current signature `app/repositories/standing_repository.py:31-35`; migration `alembic/versions/20260926_standing_ls_identity.py`. Confidence 10/10. | Yes — update the test call and row contract to LeagueSeason identity. |
| 18 | `test_standing_repository_upsert_cleans_stale_rows_by_team_scope` — same `TypeError` | Standing repository | Same old repository signature; delete/upsert SQL assertions are never reached. | B | NONE — stale direct-call contract | Test `tests/test_standings_hardening.py:233-258`; `app/repositories/standing_repository.py:31-35`; same identity migration. Confidence 10/10. | Yes — update the test call and row contract. |
| 19 | `test_refresh_pair_query_uses_match_season_and_distinct_pairs` — expected `DISTINCT` and `matches.season`; current SQL selects `league_seasons.league_id, league_seasons.season` | Standings scheduler | Test and production query encode different season-selection contracts. Current scheduler enumerates allowed LeagueSeason rows and de-duplicates pairs in Python; test requires Match-season-only selection. The present source proves the mismatch but does not prove which scheduler policy is intended. | H | MEDIUM — scheduler refresh coverage depends on intended source of seasons | Test `tests/test_standings_hardening.py:328-334`; implementation `app/services/scheduler.py:927-942`; the write path also requires LeagueSeason identity (`app/services/standing_sync_service.py:158-164`). Confidence 9/10 for mismatch; 5/10 for which contract is correct. | Yes — product/design decision and then align implementation/test. |
| 20 | `test_refresh_job_isolates_failures_and_continues` — expected 3 pairs processed, got 0; captured `AttributeError: 'FakeSchedulerDB' object has no attribute 'info'` | Standings scheduler | Advisory-lock implementation stores its pinned connection in `db.info`; test fake has `execute` but no SQLAlchemy-session `info` property. Failure occurs before pair processing. | C | NONE — incomplete DB fake | Test `tests/test_standings_hardening.py:380-402`; `app/services/scheduler.py:1042`. Confidence 10/10. | Yes — use the existing session-info-capable fake or model the required field. |
| 21 | `test_standings_model_declares_index_and_unique_constraint` — expects `ix_standings_league_id_season_position`, model exposes `ix_standings_league_season_position` | Standings model/schema | Test expects the pre-normalization index and constraint names. The identity migration explicitly drops the old index/constraint and creates the LeagueSeason-based names; current model matches that migration. | B | NONE — schema-name assertion only | Test `tests/test_standings_hardening.py:467-471`; `app/models/standing.py:10-12`; `alembic/versions/20260926_standing_ls_identity.py:46-73`. Confidence 10/10. | Yes — update model expectation to the current LeagueSeason identity. |
| 22 | `test_team_profile_standings_service_uses_cache_on_hit` — `AttributeError: 'object' object has no attribute 'execute'` | Team profile / standings | Team fake is supplied, but the test passes a bare DB object. The current service resolves the Team, then queries LeagueSeason to form the canonical cache key before reading cache; the query fake is missing. | C | NONE — invalid DB fake; cache contract is not exercised | Test `tests/test_team_profile_standings.py:75-87`; `app/services/standing_service.py:139-158`; `app/repositories/league_season_repository.py:12-17`. Confidence 10/10. | Yes — fake the LeagueSeason query and assert cache behavior after identity resolution. |
| 23 | `test_team_profile_standings_service_reads_db_and_populates_cache_on_miss` — same `AttributeError` | Team profile / standings | Same incomplete DB fake; actual StandingRepository/cache miss path is not reached. | C | NONE — invalid DB fake | Test `tests/test_team_profile_standings.py:92-99`; `app/services/standing_service.py:139-166`; `app/repositories/league_season_repository.py:12-17`. Confidence 10/10. | Yes — fake the LeagueSeason query. |
| 24 | `test_ensure_teams_exist_resolves_existing_masters_and_reports_missing` — exact-dict assertion fails because result includes `resolved: {10: 101}` | Team Master | Test's exact expected mapping omits the current additive `resolved` result field, which reports the provider-to-canonical mapping. The method has already handled existing/unresolved identities as expected. | B | LOW — internal result-contract/CI only; no runtime failure demonstrated | Test `tests/test_team_upsert_on_conflict.py:239-256`; `app/services/team_sync_service.py:331-392`, especially result construction at `:384-391`. Confidence 10/10. | Yes — align the expected result shape with the current resolver contract. |
| 25 | Collection: `test_live_active_registry_phase4.py` — `ImportError: cannot import name 'patch' from 'app.core.config'` | Test import / scheduler tests | Test imports a mocking helper from the application config module; it is not exported there. `unittest.mock` and pytest monkeypatch are the available test tools already used elsewhere. | E | LOW — that test module is not collected | Import `tests/test_live_active_registry_phase4.py:5`; no `patch` export in `app/core/config.py`. Confidence 10/10. | Yes — fix the test import in a later test-maintenance phase. |
| 26 | Collection: `test_odds_identity_mapping.py` — `ModuleNotFoundError: No module named 'app.services.odds_identity'` | Odds identity tests | The test imports a production helper module that is absent from the application tree; no third-party package with this name is implicated. Evidence does not establish whether these tests target planned functionality or a removed/renamed module. | E | LOW — odds identity tests unavailable; production consequence not established | Import `tests/test_odds_identity_mapping.py:3`; current Odds service files include `odds_service.py` and `odds_sync_service.py`, not `odds_identity.py`. Confidence 10/10 for absence; 4/10 for intended contract. | Yes — determine intended Odds identity API before implementation or test migration. |
| 27 | Collection: `test_odds_identity_resolution_bridge.py` — `ImportError: cannot import name 'COMMIT_NOT_CONFIRMED' from 'app.services.odds_sync_service'` | Odds sync tests | Test expects a module-level state constant not exported by current OddsSyncService. Current constants include `COMMIT_UNKNOWN` and `COMMIT_CONFIRMED`; absence is an in-repository symbol-contract mismatch, not a missing package. | E | LOW — affected tests cannot run; runtime impact not proven | Import `tests/test_odds_identity_resolution_bridge.py:8`; `app/services/odds_sync_service.py:17-20`. Confidence 10/10 for symbol mismatch. | Yes — reconcile the intended commit-state contract before restoring these tests. |
| 28 | Collection: `test_odds_phase3_hardening.py` — `ImportError: cannot import name 'IDENTITY_BOUNDARY_VIOLATION' from 'app.services.odds_sync_service'` | Odds sync / analytics tests | Test imports a module-level error code from OddsSyncService; the string is used in analytics projection validation but is not exported from this module. | E | LOW — affected tests cannot run; runtime impact not proven | Import `tests/test_odds_phase3_hardening.py:13`; current error text occurs in `app/services/analytics_projection_service.py:321-327`, not as an OddsSyncService export. Confidence 10/10 for symbol mismatch. | Yes — confirm intended error-code ownership and align import/implementation. |

## 9. Classification Summary

Counts for the required 24 failed items:

| Classification | Count |
|---|---:|
| A — Production-code regression | 0 |
| B — Stale/outdated test expectation | 6 |
| C — Test fixture/mock/contract mismatch | 16 |
| D — Environment/infrastructure/tooling issue | 0 |
| E — Dependency/import/collection issue | 0 |
| F — Unrelated/pre-existing failure | 0 |
| G — Test implementation defect | 0 |
| H — Unknown/insufficient evidence | 2 |

The 4 collection errors are **E=4** separately. If the complete 28-item root
cause inventory is counted together: A=0, B=6, C=16, D=0, E=4, F=0, G=0,
H=2. The standalone console-script discrepancy is a separate **D** tooling
observation and is not included in those 28.

No item is classified F: there is no historical baseline to prove it existed
before Phase 3. “Unrelated to Event” does not establish “pre-existing.”

## 10. Impact Assessment

* No confirmed production-code regression is evidenced by the 24 failures.
* Sixteen failures stop at incomplete fakes or invalid test inputs. Those
  tests do not reach the behavior they intend to verify.
* Six failures assert an older behavior/schema/result contract; the
  LeagueSeason migration and current source explain the relevant contract
  changes.
* One scheduler query expectation remains unresolved. Its effect is potentially
  on standings refresh coverage, not Event sync; settle intended season
  enumeration before assigning a production severity.
* The four collection errors reduce coverage of Odds identity/commit
  contracts. They are in-repository import/symbol mismatches; no external
  dependency or package-version failure was identified.
* There are no evidenced CRITICAL or HIGH production issues. The unresolved
  scheduler contract is MEDIUM pending a product/source-of-truth decision;
  other evidenced test-only failures are LOW or NONE as recorded in §8.
* The direct pytest console-script path failure is tooling/import-path only.

## 11. Event Team ID Regression Assessment

**Event-related failed tests: 0. Event-related collection errors: 0.**

None of the 24 failing test names or captured production traceback locations
touches Event team ID canonicalization, EventSyncService Team resolution,
TeamRepository lookup for Events, EventRepository persistence, EventService
serialization, `match_events.team_id`, or provider-to-canonical Team mapping.
The H2H failure concerns H2H input validation and occurs before Team identity
resolution; it does not traverse Event code.

The Phase 3 implementation report states focused Event sync/freshness/
replacement/lock/cache/transaction tests passed. This root-cause audit found no
contrary failure evidence. **The Event Team ID implementation can remain
frozen; no Event change is indicated.** This is not a blanket production
certification outside the audited Event behavior.

Event-readiness answers:

1. Event Team ID implementation itself affected? **No evidence.**
2. Failed tests directly caused by Event Team ID? **No.**
3. Collection errors related to Event? **No.**
4. Is Event production behavior unsafe? **No unsafe behavior is evidenced by
   this scoped audit; this is not a blanket production certification.**
5. Can Event Team ID remain frozen? **Yes.**
6. What must be resolved before Phase 3 can be called PASS? Resolve or
   explicitly accept the suite blockers under the project's pass criteria;
   resolve the separate Historical Event Sync policy in Phase 3C. No
   Event-specific fix is required by this audit.
7. Which can be handled independently? All Odds, Standings, H2H, and Team
   Master test/source contract issues are outside Event Team identity.

## 12. Standings Failure Assessment

The standings failures group into:

* **Test fakes/incomplete setup:** #2, #3, #14-#16, #20, #22, #23. The
  current service resolves local League/LeagueSeason identities and uses a
  real AsyncSession contract; those fakes do not return the required identity
  or implement the expected session interface.
* **Stale schema/API assertions:** #6, #7, #17, #18, #21. The
  `20260926_standing_ls_identity` migration removes Standings' direct
  `league_id`/`season` columns and old index/constraint names, replacing them
  with LeagueSeason identity. The current repository and model use those
  normalized keys.
* **Unresolved allowlist result contract:** #1 expects success for a blocked
  league, while current StandingSyncService returns a `league_not_allowed`
  failure before lookup/provider sync. Available evidence does not decide
  whether a blocked sync should be a successful no-op or an explicit failure.
* **Unresolved scheduler season selection:** #19 tests Match-season SQL while
  current scheduler enumerates allowed LeagueSeason records and deduplicates
  pairs in application code. This is the one failure for which evidence does
  not decide which behavior is desired. Do not change the scheduler based on
  this assertion alone.

## 13. Collection-Error Assessment

No collection error is caused by an absent third-party dependency, interpreter
version mismatch, or import cycle based on the observed traces:

1. `patch` is imported from the wrong in-repository module.
2. `app.services.odds_identity` is absent from the current source tree.
3. `COMMIT_NOT_CONFIRMED` is not a current OddsSyncService export.
4. `IDENTITY_BOUNDARY_VIOLATION` is not a current OddsSyncService export.

The imports are test/source API contract mismatches. For the missing Odds
identity module and state/error symbols, evidence identifies what is absent
but does not determine whether to add production exports/modules or revise
stale tests. They remain test-collection blockers until separately resolved.

## 14. Pre-Phase-3 Baseline Availability

**Pre-Phase-3 baseline unavailable.**

The current suite is reproducible under the project module invocation. No
before-Phase-3 report or test output was available for comparison. Therefore
the audit does not claim any failure is proven pre-existing or caused by Phase
3.

## 15. Fix Priority List

No fixes are implemented or prescribed as code patches in this audit.

* **P0 — Must fix before production confidence:** None confirmed by this
  evidence. The unknown scheduler contract (#19) needs a decision before
  making a claim about scheduler refresh coverage, but no defect is yet proven.
* **P1 — Resolve before broad-suite/Phase PASS:** Decide the intended
  LeagueSeason-versus-Match season source for scheduler refresh (#19); restore
  or formally retire the three uncollectable Odds test contracts (#26-28);
  resolve the allowlist response expectation (#1); and correct the mocking
  import (#25). These currently block an unambiguous full-suite result.
* **P2 — Independent test contract maintenance:** Align fake League/DB/Match
  fixtures and H2H inputs (#2-5, #8-16, #20, #22-23); update tests to the
  LeagueSeason repository/model contract (#6-7, #17-18, #21); align the
  TeamSyncService exact-result expectation (#24).
* **P3 — Test-only cleanup:** After intended Odds contracts are determined,
  correct the `patch` import (#25) and remove any obsolete test module/symbol
  references rather than adding dependencies blindly.

All 24 failures and 4 collection errors must be fixed, retired, or explicitly
re-baselined before claiming the full backend suite is green. None requires
changing Event Team ID code based on this evidence.

## 16. What Must NOT Be Changed

During this audit, do not change:

* production code or Event Team identity behavior;
* tests, fixtures, mocks, or assertions;
* database rows, Redis, or migrations;
* environment/configuration to force a green run; or
* historical Event data or sync policy.

An obvious production defect or test defect discovered later must be handled in
its own approved implementation phase.

## 17. Recommended Next Phase

Proceed with the separately planned **Phase 3C** historical Event Sync policy
decision, keeping it isolated from these test findings. Follow it with a
dedicated test-contract remediation phase that first decides the unresolved
standings scheduler behavior and then fixes/retire/re-baselines the listed
collection errors and test mismatches. Keep Event Team ID implementation
frozen unless new direct regression evidence appears.

## 18. Final Verdict

**PARTIAL**

The standard project-environment run reproduced the Phase 3A result, and
root causes are well established for 22 of the 24 failed tests plus the import
failure mechanics for all 4 collection errors. No Event regression was
identified. One standings scheduler contract (#19) remains ambiguous; the
missing Odds module/symbol imports also need an explicit decision about whether
the tests or production API contract is stale. The pre-Phase-3 baseline is
unavailable. This meets PARTIAL, not PASS: the Event Team ID path remains
unimplicated, but all suite blockers are not yet fully classified as intended
contracts.
