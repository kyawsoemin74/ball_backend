import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.team import Team
from app.repositories.team_repository import TeamRepository
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)


class TeamService:
    def __init__(self, client: FootballAPIClient, cache_service: CacheService | None = None) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()
        self.team_repository = TeamRepository()

    async def get_team_details(self, team_id: int) -> Optional[dict]:
        return await self.client.get("/teams", params={"id": team_id})

    async def ensure_teams_exist(self, db: AsyncSession, teams_data: list[dict]) -> dict:
        """Ensure referenced teams exist in the database with one read + bulk insert."""
        if not teams_data:
            return {"created": 0, "existing": 0, "total": 0}

        normalized_teams = []
        seen_team_ids = set()

        for item in teams_data:
            if not isinstance(item, dict):
                logger.warning("Skipping invalid team payload: %r", item)
                continue

            team_id = item.get("team_id", item.get("id"))
            name = item.get("name")

            if team_id is None:
                logger.warning("Skipping team payload without team_id: %r", item)
                continue
            if not name:
                logger.warning("Skipping team payload without name for team_id=%s", team_id)
                continue
            if team_id in seen_team_ids:
                logger.warning("Skipping duplicate team_id=%s in request payload", team_id)
                continue

            seen_team_ids.add(team_id)
            normalized_teams.append({
                "team_id": int(team_id),
                "name": str(name),
                "country": item.get("country"),
                "logo": item.get("logo"),
                "stadium": item.get("stadium"),
                "founded": item.get("founded"),
            })

        if not normalized_teams:
            return {"created": 0, "existing": 0, "total": 0}

        existing_teams = await self.team_repository.get_many_by_ids(db, [t["team_id"] for t in normalized_teams])
        existing_ids = {team.team_id for team in existing_teams}
        missing_teams = [team for team in normalized_teams if team["team_id"] not in existing_ids]

        if missing_teams:
            db.add_all([
                Team(
                    team_id=team["team_id"],
                    name=team["name"],
                    country=team.get("country"),
                    logo=team.get("logo"),
                    stadium=team.get("stadium"),
                    founded=team.get("founded"),
                )
                for team in missing_teams
            ])
            await db.flush()

        created = len(missing_teams)
        existing = len(normalized_teams) - created
        logger.info("ensure_teams_exist: created=%s, existing=%s", created, existing)
        return {"created": created, "existing": existing, "total": len(normalized_teams)}

    async def upsert_team(self, db: AsyncSession, team_data: dict) -> Team:
        team_id = team_data["team"]["id"]
        existing = await self.team_repository.get_by_id(db, team_id)

        if existing:
            existing.name = team_data["team"]["name"]
            existing.country = team_data["team"].get("country")
            existing.logo = team_data["team"].get("logo")
            existing.stadium = team_data.get("venue", {}).get("name") if team_data.get("venue") else None
            existing.founded = team_data["team"].get("founded")
            await db.flush()
            self.cache_service.delete_sync(make_cache_key("team", team_id))
            return existing

        new_team = Team(
            team_id=team_id,
            name=team_data["team"]["name"],
            country=team_data["team"].get("country"),
            logo=team_data["team"].get("logo"),
            stadium=team_data.get("venue", {}).get("name") if team_data.get("venue") else None,
            founded=team_data["team"].get("founded"),
        )
        db.add(new_team)
        await db.flush()
        self.cache_service.delete_sync(make_cache_key("team", team_id))
        return new_team
