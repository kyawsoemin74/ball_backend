from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


# Schema for creating a new match
class MatchCreate(BaseModel):
    # Primary Key
    match_id: int = Field(..., description="Unique match ID (Primary Key)")
    
    # League Info
    league_id: int = Field(..., description="League ID")
    season: Optional[int] = Field(None, description="Match season year")
    league_name: Optional[str] = Field(None, max_length=255, description="League name")
    league_logo: Optional[str] = Field(None, max_length=500, description="League logo URL")
    
    # Country Info
    country_name: Optional[str] = Field(None, max_length=255, description="Country name")
    country_logo: Optional[str] = Field(None, max_length=500, description="Country logo URL")
    
    # Match Time
    match_time: datetime = Field(..., description="Match scheduled time (UTC or Asia/Yangon)")
    
    # Match Status
    status: str = Field(default="NS", max_length=10, description="Match status (NS, 1H, 2H, HT, FT, etc.)")
    
    # Elapsed Time
    elapsed: Optional[int] = Field(default=0, ge=0, description="Elapsed time in minutes")
    
    # Home Team
    home_team: str = Field(..., max_length=255, description="Home team name")
    home_team_id: Optional[int] = Field(None, description="Home team ID")
    home_team_logo: Optional[str] = Field(None, max_length=500, description="Home team logo URL")
    
    # Away Team
    away_team: str = Field(..., max_length=255, description="Away team name")
    away_team_id: Optional[int] = Field(None, description="Away team ID")
    away_team_logo: Optional[str] = Field(None, max_length=500, description="Away team logo URL")
    
    # Scores
    home_score: int = Field(default=0, ge=0, description="Home team score")
    away_score: int = Field(default=0, ge=0, description="Away team score")
    
    # Venue
    venue_name: Optional[str] = Field(None, max_length=255, description="Venue name")
    venue_city: Optional[str] = Field(None, max_length=255, description="Venue city")


# Schema for updating an existing match
class MatchUpdate(BaseModel):
    # League Info
    league_id: Optional[int] = Field(None, description="League ID")
    season: Optional[int] = Field(None, description="Match season year")
    league_name: Optional[str] = Field(None, max_length=255, description="League name")
    league_logo: Optional[str] = Field(None, max_length=500, description="League logo URL")
    
    # Country Info
    country_name: Optional[str] = Field(None, max_length=255, description="Country name")
    country_logo: Optional[str] = Field(None, max_length=500, description="Country logo URL")
    
    # Match Time
    match_time: Optional[datetime] = Field(None, description="Match scheduled time")
    
    # Match Status
    status: Optional[str] = Field(None, max_length=10, description="Match status")
    
    # Elapsed Time
    elapsed: Optional[int] = Field(None, ge=0, description="Elapsed time in minutes")
    
    # Home Team
    home_team: Optional[str] = Field(None, max_length=255, description="Home team name")
    home_team_id: Optional[int] = Field(None, description="Home team ID")
    home_team_logo: Optional[str] = Field(None, max_length=500, description="Home team logo URL")
    
    # Away Team
    away_team: Optional[str] = Field(None, max_length=255, description="Away team name")
    away_team_id: Optional[int] = Field(None, description="Away team ID")
    away_team_logo: Optional[str] = Field(None, max_length=500, description="Away team logo URL")
    
    # Scores
    home_score: Optional[int] = Field(None, ge=0, description="Home team score")
    away_score: Optional[int] = Field(None, ge=0, description="Away team score")
    
    # Venue
    venue_name: Optional[str] = Field(None, max_length=255, description="Venue name")
    venue_city: Optional[str] = Field(None, max_length=255, description="Venue city")


class MatchStatisticItem(BaseModel):
    data_name: str = Field(..., description="Internal normalized stat key")
    label: str = Field(..., description="Human-readable stat label")
    home_value: Any = Field(..., description="Home team stat value")
    away_value: Any = Field(..., description="Away team stat value")


class MatchStatisticsResponse(BaseModel):
    match_id: int = Field(..., description="Unique match ID")
    statistics: list[MatchStatisticItem] = Field(default_factory=list, description="Normalized statistics data")

    class Config:
        from_attributes = True


class MatchDateResponse(BaseModel):
    # Primary Key
    match_id: int = Field(..., description="Unique match ID (Primary Key)")

    # League Info
    league_id: int = Field(..., description="League ID")
    season: Optional[int] = Field(None, description="Match season year")
    league_name: Optional[str] = Field(None, description="League name")
    league_logo: Optional[str] = Field(None, description="League logo URL")

    # Country Info
    country_name: Optional[str] = Field(None, description="Country name")
    country_logo: Optional[str] = Field(None, description="Country logo URL")

    # Match Time
    match_time: datetime = Field(..., description="Match scheduled time")

    # Match Status
    status: str = Field(..., description="Match status")

    # Elapsed Time
    elapsed: int = Field(default=0, description="Elapsed time in minutes")

    # Home Team
    home_team: str = Field(..., description="Home team name")
    home_team_id: Optional[int] = Field(None, description="Home team ID")
    home_team_logo: Optional[str] = Field(None, description="Home team logo URL")

    # Away Team
    away_team: str = Field(..., description="Away team name")
    away_team_id: Optional[int] = Field(None, description="Away team ID")
    away_team_logo: Optional[str] = Field(None, description="Away team logo URL")

    # Scores
    home_score: int = Field(..., description="Home team score")
    away_score: int = Field(..., description="Away team score")

    # Venue
    venue_name: Optional[str] = Field(None, description="Venue name")
    venue_city: Optional[str] = Field(None, description="Venue city")

    # Timestamps
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")

    class Config:
        from_attributes = True


# Schema for match response
class MatchResponse(BaseModel):
    # Primary Key
    match_id: int = Field(..., description="Unique match ID (Primary Key)")

    # Availability / tab support flags
    has_events: bool = Field(default=False, description="Whether match events are available for this fixture")
    has_stats: bool = Field(default=False, description="Whether statistics data is available for this fixture")
    has_lineups: bool = Field(default=False, description="Whether lineup data is available for this fixture")
    has_odds: bool = Field(default=False, description="Whether odds data is available for this fixture")
    has_h2h: bool = Field(default=False, description="Whether head-to-head data is available for this fixture")
    has_standings: bool = Field(default=False, description="Whether standings data is available for this fixture")
    has_predictions: bool = Field(default=False, description="Whether predictions data is available for this fixture")
    has_rankings: bool = Field(default=False, description="Whether rankings data is available for this fixture")
    has_news: bool = Field(default=False, description="Whether related news is available for this fixture")
    has_highlights: bool = Field(default=False, description="Whether highlights are available for this fixture")
    has_comments: bool = Field(default=False, description="Whether comments are available for this fixture")
    is_knockout: bool = Field(default=False, description="Whether the competition format is knockout/cup based")
    has_bracket: bool = Field(default=False, description="Whether bracket data can be displayed for this fixture")
    
    # League Info
    league_id: int = Field(..., description="League ID")
    season: Optional[int] = Field(None, description="Match season year")
    league_name: Optional[str] = Field(None, description="League name")
    league_logo: Optional[str] = Field(None, description="League logo URL")
    
    # Country Info
    country_name: Optional[str] = Field(None, description="Country name")
    country_logo: Optional[str] = Field(None, description="Country logo URL")
    
    # Match Time
    match_time: datetime = Field(..., description="Match scheduled time")
    
    # Match Status
    status: str = Field(..., description="Match status")
    
    # Elapsed Time
    elapsed: int = Field(default=0, description="Elapsed time in minutes")
    
    # Home Team
    home_team: str = Field(..., description="Home team name")
    home_team_id: Optional[int] = Field(None, description="Home team ID")
    home_team_logo: Optional[str] = Field(None, description="Home team logo URL")
    
    # Away Team
    away_team: str = Field(..., description="Away team name")
    away_team_id: Optional[int] = Field(None, description="Away team ID")
    away_team_logo: Optional[str] = Field(None, description="Away team logo URL")
    
    # Scores
    home_score: int = Field(..., description="Home team score")
    away_score: int = Field(..., description="Away team score")
    
    # Venue
    venue_name: Optional[str] = Field(None, description="Venue name")
    venue_city: Optional[str] = Field(None, description="Venue city")
    
    # Timestamps
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")

    class Config:
        from_attributes = True