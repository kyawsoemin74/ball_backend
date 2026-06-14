"""Canonical ladder lookup mapping for Myanmar odds labels."""

MIN_INDEX = 10
MAX_INDEX = 25 * 200 + 100


def generate_ladder_map(max_goal: int = 25) -> dict[int, str]:
    """Generate the canonical Myanmar Odds ladder map once at import time."""
    ladder_map: dict[int, str] = {}

    for index in range(10, 101, 10):
        ladder_map[index] = f"-{index}"

    for goal in range(1, max_goal + 1):
        block_start = 200 * (goal - 1)

        for index in range(110 + block_start, 200 * goal + 1, 10):
            offset = 200 * goal - index
            ladder_map[index] = f"{goal}=" if offset == 0 else f"{goal}+{offset}"

        for index in range(200 * goal + 10, 200 * goal + 101, 10):
            offset = index - 200 * goal
            ladder_map[index] = f"{goal}-{offset}"

    return ladder_map


LADDER_MAP = generate_ladder_map(max_goal=25)

def ladder_label_for_index(index: int) -> str:
    """Convert a numeric ladder index into its display label."""
    try:
        normalized_index = int(index)
    except (TypeError, ValueError):
        return "OUT_OF_RANGE"

    if normalized_index < MIN_INDEX or normalized_index > MAX_INDEX:
        return "OUT_OF_RANGE"

    if normalized_index % 10 != 0:
        normalized_index = int(round(normalized_index / 10.0) * 10)

    return LADDER_MAP.get(normalized_index, "OUT_OF_RANGE")

