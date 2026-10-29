from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import matches as matches_api
from app.api.v2 import matches as h2h_v2_api
from app.db import get_db
from app.repositories.team_repository import AmbiguousProviderIdentityError
from app.services import h2h_service as h2h_service_module
from app.services.h2h_service import H2HService


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, match=None, h2h_record=None):
        self.match = match
        self.h2h_record = h2h_record

    async def execute(self, statement):
        query = str(statement)
        if "FROM matches" in query:
            return FakeResult(self.match)
        if "FROM match_h2h" in query:
            return FakeResult(self.h2h_record)
        raise AssertionError(f"Unexpected query: {query}")


class FakeCache:
    def __init__(self, payload):
        self.payload = payload
        self.keys = []

    async def get_json(self, key):
        self.keys.append(key)
        return self.payload

    async def set_json(self, *args):
        raise AssertionError("The normalized GET must not write to cache")


class FakeTeamRepository:
    local_teams = {
        97: SimpleNamespace(
            team_id=97, provider="api-football", provider_id=5, name="Home FC"
        ),
        98: SimpleNamespace(
            team_id=98, provider="api-football", provider_id=774, name="Away FC"
        ),
        99: SimpleNamespace(
            team_id=99, provider="api-football", provider_id=600, name="Other FC"
        ),
        100: SimpleNamespace(
            team_id=100, provider="api-football", provider_id=601, name="Other United"
        ),
    }
    ambiguous_provider_ids = set()
    provider_lookups = []

    async def get_by_id(self, db, team_id):
        return self.local_teams.get(team_id)

    async def find_by_provider_identity(self, db, provider, provider_id):
        self.provider_lookups.append((provider, provider_id))
        if provider_id in self.ambiguous_provider_ids:
            raise AmbiguousProviderIdentityError("ambiguous provider identity")
        for team in self.local_teams.values():
            if team.provider == provider and team.provider_id == provider_id:
                return team
        return None


class NoProvider:
    async def get_match_h2h(self, *args, **kwargs):
        raise AssertionError("A read-only normalized GET must not call the provider")


def fixture(
    fixture_id,
    home_id=5,
    away_id=774,
    home_score=2,
    away_score=1,
    status="FT",
):
    return {
        "fixture": {
            "id": fixture_id,
            "date": "2024-01-01T12:00:00+00:00",
            "status": {"short": status},
            "venue": {"name": "Local Stadium"},
        },
        "league": {"name": "Test League"},
        "teams": {
            "home": {"id": home_id, "name": f"Provider team {home_id}"},
            "away": {"id": away_id, "name": f"Provider team {away_id}"},
        },
        "goals": {"home": home_score, "away": away_score},
    }


def match():
    return SimpleNamespace(
        local_match_id=55,
        provider="api-football",
        home_team_id=97,
        away_team_id=98,
    )


def make_service(payload, db_record=None):
    cache = FakeCache(payload)
    service = H2HService(
        client=object(),
        cache_service=cache,
        h2h_provider=NoProvider(),
    )
    return service, cache, FakeDB(match=match(), h2h_record=db_record)


@pytest.fixture(autouse=True)
def team_repository(monkeypatch):
    FakeTeamRepository.ambiguous_provider_ids = set()
    FakeTeamRepository.provider_lookups = []
    monkeypatch.setattr(
        h2h_service_module, "TeamRepository", FakeTeamRepository
    )


@pytest.mark.asyncio
async def test_normalized_summary_maps_provider_ids_in_both_historical_orientations():
    service, _, db = make_service(
        [
            fixture(1, home_id=5, away_id=774, home_score=2, away_score=1),
            fixture(2, home_id=774, away_id=5, home_score=0, away_score=3),
            fixture(3, home_id=5, away_id=774, home_score=1, away_score=1),
        ]
    )

    result = await service.get_normalized_h2h(db, match())

    assert result["availability"] == "AVAILABLE"
    assert result["summary"]["home"] == {
        "team_id": 97,
        "team_name": "Home FC",
        "wins": 2,
        "draws": 1,
        "losses": 0,
        "goals_for": 6,
        "goals_against": 2,
    }
    assert result["summary"]["away"]["team_id"] == 98
    assert result["summary"]["away"]["losses"] == 2
    assert [meeting["home_team"]["team_id"] for meeting in result["meetings"]] == [
        97,
        98,
        97,
    ]
    assert result["summary"]["included_meeting_count"] == 3
    assert FakeTeamRepository.provider_lookups == [
        ("api-football", 5),
        ("api-football", 774),
    ]


@pytest.mark.parametrize(
    ("home_score", "away_score"),
    [(None, 1), (-1, 1), ("invalid", 1), (True, 1)],
)
@pytest.mark.asyncio
async def test_invalid_or_missing_scores_are_not_counted(home_score, away_score):
    service, _, db = make_service(
        [fixture(1, home_score=home_score, away_score=away_score)]
    )

    result = await service.get_normalized_h2h(db, match())

    assert result["availability"] == "PARTIAL"
    assert result["summary"]["included_meeting_count"] == 0
    assert result["meetings"][0]["summary_status"] == "INCOMPLETE_SCORE"
    assert result["summary"]["home"]["wins"] == 0


@pytest.mark.asyncio
async def test_non_final_matches_are_displayable_but_excluded_from_summary():
    service, _, db = make_service([fixture(1, status="PST")])

    result = await service.get_normalized_h2h(db, match())

    assert result["availability"] == "AVAILABLE"
    assert result["available"] is True
    assert result["meetings"][0]["summary_status"] == "NOT_FINISHED"
    assert result["summary"]["included_meeting_count"] == 0


@pytest.mark.asyncio
async def test_unmappable_and_ambiguous_historical_team_identities_are_reported():
    service, _, db = make_service([fixture(1, home_id=9999)])

    unmappable = await service.get_normalized_h2h(db, match())

    assert unmappable["availability"] == "UNMAPPABLE"
    assert unmappable["meetings"][0]["identity_issue"] == "TEAM_NOT_FOUND"

    FakeTeamRepository.ambiguous_provider_ids = {5}
    service, _, db = make_service([fixture(2)])
    ambiguous = await service.get_normalized_h2h(db, match())

    assert ambiguous["availability"] == "UNMAPPABLE"
    assert ambiguous["meetings"][0]["identity_status"] == "AMBIGUOUS"
    assert ambiguous["meetings"][0]["identity_issue"] == "TEAM_MAPPING_AMBIGUOUS"


@pytest.mark.asyncio
async def test_unrelated_pair_is_displayed_but_not_included_in_summary():
    service, _, db = make_service([fixture(1, home_id=600, away_id=601)])

    result = await service.get_normalized_h2h(db, match())

    assert result["availability"] == "PARTIAL"
    assert result["meetings"][0]["summary_status"] == "UNRELATED_PAIR"
    assert result["summary"]["included_meeting_count"] == 0


@pytest.mark.asyncio
async def test_duplicate_fixture_ids_are_excluded_from_meetings_and_summary():
    duplicated = fixture(1)
    service, _, db = make_service([duplicated, duplicated])

    result = await service.get_normalized_h2h(db, match())

    assert result["availability"] == "PARTIAL"
    assert result["meetings"] == []
    assert result["excluded_meeting_count"] == 2
    assert result["summary"]["included_meeting_count"] == 0


@pytest.mark.asyncio
async def test_empty_h2h_row_is_empty_but_missing_h2h_row_is_not_found():
    service, _, db = make_service([])

    empty = await service.get_normalized_h2h(db, match())
    assert empty["availability"] == "EMPTY"
    assert empty["available"] is False
    assert empty["meetings"] == []

    service, _, db = make_service(None, db_record=SimpleNamespace(data=[]))
    empty_row = await service.get_normalized_h2h(db, match())
    assert empty_row["availability"] == "EMPTY"

    service, _, db = make_service(None)
    missing = await service.get_normalized_h2h(db, match())
    assert missing is None


@pytest.mark.asyncio
async def test_cache_miss_reads_persisted_h2h_without_publishing_cache():
    service, cache, db = make_service(
        None,
        db_record=SimpleNamespace(data=[fixture(1)]),
    )

    result = await service.get_normalized_h2h(db, match())

    assert result["availability"] == "AVAILABLE"
    assert result["summary"]["included_meeting_count"] == 1
    assert len(cache.keys) == 1


@pytest.mark.parametrize("available", [True, False])
@pytest.mark.asyncio
async def test_match_detail_h2h_availability_uses_normalized_displayability(
    monkeypatch, available
):
    class FakeFootballService:
        async def get_normalized_h2h(self, db, requested_match):
            return {"available": available}

    monkeypatch.setattr(matches_api, "football_service", FakeFootballService())

    assert await matches_api._has_h2h_available(match(), object()) is available


def test_v2_route_returns_typed_payload_and_rejects_mismatched_team_ids(monkeypatch):
    app = FastAPI()
    app.include_router(h2h_v2_api.router, prefix="/api/v2")

    async def get_test_db():
        return object()

    async def assert_match_allowed(match_id, db):
        return match()

    class FakeFootballService:
        async def get_normalized_h2h(self, db, requested_match):
            return {
                "contract_version": 2,
                "match_id": 55,
                "home_team_id": 97,
                "away_team_id": 98,
                "availability": "EMPTY",
                "available": False,
                "summary": {
                    "home": {
                        "team_id": 97,
                        "team_name": "Home FC",
                        "wins": 0,
                        "draws": 0,
                        "losses": 0,
                        "goals_for": 0,
                        "goals_against": 0,
                    },
                    "away": {
                        "team_id": 98,
                        "team_name": "Away FC",
                        "wins": 0,
                        "draws": 0,
                        "losses": 0,
                        "goals_for": 0,
                        "goals_against": 0,
                    },
                    "included_meeting_count": 0,
                },
                "meetings": [],
                "excluded_meeting_count": 0,
            }

    monkeypatch.setattr(h2h_v2_api, "_assert_match_allowed", assert_match_allowed)
    monkeypatch.setattr(
        h2h_v2_api, "football_service", FakeFootballService()
    )
    app.dependency_overrides[get_db] = get_test_db

    client = TestClient(app)
    response = client.get("/api/v2/matches/h2h/55/98/97")
    assert response.status_code == 200
    assert response.json()["contract_version"] == 2
    assert response.json()["availability"] == "EMPTY"

    mismatch = client.get("/api/v2/matches/h2h/55/97/99")
    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "H2H_TEAM_IDS_MISMATCH"


def test_v2_route_returns_404_for_missing_source(monkeypatch):
    app = FastAPI()
    app.include_router(h2h_v2_api.router, prefix="/api/v2")

    async def get_test_db():
        return object()

    async def assert_match_allowed(match_id, db):
        return match()

    class MissingFootballService:
        async def get_normalized_h2h(self, db, requested_match):
            return None

    monkeypatch.setattr(h2h_v2_api, "_assert_match_allowed", assert_match_allowed)
    monkeypatch.setattr(
        h2h_v2_api, "football_service", MissingFootballService()
    )
    app.dependency_overrides[get_db] = get_test_db

    response = TestClient(app).get("/api/v2/matches/h2h/55/97/98")

    assert response.status_code == 404
    assert response.json()["code"] == "H2H_NOT_FOUND"
