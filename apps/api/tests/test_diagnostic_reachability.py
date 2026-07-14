import json
import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from deutschos_api.content.diagnostic import (
    DIAGNOSTIC_BANK_SCHEMA_VERSION,
    DIAGNOSTIC_TASK_FILE_SCHEMA_VERSION,
    DiagnosticBankManifest,
    DiagnosticTaskBank,
    DiagnosticTaskDefinition,
    EditorialStatus,
    file_sha256,
    load_diagnostic_banks,
)
from deutschos_api.content.diagnostic_reachability import (
    DiagnosticReachabilityReport,
    ReachabilityIssueCode,
    analyze_diagnostic_bank,
)
from deutschos_api.diagnostic_engine.schemas import CORE_TEXT_AXES
from deutschos_api.learning_engine.curriculum import CURRICULUM_VERSION
from deutschos_api.models import DiagnosticAxis, DiagnosticTaskType

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_BANK = PROJECT_ROOT / "data" / "diagnostic"
VALIDATOR = PROJECT_ROOT / "scripts" / "validate-diagnostic-bank.sh"
REPOSITORY_SKILL_IDS = {9, 10, 11, 12, 13, 15, 16, 17, 18, 20, 21, 22}

_TASK_TYPES = {
    DiagnosticAxis.READING_COMPREHENSION: (
        DiagnosticTaskType.BINARY_CHOICE,
        DiagnosticTaskType.GAP_FILL,
    ),
    DiagnosticAxis.WRITTEN_PRODUCTION: (
        DiagnosticTaskType.PERSONAL_SHORT_ANSWER,
        DiagnosticTaskType.SHORT_MESSAGE,
    ),
    DiagnosticAxis.ACTIVE_GRAMMAR: (
        DiagnosticTaskType.SENTENCE_CORRECTION,
        DiagnosticTaskType.WORD_ORDER,
    ),
    DiagnosticAxis.RECEPTIVE_VOCABULARY: (
        DiagnosticTaskType.BINARY_CHOICE,
        DiagnosticTaskType.GAP_FILL,
    ),
    DiagnosticAxis.PRODUCTIVE_VOCABULARY: (
        DiagnosticTaskType.GAP_FILL,
        DiagnosticTaskType.PERSONAL_SHORT_ANSWER,
    ),
    DiagnosticAxis.TYPED_COMMUNICATION_REPAIR: (
        DiagnosticTaskType.TYPED_COMMUNICATION_REPAIR,
        DiagnosticTaskType.TYPED_COMMUNICATION_REPAIR,
    ),
}


def task(
    identifier: str,
    *,
    axis: DiagnosticAxis,
    task_type: DiagnosticTaskType,
    difficulty: int = 1,
    prerequisites: list[str] | None = None,
    equivalence_group: str | None = None,
) -> DiagnosticTaskDefinition:
    return DiagnosticTaskDefinition.model_validate(
        {
            "id": identifier,
            "version": "1.0.0",
            "level": "pre-A1",
            "axis": axis.value,
            "secondary_axes": [],
            "skill_id": None,
            "task_type": task_type.value,
            "difficulty": difficulty,
            "modality": "text",
            "response_type": "short_text",
            "estimated_seconds": 30,
            "prerequisites": prerequisites or [],
            "equivalence_group": equivalence_group or f"{identifier}.eq",
            "ambiguity_risk": "low",
            "scoring_mode": "deterministic",
            "rubric_version": "test.v1",
            "public": {
                "instructions": "Instrucción sintética.",
                "prompt": "Contenido sintético.",
                "options": [],
            },
            "private": {
                "rubric": {
                    "strategy": "exact_match",
                    "accepted_responses": ["ja"],
                },
                "scoring_policy": {
                    "normalization": {
                        "unicode_form": "NFC",
                        "trim_outer_whitespace": True,
                        "collapse_internal_whitespace": True,
                        "case_sensitive": False,
                        "punctuation": "significant",
                    },
                    "maximum_score": 1.0,
                    "correct_condition": "Coincidencia exacta con ja.",
                    "incorrect_condition": "Cualquier otra respuesta evaluable.",
                },
            },
            "metadata": {"topic": "synthetic", "context": None},
            "editorial": {
                "tags": ["synthetic"],
                "authoring_notes": "Fixture de alcanzabilidad sin contenido real.",
            },
        }
    )


def reachable_tasks() -> list[DiagnosticTaskDefinition]:
    tasks: list[DiagnosticTaskDefinition] = []
    for axis_index, axis in enumerate(CORE_TEXT_AXES, start=1):
        for task_index, task_type in enumerate(_TASK_TYPES[axis], start=1):
            tasks.append(
                task(
                    f"test.reachable.{axis_index}.{task_index}",
                    axis=axis,
                    task_type=task_type,
                    difficulty=task_index,
                )
            )
    return tasks


def bank(
    tasks: list[DiagnosticTaskDefinition],
    *,
    status: EditorialStatus = EditorialStatus.DRAFT,
    bank_id: str = "test.reachability",
) -> DiagnosticTaskBank:
    axes = sorted(
        {item.axis for item in tasks},
        key=lambda item: item.value,
    ) or [DiagnosticAxis.READING_COMPREHENSION]
    files = [
        {
            "path": "synthetic.tasks.json",
            "sha256": "0" * 64 if status == EditorialStatus.PRODUCTION else None,
        }
    ]
    manifest = DiagnosticBankManifest.model_validate(
        {
            "schema_version": DIAGNOSTIC_BANK_SCHEMA_VERSION,
            "bank_id": bank_id,
            "bank_version": "1.0.0",
            "diagnostic_version": "diagnostic-text.v1",
            "target_language": "de",
            "levels": ["pre-A1"],
            "modalities": ["text"],
            "axes": [axis.value for axis in axes],
            "editorial_status": status.value,
            "created_on": "2026-07-14",
            "updated_on": "2026-07-14",
            "files": files,
            "minimum_compatibility": {
                "application_version": "0.3.0",
                "diagnostic_engine_version": "diagnostic-engine.v1",
                "curriculum_version": CURRICULUM_VERSION,
            },
            "editorial_notes": "Banco sintético para pruebas de alcanzabilidad.",
        }
    )
    return DiagnosticTaskBank(
        manifest_file="synthetic.bank.json",
        manifest=manifest,
        tasks=tuple(tasks),
    )


def issue_codes(report: DiagnosticReachabilityReport) -> set[ReachabilityIssueCode]:
    return {issue.code for issue in report.problems}


def write_bank(directory: Path, value: DiagnosticTaskBank) -> None:
    task_path = directory / "synthetic.tasks.json"
    task_path.write_text(
        json.dumps(
            {
                "schema_version": DIAGNOSTIC_TASK_FILE_SCHEMA_VERSION,
                "bank_id": value.manifest.bank_id,
                "tasks": [item.model_dump(mode="json") for item in value.tasks],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manifest = value.manifest.model_dump(mode="json")
    if value.manifest.editorial_status == EditorialStatus.PRODUCTION:
        manifest["files"][0]["sha256"] = file_sha256(task_path)
    (directory / "synthetic.bank.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )


def run_validator(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(VALIDATOR), *arguments, str(directory)],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DEUTSCHOS_DATABASE_URL": "sqlite://"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_reachable_synthetic_bank_is_ready_and_exploration_is_conclusive():
    report = analyze_diagnostic_bank(bank(reachable_tasks()))

    assert report.ready_for_review is True
    assert report.exploration.conclusive is True
    assert report.potentially_reachable_task_count == 12
    assert report.permanently_unreachable_tasks == []
    assert report.minimum_observed_tasks_per_trajectory == 12
    assert report.maximum_observed_tasks_per_trajectory == 12
    assert report.maximum_evaluable_evidence_observed == 12
    assert all(item.coverage_possible for item in report.axis_coverage)
    assert report.problems == []
    assert {scenario.scenario_id for scenario in report.scenarios} == {
        "all_correct_without_help",
        "all_incorrect",
        "all_partial",
        "all_correct_with_help",
        "alternating_correct_incorrect",
        "alternating_correct_partial",
        "one_not_evaluable_per_axis",
        "all_not_evaluable",
        "controlled_contradictions",
        "early_abandonment",
    }
    all_not_evaluable = next(
        item for item in report.scenarios if item.scenario_id == "all_not_evaluable"
    )
    assert all_not_evaluable.partial is True
    assert all_not_evaluable.evaluable_evidence_count == 0


def test_missing_axis_and_axis_without_entry_are_structured_problems():
    tasks = reachable_tasks()
    tasks = [
        item
        for item in tasks
        if item.axis
        not in {DiagnosticAxis.READING_COMPREHENSION, DiagnosticAxis.WRITTEN_PRODUCTION}
    ]
    tasks.append(
        task(
            "test.reading.high",
            axis=DiagnosticAxis.READING_COMPREHENSION,
            task_type=DiagnosticTaskType.SHORT_TEXT_COMPREHENSION,
            difficulty=3,
        )
    )
    report = analyze_diagnostic_bank(bank(tasks))

    assert report.ready_for_review is False
    assert report.missing_candidate_axes == [DiagnosticAxis.WRITTEN_PRODUCTION]
    assert DiagnosticAxis.READING_COMPREHENSION in report.axes_without_entry_candidate
    assert ReachabilityIssueCode.MISSING_PRIORITY_AXIS in issue_codes(report)
    assert ReachabilityIssueCode.AXIS_WITHOUT_ENTRY_CANDIDATE in issue_codes(report)
    assert ReachabilityIssueCode.DIFFICULTY_PATH_UNREACHABLE in issue_codes(report)


def test_difficulty_three_is_reachable_after_a_valid_axis_entry():
    tasks = reachable_tasks()
    reading_second = next(item for item in tasks if item.id == "test.reachable.1.2")
    tasks[tasks.index(reading_second)] = reading_second.model_copy(update={"difficulty": 3})
    report = analyze_diagnostic_bank(bank(tasks))

    assert "test.reachable.1.2" in report.reachable_tasks
    assert all(
        issue.task_id != "test.reachable.1.2"
        or issue.code != ReachabilityIssueCode.DIFFICULTY_PATH_UNREACHABLE
        for issue in report.problems
    )


def test_prerequisite_cycles_equivalence_and_type_dead_ends_are_detected():
    first = task(
        "test.cycle.first",
        axis=DiagnosticAxis.READING_COMPREHENSION,
        task_type=DiagnosticTaskType.BINARY_CHOICE,
        prerequisites=["test.cycle.second"],
    )
    second = task(
        "test.cycle.second",
        axis=DiagnosticAxis.READING_COMPREHENSION,
        task_type=DiagnosticTaskType.BINARY_CHOICE,
        prerequisites=["test.cycle.first"],
        equivalence_group=first.equivalence_group,
    )
    third = task(
        "test.same-type.third",
        axis=DiagnosticAxis.ACTIVE_GRAMMAR,
        task_type=DiagnosticTaskType.BINARY_CHOICE,
    )
    fourth = task(
        "test.same-type.fourth",
        axis=DiagnosticAxis.ACTIVE_GRAMMAR,
        task_type=DiagnosticTaskType.BINARY_CHOICE,
    )
    fifth = task(
        "test.same-type.fifth",
        axis=DiagnosticAxis.ACTIVE_GRAMMAR,
        task_type=DiagnosticTaskType.BINARY_CHOICE,
    )
    report = analyze_diagnostic_bank(bank([first, second, third, fourth, fifth]))

    codes = issue_codes(report)
    assert ReachabilityIssueCode.PREREQUISITE_CYCLE in codes
    assert ReachabilityIssueCode.EQUIVALENCE_REDUCES_COVERAGE in codes
    assert ReachabilityIssueCode.CONSECUTIVE_TYPE_DEAD_END in codes


def test_missing_and_valid_but_unreachable_prerequisites_are_reported():
    missing = task(
        "test.prerequisite.missing",
        axis=DiagnosticAxis.READING_COMPREHENSION,
        task_type=DiagnosticTaskType.GAP_FILL,
        prerequisites=["test.absent"],
    )
    blocked_anchor = task(
        "test.prerequisite.high-anchor",
        axis=DiagnosticAxis.PRODUCTIVE_VOCABULARY,
        task_type=DiagnosticTaskType.GAP_FILL,
        difficulty=3,
    )
    blocked_dependent = task(
        "test.prerequisite.dependent",
        axis=DiagnosticAxis.PRODUCTIVE_VOCABULARY,
        task_type=DiagnosticTaskType.PERSONAL_SHORT_ANSWER,
        prerequisites=[blocked_anchor.id],
    )
    report = analyze_diagnostic_bank(bank([missing, blocked_anchor, blocked_dependent]))

    assert ReachabilityIssueCode.PREREQUISITE_MISSING in issue_codes(report)
    assert blocked_anchor.id in report.permanently_unreachable_tasks
    assert blocked_dependent.id in report.permanently_unreachable_tasks
    assert ReachabilityIssueCode.TASK_NEVER_SELECTABLE in issue_codes(report)


def test_exploration_limit_is_inconclusive_and_never_reports_ready():
    report = analyze_diagnostic_bank(
        bank(reachable_tasks()),
        max_exploration_states=1,
    )

    assert report.ready_for_review is False
    assert report.exploration.conclusive is False
    assert report.exploration.frontier_states > 0
    assert ReachabilityIssueCode.EXPLORATION_INCONCLUSIVE in issue_codes(report)
    assert report.permanently_unreachable_tasks == []


def test_report_dtos_reject_unknown_fields():
    payload = analyze_diagnostic_bank(bank(reachable_tasks())).model_dump()
    payload["invented"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DiagnosticReachabilityReport.model_validate(payload)


def test_repository_bank_is_valid_but_not_ready_and_matches_known_reachability():
    repository_bank = load_diagnostic_banks(
        REPOSITORY_BANK,
        curriculum_skill_ids={CURRICULUM_VERSION: REPOSITORY_SKILL_IDS},
    )[0]
    report = analyze_diagnostic_bank(repository_bank)

    assert repository_bank.manifest.bank_version == "0.2.0"
    assert report.ready_for_review is False
    assert report.exploration.conclusive is True
    assert report.potentially_reachable_task_count == 8
    assert report.minimum_observed_tasks_per_trajectory == 6
    assert report.maximum_observed_tasks_per_trajectory == 6
    assert report.maximum_evaluable_evidence_observed == 6
    assert report.missing_candidate_axes == [
        DiagnosticAxis.READING_COMPREHENSION,
        DiagnosticAxis.WRITTEN_PRODUCTION,
    ]
    assert report.axes_without_entry_candidate == [
        DiagnosticAxis.READING_COMPREHENSION,
        DiagnosticAxis.WRITTEN_PRODUCTION,
        DiagnosticAxis.PRODUCTIVE_VOCABULARY,
        DiagnosticAxis.TYPED_COMMUNICATION_REPAIR,
    ]
    assert report.permanently_unreachable_tasks == [
        "diagnostic.initial.communication.repeat.001",
        "diagnostic.initial.laboratory.mikroskop.001",
        "diagnostic.initial.nominative.subject.001",
        "diagnostic.initial.regular.lernen-order.001",
    ]
    assert any(
        warning.code == ReachabilityIssueCode.AXIS_EXCEEDS_RUNTIME_CAP
        and warning.axis == DiagnosticAxis.ACTIVE_GRAMMAR
        for warning in report.warnings
    )
    assert all(
        scenario.terminal_reason == "no_candidates"
        for scenario in report.scenarios
        if scenario.scenario_id != "early_abandonment"
    )


def test_cli_allows_unready_draft_but_require_ready_and_published_states_block(tmp_path):
    draft = bank([reachable_tasks()[0]], status=EditorialStatus.DRAFT)
    write_bank(tmp_path, draft)

    normal = run_validator(tmp_path)
    required = run_validator(tmp_path, "--require-ready")

    assert normal.returncode == 0
    assert "Preparado para reviewed: no" in normal.stdout
    assert required.returncode != 0
    assert "missing_priority_axis" in required.stdout

    for status in (EditorialStatus.REVIEWED, EditorialStatus.PRODUCTION):
        published_path = tmp_path / status.value
        published_path.mkdir()
        write_bank(
            published_path,
            bank([reachable_tasks()[0]], status=status, bank_id=f"test.{status.value}"),
        )
        result = run_validator(published_path)
        assert result.returncode != 0
        assert f"Estado editorial: {status.value}=1" in result.stdout


def test_cli_accepts_reachable_draft_with_require_ready(tmp_path):
    write_bank(tmp_path, bank(reachable_tasks()))

    result = run_validator(tmp_path, "--require-ready")

    assert result.returncode == 0
    assert "Preparado para reviewed: sí" in result.stdout
    assert "Tareas alcanzables: 12/12" in result.stdout
