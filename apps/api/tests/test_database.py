import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from deutschos_api.learning_engine.curriculum import CURRICULUM, CURRICULUM_VERSION
from deutschos_api.models import ExerciseAttempt, LearningSession, Mistake

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_sqlite_foreign_keys_are_enabled(db_session_factory):
    with db_session_factory() as db:
        db.add(
            ExerciseAttempt(
                learning_session_id=9999,
                exercise_type="test",
                prompt="prompt",
                student_answer="answer",
                feedback="",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_exercise_score_constraint(db_session_factory, score):
    with db_session_factory() as db:
        session = LearningSession(session_type="test", summary="")
        db.add(session)
        db.flush()
        db.add(
            ExerciseAttempt(
                learning_session_id=session.id,
                exercise_type="test",
                prompt="prompt",
                student_answer="answer",
                score=score,
                feedback="",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_mistake_confidence_constraint(db_session_factory):
    with db_session_factory() as db:
        db.add(
            Mistake(
                original_text="x",
                corrected_text="y",
                explanation_es="explicación",
                category="grammar",
                severity="minor",
                confidence=1.01,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def run_alembic(database_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DEUTSCHOS_DATABASE_URL"] = f"sqlite:///{database_path}"
    environment["DEUTSCHOS_ALLOW_DESTRUCTIVE_DOWNGRADE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(PROJECT_ROOT / "apps/api/alembic.ini"),
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )


def test_migrations_reproduce_from_empty_database(tmp_path):
    database_path = tmp_path / "clean.sqlite3"
    run_alembic(database_path, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0005",)
        assert connection.execute("SELECT preferred_name FROM student_profiles").fetchone() == (
            "Jhon",
        )
        assert connection.execute("SELECT COUNT(*) FROM skills").fetchone() == (22,)
        assert connection.execute("SELECT COUNT(*) FROM curriculum_skills").fetchone() == (14,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE type = 'trigger' "
            "AND name LIKE 'skill_evidence_no_%'"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE type = 'trigger' "
            "AND name IN ('skill_evidence_require_snapshot', "
            "'skill_evidence_validate_correction')"
        ).fetchone() == (2,)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO learning_sessions "
            "(session_type, started_at, completed_at, duration_seconds, summary) "
            "VALUES ('test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, '')"
        )
        session_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute(
            "INSERT INTO exercise_attempts "
            "(learning_session_id, exercise_type, prompt, student_answer, feedback, created_at) "
            "VALUES (?, 'test', 'p', 'a', '', CURRENT_TIMESTAMP)",
            (session_id,),
        )
        attempt_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        skill_id = connection.execute(
            "SELECT id FROM skills WHERE code='grammar.foundation'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO skill_evidence "
            "(submission_id, exercise_attempt_id, skill_id, score, source, "
            "engine_version, created_at) VALUES (?, ?, ?, 0.5, 'manual_assessment', "
            "'test', CURRENT_TIMESTAMP)",
            ("00000000-0000-0000-0000-000000000001", attempt_id, skill_id),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE skill_evidence SET score=0.6")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM skill_evidence")
    run_alembic(database_path, "check")
    run_alembic(database_path, "downgrade", "base")
    run_alembic(database_path, "upgrade", "head")


def test_migrated_curriculum_matches_the_versioned_code_snapshot(tmp_path):
    database_path = tmp_path / "curriculum.sqlite3"
    run_alembic(database_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        curriculum = connection.execute(
            "SELECT version, cefr_from, cefr_to, is_active FROM curricula"
        ).fetchone()
        assert dict(curriculum) == {
            "version": CURRICULUM_VERSION,
            "cefr_from": "A0",
            "cefr_to": "A1",
            "is_active": 1,
        }
        rows = connection.execute(
            """
            SELECT s.id, s.code, s.name, s.category, s.description,
                   s.cefr_level AS skill_cefr_level,
                   cs.curriculum_order, cs.cefr_reference, cs.difficulty,
                   cs.min_mastery, cs.min_confidence, cs.min_evidence,
                   cs.unassisted_streak, cs.exercise_types
            FROM curriculum_skills AS cs
            JOIN skills AS s ON s.id = cs.skill_id
            WHERE cs.curriculum_version = ?
            ORDER BY cs.curriculum_order
            """,
            (CURRICULUM_VERSION,),
        ).fetchall()
        assert len(rows) == len(CURRICULUM) == 14

        for row, expected in zip(rows, CURRICULUM, strict=True):
            assert row["code"] == expected.code
            assert row["name"] == expected.name
            assert row["category"] == expected.category
            assert row["description"] == expected.description
            assert row["curriculum_order"] == expected.curriculum_order
            assert row["cefr_reference"] == expected.cefr_reference
            assert row["skill_cefr_level"] == expected.cefr_reference
            assert row["difficulty"] == expected.difficulty
            assert row["min_mastery"] == expected.mastery_criteria.min_mastery
            assert row["min_confidence"] == expected.mastery_criteria.min_confidence
            assert row["min_evidence"] == expected.mastery_criteria.min_evidence
            assert row["unassisted_streak"] == expected.mastery_criteria.unassisted_streak
            assert tuple(json.loads(row["exercise_types"])) == expected.exercise_types
            prerequisites = connection.execute(
                """
                SELECT prerequisite.code
                FROM skill_prerequisites AS dependency
                JOIN skills AS prerequisite
                  ON prerequisite.id = dependency.prerequisite_skill_id
                WHERE dependency.curriculum_version = ? AND dependency.skill_id = ?
                ORDER BY dependency.prerequisite_order
                """,
                (CURRICULUM_VERSION, row["id"]),
            ).fetchall()
            assert tuple(item[0] for item in prerequisites) == expected.prerequisite_codes

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO curricula "
                "(version, name, cefr_from, cefr_to, is_active, created_at) "
                "VALUES ('conflicting.v1', 'Conflict', 'A0', 'A1', 1, CURRENT_TIMESTAMP)"
            )


def test_upgrade_0004_preserves_legacy_evidence_and_adds_correction_guard(tmp_path):
    database_path = tmp_path / "legacy-evidence.sqlite3"
    run_alembic(database_path, "upgrade", "0003")
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO learning_sessions "
            "(session_type, started_at, completed_at, duration_seconds, summary) "
            "VALUES ('test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, 'legacy')"
        )
        session_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute(
            "INSERT INTO exercise_attempts "
            "(learning_session_id, exercise_type, prompt, student_answer, score, feedback, "
            "created_at) VALUES (?, 'test', 'prompt', 'answer', 0.8, '', CURRENT_TIMESTAMP)",
            (session_id,),
        )
        attempt_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        skill_id = connection.execute(
            "SELECT id FROM skills WHERE code = 'grammar.foundation'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO student_skills "
            "(skill_id, estimated_mastery, confidence, last_practised_at, next_review_at, "
            "evidence_count, updated_at) VALUES (?, 0.8, 0.1, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP)",
            (skill_id,),
        )
        connection.execute(
            "INSERT INTO skill_evidence "
            "(submission_id, exercise_attempt_id, skill_id, score, source, engine_version, "
            "created_at) VALUES (?, ?, ?, 0.8, 'manual_assessment', "
            "'learning-engine-v1', CURRENT_TIMESTAMP)",
            ("00000000-0000-0000-0000-000000000001", attempt_id, skill_id),
        )
        evidence_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit()

    run_alembic(database_path, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT score, source, engine_version, outcome, supersedes_evidence_id "
            "FROM skill_evidence WHERE id = ?",
            (evidence_id,),
        ).fetchone() == (0.8, "manual_assessment", "learning-engine-v1", None, None)
        assert connection.execute(
            "SELECT estimated_mastery, confidence, evidence_count, last_outcome, "
            "unassisted_streak, lapse_count FROM student_skills WHERE skill_id = ?",
            (skill_id,),
        ).fetchone() == (0.8, 0.1, 1, None, 0, 0)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE type = 'trigger' "
            "AND name LIKE 'skill_evidence_no_%'"
        ).fetchone() == (2,)

        correction_attempt_ids = []
        for _suffix in (2, 3):
            connection.execute(
                "INSERT INTO learning_sessions "
                "(session_type, started_at, completed_at, duration_seconds, summary) "
                "VALUES ('test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, 'correction')"
            )
            correction_session_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute(
                "INSERT INTO exercise_attempts "
                "(learning_session_id, exercise_type, prompt, student_answer, score, feedback, "
                "created_at) VALUES (?, 'test', 'prompt', 'answer', 0.4, '', CURRENT_TIMESTAMP)",
                (correction_session_id,),
            )
            correction_attempt_ids.append(
                connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            )

        with pytest.raises(sqlite3.IntegrityError, match="complete state snapshot"):
            connection.execute(
                "INSERT INTO skill_evidence "
                "(submission_id, exercise_attempt_id, skill_id, score, source, engine_version, "
                "outcome, supersedes_evidence_id, created_at) VALUES (?, ?, ?, 0.4, "
                "'manual_assessment', 'learning-engine-v2', 'partial', ?, CURRENT_TIMESTAMP)",
                (
                    "00000000-0000-0000-0000-000000000009",
                    correction_attempt_ids[0],
                    skill_id,
                    evidence_id,
                ),
            )

        correction_sql = (
            "INSERT INTO skill_evidence "
            "(submission_id, exercise_attempt_id, skill_id, score, source, engine_version, "
            "outcome, supersedes_evidence_id, mastery_before, mastery_after, "
            "confidence_before, confidence_after, evidence_count_before, evidence_count_after, "
            "unassisted_streak_before, unassisted_streak_after, lapse_count_before, "
            "lapse_count_after, next_review_at_after, created_at) VALUES (?, ?, ?, 0.4, "
            "'manual_assessment', 'learning-engine-v2', 'partial', ?, 0.8, 0.66, 0.1, 0.2, "
            "1, 1, 0, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        other_skill_id = connection.execute(
            "SELECT id FROM skills WHERE code = 'vocabulary.general'"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="same skill"):
            connection.execute(
                correction_sql,
                (
                    "00000000-0000-0000-0000-000000000008",
                    correction_attempt_ids[0],
                    other_skill_id,
                    evidence_id,
                ),
            )

        connection.execute(
            correction_sql,
            (
                "00000000-0000-0000-0000-000000000002",
                correction_attempt_ids[0],
                skill_id,
                evidence_id,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                correction_sql,
                (
                    "00000000-0000-0000-0000-000000000003",
                    correction_attempt_ids[1],
                    skill_id,
                    evidence_id,
                ),
            )


def test_upgrade_preserves_existing_milestone_zero_rows(tmp_path):
    database_path = tmp_path / "existing.sqlite3"
    run_alembic(database_path, "upgrade", "0001")
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO learning_sessions "
            "(session_type, started_at, completed_at, duration_seconds, summary) "
            "VALUES ('teacher_chat', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, 'safe')"
        )
        session_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute(
            "INSERT INTO exercise_attempts "
            "(learning_session_id, exercise_type, prompt, student_answer, feedback, created_at) "
            "VALUES (?, 'test', 'prompt', 'answer', '', CURRENT_TIMESTAMP)",
            (session_id,),
        )
        connection.commit()

    run_alembic(database_path, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM learning_sessions").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM exercise_attempts").fetchone() == (1,)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0005",)


def test_profile_persists_across_api_process_restarts(tmp_path):
    database_path = tmp_path / "persistent.sqlite3"
    run_alembic(database_path, "upgrade", "head")
    environment = os.environ.copy()
    environment["DEUTSCHOS_DATABASE_URL"] = f"sqlite:///{database_path}"
    update_code = """
import asyncio, httpx
from deutschos_api.main import app
async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        profile = (await client.get('/api/profile')).json()
        editable = {key: value for key, value in profile.items() if key not in {'id', 'created_at', 'updated_at'}}
        editable['preferred_name'] = 'Persistente'
        response = await client.put('/api/profile', json=editable)
        response.raise_for_status()
        print(response.json()['preferred_name'])
asyncio.run(main())
"""
    updated = subprocess.run(
        [sys.executable, "-c", update_code],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    assert updated.stdout.strip() == "Persistente"

    read_code = """
import asyncio, httpx
from deutschos_api.main import app
async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/profile')
        response.raise_for_status()
        print(response.json()['preferred_name'])
asyncio.run(main())
"""
    reloaded = subprocess.run(
        [sys.executable, "-c", read_code],
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    assert reloaded.stdout.strip() == "Persistente"


def test_hardening_migration_refuses_invalid_existing_values(tmp_path):
    database_path = tmp_path / "invalid.sqlite3"
    run_alembic(database_path, "upgrade", "0001")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO mistakes "
            "(original_text, corrected_text, explanation_es, category, severity, confidence, "
            "occurrence_count, first_seen_at, last_seen_at, status) "
            "VALUES ('x', 'y', 'z', 'grammar', 'minor', 2.5, 1, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP, 'new')"
        )
        connection.commit()

    with pytest.raises(subprocess.CalledProcessError) as error:
        run_alembic(database_path, "upgrade", "head")
    assert "No learning data was modified" in error.value.stderr
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0001",)
        assert connection.execute("SELECT confidence FROM mistakes").fetchone() == (2.5,)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE '_alembic_tmp_%'"
        ).fetchone() == (0,)


def test_default_database_path_is_independent_of_cwd(tmp_path):
    environment = os.environ.copy()
    environment.pop("DEUTSCHOS_DATABASE_URL", None)
    command = [
        sys.executable,
        "-c",
        "from deutschos_api.core.config import Settings; print(Settings().database_path)",
    ]
    from_root = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    from_elsewhere = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert from_root == from_elsewhere == str(PROJECT_ROOT / "data/deutschos.sqlite3")
