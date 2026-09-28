# API-BY-API DEEP FUNCTIONAL AUDIT REPORT

Date: 2026-09-21

## 1. Executive Summary

This read-only audit inventoried the current FastAPI application, traced registered routes to dependencies/services/providers/repositories/database/cache, and probed safe GET/health endpoints in the running Docker API.

The application exposes match, league, team, player-v2, news, ads, auth, admin, upload, WebSocket, health, monitoring, and SQLAdmin surfaces. The main current functional finding is:

- `GET /api/news/tips` returns HTTP 422 in Docker because `GET /api/news/{news_id}` is registered before `GET /api/news/tips`; the dynamic route attempts to parse `tips` as an integer. This is a route-order defect.

Other observations:

- API health/readiness and core database/cache reads passed.
- Docker `/api/matches` returned persisted Match data.
- `/api/openapi.json` and `/metrics` returned 404 in Docker because those surfaces are conditionally disabled by runtime configuration.
- Several API modules access repositories/SQLAlchemy directly rather than consistently routing all read logic through a dedicated domain service.
- Protected sync/write endpoints were not called.
- No source, database, cache, Docker, or runtime configuration was modified.

Overall status: **PARTIALLY VERIFIED — ONE CURRENT ROUTE-ORDER ERROR FOUND**.

## 2. Complete API Inventory

The route table was inspected from the running application code. Registered surfaces are:

### Platform, monitoring, and realtime

| Method | Path | Function/surface |
|---|---|---|
| GET | `/` | `root` |
| GET | `/health` | `health_check` |
| GET | `/health/live` | `health_live` |
| GET | `/health/ready` | `health_ready` |
| GET | `/metrics` | conditional `metrics_router` |
| WEBSOCKET | `/ws/live` | `websocket_live_score` |
| WEBSOCKET | `/admin` | SQLAdmin panel |
| GET | `/api/openapi.json` | conditional FastAPI OpenAPI |
| GET | `/docs` | conditional Swagger UI |
| GET | `/docs/oauth2-redirect` | conditional Swagger OAuth redirect |

### Matches, fixtures, events, lineups, statistics, odds, H2H

| Method | Path | Function |
|---|---|---|
| GET | `/api/matches/live_all` | `get_all_live_matches` |
| GET | `/api/matches/` | `get_all_matches` |
| GET | `/api/matches/{match_id}` | `get_match_by_id` |
| GET | `/api/matches/date/{date_val}` | `get_matches_by_date` |
| GET | `/api/matches/{match_id}/events` | `get_match_events` |
| GET | `/api/matches/{match_id}/lineup` | `get_match_lineup` |
| GET | `/api/matches/h2h/{match_id}/{team1_id}/{team2_id}` | `get_match_h2h_symmetric` |
| GET | `/api/matches/{match_id}/statistics` | `get_match_statistics` |
| GET | `/api/matches/{match_id}/odds` | `get_match_odds` |
| POST | `/api/matches/{match_id}/heartbeat` | `heartbeat_match` |
| POST | `/api/matches/sync/h2h/{team1_id}/{team2_id}` | `refresh_h2h_route` |
| POST | `/api/matches/sync/{match_id}/lineup` | `sync_match_lineup_route` |
| POST | `/api/matches/sync/{match_id}/events` | `sync_match_events` |
| POST | `/api/matches/sync/{match_id}/statistics` | `sync_match_statistics_route` |
| POST | `/api/matches/sync/season` | `sync_full_season` |
| POST | `/api/matches/sync/{date_val}` | `sync_daily_matches` |

### Authentication

| Method | Path | Function |
|---|---|---|
| POST | `/api/auth/register` | `register_user` |
| POST | `/api/auth/login` | `login` |
| POST | `/api/auth/refresh` | `refresh_token` |
| POST | `/api/auth/google` | `google_login` |

### Home, leagues, and standings

| Method | Path | Function |
|---|---|---|
| GET | `/api/home` | `get_home` |
| GET | `/api/leagues/{league_id}/topscorers/{season}` | `get_league_top_scorers` |
| GET | `/api/leagues/grouped` | `get_grouped_leagues` |
| GET | `/api/leagues/{league_id}` | `get_league_details` |
| GET | `/api/leagues/{league_id}/standing/{season}` | `get_league_standings` |
| POST | `/api/leagues/sync` | `sync_all_leagues` |
| POST | `/api/leagues/sync/standings/{league_id}` | `sync_league_standings` |

### Admin League and authorization

| Method | Path | Function |
|---|---|---|
| POST | `/api/admin/leagues` | `register_league` |
| GET | `/api/admin/allowed-leagues` | `get_allowed_leagues` |
| POST | `/api/admin/allowed-leagues` | `create_allowed_league` |
| DELETE | `/api/admin/allowed-leagues/{league_id}` | `delete_allowed_league` |
| PATCH | `/api/admin/leagues/{league_id}` | `patch_admin_league` |

### Teams

| Method | Path | Function |
|---|---|---|
| GET | `/api/teams/{team_id}/fixtures` | `get_team_fixtures` |
| GET | `/api/teams/{team_id}/squad` | `get_team_squad` |
| GET | `/api/teams/{team_id}/matches` | `get_team_matches` |
| GET | `/api/teams/{team_id}/finished-matches` | `get_team_finished_matches` |
| GET | `/api/teams/{team_id}/statistics/{league_id}/{season}` | `get_team_statistics` |
| GET | `/api/teams/{team_id}/standings` | `get_team_standings` |
| GET | `/api/teams/{team_id}` | `get_team_details` |

### Ads, news, uploads

| Method | Path | Function |
|---|---|---|
| GET | `/api/ads/` | `get_active_ads` |
| GET | `/api/ads/config` | `get_ad_config` |
| PUT | `/api/ads/config` | `update_ad_config` |
| GET | `/api/news` | `get_news` |
| GET | `/api/news/latest` | `get_latest_news` |
| GET | `/api/news/transfers` | `get_transfer_news` |
| GET | `/api/news/{news_id}` | `get_news_detail` |
| GET | `/api/news/tips` | `get_betting_tips` |
| POST | `/api/uploads/news` | `upload_news_image_endpoint` |

### V2 player/read surfaces

| Method | Path | Function |
|---|---|---|
| GET | `/api/v2/players/provider/{provider}/{provider_player_id}` | `get_player_by_provider_identity` |
| GET | `/api/v2/players/{player_id}` | `get_player_by_local_id` |
| GET | `/api/v2/teams/{team_id}/squad` | `get_team_squad_v2` |
| GET | `/api/v2/leagues/{league_id}/topscorers/{season}` | `get_top_scorers_v2` |
| GET | `/api/v2/matches/{match_id}/lineup` | `get_match_lineup_v2` |
| GET | `/api/v2/matches/{match_id}/events` | `get_match_events_v2` |

## 3. API Dependency Map

```text
FastAPI route
  -> FastAPI dependency: get_db / current_active_user / current_active_admin
  -> API function
  -> FootballAPIService facade or direct CRUD/repository/service
  -> provider where required
  -> repository/CRUD and SQLAlchemy session
  -> PostgreSQL
  -> Redis cache where configured
  -> response model or dict/list
```

Shared construction is centralized in `app/services/football.py` through `FootballAPIService`, which owns `TeamService`, `LeagueService`, `FixtureSyncService`, Event/Statistics/H2H/Odds/Lineup services, and their providers. API modules also directly instantiate repositories and issue direct SQLAlchemy reads in several places.

## 4. Endpoint-by-Endpoint Audit

### Health/root/monitoring

- `GET /`, `GET /health`, and `GET /health/live` are synchronous constant responses. No DB, provider, or cache access.
- `GET /health/ready` calls `refresh_dependency_health()`, checking PostgreSQL and Redis, and returns 200 when both are ready or 503 with `{"status":"unhealthy"}` otherwise.
- `GET /metrics` is conditionally registered. Docker returned 404 because API metrics are disabled; Worker metrics are available on port 8001.
- `/api/openapi.json`, `/docs`, and the OAuth redirect are conditional on `ENABLE_API_DOCS`; Docker returned 404 for OpenAPI.
- SQLAdmin `/admin` is mounted by `setup_admin`; it is a separate WebSocket/SQLAdmin surface with session middleware and admin authentication.
- `WS /ws/live` connects through `socket_service.manager`, optionally scopes by `match_id`, replies `pong` to `ping`, and disconnects on WebSocket/error.

Classification: runtime verified for health/root; code-only for conditional/SQLAdmin/WebSocket.

### Match read APIs

`GET /api/matches/` calls `AllowedLeagueRepository.get_allowed_ids()` then `MatchRepository.get_all_matches()` with optional `status`, `league_id`, `skip`, and `limit`. It returns `MatchResponse` objects. Empty AllowedLeague produces an empty result. No explicit cache.

`GET /api/matches/live_all` reads `fover:live_matches` first. On cache hit it filters cached objects by current AllowedLeague IDs. On miss it queries `MatchRepository.get_live_matches()`, serializes `MatchResponse`, and writes the cache with `REDIS_TTL_LIVE_MATCHES`. Cache failures are handled by cache helpers and the DB path remains available.

`GET /api/matches/{match_id}` validates the match against AllowedLeague through `_assert_match_allowed`, then reads `MatchRepository.get_by_id()` and returns 404 when absent/not allowed. Match details compute availability flags by querying events/lineups/odds/H2H and invoking service fallback reads.

`GET /api/matches/date/{date_val}` validates a date path, loads AllowedLeague IDs, queries `MatchRepository.get_matches_by_date()` using Myanmar-day UTC boundaries and ordering by League visibility/order/country/name/time, then returns `MatchDateResponse` objects.

`GET /api/matches/{match_id}/events` checks match authorization, then calls `football_service.get_cached_match_events()`. The EventService can read DB/cache/provider according to status. Errors are translated to endpoint-specific not-found/error responses in the implementation.

`GET /api/matches/{match_id}/lineup` checks authorization and calls LineupService through the FootballAPIService facade. It uses lineup cache/DB/provider fallback according to the service contract.

`GET /api/matches/h2h/{match_id}/{team1_id}/{team2_id}` checks the match and calls H2H service/cache/provider path. Team IDs are path integers; invalid/missing match authorization is rejected.

`GET /api/matches/{match_id}/statistics` checks authorization and calls normalized StatisticsService. Missing data becomes 404.

`GET /api/matches/{match_id}/odds` checks authorization and calls cached OddsService. A returned `error` becomes 404; provider/DB behavior is owned by OddsService/OddsSyncService.

All these reads use `AsyncSession` from `get_db`; repository queries are read-only. Cache keys are match/live/events/lineup/statistics/odds/H2H keys as constructed by `make_cache_key()`.

### Match write/sync APIs

`POST /api/matches/{match_id}/heartbeat` is not admin-only. It checks match authorization, writes `fover:active_match:{match_id}` with the configured active TTL, and returns success plus TTL.

`POST /api/matches/sync/h2h/{team1_id}/{team2_id}` is admin-only. It rejects identical teams with 422, uses `run_with_resource_lock()` keyed by the sorted pair, calls H2H service, rolls back/provider-fails as 502, commits on success, invalidates the H2H cache, and returns success/analytics. Lock contention returns 409.

`POST /api/matches/sync/{match_id}/lineup`, `/events`, and `/statistics` are admin-only. Each uses a resource lock, calls its service, rolls back unsuccessful results, commits success, invalidates the relevant cache, and returns the service dict. Lock contention returns 409. Unexpected exceptions roll back and propagate to generic FastAPI error handling.

`POST /api/matches/sync/season` and `POST /api/matches/sync/{date_val}` are admin-only fixture sync routes. They use a global fixture resource lock, call FixtureSyncService through FootballAPIService, commit successful results, apply active-match updates, invalidate `fover:live_matches`, and finalize pending lineups. Unsuccessful results return the service dict after rollback, which can produce HTTP 200 with `success=false`; lock contention returns 409.

### Auth APIs

`POST /api/auth/register` accepts `UserCreate`, calls AuthService registration, and returns `UserRead`. It is public in the router. The service owns hashing/validation and DB transaction behavior.

`POST /api/auth/login` uses OAuth2 form credentials, AuthService authentication, and returns access/refresh `Token`.

`POST /api/auth/refresh` accepts a refresh token, decodes it through TokenService, loads the User, and returns a token pair. Invalid/expired/inactive users produce 401.

`POST /api/auth/google` verifies the external Google token, loads/creates the user through AuthService, and returns a Google auth response. Provider verification errors become 401.

Protected APIs use `OAuth2PasswordBearer`, TokenService access decoding, User lookup, active-user validation, and for admin routes `role == "admin"`. Invalid credentials produce 401; non-admin users produce 403.

### Home, League, and admin League APIs

`GET /api/home` builds the home aggregation through HomeService/League/Match-related repositories and cache paths. It is a read endpoint and returns an aggregate dict/list shape defined by its implementation.

`GET /api/leagues/grouped` loads AllowedLeague IDs, queries LeagueRepository, builds groups through `LeagueGroupingService`, and caches the grouped result under `fover:leagues_grouped` with the configured League/Team TTL.

`GET /api/leagues/{league_id}` reads cache first; on miss it loads League by ID and may fetch provider details through LeagueService, commit an upsert, invalidate League/grouped caches, and serialize `LeagueSchema`. This is a GET with possible provider/database write side effects in the current implementation.

`GET /api/leagues/{league_id}/topscorers/{season}` reads cache/provider through LeagueService and returns `TopScorersResponse`.

`GET /api/leagues/{league_id}/standing/{season}` requires the League ID in AllowedLeague, then uses StandingService/cache/provider/DB path; absent data returns 404.

`POST /api/leagues/sync` is admin-only and uses the League resource lock, LeagueSyncService, provider `/leagues`, LeagueRepository, TeamProvider through the corrected LeagueService wiring, LeagueSeasonSyncService, TeamSyncService, and one caller-owned commit/rollback.

`POST /api/leagues/sync/standings/{league_id}` is admin-only, resource-locked by League/season, calls StandingService, commits success, rolls back failure, and returns 409 on contention.

`POST /api/admin/leagues` is admin-only League registration. It validates provider identity, calls LeagueService registration/provider details, commits new League rows, and invalidates League caches. Registration does not automatically authorize the League.

`GET/POST/DELETE /api/admin/allowed-leagues` are admin-only allow-list reads/writes. POST validates local League provider identity and Country relationship, creates AllowedLeague, commits, and invalidates grouped-League cache. DELETE removes the row and does not delete historical data.

`PATCH /api/admin/leagues/{league_id}` is admin-only, directly loads League, updates `display_order`/`is_featured`, commits, refreshes, and invalidates League/grouped caches.

### Team APIs

`GET /api/teams/{team_id}` reads `fover:team:{id}` first; on miss it directly queries Team and caches `TeamSchema`. It does not call the provider when the Team is absent; missing Team is 404.

`GET /api/teams/{team_id}/fixtures` calls TeamService, which reads cached/database recent/upcoming Match rows and returns `TeamFixturesResponse`; errors become 404.

`GET /api/teams/{team_id}/matches` and `/finished-matches` directly validate Team with SQLAlchemy, then use MatchRepository via TeamService. Missing Team or result is 404.

`GET /api/teams/{team_id}/squad` reads Team/provider squad through TeamService/cache/provider and returns `TeamSquadResponse`; missing/error becomes 404.

`GET /api/teams/{team_id}/statistics/{league_id}/{season}` calls TeamService/TeamProvider `/teams/statistics`, maps provider data, caches it, and returns `TeamStatisticsResponse`; errors become 404.

`GET /api/teams/{team_id}/standings` uses Team context and StandingService; empty result becomes 404.

### Ads APIs

`GET /api/ads/` calls Ads CRUD and returns `AdsResponse` of active ads.

`GET /api/ads/config` reads AdConfig through CRUD/repository and returns nullable `AdConfigResponse`.

`PUT /api/ads/config` is not an admin-dependency route in the shown API module; it calls the config update path and commits according to the implementation. This is a protected-surface concern requiring explicit runtime/auth review.

### News APIs

`GET /api/news`, `/latest`, `/transfers`, and `/tips` call shared `get_news_by_tab_logic()`, which reads `fover:news:{tab}:{limit}:{offset}`, queries `app.crud.news.get_news_by_category()`, builds `NewsResponse`, and caches with `REDIS_TTL_NEWS`.

**Current defect:** `/api/news/tips` returned HTTP 422 in Docker. Because `/api/news/{news_id}` is registered before `/api/news/tips`, the dynamic route captures `tips` and integer validation fails before the intended tips handler. This is a route-order/registration defect.

`GET /api/news/{news_id}` reads detail cache, queries CRUD, returns a 404 JSON body when absent, and caches a `{success:true,data:...}` payload when present. The declared response model and not-found envelope should be reviewed for consistency.

### Upload API

`POST /api/uploads/news` is admin-protected and accepts multipart `UploadFile`. It calls upload service, maps `ValueError` to 400 and `OSError` to 500, and returns `NewsUploadResponse` with a public URL.

### V2 player/read APIs

`GET /api/v2/players/provider/{provider}/{provider_player_id}` and `/api/v2/players/{player_id}` query PlayerRepository and return V2 response models, with 404 for missing identities.

`GET /api/v2/teams/{team_id}/squad` uses PlayerRepository/team squad data and returns `SquadResponseV2`.

`GET /api/v2/leagues/{league_id}/topscorers/{season}` uses the V2 player/top-scorer service path and provider/cache/repository mapping.

`GET /api/v2/matches/{match_id}/lineup` and `/events` read canonical Match-related DB data and return V2 schemas. The current route table confirms registration; detailed runtime behavior is code-only unless a valid ID/read was probed.

## 5. Request/Response Flow

FastAPI performs path/query/body validation before endpoint execution. Pydantic schemas define most response shapes: MatchResponse, MatchDateResponse, Team/TeamFixtures/TeamSquad/TeamStatistics, League schemas, standings, news, ads, auth tokens, uploads, and V2 player schemas.

Common error mappings:

- Pydantic invalid path/query/body: 422.
- Missing resources: usually 404.
- Auth failure: 401; insufficient role: 403.
- Lock contention: 409.
- Provider refresh failure: usually 502 on explicitly mapped sync routes.
- Unexpected exceptions: generally propagate to FastAPI 500.
- Several sync routes return service `{success:false}` dictionaries after rollback with HTTP 200, which can expose business failure as transport success.

## 6. Database Access Map

| Domain | Tables/repositories |
|---|---|
| Matches | `matches`, MatchRepository, event/lineup/statistics/odds/h2h tables |
| Leagues | `leagues`, LeagueRepository, AllowedLeagueRepository |
| Seasons | `league_seasons`, LeagueSeasonRepository |
| Teams | `teams`, TeamRepository |
| Players | `players`, PlayerRepository, memberships/events/analytics |
| News | `news`, `app.crud.news` |
| Ads | ads/ad_configs CRUD/repositories |
| Auth | users/User repository/service |
| Lineups | match_lineups/finalization/analytics tables |

API modules sometimes query SQLAlchemy directly (`teams.py`, admin League patch, parts of matches) rather than exclusively using a service abstraction. This is a factual architecture inconsistency, not changed in this audit.

## 7. Cache Map

Main observed/cache-defined keys:

- `fover:live_matches` — aggregate live Match cache, short TTL, invalidated after successful fixture/sync commit.
- `fover:active_match:{id}` — Redis TTL active state.
- `fover:team:{id}`, team fixtures/squad/statistics keys.
- `fover:league:{id}`, `fover:leagues_grouped`.
- `fover:news:{tab}:{limit}:{offset}`, `fover:news:detail:{id}`.
- Match event/statistics/odds/H2H/lineup keys.

Cache helpers catch Redis exceptions, increment metrics, and usually fall back to DB/provider. This avoids many hard failures but allows stale/missing cache behavior. Successful writes generally commit before invalidation; some read APIs cache provider/DB results directly.

## 8. Provider Integration Map

The central FootballAPIClient supplies configured API-Football authentication, timeout/retry behavior, response parsing, and provider metrics. Provider classes include FixtureProvider, LeagueProvider, TeamProvider, PlayerProvider, StandingProvider, EventProvider, StatisticsProvider, OddsProvider, H2HProvider, LineupProvider, VenueProvider, RefereeProvider, and CoachProvider.

Representative endpoints:

- `/fixtures`, `/fixtures?live=all`, `/fixtures?date=...`, `/fixtures?ids=...`.
- `/leagues`, `/leagues?id=...`.
- `/teams`, `/teams?id=...`, `/teams?league=...&season=...`, `/teams/statistics`, `/players/squads`.
- `/players/topscorers`, standings/statistics/events/odds/H2H/lineup endpoints.

Provider client retries failed requests up to the configured retry count, maps malformed/request errors to response metadata/None, and records provider metrics. API routes generally reach providers through services; some League detail/team/provider fallback paths are entered by service facades, not directly by routers.

## 9. Authentication/Authorization Map

- Public: health/root, most reads, auth registration/login/refresh/google, public Match/League/Team/News/Ads reads.
- Current active user/admin dependencies: admin League routes, League/fixture/standing sync routes, Match H2H/lineup/events/statistics sync, uploads.
- Token path: OAuth2 bearer -> TokenService decode access token -> User lookup -> active check -> admin role check.
- Admin failures: 401 for missing/invalid token; 403 for inactive/non-admin users.
- Docker runtime admin mutation routes were not called.

## 10. Error Handling Matrix

| Area | Current behavior |
|---|---|
| Validation | FastAPI/Pydantic 422; explicit positive integer/path validation |
| Missing Match/Team/League/News | Usually 404 |
| Provider refresh | Service result/error, selected routes map to 502 |
| DB exception | Usually rollback then propagate; some read helpers return empty/error |
| Cache exception | Usually logged/metric-counted and fallback continues |
| Lock contention | 409 on API sync routes; scheduler logs skip |
| Business sync failure | Several routes return HTTP 200 with `success=false` result |
| News tips route | Current HTTP 422 due dynamic route precedence |

## 11. Transaction/Concurrency Matrix

- API sync routes own the outer transaction and call `commit()`/`rollback()`.
- Services/repositories generally flush/upsert without owning the global transaction.
- Scheduler jobs own commit/rollback around Fixture/Live/standings/odds workflows.
- `run_with_resource_lock()` protects fixture, live, lineup, events, statistics, H2H, standings, and League operations.
- Match uniqueness is protected by provider fixture unique constraint.
- Team uniqueness is protected by `(provider, provider_id)`.
- Advisory/session lock warnings remain visible in PostgreSQL logs; no current API failure was caused during this audit.
- Commit ambiguity handling is explicitly developed in the odds path; other API writes generally propagate commit errors after rollback.

## 12. Scheduler/API Relationship

API sync routes and scheduler jobs share the same FootballAPIService, SyncService, Provider, Repository, transaction, and cache paths for fixture, League, standings, events, statistics, lineup, odds, and H2H operations. The scheduler invokes internal service methods directly; it does not call HTTP routes. API routes add auth, resource locks, response mapping, and caller-owned commits.

Live Sync is scheduler-owned through `LiveUpdateScheduler._sync_live_matches_job()`; there is no public Live Sync HTTP route in the route inventory.

## 13. Data Integrity Audit

Current Docker evidence:

- Matches: 1,270.
- Current live-status Matches: 0.
- Duplicate provider fixture identities: 0.
- Teams: 70.
- Duplicate Team provider identities: 0.
- Persisted Match home/away Team IDs complete in the final checks.
- Relevant League/Season/AllowedLeague chains populated.
- Provider/local IDs are kept separately in models and repositories.

Potential data-integrity risks found in code:

- Empty AllowedLeague returns empty read/sync scopes by design.
- Some API response envelopes differ between declared response models and manual JSON error payloads.
- News route precedence causes `/tips` to be parsed as a numeric `news_id`.
- Several GET endpoints can populate/update caches or provider-backed records, so GET is not universally side-effect-free.

## 14. Architecture Compliance Audit

Compliant paths:

- Fixture/Live Sync: Provider -> FixtureSyncService -> Repository -> DB -> post-commit cache.
- League/Team: Provider -> League/Team SyncServices -> Repositories -> DB.
- Team identity is owned by TeamSyncService/TeamRepository.
- Scheduler is separate from API process.

Observed deviations/legacy paths:

- API modules directly issue SQLAlchemy repository/model queries (`teams.py`, admin League patch, Match reads) rather than always delegating to a domain service.
- `app.crud.news` is called directly from the News API rather than a NewsService.
- Some read endpoints perform provider fallback or DB/cache writes directly from route-adjacent code.
- SQLAdmin is a separate admin/data access surface.

No architecture or source change was made.

## 15. Runtime Verification

Safe runtime probes:

| Endpoint | Result |
|---|---|
| `/` | 200, running |
| `/health` | 200, alive |
| `/health/live` | 200, alive |
| `/health/ready` | 200, PostgreSQL/Redis ready |
| `/api/matches` | 200, persisted Match data |
| `/api/matches/live_all` | 200, empty live list |
| `/api/leagues/grouped` | 200, grouped allowed leagues |
| `/api/home` | 200, aggregate payload |
| `/api/news` | 200, empty news response |
| `/api/news/latest` | 200, empty news response |
| `/api/news/transfers` | 200, empty news response |
| `/api/news/tips` | **422 current route-order defect** |
| `/api/ads/` | 200, empty ads response |
| `/api/leagues/166` | 200, League payload |
| `/api/teams/1` | 200, Team payload |
| `/api/v2/players/1` | 404, no such player |
| `/api/openapi.json` | 404, docs disabled in Docker |
| `/metrics` | 404, API metrics disabled; Worker metrics separate |

These probes were read-only. No mutation/sync endpoint was called.

## 16. Critical Findings

### Finding 1 — News tips route shadowed

1. Endpoint: `GET /api/news/tips`.
2. File: `app/api/news.py`.
3. Functions: `get_news_detail(news_id: int)` is registered before `get_betting_tips()`.
4. Behavior: request `/api/news/tips` is captured by `/{news_id}` and fails integer validation with 422.
5. Evidence: Docker probe returned HTTP 422.
6. Root cause: route declaration order/dynamic path precedence.
7. Impact: tips feed is inaccessible through the intended endpoint.
8. Minimal fix: register `/tips` before `/{news_id}` or constrain/reorder the dynamic route.
9. Required verification: GET `/api/news/tips` should return `NewsResponse` 200 and `/api/news/{numeric_id}` should still resolve correctly.

### Finding 2 — Conditional docs/metrics unavailable in Docker

1. Endpoints: `/api/openapi.json`, `/metrics`.
2. Current behavior: both return 404 in Docker.
3. Evidence: safe runtime probes; `main.py` conditionally includes them.
4. Impact: external API discovery and API Prometheus scraping are unavailable in this runtime configuration.
5. Minimal fix: configuration/operations decision, not applied in this audit.

### Finding 3 — Business failures can return HTTP 200

Several sync routes return service dictionaries after rollback when `success=false`, rather than raising an HTTP error. This can make transport-level monitoring report success while the business operation failed.

## 17. Legacy/Dead Paths

- Conditional API docs/metrics routes are absent at runtime because configuration disables them.
- SQLAdmin `/admin` is separate from the JSON API and was not exercised.
- Legacy compatibility bridges exist in LeagueService/TeamService to preserve subclass interception.
- Multiple provider-backed read fallbacks and CRUD-direct paths coexist with service-layer paths.
- News route ordering demonstrates an active legacy/ordering defect rather than an unused path.

## 18. Recommended Fixes

No fixes were implemented. Minimal recommended fixes:

1. Reorder `/api/news/tips` before `/api/news/{news_id}` or constrain the dynamic route.
2. Decide whether API docs/metrics should be enabled operationally; if yes, change configuration in a separate approved phase.
3. Standardize business-failure HTTP mapping for sync routes so failed results are not reported as HTTP 200.
4. Review direct SQL/CRUD access from routers and consolidate only where a concrete ownership defect is proven.

## 19. Verification Plan

After any approved fix:

1. Re-run safe GET probes and confirm `/api/news/tips` returns 200.
2. Confirm numeric News detail still returns the correct 404/200 behavior.
3. Confirm all health/readiness checks and Match/League/Team reads.
4. If docs/metrics configuration changes, verify OpenAPI and Prometheus endpoints.
5. Run focused API, auth, cache, repository, and route-order tests.
6. Verify sync error responses distinguish transport failure from business failure.
7. Recheck PostgreSQL/Redis/runtime logs for new errors.

## 20. Final Status

**PARTIALLY VERIFIED — CURRENT ROUTE-ORDER ERROR FOUND**

The complete route inventory and current source call paths are documented. Safe runtime reads and health endpoints were verified. One current API defect is directly proven: `/api/news/tips` returns 422 due route precedence. Protected mutation/sync endpoints were intentionally not called, so their runtime success is code-only.
