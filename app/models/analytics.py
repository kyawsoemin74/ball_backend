from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import mapped_column
from sqlalchemy.sql import func

from app.db import Base


class AnalyticsMatchTeamStatistic(Base):
    __tablename__ = "analytics_match_team_statistics"
    __table_args__ = (
        UniqueConstraint("match_id", "team_id", "statistic_name", name="uq_analytics_statistics_business"),
        CheckConstraint("value_type IN ('integer', 'decimal', 'percent', 'text', 'boolean')", name="ck_analytics_statistics_value_type"),
        CheckConstraint("((value_type = 'integer' AND value_integer IS NOT NULL AND value_numeric IS NULL AND value_text IS NULL AND value_boolean IS NULL) OR (value_type IN ('decimal', 'percent') AND value_integer IS NULL AND value_numeric IS NOT NULL AND value_text IS NULL AND value_boolean IS NULL) OR (value_type = 'text' AND value_integer IS NULL AND value_numeric IS NULL AND value_text IS NOT NULL AND value_boolean IS NULL) OR (value_type = 'boolean' AND value_integer IS NULL AND value_numeric IS NULL AND value_text IS NULL AND value_boolean IS NOT NULL))", name="ck_analytics_statistics_typed_value"),
        Index("ix_analytics_statistics_match", "match_id"),
        Index("ix_analytics_statistics_team", "team_id"),
    )
    statistic_fact_id = mapped_column(BigInteger, Identity(), primary_key=True)
    match_id = mapped_column(Integer, ForeignKey("matches.fixture_id"), nullable=False)
    team_id = mapped_column(Integer, ForeignKey("teams.team_id"), nullable=False)
    provider_team_id = mapped_column(Text, nullable=False)
    statistic_name = mapped_column(Text, nullable=False)
    statistic_label = mapped_column(Text)
    value_type = mapped_column(Text, nullable=False)
    value_integer = mapped_column(BigInteger)
    value_numeric = mapped_column(Numeric(20, 6))
    value_text = mapped_column(Text)
    value_boolean = mapped_column(Boolean)
    source_provider = mapped_column(Text, server_default="api-football", nullable=False)
    observed_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AnalyticsTeamSeasonStanding(Base):
    __tablename__ = "analytics_team_season_standings"
    __table_args__ = (
        UniqueConstraint("league_season_id", "team_id", name="uq_analytics_standings_business"),
        Index("ix_analytics_standings_league", "league_id"),
        Index("ix_analytics_standings_season", "league_season_id"),
        Index("ix_analytics_standings_team", "team_id"),
    )
    standing_fact_id = mapped_column(BigInteger, Identity(), primary_key=True)
    league_id = mapped_column(Integer, ForeignKey("leagues.league_id"), nullable=False)
    league_season_id = mapped_column(Integer, ForeignKey("league_seasons.id"), nullable=False)
    team_id = mapped_column(Integer, ForeignKey("teams.team_id"), nullable=False)
    provider_team_id = mapped_column(Text, nullable=False)
    position = mapped_column(Integer, nullable=False)
    points = mapped_column(Integer, nullable=False)
    played = mapped_column(Integer, nullable=False)
    won = mapped_column(Integer, nullable=False)
    drawn = mapped_column(Integer, nullable=False)
    lost = mapped_column(Integer, nullable=False)
    goals_for = mapped_column(Integer, nullable=False)
    goals_against = mapped_column(Integer, nullable=False)
    goal_difference = mapped_column(Integer, nullable=False)
    group_name = mapped_column(Text)
    form = mapped_column(Text)
    description = mapped_column(Text)
    source_provider = mapped_column(Text, server_default="api-football", nullable=False)
    observed_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AnalyticsMatchOddsSnapshot(Base):
    __tablename__ = "analytics_match_odds_snapshots"
    __table_args__ = (
        UniqueConstraint("match_id", "bookmaker_name", "market_name", "selection", name="uq_analytics_odds_business"),
        Index("ix_analytics_odds_match", "match_id"),
        Index("ix_analytics_odds_market", "bookmaker_name", "market_name"),
    )
    odds_fact_id = mapped_column(BigInteger, Identity(), primary_key=True)
    match_id = mapped_column(Integer, ForeignKey("matches.fixture_id"), nullable=False)
    bookmaker_name = mapped_column(Text, nullable=False)
    market_name = mapped_column(Text, nullable=False)
    selection = mapped_column(Text, nullable=False)
    odd_value = mapped_column(Numeric(12, 6), nullable=False)
    myanmar_odd = mapped_column(Text)
    source_provider = mapped_column(Text, server_default="api-football", nullable=False)
    observed_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AnalyticsH2HSnapshot(Base):
    __tablename__ = "analytics_h2h_snapshots"
    __table_args__ = (
        UniqueConstraint("source_provider", "team_low_id", "team_high_id", "source_fixture_id", name="uq_analytics_h2h_business"),
        CheckConstraint("team_low_id < team_high_id", name="ck_analytics_h2h_team_order"),
        Index("ix_analytics_h2h_pair", "team_low_id", "team_high_id"),
        Index("ix_analytics_h2h_source_fixture", "source_provider", "source_fixture_id"),
    )
    h2h_fact_id = mapped_column(BigInteger, Identity(), primary_key=True)
    team_low_id = mapped_column(Integer, ForeignKey("teams.team_id"), nullable=False)
    team_high_id = mapped_column(Integer, ForeignKey("teams.team_id"), nullable=False)
    source_provider = mapped_column(Text, server_default="api-football", nullable=False)
    source_fixture_id = mapped_column(BigInteger, nullable=False)
    fixture_at = mapped_column(DateTime(timezone=True), nullable=False)
    fixture_status = mapped_column(Text, nullable=False)
    home_provider_team_id = mapped_column(BigInteger, nullable=False)
    away_provider_team_id = mapped_column(BigInteger, nullable=False)
    home_goals = mapped_column(Integer)
    away_goals = mapped_column(Integer)
    halftime_home_score = mapped_column(Integer)
    halftime_away_score = mapped_column(Integer)
    fulltime_home_score = mapped_column(Integer)
    fulltime_away_score = mapped_column(Integer)
    extratime_home_score = mapped_column(Integer)
    extratime_away_score = mapped_column(Integer)
    penalty_home_score = mapped_column(Integer)
    penalty_away_score = mapped_column(Integer)
    observed_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AnalyticsMatchLineup(Base):
    __tablename__ = "analytics_match_lineups"
    __table_args__ = (
        UniqueConstraint("match_id", "team_id", "player_id", "roster_role", name="uq_analytics_lineups_business"),
        CheckConstraint("roster_role IN ('STARTER', 'SUBSTITUTE')", name="ck_analytics_lineups_roster_role"),
        Index("ix_analytics_lineups_match", "match_id"),
        Index("ix_analytics_lineups_team", "team_id"),
        Index("ix_analytics_lineups_player", "player_id"),
    )
    lineup_fact_id = mapped_column(BigInteger, Identity(), primary_key=True)
    match_id = mapped_column(Integer, ForeignKey("matches.fixture_id"), nullable=False)
    team_id = mapped_column(Integer, ForeignKey("teams.team_id"), nullable=False)
    player_id = mapped_column(Integer, ForeignKey("players.player_id"), nullable=False)
    provider_team_id = mapped_column(Text, nullable=False)
    provider_player_id = mapped_column(Text, nullable=False)
    roster_role = mapped_column(Text, nullable=False)
    shirt_number = mapped_column(Integer)
    position = mapped_column(Text)
    grid = mapped_column(Text)
    formation = mapped_column(Text)
    source_provider = mapped_column(Text, server_default="api-football", nullable=False)
    observed_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)