"""Base index mapping for Asian Handicap values."""

from decimal import Decimal, ROUND_HALF_UP

_SUPPORTED_STEP = Decimal("0.25")
_MAX_SUPPORTED_HANDICAP = Decimal("25.00")

BASE_INDEX_MAP = {}
for quarter_step in range(int(_MAX_SUPPORTED_HANDICAP * 4) + 1):
    handicap = Decimal(quarter_step) / Decimal("4")
    BASE_INDEX_MAP[handicap.quantize(_SUPPORTED_STEP, rounding=ROUND_HALF_UP)] = int((handicap * Decimal("200")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _normalize_handicap(handicap: float | int | str) -> Decimal:
    if isinstance(handicap, Decimal):
        value = handicap.copy_abs()
    else:
        text = str(handicap).strip()
        if not text:
            raise ValueError("Handicap value cannot be empty")

        try:
            value = Decimal(text).copy_abs()
        except Exception as exc:
            raise ValueError(f"Unable to parse handicap value: {handicap}") from exc

    quarter_value = value * Decimal("4")
    if quarter_value != quarter_value.to_integral_value(rounding=ROUND_HALF_UP):
        raise ValueError(
            f"Unsupported handicap {handicap!r}. Myanmar Odds only supports quarter-goal increments from 0.00 to 25.00."
        )

    return value.quantize(_SUPPORTED_STEP, rounding=ROUND_HALF_UP)


def get_base_index(handicap: float | int | str) -> int:
    """Return the Myanmar base index for an explicitly supported handicap value."""
    normalized = _normalize_handicap(handicap)

    if normalized not in BASE_INDEX_MAP:
        raise ValueError(
            f"Unsupported handicap {handicap!r}. Myanmar Odds only supports quarter-goal increments from 0.00 to 25.00."
        )

    return BASE_INDEX_MAP[normalized]
