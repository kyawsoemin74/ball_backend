from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


H2HAvailability = Literal["AVAILABLE", "PARTIAL", "EMPTY", "UNMAPPABLE"]
H2HIdentityStatus = Literal["MAPPED", "UNMAPPABLE", "AMBIGUOUS"]
H2HIdentityIssue = Literal[
    "PROVIDER_ID_MISSING",
    "INVALID_PROVIDER_ID",
    "TEAM_NOT_FOUND",
    "TEAM_MAPPING_AMBIGUOUS",
]
H2HSummaryStatus = Literal[
    "INCLUDED",
    "INCOMPLETE_SCORE",
    "NOT_FINISHED",
    "UNMAPPABLE_TEAM",
    "UNRELATED_PAIR",
    "DUPLICATE_FIXTURE",
]


class H2HTeamV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: int | None
    name: str | None


class H2HScoreV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home: int | None
    away: int | None


class H2HMeetingV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_at: datetime | None
    status: str | None
    league_name: str | None
    venue_name: str | None
    home_team: H2HTeamV2
    away_team: H2HTeamV2
    score: H2HScoreV2
    identity_status: H2HIdentityStatus
    identity_issue: H2HIdentityIssue | None
    summary_status: H2HSummaryStatus


class H2HTeamSummaryV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: int
    team_name: str | None
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int


class H2HSummaryV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home: H2HTeamSummaryV2
    away: H2HTeamSummaryV2
    included_meeting_count: int
    total_matches: int
    home_win: int
    draw: int
    away_win: int
    home_goals: int
    away_goals: int


class H2HResponseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[2]
    match_id: int
    home_team_id: int
    away_team_id: int
    availability: H2HAvailability
    available: bool
    summary: H2HSummaryV2
    meetings: list[H2HMeetingV2]
    excluded_meeting_count: int
