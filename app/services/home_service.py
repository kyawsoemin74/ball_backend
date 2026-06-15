from app.repositories.league_repository import LeagueRepository
from app.services.league_grouping_service import LeagueGroupingService


class HomeService:
    """Builds the frontend-ready home screen payload."""

    def __init__(self, league_repository: LeagueRepository | None = None, grouping_service: LeagueGroupingService | None = None) -> None:
        self.league_repository = league_repository or LeagueRepository()
        self.grouping_service = grouping_service or LeagueGroupingService()

    async def get_home_payload(self, db) -> dict:
        live_today = self._order_leagues(await self.league_repository.get_leagues_with_matches_today(db))
        featured = self._order_leagues(await self.league_repository.get_featured_leagues(db))
        all_leagues = await self.league_repository.get_all_leagues(db)

        countries = self.grouping_service.build_groups(
            [league for league in all_leagues if not getattr(league, "is_featured", False)]
        )

        return {
            "live_today": [self._serialize(league) for league in live_today],
            "featured": [self._serialize(league) for league in featured],
            "countries": countries,
        }

    def _order_leagues(self, leagues) -> list:
        visible = [
            league
            for league in leagues
            if int(getattr(league, "display_order", 999) or 999) <= 200
            or bool(getattr(league, "is_featured", False))
        ]
        return sorted(
            visible,
            key=lambda league: (
                int(getattr(league, "display_order", 999) or 999),
                str(getattr(league, "name", "")).lower(),
            ),
        )

    def _serialize(self, league) -> dict:
        return self.grouping_service._serialize(league)
