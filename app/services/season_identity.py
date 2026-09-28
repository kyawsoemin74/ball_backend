def normalize_season(value: object) -> str:
    if isinstance(value, bool):
        raise ValueError("Season must be a positive integer year")

    if isinstance(value, int):
        season_value = value
    elif isinstance(value, str):
        season_text = value.strip()
        if not season_text or not season_text.isascii() or not season_text.isdigit():
            raise ValueError("Season must be a positive integer year")
        season_value = int(season_text)
    else:
        raise ValueError("Season must be a positive integer year")

    if season_value <= 0:
        raise ValueError("Season must be a positive integer year")
    return str(season_value)