"""Add the evidence ledger and seed the initial skill catalogue.

Revision ID: 0003
Revises: 0002
"""

import os

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SKILLS = [
    {
        "code": "grammar.foundation",
        "name": "Gramática fundamental",
        "category": "grammar",
        "description": "Patrones gramaticales generales observados en producción y comprensión.",
    },
    {
        "code": "vocabulary.general",
        "name": "Vocabulario general",
        "category": "vocabulary",
        "description": "Reconocimiento y producción de vocabulario cotidiano.",
    },
    {
        "code": "reading.general",
        "name": "Comprensión lectora",
        "category": "reading",
        "description": "Comprensión de textos alemanes sin atribuir un nivel CEFR global.",
    },
    {
        "code": "listening.general",
        "name": "Comprensión auditiva",
        "category": "listening",
        "description": "Comprensión de alemán hablado a partir de evidencia explícita.",
    },
    {
        "code": "writing.general",
        "name": "Producción escrita",
        "category": "writing",
        "description": "Claridad y corrección en producción escrita espontánea.",
    },
    {
        "code": "speaking.general",
        "name": "Producción oral",
        "category": "speaking",
        "description": "Producción oral espontánea cuando exista evidencia de voz.",
    },
    {
        "code": "pronunciation.general",
        "name": "Pronunciación",
        "category": "pronunciation",
        "description": "Inteligibilidad y patrones fonéticos observados explícitamente.",
    },
    {
        "code": "professional.laboratory",
        "name": "Alemán profesional de laboratorio",
        "category": "professional_language",
        "description": "Lenguaje clínico, biomédico, microbiológico y de laboratorio.",
    },
]


def upgrade() -> None:
    connection = op.get_bind()
    missing_skills: list[dict[str, str]] = []
    for skill in SKILLS:
        existing = (
            connection.execute(
                sa.text(
                    "SELECT name, category, cefr_level, description FROM skills WHERE code = :code"
                ),
                {"code": skill["code"]},
            )
            .mappings()
            .first()
        )
        if existing is None:
            missing_skills.append(skill)
            continue
        expected = {
            "name": skill["name"],
            "category": skill["category"],
            "cefr_level": None,
            "description": skill["description"],
        }
        if dict(existing) != expected:
            raise RuntimeError(
                f"Skill {skill['code']} already exists with different data. "
                "Migration stopped without overwriting it."
            )

    op.create_table(
        "skill_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("submission_id", sa.String(36), nullable=False),
        sa.Column(
            "exercise_attempt_id",
            sa.Integer(),
            sa.ForeignKey("exercise_attempts.id"),
            nullable=False,
        ),
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("engine_version", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        sa.CheckConstraint("source IN ('manual_assessment', 'diagnostic')", name="valid_source"),
        sa.UniqueConstraint("submission_id"),
        sa.UniqueConstraint("exercise_attempt_id", "skill_id"),
    )
    op.create_index("ix_skill_evidence_skill_created", "skill_evidence", ["skill_id", "created_at"])
    op.execute(
        """
        CREATE TRIGGER skill_evidence_no_update
        BEFORE UPDATE ON skill_evidence
        BEGIN
            SELECT RAISE(ABORT, 'skill evidence is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER skill_evidence_no_delete
        BEFORE DELETE ON skill_evidence
        BEGIN
            SELECT RAISE(ABORT, 'skill evidence is immutable');
        END
        """
    )

    for skill in missing_skills:
        connection.execute(
            sa.text(
                """
                INSERT INTO skills (code, name, category, cefr_level, description)
                VALUES (:code, :name, :category, NULL, :description)
                """
            ),
            skill,
        )


def downgrade() -> None:
    connection = op.get_bind()
    evidence_count = connection.scalar(sa.text("SELECT COUNT(*) FROM skill_evidence")) or 0
    if evidence_count and os.environ.get("DEUTSCHOS_ALLOW_DESTRUCTIVE_DOWNGRADE") != "1":
        raise RuntimeError(
            "Downgrade 0003 would delete immutable learning evidence. It was refused. "
            "Create a verified backup and explicitly opt in only for an intentional reset."
        )
    op.execute("DROP TRIGGER IF EXISTS skill_evidence_no_delete")
    op.execute("DROP TRIGGER IF EXISTS skill_evidence_no_update")
    op.drop_index("ix_skill_evidence_skill_created", table_name="skill_evidence")
    op.drop_table("skill_evidence")
    # Catalogue rows remain: deleting them could invalidate existing StudentSkill rows.
