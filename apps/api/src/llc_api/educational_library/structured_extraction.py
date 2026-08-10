from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from uuid import uuid4

from .comparisons import DocumentComparisonService, normalize_text, text_fingerprint
from .database import LibraryDatabase
from .inventory import sha256_file
from .runs import DocumentRunService
from .schemas import (
    DocumentPageBlockRead,
    DocumentPageRepeatRequest,
    DocumentRunCreate,
    DocumentRunDetail,
    LibraryContractError,
    LibraryNotFoundError,
    ProposedHierarchyNode,
    StructureCandidateList,
    StructureCandidateRead,
    VersionComparisonCreate,
)
from .service import json_dump, json_load, utc_text

EXTRACTOR = "poppler"
EXTRACTOR_VERSION = "structured-document.v1"
_NUMBERED = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?|[a-zäöü][.)])\s+", re.IGNORECASE)
_TOPIC = re.compile(r"\b(?:tema|thema)\s*(\d{1,2})?\b", re.IGNORECASE)
_LEVEL = re.compile(r"(?:^|\s)([GMO])(?:\s|$)")
_PAGE_NUMBER = re.compile(r"^\s*(\d{1,4})\s*$")
_BULLET = re.compile(r"^\s*[•●▪‣–—-]\s+")
_INDEX = re.compile(r"\.{3,}\s*\d{1,4}\s*$")
_CROSS_REFERENCE = re.compile(r"\b(?:véase|ver|siehe|vgl\.)\b", re.IGNORECASE)
_ALLOWED_TYPES = {
    "document_title",
    "front_matter",
    "index_entry",
    "topic_heading",
    "section_heading",
    "subsection_heading",
    "level_marker",
    "paragraph",
    "rule_block",
    "example",
    "note",
    "warning",
    "table",
    "list",
    "diagram",
    "exercise_heading",
    "exercise_instruction",
    "exercise_item",
    "solution_heading",
    "solution_item",
    "bibliography",
    "printed_page_number",
    "cross_reference",
    "unknown_block",
}


class _StageFailure(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class StructuredExtractionService:
    """Deterministic observed-PDF pipeline. It never invokes OCR, models or chunking."""

    def __init__(self, database: LibraryDatabase, materials_root: Path):
        self.database = database
        self.materials_root = materials_root
        self.runs = DocumentRunService(database, materials_root)
        self.comparisons = DocumentComparisonService(database)
        self.cache_root = database.path.parent / "page-cache"

    def create_run(self, source_version_id: int, *, actor: str | None = None) -> DocumentRunDetail:
        return self.runs.create_run(
            DocumentRunCreate(
                source_version_id=source_version_id,
                run_type="structured_extraction",
                pipeline_version="structured-document.v1",
                configuration={
                    "ocr": False,
                    "llm": False,
                    "chunking": False,
                    "embeddings": False,
                    "activation": False,
                    "text_source": "embedded_only",
                },
                selection_strategy="all_pages",
                selected_pages=[],
                initiated_by=actor,
                reason="Extracción estructural determinista de texto ya incrustado",
                exclusive=True,
            )
        )

    def repeat_pages(self, run_id: str, request: DocumentPageRepeatRequest) -> DocumentRunDetail:
        parent = self.runs._require_run(run_id)
        if parent["state"] not in {
            "completed",
            "completed_with_issues",
            "failed",
            "cancelled",
            "stale",
        }:
            raise LibraryContractError(
                "Finaliza o cancela la ejecución actual antes de repetir páginas."
            )
        total = self._registered_page_count(int(parent["source_version_id"]))
        if total is not None and any(page > total for page in request.pages):
            raise LibraryContractError("Una página seleccionada excede el PDF registrado.")
        now = utc_text()
        child_id = str(uuid4())
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO document_processing_runs("
                "id,source_id,source_version_id,target_hash,run_type,pipeline_version,"
                "configuration_json,configuration_hash,selection_strategy,selected_pages_json,"
                "reused_results_json,state,parent_run_id,base_run_id,initiated_by,reason,resumable,"
                "exclusive,created_at,updated_at) SELECT ?,source_id,source_version_id,target_hash,"
                "run_type,pipeline_version,configuration_json,configuration_hash,'explicit_pages',?,"
                "?,'planned',?,coalesce(base_run_id,id),?,?,1,exclusive,?,? "
                "FROM document_processing_runs WHERE id=?",
                (
                    child_id,
                    json_dump(request.pages),
                    json_dump({"from_run_id": run_id, "selected_pages": request.pages}),
                    run_id,
                    request.initiated_by,
                    request.reason,
                    now,
                    now,
                    run_id,
                ),
            )
            self.runs._insert_stage_catalog(connection, child_id, now)
            self.runs._event(
                connection,
                child_id,
                "selected_pages_repeat_created",
                request.initiated_by,
                None,
                "planned",
                {"parent_run_id": run_id, "pages": request.pages},
                now,
            )
        return self.runs.get_run(child_id)

    def preflight(self, run_id: str) -> DocumentRunDetail:
        stage = self.runs._pending_stage(run_id, "pdf_preflight")
        if stage["state"] in {"completed", "completed_with_issues"}:
            return self.runs.get_run(run_id)
        run, stage_id, job_id = self.runs._start_stage(run_id, "pdf_preflight")
        try:
            target = self._target(run)
            if sha256_file(target) != run["target_hash"]:
                self.runs._fail_stage(run_id, stage_id, job_id, "target_hash_mismatch", stale=True)
                return self.runs.get_run(run_id)
            info, warnings = self._pdf_info(target, all_pages=True)
            if int(info.get("pages", 0)) < 1:
                raise _StageFailure("pdf_has_no_pages")
            self._render_probe(target)
            page_sizes = info.pop("page_sizes")
            rotations = info.pop("rotations")
            metrics: dict[str, object] = {
                **info,
                "page_sizes": page_sizes,
                "rotations": rotations,
                "file_present": True,
                "hash_matches": True,
                "readable": True,
                "renderable": True,
                "embedded_text_probe": self._text_probe(target),
                "engine": "pdfinfo/pdftoppm",
                "engine_version": self._tool_version("pdfinfo"),
                "warnings": warnings,
            }
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE source_versions SET page_count=?,technical_metadata_json=? WHERE id=?",
                    (info["pages"], json_dump(metrics), run["source_version_id"]),
                )
                connection.execute(
                    "UPDATE documents SET page_count=?,metadata_json=? WHERE source_version_id=?",
                    (info["pages"], json_dump(info), run["source_version_id"]),
                )
            for warning in warnings:
                self._issue(run_id, None, "pdf_preflight", "pdf_warning", "warning", warning)
            self.runs._complete_stage(run_id, stage_id, job_id, metrics, with_issues=bool(warnings))
            self.runs._save_snapshot(
                run_id,
                int(run["source_version_id"]),
                "pdf_preflight",
                "file",
                "executed",
                denominator=1,
                completed=1,
                with_issues=int(bool(warnings)),
                failed=0,
                pending=0,
                not_applicable=0,
                unknown=0,
                breakdown=metrics,
                provenance={"engine": "poppler", "ocr": False},
            )
        except _StageFailure as exc:
            self.runs._fail_stage(run_id, stage_id, job_id, exc.code)
        except (OSError, subprocess.SubprocessError, sqlite3.Error, ET.ParseError):
            self.runs._fail_stage(run_id, stage_id, job_id, "pdf_preflight_failed")
        return self.runs.get_run(run_id)

    def materialize_pages(self, run_id: str) -> DocumentRunDetail:
        return self._page_stage(run_id, "page_materialization", self._materialize)

    def extract_embedded_text(self, run_id: str) -> DocumentRunDetail:
        return self._page_stage(run_id, "embedded_text_extraction", self._extract_text)

    def analyze_layout(self, run_id: str) -> DocumentRunDetail:
        return self._page_stage(run_id, "layout_analysis", self._layout)

    def extract_candidates(self, run_id: str) -> DocumentRunDetail:
        return self._page_stage(run_id, "structure_candidate_extraction", self._candidates)

    def reconcile_coverage(self, run_id: str) -> DocumentRunDetail:
        stage = self.runs._pending_stage(run_id, "coverage_reconciliation")
        if stage["state"] in {"completed", "completed_with_issues"}:
            return self.runs.get_run(run_id)
        run, stage_id, job_id = self.runs._start_stage(run_id, "coverage_reconciliation")
        try:
            metrics = self._coverage(run_id, int(run["source_version_id"]))
            self.runs._complete_stage(
                run_id,
                stage_id,
                job_id,
                metrics,
                with_issues=bool(metrics["review_pending"] or metrics["issues"]),
            )
        except sqlite3.Error:
            self.runs._fail_stage(run_id, stage_id, job_id, "coverage_reconciliation_failed")
        return self.runs.get_run(run_id)

    def compare_version(self, run_id: str) -> DocumentRunDetail:
        stage = self.runs._pending_stage(run_id, "version_comparison")
        if stage["state"] in {"completed", "completed_with_issues"}:
            return self.runs.get_run(run_id)
        run, stage_id, job_id = self.runs._start_stage(run_id, "version_comparison")
        try:
            base_id, reason = self._comparison_base(int(run["source_version_id"]))
            if base_id is None:
                raise _StageFailure("comparison_base_unavailable")
            self._materialize_legacy_baseline(base_id, run_id)
            comparison = self.comparisons.create(
                VersionComparisonCreate(
                    base_source_version_id=base_id,
                    target_source_version_id=int(run["source_version_id"]),
                    algorithm_version="page-comparison.v1",
                    configuration={
                        "producer": EXTRACTOR_VERSION,
                        "base_selection_reason": reason,
                        "transfer": "proposal_only",
                        "ocr": False,
                    },
                    initiated_by="structured-extraction",
                )
            )
            comparison = self.comparisons.execute(comparison.id)
            summary = {
                "comparison_id": comparison.id,
                "base_source_version_id": base_id,
                "base_selection_reason": reason,
                "state": comparison.state,
                **comparison.summary,
                "transfer_plan_executed": False,
            }
            self.runs._complete_stage(
                run_id,
                stage_id,
                job_id,
                summary,
                with_issues=comparison.state == "completed_with_issues",
            )
        except _StageFailure as exc:
            self.runs._fail_stage(run_id, stage_id, job_id, exc.code)
        except (sqlite3.Error, LibraryContractError, LibraryNotFoundError):
            self.runs._fail_stage(run_id, stage_id, job_id, "version_comparison_failed")
        return self.runs.get_run(run_id)

    def blocks(self, run_id: str, pdf_page_number: int) -> list[DocumentPageBlockRead]:
        if pdf_page_number < 1:
            raise LibraryContractError("La página PDF debe ser positiva.")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT b.* FROM document_page_blocks b JOIN document_pages p ON p.id=b.page_id "
                "WHERE b.run_id=? AND p.pdf_page_index=? ORDER BY b.stage,b.reading_order",
                (run_id, pdf_page_number - 1),
            ).fetchall()
        return [self._block_read(row) for row in rows]

    def candidates(
        self,
        *,
        source_version_id: int | None = None,
        run_id: str | None = None,
        pdf_page_number: int | None = None,
        candidate_type: str | None = None,
        status: str | None = None,
        topic: str | None = None,
        level: str | None = None,
        min_confidence: float | None = None,
        with_issue: bool | None = None,
        without_parent: bool | None = None,
        ambiguous: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> StructureCandidateList:
        clauses = ["c.status<>'superseded'"]
        params: list[object] = []
        for column, value in (
            ("c.source_version_id", source_version_id),
            ("c.run_id", run_id),
            ("c.candidate_type", candidate_type),
            ("c.status", status),
            ("c.observed_topic", topic),
            ("c.observed_pedagogical_level", level),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        if pdf_page_number is not None:
            clauses.append("p.pdf_page_index=?")
            params.append(pdf_page_number - 1)
        if min_confidence is not None:
            clauses.append("c.confidence>=?")
            params.append(min_confidence)
        if with_issue is True:
            clauses.append("c.issues_json<>'[]'")
        elif with_issue is False:
            clauses.append("c.issues_json='[]'")
        if without_parent is True:
            clauses.append("c.parent_candidate_id IS NULL")
        elif without_parent is False:
            clauses.append("c.parent_candidate_id IS NOT NULL")
        if ambiguous is True:
            clauses.append("(c.status='needs_review' OR c.canonical_match_state='ambiguous')")
        elif ambiguous is False:
            clauses.append(
                "c.status<>'needs_review' AND coalesce(c.canonical_match_state,'')<>'ambiguous'"
            )
        where = " AND ".join(clauses)
        with self.database.connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT count(*) FROM document_structure_candidates c "
                    f"JOIN document_pages p ON p.id=c.page_id WHERE {where}",
                    params,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT c.*,p.pdf_page_index FROM document_structure_candidates c "
                f"JOIN document_pages p ON p.id=c.page_id WHERE {where} "
                "ORDER BY p.pdf_page_index,c.reading_order LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return StructureCandidateList(
            items=[self._candidate_read(row) for row in rows],
            page=page,
            page_size=page_size,
            total=total,
            pages=math.ceil(total / page_size) if total else 0,
        )

    def candidate(self, candidate_id: str) -> StructureCandidateRead:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT c.*,p.pdf_page_index FROM document_structure_candidates c "
                "JOIN document_pages p ON p.id=c.page_id WHERE c.id=?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise LibraryNotFoundError("El candidato estructural no existe.")
        return self._candidate_read(row)

    def hierarchy(self, source_version_id: int) -> list[ProposedHierarchyNode]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.*,p.pdf_page_index FROM document_structure_candidates c "
                "JOIN document_pages p ON p.id=c.page_id WHERE c.source_version_id=? "
                "AND c.status<>'superseded' ORDER BY p.pdf_page_index,c.reading_order",
                (source_version_id,),
            ).fetchall()
        reads = {str(row["id"]): self._candidate_read(row) for row in rows}
        children: dict[str, list[ProposedHierarchyNode]] = defaultdict(list)
        roots: list[ProposedHierarchyNode] = []
        for row in rows:
            node = ProposedHierarchyNode(candidate=reads[str(row["id"])])
            parent = row["parent_candidate_id"]
            if parent and str(parent) in reads:
                children[str(parent)].append(node)
            else:
                roots.append(node)

        def attach(node: ProposedHierarchyNode) -> ProposedHierarchyNode:
            node.children = [attach(child) for child in children.get(node.candidate.id, [])]
            return node

        return [attach(root) for root in roots]

    def thumbnail(self, source_version_id: int, pdf_page_number: int) -> Path:
        if pdf_page_number < 1:
            raise LibraryContractError("La página PDF debe ser positiva.")
        with self.database.connect() as connection:
            version = connection.execute(
                "SELECT content_hash,observed_path,page_count FROM source_versions WHERE id=?",
                (source_version_id,),
            ).fetchone()
        if version is None:
            raise LibraryNotFoundError("La versión documental no existe.")
        if version["page_count"] and pdf_page_number > int(version["page_count"]):
            raise LibraryNotFoundError("La página PDF no existe.")
        target = self._safe_path(str(version["observed_path"]))
        cache_dir = self.cache_root / str(version["content_hash"])
        cache_dir.mkdir(parents=True, exist_ok=True)
        output = cache_dir / f"page-{pdf_page_number:05d}.png"
        if not output.exists():
            prefix = output.with_suffix("")
            self._command(
                [
                    "pdftoppm",
                    "-f",
                    str(pdf_page_number),
                    "-l",
                    str(pdf_page_number),
                    "-singlefile",
                    "-scale-to",
                    "360",
                    "-png",
                    str(target),
                    str(prefix),
                ],
                timeout=60,
            )
        return output

    def _page_stage(self, run_id: str, name: str, operation) -> DocumentRunDetail:
        stage = self.runs._pending_stage(run_id, name)
        if stage["state"] in {"completed", "completed_with_issues"}:
            return self.runs.get_run(run_id)
        run, stage_id, job_id = self.runs._start_stage(run_id, name)
        try:
            metrics, issues = operation(run, int(stage["attempt"]))
            self.runs._complete_stage(run_id, stage_id, job_id, metrics, with_issues=issues > 0)
        except _StageFailure as exc:
            self.runs._fail_stage(run_id, stage_id, job_id, exc.code)
        except (OSError, subprocess.SubprocessError, sqlite3.Error, ET.ParseError):
            self.runs._fail_stage(run_id, stage_id, job_id, f"{name}_failed")
        return self.runs.get_run(run_id)

    def _materialize(self, run: sqlite3.Row, attempt: int) -> tuple[dict[str, object], int]:
        target = self._target(run)
        info, warnings = self._pdf_info(target, all_pages=True)
        page_count = int(info["pages"])
        selected = self._selected(run, page_count)
        now = utc_text()
        sizes = info["page_sizes"]
        rotations = info["rotations"]
        with self.database.transaction(immediate=True) as connection:
            for index in selected:
                width, height = sizes.get(str(index + 1), sizes.get("default", [1.0, 1.0]))
                rotation = int(rotations.get(str(index + 1), 0))
                fingerprint = hashlib.sha256(
                    f"{run['target_hash']}:{index}:{width}:{height}:{rotation}".encode()
                ).hexdigest()
                connection.execute(
                    "INSERT INTO document_pages(source_version_id,pdf_page_index,fingerprint,"
                    "width_points,height_points,rotation_degrees,layout_state,structure_state,"
                    "review_state,issue_count,last_run_id,evidence_json,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,'pending','pending','unreviewed',0,?,?,?,?) "
                    "ON CONFLICT(source_version_id,pdf_page_index) DO UPDATE SET "
                    "fingerprint=excluded.fingerprint,width_points=excluded.width_points,"
                    "height_points=excluded.height_points,rotation_degrees=excluded.rotation_degrees,"
                    "last_run_id=excluded.last_run_id,evidence_json=excluded.evidence_json,"
                    "updated_at=excluded.updated_at",
                    (
                        run["source_version_id"],
                        index,
                        fingerprint,
                        width,
                        height,
                        rotation,
                        run["id"],
                        json_dump(
                            {
                                "provenance": "observed_pdf",
                                "source_hash": run["target_hash"],
                                "printed_page_number": "unknown",
                            }
                        ),
                        now,
                        now,
                    ),
                )
                page_id = int(
                    connection.execute(
                        "SELECT id FROM document_pages WHERE source_version_id=? AND pdf_page_index=?",
                        (run["source_version_id"], index),
                    ).fetchone()[0]
                )
                self._page_result(
                    connection,
                    str(run["id"]),
                    page_id,
                    "page_materialization",
                    attempt,
                    "completed",
                    {"width_points": width, "height_points": height, "rotation": rotation},
                    now,
                )
        return {
            "pdf_pages": page_count,
            "selected_pages": len(selected),
            "materialized_pages": len(selected),
            "warnings": warnings,
        }, len(warnings)

    def _extract_text(self, run: sqlite3.Row, attempt: int) -> tuple[dict[str, object], int]:
        target = self._target(run)
        xml = self._command(
            ["pdftotext", "-enc", "UTF-8", "-bbox-layout", str(target), "-"],
            timeout=900,
        ).stdout
        root = ET.fromstring(xml)
        pages = [node for node in root.iter() if self._tag(node) == "page"]
        selected = self._selected(run, len(pages))
        selected_set = set(selected)
        now = utc_text()
        no_text = 0
        total_characters = 0
        total_words = 0
        with self.database.transaction(immediate=True) as connection:
            for index, page_node in enumerate(pages):
                if index not in selected_set:
                    continue
                page = connection.execute(
                    "SELECT * FROM document_pages WHERE source_version_id=? AND pdf_page_index=?",
                    (run["source_version_id"], index),
                ).fetchone()
                if page is None:
                    raise _StageFailure("page_not_materialized")
                connection.execute(
                    "DELETE FROM document_page_blocks WHERE run_id=? AND page_id=? AND stage=?",
                    (run["id"], page["id"], "embedded_text_extraction"),
                )
                blocks: list[dict[str, object]] = []
                for block_node in [node for node in page_node.iter() if self._tag(node) == "block"]:
                    lines: list[dict[str, object]] = []
                    block_text: list[str] = []
                    for line_node in [node for node in block_node if self._tag(node) == "line"]:
                        words = []
                        for word in [node for node in line_node if self._tag(node) == "word"]:
                            value = "".join(word.itertext())
                            words.append(
                                {
                                    "text": value,
                                    "bbox": self._bbox(word),
                                }
                            )
                        line_text = " ".join(str(word["text"]) for word in words).strip()
                        if line_text:
                            block_text.append(line_text)
                        lines.append(
                            {"text": line_text, "bbox": self._bbox(line_node), "words": words}
                        )
                    raw = "\n".join(block_text).strip()
                    if not raw:
                        continue
                    blocks.append(
                        {
                            "raw": raw,
                            "bbox": self._bbox(block_node),
                            "lines": lines,
                            "word_count": sum(len(line["words"]) for line in lines),
                        }
                    )
                page_text = "\n\n".join(str(block["raw"]) for block in blocks)
                textual = text_fingerprint(page_text)
                total_characters += len(page_text)
                total_words += int(textual["words"])
                page_issues: list[str] = []
                if not page_text:
                    no_text += 1
                    page_issues.append("embedded_text_absent")
                elif int(textual["replacement_characters"]) > 0:
                    page_issues.append("replacement_characters_observed")
                for order, block in enumerate(blocks):
                    connection.execute(
                        "INSERT INTO document_page_blocks(id,source_id,source_version_id,page_id,"
                        "run_id,stage,block_index,block_type,raw_text,normalized_layout_text,bbox_json,"
                        "reading_order,confidence,evidence_json,issues_json,extractor,extractor_version,"
                        "created_at,updated_at) VALUES (?,?,?,?,?,? ,?,'text',?,?,?,?,0.99,?,?,?,?,?,?)",
                        (
                            str(uuid4()),
                            run["source_id"],
                            run["source_version_id"],
                            page["id"],
                            run["id"],
                            "embedded_text_extraction",
                            order,
                            block["raw"],
                            normalize_text(str(block["raw"])),
                            json_dump(block["bbox"]),
                            order,
                            json_dump(
                                {
                                    "lines": block["lines"],
                                    "word_count": block["word_count"],
                                    "source": "embedded_pdf_text",
                                }
                            ),
                            "[]",
                            "pdftotext",
                            self._tool_version("pdftotext"),
                            now,
                            now,
                        ),
                    )
                geometry_hash = hashlib.sha256(
                    f"{page['width_points']}:{page['height_points']}:{page['rotation_degrees']}".encode()
                ).hexdigest()
                cache_key = f"{run['target_hash']}:{index + 1}"
                connection.execute(
                    "INSERT INTO document_page_artifacts(page_id,artifact_version,text_content,"
                    "normalized_text,text_hash,text_metrics_json,geometry_hash,cache_key,created_at,"
                    "updated_at) VALUES (?,'page-artifacts.v2',?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(page_id) DO UPDATE SET artifact_version=excluded.artifact_version,"
                    "text_content=excluded.text_content,normalized_text=excluded.normalized_text,"
                    "text_hash=excluded.text_hash,text_metrics_json=excluded.text_metrics_json,"
                    "geometry_hash=excluded.geometry_hash,cache_key=excluded.cache_key,"
                    "updated_at=excluded.updated_at",
                    (
                        page["id"],
                        page_text,
                        textual["normalized"],
                        textual["hash"],
                        json_dump(
                            {
                                key: value
                                for key, value in textual.items()
                                if key not in {"normalized", "tokens"}
                            }
                            | {
                                "blocks": len(blocks),
                                "lines": sum(len(block["lines"]) for block in blocks),
                                "engine": "pdftotext",
                                "engine_version": self._tool_version("pdftotext"),
                                "ocr_executed": False,
                            }
                        ),
                        geometry_hash,
                        cache_key,
                        now,
                        now,
                    ),
                )
                state = "completed_with_issues" if page_issues else "completed"
                connection.execute(
                    "UPDATE document_pages SET has_text=?,character_count=?,text_quality=?,"
                    "review_state=?,issue_count=issue_count+?,last_run_id=?,updated_at=? WHERE id=?",
                    (
                        int(bool(page_text)),
                        len(page_text),
                        "absent"
                        if not page_text
                        else "observed_with_anomalies"
                        if page_issues
                        else "observed",
                        "needs_review" if page_issues else "unreviewed",
                        len(page_issues),
                        run["id"],
                        now,
                        page["id"],
                    ),
                )
                self._page_result(
                    connection,
                    str(run["id"]),
                    int(page["id"]),
                    "embedded_text_extraction",
                    attempt,
                    state,
                    {
                        "characters": len(page_text),
                        "words": textual["words"],
                        "blocks": len(blocks),
                        "text_absent": not bool(page_text),
                    },
                    now,
                )
                for issue in page_issues:
                    self._issue_in(
                        connection,
                        str(run["id"]),
                        int(page["id"]),
                        "embedded_text_extraction",
                        issue,
                        "warning",
                        "La capa de texto observada requiere revisión técnica.",
                        {"ocr_executed": False},
                        now,
                    )
        return {
            "pages_processed": len(selected),
            "pages_with_text": len(selected) - no_text,
            "pages_without_text": no_text,
            "characters": total_characters,
            "words": total_words,
            "engine": "pdftotext",
            "engine_version": self._tool_version("pdftotext"),
            "ocr_executed": False,
        }, no_text

    def _layout(self, run: sqlite3.Row, attempt: int) -> tuple[dict[str, object], int]:
        target = self._target(run)
        image_counts = self._image_counts(target)
        pages = self._page_rows(int(run["source_version_id"]))
        selected = set(self._selected(run, len(pages)))
        now = utc_text()
        totals: Counter[str] = Counter()
        review_pages = 0
        with self.database.transaction(immediate=True) as connection:
            for page in pages:
                index = int(page["pdf_page_index"])
                if index not in selected:
                    continue
                source_blocks = connection.execute(
                    "SELECT * FROM document_page_blocks WHERE run_id=? AND page_id=? "
                    "AND stage='embedded_text_extraction' ORDER BY reading_order",
                    (run["id"], page["id"]),
                ).fetchall()
                connection.execute(
                    "DELETE FROM document_page_blocks WHERE run_id=? AND page_id=? "
                    "AND stage='layout_analysis'",
                    (run["id"], page["id"]),
                )
                width = float(page["width_points"] or 1)
                height = float(page["height_points"] or 1)
                centers = [
                    (
                        json_load(block["bbox_json"], [0, 0, 0, 0])[0]
                        + json_load(block["bbox_json"], [0, 0, 0, 0])[2]
                    )
                    / 2
                    for block in source_blocks
                ]
                has_left = sum(center < width * 0.42 for center in centers) >= 2
                has_right = sum(center > width * 0.58 for center in centers) >= 2
                two_columns = has_left and has_right
                page_needs_review = False
                order = 0
                for block in source_blocks:
                    bbox = json_load(block["bbox_json"], None)
                    raw = str(block["raw_text"] or "")
                    evidence = json_load(block["evidence_json"], {})
                    kind, subtype, confidence, issues = self._layout_kind(
                        raw, bbox, width, height, evidence, two_columns
                    )
                    totals[kind] += 1
                    page_needs_review = page_needs_review or bool(issues)
                    connection.execute(
                        "INSERT INTO document_page_blocks(id,source_id,source_version_id,page_id,"
                        "run_id,stage,block_index,block_type,subtype,raw_text,normalized_layout_text,"
                        "bbox_json,reading_order,column_index,confidence,evidence_json,issues_json,"
                        "extractor,extractor_version,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            str(uuid4()),
                            run["source_id"],
                            run["source_version_id"],
                            page["id"],
                            run["id"],
                            "layout_analysis",
                            order,
                            kind,
                            subtype,
                            raw,
                            block["normalized_layout_text"],
                            block["bbox_json"],
                            order,
                            0
                            if subtype == "left_column"
                            else 1
                            if subtype == "right_column"
                            else None,
                            confidence,
                            json_dump(
                                evidence
                                | {
                                    "two_columns": two_columns,
                                    "classification_signals": [kind, subtype or "none"],
                                }
                            ),
                            json_dump(issues),
                            EXTRACTOR,
                            EXTRACTOR_VERSION,
                            now,
                            now,
                        ),
                    )
                    order += 1
                for image_index in range(image_counts.get(index + 1, 0)):
                    totals["image"] += 1
                    connection.execute(
                        "INSERT INTO document_page_blocks(id,source_id,source_version_id,page_id,"
                        "run_id,stage,block_index,block_type,subtype,reading_order,confidence,"
                        "evidence_json,issues_json,extractor,extractor_version,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,? ,?,'image','embedded_image',?,0.75,?,'[]',?,?,?,?)",
                        (
                            str(uuid4()),
                            run["source_id"],
                            run["source_version_id"],
                            page["id"],
                            run["id"],
                            "layout_analysis",
                            order,
                            order,
                            json_dump(
                                {
                                    "image_index": image_index,
                                    "bbox_available": False,
                                    "source": "pdfimages_list",
                                }
                            ),
                            "pdfimages",
                            self._tool_version("pdfimages"),
                            now,
                            now,
                        ),
                    )
                    order += 1
                if image_counts.get(index + 1, 0) and not source_blocks:
                    page_needs_review = True
                state = "completed_with_issues" if page_needs_review else "completed"
                review_pages += int(page_needs_review)
                connection.execute(
                    "UPDATE document_pages SET layout_state=?,review_state=CASE WHEN ? THEN "
                    "'needs_review' ELSE review_state END,last_run_id=?,updated_at=? WHERE id=?",
                    (state, int(page_needs_review), run["id"], now, page["id"]),
                )
                self._page_result(
                    connection,
                    str(run["id"]),
                    int(page["id"]),
                    "layout_analysis",
                    attempt,
                    state,
                    {
                        "blocks": order,
                        "images": image_counts.get(index + 1, 0),
                        "two_columns": two_columns,
                    },
                    now,
                )
        return {
            "pages_processed": len(selected),
            "blocks": sum(totals.values()),
            "block_types": dict(totals),
            "probable_tables": totals["probable_table"],
            "graphic_regions": totals["image"] + totals["graphic_region"],
            "pages_needing_review": review_pages,
        }, review_pages

    def _candidates(self, run: sqlite3.Row, attempt: int) -> tuple[dict[str, object], int]:
        pages = self._page_rows(int(run["source_version_id"]))
        selected = set(self._selected(run, len(pages)))
        canonical = self._canonical_topics(str(run["source_id"]))
        now = utc_text()
        counts: Counter[str] = Counter()
        review = 0
        current_topic: str | None = None
        current_section: str | None = None
        current_subsection: str | None = None
        current_level: str | None = None
        mode: str | None = None
        with self.database.transaction(immediate=True) as connection:
            page_ids = [
                int(page["id"]) for page in pages if int(page["pdf_page_index"]) in selected
            ]
            if page_ids:
                placeholders = ",".join("?" for _ in page_ids)
                connection.execute(
                    "UPDATE document_structure_candidates SET status='superseded',superseded_at=?,"
                    "updated_at=? WHERE source_version_id=? AND page_id IN ("
                    + placeholders
                    + ") AND status<>'superseded'",
                    [now, now, run["source_version_id"], *page_ids],
                )
            for page in pages:
                index = int(page["pdf_page_index"])
                if index not in selected:
                    continue
                blocks = connection.execute(
                    "SELECT * FROM document_page_blocks WHERE run_id=? AND page_id=? "
                    "AND stage='layout_analysis' ORDER BY reading_order",
                    (run["id"], page["id"]),
                ).fetchall()
                page_review = False
                for block in blocks:
                    raw = str(block["raw_text"] or "")
                    candidate_type, subtype, confidence, issues = self._candidate_kind(
                        raw,
                        str(block["block_type"]),
                        index,
                        int(block["reading_order"]),
                        mode,
                    )
                    if candidate_type not in _ALLOWED_TYPES:
                        candidate_type = "unknown_block"
                    if candidate_type == "exercise_heading":
                        mode = "exercise"
                    elif candidate_type == "solution_heading":
                        mode = "solution"
                    topic_match, topic_number, topic_text = self._match_topic(raw, canonical)
                    if candidate_type == "topic_heading" and topic_match == "no_match":
                        issues.append("topic_without_canonical_match")
                    level_match = _LEVEL.search(raw)
                    observed_level = level_match.group(1) if level_match else current_level
                    status = "auto_supported" if confidence >= 0.9 and not issues else "proposed"
                    if confidence < 0.65 or issues or topic_match == "ambiguous":
                        status = "needs_review"
                    candidate_id = str(uuid4())
                    parent: str | None = None
                    hierarchy_level = 5
                    if candidate_type == "topic_heading":
                        hierarchy_level = 2
                    elif candidate_type == "section_heading":
                        parent = current_topic
                        hierarchy_level = 3
                    elif candidate_type == "subsection_heading":
                        parent = current_section or current_topic
                        hierarchy_level = 4
                    else:
                        parent = current_subsection or current_section or current_topic
                    if candidate_type == "level_marker":
                        current_level = level_match.group(1) if level_match else None
                        observed_level = current_level
                    proposed_page = raw.strip() if candidate_type == "printed_page_number" else None
                    connection.execute(
                        "INSERT INTO document_structure_candidates(id,source_id,source_version_id,"
                        "page_id,run_id,block_id,stage,candidate_type,subtype,raw_text,"
                        "normalized_layout_text,position_json,bbox_json,reading_order,"
                        "parent_candidate_id,hierarchy_level,observed_pedagogical_level,"
                        "observed_topic,canonical_match_state,canonical_topic_number,"
                        "proposed_printed_page,confidence,status,evidence_json,extractor,"
                        "extractor_version,configuration_json,issues_json,needs_review,created_at,"
                        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            candidate_id,
                            run["source_id"],
                            run["source_version_id"],
                            page["id"],
                            run["id"],
                            block["id"],
                            "structure_candidate_extraction",
                            candidate_type,
                            subtype,
                            raw or None,
                            normalize_text(raw) if raw else None,
                            json_dump(
                                {
                                    "pdf_page_number": index + 1,
                                    "reading_order": block["reading_order"],
                                }
                            ),
                            block["bbox_json"],
                            block["reading_order"],
                            parent,
                            hierarchy_level,
                            observed_level,
                            topic_text,
                            topic_match,
                            topic_number,
                            proposed_page,
                            confidence,
                            status,
                            json_dump(
                                {
                                    "block_type": block["block_type"],
                                    "signals": [candidate_type, subtype or "none"],
                                    "canonical_anchor_used": topic_match != "no_match",
                                    "asserts_truth": False,
                                }
                            ),
                            "deterministic-layout-rules",
                            EXTRACTOR_VERSION,
                            json_dump({"semantic_correction": False, "llm": False}),
                            json_dump(issues),
                            int(status == "needs_review"),
                            now,
                            now,
                        ),
                    )
                    if candidate_type == "topic_heading":
                        current_topic = candidate_id
                        current_section = None
                        current_subsection = None
                    elif candidate_type == "section_heading":
                        current_section = candidate_id
                        current_subsection = None
                    elif candidate_type == "subsection_heading":
                        current_subsection = candidate_id
                    if candidate_type == "printed_page_number" and confidence >= 0.85:
                        connection.execute(
                            "UPDATE document_pages SET printed_page_number=? WHERE id=?",
                            (proposed_page, page["id"]),
                        )
                    counts[candidate_type] += 1
                    review += int(status == "needs_review")
                    page_review = page_review or status == "needs_review"
                connection.execute(
                    "UPDATE document_pages SET structure_state=?,review_state=CASE WHEN ? THEN "
                    "'needs_review' ELSE review_state END,last_run_id=?,updated_at=? WHERE id=?",
                    (
                        "completed_with_issues" if page_review else "completed",
                        int(page_review),
                        run["id"],
                        now,
                        page["id"],
                    ),
                )
                self._page_result(
                    connection,
                    str(run["id"]),
                    int(page["id"]),
                    "structure_candidate_extraction",
                    attempt,
                    "completed_with_issues" if page_review else "completed",
                    {"candidates": len(blocks), "needs_review": page_review},
                    now,
                )
        return {
            "pages_processed": len(selected),
            "candidates": sum(counts.values()),
            "candidate_types": dict(counts),
            "needs_review": review,
            "topics_detected": counts["topic_heading"],
            "levels_detected": counts["level_marker"],
            "exercises_detected": counts["exercise_item"] + counts["exercise_heading"],
            "solutions_detected": counts["solution_item"] + counts["solution_heading"],
            "knowledge_confirmed": False,
        }, review

    def _coverage(self, run_id: str, version_id: int) -> dict[str, int]:
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            page_count = int(
                connection.execute(
                    "SELECT coalesce(page_count,0) FROM source_versions WHERE id=?", (version_id,)
                ).fetchone()[0]
            )
            materialized = int(
                connection.execute(
                    "SELECT count(*) FROM document_pages WHERE source_version_id=?", (version_id,)
                ).fetchone()[0]
            )
            with_text = int(
                connection.execute(
                    "SELECT count(*) FROM document_pages WHERE source_version_id=? AND has_text=1",
                    (version_id,),
                ).fetchone()[0]
            )
            layout = int(
                connection.execute(
                    "SELECT count(*) FROM document_pages WHERE source_version_id=? "
                    "AND layout_state LIKE 'completed%'",
                    (version_id,),
                ).fetchone()[0]
            )
            structure = int(
                connection.execute(
                    "SELECT count(*) FROM document_pages WHERE source_version_id=? "
                    "AND structure_state LIKE 'completed%'",
                    (version_id,),
                ).fetchone()[0]
            )
            candidate_counts = {
                str(row["candidate_type"]): int(row["total"])
                for row in connection.execute(
                    "SELECT candidate_type,count(*) total FROM document_structure_candidates "
                    "WHERE source_version_id=? AND status<>'superseded' GROUP BY candidate_type",
                    (version_id,),
                )
            }
            candidates = sum(candidate_counts.values())
            topics = int(
                connection.execute(
                    "SELECT count(DISTINCT canonical_topic_number) FROM document_structure_candidates "
                    "WHERE source_version_id=? AND status<>'superseded' "
                    "AND canonical_topic_number IS NOT NULL",
                    (version_id,),
                ).fetchone()[0]
            )
            levels = int(
                connection.execute(
                    "SELECT count(DISTINCT observed_pedagogical_level) "
                    "FROM document_structure_candidates WHERE source_version_id=? "
                    "AND status<>'superseded' AND observed_pedagogical_level IS NOT NULL",
                    (version_id,),
                ).fetchone()[0]
            )
            review_pending = int(
                connection.execute(
                    "SELECT count(*) FROM document_structure_candidates WHERE source_version_id=? "
                    "AND status='needs_review'",
                    (version_id,),
                ).fetchone()[0]
            )
            issues = int(
                connection.execute(
                    "SELECT count(*) FROM document_run_issues WHERE run_id=? AND resolved_at IS NULL",
                    (run_id,),
                ).fetchone()[0]
            )
            visual = candidate_counts.get("diagram", 0)
            dimensions = {
                "file": (1, 1),
                "pages": (page_count, materialized),
                "text": (page_count, with_text),
                "layout": (page_count, layout),
                "structure": (page_count, structure),
                "topics_detected": (51, topics),
                "levels_detected": (3, levels),
                "exercises": (
                    candidate_counts.get("exercise_heading", 0)
                    + candidate_counts.get("exercise_item", 0),
                    candidate_counts.get("exercise_heading", 0)
                    + candidate_counts.get("exercise_item", 0),
                ),
                "solutions": (
                    candidate_counts.get("solution_heading", 0)
                    + candidate_counts.get("solution_item", 0),
                    candidate_counts.get("solution_heading", 0)
                    + candidate_counts.get("solution_item", 0),
                ),
                "tables": (candidate_counts.get("table", 0), candidate_counts.get("table", 0)),
                "visual_elements": (visual, visual),
                "candidates": (candidates, candidates),
                "issues": (issues, 0),
                "review_pending": (review_pending, 0),
            }
            for dimension, (denominator, completed) in dimensions.items():
                connection.execute(
                    "INSERT INTO document_coverage_snapshots(id,run_id,source_version_id,stage_name,"
                    "dimension,execution_status,denominator,completed,with_issues,failed,pending,"
                    "not_applicable,unknown,breakdown_json,provenance_json,captured_at) "
                    "VALUES (?,?,?,'coverage_reconciliation',?,'executed',?,?,?,?,0,0,0,?,?,?)",
                    (
                        str(uuid4()),
                        run_id,
                        version_id,
                        dimension,
                        denominator,
                        completed,
                        issues
                        if dimension == "issues"
                        else review_pending
                        if dimension == "review_pending"
                        else 0,
                        max(0, denominator - completed),
                        json_dump(candidate_counts if dimension == "candidates" else {}),
                        json_dump({"observed": True, "global_understanding": False}),
                        now,
                    ),
                )
        return {
            "pages": page_count,
            "materialized": materialized,
            "with_text": with_text,
            "without_text": max(0, page_count - with_text),
            "layout": layout,
            "structure": structure,
            "topics": topics,
            "levels": levels,
            "candidates": candidates,
            "issues": issues,
            "review_pending": review_pending,
            "tables": candidate_counts.get("table", 0),
            "visual_elements": visual,
            "exercises": candidate_counts.get("exercise_heading", 0)
            + candidate_counts.get("exercise_item", 0),
            "solutions": candidate_counts.get("solution_heading", 0)
            + candidate_counts.get("solution_item", 0),
        }

    def _comparison_base(self, target_id: int) -> tuple[int | None, str]:
        with self.database.connect() as connection:
            target = connection.execute(
                "SELECT source_id,previous_version_id FROM source_versions WHERE id=?", (target_id,)
            ).fetchone()
            if target is None:
                raise LibraryNotFoundError("La versión documental no existe.")
            if target["previous_version_id"]:
                previous_id = int(target["previous_version_id"])
                usable = connection.execute(
                    "SELECT page_count IS NOT NULL OR EXISTS(SELECT 1 FROM chunks c "
                    "WHERE c.source_version_id=sv.id) OR EXISTS(SELECT 1 FROM document_pages p "
                    "WHERE p.source_version_id=sv.id) FROM source_versions sv WHERE sv.id=?",
                    (previous_id,),
                ).fetchone()
                if usable and bool(usable[0]):
                    return previous_id, "immediate_previous_version_with_stored_evidence"
            active = connection.execute(
                "SELECT id FROM source_versions WHERE source_id=? AND is_active=1 AND id<>?",
                (target["source_id"], target_id),
            ).fetchone()
        return (
            (int(active["id"]), "active_previous_version_with_stored_evidence")
            if active
            else (None, "none")
        )

    def _materialize_legacy_baseline(self, version_id: int, run_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            existing = int(
                connection.execute(
                    "SELECT count(*) FROM document_page_artifacts a JOIN document_pages p "
                    "ON p.id=a.page_id WHERE p.source_version_id=?",
                    (version_id,),
                ).fetchone()[0]
            )
            if existing:
                return
            version = connection.execute(
                "SELECT sv.*,s.id source_id FROM source_versions sv JOIN sources s "
                "ON s.id=sv.source_id WHERE sv.id=?",
                (version_id,),
            ).fetchone()
            if version is None:
                return
            page_count = int(version["page_count"] or 0)
            if page_count < 1:
                return
            chunks = connection.execute(
                "SELECT page_start,page_end,text FROM chunks WHERE source_version_id=? "
                "AND page_start IS NOT NULL ORDER BY sequence",
                (version_id,),
            ).fetchall()
            by_page: dict[int, list[str]] = defaultdict(list)
            for chunk in chunks:
                start = int(chunk["page_start"])
                end = int(chunk["page_end"] or start)
                for page in range(max(1, start), min(page_count, end) + 1):
                    by_page[page].append(str(chunk["text"]))
            now = utc_text()
            for page_number in range(1, page_count + 1):
                text = "\n".join(by_page.get(page_number, [])) or None
                fingerprint = hashlib.sha256(
                    f"legacy:{version['content_hash']}:{page_number}".encode()
                ).hexdigest()
                connection.execute(
                    "INSERT INTO document_pages(source_version_id,pdf_page_index,fingerprint,"
                    "has_text,character_count,text_quality,layout_state,structure_state,review_state,"
                    "issue_count,last_run_id,evidence_json,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,'legacy_observed','unknown','unknown','needs_review',0,?,?,?,?) "
                    "ON CONFLICT(source_version_id,pdf_page_index) DO NOTHING",
                    (
                        version_id,
                        page_number - 1,
                        fingerprint,
                        int(text is not None),
                        len(text or ""),
                        run_id,
                        json_dump(
                            {
                                "provenance": "legacy_stored_chunks",
                                "coordinates_compatible": False,
                                "chunks_transferred": False,
                            }
                        ),
                        now,
                        now,
                    ),
                )
                page_id = int(
                    connection.execute(
                        "SELECT id FROM document_pages WHERE source_version_id=? AND pdf_page_index=?",
                        (version_id, page_number - 1),
                    ).fetchone()[0]
                )
                textual = text_fingerprint(text)
                connection.execute(
                    "INSERT INTO document_page_artifacts(page_id,artifact_version,text_content,"
                    "normalized_text,text_hash,text_metrics_json,geometry_hash,cache_key,created_at,"
                    "updated_at) VALUES (?,'legacy-page-artifacts.v1',?,?,?,?,NULL,NULL,?,?) "
                    "ON CONFLICT(page_id) DO NOTHING",
                    (
                        page_id,
                        text,
                        textual["normalized"],
                        textual["hash"],
                        json_dump(
                            {
                                "provenance": "legacy_stored_chunks",
                                "coordinates_compatible": False,
                                "chunk_transfer": False,
                                "shingles": textual["shingles"],
                                "characters": textual["characters"],
                                "words": textual["words"],
                            }
                        ),
                        now,
                        now,
                    ),
                )

    def _layout_kind(
        self,
        raw: str,
        bbox: object,
        width: float,
        height: float,
        evidence: object,
        two_columns: bool,
    ) -> tuple[str, str | None, float, list[str]]:
        box = bbox if isinstance(bbox, list) and len(bbox) == 4 else [0, 0, width, height]
        x0, y0, x1, y1 = [float(value) for value in box]
        lines = evidence.get("lines", []) if isinstance(evidence, dict) else []
        gaps = 0
        for line in lines if isinstance(lines, list) else []:
            words = line.get("words", []) if isinstance(line, dict) else []
            for left, right in zip(words, words[1:], strict=False):
                left_box = left.get("bbox") if isinstance(left, dict) else None
                right_box = right.get("bbox") if isinstance(right, dict) else None
                if (
                    left_box
                    and right_box
                    and float(right_box[0]) - float(left_box[2]) > width * 0.025
                ):
                    gaps += 1
        if y0 < height * 0.08:
            return "probable_header", None, 0.8, []
        if y1 > height * 0.91 and _PAGE_NUMBER.fullmatch(raw):
            return "probable_page_number", "footer_number", 0.92, []
        if y1 > height * 0.91:
            return "probable_footer", None, 0.8, []
        if gaps >= 2:
            return "probable_table", "aligned_cells", 0.72, ["table_requires_review"]
        if two_columns and x1 <= width * 0.56:
            return "column", "left_column", 0.85, []
        if two_columns and x0 >= width * 0.44:
            return "column", "right_column", 0.85, []
        if x0 < width * 0.04 or x1 > width * 0.96:
            return "margin", None, 0.65, ["margin_classification_uncertain"]
        return "text", None, 0.95, []

    def _candidate_kind(
        self, raw: str, block_type: str, page_index: int, order: int, mode: str | None
    ) -> tuple[str, str | None, float, list[str]]:
        clean = normalize_text(raw)
        folded = clean.casefold()
        if block_type == "probable_page_number" and _PAGE_NUMBER.fullmatch(clean):
            return "printed_page_number", None, 0.92, []
        if block_type == "probable_table":
            return "table", "probable", 0.72, ["table_structure_requires_review"]
        if block_type in {"image", "graphic_region"}:
            return "diagram", "visual_region", 0.7, ["visual_content_not_flattened"]
        if block_type == "unknown_region":
            return "unknown_block", None, 0.4, ["unknown_layout_region"]
        if page_index <= 2 and order == 0 and len(clean) < 180:
            return "document_title", None, 0.68, ["title_heuristic"]
        if _INDEX.search(clean):
            return "index_entry", None, 0.9, []
        if _TOPIC.search(clean):
            return "topic_heading", None, 0.9, []
        if re.search(r"\b(?:soluciones|lösungen)\b", folded):
            return "solution_heading", None, 0.94, []
        if re.search(r"\b(?:ejercicios|übungen)\b", folded):
            return "exercise_heading", None, 0.94, []
        if _LEVEL.search(clean) and len(clean) <= 80:
            return "level_marker", None, 0.9, []
        if re.match(r"^(?:ejemplo|beispiel)\b", folded):
            return "example", None, 0.92, []
        if re.match(r"^(?:nota|hinweis)\b", folded):
            return "note", None, 0.9, []
        if re.match(r"^(?:atención|achtung|advertencia)\b", folded):
            return "warning", None, 0.9, []
        if re.match(r"^(?:regla|regel)\b", folded):
            return "rule_block", None, 0.9, []
        if re.search(r"\b(?:bibliografía|literaturverzeichnis)\b", folded):
            return "bibliography", None, 0.9, []
        if _CROSS_REFERENCE.search(clean):
            return "cross_reference", None, 0.78, []
        if _NUMBERED.match(clean):
            if mode == "solution":
                return "solution_item", None, 0.83, []
            if mode == "exercise":
                return "exercise_item", None, 0.83, []
            depth = clean.split(maxsplit=1)[0].count(".")
            return ("subsection_heading" if depth > 1 else "section_heading"), None, 0.73, []
        if _BULLET.match(clean):
            return "list", None, 0.85, []
        if (
            mode == "exercise"
            and len(clean) < 300
            and re.match(r"^(?:complete|escriba|formen|setzen|ergänzen)", folded)
        ):
            return "exercise_instruction", None, 0.76, []
        if not clean:
            return "unknown_block", None, 0.2, ["no_text_observed"]
        return "paragraph", None, 0.82, []

    def _canonical_topics(self, source_id: str) -> list[dict[str, object]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT theme_number,title_es,title_de FROM canonical_topics WHERE source_id=? "
                "ORDER BY import_id=(SELECT id FROM canonical_route_imports WHERE source_id=? "
                "AND active=1 ORDER BY created_at DESC LIMIT 1) DESC,theme_number",
                (source_id, source_id),
            ).fetchall()
        unique: dict[int, dict[str, object]] = {}
        for row in rows:
            unique.setdefault(int(row["theme_number"]), dict(row))
        if not unique:
            with self.database.connect() as connection:
                fallback = connection.execute(
                    "SELECT theme_number,title_es,title_de FROM canonical_topics "
                    "ORDER BY updated_at DESC,theme_number"
                ).fetchall()
            for row in fallback:
                unique.setdefault(int(row["theme_number"]), dict(row))
        return list(unique.values())

    def _match_topic(
        self, raw: str, topics: list[dict[str, object]]
    ) -> tuple[str, int | None, str | None]:
        clean = normalize_text(raw)
        topic_marker = _TOPIC.search(clean)
        if topic_marker and topic_marker.group(1):
            number = int(topic_marker.group(1))
            if 1 <= number <= 51:
                return "probable_match", number, clean
        if len(clean) > 240:
            return "no_match", None, None
        folded = self._match_normalize(clean)
        scores: list[tuple[float, int, str]] = []
        for topic in topics:
            for title in (topic.get("title_es"), topic.get("title_de")):
                if not title:
                    continue
                title_text = str(title)
                if title_text.casefold() in clean.casefold():
                    return "exact_match", int(topic["theme_number"]), title_text
                normalized = self._match_normalize(title_text)
                if normalized and normalized in folded:
                    return "normalized_match", int(topic["theme_number"]), title_text
                scores.append(
                    (
                        SequenceMatcher(None, normalized, folded).ratio(),
                        int(topic["theme_number"]),
                        title_text,
                    )
                )
        scores.sort(reverse=True)
        if scores and scores[0][0] >= 0.78:
            if len(scores) > 1 and scores[0][0] - scores[1][0] < 0.03:
                return "ambiguous", None, clean
            return "probable_match", scores[0][1], scores[0][2]
        return "no_match", None, None

    @staticmethod
    def _match_normalize(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.casefold())
        return " ".join(
            re.sub(
                r"[^a-z0-9äöüß ]",
                " ",
                "".join(c for c in normalized if not unicodedata.combining(c)),
            ).split()
        )

    def _pdf_info(self, target: Path, *, all_pages: bool) -> tuple[dict[str, Any], list[str]]:
        base = self._command(["pdfinfo", str(target)], timeout=120)
        values = self._parse_key_values(base.stdout)
        pages = int(values.get("Pages", "0"))
        detail = base
        if all_pages and pages:
            detail = self._command(
                ["pdfinfo", "-box", "-f", "1", "-l", str(pages), str(target)], timeout=300
            )
        sizes: dict[str, list[float]] = {}
        rotations: dict[str, int] = {}
        for match in re.finditer(
            r"^Page\s+(\d+)\s+size:\s+([0-9.]+)\s+x\s+([0-9.]+)\s+pts", detail.stdout, re.M
        ):
            sizes[match.group(1)] = [float(match.group(2)), float(match.group(3))]
        for match in re.finditer(r"^Page\s+(\d+)\s+rot:\s+(-?\d+)", detail.stdout, re.M):
            rotations[match.group(1)] = int(match.group(2))
        default_match = re.search(
            r"^Page size:\s+([0-9.]+)\s+x\s+([0-9.]+)\s+pts", base.stdout, re.M
        )
        if default_match:
            sizes["default"] = [float(default_match.group(1)), float(default_match.group(2))]
        warnings = [
            line.strip() for line in (base.stderr + detail.stderr).splitlines() if line.strip()
        ]
        return {
            "pages": pages,
            "page_sizes": sizes,
            "rotations": rotations,
            "title": values.get("Title") or None,
            "author": values.get("Author") or None,
            "creator": values.get("Creator") or None,
            "producer": values.get("Producer") or None,
            "pdf_version": values.get("PDF version") or None,
            "encrypted": values.get("Encrypted") == "yes",
            "tagged": values.get("Tagged") == "yes",
            "file_size": int(str(values.get("File size", "0")).split()[0]),
        }, warnings

    def _render_probe(self, target: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="llc-preflight-") as directory:
            prefix = Path(directory) / "probe"
            self._command(
                [
                    "pdftoppm",
                    "-f",
                    "1",
                    "-l",
                    "1",
                    "-singlefile",
                    "-scale-to",
                    "64",
                    "-png",
                    str(target),
                    str(prefix),
                ],
                timeout=60,
            )
            if not prefix.with_suffix(".png").exists():
                raise _StageFailure("pdf_render_probe_failed")

    def _text_probe(self, target: Path) -> bool:
        result = self._command(
            ["pdftotext", "-f", "1", "-l", "1", "-enc", "UTF-8", str(target), "-"],
            timeout=60,
        )
        return bool(result.stdout.strip())

    def _image_counts(self, target: Path) -> Counter[int]:
        result = self._command(["pdfimages", "-list", str(target)], timeout=300)
        counts: Counter[int] = Counter()
        for line in result.stdout.splitlines():
            match = re.match(r"^\s*(\d+)\s+\d+\s+(?:image|mask|smask)\b", line)
            if match and " smask " not in f" {line} ":
                counts[int(match.group(1))] += 1
        return counts

    def _target(self, run: sqlite3.Row) -> Path:
        observed = run["observed_path"]
        if not observed:
            raise _StageFailure("file_metadata_unavailable")
        return self._safe_path(str(observed))

    def _safe_path(self, observed_path: str) -> Path:
        root = self.materials_root.resolve(strict=True)
        target = (root / observed_path).resolve(strict=True)
        if root not in target.parents or not target.is_file():
            raise _StageFailure("unsafe_or_missing_document_path")
        return target

    def _selected(self, run: sqlite3.Row, total: int) -> list[int]:
        selected = json_load(run["selected_pages_json"], [])
        if run["selection_strategy"] == "explicit_pages" and isinstance(selected, list):
            return sorted({int(page) - 1 for page in selected if 1 <= int(page) <= total})
        return list(range(total))

    def _registered_page_count(self, version_id: int) -> int | None:
        with self.database.connect() as connection:
            value = connection.execute(
                "SELECT page_count FROM source_versions WHERE id=?", (version_id,)
            ).fetchone()
        return int(value[0]) if value and value[0] is not None else None

    def _page_rows(self, version_id: int) -> list[sqlite3.Row]:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT * FROM document_pages WHERE source_version_id=? ORDER BY pdf_page_index",
                (version_id,),
            ).fetchall()

    @staticmethod
    def _page_result(
        connection: sqlite3.Connection,
        run_id: str,
        page_id: int,
        stage: str,
        attempt: int,
        state: str,
        metrics: dict[str, object],
        now: str,
    ) -> None:
        connection.execute(
            "INSERT INTO document_page_stage_results(run_id,page_id,stage_name,attempt,state,"
            "metrics_json,evidence_json,started_at,completed_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?, ?,?,?,?) ON CONFLICT(run_id,page_id,stage_name,attempt) "
            "DO UPDATE SET state=excluded.state,metrics_json=excluded.metrics_json,"
            "evidence_json=excluded.evidence_json,completed_at=excluded.completed_at,"
            "updated_at=excluded.updated_at",
            (
                run_id,
                page_id,
                stage,
                attempt,
                state,
                json_dump(metrics),
                json_dump({"observed": True, "ocr": False, "llm": False}),
                now,
                now,
                now,
                now,
            ),
        )

    def _issue(
        self,
        run_id: str,
        page_id: int | None,
        stage: str,
        code: str,
        severity: str,
        message: str,
    ) -> None:
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            self._issue_in(connection, run_id, page_id, stage, code, severity, message, {}, now)

    @staticmethod
    def _issue_in(
        connection: sqlite3.Connection,
        run_id: str,
        page_id: int | None,
        stage: str,
        code: str,
        severity: str,
        message: str,
        evidence: dict[str, object],
        now: str,
    ) -> None:
        exists = connection.execute(
            "SELECT id FROM document_run_issues WHERE run_id=? AND page_id IS ? "
            "AND stage_name=? AND code=? AND resolved_at IS NULL",
            (run_id, page_id, stage, code),
        ).fetchone()
        if exists:
            return
        connection.execute(
            "INSERT INTO document_run_issues(id,run_id,page_id,stage_name,code,severity,message,"
            "evidence_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                run_id,
                page_id,
                stage,
                code,
                severity,
                message,
                json_dump(evidence),
                now,
            ),
        )

    @staticmethod
    def _command(arguments: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode != 0:
            raise _StageFailure(f"{Path(arguments[0]).name}_failed")
        return result

    @staticmethod
    def _tool_version(tool: str) -> str:
        result = subprocess.run(
            [tool, "-v"], capture_output=True, check=False, text=True, timeout=10
        )
        output = (result.stderr or result.stdout).splitlines()
        return output[0].strip() if output else "unknown"

    @staticmethod
    def _parse_key_values(value: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in value.splitlines():
            if ":" in line:
                key, content = line.split(":", 1)
                result[key.strip()] = content.strip()
        return result

    @staticmethod
    def _tag(node: ET.Element) -> str:
        return node.tag.rsplit("}", 1)[-1]

    @staticmethod
    def _bbox(node: ET.Element) -> list[float] | None:
        keys = ("xMin", "yMin", "xMax", "yMax")
        if not all(key in node.attrib for key in keys):
            return None
        return [round(float(node.attrib[key]), 4) for key in keys]

    @staticmethod
    def _block_read(row: sqlite3.Row) -> DocumentPageBlockRead:
        bbox = json_load(row["bbox_json"], None)
        evidence = json_load(row["evidence_json"], {})
        issues = json_load(row["issues_json"], [])
        return DocumentPageBlockRead(
            id=row["id"],
            page_id=row["page_id"],
            run_id=row["run_id"],
            stage=row["stage"],
            block_index=row["block_index"],
            block_type=row["block_type"],
            subtype=row["subtype"],
            raw_text=row["raw_text"],
            normalized_layout_text=row["normalized_layout_text"],
            bbox=bbox if isinstance(bbox, list) else None,
            reading_order=row["reading_order"],
            column_index=row["column_index"],
            confidence=row["confidence"],
            evidence=evidence if isinstance(evidence, dict) else {},
            issues=issues if isinstance(issues, list) else [],
            extractor=row["extractor"],
            extractor_version=row["extractor_version"],
        )

    @staticmethod
    def _candidate_read(row: sqlite3.Row) -> StructureCandidateRead:
        def dictionary(column: str) -> dict[str, Any]:
            value = json_load(row[column], {})
            return value if isinstance(value, dict) else {}

        issues = json_load(row["issues_json"], [])
        bbox = json_load(row["bbox_json"], None)
        return StructureCandidateRead(
            id=row["id"],
            source_id=row["source_id"],
            source_version_id=row["source_version_id"],
            page_id=row["page_id"],
            pdf_page_number=int(row["pdf_page_index"]) + 1,
            run_id=row["run_id"],
            block_id=row["block_id"],
            stage=row["stage"],
            candidate_type=row["candidate_type"],
            subtype=row["subtype"],
            raw_text=row["raw_text"],
            normalized_layout_text=row["normalized_layout_text"],
            correction_candidate=row["correction_candidate"],
            correction_reason=row["correction_reason"],
            position=dictionary("position_json"),
            bbox=bbox if isinstance(bbox, list) else None,
            reading_order=row["reading_order"],
            parent_candidate_id=row["parent_candidate_id"],
            hierarchy_level=row["hierarchy_level"],
            observed_pedagogical_level=row["observed_pedagogical_level"],
            observed_topic=row["observed_topic"],
            canonical_match_state=row["canonical_match_state"],
            canonical_topic_number=row["canonical_topic_number"],
            proposed_printed_page=row["proposed_printed_page"],
            confidence=row["confidence"],
            status=row["status"],
            evidence=dictionary("evidence_json"),
            extractor=row["extractor"],
            extractor_version=row["extractor_version"],
            configuration=dictionary("configuration_json"),
            issues=issues if isinstance(issues, list) else [],
            needs_review=bool(row["needs_review"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
