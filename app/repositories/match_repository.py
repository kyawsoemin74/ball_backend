from datetime import date, datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.league import League
from app.models.match import Match


FINAL_MATCH_TERMINAL_STATUSES = frozenset({"FT", "AET", "PEN"})
MATCH_RESULT_LOCK_RESOURCE = "match_result"
FINALIZED_RESULT_FIELDS = ("status", "elapsed", "home_score", "away_score")
FINALIZED_METADATA_PROTECTED_FIELDS = (
    "league_id",
    "season",
    "match_time",
    "home_team_id",
    "away_team_id",
    "referee_id",
)


class MatchStateWriteOutcome(str, Enum):
    UPDATED = "updated"
    PROTECTED = "protected"
    MISSING = "missing"


def _is_finalized_match_status(status_column):
    return func.upper(func.trim(func.coalesce(status_column, ""))).in_(
        tuple(FINAL_MATCH_TERMINAL_STATUSES)
    )


def finalized_result_upsert_values(existing_columns, excluded_columns) -> dict:
    """Keep finalized result fields intact on non-authoritative fixture upserts."""
    is_finalized = _is_finalized_match_status(existing_columns.status)
    return {
        field: case(
            (is_finalized, getattr(existing_columns, field)),
            else_=getattr(excluded_columns, field),
        )
        for field in FINALIZED_RESULT_FIELDS
    }


def finalized_metadata_upsert_values(
    existing_columns,
    excluded_columns,
    *,
    fields: tuple[str, ...] = FINALIZED_METADATA_PROTECTED_FIELDS,
) -> dict:
    """Keep finalized match identity and scheduling fields while refreshing display metadata."""
    is_finalized = _is_finalized_match_status(existing_columns.status)
    return {
        field: case(
            (is_finalized, getattr(existing_columns, field)),
            else_=getattr(excluded_columns, field),
        )
        for field in fields
    }


class MatchRepository:
    async def get_by_provider_fixture_id(self, db: AsyncSession, provider: str, provider_fixture_id: int) -> Match | None:
        result = await db.execute(
            select(Match).where(
                Match.provider == provider,
                Match.provider_fixture_id == int(provider_fixture_id),
            )
        )
        return result.scalar_one_or_none()

    async def update_live_state(
        self,
        db: AsyncSession,
        local_match_id: int,
        *,
        status: str,
        elapsed: int,
        home_score: int,
        away_score: int,
        allow_finalized_result_refresh: bool = False,
    ) -> MatchStateWriteOutcome:
        result = await db.execute(
            text(
                """
                UPDATE matches
                SET status = :status,
                    elapsed = :elapsed,
                    home_score = :home_score,
                    away_score = :away_score
                WHERE local_match_id = :local_match_id
                  AND (
                    :allow_finalized_result_refresh
                    OR UPPER(TRIM(COALESCE(status, ''))) NOT IN (:status_ft, :status_aet, :status_pen)
                  )
                RETURNING local_match_id
                """
            ),
            {
                "local_match_id": local_match_id,
                "status": status,
                "elapsed": elapsed,
                "home_score": home_score,
                "away_score": away_score,
                "allow_finalized_result_refresh": allow_finalized_result_refresh,
                "status_ft": "FT",
                "status_aet": "AET",
                "status_pen": "PEN",
            },
        )
        if result.scalar_one_or_none() is not None:
            return MatchStateWriteOutcome.UPDATED

        current = await db.execute(
            text("SELECT status FROM matches WHERE local_match_id = :local_match_id"),
            {"local_match_id": local_match_id},
        )
        if current.scalar_one_or_none() is None:
            return MatchStateWriteOutcome.MISSING
        return MatchStateWriteOutcome.PROTECTED

    async def get_many_by_ids(self, db: AsyncSession, match_ids: list[int], allowed_ids: set[int] | None = None) -> list[Match]:
        if not match_ids:
            return []
        query = select(Match).where(Match.match_id.in_(match_ids))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_matches_by_date(self, db: AsyncSession, date_val: date, allowed_ids: set[int] | None = None) -> list[Match]:
        start_dt = (datetime.combine(date_val, datetime.min.time()) - timedelta(hours=6, minutes=30)).replace(tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)

        query = (
            select(Match)
            .join(League, Match.league_id == League.league_id)
            .options(joinedload(Match.league_obj))
            .where(Match.match_time >= start_dt)
            .where(Match.match_time < end_dt)
        )
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))

        result = await db.execute(
            query.order_by(
                League.is_featured.desc(),
                League.display_order.asc(),
                League.country.asc(),
                League.name.asc(),
                Match.match_time.asc(),
                Match.match_id.asc(),
            )
        )
        return list(result.scalars().all())

    async def get_by_id(self, db: AsyncSession, match_id: int, allowed_ids: set[int] | None = None) -> Match | None:
        query = select(Match).options(joinedload(Match.league_obj)).where(Match.match_id == match_id)
        if allowed_ids is not None:
            if not allowed_ids:
                return None
            query = query.where(Match.league_id.in_(allowed_ids))
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_live_matches(self, db: AsyncSession, allowed_ids: set[int] | None = None) -> list[Match]:
        from app.services.football import LIVE_STATUSES

        query = select(Match).where(Match.status.in_(LIVE_STATUSES))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))

        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_all_matches(self, db: AsyncSession, allowed_ids: set[int] | None = None, status: str | None = None, league_id: int | None = None, skip: int = 0, limit: int = 100) -> list[Match]:
        query = select(Match)
        if status:
            query = query.where(Match.status == status)
        if league_id:
            query = query.where(Match.league_id == league_id)
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))
        result = await db.execute(query.offset(skip).limit(limit))
        return list(result.scalars().all())

    async def get_team_matches(self, db: AsyncSession, team_id: int, allowed_ids: set[int] | None = None) -> list[Match]:
        query = select(Match).where((Match.home_team_id == team_id) | (Match.away_team_id == team_id))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))
        result = await db.execute(query.order_by(Match.match_time.asc(), Match.match_id.asc()))
        return list(result.scalars().all())

    async def get_team_matches_recent(self, db: AsyncSession, team_id: int, allowed_ids: set[int] | None = None) -> list[Match]:
        query = select(Match).where((Match.home_team_id == team_id) | (Match.away_team_id == team_id))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))
        result = await db.execute(query.order_by(Match.match_time.desc(), Match.match_id.desc()))
        return list(result.scalars().all())

    @staticmethod
    def order_matches_for_date(matches: list[Match]) -> list[Match]:
        return sorted(
            matches,
            key=lambda match: (
                0 if bool(getattr(getattr(match, "league_obj", None), "is_featured", False)) else 1,
                int(getattr(getattr(match, "league_obj", None), "display_order", 999) or 999),
                str(getattr(getattr(match, "league_obj", None), "country", "")).lower(),
                str(getattr(getattr(match, "league_obj", None), "name", "")).lower(),
                getattr(match, "match_time", None) or datetime.min.replace(tzinfo=timezone.utc),
            ),
        )

    async def get_live_stale(
        self,
        db: AsyncSession,
        provider_fixture_ids: set[int],
        stale_threshold: datetime,
        *,
        current_time: datetime | None = None,
        provider: str = "api-football",
    ) -> list[Match]:
        from app.services.football import LIVE_STATUSES

        upper_bound = current_time or datetime.now(timezone.utc)
        if (
            stale_threshold.tzinfo is None
            or stale_threshold.utcoffset() is None
            or upper_bound.tzinfo is None
            or upper_bound.utcoffset() is None
        ):
            raise ValueError("Live match recovery bounds must be timezone-aware.")

        lower_utc = stale_threshold.astimezone(timezone.utc)
        upper_utc = upper_bound.astimezone(timezone.utc)
        if lower_utc > upper_utc:
            raise ValueError("Live match recovery lower bound cannot exceed upper bound.")

        query = select(Match).where(
            Match.provider == provider,
            Match.status.in_(LIVE_STATUSES),
            Match.provider_fixture_id > 0,
            Match.match_time >= lower_utc,
            Match.match_time <= upper_utc,
        )
        if provider_fixture_ids:
            query = query.where(Match.provider_fixture_id.not_in(list(provider_fixture_ids)))
        result = await db.execute(
            query.order_by(Match.match_time.asc(), Match.local_match_id.asc())
        )
        return list(result.scalars().all())
