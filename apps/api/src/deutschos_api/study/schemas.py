from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from deutschos_api.educational_library.schemas import CanonicalOutlineNodeRead
from deutschos_api.models import StudyMissionType, StudyPracticalStatus, StudySessionStatus
from deutschos_api.schemas.base import APIModel


class StudyStartOrigin(StrEnum):
    FIRST_SECTION = "first_section"
    SECTION_PICKER = "section_picker"
    CONCEPT_SEARCH = "concept_search"
    MANUAL_PAGE = "manual_page"
    ALREADY_STUDYING = "already_studying"
    RECOMMENDATION = "recommendation"


class StudyMissionRead(APIModel):
    type: StudyMissionType
    label: str
    concept: str
    brief: str
    example_de: str | None
    example_es: str | None
    objective: str
    verifiable_task: str
    original_content: bool


class WorkbookLinkRead(APIModel):
    id: str
    theory_source_id: str
    theory_section_stable_key: str
    workbook_source_id: str
    workbook_source_version: int
    workbook_pdf_page: int
    printed_page_label: str | None
    exercise_start: str | None
    exercise_end: str | None
    region: Literal["full", "left", "right", "both", "unknown"]
    comment: str | None
    status: Literal["candidate", "user_confirmed", "rejected", "stale"]
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StudySectionRead(APIModel):
    id: int
    stable_key: str
    order: int
    title: str
    topic: str | None
    source_id: str
    source_version: int
    source_name: str
    pdf_page_start: int | None
    pdf_page_end: int | None
    printed_page_label: str | None
    editorial_status: str
    practical_status: StudyPracticalStatus
    current_pdf_page: int | None
    selection_origin: StudyStartOrigin | None
    last_activity_at: datetime | None
    last_session_id: str | None
    open_questions: int = 0
    workbook_link: WorkbookLinkRead | None = None
    theme_number: int | None = None
    title_es: str | None = None
    title_de: str | None = None
    printed_page_start: int | None = None
    printed_page_end: int | None = None
    printed_range_status: str = "unknown"
    reference_pdf_page: int | None = None
    manual_scan_layout: str | None = None
    manual_region: str | None = None
    outline: list[CanonicalOutlineNodeRead] = Field(default_factory=list)


class StudyPathRead(APIModel):
    source_id: str
    source_version: int
    source_name: str
    workbook_source_id: str | None
    workbook_source_name: str | None
    sections: list[StudySectionRead]


class StudySessionCreate(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    kind: Literal["guided", "free"] = "guided"
    section_id: int | None = Field(default=None, ge=1)
    concept_name: str | None = Field(default=None, min_length=1, max_length=300)
    source_id: str | None = Field(default=None, max_length=100)
    source_name: str | None = Field(default=None, max_length=500)
    section_title: str | None = Field(default=None, min_length=1, max_length=500)
    pdf_page_start: int | None = Field(default=None, ge=1)
    pdf_page_end: int | None = Field(default=None, ge=1)
    current_pdf_page: int | None = Field(default=None, ge=1)
    printed_page_label: str | None = Field(default=None, max_length=100)
    objective: str | None = Field(default=None, min_length=2, max_length=1_000)
    planned_minutes: Literal[20, 30, 45, 60] | None = None
    mission_type: StudyMissionType = StudyMissionType.AUTOMATIC
    start_origin: StudyStartOrigin = StudyStartOrigin.SECTION_PICKER
    activate: bool = True

    @model_validator(mode="after")
    def validate_kind(self):
        if self.kind == "guided" and self.section_id is None:
            raise ValueError("guided sessions require section_id")
        if self.kind == "free" and not self.section_title:
            raise ValueError("free sessions require section_title")
        if self.pdf_page_start and self.pdf_page_end and self.pdf_page_end < self.pdf_page_start:
            raise ValueError("pdf page range is invalid")
        return self


class StudySessionRead(APIModel):
    id: str
    kind: Literal["guided", "free"]
    status: StudySessionStatus
    source_id: str | None
    source_version: int | None
    source_name: str | None
    section_id: int | None
    section_stable_key: str | None
    section_title: str
    concept_name: str | None
    pdf_page_start: int | None
    pdf_page_end: int | None
    current_pdf_page: int | None
    printed_page_label: str | None
    objective: str
    mission: StudyMissionRead
    plan: dict[str, object]
    checklist: list[object]
    planned_minutes: int | None
    active_seconds: int
    started_at: datetime
    paused_at: datetime | None
    resumed_at: datetime | None
    closed_at: datetime | None
    subjective_result: str | None
    final_pdf_page: int | None
    final_workbook_exercise: str | None
    next_action: str | None
    created_at: datetime
    updated_at: datetime


class StudyTransitionRequest(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    action: Literal["activate", "pause", "resume", "complete", "abandon"]
    subjective_result: (
        Literal[
            "understood",
            "needs_review",
            "unfinished",
            "difficult",
            "continue_next_time",
            "change_topic",
        ]
        | None
    ) = None
    final_pdf_page: int | None = Field(default=None, ge=1)
    final_workbook_exercise: str | None = Field(default=None, max_length=100)
    next_action: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def close_has_result(self):
        if self.action == "complete" and self.subjective_result is None:
            raise ValueError("completed sessions require a subjective result")
        return self


class StudyPositionUpdate(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    current_pdf_page: int | None = Field(default=None, ge=1)
    printed_page_label: str | None = Field(default=None, max_length=100)
    checklist: list[object] | None = None


class StudySectionStateUpdate(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    practical_status: StudyPracticalStatus
    current_pdf_page: int | None = Field(default=None, ge=1)
    selection_origin: StudyStartOrigin = StudyStartOrigin.SECTION_PICKER


class StudyNoteWrite(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    session_id: str | None = Field(default=None, max_length=36)
    source_id: str | None = Field(default=None, max_length=100)
    section_stable_key: str | None = Field(default=None, max_length=100)
    concept_name: str | None = Field(default=None, max_length=300)
    pdf_page: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1, max_length=10_000)


class StudyNoteRead(APIModel):
    id: str
    session_id: str | None
    source_id: str | None
    section_stable_key: str | None
    concept_name: str | None
    pdf_page: int | None
    text: str
    created_at: datetime
    updated_at: datetime


class StudyQuestionWrite(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    session_id: str | None = Field(default=None, max_length=36)
    source_id: str | None = Field(default=None, max_length=100)
    section_stable_key: str | None = Field(default=None, max_length=100)
    concept_name: str | None = Field(default=None, max_length=300)
    pdf_page: int | None = Field(default=None, ge=1)
    question: str = Field(min_length=2, max_length=5_000)
    status: Literal["open", "clarified", "revisit", "archived"] = "open"


class StudyQuestionStatusUpdate(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    status: Literal["open", "clarified", "revisit", "archived"]
    answer_query_id: str | None = Field(default=None, max_length=36)


class StudyQuestionRead(APIModel):
    id: str
    session_id: str | None
    source_id: str | None
    section_stable_key: str | None
    concept_name: str | None
    pdf_page: int | None
    question: str
    answer_query_id: str | None
    status: Literal["open", "clarified", "revisit", "archived"]
    created_at: datetime
    updated_at: datetime


class WorkbookLinkCreate(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    theory_source_id: str = Field(min_length=1, max_length=100)
    theory_section_stable_key: str = Field(min_length=1, max_length=100)
    workbook_pdf_page: int = Field(ge=1)
    printed_page_label: str | None = Field(default=None, max_length=100)
    exercise_start: str | None = Field(default=None, max_length=50)
    exercise_end: str | None = Field(default=None, max_length=50)
    region: Literal["full", "left", "right", "both", "unknown"] = "unknown"
    comment: str | None = Field(default=None, max_length=1_000)


class WorkbookLinkReview(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    action: Literal["confirm", "reject", "unknown", "revert"]


class WorkbookLinkUpdate(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    workbook_pdf_page: int = Field(ge=1)
    printed_page_label: str | None = Field(default=None, max_length=100)
    exercise_start: str | None = Field(default=None, max_length=50)
    exercise_end: str | None = Field(default=None, max_length=50)
    region: Literal["full", "left", "right", "both", "unknown"] = "unknown"
    comment: str | None = Field(default=None, max_length=1_000)


class StudyPreferenceUpdate(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    mission_preference: StudyMissionType


class StudyPreferenceRead(APIModel):
    mission_preference: StudyMissionType
    active_source_id: str | None
    active_section_stable_key: str | None


class StudyRecommendationRead(APIModel):
    kind: Literal["active_session", "paused_session", "section", "manual"]
    reason: str
    session_id: str | None = None
    section_stable_key: str | None = None


class StudyDashboardRead(APIModel):
    path_ready: bool
    total_sections: int
    active_session: StudySessionRead | None
    recommendation: StudyRecommendationRead
    open_questions: int
    recent_sessions: list[StudySessionRead]
    preferences: StudyPreferenceRead


class StudyDeleteRequest(APIModel):
    operation_id: str = Field(min_length=8, max_length=100)
    confirmation: Literal["BORRAR"]


class StudyDeleteRead(APIModel):
    deleted_sessions: int
    deleted_notes: int
    deleted_questions: int
    deleted_workbook_links: int
    deleted_section_states: int


class StudyQuickActionRequest(APIModel):
    action: Literal[
        "explain",
        "summarize",
        "another_example",
        "compare_spanish",
        "why_form",
        "laboratory_example",
        "thematic_example",
        "locate_rule",
        "not_understood",
    ]
    question: str | None = Field(default=None, min_length=2, max_length=1_000)
    conversation_id: str | None = Field(default=None, max_length=100)
    include_note_ids: list[str] = Field(default_factory=list, max_length=5)
