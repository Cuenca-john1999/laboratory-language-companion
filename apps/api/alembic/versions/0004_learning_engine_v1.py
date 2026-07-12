"""Add the versioned A0-A1 curriculum and auditable daily learning state.

Revision ID: 0004
Revises: 0003

The migration is additive on upgrade. It deliberately leaves the eight broad
0003 catalogue rows untouched and gives them no membership in the new
curriculum, so existing evidence keeps its original meaning.
"""

import json
import os

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

CURRICULUM_VERSION = "a0-a1.v1"
DEFAULT_CRITERIA = {
    "min_mastery": 0.8,
    "min_confidence": 0.6,
    "min_evidence": 6,
    "unassisted_streak": 2,
}
NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}

SKILLS = [
    {
        "code": "grammar.personal_pronouns",
        "name": "Pronombres personales",
        "category": "grammar",
        "description": "Reconocer y usar los pronombres personales de sujeto en frases breves.",
        "cefr_reference": "A0",
        "difficulty": 1,
        "exercise_types": ["recognition", "gap_fill", "sentence_building"],
        "prerequisites": [],
    },
    {
        "code": "vocabulary.everyday_core",
        "name": "Vocabulario cotidiano esencial",
        "category": "vocabulary",
        "description": "Comprender y producir palabras frecuentes sobre identidad, hogar y rutina.",
        "cefr_reference": "A0",
        "difficulty": 1,
        "exercise_types": [
            "recognition",
            "listening_choice",
            "sentence_building",
            "guided_dialogue",
        ],
        "prerequisites": [],
    },
    {
        "code": "grammar.sein_present",
        "name": "Sein en presente",
        "category": "grammar",
        "description": "Conjugar sein en presente y usarlo para identidad, estado y procedencia.",
        "cefr_reference": "A0",
        "difficulty": 1,
        "exercise_types": ["gap_fill", "sentence_building", "short_answer", "guided_dialogue"],
        "prerequisites": ["grammar.personal_pronouns"],
    },
    {
        "code": "grammar.haben_present",
        "name": "Haben en presente",
        "category": "grammar",
        "description": "Conjugar haben en presente y expresar posesión o disponibilidad básica.",
        "cefr_reference": "A0",
        "difficulty": 1,
        "exercise_types": ["gap_fill", "sentence_building", "short_answer", "guided_dialogue"],
        "prerequisites": ["grammar.personal_pronouns"],
    },
    {
        "code": "grammar.present_regular",
        "name": "Presente de verbos regulares",
        "category": "grammar",
        "description": "Formar el presente de verbos regulares en enunciados cotidianos.",
        "cefr_reference": "A0",
        "difficulty": 2,
        "exercise_types": ["gap_fill", "sentence_building", "short_answer", "guided_dialogue"],
        "prerequisites": ["grammar.personal_pronouns", "vocabulary.everyday_core"],
    },
    {
        "code": "listening.basic",
        "name": "Comprensión oral básica",
        "category": "listening",
        "description": "Identificar información explícita en mensajes orales lentos y breves.",
        "cefr_reference": "A1",
        "difficulty": 2,
        "exercise_types": ["listening_choice", "listen_and_type"],
        "prerequisites": ["vocabulary.everyday_core", "grammar.sein_present"],
    },
    {
        "code": "grammar.articles_basic",
        "name": "Artículos básicos",
        "category": "grammar",
        "description": "Distinguir y usar artículos definidos e indefinidos en vocabulario conocido.",
        "cefr_reference": "A0",
        "difficulty": 2,
        "exercise_types": ["recognition", "gap_fill", "sentence_building"],
        "prerequisites": ["vocabulary.everyday_core"],
    },
    {
        "code": "grammar.nominative_basic",
        "name": "Nominativo básico",
        "category": "grammar",
        "description": "Reconocer y construir sujetos nominales sencillos en nominativo.",
        "cefr_reference": "A1",
        "difficulty": 2,
        "exercise_types": ["recognition", "gap_fill", "sentence_building"],
        "prerequisites": ["grammar.personal_pronouns", "grammar.articles_basic"],
    },
    {
        "code": "grammar.questions_basic",
        "name": "Preguntas básicas",
        "category": "grammar",
        "description": "Formar preguntas sí/no y preguntas con palabras interrogativas frecuentes.",
        "cefr_reference": "A1",
        "difficulty": 2,
        "exercise_types": [
            "sentence_building",
            "short_answer",
            "guided_dialogue",
            "listening_choice",
        ],
        "prerequisites": ["grammar.sein_present", "grammar.present_regular"],
    },
    {
        "code": "grammar.negation_basic",
        "name": "Negación básica",
        "category": "grammar",
        "description": "Negar enunciados sencillos con nicht y kein en contextos controlados.",
        "cefr_reference": "A1",
        "difficulty": 2,
        "exercise_types": ["gap_fill", "sentence_building", "short_answer", "guided_dialogue"],
        "prerequisites": ["grammar.present_regular", "grammar.articles_basic"],
    },
    {
        "code": "speaking.basic",
        "name": "Producción oral básica",
        "category": "speaking",
        "description": "Responder y mantener intercambios breves sobre información personal y cotidiana.",
        "cefr_reference": "A1",
        "difficulty": 3,
        "exercise_types": ["guided_dialogue", "spoken_response"],
        "prerequisites": [
            "vocabulary.everyday_core",
            "grammar.sein_present",
            "grammar.questions_basic",
        ],
    },
    {
        "code": "grammar.accusative_basic",
        "name": "Acusativo básico",
        "category": "grammar",
        "description": "Reconocer y usar objetos directos frecuentes con artículos básicos.",
        "cefr_reference": "A1",
        "difficulty": 3,
        "exercise_types": ["recognition", "gap_fill", "sentence_building", "guided_dialogue"],
        "prerequisites": [
            "grammar.nominative_basic",
            "grammar.present_regular",
            "grammar.articles_basic",
        ],
    },
    {
        "code": "grammar.modal_verbs_basic",
        "name": "Verbos modales básicos",
        "category": "grammar",
        "description": "Usar können, müssen y möchten en estructuras principales sencillas.",
        "cefr_reference": "A1",
        "difficulty": 3,
        "exercise_types": ["gap_fill", "sentence_building", "guided_dialogue", "spoken_response"],
        "prerequisites": ["grammar.present_regular", "grammar.questions_basic"],
    },
    {
        "code": "vocabulary.laboratory_intro",
        "name": "Vocabulario inicial de laboratorio",
        "category": "professional_language",
        "description": "Comprender y usar términos iniciales de materiales, acciones y seguridad de laboratorio.",
        "cefr_reference": "A1",
        "difficulty": 3,
        "exercise_types": [
            "recognition",
            "listening_choice",
            "sentence_building",
            "guided_dialogue",
        ],
        "prerequisites": ["vocabulary.everyday_core", "grammar.accusative_basic"],
    },
]


def _assert_skill_seed_is_safe(connection: sa.Connection) -> None:
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
            continue
        expected = {
            "name": skill["name"],
            "category": skill["category"],
            "cefr_level": skill["cefr_reference"],
            "description": skill["description"],
        }
        if dict(existing) != expected:
            raise RuntimeError(
                f"Skill {skill['code']} already exists with different data. "
                "Migration 0004 stopped without overwriting it."
            )


def _add_evidence_audit_columns() -> None:
    # ADD COLUMN preserves the 0003 table and its append-only triggers. The
    # nullable fields intentionally leave historical v1 rows uninterpreted.
    statements = [
        """
        ALTER TABLE skill_evidence ADD COLUMN outcome VARCHAR(30)
        CONSTRAINT ck_skill_evidence_valid_outcome CHECK (
            outcome IS NULL OR outcome IN
            ('failure', 'partial', 'correct_with_help', 'correct_without_help')
        )
        """,
        """
        ALTER TABLE skill_evidence ADD COLUMN supersedes_evidence_id INTEGER
        CONSTRAINT fk_skill_evidence_supersedes_evidence_id_skill_evidence
        REFERENCES skill_evidence(id)
        """,
        "ALTER TABLE skill_evidence ADD COLUMN mastery_before FLOAT "
        "CONSTRAINT ck_skill_evidence_mastery_before_range "
        "CHECK (mastery_before IS NULL OR mastery_before BETWEEN 0 AND 1)",
        "ALTER TABLE skill_evidence ADD COLUMN mastery_after FLOAT "
        "CONSTRAINT ck_skill_evidence_mastery_after_range "
        "CHECK (mastery_after IS NULL OR mastery_after BETWEEN 0 AND 1)",
        "ALTER TABLE skill_evidence ADD COLUMN confidence_before FLOAT "
        "CONSTRAINT ck_skill_evidence_confidence_before_range "
        "CHECK (confidence_before IS NULL OR confidence_before BETWEEN 0 AND 1)",
        "ALTER TABLE skill_evidence ADD COLUMN confidence_after FLOAT "
        "CONSTRAINT ck_skill_evidence_confidence_after_range "
        "CHECK (confidence_after IS NULL OR confidence_after BETWEEN 0 AND 1)",
        "ALTER TABLE skill_evidence ADD COLUMN evidence_count_before INTEGER "
        "CONSTRAINT ck_skill_evidence_evidence_count_before_nonnegative "
        "CHECK (evidence_count_before IS NULL OR evidence_count_before >= 0)",
        "ALTER TABLE skill_evidence ADD COLUMN evidence_count_after INTEGER "
        "CONSTRAINT ck_skill_evidence_evidence_count_after_nonnegative "
        "CHECK (evidence_count_after IS NULL OR evidence_count_after >= 0)",
        "ALTER TABLE skill_evidence ADD COLUMN unassisted_streak_before INTEGER "
        "CONSTRAINT ck_skill_evidence_unassisted_streak_before_nonnegative "
        "CHECK (unassisted_streak_before IS NULL OR unassisted_streak_before >= 0)",
        "ALTER TABLE skill_evidence ADD COLUMN unassisted_streak_after INTEGER "
        "CONSTRAINT ck_skill_evidence_unassisted_streak_after_nonnegative "
        "CHECK (unassisted_streak_after IS NULL OR unassisted_streak_after >= 0)",
        "ALTER TABLE skill_evidence ADD COLUMN lapse_count_before INTEGER "
        "CONSTRAINT ck_skill_evidence_lapse_count_before_nonnegative "
        "CHECK (lapse_count_before IS NULL OR lapse_count_before >= 0)",
        "ALTER TABLE skill_evidence ADD COLUMN lapse_count_after INTEGER "
        "CONSTRAINT ck_skill_evidence_lapse_count_after_nonnegative "
        "CHECK (lapse_count_after IS NULL OR lapse_count_after >= 0)",
        "ALTER TABLE skill_evidence ADD COLUMN next_review_at_before DATETIME",
        "ALTER TABLE skill_evidence ADD COLUMN next_review_at_after DATETIME",
    ]
    for statement in statements:
        op.execute(statement)
    op.create_index(
        "ix_skill_evidence_supersedes_unique",
        "skill_evidence",
        ["supersedes_evidence_id"],
        unique=True,
    )
    op.execute(
        """
        CREATE TRIGGER skill_evidence_require_snapshot
        BEFORE INSERT ON skill_evidence
        WHEN NEW.outcome IS NOT NULL AND (
            NEW.mastery_before IS NULL OR NEW.mastery_after IS NULL OR
            NEW.confidence_before IS NULL OR NEW.confidence_after IS NULL OR
            NEW.evidence_count_before IS NULL OR NEW.evidence_count_after IS NULL OR
            NEW.unassisted_streak_before IS NULL OR NEW.unassisted_streak_after IS NULL OR
            NEW.lapse_count_before IS NULL OR NEW.lapse_count_after IS NULL OR
            NEW.next_review_at_after IS NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'v2 skill evidence requires a complete state snapshot');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER skill_evidence_validate_correction
        BEFORE INSERT ON skill_evidence
        WHEN NEW.supersedes_evidence_id IS NOT NULL AND (
            NEW.supersedes_evidence_id = NEW.id OR NOT EXISTS (
                SELECT 1 FROM skill_evidence AS original
                WHERE original.id = NEW.supersedes_evidence_id
                  AND original.skill_id = NEW.skill_id
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'a correction must supersede existing evidence for the same skill');
        END
        """
    )


def upgrade() -> None:
    connection = op.get_bind()
    _assert_skill_seed_is_safe(connection)

    _add_evidence_audit_columns()

    with op.batch_alter_table("student_skills") as batch_op:
        batch_op.add_column(sa.Column("last_outcome", sa.String(30), nullable=True))
        batch_op.add_column(
            sa.Column("unassisted_streak", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("lapse_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(sa.Column("mastered_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("engine_version", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("last_evidence_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_student_skills_last_evidence_id_skill_evidence",
            "skill_evidence",
            ["last_evidence_id"],
            ["id"],
        )
        batch_op.create_check_constraint(
            "valid_last_outcome",
            "last_outcome IS NULL OR last_outcome IN "
            "('failure', 'partial', 'correct_with_help', 'correct_without_help')",
        )
        batch_op.create_check_constraint("unassisted_streak_nonnegative", "unassisted_streak >= 0")
        batch_op.create_check_constraint("lapse_count_nonnegative", "lapse_count >= 0")

    op.create_table(
        "curricula",
        sa.Column("version", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("cefr_from", sa.String(2), nullable=False),
        sa.Column("cefr_to", sa.String(2), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("cefr_from = 'A0'", name="cefr_from_a0"),
        sa.CheckConstraint("cefr_to = 'A1'", name="cefr_to_a1"),
    )
    op.create_table(
        "curriculum_skills",
        sa.Column(
            "curriculum_version",
            sa.String(30),
            sa.ForeignKey("curricula.version", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "skill_id",
            sa.Integer(),
            sa.ForeignKey("skills.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("curriculum_order", sa.Integer(), nullable=False),
        sa.Column("cefr_reference", sa.String(2), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("min_mastery", sa.Float(), nullable=False),
        sa.Column("min_confidence", sa.Float(), nullable=False),
        sa.Column("min_evidence", sa.Integer(), nullable=False),
        sa.Column("unassisted_streak", sa.Integer(), nullable=False),
        sa.Column("exercise_types", sa.Text(), nullable=False),
        sa.CheckConstraint("curriculum_order >= 1", name="order_positive"),
        sa.CheckConstraint("cefr_reference IN ('A0', 'A1')", name="cefr_a0_a1"),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name="difficulty_range"),
        sa.CheckConstraint("min_mastery BETWEEN 0 AND 1", name="mastery_range"),
        sa.CheckConstraint("min_confidence BETWEEN 0 AND 1", name="confidence_range"),
        sa.CheckConstraint("min_evidence >= 1", name="min_evidence_positive"),
        sa.CheckConstraint("unassisted_streak >= 0", name="required_unassisted_streak_nonnegative"),
        sa.CheckConstraint(
            "json_valid(exercise_types) AND json_type(exercise_types) = 'array'",
            name="exercise_types_json_array",
        ),
        sa.UniqueConstraint("curriculum_version", "curriculum_order"),
    )
    op.create_index(
        "ix_curricula_single_active",
        "curricula",
        ["is_active"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
    )
    op.create_table(
        "skill_prerequisites",
        sa.Column("curriculum_version", sa.String(30), primary_key=True),
        sa.Column("skill_id", sa.Integer(), primary_key=True),
        sa.Column("prerequisite_skill_id", sa.Integer(), primary_key=True),
        sa.Column("prerequisite_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["curriculum_version", "skill_id"],
            ["curriculum_skills.curriculum_version", "curriculum_skills.skill_id"],
            name="fk_skill_prerequisites_skill_membership",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version", "prerequisite_skill_id"],
            ["curriculum_skills.curriculum_version", "curriculum_skills.skill_id"],
            name="fk_skill_prerequisites_prerequisite_membership",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("skill_id != prerequisite_skill_id", name="not_self_referential"),
        sa.CheckConstraint("prerequisite_order >= 1", name="prerequisite_order_positive"),
        sa.UniqueConstraint("curriculum_version", "skill_id", "prerequisite_order"),
    )

    for skill in SKILLS:
        connection.execute(
            sa.text(
                """
                INSERT INTO skills (code, name, category, cefr_level, description)
                SELECT :code, :name, :category, :cefr_level, :description
                WHERE NOT EXISTS (SELECT 1 FROM skills WHERE code = :code)
                """
            ),
            {
                "code": skill["code"],
                "name": skill["name"],
                "category": skill["category"],
                "cefr_level": skill["cefr_reference"],
                "description": skill["description"],
            },
        )

    connection.execute(
        sa.text(
            """
            INSERT INTO curricula (version, name, cefr_from, cefr_to, is_active, created_at)
            VALUES (:version, :name, 'A0', 'A1', 1, CURRENT_TIMESTAMP)
            """
        ),
        {"version": CURRICULUM_VERSION, "name": "Currículo inicial A0 → A1"},
    )
    skill_ids = dict(
        connection.execute(
            sa.text("SELECT code, id FROM skills WHERE code IN :codes").bindparams(
                sa.bindparam("codes", expanding=True)
            ),
            {"codes": [skill["code"] for skill in SKILLS]},
        ).all()
    )
    for order, skill in enumerate(SKILLS, start=1):
        connection.execute(
            sa.text(
                """
                INSERT INTO curriculum_skills (
                    curriculum_version, skill_id, curriculum_order, cefr_reference,
                    difficulty, min_mastery, min_confidence, min_evidence,
                    unassisted_streak, exercise_types
                ) VALUES (
                    :curriculum_version, :skill_id, :curriculum_order, :cefr_reference,
                    :difficulty, :min_mastery, :min_confidence, :min_evidence,
                    :unassisted_streak, :exercise_types
                )
                """
            ),
            {
                "curriculum_version": CURRICULUM_VERSION,
                "skill_id": skill_ids[skill["code"]],
                "curriculum_order": order,
                "cefr_reference": skill["cefr_reference"],
                "difficulty": skill["difficulty"],
                "exercise_types": json.dumps(skill["exercise_types"], ensure_ascii=False),
                **DEFAULT_CRITERIA,
            },
        )
        for prerequisite_order, prerequisite_code in enumerate(skill["prerequisites"], start=1):
            connection.execute(
                sa.text(
                    """
                    INSERT INTO skill_prerequisites (
                        curriculum_version, skill_id, prerequisite_skill_id,
                        prerequisite_order
                    ) VALUES (
                        :curriculum_version, :skill_id, :prerequisite_skill_id,
                        :prerequisite_order
                    )
                    """
                ),
                {
                    "curriculum_version": CURRICULUM_VERSION,
                    "skill_id": skill_ids[skill["code"]],
                    "prerequisite_skill_id": skill_ids[prerequisite_code],
                    "prerequisite_order": prerequisite_order,
                },
            )

    op.create_table(
        "daily_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("student_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("requested_minutes", sa.Integer(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("motivation", sa.Integer(), nullable=False),
        sa.Column("intensity", sa.String(10), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column(
            "primary_skill_id",
            sa.Integer(),
            sa.ForeignKey("skills.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "new_skill_id",
            sa.Integer(),
            sa.ForeignKey("skills.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("internal_reason", sa.Text(), nullable=False),
        sa.Column("reason_codes", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.String(50), nullable=False),
        sa.Column(
            "curriculum_version",
            sa.String(30),
            sa.ForeignKey("curricula.version", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("profile_id = 1", name="single_user"),
        sa.CheckConstraint("requested_minutes BETWEEN 10 AND 120", name="requested_minutes_range"),
        sa.CheckConstraint(
            "duration_minutes BETWEEN 10 AND requested_minutes",
            name="duration_within_request",
        ),
        sa.CheckConstraint("motivation BETWEEN 1 AND 5", name="motivation_range"),
        sa.CheckConstraint("intensity IN ('low', 'normal', 'high')", name="valid_intensity"),
        sa.CheckConstraint(
            "json_valid(reason_codes) AND json_type(reason_codes) = 'array'",
            name="reason_codes_json_array",
        ),
    )
    op.create_index("ix_daily_plans_date_generated", "daily_plans", ["plan_date", "generated_at"])
    op.create_table(
        "daily_plan_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "daily_plan_id",
            sa.Integer(),
            sa.ForeignKey("daily_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column(
            "skill_id",
            sa.Integer(),
            sa.ForeignKey("skills.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("exercise_type", sa.String(50), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint("position >= 1", name="position_positive"),
        sa.CheckConstraint("kind IN ('review', 'new_skill', 'practice')", name="valid_kind"),
        sa.CheckConstraint("length(exercise_type) > 0", name="exercise_type_nonempty"),
        sa.CheckConstraint("duration_minutes > 0", name="duration_positive"),
        sa.UniqueConstraint("daily_plan_id", "position"),
    )


def _recreate_evidence_immutability_triggers() -> None:
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


def downgrade() -> None:
    connection = op.get_bind()
    new_data_count = (connection.scalar(sa.text("SELECT COUNT(*) FROM daily_plans")) or 0) + (
        connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM skill_evidence "
                "WHERE outcome IS NOT NULL OR supersedes_evidence_id IS NOT NULL"
            )
        )
        or 0
    )
    if new_data_count and os.environ.get("DEUTSCHOS_ALLOW_DESTRUCTIVE_DOWNGRADE") != "1":
        raise RuntimeError(
            "Downgrade 0004 would discard daily plans or v2 evidence metadata. "
            "It was refused. Create a verified backup and explicitly opt in only "
            "for an intentional destructive downgrade."
        )

    op.drop_table("daily_plan_blocks")
    op.drop_index("ix_daily_plans_date_generated", table_name="daily_plans")
    op.drop_table("daily_plans")
    op.drop_table("skill_prerequisites")
    op.drop_table("curriculum_skills")
    op.drop_table("curricula")

    with op.batch_alter_table("student_skills") as batch_op:
        batch_op.drop_constraint(
            "fk_student_skills_last_evidence_id_skill_evidence", type_="foreignkey"
        )
        batch_op.drop_constraint(op.f("ck_student_skills_lapse_count_nonnegative"), type_="check")
        batch_op.drop_constraint(
            op.f("ck_student_skills_unassisted_streak_nonnegative"), type_="check"
        )
        batch_op.drop_constraint(op.f("ck_student_skills_valid_last_outcome"), type_="check")
        for column in (
            "last_evidence_id",
            "engine_version",
            "mastered_at",
            "lapse_count",
            "unassisted_streak",
            "last_outcome",
        ):
            batch_op.drop_column(column)

    op.drop_index("ix_skill_evidence_supersedes_unique", table_name="skill_evidence")
    op.execute("DROP TRIGGER IF EXISTS skill_evidence_validate_correction")
    op.execute("DROP TRIGGER IF EXISTS skill_evidence_require_snapshot")
    op.execute("DROP TRIGGER IF EXISTS skill_evidence_no_delete")
    op.execute("DROP TRIGGER IF EXISTS skill_evidence_no_update")
    with op.batch_alter_table("skill_evidence", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.drop_constraint(
            "fk_skill_evidence_supersedes_evidence_id_skill_evidence", type_="foreignkey"
        )
        for constraint in (
            "valid_outcome",
            "mastery_before_range",
            "mastery_after_range",
            "confidence_before_range",
            "confidence_after_range",
            "evidence_count_before_nonnegative",
            "evidence_count_after_nonnegative",
            "unassisted_streak_before_nonnegative",
            "unassisted_streak_after_nonnegative",
            "lapse_count_before_nonnegative",
            "lapse_count_after_nonnegative",
        ):
            batch_op.drop_constraint(op.f(f"ck_skill_evidence_{constraint}"), type_="check")
        for column in (
            "next_review_at_after",
            "next_review_at_before",
            "lapse_count_after",
            "lapse_count_before",
            "unassisted_streak_after",
            "unassisted_streak_before",
            "evidence_count_after",
            "evidence_count_before",
            "confidence_after",
            "confidence_before",
            "mastery_after",
            "mastery_before",
            "supersedes_evidence_id",
            "outcome",
        ):
            batch_op.drop_column(column)
    _recreate_evidence_immutability_triggers()
    # Seeded Skill rows remain. Deleting them could destroy or orphan evidence;
    # upgrading again validates their immutable definitions and reuses them.
