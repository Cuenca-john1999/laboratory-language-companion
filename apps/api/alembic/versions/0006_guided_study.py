"""Add private, non-evaluative guided study persistence.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "study_section_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("student_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(100), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("section_stable_key", sa.String(100), nullable=False),
        sa.Column("section_title", sa.String(500), nullable=False),
        sa.Column("practical_status", sa.String(30), server_default="not_started", nullable=False),
        sa.Column("current_pdf_page", sa.Integer(), nullable=True),
        sa.Column("printed_page_label", sa.String(100), nullable=True),
        sa.Column("selection_origin", sa.String(40), nullable=False),
        sa.Column(
            "last_activity_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("profile_id = 1", name="single_user"),
        sa.CheckConstraint(
            "practical_status IN ('not_started','in_progress','viewed','needs_review',"
            "'completed_by_user','paused')",
            name="valid_practical_status",
        ),
        sa.CheckConstraint(
            "selection_origin IN ('first_section','section_picker','concept_search',"
            "'manual_page','already_studying','recommendation')",
            name="valid_selection_origin",
        ),
        sa.CheckConstraint(
            "current_pdf_page IS NULL OR current_pdf_page > 0", name="page_positive"
        ),
        sa.UniqueConstraint("profile_id", "source_id", "section_stable_key"),
    )
    op.create_index(
        "ix_study_section_states_status_activity",
        "study_section_states",
        ["practical_status", "last_activity_at"],
    )

    op.create_table(
        "study_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("student_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "section_state_id",
            sa.Integer(),
            sa.ForeignKey("study_section_states.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_id", sa.String(100), nullable=True),
        sa.Column("source_version", sa.Integer(), nullable=True),
        sa.Column("source_name", sa.String(500), nullable=True),
        sa.Column("section_id", sa.Integer(), nullable=True),
        sa.Column("section_stable_key", sa.String(100), nullable=True),
        sa.Column("section_title", sa.String(500), nullable=False),
        sa.Column("concept_name", sa.String(300), nullable=True),
        sa.Column("pdf_page_start", sa.Integer(), nullable=True),
        sa.Column("pdf_page_end", sa.Integer(), nullable=True),
        sa.Column("current_pdf_page", sa.Integer(), nullable=True),
        sa.Column("printed_page_label", sa.String(100), nullable=True),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("mission_type", sa.String(40), nullable=False),
        sa.Column("mission", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("plan", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("checklist", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("planned_minutes", sa.Integer(), nullable=True),
        sa.Column("active_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "started_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column("paused_at", sa.DateTime(), nullable=True),
        sa.Column("resumed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("subjective_result", sa.String(40), nullable=True),
        sa.Column("final_pdf_page", sa.Integer(), nullable=True),
        sa.Column("final_workbook_exercise", sa.String(100), nullable=True),
        sa.Column("next_action", sa.String(100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("profile_id = 1", name="single_user"),
        sa.CheckConstraint("kind IN ('guided','free')", name="valid_kind"),
        sa.CheckConstraint(
            "status IN ('planned','active','paused','completed','abandoned')",
            name="valid_status",
        ),
        sa.CheckConstraint(
            "planned_minutes IS NULL OR planned_minutes IN (20,30,45,60)",
            name="valid_planned_minutes",
        ),
        sa.CheckConstraint("active_seconds >= 0", name="active_seconds_nonnegative"),
        sa.CheckConstraint(
            "pdf_page_start IS NULL OR pdf_page_start > 0", name="page_start_positive"
        ),
        sa.CheckConstraint("pdf_page_end IS NULL OR pdf_page_end > 0", name="page_end_positive"),
        sa.CheckConstraint(
            "pdf_page_end IS NULL OR pdf_page_start IS NULL OR pdf_page_end >= pdf_page_start",
            name="valid_page_range",
        ),
        sa.CheckConstraint(
            "current_pdf_page IS NULL OR current_pdf_page > 0", name="current_page_positive"
        ),
        sa.CheckConstraint(
            "subjective_result IS NULL OR subjective_result IN ('understood','needs_review',"
            "'unfinished','difficult','continue_next_time','change_topic')",
            name="valid_subjective_result",
        ),
        sa.CheckConstraint(
            "json_valid(mission) AND json_type(mission) = 'object'", name="mission_json_object"
        ),
        sa.CheckConstraint(
            "json_valid(plan) AND json_type(plan) = 'object'", name="plan_json_object"
        ),
        sa.CheckConstraint(
            "json_valid(checklist) AND json_type(checklist) = 'array'",
            name="checklist_json_array",
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_at >= started_at", name="valid_closed_time"
        ),
    )
    op.create_index(
        "ix_study_sessions_one_active",
        "study_sessions",
        ["profile_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_study_sessions_updated", "study_sessions", ["updated_at"])

    op.create_table(
        "study_notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("student_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("study_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_id", sa.String(100), nullable=True),
        sa.Column("section_stable_key", sa.String(100), nullable=True),
        sa.Column("concept_name", sa.String(300), nullable=True),
        sa.Column("pdf_page", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("profile_id = 1", name="single_user"),
        sa.CheckConstraint("length(text) > 0", name="text_nonempty"),
        sa.CheckConstraint("pdf_page IS NULL OR pdf_page > 0", name="page_positive"),
    )
    op.create_index("ix_study_notes_section", "study_notes", ["source_id", "section_stable_key"])

    op.create_table(
        "study_questions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("student_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("study_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_id", sa.String(100), nullable=True),
        sa.Column("section_stable_key", sa.String(100), nullable=True),
        sa.Column("concept_name", sa.String(300), nullable=True),
        sa.Column("pdf_page", sa.Integer(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer_query_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(20), server_default="open", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("profile_id = 1", name="single_user"),
        sa.CheckConstraint("length(question) > 0", name="question_nonempty"),
        sa.CheckConstraint(
            "status IN ('open','clarified','revisit','archived')", name="valid_status"
        ),
        sa.CheckConstraint("pdf_page IS NULL OR pdf_page > 0", name="page_positive"),
    )
    op.create_index(
        "ix_study_questions_status_section",
        "study_questions",
        ["status", "section_stable_key"],
    )

    op.create_table(
        "study_workbook_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("student_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("theory_source_id", sa.String(100), nullable=False),
        sa.Column("theory_source_version", sa.Integer(), nullable=False),
        sa.Column("section_stable_key", sa.String(100), nullable=False),
        sa.Column("workbook_source_id", sa.String(100), nullable=False),
        sa.Column("workbook_source_version", sa.Integer(), nullable=False),
        sa.Column("workbook_pdf_page", sa.Integer(), nullable=False),
        sa.Column("workbook_printed_page", sa.String(100), nullable=True),
        sa.Column("exercise_start", sa.String(100), nullable=True),
        sa.Column("exercise_end", sa.String(100), nullable=True),
        sa.Column("region", sa.String(20), server_default="unknown", nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), server_default="candidate", nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("profile_id = 1", name="single_user"),
        sa.CheckConstraint("workbook_pdf_page > 0", name="page_positive"),
        sa.CheckConstraint(
            "region IN ('full','left','right','both','unknown')", name="valid_region"
        ),
        sa.CheckConstraint(
            "status IN ('candidate','user_confirmed','rejected','stale')", name="valid_status"
        ),
        sa.UniqueConstraint(
            "profile_id",
            "theory_source_id",
            "section_stable_key",
            "workbook_source_id",
            "workbook_pdf_page",
            "exercise_start",
            "exercise_end",
        ),
    )
    op.create_index(
        "ix_study_workbook_links_section",
        "study_workbook_links",
        ["theory_source_id", "section_stable_key"],
    )

    op.create_table(
        "study_preferences",
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("student_profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mission_preference", sa.String(40), server_default="automatic", nullable=False),
        sa.Column("active_source_id", sa.String(100), nullable=True),
        sa.Column("active_section_stable_key", sa.String(100), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("profile_id = 1", name="single_user"),
        sa.CheckConstraint(
            "mission_preference IN ('automatic','standard','laboratory','frozen_city',"
            "'underwater_exploration','space_mission','mixed')",
            name="valid_mission_preference",
        ),
    )

    op.create_table(
        "study_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("student_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation_id", sa.String(100), nullable=False, unique=True),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("before_state", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("after_state", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "reverts_event_id",
            sa.Integer(),
            sa.ForeignKey("study_events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("profile_id = 1", name="single_user"),
        sa.CheckConstraint(
            "json_valid(before_state) AND json_type(before_state) = 'object'",
            name="before_json_object",
        ),
        sa.CheckConstraint(
            "json_valid(after_state) AND json_type(after_state) = 'object'",
            name="after_json_object",
        ),
    )
    op.create_index(
        "ix_study_events_target_created",
        "study_events",
        ["target_type", "target_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_study_events_target_created", table_name="study_events")
    op.drop_table("study_events")
    op.drop_table("study_preferences")
    op.drop_index("ix_study_workbook_links_section", table_name="study_workbook_links")
    op.drop_table("study_workbook_links")
    op.drop_index("ix_study_questions_status_section", table_name="study_questions")
    op.drop_table("study_questions")
    op.drop_index("ix_study_notes_section", table_name="study_notes")
    op.drop_table("study_notes")
    op.drop_index("ix_study_sessions_updated", table_name="study_sessions")
    op.drop_index("ix_study_sessions_one_active", table_name="study_sessions")
    op.drop_table("study_sessions")
    op.drop_index("ix_study_section_states_status_activity", table_name="study_section_states")
    op.drop_table("study_section_states")
