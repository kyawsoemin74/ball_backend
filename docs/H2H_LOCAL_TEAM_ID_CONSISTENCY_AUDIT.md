# H2H Local Team ID Consistency Audit

**Audit date:** 2026-10-29  
**Scope:** Read-only audit of local-team and API-Football team identity across the backend and Flutter H2H path.  
**Conclusion:** A confirmed Flutter identity mismatch exists. No implementation was performed.

## 1. Executive Summary

The backend has a canonical identity source: `teams.team_id` is the local team ID and `teams.provider_id`, qualified by `teams.provider`, is the external provider ID. H2H synchronization resolves local match teams through this Team master before making the provider request. It stores the raw provider response under a sorted pair of local IDs and separately maps provider IDs back to local IDs for the analytics projection.

The read API returns the raw API-Football-shaped historical fixture list, whose nested `teams.home.id` and `teams.away.id` values are provider IDs. Flutter parses those provider IDs into meeting models, but calculates team names, wins, and goals by comparing them directly with local IDs supplied by the match detail flow. This is a confirmed contract mismatch: for the observed local pair `97/98` and provider pair `5/774`, team-specific summary lookups cannot match.

The GET service takes the actual home and away local team IDs from the match record and uses a match-specific Redis key before falling back to PostgreSQL. It does not call the provider, write the database, enqueue work, or populate Redis. Match availability metadata, however, uses row existence while the GET API treats an empty result as not found.

Local Docker database evidence was read inside explicit read-only SQL transactions. No provider request, sync, scheduler job, queue claim, manual sync, Redis write/delete, migration, or deployment was performed. The only repository file created by this task is this requested report.

## 2. Current End-to-End H2H Data Flow

```text
Match detail supplies local match/team IDs
        |
        v
Flutter GET /api/matches/h2h/{local_match_id}/{local_home_id}/{local_away_id}
        |
        v
Backend API validates path values and allowed match
        |
        v
H2HService replaces supplied team IDs with IDs on the local Match
        |
        +--> Redis key fover:match:h2h:<local_match_id>
        |
        +--> PostgreSQL match_h2h, canonical sorted local pair key
                  |
                  v
        Bare provider-shaped JSON fixture list
        (nested teams.*.id are provider team IDs)
                  |
                  v
Flutter API-Football parser
        |
        +--> renders fixture team names, scores, date, league and status
        |
        +--> BUG: calculates team summaries using local IDs against provider IDs

Write path (not run during this audit):
Admin POST or six-hour scheduler
        -> H2HSyncService
        -> local Match/Team master identity resolution
        -> GET /fixtures/headtohead?h2h=<provider-home>-<provider-away>
        -> raw JSONB upsert under sorted local pair
        -> normalized analytics projection and queue outcome
        -> API/scheduler commits
        -> cache invalidation after commit
```

**Evidence:** The route and match registration are in [matches.py](../app/api/matches.py#L321), [main.py](../app/main.py#L86); read and sync paths are in [h2h_service.py](../app/services/h2h_service.py#L64) and [h2h_sync_service.py](../app/services/h2h_sync_service.py#L38).

## 3. Frontend Request and Parsing Audit

### Request identity

- [VERIFIED FROM SOURCE] `ApiConstants.matchH2H()` constructs `/api/matches/h2h/$matchId/$homeTeamId/$awayTeamId` ([api_constants.dart](../../fover/lib/core/constants/api_constants.dart#L11)).
- [VERIFIED FROM SOURCE] The match detail repository passes `matchId`, `homeTeamId`, and `awayTeamId` through to the API service ([match_detail_repository_impl.dart](../../fover/lib/features/matches/data/match_detail_repository_impl.dart#L93)); the API service performs GET using that URL ([match_api_service.dart](../../fover/lib/features/home/data/match_api_service.dart#L113)).
- [VERIFIED FROM SOURCE] The list response is parsed as API-Football fixtures by `MatchH2HInfo.fromApiSportsList()` and `MatchH2HMeeting.fromApiSportsJson()` ([match_h2h_model.dart](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L69), [line 264](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L264)).
- [VERIFIED FROM SOURCE] The notifier passes the request’s IDs to the repository and keeps a status-based in-memory cache: finished match statuses have no time expiry; live statuses use 60 seconds; pre-match and unknown statuses use five minutes ([match_h2h_provider.dart](../../fover/lib/features/matches/providers/match_h2h_provider.dart#L74), [line 112](../../fover/lib/features/matches/providers/match_h2h_provider.dart#L112)).

### Summary and meeting identity

The parser reads provider IDs from `teams.home.id` and `teams.away.id`. It then uses the caller-supplied IDs to find team names and calculate wins/goals. The win and goal functions compare the meeting’s parsed IDs directly to those supplied IDs. Draws are counted without team identity comparison. See [match_h2h_model.dart](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L69), [lines 116-161](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L116).

This makes provider-specific JSON a de facto public contract: the parser depends on the provider keys `fixture`, `teams`, `goals`, and `league`. A backend-normalized map is accepted by a separate parser, but the current backend H2H route returns a bare list, not that summary map.

### Confirmed example

**[VERIFIED AT RUNTIME from existing local DB data]** The request identifiers are local `97/98`; their Team master provider IDs are `5/774`. A stored historical fixture’s `teams.*.id` values are provider IDs. Flutter therefore compares meeting IDs `5/774` with input IDs `97/98`; team-name lookup returns `''`, wins and team-specific goals calculate as zero for that pair. The individual meeting can still contain provider team names and scores and may render correctly.

**[NOT VERIFIED]** Flutter parsing/rendering was not executed in this audit.

## 4. Backend Read API Audit

### Validation and identity

- [VERIFIED FROM SOURCE] The route requires positive integer path values and calls `_assert_match_allowed()` for the match before reading H2H ([matches.py](../app/api/matches.py#L321)).
- [VERIFIED FROM SOURCE] `H2HService.get_cached_h2h()` looks up the match and replaces caller-supplied team IDs with `Match.home_team_id` and `Match.away_team_id`. The supplied IDs are not independently validated as the match’s team IDs ([h2h_service.py](../app/services/h2h_service.py#L64)).
- [VERIFIED FROM SOURCE] The database key sorts the local team IDs (`min-max`) and is symmetric ([h2h_service.py](../app/services/h2h_service.py#L57)).

### Read order, side effects, and response

- [VERIFIED FROM SOURCE] Cache lookup is first, under `fover:match:h2h:<match_id>`; on a miss, the service verifies both Team rows and queries `match_h2h` by the local pair key. Cache failures are handled by the cache service as misses.
- [VERIFIED FROM SOURCE] The H2H read path contains no provider request, queue enqueue/claim, database write, or Redis cache population. Cache rebuilds are explicitly reserved for a source owner; sync owners invalidate after commit.
- [VERIFIED FROM SOURCE] The route returns the provider-shaped list directly, after sorting by fixture timestamp in the service. It has no H2H response model.
- [VERIFIED FROM SOURCE] Missing match returns 404 `{"detail":"Match not found"}`. An absent H2H record or empty list returns 404 `{"detail":"H2H data not found"}`. Invalid/nonpositive path parameters are rejected by FastAPI validation.
- [VERIFIED FROM SOURCE] `_has_h2h_available()` returns true if a `match_h2h` row exists, without checking whether `data` is empty. Therefore `has_h2h` may claim availability where the GET endpoint returns 404 ([matches.py](../app/api/matches.py#L176)).

### Local runtime observations

- [VERIFIED AT RUNTIME] A read-only local GET for match `1285`, IDs `97/98`, returned HTTP 200 and a bare JSON list of five historical fixtures. Top-level fixture keys observed: `fixture`, `goals`, `teams`, `league`, `score`.
- [VERIFIED AT RUNTIME] Redis key `fover:match:h2h:1285` returned `EXISTS=0`, `TTL=-2`; this request used the PostgreSQL fallback. This is an observation at audit time, not evidence of a cache defect.
- [VERIFIED AT RUNTIME] A local allowed match with no pair row (match `1275`, teams `77/78`) returned HTTP 404. The request was read-only.

## 5. Provider-to-Local Identity Mapping Audit

### Authoritative identity fields

- [VERIFIED FROM SOURCE] `Match.local_match_id` is the primary key and `Match.provider_fixture_id` stores the provider fixture identifier; `Match.match_id` and `fixture_id` are synonyms ([match.py](../app/models/match.py#L8)).
- [VERIFIED FROM SOURCE] `Match.home_team_id` and `away_team_id` are foreign keys to local `teams.team_id` ([match.py](../app/models/match.py#L43)).
- [VERIFIED FROM SOURCE] `Team.team_id` is the local ID; `Team.provider` and nullable `Team.provider_id` represent the external identity. A unique constraint exists on `(provider, provider_id)` ([team.py](../app/models/team.py#L8)).
- [VERIFIED FROM SOURCE] `TeamRepository.find_by_provider_identity()` maps a provider and provider ID to a Team master row and explicitly raises if multiple rows are returned ([team_repository.py](../app/repositories/team_repository.py#L8)).

### Sync mapping and checks

- [VERIFIED FROM SOURCE] Sync first loads the local match, requires `api-football`, and validates a positive provider fixture ID. It then uses the local match’s team IDs to load Team rows; requires two distinct teams, provider `api-football`, and non-null provider IDs; and validates provider IDs as positive integer values before calling the provider ([h2h_sync_service.py](../app/services/h2h_sync_service.py#L38)).
- [VERIFIED FROM SOURCE] The provider fixture ID is validated but the pair-based H2H request uses resolved provider team IDs, not that fixture ID.
- [VERIFIED FROM SOURCE] The raw H2H result is persisted under sorted `Team.team_id` local IDs. The analytics projection separately looks up each provider team ID in Team master and stores the sorted local pair, while retaining historical home/away provider IDs in dedicated fields ([analytics_projection_service.py](../app/services/analytics_projection_service.py#L371)).
- [VERIFIED FROM SOURCE] Missing, null, non-numeric, nonpositive, same-team, unsupported-provider, or duplicate provider identity cases are rejected by sync validation or mapping lookup. The database unique constraint prevents duplicate non-null `(provider, provider_id)` identities; nullable provider IDs are allowed by the model, but H2H sync rejects missing IDs.
- [VERIFIED FROM SOURCE] Historical home/away orientation is preserved in the raw provider data and in analytics’ `home_provider_team_id` / `away_provider_team_id`; this audit found no mapping of those historical IDs to local IDs in the raw H2H result or the Flutter list parser.
- [VERIFIED FROM SOURCE] The H2H path does not assume local ID equals provider ID; the Flutter summary comparison does assume the IDs are interchangeable.

## 6. H2H Sync and Database Storage Audit

### Triggers and lifecycle

- [VERIFIED FROM SOURCE] Scheduler job `refresh_h2h` runs every six hours. It discovers API-Football matches within ±7 days, with two distinct teams, when the pair has no H2H row or its `updated_at` is null/at least 24 hours old ([scheduler.py](../app/services/scheduler.py#L586), [line 2001](../app/services/scheduler.py#L2001)).
- [VERIFIED FROM SOURCE] Discovery is keyed by local match ID and uses `ON CONFLICT DO NOTHING`; existing pending work is retained. Terminal `SUCCESS`/`EMPTY` work can be reset to `PENDING` on renewed discovery ([h2h_sync_work_repository.py](../app/repositories/h2h_sync_work_repository.py#L13)).
- [VERIFIED FROM SOURCE] Scheduler selection is ordered by match time and local match ID, claims using `FOR UPDATE SKIP LOCKED`, assigns a two-hour lease and UUID fencing token, and aims for 90 upcoming / 10 past within a maximum 100 items. Unused capacity is borrowed ([scheduler.py](../app/services/scheduler.py#L2058), [h2h_sync_work_repository.py](../app/repositories/h2h_sync_work_repository.py#L100)).
- [VERIFIED FROM SOURCE] Selected work is grouped by local pair key, so the scheduler performs one pair sync per batch group and updates sibling work. Expired processing leases return to pending. Final transitions require both `PROCESSING` state and matching claim token; retryable failures receive a six-hour retry time ([h2h_sync_work_repository.py](../app/repositories/h2h_sync_work_repository.py#L60), [line 163](../app/repositories/h2h_sync_work_repository.py#L163), [line 234](../app/repositories/h2h_sync_work_repository.py#L234)).
- [VERIFIED FROM SOURCE] The admin-only manual route is `POST /api/matches/sync/{match_id}/h2h`. It runs the same canonical match sync, records queue completion and commits before cache invalidation ([matches.py](../app/api/matches.py#L382)).

### Storage

`match_h2h` is distinct from queue state. `match_h2h` stores result JSON; `h2h_sync_work` stores fixture-level synchronization lifecycle. The result model has an integer primary key, unique/indexed non-null `h2h_key`, non-null JSONB `data`, and nullable timestamp `updated_at` ([match_h2h.py](../app/models/match_h2h.py#L7)).

`H2HRepository.upsert_one()` inserts by canonical local pair key and uses `ON CONFLICT` to replace the entire `data` array and update `updated_at` ([h2h_repository.py](../app/repositories/h2h_repository.py#L16)). Empty provider responses are persisted as an empty list. Analytics snapshots are a separate normalized projection; they are written in the same DB session/transaction, not a separate commit ([analytics.py](../app/models/analytics.py#L103), [analytics_repository.py](../app/repositories/analytics_repository.py#L49)).

### Transaction and cache ownership

- [VERIFIED FROM SOURCE] Sync services flush but do not commit. The manual API and scheduler own commit/rollback. Both invalidate match-specific H2H keys only after commit; a failed DB transaction does not reach the post-commit invalidation block ([matches.py](../app/api/matches.py#L383), [scheduler.py](../app/services/scheduler.py#L2136)).
- [VERIFIED FROM SOURCE] The GET path itself does not set a cache value or invalidate one.
- [VERIFIED AT RUNTIME] The local queue currently contains one row: match `1285`, pair `97-98`, `SUCCESS`, attempt count `1`, non-retryable, outcome count `5`. There are no expired PROCESSING rows and no due retryable FAILED rows.

## 7. Redis Cache and Queue Audit

The Redis key namespace is match-specific, not pair-specific: `fover:match:h2h:<local_match_id>`. PostgreSQL result and queue pair keys use local team IDs; the queue primary/work identity is local match ID. Analytics also uses local pair IDs for its pair identity, but stores provider IDs in source/orientation columns.

The Flutter notifier cache is independent of Redis: it caches loaded state in memory using match status to choose an expiry. Do not treat Redis absence as evidence of a problem; at the observed time it simply meant the GET read from PostgreSQL.

**Local DB evidence (read-only):**

| Observation | Result |
|---|---|
| Alembic current | `20261020_h2h_sync_work` (head) |
| Match 1285 local team IDs | `97`, `98` |
| Their API-Football provider IDs | `5`, `774` |
| `match_h2h` pair / fixture count | `97-98` / `5` |
| `analytics_h2h_snapshots` count for pair | `5` |
| Queue match 1285 | `97-98`, `SUCCESS`, attempt `1`, outcomes `5` |
| Queue status totals | `SUCCESS=1` |
| Expired PROCESSING / due retryable FAILED | `0` / `0` |
| Redis `fover:match:h2h:1285` | `EXISTS=0`, `TTL=-2` |

These queries are read-only and can be used to inspect the local development database:

```sql
BEGIN READ ONLY;

SELECT m.local_match_id, m.home_team_id, m.away_team_id,
       home.provider_id AS home_provider_id,
       away.provider_id AS away_provider_id
FROM matches AS m
JOIN teams AS home ON home.team_id = m.home_team_id
JOIN teams AS away ON away.team_id = m.away_team_id
WHERE m.local_match_id = 1285;

SELECT h2h_key, jsonb_array_length(data) AS fixture_count, updated_at
FROM match_h2h
WHERE h2h_key = '97-98';

SELECT local_match_id, pair_key, status, attempt_count, retryable,
       next_retry_at, lease_expires_at, last_outcome_count, completed_at
FROM h2h_sync_work
WHERE local_match_id = 1285;

SELECT team_low_id, team_high_id, count(*) AS snapshot_count
FROM analytics_h2h_snapshots
WHERE team_low_id = 97 AND team_high_id = 98
GROUP BY team_low_id, team_high_id;

COMMIT;
```

## 8. Current API Response Contract

The current GET response for populated data is a **bare JSON list** of API-Football-style fixture objects. The observed local record contained the top-level keys `fixture`, `goals`, `teams`, `league`, and `score`. Nested team objects contain provider IDs in `teams.home.id` / `teams.away.id`. The backend does not currently return local home/away IDs or backend-computed summary totals in this endpoint.

For absent data or `[]`, the route returns HTTP 404 with `{"detail":"H2H data not found"}`. That behavior differs from the match detail availability flag’s row-existence check.

### Minimum target contract (design recommendation only)

The future contract should:

1. Make local team IDs the only team identity exposed for comparisons and summaries.
2. Include local home/away IDs and backend-calculated wins, draws, losses, goals for, and goals against.
3. Give each historical meeting stable local home/away team IDs, scores, timestamps, status, and enough display data for the UI.
4. Preserve home/away orientation for each historical fixture even where it differs from the current match’s orientation.
5. Explicitly distinguish unavailable/unmappable history from a valid empty result; align this with `has_h2h`.
6. Avoid exposing provider IDs as the identity fields the client uses for domain calculations. Provider IDs may remain internal or be explicitly labeled diagnostic metadata if there is a documented compatibility need.

Changing the current bare list to a summary object or replacing nested IDs is potentially breaking. Before implementation, decide whether to version the route, add a new normalized response while temporarily retaining compatibility, or coordinate an atomic backend/mobile release. This audit does not select or implement one.

## 9. ID Identity Test Matrix

Expected behavior below describes the intended future design. “Static” means derived from source and not executed with fabricated fixtures. No live DB rows were modified to construct these cases.

| # | Scenario | Current behavior | Expected target behavior | Verification |
|---|---|---|---|---|
| 1 | Local IDs `97/98`; provider IDs `5/774` | Flutter compares `5/774` with `97/98`; team names/wins/team goals do not match. | Backend maps provider fixture IDs to local IDs before summaries; response carries local identity. | Current ID mapping verified in local DB; mismatch statically derived. |
| 2 | Historical home is provider ID `5`, score 2–1 | Meeting records provider ID `5`; `_computeWinsForTeam(..., 97)` does not count the win for local team 97. | Map provider ID `5` to local 97; count one win and two goals for local 97. | Static reasoning; illustrative score, not a captured fixture. |
| 3 | Historical home is provider ID `774`, score 2–1 | Meeting records provider ID `774`; comparison to local 98 fails; home/away inversion is not mapped. | Map `774` to local 98 and preserve that historical fixture’s orientation; count the correct local-team result. | Static reasoning; illustrative score. |
| 4 | Draw | Draw count is computed for any parsed meeting with equal scores; team-specific IDs are not involved. | Count a draw only for a valid meeting between the mapped pair; attribute goals by mapped team identity. | Static reasoning; `_computeDraws` located. |
| 5 | Missing score / incomplete fixture | `_toInt(null)` and invalid values return `0`; both absent values can be mistaken for a 0–0 draw. | Treat missing scores as unknown/incomplete; do not count as draw or goals unless completeness is established. | Static source behavior; no fixture execution. |
| 6 | Missing provider ID mapping | Sync rejects a Team row with null provider ID; analytics lookup fails if provider identity cannot resolve. Read GET can still return already-stored raw data. | Do not publish misleading summaries; report an explicit unmappable/unavailable outcome. | Static source behavior. |
| 7 | Invalid or ambiguous mapping | Sync rejects nonnumeric/nonpositive/equal provider IDs; repository raises on multiple mappings. DB unique constraint prevents duplicate non-null provider/id pairs. | Fail closed and surface mapping error; never assume IDs equal. | Static source behavior; no injected invalid DB rows. |
| 8 | Historical home/away order differs from current match | Raw fixtures preserve provider ordering; local pair key remains order-independent. Flutter’s direct ID comparison still fails, independently of order. | Map each fixture side independently to local ID and retain historical orientation. | Static source behavior; no fabricated fixture executed. |
| 9 | Empty H2H list | Sync persists `[]`; GET returns 404; `has_h2h` can be true if the row exists. | Make empty a deliberate consistent response and align availability metadata/UI. | Source verified; no empty record created. |
| 10 | Missing H2H database record | Cache miss and DB miss return `None`; GET returns 404. | Explicit unavailable/empty semantics should be stable and documented. | Runtime GET 404 observed on local match 1275; read-only. |
| 11 | Redis hit and miss | Code reads match-specific key first and falls back to DB; miss observed. Cache hit was not constructed. | Same read-through behavior can remain if cache payload follows the normalized contract and invalidation is post-commit. | Miss verified at runtime; hit only source-inspected. |
| 12 | Flutter summary when IDs differ | Names are blank; wins/goals compare incompatible namespaces; draws can still count; incomplete null scores can appear as 0–0 draws. | Summaries use mapped local identities and valid scores; client renders backend summary. | Confirmed from source and local mapping; no Flutter test run. |

## 10. Confirmed Bugs and Risks, Ranked by Severity

### H2H-ID-01 — Flutter compares provider team IDs with local IDs

- **Severity:** Medium
- **Status:** Confirmed
- **Evidence:** Flutter reads `teams.*.id` from provider JSON and compares those values with local IDs passed to `fromApiSportsList()` for team-name, win and goal calculations ([match_h2h_model.dart](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L69), [lines 116-161](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L116)).
- **Actual impact:** For observed local IDs `97/98` and provider IDs `5/774`, per-team names and result/goal totals fail identity matching. Fixture cards can still show provider names and scores.
- **Verification:** Confirmed by code plus existing local DB identity/JSON evidence; not executed in Flutter.
- **Recommended resolution:** Backend should own provider-to-local mapping and summary calculations; frontend should consume normalized local-ID response fields. Requires a separate approved design/compatibility decision.

### H2H-ID-02 — Empty-row `has_h2h` can disagree with GET

- **Severity:** Medium
- **Status:** Confirmed source-level inconsistency
- **Evidence:** `_has_h2h_available()` treats any matching result row as available ([matches.py](../app/api/matches.py#L176)); GET returns 404 for a falsey/empty result ([matches.py](../app/api/matches.py#L321)).
- **Actual impact:** A match may expose an H2H tab while the request displays an error/not-found state.
- **Verification:** Source verified; no local empty row was created.
- **Recommended resolution:** Define one empty/unavailable contract and make the match-detail flag, endpoint, and Flutter UI follow it.

### H2H-ID-03 — Null scores are coerced to zero in Flutter

- **Severity:** Low
- **Status:** Confirmed parser behavior; occurrence in current stored records not established
- **Evidence:** API-Football parser calls `_toInt()` for `goals.home/away`; `_toInt(null)` returns zero ([match_h2h_model.dart](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L264), [line 307](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L307)).
- **Actual impact:** An incomplete fixture with both goals missing may be counted as a 0–0 draw and contribute fabricated zero values.
- **Verification:** Static; no incomplete fixture was manufactured or evaluated.
- **Recommended resolution:** Preserve nullable score state and exclude incomplete meetings from result summaries or explicitly classify them as incomplete.

### H2H-ID-04 — Backend response exposes provider-specific identity contract

- **Severity:** Informational / compatibility risk
- **Status:** Confirmed
- **Evidence:** The sync stores the provider list unchanged; the GET returns that list, and Flutter parses its API-Football-specific nested fields ([h2h_sync_service.py](../app/services/h2h_sync_service.py#L107), [match_h2h_model.dart](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L264)).
- **Actual impact:** Provider payload shape and provider ID semantics leak across the backend/mobile boundary; provider changes or normalized backend output would require client compatibility handling.
- **Verification:** Source and existing local record verified.
- **Recommended resolution:** Define a stable backend DTO with local identity; plan versioning or a coordinated mobile release.

### H2H-ID-05 — H2H success telemetry checks a different result key

- **Severity:** Low; adjacent operational risk
- **Status:** Confirmed from source
- **Evidence:** `observe_sync()` checks `status == "SUCCESS"` ([monitoring.py](../app/monitoring.py#L240)); H2H refresh returns `outcome` rather than `status` ([h2h_sync_service.py](../app/services/h2h_sync_service.py#L127)).
- **Actual impact:** Successful H2H refreshes may be misclassified by that observer.
- **Verification:** Source inspection only; no telemetry event was generated during this audit.
- **Recommended resolution:** Align observer and sync result contract in a separately approved implementation task.

No evidence was found that the current H2H sync path treats local IDs as provider IDs. The confirmed direct namespace comparison occurs in the Flutter summary logic.

## 11. Evidence and Exact File/Line References

| Area | Source reference |
|---|---|
| Flutter URL | [api_constants.dart:11](../../fover/lib/core/constants/api_constants.dart#L11) |
| Flutter API request | [match_api_service.dart:113](../../fover/lib/features/home/data/match_api_service.dart#L113) |
| Flutter repository conversion | [match_detail_repository_impl.dart:93](../../fover/lib/features/matches/data/match_detail_repository_impl.dart#L93) |
| Flutter request/cache | [match_h2h_provider.dart:74](../../fover/lib/features/matches/providers/match_h2h_provider.dart#L74), [line 112](../../fover/lib/features/matches/providers/match_h2h_provider.dart#L112) |
| Flutter summary comparison | [match_h2h_model.dart:69](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L69), [lines 116-161](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L116) |
| Flutter provider JSON mapping | [match_h2h_model.dart:264](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L264) |
| Match and Team identities | [match.py:8](../app/models/match.py#L8), [team.py:8](../app/models/team.py#L8) |
| Team identity repository | [team_repository.py:8](../app/repositories/team_repository.py#L8) |
| Backend GET and availability | [matches.py:176](../app/api/matches.py#L176), [line 321](../app/api/matches.py#L321) |
| Backend read and canonical key | [h2h_service.py:57](../app/services/h2h_service.py#L57), [line 64](../app/services/h2h_service.py#L64) |
| Provider request construction | [h2h_provider.py:17](../app/providers/h2h_provider.py#L17) |
| Sync mapping and raw write | [h2h_sync_service.py:38](../app/services/h2h_sync_service.py#L38), [line 107](../app/services/h2h_sync_service.py#L107) |
| Result upsert | [h2h_repository.py:16](../app/repositories/h2h_repository.py#L16) |
| Queue model and transitions | [h2h_sync_work.py](../app/models/h2h_sync_work.py), [h2h_sync_work_repository.py:13](../app/repositories/h2h_sync_work_repository.py#L13) |
| Scheduler trigger and logic | [scheduler.py:586](../app/services/scheduler.py#L586), [line 2001](../app/services/scheduler.py#L2001) |
| Analytics mapping | [analytics_projection_service.py:371](../app/services/analytics_projection_service.py#L371), [analytics.py:103](../app/models/analytics.py#L103) |
| Manual transaction owner | [matches.py:382](../app/api/matches.py#L382) |
| Telemetry result key | [monitoring.py:240](../app/monitoring.py#L240) |

Existing backend tests inspected include [test_h2h_publication.py](../tests/test_h2h_publication.py) and [test_h2h_sync_work.py](../tests/test_h2h_sync_work.py). They exercise backend persistence, publication, queue selection, sibling deduplication and failure outcomes. They do not execute the Flutter parser. No tests were run for this audit.

## 12. Static Verification vs Runtime Verification

### Static source verification

Provider request construction, Team identity lookup, response extraction, raw persistence, analytics projection, GET behavior, queue behavior, cache invalidation order, Flutter parsing, and summary calculations were traced from source.

### Existing local runtime evidence

The local Docker Compose PostgreSQL service was identified by its Compose service/container names (`postgres` / `fover_postgres`), and inspected using `psql -U user -d fover_db` in read-only transactions. Alembic current was `20261020_h2h_sync_work`. On 2026-10-29, local match `1285` had local team IDs `97/98`, provider IDs `5/774`, one `match_h2h` row with five JSON fixtures, five analytics snapshots, and one SUCCESS queue row with outcome count five. Redis key presence was inspected read-only. A GET for that record returned five items; a second read-only GET for a local fixture without a row returned 404.

### Not runtime-verified

No provider request, scheduler execution, queue discovery/claim, manual sync, lease recovery, Flutter runtime parse, Redis hit, or transaction failure was exercised. The existence of a SUCCESS queue/result record is evidence of current local state, not proof that the full lifecycle was rerun during this audit.

## 13. Frozen Modules and Files That Must Not Change

No application, test, migration, configuration, database, cache, or queue source/state was changed. The frozen responsibility boundaries remain:

- Provider performs transport.
- Service owns reads, cache handling, and domain logic.
- SyncService orchestrates writes and flushes.
- Repository performs persistence operations.
- API/Scheduler owns commit and rollback.
- Cache invalidation follows successful commit.

Any future correction should preserve these boundaries. Likely implementation surfaces, if separately approved, are the backend H2H response contract/projection and Flutter H2H model/parser/rendering. The provider client, Team master schema, and migration system need not be changed merely to correct the identified frontend comparison.

## 14. Recommended Target Design

1. Keep Team master as the authoritative mapping from `(provider, provider_id)` to local `team_id`.
2. At the backend boundary, map each historical fixture’s provider home and away IDs independently to local IDs. Preserve actual historical home/away orientation.
3. Define behavior for a fixture containing an unmapped team: reject/exclude it with observable diagnostics or expose an explicit unmappable state; never compare provider ID to local ID as fallback.
4. Have backend domain logic calculate wins, draws, losses, goals for and goals against using mapped local IDs and valid scores.
5. Return a stable DTO containing local current-pair IDs, backend summary totals, and historical meetings with local side IDs, date/time, status and nullable scores.
6. Align empty history, unavailable mapping, `has_h2h`, HTTP behavior and Flutter empty/error UI.
7. Preserve a documented compatibility path for the existing raw provider-shaped list until mobile rollout is coordinated.
8. Continue committing result, analytics and queue state before cache invalidation.

These are recommendations for design review only, not authorized changes.

## 15. Proposed Implementation Scope

If approved in a later phase, a minimal implementation scope would be:

- Backend: a typed H2H response DTO/domain mapper that resolves historical provider team IDs through TeamRepository, normalizes local identities, handles missing score/mapping cases, and computes summaries.
- Backend: explicit empty/unavailable contract and matching `has_h2h` semantics.
- Flutter: parse the normalized DTO, remove provider-ID comparisons from summary calculations, preserve historical orientation, and render nullable/incomplete scores safely.
- Tests: backend mapping/summary/empty behavior and Flutter parser/widget tests using local IDs `97/98` and provider IDs `5/774`, including both historical orientations, draws, incomplete scores, and mapping failures.
- Compatibility: decide and test whether to version the route, support both response shapes temporarily, or coordinate a synchronized release.

Do not add ID mapping tables or schema columns unless a separate design demonstrates that the existing Team master mapping cannot serve the requirement.

## 16. Acceptance Criteria for Phase 2 Design Freeze

- A documented response schema identifies local IDs as the only domain identity the client compares.
- Provider-to-local mapping is defined for both historical fixture sides, including orientation changes.
- Missing/ambiguous mapping, duplicate fixture, empty list, null score, and provider error behaviors are explicit.
- Summary semantics are defined for wins, draws, losses, goals for and goals against, including incomplete fixtures.
- `has_h2h`, GET response/status behavior, and mobile empty/error UI agree.
- Backward compatibility and rollout/versioning are decided before changing the current bare-list contract.
- Transaction owner and post-commit cache invalidation remain unchanged.
- Acceptance tests cover all 12 matrix cases, with isolated fixtures and no production dependency.
- The Phase 2 design freeze is reviewed and approved before implementation starts.

## Final Conclusion

1. Provider IDs come from `Team.provider_id`; local match/team IDs come from `Match` and `Team.team_id`.
2. Backend sync maps the current pair before requesting H2H and stores the provider list unchanged; Flutter currently parses provider fixture IDs without translating them to local IDs.
3. PostgreSQL stores raw provider JSON under a local sorted pair key, plus separate normalized analytics rows and fixture-keyed queue state.
4. Backend GET resolves the actual teams from the match and returns stored/cached provider-shaped data without syncing.
5. Flutter sends local IDs but currently compares them with provider IDs from the response, confirming a Local ID ↔ Provider ID mismatch.
6. Backend should own provider-to-local mapping and summary identity; Flutter should consume the normalized local-ID contract and render it.
7. Phase 2 design can begin as a design-only step; implementation should wait for explicit approval of the response/compatibility decisions.

**Integrity confirmation:** Worktree was clean before creating this requested report. During the audit, source files and runtime DB/cache/queue state were not modified; no provider request, sync, migration, production access, deployment, commit, or push occurred.
