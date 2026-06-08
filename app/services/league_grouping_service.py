from collections import defaultdict
from typing import Iterable

from app.models.league import League


class LeagueGroupingService:
    """Builds frontend-ready league groups with featured leagues first."""

    def build_groups(self, leagues: Iterable[League]) -> list[dict]:
        ordered = sorted(
            leagues,
            key=lambda league: (
                0 if getattr(league, "is_featured", False) else 1,
                int(getattr(league, "display_order", 999) or 999),
                str(getattr(league, "name", "")).lower(),
            ),
        )

        featured = [self._serialize(league) for league in ordered if getattr(league, "is_featured", False)]
        remaining = [league for league in ordered if not getattr(league, "is_featured", False)]

        groups = []
        if featured:
            groups.append({"type": "featured", "title": "Featured Leagues", "leagues": featured})

        by_country = defaultdict(list)
        for league in remaining:
            by_country[str(getattr(league, "country") or "Unknown").strip() or "Unknown"].append(league)

        for country in sorted(by_country.keys(), key=lambda value: value.lower()):
            country_leagues = sorted(
                by_country[country],
                key=lambda league: str(getattr(league, "name", "")).lower(),
            )
            groups.append({"type": "country", "country": country, "leagues": [self._serialize(league) for league in country_leagues]})

        return groups

    def _serialize(self, league: League) -> dict:
        return {
            "league_id": league.league_id,
            "name": league.name,
            "country": league.country,
            "country_code": getattr(league, "country_code", None),
            "logo": league.logo,
            "season": league.season,
            "is_featured": bool(getattr(league, "is_featured", False)),
            "display_order": int(getattr(league, "display_order", 999) or 999),
        }
