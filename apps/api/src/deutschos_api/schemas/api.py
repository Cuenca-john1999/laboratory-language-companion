from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from deutschos_api.schemas.base import APIModel, ORMModel


class HealthResponse(APIModel):
    status: Literal["ok"]
    service: str
    version: str
    schema_revision: str


class ModelInfo(APIModel):
    name: str
    size: int | None = None
    modified_at: datetime | None = None


class ModelsResponse(APIModel):
    provider: str
    available: bool
    models: list[ModelInfo]
    error: str | None = None


class TeacherRoleStatus(APIModel):
    role: Literal["teacher", "deep_teacher"]
    available: bool


class TeacherRolesResponse(APIModel):
    provider: Literal["lm_studio"]
    available: bool
    roles: list[TeacherRoleStatus]
    error: str | None = None


class ChatTurn(APIModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10000)


class ChatRequest(APIModel):
    message: str = Field(min_length=1, max_length=10000)
    role: Literal["teacher", "deep_teacher"] = "teacher"
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)
    session_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def limit_ephemeral_context(self) -> "ChatRequest":
        if sum(len(turn.content) for turn in self.history) > 30_000:
            raise ValueError("history exceeds the 30000 character limit")
        return self


class ChatResponse(APIModel):
    response: str
    model: str
    session_id: int


class SessionRead(ORMModel):
    id: int
    session_type: str
    started_at: datetime
    completed_at: datetime | None
    duration_seconds: int | None
    model_used: str | None
    summary: str


class MistakeRead(ORMModel):
    id: int
    original_text: str
    corrected_text: str
    explanation_es: str
    category: str
    severity: str
    status: str
    next_review_at: datetime | None


class DashboardResponse(APIModel):
    preferred_name: str
    immediate_goal: str
    current_model: str | None
    lm_studio_available: bool
    pending_reviews: int
    recent_sessions: list[SessionRead]
