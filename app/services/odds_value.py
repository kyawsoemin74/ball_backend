from decimal import Decimal, InvalidOperation


MAX_ODDS_VALUE = Decimal("999999.999999")
MAX_ODDS_FRACTIONAL_PLACES = 6


def canonicalize_odd_value(value: object) -> Decimal:
    """Validate and canonicalize an odds value representable by Numeric(12, 6)."""
    try:
        odd = Decimal(str(value).strip())
    except (AttributeError, InvalidOperation, ValueError) as exc:
        raise ValueError("invalid odd") from exc

    if not odd.is_finite() or odd <= 0 or odd > MAX_ODDS_VALUE:
        raise ValueError("odd is outside the Numeric(12, 6) range")

    parts = odd.as_tuple()
    digits = list(parts.digits)
    exponent = parts.exponent
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if exponent < -MAX_ODDS_FRACTIONAL_PLACES:
        raise ValueError("odd has more than six meaningful fractional places")

    return Decimal((parts.sign, tuple(digits), exponent))


def format_canonical_odd(value: object) -> str:
    return format(canonicalize_odd_value(value), "f")
