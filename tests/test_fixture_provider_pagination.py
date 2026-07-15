import asyncio

from app.providers.fixture_provider import FixtureProvider


class StubFixtureClient:
    def __init__(self, responses_by_page):
        self.responses_by_page = responses_by_page
        self.calls = []

    async def get(self, path, params=None, timeout=30.0):
        self.calls.append({"path": path, "params": dict(params or {})})
        page = (params or {}).get("page", 1)
        return self.responses_by_page.get(page)


def test_get_fixtures_single_page_passthrough():
    response_page_1 = {
        "response": [{"fixture": {"id": 1}}],
        "paging": {"current": 1, "total": 1},
        "results": 1,
    }
    client = StubFixtureClient({1: response_page_1})
    provider = FixtureProvider(client)

    result = asyncio.run(provider.get_fixtures(league=39, season=2026))

    assert result == response_page_1
    assert len(client.calls) == 1
    assert client.calls[0]["path"] == "/fixtures"
    assert client.calls[0]["params"] == {"league": 39, "season": 2026}


def test_get_fixtures_combines_all_pages():
    response_page_1 = {
        "response": [{"fixture": {"id": 10}}, {"fixture": {"id": 11}}],
        "paging": {"current": 1, "total": 3},
        "results": 2,
    }
    response_page_2 = {
        "response": [{"fixture": {"id": 12}}],
        "paging": {"current": 2, "total": 3},
        "results": 1,
    }
    response_page_3 = {
        "response": [{"fixture": {"id": 13}}],
        "paging": {"current": 3, "total": 3},
        "results": 1,
    }

    client = StubFixtureClient({1: response_page_1, 2: response_page_2, 3: response_page_3})
    provider = FixtureProvider(client)

    result = asyncio.run(provider.get_fixtures(league=39, season=2026))

    assert result is not None
    ids = [item["fixture"]["id"] for item in result["response"]]
    assert ids == [10, 11, 12, 13]
    assert len(client.calls) == 3
    assert client.calls[1]["params"] == {"league": 39, "season": 2026, "page": 2}
    assert client.calls[2]["params"] == {"league": 39, "season": 2026, "page": 3}


def test_get_fixtures_returns_none_if_later_page_fails():
    response_page_1 = {
        "response": [{"fixture": {"id": 10}}],
        "paging": {"current": 1, "total": 3},
        "results": 1,
    }
    response_page_2 = {
        "response": [{"fixture": {"id": 12}}],
        "paging": {"current": 2, "total": 3},
        "results": 1,
    }

    client = StubFixtureClient({1: response_page_1, 2: response_page_2, 3: None})
    provider = FixtureProvider(client)

    result = asyncio.run(provider.get_fixtures(league=39, season=2026))

    assert result is None
    assert len(client.calls) == 3
