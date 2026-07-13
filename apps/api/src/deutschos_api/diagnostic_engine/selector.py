"""Reproducible adaptive selection and stopping rules."""

from __future__ import annotations

from collections.abc import Sequence

from deutschos_api.models import DiagnosticAxis, DiagnosticPolarity, DiagnosticTaskType

from .aggregation import aggregate_axis
from .exceptions import CandidateUnavailableError
from .schemas import (
    FUTURE_MODALITY_AXES,
    TEXT_AXES,
    CompletionReason,
    CoverageStatus,
    EvaluationOutcome,
    EvidenceObservation,
    SelectionContext,
    SelectionDecision,
    StopDecision,
    TaskCandidate,
)

MAX_TASKS_PER_AXIS = 5
MIN_EVALUABLE_FOR_EARLY_STOP = 10


def _validate_candidate_identities(candidates: Sequence[TaskCandidate]) -> None:
    definitions: dict[str, TaskCandidate] = {}
    for candidate in candidates:
        existing = definitions.get(candidate.stable_key)
        if existing is not None and existing != candidate:
            raise CandidateUnavailableError(
                f"Conflicting definitions for candidate {candidate.stable_key}."
            )
        definitions[candidate.stable_key] = candidate


def _evaluable(observation: EvidenceObservation) -> bool:
    return (
        observation.outcome != EvaluationOutcome.NOT_EVALUABLE
        and observation.score is not None
        and observation.evaluator_confidence >= 0.6
    )


def _axis_evidence(
    context: SelectionContext,
    axis: DiagnosticAxis,
) -> list[EvidenceObservation]:
    return [item for item in context.evidence if item.axis == axis]


def _axis_target_difficulty(
    context: SelectionContext,
    axis: DiagnosticAxis,
) -> int:
    valid = [item for item in _axis_evidence(context, axis) if _evaluable(item)]
    if not valid:
        return 1

    last = valid[-1]
    if last.outcome == EvaluationOutcome.INCORRECT:
        return max(1, last.difficulty - 1)
    if last.outcome in {
        EvaluationOutcome.PARTIAL,
        EvaluationOutcome.CORRECT_WITH_HELP,
    }:
        return last.difficulty

    # Raising difficulty requires two immediately concordant, independent
    # unassisted observations. Older successes never override a recent error.
    if len(valid) >= 2:
        previous = valid[-2]
        independent = (
            previous.candidate_id,
            previous.candidate_version,
        ) != (
            last.candidate_id,
            last.candidate_version,
        )
        if (
            independent
            and previous.outcome == EvaluationOutcome.CORRECT_WITHOUT_HELP
            and last.outcome == EvaluationOutcome.CORRECT_WITHOUT_HELP
            and abs(previous.difficulty - last.difficulty) <= 1
        ):
            return min(5, max(previous.difficulty, last.difficulty) + 1)
    return last.difficulty


def _contradictory_axes(context: SelectionContext) -> set[DiagnosticAxis]:
    already_tiebroken = {item.axis for item in context.presented if item.tiebreak}
    contradictory: set[DiagnosticAxis] = set()
    for axis in TEXT_AXES:
        if axis in already_tiebroken:
            continue
        evidence = [item for item in _axis_evidence(context, axis) if _evaluable(item)]
        has_positive = any(item.polarity == DiagnosticPolarity.POSITIVE for item in evidence)
        has_negative = any(item.polarity == DiagnosticPolarity.NEGATIVE for item in evidence)
        if has_positive and has_negative:
            contradictory.add(axis)
    return contradictory


def _prerequisites_met(
    candidate: TaskCandidate,
    context: SelectionContext,
) -> bool:
    if not candidate.prerequisite_candidate_ids:
        return True
    positive_candidates = {
        item.candidate_id
        for item in context.evidence
        if _evaluable(item) and item.polarity == DiagnosticPolarity.POSITIVE
    }
    return set(candidate.prerequisite_candidate_ids).issubset(positive_candidates)


def _eligible_candidates(
    context: SelectionContext,
    candidates: Sequence[TaskCandidate],
) -> list[TaskCandidate]:
    presented_keys = {item.stable_key for item in context.presented}
    presented_equivalences = {item.equivalence_key for item in context.presented}
    counts_by_axis = {
        axis: sum(item.axis == axis for item in context.presented) for axis in TEXT_AXES
    }
    eligible = [
        candidate
        for candidate in candidates
        if candidate.modality == "text"
        and candidate.auto_evaluable
        and candidate.axis in TEXT_AXES
        and candidate.axis not in FUTURE_MODALITY_AXES
        and candidate.stable_key not in presented_keys
        and candidate.equivalence_key not in presented_equivalences
        and counts_by_axis.get(candidate.axis, 0) < MAX_TASKS_PER_AXIS
        and _prerequisites_met(candidate, context)
        and (
            any(_evaluable(item) for item in _axis_evidence(context, candidate.axis))
            or candidate.difficulty <= 2
        )
    ]
    if len(context.presented) >= 2:
        repeated_type = context.presented[-1].task_type
        if context.presented[-2].task_type == repeated_type:
            return [item for item in eligible if item.task_type != repeated_type]
    return eligible


def _axis_priority(
    context: SelectionContext,
    axis: DiagnosticAxis,
    contradictory_axes: set[DiagnosticAxis],
) -> tuple[int, int, str]:
    core_order = {item: index for index, item in enumerate(context.core_axes)}
    aggregate = aggregate_axis(axis, context.evidence)
    if axis in core_order and aggregate.coverage_status == CoverageStatus.NOT_ASSESSED:
        tier = 0
    elif axis in contradictory_axes:
        tier = 1
    elif axis in core_order and aggregate.coverage_status == CoverageStatus.INSUFFICIENT:
        tier = 2
    elif axis in core_order and aggregate.coverage_status != CoverageStatus.SUFFICIENT:
        tier = 3
    elif axis not in core_order and aggregate.coverage_status == CoverageStatus.NOT_ASSESSED:
        tier = 4
    elif axis not in core_order and aggregate.coverage_status != CoverageStatus.SUFFICIENT:
        tier = 5
    else:
        tier = 6
    return tier, core_order.get(axis, len(core_order)), axis.value


def _repeated_type_penalty(
    context: SelectionContext,
    task_type: DiagnosticTaskType,
) -> int:
    if len(context.presented) < 2:
        return 0
    return int(all(item.task_type == task_type for item in context.presented[-2:]))


def select_next_task(
    context: SelectionContext,
    candidates: Sequence[TaskCandidate],
) -> SelectionDecision | None:
    """Choose one candidate using a complete, stable ordering and no randomness."""

    _validate_candidate_identities(candidates)
    eligible = _eligible_candidates(context, candidates)
    if not eligible:
        return None

    contradictory_axes = _contradictory_axes(context)

    def rank(candidate: TaskCandidate) -> tuple[object, ...]:
        target = _axis_target_difficulty(context, candidate.axis)
        ambiguity_order = {"low": 0, "medium": 1, "high": 2}
        prior_axis_types = {item.task_type for item in _axis_evidence(context, candidate.axis)}
        tiebreak_type_penalty = int(
            candidate.axis in contradictory_axes and candidate.task_type in prior_axis_types
        )
        return (
            *_axis_priority(context, candidate.axis, contradictory_axes),
            tiebreak_type_penalty,
            _repeated_type_penalty(context, candidate.task_type),
            abs(candidate.difficulty - target),
            candidate.difficulty,
            ambiguity_order[candidate.ambiguity_risk.value],
            candidate.task_type.value,
            candidate.candidate_id,
            candidate.version,
        )

    selected = min(eligible, key=rank)
    tiebreak = selected.axis in contradictory_axes
    target = _axis_target_difficulty(context, selected.axis)
    aggregate = aggregate_axis(selected.axis, context.evidence)
    if tiebreak:
        reason = "contradiction_tiebreak"
    elif aggregate.coverage_status == CoverageStatus.NOT_ASSESSED:
        reason = "core_coverage" if selected.axis in context.core_axes else "optional_coverage"
    elif aggregate.coverage_status == CoverageStatus.INSUFFICIENT:
        reason = "second_independent_sample"
    else:
        reason = "reduce_axis_uncertainty"
    return SelectionDecision(
        candidate=selected,
        reason=reason,
        target_difficulty=target,
        tiebreak=tiebreak,
    )


def select_candidate(
    candidates: Sequence[TaskCandidate],
    context: SelectionContext,
) -> SelectionDecision | None:
    """Service-facing alias with provider output as its first argument."""

    return select_next_task(context, candidates)


def decide_stop(
    context: SelectionContext,
    candidates_available: bool,
) -> StopDecision:
    """Apply hard limits before confidence-based or exhaustion-based stopping."""

    aggregates = [aggregate_axis(axis, context.evidence) for axis in context.core_axes]
    all_observed = bool(aggregates) and all(
        item.coverage_status in {CoverageStatus.OBSERVED, CoverageStatus.SUFFICIENT}
        for item in aggregates
    )
    completed_tiebreak_axes = {
        presented.axis
        for presented in context.presented
        if presented.tiebreak
        and any(
            observation.task_id == presented.task_id and _evaluable(observation)
            for observation in context.evidence
        )
    }
    unresolved_contradiction = any(
        item.positive_evidence_count > 0
        and item.negative_evidence_count > 0
        and item.axis not in completed_tiebreak_axes
        for item in aggregates
    )
    minimum_coverage = (
        all_observed
        and not unresolved_contradiction
        and all(item.evidence_count >= 2 and item.estimate_confidence >= 0.5 for item in aggregates)
    )
    evaluable_count = sum(_evaluable(item) for item in context.evidence)
    partial = not minimum_coverage

    if len(context.presented) >= context.max_task_count:
        return StopDecision(
            should_stop=True,
            reason=CompletionReason.MAX_TASKS,
            partial=partial,
            detail="Se alcanzó el máximo absoluto de 20 tareas presentadas.",
        )
    if context.active_seconds >= context.max_duration_seconds:
        return StopDecision(
            should_stop=True,
            reason=CompletionReason.TIME_LIMIT,
            partial=partial,
            detail="Se alcanzaron 25 minutos activos; se conserva un resultado parcial.",
        )
    if (
        evaluable_count >= MIN_EVALUABLE_FOR_EARLY_STOP
        and minimum_coverage
        and all(item.estimate_confidence >= 0.6 for item in aggregates)
    ):
        return StopDecision(
            should_stop=True,
            reason=CompletionReason.COVERAGE_AND_CONFIDENCE,
            partial=False,
            detail="Los ejes prioritarios tienen cobertura y confianza suficientes.",
        )
    if len(context.presented) >= context.target_task_count and minimum_coverage:
        return StopDecision(
            should_stop=True,
            reason=CompletionReason.TARGET_REACHED,
            partial=partial,
            detail="Se alcanzó el objetivo de tareas con muestras en todos los ejes prioritarios.",
        )
    if not candidates_available:
        return StopDecision(
            should_stop=True,
            reason=CompletionReason.NO_CANDIDATES,
            partial=partial,
            detail="No quedan candidatos válidos sin repetir tareas o modalidades.",
        )
    return StopDecision(
        should_stop=False,
        reason=CompletionReason.CONTINUE,
        partial=partial,
        detail="El diagnóstico puede seleccionar otra tarea determinista.",
    )


__all__ = [
    "MAX_TASKS_PER_AXIS",
    "MIN_EVALUABLE_FOR_EARLY_STOP",
    "decide_stop",
    "select_candidate",
    "select_next_task",
]
