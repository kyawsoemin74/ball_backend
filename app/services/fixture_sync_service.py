import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.league import League
from app.models.match import Match
from app.providers.fixture_provider import FixtureProvider
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.repositories.league_repository import LeagueRepository
from app.repositories.match_repository import MatchRepository
from app.schemas.match import MatchCreate
from app.services.active_match_service import active_match_service
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.standing_service import StandingService
from app.services.team_service import TeamService
from app.services.team_sync_service import TeamSyncService

logger = logging.getLogger(__name__)

FINISHED_STATUSES = {"FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO"}
LIVE_STATUSES = {"1H", "2H", "HT", "ET", "LIVE", "BT", "P"}
ACTIVE_MATCH_REGISTER_STATUSES = {"1H", "2H", "HT", "LIVE"}
ACTIVE_MATCH_TERMINAL_STATUSES = FINISHED_STATUSES | {"PST"}


class FixtureSyncService:
    def __init__(
        self,
        client: FootballAPIClient,
        team_service: TeamService,
        cache_service: CacheService | None = None,
        standing_service: StandingService | None = None,
        fixture_provider: FixtureProvider | None = None,
    ) -> None:
        self.client = client
        self.fixture_provider = fixture_provider or FixtureProvider(self.client)
        self.team_service = team_service
        self.cache_service = cache_service or CacheService()
        self.match_repository = MatchRepository()
        self.league_repository = LeagueRepository()
        self.allowed_league_repository = AllowedLeagueRepository()
        self.standing_service = standing_service or StandingService(self.client, self.team_service, self.cache_service)
        self.team_sync_service = TeamSyncService(self.cache_service)

    def _extract_id_from_logo(self, obj: dict) -> Optional[int]:
        url = obj.get("logo")
        if not url or not isinstance(url, str):
            return None
        match = re.search(r"/teams/(\d+)", url)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, TypeError):
                return None
        return None

    @staticmethod
    def _coerce_season(value: object) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _collect_standings_prewarm_candidates(self, matches: list[MatchCreate]) -> set[tuple[int, int]]:
        candidates: set[tuple[int, int]] = set()
        for match in matches:
            if match.season is None:
                continue
            try:
                candidates.add((int(match.league_id), int(match.season)))
            except (TypeError, ValueError):
                continue
        return candidates

    async def _sync_active_match_registration(self, match_id: int, status: str | None) -> None:
        try:
            normalized_status = str(status or "").upper()
            if normalized_status in ACTIVE_MATCH_REGISTER_STATUSES:
                await active_match_service.mark_match_active(match_id)
                return

            if normalized_status in ACTIVE_MATCH_TERMINAL_STATUSES:
                await active_match_service.remove_match_active(match_id)
                return

            await active_match_service.remove_match_active(match_id)
        except Exception as exc:
            logger.warning("ACTIVE_MATCH_SYNC_SIDE_EFFECT_FAILED match_id=%s status=%s error=%s", match_id, status, exc)

    async def _prewarm_missing_standings(self, db: AsyncSession, candidates: set[tuple[int, int]]) -> dict[str, int]:
        total_pairs = len(candidates)
        already_present_pairs = 0
        synced_pairs = 0
        failed_pairs = 0

        logger.info("Daily fixture standings prewarm starting: total_unique_pairs=%s", total_pairs)

        for league_id, season in sorted(candidates):
            try:
                logger.debug("PREWARM_CANDIDATE league_id=%s season=%s", league_id, season)
                existing_rows = await self.standing_service.standing_repository.get_for_league_season(db, league_id, season)
                if existing_rows:
                    already_present_pairs += 1
                    logger.debug(
                        "PREWARM_SKIPPED league_id=%s season=%s rows=%s",
                        league_id,
                        season,
                        len(existing_rows),
                    )
                    continue

                result = await self.standing_service.sync_standings(db, league_id, season)
                if result.get("success"):
                    await db.commit()
                    await self.cache_service.delete(make_cache_key("standings", league_id, season))
                    synced_pairs += 1
                    logger.debug(
                        "PREWARM_SYNCED league_id=%s season=%s updated=%s",
                        league_id,
                        season,
                        result.get("updated", 0),
                    )
                else:
                    await db.rollback()
                    failed_pairs += 1
                    logger.warning(
                        "PREWARM_FAILED league_id=%s season=%s reason=%s",
                        league_id,
                        season,
                        result.get("message", "unknown error"),
                    )
            except Exception as exc:
                await db.rollback()
                failed_pairs += 1
                logger.warning(
                    "PREWARM_FAILED league_id=%s season=%s error=%s",
                    league_id,
                    season,
                    exc,
                )

        logger.info(
            "Daily fixture standings prewarm completed: total_unique_pairs=%s already_present_pairs=%s synced_pairs=%s failed_pairs=%s",
            total_pairs,
            already_present_pairs,
            synced_pairs,
            failed_pairs,
        )
        return {
            "total_unique_pairs": total_pairs,
            "already_present_pairs": already_present_pairs,
            "synced_pairs": synced_pairs,
            "failed_pairs": failed_pairs,
        }

    def parse_fixture_to_match(self, fixture: dict) -> Optional[MatchCreate]:
        try:
            f_info = fixture.get("fixture", {})
            f_league = fixture.get("league", {})
            f_teams = fixture.get("teams", {})
            f_goals = fixture.get("goals", {})
            date_str = f_info.get("date")
            if not date_str:
                return None

            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            home_team_id = f_teams.get("home", {}).get("id") or self._extract_id_from_logo(f_teams.get("home", {}))
            away_team_id = f_teams.get("away", {}).get("id") or self._extract_id_from_logo(f_teams.get("away", {}))
            return MatchCreate(
                match_id=int(f_info.get("id")),
                league_id=f_league.get("id"),
                season=self._coerce_season(f_league.get("season")),
                league_name=f_league.get("name"),
                league_logo=f_league.get("logo"),
                country_name=f_league.get("country"),
                country_logo=f_league.get("flag"),
                match_time=dt,
                status=f_info.get("status", {}).get("short", "NS"),
                elapsed=f_info.get("status", {}).get("elapsed", 0) or 0,
                home_team=f_teams.get("home", {}).get("name"),
                home_team_id=home_team_id,
                home_team_logo=f_teams.get("home", {}).get("logo"),
                away_team=f_teams.get("away", {}).get("name"),
                away_team_id=away_team_id,
                away_team_logo=f_teams.get("away", {}).get("logo"),
                home_score=f_goals.get("home") or 0,
                away_score=f_goals.get("away") or 0,
                venue_name=f_info.get("venue", {}).get("name", "Unknown"),
                venue_city=f_info.get("venue", {}).get("city", "Unknown"),
            )
        except Exception as exc:
            logger.error("Parsing error for Fixture ID %s: %s", fixture.get("fixture", {}).get("id"), exc)
            return None

    async def _process_sync_with_candidates(self, db: AsyncSession, fixtures: list) -> tuple[dict, set[tuple[int, int]]]:
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        if not allowed_ids:
            logger.info("Allowed league list is empty; skipping all fixture synchronization.")
            return {"success": True, "inserted": 0, "updated": 0, "total": 0, "failed": 0}, set()

        filtered_fixtures = []
        for fixture_raw in fixtures:
            league_info = fixture_raw.get("league") or {}
            league_id = league_info.get("id")
            league_name = league_info.get("name") or "Unknown league"
            if league_id is None:
                logger.warning("Skipping fixture %s with missing league_id", fixture_raw.get("fixture", {}).get("id"))
                continue
            if int(league_id) not in allowed_ids:
                logger.debug("SKIPPED LEAGUE: league_id=%s league_name=%s", league_id, league_name)
                continue

            logger.debug("ALLOWED LEAGUE: league_id=%s league_name=%s", league_id, league_name)
            filtered_fixtures.append(fixture_raw)

        if not filtered_fixtures:
            logger.info("No allowed leagues were present in the fixture payload; skipping fixture synchronization.")
            return {"success": True, "inserted": 0, "updated": 0, "total": 0, "failed": 0}, set()

        result = {
            "success": True,
            "inserted": 0,
            "updated": 0,
            "total": len(filtered_fixtures),
            "failed": 0,
            "standings_prewarm_candidates": 0,
        }
        prewarm_candidates: set[tuple[int, int]] = set()
        seen_league_ids: set[int] = set()

        for fixture_raw in filtered_fixtures:
            fixture_id = fixture_raw.get("fixture", {}).get("id")
            try:
                match = self.parse_fixture_to_match(fixture_raw)
                if match is None:
                    result["failed"] += 1
                    logger.warning("Fixture ID %s failed parsing.", fixture_id)
                    await db.rollback()
                    continue

                if match.season is not None:
                    try:
                        prewarm_candidates.add((int(match.league_id), int(match.season)))
                    except (TypeError, ValueError):
                        pass

                await self._upsert_leagues_from_fixtures(db, [fixture_raw], seen_league_ids)

                needed_teams = {}
                if match.home_team_id and match.home_team_id not in needed_teams:
                    needed_teams[match.home_team_id] = {"name": match.home_team, "logo": match.home_team_logo}
                if match.away_team_id and match.away_team_id not in needed_teams:
                    needed_teams[match.away_team_id] = {"name": match.away_team, "logo": match.away_team_logo}

                if needed_teams:
                    await self.team_service.ensure_teams_exist(db, [{"team_id": tid, "name": info["name"], "logo": info["logo"]} for tid, info in needed_teams.items()])

                existing_rows = await self.match_repository.get_many_by_ids(db, [match.match_id])
                existing_ids = {row.match_id for row in existing_rows}
                updated = 1 if match.match_id in existing_ids else 0
                inserted = 1 - updated

                match_row = match.model_dump()
                insert_stmt = pg_insert(Match).values([match_row])
                upsert_stmt = insert_stmt.on_conflict_do_update(
                    index_elements=["fixture_id"],
                    set_={
                        "league_id": insert_stmt.excluded.league_id,
                        "season": insert_stmt.excluded.season,
                        "league_name": insert_stmt.excluded.league_name,
                        "league_logo": insert_stmt.excluded.league_logo,
                        "country_name": insert_stmt.excluded.country_name,
                        "country_logo": insert_stmt.excluded.country_logo,
                        "match_time": insert_stmt.excluded.match_time,
                        "status": insert_stmt.excluded.status,
                        "elapsed": insert_stmt.excluded.elapsed,
                        "home_team": insert_stmt.excluded.home_team,
                        "home_team_id": insert_stmt.excluded.home_team_id,
                        "home_team_logo": insert_stmt.excluded.home_team_logo,
                        "away_team": insert_stmt.excluded.away_team,
                        "away_team_id": insert_stmt.excluded.away_team_id,
                        "away_team_logo": insert_stmt.excluded.away_team_logo,
                        "home_score": insert_stmt.excluded.home_score,
                        "away_score": insert_stmt.excluded.away_score,
                        "venue_name": insert_stmt.excluded.venue_name,
                        "venue_city": insert_stmt.excluded.venue_city,
                    },
                )
                await db.execute(upsert_stmt)

                home_team_id = getattr(match, "home_team_id", None)
                away_team_id = getattr(match, "away_team_id", None)
                current_league_id = int(match.league_id) if match.league_id is not None else None
                current_season = str(match.season) if match.season is not None else None

                if current_league_id is not None and current_season is not None:
                    if home_team_id is not None:
                        await self.team_sync_service.update_team_context(
                            db,
                            int(home_team_id),
                            current_league_id=current_league_id,
                            current_season=current_season,
                        )

                    if away_team_id is not None:
                        await self.team_sync_service.update_team_context(
                            db,
                            int(away_team_id),
                            current_league_id=current_league_id,
                            current_season=current_season,
                        )

                await db.flush()
                await db.commit()
                await self._sync_active_match_registration(match.match_id, match.status)

                result["inserted"] += inserted
                result["updated"] += updated
                if not getattr(self, "_defer_live_cache_invalidation", False):
                    try:
                        await self.cache_service.delete(make_cache_key("live_matches"))
                    except Exception as exc:
                        logger.warning("Cache invalidation failed for fixture_id=%s error=%s", fixture_id, exc)
            except Exception as exc:
                await db.rollback()
                result["failed"] += 1
                logger.warning("Fixture ID %s failed during sync: %s", fixture_id, exc)
                continue

        result["standings_prewarm_candidates"] = len(prewarm_candidates)
        return result, prewarm_candidates

    async def _upsert_leagues_from_fixtures(self, db: AsyncSession, fixtures: list[dict], seen_league_ids: set[int] | None = None) -> None:
        league_payloads: dict[int, dict] = {}
        seen = seen_league_ids if seen_league_ids is not None else set()

        for fixture in fixtures:
            league_info = fixture.get("league") or {}
            league_id = league_info.get("id")
            if league_id is None:
                continue

            try:
                normalized_id = int(league_id)
            except (TypeError, ValueError):
                continue

            league_payloads[normalized_id] = league_info

        if not league_payloads:
            return

        existing_rows = await self.league_repository.get_many_by_ids(db, list(league_payloads.keys()))
        existing_map = {league.league_id: league for league in existing_rows}

        for league_id, payload in league_payloads.items():
            season_value = payload.get("season")
            season_text = str(season_value) if season_value is not None else None

            if league_id in seen:
                continue

            existing = existing_map.get(league_id)
            if existing:
                existing.name = payload.get("name") or existing.name
                existing.country = payload.get("country") or existing.country
                existing.country_code = payload.get("code") or existing.country_code
                existing.logo = payload.get("logo") if payload.get("logo") is not None else existing.logo
                existing.season = season_text or existing.season
                seen.add(league_id)
                continue

            db.add(
                League(
                    league_id=league_id,
                    name=payload.get("name") or "Unknown League",
                    country=payload.get("country"),
                    country_code=payload.get("code"),
                    logo=payload.get("logo"),
                    season=season_text,
                )
            )
            seen.add(league_id)

    async def _process_sync(self, db: AsyncSession, fixtures: list) -> dict:
        result, _ = await self._process_sync_with_candidates(db, fixtures)
        return result

    async def sync_full_season(self, db: AsyncSession, league: int, season: int) -> dict:
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        if league not in allowed_ids:
            logger.debug("SKIPPED LEAGUE: league_id=%s league_name=%s", league, "requested league")
            return {"success": True, "inserted": 0, "updated": 0, "total": 0, "message": "League is not allowed for synchronization"}

        result = await self.fixture_provider.get_fixtures(league=league, season=season)
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": False, "message": "No fixtures found"}

        try:
            self._defer_live_cache_invalidation = True
            sync_result = await self._process_sync(db, fixtures)
            return sync_result
        except Exception:
            await db.rollback()
            raise
        finally:
            self._defer_live_cache_invalidation = False

    async def sync_daily_fixtures(self, db: AsyncSession, target_date: str) -> dict:
        result = await self.fixture_provider.get_fixtures_by_date(target_date=target_date)
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": True, "message": "No matches for today", "updated": 0}

        try:
            self._defer_live_cache_invalidation = True
            sync_result, prewarm_candidates = await self._process_sync_with_candidates(db, fixtures)
        finally:
            self._defer_live_cache_invalidation = False

        if not sync_result.get("success"):
            return sync_result

        prewarm_metrics = {
            "standings_prewarm_candidates": sync_result.get("standings_prewarm_candidates", 0),
            "standings_prewarm_synced": 0,
            "standings_prewarm_skipped": 0,
            "standings_prewarm_failed": 0,
        }

        if prewarm_candidates:
            previous_defer_value = getattr(self.standing_service, "_defer_standings_cache_invalidation", False)
            self.standing_service._defer_standings_cache_invalidation = True
            try:
                prewarm_result = await self._prewarm_missing_standings(db, prewarm_candidates)
            finally:
                self.standing_service._defer_standings_cache_invalidation = previous_defer_value
            prewarm_metrics.update(
                {
                    "standings_prewarm_candidates": prewarm_result.get("total_unique_pairs", prewarm_metrics["standings_prewarm_candidates"]),
                    "standings_prewarm_synced": prewarm_result.get("synced_pairs", 0),
                    "standings_prewarm_skipped": prewarm_result.get("already_present_pairs", 0),
                    "standings_prewarm_failed": prewarm_result.get("failed_pairs", 0),
                }
            )

        sync_result.update(prewarm_metrics)

        return sync_result

    async def sync_live_matches(self, db: AsyncSession) -> dict:
        result = await self.fixture_provider.get_live_fixtures()
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}

        fixtures = result.get("response", [])
        api_live_ids = {item["fixture"]["id"] for item in fixtures if item.get("fixture") and item["fixture"].get("id")}
        stale_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
        stale_matches = await self.match_repository.get_live_stale(db, api_live_ids, stale_threshold)
        logger.debug("get_live_stale returned %d stale matches", len(stale_matches))
        for match in stale_matches[:20]:
            logger.debug("fixture_id=%s status=%s match_time=%s", match.match_id, getattr(match, "status", None), getattr(match, "match_time", None))

        if stale_matches:
            stale_ids = [str(match.match_id) for match in stale_matches]
            logger.debug("Syncing %d stale matches that are no longer in live feed", len(stale_ids))
            try:
                stale_chunks = [stale_ids[i : i + 20] for i in range(0, len(stale_ids), 20)]
                total_chunks = len(stale_chunks)
                stale_api_fixtures = []

                for chunk_index, chunk in enumerate(stale_chunks, start=1):
                    logger.debug("Fetching stale chunk %s/%s (%s ids)", chunk_index, total_chunks, len(chunk))
                    stale_resp = await self.fixture_provider.get_fixtures_by_ids(chunk)
                    chunk_response = stale_resp.get("response", []) if isinstance(stale_resp, dict) else []
                    logger.debug("Chunk returned %s fixtures", len(chunk_response))

                    print("STALE_RESULTS =", stale_resp.get("results"))
                    print("STALE_ERRORS =", stale_resp.get("errors"))
                    print("STALE_RESPONSE_LEN =", len(chunk_response))
                    if chunk_response:
                        first = chunk_response[0]
                        print("FIRST_FIXTURE_ID =", first.get("fixture", {}).get("id"))
                        print("FIRST_STATUS =", first.get("fixture", {}).get("status", {}).get("short"))
                        print("FIRST_ELAPSED =", first.get("fixture", {}).get("status", {}).get("elapsed"))
                    print(f"STALE_API_COUNT={len(chunk_response)}")

                    stale_api_fixtures.extend(chunk_response)

                if stale_api_fixtures:
                    logger.debug("total_stale_api_fixtures=%d", len(stale_api_fixtures))
                    for fixture in stale_api_fixtures:
                        fixture_info = fixture.get("fixture", {})
                        status_info = fixture_info.get("status", {})
                        logger.debug(
                            "fixture_id=%s api_status=%s elapsed=%s",
                            fixture_info.get("id"),
                            status_info.get("short"),
                            status_info.get("elapsed"),
                        )
                    fixtures.extend(stale_api_fixtures)
            except Exception as exc:
                logger.error("Failed to fetch updates for stale matches: %s", exc)

        if not fixtures:
            return {"success": True, "message": "No live matches", "updated": 0}

        try:
            self._defer_live_cache_invalidation = True
            return await self._process_sync(db, fixtures)
        finally:
            self._defer_live_cache_invalidation = False
