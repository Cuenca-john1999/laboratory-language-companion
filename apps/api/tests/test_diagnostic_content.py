import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from deutschos_api.api import diagnostic as diagnostic_api
from deutschos_api.content.diagnostic import (
    DIAGNOSTIC_BANK_SCHEMA_VERSION,
    DiagnosticContentFormatError,
    DiagnosticTaskDefinition,
    FilesystemCandidateProvider,
    load_diagnostic_banks,
)
from deutschos_api.diagnostic_engine.exceptions import CandidateUnavailableError


def task_payload(identifier: str = "test.placeholder", **overrides):
    payload = {
        "id": identifier,
        "version": "1",
        "axis": "reading_comprehension",
        "skill_id": None,
        "task_type": "binary_choice",
        "difficulty": 1,
        "prompt": "Contenido sintético exclusivo de prueba.",
        "expected_response_type": "single_choice",
        "rubric_version": "deterministic.v1",
        "metadata": {
            "equivalence_key": f"{identifier}.equivalent",
            "ambiguity_risk": "low",
            "tags": ["synthetic-test"],
        },
        "estimated_seconds": 30,
        "options": ["alpha", "beta"],
        "rubric": {
            "strategy": "exact_match",
            "accepted_responses": ["alpha"],
        },
    }
    payload.update(overrides)
    return payload


def bank_payload(*tasks: dict, diagnostic_version: str = "diagnostic-text.v1"):
    return {
        "schema_version": DIAGNOSTIC_BANK_SCHEMA_VERSION,
        "diagnostic_version": diagnostic_version,
        "bank_version": "test-bank.v1",
        "tasks": list(tasks),
    }


def write_bank(directory: Path, filename: str, payload: dict) -> Path:
    path = directory / filename
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_json_schema_contains_every_required_task_field():
    schema = DiagnosticTaskDefinition.model_json_schema()
    required = set(schema["required"])

    assert {
        "id",
        "version",
        "axis",
        "skill_id",
        "task_type",
        "difficulty",
        "prompt",
        "expected_response_type",
        "rubric_version",
        "metadata",
        "estimated_seconds",
    }.issubset(required)
    assert schema["additionalProperties"] is False


def test_loader_maps_valid_json_to_strict_engine_candidate(tmp_path):
    write_bank(tmp_path, "reading.json", bank_payload(task_payload()))

    banks = load_diagnostic_banks(tmp_path)
    provider = FilesystemCandidateProvider.from_directory(tmp_path)
    candidates = provider.candidates(diagnostic_version="diagnostic-text.v1")

    assert len(banks) == 1
    assert provider.has_candidates is True
    assert provider.available_versions == ("diagnostic-text.v1",)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_id == "test.placeholder"
    assert candidate.content == {"prompt": "Contenido sintético exclusivo de prueba."}
    assert candidate.expected_answer == {
        "response_type": "single_choice",
        "rubric_version": "deterministic.v1",
        "accepted_responses": ["alpha"],
    }
    assert candidate.rubric.accepted_answers == ["alpha"]


def test_empty_or_absent_directory_has_no_candidates(tmp_path):
    absent = FilesystemCandidateProvider.from_directory(tmp_path / "absent")
    empty = FilesystemCandidateProvider.from_directory(tmp_path)

    assert absent.has_candidates is False
    assert empty.has_candidates is False
    with pytest.raises(CandidateUnavailableError):
        empty.candidates(diagnostic_version="diagnostic-text.v1")


@pytest.mark.parametrize(
    "payload",
    [
        {**task_payload(), "unexpected": True},
        {**task_payload(), "difficulty": 0},
        {key: value for key, value in task_payload().items() if key != "metadata"},
        {
            **task_payload(),
            "expected_response_type": "single_choice",
            "rubric": {"strategy": "ordered_tokens", "expected_tokens": ["a", "b"]},
        },
        {
            **task_payload(),
            "rubric": {"strategy": "exact_match", "accepted_responses": ["gamma"]},
        },
    ],
)
def test_task_contract_rejects_unknown_missing_or_incompatible_fields(payload):
    with pytest.raises(ValidationError):
        DiagnosticTaskDefinition.model_validate(payload)


def test_loader_rejects_malformed_json_and_unknown_bank_fields(tmp_path):
    (tmp_path / "broken.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises(DiagnosticContentFormatError):
        load_diagnostic_banks(tmp_path)

    (tmp_path / "broken.json").write_text(
        json.dumps({**bank_payload(), "unexpected": True}),
        encoding="utf-8",
    )
    with pytest.raises(DiagnosticContentFormatError):
        load_diagnostic_banks(tmp_path)


def test_loader_rejects_duplicate_ids_and_missing_prerequisites(tmp_path):
    write_bank(tmp_path, "one.json", bank_payload(task_payload("test.one")))
    write_bank(tmp_path, "two.json", bank_payload(task_payload("test.one")))
    with pytest.raises(DiagnosticContentFormatError, match="duplicate task id"):
        load_diagnostic_banks(tmp_path)

    (tmp_path / "two.json").unlink()
    dependent = task_payload(
        "test.dependent",
        metadata={"prerequisite_task_ids": ["test.absent"]},
    )
    write_bank(tmp_path, "one.json", bank_payload(dependent))
    with pytest.raises(DiagnosticContentFormatError, match="prerequisite"):
        load_diagnostic_banks(tmp_path)


def test_provider_orders_candidates_reproducibly_and_filters_versions(tmp_path):
    write_bank(
        tmp_path,
        "z-bank.json",
        bank_payload(task_payload("test.zulu"), task_payload("test.alpha")),
    )
    write_bank(
        tmp_path,
        "other-version.json",
        bank_payload(
            task_payload("test.other"),
            diagnostic_version="diagnostic-text.v2",
        ),
    )
    provider = FilesystemCandidateProvider.from_directory(tmp_path)

    assert [
        candidate.candidate_id
        for candidate in provider.candidates(diagnostic_version="diagnostic-text.v1")
    ] == ["test.alpha", "test.zulu"]
    assert [
        candidate.candidate_id
        for candidate in provider.candidates(diagnostic_version="diagnostic-text.v2")
    ] == ["test.other"]
    with pytest.raises(CandidateUnavailableError):
        provider.candidates(diagnostic_version="diagnostic-text.absent")


def test_production_dependency_keeps_exact_503_for_an_empty_bank(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic_api, "DIAGNOSTIC_CONTENT_DIRECTORY", tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        diagnostic_api.get_diagnostic_candidate_provider()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == (
        "El banco diagnóstico versionado no está configurado en esta instalación."
    )


def test_production_dependency_rejects_an_invalid_bank(tmp_path, monkeypatch):
    (tmp_path / "invalid.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(diagnostic_api, "DIAGNOSTIC_CONTENT_DIRECTORY", tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        diagnostic_api.get_diagnostic_candidate_provider()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "El banco diagnóstico local no supera la validación."
    assert "invalid.json" not in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_http_api_uses_a_validated_disk_provider(tmp_path, monkeypatch, client):
    write_bank(tmp_path, "reading.json", bank_payload(task_payload()))
    monkeypatch.setattr(diagnostic_api, "DIAGNOSTIC_CONTENT_DIRECTORY", tmp_path)

    created = await client.post(
        "/api/diagnostic/sessions",
        json={
            "request_id": "c6144f12-eb3a-4c18-b093-cad002db74a1",
            "curriculum_version": "a0-a1.v1",
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    started = await client.post(
        f"/api/diagnostic/sessions/{session_id}/start",
        json={"operation_id": "6127df2b-f89c-4624-8e13-d5e486f93727"},
    )
    selected = await client.post(
        f"/api/diagnostic/sessions/{session_id}/next-task",
        json={"operation_id": "5177b943-8db0-432d-a0d3-6cbb091e02f8"},
    )

    assert started.status_code == 200
    assert selected.status_code == 200
    assert selected.json()["task"]["content"] == {
        "prompt": "Contenido sintético exclusivo de prueba."
    }
    assert "expected_answer" not in selected.text


def test_repository_diagnostic_directory_contains_no_task_bank():
    assert load_diagnostic_banks(diagnostic_api.DIAGNOSTIC_CONTENT_DIRECTORY) == ()
