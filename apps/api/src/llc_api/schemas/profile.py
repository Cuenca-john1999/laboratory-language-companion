from datetime import datetime

from pydantic import Field, JsonValue

from llc_api.schemas.base import APIModel


class ProfileUpdate(APIModel):
    preferred_name: str = Field(min_length=1, max_length=100)
    native_language: str = Field(min_length=1, max_length=100)
    additional_languages: list[str] = Field(default_factory=list, max_length=20)
    current_location: str = Field(default="", max_length=200)
    professional_background: str = Field(default="", max_length=5000)
    learning_goals: list[str] = Field(default_factory=list, max_length=30)
    interests: list[str] = Field(default_factory=list, max_length=100)
    learning_preferences: dict[str, JsonValue] = Field(default_factory=dict)


class ProfileRead(ProfileUpdate):
    id: int
    created_at: datetime
    updated_at: datetime
