import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from deutschos_api.api import diagnostic as diagnostic_api
from deutschos_api.content.diagnostic import (
    DIAGNOSTIC_BANK_SCHEMA_VERSION,
    DIAGNOSTIC_TASK_FILE_SCHEMA_VERSION,
    DiagnosticContentFormatError,
    DiagnosticTaskDefinition,
    EditorialStatus,
    FilesystemCandidateProvider,
    file_sha256,
    load_diagnostic_banks,
)
from deutschos_api.db.session import get_db
from deutschos_api.diagnostic_engine.exceptions import CandidateUnavailableError
from deutschos_api.diagnostic_engine.schemas import EvaluationOutcome, ResponseSubmission
from deutschos_api.diagnostic_engine.scoring import evaluate_response
from deutschos_api.learning_engine.curriculum import CURRICULUM_VERSION
from deutschos_api.main import app

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = PROJECT_ROOT / "scripts" / "validate-diagnostic-bank.sh"
REPOSITORY_BANK = PROJECT_ROOT / "data" / "diagnostic"
REPOSITORY_SKILL_IDS = {9, 10, 11, 12, 13, 17}


def task_payload(identifier: str = "test.placeholder", **overrides):
    payload = {
        "id": identifier,
        "version": "1",
        "level": "pre-A1",
        "axis": "reading_comprehension",
        "secondary_axes": [],
        "skill_id": None,
        "task_type": "binary_choice",
        "difficulty": 1,
        "modality": "text",
        "response_type": "single_choice",
        "estimated_seconds": 30,
        "prerequisites": [],
        "equivalence_group": f"{identifier}.equivalent",
        "ambiguity_risk": "low",
        "scoring_mode": "deterministic",
        "rubric_version": "deterministic.v1",
        "public": {
            "instructions": "Instrucción sintética exclusiva de prueba.",
            "prompt": "Contenido sintético exclusivo de prueba.",
            "options": ["alpha", "beta"],
        },
        "private": {
            "rubric": {
                "strategy": "exact_match",
                "accepted_responses": ["alpha"],
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
                "correct_condition": "Coincidencia exacta con alpha tras normalizar.",
                "incorrect_condition": "Cualquier otra respuesta evaluable.",
            },
        },
        "metadata": {"topic": "synthetic-test", "context": None},
        "editorial": {
            "tags": ["synthetic-test"],
            "authoring_notes": "Fixture sin contenido pedagógico real.",
        },
    }
    payload.update(overrides)
    return payload


def task_file_payload(bank_id: str, *tasks: dict):
    return {
        "schema_version": DIAGNOSTIC_TASK_FILE_SCHEMA_VERSION,
        "bank_id": bank_id,
        "tasks": list(tasks),
    }


def manifest_payload(
    bank_id: str,
    *tasks: dict,
    status: str = "draft",
    file_path: str = "synthetic.tasks.json",
    sha256: str | None = None,
    **overrides,
):
    axes = sorted(
        {axis for task in tasks for axis in (task["axis"], *task.get("secondary_axes", []))}
    ) or ["reading_comprehension"]
    levels = sorted({task["level"] for task in tasks}) or ["pre-A1"]
    file_reference = {"path": file_path}
    if sha256 is not None:
        file_reference["sha256"] = sha256
    payload = {
        "schema_version": DIAGNOSTIC_BANK_SCHEMA_VERSION,
        "bank_id": bank_id,
        "bank_version": "test-bank.v1",
        "diagnostic_version": "diagnostic-text.v1",
        "target_language": "de",
        "levels": levels,
        "modalities": ["text"],
        "axes": axes,
        "editorial_status": status,
        "created_on": "2026-07-14",
        "updated_on": "2026-07-14",
        "files": [file_reference],
        "minimum_compatibility": {
            "application_version": "0.3.0",
            "diagnostic_engine_version": "diagnostic-engine.v1",
            "curriculum_version": CURRICULUM_VERSION,
        },
        "editorial_notes": "Manifiesto sintético exclusivo de prueba.",
    }
    payload.update(overrides)
    return payload


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def write_bank(
    directory: Path,
    *tasks: dict,
    bank_id: str = "test.bank",
    status: str = "draft",
    task_filename: str = "synthetic.tasks.json",
    manifest_filename: str = "synthetic.bank.json",
    manifest_overrides: dict | None = None,
) -> tuple[Path, Path]:
    task_path = write_json(
        directory / task_filename,
        task_file_payload(bank_id, *tasks),
    )
    checksum = file_sha256(task_path) if status == "production" else None
    manifest = manifest_payload(
        bank_id,
        *tasks,
        status=status,
        file_path=task_filename,
        sha256=checksum,
        **(manifest_overrides or {}),
    )
    manifest_path = write_json(directory / manifest_filename, manifest)
    return manifest_path, task_path


def current_skills(*skill_ids: int) -> dict[str, set[int]]:
    return {CURRICULUM_VERSION: set(skill_ids)}


def test_task_schema_separates_public_private_and_editorial_fields():
    schema = DiagnosticTaskDefinition.model_json_schema()
    required = set(schema["required"])

    assert {
        "id",
        "version",
        "axis",
        "skill_id",
        "task_type",
        "difficulty",
        "modality",
        "response_type",
        "estimated_seconds",
        "prerequisites",
        "equivalence_group",
        "ambiguity_risk",
        "scoring_mode",
        "rubric_version",
        "public",
        "private",
        "metadata",
        "editorial",
    }.issubset(required)
    assert schema["additionalProperties"] is False


def test_loader_maps_a_valid_production_bank_to_engine_candidates(tmp_path):
    write_bank(tmp_path, task_payload(), status="production")

    banks = load_diagnostic_banks(tmp_path, curriculum_skill_ids=current_skills())
    provider = FilesystemCandidateProvider.from_directory(
        tmp_path,
        curriculum_skill_ids=current_skills(),
    )
    candidate = provider.candidates(diagnostic_version="diagnostic-text.v1")[0]

    assert len(banks) == 1
    assert banks[0].manifest.editorial_status == EditorialStatus.PRODUCTION
    assert provider.available_versions == ("diagnostic-text.v1",)
    assert candidate.candidate_id == "test.placeholder"
    assert candidate.content == {
        "instructions": "Instrucción sintética exclusiva de prueba.",
        "prompt": "Contenido sintético exclusivo de prueba.",
    }
    assert candidate.expected_answer == {
        "response_type": "single_choice",
        "rubric_version": "deterministic.v1",
        "scoring_mode": "deterministic",
        "accepted_responses": ["alpha"],
    }


def test_absent_or_empty_bank_is_valid_but_has_no_production_candidates(tmp_path):
    absent = FilesystemCandidateProvider.from_directory(tmp_path / "absent")
    empty = FilesystemCandidateProvider.from_directory(tmp_path)

    assert absent.has_candidates is False
    assert empty.has_candidates is False
    with pytest.raises(CandidateUnavailableError):
        empty.candidates(diagnostic_version="diagnostic-text.v1")


@pytest.mark.parametrize("status", ["draft", "reviewed"])
def test_non_production_banks_are_unavailable_to_the_production_provider(tmp_path, status):
    write_bank(tmp_path, task_payload(), status=status)

    production_provider = FilesystemCandidateProvider.from_directory(tmp_path)
    editorial_provider = FilesystemCandidateProvider.from_directory(
        tmp_path,
        production_only=False,
    )

    assert production_provider.has_candidates is False
    with pytest.raises(CandidateUnavailableError):
        production_provider.candidates(diagnostic_version="diagnostic-text.v1")
    assert editorial_provider.has_candidates is True


@pytest.mark.parametrize(
    "payload",
    [
        {**task_payload(), "unexpected": True},
        {**task_payload(), "difficulty": 0},
        {**task_payload(), "modality": "audio"},
        {key: value for key, value in task_payload().items() if key != "private"},
        {
            **task_payload(),
            "private": {"rubric": {"strategy": "ordered_tokens", "expected_tokens": ["a", "b"]}},
        },
        {
            **task_payload(),
            "private": {"rubric": {"strategy": "exact_match", "accepted_responses": ["gamma"]}},
        },
        {
            **task_payload(),
            "scoring_mode": "deterministic",
            "response_type": "free_text",
            "public": {
                "instructions": "Sintética.",
                "prompt": "Sintético.",
                "options": [],
            },
            "private": {"rubric": {"strategy": "manual_only"}},
        },
    ],
)
def test_task_contract_rejects_extra_invalid_or_non_deterministic_fields(payload):
    with pytest.raises(ValidationError):
        DiagnosticTaskDefinition.model_validate(payload)


def test_invalid_manifest_and_incompatible_versions_report_file_and_location(tmp_path):
    write_bank(
        tmp_path,
        task_payload(),
        manifest_overrides={"target_language": "fr"},
    )
    with pytest.raises(
        DiagnosticContentFormatError,
        match=r"synthetic\.bank\.json:target_language:",
    ):
        load_diagnostic_banks(tmp_path)

    manifest_path = tmp_path / "synthetic.bank.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["target_language"] = "de"
    payload["schema_version"] = "diagnostic-bank-manifest.v99"
    write_json(manifest_path, payload)
    with pytest.raises(
        DiagnosticContentFormatError,
        match=r"synthetic\.bank\.json:schema_version:",
    ):
        load_diagnostic_banks(tmp_path)

    payload["schema_version"] = DIAGNOSTIC_BANK_SCHEMA_VERSION
    payload["minimum_compatibility"]["application_version"] = "99.0.0"
    write_json(manifest_path, payload)
    with pytest.raises(
        DiagnosticContentFormatError,
        match=r"minimum_compatibility:",
    ):
        load_diagnostic_banks(tmp_path)


def test_missing_declared_file_and_path_traversal_are_rejected(tmp_path):
    manifest = manifest_payload("test.bank", task_payload(), file_path="missing.tasks.json")
    write_json(tmp_path / "test.bank.json", manifest)
    with pytest.raises(DiagnosticContentFormatError, match="declared file does not exist"):
        load_diagnostic_banks(tmp_path)

    manifest["files"] = [{"path": "../outside.tasks.json"}]
    write_json(tmp_path / "test.bank.json", manifest)
    with pytest.raises(DiagnosticContentFormatError, match=r"files\.0\.path"):
        load_diagnostic_banks(tmp_path)


def test_symlink_outside_bank_is_rejected(tmp_path):
    outside = write_json(tmp_path.parent / "outside.tasks.json", task_file_payload("test.bank"))
    link = tmp_path / "linked.tasks.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable on this filesystem")
    write_json(
        tmp_path / "test.bank.json",
        manifest_payload("test.bank", file_path="linked.tasks.json"),
    )

    with pytest.raises(DiagnosticContentFormatError, match="symbolic links"):
        load_diagnostic_banks(tmp_path)


def test_malformed_json_reports_line_and_column_and_load_is_atomic(tmp_path):
    write_bank(tmp_path, task_payload("test.valid"))
    invalid = tmp_path / "invalid.tasks.json"
    invalid.write_text("{not-json", encoding="utf-8")
    manifest = manifest_payload(
        "test.second",
        task_payload("test.invalid"),
        file_path="invalid.tasks.json",
    )
    write_json(tmp_path / "second.bank.json", manifest)

    with pytest.raises(
        DiagnosticContentFormatError,
        match=r"invalid\.tasks\.json:1:2: malformed JSON",
    ):
        FilesystemCandidateProvider.from_directory(tmp_path, production_only=False)


def test_duplicate_ids_unknown_skills_and_missing_prerequisites_are_rejected(tmp_path):
    write_bank(tmp_path, task_payload("test.same"), task_payload("test.same"))
    with pytest.raises(DiagnosticContentFormatError, match="duplicate task id"):
        load_diagnostic_banks(tmp_path)

    write_bank(
        tmp_path,
        task_payload("test.skill", skill_id=999),
    )
    with pytest.raises(DiagnosticContentFormatError, match="unknown skill_id 999"):
        load_diagnostic_banks(tmp_path, curriculum_skill_ids=current_skills(1))

    write_bank(
        tmp_path,
        task_payload("test.dependent", prerequisites=["test.absent"]),
    )
    with pytest.raises(DiagnosticContentFormatError, match="undeclared prerequisites"):
        load_diagnostic_banks(tmp_path)


def test_equivalence_groups_cannot_mix_axes_or_skill_dimensions(tmp_path):
    first = task_payload("test.first", equivalence_group="test.shared")
    second = task_payload(
        "test.second",
        axis="active_grammar",
        equivalence_group="test.shared",
    )
    write_bank(tmp_path, first, second)

    with pytest.raises(DiagnosticContentFormatError, match="mixes diagnostic dimensions"):
        load_diagnostic_banks(tmp_path)


def test_checksum_is_required_for_production_and_verified_when_present(tmp_path):
    manifest_path, task_path = write_bank(tmp_path, task_payload(), status="draft")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["editorial_status"] = "production"
    write_json(manifest_path, manifest)
    with pytest.raises(DiagnosticContentFormatError, match="require SHA-256"):
        load_diagnostic_banks(tmp_path)

    manifest["files"][0]["sha256"] = "0" * 64
    write_json(manifest_path, manifest)
    assert file_sha256(task_path) != "0" * 64
    with pytest.raises(DiagnosticContentFormatError, match="checksum mismatch"):
        load_diagnostic_banks(tmp_path)


def test_orphan_json_and_manifest_declarations_must_match_tasks(tmp_path):
    write_bank(tmp_path, task_payload())
    write_json(tmp_path / "orphan.json", {})
    with pytest.raises(DiagnosticContentFormatError, match="not declared"):
        load_diagnostic_banks(tmp_path)

    (tmp_path / "orphan.json").unlink()
    manifest_path = tmp_path / "synthetic.bank.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["axes"] = ["active_grammar"]
    write_json(manifest_path, manifest)
    with pytest.raises(DiagnosticContentFormatError, match="axes do not match"):
        load_diagnostic_banks(tmp_path)


def test_provider_order_is_reproducible(tmp_path):
    write_bank(
        tmp_path,
        task_payload("test.zulu"),
        task_payload("test.alpha"),
        status="production",
    )
    provider = FilesystemCandidateProvider.from_directory(tmp_path)

    assert [
        candidate.candidate_id
        for candidate in provider.candidates(diagnostic_version="diagnostic-text.v1")
    ] == ["test.alpha", "test.zulu"]


def test_production_dependency_returns_503_for_empty_or_invalid_bank(
    tmp_path,
    monkeypatch,
    db_session_factory,
):
    monkeypatch.setattr(diagnostic_api, "DIAGNOSTIC_CONTENT_DIRECTORY", tmp_path)
    with db_session_factory() as db:
        with pytest.raises(HTTPException) as empty:
            diagnostic_api.get_diagnostic_candidate_provider(db)
        assert empty.value.status_code == 503
        assert empty.value.detail == (
            "El banco diagnóstico versionado no está configurado en esta instalación."
        )

        write_json(tmp_path / "invalid.bank.json", [])
        with pytest.raises(HTTPException) as invalid:
            diagnostic_api.get_diagnostic_candidate_provider(db)
        assert invalid.value.status_code == 503
        assert invalid.value.detail == "El banco diagnóstico local no supera la validación."
        assert "invalid.bank.json" not in str(invalid.value.detail)


@pytest.mark.anyio
async def test_http_api_uses_validated_production_bank_without_private_fields(
    tmp_path,
    monkeypatch,
    client,
):
    write_bank(tmp_path, task_payload(), status="production")
    monkeypatch.setattr(diagnostic_api, "DIAGNOSTIC_CONTENT_DIRECTORY", tmp_path)

    created = await client.post(
        "/api/diagnostic/sessions",
        json={
            "request_id": "c6144f12-eb3a-4c18-b093-cad002db74a1",
            "curriculum_version": CURRICULUM_VERSION,
        },
    )
    session_id = created.json()["session_id"]
    await client.post(
        f"/api/diagnostic/sessions/{session_id}/start",
        json={"operation_id": "6127df2b-f89c-4624-8e13-d5e486f93727"},
    )
    selected = await client.post(
        f"/api/diagnostic/sessions/{session_id}/next-task",
        json={"operation_id": "5177b943-8db0-432d-a0d3-6cbb091e02f8"},
    )

    assert created.status_code == 201
    assert selected.status_code == 200
    assert selected.json()["task"]["content"] == {
        "instructions": "Instrucción sintética exclusiva de prueba.",
        "prompt": "Contenido sintético exclusivo de prueba.",
    }
    assert all(
        private not in selected.text
        for private in ("accepted_responses", "authoring_notes", "private", "rubric")
    )


def test_editorial_tool_validates_draft_summarises_and_does_not_modify_files(tmp_path):
    manifest_path, task_path = write_bank(tmp_path, task_payload(), status="draft")
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (manifest_path, task_path)
    }
    environment = {**os.environ, "DEUTSCHOS_DATABASE_URL": "sqlite://"}

    result = subprocess.run(
        [str(VALIDATOR), str(tmp_path)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Tareas: 1" in result.stdout
    assert "Estado editorial: draft=1" in result.stdout
    assert "Ejes: reading_comprehension" in result.stdout
    assert "Habilidades: ninguno" in result.stdout
    assert "Dificultad: 1=1" in result.stdout
    assert "Tipos: binary_choice=1" in result.stdout
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (manifest_path, task_path)
    } == before


def test_editorial_tool_returns_nonzero_and_location_for_invalid_fixture(tmp_path):
    invalid = tmp_path / "invalid.tasks.json"
    invalid.write_text("{not-json", encoding="utf-8")
    write_json(
        tmp_path / "invalid.bank.json",
        manifest_payload("test.bank", file_path="invalid.tasks.json"),
    )
    environment = {**os.environ, "DEUTSCHOS_DATABASE_URL": "sqlite://"}

    result = subprocess.run(
        [str(VALIDATOR), str(tmp_path)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid.tasks.json:1:2" in result.stderr


def test_repository_draft_bank_is_valid_complete_and_not_available_in_production():
    skill_map = current_skills(*REPOSITORY_SKILL_IDS)
    banks = load_diagnostic_banks(REPOSITORY_BANK, curriculum_skill_ids=skill_map)
    production = FilesystemCandidateProvider.from_directory(
        REPOSITORY_BANK,
        curriculum_skill_ids=skill_map,
    )
    editorial = FilesystemCandidateProvider.from_directory(
        REPOSITORY_BANK,
        curriculum_skill_ids=skill_map,
        production_only=False,
    )

    assert len(banks) == 1
    bank = banks[0]
    assert bank.manifest.bank_id == "deutschos.diagnostic.initial-text"
    assert bank.manifest.bank_version == "0.1.0"
    assert bank.manifest.editorial_status == EditorialStatus.DRAFT
    assert bank.manifest.created_on.isoformat() == "2026-07-14"
    assert bank.manifest.updated_on >= bank.manifest.created_on
    assert len(bank.tasks) == 6
    assert {task.skill_id for task in bank.tasks} == REPOSITORY_SKILL_IDS
    assert {task.modality for task in bank.tasks} == {"text"}
    assert {task.scoring_mode.value for task in bank.tasks} == {"deterministic"}
    assert all(task.ambiguity_risk.value == "low" for task in bank.tasks)
    assert len({task.id for task in bank.tasks}) == 6
    assert len({task.equivalence_group for task in bank.tasks}) == 6
    assert all(task.private.scoring_policy.maximum_score == 1.0 for task in bank.tasks)
    assert all(
        task.private.scoring_policy.normalization.case_sensitive
        == task.private.rubric.case_sensitive
        for task in bank.tasks
    )

    assert production.has_candidates is False
    assert editorial.has_candidates is True
    candidates = editorial.candidates(diagnostic_version="diagnostic-text.v1")
    assert len(candidates) == 6
    assert [candidate.candidate_id for candidate in candidates] == sorted(
        candidate.candidate_id for candidate in candidates
    )
    assert all(set(candidate.content) == {"instructions", "prompt"} for candidate in candidates)
    public_payload = json.dumps(
        [{"content": candidate.content, "options": candidate.options} for candidate in candidates],
        ensure_ascii=False,
    )
    assert all(
        private_key not in public_payload
        for private_key in (
            "accepted_responses",
            "authoring_notes",
            "correct_condition",
            "editorial",
            "expected_tokens",
            "private",
            "rubric",
            "scoring_policy",
        )
    )

    correct_responses = {
        "diagnostic.initial.pronouns.wir.001": "Wir",
        "diagnostic.initial.everyday.greeting.001": "Guten Morgen",
        "diagnostic.initial.sein.du.001": "bist",
        "diagnostic.initial.haben.wir.001": "haben",
        "diagnostic.initial.regular.lernen-order.001": "Ich lerne Deutsch",
        "diagnostic.initial.questions.wo-order.001": "Wo wohnst du",
    }
    for task_id, candidate in enumerate(candidates, start=1):
        result = evaluate_response(
            candidate,
            ResponseSubmission(
                session_id=1,
                task_id=task_id,
                evaluation_id=uuid4(),
                submission_id=uuid4(),
                response_text=correct_responses[candidate.candidate_id],
                response_language="de",
            ),
        )
        assert result.outcome == EvaluationOutcome.CORRECT_WITHOUT_HELP
        assert result.score == 1.0

    for candidate_id, response in (
        ("diagnostic.initial.regular.lernen-order.001", "Ich lerne Deutsch heute"),
        ("diagnostic.initial.questions.wo-order.001", "Wo wohnst du?"),
    ):
        candidate = next(item for item in candidates if item.candidate_id == candidate_id)
        result = evaluate_response(
            candidate,
            ResponseSubmission(
                session_id=1,
                task_id=1,
                evaluation_id=uuid4(),
                submission_id=uuid4(),
                response_text=response,
                response_language="de",
            ),
        )
        assert result.outcome == EvaluationOutcome.INCORRECT
        assert result.score == 0.0


@pytest.mark.anyio
async def test_repository_draft_bank_keeps_production_http_api_unavailable(client):
    class CurriculumRows:
        def all(self):
            return sorted(REPOSITORY_SKILL_IDS)

    class CurriculumOnlySession:
        def scalars(self, _statement):
            return CurriculumRows()

    app.dependency_overrides[get_db] = lambda: CurriculumOnlySession()
    response = await client.post(
        "/api/diagnostic/sessions",
        json={
            "request_id": "f98bc330-6193-4d2e-947e-ac567877331d",
            "curriculum_version": CURRICULUM_VERSION,
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "El banco diagnóstico versionado no está configurado en esta instalación."
    }
