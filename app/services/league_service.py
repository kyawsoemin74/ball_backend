import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.league import League
from app.providers.league_provider import LeagueProvider
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.repositories.league_repository import LeagueRepository
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.country_sync_service import CountrySyncService
from app.services.league_sync_service import LeagueSyncService

logger = logging.getLogger(__name__)


class LeagueService:
    async def find_by_provider_identity(
        self,
        db: AsyncSession,
        provider: str,
        provider_id: str | int | None,
    ) -> League | None:
        return await self._league_repository.find_by_provider_identity(
            db,
            provider,
            provider_id,
        )

    def __init__(
        self,
        client: FootballAPIClient,
        cache_service: CacheService | None = None,
        league_provider: LeagueProvider | None = None,
        league_sync_service: LeagueSyncService | None = None,
        team_sync_service=None,
    ) -> None:
        self.client = client
        self.league_provider = league_provider or LeagueProvider(client)
        self.cache_service = cache_service or CacheService()
        self._league_repository = LeagueRepository()
        self._allowed_league_repository = AllowedLeagueRepository()
        self.country_sync_service = CountrySyncService()
        self.league_sync_service = league_sync_service or LeagueSyncService(
            cache_service=self.cache_service,
            league_repository=self._league_repository,
            allowed_league_repository=self._allowed_league_repository,
            fetch_all_leagues=self.get_all_leagues,
            fetch_league_teams=self.get_league_teams,
            team_sync_service=team_sync_service,
        )
        self._league_sync_upsert_impl = self.league_sync_service.upsert_league
        self.league_sync_service.upsert_league = self._upsert_league_bridge
        self.league_repository = self._league_repository
        self.allowed_league_repository = self._allowed_league_repository

    async def _upsert_league_bridge(
        self,
        db: AsyncSession,
        league_data: dict,
        allowed_ids: set[int] | None = None,
    ) -> League | None:
        # Preserve compatibility so service overrides still intercept sync writes.
        return await self.upsert_league(db, league_data, allowed_ids=allowed_ids)

    @staticmethod
    def _ensure_repository_write_compat(repository) -> None:
        if hasattr(repository, "upsert_one"):
            return

        async def _compat_upsert_one(db: AsyncSession, row: dict) -> League:
            league_id = int(row["league_id"])
            existing = await repository.get_by_id(db, league_id)
            if existing:
                existing.name = row["name"]
                existing.country = row["country"]
                existing.country_code = row.get("country_code")
                existing.logo = row.get("logo")
                existing.season = row.get("season")
                existing.is_featured = bool(
                    row.get("is_featured", existing.is_featured)
                )
                existing.display_order = int(
                    row.get("display_order", existing.display_order)
                )
                if db is not None:
                    await db.flush()
                return existing

            new_league = League(
                league_id=league_id,
                name=row["name"],
                country=row.get("country"),
                country_code=row.get("country_code"),
                logo=row.get("logo"),
                season=row.get("season"),
                is_featured=bool(row.get("is_featured", False)),
                display_order=int(row.get("display_order", 999)),
            )
            if db is not None:
                db.add(new_league)
                await db.flush()
                if hasattr(db, "refresh"):
                    await db.refresh(new_league)
            return new_league

        setattr(repository, "upsert_one", _compat_upsert_one)

    @property
    def league_repository(self):
        return self._league_repository

    @league_repository.setter
    def league_repository(self, value):
        self._ensure_repository_write_compat(value)
        self._league_repository = value
        self.league_sync_service.league_repository = value

    @property
    def allowed_league_repository(self):
        return self._allowed_league_repository

    @allowed_league_repository.setter
    def allowed_league_repository(self, value):
        self._allowed_league_repository = value
        self.league_sync_service.allowed_league_repository = value

    async def get_cached_league_top_scorers(
        self,
        league_id: int,
        season: int,
    ) -> Optional[dict]:
        from app.cache import make_cache_key
        from app.db import async_session

        async with async_session() as db:
            master = await self._league_repository.get_by_id(db, league_id)
        if master is None or master.provider_id is None:
            return {"error": "League not found"}

        cache_key = make_cache_key("league", league_id, "topscorers", season)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        result = await self.league_provider.get_league_top_scorers(
            master.provider_id,
            season,
        )
        if not result or "response" not in result or not result["response"]:
            return {"error": "Top scorers not found"}

        payload = {
            "league_id": league_id,
            "season": season,
            "players": [
                {
                    "player_id": item.get("player", {}).get("id"),
                    "player_name": item.get("player", {}).get("name"),
                    "team_id": item.get("statistics", [{}])[0]
                    .get("team", {})
                    .get("id")
                    if isinstance(item.get("statistics"), list)
                    and item.get("statistics")
                    else None,
                    "team_name": item.get("statistics", [{}])[0]
                    .get("team", {})
                    .get("name")
                    if isinstance(item.get("statistics"), list)
                    and item.get("statistics")
                    else None,
                    "goals": item.get("statistics", [{}])[0]
                    .get("goals", {})
                    .get("total")
                    if isinstance(item.get("statistics"), list)
                    and item.get("statistics")
                    else None,
                    "assists": item.get("statistics", [{}])[0]
                    .get("goals", {})
                    .get("assists")
                    if isinstance(item.get("statistics"), list)
                    and item.get("statistics")
                    else None,
                    "appearances": item.get("statistics", [{}])[0]
                    .get("games", {})
                    .get("appearences")
                    if isinstance(item.get("statistics"), list)
                    and item.get("statistics")
                    else None,
                    "photo": item.get("player", {}).get("photo"),
                }
                for item in result["response"]
                if isinstance(item, dict)
            ],
        }
        await self.cache_service.set_json(
            cache_key,
            payload,
            settings.REDIS_TTL_LEAGUE_TOP_SCORERS,
        )
        return payload

    async def get_league_details(self, league_id: int) -> Optional[dict]:
        return await self.league_provider.get_league_details(league_id)

    async def get_all_leagues(self) -> Optional[dict]:
        return await self.league_provider.get_all_leagues()

    async def get_league_teams(self, league_id: int, season: int) -> Optional[list[dict]]:
        return await self.league_provider.get_league_teams(league_id, season)

    async def register_league(
        self,
        db: AsyncSession,
        provider: str,
        provider_id: int,
    ) -> tuple[League, bool]:
        if provider != "api-football":
            raise ValueError("Unsupported league provider")
        if (
            not isinstance(provider_id, int)
            or isinstance(provider_id, bool)
            or provider_id <= 0
        ):
            raise ValueError("provider_id must be a positive integer")

        provider_id_text = str(provider_id)
        existing = await self._league_repository.find_by_provider_identity(
            db,
            provider,
            provider_id_text,
        )
        if existing is not None:
            return existing, False

        local_collision = await self._league_repository.get_by_id(db, provider_id)
        if local_collision is not None:
            raise ValueError("Canonical local league_id is already occupied")

        result = await self.league_provider.get_league_details(provider_id)
        if result is None:
            raise ConnectionError("League provider is unavailable")
        if "response" not in result or not result["response"]:
            raise LookupError("Provider league not found")

        payload = result["response"][0]
        league_payload = payload.get("league") or payload
        if str(league_payload.get("id")) != provider_id_text:
            raise ValueError("Provider response identity does not match provider_id")
        name = league_payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Provider league metadata is missing a name")

        country_payload = payload.get("country")
        if isinstance(country_payload, dict):
            country = country_payload.get("name")
            country_code = country_payload.get("code")
        else:
            country = country_payload or league_payload.get("country")
            country_code = league_payload.get("country_code")

        country_result = await self.country_sync_service.sync_country(
            db,
            {
                "name": country,
                "code": country_code,
            },
            source="league_registration",
        ) if country else {"country": None}
        country_record = country_result.get("country") if isinstance(country_result, dict) else None
        country_id = country_record.get("country_id") if isinstance(country_record, dict) else None

        row = {
            "league_id": provider_id,
            "provider": provider,
            "provider_id": provider_id_text,
            "name": name.strip(),
            "country": country,
            "country_code": country_code,
            "logo": league_payload.get("logo"),
            "type": league_payload.get("type"),
            "national": league_payload.get("national"),
            "country_id": country_id,
            "season": None,
            "is_featured": False,
            "display_order": 999,
        }
        return await self._league_repository.create_registered(db, row), True

    async def upsert_league(
        self,
        db: AsyncSession,
        league_data: dict,
        allowed_ids: set[int] | None = None,
    ) -> League | None:
        return await self._league_sync_upsert_impl(
            db,
            league_data,
            allowed_ids=allowed_ids,
        )

    async def sync_all_leagues(self, db: AsyncSession) -> dict:
        return await self.league_sync_service.sync_all_leagues(db)
