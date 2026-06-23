import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.league import League
from app.models.team import Team
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.event_service import EventService
from app.services.h2h_service import H2HService
from app.services.league_service import LeagueService
from app.services.lineup_service import LineupService
from app.services.match_service import MatchService
from app.services.odds_service import OddsService
from app.services.standing_service import StandingService
from app.services.statistics_service import StatisticsService
from app.services.team_service import TeamService

FINISHED_STATUSES = {"FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO"}
LIVE_STATUSES = {"1H", "2H", "HT", "ET", "LIVE", "BT", "P"}

logger = logging.getLogger(__name__)


class FootballAPIService:
    """Lightweight compatibility facade over the dedicated service layer."""

    def __init__(
        self,
        client: Optional[FootballAPIClient] = None,
        cache_service: Optional[CacheService] = None,
        team_service: Optional[TeamService] = None,
        league_service: Optional[LeagueService] = None,
        standing_service: Optional[StandingService] = None,
        match_service: Optional[MatchService] = None,
        odds_service: Optional[OddsService] = None,
        h2h_service: Optional[H2HService] = None,
        lineup_service: Optional[LineupService] = None,
        event_service: Optional[EventService] = None,
    ) -> None:
        self.client = client or FootballAPIClient()
        self.cache_service = cache_service or CacheService()
        self.team_service = team_service or TeamService(self.client, self.cache_service)
        self.league_service = league_service or LeagueService(self.client, self.cache_service)
        self.standing_service = standing_service or StandingService(self.client, self.team_service, self.cache_service)
        self.match_service = match_service or MatchService(self.client, self.team_service, self.cache_service)
        self.odds_service = odds_service or OddsService(self.client, self.cache_service)
        self.h2h_service = h2h_service or H2HService(self.client, self.cache_service)
        self.lineup_service = lineup_service or LineupService(self.client, self.cache_service)
        self.event_service = event_service or EventService(self.client, self.cache_service)
        self.statistics_service = StatisticsService(self.client, self.cache_service)

    async def get_fixtures(self, league: int, season: int) -> Optional[dict]:
        return await self.client.get("/fixtures", params={"league": league, "season": season})

    async def get_fixtures_by_date(self, target_date: str) -> Optional[dict]:
        return await self.client.get("/fixtures", params={"date": target_date})

    async def get_live_fixtures(self) -> Optional[dict]:
        return await self.client.get("/fixtures", params={"live": "all"})

    def parse_fixture_to_match(self, fixture: dict) -> Optional[Any]:
        return self.match_service.parse_fixture_to_match(fixture)

    async def ensure_teams_exist(self, db: AsyncSession, teams_data: list[dict]) -> dict:
        return await self.team_service.ensure_teams_exist(db, teams_data)

    async def _process_sync(self, db: AsyncSession, fixtures: list) -> dict:
        return await self.match_service._process_sync(db, fixtures)

    async def sync_full_season(self, db: AsyncSession, league: int, season: int) -> dict:
        return await self.match_service.sync_full_season(db, league, season)

    async def sync_daily_fixtures(self, db: AsyncSession, target_date: str) -> dict:
        return await self.match_service.sync_daily_fixtures(db, target_date)

    async def sync_live_matches(self, db: AsyncSession) -> dict:
        return await self.match_service.sync_live_matches(db)

    async def get_match_events(self, match_id: int) -> Optional[dict]:
        return await self.event_service.get_match_events(match_id)

    async def get_match_lineup(self, match_id: int) -> Optional[dict]:
        return await self.lineup_service.get_match_lineup(match_id)

    async def get_cached_match_lineup(self, db: AsyncSession, match_id: int) -> Optional[List[Dict[str, Any]]]:
        return await self.lineup_service.get_cached_match_lineup(db, match_id)

    async def sync_match_lineup(self, db: AsyncSession, match_id: int) -> Dict[str, Any]:
        return await self.lineup_service.sync_lineup(db, match_id)

    async def get_match_h2h(self, match_id: int) -> Optional[dict]:
        return await self.h2h_service.get_match_h2h(match_id)

    async def get_cached_statistics(self, db: AsyncSession, match_id: int) -> Optional[dict]:
        return await self.statistics_service.get_cached_statistics(db, match_id)

    async def get_normalized_statistics(self, db: AsyncSession, match_id: int) -> Optional[dict]:
        return await self.statistics_service.get_normalized_statistics(db, match_id)

    async def get_cached_h2h(self, db: AsyncSession, team1_id: int, team2_id: int, match_id: int) -> Optional[dict]:
        return await self.h2h_service.get_cached_h2h(db, team1_id, team2_id, match_id)

    async def get_match_odds(self, match_id: int) -> Optional[dict]:
        return await self.odds_service.get_match_odds(match_id)

    async def sync_match_events(self, db: AsyncSession, match_id: int) -> dict:
        return await self.event_service.sync_match_events(db, match_id)

    async def get_cached_match_events(self, db: AsyncSession, match_id: int) -> List[Dict[str, Any]]:
        return await self.event_service.get_cached_match_events(db, match_id)

    async def get_cached_odds(self, db: AsyncSession, fixture_id: int) -> dict:
        return await self.odds_service.get_cached_odds(db, fixture_id)

    async def get_league_details(self, league_id: int) -> Optional[dict]:
        return await self.league_service.get_league_details(league_id)

    async def get_league_top_scorers(self, league_id: int, season: int) -> Optional[dict]:
        return await self.league_service.get_cached_league_top_scorers(league_id, season)

    async def get_all_leagues(self) -> Optional[dict]:
        return await self.league_service.get_all_leagues()

    async def get_team_details(self, team_id: int) -> Optional[dict]:
        return await self.team_service.get_team_details(team_id)

    async def get_team_fixtures(self, db: AsyncSession, team_id: int) -> Optional[dict]:
        return await self.team_service.get_cached_team_fixtures(db, team_id)

    async def get_team_squad(self, team_id: int) -> Optional[dict]:
        return await self.team_service.get_cached_team_squad(team_id)

    async def get_team_statistics(self, team_id: int, league_id: int, season: int) -> Optional[dict]:
        return await self.team_service.get_cached_team_statistics(team_id, league_id, season)

    async def get_league_standings(self, league_id: int, season: int) -> Optional[dict]:
        return await self.standing_service.get_league_standings(league_id, season)

    async def upsert_league(self, db: AsyncSession, league_data: dict) -> League:
        return await self.league_service.upsert_league(db, league_data)

    async def upsert_team(self, db: AsyncSession, team_data: dict) -> Team:
        return await self.team_service.upsert_team(db, team_data)

    async def sync_all_leagues(self, db: AsyncSession) -> dict:
        return await self.league_service.sync_all_leagues(db)

    async def sync_standings(self, db: AsyncSession, league_id: int, season: int) -> dict:
        return await self.standing_service.sync_standings(db, league_id, season)

    async def upsert_standings(self, db: AsyncSession, standings_data: list, league_id: int, season: str):
        return await self.standing_service.upsert_standings(db, standings_data, league_id, season)

    async def get_cached_standings(self, db: AsyncSession, league_id: int, season: int | str) -> Optional[list]:
        return await self.standing_service.get_cached_standings(db, league_id, season)


football_service = FootballAPIService()
