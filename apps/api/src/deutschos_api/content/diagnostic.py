"""Strict JSON format and disk-backed provider for diagnostic task banks."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, ValidationError, model_validator

from deutschos_api.diagnostic_engine.exceptions import CandidateUnavailableError
from deutschos_api.diagnostic_engine.schemas import (
    AmbiguityRisk,
    DeterministicRubric,
    RubricStrategy,
    TaskCandidate,
)
from deutschos_api.models import DiagnosticAxis, DiagnosticTaskType
from deutschos_api.schemas.base import APIModel

DIAGNOSTIC_BANK_SCHEMA_VERSION = "diagnostic-task-bank.v1"
MAX_BANK_BYTES = 2 * 1024 * 1024
_STABLE_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]+$"


class DiagnosticContentError(RuntimeError):
    """Base error for local pedagogical content that cannot be used safely."""


class DiagnosticContentFormatError(DiagnosticContentError):
    """A versioned JSON bank is malformed or internally inconsistent."""


class ExpectedResponseType(StrEnum):
    SINGLE_CHOICE = "single_choice"
    SHORT_TEXT = "short_text"
    ORDERED_TOKENS = "ordered_tokens"
    FREE_TEXT = "free_text"


class DiagnosticTaskMetadata(APIModel):
    """Selection metadata; it is never copied into learner-visible content."""

    equivalence_key: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        pattern=_STABLE_ID_PATTERN,
    )
    prerequisite_task_ids: list[str] = Field(default_factory=list, max_length=20)
    ambiguity_risk: AmbiguityRisk = AmbiguityRisk.LOW
    tags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def values_are_unique(self):
        if len(set(self.prerequisite_task_ids)) != len(self.prerequisite_task_ids):
            raise ValueError("prerequisite_task_ids must be unique")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("metadata tags must be unique")
        return self


class DiagnosticRubricDefinition(APIModel):
    strategy: RubricStrategy
    accepted_responses: list[str] = Field(default_factory=list, max_length=50)
    partial_responses: list[str] = Field(default_factory=list, max_length=50)
    expected_tokens: list[str] = Field(default_factory=list, max_length=100)
    case_sensitive: bool = False

    def to_engine_rubric(self) -> DeterministicRubric:
        return DeterministicRubric(
            strategy=self.strategy,
            accepted_answers=self.accepted_responses,
            partial_answers=self.partial_responses,
            expected_tokens=self.expected_tokens,
            case_sensitive=self.case_sensitive,
        )

    @model_validator(mode="after")
    def engine_rubric_is_valid(self):
        self.to_engine_rubric()
        return self


class DiagnosticTaskDefinition(APIModel):
    id: str = Field(min_length=2, max_length=100, pattern=_STABLE_ID_PATTERN)
    version: str = Field(min_length=1, max_length=50)
    axis: DiagnosticAxis
    secondary_axes: list[DiagnosticAxis] = Field(default_factory=list, max_length=8)
    skill_id: int | None = Field(ge=1)
    task_type: DiagnosticTaskType
    difficulty: int = Field(ge=1, le=5)
    prompt: str = Field(min_length=1, max_length=10_000)
    expected_response_type: ExpectedResponseType
    rubric_version: str = Field(min_length=1, max_length=50)
    metadata: DiagnosticTaskMetadata
    estimated_seconds: int = Field(ge=5, le=600)
    options: list[str] = Field(default_factory=list, max_length=50)
    rubric: DiagnosticRubricDefinition

    @model_validator(mode="after")
    def response_contract_matches_rubric(self):
        if self.id in self.metadata.prerequisite_task_ids:
            raise ValueError("a task cannot require itself")
        allowed_strategies = {
            ExpectedResponseType.SINGLE_CHOICE: {
                RubricStrategy.EXACT_MATCH,
                RubricStrategy.ACCEPTED_ANSWERS,
            },
            ExpectedResponseType.SHORT_TEXT: {
                RubricStrategy.EXACT_MATCH,
                RubricStrategy.ACCEPTED_ANSWERS,
            },
            ExpectedResponseType.ORDERED_TOKENS: {RubricStrategy.ORDERED_TOKENS},
            ExpectedResponseType.FREE_TEXT: {RubricStrategy.MANUAL_ONLY},
        }
        if self.rubric.strategy not in allowed_strategies[self.expected_response_type]:
            raise ValueError("expected response type is incompatible with rubric strategy")
        if self.expected_response_type == ExpectedResponseType.SINGLE_CHOICE:
            if len(self.options) < 2 or len(set(self.options)) != len(self.options):
                raise ValueError("single_choice tasks require at least two unique options")
            scored_options = {
                *self.rubric.accepted_responses,
                *self.rubric.partial_responses,
            }
            if not scored_options.issubset(self.options):
                raise ValueError("single_choice rubric responses must exist in options")
        elif self.options:
            raise ValueError("only single_choice tasks may define options")
        return self

    def to_candidate(self) -> TaskCandidate:
        engine_rubric = self.rubric.to_engine_rubric()
        expected_answer: dict[str, JsonValue] = {
            "response_type": self.expected_response_type.value,
            "rubric_version": self.rubric_version,
        }
        if self.rubric.accepted_responses:
            expected_answer["accepted_responses"] = list(self.rubric.accepted_responses)
        if self.rubric.partial_responses:
            expected_answer["partial_responses"] = list(self.rubric.partial_responses)
        if self.rubric.expected_tokens:
            expected_answer["expected_tokens"] = list(self.rubric.expected_tokens)
        return TaskCandidate(
            candidate_id=self.id,
            version=self.version,
            equivalence_key=self.metadata.equivalence_key or self.id,
            axis=self.axis,
            secondary_axes=self.secondary_axes,
            task_type=self.task_type,
            difficulty=self.difficulty,
            prerequisite_candidate_ids=self.metadata.prerequisite_task_ids,
            modality="text",
            content={"prompt": self.prompt},
            options=list(self.options),
            expected_answer=expected_answer,
            rubric=engine_rubric,
            auto_evaluable=engine_rubric.strategy != RubricStrategy.MANUAL_ONLY,
            ambiguity_risk=self.metadata.ambiguity_risk,
            estimated_seconds=self.estimated_seconds,
            skill_id=self.skill_id,
        )


class DiagnosticTaskBank(APIModel):
    schema_version: Literal["diagnostic-task-bank.v1"] = DIAGNOSTIC_BANK_SCHEMA_VERSION
    diagnostic_version: str = Field(min_length=1, max_length=50)
    bank_version: str = Field(min_length=1, max_length=50)
    tasks: list[DiagnosticTaskDefinition] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def task_ids_are_unique(self):
        task_ids = [task.id for task in self.tasks]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("task ids must be unique inside a diagnostic bank")
        return self


def _read_bank(path: Path) -> DiagnosticTaskBank:
    try:
        if path.stat().st_size > MAX_BANK_BYTES:
            raise DiagnosticContentFormatError(f"{path.name}: bank file exceeds size limit")
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        return DiagnosticTaskBank.model_validate(payload)
    except DiagnosticContentFormatError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise DiagnosticContentFormatError(f"{path.name}: invalid diagnostic bank") from exc


def load_diagnostic_banks(directory: Path) -> tuple[DiagnosticTaskBank, ...]:
    """Load top-level JSON banks in stable filename order; an absent directory is empty."""

    if not directory.exists():
        return ()
    if not directory.is_dir():
        raise DiagnosticContentFormatError("diagnostic content path is not a directory")
    banks = tuple(_read_bank(path) for path in sorted(directory.glob("*.json")))
    tasks_by_version: dict[str, set[str]] = defaultdict(set)
    for bank in banks:
        known_ids = tasks_by_version[bank.diagnostic_version]
        for task in bank.tasks:
            if task.id in known_ids:
                raise DiagnosticContentFormatError("duplicate task id in one diagnostic version")
            known_ids.add(task.id)
    for bank in banks:
        known_ids = tasks_by_version[bank.diagnostic_version]
        for task in bank.tasks:
            if not set(task.metadata.prerequisite_task_ids).issubset(known_ids):
                raise DiagnosticContentFormatError(
                    "task prerequisite is absent from its diagnostic version"
                )
    return banks


class FilesystemCandidateProvider:
    """Immutable CandidateProvider built from validated local JSON files."""

    def __init__(self, candidates_by_version: dict[str, Sequence[TaskCandidate]]) -> None:
        self._candidates_by_version = {
            version: tuple(sorted(candidates, key=lambda item: item.stable_key))
            for version, candidates in candidates_by_version.items()
        }

    @classmethod
    def from_directory(cls, directory: Path) -> FilesystemCandidateProvider:
        candidates_by_version: dict[str, list[TaskCandidate]] = defaultdict(list)
        for bank in load_diagnostic_banks(directory):
            try:
                candidates_by_version[bank.diagnostic_version].extend(
                    task.to_candidate() for task in bank.tasks
                )
            except ValidationError as exc:
                raise DiagnosticContentFormatError(
                    "diagnostic task is incompatible with the engine contract"
                ) from exc
        return cls(candidates_by_version)

    @property
    def has_candidates(self) -> bool:
        return any(self._candidates_by_version.values())

    @property
    def available_versions(self) -> tuple[str, ...]:
        return tuple(sorted(self._candidates_by_version))

    def candidates(self, *, diagnostic_version: str) -> Sequence[TaskCandidate]:
        candidates = self._candidates_by_version.get(diagnostic_version, ())
        if not candidates:
            raise CandidateUnavailableError(
                "No validated tasks exist for the requested diagnostic version."
            )
        return candidates


__all__ = [
    "DIAGNOSTIC_BANK_SCHEMA_VERSION",
    "DiagnosticContentError",
    "DiagnosticContentFormatError",
    "DiagnosticRubricDefinition",
    "DiagnosticTaskBank",
    "DiagnosticTaskDefinition",
    "DiagnosticTaskMetadata",
    "ExpectedResponseType",
    "FilesystemCandidateProvider",
    "load_diagnostic_banks",
]
