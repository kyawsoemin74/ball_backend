from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class VenueBase(BaseModel):
    """Base Venue schema with required fields."""
    provider: str = Field(default="api-football", description="Provider identifier")
    provider_id: str = Field(..., min_length=1, description="External provider venue ID")
    name: str = Field(..., min_length=1, max_length=255, description="Venue name")
    city: Optional[str] = Field(None, max_length=255, description="City where venue is located")
    country: Optional[str] = Field(None, max_length=100, description="Country")
    country_code: Optional[str] = Field(None, max_length=2, description="Country code (ISO-3166-1)")
    capacity: Optional[int] = Field(None, description="Stadium capacity")
    surface: Optional[str] = Field(None, max_length=50, description="Playing surface type")
    image: Optional[str] = Field(None, description="Venue image URL")


class VenueCreate(VenueBase):
    """Schema for creating a new Venue."""
    pass


class VenueUpdate(BaseModel):
    """Schema for updating a Venue."""
    name: Optional[str] = Field(None, max_length=255, description="Venue name")
    city: Optional[str] = Field(None, max_length=255, description="City")
    country: Optional[str] = Field(None, max_length=100, description="Country")
    country_code: Optional[str] = Field(None, max_length=2, description="Country code")
    capacity: Optional[int] = Field(None, description="Stadium capacity")
    surface: Optional[str] = Field(None, max_length=50, description="Playing surface type")
    image: Optional[str] = Field(None, description="Venue image URL")


class Venue(VenueBase):
    """Complete Venue schema with all fields including ID and timestamps."""
    venue_id: int = Field(..., description="Local Fover venue ID")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    model_config = ConfigDict(from_attributes=True)


class VenueResponse(Venue):
    """Alias for Venue response schema."""
    pass
