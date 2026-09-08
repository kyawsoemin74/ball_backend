"""Frozen Analytics V1 projections from canonical source payloads."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
import re
import logging
from typing import Any

from app.repositories.analytics_repository import (
    AnalyticsH2HRepository,
    AnalyticsLineupRepository,
    AnalyticsOddsRepository,
    AnalyticsStandingRepository,
    AnalyticsStatisticsRepository,
)
from app.repositories.league_season_repository import LeagueSeasonRepository
from app.repositories.team_repository import TeamRepository

logger = logging.getLogger(__name__)


def log_projection_failure(fact: str, scope: dict, failure_type: str) -> None:
    logger.error(
        "ANALYTICS_PROJECTION_FAILURE",
        extra={
            "fact": fact,
            "scope": scope,
            "source_count": None,
            "accepted_count": None,
            "rejected_count": None,
            "duplicate_count": None,
            "unresolved_count": None,
            "orphan_count": None,
            "analytics_count": None,
            "success": False,
            "failure_type": failure_type,
            "transaction_outcome": "rollback_required",
        },
    )


def _projection_observer(fact: str, scope_builder):
    def decorator(method):
        @wraps(method)
        async def observed(self, db, *args, **kwargs):
            scope = scope_builder(args, kwargs)
            try:
                result = await method(self, db, *args, **kwargs)
            except Exception as exc:
                log_projection_failure(fact, scope, exc.__class__.__name__)
                raise

            result["fact"] = fact
            result["transaction_outcome"] = "pending_outer_transaction"
            logger.log(
                logging.INFO if result.get("success") else logging.ERROR,
                "ANALYTICS_PROJECTION_RESULT",
                extra=result,
            )
            return result

        return observed

    return decorator


def log_projection_transaction(result: dict, outcome: str) -> None:
    """Report the outer transaction outcome without owning the transaction."""
    payload = dict(result)
    payload["transaction_outcome"] = outcome
    logger.info("ANALYTICS_PROJECTION_TRANSACTION", extra=payload)


@dataclass
class ProjectionResult:
    source_count: int = 0
    analytics_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0
    unresolved_count: int = 0
    orphan_count: int = 0
    scope: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @staticmethod
    def unavailable_for_fake_db(exc: AttributeError) -> bool:
        # Legacy unit-test sessions do not implement SQLAlchemy result APIs.
        return True


class AnalyticsProjectionService:
    """Transforms and replaces Analytics facts without transaction ownership."""

    @staticmethod
    def unavailable_for_fake_db(exc: AttributeError) -> bool:
        return True

    def __init__(self, *, team_repository=None, player_repository=None, league_season_repository=None):
        self.team_repository = team_repository or TeamRepository()
        self.league_season_repository = league_season_repository or LeagueSeasonRepository()
        self.statistics_repository = AnalyticsStatisticsRepository()
        self.standing_repository = AnalyticsStandingRepository()
        self.odds_repository = AnalyticsOddsRepository()
        self.h2h_repository = AnalyticsH2HRepository()
        self.lineup_repository = AnalyticsLineupRepository()

    @staticmethod
    def _result(scope, source_count, accepted, rows, **kwargs):
        result = ProjectionResult(
            source_count=source_count,
            analytics_count=len(rows),
            accepted_count=accepted,
            scope=scope,
            success=accepted == source_count and not any(kwargs.get(name, 0) for name in ("rejected_count", "duplicate_count", "unresolved_count", "orphan_count")),
            **kwargs,
        )
        return result.as_dict()

    async def _persisted_result(self, db, result: dict, rows: list, fetch_rows) -> dict:
        if db is None:
            persisted_count = len(rows)
        else:
            await db.flush()
            persisted_count = len(await fetch_rows())
        result["analytics_count"] = persisted_count
        if (
            persisted_count != result["accepted_count"]
            or result["rejected_count"]
            or result["duplicate_count"]
            or result["unresolved_count"]
            or result["orphan_count"]
        ):
            result["success"] = False
            result["reason"] = "persisted_reconciliation_failed"
            raise ValueError("Analytics persisted reconciliation failed")
        return result

    @staticmethod
    def _typed_value(value: Any) -> tuple[str, dict[str, Any]]:
        if isinstance(value, bool):
            return "boolean", {"value_boolean": value}
        if isinstance(value, int) and not isinstance(value, bool):
            return "integer", {"value_integer": value}
        if isinstance(value, float) or isinstance(value, Decimal):
            return "decimal", {"value_numeric": Decimal(str(value))}
        if value is None:
            raise ValueError("statistic value is required")
        text = str(value).strip()
        if not text:
            raise ValueError("statistic value is empty")
        if text.endswith("%"):
            try:
                return "percent", {"value_numeric": Decimal(text[:-1].strip())}
            except InvalidOperation as exc:
                raise ValueError("invalid percentage") from exc
        try:
            return ("decimal", {"value_numeric": Decimal(text)}) if "." in text else ("integer", {"value_integer": int(text)})
        except (InvalidOperation, ValueError):
            return "text", {"value_text": text}

    @staticmethod
    def _normalize_statistic_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "statistic"

    @_projection_observer("statistics", lambda args, kwargs: {"match_id": args[0] if args else kwargs.get("match_id")})
    async def project_statistics(self, db, match_id: int, payload: Any) -> dict:
        scope = {"match_id": match_id}
        entries = payload.get("response", []) if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            raise ValueError("invalid statistics response")
        flattened = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("team"), dict):
                raise ValueError("statistics team identity is required")
            provider_team_id = entry["team"].get("id")
            if provider_team_id is None or not isinstance(entry.get("statistics"), list):
                raise ValueError("invalid statistics entry")
            team = await self.team_repository.find_by_provider_identity(db, "api-football", provider_team_id)
            if team is None:
                raise ValueError("unresolved provider Team identity in statistics")
            for stat in entry["statistics"]:
                if not isinstance(stat, dict):
                    raise ValueError("invalid statistic row")
                name = str(stat.get("type") or stat.get("name") or "").strip()
                if not name:
                    raise ValueError("statistic name is required")
                value_type, typed = self._typed_value(stat.get("value"))
                flattened.append({"team_id": int(team.team_id), "provider_team_id": str(provider_team_id), "statistic_name": self._normalize_statistic_name(name), "statistic_label": name, "value_type": value_type, **typed, "source_provider": "api-football"})
        if not flattened:
            existing = await self.statistics_repository.list_by_match(db, match_id)
            return self._result(scope, 0, 0, existing, reason="empty_preserved")
        keys = [(row["team_id"], row["statistic_name"]) for row in flattened]
        duplicates = len(keys) - len(set(keys))
        if duplicates:
            return self._result(scope, len(flattened), 0, [], duplicate_count=duplicates, reason="duplicate_source_rejected")
        await self.statistics_repository.replace_by_match(db, match_id, flattened)
        result = self._result(scope, len(flattened), len(flattened), flattened)
        return await self._persisted_result(
            db, result, flattened,
            lambda: self.statistics_repository.list_by_match(db, match_id),
        )

    @_projection_observer("standing", lambda args, kwargs: {"league_id": args[0] if args else kwargs.get("league_id"), "season": str(args[1] if len(args) > 1 else kwargs.get("season"))})
    async def project_standing(self, db, league_id: int, season: str | int, standings: list[dict]) -> dict:
        scope = {"league_season_id": None, "league_id": league_id, "season": str(season)}
        league_season = await self.league_season_repository.get_by_league_and_season(db, league_id, season)
        if league_season is None:
            raise ValueError("unresolved League Season identity")
        scope["league_season_id"] = int(league_season.id)
        rows = []
        seen = set()
        for standing in standings:
            team_data = standing.get("team") if isinstance(standing, dict) else None
            provider_id = team_data.get("id") if isinstance(team_data, dict) else None
            if provider_id is None:
                raise ValueError("unresolved Team identity in standing")
            team = await self.team_repository.find_by_provider_identity(db, "api-football", provider_id)
            if team is None:
                raise ValueError("unresolved Team identity in standing")
            key = int(team.team_id)
            if key in seen:
                return self._result(scope, len(standings), 0, [], duplicate_count=1, reason="duplicate_source_rejected")
            seen.add(key)
            all_stats = standing.get("all") or {}
            goals = all_stats.get("goals") or {}
            rows.append({"league_id": league_id, "team_id": key, "provider_team_id": str(provider_id), "position": standing["rank"], "points": standing["points"], "played": all_stats["played"], "won": all_stats["win"], "drawn": all_stats["draw"], "lost": all_stats["lose"], "goals_for": goals.get("for", 0), "goals_against": goals.get("against", 0), "goal_difference": standing.get("goalsDiff", goals.get("for", 0) - goals.get("against", 0)), "group_name": standing.get("group"), "form": standing.get("form"), "description": standing.get("description"), "source_provider": "api-football"})
        await self.standing_repository.replace_by_season(db, int(league_season.id), rows)
        result = self._result(scope, len(standings), len(rows), rows)
        return await self._persisted_result(
            db, result, rows,
            lambda: self.standing_repository.list_by_season(db, int(league_season.id)),
        )

    @_projection_observer("odds", lambda args, kwargs: {"match_id": args[0] if args else kwargs.get("match_id")})
    async def project_odds(self, db, match_id: int, rows: list[dict]) -> dict:
        scope = {"match_id": match_id}
        approved_bookmakers = {"1xbet", "1xbet"}
        approved_markets = {"Match Winner", "Asian Handicap", "Goals Over/Under", "Both Teams Score"}
        source = [row for row in rows if str(row.get("bookmaker_name", "")).strip().lower() in approved_bookmakers and row.get("market_name") in approved_markets]
        projected = []
        seen = set()
        for row in source:
            try:
                odd = Decimal(str(row["odd_value"]))
            except (KeyError, InvalidOperation, ValueError) as exc:
                raise ValueError("invalid odd") from exc
            if odd <= 0:
                raise ValueError("invalid odd")
            key = (row.get("bookmaker_name"), row.get("market_name"), row.get("selection"))
            if key in seen:
                return self._result(scope, len(source), 0, [], duplicate_count=1, reason="duplicate_source_rejected")
            seen.add(key)
            projected.append({"bookmaker_name": row["bookmaker_name"], "market_name": row["market_name"], "selection": row["selection"], "odd_value": odd, "myanmar_odd": row.get("myanmar_odd"), "source_provider": "api-football"})
        if not projected:
            existing = await self.odds_repository.list_by_match(db, match_id)
            return self._result(scope, 0, 0, existing, reason="filtered_to_zero_preserved")
        await self.odds_repository.replace_by_match(db, match_id, projected)
        result = self._result(scope, len(source), len(projected), projected)
        return await self._persisted_result(
            db, result, projected,
            lambda: self.odds_repository.list_by_match(db, match_id),
        )

    @_projection_observer("lineup", lambda args, kwargs: {"match_id": args[0] if args else kwargs.get("match_id")})
    async def project_lineup(self, db, match_id: int, payload: list[dict]) -> dict:
        scope = {"match_id": match_id}
        projected = []
        seen = set()
        for lineup in payload:
            team_data = lineup.get("team") if isinstance(lineup, dict) else None
            provider_team_id = team_data.get("id") if isinstance(team_data, dict) else None
            team = await self.team_repository.find_by_provider_identity(db, "api-football", provider_team_id)
            if team is None:
                raise ValueError("unresolved Team identity in lineup")
            for section, role in (("startXI", "STARTER"), ("substitutes", "SUBSTITUTE")):
                players = lineup.get(section)
                if not isinstance(players, list):
                    raise ValueError("invalid lineup section")
                for entry in players:
                    player_data = entry.get("player") if isinstance(entry, dict) else None
                    player_id = player_data.get("player_id") if isinstance(player_data, dict) else None
                    if player_id is None:
                        raise ValueError("IDENTITY_BOUNDARY_VIOLATION: unresolved Player canonical player_id is required")
                    key = (int(team.team_id), int(player_id), role)
                    if key in seen:
                        return self._result(scope, len(projected) + 1, 0, [], duplicate_count=1, reason="duplicate_source_rejected")
                    seen.add(key)
                    projected.append({"team_id": key[0], "player_id": key[1], "provider_team_id": str(provider_team_id), "provider_player_id": str(player_data.get("id")), "roster_role": role, "shirt_number": player_data.get("number"), "position": player_data.get("pos"), "grid": player_data.get("grid"), "formation": lineup.get("formation"), "source_provider": "api-football"})
        if not projected:
            existing = await self.lineup_repository.list_by_match(db, match_id)
            return self._result(scope, 0, 0, existing, reason="empty_preserved")
        await self.lineup_repository.replace_by_match(db, match_id, projected)
        result = self._result(scope, len(projected), len(projected), projected)
        return await self._persisted_result(
            db, result, projected,
            lambda: self.lineup_repository.list_by_match(db, match_id),
        )

    @_projection_observer("h2h", lambda args, kwargs: {"provider_team_low_id": args[0] if args else kwargs.get("provider_team_low_id"), "provider_team_high_id": args[1] if len(args) > 1 else kwargs.get("provider_team_high_id")})
    async def project_h2h(self, db, provider_team_low_id: int, provider_team_high_id: int, fixtures: list[dict]) -> dict:
        low = await self.team_repository.find_by_provider_identity(db, "api-football", provider_team_low_id)
        high = await self.team_repository.find_by_provider_identity(db, "api-football", provider_team_high_id)
        if low is None or high is None:
            raise ValueError("unresolved Team identity in H2H")
        local_low, local_high = sorted((int(low.team_id), int(high.team_id)))
        rows = []
        seen = set()
        for fixture in fixtures:
            info = fixture.get("fixture") or {}
            fixture_id = info.get("id")
            if fixture_id is None or fixture_id in seen:
                return self._result({"team_low_id": local_low, "team_high_id": local_high}, len(fixtures), 0, [], duplicate_count=1, reason="duplicate_source_rejected")
            seen.add(fixture_id)
            timestamp = info.get("timestamp")
            fixture_at = datetime.fromtimestamp(timestamp, timezone.utc) if isinstance(timestamp, (int, float)) else datetime.fromisoformat(str(info.get("date")).replace("Z", "+00:00"))
            teams = fixture.get("teams") or {}
            goals = fixture.get("goals") or {}
            score = fixture.get("score") or {}
            rows.append({"source_provider": "api-football", "source_fixture_id": int(fixture_id), "fixture_at": fixture_at, "fixture_status": str((info.get("status") or {}).get("short", "")), "home_provider_team_id": int((teams.get("home") or {}).get("id")), "away_provider_team_id": int((teams.get("away") or {}).get("id")), "home_goals": goals.get("home"), "away_goals": goals.get("away"), "halftime_home_score": (score.get("halftime") or {}).get("home"), "halftime_away_score": (score.get("halftime") or {}).get("away"), "fulltime_home_score": (score.get("fulltime") or {}).get("home"), "fulltime_away_score": (score.get("fulltime") or {}).get("away"), "extratime_home_score": (score.get("extratime") or {}).get("home"), "extratime_away_score": (score.get("extratime") or {}).get("away"), "penalty_home_score": (score.get("penalty") or {}).get("home"), "penalty_away_score": (score.get("penalty") or {}).get("away")})
        await self.h2h_repository.replace_by_pair(db, local_low, local_high, rows)
        result = self._result({"team_low_id": local_low, "team_high_id": local_high}, len(fixtures), len(rows), rows)
        return await self._persisted_result(
            db, result, rows,
            lambda: self.h2h_repository.list_by_pair(db, local_low, local_high),
        )
