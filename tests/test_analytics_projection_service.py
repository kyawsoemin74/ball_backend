import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.analytics_projection_service import AnalyticsProjectionService, log_projection_transaction


class Team:
    def __init__(self, team_id):
        self.team_id = team_id


class Player:
    def __init__(self, player_id):
        self.player_id = player_id


class Teams:
    async def find_by_provider_identity(self, db, provider, provider_id):
        return Team(int(provider_id) + 100) if int(provider_id) in {1, 2} else None


class Players:
    async def get_by_provider_id(self, db, provider_id, provider="api-football"):
        return Player(int(provider_id) + 1000) if str(provider_id) == "9" else None


class CanonicalPlayers:
    def __init__(self, player=None):
        self.player = player
        self.calls = []

    async def get_by_id(self, db, player_id):
        self.calls.append(player_id)
        return self.player


class Matches:
    def __init__(self, match=None):
        self.match = match

    async def get_by_id(self, db, match_id):
        return self.match


class Seasons:
    async def get_by_league_and_season(self, db, league_id, season):
        return type("Season", (), {"id": 55})()


class Repository:
    def __init__(self, existing=None):
        self.rows = existing or []

    async def list_by_match(self, db, match_id):
        return self.rows

    async def list_by_season(self, db, season_id):
        return self.rows

    async def replace_by_match(self, db, match_id, rows):
        self.rows = rows

    async def replace_by_season(self, db, season_id, rows):
        self.rows = rows

    async def list_by_pair(self, db, team_low_id, team_high_id):
        return self.rows

    async def replace_by_pair(self, db, team_low_id, team_high_id, rows):
        self.rows = rows


class FlushSession:
    async def flush(self):
        return None


def service():
    projection = AnalyticsProjectionService(team_repository=Teams(), player_repository=Players(), league_season_repository=Seasons())
    projection.statistics_repository = Repository()
    projection.standing_repository = Repository()
    projection.odds_repository = Repository([{"old": True}])
    projection.lineup_repository = Repository()
    return projection


def test_statistics_projection_normalizes_typed_values_and_replaces_scope():
    result = asyncio.run(service().project_statistics(None, 10, {"response": [{"team": {"id": 1}, "statistics": [{"type": "Ball Possession", "value": "55%"}]}]}))
    assert result["success"] is True
    assert result["accepted_count"] == 1
    assert result["scope"] == {"match_id": 10}


def test_statistics_duplicate_source_is_rejected_without_replacement():
    projection = service()
    result = asyncio.run(projection.project_statistics(None, 10, {"response": [{"team": {"id": 1}, "statistics": [{"type": "Shots", "value": 1}, {"type": "Shots", "value": 2}]}]}))
    assert result["success"] is False
    assert result["duplicate_count"] == 1
    assert projection.statistics_repository.rows == []


def test_unsupported_odds_source_is_rejected_before_replacement():
    projection = service()
    with pytest.raises(ValueError, match="unsupported Odds bookmaker"):
        asyncio.run(
            projection.project_odds(
                None,
                10,
                [
                    {
                        "bookmaker_name": "Other",
                        "market_name": "Unknown",
                        "selection": "Home",
                        "odd_value": "2.0",
                    }
                ],
            )
        )
    assert projection.odds_repository.rows == [{"old": True}]


def test_odds_projection_carries_giving_team_without_recalculation():
    projection = service()
    source = [
        {
            "bookmaker_name": "1xBet",
            "market_name": "ASIAN_HANDICAP",
            "selection": "Home -0.25",
            "odd_value": "1.9100",
            "myanmar_odd": "-90",
            "giving_team": "AWAY",
        }
    ]

    result = asyncio.run(projection.project_odds(None, 10, source))

    assert result["success"] is True
    assert projection.odds_repository.rows[0]["giving_team"] == "AWAY"
    assert projection.odds_repository.rows[0]["selection"] == "Home -0.25"


@pytest.mark.asyncio
async def test_analytics_odds_repository_persists_giving_team():
    from app.repositories.analytics_repository import AnalyticsOddsRepository

    class CapturingDB:
        rows = []

        async def execute(self, _statement):
            return None

        def add_all(self, rows):
            self.rows = rows

    db = CapturingDB()
    await AnalyticsOddsRepository().replace_by_match(
        db,
        42,
        [
            {
                "bookmaker_name": "1xBet",
                "market_name": "ASIAN_HANDICAP",
                "selection": "Home -0.25",
                "odd_value": Decimal("1.91"),
                "myanmar_odd": "-90",
                "giving_team": "AWAY",
                "source_provider": "api-football",
                "observed_at": datetime.now(timezone.utc),
            }
        ],
    )
    assert db.rows[0].giving_team == "AWAY"
    assert db.rows[0].giving_team == "AWAY"


def test_lineup_unresolved_player_fails_closed():
    with pytest.raises(ValueError, match="unresolved Player"):
        asyncio.run(service().project_lineup(None, 10, [{"team": {"id": 1}, "startXI": [{"player": {"id": 99}}], "substitutes": []}]))


def database_lineup_service(*, player, match=object()):
    projection = AnalyticsProjectionService(
        team_repository=Teams(),
        player_repository=CanonicalPlayers(player),
        match_repository=Matches(match),
    )
    projection.lineup_repository = Repository()
    return projection


def canonical_lineup(player_id=7, provider_player_id="9"):
    return [{
        "team": {"id": 1},
        "startXI": [{"player": {"id": provider_player_id, "player_id": player_id}}],
        "substitutes": [],
    }]


def test_lineup_projection_accepts_existing_canonical_player_and_match():
    projection = database_lineup_service(
        player=type("Player", (), {"player_id": 7, "provider": "api-football", "provider_id": "9"})(),
    )

    result = asyncio.run(projection.project_lineup(FlushSession(), 10, canonical_lineup()))

    assert result["success"] is True
    assert result["analytics_count"] == 1
    assert projection.lineup_repository.rows[0]["player_id"] == 7


def test_lineup_projection_rejects_missing_canonical_player_before_replace():
    projection = database_lineup_service(player=None)
    projection.lineup_repository.rows = [{"old": True}]

    with pytest.raises(ValueError, match="canonical Player does not exist"):
        asyncio.run(projection.project_lineup(object(), 10, canonical_lineup()))

    assert projection.lineup_repository.rows == [{"old": True}]


def test_lineup_projection_rejects_provider_local_player_mismatch():
    projection = database_lineup_service(
        player=type("Player", (), {"player_id": 7, "provider": "api-football", "provider_id": "99"})(),
    )

    with pytest.raises(ValueError, match="Player provider identity mismatch"):
        asyncio.run(projection.project_lineup(object(), 10, canonical_lineup()))


def test_lineup_projection_rejects_missing_match_before_replace():
    projection = database_lineup_service(player=object(), match=None)

    with pytest.raises(ValueError, match="match does not exist"):
        asyncio.run(projection.project_lineup(object(), 10, canonical_lineup()))


def test_projection_logging_exposes_success_fields(caplog):
    with caplog.at_level("INFO"):
        result = asyncio.run(service().project_statistics(None, 10, {"response": [{"team": {"id": 1}, "statistics": [{"type": "Shots", "value": 1}]}]}))

    record = next(item for item in caplog.records if item.message == "ANALYTICS_PROJECTION_RESULT")
    assert record.fact == "statistics"
    assert record.scope == {"match_id": 10}
    assert record.source_count == result["source_count"]
    assert record.accepted_count == result["accepted_count"]
    assert record.analytics_count == result["analytics_count"]
    assert record.transaction_outcome == "pending_outer_transaction"


def test_projection_logging_exposes_empty_and_duplicate_results(caplog):
    with caplog.at_level("INFO"):
        empty = asyncio.run(service().project_odds(None, 10, []))
    empty_record = next(item for item in caplog.records if item.message == "ANALYTICS_PROJECTION_RESULT")
    assert empty_record.fact == "odds"
    assert empty_record.analytics_count == empty["analytics_count"]

    projection = service()
    with caplog.at_level("ERROR"):
        duplicate = asyncio.run(projection.project_statistics(None, 10, {"response": [{"team": {"id": 1}, "statistics": [{"type": "Shots", "value": 1}, {"type": "Shots", "value": 2}]}]}))
    duplicate_record = [item for item in caplog.records if item.message == "ANALYTICS_PROJECTION_RESULT"][-1]
    assert duplicate["success"] is False
    assert duplicate_record.duplicate_count == 1
    assert duplicate_record.success is False


def test_projection_logging_exposes_identity_failure(caplog):
    with caplog.at_level("ERROR"), pytest.raises(ValueError, match="unresolved Player"):
        asyncio.run(service().project_lineup(None, 10, [{"team": {"id": 1}, "startXI": [{"player": {"id": 99}}], "substitutes": []}]))

    record = next(item for item in caplog.records if item.message == "ANALYTICS_PROJECTION_FAILURE")
    assert record.fact == "lineup"
    assert record.failure_type == "ValueError"
    assert record.transaction_outcome == "rollback_required"


def test_h2h_projection_logging_exposes_reconciliation_fields(caplog):
    projection = service()
    projection.h2h_repository = Repository()
    with caplog.at_level("INFO"):
        result = asyncio.run(projection.project_h2h(None, 1, 2, []))

    record = next(item for item in caplog.records if item.message == "ANALYTICS_PROJECTION_RESULT")
    assert record.fact == "h2h"
    assert record.source_count == result["source_count"] == 0
    assert record.analytics_count == result["analytics_count"] == 0


class MismatchedRepository(Repository):
    async def list_by_match(self, db, match_id):
        return []


class FlushDB:
    async def flush(self):
        return None


def test_reconciliation_failure_and_commit_outcome_are_observable(caplog):
    projection = service()
    projection.statistics_repository = MismatchedRepository()
    with caplog.at_level("INFO"), pytest.raises(ValueError, match="persisted reconciliation"):
        asyncio.run(projection.project_statistics(FlushDB(), 10, {"response": [{"team": {"id": 1}, "statistics": [{"type": "Shots", "value": 1}]}]}))

    failure = next(item for item in caplog.records if item.message == "ANALYTICS_PROJECTION_FAILURE")
    assert failure.fact == "statistics"
    assert failure.transaction_outcome == "rollback_required"

    with caplog.at_level("INFO"):
        log_projection_transaction({"fact": "statistics", "scope": {"match_id": 10}, "source_count": 1, "accepted_count": 1, "rejected_count": 0, "duplicate_count": 0, "unresolved_count": 0, "orphan_count": 0, "analytics_count": 1, "success": True}, "committed")
    committed = next(item for item in caplog.records if item.message == "ANALYTICS_PROJECTION_TRANSACTION")
    assert committed.transaction_outcome == "committed"
