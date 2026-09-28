import math
from decimal import Decimal, InvalidOperation
from typing import Any

MARKET_TYPE_BY_ID = {
    1: "MATCH_WINNER",
    4: "ASIAN_HANDICAP",
    5: "GOALS_OVER_UNDER",
    45: "CORNERS_OVER_UNDER",
}
SUPPORTED_MARKET_IDS = set(MARKET_TYPE_BY_ID)


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_bookmaker_name(value: Any) -> str:
    return _normalize_text(value).casefold()


def classify_market_id(raw_market_id: Any) -> str:
    if isinstance(raw_market_id, bool):
        return ""
    if isinstance(raw_market_id, int):
        int_id = raw_market_id
    elif isinstance(raw_market_id, str) and raw_market_id.strip().isdigit():
        int_id = int(raw_market_id.strip())
    else:
        return ""
    return MARKET_TYPE_BY_ID.get(int_id, "")


def _normalize_market_name(raw_market_id: Any) -> str:
    return classify_market_id(raw_market_id)


def _canonical_selection(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    return text


def normalize_odds_value(value: Any) -> float:
    numeric = _parse_decimal(value, "odd")
    if numeric <= 0:
        raise ValueError("invalid_odd_value")
    try:
        float_value = float(numeric)
    except OverflowError as exc:
        raise ValueError("invalid_odd_value") from exc
    if not math.isfinite(float_value):
        raise ValueError("invalid_odd_value")
    return float_value


def _parse_decimal(value: Any, field_name: str) -> Decimal:
    missing_reason = f"missing_{field_name}"
    invalid_reason = f"invalid_{field_name}"
    if value is None or isinstance(value, bool):
        raise ValueError(missing_reason if value is None else invalid_reason)
    text = _normalize_text(value)
    if not text:
        raise ValueError(missing_reason)
    try:
        numeric = Decimal(text)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(invalid_reason) from exc
    if not numeric.is_finite():
        raise ValueError(invalid_reason)
    return numeric


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _normalize_selection(market_id: int, value: Any) -> tuple[str, str, Decimal | None]:
    if not isinstance(value, str):
        raise ValueError("missing_selection")
    text = value.strip()
    if not text:
        raise ValueError("missing_selection")

    if market_id == 1:
        outcomes = {"home": "Home", "draw": "Draw", "away": "Away"}
        normalized = outcomes.get(text.casefold())
        if normalized is None:
            raise ValueError("invalid_selection")
        return normalized, normalized.casefold(), None

    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        raise ValueError("missing_line")

    side_text, line_text = parts
    side = side_text.casefold()
    if market_id == 4:
        if side not in {"home", "away"}:
            raise ValueError("invalid_selection")
        line = _parse_decimal(line_text, "line")
        signed_line = _canonical_decimal(line)
        if line > 0:
            signed_line = f"+{signed_line}"
        canonical_selection = f"{side.title()} {signed_line}"
        return canonical_selection, side, abs(line)

    if market_id in {5, 45}:
        if side not in {"over", "under"}:
            raise ValueError("invalid_selection")
        line = _parse_decimal(line_text, "line")
        if line < 0:
            raise ValueError("invalid_line")
        canonical_selection = f"{side.title()} {_canonical_decimal(line)}"
        return canonical_selection, side, line

    raise ValueError("unsupported_market")


def _normalized_record(
    local_match_id: int,
    market_id: int,
    market_type: str,
    bookmaker_name: str,
    selection: str,
    side: str,
    line_key: Decimal | None,
    odd: Decimal,
) -> dict:
    return {
        "fixture_id": local_match_id,
        "bookmaker_name": bookmaker_name,
        "market_name": market_type,
        "selection": selection,
        "odd_value": _canonical_decimal(odd),
        "myanmar_odd": None,
        "source_market_id": market_id,
        "side": side,
        "line_key": line_key,
        "odd_decimal": odd,
    }


def _persistable_record(record: dict) -> dict:
    return {
        "fixture_id": record["fixture_id"],
        "bookmaker_name": record["bookmaker_name"],
        "market_name": record["market_name"],
        "selection": record["selection"],
        "odd_value": record["odd_value"],
        "myanmar_odd": record["myanmar_odd"],
    }


def _select_best_pair(
    market_records: list[dict],
    market_type: str,
    metrics: dict[str, int] | None = None,
) -> tuple[list[dict], list[dict]]:
    pairs: dict[Decimal, dict[str, dict]] = {}
    rejected: list[dict] = []
    for record in market_records:
        line_key = record["line_key"]
        side = record["side"]
        pair = pairs.setdefault(line_key, {})
        if side in pair:
            rejected.append({
                "reason": "duplicate_selection",
                "market_name": market_type,
                "selection": record["selection"],
            })
            continue
        pair[side] = record

    if rejected:
        return [], rejected

    first_side, second_side = ("home", "away") if market_type == "ASIAN_HANDICAP" else ("over", "under")
    complete_pairs = [
        (line_key, pair)
        for line_key, pair in pairs.items()
        if first_side in pair and second_side in pair
    ]
    if metrics is not None:
        metrics["valid_pair_candidates"] += len(complete_pairs)
    if not complete_pairs:
        return [], [{"reason": "missing_complete_pair", "market_name": market_type}]

    def pair_rank(candidate: tuple[Decimal, dict]) -> tuple[Decimal, Decimal]:
        line_key, pair = candidate
        first_distance = abs(pair[first_side]["odd_decimal"] - Decimal("2.00"))
        second_distance = abs(pair[second_side]["odd_decimal"] - Decimal("2.00"))
        return first_distance + second_distance, line_key

    selected_pair = min(complete_pairs, key=pair_rank)[1]
    if metrics is not None:
        metrics["selected_pairs"] += 1
    return [selected_pair[first_side], selected_pair[second_side]], []


def build_odds_business_key(local_match_id: Any, bookmaker_name: Any, market_name: Any, selection: Any, handicap: Any | None = None) -> tuple:
    match_id = int(local_match_id)
    bookmaker = _normalize_text(bookmaker_name).lower()
    market = _normalize_text(market_name).lower()
    selection_text = _canonical_selection(selection).lower()
    handicap_text = _normalize_text(handicap).lower() if handicap is not None else ""
    return (match_id, bookmaker, market, selection_text, handicap_text)


def _count_raw_provider_records(payload: Any) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get("response"), list):
        return 0
    count = 0
    for item in payload["response"]:
        if not isinstance(item, dict) or not isinstance(item.get("bookmakers"), list):
            continue
        for bookmaker in item["bookmakers"]:
            if not isinstance(bookmaker, dict) or not isinstance(bookmaker.get("bets"), list):
                continue
            for bet in bookmaker["bets"]:
                if isinstance(bet, dict) and isinstance(bet.get("values"), list):
                    count += len(bet["values"])
    return count


def normalize_odds_response(
    payload: Any,
    provider_fixture_id: Any,
    local_match_id: Any,
    *,
    metrics: dict[str, int] | None = None,
) -> tuple[list[dict], list[dict]]:
    counters = {
        "raw_provider_records": _count_raw_provider_records(payload),
        "bookmaker_filtered_records": 0,
        "market_filtered_records": 0,
        "valid_pair_candidates": 0,
        "selected_pairs": 0,
    }

    def finish(records: list[dict], rejected: list[dict]) -> tuple[list[dict], list[dict]]:
        if metrics is not None:
            metrics.clear()
            metrics.update(counters)
            metrics["ignored_records"] = max(
                counters["raw_provider_records"] - len(records) - len(rejected),
                0,
            )
            metrics["rejected_records"] = len(rejected)
            metrics["final_persisted_records"] = len(records)
        return records, rejected

    if not isinstance(payload, dict):
        return finish([], [{"reason": "invalid_payload", "provider_fixture_id": provider_fixture_id}])

    response_items = payload.get("response")
    if not isinstance(response_items, list):
        return finish([], [{"reason": "invalid_payload", "provider_fixture_id": provider_fixture_id}])
    if not response_items:
        return finish([], [])

    rejected: list[dict] = []
    market_records: dict[str, list[dict]] = {market_type: [] for market_type in MARKET_TYPE_BY_ID.values()}
    found_bet365 = False

    for item in response_items:
        if not isinstance(item, dict):
            rejected.append({"reason": "invalid_payload", "provider_fixture_id": provider_fixture_id})
            continue

        fixture = item.get("fixture")
        if not isinstance(fixture, dict):
            rejected.append({"reason": "missing_fixture", "provider_fixture_id": provider_fixture_id})
            continue

        actual_fixture_id = fixture.get("id")
        if actual_fixture_id is None:
            rejected.append({"reason": "missing_fixture", "provider_fixture_id": provider_fixture_id})
            continue

        try:
            if int(actual_fixture_id) != int(provider_fixture_id):
                rejected.append({"reason": "fixture_mismatch", "provider_fixture_id": provider_fixture_id})
                continue
        except (TypeError, ValueError):
            rejected.append({"reason": "fixture_mismatch", "provider_fixture_id": provider_fixture_id})
            continue

        bookmakers = item.get("bookmakers")
        if not isinstance(bookmakers, list):
            rejected.append({"reason": "missing_bookmakers", "provider_fixture_id": provider_fixture_id})
            continue

        for bookmaker in bookmakers:
            if not isinstance(bookmaker, dict):
                continue

            bookmaker_name = _normalize_text(bookmaker.get("name"))
            if normalize_bookmaker_name(bookmaker_name) != "bet365":
                continue
            found_bet365 = True

            bets = bookmaker.get("bets")
            if not isinstance(bets, list):
                rejected.append({"reason": "missing_bets", "bookmaker_name": bookmaker_name, "provider_fixture_id": provider_fixture_id})
                continue

            seen_market_ids: set[int] = set()
            for bet in bets:
                if not isinstance(bet, dict):
                    rejected.append({"reason": "invalid_market", "bookmaker_name": bookmaker_name, "provider_fixture_id": provider_fixture_id})
                    continue

                if isinstance(bet.get("values"), list):
                    counters["bookmaker_filtered_records"] += len(bet["values"])

                market_id = bet.get("id")
                market_name = _normalize_market_name(market_id)
                if not market_name:
                    continue

                normalized_market_id = int(market_id)
                if normalized_market_id in seen_market_ids:
                    rejected.append({"reason": "duplicate_market", "bookmaker_name": "Bet365", "market_name": market_name, "provider_fixture_id": provider_fixture_id})
                    continue
                seen_market_ids.add(normalized_market_id)

                values = bet.get("values")
                if not isinstance(values, list):
                    rejected.append({"reason": "missing_values", "bookmaker_name": bookmaker_name, "market_name": market_name, "provider_fixture_id": provider_fixture_id})
                    continue
                counters["market_filtered_records"] += len(values)

                for value in values:
                    if not isinstance(value, dict):
                        rejected.append({"reason": "invalid_selection", "bookmaker_name": bookmaker_name, "market_name": market_name, "provider_fixture_id": provider_fixture_id})
                        continue

                    try:
                        selection, side, line_key = _normalize_selection(
                            normalized_market_id,
                            value.get("value", value.get("selection")),
                        )
                    except ValueError as exc:
                        rejected.append({"reason": str(exc), "bookmaker_name": "Bet365", "market_name": market_name, "provider_fixture_id": provider_fixture_id})
                        continue

                    try:
                        odd_decimal = _parse_decimal(value.get("odd"), "odd_value")
                    except ValueError:
                        rejected.append({"reason": "invalid_odd_value", "bookmaker_name": "Bet365", "market_name": market_name, "selection": selection, "provider_fixture_id": provider_fixture_id})
                        continue
                    if odd_decimal <= 0:
                        rejected.append({"reason": "invalid_odd_value", "bookmaker_name": "Bet365", "market_name": market_name, "selection": selection, "provider_fixture_id": provider_fixture_id})
                        continue

                    market_records[market_name].append(
                        _normalized_record(
                            int(local_match_id),
                            normalized_market_id,
                            market_name,
                            "Bet365",
                            selection,
                            side,
                            line_key,
                            odd_decimal,
                        )
                    )

    if not found_bet365:
        rejected.append({"reason": "missing_bet365_bookmaker", "provider_fixture_id": provider_fixture_id})

    if not rejected:
        winner_records = market_records["MATCH_WINNER"]
        winner_by_selection: dict[str, dict] = {}
        for record in winner_records:
            if record["side"] in winner_by_selection:
                rejected.append({"reason": "duplicate_selection", "market_name": "MATCH_WINNER", "selection": record["selection"], "provider_fixture_id": provider_fixture_id})
                continue
            winner_by_selection[record["side"]] = record
        for outcome in ("home", "draw", "away"):
            if outcome not in winner_by_selection:
                rejected.append({"reason": "missing_match_winner_outcome", "selection": outcome, "provider_fixture_id": provider_fixture_id})

    selected_records: list[dict] = []
    if not rejected:
        selected_records.extend(
            winner_by_selection[outcome]
            for outcome in ("home", "draw", "away")
        )
        for market_type in ("ASIAN_HANDICAP", "GOALS_OVER_UNDER", "CORNERS_OVER_UNDER"):
            selected_pair, pair_rejected = _select_best_pair(
                market_records[market_type],
                market_type,
                counters,
            )
            if pair_rejected:
                rejected.extend(pair_rejected)
            else:
                selected_records.extend(selected_pair)

    if rejected:
        return finish([], rejected)

    return finish([_persistable_record(record) for record in selected_records], [])
