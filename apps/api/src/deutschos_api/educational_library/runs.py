from __future__ import annotations

import hashlib
import math
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from .database import LibraryDatabase
from .inventory import sha256_file
from .schemas import (
    CoverageSnapshotRead,
    DocumentPageList,
    DocumentPageRead,
    DocumentRunCreate,
    DocumentRunDetail,
    DocumentRunEventRead,
    DocumentRunIssueRead,
    DocumentRunPassCreate,
    DocumentRunRead,
    DocumentRunStageRead,
    LibraryBusyError,
    LibraryContractError,
    LibraryNotFoundError,
    PageStageState,
)
from .service import json_dump, json_load, utc_text

PIPELINE_STAGES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("preflight", "preflight.v1", (), True),
    ("text_extraction", "text-extraction.v1", ("preflight",), False),
    ("ocr", "ocr.v1", ("preflight",), False),
    ("layout_analysis", "layout-analysis.v1", ("text_extraction",), False),
    (
        "structural_extraction",
        "structural-extraction.v1",
        ("layout_analysis",),
        False,
    ),
    (
        "pedagogical_candidate_extraction",
        "pedagogical-candidates.v1",
        ("structural_extraction",),
        False,
    ),
    ("review", "review.v1", ("structural_extraction",), False),
    ("chunking", "chunking.v1", ("structural_extraction",), False),
    ("embeddings", "embeddings.v1", ("chunking",), False),
    ("canonical_mapping", "canonical-mapping.v1", ("review",), False),
    ("validation", "validation.v1", ("review",), False),
    (
        "coverage_reconciliation",
        "coverage-reconciliation.v1",
        ("preflight",),
        True,
    ),
    (
        "activation_readiness",
        "activation-readiness.v1",
        ("validation", "canonical_mapping"),
        False,
    ),
)

RUN_TRANSITIONS: dict[str, set[str]] = {
    "planned": {"queued", "cancelled", "stale"},
    "queued": {"running", "paused", "cancelled", "failed", "stale"},
    "running": {
        "paused",
        "completed",
        "completed_with_issues",
        "failed",
        "cancelled",
        "stale",
    },
    "paused": {"queued", "running", "cancelled", "stale"},
    "failed": {"queued", "cancelled", "superseded"},
    "completed": {"superseded"},
    "completed_with_issues": {"superseded"},
    "cancelled": {"superseded"},
    "stale": {"superseded"},
    "superseded": set(),
}

PAGE_FILTER_STATES = {
    "pending": {"pending", "not_scheduled"},
    "completed": {"completed", "completed_with_issues"},
    "issues": {"completed_with_issues", "needs_review"},
    "failed": {"failed"},
    "needs_review": {"needs_review"},
}


def stable_configuration_hash(configuration: dict[str, object]) -> str:
    encoded = json_dump(configuration).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DocumentRunService:
    """Version-pinned orchestration for safe, iterative document processing."""

    def __init__(self, database: LibraryDatabase, materials_root: Path):
        self.database = database
        self.materials_root = materials_root

    def create_run(self, request: DocumentRunCreate) -> DocumentRunDetail:
        now = utc_text()
        run_id = str(uuid4())
        configuration = request.configuration
        with self.database.transaction(immediate=True) as connection:
            version = connection.execute(
                "SELECT sv.*,s.name source_name FROM source_versions sv "
                "JOIN sources s ON s.id=sv.source_id WHERE sv.id=?",
                (request.source_version_id,),
            ).fetchone()
            if not version:
                raise LibraryNotFoundError("La versión documental no existe.")
            connection.execute(
                "INSERT INTO document_processing_runs("
                "id,source_id,source_version_id,target_hash,run_type,pipeline_version,"
                "configuration_json,configuration_hash,selection_strategy,selected_pages_json,"
                "reused_results_json,state,initiated_by,reason,resumable,exclusive,created_at,"
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'planned',?,?,?,?,?,?)",
                (
                    run_id,
                    version["source_id"],
                    request.source_version_id,
                    version["content_hash"],
                    request.run_type,
                    request.pipeline_version,
                    json_dump(configuration),
                    stable_configuration_hash(configuration),
                    request.selection_strategy,
                    json_dump(request.selected_pages),
                    "{}",
                    request.initiated_by,
                    request.reason,
                    1,
                    int(request.exclusive),
                    now,
                    now,
                ),
            )
            self._insert_stage_catalog(connection, run_id, now)
            self._event(
                connection,
                run_id,
                "run_created",
                request.initiated_by,
                None,
                "planned",
                {
                    "source_version_id": request.source_version_id,
                    "selection_strategy": request.selection_strategy,
                },
                now,
            )
        return self.get_run(run_id)

    def list_runs(
        self,
        *,
        source_id: str | None = None,
        source_version_id: int | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[DocumentRunRead]:
        conditions: list[str] = []
        parameters: list[object] = []
        if source_id:
            conditions.append("r.source_id=?")
            parameters.append(source_id)
        if source_version_id is not None:
            conditions.append("r.source_version_id=?")
            parameters.append(source_version_id)
        if state:
            conditions.append("r.state=?")
            parameters.append(state)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                self._run_select() + f" {where} ORDER BY r.created_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [self._run_read(row) for row in rows]

    def get_run(self, run_id: str) -> DocumentRunDetail:
        with self.database.connect() as connection:
            row = connection.execute(
                self._run_select() + " WHERE r.id=?",
                (run_id,),
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("La ejecución documental no existe.")
            stages = connection.execute(
                "SELECT * FROM document_run_stages WHERE run_id=? ORDER BY created_at,name,attempt",
                (run_id,),
            ).fetchall()
            snapshots = connection.execute(
                "SELECT * FROM document_coverage_snapshots WHERE run_id=? "
                "ORDER BY captured_at,dimension",
                (run_id,),
            ).fetchall()
            events = connection.execute(
                "SELECT * FROM document_run_events WHERE run_id=? ORDER BY created_at,id",
                (run_id,),
            ).fetchall()
        data = self._run_read(row).model_dump()
        return DocumentRunDetail(
            **data,
            stages=[self._stage_read(stage) for stage in stages],
            coverage=[self._coverage_read(snapshot) for snapshot in snapshots],
            events=[self._event_read(event) for event in events],
        )

    def stages(self, run_id: str) -> list[DocumentRunStageRead]:
        self._require_run(run_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM document_run_stages WHERE run_id=? ORDER BY created_at,name,attempt",
                (run_id,),
            ).fetchall()
        return [self._stage_read(row) for row in rows]

    def coverage(self, run_id: str) -> list[CoverageSnapshotRead]:
        self._require_run(run_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM document_coverage_snapshots WHERE run_id=? "
                "ORDER BY captured_at DESC,dimension",
                (run_id,),
            ).fetchall()
        return [self._coverage_read(row) for row in rows]

    def issues(self, run_id: str) -> list[DocumentRunIssueRead]:
        self._require_run(run_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT i.*,p.pdf_page_index FROM document_run_issues i "
                "LEFT JOIN document_pages p ON p.id=i.page_id "
                "WHERE i.run_id=? ORDER BY i.resolved_at IS NOT NULL,i.created_at",
                (run_id,),
            ).fetchall()
        return [self._issue_read(row) for row in rows]

    def events(self, run_id: str) -> list[DocumentRunEventRead]:
        self._require_run(run_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM document_run_events WHERE run_id=? ORDER BY created_at,id",
                (run_id,),
            ).fetchall()
        return [self._event_read(row) for row in rows]

    def pages(
        self,
        run_id: str,
        *,
        page: int,
        page_size: int,
        filter_name: str | None,
    ) -> DocumentPageList:
        run = self._require_run(run_id)
        conditions = ["p.source_version_id=?"]
        parameters: list[object] = [run["source_version_id"]]
        if filter_name == "without_text":
            conditions.append("p.has_text=0")
        elif filter_name in PAGE_FILTER_STATES:
            states = sorted(PAGE_FILTER_STATES[filter_name])
            placeholders = ",".join("?" for _ in states)
            conditions.append(
                "EXISTS(SELECT 1 FROM document_page_stage_results ps "
                f"WHERE ps.page_id=p.id AND ps.run_id=? AND ps.state IN ({placeholders}))"
            )
            parameters.extend([run_id, *states])
        elif filter_name not in {None, "all"}:
            raise LibraryContractError("El filtro de páginas no es válido.")
        where = " AND ".join(conditions)
        with self.database.connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT count(*) FROM document_pages p WHERE {where}",
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT p.* FROM document_pages p WHERE {where} "
                "ORDER BY p.pdf_page_index LIMIT ? OFFSET ?",
                [*parameters, page_size, (page - 1) * page_size],
            ).fetchall()
            page_ids = [row["id"] for row in rows]
            states_by_page: dict[int, dict[str, PageStageState]] = {item: {} for item in page_ids}
            if page_ids:
                placeholders = ",".join("?" for _ in page_ids)
                state_rows = connection.execute(
                    "SELECT page_id,stage_name,state FROM document_page_stage_results "
                    f"WHERE run_id=? AND page_id IN ({placeholders}) "
                    "ORDER BY attempt",
                    [run_id, *page_ids],
                ).fetchall()
                for state_row in state_rows:
                    states_by_page[state_row["page_id"]][state_row["stage_name"]] = PageStageState(
                        state_row["state"]
                    )
        return DocumentPageList(
            items=[self._page_read(row, states_by_page.get(row["id"], {})) for row in rows],
            page=page,
            page_size=page_size,
            total=total,
            pages=math.ceil(total / page_size) if total else 0,
        )

    def run_preflight(self, run_id: str) -> DocumentRunDetail:
        stage = self._pending_stage(run_id, "preflight")
        if stage["state"] in {"completed", "completed_with_issues"}:
            return self.get_run(run_id)
        run, stage_id, job_id = self._start_stage(run_id, "preflight")
        try:
            observed_path = run["observed_path"]
            if not observed_path:
                raise _SafeStageFailure("file_metadata_unavailable")
            root = self.materials_root.resolve(strict=True)
            target = (root / observed_path).resolve(strict=True)
            if root not in target.parents:
                raise _SafeStageFailure("unsafe_document_path")
            if not target.is_file():
                raise _SafeStageFailure("document_unavailable")
            actual_hash = sha256_file(target)
            if actual_hash != run["target_hash"]:
                self._fail_stage(
                    run_id,
                    stage_id,
                    job_id,
                    "target_hash_mismatch",
                    stale=True,
                )
                return self.get_run(run_id)
            metrics = {
                "file_present": True,
                "hash_matches": True,
                "format": run["format"],
                "size_bytes": target.stat().st_size,
                "page_count": run["page_count"],
                "metadata_available": bool(run["observed_name"]),
                "registered_documents": int(run["document_count"]),
                "registered_chunks": int(run["chunk_count"]),
                "registered_embeddings": int(run["embedding_count"]),
            }
            self._complete_stage(run_id, stage_id, job_id, metrics, with_issues=False)
            self._save_snapshot(
                run_id,
                run["source_version_id"],
                "preflight",
                "file",
                "executed",
                denominator=1,
                completed=1,
                with_issues=0,
                failed=0,
                pending=0,
                not_applicable=0,
                unknown=0,
                breakdown=metrics,
                provenance={"kind": "preflight", "pipeline_version": run["pipeline_version"]},
            )
        except _SafeStageFailure as exc:
            self._fail_stage(run_id, stage_id, job_id, exc.code)
        except (OSError, sqlite3.Error):
            self._fail_stage(run_id, stage_id, job_id, "preflight_failed")
        return self.get_run(run_id)

    def reconcile_coverage(self, run_id: str) -> DocumentRunDetail:
        stage = self._pending_stage(run_id, "coverage_reconciliation")
        if stage["state"] in {"completed", "completed_with_issues"}:
            return self.get_run(run_id)
        run, stage_id, job_id = self._start_stage(run_id, "coverage_reconciliation")
        try:
            metrics, has_issues = self._reconcile_in_transaction(run, stage["attempt"])
            self._complete_stage(run_id, stage_id, job_id, metrics, with_issues=has_issues)
        except sqlite3.Error:
            self._fail_stage(run_id, stage_id, job_id, "coverage_reconciliation_failed")
        return self.get_run(run_id)

    def pause(self, run_id: str, actor: str | None = None) -> DocumentRunDetail:
        with self.database.transaction(immediate=True) as connection:
            run = self._run_row(connection, run_id)
            if run["state"] not in {"queued", "running"}:
                raise LibraryContractError("La ejecución no puede pausarse en su estado actual.")
            now = utc_text()
            self._transition(connection, run, "paused", actor, "run_paused", now)
            connection.execute(
                "UPDATE document_run_stages SET state='paused',updated_at=? "
                "WHERE run_id=? AND state IN ('queued','running')",
                (now, run_id),
            )
            connection.execute(
                "UPDATE processing_jobs SET state='paused',cancel_requested=1,updated_at=? "
                "WHERE document_run_id=? AND state IN ('queued','running')",
                (now, run_id),
            )
        return self.get_run(run_id)

    def resume(self, run_id: str, actor: str | None = None) -> DocumentRunDetail:
        with self.database.transaction(immediate=True) as connection:
            run = self._run_row(connection, run_id)
            if run["state"] != "paused" or not run["resumable"]:
                raise LibraryContractError("La ejecución no se puede reanudar.")
            now = utc_text()
            self._transition(connection, run, "queued", actor, "run_resumed", now)
            connection.execute(
                "UPDATE document_run_stages SET state='pending',updated_at=? "
                "WHERE run_id=? AND state='paused'",
                (now, run_id),
            )
            connection.execute(
                "UPDATE processing_jobs SET state='interrupted',error_code='resume_requested',"
                "updated_at=? WHERE document_run_id=? AND state='paused'",
                (now, run_id),
            )
        return self.get_run(run_id)

    def cancel(self, run_id: str, actor: str | None = None) -> DocumentRunDetail:
        with self.database.transaction(immediate=True) as connection:
            run = self._run_row(connection, run_id)
            if run["state"] in {
                "completed",
                "completed_with_issues",
                "cancelled",
                "stale",
                "superseded",
            }:
                if run["state"] == "cancelled":
                    return self.get_run(run_id)
                raise LibraryContractError("La ejecución ya es terminal.")
            now = utc_text()
            self._transition(connection, run, "cancelled", actor, "run_cancelled", now)
            connection.execute(
                "UPDATE document_run_stages SET state='cancelled',resumable=0,"
                "completed_at=coalesce(completed_at,?),updated_at=? "
                "WHERE run_id=? AND state IN ('pending','queued','running','paused')",
                (now, now, run_id),
            )
            connection.execute(
                "UPDATE processing_jobs SET state='cancelled',cancel_requested=1,"
                "completed_at=coalesce(completed_at,?),updated_at=? "
                "WHERE document_run_id=? AND state IN ('queued','running','paused')",
                (now, now, run_id),
            )
        return self.get_run(run_id)

    def retry_stage(
        self, run_id: str, stage_name: str, *, failed_pages_only: bool
    ) -> DocumentRunDetail:
        with self.database.transaction(immediate=True) as connection:
            run = self._run_row(connection, run_id)
            previous = connection.execute(
                "SELECT * FROM document_run_stages WHERE run_id=? AND name=? "
                "ORDER BY attempt DESC LIMIT 1",
                (run_id, stage_name),
            ).fetchone()
            if not previous:
                raise LibraryNotFoundError("La etapa documental no existe.")
            if previous["state"] != "failed":
                raise LibraryContractError("Solo se pueden reintentar etapas fallidas.")
            attempt = int(previous["attempt"]) + 1
            now = utc_text()
            stage_id = str(uuid4())
            configuration = json_load(previous["configuration_json"], {})
            if isinstance(configuration, dict):
                configuration["failed_pages_only"] = failed_pages_only
                if failed_pages_only:
                    failed_pages = [
                        int(row["pdf_page_index"]) + 1
                        for row in connection.execute(
                            "SELECT DISTINCT p.pdf_page_index "
                            "FROM document_page_stage_results ps "
                            "JOIN document_pages p ON p.id=ps.page_id "
                            "WHERE ps.run_id=? AND ps.state='failed' "
                            "ORDER BY p.pdf_page_index",
                            (run_id,),
                        )
                    ]
                    configuration["selected_pages"] = failed_pages
            connection.execute(
                "INSERT INTO document_run_stages(id,run_id,name,version,state,attempt,"
                "configuration_json,dependencies_json,resumable,created_at,updated_at) "
                "VALUES (?,?,?,?,'pending',?,?,?,?,?,?)",
                (
                    stage_id,
                    run_id,
                    stage_name,
                    previous["version"],
                    attempt,
                    json_dump(configuration),
                    previous["dependencies_json"],
                    previous["resumable"],
                    now,
                    now,
                ),
            )
            self._transition(connection, run, "queued", None, "stage_retry_created", now)
            self._event(
                connection,
                run_id,
                "stage_retry_created",
                None,
                previous["state"],
                "pending",
                {
                    "stage": stage_name,
                    "attempt": attempt,
                    "failed_pages_only": failed_pages_only,
                },
                now,
            )
        return self.get_run(run_id)

    def create_pass(self, parent_run_id: str, request: DocumentRunPassCreate) -> DocumentRunDetail:
        with self.database.connect() as connection:
            parent = self._run_row(connection, parent_run_id)
            pending_rows = connection.execute(
                "SELECT DISTINCT p.pdf_page_index FROM document_page_stage_results ps "
                "JOIN document_pages p ON p.id=ps.page_id "
                "WHERE ps.run_id=? AND ps.state IN "
                "('pending','failed','completed_with_issues','needs_review') "
                "ORDER BY p.pdf_page_index",
                (parent_run_id,),
            ).fetchall()
        selected_pages = [int(row["pdf_page_index"]) + 1 for row in pending_rows]
        if not selected_pages:
            raise LibraryContractError("La ejecución no tiene páginas pendientes o problemáticas.")
        now = utc_text()
        run_id = str(uuid4())
        base_run_id = parent["base_run_id"] or parent_run_id
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO document_processing_runs("
                "id,source_id,source_version_id,target_hash,run_type,pipeline_version,"
                "configuration_json,configuration_hash,selection_strategy,selected_pages_json,"
                "reused_results_json,state,parent_run_id,base_run_id,initiated_by,reason,resumable,"
                "exclusive,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'planned',"
                "?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    parent["source_id"],
                    parent["source_version_id"],
                    parent["target_hash"],
                    parent["run_type"],
                    parent["pipeline_version"],
                    parent["configuration_json"],
                    parent["configuration_hash"],
                    "pending_only",
                    json_dump(selected_pages),
                    json_dump(
                        {
                            "from_run_id": parent_run_id,
                            "completed_pages_reused": True,
                        }
                    ),
                    parent_run_id,
                    base_run_id,
                    request.initiated_by,
                    request.reason,
                    1,
                    parent["exclusive"],
                    now,
                    now,
                ),
            )
            self._insert_stage_catalog(connection, run_id, now)
            self._event(
                connection,
                run_id,
                "iterative_pass_created",
                request.initiated_by,
                None,
                "planned",
                {
                    "parent_run_id": parent_run_id,
                    "selected_page_count": len(selected_pages),
                },
                now,
            )
        return self.get_run(run_id)

    def _reconcile_in_transaction(
        self, run: sqlite3.Row, attempt: int
    ) -> tuple[dict[str, object], bool]:
        run_id = str(run["id"])
        version_id = int(run["source_version_id"])
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            version = connection.execute(
                "SELECT sv.*,d.page_count document_page_count FROM source_versions sv "
                "LEFT JOIN documents d ON d.source_version_id=sv.id WHERE sv.id=?",
                (version_id,),
            ).fetchone()
            page_quality_rows = connection.execute(
                "SELECT * FROM page_quality WHERE source_version_id=? ORDER BY page_number",
                (version_id,),
            ).fetchall()
            chunk_rows = connection.execute(
                "SELECT id,page_start,page_end FROM chunks WHERE source_version_id=?",
                (version_id,),
            ).fetchall()
            embedding_rows = connection.execute(
                "SELECT c.page_start,c.page_end FROM embeddings e "
                "JOIN chunks c ON c.id=e.chunk_id WHERE c.source_version_id=? "
                "AND coalesce(e.status,'indexed')='indexed'",
                (version_id,),
            ).fetchall()
            section_rows = connection.execute(
                "SELECT se.page_start,se.page_end FROM sections se JOIN documents d "
                "ON d.id=se.document_id WHERE d.source_version_id=?",
                (version_id,),
            ).fetchall()
            total = self._known_page_count(
                version["page_count"],
                version["document_page_count"],
                page_quality_rows,
                chunk_rows,
            )
            quality_by_page = {int(row["page_number"]): row for row in page_quality_rows}
            chunk_pages = self._expand_ranges(chunk_rows)
            embedding_pages = self._expand_ranges(embedding_rows)
            structure_pages = self._expand_ranges(section_rows)
            selected = set(json_load(run["selected_pages_json"], []))
            if not all(isinstance(item, int) for item in selected):
                selected = set()
            active_stage = connection.execute(
                "SELECT configuration_json FROM document_run_stages WHERE id=?",
                (run["active_stage_id"],),
            ).fetchone()
            stage_configuration = json_load(
                active_stage["configuration_json"] if active_stage else "{}", {}
            )
            retry_pages = (
                stage_configuration.get("selected_pages")
                if isinstance(stage_configuration, dict)
                and stage_configuration.get("failed_pages_only")
                else None
            )
            if isinstance(retry_pages, list) and retry_pages:
                selected = {
                    int(item) for item in retry_pages if isinstance(item, int) and int(item) > 0
                }
            page_ids: dict[int, int] = {}
            connection.execute("DELETE FROM document_run_issues WHERE run_id=?", (run_id,))
            if total is not None:
                for page_number in range(1, total + 1):
                    quality = quality_by_page.get(page_number)
                    warnings = (
                        json_load(quality["warnings_json"], []) if quality is not None else []
                    )
                    issue_count = len(warnings) if isinstance(warnings, list) else 0
                    if quality is not None and quality["quality"] in {"poor", "unusable"}:
                        issue_count = max(1, issue_count)
                    connection.execute(
                        "INSERT INTO document_pages("
                        "source_version_id,pdf_page_index,has_text,character_count,text_quality,"
                        "review_state,issue_count,last_run_id,evidence_json,created_at,updated_at"
                        ") VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(source_version_id,pdf_page_index) DO UPDATE SET "
                        "has_text=excluded.has_text,character_count=excluded.character_count,"
                        "text_quality=excluded.text_quality,review_state=excluded.review_state,"
                        "issue_count=excluded.issue_count,last_run_id=excluded.last_run_id,"
                        "evidence_json=excluded.evidence_json,updated_at=excluded.updated_at",
                        (
                            version_id,
                            page_number - 1,
                            None if quality is None else int(int(quality["character_count"]) > 0),
                            None if quality is None else int(quality["character_count"]),
                            None if quality is None else quality["quality"],
                            None if quality is None else quality["review_status"],
                            issue_count,
                            run_id,
                            json_dump(
                                {
                                    "kind": "stored_artifact_reconciliation",
                                    "page_quality": quality is not None,
                                }
                            ),
                            now,
                            now,
                        ),
                    )
                    page_id = int(
                        connection.execute(
                            "SELECT id FROM document_pages WHERE source_version_id=? "
                            "AND pdf_page_index=?",
                            (version_id, page_number - 1),
                        ).fetchone()[0]
                    )
                    page_ids[page_number] = page_id
                    if issue_count:
                        connection.execute(
                            "INSERT INTO document_run_issues("
                            "id,run_id,page_id,stage_name,code,severity,message,evidence_json,"
                            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                str(uuid4()),
                                run_id,
                                page_id,
                                "coverage_reconciliation",
                                "stored_page_quality_issue",
                                "warning",
                                "La evidencia almacenada indica que esta página requiere revisión.",
                                json_dump({"warning_count": issue_count}),
                                now,
                            ),
                        )
                    self._write_page_states(
                        connection,
                        run,
                        page_id,
                        page_number,
                        attempt,
                        quality,
                        chunk_pages,
                        embedding_pages,
                        structure_pages,
                        selected,
                        now,
                    )
            issue_pages = {
                number
                for number, quality in quality_by_page.items()
                if quality["quality"] in {"poor", "unusable"}
                or bool(json_load(quality["warnings_json"], []))
            }
            dimensions = self._coverage_dimensions(
                version,
                total,
                quality_by_page,
                chunk_pages,
                embedding_pages,
                structure_pages,
                issue_pages,
            )
            for dimension, values in dimensions.items():
                self._insert_snapshot(connection, run_id, version_id, dimension, values, now)
            metrics = {
                "known_pages": total,
                "pages_with_text": sum(
                    int(row["character_count"]) > 0 for row in page_quality_rows
                ),
                "pages_with_quality": len(quality_by_page),
                "pages_with_chunks": len(chunk_pages),
                "pages_with_embeddings": len(embedding_pages),
                "pages_with_issues": len(issue_pages),
                "pages_reviewed": sum(
                    row["review_status"] != "unreviewed" for row in page_quality_rows
                ),
                "snapshots_created": len(dimensions),
            }
            connection.execute(
                "UPDATE document_run_stages SET metrics_json=?,updated_at=? WHERE id=?",
                (json_dump(metrics), now, run["active_stage_id"]),
            )
        return metrics, bool(issue_pages)

    def _write_page_states(
        self,
        connection: sqlite3.Connection,
        run: sqlite3.Row,
        page_id: int,
        page_number: int,
        attempt: int,
        quality: sqlite3.Row | None,
        chunk_pages: set[int],
        embedding_pages: set[int],
        structure_pages: set[int],
        selected: set[object],
        now: str,
    ) -> None:
        scheduled = not selected or page_number in selected
        stage_states: dict[str, str] = {
            "text_extraction": (
                "not_scheduled"
                if not scheduled
                else "completed"
                if quality is not None and int(quality["character_count"]) > 0
                else "pending"
            ),
            "layout_analysis": "not_scheduled",
            "structural_extraction": (
                "not_scheduled"
                if not scheduled
                else "completed"
                if page_number in structure_pages
                else "pending"
            ),
            "review": (
                "not_scheduled"
                if not scheduled
                else "needs_review"
                if quality is not None
                and (
                    quality["review_status"] == "unreviewed"
                    or quality["quality"] in {"poor", "unusable"}
                )
                else "completed"
                if quality is not None
                else "pending"
            ),
            "chunking": (
                "not_scheduled"
                if not scheduled
                else "completed"
                if page_number in chunk_pages
                else "pending"
            ),
            "embeddings": (
                "not_scheduled"
                if not scheduled
                else "completed"
                if page_number in embedding_pages
                else "pending"
            ),
            "canonical_mapping": "not_scheduled",
        }
        for stage_name, state in stage_states.items():
            existing = connection.execute(
                "SELECT state FROM document_page_stage_results "
                "WHERE run_id=? AND page_id=? AND stage_name=? AND attempt=?",
                (run["id"], page_id, stage_name, attempt),
            ).fetchone()
            if existing and existing["state"] in {
                "completed",
                "completed_with_issues",
                "skipped",
            }:
                continue
            connection.execute(
                "INSERT INTO document_page_stage_results("
                "run_id,page_id,stage_name,attempt,state,metrics_json,evidence_json,"
                "reused_from_run_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id,page_id,stage_name,attempt) DO UPDATE SET "
                "state=excluded.state,metrics_json=excluded.metrics_json,"
                "evidence_json=excluded.evidence_json,"
                "reused_from_run_id=excluded.reused_from_run_id,updated_at=excluded.updated_at",
                (
                    run["id"],
                    page_id,
                    stage_name,
                    attempt,
                    state,
                    "{}",
                    json_dump({"kind": "stored_artifact_reconciliation"}),
                    ((run["parent_run_id"] or run["id"]) if not scheduled else None),
                    now,
                    now,
                ),
            )

    def _coverage_dimensions(
        self,
        version: sqlite3.Row,
        total: int | None,
        quality_by_page: dict[int, sqlite3.Row],
        chunk_pages: set[int],
        embedding_pages: set[int],
        structure_pages: set[int],
        issue_pages: set[int],
    ) -> dict[str, dict[str, object]]:
        def executed(completed: int, issues: int = 0) -> dict[str, object]:
            if total is None:
                return self._coverage_values("no_data", None)
            return self._coverage_values(
                "executed",
                total,
                completed=completed,
                with_issues=issues,
                pending=max(0, total - completed),
            )

        text_is_executed = bool(quality_by_page) or version["extraction_state"] in {
            "extracted",
            "needs_ocr",
            "failed",
        }
        reviewed = sum(row["review_status"] != "unreviewed" for row in quality_by_page.values())
        return {
            "pages": (
                executed(total or 0)
                if total is not None
                else self._coverage_values("no_data", None)
            ),
            "text": (
                executed(
                    sum(int(row["character_count"]) > 0 for row in quality_by_page.values()),
                    len(issue_pages),
                )
                if text_is_executed
                else self._coverage_values("not_executed", total)
            ),
            "quality": (
                executed(len(quality_by_page), len(issue_pages))
                if quality_by_page
                else self._coverage_values("not_executed", total)
            ),
            "structure": (
                executed(len(structure_pages))
                if structure_pages
                else self._coverage_values("not_executed", total)
            ),
            "review": (
                executed(reviewed, len(issue_pages))
                if reviewed
                else self._coverage_values("not_executed", total)
            ),
            "chunks": (
                executed(len(chunk_pages))
                if chunk_pages or version["chunk_state"] == "available"
                else self._coverage_values("not_executed", total)
            ),
            "embeddings": (
                executed(len(embedding_pages))
                if embedding_pages or version["embedding_state"] == "available"
                else self._coverage_values("not_executed", total)
            ),
            "canonical_mapping": self._coverage_values("not_executed", total),
        }

    @staticmethod
    def _coverage_values(
        execution_status: str,
        denominator: int | None,
        *,
        completed: int | None = None,
        with_issues: int | None = None,
        pending: int | None = None,
    ) -> dict[str, object]:
        if execution_status == "not_executed":
            return {
                "execution_status": execution_status,
                "denominator": denominator,
                "completed": None,
                "with_issues": None,
                "failed": None,
                "pending": None,
                "not_applicable": None,
                "unknown": denominator,
            }
        return {
            "execution_status": execution_status,
            "denominator": denominator,
            "completed": completed,
            "with_issues": with_issues,
            "failed": 0 if denominator is not None else None,
            "pending": pending,
            "not_applicable": 0 if denominator is not None else None,
            "unknown": 0 if denominator is not None else None,
        }

    def _insert_snapshot(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        version_id: int,
        dimension: str,
        values: dict[str, object],
        now: str,
    ) -> None:
        connection.execute(
            "INSERT INTO document_coverage_snapshots("
            "id,run_id,source_version_id,stage_name,dimension,execution_status,denominator,"
            "completed,with_issues,failed,pending,not_applicable,unknown,breakdown_json,"
            "provenance_json,captured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                run_id,
                version_id,
                "coverage_reconciliation",
                dimension,
                values["execution_status"],
                values["denominator"],
                values["completed"],
                values["with_issues"],
                values["failed"],
                values["pending"],
                values["not_applicable"],
                values["unknown"],
                "{}",
                json_dump({"kind": "stored_artifact_reconciliation", "schema": "coverage.v1"}),
                now,
            ),
        )

    def _save_snapshot(
        self,
        run_id: str,
        version_id: int,
        stage_name: str,
        dimension: str,
        execution_status: str,
        *,
        denominator: int | None,
        completed: int | None,
        with_issues: int | None,
        failed: int | None,
        pending: int | None,
        not_applicable: int | None,
        unknown: int | None,
        breakdown: dict[str, object],
        provenance: dict[str, object],
    ) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO document_coverage_snapshots("
                "id,run_id,source_version_id,stage_name,dimension,execution_status,denominator,"
                "completed,with_issues,failed,pending,not_applicable,unknown,breakdown_json,"
                "provenance_json,captured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    run_id,
                    version_id,
                    stage_name,
                    dimension,
                    execution_status,
                    denominator,
                    completed,
                    with_issues,
                    failed,
                    pending,
                    not_applicable,
                    unknown,
                    json_dump(breakdown),
                    json_dump(provenance),
                    utc_text(),
                ),
            )

    def _start_stage(self, run_id: str, stage_name: str) -> tuple[sqlite3.Row, str, str]:
        with self.database.transaction(immediate=True) as connection:
            run = self._run_row(connection, run_id, include_target=True)
            stage = connection.execute(
                "SELECT * FROM document_run_stages WHERE run_id=? AND name=? "
                "ORDER BY attempt DESC LIMIT 1",
                (run_id, stage_name),
            ).fetchone()
            if not stage or stage["state"] not in {"pending", "queued"}:
                raise LibraryContractError("La etapa no está disponible para ejecución.")
            dependencies = json_load(stage["dependencies_json"], [])
            for dependency in dependencies if isinstance(dependencies, list) else []:
                dependency_row = connection.execute(
                    "SELECT state FROM document_run_stages WHERE run_id=? AND name=? "
                    "ORDER BY attempt DESC LIMIT 1",
                    (run_id, dependency),
                ).fetchone()
                if not dependency_row or dependency_row["state"] not in {
                    "completed",
                    "completed_with_issues",
                    "skipped",
                }:
                    raise LibraryContractError(f"La etapa requiere completar primero {dependency}.")
            if run["state"] in {
                "completed",
                "completed_with_issues",
                "cancelled",
                "stale",
                "superseded",
            }:
                raise LibraryContractError("La ejecución ya es terminal.")
            if run["content_hash"] != run["target_hash"]:
                now = utc_text()
                self._transition(connection, run, "stale", None, "target_became_stale", now)
                raise LibraryContractError("El hash de la versión objetivo ya no coincide.")
            now = utc_text()
            if run["state"] in {"planned", "failed"}:
                self._transition(connection, run, "queued", None, "run_queued", now)
                run = self._run_row(connection, run_id, include_target=True)
            if run["state"] == "paused":
                raise LibraryContractError("La ejecución está pausada.")
            if run["state"] == "queued":
                self._transition(connection, run, "running", None, "run_started", now)
            elif run["state"] != "running":
                raise LibraryContractError("La ejecución no está disponible.")
            stage_id = str(stage["id"])
            job_id = str(uuid4())
            try:
                connection.execute(
                    "UPDATE document_run_stages SET state='running',started_at=coalesce("
                    "started_at,?),updated_at=?,error_code=NULL,error_detail=NULL WHERE id=?",
                    (now, now, stage_id),
                )
                connection.execute(
                    "INSERT INTO processing_jobs("
                    "id,kind,state,priority,payload_json,attempts,created_at,started_at,updated_at,"
                    "cancel_requested,document_run_id,document_run_stage_id,legacy"
                    ") VALUES (?,?,'running',0,?,1,?,?,?,0,?,?,0)",
                    (
                        job_id,
                        f"document_run:{stage_name}",
                        json_dump({"run_id": run_id, "stage": stage_name}),
                        now,
                        now,
                        now,
                        run_id,
                        stage_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise LibraryBusyError(
                    "Ya existe una ejecución incompatible activa para esta versión."
                ) from exc
            self._event(
                connection,
                run_id,
                "stage_started",
                None,
                stage["state"],
                "running",
                {"stage": stage_name, "attempt": stage["attempt"]},
                now,
            )
            active = self._run_row(connection, run_id, include_target=True)
            values = dict(active)
            values["active_stage_id"] = stage_id
            return _dict_row(values), stage_id, job_id

    def _complete_stage(
        self,
        run_id: str,
        stage_id: str,
        job_id: str,
        metrics: dict[str, object],
        *,
        with_issues: bool,
    ) -> None:
        now = utc_text()
        stage_state = "completed_with_issues" if with_issues else "completed"
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE document_run_stages SET state=?,metrics_json=?,completed_at=?,updated_at=? "
                "WHERE id=?",
                (stage_state, json_dump(metrics), now, now, stage_id),
            )
            connection.execute(
                "UPDATE processing_jobs SET state='completed',progress_current=1,"
                "progress_total=1,completed_at=?,updated_at=? WHERE id=?",
                (now, now, job_id),
            )
            self._event(
                connection,
                run_id,
                "stage_completed",
                None,
                "running",
                stage_state,
                {"stage_id": stage_id},
                now,
            )
            remaining = int(
                connection.execute(
                    "SELECT count(*) FROM document_run_stages rs WHERE run_id=? "
                    "AND name IN ('preflight','coverage_reconciliation') "
                    "AND attempt=(SELECT max(attempt) FROM document_run_stages latest "
                    "WHERE latest.run_id=rs.run_id AND latest.name=rs.name) "
                    "AND state NOT IN ('completed','completed_with_issues','skipped')",
                    (run_id,),
                ).fetchone()[0]
            )
            if remaining == 0:
                run = self._run_row(connection, run_id)
                issues = int(
                    connection.execute(
                        "SELECT count(*) FROM document_run_issues WHERE run_id=? "
                        "AND resolved_at IS NULL",
                        (run_id,),
                    ).fetchone()[0]
                )
                final_state = "completed_with_issues" if issues else "completed"
                stage_counts = {
                    row["state"]: int(row["total"])
                    for row in connection.execute(
                        "SELECT state,count(*) total FROM document_run_stages rs "
                        "WHERE run_id=? AND attempt=(SELECT max(attempt) "
                        "FROM document_run_stages latest WHERE latest.run_id=rs.run_id "
                        "AND latest.name=rs.name) GROUP BY state",
                        (run_id,),
                    )
                }
                snapshot_count = int(
                    connection.execute(
                        "SELECT count(*) FROM document_coverage_snapshots WHERE run_id=?",
                        (run_id,),
                    ).fetchone()[0]
                )
                connection.execute(
                    "UPDATE document_processing_runs SET summary_json=? WHERE id=?",
                    (
                        json_dump(
                            {
                                "stage_states": stage_counts,
                                "open_issues": issues,
                                "coverage_snapshots": snapshot_count,
                            }
                        ),
                        run_id,
                    ),
                )
                self._transition(connection, run, final_state, None, "run_completed", now)

    def _fail_stage(
        self,
        run_id: str,
        stage_id: str,
        job_id: str,
        error_code: str,
        *,
        stale: bool = False,
    ) -> None:
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE document_run_stages SET state='failed',error_code=?,"
                "error_detail='La etapa no pudo completarse de forma segura.',completed_at=?,"
                "updated_at=? WHERE id=?",
                (error_code, now, now, stage_id),
            )
            connection.execute(
                "UPDATE processing_jobs SET state='failed',error_code=?,"
                "error_detail='La etapa no pudo completarse de forma segura.',completed_at=?,"
                "updated_at=? WHERE id=?",
                (error_code, now, now, job_id),
            )
            run = self._run_row(connection, run_id)
            target_state = "stale" if stale else "failed"
            self._transition(connection, run, target_state, None, "stage_failed", now)
            connection.execute(
                "UPDATE document_processing_runs SET error_code=?,"
                "error_detail='La ejecución requiere revisión antes de continuar.' WHERE id=?",
                (error_code, run_id),
            )

    def _pending_stage(self, run_id: str, stage_name: str) -> sqlite3.Row:
        self._require_run(run_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_run_stages WHERE run_id=? AND name=? "
                "ORDER BY attempt DESC LIMIT 1",
                (run_id, stage_name),
            ).fetchone()
        if not row:
            raise LibraryNotFoundError("La etapa documental no existe.")
        if row["state"] == "not_scheduled":
            raise LibraryContractError("Esta etapa no es ejecutable en el pipeline actual.")
        return row

    def _insert_stage_catalog(self, connection: sqlite3.Connection, run_id: str, now: str) -> None:
        for name, version, dependencies, executable in PIPELINE_STAGES:
            connection.execute(
                "INSERT INTO document_run_stages("
                "id,run_id,name,version,state,attempt,configuration_json,metrics_json,"
                "dependencies_json,resumable,created_at,updated_at"
                ") VALUES (?,?,?,?,?,1,'{}','{}',?,?,?,?)",
                (
                    str(uuid4()),
                    run_id,
                    name,
                    version,
                    "pending" if executable else "not_scheduled",
                    json_dump(list(dependencies)),
                    int(executable),
                    now,
                    now,
                ),
            )

    def _transition(
        self,
        connection: sqlite3.Connection,
        run: sqlite3.Row,
        target: str,
        actor: str | None,
        event_type: str,
        now: str,
    ) -> None:
        source = str(run["state"])
        if target not in RUN_TRANSITIONS.get(source, set()):
            raise LibraryContractError(f"La transición de {source} a {target} no está permitida.")
        terminal = target in {
            "completed",
            "completed_with_issues",
            "cancelled",
            "stale",
            "superseded",
        }
        try:
            connection.execute(
                "UPDATE document_processing_runs SET state=?,started_at=CASE "
                "WHEN ?='running' THEN coalesce(started_at,?) ELSE started_at END,"
                "completed_at=CASE WHEN ? THEN ? ELSE completed_at END,"
                "resumable=?,updated_at=? WHERE id=?",
                (
                    target,
                    target,
                    now,
                    int(terminal),
                    now,
                    int(not terminal),
                    now,
                    run["id"],
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LibraryBusyError(
                "Ya existe una ejecución incompatible activa para esta versión."
            ) from exc
        self._event(
            connection,
            str(run["id"]),
            event_type,
            actor,
            source,
            target,
            {},
            now,
        )

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        actor: str | None,
        from_state: str | None,
        to_state: str | None,
        detail: dict[str, object],
        now: str,
    ) -> None:
        connection.execute(
            "INSERT INTO document_run_events("
            "id,run_id,event_type,actor,from_state,to_state,detail_json,created_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                run_id,
                event_type,
                actor,
                from_state,
                to_state,
                json_dump(detail),
                now,
            ),
        )

    def _require_run(self, run_id: str) -> sqlite3.Row:
        with self.database.connect() as connection:
            return self._run_row(connection, run_id)

    @staticmethod
    def _run_row(
        connection: sqlite3.Connection, run_id: str, *, include_target: bool = False
    ) -> sqlite3.Row:
        select = "SELECT r.*"
        if include_target:
            select += (
                ",sv.content_hash,sv.observed_path,sv.observed_name,sv.page_count,"
                "s.format,(SELECT count(*) FROM documents d WHERE d.source_version_id=sv.id) "
                "document_count,(SELECT count(*) FROM chunks c WHERE c.source_version_id=sv.id) "
                "chunk_count,(SELECT count(*) FROM embeddings e "
                "WHERE e.source_version_id=sv.id) embedding_count"
            )
        row = connection.execute(
            select + " FROM document_processing_runs r JOIN source_versions sv "
            "ON sv.id=r.source_version_id JOIN sources s ON s.id=r.source_id WHERE r.id=?",
            (run_id,),
        ).fetchone()
        if not row:
            raise LibraryNotFoundError("La ejecución documental no existe.")
        return row

    @staticmethod
    def _known_page_count(
        version_count: object,
        document_count: object,
        quality_rows: Iterable[sqlite3.Row],
        chunk_rows: Iterable[sqlite3.Row],
    ) -> int | None:
        candidates = [
            int(value)
            for value in (version_count, document_count)
            if value is not None and int(value) > 0
        ]
        quality_numbers = [int(row["page_number"]) for row in quality_rows]
        chunk_ends = [
            int(row["page_end"] or row["page_start"])
            for row in chunk_rows
            if row["page_start"] is not None
        ]
        candidates.extend(quality_numbers)
        candidates.extend(chunk_ends)
        return max(candidates) if candidates else None

    @staticmethod
    def _expand_ranges(rows: Iterable[sqlite3.Row]) -> set[int]:
        pages: set[int] = set()
        for row in rows:
            start = row["page_start"]
            if start is None:
                continue
            end = row["page_end"] or start
            pages.update(range(int(start), int(end) + 1))
        return pages

    @staticmethod
    def _run_select() -> str:
        return (
            "SELECT r.*,s.name source_title,sv.version_number source_version_number,"
            "(SELECT rs.name FROM document_run_stages rs WHERE rs.run_id=r.id "
            "AND rs.state='running' ORDER BY rs.started_at DESC LIMIT 1) current_stage,"
            "(SELECT count(*) FROM document_run_issues ri WHERE ri.run_id=r.id "
            "AND ri.resolved_at IS NULL) issue_count "
            "FROM document_processing_runs r JOIN sources s ON s.id=r.source_id "
            "JOIN source_versions sv ON sv.id=r.source_version_id"
        )

    @staticmethod
    def _run_read(row: sqlite3.Row) -> DocumentRunRead:
        configuration = json_load(row["configuration_json"], {})
        selected_pages = json_load(row["selected_pages_json"], [])
        reused = json_load(row["reused_results_json"], {})
        summary = json_load(row["summary_json"], {})
        return DocumentRunRead(
            id=row["id"],
            source_id=row["source_id"],
            source_title=row["source_title"],
            source_version_id=row["source_version_id"],
            source_version_number=row["source_version_number"],
            target_hash=row["target_hash"],
            run_type=row["run_type"],
            pipeline_version=row["pipeline_version"],
            configuration=configuration if isinstance(configuration, dict) else {},
            configuration_hash=row["configuration_hash"],
            selection_strategy=row["selection_strategy"],
            selected_pages=selected_pages if isinstance(selected_pages, list) else [],
            reused_results=reused if isinstance(reused, dict) else {},
            state=row["state"],
            parent_run_id=row["parent_run_id"],
            base_run_id=row["base_run_id"],
            initiated_by=row["initiated_by"],
            reason=row["reason"],
            summary=summary if isinstance(summary, dict) else {},
            error_code=row["error_code"],
            error_detail=row["error_detail"],
            resumable=bool(row["resumable"]),
            exclusive=bool(row["exclusive"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            updated_at=row["updated_at"],
            current_stage=row["current_stage"],
            issue_count=row["issue_count"],
        )

    @staticmethod
    def _stage_read(row: sqlite3.Row) -> DocumentRunStageRead:
        configuration = json_load(row["configuration_json"], {})
        metrics = json_load(row["metrics_json"], {})
        dependencies = json_load(row["dependencies_json"], [])
        return DocumentRunStageRead(
            id=row["id"],
            run_id=row["run_id"],
            name=row["name"],
            version=row["version"],
            state=row["state"],
            attempt=row["attempt"],
            configuration=configuration if isinstance(configuration, dict) else {},
            metrics=metrics if isinstance(metrics, dict) else {},
            dependencies=dependencies if isinstance(dependencies, list) else [],
            error_code=row["error_code"],
            error_detail=row["error_detail"],
            resumable=bool(row["resumable"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _coverage_read(row: sqlite3.Row) -> CoverageSnapshotRead:
        breakdown = json_load(row["breakdown_json"], {})
        provenance = json_load(row["provenance_json"], {})
        return CoverageSnapshotRead(
            id=row["id"],
            run_id=row["run_id"],
            source_version_id=row["source_version_id"],
            stage_name=row["stage_name"],
            dimension=row["dimension"],
            execution_status=row["execution_status"],
            denominator=row["denominator"],
            completed=row["completed"],
            with_issues=row["with_issues"],
            failed=row["failed"],
            pending=row["pending"],
            not_applicable=row["not_applicable"],
            unknown=row["unknown"],
            breakdown=breakdown if isinstance(breakdown, dict) else {},
            provenance=provenance if isinstance(provenance, dict) else {},
            captured_at=row["captured_at"],
        )

    @staticmethod
    def _page_read(row: sqlite3.Row, stage_states: dict[str, PageStageState]) -> DocumentPageRead:
        return DocumentPageRead(
            id=row["id"],
            source_version_id=row["source_version_id"],
            pdf_page_number=int(row["pdf_page_index"]) + 1,
            printed_page_number=row["printed_page_number"],
            fingerprint=row["fingerprint"],
            width_points=row["width_points"],
            height_points=row["height_points"],
            rotation_degrees=row["rotation_degrees"],
            has_text=None if row["has_text"] is None else bool(row["has_text"]),
            character_count=row["character_count"],
            text_quality=row["text_quality"],
            layout_state=row["layout_state"],
            structure_state=row["structure_state"],
            review_state=row["review_state"],
            issue_count=row["issue_count"],
            stage_states=stage_states,
        )

    @staticmethod
    def _issue_read(row: sqlite3.Row) -> DocumentRunIssueRead:
        evidence = json_load(row["evidence_json"], {})
        return DocumentRunIssueRead(
            id=row["id"],
            run_id=row["run_id"],
            pdf_page_number=(
                None if row["pdf_page_index"] is None else int(row["pdf_page_index"]) + 1
            ),
            stage_name=row["stage_name"],
            code=row["code"],
            severity=row["severity"],
            message=row["message"],
            evidence=evidence if isinstance(evidence, dict) else {},
            resolved_at=row["resolved_at"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _event_read(row: sqlite3.Row) -> DocumentRunEventRead:
        detail = json_load(row["detail_json"], {})
        return DocumentRunEventRead(
            id=row["id"],
            run_id=row["run_id"],
            event_type=row["event_type"],
            actor=row["actor"],
            from_state=row["from_state"],
            to_state=row["to_state"],
            detail=detail if isinstance(detail, dict) else {},
            created_at=row["created_at"],
        )


class _SafeStageFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _dict_row(values: dict[str, object]) -> sqlite3.Row:
    """Return a mapping-compatible object while preserving sqlite Row call sites."""
    return values  # type: ignore[return-value]
