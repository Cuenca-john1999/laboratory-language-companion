"""Editorial reachability analysis driven by the runtime diagnostic selector.

The analyzer is deliberately pure: it consumes an already validated in-memory
bank, builds the existing engine DTOs, and calls the production selector and
stopping functions. It never writes content, sessions, or SQLite rows.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field, JsonValue, model_validator

from deutschos_api.content.diagnostic import (
    DiagnosticTaskBank,
    EditorialStatus,
)
from deutschos_api.diagnostic_engine.aggregation import aggregate_axis
from deutschos_api.diagnostic_engine.schemas import (
    CORE_TEXT_AXES,
    CompletionReason,
    CoverageStatus,
    EvaluationOutcome,
    EvidenceObservation,
    PresentedTaskObservation,
    SelectionContext,
    TaskCandidate,
)
from deutschos_api.diagnostic_engine.selector import (
    MAX_TASKS_PER_AXIS,
    MIN_EVALUABLE_FOR_EARLY_STOP,
    decide_stop,
    select_next_task,
)
from deutschos_api.models import DiagnosticAxis, DiagnosticPolarity, DiagnosticTaskType
from deutschos_api.schemas.base import APIModel

MIN_REVIEW_TASKS = 12
MIN_AXIS_EVIDENCE = 2
DEFAULT_EXPLORATION_STATE_LIMIT = 200_000


class ReachabilitySeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ReachabilityIssueCode(StrEnum):
    MISSING_PRIORITY_AXIS = "missing_priority_axis"
    AXIS_WITHOUT_ENTRY_CANDIDATE = "axis_without_entry_candidate"
    INSUFFICIENT_AXIS_CANDIDATES = "insufficient_axis_candidates"
    SINGLE_CANDIDATE_AXIS = "single_candidate_axis"
    DIFFICULTY_PATH_UNREACHABLE = "difficulty_path_unreachable"
    DIFFICULTY_GAP = "difficulty_gap"
    PREREQUISITE_MISSING = "prerequisite_missing"
    PREREQUISITE_CYCLE = "prerequisite_cycle"
    EQUIVALENCE_REDUCES_COVERAGE = "equivalence_reduces_coverage"
    AXIS_EXCEEDS_RUNTIME_CAP = "axis_exceeds_runtime_cap"
    AXIS_CAP_REDUCES_GLOBAL_CAPACITY = "axis_cap_reduces_global_capacity"
    INSUFFICIENT_TASK_TYPE_DIVERSITY = "insufficient_task_type_diversity"
    TASK_NEVER_SELECTABLE = "task_never_selectable"
    INSUFFICIENT_REACHABLE_TASKS = "insufficient_reachable_tasks"
    INSUFFICIENT_AXIS_COVERAGE = "insufficient_axis_coverage"
    SCENARIO_ENDS_NO_CANDIDATES = "scenario_ends_no_candidates"
    CONSECUTIVE_TYPE_DEAD_END = "consecutive_type_dead_end"
    EXPLORATION_INCONCLUSIVE = "exploration_inconclusive"


class ReachabilityIssue(APIModel):
    code: ReachabilityIssueCode
    severity: ReachabilitySeverity
    bank_id: str = Field(min_length=2, max_length=100)
    file: str = Field(min_length=1, max_length=200)
    task_id: str | None = Field(default=None, max_length=100)
    axis: DiagnosticAxis | None = None
    message: str = Field(min_length=1, max_length=1000)
    technical_details: dict[str, JsonValue] = Field(default_factory=dict)
    recommendation: str = Field(min_length=1, max_length=1000)


class ScenarioStep(APIModel):
    sequence: int = Field(ge=1, le=20)
    task_id: str = Field(min_length=1, max_length=100)
    axis: DiagnosticAxis
    difficulty: int = Field(ge=1, le=5)
    task_type: DiagnosticTaskType
    outcome: EvaluationOutcome
    tiebreak: bool


class ScenarioAxisCoverage(APIModel):
    axis: DiagnosticAxis
    evidence_count: int = Field(ge=0, le=20)
    insufficient_evidence_count: int = Field(ge=0, le=20)
    coverage_status: CoverageStatus
    estimate_confidence: float = Field(ge=0, le=1)


class ScenarioReport(APIModel):
    scenario_id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    steps: list[ScenarioStep] = Field(max_length=20)
    evaluable_evidence_count: int = Field(ge=0, le=20)
    coverage: list[ScenarioAxisCoverage]
    terminal_reason: str = Field(min_length=1, max_length=100)
    partial: bool
    reached_minimum_evidence: bool
    ended_no_candidates: bool
    never_selected_tasks: list[str]


class AxisReachability(APIModel):
    axis: DiagnosticAxis
    candidate_count: int = Field(ge=0)
    entry_candidate_count: int = Field(ge=0)
    independent_candidate_count: int = Field(ge=0)
    reachable_task_count: int = Field(ge=0)
    maximum_evaluable_evidence_observed: int = Field(ge=0, le=20)
    coverage_possible: bool


class TaskUnreachability(APIModel):
    task_id: str = Field(min_length=1, max_length=100)
    axis: DiagnosticAxis
    reasons: list[str] = Field(min_length=1)


class ExplorationReport(APIModel):
    conclusive: bool
    state_limit: int = Field(ge=1)
    states_explored: int = Field(ge=0)
    memoized_states: int = Field(ge=0)
    frontier_states: int = Field(ge=0)
    depth_limit: int = Field(ge=1, le=20)
    minimum_terminal_tasks: int | None = Field(default=None, ge=0, le=20)
    maximum_terminal_tasks: int | None = Field(default=None, ge=0, le=20)
    maximum_evaluable_evidence: int = Field(ge=0, le=20)
    reachable_tasks: list[str]
    terminal_reasons: dict[str, int]
    maximum_evidence_by_axis: dict[str, int]

    @model_validator(mode="after")
    def terminal_bounds_are_ordered(self):
        if (
            self.minimum_terminal_tasks is not None
            and self.maximum_terminal_tasks is not None
            and self.minimum_terminal_tasks > self.maximum_terminal_tasks
        ):
            raise ValueError("minimum terminal tasks cannot exceed maximum")
        return self


class DiagnosticReachabilityReport(APIModel):
    ready_for_review: bool
    bank_id: str = Field(min_length=2, max_length=100)
    bank_version: str = Field(min_length=1, max_length=50)
    manifest_file: str = Field(min_length=1, max_length=200)
    editorial_status: EditorialStatus
    priority_axes: list[DiagnosticAxis]
    present_axes: list[DiagnosticAxis]
    missing_candidate_axes: list[DiagnosticAxis]
    axes_without_entry_candidate: list[DiagnosticAxis]
    total_task_count: int = Field(ge=0)
    potentially_reachable_task_count: int = Field(ge=0)
    minimum_observed_tasks_per_trajectory: int | None = Field(default=None, ge=0, le=20)
    maximum_observed_tasks_per_trajectory: int | None = Field(default=None, ge=0, le=20)
    maximum_evaluable_evidence_observed: int = Field(ge=0, le=20)
    reachable_tasks: list[str]
    permanently_unreachable_tasks: list[str]
    task_unreachability: list[TaskUnreachability]
    scenarios: list[ScenarioReport]
    terminal_reasons: dict[str, int]
    axis_coverage: list[AxisReachability]
    problems: list[ReachabilityIssue]
    warnings: list[ReachabilityIssue]
    editorial_information: list[str]
    exploration: ExplorationReport


_SCENARIO_LABELS = {
    "all_correct_without_help": "Todas correctas sin ayuda",
    "all_incorrect": "Todas incorrectas",
    "all_partial": "Todas parciales",
    "all_correct_with_help": "Todas correctas con ayuda",
    "alternating_correct_incorrect": "Alternancia correcta/incorrecta",
    "alternating_correct_partial": "Alternancia correcta/parcial",
    "one_not_evaluable_per_axis": "Una no evaluable por eje",
    "all_not_evaluable": "Todas no evaluables",
    "controlled_contradictions": "Contradicciones controladas",
    "early_abandonment": "Abandono temprano",
}

_BLOCKING_SCENARIOS = frozenset(
    {
        "all_correct_without_help",
        "all_incorrect",
        "all_partial",
        "all_correct_with_help",
        "alternating_correct_incorrect",
        "alternating_correct_partial",
        "controlled_contradictions",
    }
)

_EXPLORATION_OUTCOMES = (
    EvaluationOutcome.CORRECT_WITHOUT_HELP,
    EvaluationOutcome.INCORRECT,
    EvaluationOutcome.PARTIAL,
    EvaluationOutcome.CORRECT_WITH_HELP,
    EvaluationOutcome.NOT_EVALUABLE,
)


def _blocking_severity(status: EditorialStatus) -> ReachabilitySeverity:
    return (
        ReachabilitySeverity.ERROR
        if status in {EditorialStatus.REVIEWED, EditorialStatus.PRODUCTION}
        else ReachabilitySeverity.WARNING
    )


def _issue(
    bank: DiagnosticTaskBank,
    code: ReachabilityIssueCode,
    message: str,
    recommendation: str,
    *,
    task_id: str | None = None,
    axis: DiagnosticAxis | None = None,
    details: dict[str, JsonValue] | None = None,
    severity: ReachabilitySeverity | None = None,
) -> ReachabilityIssue:
    return ReachabilityIssue(
        code=code,
        severity=severity or _blocking_severity(bank.manifest.editorial_status),
        bank_id=bank.manifest.bank_id,
        file=bank.manifest_file,
        task_id=task_id,
        axis=axis,
        message=message,
        technical_details=details or {},
        recommendation=recommendation,
    )


def _empty_context() -> SelectionContext:
    return SelectionContext(
        presented=[],
        evidence=[],
        active_seconds=0,
        target_task_count=MIN_REVIEW_TASKS,
    )


def _is_evaluable(item: EvidenceObservation) -> bool:
    return (
        item.outcome != EvaluationOutcome.NOT_EVALUABLE
        and item.score is not None
        and item.evaluator_confidence >= 0.6
    )


def _evidence_for(
    candidate: TaskCandidate,
    *,
    task_id: int,
    outcome: EvaluationOutcome,
) -> EvidenceObservation:
    score, polarity, confidence, assistance = {
        EvaluationOutcome.CORRECT_WITHOUT_HELP: (
            1.0,
            DiagnosticPolarity.POSITIVE,
            1.0,
            [],
        ),
        EvaluationOutcome.INCORRECT: (0.0, DiagnosticPolarity.NEGATIVE, 1.0, []),
        EvaluationOutcome.PARTIAL: (0.4, DiagnosticPolarity.POSITIVE, 1.0, []),
        EvaluationOutcome.CORRECT_WITH_HELP: (
            0.7,
            DiagnosticPolarity.POSITIVE,
            1.0,
            ["lexical_hint"],
        ),
        EvaluationOutcome.NOT_EVALUABLE: (
            None,
            DiagnosticPolarity.INSUFFICIENT,
            0.0,
            [],
        ),
    }[outcome]
    return EvidenceObservation(
        task_id=task_id,
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.version,
        axis=candidate.axis,
        task_type=candidate.task_type,
        difficulty=candidate.difficulty,
        attempt_number=1,
        outcome=outcome,
        score=score,
        polarity=polarity,
        evaluator_confidence=confidence,
        assistance=assistance,
        skill_id=candidate.skill_id,
    )


def _advance_context(
    context: SelectionContext,
    candidate: TaskCandidate,
    *,
    outcome: EvaluationOutcome,
    tiebreak: bool,
) -> SelectionContext:
    task_id = len(context.presented) + 1
    presented = PresentedTaskObservation(
        task_id=task_id,
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.version,
        equivalence_key=candidate.equivalence_key,
        axis=candidate.axis,
        task_type=candidate.task_type,
        difficulty=candidate.difficulty,
        tiebreak=tiebreak,
    )
    evidence = _evidence_for(candidate, task_id=task_id, outcome=outcome)
    return context.model_copy(
        update={
            "presented": [*context.presented, presented],
            "evidence": [*context.evidence, evidence],
        }
    )


def _scenario_outcome(
    scenario_id: str,
    candidate: TaskCandidate,
    sequence: int,
    seen_not_evaluable_axes: set[DiagnosticAxis],
) -> EvaluationOutcome:
    if scenario_id == "all_correct_without_help":
        return EvaluationOutcome.CORRECT_WITHOUT_HELP
    if scenario_id == "all_incorrect":
        return EvaluationOutcome.INCORRECT
    if scenario_id == "all_partial":
        return EvaluationOutcome.PARTIAL
    if scenario_id == "all_correct_with_help":
        return EvaluationOutcome.CORRECT_WITH_HELP
    if scenario_id in {"alternating_correct_incorrect", "controlled_contradictions"}:
        return (
            EvaluationOutcome.CORRECT_WITHOUT_HELP if sequence % 2 else EvaluationOutcome.INCORRECT
        )
    if scenario_id == "alternating_correct_partial":
        return EvaluationOutcome.CORRECT_WITHOUT_HELP if sequence % 2 else EvaluationOutcome.PARTIAL
    if scenario_id == "one_not_evaluable_per_axis":
        if candidate.axis not in seen_not_evaluable_axes:
            seen_not_evaluable_axes.add(candidate.axis)
            return EvaluationOutcome.NOT_EVALUABLE
        return EvaluationOutcome.CORRECT_WITHOUT_HELP
    if scenario_id == "all_not_evaluable":
        return EvaluationOutcome.NOT_EVALUABLE
    return EvaluationOutcome.CORRECT_WITHOUT_HELP


def _scenario_report(
    scenario_id: str,
    candidates: Sequence[TaskCandidate],
) -> ScenarioReport:
    context = _empty_context()
    steps: list[ScenarioStep] = []
    seen_not_evaluable_axes: set[DiagnosticAxis] = set()
    terminal_reason = CompletionReason.CONTINUE.value
    partial = True

    while True:
        if scenario_id == "early_abandonment" and len(steps) >= 2:
            terminal_reason = "abandoned"
            partial = True
            break
        decision = select_next_task(context, candidates)
        stop = decide_stop(context, candidates_available=decision is not None)
        if stop.should_stop:
            terminal_reason = stop.reason.value
            partial = stop.partial
            break
        if decision is None:  # Defensive: decide_stop must convert this to no_candidates.
            terminal_reason = CompletionReason.NO_CANDIDATES.value
            partial = True
            break
        sequence = len(steps) + 1
        outcome = _scenario_outcome(
            scenario_id,
            decision.candidate,
            sequence,
            seen_not_evaluable_axes,
        )
        steps.append(
            ScenarioStep(
                sequence=sequence,
                task_id=decision.candidate.candidate_id,
                axis=decision.candidate.axis,
                difficulty=decision.candidate.difficulty,
                task_type=decision.candidate.task_type,
                outcome=outcome,
                tiebreak=decision.tiebreak,
            )
        )
        context = _advance_context(
            context,
            decision.candidate,
            outcome=outcome,
            tiebreak=decision.tiebreak,
        )

    coverage = [
        ScenarioAxisCoverage(
            axis=aggregate.axis,
            evidence_count=aggregate.evidence_count,
            insufficient_evidence_count=aggregate.insufficient_evidence_count,
            coverage_status=aggregate.coverage_status,
            estimate_confidence=aggregate.estimate_confidence,
        )
        for aggregate in (aggregate_axis(axis, context.evidence) for axis in CORE_TEXT_AXES)
    ]
    selected_ids = {step.task_id for step in steps}
    evaluable_count = sum(_is_evaluable(item) for item in context.evidence)
    return ScenarioReport(
        scenario_id=scenario_id,
        label=_SCENARIO_LABELS[scenario_id],
        steps=steps,
        evaluable_evidence_count=evaluable_count,
        coverage=coverage,
        terminal_reason=terminal_reason,
        partial=partial,
        reached_minimum_evidence=evaluable_count >= MIN_EVALUABLE_FOR_EARLY_STOP,
        ended_no_candidates=terminal_reason == CompletionReason.NO_CANDIDATES.value,
        never_selected_tasks=sorted(
            candidate.candidate_id
            for candidate in candidates
            if candidate.candidate_id not in selected_ids
        ),
    )


def _scenario_context(
    scenario: ScenarioReport,
    candidates: Sequence[TaskCandidate],
) -> SelectionContext:
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    context = _empty_context()
    for step in scenario.steps:
        context = _advance_context(
            context,
            by_id[step.task_id],
            outcome=step.outcome,
            tiebreak=step.tiebreak,
        )
    return context


def _consecutive_type_is_the_blocker(
    scenario: ScenarioReport,
    candidates: Sequence[TaskCandidate],
) -> bool:
    if len(scenario.steps) < 2:
        return False
    repeated_type = scenario.steps[-1].task_type
    if scenario.steps[-2].task_type != repeated_type:
        return False
    context = _scenario_context(scenario, candidates)
    replacement = next(item for item in DiagnosticTaskType if item != repeated_type)
    altered_penultimate = context.presented[-2].model_copy(update={"task_type": replacement})
    altered = context.model_copy(
        update={
            "presented": [
                *context.presented[:-2],
                altered_penultimate,
                context.presented[-1],
            ]
        }
    )
    decision = select_next_task(altered, candidates)
    return decision is not None and decision.candidate.task_type == repeated_type


def _canonical_outcome(outcome: EvaluationOutcome) -> str:
    if outcome in {EvaluationOutcome.PARTIAL, EvaluationOutcome.CORRECT_WITH_HELP}:
        # These outcomes are separate in scenarios and scoring, but the current
        # selector treats both as positive evidence that maintains difficulty.
        return "positive_maintain"
    return outcome.value


def _state_signature(
    context: SelectionContext,
    candidates: Sequence[TaskCandidate],
) -> tuple[object, ...]:
    """Return only state that can affect the next selection or stop decision."""

    presented_keys = {item.stable_key for item in context.presented}
    presented_equivalences = {item.equivalence_key for item in context.presented}
    prerequisite_targets = {
        prerequisite
        for candidate in candidates
        for prerequisite in candidate.prerequisite_candidate_ids
    }
    positive_prerequisites = tuple(
        sorted(
            item.candidate_id
            for item in context.evidence
            if item.candidate_id in prerequisite_targets
            and _is_evaluable(item)
            and item.polarity == DiagnosticPolarity.POSITIVE
        )
    )
    presented_signature = tuple(
        (
            item.candidate_id,
            item.candidate_version,
            item.equivalence_key,
            item.axis.value,
            item.task_type.value,
            item.difficulty,
            item.tiebreak,
        )
        for item in context.presented
    )
    completed_tiebreak_tasks = {
        item.task_id
        for item in context.presented
        if item.tiebreak
        and any(
            evidence.task_id == item.task_id and _is_evaluable(evidence)
            for evidence in context.evidence
        )
    }
    open_axis_signatures: list[tuple[object, ...]] = []
    saturated_axis_count = 0
    saturated_evaluable_count = 0
    saturated_minimum_coverage = True
    saturated_confidence_six = True
    for axis in context.core_axes:
        axis_candidates = [candidate for candidate in candidates if candidate.axis == axis]
        presented_for_axis = [item for item in context.presented if item.axis == axis]
        saturated = len(presented_for_axis) >= MAX_TASKS_PER_AXIS or all(
            candidate.stable_key in presented_keys
            or candidate.equivalence_key in presented_equivalences
            for candidate in axis_candidates
        )
        aggregate = aggregate_axis(axis, context.evidence)
        evaluable = [item for item in context.evidence if item.axis == axis and _is_evaluable(item)]
        completed_tiebreak = any(
            item.task_id in completed_tiebreak_tasks for item in presented_for_axis
        )
        if saturated:
            saturated_axis_count += 1
            saturated_evaluable_count += aggregate.evidence_count
            unresolved_contradiction = (
                aggregate.positive_evidence_count > 0
                and aggregate.negative_evidence_count > 0
                and not completed_tiebreak
            )
            saturated_minimum_coverage = saturated_minimum_coverage and (
                aggregate.coverage_status in {CoverageStatus.OBSERVED, CoverageStatus.SUFFICIENT}
                and aggregate.evidence_count >= MIN_AXIS_EVIDENCE
                and aggregate.estimate_confidence >= 0.5
                and not unresolved_contradiction
            )
            saturated_confidence_six = (
                saturated_confidence_six and aggregate.estimate_confidence >= 0.6
            )
            continue
        last_two = tuple(
            (
                item.candidate_id,
                item.candidate_version,
                item.difficulty,
                _canonical_outcome(item.outcome),
            )
            for item in evaluable[-2:]
        )
        open_axis_signatures.append(
            (
                axis.value,
                "open",
                aggregate.evidence_count,
                aggregate.positive_evidence_count,
                aggregate.negative_evidence_count,
                aggregate.insufficient_evidence_count,
                aggregate.coverage_status.value,
                aggregate.estimate_confidence,
                tuple(sorted(item.value for item in aggregate.task_types)),
                tuple(sorted({item.difficulty for item in evaluable})),
                last_two,
                completed_tiebreak,
            )
        )
    return (
        presented_signature,
        positive_prerequisites,
        (
            saturated_axis_count,
            saturated_evaluable_count,
            saturated_minimum_coverage,
            saturated_confidence_six,
        ),
        tuple(open_axis_signatures),
        context.target_task_count,
        context.max_task_count,
        context.max_duration_seconds,
    )


def _explore(
    candidates: Sequence[TaskCandidate],
    *,
    state_limit: int,
) -> ExplorationReport:
    if state_limit < 1:
        raise ValueError("state_limit must be positive")
    initial = _empty_context()
    queue = deque([initial])
    visited = {_state_signature(initial, candidates)}
    reachable: set[str] = set()
    terminal_lengths: list[int] = []
    terminal_reasons: Counter[str] = Counter()
    maximum_evaluable = 0
    maximum_evidence_by_axis = {axis.value: 0 for axis in CORE_TEXT_AXES}
    states_explored = 0

    while queue and states_explored < state_limit:
        context = queue.popleft()
        states_explored += 1
        maximum_evaluable = max(
            maximum_evaluable,
            sum(_is_evaluable(item) for item in context.evidence),
        )
        for axis in CORE_TEXT_AXES:
            maximum_evidence_by_axis[axis.value] = max(
                maximum_evidence_by_axis[axis.value],
                aggregate_axis(axis, context.evidence).evidence_count,
            )

        decision = select_next_task(context, candidates)
        stop = decide_stop(context, candidates_available=decision is not None)
        if stop.should_stop:
            terminal_lengths.append(len(context.presented))
            terminal_reasons[stop.reason.value] += 1
            continue
        if decision is None:  # Defensive consistency with decide_stop.
            terminal_lengths.append(len(context.presented))
            terminal_reasons[CompletionReason.NO_CANDIDATES.value] += 1
            continue

        candidate = decision.candidate
        reachable.add(candidate.candidate_id)
        for outcome in _EXPLORATION_OUTCOMES:
            child = _advance_context(
                context,
                candidate,
                outcome=outcome,
                tiebreak=decision.tiebreak,
            )
            signature = _state_signature(child, candidates)
            if signature in visited:
                continue
            visited.add(signature)
            queue.append(child)

    conclusive = not queue
    return ExplorationReport(
        conclusive=conclusive,
        state_limit=state_limit,
        states_explored=states_explored,
        memoized_states=len(visited),
        frontier_states=len(queue),
        depth_limit=initial.max_task_count,
        minimum_terminal_tasks=min(terminal_lengths, default=None),
        maximum_terminal_tasks=max(terminal_lengths, default=None),
        maximum_evaluable_evidence=maximum_evaluable,
        reachable_tasks=sorted(reachable),
        terminal_reasons=dict(sorted(terminal_reasons.items())),
        maximum_evidence_by_axis=maximum_evidence_by_axis,
    )


def _prerequisite_cycles(candidates: Sequence[TaskCandidate]) -> set[str]:
    graph = {
        candidate.candidate_id: tuple(candidate.prerequisite_candidate_ids)
        for candidate in candidates
    }
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()
    cycles: set[str] = set()

    def visit(candidate_id: str) -> None:
        if candidate_id in active_set:
            cycles.update(active[active.index(candidate_id) :])
            return
        if candidate_id in visited or candidate_id not in graph:
            return
        active.append(candidate_id)
        active_set.add(candidate_id)
        for prerequisite in graph[candidate_id]:
            visit(prerequisite)
        active.pop()
        active_set.remove(candidate_id)
        visited.add(candidate_id)

    for candidate_id in sorted(graph):
        visit(candidate_id)
    return cycles


def _entry_candidates(
    axis: DiagnosticAxis,
    candidates: Sequence[TaskCandidate],
) -> list[TaskCandidate]:
    return [
        candidate
        for candidate in candidates
        if candidate.axis == axis and select_next_task(_empty_context(), [candidate]) is not None
    ]


def _unreachability_reasons(
    candidate: TaskCandidate,
    *,
    candidates: Sequence[TaskCandidate],
    reachable_ids: set[str],
    entry_axes: set[DiagnosticAxis],
    cycle_ids: set[str],
) -> list[str]:
    known_ids = {item.candidate_id for item in candidates}
    reasons: list[str] = []
    if set(candidate.prerequisite_candidate_ids) - known_ids:
        reasons.append(ReachabilityIssueCode.PREREQUISITE_MISSING.value)
    if candidate.candidate_id in cycle_ids:
        reasons.append(ReachabilityIssueCode.PREREQUISITE_CYCLE.value)
    if candidate.axis not in entry_axes:
        reasons.append(ReachabilityIssueCode.AXIS_WITHOUT_ENTRY_CANDIDATE.value)
    if candidate.difficulty >= 3:
        reasons.append(ReachabilityIssueCode.DIFFICULTY_PATH_UNREACHABLE.value)
    equivalent_reachable = any(
        item.candidate_id in reachable_ids and item.equivalence_key == candidate.equivalence_key
        for item in candidates
    )
    if equivalent_reachable:
        reasons.append(ReachabilityIssueCode.EQUIVALENCE_REDUCES_COVERAGE.value)
    if sum(item.axis == candidate.axis for item in candidates) > MAX_TASKS_PER_AXIS:
        reasons.append(ReachabilityIssueCode.AXIS_EXCEEDS_RUNTIME_CAP.value)
    if not reasons:
        reasons.append("selector_order_or_combined_constraints")
    return sorted(set(reasons))


def _append_unique(items: list[ReachabilityIssue], issue: ReachabilityIssue) -> None:
    identity = (issue.code, issue.task_id, issue.axis)
    if all((item.code, item.task_id, item.axis) != identity for item in items):
        items.append(issue)


def analyze_diagnostic_bank(
    bank: DiagnosticTaskBank,
    *,
    max_exploration_states: int = DEFAULT_EXPLORATION_STATE_LIMIT,
) -> DiagnosticReachabilityReport:
    """Analyze one validated bank without persistence or runtime side effects."""

    candidates = tuple(
        sorted((task.to_candidate() for task in bank.tasks), key=lambda item: item.stable_key)
    )
    present_axes = sorted({candidate.axis for candidate in candidates}, key=lambda item: item.value)
    missing_axes = [axis for axis in CORE_TEXT_AXES if axis not in present_axes]
    entries_by_axis = {axis: _entry_candidates(axis, candidates) for axis in CORE_TEXT_AXES}
    axes_without_entry = [axis for axis in CORE_TEXT_AXES if not entries_by_axis[axis]]
    entry_axes = {axis for axis, entries in entries_by_axis.items() if entries}
    problems: list[ReachabilityIssue] = []
    warnings: list[ReachabilityIssue] = []
    known_ids = {candidate.candidate_id for candidate in candidates}
    cycle_ids = _prerequisite_cycles(candidates)

    for axis in missing_axes:
        _append_unique(
            problems,
            _issue(
                bank,
                ReachabilityIssueCode.MISSING_PRIORITY_AXIS,
                f"El eje prioritario {axis.value} no tiene candidatos.",
                "Añadir al menos dos tareas independientes y una entrada de dificultad 1 o 2.",
                axis=axis,
            ),
        )
    for axis in axes_without_entry:
        _append_unique(
            problems,
            _issue(
                bank,
                ReachabilityIssueCode.AXIS_WITHOUT_ENTRY_CANDIDATE,
                f"El eje {axis.value} no puede abrirse desde una sesión vacía.",
                "Añadir una tarea textual autoevaluable de dificultad 1 o 2 sin prerrequisitos bloqueantes.",
                axis=axis,
                details={
                    "candidate_difficulties": sorted(
                        candidate.difficulty for candidate in candidates if candidate.axis == axis
                    )
                },
            ),
        )

    for candidate in candidates:
        missing_prerequisites = sorted(set(candidate.prerequisite_candidate_ids) - known_ids)
        if missing_prerequisites:
            _append_unique(
                problems,
                _issue(
                    bank,
                    ReachabilityIssueCode.PREREQUISITE_MISSING,
                    f"La tarea {candidate.candidate_id} tiene prerrequisitos inexistentes.",
                    "Declarar los prerrequisitos dentro del mismo banco o retirarlos.",
                    task_id=candidate.candidate_id,
                    axis=candidate.axis,
                    details={"missing_prerequisites": missing_prerequisites},
                ),
            )
        if candidate.candidate_id in cycle_ids:
            _append_unique(
                problems,
                _issue(
                    bank,
                    ReachabilityIssueCode.PREREQUISITE_CYCLE,
                    f"La tarea {candidate.candidate_id} participa en un ciclo de prerrequisitos.",
                    "Romper el ciclo y conservar una ruta de entrada acíclica.",
                    task_id=candidate.candidate_id,
                    axis=candidate.axis,
                ),
            )

    static_capacity = 0
    for axis in CORE_TEXT_AXES:
        axis_candidates = [candidate for candidate in candidates if candidate.axis == axis]
        independent_count = len({candidate.equivalence_key for candidate in axis_candidates})
        static_capacity += min(independent_count, MAX_TASKS_PER_AXIS)
        if independent_count < MIN_AXIS_EVIDENCE:
            _append_unique(
                problems,
                _issue(
                    bank,
                    ReachabilityIssueCode.INSUFFICIENT_AXIS_CANDIDATES,
                    f"El eje {axis.value} no puede aportar dos evidencias independientes.",
                    "Crear al menos dos grupos de equivalencia realmente independientes.",
                    axis=axis,
                    details={"independent_candidates": independent_count},
                ),
            )
        if len(axis_candidates) == 1:
            _append_unique(
                problems,
                _issue(
                    bank,
                    ReachabilityIssueCode.SINGLE_CANDIDATE_AXIS,
                    f"El eje {axis.value} depende de una única tarea.",
                    "Añadir una segunda muestra independiente antes de revisión.",
                    axis=axis,
                    task_id=axis_candidates[0].candidate_id,
                ),
            )
        if len(axis_candidates) > independent_count:
            _append_unique(
                problems,
                _issue(
                    bank,
                    ReachabilityIssueCode.EQUIVALENCE_REDUCES_COVERAGE,
                    f"Las equivalencias reducen la capacidad independiente de {axis.value}.",
                    "Revisar grupos de equivalencia y no contar variantes como evidencias nuevas.",
                    axis=axis,
                    details={
                        "tasks": len(axis_candidates),
                        "independent_groups": independent_count,
                    },
                ),
            )
        if len(axis_candidates) > MAX_TASKS_PER_AXIS:
            warnings.append(
                _issue(
                    bank,
                    ReachabilityIssueCode.AXIS_EXCEEDS_RUNTIME_CAP,
                    f"El eje {axis.value} contiene más de {MAX_TASKS_PER_AXIS} tareas.",
                    "Comprobar mediante simulación que ninguna queda permanentemente inaccesible.",
                    axis=axis,
                    details={"tasks": len(axis_candidates), "runtime_cap": MAX_TASKS_PER_AXIS},
                    severity=ReachabilitySeverity.WARNING,
                )
            )
        task_types = {candidate.task_type for candidate in axis_candidates}
        if len(axis_candidates) >= 2 and len(task_types) < 2:
            warnings.append(
                _issue(
                    bank,
                    ReachabilityIssueCode.INSUFFICIENT_TASK_TYPE_DIVERSITY,
                    f"El eje {axis.value} solo usa un tipo de tarea.",
                    "Añadir variedad si se pretende alcanzar cobertura suficiente y mayor confianza.",
                    axis=axis,
                    details={"task_types": sorted(item.value for item in task_types)},
                    severity=ReachabilitySeverity.WARNING,
                )
            )
        difficulties = sorted({candidate.difficulty for candidate in axis_candidates})
        for lower, upper in zip(difficulties, difficulties[1:], strict=False):
            if upper - lower > 1:
                warnings.append(
                    _issue(
                        bank,
                        ReachabilityIssueCode.DIFFICULTY_GAP,
                        f"El eje {axis.value} salta de dificultad {lower} a {upper}.",
                        "Añadir una transición intermedia o justificar editorialmente el salto.",
                        axis=axis,
                        details={"lower": lower, "upper": upper},
                        severity=ReachabilitySeverity.WARNING,
                    )
                )

    if len(candidates) < MIN_REVIEW_TASKS or static_capacity < MIN_REVIEW_TASKS:
        _append_unique(
            problems,
            _issue(
                bank,
                ReachabilityIssueCode.AXIS_CAP_REDUCES_GLOBAL_CAPACITY,
                "La capacidad estática del banco no alcanza las doce tareas mínimas.",
                "Equilibrar tareas independientes entre ejes sin superar el máximo por eje.",
                details={
                    "task_count": len(candidates),
                    "capacity_after_equivalence_and_axis_cap": static_capacity,
                    "required": MIN_REVIEW_TASKS,
                },
            ),
        )

    scenarios = [_scenario_report(scenario_id, candidates) for scenario_id in _SCENARIO_LABELS]
    for scenario in scenarios:
        if (
            scenario.scenario_id in _BLOCKING_SCENARIOS
            and scenario.ended_no_candidates
            and not scenario.reached_minimum_evidence
        ):
            _append_unique(
                problems,
                _issue(
                    bank,
                    ReachabilityIssueCode.SCENARIO_ENDS_NO_CANDIDATES,
                    f"El escenario «{scenario.label}» agota candidatos con solo "
                    f"{scenario.evaluable_evidence_count} evidencias.",
                    "Añadir rutas que permitan alcanzar el mínimo sin depender de respuestas perfectas.",
                    details={
                        "scenario": scenario.scenario_id,
                        "tasks": len(scenario.steps),
                        "evaluable_evidence": scenario.evaluable_evidence_count,
                    },
                ),
            )
            if _consecutive_type_is_the_blocker(scenario, candidates):
                _append_unique(
                    problems,
                    _issue(
                        bank,
                        ReachabilityIssueCode.CONSECUTIVE_TYPE_DEAD_END,
                        f"El escenario «{scenario.label}» puede cerrar tras dos tipos consecutivos.",
                        "Añadir un tipo alternativo elegible en ese punto de la trayectoria.",
                        details={
                            "scenario": scenario.scenario_id,
                            "repeated_type": scenario.steps[-1].task_type.value,
                        },
                    ),
                )

    exploration = _explore(candidates, state_limit=max_exploration_states)
    reachable_ids = set(exploration.reachable_tasks)
    unreachable_ids = sorted(known_ids - reachable_ids) if exploration.conclusive else []
    task_unreachability = [
        TaskUnreachability(
            task_id=candidate.candidate_id,
            axis=candidate.axis,
            reasons=_unreachability_reasons(
                candidate,
                candidates=candidates,
                reachable_ids=reachable_ids,
                entry_axes=entry_axes,
                cycle_ids=cycle_ids,
            ),
        )
        for candidate in candidates
        if candidate.candidate_id in unreachable_ids
    ]
    for item in task_unreachability:
        _append_unique(
            problems,
            _issue(
                bank,
                ReachabilityIssueCode.TASK_NEVER_SELECTABLE,
                f"La tarea {item.task_id} no es seleccionable en ninguna trayectoria explorada.",
                "Corregir eje, dificultad, prerrequisitos, equivalencia, tipo u orden editorial.",
                task_id=item.task_id,
                axis=item.axis,
                details={"reasons": item.reasons},
            ),
        )
        candidate = next(
            candidate for candidate in candidates if candidate.candidate_id == item.task_id
        )
        if candidate.difficulty >= 3:
            _append_unique(
                problems,
                _issue(
                    bank,
                    ReachabilityIssueCode.DIFFICULTY_PATH_UNREACHABLE,
                    f"La tarea {item.task_id} de dificultad {candidate.difficulty} no tiene una ruta efectiva.",
                    "Añadir evidencia previa alcanzable en el mismo eje y verificar sus prerrequisitos.",
                    task_id=item.task_id,
                    axis=item.axis,
                    details={"difficulty": candidate.difficulty},
                ),
            )

    if not exploration.conclusive:
        _append_unique(
            problems,
            _issue(
                bank,
                ReachabilityIssueCode.EXPLORATION_INCONCLUSIVE,
                "La exploración alcanzó su límite antes de vaciar la frontera de estados.",
                "Aumentar el límite editorial o reducir el banco antes de declarar readiness.",
                details={
                    "state_limit": exploration.state_limit,
                    "frontier_states": exploration.frontier_states,
                },
            ),
        )
    if len(reachable_ids) < MIN_REVIEW_TASKS:
        _append_unique(
            problems,
            _issue(
                bank,
                ReachabilityIssueCode.INSUFFICIENT_REACHABLE_TASKS,
                f"Solo {len(reachable_ids)} de {len(candidates)} tareas fueron alcanzables.",
                "Reequilibrar ejes y rutas hasta disponer de al menos doce tareas alcanzables.",
                details={"reachable": len(reachable_ids), "required": MIN_REVIEW_TASKS},
            ),
        )

    axis_coverage: list[AxisReachability] = []
    for axis in CORE_TEXT_AXES:
        axis_candidates = [candidate for candidate in candidates if candidate.axis == axis]
        maximum_evidence = exploration.maximum_evidence_by_axis.get(axis.value, 0)
        reachable_axis_tasks = [
            candidate for candidate in axis_candidates if candidate.candidate_id in reachable_ids
        ]
        axis_coverage.append(
            AxisReachability(
                axis=axis,
                candidate_count=len(axis_candidates),
                entry_candidate_count=len(entries_by_axis[axis]),
                independent_candidate_count=len(
                    {candidate.equivalence_key for candidate in axis_candidates}
                ),
                reachable_task_count=len(reachable_axis_tasks),
                maximum_evaluable_evidence_observed=maximum_evidence,
                coverage_possible=maximum_evidence >= MIN_AXIS_EVIDENCE,
            )
        )
        if maximum_evidence < MIN_AXIS_EVIDENCE:
            _append_unique(
                problems,
                _issue(
                    bank,
                    ReachabilityIssueCode.INSUFFICIENT_AXIS_COVERAGE,
                    f"El eje {axis.value} alcanzó como máximo {maximum_evidence} evidencias evaluables.",
                    "Proporcionar dos rutas independientes realmente seleccionables.",
                    axis=axis,
                    details={"maximum_evidence": maximum_evidence},
                ),
            )

    ready_for_review = exploration.conclusive and not problems
    return DiagnosticReachabilityReport(
        ready_for_review=ready_for_review,
        bank_id=bank.manifest.bank_id,
        bank_version=bank.manifest.bank_version,
        manifest_file=bank.manifest_file,
        editorial_status=bank.manifest.editorial_status,
        priority_axes=list(CORE_TEXT_AXES),
        present_axes=present_axes,
        missing_candidate_axes=missing_axes,
        axes_without_entry_candidate=axes_without_entry,
        total_task_count=len(candidates),
        potentially_reachable_task_count=len(reachable_ids),
        minimum_observed_tasks_per_trajectory=exploration.minimum_terminal_tasks,
        maximum_observed_tasks_per_trajectory=exploration.maximum_terminal_tasks,
        maximum_evaluable_evidence_observed=max(
            exploration.maximum_evaluable_evidence,
            *(scenario.evaluable_evidence_count for scenario in scenarios),
        ),
        reachable_tasks=sorted(reachable_ids),
        permanently_unreachable_tasks=unreachable_ids,
        task_unreachability=task_unreachability,
        scenarios=scenarios,
        terminal_reasons=exploration.terminal_reasons,
        axis_coverage=axis_coverage,
        problems=problems,
        warnings=warnings,
        editorial_information=[
            f"El selector runtime limita cada eje a {MAX_TASKS_PER_AXIS} tareas.",
            f"La preparación exige {MIN_REVIEW_TASKS} tareas y al menos "
            f"{MIN_EVALUABLE_FOR_EARLY_STOP} evidencias evaluables en escenarios ordinarios.",
            "El escenario all_not_evaluable y el abandono temprano son cierres parciales esperados.",
            "El análisis es local, determinista y no persiste sesiones ni respuestas.",
        ],
        exploration=exploration,
    )


__all__ = [
    "DEFAULT_EXPLORATION_STATE_LIMIT",
    "DiagnosticReachabilityReport",
    "ExplorationReport",
    "ReachabilityIssue",
    "ReachabilityIssueCode",
    "ReachabilitySeverity",
    "ScenarioReport",
    "analyze_diagnostic_bank",
]
