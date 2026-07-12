import json
import os

os.environ["DEUTSCHOS_DATABASE_URL"] = "sqlite://"

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from deutschos_api.db.base import Base
from deutschos_api.db.session import get_db, make_engine
from deutschos_api.learning_engine.curriculum import CURRICULUM, CURRICULUM_VERSION
from deutschos_api.main import app
from deutschos_api.models import (
    Curriculum,
    CurriculumSkill,
    Skill,
    SkillPrerequisite,
    StudentProfile,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _seed_database(factory):
    with factory() as db:
        db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        db.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0004')"))
        db.add(
            StudentProfile(
                id=1,
                preferred_name="Jhon",
                native_language="español",
                additional_languages="[]",
                current_location="Alemania",
                professional_background="Laboratorio",
                learning_goals='["Aprender alemán"]',
                interests="[]",
                learning_preferences="{}",
            )
        )
        db.add(
            Curriculum(
                version=CURRICULUM_VERSION,
                name="DeutschOS A0 → A1",
                cefr_from="A0",
                cefr_to="A1",
                is_active=True,
            )
        )
        skill_ids: dict[str, int] = {}
        for definition in CURRICULUM:
            skill = Skill(
                code=definition.code,
                name=definition.name,
                category=definition.category,
                cefr_level=definition.cefr_reference,
                description=definition.description,
            )
            db.add(skill)
            db.flush()
            skill_ids[definition.code] = skill.id
            db.add(
                CurriculumSkill(
                    curriculum_version=CURRICULUM_VERSION,
                    skill_id=skill.id,
                    curriculum_order=definition.curriculum_order,
                    cefr_reference=definition.cefr_reference,
                    difficulty=definition.difficulty,
                    min_mastery=definition.mastery_criteria.min_mastery,
                    min_confidence=definition.mastery_criteria.min_confidence,
                    min_evidence=definition.mastery_criteria.min_evidence,
                    unassisted_streak=definition.mastery_criteria.unassisted_streak,
                    exercise_types=json.dumps(list(definition.exercise_types)),
                )
            )
        db.flush()
        for definition in CURRICULUM:
            for order, prerequisite in enumerate(definition.prerequisite_codes, start=1):
                db.add(
                    SkillPrerequisite(
                        curriculum_version=CURRICULUM_VERSION,
                        skill_id=skill_ids[definition.code],
                        prerequisite_skill_id=skill_ids[prerequisite],
                        prerequisite_order=order,
                    )
                )
        db.commit()


@pytest.fixture
def db_session_factory():
    engine = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    _seed_database(factory)
    yield factory
    engine.dispose()


@pytest.fixture
def file_db_session_factory(tmp_path):
    database_path = tmp_path / "learning-engine-concurrency.sqlite3"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    _seed_database(factory)
    yield factory
    engine.dispose()


@pytest.fixture
async def client(db_session_factory):
    def override_db():
        with db_session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()
