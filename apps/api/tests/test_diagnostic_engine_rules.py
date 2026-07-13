from uuid import uuid4

import pytest
from pydantic import ValidationError

from deutschos_api.diagnostic_engine.aggregation import aggregate_axis, aggregate_by_axis
from deutschos_api.diagnostic_engine.exceptions import (
    CandidateUnavailableError,
    InvalidTransitionError,
)
from deutschos_api.diagnostic_engine.schemas import (
    CORE_TEXT_AXES,
    AmbiguityRisk,
    AxisAggregate,
    CompletionReason,
    CoverageStatus,
    DeterministicRubric,
    EngineSessionState,
    EvaluationOutcome,
    EvidenceObservation,
    PresentedTaskObservation,
    ResponseSubmission,
    RubricStrategy,
    SelectionContext,
    SessionAction,
    SessionQuery,
    TaskAction,
    TaskCandidate,
)
from deutschos_api.diagnostic_engine.scoring import evaluate_response, evaluate_submission
from deutschos_api.diagnostic_engine.selector import decide_stop, select_next_task
from deutschos_api.diagnostic_engine.state_machine import (
    engine_state,
    session_transition,
    task_transition,
)
from deutschos_api.models import (
    DiagnosticAxis,
    DiagnosticBand,
    DiagnosticPolarity,
    DiagnosticSessionStatus,
    DiagnosticTaskStatus,
    DiagnosticTaskType,
)


def candidate(
    suffix: str,
    *,
    axis: DiagnosticAxis = DiagnosticAxis.ACTIVE_GRAMMAR,
    task_type: DiagnosticTaskType = DiagnosticTaskType.BINARY_CHOICE,
    difficulty: int = 1,
    equivalence_key: str | None = None,
    modality: str = "text",
    auto_evaluable: bool = True,
    ambiguity_risk: AmbiguityRisk = AmbiguityRisk.LOW,
    accepted_answers: list[str] | None = None,
    partial_answers: list[str] | None = None,
    strategy: RubricStrategy = RubricStrategy.EXACT_MATCH,
    skill_id: int | None = None,
) -> TaskCandidate:
    if axis == DiagnosticAxis.TYPED_COMMUNICATION_REPAIR:
        task_type = DiagnosticTaskType.TYPED_COMMUNICATION_REPAIR
    rubric = DeterministicRubric(
        strategy=strategy,
        accepted_answers=accepted_answers
        or (["ja"] if strategy != RubricStrategy.ORDERED_TOKENS else []),
        partial_answers=partial_answers or [],
        expected_tokens=["ich", "bin", "hier"] if strategy == RubricStrategy.ORDERED_TOKENS else [],
    )
    return TaskCandidate(
        candidate_id=f"candidate.{suffix}",
        version="v1",
        equivalence_key=equivalence_key or f"equivalence.{suffix}",
        axis=axis,
        task_type=task_type,
        difficulty=difficulty,
        modality=modality,
        content={"prompt": "Prueba determinista"},
        rubric=rubric,
        auto_evaluable=auto_evaluable,
        ambiguity_risk=ambiguity_risk,
        estimated_seconds=30,
        skill_id=skill_id,
    )


def observation(
    task_id: int,
    *,
    axis: DiagnosticAxis = DiagnosticAxis.ACTIVE_GRAMMAR,
    outcome: EvaluationOutcome = EvaluationOutcome.CORRECT_WITHOUT_HELP,
    difficulty: int = 1,
    task_type: DiagnosticTaskType = DiagnosticTaskType.BINARY_CHOICE,
    evaluator_confidence: float = 1.0,
    assistance: list[str] | None = None,
    attempt_number: int = 1,
    skill_id: int | None = None,
    skill_cefr_reference: str | None = None,
) -> EvidenceObservation:
    scores = {
        EvaluationOutcome.INCORRECT: 0.0,
        EvaluationOutcome.PARTIAL: 0.4,
        EvaluationOutcome.CORRECT_WITH_HELP: 0.7,
        EvaluationOutcome.CORRECT_WITHOUT_HELP: 1.0,
        EvaluationOutcome.NOT_EVALUABLE: None,
    }
    if outcome == EvaluationOutcome.NOT_EVALUABLE:
        polarity = DiagnosticPolarity.INSUFFICIENT
    elif outcome == EvaluationOutcome.INCORRECT:
        polarity = DiagnosticPolarity.NEGATIVE
    else:
        polarity = DiagnosticPolarity.POSITIVE
    return EvidenceObservation(
        task_id=task_id,
        candidate_id=f"candidate.observation-{task_id}",
        candidate_version="v1",
        axis=axis,
        task_type=task_type,
        difficulty=difficulty,
        attempt_number=attempt_number,
        outcome=outcome,
        score=scores[outcome],
        polarity=polarity,
        evaluator_confidence=evaluator_confidence,
        assistance=assistance or [],
        skill_id=skill_id,
        skill_cefr_reference=(skill_cefr_reference or "A0" if skill_id is not None else None),
    )


def presented(
    task_id: int,
    *,
    axis: DiagnosticAxis = DiagnosticAxis.ACTIVE_GRAMMAR,
    task_type: DiagnosticTaskType = DiagnosticTaskType.BINARY_CHOICE,
    difficulty: int = 1,
    candidate_id: str | None = None,
    equivalence_key: str | None = None,
    tiebreak: bool = False,
) -> PresentedTaskObservation:
    return PresentedTaskObservation(
        task_id=task_id,
        candidate_id=candidate_id or f"candidate.presented-{task_id}",
        candidate_version="v1",
        equivalence_key=equivalence_key or f"equivalence.presented-{task_id}",
        axis=axis,
        task_type=task_type,
        difficulty=difficulty,
        tiebreak=tiebreak,
    )


def submission(**overrides) -> ResponseSubmission:
    values = {
        "session_id": 1,
        "task_id": 1,
        "evaluation_id": uuid4(),
        "submission_id": uuid4(),
        "response_text": "ja",
        "response_language": "de",
        "instruction_state": "understood",
    }
    values.update(overrides)
    return ResponseSubmission.model_validate(values)


def context(
    *,
    evidence: list[EvidenceObservation] | None = None,
    presented_tasks: list[PresentedTaskObservation] | None = None,
    active_seconds: int = 0,
    target_task_count: int = 14,
) -> SelectionContext:
    return SelectionContext(
        evidence=evidence or [],
        presented=presented_tasks or [],
        active_seconds=active_seconds,
        target_task_count=target_task_count,
    )


def test_engine_schemas_reject_extra_fields_and_invalid_ranges():
    payload = candidate("strict").model_dump()
    payload["invented"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TaskCandidate.model_validate(payload)

    for invalid_difficulty in (0, 6):
        with pytest.raises(ValidationError):
            TaskCandidate.model_validate(
                {**candidate("range").model_dump(), "difficulty": invalid_difficulty}
            )

    with pytest.raises(ValidationError):
        EvidenceObservation.model_validate(
            {**observation(1).model_dump(), "evaluator_confidence": 1.01}
        )
    with pytest.raises(ValidationError):
        SelectionContext(active_seconds=-1)
    with pytest.raises(ValidationError):
        ResponseSubmission.model_validate({**submission().model_dump(), "unexpected": "value"})
    with pytest.raises(ValidationError):
        SessionQuery.model_validate({"session_id": 1, "unexpected": "value"})
    unassessed = aggregate_axis(DiagnosticAxis.READING_COMPREHENSION, []).model_dump()
    with pytest.raises(ValidationError, match="must equal evaluable evidence"):
        AxisAggregate.model_validate({**unassessed, "positive_evidence_count": 1})


def test_schema_forbids_text_claims_about_future_modalities_and_mismatched_repair_tasks():
    with pytest.raises(ValidationError, match="text candidates cannot claim"):
        candidate("fake-listening", axis=DiagnosticAxis.LISTENING_COMPREHENSION)

    repair = candidate("repair", axis=DiagnosticAxis.TYPED_COMMUNICATION_REPAIR)
    payload = repair.model_dump()
    payload["task_type"] = DiagnosticTaskType.GAP_FILL
    with pytest.raises(ValidationError, match="dedicated text task type"):
        TaskCandidate.model_validate(payload)


@pytest.mark.parametrize(
    ("persisted", "public"),
    [
        (DiagnosticSessionStatus.NOT_STARTED, EngineSessionState.CREATED),
        (DiagnosticSessionStatus.CALIBRATING, EngineSessionState.ACTIVE),
        (DiagnosticSessionStatus.PAUSED, EngineSessionState.PAUSED),
        (DiagnosticSessionStatus.COMPLETED, EngineSessionState.COMPLETED),
        (DiagnosticSessionStatus.CANCELLED, EngineSessionState.ABANDONED),
        (DiagnosticSessionStatus.ABANDONED, EngineSessionState.ABANDONED),
        (DiagnosticSessionStatus.ERROR, EngineSessionState.FAILED),
    ],
)
def test_public_session_state_mapping(persisted, public):
    assert engine_state(persisted) == public


def test_session_state_machine_accepts_the_complete_happy_path():
    current = DiagnosticSessionStatus.NOT_STARTED
    for action, expected in [
        (SessionAction.START, DiagnosticSessionStatus.ONBOARDING),
        (SessionAction.BEGIN_CALIBRATION, DiagnosticSessionStatus.CALIBRATING),
        (SessionAction.BEGIN_ASSESSMENT, DiagnosticSessionStatus.ASSESSING),
        (SessionAction.BEGIN_REVIEW, DiagnosticSessionStatus.REVIEWING),
        (SessionAction.BEGIN_COMPLETION, DiagnosticSessionStatus.COMPLETING),
        (SessionAction.COMPLETE, DiagnosticSessionStatus.COMPLETED),
    ]:
        transition = session_transition(current, action)
        assert transition.target == expected
        assert transition.changed is True
        current = transition.target


def test_pause_resume_and_reasonable_repeats_are_idempotent():
    paused = session_transition(DiagnosticSessionStatus.ASSESSING, SessionAction.PAUSE)
    assert paused.target == DiagnosticSessionStatus.PAUSED
    assert session_transition(paused.target, SessionAction.PAUSE).changed is False

    resumed = session_transition(
        paused.target,
        SessionAction.RESUME,
        paused_from=DiagnosticSessionStatus.ASSESSING,
    )
    assert resumed.target == DiagnosticSessionStatus.ASSESSING
    assert session_transition(resumed.target, SessionAction.RESUME).changed is False

    completed = session_transition(DiagnosticSessionStatus.COMPLETING, SessionAction.COMPLETE)
    assert session_transition(completed.target, SessionAction.COMPLETE).changed is False
    abandoned = session_transition(DiagnosticSessionStatus.ASSESSING, SessionAction.ABANDON)
    assert session_transition(abandoned.target, SessionAction.ABANDON).changed is False
    failed = session_transition(DiagnosticSessionStatus.ASSESSING, SessionAction.FAIL)
    assert session_transition(failed.target, SessionAction.FAIL).changed is False


def test_invalid_session_transitions_are_rejected():
    with pytest.raises(InvalidTransitionError):
        session_transition(DiagnosticSessionStatus.COMPLETED, SessionAction.RESUME)
    with pytest.raises(InvalidTransitionError):
        session_transition(DiagnosticSessionStatus.ABANDONED, SessionAction.START)
    with pytest.raises(InvalidTransitionError):
        session_transition(
            DiagnosticSessionStatus.PAUSED,
            SessionAction.RESUME,
            paused_from=DiagnosticSessionStatus.COMPLETED,
        )
    with pytest.raises(InvalidTransitionError):
        session_transition(DiagnosticSessionStatus.COMPLETING, SessionAction.PAUSE)


def test_task_state_machine_has_explicit_valid_idempotent_and_invalid_edges():
    assert (
        task_transition(DiagnosticTaskStatus.SELECTED, TaskAction.PRESENT).target
        == DiagnosticTaskStatus.PRESENTED
    )
    assert (
        task_transition(DiagnosticTaskStatus.PRESENTED, TaskAction.ANSWER).target
        == DiagnosticTaskStatus.ANSWERED
    )
    assert (
        task_transition(DiagnosticTaskStatus.ANSWERED, TaskAction.EVALUATE).target
        == DiagnosticTaskStatus.EVALUATED
    )
    assert task_transition(DiagnosticTaskStatus.EVALUATED, TaskAction.EVALUATE).changed is False
    assert (
        task_transition(DiagnosticTaskStatus.NOT_UNDERSTOOD, TaskAction.RETRY).target
        == DiagnosticTaskStatus.PRESENTED
    )
    with pytest.raises(InvalidTransitionError):
        task_transition(DiagnosticTaskStatus.SKIPPED, TaskAction.ANSWER)


def test_selection_is_reproducible_and_uses_stable_tiebreaks():
    candidates = [
        candidate("z-last", axis=DiagnosticAxis.READING_COMPREHENSION),
        candidate("a-first", axis=DiagnosticAxis.READING_COMPREHENSION),
    ]
    first = select_next_task(context(), candidates)
    second = select_next_task(context(), list(reversed(candidates)))
    assert first is not None and second is not None
    assert first.candidate.stable_key == second.candidate.stable_key
    assert first.candidate.candidate_id == "candidate.a-first"


def test_initial_selection_prioritises_core_coverage_in_fixed_axis_order():
    options = [
        candidate("optional", axis=DiagnosticAxis.EVERYDAY_FAMILIARITY),
        candidate("grammar", axis=DiagnosticAxis.ACTIVE_GRAMMAR),
        candidate("reading", axis=DiagnosticAxis.READING_COMPREHENSION),
    ]
    decision = select_next_task(context(), options)
    assert decision is not None
    assert decision.candidate.axis == DiagnosticAxis.READING_COMPREHENSION
    assert decision.reason == "core_coverage"


def test_uncovered_core_axis_precedes_depth_for_a_contradictory_axis():
    evidence = [
        observation(1, axis=DiagnosticAxis.ACTIVE_GRAMMAR),
        observation(
            2,
            axis=DiagnosticAxis.ACTIVE_GRAMMAR,
            outcome=EvaluationOutcome.INCORRECT,
        ),
    ]
    options = [
        candidate("grammar-tiebreak", axis=DiagnosticAxis.ACTIVE_GRAMMAR),
        candidate("reading-first", axis=DiagnosticAxis.READING_COMPREHENSION),
    ]
    decision = select_next_task(context(evidence=evidence), options)
    assert decision is not None
    assert decision.candidate.axis == DiagnosticAxis.READING_COMPREHENSION


def test_two_independent_unassisted_successes_raise_difficulty():
    evidence = [observation(1, difficulty=1), observation(2, difficulty=1)]
    options = [candidate("level-1", difficulty=1), candidate("level-2", difficulty=2)]
    decision = select_next_task(context(evidence=evidence), options)
    assert decision is not None
    assert decision.target_difficulty == 2
    assert decision.candidate.difficulty == 2


def test_clear_error_lowers_difficulty_but_not_evaluable_does_not():
    after_error = context(
        evidence=[observation(1, outcome=EvaluationOutcome.INCORRECT, difficulty=3)]
    )
    options = [candidate("level-2", difficulty=2), candidate("level-3", difficulty=3)]
    decision = select_next_task(after_error, options)
    assert decision is not None
    assert decision.target_difficulty == 2
    assert decision.candidate.difficulty == 2

    after_non_evaluable = context(
        evidence=[
            observation(1, difficulty=2),
            observation(
                2,
                outcome=EvaluationOutcome.NOT_EVALUABLE,
                difficulty=2,
                evaluator_confidence=0.0,
            ),
        ]
    )
    decision = select_next_task(after_non_evaluable, options)
    assert decision is not None
    assert decision.target_difficulty == 2


def test_help_never_raises_difficulty():
    evidence = [
        observation(1, difficulty=2),
        observation(
            2,
            outcome=EvaluationOutcome.CORRECT_WITH_HELP,
            difficulty=2,
            assistance=["lexical_hint"],
        ),
    ]
    decision = select_next_task(
        context(evidence=evidence),
        [candidate("level-2", difficulty=2), candidate("level-3", difficulty=3)],
    )
    assert decision is not None
    assert decision.target_difficulty == 2
    assert decision.candidate.difficulty == 2


def test_contradiction_gets_one_tiebreak_with_a_different_task_type():
    evidence = [
        observation(1, task_type=DiagnosticTaskType.BINARY_CHOICE),
        observation(
            2,
            outcome=EvaluationOutcome.INCORRECT,
            task_type=DiagnosticTaskType.BINARY_CHOICE,
        ),
    ]
    options = [
        candidate("same-format", task_type=DiagnosticTaskType.BINARY_CHOICE),
        candidate("new-format", task_type=DiagnosticTaskType.GAP_FILL),
    ]
    decision = select_next_task(context(evidence=evidence), options)
    assert decision is not None
    assert decision.tiebreak is True
    assert decision.reason == "contradiction_tiebreak"
    assert decision.candidate.task_type == DiagnosticTaskType.GAP_FILL

    already_tiebroken = context(
        evidence=evidence,
        presented_tasks=[presented(3, tiebreak=True)],
    )
    follow_up = select_next_task(already_tiebroken, [candidate("after-tiebreak")])
    assert follow_up is not None
    assert follow_up.tiebreak is False


def test_selector_never_repeats_identity_or_equivalent_candidate():
    existing = presented(
        1,
        candidate_id="candidate.used",
        equivalence_key="equivalence.used",
    )
    options = [
        candidate("used", equivalence_key="equivalence.other"),
        candidate("different-id", equivalence_key="equivalence.used"),
        candidate("fresh", equivalence_key="equivalence.fresh"),
    ]
    decision = select_next_task(context(presented_tasks=[existing]), options)
    assert decision is not None
    assert decision.candidate.candidate_id == "candidate.fresh"


def test_conflicting_duplicate_candidate_identity_is_rejected():
    original = candidate("duplicate", accepted_answers=["ja"])
    conflicting = original.model_copy(
        update={
            "content": {"prompt": "Contenido distinto con la misma identidad"},
        }
    )
    with pytest.raises(CandidateUnavailableError, match="Conflicting definitions"):
        select_next_task(context(), [original, conflicting])


def test_selector_excludes_future_modalities_and_limits_each_axis_to_five_tasks():
    listening = candidate(
        "listening",
        axis=DiagnosticAxis.LISTENING_COMPREHENSION,
        modality="audio",
    )
    five_grammar = [presented(index) for index in range(1, 6)]
    reading = candidate(
        "reading",
        axis=DiagnosticAxis.READING_COMPREHENSION,
        task_type=DiagnosticTaskType.GAP_FILL,
    )
    decision = select_next_task(
        context(presented_tasks=five_grammar),
        [listening, candidate("sixth-grammar"), reading],
    )
    assert decision is not None
    assert decision.candidate == reading
    assert select_next_task(context(), [listening]) is None


def test_selector_never_presents_a_third_consecutive_task_of_the_same_type():
    previous = [presented(1), presented(2)]
    options = [
        candidate("third-binary", axis=DiagnosticAxis.READING_COMPREHENSION),
        candidate(
            "different-type",
            axis=DiagnosticAxis.READING_COMPREHENSION,
            task_type=DiagnosticTaskType.GAP_FILL,
        ),
    ]
    decision = select_next_task(context(presented_tasks=previous), options)
    assert decision is not None
    assert decision.candidate.task_type == DiagnosticTaskType.GAP_FILL


def test_hard_stop_at_twenty_tasks_precedes_other_rules():
    decision = decide_stop(
        context(presented_tasks=[presented(index) for index in range(1, 21)]),
        candidates_available=True,
    )
    assert decision.should_stop is True
    assert decision.reason == CompletionReason.MAX_TASKS
    assert decision.partial is True


def test_hard_stop_at_twenty_five_active_minutes():
    decision = decide_stop(context(active_seconds=1500), candidates_available=True)
    assert decision.should_stop is True
    assert decision.reason == CompletionReason.TIME_LIMIT
    assert decision.partial is True


def test_candidate_exhaustion_stops_with_an_explicit_partial_result():
    decision = decide_stop(context(), candidates_available=False)
    assert decision.should_stop is True
    assert decision.reason == CompletionReason.NO_CANDIDATES
    assert decision.partial is True


def core_evidence(*, count_per_axis: int, varied_types: bool) -> list[EvidenceObservation]:
    rows: list[EvidenceObservation] = []
    task_id = 1
    for axis in CORE_TEXT_AXES:
        for index in range(count_per_axis):
            rows.append(
                observation(
                    task_id,
                    axis=axis,
                    difficulty=1 + (index % 2),
                    task_type=(
                        DiagnosticTaskType.GAP_FILL
                        if varied_types and index % 2
                        else DiagnosticTaskType.BINARY_CHOICE
                    ),
                )
            )
            task_id += 1
    return rows


def test_sufficient_coverage_and_confidence_can_finish_before_sixteen_tasks():
    evidence = core_evidence(count_per_axis=3, varied_types=True)
    tasks = [presented(item.task_id, axis=item.axis) for item in evidence]
    decision = decide_stop(
        context(evidence=evidence, presented_tasks=tasks, target_task_count=16),
        candidates_available=True,
    )
    assert decision.should_stop is True
    assert decision.reason == CompletionReason.COVERAGE_AND_CONFIDENCE
    assert decision.partial is False


def test_target_count_can_stop_with_coverage_but_only_moderate_certainty():
    evidence = core_evidence(count_per_axis=2, varied_types=False)
    tasks = [presented(item.task_id, axis=item.axis) for item in evidence]
    decision = decide_stop(
        context(evidence=evidence, presented_tasks=tasks, target_task_count=12),
        candidates_available=True,
    )
    assert decision.should_stop is True
    assert decision.reason == CompletionReason.TARGET_REACHED
    assert decision.partial is False


def test_empty_spanish_and_not_understood_responses_are_not_evaluable():
    task = candidate("score")
    cases = [
        (submission(response_text=""), "empty_response"),
        (
            submission(response_text="sí", response_language="es"),
            "spanish_response_requires_clarification",
        ),
        (
            submission(instruction_state="not_understood"),
            "instruction_not_understood",
        ),
    ]
    for response, reason in cases:
        result = evaluate_response(task, response)
        assert result.outcome == EvaluationOutcome.NOT_EVALUABLE
        assert result.score is None
        assert result.polarity == DiagnosticPolarity.INSUFFICIENT
        assert reason in result.reason_codes


def test_correct_answer_with_help_is_never_unassisted():
    result = evaluate_response(
        candidate("help"),
        submission(assistance=["lexical_hint"]),
    )
    assert result.outcome == EvaluationOutcome.CORRECT_WITH_HELP
    assert result.score == 0.7
    assert result.polarity == DiagnosticPolarity.POSITIVE


def test_out_of_topic_and_partially_communicative_responses_are_distinguished():
    task = candidate("structured")
    outside = evaluate_response(task, submission(response_text="nein", out_of_topic=True))
    assert outside.outcome == EvaluationOutcome.INCORRECT
    assert outside.score == 0.0
    assert outside.polarity == DiagnosticPolarity.NEGATIVE

    partial = evaluate_response(
        task,
        submission(response_text="etwas", partially_communicative=True),
    )
    assert partial.outcome == EvaluationOutcome.PARTIAL
    assert partial.score == 0.4
    assert partial.polarity == DiagnosticPolarity.POSITIVE


def test_deterministic_partial_key_and_second_empty_attempt_are_auditable():
    task = candidate("partial", partial_answers=["fast"])
    partial = evaluate_response(task, submission(response_text="fast"))
    assert partial.outcome == EvaluationOutcome.PARTIAL
    assert "deterministic_partial_match" in partial.reason_codes

    empty_retry = evaluate_submission(task, submission(response_text=""), attempt_number=2)
    assert empty_retry.outcome == EvaluationOutcome.NOT_EVALUABLE
    assert empty_retry.reason_codes == ["empty_response_retry_exhausted"]


def test_free_text_without_deterministic_rubric_remains_not_evaluable_without_ollama():
    task = candidate(
        "manual",
        strategy=RubricStrategy.MANUAL_ONLY,
        auto_evaluable=False,
        accepted_answers=[],
    )
    result = evaluate_response(task, submission(response_text="Ich heiße Jhon."))
    assert result.outcome == EvaluationOutcome.NOT_EVALUABLE
    assert "deterministic_rubric_unavailable" in result.reason_codes


def test_one_answer_cannot_create_a_strong_band_or_score_estimate():
    aggregate = aggregate_axis(DiagnosticAxis.ACTIVE_GRAMMAR, [observation(1)])
    assert aggregate.evidence_count == 1
    assert aggregate.estimated_score is None
    assert aggregate.band == DiagnosticBand.INSUFFICIENT_EVIDENCE
    assert aggregate.coverage_status == CoverageStatus.INSUFFICIENT


def test_help_reduces_estimated_score_without_becoming_no_evidence():
    unassisted = aggregate_axis(
        DiagnosticAxis.ACTIVE_GRAMMAR,
        [observation(1), observation(2, task_type=DiagnosticTaskType.GAP_FILL)],
    )
    helped = aggregate_axis(
        DiagnosticAxis.ACTIVE_GRAMMAR,
        [
            observation(
                3,
                outcome=EvaluationOutcome.CORRECT_WITH_HELP,
                assistance=["grammar_hint"],
            ),
            observation(
                4,
                outcome=EvaluationOutcome.CORRECT_WITH_HELP,
                assistance=["grammar_hint"],
                task_type=DiagnosticTaskType.GAP_FILL,
            ),
        ],
    )
    assert helped.evidence_count == 2
    assert helped.estimated_score is not None
    assert unassisted.estimated_score is not None
    assert helped.estimated_score < unassisted.estimated_score
    assert helped.band != DiagnosticBand.CONSISTENT_SAMPLE


def test_contradictory_evidence_reduces_confidence_and_blocks_strong_band():
    positive = [
        observation(
            index,
            difficulty=1 + (index % 2),
            task_type=(
                DiagnosticTaskType.GAP_FILL if index % 2 else DiagnosticTaskType.BINARY_CHOICE
            ),
        )
        for index in range(1, 5)
    ]
    contradictory = [
        *positive[:3],
        observation(
            4,
            outcome=EvaluationOutcome.INCORRECT,
            difficulty=2,
            task_type=DiagnosticTaskType.GAP_FILL,
        ),
    ]
    stable = aggregate_axis(DiagnosticAxis.ACTIVE_GRAMMAR, positive)
    mixed = aggregate_axis(DiagnosticAxis.ACTIVE_GRAMMAR, contradictory)
    assert mixed.estimate_confidence < stable.estimate_confidence
    assert mixed.positive_evidence_count == 3
    assert mixed.negative_evidence_count == 1
    assert mixed.band != DiagnosticBand.CONSISTENT_SAMPLE
    assert "contradictoria" in mixed.reason


def test_strong_internal_band_requires_multiple_formats_and_evidences():
    evidence = [
        observation(1, difficulty=1),
        observation(2, difficulty=2, task_type=DiagnosticTaskType.GAP_FILL),
        observation(3, difficulty=2),
        observation(4, difficulty=3, task_type=DiagnosticTaskType.GAP_FILL),
    ]
    aggregate = aggregate_axis(DiagnosticAxis.ACTIVE_GRAMMAR, evidence)
    assert aggregate.evidence_count == 4
    assert aggregate.maximum_demonstrated_difficulty == 3
    assert aggregate.band == DiagnosticBand.CONSISTENT_SAMPLE
    assert aggregate.coverage_status == CoverageStatus.SUFFICIENT


def test_cefr_hint_is_per_axis_and_requires_a_curriculum_skill_and_evidence():
    without_skill = aggregate_axis(
        DiagnosticAxis.ACTIVE_GRAMMAR,
        [observation(1), observation(2, task_type=DiagnosticTaskType.GAP_FILL, difficulty=2)],
    )
    with_skill = aggregate_axis(
        DiagnosticAxis.ACTIVE_GRAMMAR,
        [
            observation(3, skill_id=7),
            observation(
                4,
                task_type=DiagnosticTaskType.GAP_FILL,
                difficulty=2,
                skill_id=7,
            ),
        ],
    )
    assert without_skill.cefr_band is None
    assert with_skill.cefr_band == "pre-A1"
    assert with_skill.axis == DiagnosticAxis.ACTIVE_GRAMMAR


def test_a1_hint_requires_explicit_a1_curriculum_content_not_internal_difficulty():
    evidence = [
        observation(
            10,
            skill_id=9,
            skill_cefr_reference="A1",
            difficulty=1,
        ),
        observation(
            11,
            skill_id=9,
            skill_cefr_reference="A1",
            difficulty=1,
            task_type=DiagnosticTaskType.GAP_FILL,
        ),
        observation(
            12,
            skill_id=9,
            skill_cefr_reference="A1",
            difficulty=1,
        ),
    ]
    aggregate = aggregate_axis(DiagnosticAxis.ACTIVE_GRAMMAR, evidence)
    assert aggregate.cefr_band == "A1"

    pre_a1_content = [item.model_copy(update={"skill_cefr_reference": "A0"}) for item in evidence]
    assert aggregate_axis(DiagnosticAxis.ACTIVE_GRAMMAR, pre_a1_content).cefr_band == "pre-A1"


def test_future_modalities_remain_not_assessed_even_if_malformed_evidence_is_injected():
    aggregate = aggregate_axis(
        DiagnosticAxis.LISTENING_COMPREHENSION,
        [observation(1, axis=DiagnosticAxis.LISTENING_COMPREHENSION)],
    )
    assert aggregate.coverage_status == CoverageStatus.NOT_ASSESSED
    assert aggregate.evidence_count == 0
    assert aggregate.estimated_score is None
    assert aggregate.band is None
    assert aggregate.cefr_band is None


def test_typed_communication_repair_never_becomes_grammar_evidence():
    evidence = [
        observation(1, axis=DiagnosticAxis.TYPED_COMMUNICATION_REPAIR),
        observation(2, axis=DiagnosticAxis.ACTIVE_GRAMMAR),
    ]
    repair = aggregate_axis(DiagnosticAxis.TYPED_COMMUNICATION_REPAIR, evidence)
    grammar = aggregate_axis(DiagnosticAxis.ACTIVE_GRAMMAR, evidence)
    assert repair.evidence_count == 1
    assert grammar.evidence_count == 1
    assert repair.axis == DiagnosticAxis.TYPED_COMMUNICATION_REPAIR
    assert grammar.axis == DiagnosticAxis.ACTIVE_GRAMMAR


def test_latest_attempt_replaces_prior_attempt_without_inflating_independent_evidence():
    evidence = [
        observation(1, outcome=EvaluationOutcome.INCORRECT, attempt_number=1),
        observation(1, attempt_number=2),
        observation(2, task_type=DiagnosticTaskType.GAP_FILL),
    ]
    aggregate = aggregate_axis(DiagnosticAxis.ACTIVE_GRAMMAR, evidence)
    assert aggregate.evidence_count == 2
    assert aggregate.positive_evidence_count == 2
    assert aggregate.negative_evidence_count == 0


def test_complete_aggregate_keeps_every_unmeasured_axis_explicit():
    aggregates = aggregate_by_axis([observation(1)])
    by_axis = {item.axis: item for item in aggregates}
    assert by_axis[DiagnosticAxis.ACTIVE_GRAMMAR].evidence_count == 1
    assert by_axis[DiagnosticAxis.READING_COMPREHENSION].coverage_status == (
        CoverageStatus.NOT_ASSESSED
    )
    assert by_axis[DiagnosticAxis.ORAL_PRODUCTION].coverage_status == CoverageStatus.NOT_ASSESSED
