"""Strict manifests, task files, and disk-backed diagnostic candidates."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator

from deutschos_api.core.version import APPLICATION_VERSION
from deutschos_api.diagnostic_engine.exceptions import CandidateUnavailableError
from deutschos_api.diagnostic_engine.schemas import (
    DIAGNOSTIC_ENGINE_VERSION,
    TEXT_AXES,
    AmbiguityRisk,
    DeterministicRubric,
    RubricStrategy,
    TaskCandidate,
)
from deutschos_api.learning_engine.curriculum import CURRICULUM_VERSION
from deutschos_api.models import DiagnosticAxis, DiagnosticTaskType
from deutschos_api.schemas.base import APIModel

DIAGNOSTIC_BANK_SCHEMA_VERSION = "diagnostic-bank-manifest.v1"
DIAGNOSTIC_TASK_FILE_SCHEMA_VERSION = "diagnostic-task-file.v1"
MAX_CONTENT_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_CONTENT_BYTES = 16 * 1024 * 1024
MAX_DECLARED_FILES = 100
_STABLE_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]+$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class DiagnosticContentError(RuntimeError):
    """Base error for local pedagogical content that cannot be used safely."""


class DiagnosticContentFormatError(DiagnosticContentError):
    """A versioned JSON bank is malformed or internally inconsistent."""


class ExpectedResponseType(StrEnum):
    SINGLE_CHOICE = "single_choice"
    SHORT_TEXT = "short_text"
    ORDERED_TOKENS = "ordered_tokens"
    FREE_TEXT = "free_text"


class EditorialStatus(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    PRODUCTION = "production"
    DEPRECATED = "deprecated"


class ContentLevel(StrEnum):
    PRE_A1 = "pre-A1"
    A1 = "A1"


class ScoringMode(StrEnum):
    DETERMINISTIC = "deterministic"
    MANUAL = "manual"


class MinimumCompatibility(APIModel):
    application_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    diagnostic_engine_version: Literal["diagnostic-engine.v1"] = DIAGNOSTIC_ENGINE_VERSION
    curriculum_version: Literal["a0-a1.v1"] = CURRICULUM_VERSION

    @model_validator(mode="after")
    def application_is_compatible(self):
        if _semantic_version(self.application_version) > _semantic_version(APPLICATION_VERSION):
            raise ValueError("bank requires a newer DeutschOS application version")
        return self


class DiagnosticContentFileReference(APIModel):
    path: str = Field(min_length=1, max_length=200)
    sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @field_validator("path")
    @classmethod
    def path_is_local_and_portable(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("declared paths must use portable forward slashes")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("declared path must stay inside the diagnostic directory")
        if not value.endswith(".tasks.json"):
            raise ValueError("declared task files must end in .tasks.json")
        return value


class DiagnosticBankManifest(APIModel):
    schema_version: Literal["diagnostic-bank-manifest.v1"] = DIAGNOSTIC_BANK_SCHEMA_VERSION
    bank_id: str = Field(min_length=2, max_length=100, pattern=_STABLE_ID_PATTERN)
    bank_version: str = Field(min_length=1, max_length=50)
    diagnostic_version: str = Field(min_length=1, max_length=50)
    target_language: Literal["de"]
    levels: list[ContentLevel] = Field(min_length=1, max_length=2)
    modalities: list[Literal["text"]] = Field(min_length=1, max_length=1)
    axes: list[DiagnosticAxis] = Field(min_length=1, max_length=len(TEXT_AXES))
    editorial_status: EditorialStatus
    created_on: date
    updated_on: date
    files: list[DiagnosticContentFileReference] = Field(
        default_factory=list,
        max_length=MAX_DECLARED_FILES,
    )
    minimum_compatibility: MinimumCompatibility
    editorial_notes: str = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def declarations_are_coherent(self):
        if self.updated_on < self.created_on:
            raise ValueError("manifest updated_on cannot precede created_on")
        if len(set(self.levels)) != len(self.levels):
            raise ValueError("manifest levels must be unique")
        if self.modalities != ["text"]:
            raise ValueError("diagnostic bank v1 supports only text modality")
        if len(set(self.axes)) != len(self.axes):
            raise ValueError("manifest axes must be unique")
        if any(axis not in TEXT_AXES for axis in self.axes):
            raise ValueError("manifest may declare only text diagnostic axes")
        paths = [reference.path for reference in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("manifest file paths must be unique")
        if self.editorial_status == EditorialStatus.PRODUCTION:
            if not self.files:
                raise ValueError("production banks must declare at least one task file")
            if any(reference.sha256 is None for reference in self.files):
                raise ValueError("production task files require SHA-256 checksums")
        return self


class DiagnosticTaskMetadata(APIModel):
    topic: str | None = Field(default=None, max_length=100)
    context: str | None = Field(default=None, max_length=500)


class DiagnosticEditorialFields(APIModel):
    tags: list[str] = Field(default_factory=list, max_length=20)
    authoring_notes: str = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def tags_are_unique(self):
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("editorial tags must be unique")
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


class DiagnosticNormalizationPolicy(APIModel):
    unicode_form: Literal["NFC"]
    trim_outer_whitespace: Literal[True]
    collapse_internal_whitespace: Literal[True]
    case_sensitive: bool
    punctuation: Literal["significant"]


class DiagnosticScoringPolicy(APIModel):
    normalization: DiagnosticNormalizationPolicy
    maximum_score: Literal[1.0]
    correct_condition: str = Field(min_length=1, max_length=1000)
    incorrect_condition: str = Field(min_length=1, max_length=1000)


class DiagnosticPublicFields(APIModel):
    instructions: str = Field(min_length=1, max_length=5000)
    prompt: str = Field(min_length=1, max_length=10_000)
    options: list[str] = Field(default_factory=list, max_length=50)


class DiagnosticPrivateFields(APIModel):
    rubric: DiagnosticRubricDefinition
    scoring_policy: DiagnosticScoringPolicy


class DiagnosticTaskDefinition(APIModel):
    id: str = Field(min_length=2, max_length=100, pattern=_STABLE_ID_PATTERN)
    version: str = Field(min_length=1, max_length=50)
    level: ContentLevel
    axis: DiagnosticAxis
    secondary_axes: list[DiagnosticAxis] = Field(default_factory=list, max_length=8)
    skill_id: int | None = Field(ge=1)
    task_type: DiagnosticTaskType
    difficulty: int = Field(ge=1, le=5)
    modality: Literal["text"]
    response_type: ExpectedResponseType
    estimated_seconds: int = Field(ge=5, le=600)
    prerequisites: list[str] = Field(max_length=20)
    equivalence_group: str = Field(
        min_length=2,
        max_length=100,
        pattern=_STABLE_ID_PATTERN,
    )
    ambiguity_risk: AmbiguityRisk
    scoring_mode: ScoringMode
    rubric_version: str = Field(min_length=1, max_length=50)
    public: DiagnosticPublicFields
    private: DiagnosticPrivateFields
    metadata: DiagnosticTaskMetadata
    editorial: DiagnosticEditorialFields

    @model_validator(mode="after")
    def task_contract_is_coherent(self):
        if self.id in self.prerequisites:
            raise ValueError("a task cannot require itself")
        if len(set(self.prerequisites)) != len(self.prerequisites):
            raise ValueError("task prerequisites must be unique")
        if self.axis in self.secondary_axes or len(set(self.secondary_axes)) != len(
            self.secondary_axes
        ):
            raise ValueError("task axes must be unique")
        if any(axis not in TEXT_AXES for axis in (self.axis, *self.secondary_axes)):
            raise ValueError("diagnostic bank v1 tasks may use only text axes")

        rubric = self.private.rubric
        scoring_policy = self.private.scoring_policy
        if scoring_policy.normalization.case_sensitive != rubric.case_sensitive:
            raise ValueError("scoring policy case sensitivity must match the engine rubric")
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
        if rubric.strategy not in allowed_strategies[self.response_type]:
            raise ValueError("response_type is incompatible with private rubric strategy")
        if self.scoring_mode == ScoringMode.DETERMINISTIC:
            if rubric.strategy == RubricStrategy.MANUAL_ONLY:
                raise ValueError("deterministic scoring requires an evaluable rubric")
        elif rubric.strategy != RubricStrategy.MANUAL_ONLY:
            raise ValueError("manual scoring must use the manual_only rubric strategy")

        options = self.public.options
        if self.response_type == ExpectedResponseType.SINGLE_CHOICE:
            if len(options) < 2 or len(set(options)) != len(options):
                raise ValueError("single_choice tasks require at least two unique options")
            scored_options = {*rubric.accepted_responses, *rubric.partial_responses}
            if not scored_options.issubset(options):
                raise ValueError("single_choice rubric responses must exist in public options")
        elif options:
            raise ValueError("only single_choice tasks may define public options")
        return self

    def to_candidate(self) -> TaskCandidate:
        rubric = self.private.rubric.to_engine_rubric()
        expected_answer: dict[str, JsonValue] = {
            "response_type": self.response_type.value,
            "rubric_version": self.rubric_version,
            "scoring_mode": self.scoring_mode.value,
        }
        if rubric.accepted_answers:
            expected_answer["accepted_responses"] = list(rubric.accepted_answers)
        if rubric.partial_answers:
            expected_answer["partial_responses"] = list(rubric.partial_answers)
        if rubric.expected_tokens:
            expected_answer["expected_tokens"] = list(rubric.expected_tokens)
        return TaskCandidate(
            candidate_id=self.id,
            version=self.version,
            equivalence_key=self.equivalence_group,
            axis=self.axis,
            secondary_axes=self.secondary_axes,
            task_type=self.task_type,
            difficulty=self.difficulty,
            prerequisite_candidate_ids=self.prerequisites,
            modality="text",
            content={
                "instructions": self.public.instructions,
                "prompt": self.public.prompt,
            },
            options=list(self.public.options),
            expected_answer=expected_answer,
            rubric=rubric,
            auto_evaluable=self.scoring_mode == ScoringMode.DETERMINISTIC,
            ambiguity_risk=self.ambiguity_risk,
            estimated_seconds=self.estimated_seconds,
            skill_id=self.skill_id,
        )


class DiagnosticTaskFile(APIModel):
    schema_version: Literal["diagnostic-task-file.v1"] = DIAGNOSTIC_TASK_FILE_SCHEMA_VERSION
    bank_id: str = Field(min_length=2, max_length=100, pattern=_STABLE_ID_PATTERN)
    tasks: list[DiagnosticTaskDefinition] = Field(default_factory=list, max_length=1000)


class DiagnosticTaskBank(APIModel):
    """Fully validated in-memory bank assembled atomically from declared files."""

    manifest_file: str
    manifest: DiagnosticBankManifest
    tasks: tuple[DiagnosticTaskDefinition, ...]


def _semantic_version(value: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("version must use major.minor.patch numeric format")
    return tuple(int(part) for part in match.groups())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validation_detail(path: Path, exc: ValidationError) -> str:
    error = exc.errors(include_url=False)[0]
    location = ".".join(str(part) for part in error["loc"]) or "document"
    return f"{path.name}:{location}: {error['msg']}"


def _authorised_file(root: Path, path: Path, *, location: str) -> Path:
    if path.is_symlink():
        raise DiagnosticContentFormatError(f"{location}: symbolic links are not allowed")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise DiagnosticContentFormatError(f"{location}: declared file does not exist") from exc
    if not resolved.is_relative_to(root):
        raise DiagnosticContentFormatError(f"{location}: file escapes diagnostic directory")
    if not resolved.is_file():
        raise DiagnosticContentFormatError(f"{location}: declared path is not a file")
    return resolved


def _read_model[T: APIModel](
    root: Path, path: Path, model: type[T], *, location: str
) -> tuple[T, int]:
    resolved = _authorised_file(root, path, location=location)
    try:
        size = resolved.stat().st_size
        if size > MAX_CONTENT_FILE_BYTES:
            raise DiagnosticContentFormatError(f"{path.name}: file exceeds 2 MiB limit")
        raw = resolved.read_text(encoding="utf-8")
        payload = json.loads(raw)
        return model.model_validate(payload), size
    except DiagnosticContentFormatError:
        raise
    except json.JSONDecodeError as exc:
        raise DiagnosticContentFormatError(
            f"{path.name}:{exc.lineno}:{exc.colno}: malformed JSON"
        ) from exc
    except ValidationError as exc:
        raise DiagnosticContentFormatError(_validation_detail(path, exc)) from exc
    except (OSError, UnicodeError) as exc:
        raise DiagnosticContentFormatError(f"{path.name}: file is not readable UTF-8") from exc


def _validate_bank_relations(
    bank: DiagnosticTaskBank,
    *,
    curriculum_skill_ids: Mapping[str, set[int]] | None,
) -> None:
    manifest = bank.manifest
    tasks = bank.tasks
    if manifest.editorial_status == EditorialStatus.PRODUCTION:
        if not tasks:
            raise DiagnosticContentFormatError(
                f"{bank.manifest_file}: production bank has no tasks"
            )
        if any(task.scoring_mode != ScoringMode.DETERMINISTIC for task in tasks):
            raise DiagnosticContentFormatError(
                f"{bank.manifest_file}: production bank contains non-deterministic tasks"
            )
    if tasks:
        task_axes = {axis for task in tasks for axis in (task.axis, *task.secondary_axes)}
        task_levels = {task.level for task in tasks}
        task_modalities = {task.modality for task in tasks}
        if task_axes != set(manifest.axes):
            raise DiagnosticContentFormatError(
                f"{bank.manifest_file}: manifest axes do not match task axes"
            )
        if task_levels != set(manifest.levels):
            raise DiagnosticContentFormatError(
                f"{bank.manifest_file}: manifest levels do not match task levels"
            )
        if task_modalities != set(manifest.modalities):
            raise DiagnosticContentFormatError(
                f"{bank.manifest_file}: manifest modalities do not match task modalities"
            )

    known_ids = {task.id for task in tasks}
    for task in tasks:
        missing = set(task.prerequisites) - known_ids
        if missing:
            raise DiagnosticContentFormatError(
                f"{bank.manifest_file}: task {task.id} has undeclared prerequisites"
            )
        if task.skill_id is not None:
            curriculum_version = manifest.minimum_compatibility.curriculum_version
            if curriculum_skill_ids is None:
                raise DiagnosticContentFormatError(
                    f"{bank.manifest_file}: curriculum skill validation is required"
                )
            if task.skill_id not in curriculum_skill_ids.get(curriculum_version, set()):
                raise DiagnosticContentFormatError(
                    f"{bank.manifest_file}: task {task.id} references unknown skill_id "
                    f"{task.skill_id}"
                )


def load_diagnostic_banks(
    directory: Path,
    *,
    curriculum_skill_ids: Mapping[str, set[int]] | None = None,
) -> tuple[DiagnosticTaskBank, ...]:
    """Load every declared bank atomically; absent or empty directories are valid."""

    if not directory.exists():
        return ()
    if not directory.is_dir():
        raise DiagnosticContentFormatError("diagnostic content path is not a directory")
    root = directory.resolve(strict=True)
    manifest_paths = sorted(directory.glob("*.bank.json"))
    all_json_paths = sorted(directory.rglob("*.json"))
    if all_json_paths and not manifest_paths:
        raise DiagnosticContentFormatError(
            f"{all_json_paths[0].name}: JSON content is not declared by a bank manifest"
        )

    loaded: list[DiagnosticTaskBank] = []
    declared_resolved: set[Path] = set()
    bank_ids: set[str] = set()
    total_bytes = 0
    for manifest_path in manifest_paths:
        manifest, size = _read_model(
            root,
            manifest_path,
            DiagnosticBankManifest,
            location=manifest_path.name,
        )
        total_bytes += size
        if total_bytes > MAX_TOTAL_CONTENT_BYTES:
            raise DiagnosticContentFormatError("diagnostic bank exceeds 16 MiB total size limit")
        declared_resolved.add(manifest_path.resolve())
        if manifest.bank_id in bank_ids:
            raise DiagnosticContentFormatError(
                f"{manifest_path.name}: duplicate bank_id {manifest.bank_id}"
            )
        bank_ids.add(manifest.bank_id)

        tasks: list[DiagnosticTaskDefinition] = []
        task_ids: set[str] = set()
        for index, reference in enumerate(manifest.files):
            task_path = directory / PurePosixPath(reference.path)
            location = f"{manifest_path.name}:files.{index}.path"
            resolved = _authorised_file(root, task_path, location=location)
            if resolved in declared_resolved:
                raise DiagnosticContentFormatError(f"{location}: file is declared more than once")
            declared_resolved.add(resolved)
            task_file, task_size = _read_model(
                root,
                task_path,
                DiagnosticTaskFile,
                location=location,
            )
            total_bytes += task_size
            if total_bytes > MAX_TOTAL_CONTENT_BYTES:
                raise DiagnosticContentFormatError(
                    "diagnostic bank exceeds 16 MiB total size limit"
                )
            if reference.sha256 is not None and file_sha256(resolved) != reference.sha256:
                raise DiagnosticContentFormatError(f"{location}: SHA-256 checksum mismatch")
            if task_file.bank_id != manifest.bank_id:
                raise DiagnosticContentFormatError(
                    f"{reference.path}:bank_id: does not match manifest bank_id"
                )
            for task in task_file.tasks:
                if task.id in task_ids:
                    raise DiagnosticContentFormatError(
                        f"{reference.path}: duplicate task id {task.id}"
                    )
                task_ids.add(task.id)
                tasks.append(task)

        bank = DiagnosticTaskBank(
            manifest_file=manifest_path.name,
            manifest=manifest,
            tasks=tuple(tasks),
        )
        _validate_bank_relations(bank, curriculum_skill_ids=curriculum_skill_ids)
        loaded.append(bank)

    undeclared = [path for path in all_json_paths if path.resolve() not in declared_resolved]
    if undeclared:
        raise DiagnosticContentFormatError(
            f"{undeclared[0].name}: JSON file is not declared by any bank manifest"
        )

    tasks_by_version: dict[str, dict[str, DiagnosticTaskDefinition]] = defaultdict(dict)
    equivalence_dimensions: dict[tuple[str, str], tuple[DiagnosticAxis, int | None]] = {}
    for bank in loaded:
        diagnostic_version = bank.manifest.diagnostic_version
        for task in bank.tasks:
            if task.id in tasks_by_version[diagnostic_version]:
                raise DiagnosticContentFormatError(
                    f"{bank.manifest_file}: duplicate task id {task.id} in diagnostic version"
                )
            tasks_by_version[diagnostic_version][task.id] = task
            group_key = (diagnostic_version, task.equivalence_group)
            dimension = (task.axis, task.skill_id)
            previous = equivalence_dimensions.setdefault(group_key, dimension)
            if previous != dimension:
                raise DiagnosticContentFormatError(
                    f"{bank.manifest_file}: equivalence group {task.equivalence_group} "
                    "mixes diagnostic dimensions"
                )
    return tuple(loaded)


class FilesystemCandidateProvider:
    """Immutable CandidateProvider exposing production banks only by default."""

    def __init__(self, candidates_by_version: Mapping[str, Sequence[TaskCandidate]]) -> None:
        self._candidates_by_version = {
            version: tuple(sorted(candidates, key=lambda item: item.stable_key))
            for version, candidates in candidates_by_version.items()
        }

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        curriculum_skill_ids: Mapping[str, set[int]] | None = None,
        production_only: bool = True,
    ) -> FilesystemCandidateProvider:
        candidates_by_version: dict[str, list[TaskCandidate]] = defaultdict(list)
        for bank in load_diagnostic_banks(
            directory,
            curriculum_skill_ids=curriculum_skill_ids,
        ):
            if production_only and bank.manifest.editorial_status != EditorialStatus.PRODUCTION:
                continue
            if bank.manifest.editorial_status == EditorialStatus.DEPRECATED:
                continue
            try:
                candidates_by_version[bank.manifest.diagnostic_version].extend(
                    task.to_candidate() for task in bank.tasks
                )
            except ValidationError as exc:
                raise DiagnosticContentFormatError(
                    f"{bank.manifest_file}: task is incompatible with engine contract"
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
                "No production tasks exist for the requested diagnostic version."
            )
        return candidates


__all__ = [
    "DIAGNOSTIC_BANK_SCHEMA_VERSION",
    "DIAGNOSTIC_TASK_FILE_SCHEMA_VERSION",
    "ContentLevel",
    "DiagnosticBankManifest",
    "DiagnosticContentError",
    "DiagnosticContentFileReference",
    "DiagnosticContentFormatError",
    "DiagnosticEditorialFields",
    "DiagnosticPrivateFields",
    "DiagnosticPublicFields",
    "DiagnosticNormalizationPolicy",
    "DiagnosticRubricDefinition",
    "DiagnosticScoringPolicy",
    "DiagnosticTaskBank",
    "DiagnosticTaskDefinition",
    "DiagnosticTaskFile",
    "DiagnosticTaskMetadata",
    "EditorialStatus",
    "ExpectedResponseType",
    "FilesystemCandidateProvider",
    "MinimumCompatibility",
    "ScoringMode",
    "file_sha256",
    "load_diagnostic_banks",
]
