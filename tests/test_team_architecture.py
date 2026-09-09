import asyncio
from types import SimpleNamespace

from app.providers.team_provider import TeamProvider
from app.services.team_sync_service import TeamSyncService


class PagingClient:
    def __init__(self):
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        if params.get("page") == 2:
            return {"response": [{"team": {"id": 52, "name": "Two"}}]}
        return {
            "response": [{"team": {"id": 51, "name": "One"}}],
            "paging": {"current": 1, "total": 2},
        }


class TeamRepositoryFake:
    def __init__(self):
        self.teams = {}
        self.next_team_id = 1001

    async def find_by_provider_identity(self, db, provider, provider_id):
        return self.teams.get((provider, str(provider_id)))

    async def upsert_by_provider_identity(self, db, row):
        key = (row["provider"], str(row["provider_id"]))
        team = self.teams.get(key)
        if team is None:
            team = SimpleNamespace(team_id=self.next_team_id)
            self.next_team_id += 1
            self.teams[key] = team
        team.provider = row["provider"]
        team.provider_id = str(row["provider_id"])
        team.name = row["name"]
        team.country = row.get("country")
        team.logo = row.get("logo")
        team.stadium = row.get("stadium")
        team.founded = row.get("founded")
        team.country_id = None
        return team

    async def update_provider_metadata(self, db, team_id, **values):
        team = next(team for team in self.teams.values() if team.team_id == team_id)
        for key, value in values.items():
            if value is not None:
                setattr(team, key, value)


class TeamProviderFake:
    async def get_team_details(self, team_id):
        return {
            "response": [
                {
                    "team": {
                        "id": team_id,
                        "name": "Fetched Team",
                        "country": "Myanmar",
                        "logo": None,
                        "founded": 2001,
                    },
                    "venue": {"name": "Fetched Ground"},
                }
            ]
        }


def test_team_provider_fetches_all_league_pages():
    client = PagingClient()
    teams = asyncio.run(TeamProvider(client).get_league_teams(39, 2026))

    assert [item["team"]["id"] for item in teams] == [51, 52]
    assert client.calls == [
        ("/teams", {"league": 39, "season": 2026}),
        ("/teams", {"league": 39, "season": 2026, "page": 2}),
    ]


def test_team_sync_fetches_creates_verifies_and_resolves_missing_team():
    repository = TeamRepositoryFake()
    service = TeamSyncService(
        cache_service=SimpleNamespace(),
        team_repository=repository,
        team_provider=TeamProviderFake(),
    )

    result = asyncio.run(
        service.ensure_teams_exist(
            SimpleNamespace(),
            [{"provider_id": 51}],
        )
    )

    assert result["created"] == 1
    assert result["unresolved"] == 0
    assert repository.teams[("api-football", "51")].team_id == 1001


def test_team_sync_preserves_local_id_when_provider_identity_already_exists():
    repository = TeamRepositoryFake()
    existing = SimpleNamespace(
        team_id=7001,
        provider="api-football",
        provider_id="99",
        name="Old Name",
        country="Myanmar",
        logo=None,
        stadium=None,
        founded=None,
        country_id=None,
    )
    repository.teams[("api-football", "99")] = existing
    service = TeamSyncService(
        cache_service=SimpleNamespace(),
        team_repository=repository,
        team_provider=TeamProviderFake(),
    )

    result = asyncio.run(
        service.upsert_team(
            SimpleNamespace(),
            {"team": {"id": 99, "name": "New Name"}},
        )
    )

    assert result.team_id == 7001
    assert result.provider_id == "99"
    assert result.name == "New Name"
