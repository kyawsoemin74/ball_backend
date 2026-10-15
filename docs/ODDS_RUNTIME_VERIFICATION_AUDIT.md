# Odds Runtime Verification Audit

**Observation window:** 2026-10-09 12:38-12:45 +06:30 (06:08-06:15 UTC)  
**Environment inspected:** Local Docker Compose runtime on this workstation; **not production or staging**.  
**Report status:** Runtime evidence is limited to the running local containers and their configured local PostgreSQL/Redis.

## 1. Executive Summary

This audit independently checked the five reported concerns against the running local Docker stack, its imported Python classes, existing container logs, current database aggregates, and one read-only local Odds GET. No provider, sync, or scheduler job was triggered; no data or schema was changed.

- **Issue A — Sync result accuracy:** No Odds refresh completed during the observed worker lifetime. Existing logs do not reproduce a false-success event. The misleading-success behavior remains a **SOURCE-ONLY RISK**.
- **Issue B — Analytics compatibility:** Current database values show different Odds and Analytics market representations, but the Analytics snapshot is for a different Match from the only current main Odds snapshot. There is no rejection/execution log or same-Match comparison proving the current projection rejected current Odds. **SOURCE-ONLY RISK**, not a confirmed runtime defect.
- **Issue C — Transaction recovery:** Runtime introspection in both local API and worker containers confirms the imported `OddsSyncService` lacks `is_commit_ambiguous` and `verify_commit_unknown`, although the local scheduler calls them in its ambiguous-commit branch. The branch did not execute in the observed window. **Confirmed local Docker implementation gap; recovery outcome and production behavior are INCONCLUSIVE.**
- **Issue D — Database integrity:** The current local database has 9 Odds rows, all linked to one finished Match. No duplicate identities, orphan rows, invalid/nonpositive values, blank selections, or future timestamps were found. All rows are older than 12 hours, but their Match is finished and outside scheduler eligibility. **NOT REPRODUCED** for the tested integrity conditions; provider completeness and production data remain unverified.
- **Issue E — Myanmar conversion:** Four persisted Myanmar labels were returned by the local GET response from the database-backed cache snapshot. This verifies field persistence/delivery for this snapshot, but not that the current conversion function generated them during the observed runtime. Flutter consumption is unavailable. **PARTIALLY VERIFIED.**

### Overall verdict

- **RUNTIME AUDIT:** PARTIAL.
- **CONFIRMED DEFECTS:** Local Docker worker/API `OddsSyncService` is missing the two methods referenced by scheduler ambiguous-commit recovery. No execution failure from that branch was observed.
- **SOURCE-ONLY RISKS:** Scheduler may count no-data outcomes as refreshed; current projection filters do not match current Odds normalizer representation.
- **DATABASE INTEGRITY:** PASS for the queried current local database checks; Odds availability against provider supply is not verified.
- **MYANMAR ODDS:** PARTIALLY VERIFIED.
- **CODE CHANGES:** NONE.

## 2. Environment and Safety Confirmation

### Environment and timing

- `docker ps` showed running containers named `fover_api`, `fover_worker`, `fover_postgres`, and `fover_redis`; API and worker images are local `fover_backend-*` images. API/worker were healthy when inspected.
- Container start times were approximately **2026-10-09 06:08:29 UTC** (12:38:29 +06:30). The database aggregate query ran at **2026-10-09 06:14:59 UTC** (12:44:59 +06:30); the local API GET completed during the same observation window.
- The database connection was to the local Docker PostgreSQL container and database `fover_db`. `current_setting('transaction_read_only')` returned `on`. The inspected database is not evidence of production or staging.
- The worker log showed scheduler registration/start at `2026-10-09T06:08:31.455Z`. The worker's six-hour Odds job was configured, but the observed container lifetime was only minutes.

### Actions and safety

- Database inspection used `SELECT` statements only with PostgreSQL `default_transaction_read_only=on`. No inserts, updates, deletes, DDL, migrations, flushes, or commits were performed.
- One `GET /api/matches/<local-id>/odds` request was made to the **local** API only. It was read-only and returned a cache hit; it did not call the Odds sync route or API-Football. Normal request/cache telemetry may record that GET.
- Read-only Python introspection imported the Odds sync class in the running API/worker containers without invoking its methods. No API client method, provider method, scheduler job, or database write was invoked.
- No service was restarted, stopped, or reconfigured. No credentials or secret environment values were printed.
- `app/filter_odds.py` and `app/E0.csv` were absent at the start of this phase; no attempt was made to restore or modify them.

## 3. Issue A — Sync Result Accuracy

### Runtime observations

- Worker logs in the available post-start window contained scheduler registration/start:
  - `2026-10-09T06:08:31.454Z`: APScheduler added `"Refresh Odds Snapshots"`.
  - `2026-10-09T06:08:31.455Z`: `SCHEDULER_STARTED` listed `refresh_odds`.
- No `ODDS_REFRESH_COMPLETE`, `ODDS_REFRESH_FAILED`, `ODDS_REFRESH_SYNCED`, Odds provider error, cache invalidation outcome, or commit outcome log was observed after startup.
- Before the audit's own local GET-only request, API logs showed no Odds sync/provider outcome logs. The request logs available during inspection were generic `http_request_completed` lines without endpoint/request extras, so they cannot establish other Odds GET activity. The audit GET itself returned a cache hit and generated normal request/cache telemetry.
- Current local DB state contained 9 rows, all `last_updated=2026-09-29 07:08:09 UTC`. All belong to one Match whose status is `FT` and match date is 2026-09-29. These records were not changed during this audit.
- No provider response was observed, so whether a request was successful, empty, malformed, or unavailable in the runtime is **NOT VERIFIED**. No `NO_DATA`, `UNCHANGED`, or `UPDATED` refresh result was observed in logs.

### Source-level comparison (not runtime reproduction)

- `OddsSyncService.refresh_odds` returns `{"odds": [], ..., "reason": "no_data"}` for an empty response, and returns an empty result with reason `1xbet_data_not_found` or `filtered_no_odds` when no usable rows are selected. These result dictionaries do not carry `"error"` or `status: SUCCESS`. Evidence: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 75-120.
- The scheduler treats only a result containing `"error"` as a provider failure; otherwise it calls the commit helper, invalidates cache after the helper reports a refresh, and increments `refreshed_matches`. Evidence: [app/services/scheduler.py](../app/services/scheduler.py), lines 867-923.
- The admin route uses a different condition: it requires `status == SUCCESS`, otherwise rolls back and returns 502. Evidence: [app/api/matches.py](../app/api/matches.py), lines 394-404.
- On those empty/no-usable branches, main Odds persistence replacement is not called; existing Odds therefore remain. Evidence: [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 75-120; [app/repositories/odds_repository.py](../app/repositories/odds_repository.py), lines 52-60.

**Expected:** No usable provider data should not be represented as a newly updated snapshot.  
**Observed:** No refresh outcome occurred in the observed local runtime; existing DB rows were old and attached to a finished match.  
**Verdict:** **SOURCE-ONLY RISK.** The false-success path is visible in the local code, but no false success, provider outcome, persistence, or commit was observed. Production behavior is not established.

## 4. Issue B — Analytics Projection Compatibility

### Runtime database values

Read-only aggregate results:

- Main Odds: 9 records on one Match; bookmaker `1xBet`; market names `Asian Handicap` (2), `Corners Over Under` (2), `Goals Over/Under` (2), and `Match Winner` (3).
- Analytics Odds: 77 records on one different Match; bookmaker `1xBet`; canonical market names `ASIAN_HANDICAP` (34), `GOALS_OVER_UNDER` (40), and `MATCH_WINNER` (3).
- Both sets were stale at observation time (main Odds timestamp September 29; Analytics observed/updated timestamp September 28).
- The sets did not overlap by `match_id`: 0 Analytics Match IDs had a corresponding Match ID in the main Odds table. No same-Match row comparison can establish that the currently stored main rows were rejected by projection.
- No projection rejection/execution log was observed.

### Comparison

| Field | Active Odds sync output contract (source) | Analytics accepted value (source) | Runtime evidence | Verdict |
|---|---|---|---|---|
| Bookmaker | Selects provider bookmaker ID `11`; persists provider `name` (fallback `"1xBet"`). | Lowercased bookmaker must equal `"bet365"`. | Current main Odds and Analytics rows both contain `1xBet`, but they belong to different Matches. No execution/rejection record. | SOURCE-ONLY RISK |
| 1X2 market | Provider bet ID `1`; persists provider name such as `Match Winner`. | `MATCH_WINNER`. | Main table has `Match Winner`; Analytics table has `MATCH_WINNER`; no shared Match snapshot. | SOURCE-ONLY RISK |
| Asian Handicap | Provider bet ID `4`; persists provider name such as `Asian Handicap`. | `ASIAN_HANDICAP`. | Both representations exist in separate tables, with no shared Match. | SOURCE-ONLY RISK |
| Goals Over/Under | Provider bet ID `5`; persists provider name such as `Goals Over/Under`. | `GOALS_OVER_UNDER`. | Both representations exist in separate tables, with no shared Match. | SOURCE-ONLY RISK |
| Corners Over/Under | Provider bet ID `45`; persists provider name such as `Corners Over Under`. | `CORNERS_OVER_UNDER`. | Main table has `Corners Over Under`; no corresponding Analytics market count was present. | SOURCE-ONLY RISK |

Relevant code: active selection/persistence fields in [app/services/odds_service.py](../app/services/odds_service.py), lines 162-216; projection filters and zero-input preserve path in [app/services/analytics_projection_service.py](../app/services/analytics_projection_service.py), lines 249-274.

**Expected:** Projection accepts and writes the current Odds snapshot or logs a rejected/incompatible snapshot.  
**Observed:** Current DB tables demonstrate different representations but do not share a Match, and no projection execution log was present.  
**Verdict:** **SOURCE-ONLY RISK**, not CONFIRMED. No projection was manually run.

## 5. Issue C — Transaction and Ambiguous-Commit Recovery

### Runtime introspection

The actual imported class was inspected inside both running local containers without calling its methods:

| Runtime | Imported module | `refresh_odds` | `is_commit_ambiguous` | `verify_commit_unknown` |
|---|---|---:|---:|---:|
| `fover_worker` | `/usr/src/app/app/services/odds_sync_service.py` | Present | Absent | Absent |
| `fover_api` | `/usr/src/app/app/services/odds_sync_service.py` | Present | Absent | Absent |

The SHA-256 of `odds_sync_service.py` in both containers matched the local source file. Worker `scheduler.py` and `analytics_projection_service.py` also matched the local source hashes. This establishes the implementation in these **local Docker images**, not production/staging.

### Runtime event evidence

- The worker startup log confirms the `refresh_odds` job was registered.
- No commit-failure, `COMMIT_UNKNOWN`, verification, `AttributeError`, or Odds refresh outcome log was found in the available worker/API logs.
- The ambiguous commit branch was not exercised. No commit failure was simulated.

### Assessment

The scheduler's [commit helper](../app/services/scheduler.py) calls `is_commit_ambiguous` after a commit exception, then may call `verify_commit_unknown`; neither method exists on the imported local runtime class. The scheduler also expects transaction context that the current `refresh_odds` result does not populate. If the ambiguous branch is entered in this local build, the first missing-method call is a runtime failure before verification can occur; the actual resulting transaction/data state was not reproduced.

**Verdict:** **CONFIRMED local Docker implementation gap; recovery failure NOT REPRODUCED.** Whether production has the same image/code and whether its branch can determine commit outcome are **INCONCLUSIVE**. No evidence of actual duplicate, omitted, or incorrectly reported Odds due to an ambiguous commit was found.

## 6. Issue D — Database Odds Integrity

### Database target and schema

The target was the local Docker PostgreSQL database `fover_db`; the database reported `transaction_read_only=on`.

Actual `odds` columns observed:

`id integer NOT NULL`, `fixture_id integer NOT NULL`, `bookmaker_name varchar NULL`, `market_name varchar NOT NULL`, `selection varchar NOT NULL`, `odd_value varchar NOT NULL`, `last_updated timestamptz NOT NULL`, `myanmar_odd varchar NULL`.

Actual constraints observed:

- Primary key: `odds_pkey (id)`.
- Unique: `(fixture_id, bookmaker_name, market_name, selection)`.
- Foreign key: `fixture_id -> matches(local_match_id)`.

This agrees with the inspected application model for these columns/constraints. Deployed database constraints were queried rather than assumed.

### Read-only results at 2026-10-09 06:14:59 UTC

The following are aggregate results, not fabricated or sample data:

| Check | Result |
|---|---:|
| Total Odds rows | 9 |
| Distinct local Matches with Odds | 1 |
| Duplicate business-key groups | 0 |
| Orphan Odds rows against `matches.local_match_id` | 0 |
| Values not matching a plain signed decimal representation | 0 |
| Numeric values <= 0 | 0 |
| Blank selections | 0 |
| Future timestamps more than 5 minutes ahead | 0 |
| Rows older than the scheduler's 12-hour freshness threshold | 9 |
| Match status for Odds rows | FT |
| Odds last-updated range | 2026-09-29 07:08:09 UTC (all rows) |
| Upcoming NS/TBD/PST Matches within 72h with no Odds | 16 |

Market completeness among the stored snapshot:

- 1X2: one group; complete Home/Draw/Away set (1 complete, 0 incomplete).
- Goals/Corners Over/Under: two line groups; both had Over and Under (2 complete, 0 incomplete).
- Asian Handicap: one parsed line group; it had Home and Away (1 complete, 0 incomplete).

All 9 rows are older than 12 hours, but they belong to a finished Match dated September 29; finished Matches are excluded from the current scheduled Odds refresh eligibility. The timestamps therefore do not alone establish a current refresh defect. The 16 upcoming Matches without Odds are not counted as defects because no evidence shows the provider supplied Odds for them.

**Canonical identity limitation:** The actual FK proves each present Odds `fixture_id` references a local Match primary key; it does not independently prove that the related Match is semantically the intended provider fixture. No orphan was found. Provider-supply identity could not be checked without provider calls or suitable retained logs.

**Verdict:** **NOT REPRODUCED** for duplicates, orphan rows, invalid/nonpositive values, blank selections, incomplete stored market groups, or future timestamps. **DATABASE INTEGRITY: PASS** for these queried local data checks. Provider completeness, production integrity, and any data not represented in this database remain unverified.

## 7. Issue E — Myanmar Odds Conversion

### Existing database and local GET evidence

- Main Odds contained 4 non-null Myanmar labels across 9 records: `+8` and `-8` on `Asian Handicap`, and `2=` twice on `Goals Over/Under`. Corners and Match Winner rows had no Myanmar label.
- The local endpoint `GET /api/matches/<local_match_id>/odds` returned **HTTP 200**, `source=database`, `cached=true`, 9 Odds items, and 4 non-null Myanmar labels (`-8`, `+8`, `2=`). The response did not contain a `match_id` field.
- Because the response was a Redis cache hit, the live GET verifies that the cached response carried Myanmar labels. It does not independently prove those cached labels were freshly read from PostgreSQL during that request. The database aggregate separately confirms four non-null stored values.
- No current-runtime conversion log or Odds sync occurred in the observation window. The provenance/time of the stored labels cannot be attributed to a current conversion execution.
- No Dart/Flutter source was present, so whether Flutter reads/displays `myanmar_odd` is **NOT VERIFIED**.

### Source path comparison

- Active provider-to-sync flow calls `OddsService._filter_main_lines`; that method selects bookmaker ID `11` and calls `_select_main_line_by_id`.
- `_select_main_line_by_id` invokes `_attach_myanmar_odd_labels` for eligible paired Asian Handicap ID 4 and Over/Under IDs 5/45. `_attach_myanmar_odd_labels` currently accepts exact market names `"Asian Handicap"` and `"Goals Over/Under"`; consequently the active corners name `"Corners Over Under"` is not converted.
- The helper calls `MyanmarOddsService.convert_to_myanmar_odds`, places the returned ladder label on the selected rows, and sync persistence copies `myanmar_odd` into main Odds rows.
- The read serializer includes `myanmar_odd` in each response item.

Evidence: [app/services/odds_service.py](../app/services/odds_service.py), lines 73-120, 123-216, and 236-254; [app/services/odds_sync_service.py](../app/services/odds_sync_service.py), lines 91-107; [myanmar_odds/services/myanmar_odds_service.py](../myanmar_odds/services/myanmar_odds_service.py), lines 56-126.

**Expected:** Eligible paired markets produce a Myanmar label which is persisted and included in the API response.  
**Observed:** Current local database has Myanmar labels and a local read response carries them, but generation during the current runtime and Flutter consumption were not observed.  
**Verdict:** **PARTIALLY VERIFIED** for stored-value/API delivery. Current conversion execution and Flutter UI behavior are **INCONCLUSIVE/NOT VERIFIED**.

## 8. Evidence and Reproduction Results

| Evidence source | Actual result | Reproduction status |
|---|---|---|
| Local worker logs | Scheduler started and Odds job registered; no refresh outcome, provider error, commit or recovery logs in the observed window. | No false success or recovery failure reproduced. |
| Local API logs | No Odds endpoint/provider outcome log observed. | No API sync outcome reproduced. |
| Runtime class introspection | API and worker imported `OddsSyncService` lack the two recovery methods; module hashes match local source. | Missing symbols directly verified in local Docker. |
| PostgreSQL (read-only) | 9 valid, linked Odds rows; 0 duplicate groups; 0 orphans; 0 nonnumeric/nonpositive/blank; all rows belong to one FT Match. | Listed integrity defects not reproduced in local DB. |
| Analytics table (read-only) | 77 stale rows for a different single Match; bookmaker 1xBet and canonical market names; no overlap with main Odds Match ID. | Actual rejection not observed; compatibility risk remains unproven at runtime. |
| Local Odds GET | HTTP 200, database-origin cache hit, 9 rows, 4 non-null Myanmar labels. | Stored label/API delivery observed for this local snapshot. |
| Flutter source | No Dart files found in this repository. | Frontend use/display not verified. |

No production or staging logs, image digests, database, provider dashboard, or Flutter repository were available for inspection. Container logs do not include an Odds request correlation ID because no Odds request/outcome was observed.

## 9. Confirmed Defects

1. **Local Docker ambiguous-commit recovery has missing methods.** Runtime introspection directly verified that the imported class lacks `is_commit_ambiguous` and `verify_commit_unknown`, while the local scheduler source calls them in its ambiguous-commit path. This is a confirmed implementation gap in the local Docker worker/API image. The recovery branch was not exercised, and this is not proof about production.

No other suspected defect was confirmed by runtime evidence in this audit.

## 10. Source-Only Risks

1. **Potential false-success reporting for no-data refreshes:** scheduler treats non-error no-data results as commit/refreshed outcomes even where no Odds replacement occurs; no such outcome was observed.
2. **Potential Analytics representation mismatch:** live normalizer and current projection use incompatible bookmaker/market names; existing tables use distinct snapshots for different Matches and no rejection log was found.
3. **Potential stale/partial snapshot behavior:** source retains old rows for empty/unusable responses and replaces all rows for a nonempty partial normalized result; neither behavior was exercised.
4. **Potential missed commit verification:** absent runtime helper methods prevent the intended local recovery path if reached; actual consequence for a real transaction is unobserved.

## 11. Inconclusive Items

- Production/staging deployment version and whether its OddsSyncService has the recovery methods.
- Whether any provider response was empty, malformed, unavailable, or supplied no supported Odds during the observed runtime.
- Whether current refresh result reporting has ever falsely indicated a successful update in production.
- Whether current Odds rows were rejected by the Analytics projection in an actual execution or whether historical Analytics data came from another/older normalization path.
- Whether the local GET cache payload was originally populated from the currently inspected database snapshot.
- Whether current conversion generated the four stored labels or whether a different/older path populated them.
- Whether Flutter consumes or displays Myanmar values.
- Whether the 16 upcoming local Matches without Odds were expected to have Odds from API-Football.

Minimum safe evidence needed: retained production/staging logs correlated to Odds job/request IDs; a read-only deployment image/source identity check; a read-only same-Match Odds/Analytics query; and Flutter client source. Reproducing commit recovery or sync-result accuracy would require controlled fault/provider data or a test database and is not authorized in this phase.

## 12. Recommended Next Steps

1. Review the confirmed **local Docker** recovery-method gap against the intended transaction contract; separately verify production image contents before drawing a production conclusion.
2. For Issue A, inspect retained job outcome logs and compare result state with row-count/timestamp deltas for the same local Match. Do not trigger a refresh as part of this verification.
3. For Issue B, use a read-only same-Match comparison with retained projection logs; current table contents belong to different Matches and are not sufficient to prove a rejected projection.
4. For database completeness, obtain read-only provider-availability history or existing provider response evidence for the 16 upcoming Matches; absence alone is not a defect.
5. For Myanmar, inspect Flutter model/screen code and use an already-existing redacted response/screenshot. Do not force a fresh sync.

## Final Verdict Table

| Issue | Runtime Verdict | Evidence | Production Impact | Next Action |
|---|---|---|---|---|
| Sync result accuracy | SOURCE-ONLY RISK; NOT REPRODUCED | No refresh outcome during short local worker window; source result handling can count non-error no-data as refreshed; current Odds rows were old and on FT Match. | No production false-success event proven. | Correlate existing production job logs with persisted row/timestamp deltas. |
| Analytics compatibility | SOURCE-ONLY RISK | Local Odds and Analytics values use different representations but refer to different Matches; no projection rejection log. | No current projection rejection proven. | Compare same-Match retained snapshots and projection logs read-only. |
| Transaction recovery | CONFIRMED local Docker symbol gap; recovery execution INCONCLUSIVE | API/worker imported classes lack referenced methods; matching module hash; no branch/error log. | Conditional local recovery failure; production not established. | Check deployed production module/image and retained commit-failure evidence. |
| Database integrity | NOT REPRODUCED; PASS for queried local checks | 9 rows; zero duplicates/orphans/invalid/nonpositive/blanks; stored market groups complete; all on FT Match. | No integrity defect found in this local database snapshot. | Repeat with read-only production access and provider-supply evidence if authorized. |
| Myanmar Odds conversion | PARTIALLY VERIFIED | 4 DB labels and local HTTP 200 cache response carrying 4 labels; no sync/conversion event observed; no Flutter source. | Persistence/API field delivery observed locally; frontend and conversion provenance unknown. | Inspect Flutter integration and existing conversion/job evidence. |

**Overall:** **RUNTIME AUDIT: PARTIAL.**  
**CONFIRMED DEFECTS:** Missing ambiguous-commit recovery methods in the running local Docker implementation only.  
**SOURCE-ONLY RISKS:** Potential no-data false-success reporting; potential Odds/Analytics contract mismatch.  
**DATABASE INTEGRITY:** PASS for queried local database checks; production and provider completeness unverified.  
**MYANMAR ODDS:** PARTIALLY VERIFIED.  
**CODE CHANGES:** NONE.
