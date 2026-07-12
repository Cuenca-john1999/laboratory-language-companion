"""Harden constraints and add review indexes without discarding data.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def assert_existing_data_is_safe() -> None:
    connection = op.get_bind()
    validations = {
        "student_profiles": """
            SELECT COUNT(*) FROM student_profiles
            WHERE id != 1
               OR NOT json_valid(additional_languages)
               OR json_type(additional_languages) != 'array'
               OR NOT json_valid(learning_goals)
               OR json_type(learning_goals) != 'array'
               OR NOT json_valid(interests)
               OR json_type(interests) != 'array'
               OR NOT json_valid(learning_preferences)
               OR json_type(learning_preferences) != 'object'
        """,
        "skills": """
            SELECT COUNT(*) FROM skills WHERE category NOT IN
            ('grammar', 'vocabulary', 'reading', 'listening', 'writing',
             'speaking', 'pronunciation', 'professional_language')
        """,
        "student_skills": """
            SELECT COUNT(*) FROM student_skills
            WHERE evidence_count < 0
               OR estimated_mastery NOT BETWEEN 0 AND 1
               OR confidence NOT BETWEEN 0 AND 1
        """,
        "mistakes": """
            SELECT COUNT(*) FROM mistakes
            WHERE confidence NOT BETWEEN 0 AND 1
               OR occurrence_count < 1
               OR status NOT IN ('new', 'learning', 'improving', 'mastered', 'ignored')
        """,
        "exercise_attempts": """
            SELECT COUNT(*) FROM exercise_attempts
            WHERE score IS NOT NULL AND score NOT BETWEEN 0 AND 1
        """,
        "learning_sessions": """
            SELECT COUNT(*) FROM learning_sessions
            WHERE (duration_seconds IS NOT NULL AND duration_seconds < 0)
               OR (completed_at IS NOT NULL AND completed_at < started_at)
        """,
    }
    invalid = [table for table, query in validations.items() if connection.scalar(sa.text(query))]
    if invalid:
        tables = ", ".join(invalid)
        raise RuntimeError(
            f"Migration 0002 stopped: invalid existing values in {tables}. "
            "No learning data was modified. Repair or export those rows before retrying."
        )


def upgrade() -> None:
    assert_existing_data_is_safe()

    with op.batch_alter_table("student_profiles") as batch_op:
        batch_op.create_check_constraint("single_user", "id = 1")
        batch_op.create_check_constraint(
            "additional_languages_json_array",
            "json_valid(additional_languages) AND json_type(additional_languages) = 'array'",
        )
        batch_op.create_check_constraint(
            "learning_goals_json_array",
            "json_valid(learning_goals) AND json_type(learning_goals) = 'array'",
        )
        batch_op.create_check_constraint(
            "interests_json_array",
            "json_valid(interests) AND json_type(interests) = 'array'",
        )
        batch_op.create_check_constraint(
            "learning_preferences_json_object",
            "json_valid(learning_preferences) AND json_type(learning_preferences) = 'object'",
        )

    with op.batch_alter_table("skills") as batch_op:
        batch_op.create_check_constraint(
            "valid_category",
            "category IN ('grammar', 'vocabulary', 'reading', 'listening', 'writing', "
            "'speaking', 'pronunciation', 'professional_language')",
        )

    with op.batch_alter_table("student_skills") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_student_skills_ck_student_skills_mastery_range"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_student_skills_ck_student_skills_confidence_range"), type_="check"
        )
        batch_op.create_check_constraint(
            "mastery_range", "estimated_mastery >= 0 AND estimated_mastery <= 1"
        )
        batch_op.create_check_constraint("confidence_range", "confidence >= 0 AND confidence <= 1")
        batch_op.create_check_constraint("evidence_nonnegative", "evidence_count >= 0")

    with op.batch_alter_table("student_vocabulary") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_student_vocabulary_ck_student_vocabulary_recognition_range"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_student_vocabulary_ck_student_vocabulary_written_range"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_student_vocabulary_ck_student_vocabulary_spoken_range"), type_="check"
        )
        batch_op.create_check_constraint("recognition_range", "recognition_mastery BETWEEN 0 AND 1")
        batch_op.create_check_constraint(
            "written_range", "written_production_mastery BETWEEN 0 AND 1"
        )
        batch_op.create_check_constraint(
            "spoken_range", "spoken_production_mastery BETWEEN 0 AND 1"
        )

    with op.batch_alter_table("mistakes") as batch_op:
        batch_op.create_check_constraint("confidence_range", "confidence >= 0 AND confidence <= 1")
        batch_op.create_check_constraint("occurrence_positive", "occurrence_count >= 1")
        batch_op.create_check_constraint(
            "valid_status",
            "status IN ('new', 'learning', 'improving', 'mastered', 'ignored')",
        )

    with op.batch_alter_table("exercise_attempts") as batch_op:
        batch_op.create_check_constraint(
            "score_range",
            "score IS NULL OR (score >= 0 AND score <= 1)",
        )

    with op.batch_alter_table("learning_sessions") as batch_op:
        batch_op.create_check_constraint(
            "duration_nonnegative",
            "duration_seconds IS NULL OR duration_seconds >= 0",
        )
        batch_op.create_check_constraint(
            "valid_time_range",
            "completed_at IS NULL OR completed_at >= started_at",
        )

    op.create_index("ix_learning_sessions_started_at", "learning_sessions", ["started_at"])
    op.create_index("ix_mistakes_review", "mistakes", ["status", "next_review_at"])
    op.create_index("ix_student_skills_next_review", "student_skills", ["next_review_at"])
    op.create_index("ix_student_vocabulary_next_review", "student_vocabulary", ["next_review_at"])


def downgrade() -> None:
    op.drop_index("ix_student_vocabulary_next_review", table_name="student_vocabulary")
    op.drop_index("ix_student_skills_next_review", table_name="student_skills")
    op.drop_index("ix_mistakes_review", table_name="mistakes")
    op.drop_index("ix_learning_sessions_started_at", table_name="learning_sessions")

    with op.batch_alter_table("exercise_attempts") as batch_op:
        batch_op.drop_constraint(op.f("ck_exercise_attempts_score_range"), type_="check")
    with op.batch_alter_table("learning_sessions") as batch_op:
        batch_op.drop_constraint(op.f("ck_learning_sessions_valid_time_range"), type_="check")
        batch_op.drop_constraint(op.f("ck_learning_sessions_duration_nonnegative"), type_="check")
    with op.batch_alter_table("mistakes") as batch_op:
        batch_op.drop_constraint(op.f("ck_mistakes_valid_status"), type_="check")
        batch_op.drop_constraint(op.f("ck_mistakes_occurrence_positive"), type_="check")
        batch_op.drop_constraint(op.f("ck_mistakes_confidence_range"), type_="check")
    with op.batch_alter_table("student_skills") as batch_op:
        batch_op.drop_constraint(op.f("ck_student_skills_evidence_nonnegative"), type_="check")
        batch_op.drop_constraint(op.f("ck_student_skills_confidence_range"), type_="check")
        batch_op.drop_constraint(op.f("ck_student_skills_mastery_range"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_student_skills_ck_student_skills_mastery_range"),
            "estimated_mastery >= 0 AND estimated_mastery <= 1",
        )
        batch_op.create_check_constraint(
            op.f("ck_student_skills_ck_student_skills_confidence_range"),
            "confidence >= 0 AND confidence <= 1",
        )
    with op.batch_alter_table("student_vocabulary") as batch_op:
        batch_op.drop_constraint(op.f("ck_student_vocabulary_spoken_range"), type_="check")
        batch_op.drop_constraint(op.f("ck_student_vocabulary_written_range"), type_="check")
        batch_op.drop_constraint(op.f("ck_student_vocabulary_recognition_range"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_student_vocabulary_ck_student_vocabulary_recognition_range"),
            "recognition_mastery BETWEEN 0 AND 1",
        )
        batch_op.create_check_constraint(
            op.f("ck_student_vocabulary_ck_student_vocabulary_written_range"),
            "written_production_mastery BETWEEN 0 AND 1",
        )
        batch_op.create_check_constraint(
            op.f("ck_student_vocabulary_ck_student_vocabulary_spoken_range"),
            "spoken_production_mastery BETWEEN 0 AND 1",
        )
    with op.batch_alter_table("skills") as batch_op:
        batch_op.drop_constraint(op.f("ck_skills_valid_category"), type_="check")
    with op.batch_alter_table("student_profiles") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_student_profiles_learning_preferences_json_object"), type_="check"
        )
        batch_op.drop_constraint(op.f("ck_student_profiles_interests_json_array"), type_="check")
        batch_op.drop_constraint(
            op.f("ck_student_profiles_learning_goals_json_array"), type_="check"
        )
        batch_op.drop_constraint(
            op.f("ck_student_profiles_additional_languages_json_array"), type_="check"
        )
        batch_op.drop_constraint(op.f("ck_student_profiles_single_user"), type_="check")
