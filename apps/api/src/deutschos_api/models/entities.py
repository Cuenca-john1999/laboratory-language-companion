from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deutschos_api.core.time import utc_now
from deutschos_api.db.base import Base, TimestampMixin
from deutschos_api.db.types import UTCDateTime


class SkillCategory(StrEnum):
    GRAMMAR = "grammar"
    VOCABULARY = "vocabulary"
    READING = "reading"
    LISTENING = "listening"
    WRITING = "writing"
    SPEAKING = "speaking"
    PRONUNCIATION = "pronunciation"
    PROFESSIONAL_LANGUAGE = "professional_language"


class MistakeStatus(StrEnum):
    NEW = "new"
    LEARNING = "learning"
    IMPROVING = "improving"
    MASTERED = "mastered"
    IGNORED = "ignored"


class StudyPracticalStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    VIEWED = "viewed"
    NEEDS_REVIEW = "needs_review"
    COMPLETED_BY_USER = "completed_by_user"
    PAUSED = "paused"


class StudySessionStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class StudyMissionType(StrEnum):
    AUTOMATIC = "automatic"
    STANDARD = "standard"
    LABORATORY = "laboratory"
    FROZEN_CITY = "frozen_city"
    UNDERWATER_EXPLORATION = "underwater_exploration"
    SPACE_MISSION = "space_mission"
    MIXED = "mixed"


class DiagnosticSessionStatus(StrEnum):
    NOT_STARTED = "not_started"
    ONBOARDING = "onboarding"
    CALIBRATING = "calibrating"
    ASSESSING = "assessing"
    REVIEWING = "reviewing"
    PAUSED = "paused"
    TIME_LIMITED = "time_limited"
    COMPLETING = "completing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"
    ERROR = "error"


class DiagnosticTaskStatus(StrEnum):
    SELECTED = "selected"
    PRESENTED = "presented"
    ANSWERED = "answered"
    SKIPPED = "skipped"
    NOT_UNDERSTOOD = "not_understood"
    ABANDONED = "abandoned"
    EVALUATED = "evaluated"
    INVALIDATED = "invalidated"


class DiagnosticAxis(StrEnum):
    READING_COMPREHENSION = "reading_comprehension"
    WRITTEN_PRODUCTION = "written_production"
    ACTIVE_GRAMMAR = "active_grammar"
    RECEPTIVE_VOCABULARY = "receptive_vocabulary"
    PRODUCTIVE_VOCABULARY = "productive_vocabulary"
    LISTENING_COMPREHENSION = "listening_comprehension"
    ORAL_PRODUCTION = "oral_production"
    PRONUNCIATION = "pronunciation"
    WRITTEN_FLUENCY = "written_fluency"
    ORAL_FLUENCY = "oral_fluency"
    TYPED_COMMUNICATION_REPAIR = "communication_repair.typed"
    EVERYDAY_FAMILIARITY = "everyday_familiarity"
    PROFESSIONAL_LABORATORY_FAMILIARITY = "professional_laboratory_familiarity"


class DiagnosticTaskType(StrEnum):
    BINARY_CHOICE = "binary_choice"
    WORD_ORDER = "word_order"
    GAP_FILL = "gap_fill"
    SENTENCE_CORRECTION = "sentence_correction"
    PERSONAL_SHORT_ANSWER = "personal_short_answer"
    SHORT_MESSAGE = "short_message"
    SHORT_TEXT_COMPREHENSION = "short_text_comprehension"
    PARAPHRASE = "paraphrase"
    SITUATION_DESCRIPTION = "situation_description"
    TYPED_COMMUNICATION_REPAIR = "communication_repair.typed"


class DiagnosticOutcome(StrEnum):
    FAILURE = "failure"
    PARTIAL = "partial"
    CORRECT_WITH_HELP = "correct_with_help"
    CORRECT_WITHOUT_HELP = "correct_without_help"
    NOT_EVALUABLE = "not_evaluable"


class DiagnosticPolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    INSUFFICIENT = "insufficient"


class DiagnosticBand(StrEnum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INITIAL_BASIS = "initial_basis"
    DEVELOPING = "developing"
    FUNCTIONAL_GUIDED = "functional_guided"
    CONSISTENT_SAMPLE = "consistent_sample"


class DiagnosticConfidenceLabel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class StudentProfile(Base, TimestampMixin):
    __tablename__ = "student_profiles"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_user"),
        CheckConstraint(
            "json_valid(additional_languages) AND json_type(additional_languages) = 'array'",
            name="additional_languages_json_array",
        ),
        CheckConstraint(
            "json_valid(learning_goals) AND json_type(learning_goals) = 'array'",
            name="learning_goals_json_array",
        ),
        CheckConstraint(
            "json_valid(interests) AND json_type(interests) = 'array'",
            name="interests_json_array",
        ),
        CheckConstraint(
            "json_valid(learning_preferences) AND json_type(learning_preferences) = 'object'",
            name="learning_preferences_json_object",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    preferred_name: Mapped[str] = mapped_column(String(100))
    native_language: Mapped[str] = mapped_column(String(100))
    additional_languages: Mapped[str] = mapped_column(Text, default="[]")
    current_location: Mapped[str] = mapped_column(String(200), default="")
    professional_background: Mapped[str] = mapped_column(Text, default="")
    learning_goals: Mapped[str] = mapped_column(Text, default="[]")
    interests: Mapped[str] = mapped_column(Text, default="[]")
    learning_preferences: Mapped[str] = mapped_column(Text, default="{}")
    daily_plans: Mapped[list["DailyPlan"]] = relationship(back_populates="profile")
    diagnostic_sessions: Mapped[list["DiagnosticSession"]] = relationship(
        back_populates="profile", passive_deletes=True
    )


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint(
            "category IN ('grammar', 'vocabulary', 'reading', 'listening', 'writing', "
            "'speaking', 'pronunciation', 'professional_language')",
            name="valid_category",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))
    cefr_level: Mapped[str | None] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(Text, default="")
    student_skill: Mapped["StudentSkill | None"] = relationship(back_populates="skill")
    evidence: Mapped[list["SkillEvidence"]] = relationship(back_populates="skill")
    curriculum_memberships: Mapped[list["CurriculumSkill"]] = relationship(back_populates="skill")
    diagnostic_tasks: Mapped[list["DiagnosticTask"]] = relationship(
        back_populates="skill", passive_deletes=True
    )
    diagnostic_results: Mapped[list["DiagnosticResult"]] = relationship(
        back_populates="skill", passive_deletes=True
    )


class Curriculum(Base):
    __tablename__ = "curricula"
    __table_args__ = (
        CheckConstraint("cefr_from = 'A0'", name="cefr_from_a0"),
        CheckConstraint("cefr_to = 'A1'", name="cefr_to_a1"),
        Index(
            "ix_curricula_single_active",
            "is_active",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
    )
    version: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    cefr_from: Mapped[str] = mapped_column(String(2))
    cefr_to: Mapped[str] = mapped_column(String(2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    skills: Mapped[list["CurriculumSkill"]] = relationship(
        back_populates="curriculum", cascade="all, delete-orphan", passive_deletes=True
    )
    daily_plans: Mapped[list["DailyPlan"]] = relationship(back_populates="curriculum")
    diagnostic_sessions: Mapped[list["DiagnosticSession"]] = relationship(
        back_populates="curriculum", passive_deletes=True
    )


class CurriculumSkill(Base):
    __tablename__ = "curriculum_skills"
    __table_args__ = (
        CheckConstraint("curriculum_order >= 1", name="order_positive"),
        CheckConstraint("cefr_reference IN ('A0', 'A1')", name="cefr_a0_a1"),
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="difficulty_range"),
        CheckConstraint("min_mastery BETWEEN 0 AND 1", name="mastery_range"),
        CheckConstraint("min_confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint("min_evidence >= 1", name="min_evidence_positive"),
        CheckConstraint(
            "unassisted_streak >= 0",
            name="required_unassisted_streak_nonnegative",
        ),
        CheckConstraint(
            "json_valid(exercise_types) AND json_type(exercise_types) = 'array'",
            name="exercise_types_json_array",
        ),
        UniqueConstraint("curriculum_version", "curriculum_order"),
    )
    curriculum_version: Mapped[str] = mapped_column(
        ForeignKey("curricula.version", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), primary_key=True
    )
    curriculum_order: Mapped[int] = mapped_column(Integer)
    cefr_reference: Mapped[str] = mapped_column(String(2))
    difficulty: Mapped[int] = mapped_column(Integer)
    min_mastery: Mapped[float] = mapped_column(Float)
    min_confidence: Mapped[float] = mapped_column(Float)
    min_evidence: Mapped[int] = mapped_column(Integer)
    unassisted_streak: Mapped[int] = mapped_column(Integer)
    exercise_types: Mapped[str] = mapped_column(Text)
    curriculum: Mapped[Curriculum] = relationship(back_populates="skills")
    skill: Mapped[Skill] = relationship(back_populates="curriculum_memberships")


class SkillPrerequisite(Base):
    __tablename__ = "skill_prerequisites"
    __table_args__ = (
        ForeignKeyConstraint(
            ["curriculum_version", "skill_id"],
            ["curriculum_skills.curriculum_version", "curriculum_skills.skill_id"],
            name="fk_skill_prerequisites_skill_membership",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["curriculum_version", "prerequisite_skill_id"],
            ["curriculum_skills.curriculum_version", "curriculum_skills.skill_id"],
            name="fk_skill_prerequisites_prerequisite_membership",
            ondelete="CASCADE",
        ),
        CheckConstraint("skill_id != prerequisite_skill_id", name="not_self_referential"),
        CheckConstraint("prerequisite_order >= 1", name="prerequisite_order_positive"),
        UniqueConstraint("curriculum_version", "skill_id", "prerequisite_order"),
    )
    curriculum_version: Mapped[str] = mapped_column(String(30), primary_key=True)
    skill_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prerequisite_skill_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prerequisite_order: Mapped[int] = mapped_column(Integer)


class StudentSkill(Base):
    __tablename__ = "student_skills"
    __table_args__ = (
        CheckConstraint("estimated_mastery >= 0 AND estimated_mastery <= 1", name="mastery_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("evidence_count >= 0", name="evidence_nonnegative"),
        CheckConstraint(
            "last_outcome IS NULL OR last_outcome IN "
            "('failure', 'partial', 'correct_with_help', 'correct_without_help')",
            name="valid_last_outcome",
        ),
        CheckConstraint("unassisted_streak >= 0", name="unassisted_streak_nonnegative"),
        CheckConstraint("lapse_count >= 0", name="lapse_count_nonnegative"),
        Index("ix_student_skills_next_review", "next_review_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), unique=True)
    estimated_mastery: Mapped[float] = mapped_column(Float, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    last_practised_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_review_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    last_outcome: Mapped[str | None] = mapped_column(String(30))
    unassisted_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lapse_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    mastered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    engine_version: Mapped[str | None] = mapped_column(String(50))
    last_evidence_id: Mapped[int | None] = mapped_column(ForeignKey("skill_evidence.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)
    skill: Mapped[Skill] = relationship(back_populates="student_skill")
    last_evidence: Mapped["SkillEvidence | None"] = relationship(foreign_keys=[last_evidence_id])


class LearningSession(Base):
    __tablename__ = "learning_sessions"
    __table_args__ = (
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name="duration_nonnegative"
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="valid_time_range"
        ),
        Index("ix_learning_sessions_started_at", "started_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    session_type: Mapped[str] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    model_used: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    user_motivation_before: Mapped[int | None] = mapped_column(Integer)
    user_motivation_after: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[list["ExerciseAttempt"]] = relationship(back_populates="learning_session")


class StudySectionState(Base, TimestampMixin):
    __tablename__ = "study_section_states"
    __table_args__ = (
        CheckConstraint("profile_id = 1", name="single_user"),
        CheckConstraint(
            "practical_status IN ('not_started','in_progress','viewed','needs_review',"
            "'completed_by_user','paused')",
            name="valid_practical_status",
        ),
        CheckConstraint(
            "selection_origin IN ('first_section','section_picker','concept_search',"
            "'manual_page','already_studying','recommendation')",
            name="valid_selection_origin",
        ),
        CheckConstraint("current_pdf_page IS NULL OR current_pdf_page > 0", name="page_positive"),
        UniqueConstraint("profile_id", "source_id", "section_stable_key"),
        Index("ix_study_section_states_status_activity", "practical_status", "last_activity_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), default=1
    )
    source_id: Mapped[str] = mapped_column(String(100))
    source_version: Mapped[int] = mapped_column(Integer)
    section_id: Mapped[int] = mapped_column(Integer)
    section_stable_key: Mapped[str] = mapped_column(String(100))
    section_title: Mapped[str] = mapped_column(String(500))
    practical_status: Mapped[str] = mapped_column(
        String(30), default=StudyPracticalStatus.NOT_STARTED
    )
    current_pdf_page: Mapped[int | None] = mapped_column(Integer)
    printed_page_label: Mapped[str | None] = mapped_column(String(100))
    selection_origin: Mapped[str] = mapped_column(String(40))
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class StudySession(Base, TimestampMixin):
    __tablename__ = "study_sessions"
    __table_args__ = (
        CheckConstraint("profile_id = 1", name="single_user"),
        CheckConstraint("kind IN ('guided','free')", name="valid_kind"),
        CheckConstraint(
            "status IN ('planned','active','paused','completed','abandoned')",
            name="valid_status",
        ),
        CheckConstraint(
            "planned_minutes IS NULL OR planned_minutes IN (20,30,45,60)",
            name="valid_planned_minutes",
        ),
        CheckConstraint("active_seconds >= 0", name="active_seconds_nonnegative"),
        CheckConstraint("pdf_page_start IS NULL OR pdf_page_start > 0", name="page_start_positive"),
        CheckConstraint("pdf_page_end IS NULL OR pdf_page_end > 0", name="page_end_positive"),
        CheckConstraint(
            "pdf_page_end IS NULL OR pdf_page_start IS NULL OR pdf_page_end >= pdf_page_start",
            name="valid_page_range",
        ),
        CheckConstraint(
            "current_pdf_page IS NULL OR current_pdf_page > 0", name="current_page_positive"
        ),
        CheckConstraint(
            "subjective_result IS NULL OR subjective_result IN ('understood','needs_review',"
            "'unfinished','difficult','continue_next_time','change_topic')",
            name="valid_subjective_result",
        ),
        CheckConstraint(
            "json_valid(mission) AND json_type(mission) = 'object'", name="mission_json_object"
        ),
        CheckConstraint("json_valid(plan) AND json_type(plan) = 'object'", name="plan_json_object"),
        CheckConstraint(
            "json_valid(checklist) AND json_type(checklist) = 'array'",
            name="checklist_json_array",
        ),
        CheckConstraint("closed_at IS NULL OR closed_at >= started_at", name="valid_closed_time"),
        Index(
            "ix_study_sessions_one_active",
            "profile_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
        Index("ix_study_sessions_updated", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), default=1
    )
    section_state_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_section_states.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=StudySessionStatus.PLANNED)
    source_id: Mapped[str | None] = mapped_column(String(100))
    source_version: Mapped[int | None] = mapped_column(Integer)
    source_name: Mapped[str | None] = mapped_column(String(500))
    section_id: Mapped[int | None] = mapped_column(Integer)
    section_stable_key: Mapped[str | None] = mapped_column(String(100))
    section_title: Mapped[str] = mapped_column(String(500))
    concept_name: Mapped[str | None] = mapped_column(String(300))
    pdf_page_start: Mapped[int | None] = mapped_column(Integer)
    pdf_page_end: Mapped[int | None] = mapped_column(Integer)
    current_pdf_page: Mapped[int | None] = mapped_column(Integer)
    printed_page_label: Mapped[str | None] = mapped_column(String(100))
    objective: Mapped[str] = mapped_column(Text)
    mission_type: Mapped[str] = mapped_column(String(40))
    mission: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    plan: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    checklist: Mapped[list[object]] = mapped_column(JSON, default=list)
    planned_minutes: Mapped[int | None] = mapped_column(Integer)
    active_seconds: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    subjective_result: Mapped[str | None] = mapped_column(String(40))
    final_pdf_page: Mapped[int | None] = mapped_column(Integer)
    final_workbook_exercise: Mapped[str | None] = mapped_column(String(100))
    next_action: Mapped[str | None] = mapped_column(String(100))


class StudyNote(Base, TimestampMixin):
    __tablename__ = "study_notes"
    __table_args__ = (
        CheckConstraint("profile_id = 1", name="single_user"),
        CheckConstraint("length(text) > 0", name="text_nonempty"),
        CheckConstraint("pdf_page IS NULL OR pdf_page > 0", name="page_positive"),
        Index("ix_study_notes_section", "source_id", "section_stable_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), default=1
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("study_sessions.id", ondelete="SET NULL")
    )
    source_id: Mapped[str | None] = mapped_column(String(100))
    section_stable_key: Mapped[str | None] = mapped_column(String(100))
    concept_name: Mapped[str | None] = mapped_column(String(300))
    pdf_page: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class StudyQuestion(Base, TimestampMixin):
    __tablename__ = "study_questions"
    __table_args__ = (
        CheckConstraint("profile_id = 1", name="single_user"),
        CheckConstraint("length(question) > 0", name="question_nonempty"),
        CheckConstraint("status IN ('open','clarified','revisit','archived')", name="valid_status"),
        CheckConstraint("pdf_page IS NULL OR pdf_page > 0", name="page_positive"),
        Index("ix_study_questions_status_section", "status", "section_stable_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), default=1
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("study_sessions.id", ondelete="SET NULL")
    )
    source_id: Mapped[str | None] = mapped_column(String(100))
    section_stable_key: Mapped[str | None] = mapped_column(String(100))
    concept_name: Mapped[str | None] = mapped_column(String(300))
    pdf_page: Mapped[int | None] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    answer_query_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="open")


class StudyWorkbookLink(Base, TimestampMixin):
    __tablename__ = "study_workbook_links"
    __table_args__ = (
        CheckConstraint("profile_id = 1", name="single_user"),
        CheckConstraint("workbook_pdf_page > 0", name="page_positive"),
        CheckConstraint("region IN ('full','left','right','both','unknown')", name="valid_region"),
        CheckConstraint(
            "status IN ('candidate','user_confirmed','rejected','stale')", name="valid_status"
        ),
        UniqueConstraint(
            "profile_id",
            "theory_source_id",
            "section_stable_key",
            "workbook_source_id",
            "workbook_pdf_page",
            "exercise_start",
            "exercise_end",
        ),
        Index("ix_study_workbook_links_section", "theory_source_id", "section_stable_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), default=1
    )
    theory_source_id: Mapped[str] = mapped_column(String(100))
    theory_source_version: Mapped[int] = mapped_column(Integer)
    section_stable_key: Mapped[str] = mapped_column(String(100))
    workbook_source_id: Mapped[str] = mapped_column(String(100))
    workbook_source_version: Mapped[int] = mapped_column(Integer)
    workbook_pdf_page: Mapped[int] = mapped_column(Integer)
    workbook_printed_page: Mapped[str | None] = mapped_column(String(100))
    exercise_start: Mapped[str | None] = mapped_column(String(100))
    exercise_end: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str] = mapped_column(String(20), default="unknown")
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class StudyPreference(Base):
    __tablename__ = "study_preferences"
    __table_args__ = (
        CheckConstraint("profile_id = 1", name="single_user"),
        CheckConstraint(
            "mission_preference IN ('automatic','standard','laboratory','frozen_city',"
            "'underwater_exploration','space_mission','mixed')",
            name="valid_mission_preference",
        ),
    )
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    mission_preference: Mapped[str] = mapped_column(String(40), default="automatic")
    active_source_id: Mapped[str | None] = mapped_column(String(100))
    active_section_stable_key: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)


class StudyEvent(Base):
    __tablename__ = "study_events"
    __table_args__ = (
        CheckConstraint("profile_id = 1", name="single_user"),
        CheckConstraint(
            "json_valid(before_state) AND json_type(before_state) = 'object'",
            name="before_json_object",
        ),
        CheckConstraint(
            "json_valid(after_state) AND json_type(after_state) = 'object'",
            name="after_json_object",
        ),
        UniqueConstraint("operation_id"),
        Index("ix_study_events_target_created", "target_type", "target_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"), default=1
    )
    operation_id: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    before_state: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    after_state: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    reverts_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_events.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class DailyPlan(Base):
    __tablename__ = "daily_plans"
    __table_args__ = (
        CheckConstraint("profile_id = 1", name="single_user"),
        CheckConstraint("requested_minutes BETWEEN 10 AND 120", name="requested_minutes_range"),
        CheckConstraint(
            "duration_minutes BETWEEN 10 AND requested_minutes",
            name="duration_within_request",
        ),
        CheckConstraint("motivation BETWEEN 1 AND 5", name="motivation_range"),
        CheckConstraint("intensity IN ('low', 'normal', 'high')", name="valid_intensity"),
        CheckConstraint(
            "json_valid(reason_codes) AND json_type(reason_codes) = 'array'",
            name="reason_codes_json_array",
        ),
        Index("ix_daily_plans_date_generated", "plan_date", "generated_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("student_profiles.id", ondelete="RESTRICT"))
    plan_date: Mapped[date] = mapped_column(Date)
    requested_minutes: Mapped[int] = mapped_column(Integer)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    motivation: Mapped[int] = mapped_column(Integer)
    intensity: Mapped[str] = mapped_column(String(10))
    objective: Mapped[str] = mapped_column(Text)
    primary_skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"))
    new_skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"))
    internal_reason: Mapped[str] = mapped_column(Text)
    reason_codes: Mapped[str] = mapped_column(Text)
    engine_version: Mapped[str] = mapped_column(String(50))
    curriculum_version: Mapped[str] = mapped_column(
        ForeignKey("curricula.version", ondelete="RESTRICT")
    )
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    profile: Mapped[StudentProfile] = relationship(back_populates="daily_plans")
    curriculum: Mapped[Curriculum] = relationship(back_populates="daily_plans")
    primary_skill: Mapped[Skill] = relationship(foreign_keys=[primary_skill_id])
    new_skill: Mapped[Skill | None] = relationship(foreign_keys=[new_skill_id])
    blocks: Mapped[list["DailyPlanBlock"]] = relationship(
        back_populates="daily_plan",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DailyPlanBlock.position",
    )


class DailyPlanBlock(Base):
    __tablename__ = "daily_plan_blocks"
    __table_args__ = (
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint("kind IN ('review', 'new_skill', 'practice')", name="valid_kind"),
        CheckConstraint("length(exercise_type) > 0", name="exercise_type_nonempty"),
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
        UniqueConstraint("daily_plan_id", "position"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    daily_plan_id: Mapped[int] = mapped_column(ForeignKey("daily_plans.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"))
    exercise_type: Mapped[str] = mapped_column(String(50))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    objective: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    daily_plan: Mapped[DailyPlan] = relationship(back_populates="blocks")
    skill: Mapped[Skill] = relationship()


class ExerciseAttempt(Base):
    __tablename__ = "exercise_attempts"
    __table_args__ = (
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="score_range"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    learning_session_id: Mapped[int] = mapped_column(ForeignKey("learning_sessions.id"))
    exercise_type: Mapped[str] = mapped_column(String(50), default="chat")
    prompt: Mapped[str] = mapped_column(Text)
    student_answer: Mapped[str] = mapped_column(Text)
    expected_answer: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    feedback: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    learning_session: Mapped[LearningSession] = relationship(back_populates="attempts")
    skill_evidence: Mapped[list["SkillEvidence"]] = relationship(back_populates="exercise_attempt")


class Mistake(Base):
    __tablename__ = "mistakes"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("occurrence_count >= 1", name="occurrence_positive"),
        CheckConstraint(
            "status IN ('new', 'learning', 'improving', 'mastered', 'ignored')",
            name="valid_status",
        ),
        Index("ix_mistakes_review", "status", "next_review_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    original_text: Mapped[str] = mapped_column(Text)
    corrected_text: Mapped[str] = mapped_column(Text)
    explanation_es: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(50))
    subcategory: Mapped[str | None] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float, default=0)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    next_review_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(20), default=MistakeStatus.NEW)


class VocabularyItem(Base):
    __tablename__ = "vocabulary_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    lemma: Mapped[str] = mapped_column(String(200))
    article: Mapped[str | None] = mapped_column(String(20))
    plural: Mapped[str | None] = mapped_column(String(200))
    part_of_speech: Mapped[str] = mapped_column(String(50))
    translation_es: Mapped[str] = mapped_column(String(300))
    example_de: Mapped[str] = mapped_column(Text)
    example_es: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(100), default="general")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    student_vocabulary: Mapped["StudentVocabulary | None"] = relationship(
        back_populates="vocabulary_item"
    )


class StudentVocabulary(Base):
    __tablename__ = "student_vocabulary"
    __table_args__ = (
        CheckConstraint("recognition_mastery BETWEEN 0 AND 1", name="recognition_range"),
        CheckConstraint("written_production_mastery BETWEEN 0 AND 1", name="written_range"),
        CheckConstraint("spoken_production_mastery BETWEEN 0 AND 1", name="spoken_range"),
        Index("ix_student_vocabulary_next_review", "next_review_at"),
    )
    vocabulary_item_id: Mapped[int] = mapped_column(
        ForeignKey("vocabulary_items.id"), primary_key=True
    )
    recognition_mastery: Mapped[float] = mapped_column(Float, default=0)
    written_production_mastery: Mapped[float] = mapped_column(Float, default=0)
    spoken_production_mastery: Mapped[float] = mapped_column(Float, default=0)
    next_review_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    vocabulary_item: Mapped[VocabularyItem] = relationship(back_populates="student_vocabulary")


class SkillEvidence(Base):
    __tablename__ = "skill_evidence"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        CheckConstraint("source IN ('manual_assessment', 'diagnostic')", name="valid_source"),
        UniqueConstraint("submission_id"),
        UniqueConstraint("exercise_attempt_id", "skill_id"),
        Index("ix_skill_evidence_skill_created", "skill_id", "created_at"),
        Index("ix_skill_evidence_supersedes_unique", "supersedes_evidence_id", unique=True),
        CheckConstraint(
            "outcome IS NULL OR outcome IN "
            "('failure', 'partial', 'correct_with_help', 'correct_without_help')",
            name="valid_outcome",
        ),
        CheckConstraint(
            "mastery_before IS NULL OR mastery_before BETWEEN 0 AND 1",
            name="mastery_before_range",
        ),
        CheckConstraint(
            "mastery_after IS NULL OR mastery_after BETWEEN 0 AND 1",
            name="mastery_after_range",
        ),
        CheckConstraint(
            "confidence_before IS NULL OR confidence_before BETWEEN 0 AND 1",
            name="confidence_before_range",
        ),
        CheckConstraint(
            "confidence_after IS NULL OR confidence_after BETWEEN 0 AND 1",
            name="confidence_after_range",
        ),
        CheckConstraint(
            "evidence_count_before IS NULL OR evidence_count_before >= 0",
            name="evidence_count_before_nonnegative",
        ),
        CheckConstraint(
            "evidence_count_after IS NULL OR evidence_count_after >= 0",
            name="evidence_count_after_nonnegative",
        ),
        CheckConstraint(
            "unassisted_streak_before IS NULL OR unassisted_streak_before >= 0",
            name="unassisted_streak_before_nonnegative",
        ),
        CheckConstraint(
            "unassisted_streak_after IS NULL OR unassisted_streak_after >= 0",
            name="unassisted_streak_after_nonnegative",
        ),
        CheckConstraint(
            "lapse_count_before IS NULL OR lapse_count_before >= 0",
            name="lapse_count_before_nonnegative",
        ),
        CheckConstraint(
            "lapse_count_after IS NULL OR lapse_count_after >= 0",
            name="lapse_count_after_nonnegative",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[str] = mapped_column(String(36))
    exercise_attempt_id: Mapped[int] = mapped_column(ForeignKey("exercise_attempts.id"))
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"))
    score: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(30))
    engine_version: Mapped[str] = mapped_column(String(50))
    outcome: Mapped[str | None] = mapped_column(String(30))
    supersedes_evidence_id: Mapped[int | None] = mapped_column(ForeignKey("skill_evidence.id"))
    mastery_before: Mapped[float | None] = mapped_column(Float)
    mastery_after: Mapped[float | None] = mapped_column(Float)
    confidence_before: Mapped[float | None] = mapped_column(Float)
    confidence_after: Mapped[float | None] = mapped_column(Float)
    evidence_count_before: Mapped[int | None] = mapped_column(Integer)
    evidence_count_after: Mapped[int | None] = mapped_column(Integer)
    unassisted_streak_before: Mapped[int | None] = mapped_column(Integer)
    unassisted_streak_after: Mapped[int | None] = mapped_column(Integer)
    lapse_count_before: Mapped[int | None] = mapped_column(Integer)
    lapse_count_after: Mapped[int | None] = mapped_column(Integer)
    next_review_at_before: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_review_at_after: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    exercise_attempt: Mapped[ExerciseAttempt] = relationship(back_populates="skill_evidence")
    skill: Mapped[Skill] = relationship(back_populates="evidence")
    supersedes: Mapped["SkillEvidence | None"] = relationship(
        remote_side=[id], foreign_keys=[supersedes_evidence_id], back_populates="correction"
    )
    correction: Mapped["SkillEvidence | None"] = relationship(
        foreign_keys=[supersedes_evidence_id], back_populates="supersedes", uselist=False
    )

    @property
    def is_correction(self) -> bool:
        return self.supersedes_evidence_id is not None


class DiagnosticSession(Base, TimestampMixin):
    __tablename__ = "diagnostic_sessions"
    __table_args__ = (
        CheckConstraint("profile_id = 1", name="single_user"),
        CheckConstraint(
            "status IN ('not_started', 'onboarding', 'calibrating', 'assessing', "
            "'reviewing', 'paused', 'time_limited', 'completing', 'completed', "
            "'cancelled', 'abandoned', 'error')",
            name="valid_status",
        ),
        CheckConstraint(
            "paused_from_status IS NULL OR paused_from_status IN "
            "('onboarding', 'calibrating', 'assessing', 'reviewing', 'error')",
            name="valid_paused_from_status",
        ),
        CheckConstraint("target_task_count BETWEEN 12 AND 16", name="target_task_range"),
        CheckConstraint("max_task_count = 20", name="max_task_count_v1"),
        CheckConstraint(
            "target_duration_seconds BETWEEN 900 AND 1500", name="target_duration_range"
        ),
        CheckConstraint("max_duration_seconds = 1500", name="max_duration_v1"),
        CheckConstraint("active_seconds >= 0", name="active_seconds_nonnegative"),
        CheckConstraint(
            "tasks_presented BETWEEN 0 AND max_task_count", name="tasks_presented_range"
        ),
        CheckConstraint(
            "tasks_evaluable BETWEEN 0 AND tasks_presented", name="tasks_evaluable_range"
        ),
        CheckConstraint(
            "json_valid(selection_state) AND json_type(selection_state) = 'object'",
            name="selection_state_json_object",
        ),
        CheckConstraint("status != 'paused' OR paused_at IS NOT NULL", name="paused_has_time"),
        CheckConstraint(
            "status != 'completed' OR completed_at IS NOT NULL", name="completed_has_time"
        ),
        CheckConstraint(
            "status != 'abandoned' OR abandoned_at IS NOT NULL", name="abandoned_has_time"
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="valid_completed_time"
        ),
        CheckConstraint(
            "abandoned_at IS NULL OR abandoned_at >= started_at", name="valid_abandoned_time"
        ),
        CheckConstraint(
            "resumed_at IS NULL OR paused_at IS NULL OR resumed_at >= paused_at",
            name="valid_resume_time",
        ),
        CheckConstraint(
            "repeats_session_id IS NULL OR repeats_session_id != id", name="not_self_repeating"
        ),
        Index("ix_diagnostic_sessions_status_updated", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="RESTRICT"), default=1
    )
    diagnostic_version: Mapped[str] = mapped_column(String(50))
    persistence_version: Mapped[str] = mapped_column(String(50))
    curriculum_version: Mapped[str] = mapped_column(
        ForeignKey("curricula.version", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(30), default=DiagnosticSessionStatus.NOT_STARTED)
    paused_from_status: Mapped[str | None] = mapped_column(String(30))
    active_section: Mapped[str | None] = mapped_column(String(100))
    selection_state: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    random_seed: Mapped[str] = mapped_column(String(100))
    instruction_language: Mapped[str] = mapped_column(String(20), default="es")
    target_task_count: Mapped[int] = mapped_column(Integer, default=14)
    max_task_count: Mapped[int] = mapped_column(Integer, default=20)
    target_duration_seconds: Mapped[int] = mapped_column(Integer, default=1200)
    max_duration_seconds: Mapped[int] = mapped_column(Integer, default=1500)
    active_seconds: Mapped[int] = mapped_column(Integer, default=0)
    tasks_presented: Mapped[int] = mapped_column(Integer, default=0)
    tasks_evaluable: Mapped[int] = mapped_column(Integer, default=0)
    termination_reason: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    abandoned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    repeats_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("diagnostic_sessions.id", ondelete="RESTRICT")
    )

    profile: Mapped[StudentProfile] = relationship(back_populates="diagnostic_sessions")
    curriculum: Mapped[Curriculum] = relationship(back_populates="diagnostic_sessions")
    repeats_session: Mapped["DiagnosticSession | None"] = relationship(
        remote_side=[id], foreign_keys=[repeats_session_id]
    )
    tasks: Mapped[list["DiagnosticTask"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DiagnosticTask.sequence",
    )
    results: Mapped[list["DiagnosticResult"]] = relationship(
        back_populates="session", passive_deletes=True
    )


class DiagnosticTask(Base):
    __tablename__ = "diagnostic_tasks"
    __table_args__ = (
        CheckConstraint("sequence BETWEEN 1 AND 20", name="sequence_range"),
        CheckConstraint(
            "status IN ('selected', 'presented', 'answered', 'skipped', "
            "'not_understood', 'abandoned', 'evaluated', 'invalidated')",
            name="valid_status",
        ),
        CheckConstraint(
            "task_type IN ('binary_choice', 'word_order', 'gap_fill', "
            "'sentence_correction', 'personal_short_answer', 'short_message', "
            "'short_text_comprehension', 'paraphrase', 'situation_description', "
            "'communication_repair.typed')",
            name="valid_task_type",
        ),
        CheckConstraint(
            "primary_axis IN ('reading_comprehension', 'written_production', "
            "'active_grammar', 'receptive_vocabulary', 'productive_vocabulary', "
            "'written_fluency', 'communication_repair.typed', "
            "'everyday_familiarity', 'professional_laboratory_familiarity')",
            name="valid_primary_axis",
        ),
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="difficulty_range"),
        CheckConstraint("modality = 'text'", name="text_only_v1"),
        CheckConstraint("origin IN ('bank', 'manual', 'structured_llm')", name="valid_origin"),
        CheckConstraint(
            "json_valid(secondary_axes) AND json_type(secondary_axes) = 'array'",
            name="secondary_axes_json_array",
        ),
        CheckConstraint(
            "json_valid(content) AND json_type(content) = 'object'",
            name="content_json_object",
        ),
        CheckConstraint(
            "json_valid(options) AND json_type(options) = 'array'",
            name="options_json_array",
        ),
        CheckConstraint(
            "expected_answer IS NULL OR "
            "(json_valid(expected_answer) AND json_type(expected_answer) = 'object')",
            name="expected_answer_json_object",
        ),
        CheckConstraint(
            "json_valid(rubric) AND json_type(rubric) = 'object'",
            name="rubric_json_object",
        ),
        CheckConstraint(
            "presented_at IS NULL OR presented_at >= selected_at", name="valid_presented_time"
        ),
        CheckConstraint(
            "answered_at IS NULL OR (presented_at IS NOT NULL AND answered_at >= presented_at)",
            name="valid_answered_time",
        ),
        UniqueConstraint("session_id", "sequence"),
        Index("ix_diagnostic_tasks_session_status", "session_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostic_sessions.id", ondelete="CASCADE")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default=DiagnosticTaskStatus.SELECTED)
    template_id: Mapped[str] = mapped_column(String(100))
    template_version: Mapped[str] = mapped_column(String(50))
    task_type: Mapped[str] = mapped_column(String(50))
    primary_axis: Mapped[str] = mapped_column(String(60))
    secondary_axes: Mapped[list[str]] = mapped_column(JSON, default=list)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"))
    difficulty: Mapped[int] = mapped_column(Integer)
    modality: Mapped[str] = mapped_column(String(20), default="text")
    selection_reason: Mapped[str] = mapped_column(String(100))
    content: Mapped[dict[str, object]] = mapped_column(JSON)
    options: Mapped[list[object]] = mapped_column(JSON, default=list)
    expected_answer: Mapped[dict[str, object] | None] = mapped_column(JSON)
    rubric: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    origin: Mapped[str] = mapped_column(String(30), default="bank")
    generator_version: Mapped[str | None] = mapped_column(String(50))
    selected_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    presented_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    session: Mapped[DiagnosticSession] = relationship(back_populates="tasks")
    skill: Mapped[Skill | None] = relationship(back_populates="diagnostic_tasks")
    responses: Mapped[list["DiagnosticResponse"]] = relationship(
        back_populates="task", passive_deletes=True, order_by="DiagnosticResponse.id"
    )


class DiagnosticResponse(Base):
    __tablename__ = "diagnostic_responses"
    __table_args__ = (
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint("evaluation_revision >= 1", name="evaluation_revision_positive"),
        CheckConstraint("length(evaluation_id) = 36", name="evaluation_id_uuid_length"),
        CheckConstraint("length(submission_id) = 36", name="submission_id_uuid_length"),
        CheckConstraint(
            "instruction_state IN ('understood', 'not_understood', 'unknown')",
            name="valid_instruction_state",
        ),
        CheckConstraint(
            "json_valid(assistance) AND json_type(assistance) = 'array'",
            name="assistance_json_array",
        ),
        CheckConstraint(
            "active_seconds IS NULL OR active_seconds >= 0", name="active_seconds_nonnegative"
        ),
        CheckConstraint(
            "outcome IN ('failure', 'partial', 'correct_with_help', "
            "'correct_without_help', 'not_evaluable')",
            name="valid_outcome",
        ),
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 1", name="score_range"),
        CheckConstraint(
            "(outcome = 'not_evaluable' AND score IS NULL) OR "
            "(outcome != 'not_evaluable' AND score IS NOT NULL)",
            name="outcome_score_consistent",
        ),
        CheckConstraint(
            "outcome = 'not_evaluable' OR length(trim(response_text)) > 0",
            name="evaluable_response_nonempty",
        ),
        CheckConstraint(
            "json_valid(rubric) AND json_type(rubric) = 'object'",
            name="rubric_json_object",
        ),
        CheckConstraint(
            "polarity IN ('positive', 'negative', 'insufficient')", name="valid_polarity"
        ),
        CheckConstraint(
            "outcome != 'not_evaluable' OR polarity = 'insufficient'",
            name="not_evaluable_is_insufficient",
        ),
        CheckConstraint("evaluator_confidence BETWEEN 0 AND 1", name="evaluator_confidence_range"),
        CheckConstraint(
            "evaluator_type IN ('deterministic', 'manual', 'structured_llm')",
            name="valid_evaluator_type",
        ),
        CheckConstraint(
            "json_valid(reason_codes) AND json_type(reason_codes) = 'array'",
            name="reason_codes_json_array",
        ),
        CheckConstraint(
            "(supersedes_response_id IS NULL AND evaluation_revision = 1) OR "
            "(supersedes_response_id IS NOT NULL AND evaluation_revision > 1)",
            name="correction_revision_shape",
        ),
        UniqueConstraint("evaluation_id"),
        UniqueConstraint("submission_id", "evaluation_revision"),
        UniqueConstraint("task_id", "attempt_number", "evaluation_revision"),
        Index(
            "ix_diagnostic_responses_supersedes_unique",
            "supersedes_response_id",
            unique=True,
        ),
        Index("ix_diagnostic_responses_task_created", "task_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(String(36))
    submission_id: Mapped[str] = mapped_column(String(36))
    task_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_tasks.id", ondelete="RESTRICT"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    evaluation_revision: Mapped[int] = mapped_column(Integer, default=1)
    response_text: Mapped[str | None] = mapped_column(Text)
    response_language: Mapped[str | None] = mapped_column(String(20))
    instruction_state: Mapped[str] = mapped_column(String(20), default="unknown")
    assistance: Mapped[list[str]] = mapped_column(JSON, default=list)
    active_seconds: Mapped[int | None] = mapped_column(Integer)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    outcome: Mapped[str] = mapped_column(String(30))
    score: Mapped[float | None] = mapped_column(Float)
    rubric: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    polarity: Mapped[str] = mapped_column(String(20))
    evaluator_confidence: Mapped[float] = mapped_column(Float)
    evaluator_type: Mapped[str] = mapped_column(String(30))
    evaluator_version: Mapped[str] = mapped_column(String(50))
    diagnostic_version: Mapped[str] = mapped_column(String(50))
    task_version: Mapped[str] = mapped_column(String(50))
    justification: Mapped[str] = mapped_column(Text, default="")
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    supersedes_response_id: Mapped[int | None] = mapped_column(
        ForeignKey("diagnostic_responses.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    task: Mapped[DiagnosticTask] = relationship(back_populates="responses")
    supersedes: Mapped["DiagnosticResponse | None"] = relationship(
        remote_side=[id], foreign_keys=[supersedes_response_id], back_populates="correction"
    )
    correction: Mapped["DiagnosticResponse | None"] = relationship(
        foreign_keys=[supersedes_response_id], back_populates="supersedes", uselist=False
    )


class DiagnosticResult(Base):
    __tablename__ = "diagnostic_results"
    __table_args__ = (
        CheckConstraint("result_revision >= 1", name="result_revision_positive"),
        CheckConstraint("length(result_id) = 36", name="result_id_uuid_length"),
        CheckConstraint("length(result_group_id) = 36", name="result_group_id_uuid_length"),
        CheckConstraint(
            "axis IN ('reading_comprehension', 'written_production', 'active_grammar', "
            "'receptive_vocabulary', 'productive_vocabulary', 'listening_comprehension', "
            "'oral_production', 'pronunciation', 'written_fluency', 'oral_fluency', "
            "'communication_repair.typed', 'everyday_familiarity', "
            "'professional_laboratory_familiarity')",
            name="valid_axis",
        ),
        CheckConstraint("modality = 'text'", name="text_only_v1"),
        CheckConstraint(
            "band IN ('insufficient_evidence', 'initial_basis', 'developing', "
            "'functional_guided', 'consistent_sample')",
            name="valid_band",
        ),
        CheckConstraint(
            "estimated_score IS NULL OR estimated_score BETWEEN 0 AND 1",
            name="estimated_score_range",
        ),
        CheckConstraint(
            "(band = 'insufficient_evidence' AND estimated_score IS NULL) OR "
            "(band != 'insufficient_evidence' AND estimated_score IS NOT NULL)",
            name="band_score_consistent",
        ),
        CheckConstraint("estimate_confidence BETWEEN 0 AND 1", name="estimate_confidence_range"),
        CheckConstraint(
            "confidence_label IN ('low', 'moderate', 'high')", name="valid_confidence_label"
        ),
        CheckConstraint("positive_evidence_count >= 0", name="positive_count_nonnegative"),
        CheckConstraint("negative_evidence_count >= 0", name="negative_count_nonnegative"),
        CheckConstraint("insufficient_evidence_count >= 0", name="insufficient_count_nonnegative"),
        CheckConstraint(
            "json_valid(task_types) AND json_type(task_types) = 'array'",
            name="task_types_json_array",
        ),
        CheckConstraint(
            "(difficulty_min IS NULL AND difficulty_max IS NULL) OR "
            "(difficulty_min BETWEEN 1 AND 5 AND difficulty_max BETWEEN difficulty_min AND 5)",
            name="valid_difficulty_range",
        ),
        CheckConstraint("cefr_band IS NULL OR cefr_band IN ('pre-A1', 'A1')", name="valid_cefr"),
        CheckConstraint(
            "cefr_band IS NULL OR (skill_id IS NOT NULL AND positive_evidence_count >= 2 "
            "AND estimate_confidence >= 0.6)",
            name="cefr_requires_skill_evidence",
        ),
        CheckConstraint(
            "axis NOT IN ('listening_comprehension', 'oral_production', 'pronunciation', "
            "'oral_fluency') OR (band = 'insufficient_evidence' AND cefr_band IS NULL "
            "AND positive_evidence_count = 0 AND negative_evidence_count = 0 "
            "AND difficulty_min IS NULL AND difficulty_max IS NULL)",
            name="future_modalities_unassessed",
        ),
        CheckConstraint("projection_status = 'not_projected'", name="not_projected_v1"),
        CheckConstraint(
            "(supersedes_result_id IS NULL AND result_revision = 1) OR "
            "(supersedes_result_id IS NOT NULL AND result_revision > 1)",
            name="correction_revision_shape",
        ),
        UniqueConstraint("result_id"),
        UniqueConstraint("result_group_id", "result_revision"),
        Index("ix_diagnostic_results_supersedes_unique", "supersedes_result_id", unique=True),
        Index("ix_diagnostic_results_session_axis", "session_id", "axis"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    result_id: Mapped[str] = mapped_column(String(36))
    result_group_id: Mapped[str] = mapped_column(String(36))
    result_revision: Mapped[int] = mapped_column(Integer, default=1)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostic_sessions.id", ondelete="RESTRICT")
    )
    axis: Mapped[str] = mapped_column(String(60))
    modality: Mapped[str] = mapped_column(String(20), default="text")
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"))
    band: Mapped[str] = mapped_column(String(40))
    cefr_band: Mapped[str | None] = mapped_column(String(10))
    estimated_score: Mapped[float | None] = mapped_column(Float)
    estimate_confidence: Mapped[float] = mapped_column(Float)
    confidence_label: Mapped[str] = mapped_column(String(20))
    positive_evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    negative_evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    insufficient_evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    task_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    difficulty_min: Mapped[int | None] = mapped_column(Integer)
    difficulty_max: Mapped[int | None] = mapped_column(Integer)
    strengths: Mapped[str] = mapped_column(Text, default="")
    limitations: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    projection_status: Mapped[str] = mapped_column(String(30), default="not_projected")
    result_version: Mapped[str] = mapped_column(String(50))
    supersedes_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("diagnostic_results.id", ondelete="RESTRICT")
    )
    correction_reason: Mapped[str | None] = mapped_column(String(200))
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    session: Mapped[DiagnosticSession] = relationship(back_populates="results")
    skill: Mapped[Skill | None] = relationship(back_populates="diagnostic_results")
    supersedes: Mapped["DiagnosticResult | None"] = relationship(
        remote_side=[id], foreign_keys=[supersedes_result_id], back_populates="correction"
    )
    correction: Mapped["DiagnosticResult | None"] = relationship(
        foreign_keys=[supersedes_result_id], back_populates="supersedes", uselist=False
    )
