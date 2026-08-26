from app.services.base.football_client import FootballAPIClient


class RefereeProvider:
    """Transport-only provider for referee data from API-Football."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_fixture_referee(self, fixture_id: int) -> str | None:
        data = await self.client.get("/fixtures", params={"id": fixture_id})
        if not isinstance(data, dict) or "response" not in data:
            return None

        fixtures = data.get("response", [])
        if not isinstance(fixtures, list) or not fixtures:
            return None

        fixture = fixtures[0]
        if not isinstance(fixture, dict):
            return None

        fixture_block = fixture.get("fixture") if isinstance(fixture.get("fixture"), dict) else {}
        referee = fixture_block.get("referee")
        if isinstance(referee, str) and referee.strip():
            return referee.strip()
        return None

    async def get_fixture_referee_payload(self, fixture_id: int) -> dict | None:
        data = await self.client.get("/fixtures", params={"id": fixture_id})
        if not isinstance(data, dict) or "response" not in data:
            return None

        fixtures = data.get("response", [])
        if not isinstance(fixtures, list) or not fixtures:
            return None

        fixture = fixtures[0]
        if not isinstance(fixture, dict):
            return None

        ref_payload = fixture.get("fixture", {}).get("referee")
        return {"referee": ref_payload} if isinstance(ref_payload, str) and ref_payload.strip() else None
