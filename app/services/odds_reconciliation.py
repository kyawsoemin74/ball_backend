import hashlib
import json
from datetime import timezone

from app.services.odds_value import format_canonical_odd


ATTEMPT_IN_PROGRESS = "ATTEMPT_IN_PROGRESS"
COMMIT_PENDING = "COMMIT_PENDING"
COMMIT_CONFIRMED_CACHE_PENDING = "COMMIT_CONFIRMED_CACHE_PENDING"
COMMIT_NOT_APPLIED_STATE = "COMMIT_NOT_APPLIED"
MANUAL_REVIEW = "MANUAL_REVIEW"
RESOLVED = "RESOLVED"
PENDING_RECONCILIATION_STATES = (
    ATTEMPT_IN_PROGRESS,
    COMMIT_PENDING,
    COMMIT_CONFIRMED_CACHE_PENDING,
)
BLOCKED_RECONCILIATION_STATES = (ATTEMPT_IN_PROGRESS, COMMIT_PENDING, MANUAL_REVIEW)


def _value(row: dict | object, key: str):
    return row.get(key) if isinstance(row, dict) else getattr(row, key)


def snapshot_fingerprint(rows: list[dict | object]) -> str:
    canonical_rows = []
    for row in rows:
        timestamp = (
            row.get("last_updated") or row.get("observed_at")
            if isinstance(row, dict)
            else getattr(row, "last_updated", None) or getattr(row, "observed_at", None)
        )
        if timestamp is not None:
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("Odds reconciliation timestamps must be timezone-aware")
            timestamp = timestamp.astimezone(timezone.utc).isoformat()
        canonical_rows.append(
            (
                _value(row, "bookmaker_name"),
                _value(row, "market_name"),
                _value(row, "selection"),
                format_canonical_odd(_value(row, "odd_value")),
                _value(row, "myanmar_odd"),
                (
                    row.get("giving_team")
                    if isinstance(row, dict)
                    else getattr(row, "giving_team", None)
                ),
                timestamp,
                (
                    row.get("source_provider", "api-football")
                    if isinstance(row, dict)
                    else getattr(row, "source_provider", "api-football")
                ),
            )
        )

    serialized_rows = sorted(
        json.dumps(row, ensure_ascii=True, separators=(",", ":"))
        for row in canonical_rows
    )
    payload = json.dumps(serialized_rows, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
