import asyncio

from app.services.h2h_service import H2HService


class StubClient:
    def __init__(self):
        self.calls = []

    async def get(self, path, params=None, timeout=30.0):
        self.calls.append({"path": path, "params": dict(params or {}), "timeout": timeout})
        return {"response": [{"fixture": {"id": 1}}]}


class StubProvider:
    def __init__(self):
        self.calls = []

    async def get_match_h2h(self, match_id):
        self.calls.append(match_id)
        return {"response": [{"fixture": {"id": 99}}]}


def test_h2h_service_delegates_transport_to_provider():
    client = StubClient()
    service = H2HService(client)
    provider = StubProvider()
    service.h2h_provider = provider

    result = asyncio.run(service.get_match_h2h(123))

    assert result == {"response": [{"fixture": {"id": 99}}]}
    assert provider.calls == [123]
    assert client.calls == []
