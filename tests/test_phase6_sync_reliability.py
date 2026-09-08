import asyncio

import pytest

from app.services.h2h_sync_service import H2HSyncService
from app.services.statistics_sync_service import StatisticsSyncService


class Provider:
    def __init__(self, response):
        self.response = response

    async def get_match_statistics(self, match_id):
        return self.response

    async def get_h2h_by_key(self, h2h_key):
        return self.response


class Repository:
    async def replace_match_statistics(self, db, match_id, data):
        raise AssertionError("repository must not run after provider failure")

    async def find_by_provider_identity(self, db, provider, provider_id):
        return None


class Projection:
    async def project_statistics(self, *args):
        raise AssertionError("projection must not run after provider failure")


def test_statistics_provider_failure_is_not_success():
    service = StatisticsSyncService(
        statistics_provider=Provider(None),
        statistics_repository=Repository(),
        analytics_projection_service=Projection(),
    )
    result = asyncio.run(service.sync_match_statistics(object(), 10))
    assert result["success"] is False


def test_statistics_empty_response_is_not_treated_as_success():
    service = StatisticsSyncService(
        statistics_provider=Provider({"response": []}),
        statistics_repository=Repository(),
        analytics_projection_service=Projection(),
    )
    result = asyncio.run(service.sync_match_statistics(object(), 10))
    assert result["success"] is False


def test_h2h_unresolved_team_identity_fails_before_repository_write():
    service = H2HSyncService(
        h2h_service=None,
        h2h_provider=Provider({"response": [{"fixture": 1}]}),
        h2h_repository=Repository(),
        team_repository=Repository(),
        analytics_projection_service=Projection(),
    )
    with pytest.raises(ValueError, match="Unresolved provider Team identity"):
        asyncio.run(service.refresh_h2h(object(), "10-20"))
