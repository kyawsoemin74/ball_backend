import asyncio
import importlib.util
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import CheckConstraint

from app.api import matches as matches_api
from app.models.analytics import AnalyticsH2HSnapshot
from app.repositories.team_repository import AmbiguousProviderIdentityError
from app.services.analytics_projection_service import (
    AnalyticsProjectionService,
    H2HIdentityProjectionError,
    log_projection_transaction,
)
from app.services.h2h_service import H2HService
from app.services.h2h_sync_service import H2HSyncService


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


class H2HTeams:
    def __init__(self, *, unresolved=(), ambiguous=()):
        self.unresolved = set(unresolved)
        self.ambiguous = set(ambiguous)
        self.calls = []

    async def find_by_provider_identity(self, _db, provider, provider_id):
        provider_id = str(provider_id)
        self.calls.append((provider, provider_id))
        if provider_id in self.ambiguous:
            raise AmbiguousProviderIdentityError("ambiguous test identity")
        if provider_id in self.unresolved:
            return None
        return Team(int(provider_id) + 100)

    async def get_by_id(self, _db, team_id):
        provider_id = {101: "1", 102: "2"}.get(team_id)
        if provider_id is None:
            return None
        return SimpleNamespace(
            team_id=team_id,
            provider="api-football",
            provider_id=provider_id,
        )


class H2HRepository(Repository):
    def __init__(self):
        super().__init__([{"old": True}])
        self.replacements = 0

    async def replace_by_pair(self, db, team_low_id, team_high_id, rows):
        self.replacements += 1
        self.rows = [
            {
                "team_low_id": team_low_id,
                "team_high_id": team_high_id,
                **row,
            }
            for row in rows
        ]


def h2h_fixture(fixture_id=700, home_id=1, away_id=2):
    return {
        "fixture": {
            "id": fixture_id,
            "timestamp": 1,
            "status": {"short": "FT"},
        },
        "teams": {
            "home": {"id": home_id},
            "away": {"id": away_id},
        },
        "goals": {"home": 2, "away": 1},
        "score": {},
    }


def h2h_projection(team_repository=None):
    projection = AnalyticsProjectionService(
        team_repository=team_repository or H2HTeams(),
        player_repository=Players(),
        league_season_repository=Seasons(),
    )
    projection.h2h_repository = H2HRepository()
    return projection


def test_h2h_projection_persists_local_ids_and_preserves_fixture_orientation():
    team_repository = H2HTeams()
    projection = h2h_projection(team_repository)
    fixture = h2h_fixture(home_id="2", away_id="1")

    result = asyncio.run(projection.project_h2h(None, 1, 2, [fixture]))

    assert result["success"] is True
    row = projection.h2h_repository.rows[0]
    assert (row["team_low_id"], row["team_high_id"]) == (101, 102)
    assert (row["home_local_team_id"], row["away_local_team_id"]) == (102, 101)
    assert (row["home_provider_team_id"], row["away_provider_team_id"]) == (2, 1)
    assert team_repository.calls == [
        ("api-football", "1"),
        ("api-football", "2"),
    ]


@pytest.mark.parametrize(
    ("team_repository", "fixture", "failure"),
    [
        (H2HTeams(unresolved={"3"}), h2h_fixture(home_id=3), "UNRESOLVED_PROVIDER_IDENTITY"),
        (H2HTeams(ambiguous={"3"}), h2h_fixture(home_id=3), "AMBIGUOUS_PROVIDER_IDENTITY"),
        (H2HTeams(), h2h_fixture(home_id=1, away_id=1), "SAME_LOCAL_TEAM_ON_BOTH_SIDES"),
        (H2HTeams(), h2h_fixture(home_id=3), "UNRELATED_FIXTURE_PAIR"),
        (H2HTeams(), h2h_fixture(home_id=None), "INVALID_HOME_PROVIDER_TEAM_ID"),
    ],
)
def test_h2h_identity_failure_preserves_existing_pair_rows(
    team_repository,
    fixture,
    failure,
):
    projection = h2h_projection(team_repository)
    existing_rows = projection.h2h_repository.rows.copy()

    with pytest.raises(H2HIdentityProjectionError, match=failure):
        asyncio.run(projection.project_h2h(None, 1, 2, [fixture]))

    assert projection.h2h_repository.replacements == 0
    assert projection.h2h_repository.rows == existing_rows


def test_h2h_identity_failure_log_includes_fixture_and_local_pair(caplog):
    projection = h2h_projection(H2HTeams(unresolved={"3"}))
    with caplog.at_level("ERROR"), pytest.raises(H2HIdentityProjectionError):
        asyncio.run(projection.project_h2h(None, 1, 2, [h2h_fixture(home_id=3)]))

    record = next(item for item in caplog.records if item.message == "ANALYTICS_PROJECTION_FAILURE")
    assert record.scope["provider"] == "api-football"
    assert record.scope["fixture_id"] == 700
    assert record.scope["local_pair"] == (101, 102)
    assert record.scope["identity_failure"] == "UNRESOLVED_PROVIDER_IDENTITY"


def test_h2h_api_rolls_back_transaction_on_identity_projection_failure(monkeypatch):
    async def allow_match(_match_id, _db):
        return SimpleNamespace(home_team_id=101, away_team_id=102)

    async def fail_refresh(**_kwargs):
        raise H2HIdentityProjectionError(
            "UNRELATED_FIXTURE_PAIR",
            fixture_id=700,
            local_pair=(101, 102),
        )

    async def run_locked(db, _resource_type, _resource_id, operation):
        return True, await operation()

    class DB:
        def __init__(self):
            self.rollbacks = 0
            self.commits = 0

        async def rollback(self):
            self.rollbacks += 1

        async def commit(self):
            self.commits += 1

    monkeypatch.setattr(matches_api, "_assert_match_allowed", allow_match)
    monkeypatch.setattr(matches_api.football_service, "refresh_h2h", fail_refresh)
    monkeypatch.setattr(matches_api, "run_with_resource_lock", run_locked)
    db = DB()

    with pytest.raises(Exception) as exc_info:
        asyncio.run(matches_api.refresh_h2h_route(77, db))

    assert getattr(exc_info.value, "status_code", None) == 422
    assert db.rollbacks == 1
    assert db.commits == 0


def test_legacy_h2h_enrichment_preserves_provider_fields_and_orientation(monkeypatch):
    class Cache:
        async def get_json(self, _key):
            return None

    raw_payload = [
        {
            "fixture": {"id": 700, "date": "2025-01-01"},
            "teams": {
                "home": {"id": 2, "name": "Provider Away", "logo": "home.png"},
                "away": {"id": 1, "name": "Provider Home", "logo": "away.png"},
            },
            "goals": {"home": 2, "away": 1},
        },
        {
            "fixture": {"id": 701, "date": "2025-02-01"},
            "teams": {
                "home": {"id": 1, "name": "Provider Home"},
                "away": {"id": 2, "name": "Provider Away"},
            },
            "goals": {"home": 1, "away": 0},
        },
        {
            "fixture": {"id": 702, "date": "2025-03-01"},
            "teams": {
                "home": {"id": 999, "name": "Unmapped"},
                "away": {"id": 1, "name": "Provider Home"},
            },
            "goals": {"home": 0, "away": 1},
        },
    ]
    lookups = []

    class TeamRepository:
        async def find_by_provider_identity(self, _db, provider, provider_id):
            lookups.append((provider, provider_id))
            return {
                1: SimpleNamespace(team_id=101),
                2: SimpleNamespace(team_id=102),
            }.get(provider_id)

    monkeypatch.setattr("app.services.h2h_service.TeamRepository", TeamRepository)
    service = H2HService(client=object(), cache_service=Cache())
    reads = []
    writes = []

    async def get_raw_payload(_db, *, team1_id, team2_id, match_id):
        reads.append((team1_id, team2_id, match_id))
        return raw_payload

    monkeypatch.setattr(service, "get_cached_h2h", get_raw_payload)

    class DB:
        async def execute(self, _statement):
            writes.append("execute")
            raise AssertionError("enrichment must not query or write through DB")

        async def flush(self):
            writes.append("flush")

        async def commit(self):
            writes.append("commit")

    enriched = asyncio.run(
        service.get_h2h_with_local_team_ids(DB(), 101, 102, 55)
    )

    assert reads == [(101, 102, 55)]
    assert writes == []
    assert enriched[0]["teams"]["home"] == {
        "id": 2,
        "name": "Provider Away",
        "logo": "home.png",
        "local_id": 102,
    }
    assert enriched[0]["teams"]["away"]["id"] == 1
    assert enriched[0]["teams"]["away"]["local_id"] == 101
    assert enriched[1]["teams"]["home"]["id"] == 1
    assert enriched[1]["teams"]["home"]["local_id"] == 101
    assert enriched[1]["teams"]["away"]["id"] == 2
    assert enriched[1]["teams"]["away"]["local_id"] == 102
    assert enriched[2]["teams"]["home"]["id"] == 999
    assert enriched[2]["teams"]["home"]["local_id"] is None
    assert enriched[2]["teams"]["away"]["local_id"] == 101
    assert raw_payload[0]["teams"]["home"] == {
        "id": 2,
        "name": "Provider Away",
        "logo": "home.png",
    }
    assert lookups == [
        ("api-football", 2),
        ("api-football", 1),
        ("api-football", 999),
    ]


def test_legacy_h2h_endpoint_returns_additive_local_ids(monkeypatch):
    async def allow_match(_match_id, _db):
        return object()

    async def get_enriched_payload(_db, team1_id, team2_id, match_id):
        return [{
            "fixture": {"id": 700},
            "teams": {
                "home": {"id": 2, "local_id": 102},
                "away": {"id": 1, "local_id": 101},
            },
            "goals": {"home": 2, "away": 1},
        }]

    monkeypatch.setattr(matches_api, "_assert_match_allowed", allow_match)
    monkeypatch.setattr(
        matches_api.football_service,
        "get_h2h_with_local_team_ids",
        get_enriched_payload,
    )

    result = asyncio.run(
        matches_api.get_match_h2h_symmetric(55, 101, 102, object())
    )

    assert result[0]["teams"] == {
        "home": {"id": 2, "local_id": 102},
        "away": {"id": 1, "local_id": 101},
    }
    assert result[0]["goals"] == {"home": 2, "away": 1}


def test_h2h_provider_and_stored_refresh_share_projection_and_preserve_raw_payload():
    raw_payload = [h2h_fixture(home_id=1, away_id=2)]

    class SourceRepository:
        def __init__(self):
            self.rows = {}

        async def upsert_one(self, _db, key, data):
            self.rows[key] = data

    class Provider:
        def __init__(self):
            self.calls = []

        async def get_h2h_by_team_ids(self, home_id, away_id):
            self.calls.append((home_id, away_id))
            return {"response": raw_payload}

    source_repository = SourceRepository()
    provider = Provider()
    projection = h2h_projection()

    class MatchResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(
                local_match_id=77,
                provider="api-football",
                provider_fixture_id=700,
                home_team_id=101,
                away_team_id=102,
            )

    class DB:
        async def execute(self, _statement):
            return MatchResult()

        async def flush(self):
            return None

    sync = H2HSyncService(
        h2h_service=None,
        h2h_provider=provider,
        h2h_repository=source_repository,
        analytics_projection_service=projection,
        team_repository=H2HTeams(),
    )

    async def run_both():
        fresh = await sync.refresh_h2h(DB(), 77)
        stored = await sync.project_stored_h2h(DB(), 77, raw_payload)
        return fresh, stored

    fresh, stored = asyncio.run(run_both())

    assert provider.calls == [(1, 2)]
    assert fresh["data"] == stored["data"] == raw_payload
    assert source_repository.rows["101-102"] == raw_payload
    assert source_repository.rows["101-102"][0]["teams"] == {
        "home": {"id": 1},
        "away": {"id": 2},
    }
    assert projection.h2h_repository.replacements == 2
    assert [
        (row["home_local_team_id"], row["away_local_team_id"])
        for row in projection.h2h_repository.rows
    ] == [(101, 102)]


def test_h2h_model_declares_nullable_local_side_columns_and_pair_check():
    table = AnalyticsH2HSnapshot.__table__
    assert table.c.home_local_team_id.nullable
    assert table.c.away_local_team_id.nullable
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_analytics_h2h_local_sides_match_pair" in check_names
    assert {
        fk.name: fk.ondelete
        for fk in table.foreign_keys
        if fk.name in {
            "fk_analytics_h2h_home_local_team",
            "fk_analytics_h2h_away_local_team",
        }
    } == {
        "fk_analytics_h2h_home_local_team": "RESTRICT",
        "fk_analytics_h2h_away_local_team": "RESTRICT",
    }


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "20261030_h2h_local_sides.py"
)
MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "h2h_local_sides_migration",
    MIGRATION_PATH,
)
H2H_MIGRATION = importlib.util.module_from_spec(MIGRATION_SPEC)
MIGRATION_SPEC.loader.exec_module(H2H_MIGRATION)


class MigrationOperations:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def capture(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return capture


def test_h2h_local_sides_migration_upgrade_and_downgrade_are_additive(monkeypatch):
    assert H2H_MIGRATION.revision == "20261030_h2h_local_sides"
    assert H2H_MIGRATION.down_revision == "20261020_h2h_sync_work"

    operations = MigrationOperations()
    monkeypatch.setattr(H2H_MIGRATION, "op", operations)
    monkeypatch.setattr(H2H_MIGRATION.context, "is_offline_mode", lambda: True)
    H2H_MIGRATION.upgrade()

    columns = [call for call in operations.calls if call[0] == "add_column"]
    assert [call[1][1].name for call in columns] == [
        "home_local_team_id",
        "away_local_team_id",
    ]
    assert all(call[1][1].nullable for call in columns)
    foreign_keys = [
        call for call in operations.calls if call[0] == "create_foreign_key"
    ]
    assert [call[1][0] for call in foreign_keys] == [
        "fk_analytics_h2h_home_local_team",
        "fk_analytics_h2h_away_local_team",
    ]
    assert all(call[2]["ondelete"] == "RESTRICT" for call in foreign_keys)
    backfill = str(next(
        call[1][0] for call in operations.calls if call[0] == "execute"
    ))
    assert "home_team.provider = snapshot.source_provider" in backfill
    assert "home_team.provider_id = snapshot.home_provider_team_id::text" in backfill
    assert "away_team.provider_id = snapshot.away_provider_team_id::text" in backfill
    assert "LEAST(home_team.team_id, away_team.team_id) = snapshot.team_low_id" in backfill
    assert "GREATEST(home_team.team_id, away_team.team_id) = snapshot.team_high_id" in backfill
    assert "name" not in backfill.lower()

    operations.calls.clear()
    H2H_MIGRATION.downgrade()
    assert [(call[0], call[1][0]) for call in operations.calls] == [
        ("drop_constraint", "ck_analytics_h2h_local_sides_match_pair"),
        ("drop_constraint", "fk_analytics_h2h_away_local_team"),
        ("drop_constraint", "fk_analytics_h2h_home_local_team"),
        ("drop_column", "analytics_h2h_snapshots"),
        ("drop_column", "analytics_h2h_snapshots"),
    ]
    assert [call[1][1] for call in operations.calls[-2:]] == [
        "away_local_team_id",
        "home_local_team_id",
    ]


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
