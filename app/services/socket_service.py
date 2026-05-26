import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket
from redis.asyncio.client import PubSub

from app.redis import async_redis

logger = logging.getLogger(__name__)

LIVE_UPDATE_CHANNEL = "fover:live_updates"


def _format_match_payload(match_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "match_update",
        "channel": f"match:{match_data.get('match_id')}",
        "match_id": match_data.get("match_id"),
        "status": match_data.get("status"),
        "elapsed": match_data.get("elapsed"),
        "home_score": match_data.get("home_score"),
        "away_score": match_data.get("away_score"),
        "home_team": match_data.get("home_team"),
        "away_team": match_data.get("away_team"),
        "home_team_logo": match_data.get("home_team_logo"),
        "away_team_logo": match_data.get("away_team_logo"),
        "league": match_data.get("league_name"),
        "venue": match_data.get("venue_name"),
        "payload": match_data,
    }


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()
        self.subscriptions: Dict[str, Set[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, match_id: Optional[int] = None) -> None:
        await websocket.accept()
        async with self.lock:
            self.active_connections.add(websocket)
            channel = self._channel_for_match(match_id)
            self.subscriptions.setdefault(channel, set()).add(websocket)
            logger.info("WebSocket connected to %s", channel)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self.lock:
            self.active_connections.discard(websocket)
            for subscribers in self.subscriptions.values():
                subscribers.discard(websocket)
            logger.info("WebSocket disconnected")

    async def broadcast(self, message: Dict[str, Any], channel: Optional[str] = None) -> None:
        if channel is None:
            channel = "global"

        async with self.lock:
            subscribers = set(self.subscriptions.get(channel, set()))
            global_subscribers = set(self.subscriptions.get("global", set()))
            targets = subscribers.union(global_subscribers)

        if not targets:
            return

        payload = json.dumps(message)
        send_tasks = [self._send_text(connection, payload) for connection in targets]
        await asyncio.gather(*send_tasks, return_exceptions=True)

    async def _send_text(self, websocket: WebSocket, payload: str) -> None:
        try:
            await websocket.send_text(payload)
        except Exception as exc:
            logger.warning("Failed to send websocket payload: %s", exc)
            await self.disconnect(websocket)

    def _channel_for_match(self, match_id: Optional[int]) -> str:
        return f"match:{match_id}" if match_id is not None else "global"


class RedisPubSubBroker:
    def __init__(self, manager: ConnectionManager, channel: str = LIVE_UPDATE_CHANNEL) -> None:
        self.manager = manager
        self.channel = channel
        self._task: Optional[asyncio.Task[None]] = None
        self._pubsub: Optional[PubSub] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._pubsub = async_redis.pubsub()
        await self._pubsub.subscribe(self.channel)
        logger.info("Subscribed to Redis channel %s", self.channel)

        while self._running:
            try:
                message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    continue

                if message.get("type") != "message":
                    continue

                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")

                payload = json.loads(data)
                channel = payload.get("channel")
                await self.manager.broadcast(payload, channel=channel)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Redis pubsub loop error: %s", exc)
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(self.channel)
                await self._pubsub.close()
            except Exception:
                pass


manager = ConnectionManager()
broker = RedisPubSubBroker(manager)


async def publish_match_update(match_data: Dict[str, Any]) -> None:
    payload = _format_match_payload(match_data)
    await async_redis.publish(LIVE_UPDATE_CHANNEL, json.dumps(payload))
