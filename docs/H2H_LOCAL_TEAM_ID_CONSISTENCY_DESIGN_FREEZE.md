# H2H Local Team ID Consistency — Design Freeze

**Phase:** 2 — design only  
**Date:** 2026-10-29  
**Status:** Proposed design; decisions marked for approval below  
**Primary evidence:** [Phase 1 H2H Local Team ID Consistency Audit](./H2H_LOCAL_TEAM_ID_CONSISTENCY_AUDIT.md)

> This document defines a proposed implementation contract. It does not authorize or perform implementation. No source, tests, database, cache, queue, provider, or production state was changed in this phase.

## 1. Executive Summary

Phase 1's confirmed issue remains present in current source: the Flutter API-Football parser reads provider team IDs from historical fixtures, then compares them to local team IDs supplied from match detail when calculating team names, wins, and goals. In the observed local pair, local IDs are `97/98` while provider IDs are `5/774`.

The backend already has the required canonical mapping: `teams.team_id` is the local ID and `(teams.provider, teams.provider_id)` is the unique provider identity. H2H sync resolves current match teams through this Team master, and the analytics projection already resolves provider IDs back to local IDs. The smallest correction is to put equivalent historical-fixture normalization and summary calculation in the backend read Service.

Replacing the existing bare provider-shaped response would break the current Flutter parser and potentially undocumented consumers. Therefore, the recommended rollout is:

1. Keep `GET /api/matches/h2h/{match_id}/{team1_id}/{team2_id}` unchanged as the legacy contract.
2. Add `GET /api/v2/matches/h2h/{match_id}/{team1_id}/{team2_id}` with a versioned normalized response.
3. Switch Flutter to v2 only after backend v2 is deployed.
4. Align match-detail `has_h2h` with whether the v2 response contains displayable, locally mapped meetings.
5. Keep raw provider JSON storage, analytics projection, durable queue, transaction ownership, and post-commit cache invalidation unchanged.

The response/status choices and summary eligibility rules below are ready for explicit approval. Phase 3 implementation must not begin until the user approves this freeze.

## 2. Pre-Design Verification

The complete Phase 1 report was reviewed, and its material findings were checked against current backend and Flutter source.

| Phase 1 finding | Current-source check | Result |
|---|---|---|
| Flutter sends local match/team IDs in the H2H URL | `ApiConstants.matchH2H`, match-detail repository and API service | Confirmed |
| Flutter parses provider-shaped fixture fields | `MatchH2HMeeting.fromApiSportsJson` reads `fixture`, `teams`, `goals`, `league` | Confirmed |
| Flutter summary compares those parsed IDs with supplied IDs | `_computeWinsForTeam`, `_computeGoalsForTeam`, `_teamNameForId` | Confirmed |
| Team master is the canonical local/provider identity mapping | `Team.team_id`, `Team.provider`, `Team.provider_id`; TeamRepository lookup | Confirmed |
| Sync resolves current pair provider IDs and stores under local pair key | H2HSyncService and H2HRepository | Confirmed |
| H2H GET is read-only and returns raw list | route and H2HService read path | Confirmed |
| Empty result and `has_h2h` can disagree | GET checks falsey result; availability checks row existence | Confirmed |
| Existing Flutter/parser and backend H2H tests cover separate surfaces | tests and Flutter test suite searches | Confirmed |

No material contradiction with Phase 1 was found. The repository has an existing v2 API namespace for player endpoints, but no current v2 H2H route was found. Within the backend and Flutter repositories searched, the Flutter match-detail flow is the only direct client of the H2H GET contract; unknown external clients cannot be ruled out.

### Verified source references

- Backend GET: [matches.py](../app/api/matches.py#L321)
- H2H read service and local pair/cache key: [h2h_service.py](../app/services/h2h_service.py#L57)
- Sync identity and raw persistence: [h2h_sync_service.py](../app/services/h2h_sync_service.py#L38)
- Existing provider identity resolution: [team_repository.py](../app/repositories/team_repository.py#L8)
- Team master: [team.py](../app/models/team.py#L8)
- Raw result upsert: [h2h_repository.py](../app/repositories/h2h_repository.py#L16)
- Flutter URL and request: [api_constants.dart](../../fover/lib/core/constants/api_constants.dart#L11), [match_api_service.dart](../../fover/lib/features/home/data/match_api_service.dart#L113)
- Flutter parser and summary calculation: [match_h2h_model.dart](../../fover/lib/features/matches/domain/models/match_h2h_model.dart#L69)
- Existing H2H backend tests: [test_h2h_publication.py](../tests/test_h2h_publication.py), [test_h2h_sync_work.py](../tests/test_h2h_sync_work.py)
- Existing H2H Flutter/UI tests: `D:\fover\test\match_h2h_enhancement_test.dart` and match-detail tab/cache tests.

## 3. Design A — Identity and Mapping Contract

### Identity ownership by layer

| Identity | Example | Authority | Use |
|---|---:|---|---|
| Local Match ID | `1285` | `matches.local_match_id` | URL match identifier; load match and its authoritative local teams |
| Local current home Team ID | `97` | `matches.home_team_id` → `teams.team_id` | Client-facing current-pair identity and Team A in the response |
| Local current away Team ID | `98` | `matches.away_team_id` → `teams.team_id` | Client-facing current-pair identity and Team B in the response |
| Provider fixture ID | Existing row's `provider_fixture_id`; no numeric value assumed here | `matches.provider_fixture_id` with `matches.provider` | Backend sync identity/validation; not used by pair H2H query and not returned as a local ID |
| Provider current home Team ID | `5` | `teams.provider_id` on local Team `97`, qualified by `teams.provider='api-football'` | Provider request only |
| Provider current away Team ID | `774` | `teams.provider_id` on local Team `98`, qualified by provider | Provider request only |
| Historical provider home/away IDs | e.g. `774` / `5` | Raw provider fixture `teams.home.id` / `teams.away.id` | Backend-only mapping input; never compared to local IDs |
| Historical local home/away IDs | `98` / `97` for that reversed example | TeamRepository lookup of each historical provider identity | Normalized response and summaries |

The numerical equality of local and provider IDs is never assumed.

### Request and resolution rules

1. Flutter continues to send the local match ID and local home/away Team IDs. The proposed v2 route is:
   `GET /api/v2/matches/h2h/{match_id}/{team1_id}/{team2_id}`.
2. All three path values must be positive integers. The backend loads the allowed local Match using `match_id`, then reads the authoritative local home/away Team IDs from that Match.
3. For v2, the unordered supplied team-ID pair must equal the unordered pair on the Match. A mismatch or duplicate team ID returns 422. Output orientation always follows the Match record, not caller order.
4. The backend loads both current Team rows and verifies that the Teams and match are API-Football identities with usable provider IDs, reusing current validation rules. It does not infer a mapping from names.
5. For each historical fixture, the read Service resolves `teams.home.id` and `teams.away.id` independently through the existing TeamRepository identity lookup with provider `"api-football"`. It places the returned `Team.team_id` values in local-ID fields.
6. Historical order is preserved. If the historical home ID `774` maps to local `98`, the normalized meeting's home remains local `98`, even though the current Match's home is local `97`.
7. Mapping uses strict provider identity values: positive integer or digit-string IDs are eligible; booleans, floats, nulls, invalid strings, and nonpositive values are not coerced into IDs.
8. A missing or ambiguous historical mapping is marked on that meeting and excluded from summaries. No fallback compares provider IDs to local IDs. Ambiguity is logged as an integrity problem; the response carries a safe mapping state without exposing provider IDs.
9. Provider fixture IDs are used internally for duplicate detection only. They are not returned as local meeting identities. The current provider fixture ID remains backend-only as well.
10. Resolve each distinct provider team identity once per response and reuse that result for repeated fixtures; do not create a cross-request identity cache as part of this work.

## 4. Design B — Backend H2H Response Contract

### Version and compatibility

The new normalized contract is v2. The current v1 route and its bare API-Football-shaped list remain unchanged during rollout. The v2 route returns a response model and never returns raw provider IDs in fields named as local IDs. No undocumented consumer should be forced to accept a shape change.

### Field definitions

All IDs below are local database IDs. Provider team and fixture IDs are not part of this public response.

| Field | Type | Nullable | Meaning |
|---|---|---:|---|
| `contract_version` | integer | No | Constant `2` |
| `match_id` | integer | No | Local Match ID |
| `home_team_id`, `away_team_id` | integer | No | Local current Match orientation |
| `availability` | enum string | No | `AVAILABLE`, `PARTIAL`, `EMPTY`, or `UNMAPPABLE` |
| `available` | boolean | No | True iff at least one historical meeting is locally mapped and displayable |
| `summary.home`, `summary.away` | team summary object | No | Counters attributed to the corresponding current local team |
| `summary.included_meeting_count` | integer | No | Distinct, valid, completed meetings included in all summary counters |
| `meetings` | array | No | Normalized meetings, including safely identified partial/unmappable records |
| `excluded_meeting_count` | integer | No | Source records omitted from `meetings` (e.g. duplicate fixture rows or non-history status) |

`availability` is assigned in this order to avoid overlapping meanings:

1. `EMPTY`: the provider list is empty or contains no historical meetings after excluding `NS` fixtures, with no malformed or otherwise excluded records requiring a partial/unmappable state.
2. `UNMAPPABLE`: non-`NS` historical fixtures exist, and every such fixture fails historical Team identity mapping because one or both IDs are missing, invalid, not found, or ambiguous.
3. `AVAILABLE`: at least one mapped, displayable historical meeting exists, and no considered source meeting was downgraded or omitted for incomplete/invalid identity, score, duplicate, or unrelated-pair data.
4. `PARTIAL`: any other non-empty result requiring omission or per-meeting diagnostics, including a mixture of mapped and unmappable fixtures, duplicate-only records, or incomplete scores. This value does not imply that a displayable mapped meeting remains; `available` separately reports that fact.

`available` is true exactly when at least one mapped, non-`NS` meeting for the requested pair is displayable. A meeting with an incomplete score may still be displayable, but it does not contribute to summary totals. `excluded_meeting_count` counts source rows not present in `meetings`; a retained but unmappable/incomplete meeting is instead represented in `meetings` with explicit status.

Each summary team object:

| Field | Type | Nullable | Meaning |
|---|---|---:|---|
| `team_id` | integer | No | Local current team ID |
| `team_name` | string | Yes | Canonical local Team name |
| `wins` | integer | No | Eligible meetings won by this local team |
| `draws` | integer | No | Eligible meetings drawn |
| `losses` | integer | No | Eligible meetings lost |
| `goals_for` | integer | No | Goals scored in eligible meetings |
| `goals_against` | integer | No | Goals conceded in eligible meetings |

Each meeting object:

| Field | Type | Nullable | Meaning |
|---|---|---:|---|
| `fixture_at` | ISO-8601 datetime string | Yes | Historical fixture date/time in UTC where source parsing permits |
| `status` | string | Yes | Provider status code, e.g. `FT`, for display only |
| `league_name` | string | Yes | Historical league display name |
| `venue_name` | string | Yes | Historical venue display name |
| `home_team` | team object | No | Historical home side; `team_id` is a local ID or null |
| `away_team` | team object | No | Historical away side; `team_id` is a local ID or null |
| `home_team.team_id`, `away_team.team_id` | integer | Yes | Local Team ID; null if mapping is unavailable/ambiguous |
| `home_team.name`, `away_team.name` | string | Yes | Provider display name only; not an identity |
| `score.home`, `score.away` | integer | Yes | Goals from the provider's `goals.home/away`; null when missing/invalid |
| `identity_status` | enum string | No | `MAPPED`, `UNMAPPABLE`, or `AMBIGUOUS` |
| `identity_issue` | enum string | Yes | Safe reason such as `PROVIDER_ID_MISSING`, `INVALID_PROVIDER_ID`, `TEAM_NOT_FOUND`, or `TEAM_MAPPING_AMBIGUOUS` |
| `summary_status` | enum string | No | `INCLUDED`, `INCOMPLETE_SCORE`, `NOT_FINISHED`, `UNMAPPABLE_TEAM`, `UNRELATED_PAIR`, or `DUPLICATE_FIXTURE` |

The meeting name is display text, never an identity key. Provider team IDs and fixture IDs are deliberately absent. A meeting without a resolvable local identity has a null local `team_id`, an explicit `identity_status`/`identity_issue`, and is excluded from summary totals.

### Complete illustrative response

This is a **design example**, not a captured provider fixture or a statement about the runtime record's scores. It demonstrates a historical fixture whose home side is provider team `774` (local `98`), reversed from the current Match's home side. The raw provider IDs are shown only in the explanatory comment and are not included in the response.

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
      "wins": 0,
      "draws": 0,
      "losses": 1,
      "goals_for": 1,
      "goals_against": 2
    },
    "away": {
      "team_id": 98,
      "team_name": "Current Away Team",
      "wins": 1,
      "draws": 0,
      "losses": 0,
      "goals_for": 2,
      "goals_against": 1
    },
    "included_meeting_count": 1
  },
  "meetings": [
    {
      "fixture_at": "2025-03-01T15:00:00Z",
      "status": "FT",
      "league_name": "Example League",
      "venue_name": null,
      "home_team": {
        "team_id": 98,
        "name": "Current Away Team"
      },
      "away_team": {
        "team_id": 97,
        "name": "Current Home Team"
      },
      "score": {
        "home": 2,
        "away": 1
      },
      "identity_status": "MAPPED",
      "identity_issue": null,
      "summary_status": "INCLUDED"
    }
  ],
  "excluded_meeting_count": 0
}
```

In this example, the historical provider IDs `774` and `5` have been mapped to local IDs `98` and `97`. The response contains only local IDs.

An unmappable historical record is represented without exposing its provider ID or pretending that an unknown ID is local:

```json
{
  "contract_version": 2,
  "match_id": 1285,
  "home_team_id": 97,
  "away_team_id": 98,
  "availability": "UNMAPPABLE",
  "available": false,
  "summary": {
    "home": {
      "team_id": 97,
      "team_name": "Current Home Team",
      "wins": 0,
      "draws": 0,
      "losses": 0,
      "goals_for": 0,
      "goals_against": 0
    },
    "away": {
      "team_id": 98,
      "team_name": "Current Away Team",
      "wins": 0,
      "draws": 0,
      "losses": 0,
      "goals_for": 0,
      "goals_against": 0
    },
    "included_meeting_count": 0
  },
  "meetings": [
    {
      "fixture_at": "2025-03-01T15:00:00Z",
      "status": "FT",
      "league_name": "Example League",
      "venue_name": null,
      "home_team": {
        "team_id": null,
        "name": "Unmapped Provider Team"
      },
      "away_team": {
        "team_id": 97,
        "name": "Current Home Team"
      },
      "score": {
        "home": 2,
        "away": 1
      },
      "identity_status": "UNMAPPABLE",
      "identity_issue": "TEAM_NOT_FOUND",
      "summary_status": "UNMAPPABLE_TEAM"
    }
  ],
  "excluded_meeting_count": 0
}
```

### Compatibility requirements

- V1 keeps the existing raw list and current semantics during the migration window.
- V2 is additive and is the only contract Flutter adopts after it is deployed.
- Do not silently return a summary map on the legacy route.
- Existing backend tests that assert the v1 raw contract remain and are extended with v2-specific tests.
- External consumers of v1 are unknown; keeping v1 avoids assuming there are none.
- Deprecation/removal of v1 is a later explicit decision, not part of this freeze.

## 5. Design C — Summary Calculation Rules

1. **Summary subjects:** the Match's local home ID (`97`, Team A) and local away ID (`98`, Team B). Their order is stable for the whole response.
2. **Mapping:** map each fixture side independently via `(provider="api-football", provider_id)` to `Team.team_id` before using it in any summary logic.
3. **Pair guard:** only fixtures whose mapped sides are exactly the two current local teams, in either order, are summary candidates. A mapped unrelated pair is returned as `UNRELATED_PAIR` and excluded.
4. **Orientation:** use mapped historical home/away IDs, not the current match orientation. A fixture with local 98 at home and local 97 away contributes scores and win/loss to the correct current team.
5. **Score source:** preserve current client semantics by reading provider `goals.home` and `goals.away`. Each must be a nonnegative integer (or a strictly valid digit string); do not substitute halftime, extra-time, penalty, or fabricated zero values.
6. **Completion requirement:** include summary results only for final result statuses `FT`, `AET`, `PEN`, `AWD`, or `WO` and only when both goals values are valid. Pending, live, cancelled, abandoned, and unknown statuses do not count as completed results. Displayable mapped meetings may still be returned with `summary_status="NOT_FINISHED"` or `"INCOMPLETE_SCORE"`.
7. **Win/draw/loss:** for each included meeting, compare its home and away goals. If equal, increment one draw for both teams; otherwise increment one win for the side with more goals and one loss for the other.
8. **Goals for/against:** assign each side's goals by its mapped local ID, regardless of home/away orientation. Both summary objects use the same included-meeting set.
9. **Incomplete score:** if either score is null, negative, malformed, or not an integer, retain the mapped meeting with nullable score values and `summary_status="INCOMPLETE_SCORE"`. Exclude it from wins/draws/losses/goals. This prevents a null-to-zero conversion from inventing a 0–0 draw.
10. **Unknown identity:** if either team cannot be mapped, keep a safe diagnostic meeting if the remaining fields are structurally usable, set the unresolved local ID to null and mark `UNMAPPABLE` or `AMBIGUOUS`; exclude it from summaries. Never compare the provider ID to `97` or `98`.
11. **Duplicates:** use provider fixture ID internally. If the same valid fixture ID occurs more than once, exclude all copies of that ID from the public meeting list and summary; increment `excluded_meeting_count` for each excluded source row. This avoids nondeterministically choosing between conflicting duplicates. A missing/invalid fixture ID cannot be deduplicated and is excluded with a safe diagnostic count.
12. **Date handling:** parse provider timestamp/date to UTC ISO-8601 where valid. A bad/missing timestamp is nullable; it alone does not fabricate a score or identity and may remain displayable if all summary conditions hold.
13. **Draws and losses:** losses are counted only among included completed meetings. For either team, `losses = included_meeting_count - wins - draws` for its own results, equivalently incremented per eligible opposite-team win.
14. **Names:** use canonical local Team names for summary subjects. For a historical mapped team use the Team master name; provider name may be used only as display fallback when mapping is unavailable and must never drive identity or summary logic.

The result-status set above is a proposed H2H summary policy, not a claim that the current Flutter parser applies this rule. It intentionally excludes cancelled/abandoned fixtures even though other backend code may classify some terminal statuses more broadly for unrelated workflows.

## 6. Design D — Data Availability and Error Contract

### HTTP and UI behavior

| Case | HTTP / response | `availability` / `available` / `has_h2h` | Frontend behavior |
|---|---|---|---|
| At least one mapped historical meeting; all included or safely represented | 200 normalized envelope | `AVAILABLE` / true / true | Render summary and meetings |
| Mapped data with some incomplete or excluded records | 200 normalized envelope with per-meeting statuses and counts | `PARTIAL` / true / true if at least one mapped, non-`NS` meeting is displayable | Render mapped meetings and summary from eligible subset; indicate missing score/partial state without turning it into request failure |
| Source row exists with empty array or no historical meetings after excluding `NS` | 200 normalized envelope, empty `meetings`, zero summary | `EMPTY` / false / false | Show existing “No head-to-head fixtures found” empty state |
| No result row exists | 404 `{"detail":"H2H data not found","code":"H2H_NOT_FOUND"}` | Not returned; match detail flag false | Hide tab when known from detail metadata; direct request uses existing not-found/unavailable UI |
| No historical team can be mapped | 200 envelope with safe unmappable diagnostics and zero summary | `UNMAPPABLE` / false / false | Show unavailable state, not a false zero-result summary |
| Some meetings map, others do not | 200 envelope with mapped and marked-unmappable meetings | `PARTIAL` / true / true if at least one mapped, non-`NS` meeting is displayable | Render available meetings; do not count unmappable ones |
| Redis unavailable, database available | Same response that DB fallback would produce; no cache error exposed as missing data | As derived from DB | Render normal result/empty state; server logs/cache metrics record Redis failure |
| Database access fails | HTTP 500 using the standard sanitized server-error response; do not return an empty success-shaped envelope | Not returned; do not claim false availability | Show retryable error state |
| Invalid path IDs or supplied local pair differs from actual Match | HTTP 422 using FastAPI validation / a stable `H2H_TEAM_IDS_MISMATCH` detail code | Not returned | Treat as request error; do not render summary |
| Match missing or not allowed | HTTP 404, existing `{"detail":"Match not found"}` convention | Not returned; match detail flag false | Existing match-not-found handling |

`has_h2h` is defined as true when the v2 availability calculation finds at least one locally mapped, non-`NS` meeting that is displayable. A meeting may be displayable while excluded from summary for an incomplete score. Empty, missing, or wholly unmappable history has `has_h2h=false`. The detail endpoint and H2H v2 endpoint must share the same read-Service normalization/availability logic; they must not implement subtly different row-existence checks.

Redis errors remain a cache miss followed by PostgreSQL lookup. Database exceptions are not swallowed into empty results. H2H GET stays read-only: no provider calls, queue operations, database writes, or cache writes.

### Existing convention evidence

The current matches API uses HTTP 404 for absent event, lineup, statistics, and H2H data; it uses FastAPI's 422 validation for invalid path constraints. This design retains those conventions for missing data and invalid inputs. The additive v2 contract changes a persisted empty list to a well-shaped 200 `EMPTY` response so empty history can be distinguished from absent storage and can agree with `has_h2h=false`.

## 7. Design E — Storage, Cache, and Sync Boundaries

| Concern | Decision |
|---|---|
| `match_h2h.data` | Continue storing the raw provider-shaped `response` JSONB. No migration or rewrite is justified by Phase 1 evidence. |
| Normalization | Normalize at read time in H2HService (read/domain logic), not in provider transport or database repository. |
| Team mapping | Reuse TeamRepository's existing provider identity lookup and `teams` uniqueness rule. Do not create a mapping table or alter Team schema. |
| Analytics projection | No change. Its normalized pair facts are a separate analytics representation and are not currently the public H2H response source. |
| Durable queue | No change to `local_match_id` work key, pair key, status, retry, lease, or fencing behavior. |
| Provider and sync orchestration | No change. Provider request remains pair-keyed by provider IDs; SyncService continues writing/flushing raw result and analytics. |
| Transaction ownership | No change. API/Scheduler remains the commit/rollback owner; cache invalidation remains after confirmed commit. |
| Existing Redis key | Keep the current match-specific raw-data key `fover:match:h2h:<local_match_id>` as the raw source cache, if populated by an existing writer. V2 reads raw cached list then normalizes it; it never interprets that key as a v2 envelope. |
| New v2 response caching | Do not add a normalized response cache in this implementation. The current GET path has no cache population/TTL policy; adding one is unnecessary for the identity fix. |
| Invalidation | Existing post-commit invalidation remains sufficient because v2 does not introduce its own cache value. If future work caches normalized envelopes, it must add a separate versioned key and invalidate it after commit. |
| V1 compatibility | Retain v1 raw response. Do not place v2 envelopes under the legacy raw key. |

The current codebase does not show a H2H cache writer in the GET path. A cache miss is not treated as a defect. The design supports a future raw-list cache hit without making that list the public v2 response.

## 8. Design F — Frontend Responsibilities

The Flutter client continues to send **local** IDs. It no longer derives team identity or result summaries from provider fields.

| Future file | Current responsibility | Proposed modification | Dependency / risk |
|---|---|---|---|
| `D:\fover_backend\app\api\v2\matches.py` (new) | No H2H v2 route exists | Define v2 read-only route, validate local identity pair, call H2H read service | Depends on stable response schema; additive route |
| `D:\fover_backend\app\main.py` | Registers API routers | Include v2 match router under `/api/v2/matches` | Additive route registration; no existing route removal |
| `D:\fover_backend\app\schemas\h2h_v2.py` (new) | No H2H v2 response schema exists | Define typed contract and response validation | Schema precision; no DB impact |
| `D:\fover_backend\app\services\h2h_service.py` | Redis/DB read path and raw-list sort | Add normalized v2 read and shared availability calculation; resolve historical IDs via TeamRepository | Core mapping logic; must preserve v1 method behavior |
| `D:\fover_backend\app\api\matches.py` | Existing GET and match availability flags | Change `has_h2h` to the shared normalized availability semantics; preserve legacy H2H GET | Detail-flag behavior changes for empty/unmappable data |
| `D:\fover\lib\core\constants\api_constants.dart` | Existing v1 URL builder | Add v2 URL builder using the same local IDs | Additive |
| `D:\fover\lib\features\home\data\match_api_service.dart` | HTTP GET transport | Add v2 H2H request; retain old method only while needed by compatibility/tests | Additive |
| `D:\fover\lib\features\matches\data\match_detail_repository_impl.dart` | Parses map or API-Football list | Parse the v2 normalized contract; stop selecting provider-list parser for the v2 path | Requires backend v2 deployment before client rollout |
| `D:\fover\lib\features\matches\domain\models\match_h2h_model.dart` | Accepts both summary maps and API-Football fixture lists; computes summaries | Model contract v2 fields; summary values are parsed, not computed from meeting IDs; local IDs only | Remove provider-ID/local-ID comparison paths from v2 |
| `D:\fover\lib\features\matches\providers\match_h2h_provider.dart` | Request/cache/loading/error state | Use v2 result, preserve existing in-memory TTL and add explicit empty/partial/unmappable state if needed | UI state compatibility |
| `D:\fover\lib\features\matches\presentation\sections\match_detail_h2h_section.dart` | Displays summary and up to five meetings; assumes scores are ints | Render server summary, nullable scores and incomplete/unmappable states; keep loading, retry, empty, Show More behavior | UI changes; preserve intended layout |
| `D:\fover_backend\tests\test_h2h_read_service.py` (new) | No dedicated normalized read-service suite exists | Test mapping, orientation, summary and data quality in isolated fixtures | Use fake TeamRepository/DB; no provider/network |
| `D:\fover_backend\tests\test_h2h_api_v2.py` (new) | No v2 H2H API test exists | Test path validation, response schema, statuses, and read-only boundary | Use dependency overrides/mocks |
| `D:\fover\test\match_h2h_model_test.dart` (new) | No dedicated normalized parser test found | Test local-ID response parsing, nullable scores and that summaries are not recomputed from provider IDs | Pure unit tests |
| `D:\fover\test\match_h2h_enhancement_test.dart` | Existing H2H UI behavior tests | Update fixtures to normalized response and retain summary/meeting UI expectations | Existing UI regression suite |
| `D:\fover_backend\docs\H2H_LOCAL_TEAM_ID_CONSISTENCY_DESIGN_FREEZE.md` | This design artifact | Remains the approved contract reference if accepted | Documentation only |

### Explicitly excluded from implementation

- `app/models/team.py`, `app/models/match.py`, migrations, provider identity schema, and Team master identity rules.
- `app/providers/h2h_provider.py`, FootballAPIClient, and provider endpoint/transport behavior.
- `app/services/h2h_sync_service.py`, `app/repositories/h2h_repository.py`, analytics projection/model/repository, and `h2h_sync_work` model/repository.
- Scheduler selection, discovery, retry, lease, pair deduplication, and queue state.
- Odds, events, lineups, standings, match storage, and unrelated frozen modules.
- Production data, local DB schema/data, Redis contents, and environment configuration.

No source or test change is authorized by this design phase.

## 9. Design G — Acceptance and Regression Matrix

All cases should use isolated fake repositories/unit fixtures unless explicitly called a local integration test after separate authorization. No test should contact the provider or mutate the shared local database/Redis.

| # | Setup | Expected backend response / summary | Expected Flutter behavior | Test isolation |
|---|---|---|---|---|
| 1 | Match `1285`, local `97/98`, Team master maps to provider `5/774` | Envelope IDs are `97/98`; summary subject IDs are `97/98`; no provider IDs in local-ID fields | Parse local IDs and display backend summary | Fake TeamRepository + fake source list |
| 2 | Historical home provider ID `5`, away `774`; final 2–1 | Resolve home local `97`; local 97 gets win, 2 GF/1 GA | Show historical home `97`, score 2–1, Team A win | Isolated fixture |
| 3 | Historical home provider ID `774`, away `5`; final 2–1 | Resolve home local `98`; local 98 gets win, local 97 loss | Preserve reversed historical order; totals attach to correct teams | Isolated fixture |
| 4 | Same pair returned with reversed home/away order across fixtures | Each meeting preserves its own mapped home/away sides | Rows retain historical orientation; summary remains by local identity | Isolated fixture |
| 5 | Local 97 wins a completed fixture regardless of side | Increment local 97 wins, opponent losses; goals assigned by local identity | Render exact server totals; do not recompute by IDs | Isolated fixture |
| 6 | Local 98 wins a completed fixture regardless of side | Increment local 98 wins, local 97 losses | Render exact server totals | Isolated fixture |
| 7 | Completed 1–1 | Increment draws for both; goals for/against assigned by local IDs | Render one draw; display 1–1 | Isolated fixture |
| 8 | One/both goals missing, malformed, negative, or null | Return nullable score; `INCOMPLETE_SCORE`; exclude from all summary counters | Render partial score/unavailable-score state, never fabricate 0–0 | Isolated fixture |
| 9 | Provider team ID has no Team master row | Meeting identity `UNMAPPABLE`; null local side ID; exclude from totals; response `PARTIAL` or `UNMAPPABLE` | Do not infer identity; show partial/unavailable state | Fake TeamRepository |
| 10 | TeamRepository returns ambiguous provider identity | Do not choose arbitrarily; mark ambiguous safely, log integrity issue, exclude from totals | Do not display a false summary; allow partial/unavailable state | Fake repository returning ambiguity |
| 11 | Source result list is empty | HTTP 200 envelope, `EMPTY`, zero totals, empty meetings; `has_h2h=false` | Existing no-history empty state, not request error | Fake DB |
| 12 | No H2H result row and no usable raw cache | HTTP 404 `H2H_NOT_FOUND`; `has_h2h=false` | Existing unavailable/not-found handling | Fake DB/cache |
| 13 | Redis raw-list cache hit | Normalize cached raw list into v2 contract; no cache write | Parse normalized envelope | Mock cache + fake mapping; assert exact legacy raw key |
| 14 | Redis miss/failure, DB available | Read raw DB list and return same normalized contract; no cache set | Normal result/empty state, not cache error | Mock Redis and fake DB |
| 15 | Mapped meeting has incomplete score plus another valid meeting | `PARTIAL`; valid row contributes totals, incomplete row remains with null score and exclusion reason | Show both safely; totals reflect only valid meeting | Isolated fixture |
| 16 | Existing H2H screen has summary card, first-five list, Show More, retry, and empty behavior | Contract carries fields needed by existing UI | Preserve layout and existing intended states; summary comes from backend | Flutter widget tests |
| 17 | Invoke v2 GET | Only SELECT/cache read; no provider call, enqueue, DB flush/write, commit, or cache set | Same output as read contract | API test with forbidden-call/write mocks |
| 18 | Existing sync/manual/scheduler behavior | Existing raw `match_h2h` list, analytics projection, queue state transitions unchanged | No change to existing v1 consumer until Flutter cutover | Existing H2H sync/queue tests remain |
| 19 | Duplicate source fixture ID repeated identically or conflicting | Exclude all copies of that ID, count exclusions; no nondeterministic summary contribution | Render remaining valid meetings and partial status | Isolated fixture |
| 20 | Historical mapped teams are not the current pair | Return safe diagnostic meeting `UNRELATED_PAIR`; exclude from totals | Do not attribute unrelated result to current teams | Isolated fixture |
| 21 | DB read fails | Generic sanitized HTTP 500; no empty success fallback | Error/retry state | Mock DB exception |
| 22 | Supplied local team pair differs from match pair | 422 mismatch; no provider/sync side effect | Treat as request error | API dependency override |
| 23 | Match missing or not in allowed leagues | Existing 404 match response | Existing match-not-found behavior | API dependency override |

## 10. Confirmed Bugs and Risks

| ID | Severity | Finding | Confirmed impact | Design control |
|---|---|---|---|---|
| H2H-ID-01 | Medium | Flutter compares provider fixture IDs to local Team IDs | Summary names, wins and goals fail when numeric namespaces differ (observed `97/98` vs `5/774`) | Normalize in backend and consume local-ID response |
| H2H-ID-02 | Medium | `has_h2h` uses row existence but GET treats empty as not found | H2H tab may appear for unusable/empty source data | Shared v2 availability calculation and empty envelope |
| H2H-ID-03 | Low | Flutter parser converts absent goals to zero | Incomplete fixtures can be misclassified as draws | Nullable scores and explicit summary eligibility |
| H2H-ID-04 | Informational | Public API exposes provider-specific schema | Mobile depends on API-Football field names and identity semantics | Versioned backend DTO; retain v1 for compatibility |
| H2H-ID-05 | Low, adjacent | H2H telemetry observer checks `status`, sync result returns `outcome` | Successful refresh may be mislabeled by observer | Excluded from this identity design; separate scoped follow-up if approved |

## 11. Decision Table

“Approval required” means the decision changes a public contract or business rule and must be explicitly accepted before Phase 3. “No” means the recommendation follows existing architecture/evidence and introduces no independent product/API policy choice.

| Decision ID | Design question | Recommended decision | Phase 1 evidence | Alternatives considered | Compatibility impact | Approval required |
|---|---|---|---|---|---|---|
| D-01 | How to evolve the current bare-list response? | Add `/api/v2/matches/h2h/...`; keep v1 unchanged | V1 is a bare provider list consumed by Flutter; unknown external consumers cannot be excluded | Replace v1 in place; coordinate one atomic breaking rollout | Additive route avoids silent breaking change; extra temporary endpoint | YES |
| D-02 | Where is provider→local mapping performed? | H2H read Service using existing TeamRepository/Team master | Unique `(provider, provider_id)` and current analytics mapping already exist | New mapping table; Flutter-side mapping; team-name inference | No schema change; client no longer handles provider IDs | NO |
| D-03 | What should the v2 payload contain? | Typed local-ID envelope with summary and normalized meetings as specified | Current Flutter computes summaries from provider IDs; API has no response schema | Keep raw list and fix Flutter with another mapping endpoint | Response is new but isolated to v2 | YES |
| D-04 | What should empty persisted data return? | 200 `EMPTY` envelope; missing row remains 404 | Current empty `[]` becomes 404 and conflicts with row-existence `has_h2h` | 404 for both empty/missing; 200 empty list | New v2 semantics differ; v1 unchanged | YES |
| D-05 | What makes `has_h2h` true? | At least one locally mapped, non-`NS`, displayable meeting | Existing flag checks row existence, not usable data | Preserve existence semantics; use only complete scored fixtures | May hide tab for empty/unmappable data; more precise | YES |
| D-06 | How are unknown/ambiguous fixture mappings handled? | Mark per meeting; null local ID; exclude from totals; return partial/unmappable state; log ambiguity | Existing Team lookup distinguishes no result and raises on multiple results | Fail entire request; drop silently; infer by names | Safe partial behavior; clients must handle diagnostic states | YES |
| D-07 | What scores count in summaries? | Only nonnegative integer goals in final statuses `FT/AET/PEN/AWD/WO`; missing/invalid excluded | Flutter currently coerces missing to zero; source data contains separate goals/score fields | Count any numeric score; fallback to other score periods; exclude all non-FT | Corrects totals; domain policy visible to users | YES |
| D-08 | How are duplicate fixtures treated? | Exclude all source rows with duplicated provider fixture ID | Raw result currently not deduplicated; analytics rejects duplicate IDs | Keep first; keep all; fail whole response | May reduce displayed list for duplicate source data | YES |
| D-09 | Should raw JSON or analytics/queue schema change? | No; keep raw persistence, projection, queue, and transaction boundaries | Raw storage and analytics are distinct; existing mapping exists; no schema gap found | Persist normalized DTO; add local-ID columns/table | None under recommendation; avoids migrations | NO |
| D-10 | How does v2 use Redis? | Treat existing key strictly as a raw-list source; normalize after retrieval; do not cache v2 envelope | Existing key is match-specific raw payload; GET is read-only and does not populate cache | Reuse key for envelope; add v2 normalized cache | No cache format collision or new invalidation requirement | YES |
| D-11 | Should Flutter own summary correction? | No; backend owns mapping and totals; Flutter renders DTO | Flutter has no canonical provider mapping and current comparison is incorrect | Add provider mapping call/client-side resolver | Central backend contract; requires client adoption | NO |
| D-12 | Is legacy v1 removable? | No decision in Phase 2; retain until consumers are inventoried and migration approved | Only in-repo Flutter consumer found; external consumers unknown | Remove immediately; set sunset date now | Unknown potential break if removed | YES |
| D-13 | Should historical provider team IDs be exposed in v2? | No; only local IDs and display names are public | Provider identifiers cause the current namespace mix-up and are not needed by UI | Expose explicitly named provider IDs for diagnostics | Reduced external detail; no current UI requirement found | YES |
| D-14 | How to identify a meeting in Flutter? | No new public provider fixture ID; use list position/available normalized fields unless product requires stable identity | Current widget renders list without relying on fixture IDs | Add opaque generated ID; expose provider fixture ID | Stable list keys may be desired but not evidenced as required | YES if stable identifier is required |

Repository evidence cannot settle the product policy for score-eligible terminal statuses, partial/unmappable UI wording, v1 sunset, or whether the mobile product requires stable meeting IDs. Those remain explicit approval questions rather than assumed facts.

## 12. Static Verification vs Runtime Verification

### Verified from source

The Phase 1 mapping issue, identity fields, TeamRepository uniqueness behavior, raw storage/upsert, provider request identities, v1 GET read path, existing cache key, sync boundaries, Flutter parser and affected UI fields were checked against current source. No contradiction with the audit was found.

### Existing runtime evidence (from Phase 1)

The Phase 1 report recorded local Docker PostgreSQL at Alembic revision `20261020_h2h_sync_work`; existing match `1285` mapped local `97/98` to provider `5/774`; the raw pair row had five fixtures, analytics had five snapshots, and the queue had one SUCCESS row. Read-only GETs returned five records for the populated match and 404 for a match without a stored H2H row. This Phase 2 design task did not repeat any requests or database/cache inspection.

### Not verified in this design phase

No provider request, sync, scheduler, queue claim, manual sync, database/cache mutation, Flutter runtime parse, isolated test execution, or deployment was performed. The response example and matrix outcomes are design specifications, not runtime observations.

## 13. Frozen Architecture and Scope Guard

The frozen architecture remains:

```text
Provider (transport)
  -> Read Service (read-side normalization/domain summaries)
  -> SyncService (write orchestration and flush)
  -> Repository (SQL only)
  -> Database

API/Scheduler owns commit/rollback.
Cache invalidation follows successful commit.
H2H GET remains read-only.
```

No Match, Team, League, Standing, or Odds data model/identity system is redesigned. Existing Team master identity is reused. No new migration, table, column, provider call, sync trigger, queue state, or production behavior is proposed.

## 14. Smallest Future Implementation Scope

### Backend

1. Add a v2 H2H read route and response schema.
2. Add Service normalization that resolves historical provider team IDs using TeamRepository, preserves orientation, marks unmappable records, and calculates summary totals.
3. Share that normalized availability logic with match-detail `has_h2h`.
4. Preserve v1 GET behavior, raw JSONB storage, analytics projection, queue, scheduler, manual sync, commit/rollback ownership, and cache invalidation.

### Flutter

1. Add the v2 local-ID URL and transport call.
2. Parse the normalized envelope and use server summary totals.
3. Remove v2 provider-ID/local-ID comparisons.
4. Render partial/incomplete, empty, unavailable, and error states while retaining the H2H screen's current layout and interactions.

### Tests and documentation

Add focused backend Service/API tests and Flutter parser/widget tests covering the matrix in Section 9. Keep existing sync/queue tests as regression coverage. Treat this freeze as the contract reference after approval.

### Explicit exclusions

No changes to provider transport, current H2H SyncService, result/analytics/queue schemas, migrations, Team/Match identity schemas, scheduler policies, odds/events/lineups/standings modules, environment configuration, or production/local DB/cache/queue data.

## 15. Phase 2 Acceptance Criteria

- Local and provider identities have distinct, documented fields and owners.
- The Team master is the only source used to map historical provider IDs to local IDs.
- The v2 route, request validation, exact response types, statuses, and compatibility behavior are approved.
- Summary rules are deterministic for orientation, valid completion, missing scores, unknown IDs, and duplicates.
- `has_h2h`, empty data, missing data, partial mapping, cache failure, and database failure behavior agree.
- V1 remains unchanged until external consumers and rollout are addressed.
- Raw storage, analytics, queue, transaction ownership, and post-commit invalidation remain frozen.
- The 23-case test matrix is accepted for isolated implementation tests.
- Phase 3 starts only after explicit user approval of this design freeze.

## Final Conclusion

1. Flutter sends local IDs; backend sync maps them to provider IDs only at the provider boundary.
2. Historical fixture provider IDs must be mapped through Team master before any summary calculation.
3. The recommended v2 response contains only local identity fields, backend summaries, normalized meetings, and explicit mapping/score status.
4. A persisted empty list returns v2 `EMPTY`/200; a missing row remains 404; Redis failure falls back to DB; database failure remains an error.
5. Raw H2H JSON, analytics projection, queue state, transaction owners, and sync behavior stay unchanged.
6. Flutter consumes backend totals, keeps the current UI shape/behavior, and never compares provider IDs with local IDs.
7. Main remaining approvals are the v2 contract, summary status policy, empty/unmappable semantics, v1 lifetime, Redis contract, and whether a stable meeting identifier is required.

**Implementation is not authorized by this design document. Wait for explicit approval before Phase 3.**
