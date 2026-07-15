from typing import Optional, Protocol


class FootballAPIClient(Protocol):
	async def get(self, path: str, params: Optional[dict] = None, timeout: float = 30.0) -> Optional[dict]:
		...

	async def post(self, path: str, params: Optional[dict] = None, timeout: float = 30.0) -> Optional[dict]:
		...


__all__ = ["FootballAPIClient"]
