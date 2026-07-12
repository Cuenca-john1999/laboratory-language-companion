from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
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
