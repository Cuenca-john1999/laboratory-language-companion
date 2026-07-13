"""Add isolated, append-only persistence for the adaptive diagnostic.

Revision ID: 0005
Revises: 0004
"""

import os

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("student_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("diagnostic_version", sa.String(50), nullable=False),
        sa.Column("persistence_version", sa.String(50), nullable=False),
        sa.Column(
            "curriculum_version",
            sa.String(30),
            sa.ForeignKey("curricula.version", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("paused_from_status", sa.String(30), nullable=True),
        sa.Column("active_section", sa.String(100), nullable=True),
        sa.Column("selection_state", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("random_seed", sa.String(100), nullable=False),
        sa.Column("instruction_language", sa.String(20), server_default="es", nullable=False),
        sa.Column("target_task_count", sa.Integer(), server_default="14", nullable=False),
        sa.Column("max_task_count", sa.Integer(), server_default="20", nullable=False),
        sa.Column("target_duration_seconds", sa.Integer(), server_default="1200", nullable=False),
        sa.Column("max_duration_seconds", sa.Integer(), server_default="1500", nullable=False),
        sa.Column("active_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tasks_presented", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tasks_evaluable", sa.Integer(), server_default="0", nullable=False),
        sa.Column("termination_reason", sa.String(100), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column("paused_at", sa.DateTime(), nullable=True),
        sa.Column("resumed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(), nullable=True),
        sa.Column(
            "repeats_session_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_sessions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("profile_id = 1", name="single_user"),
        sa.CheckConstraint(
            "status IN ('not_started', 'onboarding', 'calibrating', 'assessing', "
            "'reviewing', 'paused', 'time_limited', 'completing', 'completed', "
            "'cancelled', 'abandoned', 'error')",
            name="valid_status",
        ),
        sa.CheckConstraint(
            "paused_from_status IS NULL OR paused_from_status IN "
            "('onboarding', 'calibrating', 'assessing', 'reviewing', 'error')",
            name="valid_paused_from_status",
        ),
        sa.CheckConstraint("target_task_count BETWEEN 12 AND 16", name="target_task_range"),
        sa.CheckConstraint("max_task_count = 20", name="max_task_count_v1"),
        sa.CheckConstraint(
            "target_duration_seconds BETWEEN 900 AND 1500", name="target_duration_range"
        ),
        sa.CheckConstraint("max_duration_seconds = 1500", name="max_duration_v1"),
        sa.CheckConstraint("active_seconds >= 0", name="active_seconds_nonnegative"),
        sa.CheckConstraint(
            "tasks_presented BETWEEN 0 AND max_task_count", name="tasks_presented_range"
        ),
        sa.CheckConstraint(
            "tasks_evaluable BETWEEN 0 AND tasks_presented", name="tasks_evaluable_range"
        ),
        sa.CheckConstraint(
            "json_valid(selection_state) AND json_type(selection_state) = 'object'",
            name="selection_state_json_object",
        ),
        sa.CheckConstraint("status != 'paused' OR paused_at IS NOT NULL", name="paused_has_time"),
        sa.CheckConstraint(
            "status != 'completed' OR completed_at IS NOT NULL", name="completed_has_time"
        ),
        sa.CheckConstraint(
            "status != 'abandoned' OR abandoned_at IS NOT NULL", name="abandoned_has_time"
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="valid_completed_time"
        ),
        sa.CheckConstraint(
            "abandoned_at IS NULL OR abandoned_at >= started_at", name="valid_abandoned_time"
        ),
        sa.CheckConstraint(
            "resumed_at IS NULL OR paused_at IS NULL OR resumed_at >= paused_at",
            name="valid_resume_time",
        ),
        sa.CheckConstraint(
            "repeats_session_id IS NULL OR repeats_session_id != id", name="not_self_repeating"
        ),
    )
    op.create_index(
        "ix_diagnostic_sessions_status_updated", "diagnostic_sessions", ["status", "updated_at"]
    )

    op.create_table(
        "diagnostic_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("template_id", sa.String(100), nullable=False),
        sa.Column("template_version", sa.String(50), nullable=False),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("primary_axis", sa.String(60), nullable=False),
        sa.Column("secondary_axes", sa.JSON(), server_default="[]", nullable=False),
        sa.Column(
            "skill_id",
            sa.Integer(),
            sa.ForeignKey("skills.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("modality", sa.String(20), server_default="text", nullable=False),
        sa.Column("selection_reason", sa.String(100), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("options", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("expected_answer", sa.JSON(), nullable=True),
        sa.Column("rubric", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("origin", sa.String(30), server_default="bank", nullable=False),
        sa.Column("generator_version", sa.String(50), nullable=True),
        sa.Column(
            "selected_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("presented_at", sa.DateTime(), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("sequence BETWEEN 1 AND 20", name="sequence_range"),
        sa.CheckConstraint(
            "status IN ('selected', 'presented', 'answered', 'skipped', "
            "'not_understood', 'abandoned', 'evaluated', 'invalidated')",
            name="valid_status",
        ),
        sa.CheckConstraint(
            "task_type IN ('binary_choice', 'word_order', 'gap_fill', "
            "'sentence_correction', 'personal_short_answer', 'short_message', "
            "'short_text_comprehension', 'paraphrase', 'situation_description', "
            "'communication_repair.typed')",
            name="valid_task_type",
        ),
        sa.CheckConstraint(
            "primary_axis IN ('reading_comprehension', 'written_production', "
            "'active_grammar', 'receptive_vocabulary', 'productive_vocabulary', "
            "'written_fluency', 'communication_repair.typed', "
            "'everyday_familiarity', 'professional_laboratory_familiarity')",
            name="valid_primary_axis",
        ),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name="difficulty_range"),
        sa.CheckConstraint("modality = 'text'", name="text_only_v1"),
        sa.CheckConstraint("origin IN ('bank', 'manual', 'structured_llm')", name="valid_origin"),
        sa.CheckConstraint(
            "json_valid(secondary_axes) AND json_type(secondary_axes) = 'array'",
            name="secondary_axes_json_array",
        ),
        sa.CheckConstraint(
            "json_valid(content) AND json_type(content) = 'object'",
            name="content_json_object",
        ),
        sa.CheckConstraint(
            "json_valid(options) AND json_type(options) = 'array'",
            name="options_json_array",
        ),
        sa.CheckConstraint(
            "expected_answer IS NULL OR "
            "(json_valid(expected_answer) AND json_type(expected_answer) = 'object')",
            name="expected_answer_json_object",
        ),
        sa.CheckConstraint(
            "json_valid(rubric) AND json_type(rubric) = 'object'",
            name="rubric_json_object",
        ),
        sa.CheckConstraint(
            "presented_at IS NULL OR presented_at >= selected_at", name="valid_presented_time"
        ),
        sa.CheckConstraint(
            "answered_at IS NULL OR (presented_at IS NOT NULL AND answered_at >= presented_at)",
            name="valid_answered_time",
        ),
        sa.UniqueConstraint("session_id", "sequence"),
    )
    op.create_index(
        "ix_diagnostic_tasks_session_status", "diagnostic_tasks", ["session_id", "status"]
    )

    op.create_table(
        "diagnostic_responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evaluation_id", sa.String(36), nullable=False),
        sa.Column("submission_id", sa.String(36), nullable=False),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_tasks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("evaluation_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("response_language", sa.String(20), nullable=True),
        sa.Column("instruction_state", sa.String(20), server_default="unknown", nullable=False),
        sa.Column("assistance", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("active_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("rubric", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("polarity", sa.String(20), nullable=False),
        sa.Column("evaluator_confidence", sa.Float(), nullable=False),
        sa.Column("evaluator_type", sa.String(30), nullable=False),
        sa.Column("evaluator_version", sa.String(50), nullable=False),
        sa.Column("diagnostic_version", sa.String(50), nullable=False),
        sa.Column("task_version", sa.String(50), nullable=False),
        sa.Column("justification", sa.Text(), server_default="", nullable=False),
        sa.Column("reason_codes", sa.JSON(), server_default="[]", nullable=False),
        sa.Column(
            "supersedes_response_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_responses.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        sa.CheckConstraint("evaluation_revision >= 1", name="evaluation_revision_positive"),
        sa.CheckConstraint("length(evaluation_id) = 36", name="evaluation_id_uuid_length"),
        sa.CheckConstraint("length(submission_id) = 36", name="submission_id_uuid_length"),
        sa.CheckConstraint(
            "instruction_state IN ('understood', 'not_understood', 'unknown')",
            name="valid_instruction_state",
        ),
        sa.CheckConstraint(
            "json_valid(assistance) AND json_type(assistance) = 'array'",
            name="assistance_json_array",
        ),
        sa.CheckConstraint(
            "active_seconds IS NULL OR active_seconds >= 0", name="active_seconds_nonnegative"
        ),
        sa.CheckConstraint(
            "outcome IN ('failure', 'partial', 'correct_with_help', "
            "'correct_without_help', 'not_evaluable')",
            name="valid_outcome",
        ),
        sa.CheckConstraint("score IS NULL OR score BETWEEN 0 AND 1", name="score_range"),
        sa.CheckConstraint(
            "(outcome = 'not_evaluable' AND score IS NULL) OR "
            "(outcome != 'not_evaluable' AND score IS NOT NULL)",
            name="outcome_score_consistent",
        ),
        sa.CheckConstraint(
            "outcome = 'not_evaluable' OR length(trim(response_text)) > 0",
            name="evaluable_response_nonempty",
        ),
        sa.CheckConstraint(
            "json_valid(rubric) AND json_type(rubric) = 'object'",
            name="rubric_json_object",
        ),
        sa.CheckConstraint(
            "polarity IN ('positive', 'negative', 'insufficient')", name="valid_polarity"
        ),
        sa.CheckConstraint(
            "outcome != 'not_evaluable' OR polarity = 'insufficient'",
            name="not_evaluable_is_insufficient",
        ),
        sa.CheckConstraint(
            "evaluator_confidence BETWEEN 0 AND 1", name="evaluator_confidence_range"
        ),
        sa.CheckConstraint(
            "evaluator_type IN ('deterministic', 'manual', 'structured_llm')",
            name="valid_evaluator_type",
        ),
        sa.CheckConstraint(
            "json_valid(reason_codes) AND json_type(reason_codes) = 'array'",
            name="reason_codes_json_array",
        ),
        sa.CheckConstraint(
            "(supersedes_response_id IS NULL AND evaluation_revision = 1) OR "
            "(supersedes_response_id IS NOT NULL AND evaluation_revision > 1)",
            name="correction_revision_shape",
        ),
        sa.UniqueConstraint("evaluation_id"),
        sa.UniqueConstraint("submission_id", "evaluation_revision"),
        sa.UniqueConstraint("task_id", "attempt_number", "evaluation_revision"),
    )
    op.create_index(
        "ix_diagnostic_responses_supersedes_unique",
        "diagnostic_responses",
        ["supersedes_response_id"],
        unique=True,
    )
    op.create_index(
        "ix_diagnostic_responses_task_created",
        "diagnostic_responses",
        ["task_id", "created_at"],
    )

    op.create_table(
        "diagnostic_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("result_id", sa.String(36), nullable=False),
        sa.Column("result_group_id", sa.String(36), nullable=False),
        sa.Column("result_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("axis", sa.String(60), nullable=False),
        sa.Column("modality", sa.String(20), server_default="text", nullable=False),
        sa.Column(
            "skill_id",
            sa.Integer(),
            sa.ForeignKey("skills.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("band", sa.String(40), nullable=False),
        sa.Column("cefr_band", sa.String(10), nullable=True),
        sa.Column("estimated_score", sa.Float(), nullable=True),
        sa.Column("estimate_confidence", sa.Float(), nullable=False),
        sa.Column("confidence_label", sa.String(20), nullable=False),
        sa.Column("positive_evidence_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("negative_evidence_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("insufficient_evidence_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("task_types", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("difficulty_min", sa.Integer(), nullable=True),
        sa.Column("difficulty_max", sa.Integer(), nullable=True),
        sa.Column("strengths", sa.Text(), server_default="", nullable=False),
        sa.Column("limitations", sa.Text(), server_default="", nullable=False),
        sa.Column("recommendation", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "projection_status", sa.String(30), server_default="not_projected", nullable=False
        ),
        sa.Column("result_version", sa.String(50), nullable=False),
        sa.Column(
            "supersedes_result_id",
            sa.Integer(),
            sa.ForeignKey("diagnostic_results.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("correction_reason", sa.String(200), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("result_revision >= 1", name="result_revision_positive"),
        sa.CheckConstraint("length(result_id) = 36", name="result_id_uuid_length"),
        sa.CheckConstraint("length(result_group_id) = 36", name="result_group_id_uuid_length"),
        sa.CheckConstraint(
            "axis IN ('reading_comprehension', 'written_production', 'active_grammar', "
            "'receptive_vocabulary', 'productive_vocabulary', 'listening_comprehension', "
            "'oral_production', 'pronunciation', 'written_fluency', 'oral_fluency', "
            "'communication_repair.typed', 'everyday_familiarity', "
            "'professional_laboratory_familiarity')",
            name="valid_axis",
        ),
        sa.CheckConstraint("modality = 'text'", name="text_only_v1"),
        sa.CheckConstraint(
            "band IN ('insufficient_evidence', 'initial_basis', 'developing', "
            "'functional_guided', 'consistent_sample')",
            name="valid_band",
        ),
        sa.CheckConstraint(
            "estimated_score IS NULL OR estimated_score BETWEEN 0 AND 1",
            name="estimated_score_range",
        ),
        sa.CheckConstraint(
            "(band = 'insufficient_evidence' AND estimated_score IS NULL) OR "
            "(band != 'insufficient_evidence' AND estimated_score IS NOT NULL)",
            name="band_score_consistent",
        ),
        sa.CheckConstraint("estimate_confidence BETWEEN 0 AND 1", name="estimate_confidence_range"),
        sa.CheckConstraint(
            "confidence_label IN ('low', 'moderate', 'high')", name="valid_confidence_label"
        ),
        sa.CheckConstraint("positive_evidence_count >= 0", name="positive_count_nonnegative"),
        sa.CheckConstraint("negative_evidence_count >= 0", name="negative_count_nonnegative"),
        sa.CheckConstraint(
            "insufficient_evidence_count >= 0", name="insufficient_count_nonnegative"
        ),
        sa.CheckConstraint(
            "json_valid(task_types) AND json_type(task_types) = 'array'",
            name="task_types_json_array",
        ),
        sa.CheckConstraint(
            "(difficulty_min IS NULL AND difficulty_max IS NULL) OR "
            "(difficulty_min BETWEEN 1 AND 5 AND difficulty_max BETWEEN difficulty_min AND 5)",
            name="valid_difficulty_range",
        ),
        sa.CheckConstraint("cefr_band IS NULL OR cefr_band IN ('pre-A1', 'A1')", name="valid_cefr"),
        sa.CheckConstraint(
            "cefr_band IS NULL OR (skill_id IS NOT NULL AND positive_evidence_count >= 2 "
            "AND estimate_confidence >= 0.6)",
            name="cefr_requires_skill_evidence",
        ),
        sa.CheckConstraint(
            "axis NOT IN ('listening_comprehension', 'oral_production', 'pronunciation', "
            "'oral_fluency') OR (band = 'insufficient_evidence' AND cefr_band IS NULL "
            "AND positive_evidence_count = 0 AND negative_evidence_count = 0 "
            "AND difficulty_min IS NULL AND difficulty_max IS NULL)",
            name="future_modalities_unassessed",
        ),
        sa.CheckConstraint("projection_status = 'not_projected'", name="not_projected_v1"),
        sa.CheckConstraint(
            "(supersedes_result_id IS NULL AND result_revision = 1) OR "
            "(supersedes_result_id IS NOT NULL AND result_revision > 1)",
            name="correction_revision_shape",
        ),
        sa.UniqueConstraint("result_id"),
        sa.UniqueConstraint("result_group_id", "result_revision"),
    )
    op.create_index(
        "ix_diagnostic_results_supersedes_unique",
        "diagnostic_results",
        ["supersedes_result_id"],
        unique=True,
    )
    op.create_index(
        "ix_diagnostic_results_session_axis", "diagnostic_results", ["session_id", "axis"]
    )

    _create_immutability_triggers()
    _create_correction_triggers()
    op.execute(
        """
        CREATE TRIGGER diagnostic_tasks_preserve_presented
        BEFORE DELETE ON diagnostic_tasks
        WHEN OLD.status != 'selected'
        BEGIN
            SELECT RAISE(ABORT, 'a presented diagnostic task cannot be deleted');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER diagnostic_tasks_no_return_to_selected
        BEFORE UPDATE OF status ON diagnostic_tasks
        WHEN OLD.status != 'selected' AND NEW.status = 'selected'
        BEGIN
            SELECT RAISE(ABORT, 'a presented diagnostic task cannot return to selected');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER diagnostic_tasks_lock_presented_content
        BEFORE UPDATE OF
            session_id, sequence, template_id, template_version, task_type,
            primary_axis, secondary_axes, skill_id, difficulty, modality,
            selection_reason, content, options, expected_answer, rubric, origin,
            generator_version, selected_at, presented_at
        ON diagnostic_tasks
        WHEN OLD.status != 'selected'
        BEGIN
            SELECT RAISE(ABORT, 'presented diagnostic task content is immutable');
        END
        """
    )


def _create_immutability_triggers() -> None:
    for table, label in (
        ("diagnostic_responses", "diagnostic response evaluation"),
        ("diagnostic_results", "diagnostic result"),
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{label} is immutable');
            END
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{label} is immutable');
            END
            """
        )


def _create_correction_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER diagnostic_responses_validate_correction
        BEFORE INSERT ON diagnostic_responses
        WHEN NEW.supersedes_response_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM diagnostic_responses AS original
            WHERE original.id = NEW.supersedes_response_id
              AND original.task_id = NEW.task_id
              AND original.submission_id = NEW.submission_id
              AND original.attempt_number = NEW.attempt_number
              AND original.response_text IS NEW.response_text
              AND original.response_language IS NEW.response_language
              AND original.instruction_state = NEW.instruction_state
              AND original.assistance = NEW.assistance
              AND original.active_seconds IS NEW.active_seconds
              AND original.submitted_at = NEW.submitted_at
              AND original.diagnostic_version = NEW.diagnostic_version
              AND original.task_version = NEW.task_version
              AND NEW.evaluation_revision = original.evaluation_revision + 1
        )
        BEGIN
            SELECT RAISE(ABORT, 'a response correction must preserve the submitted response');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER diagnostic_results_validate_correction
        BEFORE INSERT ON diagnostic_results
        WHEN NEW.supersedes_result_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM diagnostic_results AS original
            WHERE original.id = NEW.supersedes_result_id
              AND original.result_group_id = NEW.result_group_id
              AND original.session_id = NEW.session_id
              AND original.axis = NEW.axis
              AND original.modality = NEW.modality
              AND original.skill_id IS NEW.skill_id
              AND NEW.result_revision = original.result_revision + 1
        )
        BEGIN
            SELECT RAISE(ABORT, 'a result correction must preserve its diagnostic dimension');
        END
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    session_count = connection.scalar(sa.text("SELECT COUNT(*) FROM diagnostic_sessions")) or 0
    if session_count and os.environ.get("DEUTSCHOS_ALLOW_DESTRUCTIVE_DOWNGRADE") != "1":
        raise RuntimeError(
            "Downgrade 0005 would delete diagnostic history. It was refused. "
            "Create a verified backup and explicitly opt in only for an intentional reset."
        )

    for trigger in (
        "diagnostic_tasks_lock_presented_content",
        "diagnostic_tasks_no_return_to_selected",
        "diagnostic_tasks_preserve_presented",
        "diagnostic_results_validate_correction",
        "diagnostic_responses_validate_correction",
        "diagnostic_results_no_delete",
        "diagnostic_results_no_update",
        "diagnostic_responses_no_delete",
        "diagnostic_responses_no_update",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")

    op.drop_index("ix_diagnostic_results_session_axis", table_name="diagnostic_results")
    op.drop_index("ix_diagnostic_results_supersedes_unique", table_name="diagnostic_results")
    op.drop_table("diagnostic_results")
    op.drop_index("ix_diagnostic_responses_task_created", table_name="diagnostic_responses")
    op.drop_index("ix_diagnostic_responses_supersedes_unique", table_name="diagnostic_responses")
    op.drop_table("diagnostic_responses")
    op.drop_index("ix_diagnostic_tasks_session_status", table_name="diagnostic_tasks")
    op.drop_table("diagnostic_tasks")
    op.drop_index("ix_diagnostic_sessions_status_updated", table_name="diagnostic_sessions")
    op.drop_table("diagnostic_sessions")
