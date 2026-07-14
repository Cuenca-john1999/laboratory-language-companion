"""Conservative, per-axis aggregation of diagnostic evidence."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from deutschos_api.models import (
    DiagnosticAxis,
    DiagnosticBand,
    DiagnosticConfidenceLabel,
    DiagnosticPolarity,
)

from .schemas import (
    FUTURE_MODALITY_AXES,
    TEXT_AXES,
    AxisAggregate,
    CoverageStatus,
    EvaluationOutcome,
    EvidenceObservation,
)

_MIN_EVALUATOR_CONFIDENCE = 0.6
_ASSISTED_SCORE_WEIGHT = 0.75
# Assistance lowers certainty about autonomous performance without changing the
# evaluator's certainty that the submitted answer was classified correctly.
# A 10% maximum discount keeps the bank's two difficulty-diverse assisted
# anchors above the v1 coverage threshold while remaining strictly below
# equivalent autonomous evidence.
_ASSISTED_CONFIDENCE_FACTOR = 0.9


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _has_help(observation: EvidenceObservation) -> bool:
    return any(item != "none" for item in observation.assistance)


def _weight(observation: EvidenceObservation) -> float:
    # Difficulty has a deliberately modest effect: it differentiates otherwise
    # equivalent evidence without allowing one difficult item to dominate.
    difficulty_weight = 0.8 + (0.05 * observation.difficulty)
    assistance_weight = _ASSISTED_SCORE_WEIGHT if _has_help(observation) else 1.0
    return observation.evaluator_confidence * difficulty_weight * assistance_weight


def _autonomy_factor(evidence: Sequence[EvidenceObservation]) -> float:
    return sum(_ASSISTED_CONFIDENCE_FACTOR if _has_help(item) else 1.0 for item in evidence) / len(
        evidence
    )


def _confidence(
    evidence: Sequence[EvidenceObservation],
    *,
    task_type_count: int,
    difficulty_count: int,
    contradictory: bool,
    insufficient_count: int,
) -> float:
    count = len(evidence)
    if count == 0:
        return 0.0
    base = {
        1: 0.25,
        2: 0.52,
        3: 0.66,
        4: 0.78,
    }.get(count, min(0.9, 0.78 + (0.04 * (count - 4))))
    diversity_bonus = 0.08 if task_type_count >= 2 else 0.0
    difficulty_bonus = 0.04 if difficulty_count >= 2 else 0.0
    evaluator_factor = 0.75 + (0.25 * sum(item.evaluator_confidence for item in evidence) / count)
    autonomy_factor = _autonomy_factor(evidence)
    contradiction_penalty = 0.24 if contradictory else 0.0
    insufficient_penalty = min(0.12, 0.03 * insufficient_count)
    return round(
        _clamp(
            ((base + diversity_bonus + difficulty_bonus) * evaluator_factor * autonomy_factor)
            - contradiction_penalty
            - insufficient_penalty,
            upper=0.95,
        ),
        6,
    )


def _confidence_label(confidence: float) -> DiagnosticConfidenceLabel:
    if confidence >= 0.8:
        return DiagnosticConfidenceLabel.HIGH
    if confidence >= 0.6:
        return DiagnosticConfidenceLabel.MODERATE
    return DiagnosticConfidenceLabel.LOW


def _empty_aggregate(axis: DiagnosticAxis, *, future_modality: bool) -> AxisAggregate:
    reason = (
        "La modalidad todavía no puede evaluarse en el diagnóstico de texto."
        if future_modality
        else "No se presentó evidencia para este eje."
    )
    return AxisAggregate(
        axis=axis,
        evidence_count=0,
        positive_evidence_count=0,
        negative_evidence_count=0,
        insufficient_evidence_count=0,
        maximum_demonstrated_difficulty=None,
        estimated_score=None,
        estimate_confidence=0.0,
        band=None,
        cefr_band=None,
        confidence_label=DiagnosticConfidenceLabel.LOW,
        reason=reason,
        coverage_status=CoverageStatus.NOT_ASSESSED,
        task_types=[],
        difficulty_min=None,
        difficulty_max=None,
        skill_id=None,
    )


def _observed_skill_id(evidence: Sequence[EvidenceObservation]) -> int | None:
    values = {item.skill_id for item in evidence}
    if len(values) != 1 or None in values:
        return None
    return next(iter(values))


def _observed_cefr_reference(
    evidence: Sequence[EvidenceObservation],
) -> str | None:
    values = {item.skill_cefr_reference for item in evidence}
    if len(values) != 1 or None in values:
        return None
    return next(iter(values))


def _latest_attempt_per_task(
    observations: Sequence[EvidenceObservation],
) -> list[EvidenceObservation]:
    """Keep one effective observation per independent presented task."""

    latest: dict[int, tuple[int, int, EvidenceObservation]] = {}
    for stable_order, item in enumerate(observations):
        order = (item.attempt_number, stable_order)
        current = latest.get(item.task_id)
        if current is None or order > current[:2]:
            latest[item.task_id] = (*order, item)
    return [latest[task_id][2] for task_id in sorted(latest)]


def _band_for(
    *,
    estimated_score: float,
    confidence: float,
    evidence_count: int,
    positive_count: int,
    negative_count: int,
    task_type_count: int,
    maximum_demonstrated_difficulty: int | None,
) -> DiagnosticBand:
    if (
        estimated_score >= 0.8
        and confidence >= 0.75
        and evidence_count >= 4
        and positive_count >= 3
        and negative_count == 0
        and task_type_count >= 2
        and (maximum_demonstrated_difficulty or 0) >= 2
    ):
        return DiagnosticBand.CONSISTENT_SAMPLE
    if estimated_score >= 0.65:
        return DiagnosticBand.FUNCTIONAL_GUIDED
    if estimated_score >= 0.4:
        return DiagnosticBand.DEVELOPING
    return DiagnosticBand.INITIAL_BASIS


def aggregate_axis(
    axis: DiagnosticAxis,
    observations: Sequence[EvidenceObservation],
) -> AxisAggregate:
    """Aggregate one axis without converting absence into negative evidence."""

    axis_observations = [item for item in observations if item.axis == axis]
    if axis in FUTURE_MODALITY_AXES:
        # Defensive even if malformed observations are injected: text never
        # becomes evidence of listening, speech, pronunciation, or oral fluency.
        return _empty_aggregate(axis, future_modality=True)
    if not axis_observations:
        return _empty_aggregate(axis, future_modality=False)

    effective_attempts = _latest_attempt_per_task(axis_observations)
    evidence = [
        item
        for item in effective_attempts
        if item.outcome != EvaluationOutcome.NOT_EVALUABLE
        and item.score is not None
        and item.evaluator_confidence >= _MIN_EVALUATOR_CONFIDENCE
    ]
    insufficient_count = sum(
        item.outcome == EvaluationOutcome.NOT_EVALUABLE
        or item.score is None
        or item.evaluator_confidence < _MIN_EVALUATOR_CONFIDENCE
        for item in effective_attempts
    )
    positive_count = sum(item.polarity == DiagnosticPolarity.POSITIVE for item in evidence)
    negative_count = sum(item.polarity == DiagnosticPolarity.NEGATIVE for item in evidence)
    contradictory = positive_count > 0 and negative_count > 0
    task_types = sorted({item.task_type for item in evidence}, key=lambda item: item.value)
    difficulties = sorted({item.difficulty for item in evidence})
    confidence = _confidence(
        evidence,
        task_type_count=len(task_types),
        difficulty_count=len(difficulties),
        contradictory=contradictory,
        insufficient_count=insufficient_count,
    )

    demonstrated = [
        item.difficulty
        for item in evidence
        if item.outcome
        in {
            EvaluationOutcome.CORRECT_WITH_HELP,
            EvaluationOutcome.CORRECT_WITHOUT_HELP,
        }
    ]
    maximum_demonstrated = max(demonstrated, default=None)

    if len(evidence) < 2 or confidence < 0.4:
        estimated_score = None
        band = DiagnosticBand.INSUFFICIENT_EVIDENCE
    else:
        weights = [_weight(item) for item in evidence]
        weight_total = sum(weights)
        estimated_score = round(
            sum(float(item.score) * weight for item, weight in zip(evidence, weights, strict=True))
            / weight_total,
            6,
        )
        band = _band_for(
            estimated_score=estimated_score,
            confidence=confidence,
            evidence_count=len(evidence),
            positive_count=positive_count,
            negative_count=negative_count,
            task_type_count=len(task_types),
            maximum_demonstrated_difficulty=maximum_demonstrated,
        )

    if len(evidence) < 2:
        coverage = CoverageStatus.INSUFFICIENT
    elif len(evidence) >= 3 and len(task_types) >= 2 and confidence >= 0.6 and not contradictory:
        coverage = CoverageStatus.SUFFICIENT
    else:
        coverage = CoverageStatus.OBSERVED

    # Keep the persistence dimension even when every attempt is insufficient.
    # Filtering first would collapse a skill-bound not_evaluable sample into
    # ``skill_id=None`` and could leave a different result chain active.
    skill_id = _observed_skill_id(effective_attempts)
    skill_cefr_reference = _observed_cefr_reference(effective_attempts)
    cefr_band: str | None = None
    unassisted_positive_count = sum(
        item.outcome == EvaluationOutcome.CORRECT_WITHOUT_HELP for item in evidence
    )
    if (
        skill_id is not None
        and skill_cefr_reference is not None
        and positive_count >= 2
        and confidence >= 0.6
        and estimated_score is not None
        and maximum_demonstrated is not None
    ):
        cefr_band = (
            "A1"
            if skill_cefr_reference == "A1"
            and positive_count >= 3
            and unassisted_positive_count >= 2
            and confidence >= 0.7
            and estimated_score >= 0.65
            else "pre-A1"
        )

    reason_parts = [f"{len(evidence)} evidencias evaluables"]
    if insufficient_count:
        reason_parts.append(f"{insufficient_count} insuficientes")
    assisted_count = sum(_has_help(item) for item in evidence)
    if assisted_count:
        reason_parts.append(f"{assisted_count} con ayuda; confianza de autonomía reducida")
    if contradictory:
        reason_parts.append("evidencia contradictoria; confianza reducida")
    elif len(task_types) < 2:
        reason_parts.append("variedad de formatos limitada")
    else:
        reason_parts.append("formatos variados")

    return AxisAggregate(
        axis=axis,
        evidence_count=len(evidence),
        positive_evidence_count=positive_count,
        negative_evidence_count=negative_count,
        insufficient_evidence_count=insufficient_count,
        maximum_demonstrated_difficulty=maximum_demonstrated,
        estimated_score=estimated_score,
        estimate_confidence=confidence,
        band=band,
        cefr_band=cefr_band,
        confidence_label=_confidence_label(confidence),
        reason="; ".join(reason_parts) + ".",
        coverage_status=coverage,
        task_types=task_types,
        difficulty_min=min(difficulties, default=None),
        difficulty_max=max(difficulties, default=None),
        skill_id=skill_id,
    )


def aggregate_axes(
    axes: Iterable[DiagnosticAxis],
    observations: Sequence[EvidenceObservation],
) -> list[AxisAggregate]:
    """Return explicit read models for requested axes, in caller-defined order."""

    return [aggregate_axis(axis, observations) for axis in axes]


def aggregate_by_axis(
    evidence: Sequence[EvidenceObservation],
    axes: Iterable[DiagnosticAxis] = (*TEXT_AXES, *FUTURE_MODALITY_AXES),
) -> list[AxisAggregate]:
    """Aggregate for services while keeping absent modalities explicit.

    A ``not_assessed`` value carries no score or band. Persistence code can
    therefore omit it while state/report readers still state missing evidence.
    """

    return aggregate_axes(axes, evidence)


def aggregate_by_axis_and_skill(
    evidence: Sequence[EvidenceObservation],
    axes: Iterable[DiagnosticAxis] = (*TEXT_AXES, *FUTURE_MODALITY_AXES),
) -> list[AxisAggregate]:
    """Build stable persistence dimensions without mixing curriculum skills.

    Selection and stopping deliberately reason over the complete diagnostic
    axis. Persisted results additionally preserve an optional curriculum-skill
    dimension, so a later observation for another skill cannot leave the
    earlier skill result active but stale. Unassessed axes still appear exactly
    once and future modalities remain explicitly unassessed.
    """

    observations = list(evidence)
    aggregates: list[AxisAggregate] = []
    for axis in axes:
        axis_observations = [item for item in observations if item.axis == axis]
        if axis in FUTURE_MODALITY_AXES or not axis_observations:
            aggregates.append(aggregate_axis(axis, axis_observations))
            continue

        skill_dimensions = sorted(
            {item.skill_id for item in axis_observations},
            key=lambda skill_id: (skill_id is not None, skill_id or 0),
        )
        for skill_id in skill_dimensions:
            aggregates.append(
                aggregate_axis(
                    axis,
                    [item for item in axis_observations if item.skill_id == skill_id],
                )
            )
    return aggregates


__all__ = [
    "aggregate_axes",
    "aggregate_axis",
    "aggregate_by_axis",
    "aggregate_by_axis_and_skill",
]
