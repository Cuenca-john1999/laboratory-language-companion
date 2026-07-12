"""Foundation schema and seed profile.

Revision ID: 0001
"""

import os

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("preferred_name", sa.String(100), nullable=False),
        sa.Column("native_language", sa.String(100), nullable=False),
        sa.Column("additional_languages", sa.Text(), nullable=False),
        sa.Column("current_location", sa.String(200), nullable=False),
        sa.Column("professional_background", sa.Text(), nullable=False),
        sa.Column("learning_goals", sa.Text(), nullable=False),
        sa.Column("interests", sa.Text(), nullable=False),
        sa.Column("learning_preferences", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("cefr_level", sa.String(10)),
        sa.Column("description", sa.Text(), nullable=False),
    )
    op.create_table(
        "learning_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_type", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("model_used", sa.String(200)),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("user_motivation_before", sa.Integer()),
        sa.Column("user_motivation_after", sa.Integer()),
    )
    op.create_table(
        "mistakes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("corrected_text", sa.Text(), nullable=False),
        sa.Column("explanation_es", sa.Text(), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("subcategory", sa.String(100)),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("next_review_at", sa.DateTime()),
        sa.Column("status", sa.String(20), nullable=False),
    )
    op.create_table(
        "vocabulary_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lemma", sa.String(200), nullable=False),
        sa.Column("article", sa.String(20)),
        sa.Column("plural", sa.String(200)),
        sa.Column("part_of_speech", sa.String(50), nullable=False),
        sa.Column("translation_es", sa.String(300), nullable=False),
        sa.Column("example_de", sa.Text(), nullable=False),
        sa.Column("example_es", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "student_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "skill_id", sa.Integer(), sa.ForeignKey("skills.id"), nullable=False, unique=True
        ),
        sa.Column("estimated_mastery", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("last_practised_at", sa.DateTime()),
        sa.Column("next_review_at", sa.DateTime()),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "estimated_mastery >= 0 AND estimated_mastery <= 1",
            name="ck_student_skills_mastery_range",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_student_skills_confidence_range"
        ),
    )
    op.create_table(
        "exercise_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "learning_session_id",
            sa.Integer(),
            sa.ForeignKey("learning_sessions.id"),
            nullable=False,
        ),
        sa.Column("exercise_type", sa.String(50), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("student_answer", sa.Text(), nullable=False),
        sa.Column("expected_answer", sa.Text()),
        sa.Column("score", sa.Float()),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "student_vocabulary",
        sa.Column(
            "vocabulary_item_id",
            sa.Integer(),
            sa.ForeignKey("vocabulary_items.id"),
            primary_key=True,
        ),
        sa.Column("recognition_mastery", sa.Float(), nullable=False),
        sa.Column("written_production_mastery", sa.Float(), nullable=False),
        sa.Column("spoken_production_mastery", sa.Float(), nullable=False),
        sa.Column("next_review_at", sa.DateTime()),
        sa.Column("last_reviewed_at", sa.DateTime()),
        sa.CheckConstraint(
            "recognition_mastery BETWEEN 0 AND 1", name="ck_student_vocabulary_recognition_range"
        ),
        sa.CheckConstraint(
            "written_production_mastery BETWEEN 0 AND 1", name="ck_student_vocabulary_written_range"
        ),
        sa.CheckConstraint(
            "spoken_production_mastery BETWEEN 0 AND 1", name="ck_student_vocabulary_spoken_range"
        ),
    )
    profile = sa.table(
        "student_profiles",
        sa.column("preferred_name"),
        sa.column("native_language"),
        sa.column("additional_languages"),
        sa.column("current_location"),
        sa.column("professional_background"),
        sa.column("learning_goals"),
        sa.column("interests"),
        sa.column("learning_preferences"),
    )
    op.bulk_insert(
        profile,
        [
            {
                "preferred_name": "Jhon",
                "native_language": "español",
                "additional_languages": '["inglés (aprox. B2)"]',
                "current_location": "Alemania",
                "professional_background": "Entorno clínico, biomédico, microbiológico y de investigación de laboratorio",
                "learning_goals": '["Alcanzar alemán B1 tan rápido como sea realista", "Continuar hacia B2", "Preparar el reconocimiento profesional en Alemania", "Mejorar comprensión auditiva y habla espontánea"]',
                "interests": '["microbiología", "investigación biomédica", "inteligencia artificial", "tecnología", "Destiny", "Frostpunk", "Subnautica", "Resident Evil", "juegos de supervivencia y estrategia", "Attack on Titan", "anime isekai y romance"]',
                "learning_preferences": '{"explanations_language": "español", "priority": ["listening", "spontaneous_speech"], "note": "No inferir una única etiqueta CEFR; lectura más fuerte que habla y escucha"}',
            }
        ],
    )


def downgrade() -> None:
    if os.environ.get("DEUTSCHOS_ALLOW_DESTRUCTIVE_DOWNGRADE") != "1":
        raise RuntimeError(
            "Downgrade 0001 would delete all DeutschOS learning data. "
            "It was refused. Create a verified backup and set "
            "DEUTSCHOS_ALLOW_DESTRUCTIVE_DOWNGRADE=1 only for an intentional reset."
        )
    for table in [
        "student_vocabulary",
        "exercise_attempts",
        "student_skills",
        "vocabulary_items",
        "mistakes",
        "learning_sessions",
        "skills",
        "student_profiles",
    ]:
        op.drop_table(table)
