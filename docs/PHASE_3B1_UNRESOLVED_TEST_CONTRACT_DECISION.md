# Phase 3B.1 — Unresolved Test Contract Decision

**Date:** 2026-10-07  
**Mode:** Audit / decision only; no code, test, DB, or Redis changes

## 1. Objective

Resolve the intended contracts behind the two remaining Phase 3B test
failures:

1. Standing sync response when the requested League is not allowlisted.
2. Season identity source used by the standings scheduler.

This report does not address Historical Event Sync policy (Phase 3C) and does
not reopen the cleared Event Team ID implementation.

## 2. Scope

Inspected only the two contracts and their adjacent source/tests:

* `StandingSyncService`, related sync services, standings API and scheduler,
  allowlist tests.
* Scheduler standings-pair query, `LeagueSeason`/`Match` models, standings
  sync/repository, LeagueSeason migration and related tests/docs.

No tests or commands that write database/cache state were run. No source,
configuration, DB, Redis, or Event identity behavior was changed.

## 3. Issue A — Allowlist Contract

### Current behavior

`StandingSyncService.sync_standings` normalizes the requested season, reads
allowed local League IDs, and if the requested League is not present returns:

```python
{
    "success": False,
    "league_id": league_id,
    "season": int(season_text),
    "updated": 0,
    "message": "League is not allowed for standings synchronization",
    "reason": "league_not_allowed",
}
```

The check occurs before League identity lookup, provider calls, LeagueSeason
lookup, or persistence. It is therefore a deliberate block/no-write response,
not an accidental exception.

### Test expectation

`test_standing_service_sync_standings_skips_unallowed_league` expects
`success=True`, `updated=0`, and the generic
`"League is not allowed for synchronization"` message. This matches the
no-op convention in fixture/Match synchronization, but the test does not
currently pass.

### Architecture / caller behavior

* `MatchService.sync_full_season` returns `success=True` with zero counters
  when a requested League is not allowlisted.
* `FixtureSyncService.sync_full_season` does the same, with an explicit
  `SKIPPED LEAGUE` log and zero writes.
* Fixture batch processing filters non-allowlisted League fixtures and returns
  overall `success=True` when every fixture is simply filtered. Unresolved
  League identity is a distinct failure.
* `LeagueSyncService` tests cover filtering unallowed Leagues while an overall
  multi-League operation succeeds.
* The standings scheduler queries only the join of `LeagueSeason` and
  `AllowedLeague`. Under normal execution it does not send an unallowed
  League to standings sync. If a League becomes disallowed between query and
  service call, current behavior counts the result as a failed pair and rolls
  back; there is no special “skip” result interpretation in the scheduler.
* The admin standings sync route returns the service result in its response;
  `success=False` causes rollback, while `success=True` commits. It does not
  translate the service result into a different API status.
* The public standings read API separately returns HTTP 404 for a
  non-allowlisted League. That is a read-visibility behavior, not the sync
  service result contract.
* A prior League identity audit documents a filtered fixture returning HTTP
  200 with `success=True` and zero writes when the League is not allowed.

### Contract decision

**An allowlist miss in a sync operation is an expected filtered no-op:
`success=True`, zero updated/inserted records, and an explicit skip message.**

The sync must still distinguish this policy filter from malformed season,
unresolved canonical League/LeagueSeason identity, provider failure, or
persistence failure, which remain failures. The existing StandingSyncService
behavior is inconsistent with the established fixture/Match no-op convention.
The failing test's success expectation is consistent with that convention;
its message text should follow the service's established response wording.

The scheduler's allowlist join normally prevents this branch from being
reached. An allowlist change during a running scheduler cycle should be
accounted as a skip rather than an operational failure if the service returns
the agreed no-op contract.

## 4. Issue B — Scheduler Season-Selection Contract

### Current behavior

`LiveUpdateScheduler._get_allowed_standings_pairs` selects
`LeagueSeason.league_id` and `LeagueSeason.season`, joins `AllowedLeague` on
the canonical local League ID, normalizes each season, de-duplicates the
pairs in Python, and passes each `(league_id, season)` to
`football_service.sync_standings`.

`StandingSyncService` resolves that exact `(league_id, season)` to a
`LeagueSeason` row. It verifies provider consistency, obtains provider League
identity from League Master, and passes `league_season.id` to its upsert.
`StandingRepository` writes and removes rows scoped by `league_season_id`.

`Match.season` is a separate nullable fixture field. The scheduler's current
standings query does not use it.

### Test expectation

`test_refresh_pair_query_uses_match_season_and_distinct_pairs` expects SQL
against `matches.season`, `DISTINCT`, and a non-null Match season predicate.
It expressly rejects a LeagueSeason-based source. This expectation conflicts
with the current canonical standings persistence identity.

### Architecture evidence

* `LeagueSeason` has a unique `(league_id, season)` key and represents a
  provider League's season identity and metadata (`provider`, `provider_id`,
  dates, and `current`).
* The migration
  `alembic/versions/20260926_standing_ls_identity.py` explicitly says it
  scopes standing rows by canonical LeagueSeason identity. It migrates old
  `(league_id, season)` standing pairs to `league_season_id`, makes that
  foreign key non-null, and removes the old direct `Standings.league_id` and
  `Standings.season` columns.
* `Standings` is unique by `(league_season_id, team_id)`. The standing
  repository's read, delete, and upsert methods operate on
  `league_season_id`.
* Phase 6 standing runtime evidence states: “League Season owns
  league/season identity” and “Standing Sync consumes canonical local League
  and Team IDs.”
* `Match.season` remains useful fixture context. For example,
  `LeagueStructureResolver` chooses Match season when available, then falls
  back to League season, but its standings lookup joins the `Standings`
  record through `LeagueSeason`. That per-Match structure lookup does not make
  Match the canonical identity of a league's aggregate standings snapshot.
* Standing sync refuses to persist when the requested LeagueSeason cannot be
  resolved. The canonical key for persisted standings is therefore the
  LeagueSeason row, not a Match row.

### Contract decision

**The standings scheduler's season source is `LeagueSeason`, and it should
pass each allowed LeagueSeason's own season with its canonical local
`league_id`.**

When `Match.season` and `LeagueSeason.season` differ, the standings scheduler
uses the LeagueSeason value. Match season remains fixture metadata and can be
used by Match-specific decisions; it does not override the season identity
under which aggregate standings are stored. If several LeagueSeason rows
exist for one allowed League, each distinct LeagueSeason pair is an
independent standings scope.

Under this contract, the test requiring Match-season SQL is stale relative to
the normalized LeagueSeason architecture. Its general de-duplication goal is
already satisfied by the scheduler's set of `(league_id, season)` pairs, but
the expected query source is no longer correct.

This decision concerns the canonical source and identity passed to standings
sync. The current query enumerates every LeagueSeason for each allowlisted
League (it does not filter to `current=True` or to seasons present in
`matches`). No reviewed design document establishes an additional rule to
restrict scheduler candidates to current seasons or seasons represented by
Matches; do not add such a restriction as part of this contract decision.

## 5. Source Evidence

| Evidence | Relevance |
|---|---|
| `app/services/standing_sync_service.py` allowlist guard at `sync_standings` entry | Current explicit false result before provider or persistence |
| `app/services/match_service.py` and `app/services/fixture_sync_service.py` allowlist checks | Neighboring sync services model blocked Leagues as successful zero-work no-ops |
| `app/services/scheduler.py` `_get_allowed_standings_pairs` and `_refresh_standings_job` | Scheduler selects allowed LeagueSeason pairs and forwards their season |
| `app/models/league_season.py` | Canonical local `(league_id, season)` uniqueness and season metadata |
| `app/models/match.py` | `Match.season` is a nullable fixture field, separate from LeagueSeason |
| `app/models/standing.py` and `app/repositories/standing_repository.py` | Standing identity and repository operations use `league_season_id` |
| `app/services/standing_sync_service.py` upsert path | Resolves exact LeagueSeason and persists under its ID |
| `app/api/leagues.py` standings GET and admin sync routes | Read 404 is distinct from sync's returned no-op/failure contract |
| `alembic/versions/20260926_standing_ls_identity.py` | Explicit schema transition to canonical LeagueSeason identity |
| `docs/PHASE_6_REAL_STANDING_SYNC_RUNTIME_VERIFICATION_REPORT.md` | Runtime verification calls League Season owner of league/season identity |
| `docs/PHASE_1_CURRENT_LEAGUE_IDENTITY_AUDIT_REPORT.md` | Existing allowlist-filtered sync no-op returns HTTP 200 |

## 6. Test Evidence

* `tests/test_allowed_leagues.py::test_standing_service_sync_standings_skips_unallowed_league`
  asserts successful no-op semantics, matching neighboring sync modules.
* `tests/test_standings_hardening.py::test_refresh_pair_query_uses_match_season_and_distinct_pairs`
  asserts a Match-season source and rejects LeagueSeason; this predates or
  otherwise conflicts with the canonical standing identity represented in the
  current model/migration.
* Season resolution tests for `LeagueStructureResolver` assert Match-season
  precedence for a per-Match standings-presence lookup. They do not test the
  periodic scheduler and do not establish that the scheduler should use Match
  season.
* Scheduler pair tests also assert filtering to allowlisted Leagues and
  deterministic distinct pairs; the current implementation satisfies those
  broad requirements using LeagueSeason rows and Python set de-duplication.

## 7. Architecture Evidence

The relevant data ownership chain is:

```text
League Master (canonical league_id)
        +
LeagueSeason (canonical league_id + season identity)
        ↓ league_season_id
Standings rows (one canonical season scope)
```

`Match.season` describes a fixture's season. It is not the foreign-key
identity used by Standing rows. The scheduler is refreshing league-wide
standing snapshots, not resolving one Match's competition structure, so its
input should follow the LeagueSeason identity used by the destination data.

Allowlist status is a filter/authorization boundary on League sync scope.
Neighboring sync code distinguishes an expected filtered result (successful,
zero writes) from an actual unresolved identity or provider failure.

## 8. Decision Matrix

| Issue | Current Behavior | Test Expectation | Intended Contract | Production Wrong? | Test Wrong? | Confidence |
|---|---|---|---|---|---|---:|
| A — Standings Allowlist Response | `success=False`, `reason="league_not_allowed"`, `updated=0`; no provider call or write | `success=True`, `updated=0`, skip message | Expected allowlist filter is a successful no-op, consistent with fixture/Match sync; true data/identity/provider errors remain failures | **Yes — inconsistent with neighboring sync result contract** | **No for success/no-op; wording should align with existing message convention** | 8/10 |
| B — Scheduler Season Selection | Enumerates allowed `LeagueSeason` pairs and passes each LeagueSeason season | Selects distinct non-null `Match.season`; rejects LeagueSeason source | Use canonical LeagueSeason season for standings scheduler and persistence scope; Match season remains fixture context | **No, for canonical source/identity** | **Yes, the asserted source is stale for standings identity** | 8/10 |

## 9. Final Contract Decisions

### A. Allowlist

An unallowed League requested for a sync returns a successful zero-work skip,
with a clear message and zero counters. The current `success=False` result is
inconsistent with project sync-filter conventions. No provider or persistence
work occurs.

### B. Scheduler season

The scheduler selects and passes `LeagueSeason.season` for each allowed
canonical LeagueSeason. Match season does not supersede it. Standing rows are
stored under the exact canonical `league_season_id`.

## 10. What Must Change Later

No change is made in this phase. In a separate implementation/test phase:

1. Align the StandingSyncService allowlist result with the expected successful
   no-op convention, keeping `reason="league_not_allowed"` or an equivalent
   explicit skipped indicator only if the result contract is made consistent
   across callers.
2. Update the allowlist test's expected message to the service's agreed
   wording.
3. Update the scheduler query test to assert LeagueSeason source, allowed
   League filtering, normalization, and distinct LeagueSeason pairs instead
   of Match-season SQL.
4. Preserve the current canonical `league_season_id` repository behavior.

These are recommendations for a later phase, not authorization to edit code
or tests in Phase 3B.1.

## 11. What Must NOT Change

* Do not alter Event Team ID code or its frozen canonical identity contract.
* Do not change database rows, Redis, migrations, or provider sync behavior.
* Do not use `Match.season` as a replacement for `league_season_id` in
  StandingRepository.
* Do not infer a current-season-only scheduler rule; none was established by
  the evidence reviewed here.
* Do not turn genuine League/LeagueSeason identity failures or provider
  failures into successful allowlist skips.

## 12. Confidence / Remaining Unknowns

* **Allowlist contract — 8/10.** Multiple neighboring sync paths and the
  failing test agree on successful no-op filtering; the scheduler prefilters
  allowed pairs. No standalone sync-result specification was found, and the
  public standings GET correctly uses HTTP 404 for hidden/disallowed reads.
  That GET behavior does not outweigh the sync conventions, but the admin
  endpoint's desired response wording is not separately specified.
* **Season identity source — 8/10.** The model, migration, repository, sync
  service, and Phase 6 architecture evidence consistently make LeagueSeason
  canonical for aggregate standings. No reviewed specification decides
  whether periodic scheduler candidate enumeration should be limited to
  current LeagueSeasons or seasons represented by Match rows. The existing
  scheduler enumerates all LeagueSeason rows for allowed Leagues; this audit
  retains that behavior and does not endorse a new restriction.
* No evidence in this phase touches or weakens the Phase 3 Event Team identity
  decision.

## 13. Final Verdict

**PASS**

Both previously unresolved contracts have evidence-backed decisions:
allowlist misses during sync are expected successful no-op filters, and the
standings scheduler uses canonical LeagueSeason season identity. The remaining
candidate-enumeration nuance (current/all LeagueSeason seasons versus
Match-present seasons) is recorded as unspecified; it does not change which
entity owns the season identity used for standings persistence. No code or
tests were changed.
