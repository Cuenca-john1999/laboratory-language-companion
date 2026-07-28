from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

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
    request_id: UUID = Field(default_factory=uuid4)
    logical_generation_id: UUID = Field(default_factory=uuid4)
    message: str = Field(min_length=1, max_length=10000)
    role: Literal["teacher", "deep_teacher"] = "teacher"
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)
    session_id: int | None = Field(default=None, gt=0)
    continuation_from: str | None = Field(default=None, max_length=100_000)
    manual_continuation: bool = False
    prior_segment_count: int = Field(default=0, ge=0, le=100)
    automatic_continuation_count: int = Field(default=0, ge=0, le=1)
    manual_continuation_count: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def limit_ephemeral_context(self) -> "ChatRequest":
        if sum(len(turn.content) for turn in self.history) > 30_000:
            raise ValueError("history exceeds the 30000 character limit")
        if self.manual_continuation != bool(
            self.continuation_from and self.continuation_from.strip()
        ):
            raise ValueError(
                "manual_continuation requires non-empty continuation_from and vice versa"
            )
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
