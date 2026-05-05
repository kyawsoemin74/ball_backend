from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# Schema for creating a new match
class MatchCreate(BaseModel):
    # Primary Key
    fixture_id: int = Field(..., description="Unique fixture ID (Primary Key)")
    
    # League Info
    league_id: int = Field(..., description="League ID")
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
    home_team_logo: Optional[str] = Field(None, max_length=500, description="Home team logo URL")
    
    # Away Team
    away_team: str = Field(..., max_length=255, description="Away team name")
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
    home_team_logo: Optional[str] = Field(None, max_length=500, description="Home team logo URL")
    
    # Away Team
    away_team: Optional[str] = Field(None, max_length=255, description="Away team name")
    away_team_logo: Optional[str] = Field(None, max_length=500, description="Away team logo URL")
    
    # Scores
    home_score: Optional[int] = Field(None, ge=0, description="Home team score")
    away_score: Optional[int] = Field(None, ge=0, description="Away team score")
    
    # Venue
    venue_name: Optional[str] = Field(None, max_length=255, description="Venue name")
    venue_city: Optional[str] = Field(None, max_length=255, description="Venue city")


# Schema for match response
class MatchResponse(BaseModel):
    # Primary Key
    fixture_id: int = Field(..., description="Unique fixture ID (Primary Key)")
    
    # League Info
    league_id: int = Field(..., description="League ID")
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
    home_team_logo: Optional[str] = Field(None, description="Home team logo URL")
    
    # Away Team
    away_team: str = Field(..., description="Away team name")
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