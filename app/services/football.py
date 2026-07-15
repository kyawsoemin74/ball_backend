import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.league import League
from app.models.team import Team
from app.providers.fixture_provider import FixtureProvider
from app.providers.h2h_provider import H2HProvider
from app.providers.league_provider import LeagueProvider
from app.providers.standing_provider import StandingProvider
from app.providers.statistics_provider import StatisticsProvider
from app.providers.team_provider import TeamProvider
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.event_service import EventService
from app.services.event_sync_service import EventSyncService
from app.services.fixture_sync_service import FixtureSyncService
from app.services.h2h_service import H2HService
from app.services.h2h_sync_service import H2HSyncService
from app.services.league_service import LeagueService
from app.services.league_sync_service import LeagueSyncService
from app.providers.event_provider import EventProvider
from app.providers.lineup_provider import LineupProvider
from app.services.lineup_service import LineupService
from app.services.lineup_sync_service import LineupSyncService
from app.services.match_service import MatchService
from app.providers.odds_provider import OddsProvider
from app.services.odds_service import OddsService
from app.services.odds_sync_service import OddsSyncService
from app.services.standing_service import StandingService
from app.services.statistics_service import StatisticsService
from app.services.statistics_sync_service import StatisticsSyncService
from app.services.team_service import TeamService
from app.services.team_sync_service import TeamSyncService

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
        fixture_sync_service: Optional[FixtureSyncService] = None,
        fixture_provider: Optional[FixtureProvider] = None,
        league_provider: Optional[LeagueProvider] = None,
        league_sync_service: Optional[LeagueSyncService] = None,
        standing_provider: Optional[StandingProvider] = None,
        team_provider: Optional[TeamProvider] = None,
        team_sync_service: Optional[TeamSyncService] = None,
        odds_service: Optional[OddsService] = None,
        h2h_service: Optional[H2HService] = None,
        lineup_service: Optional[LineupService] = None,
        event_service: Optional[EventService] = None,
    ) -> None:
        self.client = client or FootballAPIClient()
        self.fixture_provider = fixture_provider or FixtureProvider(self.client)
        self.league_provider = league_provider or LeagueProvider(self.client)
        self.standing_provider = standing_provider or StandingProvider(self.client)
        self.team_provider = team_provider or TeamProvider(self.client)
        self.cache_service = cache_service or CacheService()
        self.team_service = team_service or TeamService(
            self.client,
            self.cache_service,
            team_provider=self.team_provider,
        )
        self.team_sync_service = team_sync_service or self.team_service.team_sync_service
        self.league_sync_service = league_sync_service
        self.league_service = league_service or LeagueService(
            self.client,
            self.cache_service,
            league_provider=self.league_provider,
            league_sync_service=self.league_sync_service,
        )
        if self.league_sync_service is None:
            self.league_sync_service = self.league_service.league_sync_service
        self.standing_service = standing_service or StandingService(
            self.client,
            self.team_service,
            self.cache_service,
            standing_provider=self.standing_provider,
        )
        self.fixture_sync_service = fixture_sync_service or FixtureSyncService(
            self.client,
            self.team_service,
            self.cache_service,
            self.standing_service,
            fixture_provider=self.fixture_provider,
        )
        self.match_service = match_service or MatchService(
            self.client,
            self.team_service,
            self.cache_service,
            self.standing_service,
            fixture_provider=self.fixture_provider,
            fixture_sync_service=self.fixture_sync_service,
        )
        self.odds_provider = OddsProvider(self.client)
        self.odds_service = odds_service or OddsService(self.client, self.cache_service, odds_provider=self.odds_provider)
        self.odds_sync_service = OddsSyncService(self.odds_service, cache_service=self.cache_service, odds_provider=self.odds_provider)
        self.odds_service.odds_sync_service = self.odds_sync_service
        self.h2h_provider = H2HProvider(self.client)
        self.h2h_service = h2h_service or H2HService(
            self.client,
            self.cache_service,
            h2h_provider=self.h2h_provider,
        )
        self.h2h_sync_service = self.h2h_service.h2h_sync_service
        self.lineup_provider = LineupProvider(self.client)
        self.lineup_sync_service = LineupSyncService(lineup_provider=self.lineup_provider)
        self.lineup_service = lineup_service or LineupService(
            self.client,
            self.cache_service,
            lineup_provider=self.lineup_provider,
            lineup_sync_service=self.lineup_sync_service,
        )
        self.event_provider = EventProvider(self.client)
        self.event_sync_service = EventSyncService(event_provider=self.event_provider)
        self.statistics_provider = StatisticsProvider(self.client)
        self.event_service = event_service or EventService(
            self.client,
            self.cache_service,
            event_provider=self.event_provider,
            event_sync_service=self.event_sync_service,
        )
        self.statistics_sync_service = StatisticsSyncService(statistics_provider=self.statistics_provider)
        self.statistics_service = StatisticsService(
            self.client,
            self.cache_service,
            statistics_provider=self.statistics_provider,
            statistics_sync_service=self.statistics_sync_service,
        )

    async def get_fixtures(self, league: int, season: int) -> Optional[dict]:
        return await self.fixture_provider.get_fixtures(league=league, season=season)

    async def get_fixtures_by_date(self, target_date: str) -> Optional[dict]:
        return await self.fixture_provider.get_fixtures_by_date(target_date=target_date)

    async def get_live_fixtures(self) -> Optional[dict]:
        return await self.fixture_provider.get_live_fixtures()

    def parse_fixture_to_match(self, fixture: dict) -> Optional[Any]:
        return self.fixture_sync_service.parse_fixture_to_match(fixture)

    async def ensure_teams_exist(self, db: AsyncSession, teams_data: list[dict]) -> dict:
        return await self.team_sync_service.ensure_teams_exist(db, teams_data)

    async def _process_sync(self, db: AsyncSession, fixtures: list) -> dict:
        return await self.fixture_sync_service._process_sync(db, fixtures)

    async def sync_full_season(self, db: AsyncSession, league: int, season: int) -> dict:
        return await self.fixture_sync_service.sync_full_season(db, league, season)

    async def sync_daily_fixtures(self, db: AsyncSession, target_date: str) -> dict:
        return await self.fixture_sync_service.sync_daily_fixtures(db, target_date)

    async def sync_live_matches(self, db: AsyncSession) -> dict:
        return await self.fixture_sync_service.sync_live_matches(db)

    async def get_match_events(self, match_id: int) -> Optional[dict]:
        return await self.event_service.get_match_events(match_id)

    async def get_match_lineup(self, match_id: int) -> Optional[dict]:
        return await self.lineup_service.get_match_lineup(match_id)

    async def get_cached_match_lineup(self, db: AsyncSession, match_id: int) -> Optional[List[Dict[str, Any]]]:
        return await self.lineup_service.get_cached_match_lineup(db, match_id)

    async def sync_match_lineup(self, db: AsyncSession, match_id: int) -> Dict[str, Any]:
        return await self.lineup_sync_service.sync_lineup(
            db=db,
            match_id=match_id,
            validate_lineup=self.lineup_service._is_valid_lineup_response,
            cache_service=self.cache_service,
            cache_key=make_cache_key("lineup", match_id),
        )

    async def get_match_h2h(self, match_id: int) -> Optional[dict]:
        return await self.h2h_service.get_match_h2h(match_id)

    async def get_cached_statistics(self, db: AsyncSession, match_id: int) -> Optional[dict]:
        return await self.statistics_service.get_cached_statistics(db, match_id)

    async def get_normalized_statistics(self, db: AsyncSession, match_id: int) -> Optional[dict]:
        return await self.statistics_service.get_normalized_statistics(db, match_id)

    async def sync_match_statistics(self, db: AsyncSession, match_id: int) -> Dict[str, Any]:
        return await self.statistics_service.sync_match_statistics(db, match_id)

    async def get_cached_h2h(self, db: AsyncSession, team1_id: int, team2_id: int, match_id: int) -> Optional[dict]:
        return await self.h2h_service.get_cached_h2h(db, team1_id, team2_id, match_id)

    async def get_match_odds(self, match_id: int) -> Optional[dict]:
        return await self.odds_service.get_match_odds(match_id)

    async def sync_match_events(self, db: AsyncSession, match_id: int) -> dict:
        # Preserve facade signature. Delegate to EventSyncService via EventService
        # which now returns sync result without committing or invalidating cache.
        return await self.event_service.sync_match_events(db, match_id)

    async def get_cached_match_events(self, db: AsyncSession, match_id: int) -> List[Dict[str, Any]]:
        return await self.event_service.get_cached_match_events(db, match_id)

    async def get_cached_odds(self, db: AsyncSession, fixture_id: int) -> dict:
        # Read-only odds access; scheduler owns snapshot refresh.
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
        return await self.league_sync_service.upsert_league(db, league_data)

    async def upsert_team(self, db: AsyncSession, team_data: dict) -> Team:
        return await self.team_sync_service.upsert_team(db, team_data)

    async def sync_all_leagues(self, db: AsyncSession) -> dict:
        return await self.league_sync_service.sync_all_leagues(db)

    async def sync_standings(self, db: AsyncSession, league_id: int, season: int) -> dict:
        return await self.standing_service.sync_standings(db, league_id, season)

    async def upsert_standings(self, db: AsyncSession, standings_data: list, league_id: int, season: str):
        return await self.standing_service.upsert_standings(db, standings_data, league_id, season)

    async def get_cached_standings(self, db: AsyncSession, league_id: int, season: int | str) -> Optional[list]:
        return await self.standing_service.get_cached_standings(db, league_id, season)

    async def get_team_profile_standings(self, db: AsyncSession, team_id: int) -> Optional[list]:
        return await self.standing_service.get_team_profile_standings(db, team_id)

    async def get_team_matches(self, db: AsyncSession, team_id: int):
        return await self.match_service.get_team_matches(db, team_id)

    async def get_team_finished_matches(self, db: AsyncSession, team_id: int):
        return await self.match_service.get_team_finished_matches(db, team_id)


football_service = FootballAPIService()
