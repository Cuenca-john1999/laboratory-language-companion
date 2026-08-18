from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from llc_api.core.config import Settings
from llc_api.educational_library.database import LibraryDatabase
from llc_api.educational_library.inventory import sha256_file
from llc_api.educational_library.runs import DocumentRunService
from llc_api.educational_library.schemas import (
    DocumentRunCreate,
    LibraryContractError,
    LibraryNotFoundError,
)
from llc_api.educational_library.service import json_dump, json_load, utc_text

from .models import (
    CanonicalStructure,
    CapabilityRequirements,
    CloudArtifactRead,
    CloudExtractionCreate,
    CloudExtractionMode,
    CloudExtractionRunRead,
    CompactContentTransport,
    CompactStructureTransport,
    ProviderAvailability,
    ProviderExtractionRequest,
    ProviderRawResponse,
    ProviderUsage,
    ValidationOutcome,
    ValidationReport,
)
from .normalization import expand_content, expand_structure, provenance
from .prompts import prompt_for
from .provider import CloudProviderError, CloudProviderRegistry
from .validation import validate_content, validate_structure

CANONICAL_SCHEMAS = {
    CloudExtractionMode.STRUCTURE: "llc.cloud-structure.v1",
    CloudExtractionMode.CONTENT: "llc.cloud-content.v1",
}


class CloudKnowledgeExtractionService:
    """Explicit cloud boundary over version-pinned LLC document runs."""

    def __init__(
        self,
        database: LibraryDatabase,
        materials_root: Path,
        runtime_root: Path,
        settings: Settings,
        registry: CloudProviderRegistry,
    ):
        self.database = database
        self.materials_root = materials_root
        self.runtime_root = runtime_root
        self.settings = settings
        self.registry = registry
        self.runs = DocumentRunService(database, materials_root)

    def providers(self):
        return self.registry.descriptors()

    def create_run(self, request: CloudExtractionCreate) -> CloudExtractionRunRead:
        selected_provider = (
            self.settings.cloud_knowledge_provider
            if request.provider == "auto" and self.settings.cloud_knowledge_provider != "auto"
            else request.provider
        )
        provider = self.registry.resolve(selected_provider, self._requirements())
        source = self._source_context(request.source_version_id)
        if source["format"].lower() != ".pdf":
            raise LibraryContractError("Cloud knowledge extraction currently accepts PDF sources.")
        pages = request.pages or self._known_pages(source)
        page_count = source["page_count"]
        if page_count is not None and any(page > int(page_count) for page in pages):
            raise LibraryContractError("A requested page is outside the registered PDF page count.")
        prompt, prompt_version, transport_version = prompt_for(request.mode, pages)
        model = request.model or provider.descriptor.model
        document_run = self.runs.create_run(
            DocumentRunCreate(
                source_version_id=request.source_version_id,
                run_type="cloud_knowledge_extraction",
                pipeline_version="cloud-knowledge.v1",
                configuration={
                    "provider": provider.descriptor.provider_id,
                    "model": model,
                    "mode": request.mode.value,
                    "prompt_version": prompt_version,
                    "transport_version": transport_version,
                    "canonical_schema_version": CANONICAL_SCHEMAS[request.mode],
                    "network_action": "explicit_only",
                },
                selection_strategy="explicit_pages" if request.pages else "all_pages",
                selected_pages=request.pages,
                initiated_by=request.initiated_by,
                reason=request.reason,
                exclusive=False,
            )
        )
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO cloud_extraction_runs("
                "run_id,provider_id,model,mode,prompt_version,transport_version,"
                "canonical_schema_version,requested_pages_json,provider_status,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    document_run.id,
                    provider.descriptor.provider_id,
                    model,
                    request.mode.value,
                    prompt_version,
                    transport_version,
                    CANONICAL_SCHEMAS[request.mode],
                    json_dump(pages),
                    ProviderAvailability.AVAILABLE.value,
                    now,
                    now,
                ),
            )
        return self.get_run(document_run.id)

    def get_run(self, run_id: str) -> CloudExtractionRunRead:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT c.*,r.source_id,r.source_version_id,r.state "
                "FROM cloud_extraction_runs c JOIN document_processing_runs r ON r.id=c.run_id "
                "WHERE c.run_id=?",
                (run_id,),
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("The cloud extraction run does not exist.")
            artifacts = connection.execute(
                "SELECT * FROM cloud_extraction_artifacts WHERE run_id=? ORDER BY created_at,kind",
                (run_id,),
            ).fetchall()
        usage = json_load(row["usage_json"], {})
        return CloudExtractionRunRead(
            id=row["run_id"],
            document_run_id=row["run_id"],
            source_id=row["source_id"],
            source_version_id=row["source_version_id"],
            provider=row["provider_id"],
            model=row["model"],
            mode=row["mode"],
            prompt_version=row["prompt_version"],
            transport_version=row["transport_version"],
            canonical_schema_version=row["canonical_schema_version"],
            pages=json_load(row["requested_pages_json"], []),
            state=row["state"],
            provider_status=row["provider_status"],
            validation_outcome=row["validation_outcome"],
            usage=ProviderUsage.model_validate(usage if isinstance(usage, dict) else {}),
            provider_metadata=self._dict(row["provider_metadata_json"]),
            normalized_error=row["normalized_error_state"],
            provider_error_code=row["provider_error_code"],
            provider_error_message=row["provider_error_message"],
            provider_error_details=self._dict(row["provider_error_details_json"]),
            artifacts=[self._artifact_read(item) for item in artifacts],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def execute(self, run_id: str) -> CloudExtractionRunRead:
        cloud = self.get_run(run_id)
        if cloud.state not in {"planned", "queued", "failed"}:
            raise LibraryContractError("Cloud extraction is not available for execution.")
        provider = self.registry.resolve(cloud.provider, self._requirements())
        source = self._source_context(cloud.source_version_id)
        target = self._safe_path(str(source["observed_path"] or source["current_path"]))
        prompt, _, _ = prompt_for(cloud.mode, cloud.pages)

        _, provider_stage_id, provider_job_id = self.runs._start_stage(run_id, "provider_request")
        if sha256_file(target) != source["content_hash"]:
            self.runs._fail_stage(
                run_id,
                provider_stage_id,
                provider_job_id,
                "target_hash_mismatch",
                stale=True,
            )
            return self.get_run(run_id)
        provider_request = ProviderExtractionRequest(
            run_id=run_id,
            source_id=cloud.source_id,
            source_version_id=cloud.source_version_id,
            document_path=target,
            mime_type="application/pdf",
            mode=cloud.mode,
            model=cloud.model,
            pages=cloud.pages,
            prompt=prompt,
            response_schema=self._transport_model(cloud.mode).model_json_schema(),
        )
        try:
            raw = provider.extract(provider_request)
        except CloudProviderError as exc:
            self._record_provider_error(run_id, exc)
            self.runs._fail_stage(
                run_id, provider_stage_id, provider_job_id, exc.code or exc.state.value
            )
            return self.get_run(run_id)
        except Exception as exc:
            error = CloudProviderError(
                ProviderAvailability.UNKNOWN,
                f"Unexpected provider failure ({type(exc).__name__}).",
                code="unexpected_provider_failure",
                details={"exception_type": type(exc).__name__},
            )
            self._record_provider_error(run_id, error)
            self.runs._fail_stage(run_id, provider_stage_id, provider_job_id, error.code or "unknown")
            return self.get_run(run_id)

        # Mandatory durability boundary: no Pydantic/transport parsing occurs before this write.
        try:
            self._save_artifact(
                run_id,
                "raw",
                {
                    "provider_text": raw.text,
                    "provider_response": raw.raw,
                    "usage": raw.usage.model_dump(mode="json"),
                    "provider_metadata": raw.provider_metadata,
                },
            )
        except (OSError, sqlite3.Error, LibraryContractError):
            self.runs._fail_stage(
                run_id,
                provider_stage_id,
                provider_job_id,
                "raw_artifact_persistence_failed",
            )
            return self.get_run(run_id)
        self._record_provider_success(run_id, raw)
        self.runs._complete_stage(
            run_id,
            provider_stage_id,
            provider_job_id,
            {"raw_artifact_saved": True, "usage": raw.usage.model_dump(mode="json")},
            with_issues=bool(
                raw.provider_metadata.get("remote_cleanup_error")
                or raw.provider_metadata.get("local_cleanup_error")
            ),
        )

        try:
            _, stage_id, job_id = self.runs._start_stage(run_id, "transport_validation")
            transport = self._transport_model(cloud.mode).model_validate_json(raw.text)
            self._save_artifact(run_id, "transport", transport.model_dump(mode="json"))
            self.runs._complete_stage(
                run_id, stage_id, job_id, {"transport_valid": True}, with_issues=False
            )
        except (ValidationError, ValueError, json.JSONDecodeError):
            self.runs._fail_stage(run_id, stage_id, job_id, "invalid_transport")
            self._set_validation_outcome(run_id, ValidationOutcome.FAIL)
            return self.get_run(run_id)
        except (OSError, sqlite3.Error, LibraryContractError):
            self.runs._fail_stage(run_id, stage_id, job_id, "transport_artifact_persistence_failed")
            self._set_validation_outcome(run_id, ValidationOutcome.FAIL)
            return self.get_run(run_id)

        try:
            _, stage_id, job_id = self.runs._start_stage(run_id, "canonical_normalization")
            metadata = provenance(
                run_id=run_id,
                provider=cloud.provider,
                model=cloud.model,
                mode=cloud.mode,
                prompt_version=cloud.prompt_version,
                transport_version=cloud.transport_version,
                source_id=cloud.source_id,
                source_version_id=cloud.source_version_id,
                pages=cloud.pages,
            )
            canonical = (
                expand_structure(transport, metadata)
                if isinstance(transport, CompactStructureTransport)
                else expand_content(transport, metadata)
            )
            self._save_artifact(run_id, "canonical", canonical.model_dump(mode="json"))
            self.runs._complete_stage(
                run_id, stage_id, job_id, {"canonical_saved": True}, with_issues=False
            )
        except (ValidationError, ValueError, KeyError):
            self.runs._fail_stage(run_id, stage_id, job_id, "canonical_normalization_failed")
            self._set_validation_outcome(run_id, ValidationOutcome.FAIL)
            return self.get_run(run_id)
        except (OSError, sqlite3.Error, LibraryContractError):
            self.runs._fail_stage(run_id, stage_id, job_id, "canonical_artifact_persistence_failed")
            self._set_validation_outcome(run_id, ValidationOutcome.FAIL)
            return self.get_run(run_id)

        _, stage_id, job_id = self.runs._start_stage(run_id, "local_validation")
        report = (
            validate_structure(canonical, cloud.pages)
            if isinstance(canonical, CanonicalStructure)
            else validate_content(canonical, cloud.pages)
        )
        try:
            self._save_artifact(run_id, "validation", report.model_dump(mode="json"))
            self._record_validation(run_id, report)
        except (OSError, sqlite3.Error, LibraryContractError):
            self.runs._fail_stage(run_id, stage_id, job_id, "validation_artifact_persistence_failed")
            self._set_validation_outcome(run_id, ValidationOutcome.FAIL)
            return self.get_run(run_id)
        self.runs._complete_stage(
            run_id,
            stage_id,
            job_id,
            {"validation_outcome": report.outcome.value, **report.metrics},
            with_issues=report.outcome != ValidationOutcome.PASS,
        )
        return self.get_run(run_id)

    def validation_report(self, run_id: str) -> ValidationReport:
        self.get_run(run_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT relative_path FROM cloud_extraction_artifacts "
                "WHERE run_id=? AND kind='validation'",
                (run_id,),
            ).fetchone()
        if not row:
            raise LibraryNotFoundError("This run has no validation report yet.")
        path = self._artifact_path(row["relative_path"])
        return ValidationReport.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _requirements() -> CapabilityRequirements:
        return CapabilityRequirements(
            supports_pdf=True,
            supports_images=True,
            supports_structured_output=True,
            supports_file_upload=True,
        )

    @staticmethod
    def _transport_model(mode: CloudExtractionMode):
        return (
            CompactStructureTransport
            if mode == CloudExtractionMode.STRUCTURE
            else CompactContentTransport
        )

    def _source_context(self, source_version_id: int) -> sqlite3.Row:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT sv.*,s.current_path,s.format FROM source_versions sv "
                "JOIN sources s ON s.id=sv.source_id WHERE sv.id=?",
                (source_version_id,),
            ).fetchone()
        if not row:
            raise LibraryNotFoundError("The source version does not exist.")
        return row

    def _known_pages(self, source: sqlite3.Row) -> list[int]:
        count = source["page_count"]
        if count is None:
            with self.database.connect() as connection:
                count = connection.execute(
                    "SELECT count(*) FROM document_pages WHERE source_version_id=?",
                    (source["id"],),
                ).fetchone()[0]
        return list(range(1, int(count) + 1)) if count else []

    def _safe_path(self, observed_path: str) -> Path:
        root = self.materials_root.resolve(strict=True)
        target = (root / observed_path).resolve(strict=True)
        if root not in target.parents or not target.is_file():
            raise LibraryContractError("The registered document path is unsafe or unavailable.")
        return target

    def _save_artifact(self, run_id: str, kind: str, payload: object) -> CloudArtifactRead:
        data = json_dump(payload).encode("utf-8")
        relative = Path("cloud-knowledge") / run_id / f"{kind}.json"
        target = self._artifact_path(str(relative))
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved_parent = target.parent.resolve(strict=True)
        root = self.runtime_root.resolve(strict=True)
        if root not in resolved_parent.parents:
            raise LibraryContractError("Unsafe cloud artifact path.")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{kind}-", dir=resolved_parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            Path(temporary).unlink(missing_ok=True)
        artifact_id = str(uuid4())
        now = utc_text()
        digest = hashlib.sha256(data).hexdigest()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO cloud_extraction_artifacts("
                "id,run_id,kind,format,relative_path,sha256,size_bytes,created_at"
                ") VALUES (?,?,?,?,?,?,?,?)",
                (
                    artifact_id,
                    run_id,
                    kind,
                    "application/json",
                    str(relative),
                    digest,
                    len(data),
                    now,
                ),
            )
        return CloudArtifactRead(
            id=artifact_id,
            kind=kind,
            format="application/json",
            relative_path=str(relative),
            sha256=digest,
            size_bytes=len(data),
            created_at=now,
        )

    def _artifact_path(self, relative_path: str) -> Path:
        root = self.runtime_root.resolve(strict=True)
        candidate = root / relative_path
        if candidate.is_absolute() and root not in candidate.resolve(strict=False).parents:
            raise LibraryContractError("Unsafe cloud artifact path.")
        return candidate

    def _record_provider_success(self, run_id: str, response: ProviderRawResponse) -> None:
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE cloud_extraction_runs SET provider_status='available',usage_json=?,"
                "provider_metadata_json=?,updated_at=? WHERE run_id=?",
                (
                    json_dump(response.usage.model_dump(mode="json")),
                    json_dump(response.provider_metadata),
                    now,
                    run_id,
                ),
            )
            cleanup_errors = {
                key: response.provider_metadata[key]
                for key in ("remote_cleanup_error", "local_cleanup_error")
                if response.provider_metadata.get(key)
            }
            if cleanup_errors:
                connection.execute(
                    "INSERT INTO document_run_issues("
                    "id,run_id,stage_name,code,severity,message,evidence_json,created_at"
                    ") VALUES (?,?,'provider_request','remote_cleanup_failed','warning',?,?,?)",
                    (
                        str(uuid4()),
                        run_id,
                        "The provider response was preserved, but file cleanup needs review.",
                        json_dump({"details": cleanup_errors}),
                        now,
                    ),
                )

    def _record_provider_error(self, run_id: str, error: CloudProviderError) -> None:
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE cloud_extraction_runs SET provider_status=?,normalized_error_state=?,"
                "provider_error_code=?,provider_error_message=?,provider_error_details_json=?,"
                "updated_at=? WHERE run_id=?",
                (
                    error.state.value,
                    error.state.value,
                    error.code,
                    error.safe_message,
                    json_dump(error.details),
                    now,
                    run_id,
                ),
            )

    def _record_validation(self, run_id: str, report: ValidationReport) -> None:
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE cloud_extraction_runs SET validation_outcome=?,updated_at=? WHERE run_id=?",
                (report.outcome.value, now, run_id),
            )
            for issue in report.issues:
                connection.execute(
                    "INSERT INTO document_run_issues("
                    "id,run_id,stage_name,code,severity,message,evidence_json,created_at"
                    ") VALUES (?,?,'local_validation',?,?,?,?,?)",
                    (
                        str(uuid4()),
                        run_id,
                        issue.code,
                        issue.severity,
                        issue.message,
                        json_dump({"path": issue.path}),
                        now,
                    ),
                )

    def _set_validation_outcome(self, run_id: str, outcome: ValidationOutcome) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE cloud_extraction_runs SET validation_outcome=?,updated_at=? WHERE run_id=?",
                (outcome.value, utc_text(), run_id),
            )

    @staticmethod
    def _dict(value: str) -> dict:
        loaded = json_load(value, {})
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _artifact_read(row: sqlite3.Row) -> CloudArtifactRead:
        return CloudArtifactRead(
            id=row["id"],
            kind=row["kind"],
            format=row["format"],
            relative_path=row["relative_path"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            created_at=row["created_at"],
        )
