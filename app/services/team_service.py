import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.match import Match
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

    @staticmethod
    def _normalize_fixture_result(fixture: dict, team_id: int) -> Optional[str]:
        try:
            home_id = int(fixture.get("teams", {}).get("home", {}).get("id") or 0)
            away_id = int(fixture.get("teams", {}).get("away", {}).get("id") or 0)
            home_score = int(fixture.get("goals", {}).get("home") or 0)
            away_score = int(fixture.get("goals", {}).get("away") or 0)
        except (TypeError, ValueError):
            return None

        if fixture.get("fixture", {}).get("status", {}).get("short") not in {"FT", "AET", "PEN"}:
            return None

        if team_id == home_id:
            return "W" if home_score > away_score else "D" if home_score == away_score else "L"
        if team_id == away_id:
            return "W" if away_score > home_score else "D" if home_score == away_score else "L"
        return None

    @staticmethod
    def _normalize_fixture_item(fixture: dict, team_id: int) -> dict[str, Any]:
        info = fixture.get("fixture", {}) or {}
        league = fixture.get("league", {}) or {}
        teams = fixture.get("teams", {}) or {}
        goals = fixture.get("goals", {}) or {}
        return {
            "match_id": info.get("id"),
            "date": info.get("date"),
            "league_id": league.get("id"),
            "league_name": league.get("name"),
            "home_team_id": teams.get("home", {}).get("id"),
            "home_team": teams.get("home", {}).get("name"),
            "away_team_id": teams.get("away", {}).get("id"),
            "away_team": teams.get("away", {}).get("name"),
            "home_score": goals.get("home"),
            "away_score": goals.get("away"),
            "status": info.get("status", {}).get("short"),
            "result": TeamService._normalize_fixture_result(fixture, team_id),
        }

    async def get_team_details(self, team_id: int) -> Optional[dict]:
        return await self.client.get("/teams", params={"id": team_id})

    @staticmethod
    def _normalize_db_fixture_item(match: Match, team_id: int) -> dict[str, Any]:
        home_team_id = getattr(match, "home_team_id", None)
        away_team_id = getattr(match, "away_team_id", None)
        home_score = getattr(match, "home_score", None)
        away_score = getattr(match, "away_score", None)
        status = getattr(match, "status", None)
        match_time = getattr(match, "match_time", None)
        if hasattr(match_time, "isoformat"):
            match_time_value = match_time.isoformat()
        else:
            match_time_value = match_time

        result = None
        if status in {"FT", "AET", "PEN"}:
            if team_id == home_team_id:
                if home_score > away_score:
                    result = "W"
                elif home_score == away_score:
                    result = "D"
                else:
                    result = "L"
            elif team_id == away_team_id:
                if away_score > home_score:
                    result = "W"
                elif home_score == away_score:
                    result = "D"
                else:
                    result = "L"

        return {
            "match_id": getattr(match, "match_id", None),
            "date": match_time_value,
            "league_id": getattr(match, "league_id", None),
            "league_name": getattr(match, "league_name", None),
            "home_team_id": home_team_id,
            "home_team": getattr(match, "home_team", None),
            "away_team_id": away_team_id,
            "away_team": getattr(match, "away_team", None),
            "home_score": home_score,
            "away_score": away_score,
            "status": status,
            "result": result,
        }

    async def get_cached_team_fixtures(self, db: AsyncSession, team_id: int) -> Optional[dict]:
        cache_key = make_cache_key("team", team_id, "fixtures")
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        recent_query = (
            select(Match)
            .where((Match.home_team_id == team_id) | (Match.away_team_id == team_id))
            .where(Match.status.in_(["FT", "AET", "PEN"]))
            .order_by(Match.match_time.desc())
            .limit(10)
        )
        upcoming_query = (
            select(Match)
            .where((Match.home_team_id == team_id) | (Match.away_team_id == team_id))
            .where(Match.status == "NS")
            .order_by(Match.match_time.asc())
            .limit(10)
        )

        recent_result = await db.execute(recent_query)
        upcoming_result = await db.execute(upcoming_query)

        recent = [self._normalize_db_fixture_item(item, team_id) for item in recent_result.scalars().all()]
        upcoming = [self._normalize_db_fixture_item(item, team_id) for item in upcoming_result.scalars().all()]

        payload = {"team_id": team_id, "recent": recent, "upcoming": upcoming}
        await self.cache_service.set_json(cache_key, payload, 21600)
        return payload

    async def get_cached_team_squad(self, team_id: int) -> Optional[dict]:
        from app.cache import make_cache_key

        cache_key = make_cache_key("team", team_id, "squad")
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        result = await self.client.get("/players/squads", params={"team": team_id})
        if not result or "response" not in result or not result["response"]:
            return {"team_id": team_id, "team_name": None, "players": []}

        team_data = result["response"][0] if isinstance(result["response"], list) else {}
        players = []
        for player in (team_data.get("players") or []):
            if isinstance(player, dict):
                players.append({
                    "player_id": player.get("id"),
                    "player_name": player.get("name"),
                    "age": player.get("age"),
                    "nationality": player.get("nationality"),
                    "position": player.get("position"),
                    "photo": player.get("photo"),
                })

        payload = {
            "team_id": team_id,
            "team_name": team_data.get("team", {}).get("name") if isinstance(team_data.get("team"), dict) else None,
            "players": players,
        }
        await self.cache_service.set_json(cache_key, payload, 86400)
        return payload

    async def get_cached_team_statistics(self, team_id: int, league_id: int, season: int) -> Optional[dict]:
        from app.cache import make_cache_key

        cache_key = make_cache_key("team", team_id, "statistics", league_id, season)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        result = await self.client.get(
            "/teams/statistics",
            params={"team": team_id, "league": league_id, "season": season},
        )
        if not result or "response" not in result or not result["response"]:
            return {"error": "Statistics not found"}

        stats = result["response"]
        if isinstance(stats, list):
            stats = stats[0] if stats else {}

        goals_for = stats.get("goals", {}).get("for", {}).get("total", {}).get("total")
        goals_against = stats.get("goals", {}).get("against", {}).get("total", {}).get("total")
        average_goals_scored = stats.get("goals", {}).get("for", {}).get("average", {}).get("total")
        average_goals_conceded = stats.get("goals", {}).get("against", {}).get("average", {}).get("total")

        payload = {
            "team_id": team_id,
            "league_id": league_id,
            "season": season,
            "played": stats.get("games", {}).get("played"),
            "wins": stats.get("games", {}).get("wins"),
            "draws": stats.get("games", {}).get("draws"),
            "losses": stats.get("games", {}).get("loses"),
            "goals_for": int(goals_for) if goals_for is not None else None,
            "goals_against": int(goals_against) if goals_against is not None else None,
            "clean_sheets": stats.get("clean_sheet", {}).get("home") if isinstance(stats.get("clean_sheet"), dict) else None,
            "failed_to_score": stats.get("failed_to_score", {}).get("home") if isinstance(stats.get("failed_to_score"), dict) else None,
            "average_goals_scored": float(average_goals_scored) if average_goals_scored is not None else None,
            "average_goals_conceded": float(average_goals_conceded) if average_goals_conceded is not None else None,
        }
        await self.cache_service.set_json(cache_key, payload, 86400)
        return payload

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
