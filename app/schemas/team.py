from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel


class TeamBase(BaseModel):
    team_id: int
    name: str
    country: Optional[str] = None
    logo: Optional[str] = None
    stadium: Optional[str] = None
    founded: Optional[int] = None


class TeamCreate(TeamBase):
    pass


class Team(TeamBase):
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TeamFixtureItem(BaseModel):
    match_id: Optional[int] = None
    date: Optional[str] = None
    league_id: Optional[int] = None
    league_name: Optional[str] = None
    home_team_id: Optional[int] = None
    home_team: Optional[str] = None
    away_team_id: Optional[int] = None
    away_team: Optional[str] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    status: Optional[str] = None
    result: Optional[str] = None


class TeamFixturesResponse(BaseModel):
    team_id: int
    recent: List[TeamFixtureItem] = []
    upcoming: List[TeamFixtureItem] = []


class TeamSquadPlayer(BaseModel):
    player_id: Optional[int] = None
    player_name: Optional[str] = None
    age: Optional[int] = None
    nationality: Optional[str] = None
    position: Optional[str] = None
    photo: Optional[str] = None


class TeamSquadResponse(BaseModel):
    team_id: int
    team_name: Optional[str] = None
    players: List[TeamSquadPlayer] = []


class TeamStatisticsResponse(BaseModel):
    team_id: int
    league_id: int
    season: int
    played: Optional[int] = None
    wins: Optional[int] = None
    draws: Optional[int] = None
    losses: Optional[int] = None
    goals_for: Optional[int] = None
    goals_against: Optional[int] = None
    clean_sheets: Optional[int] = None
    failed_to_score: Optional[int] = None
    average_goals_scored: Optional[float] = None
    average_goals_conceded: Optional[float] = None