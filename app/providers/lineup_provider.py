from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


class LineupProvider:
    """Transport-only provider for lineup-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    @classmethod
    def _has_meaningful_errors(cls, value: object) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, dict):
            return any(cls._has_meaningful_errors(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return any(cls._has_meaningful_errors(item) for item in value)
        return bool(value)

    async def get_match_lineup(self, provider_fixture_id: int) -> Optional[dict]:
        params = {"fixture": provider_fixture_id}
        get_with_metadata = getattr(self.client, "get_with_metadata", None)
        if callable(get_with_metadata):
            result = await get_with_metadata("/fixtures/lineups", params=params)
            status_code = getattr(result, "status_code", None)
            if status_code is not None and (
                not isinstance(status_code, int) or not 200 <= status_code < 300
            ):
                return None
            if any(
                getattr(result, field, None)
                for field in ("error_code", "exception_type")
            ):
                return None
            payload = getattr(result, "payload", None)
        else:
            payload = await self.client.get("/fixtures/lineups", params=params)

        if not isinstance(payload, dict) or self._has_meaningful_errors(payload.get("errors")):
            return None
        response = payload.get("response")
        if not isinstance(response, list) or not response:
            return None
        return payload
