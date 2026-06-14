"""Handicap parsing utilities for the Myanmar Odds domain."""

import re


_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def parse_handicap_value(handicap: float | int | str) -> float:
    """Extract and normalize a handicap value from a raw string or number."""
    if isinstance(handicap, (int, float)):
        return abs(float(handicap))

    text = str(handicap).strip()
    if not text:
        raise ValueError("Handicap value cannot be empty")

    match = _NUMBER_RE.search(text)
    if not match:
        raise ValueError(f"Unable to parse handicap value: {handicap}")

    value = float(match.group(0))
    return abs(value)
