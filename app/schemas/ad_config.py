from datetime import datetime
from typing import Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class AdConfigBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    is_enabled: bool = False
    banner_android: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("banner_android", "android_banner_id"),
        serialization_alias="banner_android",
    )
    banner_ios: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("banner_ios", "ios_banner_id"),
        serialization_alias="banner_ios",
    )
    interstitial_android: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("interstitial_android", "android_interstitial_id"),
        serialization_alias="interstitial_android",
    )
    interstitial_ios: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("interstitial_ios", "ios_interstitial_id"),
        serialization_alias="interstitial_ios",
    )
    rewarded_android: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("rewarded_android", "android_rewarded_id"),
        serialization_alias="rewarded_android",
    )
    rewarded_ios: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("rewarded_ios", "ios_rewarded_id"),
        serialization_alias="rewarded_ios",
    )
    app_open_android: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("app_open_android", "android_app_id"),
        serialization_alias="app_open_android",
    )
    app_open_ios: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("app_open_ios", "ios_app_id"),
        serialization_alias="app_open_ios",
    )


class AdConfigUpdateRequest(AdConfigBase):
    pass


class AdConfigResponse(AdConfigBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
