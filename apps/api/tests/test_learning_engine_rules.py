from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from llc_api.learning_engine.curriculum import (
    CURRICULUM,
    CURRICULUM_VERSION,
    InvalidCurriculumError,
    get_curriculum_skill,
    validate_curriculum,
)
from llc_api.learning_engine.planner import (
    PlanBlockKind,
    PlanIntensity,
    SkillProgress,
    build_daily_plan,
)
from llc_api.learning_engine.reviews import (
    ReviewCandidate,
    calculate_review_schedule,
    rank_due_reviews,
    review_interval_days,
)
from llc_api.learning_engine.scoring import (
    ENGINE_VERSION,
    AttemptOutcome,
    calculate_mastery_update,
    is_internally_mastered,
    score_for_outcome,
)

NOW = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)


def progress(
    skill_code: str,
    *,
    mastery: float = 0.5,
    confidence: float = 0.5,
    evidence: int = 5,
    next_review_at: datetime | None = None,
    streak: int = 0,
) -> SkillProgress:
    return SkillProgress(
        skill_code=skill_code,
        estimated_mastery=mastery,
        confidence=confidence,
        evidence_count=evidence,
        next_review_at=next_review_at,
        last_practised_at=NOW - timedelta(days=1),
        unassisted_streak=streak,
    )


def mastered(skill_code: str, **overrides) -> SkillProgress:
    values = {
        "mastery": 0.9,
        "confidence": 0.8,
        "evidence": 8,
        "streak": 2,
        "next_review_at": NOW + timedelta(days=1),
    }
    values.update(overrides)
    return progress(skill_code, **values)


def test_curriculum_is_a_versioned_ordered_dag_with_all_required_skills():
    assert CURRICULUM_VERSION == "a0-a1.v1"
    assert len(CURRICULUM) == 14
    assert [skill.curriculum_order for skill in CURRICULUM] == list(range(1, 15))
    assert {skill.cefr_reference for skill in CURRICULUM} == {"A0", "A1"}
    assert {skill.code for skill in CURRICULUM} == {
        "grammar.personal_pronouns",
        "grammar.sein_present",
        "grammar.haben_present",
        "grammar.present_regular",
        "grammar.questions_basic",
        "grammar.negation_basic",
        "grammar.articles_basic",
        "grammar.nominative_basic",
        "grammar.accusative_basic",
        "grammar.modal_verbs_basic",
        "vocabulary.everyday_core",
        "vocabulary.laboratory_intro",
        "listening.basic",
        "speaking.basic",
    }
    validate_curriculum(CURRICULUM)
    for skill in CURRICULUM:
        assert skill.description
        assert 1 <= skill.difficulty <= 5
        assert skill.exercise_types
        assert skill.curriculum_version == CURRICULUM_VERSION


def test_curriculum_validation_rejects_forward_prerequisite():
    first = replace(CURRICULUM[0], prerequisite_codes=(CURRICULUM[1].code,))
    with pytest.raises(InvalidCurriculumError, match="must occur earlier"):
        validate_curriculum((first, *CURRICULUM[1:]))


@pytest.mark.parametrize(
    ("outcome", "score"),
    [
        (AttemptOutcome.FAILURE, 0.0),
        (AttemptOutcome.PARTIAL, 0.4),
        (AttemptOutcome.CORRECT_WITH_HELP, 0.7),
        (AttemptOutcome.CORRECT_WITHOUT_HELP, 1.0),
    ],
)
def test_four_outcomes_have_code_owned_scores(outcome, score):
    assert score_for_outcome(outcome) == score


def test_mastery_is_recent_evidence_weighted_and_bounded():
    first = calculate_mastery_update(
        current_mastery=0,
        evidence_count=0,
        outcome=AttemptOutcome.CORRECT_WITH_HELP,
    )
    assert first.estimated_mastery == 0.7
    assert first.confidence == 0.1
    assert first.unassisted_streak == 0

    second = calculate_mastery_update(
        current_mastery=first.estimated_mastery,
        evidence_count=first.evidence_count,
        outcome=AttemptOutcome.CORRECT_WITHOUT_HELP,
        current_unassisted_streak=first.unassisted_streak,
    )
    assert second.estimated_mastery == 0.805
    assert second.confidence == 0.2
    assert second.unassisted_streak == 1

    lapse = calculate_mastery_update(
        current_mastery=second.estimated_mastery,
        evidence_count=second.evidence_count,
        outcome=AttemptOutcome.FAILURE,
        current_unassisted_streak=second.unassisted_streak,
    )
    assert lapse.estimated_mastery == 0.5232
    assert lapse.unassisted_streak == 0
    assert lapse.lapse_count == 1
    assert 0 <= lapse.estimated_mastery <= 1
    assert 0 <= lapse.confidence <= 1


def test_mastery_uses_each_skills_own_criteria():
    criteria = get_curriculum_skill("grammar.sein_present").mastery_criteria
    assert is_internally_mastered(
        estimated_mastery=criteria.min_mastery,
        confidence=criteria.min_confidence,
        evidence_count=criteria.min_evidence,
        unassisted_streak=criteria.unassisted_streak,
        criteria=criteria,
    )
    assert not is_internally_mastered(
        estimated_mastery=criteria.min_mastery - 0.0001,
        confidence=criteria.min_confidence,
        evidence_count=criteria.min_evidence,
        unassisted_streak=criteria.unassisted_streak,
        criteria=criteria,
    )


@pytest.mark.parametrize(
    ("outcome", "streak", "days"),
    [
        (AttemptOutcome.FAILURE, 0, 1),
        (AttemptOutcome.PARTIAL, 0, 2),
        (AttemptOutcome.CORRECT_WITH_HELP, 0, 3),
        (AttemptOutcome.CORRECT_WITHOUT_HELP, 1, 3),
        (AttemptOutcome.CORRECT_WITHOUT_HELP, 2, 7),
        (AttemptOutcome.CORRECT_WITHOUT_HELP, 3, 14),
        (AttemptOutcome.CORRECT_WITHOUT_HELP, 4, 30),
        (AttemptOutcome.CORRECT_WITHOUT_HELP, 5, 60),
        (AttemptOutcome.CORRECT_WITHOUT_HELP, 12, 60),
    ],
)
def test_review_schedule_distinguishes_all_outcomes_and_caps_unaided_growth(outcome, streak, days):
    assert review_interval_days(outcome, unassisted_streak=streak) == days
    schedule = calculate_review_schedule(
        outcome=outcome,
        observed_at=NOW,
        unassisted_streak=streak,
    )
    assert schedule.interval_days == days
    assert schedule.next_review_at == NOW + timedelta(days=days)


def test_due_reviews_are_ranked_by_explicit_stable_keys():
    candidates = [
        ReviewCandidate("later", NOW - timedelta(days=1), 0.2, 0.2, 2, 1),
        ReviewCandidate("old-high", NOW - timedelta(days=4), 0.8, 0.8, 8, 2),
        ReviewCandidate("old-low", NOW - timedelta(days=4), 0.3, 0.8, 8, 3),
        ReviewCandidate("future", NOW + timedelta(days=1), 0.1, 0.1, 1, 4),
    ]
    ranked = rank_due_reviews(candidates, as_of=NOW)
    assert [item.skill_code for item in ranked] == ["old-low", "old-high", "later"]
    assert [item.priority_rank for item in ranked] == [1, 2, 3]
    assert ranked[0].overdue_days == 4


def test_ten_minute_bootstrap_plan_is_complete_and_alternates_exercises():
    plan = build_daily_plan(
        available_minutes=10,
        motivation=1,
        generated_at=NOW,
        progress_by_code={},
    )
    assert plan.engine_version == ENGINE_VERSION
    assert plan.intensity is PlanIntensity.LOW
    assert plan.duration_minutes == 10
    assert len(plan.blocks) == 2
    assert [block.duration_minutes for block in plan.blocks] == [5, 5]
    assert plan.new_skill_code == "grammar.personal_pronouns"
    assert plan.primary_skill_code == plan.new_skill_code
    assert plan.blocks[0].kind is PlanBlockKind.NEW_SKILL
    assert plan.blocks[1].kind is PlanBlockKind.PRACTICE
    assert plan.blocks[0].exercise_type != plan.blocks[1].exercise_type
    assert "ten_minute_session_supported" in plan.reason_codes


def test_due_reviews_fill_blocks_before_any_new_skill():
    states = {
        "grammar.personal_pronouns": progress(
            "grammar.personal_pronouns", next_review_at=NOW - timedelta(days=1)
        ),
        "vocabulary.everyday_core": progress(
            "vocabulary.everyday_core", next_review_at=NOW - timedelta(days=3)
        ),
    }
    plan = build_daily_plan(
        available_minutes=20,
        motivation=5,
        generated_at=NOW,
        progress_by_code=states,
    )
    assert [block.kind for block in plan.blocks] == [PlanBlockKind.REVIEW, PlanBlockKind.REVIEW]
    assert plan.review_skill_codes == (
        "vocabulary.everyday_core",
        "grammar.personal_pronouns",
    )
    assert plan.primary_skill_code == "vocabulary.everyday_core"
    assert plan.new_skill_code is None
    assert "reviews_due_first" in plan.reason_codes


def test_low_motivation_suppresses_new_content_when_a_skill_is_active():
    states = {
        "grammar.personal_pronouns": mastered("grammar.personal_pronouns"),
        "vocabulary.everyday_core": mastered("vocabulary.everyday_core"),
    }
    plan = build_daily_plan(
        available_minutes=20,
        motivation=2,
        generated_at=NOW,
        progress_by_code=states,
    )
    assert plan.intensity is PlanIntensity.LOW
    assert plan.new_skill_code is None
    assert all(block.kind is PlanBlockKind.PRACTICE for block in plan.blocks)
    assert "low_motivation_reduced_intensity" in plan.reason_codes


def test_skill_is_not_introduced_until_prerequisite_meets_its_own_criteria():
    below_threshold = {
        "grammar.personal_pronouns": mastered("grammar.personal_pronouns", mastery=0.7999),
        "vocabulary.everyday_core": mastered("vocabulary.everyday_core"),
    }
    blocked = build_daily_plan(
        available_minutes=20,
        motivation=4,
        generated_at=NOW,
        progress_by_code=below_threshold,
    )
    assert blocked.new_skill_code != "grammar.sein_present"

    ready = dict(below_threshold)
    ready["grammar.personal_pronouns"] = mastered("grammar.personal_pronouns")
    unlocked = build_daily_plan(
        available_minutes=20,
        motivation=4,
        generated_at=NOW,
        progress_by_code=ready,
    )
    assert unlocked.new_skill_code == "grammar.sein_present"
    assert sum(block.kind is PlanBlockKind.NEW_SKILL for block in unlocked.blocks) == 1


def test_planner_prefers_listening_and_speaking_types_and_alternates():
    states = {
        "grammar.personal_pronouns": mastered("grammar.personal_pronouns"),
        "vocabulary.everyday_core": mastered(
            "vocabulary.everyday_core", next_review_at=NOW - timedelta(days=1)
        ),
    }
    plan = build_daily_plan(
        available_minutes=20,
        motivation=4,
        generated_at=NOW,
        progress_by_code=states,
    )
    assert plan.blocks[0].exercise_type == "listening_choice"
    assert plan.blocks[1].exercise_type == "guided_dialogue"
    assert plan.blocks[0].exercise_type != plan.blocks[1].exercise_type
    assert "listening_speaking_preferred" in plan.reason_codes


def test_planner_is_reproducible_for_identical_inputs():
    states = {"grammar.personal_pronouns": mastered("grammar.personal_pronouns")}
    kwargs = {
        "available_minutes": 25,
        "motivation": 3,
        "generated_at": NOW,
        "progress_by_code": states,
        "last_exercise_type": "recognition",
    }
    assert build_daily_plan(**kwargs) == build_daily_plan(**kwargs)


def test_rules_reject_naive_datetimes():
    with pytest.raises(ValueError, match="timezone"):
        calculate_review_schedule(
            outcome=AttemptOutcome.FAILURE,
            observed_at=datetime(2026, 7, 11, 9, 0),
            unassisted_streak=0,
        )
    with pytest.raises(ValueError, match="timezone"):
        build_daily_plan(
            available_minutes=10,
            motivation=3,
            generated_at=datetime(2026, 7, 11, 9, 0),
            progress_by_code={},
        )
