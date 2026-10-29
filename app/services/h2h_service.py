import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.match import Match
from app.models.match_h2h import MatchH2H
from app.providers.h2h_provider import H2HProvider
from app.repositories.team_repository import (
    AmbiguousProviderIdentityError,
    TeamRepository,
)
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.h2h_sync_service import H2HSyncService

logger = logging.getLogger(__name__)

H2H_SUMMARY_STATUSES = {"FT", "AET", "PEN", "AWD", "WO"}


class H2HTeamIdentityUnavailable(ValueError):
    pass


class H2HService:
    def __init__(
        self,
        client: FootballAPIClient,
        cache_service: CacheService | None = None,
        h2h_provider: H2HProvider | None = None,
        h2h_sync_service: H2HSyncService | None = None,
    ) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()
        self.h2h_provider = h2h_provider or H2HProvider(client)
        self.h2h_sync_service = h2h_sync_service or H2HSyncService(self, cache_service=self.cache_service, h2h_provider=self.h2h_provider)

    def _prepare_h2h_payload(self, payload: Optional[list[dict]]) -> Optional[list[dict]]:
        if not isinstance(payload, list):
            return payload

        def sort_key(item: object) -> float:
            if not isinstance(item, dict):
                return float("-inf")

            fixture = item.get("fixture")
            if not isinstance(fixture, dict):
                return float("-inf")

            timestamp = fixture.get("timestamp")
            if isinstance(timestamp, (int, float)):
                return float(timestamp)
            if isinstance(timestamp, str):
                try:
                    return float(timestamp)
                except ValueError:
                    return float("-inf")
            return float("-inf")

        return sorted(payload, key=sort_key, reverse=True)

    @staticmethod
    def _build_h2h_key(team1_id: int, team2_id: int) -> str:
        ids = sorted([int(team1_id), int(team2_id)])
        return f"{ids[0]}-{ids[1]}"

    async def get_match_h2h(self, provider_fixture_id: int) -> Optional[dict]:
        return await self.h2h_provider.get_match_h2h(provider_fixture_id)

    async def get_cached_h2h(
        self,
        db: AsyncSession,
        *args,
        match_id: int | None = None,
        team1_id: int | None = None,
        team2_id: int | None = None,
    ) -> Optional[dict]:
        if args:
            if len(args) == 1:
                match_id = int(args[0])
            elif len(args) == 2:
                team1_id, team2_id = int(args[0]), int(args[1])
            elif len(args) == 3:
                team1_id, team2_id, match_id = int(args[0]), int(args[1]), int(args[2])

        if match_id is not None:
            match = (await db.execute(select(Match).where(Match.match_id == match_id))).scalar_one_or_none()
            if match is not None:
                team1_id = int(getattr(match, "home_team_id", 0) or 0)
                team2_id = int(getattr(match, "away_team_id", 0) or 0)

        if team1_id is None or team2_id is None:
            return None

        local_h2h_key = self._build_h2h_key(team1_id, team2_id)
        cache_key = make_cache_key("match", "h2h", str(match_id) if match_id is not None else local_h2h_key)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return self._prepare_h2h_payload(cached)

        if match_id is None:
            return None

        team_repository = TeamRepository()
        home_team = await team_repository.get_by_id(db, team1_id)
        away_team = await team_repository.get_by_id(db, team2_id)
        if home_team is None or away_team is None:
            return None
        db_record = (await db.execute(select(MatchH2H).where(MatchH2H.h2h_key == local_h2h_key))).scalar_one_or_none()
        if db_record:
            prepared_payload = self._prepare_h2h_payload(db_record.data)
            # Read path remains read-only for H2H. Cache rebuilds are owned by
            # the explicit source-sync / post-commit invalidation lifecycle.
            return prepared_payload

        # Reads never trigger Analytics publication. Call refresh_h2h explicitly from a source owner.
        return None

    async def get_h2h_with_local_team_ids(
        self,
        db: AsyncSession,
        team1_id: int,
        team2_id: int,
        match_id: int,
    ) -> Optional[dict]:
        payload = await self.get_cached_h2h(
            db,
            team1_id=team1_id,
            team2_id=team2_id,
            match_id=match_id,
        )
        if not isinstance(payload, list):
            return payload

        team_cache: dict[int, tuple[object | None, str | None]] = {}
        enriched_payload = []
        for fixture in payload:
            if not isinstance(fixture, dict):
                enriched_payload.append(fixture)
                continue

            teams = fixture.get("teams")
            if not isinstance(teams, dict):
                enriched_payload.append(fixture)
                continue

            enriched_teams = dict(teams)
            for side in ("home", "away"):
                team = teams.get(side)
                if not isinstance(team, dict):
                    continue
                mapped_team, _issue = await self._resolve_historical_team(
                    db,
                    team.get("id"),
                    team_cache,
                )
                enriched_team = dict(team)
                enriched_team["local_id"] = (
                    int(mapped_team.team_id) if mapped_team is not None else None
                )
                enriched_teams[side] = enriched_team

            enriched_fixture = dict(fixture)
            enriched_fixture["teams"] = enriched_teams
            enriched_payload.append(enriched_fixture)

        return enriched_payload

    async def get_normalized_h2h(self, db: AsyncSession, match: Match) -> dict | None:
        match_id = int(getattr(match, "local_match_id", getattr(match, "match_id", 0)) or 0)
        home_team_id = self._positive_int(getattr(match, "home_team_id", None))
        away_team_id = self._positive_int(getattr(match, "away_team_id", None))
        if (
            match_id <= 0
            or str(getattr(match, "provider", "")).casefold() != "api-football"
            or home_team_id is None
            or away_team_id is None
            or home_team_id == away_team_id
        ):
            raise H2HTeamIdentityUnavailable("H2H match identity is unavailable")

        team_repository = TeamRepository()
        home_team = await team_repository.get_by_id(db, home_team_id)
        away_team = await team_repository.get_by_id(db, away_team_id)
        current_teams = (home_team, away_team)
        if any(
            team is None
            or str(getattr(team, "provider", "")).casefold() != "api-football"
            or self._provider_team_id(getattr(team, "provider_id", None)) is None
            for team in current_teams
        ):
            raise H2HTeamIdentityUnavailable("H2H team identity is unavailable")

        raw_data = await self.get_cached_h2h(
            db,
            team1_id=home_team_id,
            team2_id=away_team_id,
            match_id=match_id,
        )
        if raw_data is None:
            return None
        if not isinstance(raw_data, list):
            raise ValueError("Stored H2H data must be a list")

        return await self._normalize_h2h_data(
            db,
            match_id=match_id,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_team=home_team,
            away_team=away_team,
            fixtures=raw_data,
        )

    async def _normalize_h2h_data(
        self,
        db: AsyncSession,
        *,
        match_id: int,
        home_team_id: int,
        away_team_id: int,
        home_team,
        away_team,
        fixtures: list,
    ) -> dict:
        fixture_ids: list[int] = []
        valid_entries: list[tuple[dict, int]] = []
        excluded_count = 0
        source_anomaly = False

        for item in fixtures:
            if not isinstance(item, dict):
                excluded_count += 1
                source_anomaly = True
                continue
            fixture = item.get("fixture")
            fixture = fixture if isinstance(fixture, dict) else {}
            fixture_id = self._provider_team_id(fixture.get("id"))
            if fixture_id is None:
                excluded_count += 1
                source_anomaly = True
                continue
            fixture_ids.append(fixture_id)
            valid_entries.append((item, fixture_id))

        duplicate_ids = {
            fixture_id
            for fixture_id, count in Counter(fixture_ids).items()
            if count > 1
        }
        if duplicate_ids:
            duplicate_count = sum(
                count for fixture_id, count in Counter(fixture_ids).items()
                if fixture_id in duplicate_ids
            )
            excluded_count += duplicate_count
            source_anomaly = True

        team_cache: dict[int, tuple[object | None, str | None]] = {}
        meetings: list[dict] = []
        counters = {
            home_team_id: {"wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0},
            away_team_id: {"wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0},
        }
        included_meeting_count = 0
        candidate_count = 0
        identity_failure_count = 0
        non_identity_problem = False
        displayable_count = 0

        for item, fixture_id in valid_entries:
            if fixture_id in duplicate_ids:
                continue
            fixture = item.get("fixture")
            fixture = fixture if isinstance(fixture, dict) else {}
            status_info = fixture.get("status")
            status = self._optional_text(
                status_info.get("short") if isinstance(status_info, dict) else None
            )
            if status and status.upper() == "NS":
                excluded_count += 1
                continue
            candidate_count += 1

            teams = item.get("teams")
            teams = teams if isinstance(teams, dict) else {}
            home_info = teams.get("home")
            away_info = teams.get("away")
            home_info = home_info if isinstance(home_info, dict) else {}
            away_info = away_info if isinstance(away_info, dict) else {}
            mapped_home, home_issue = await self._resolve_historical_team(
                db, home_info.get("id"), team_cache
            )
            mapped_away, away_issue = await self._resolve_historical_team(
                db, away_info.get("id"), team_cache
            )

            identity_issues = [issue for issue in (home_issue, away_issue) if issue]
            identity_status = "MAPPED"
            identity_issue = None
            if identity_issues:
                identity_status = (
                    "AMBIGUOUS"
                    if "TEAM_MAPPING_AMBIGUOUS" in identity_issues
                    else "UNMAPPABLE"
                )
                identity_issue = (
                    "TEAM_MAPPING_AMBIGUOUS"
                    if "TEAM_MAPPING_AMBIGUOUS" in identity_issues
                    else identity_issues[0]
                )
                identity_failure_count += 1
            pair_matches = (
                identity_status == "MAPPED"
                and {int(mapped_home.team_id), int(mapped_away.team_id)}
                == {home_team_id, away_team_id}
                and int(mapped_home.team_id) != int(mapped_away.team_id)
            )
            if identity_status == "MAPPED" and not pair_matches:
                non_identity_problem = True
                source_anomaly = True

            goals = item.get("goals")
            goals = goals if isinstance(goals, dict) else {}
            home_score = self._score(goals.get("home"))
            away_score = self._score(goals.get("away"))
            fixture_at = self._fixture_datetime(fixture)
            league = item.get("league")
            league = league if isinstance(league, dict) else {}
            venue = fixture.get("venue")
            venue = venue if isinstance(venue, dict) else {}

            if identity_status != "MAPPED":
                summary_status = "UNMAPPABLE_TEAM"
            elif not pair_matches:
                summary_status = "UNRELATED_PAIR"
            elif home_score is None or away_score is None:
                summary_status = "INCOMPLETE_SCORE"
                source_anomaly = True
            elif (status or "").upper() not in H2H_SUMMARY_STATUSES:
                summary_status = "NOT_FINISHED"
            else:
                summary_status = "INCLUDED"
                included_meeting_count += 1
                home_counter = counters[int(mapped_home.team_id)]
                away_counter = counters[int(mapped_away.team_id)]
                home_counter["goals_for"] += home_score
                home_counter["goals_against"] += away_score
                away_counter["goals_for"] += away_score
                away_counter["goals_against"] += home_score
                if home_score == away_score:
                    home_counter["draws"] += 1
                    away_counter["draws"] += 1
                elif home_score > away_score:
                    home_counter["wins"] += 1
                    away_counter["losses"] += 1
                else:
                    away_counter["wins"] += 1
                    home_counter["losses"] += 1

            if pair_matches:
                displayable_count += 1

            meetings.append(
                {
                    "fixture_at": fixture_at,
                    "status": status,
                    "league_name": self._optional_text(league.get("name")),
                    "venue_name": self._optional_text(venue.get("name")),
                    "home_team": {
                        "team_id": int(mapped_home.team_id) if mapped_home else None,
                        "name": (
                            self._optional_text(getattr(mapped_home, "name", None))
                            if mapped_home
                            else self._optional_text(home_info.get("name"))
                        ),
                    },
                    "away_team": {
                        "team_id": int(mapped_away.team_id) if mapped_away else None,
                        "name": (
                            self._optional_text(getattr(mapped_away, "name", None))
                            if mapped_away
                            else self._optional_text(away_info.get("name"))
                        ),
                    },
                    "score": {"home": home_score, "away": away_score},
                    "identity_status": identity_status,
                    "identity_issue": identity_issue,
                    "summary_status": summary_status,
                }
            )

        if candidate_count == 0:
            availability = "PARTIAL" if source_anomaly else "EMPTY"
        elif displayable_count:
            availability = (
                "PARTIAL"
                if source_anomaly or identity_failure_count or non_identity_problem
                else "AVAILABLE"
            )
        elif (
            identity_failure_count == candidate_count
            and not non_identity_problem
        ):
            availability = "PARTIAL" if source_anomaly else "UNMAPPABLE"
        else:
            availability = "PARTIAL"

        return {
            "contract_version": 2,
            "match_id": match_id,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "availability": availability,
            "available": displayable_count > 0,
            "summary": {
                "home": {
                    "team_id": home_team_id,
                    "team_name": getattr(home_team, "name", None),
                    **counters[home_team_id],
                },
                "away": {
                    "team_id": away_team_id,
                    "team_name": getattr(away_team, "name", None),
                    **counters[away_team_id],
                },
                "included_meeting_count": included_meeting_count,
                "total_matches": included_meeting_count,
                "home_win": counters[home_team_id]["wins"],
                "draw": counters[home_team_id]["draws"],
                "away_win": counters[away_team_id]["wins"],
                "home_goals": counters[home_team_id]["goals_for"],
                "away_goals": counters[away_team_id]["goals_for"],
            },
            "meetings": meetings,
            "excluded_meeting_count": excluded_count,
        }

    async def _resolve_historical_team(
        self,
        db: AsyncSession,
        provider_id_value,
        team_cache: dict[int, tuple[object | None, str | None]],
    ) -> tuple[object | None, str | None]:
        if provider_id_value is None:
            return None, "PROVIDER_ID_MISSING"
        provider_id = self._provider_team_id(provider_id_value)
        if provider_id is None:
            return None, "INVALID_PROVIDER_ID"
        if provider_id not in team_cache:
            try:
                team = await TeamRepository().find_by_provider_identity(
                    db,
                    "api-football",
                    provider_id,
                )
            except AmbiguousProviderIdentityError:
                logger.warning("H2H historical team identity is ambiguous")
                team_cache[provider_id] = (None, "TEAM_MAPPING_AMBIGUOUS")
            else:
                team_cache[provider_id] = (
                    (team, None) if team is not None else (None, "TEAM_NOT_FOUND")
                )
        return team_cache[provider_id]

    @staticmethod
    def _positive_int(value) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        if isinstance(value, str) and not value.strip().isdigit():
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None

    @classmethod
    def _provider_team_id(cls, value) -> int | None:
        return cls._positive_int(value)

    @classmethod
    def _score(cls, value) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        if isinstance(value, str) and not value.strip().isdigit():
            return None
        parsed = int(value)
        return parsed if parsed >= 0 else None

    @staticmethod
    def _optional_text(value) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _fixture_datetime(fixture: dict) -> datetime | None:
        timestamp = fixture.get("timestamp")
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            try:
                return datetime.fromtimestamp(timestamp, timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        date_value = fixture.get("date")
        if not isinstance(date_value, str) or not date_value:
            return None
        try:
            parsed = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed

    async def refresh_h2h(self, db: AsyncSession, match_id: int) -> dict:
        result = await self.h2h_sync_service.refresh_h2h(db, int(match_id))
        if "data" in result:
            result["data"] = self._prepare_h2h_payload(result["data"])
        return result

    async def project_stored_h2h(
        self,
        db: AsyncSession,
        match_id: int,
        h2h_data: list[dict],
    ) -> dict:
        result = await self.h2h_sync_service.project_stored_h2h(
            db,
            int(match_id),
            h2h_data,
        )
        if "data" in result:
            result["data"] = self._prepare_h2h_payload(result["data"])
        return result
