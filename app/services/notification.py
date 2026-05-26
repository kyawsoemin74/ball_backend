import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

from app.redis import async_redis

FCM_SERVICE_ACCOUNT_PATH = os.getenv("FCM_SERVICE_ACCOUNT_PATH")
FCM_SERVICE_ACCOUNT_JSON = os.getenv("FCM_SERVICE_ACCOUNT_JSON")
FCM_DEFAULT_TOPIC = os.getenv("FCM_DEFAULT_TOPIC", "fover_events")
FCM_RETRY_MAX = int(os.getenv("FCM_RETRY_MAX", "3"))
FCM_RETRY_DELAY_SECONDS = int(os.getenv("FCM_RETRY_DELAY_SECONDS", "10"))

logger = logging.getLogger(__name__)

NOTIFICATION_QUEUE_KEY = "fover:notification:queue"
NOTIFICATION_DEAD_LETTER_KEY = "fover:notification:dead"
PROCESSED_EVENT_SET = "fover:notification:processed:{}"


class FirebaseNotifier:
    def __init__(self) -> None:
        self._enabled = False
        self._app = None
        self._messaging = None
        self._initialize()

    def _initialize(self) -> None:
        if not FCM_SERVICE_ACCOUNT_JSON and not FCM_SERVICE_ACCOUNT_PATH:
            logger.warning("Firebase Cloud Messaging is not configured. Notifications will be disabled.")
            return

        try:
            import firebase_admin
            from firebase_admin import credentials, initialize_app, messaging

            if firebase_admin._apps:
                self._app = firebase_admin.get_app()
            else:
                if FCM_SERVICE_ACCOUNT_JSON:
                    service_account_info = json.loads(FCM_SERVICE_ACCOUNT_JSON)
                    cred = credentials.Certificate(service_account_info)
                else:
                    cred = credentials.Certificate(FCM_SERVICE_ACCOUNT_PATH)
                self._app = initialize_app(cred)

            self._messaging = messaging
            self._enabled = True
            logger.info("Firebase notifier initialized successfully.")
        except ImportError:
            logger.error("firebase-admin package not installed. Install firebase-admin to enable notifications.")
        except Exception as exc:
            logger.error("Could not initialize Firebase Admin SDK: %s", exc)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def send(self, title: str, body: str, data: Dict[str, str], token: Optional[str] = None, topic: Optional[str] = None) -> str:
        if not self._enabled or self._messaging is None:
            raise RuntimeError("Firebase Cloud Messaging is not configured.")

        if token is None and topic is None:
            topic = FCM_DEFAULT_TOPIC

        if token is None and not topic:
            raise ValueError("Either token or topic must be provided for FCM notifications.")

        message_payload: Dict[str, Any] = {
            "notification": self._messaging.Notification(title=title, body=body),
            "data": {k: str(v) for k, v in data.items()} if data else {},
        }

        if token:
            message_payload["token"] = token
        else:
            message_payload["topic"] = topic

        return await asyncio.to_thread(self._send_sync, message_payload)

    def _send_sync(self, message_payload: Dict[str, Any]) -> str:
        message = self._messaging.Message(**message_payload)
        return self._messaging.send(message)


notifier = FirebaseNotifier()


async def enqueue_notification(
    event_type: str,
    match_id: int,
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
    topic: Optional[str] = None,
    retry: int = 0,
) -> None:
    payload = {
        "event_type": event_type,
        "match_id": match_id,
        "title": title,
        "body": body,
        "data": {k: str(v) for k, v in (data or {}).items()},
        "token": token,
        "topic": topic or FCM_DEFAULT_TOPIC,
        "retry": retry,
    }
    await async_redis.rpush(NOTIFICATION_QUEUE_KEY, json.dumps(payload))
    logger.debug("Enqueued notification %s for match %s", event_type, match_id)


async def _send_notification(payload: Dict[str, Any]) -> None:
    title = payload["title"]
    body = payload["body"]
    data = payload.get("data", {})
    token = payload.get("token")
    topic = payload.get("topic")
    await notifier.send(title=title, body=body, data=data, token=token, topic=topic)


class NotificationWorker:
    def __init__(self, max_retries: int = FCM_RETRY_MAX, retry_delay: int = FCM_RETRY_DELAY_SECONDS) -> None:
        self.queue_key = NOTIFICATION_QUEUE_KEY
        self.dead_letter_key = NOTIFICATION_DEAD_LETTER_KEY
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._running = False

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        logger.info("Notification worker started.")

        while self._running:
            message = await async_redis.brpop(self.queue_key, timeout=5)
            if not message:
                continue

            _, raw_payload = message
            if isinstance(raw_payload, bytes):
                raw_payload = raw_payload.decode("utf-8")

            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                logger.error("Invalid notification payload discarded: %s", raw_payload)
                continue

            await self._process_payload(payload)

    async def stop(self) -> None:
        self._running = False
        logger.info("Notification worker stopping.")

    async def _process_payload(self, payload: Dict[str, Any]) -> None:
        retry = int(payload.get("retry", 0))
        try:
            await _send_notification(payload)
            logger.info("Notification sent: %s", payload["event_type"])
        except Exception as exc:
            retry += 1
            payload["retry"] = retry
            payload["last_error"] = str(exc)

            if retry <= self.max_retries:
                logger.warning("Notification send failed, retrying %s/%s: %s", retry, self.max_retries, exc)
                await async_redis.rpush(self.queue_key, json.dumps(payload))
                await asyncio.sleep(self.retry_delay)
            else:
                logger.error("Notification failed permanently and moved to dead-letter queue: %s", exc)
                await async_redis.rpush(self.dead_letter_key, json.dumps(payload))


notification_worker = NotificationWorker()


def _render_match_context(match_data: Dict[str, Any]) -> Dict[str, str]:
    return {
        "match_id": str(match_data.get("match_id", "")),
        "status": match_data.get("status", ""),
        "elapsed": str(match_data.get("elapsed", "")),
        "home_team": match_data.get("home_team", ""),
        "away_team": match_data.get("away_team", ""),
        "home_score": str(match_data.get("home_score", "")),
        "away_score": str(match_data.get("away_score", "")),
    }


def build_match_start_notification(match_data: Dict[str, Any]) -> Dict[str, Any]:
    title = f"Match Started: {match_data.get('home_team')} vs {match_data.get('away_team')}"
    body = f"{match_data.get('home_team')} vs {match_data.get('away_team')} has started. Score {match_data.get('home_score')} - {match_data.get('away_score')}"
    return {
        "event_type": "match_start",
        "match_id": match_data["match_id"],
        "title": title,
        "body": body,
        "data": _render_match_context(match_data),
        "topic": f"fover_match_{match_data['match_id']}",
    }


def build_kickoff_notification(match_data: Dict[str, Any]) -> Dict[str, Any]:
    title = f"Kickoff: {match_data.get('home_team')} vs {match_data.get('away_team')}"
    body = f"The game is underway. {match_data.get('home_team')} {match_data.get('home_score')} - {match_data.get('away_score')} {match_data.get('away_team')}"
    return {
        "event_type": "kickoff_alert",
        "match_id": match_data["match_id"],
        "title": title,
        "body": body,
        "data": _render_match_context(match_data),
        "topic": f"fover_match_{match_data['match_id']}",
    }


def build_goal_notification(match_data: Dict[str, Any], scoring_team: str) -> Dict[str, Any]:
    title = f"Goal! {scoring_team}"
    body = f"{match_data.get('home_team')} {match_data.get('home_score')} - {match_data.get('away_score')} {match_data.get('away_team')} ({match_data.get('elapsed')}')"
    return {
        "event_type": "goal_alert",
        "match_id": match_data["match_id"],
        "title": title,
        "body": body,
        "data": {**_render_match_context(match_data), "scoring_team": scoring_team},
        "topic": f"fover_match_{match_data['match_id']}",
    }


def build_red_card_notification(match_data: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    player = event.get("player", "A player")
    team = event.get("team", "Team")
    title = f"Red Card: {player}"
    body = f"{player} of {team} has been sent off in {match_data.get('home_team')} vs {match_data.get('away_team')}"
    data = {**_render_match_context(match_data), "type": "red_card", "player": player, "team": team, "detail": event.get("detail", "red card")}
    return {
        "event_type": "red_card",
        "match_id": match_data["match_id"],
        "title": title,
        "body": body,
        "data": data,
        "topic": f"fover_match_{match_data['match_id']}",
    }


async def gather_red_card_notifications(match_data: Dict[str, Any], fixture: Dict[str, Any]) -> list[Dict[str, Any]]:
    notifications: list[Dict[str, Any]] = []
    fixture_info = fixture.get("fixture", {})
    match_id = fixture_info.get("id")
    if not match_id:
        return notifications

    events = fixture.get("events") or []
    if not isinstance(events, list):
        return notifications

    processed_key = PROCESSED_EVENT_SET.format(match_id)
    for event in events:
        if not isinstance(event, dict):
            continue

        event_type = str(event.get("type", "")).lower()
        event_detail = str(event.get("detail", "")).lower()
        if event_type != "card" and "red card" not in event_detail:
            continue

        event_id = event.get("id") or f"{event.get('time', '')}-{event.get('player', '')}-{event_detail}"
        if not event_id:
            continue

        already_processed = await async_redis.sismember(processed_key, str(event_id))
        if already_processed:
            continue

        await async_redis.sadd(processed_key, str(event_id))
        notifications.append(build_red_card_notification(match_data, event))

    return notifications
