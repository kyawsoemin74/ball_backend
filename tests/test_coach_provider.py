import asyncio

from app.services.coach_sync_service import CoachSyncService  # noqa: F401
from app.providers.coach_provider import CoachProvider


class FakeClient:
    def __init__(self, response):
        self.response = response

    async def get(self, path, params=None):
        return self.response


def test_legacy_team_coach_wrapper_only_returns_verified_selection():
    provider = CoachProvider(FakeClient({"response": [{"id": 1, "name": "Coach A"}]}))

    result = asyncio.run(provider.get_team_coach(118))

    assert result == {"id": 1, "name": "Coach A"}


def test_legacy_team_coach_wrapper_blocks_ambiguous_selection():
    provider = CoachProvider(FakeClient({"response": [{"id": 1, "name": "Coach A"}, {"id": 2, "name": "Coach B"}]}))

    result = asyncio.run(provider.get_team_coach(118))

    assert result is None


def test_selection_contract_classifies_no_data_and_invalid_payloads():
    no_data = CoachProvider(FakeClient({"response": []}))
    invalid = CoachProvider(FakeClient({"response": [{"id": 1}]}))

    assert asyncio.run(no_data.get_team_coach_selection(118)) == {"status": "NO_DATA", "coach": None}
    assert asyncio.run(invalid.get_team_coach_selection(118)) == {"status": "INVALID", "coach": None}