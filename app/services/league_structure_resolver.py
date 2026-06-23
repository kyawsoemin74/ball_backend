import logging
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import Match
from app.models.standing import Standings


logger = logging.getLogger(__name__)


class LeagueCompetitionType(str, Enum):
    REGULAR = "regular"
    KNOCKOUT = "knockout"
    GROUP_STAGE = "group_stage"
    GROUP_KNOCKOUT = "group_knockout"


@dataclass(frozen=True)
class LeagueStructureResolution:
    competition_type: LeagueCompetitionType
    has_standings: bool
    is_knockout: bool
    has_bracket: bool


class LeagueStructureResolver:
    KNOCKOUT_KEYWORDS = ("cup", "knockout", "playoff", "final", "semi")

    def __init__(self) -> None:
        self._resolver_calls = 0
        self._match_season_used = 0
        self._league_season_fallback_used = 0

    def get_metrics(self) -> dict[str, int]:
        return {
            "resolver_calls": self._resolver_calls,
            "match_season_used": self._match_season_used,
            "league_season_fallback_used": self._league_season_fallback_used,
        }

    def reset_metrics(self) -> None:
        self._resolver_calls = 0
        self._match_season_used = 0
        self._league_season_fallback_used = 0

    async def resolve(self, match: Match, db: AsyncSession) -> LeagueStructureResolution:
        self._resolver_calls += 1

        fixture_id = getattr(match, "match_id", None)
        league_id = getattr(match, "league_id", None)
        match_season = getattr(match, "season", None)
        league_season = getattr(getattr(match, "league_obj", None), "season", None)

        standings_season = match_season
        if standings_season is not None:
            self._match_season_used += 1
            logger.info(
                "MATCH_SEASON_USED fixture_id=%s league_id=%s match_season=%s league_season=%s",
                fixture_id,
                league_id,
                match_season,
                league_season,
            )
        elif league_season is not None:
            standings_season = league_season
            self._league_season_fallback_used += 1
            logger.info(
                "LEAGUE_SEASON_FALLBACK_USED fixture_id=%s league_id=%s match_season=%s league_season=%s",
                fixture_id,
                league_id,
                match_season,
                league_season,
            )

        league_name = (match.league_name or "").lower()
        knockout_hint = any(keyword in league_name for keyword in self.KNOCKOUT_KEYWORDS)

        has_standings = False
        has_groups = False

        if standings_season is not None:
            # Standings must be scoped by both league and season because league IDs
            # are reused across years; league-only checks leak previous-season data.
            result = await db.execute(
                select(Standings.id, Standings.group_name)
                .where(
                    Standings.league_id == match.league_id,
                    Standings.season == str(standings_season),
                )
                .order_by(Standings.group_name.is_(None), Standings.position)
                .limit(1)
            )
            row = result.first() if hasattr(result, "first") else result.scalar_one_or_none()
            has_standings = row is not None
            has_groups = bool(row and getattr(row, "group_name", None))

        if has_groups and knockout_hint:
            competition_type = LeagueCompetitionType.GROUP_KNOCKOUT
        elif has_groups:
            competition_type = LeagueCompetitionType.GROUP_STAGE
        elif knockout_hint:
            competition_type = LeagueCompetitionType.KNOCKOUT
        else:
            competition_type = LeagueCompetitionType.REGULAR

        # Keep bracket availability conservative until a dedicated bracket source
        # is wired in; the resolver still owns the decision so future support stays local.
        has_bracket = False

        return LeagueStructureResolution(
            competition_type=competition_type,
            has_standings=has_standings,
            is_knockout=competition_type in {
                LeagueCompetitionType.KNOCKOUT,
                LeagueCompetitionType.GROUP_KNOCKOUT,
            },
            has_bracket=has_bracket,
        )
