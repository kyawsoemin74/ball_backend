from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.match import Match
from app.providers.fixture_provider import FixtureProvider
from app.schemas.match import MatchResponse
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.fixture_sync_service import FixtureSyncService
from app.services.standing_service import StandingService
from app.services.team_service import TeamService
from app.core.config import settings


class MatchService:
    """Compatibility facade delegating write/sync operations to FixtureSyncService."""

    def __init__(
        self,
        client: FootballAPIClient,
        team_service: TeamService,
        cache_service: CacheService | None = None,
        standing_service: StandingService | None = None,
        fixture_provider: FixtureProvider | None = None,
        fixture_sync_service: FixtureSyncService | None = None,
    ) -> None:
        self.fixture_sync_service = fixture_sync_service or FixtureSyncService(
            client=client,
            team_service=team_service,
            cache_service=cache_service,
            standing_service=standing_service,
            fixture_provider=fixture_provider,
        )
        self._fixture_sync_upsert_leagues_impl = self.fixture_sync_service._upsert_leagues_from_fixtures
        self.fixture_sync_service._upsert_leagues_from_fixtures = self._upsert_leagues_from_fixtures

    @property
    def fixture_provider(self):
        return self.fixture_sync_service.fixture_provider

    @fixture_provider.setter
    def fixture_provider(self, value):
        self.fixture_sync_service.fixture_provider = value

    @property
    def allowed_league_repository(self):
        return self.fixture_sync_service.allowed_league_repository

    @allowed_league_repository.setter
    def allowed_league_repository(self, value):
        self.fixture_sync_service.allowed_league_repository = value

    @property
    def league_repository(self):
        return self.fixture_sync_service.league_repository

    @league_repository.setter
    def league_repository(self, value):
        self.fixture_sync_service.league_repository = value

    @property
    def match_repository(self):
        return self.fixture_sync_service.match_repository

    @match_repository.setter
    def match_repository(self, value):
        self.fixture_sync_service.match_repository = value

    @property
    def cache_service(self):
        return self.fixture_sync_service.cache_service

    @cache_service.setter
    def cache_service(self, value):
        self.fixture_sync_service.cache_service = value

    def parse_fixture_to_match(self, fixture: dict):
        return self.fixture_sync_service.parse_fixture_to_match(fixture)

    async def _process_sync_with_candidates(self, db: AsyncSession, fixtures: list):
        return await self.fixture_sync_service._process_sync_with_candidates(db, fixtures)

    async def _process_sync(self, db: AsyncSession, fixtures: list):
        result, _ = await self._process_sync_with_candidates(db, fixtures)
        return result

    async def _upsert_leagues_from_fixtures(self, db: AsyncSession, fixtures: list[dict], seen_league_ids: set[int] | None = None):
        return await self._fixture_sync_upsert_leagues_impl(db, fixtures, seen_league_ids)

    async def sync_full_season(self, db: AsyncSession, league: int, season: int):
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        if league not in allowed_ids:
            return {"success": True, "inserted": 0, "updated": 0, "total": 0, "message": "League is not allowed for synchronization"}

        result = await self.fixture_provider.get_fixtures(league=league, season=season)
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": False, "message": "No fixtures found"}

        try:
            self.fixture_sync_service._defer_live_cache_invalidation = True
            sync_result = await self._process_sync(db, fixtures)
            return sync_result
        except Exception:
            await db.rollback()
            raise
        finally:
            self.fixture_sync_service._defer_live_cache_invalidation = False

    async def sync_daily_fixtures(self, db: AsyncSession, target_date: str):
        result = await self.fixture_provider.get_fixtures_by_date(target_date=target_date)
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": True, "message": "No matches for today", "updated": 0}

        try:
            self.fixture_sync_service._defer_live_cache_invalidation = True
            sync_result, prewarm_candidates = await self._process_sync_with_candidates(db, fixtures)
        finally:
            self.fixture_sync_service._defer_live_cache_invalidation = False

        if not sync_result.get("success"):
            return sync_result

        prewarm_metrics = {
            "standings_prewarm_candidates": sync_result.get("standings_prewarm_candidates", 0),
            "standings_prewarm_synced": 0,
            "standings_prewarm_skipped": 0,
            "standings_prewarm_failed": 0,
        }

        if prewarm_candidates:
            previous_defer_value = getattr(self.fixture_sync_service.standing_service, "_defer_standings_cache_invalidation", False)
            self.fixture_sync_service.standing_service._defer_standings_cache_invalidation = True
            try:
                prewarm_result = await self.fixture_sync_service._prewarm_missing_standings(db, prewarm_candidates)
            finally:
                self.fixture_sync_service.standing_service._defer_standings_cache_invalidation = previous_defer_value
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

    async def sync_live_matches(self, db: AsyncSession):
        return await self.fixture_sync_service.sync_live_matches(db)

    async def get_team_matches(self, db: AsyncSession, team_id: int):
        cache_key = make_cache_key("team", team_id, "matches")
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        matches = await self.match_repository.get_team_matches(db, team_id)
        if not matches:
            payload = []
            await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_TEAM_FIXTURES)
            return payload

        upcoming = []
        finished = []
        for match in matches:
            response = MatchResponse.model_validate(match)
            if response.status in {"FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO"}:
                finished.append(response.model_dump(mode="json"))
            else:
                upcoming.append(response.model_dump(mode="json"))

        payload = upcoming + finished
        await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_TEAM_FIXTURES)
        return payload

    async def get_team_finished_matches(self, db: AsyncSession, team_id: int):
        cache_key = make_cache_key("team", team_id, "finished-matches")
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        # Delegate to repository for newest-first results
        matches = await self.match_repository.get_team_matches_recent(db, team_id)
        if not matches:
            payload: list = []
            await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_TEAM_FIXTURES)
            return payload

        finished: list = []
        for match in matches:
            response = MatchResponse.model_validate(match)
            if response.status in {"FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO"}:
                finished.append(response.model_dump(mode="json"))

        # Apply business rule: newest first already from repo, take top 10 finished matches
        payload = finished[:10]
        await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_TEAM_FIXTURES)
        return payload
