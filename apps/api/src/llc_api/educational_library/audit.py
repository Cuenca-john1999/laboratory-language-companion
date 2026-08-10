from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from llc_api.core.version import APPLICATION_VERSION

from .database import LIBRARY_SCHEMA_VERSION, LibraryDatabase
from .schemas import LibraryContractError, LibraryNotFoundError
from .service import json_dump, json_load, utc_text
from .structured_extraction import StructuredExtractionService

PACKAGE_SCHEMA = "audit-package.v1"
DECISION_SCHEMA = "audit-decisions.v1"
EXPORT_MODES = {"full", "review_only", "targeted"}
DECISION_ACTIONS = {
    "confirm",
    "reject",
    "support",
    "mark_conflicted",
    "choose_primary",
    "change_type",
    "change_parent",
    "adjust_printed_page",
    "confirm_relation",
    "reject_relation",
    "classify_visual",
    "resolve_topic_identity",
}
TARGET_ACTIONS = {
    "candidate": {
        "confirm",
        "reject",
        "support",
        "mark_conflicted",
        "change_type",
        "change_parent",
        "adjust_printed_page",
    },
    "topic": {"choose_primary", "resolve_topic_identity", "mark_conflicted"},
    "relation": {"confirm_relation", "reject_relation", "mark_conflicted"},
    "visual": {"classify_visual"},
}
VISUAL_CLASSES = {
    "blank",
    "image_only",
    "graphic_exercise",
    "divider",
    "cover",
    "damaged",
    "unknown",
}
PAYLOAD_FILES = (
    "source.json",
    "version.json",
    "coverage.json",
    "pages.json",
    "topics.json",
    "hierarchy.json",
    "candidates.json",
    "decisions.json",
    "review_queue.json",
    "conflicts.json",
    "visual_pending.json",
    "exercise_solution_relations.json",
    "comparisons.json",
    "transfer_plans.json",
    "readiness.json",
    "provenance.json",
    "audit_summary.md",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class DocumentAuditService:
    """Version-bound exports, external decisions and immutable closure snapshots."""

    def __init__(self, database: LibraryDatabase, materials_root: Path):
        self.database = database
        self.materials_root = materials_root
        self.exports_root = database.path.parent / "exports"
        self.extraction = StructuredExtractionService(database, materials_root)

    def preview(
        self,
        source_version_id: int,
        *,
        mode: str,
        selection: dict[str, Any] | None = None,
        include_visuals: bool = False,
    ) -> dict[str, Any]:
        package = self._collect(source_version_id, mode, selection or {})
        encoded = self._encode_payload(package)
        visual_pages = package["_visual_pages"] if include_visuals else []
        return {
            "source_version_id": source_version_id,
            "mode": mode,
            "selection": package["_selection"],
            "include_visuals": include_visuals,
            "counts": package["_counts"],
            "estimated_size_bytes": sum(len(value) for value in encoded.values()),
            "visual_asset_count": len(visual_pages),
            "affected_pages": package["_selected_pages"],
            "readiness": package["readiness.json"]["structural_readiness"],
            "ai_readiness": self._ai_readiness(package["readiness.json"]),
        }

    def create_export(
        self,
        source_version_id: int,
        *,
        mode: str,
        selection: dict[str, Any] | None = None,
        include_visuals: bool = False,
    ) -> dict[str, Any]:
        selection = selection or {}
        package = self._collect(source_version_id, mode, selection)
        version = package["version.json"]
        export_id = str(uuid4())
        now = utc_text()
        relative = Path(str(source_version_id)) / f"{export_id}-{mode}.zip"
        target = self.exports_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO document_audit_exports("
                "id,source_id,source_version_id,document_hash,review_run_id,package_schema,"
                "export_mode,selection_json,include_visuals,state,relative_path,created_at) "
                "VALUES (?,?,?,?,?,?,?,? ,?,'creating',?,?)",
                (
                    export_id,
                    version["source_id"],
                    source_version_id,
                    version["content_hash"],
                    package["_review_run_id"],
                    PACKAGE_SCHEMA,
                    mode,
                    json_dump(package["_selection"]),
                    int(include_visuals),
                    str(relative),
                    now,
                ),
            )
        try:
            payload = self._encode_payload(package)
            visual_entries: dict[str, bytes] = {}
            if include_visuals:
                for page_number in package["_visual_pages"]:
                    path = self.extraction.thumbnail(source_version_id, page_number)
                    visual_entries[f"review/page-{page_number:05d}.png"] = path.read_bytes()
            payload.update(visual_entries)
            file_records = {
                name: {"sha256": sha256_bytes(value), "size_bytes": len(value)}
                for name, value in sorted(payload.items())
            }
            logical_basis = {
                "package_schema": PACKAGE_SCHEMA,
                "source_id": version["source_id"],
                "source_version_id": source_version_id,
                "document_hash": version["content_hash"],
                "export_mode": mode,
                "selection": package["_selection"],
                "files": {name: value["sha256"] for name, value in file_records.items()},
            }
            logical_hash = sha256_bytes(canonical_bytes(logical_basis))
            manifest = {
                "package_schema": PACKAGE_SCHEMA,
                "generated_at": now,
                "deutschos": {
                    "application_version": APPLICATION_VERSION,
                    "commit": self._git_commit(),
                },
                "source": {
                    "source_id": version["source_id"],
                    "source_version_id": source_version_id,
                    "source_version_number": version["version_number"],
                    "document_sha256": version["content_hash"],
                    "page_count": version["page_count"],
                },
                "library_schema_version": LIBRARY_SCHEMA_VERSION,
                "pipeline_versions": package["provenance.json"]["pipeline_versions"],
                "extraction_run": package["provenance.json"]["extraction_run"],
                "review_run": package["provenance.json"]["review_run"],
                "files": file_records,
                "counts": package["_counts"],
                "readiness": package["readiness.json"]["structural_readiness"],
                "ai_readiness": self._ai_readiness(package["readiness.json"]),
                "unresolved_blockers": package["readiness.json"]["blockers"],
                "export_mode": mode,
                "selection": package["_selection"],
                "logical_hash": logical_hash,
                "integrity_notice": "SHA-256 provides integrity, not reviewer authenticity.",
            }
            payload["manifest.json"] = canonical_bytes(manifest)
            self._write_zip(target, payload)
            archive_sha = sha256_bytes(target.read_bytes())
            size = target.stat().st_size
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE document_audit_exports SET state='completed',manifest_json=?,"
                    "logical_hash=?,archive_sha256=?,size_bytes=?,counts_json=?,completed_at=? "
                    "WHERE id=?",
                    (
                        json_dump(manifest),
                        logical_hash,
                        archive_sha,
                        size,
                        json_dump(package["_counts"]),
                        utc_text(),
                        export_id,
                    ),
                )
            return self.export_detail(export_id)
        except Exception as exc:
            if target.exists():
                target.unlink()
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE document_audit_exports SET state='failed',error_code=? WHERE id=?",
                    (type(exc).__name__, export_id),
                )
            raise

    def exports(self, source_version_id: int | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM document_audit_exports "
                + ("WHERE source_version_id=? " if source_version_id else "")
                + "ORDER BY created_at DESC",
                (source_version_id,) if source_version_id else (),
            ).fetchall()
            return [self._export_read(row) for row in rows]

    def export_detail(self, export_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_audit_exports WHERE id=?", (export_id,)
            ).fetchone()
        if row is None:
            raise LibraryNotFoundError("Export de auditoría no encontrado.")
        return self._export_read(row)

    def manifest(self, export_id: str) -> dict[str, Any]:
        export = self.export_detail(export_id)
        if export["state"] != "completed":
            raise LibraryContractError("El export todavía no está completo.")
        return export["manifest"]

    def archive_path(self, export_id: str) -> Path:
        export = self.export_detail(export_id)
        if export["state"] != "completed" or not export["relative_path"]:
            raise LibraryContractError("El paquete no está disponible.")
        path = (self.exports_root / export["relative_path"]).resolve()
        root = self.exports_root.resolve()
        if root not in path.parents or not path.is_file():
            raise LibraryNotFoundError("Archivo de export no encontrado.")
        if sha256_bytes(path.read_bytes()) != export["archive_sha256"]:
            raise LibraryContractError("El paquete exportado no supera su hash de integridad.")
        return path

    def validate_decision_set(self, value: dict[str, Any]) -> dict[str, Any]:
        result = {
            "valid": False,
            "decision_set_hash": sha256_bytes(canonical_bytes(value)),
            "applicable": [],
            "stale": [],
            "conflicts": [],
            "invalid": [],
            "no_effect": [],
            "estimated_impact": {},
        }
        required = {
            "decision_schema",
            "package_logical_hash",
            "source_id",
            "source_version_id",
            "document_hash",
            "reviewer",
            "reviewed_at",
            "decisions",
        }
        missing = sorted(required - value.keys())
        if missing:
            result["invalid"].append({"reason": "missing_fields", "fields": missing})
            return result
        if value["decision_schema"] != DECISION_SCHEMA:
            result["invalid"].append({"reason": "unsupported_decision_schema"})
            return result
        if not isinstance(value["decisions"], list) or not value["decisions"]:
            result["invalid"].append({"reason": "decisions_must_be_non_empty_list"})
            return result
        with self.database.connect() as connection:
            existing_import = connection.execute(
                "SELECT id,state FROM document_audit_imports WHERE decision_set_hash=?",
                (result["decision_set_hash"],),
            ).fetchone()
            if existing_import is not None:
                result["stale"].append(
                    {
                        "reason": "decision_set_already_imported",
                        "import_id": existing_import["id"],
                        "state": existing_import["state"],
                    }
                )
                return result
            export = connection.execute(
                "SELECT * FROM document_audit_exports WHERE logical_hash=? AND state='completed'",
                (value["package_logical_hash"],),
            ).fetchone()
            if export is None:
                result["invalid"].append({"reason": "package_logical_hash_unknown"})
                return result
            version = connection.execute(
                "SELECT * FROM source_versions WHERE id=?", (value["source_version_id"],)
            ).fetchone()
            if version is None:
                result["invalid"].append({"reason": "source_version_unknown"})
                return result
            for field, expected in (
                ("source_id", export["source_id"]),
                ("source_version_id", export["source_version_id"]),
                ("document_hash", export["document_hash"]),
            ):
                if value[field] != expected:
                    result["invalid"].append({"reason": f"{field}_mismatch"})
            if version["content_hash"] != value["document_hash"]:
                result["stale"].append({"reason": "current_document_hash_changed"})
            if version["activation_state"] != "candidate":
                result["stale"].append({"reason": "version_no_longer_candidate"})
            if result["invalid"] or result["stale"]:
                return result
            try:
                included_targets, package_files = self._package_scope(export)
            except (OSError, ValueError, zipfile.BadZipFile, KeyError):
                result["invalid"].append({"reason": "package_integrity_failed"})
                return result
            for ordinal, decision in enumerate(value["decisions"]):
                checked = self._validate_decision(
                    connection,
                    decision,
                    ordinal,
                    export,
                    included_targets,
                    package_files,
                )
                result[checked["bucket"]].append(checked["detail"])
        result["valid"] = not any(result[key] for key in ("invalid", "stale", "conflicts"))
        result["estimated_impact"] = {
            "applicable": len(result["applicable"]),
            "no_effect": len(result["no_effect"]),
            "will_recalculate": ["review_queue", "readiness", "coverage"],
            "atomic": True,
            "activation_changed": False,
        }
        return result

    def dry_run(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.validate_decision_set(value)

    def apply_decision_set(self, value: dict[str, Any], *, atomic: bool = True) -> dict[str, Any]:
        if not atomic:
            raise LibraryContractError("El import parcial no está habilitado; use modo atomic.")
        validation = self.validate_decision_set(value)
        if not validation["valid"]:
            raise LibraryContractError("El decision set es inválido, stale o conflictivo.")
        now = utc_text()
        import_id = str(uuid4())
        with self.database.transaction(immediate=True) as connection:
            export = connection.execute(
                "SELECT * FROM document_audit_exports WHERE logical_hash=?",
                (value["package_logical_hash"],),
            ).fetchone()
            connection.execute(
                "INSERT INTO document_audit_imports("
                "id,export_id,source_id,source_version_id,document_hash,package_logical_hash,"
                "decision_schema,decision_set_hash,reviewer,reviewed_at,mode,state,decision_count,"
                "validation_json,impact_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,"
                "'atomic','validated',?,?,?,?)",
                (
                    import_id,
                    export["id"],
                    value["source_id"],
                    value["source_version_id"],
                    value["document_hash"],
                    value["package_logical_hash"],
                    DECISION_SCHEMA,
                    validation["decision_set_hash"],
                    value["reviewer"],
                    value["reviewed_at"],
                    len(value["decisions"]),
                    json_dump(validation),
                    json_dump(validation["estimated_impact"]),
                    now,
                ),
            )
            applied = 0
            for ordinal, decision in enumerate(value["decisions"]):
                candidate_decision_id, resulting_state = self._apply_external_decision(
                    connection,
                    decision,
                    ordinal,
                    import_id,
                    value,
                    validation["decision_set_hash"],
                    now,
                )
                connection.execute(
                    "INSERT INTO document_audit_import_decisions("
                    "id,import_id,ordinal,target_type,target_id,expected_previous_state,action,"
                    "replacement_json,evidence_references_json,rationale,confidence,note,"
                    "resulting_state,candidate_decision_id,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        import_id,
                        ordinal,
                        decision["target_type"],
                        decision["target_id"],
                        decision["expected_previous_state"],
                        decision["action"],
                        json_dump(decision.get("replacement") or {}),
                        json_dump(decision.get("evidence_references") or []),
                        decision["rationale"],
                        decision["confidence"],
                        decision.get("note"),
                        resulting_state,
                        candidate_decision_id,
                        now,
                    ),
                )
                applied += 1
            readiness = self._insert_readiness(connection, int(value["source_version_id"]), now=now)
            connection.execute(
                "UPDATE document_audit_imports SET state='applied',applied_count=?,"
                "impact_json=?,applied_at=? WHERE id=?",
                (
                    applied,
                    json_dump(validation["estimated_impact"] | {"readiness": readiness["state"]}),
                    now,
                    import_id,
                ),
            )
        return self.import_detail(import_id)

    def imports(self, source_version_id: int | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM document_audit_imports "
                + ("WHERE source_version_id=? " if source_version_id else "")
                + "ORDER BY created_at DESC",
                (source_version_id,) if source_version_id else (),
            ).fetchall()
            return [self._import_read(row) for row in rows]

    def import_detail(self, import_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_audit_imports WHERE id=?", (import_id,)
            ).fetchone()
            if row is None:
                raise LibraryNotFoundError("Import de auditoría no encontrado.")
            decisions = connection.execute(
                "SELECT * FROM document_audit_import_decisions WHERE import_id=? ORDER BY ordinal",
                (import_id,),
            ).fetchall()
        result = self._import_read(row)
        result["decisions"] = [self._import_decision_read(value) for value in decisions]
        return result

    def recalculate_readiness(self, source_version_id: int) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            return self._insert_readiness(connection, source_version_id, now=utc_text())

    def closure_status(self, source_version_id: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            version = self._version(connection, source_version_id)
            readiness = self._latest_readiness(connection, source_version_id)
            snapshot = connection.execute(
                "SELECT * FROM document_closure_snapshots WHERE source_version_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (source_version_id,),
            ).fetchone()
            queue = dict(
                connection.execute(
                    "SELECT coalesce(s.priority,'P2'),count(*) FROM "
                    "document_candidate_review_state s JOIN document_structure_candidates c "
                    "ON c.id=s.candidate_id WHERE c.source_version_id=? AND "
                    "s.editorial_state IN ('needs_review','conflicted') GROUP BY 1",
                    (source_version_id,),
                ).fetchall()
            )
            unresolved_topics = int(
                connection.execute(
                    "SELECT count(*) FROM document_consolidated_topics WHERE review_run_id=? "
                    "AND (identity_resolution='unresolved' OR state='conflicted')",
                    (readiness["review_run_id"],),
                ).fetchone()[0]
            )
            visuals = int(
                connection.execute(
                    "SELECT count(*) FROM document_visual_page_classifications "
                    "WHERE review_run_id=? AND state='needs_review'",
                    (readiness["review_run_id"],),
                ).fetchone()[0]
            )
        readiness_read = self._readiness_read(readiness)
        return {
            "source_id": version["source_id"],
            "source_version_id": source_version_id,
            "source_title": version["title"],
            "document_hash": version["content_hash"],
            "activation_state": version["activation_state"],
            "structural_readiness": readiness_read["state"],
            "ai_readiness": self._ai_readiness(readiness_read),
            "blockers": readiness_read["blockers"],
            "queue": {"P0": queue.get("P0", 0), "P1": queue.get("P1", 0), "P2": queue.get("P2", 0)},
            "unresolved_topics": unresolved_topics,
            "visual_pending": visuals,
            "latest_snapshot": self._snapshot_read(snapshot) if snapshot else None,
        }

    def create_snapshot(self, source_version_id: int) -> dict[str, Any]:
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            version = self._version(connection, source_version_id)
            readiness_row = self._latest_readiness(connection, source_version_id)
            readiness = self._readiness_read(readiness_row)
            run_id = readiness_row["review_run_id"]
            topics = connection.execute(
                "SELECT theme_number,state,identity_resolution,identity_evidence_candidate_id,"
                "resolution_method,primary_candidate_id,pdf_page_number,confidence "
                "FROM document_consolidated_topics WHERE review_run_id=? ORDER BY theme_number",
                (run_id,),
            ).fetchall()
            node_rows = connection.execute(
                "SELECT id,source_candidate_id,parent_node_id,node_type,theme_number,state "
                "FROM document_consolidated_nodes WHERE review_run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()
            relations = connection.execute(
                "SELECT id,relation_type,state,confidence FROM document_exercise_solution_relations "
                "WHERE review_run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()
            review_revision = connection.execute(
                "SELECT count(*),coalesce(max(d.created_at),'') FROM document_candidate_decisions d "
                "JOIN document_structure_candidates c ON c.id=d.candidate_id "
                "WHERE c.source_version_id=?",
                (source_version_id,),
            ).fetchone()
            topic_payload = [dict(row) for row in topics]
            hierarchy_payload = [dict(row) for row in node_rows]
            relation_payload = [dict(row) for row in relations]
            ai_readiness = self._ai_readiness(readiness)
            unresolved = [
                {
                    "theme_number": row["theme_number"],
                    "state": row["state"],
                    "identity_resolution": row["identity_resolution"],
                }
                for row in topics
                if row["identity_resolution"] == "unresolved" or row["state"] == "conflicted"
            ]
            payload = {
                "source_id": version["source_id"],
                "source_version_id": source_version_id,
                "document_hash": version["content_hash"],
                "review_run_id": run_id,
                "structural_readiness": readiness["state"],
                "ai_readiness": ai_readiness,
                "topics": topic_payload,
                "hierarchy_hash": sha256_bytes(canonical_bytes(hierarchy_payload)),
                "relations_hash": sha256_bytes(canonical_bytes(relation_payload)),
                "review_revision": [review_revision[0], review_revision[1]],
                "coverage": readiness["metrics"],
                "unresolved": unresolved,
                "blocking": readiness["blockers"],
                "pipeline_versions": self._pipeline_versions(connection, source_version_id),
            }
            snapshot_hash = sha256_bytes(canonical_bytes(payload))
            state = {
                "blocked_for_ai": "blocked",
                "not_ready_for_ai": "open",
                "ready_for_ai_with_issues": "ready_with_issues",
                "ready_for_ai": "ready",
            }[ai_readiness]
            snapshot_id = str(uuid4())
            connection.execute(
                "INSERT INTO document_closure_snapshots("
                "id,source_id,source_version_id,document_hash,review_run_id,structural_readiness,"
                "ai_readiness,state,topic_revision,hierarchy_revision,review_revision,"
                "relation_revision,coverage_json,unresolved_json,blocking_json,"
                "pipeline_versions_json,snapshot_payload_json,snapshot_hash,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    version["source_id"],
                    source_version_id,
                    version["content_hash"],
                    run_id,
                    readiness["state"],
                    ai_readiness,
                    state,
                    sha256_bytes(canonical_bytes(topic_payload)),
                    payload["hierarchy_hash"],
                    sha256_bytes(canonical_bytes(payload["review_revision"])),
                    payload["relations_hash"],
                    json_dump(readiness["metrics"]),
                    json_dump(unresolved),
                    json_dump(readiness["blockers"]),
                    json_dump(payload["pipeline_versions"]),
                    json_dump(payload),
                    snapshot_hash,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM document_closure_snapshots WHERE id=?", (snapshot_id,)
            ).fetchone()
        return self._snapshot_read(row)

    def snapshots(self, source_version_id: int | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM document_closure_snapshots "
                + ("WHERE source_version_id=? " if source_version_id else "")
                + "ORDER BY created_at DESC",
                (source_version_id,) if source_version_id else (),
            ).fetchall()
            return [self._snapshot_read(row) for row in rows]

    def snapshot(self, snapshot_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_closure_snapshots WHERE id=?", (snapshot_id,)
            ).fetchone()
        if row is None:
            raise LibraryNotFoundError("Snapshot documental no encontrado.")
        return self._snapshot_read(row)

    def _collect(
        self, source_version_id: int, mode: str, selection: dict[str, Any]
    ) -> dict[str, Any]:
        if mode not in EXPORT_MODES:
            raise LibraryContractError("Modo de exportación no permitido.")
        if mode == "targeted" and not any(
            selection.get(key)
            for key in ("theme_number", "pages", "candidate_ids", "relation_ids", "review_item_ids")
        ):
            raise LibraryContractError("Un export targeted requiere una selección explícita.")
        normalized_selection = self._normalize_selection(selection)
        with self.database.connect() as connection:
            version = self._version(connection, source_version_id)
            run = connection.execute(
                "SELECT * FROM document_review_runs WHERE source_version_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (source_version_id,),
            ).fetchone()
            if run is None:
                raise LibraryContractError("La versión no tiene consolidación documental.")
            readiness = self._readiness_read(self._latest_readiness(connection, source_version_id))
            candidates = connection.execute(
                "SELECT c.*,p.pdf_page_index+1 pdf_page_number,s.editorial_state,"
                "s.effective_confidence,s.priority,s.reason,s.primary_candidate_id "
                "FROM document_structure_candidates c JOIN document_pages p ON p.id=c.page_id "
                "LEFT JOIN document_candidate_review_state s ON s.candidate_id=c.id "
                "WHERE c.source_version_id=? AND c.superseded_at IS NULL "
                "ORDER BY p.pdf_page_index,c.reading_order,c.id",
                (source_version_id,),
            ).fetchall()
            candidate_rows = [dict(row) for row in candidates]
            selected_ids, selected_pages = self._scope(
                connection,
                source_version_id,
                run["id"],
                mode,
                normalized_selection,
                candidate_rows,
            )
            if mode != "full":
                selected_ids = self._expand_candidate_context(candidate_rows, selected_ids)
                selected_pages |= {
                    int(row["pdf_page_number"])
                    for row in candidate_rows
                    if row["id"] in selected_ids
                }
            else:
                selected_pages = set(range(1, int(version["page_count"] or 0) + 1))
            selected_candidates = [row for row in candidate_rows if row["id"] in selected_ids]
            decisions_by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
            decision_rows = connection.execute(
                "SELECT d.* FROM document_candidate_decisions d "
                "JOIN document_structure_candidates c ON c.id=d.candidate_id "
                "WHERE c.source_version_id=? ORDER BY d.created_at,d.id",
                (source_version_id,),
            ).fetchall()
            for row in decision_rows:
                if mode == "full" or row["candidate_id"] in selected_ids:
                    decisions_by_candidate[row["candidate_id"]].append(self._decision_export(row))
            page_candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in candidate_rows:
                page_candidates[int(row["pdf_page_number"])].append(row)
            candidate_payload = [
                self._candidate_export(
                    row, page_candidates[int(row["pdf_page_number"])], decisions_by_candidate
                )
                for row in selected_candidates
            ]
            pages = self._pages_export(connection, source_version_id, selected_pages)
            topics = [
                self._json_row(
                    row, ("alternative_candidate_ids_json", "evidence_json", "issues_json")
                )
                for row in connection.execute(
                    "SELECT * FROM document_consolidated_topics WHERE review_run_id=? "
                    "ORDER BY theme_number",
                    (run["id"],),
                ).fetchall()
                if mode == "full"
                or row["theme_number"] in self._selected_themes(normalized_selection)
                or row["state"] in {"pending", "conflicted"}
            ]
            hierarchy = [
                self._json_row(row, ("evidence_json", "issues_json"))
                for row in connection.execute(
                    "SELECT * FROM document_consolidated_nodes WHERE review_run_id=? "
                    "ORDER BY pdf_page_number,reading_order,id",
                    (run["id"],),
                ).fetchall()
                if mode == "full"
                or row["source_candidate_id"] in selected_ids
                or row["node_type"] in {"book", "editorial_level"}
            ]
            relation_rows = connection.execute(
                "SELECT * FROM document_exercise_solution_relations WHERE review_run_id=? "
                "ORDER BY id",
                (run["id"],),
            ).fetchall()
            relations = []
            for row in relation_rows:
                item = self._json_row(
                    row,
                    (
                        "exercise_candidate_ids_json",
                        "solution_candidate_ids_json",
                        "evidence_json",
                        "issues_json",
                    ),
                )
                member_ids = set(item["exercise_candidate_ids"] + item["solution_candidate_ids"])
                if (
                    mode == "full"
                    or row["state"] in {"needs_review", "conflicted"}
                    and mode == "review_only"
                    or member_ids & selected_ids
                    or row["id"] in normalized_selection.get("relation_ids", [])
                ):
                    relations.append(item)
            visual_rows = connection.execute(
                "SELECT v.*,p.pdf_page_index+1 pdf_page_number FROM "
                "document_visual_page_classifications v JOIN document_pages p ON p.id=v.page_id "
                "WHERE v.review_run_id=? ORDER BY p.pdf_page_index",
                (run["id"],),
            ).fetchall()
            visuals = [
                self._json_row(row, ("evidence_json",))
                for row in visual_rows
                if mode == "full"
                or row["state"] == "needs_review"
                and mode == "review_only"
                or int(row["pdf_page_number"]) in selected_pages
            ]
            queue = [
                {
                    "candidate_id": row["id"],
                    "pdf_page_number": row["pdf_page_number"],
                    "candidate_type": row["candidate_type"],
                    "theme_number": row["canonical_topic_number"],
                    "priority": row["priority"] or "P2",
                    "reason": row["reason"],
                    "state": row["editorial_state"],
                }
                for row in candidate_rows
                if row["editorial_state"] in {"needs_review", "conflicted"}
                and (mode == "full" or row["id"] in selected_ids)
            ]
            comparisons = [
                self._json_row(row, ("configuration_json", "summary_json"))
                for row in connection.execute(
                    "SELECT * FROM document_version_comparisons WHERE "
                    "base_source_version_id=? OR target_source_version_id=? ORDER BY created_at",
                    (source_version_id, source_version_id),
                ).fetchall()
            ]
            comparison_ids = [row["id"] for row in comparisons]
            transfer_plans = []
            if comparison_ids:
                marks = ",".join("?" for _ in comparison_ids)
                transfer_plans = [
                    self._json_row(row, ("plan_json",))
                    for row in connection.execute(
                        "SELECT * FROM document_transfer_plans WHERE comparison_id IN ("
                        + marks
                        + ") ORDER BY created_at",
                        comparison_ids,
                    ).fetchall()
                ]
            coverage = {
                "document": readiness["metrics"],
                "snapshots": [
                    self._json_row(row, ("breakdown_json", "provenance_json"))
                    for row in connection.execute(
                        "SELECT * FROM document_coverage_snapshots WHERE source_version_id=? "
                        "ORDER BY captured_at",
                        (source_version_id,),
                    ).fetchall()
                ],
            }
            source = {
                "id": version["source_id"],
                "title": version["title"],
                "kind": version["kind"],
                "format": version["format"],
                "pdf_included": False,
            }
            version_payload = {
                "source_id": version["source_id"],
                "source_version_id": source_version_id,
                "version_number": version["version_number"],
                "content_hash": version["content_hash"],
                "page_count": version["page_count"],
                "activation_state": version["activation_state"],
                "is_active": bool(version["is_active"]),
                "observed_name": version["observed_name"],
                "provenance": version["version_provenance"],
            }
            counts = {
                "pages": len(pages),
                "topics": len(topics),
                "hierarchy_nodes": len(hierarchy),
                "candidates": len(candidate_payload),
                "decisions": sum(len(value) for value in decisions_by_candidate.values()),
                "review_queue": len(queue),
                "conflicts": sum(item["state"] == "conflicted" for item in queue),
                "visual_pending": len(visuals),
                "exercise_solution_relations": len(relations),
            }
            provenance = {
                "package_schema": PACKAGE_SCHEMA,
                "library_schema_version": LIBRARY_SCHEMA_VERSION,
                "pipeline_versions": self._pipeline_versions(connection, source_version_id),
                "extraction_run": version["latest_run_id"],
                "review_run": run["id"],
                "ocr_executed": False,
                "vision_ai_executed": False,
                "llm_executed": False,
                "chunks_created": False,
                "embeddings_created": False,
                "activation_changed": False,
            }
        summary = self._summary_markdown(
            source, version_payload, mode, counts, readiness, normalized_selection
        )
        package: dict[str, Any] = {
            "source.json": source,
            "version.json": version_payload,
            "coverage.json": coverage,
            "pages.json": pages,
            "topics.json": topics,
            "hierarchy.json": hierarchy,
            "candidates.json": candidate_payload,
            "decisions.json": [
                decision
                for candidate_id in sorted(decisions_by_candidate)
                for decision in decisions_by_candidate[candidate_id]
            ],
            "review_queue.json": queue,
            "conflicts.json": [item for item in queue if item["state"] == "conflicted"],
            "visual_pending.json": visuals,
            "exercise_solution_relations.json": relations,
            "comparisons.json": comparisons,
            "transfer_plans.json": transfer_plans,
            "readiness.json": {
                "structural_readiness": readiness["state"],
                "metrics": readiness["metrics"],
                "blockers": readiness["blockers"],
                "rationale": readiness["rationale"],
            },
            "provenance.json": provenance,
            "audit_summary.md": summary,
            "_counts": counts,
            "_selection": normalized_selection,
            "_selected_pages": sorted(selected_pages),
            "_visual_pages": sorted(
                {
                    int(item["pdf_page_number"])
                    for item in visuals
                    if int(item["pdf_page_number"]) in selected_pages
                }
                or set(sorted(selected_pages)[:6])
            )[:12],
            "_review_run_id": run["id"],
        }
        return package

    def _scope(
        self,
        connection: sqlite3.Connection,
        source_version_id: int,
        run_id: str,
        mode: str,
        selection: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> tuple[set[str], set[int]]:
        if mode == "full":
            return {row["id"] for row in candidates}, set()
        ids: set[str] = set(selection.get("candidate_ids", [])) | set(
            selection.get("review_item_ids", [])
        )
        pages = {int(value) for value in selection.get("pages", [])}
        if mode == "review_only":
            ids |= {
                row["id"]
                for row in candidates
                if row["editorial_state"] in {"needs_review", "conflicted"}
                and ((row["priority"] or "P2") != "P2" or selection.get("include_p2", False))
            }
            pages |= {int(row["pdf_page_number"]) for row in candidates if row["id"] in ids}
        theme = selection.get("theme_number")
        if theme:
            for row in candidates:
                raw = (row["raw_text"] or "").casefold()
                if row["canonical_topic_number"] == theme or f"tema {theme}" in raw:
                    ids.add(row["id"])
                    pages.add(int(row["pdf_page_number"]))
            for topic in connection.execute(
                "SELECT theme_number,alternative_candidate_ids_json,primary_candidate_id FROM "
                "document_consolidated_topics WHERE review_run_id=? AND theme_number IN (?,?,?)",
                (run_id, max(1, theme - 1), theme, min(51, theme + 1)),
            ).fetchall():
                if topic["theme_number"] == theme:
                    ids |= set(json_load(topic["alternative_candidate_ids_json"], []))
                if topic["primary_candidate_id"]:
                    ids.add(topic["primary_candidate_id"])
                    primary = next(
                        (row for row in candidates if row["id"] == topic["primary_candidate_id"]),
                        None,
                    )
                    if primary:
                        pages.add(int(primary["pdf_page_number"]))
        for row in candidates:
            if int(row["pdf_page_number"]) in pages:
                ids.add(row["id"])
        return ids, pages

    @staticmethod
    def _expand_candidate_context(candidates: list[dict[str, Any]], selected: set[str]) -> set[str]:
        by_id = {row["id"]: row for row in candidates}
        by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in candidates:
            by_page[int(row["pdf_page_number"])].append(row)
        expanded = set(selected)
        for candidate_id in list(selected):
            row = by_id.get(candidate_id)
            if not row:
                continue
            if row["parent_candidate_id"]:
                expanded.add(row["parent_candidate_id"])
            expanded |= {
                value["id"] for value in candidates if value["parent_candidate_id"] == candidate_id
            }
            page_rows = by_page[int(row["pdf_page_number"])]
            for index, value in enumerate(page_rows):
                if value["id"] == candidate_id:
                    expanded |= {item["id"] for item in page_rows[max(0, index - 2) : index + 3]}
                    break
        return expanded

    def _pages_export(
        self,
        connection: sqlite3.Connection,
        source_version_id: int,
        pages: set[int],
    ) -> list[dict[str, Any]]:
        if not pages:
            return []
        marks = ",".join("?" for _ in pages)
        page_rows = connection.execute(
            "SELECT p.*,a.text_content,a.normalized_text,a.text_hash,a.visual_hash,"
            "a.geometry_hash FROM document_pages p LEFT JOIN document_page_artifacts a "
            "ON a.page_id=p.id WHERE p.source_version_id=? AND p.pdf_page_index+1 IN ("
            + marks
            + ") ORDER BY p.pdf_page_index",
            [source_version_id, *sorted(pages)],
        ).fetchall()
        result = []
        for page in page_rows:
            blocks = connection.execute(
                "SELECT id,block_type,raw_text,normalized_layout_text,bbox_json,reading_order,"
                "confidence,evidence_json,issues_json FROM document_page_blocks WHERE page_id=? "
                "ORDER BY reading_order",
                (page["id"],),
            ).fetchall()
            result.append(
                {
                    "page_id": page["id"],
                    "pdf_page_number": int(page["pdf_page_index"]) + 1,
                    "printed_page_number": page["printed_page_number"],
                    "has_text": bool(page["has_text"]),
                    "character_count": page["character_count"],
                    "text_quality": page["text_quality"],
                    "issue_count": page["issue_count"],
                    "text": self._text_evidence(page["text_content"], 6000),
                    "normalized_text": self._text_evidence(page["normalized_text"], 6000),
                    "text_hash": page["text_hash"],
                    "visual_hash": page["visual_hash"],
                    "geometry_hash": page["geometry_hash"],
                    "blocks": [
                        {
                            "id": block["id"],
                            "type": block["block_type"],
                            "raw": self._text_evidence(block["raw_text"], 1200),
                            "normalized": self._text_evidence(
                                block["normalized_layout_text"], 1200
                            ),
                            "bbox": json_load(block["bbox_json"], None),
                            "reading_order": block["reading_order"],
                            "confidence": block["confidence"],
                            "evidence": json_load(block["evidence_json"], {}),
                            "issues": json_load(block["issues_json"], []),
                        }
                        for block in blocks
                    ],
                }
            )
        return result

    def _candidate_export(
        self,
        row: dict[str, Any],
        page_candidates: list[dict[str, Any]],
        decisions: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        index = next(
            (
                value
                for value, candidate in enumerate(page_candidates)
                if candidate["id"] == row["id"]
            ),
            0,
        )
        return {
            "id": row["id"],
            "type": row["candidate_type"],
            "pdf_page_number": row["pdf_page_number"],
            "printed_page_candidate": row["proposed_printed_page"],
            "raw_ocr": self._text_evidence(row["raw_text"], 4000),
            "normalized_layout_text": self._text_evidence(row["normalized_layout_text"], 4000),
            "bbox": json_load(row["bbox_json"], None),
            "position": json_load(row["position_json"], {}),
            "reading_order": row["reading_order"],
            "parent_id": row["parent_candidate_id"],
            "children_ids": [
                value["id"]
                for value in page_candidates
                if value["parent_candidate_id"] == row["id"]
            ],
            "previous_context_ids": [
                value["id"] for value in page_candidates[max(0, index - 2) : index]
            ],
            "next_context_ids": [value["id"] for value in page_candidates[index + 1 : index + 3]],
            "canonical_topic_number": row["canonical_topic_number"],
            "observed_level": row["observed_pedagogical_level"],
            "confidence": row["confidence"],
            "observed_status": row["status"],
            "editorial_state": row["editorial_state"],
            "effective_confidence": row["effective_confidence"],
            "priority": row["priority"],
            "review_reason": row["reason"],
            "primary_candidate_id": row["primary_candidate_id"],
            "evidence": json_load(row["evidence_json"], {}),
            "issues": json_load(row["issues_json"], []),
            "decisions": decisions.get(row["id"], []),
        }

    def _validate_decision(
        self,
        connection: sqlite3.Connection,
        decision: Any,
        ordinal: int,
        export: sqlite3.Row,
        included_targets: dict[str, set[str]],
        package_files: set[str],
    ) -> dict[str, Any]:
        base = {"ordinal": ordinal}
        required = {
            "target_type",
            "target_id",
            "expected_previous_state",
            "action",
            "evidence_references",
            "rationale",
            "confidence",
        }
        if not isinstance(decision, dict) or required - decision.keys():
            return {"bucket": "invalid", "detail": base | {"reason": "decision_fields_invalid"}}
        if decision["action"] not in DECISION_ACTIONS:
            return {"bucket": "invalid", "detail": base | {"reason": "action_not_allowed"}}
        if (
            decision.get("target_type") not in TARGET_ACTIONS
            or decision["action"] not in (TARGET_ACTIONS[decision["target_type"]])
        ):
            return {
                "bucket": "invalid",
                "detail": base | {"reason": "action_not_allowed_for_target"},
            }
        evidence_references = decision.get("evidence_references")
        if (
            not isinstance(evidence_references, list)
            or not evidence_references
            or any(
                not isinstance(reference, str)
                or not reference
                or reference.split("#", 1)[0] not in package_files
                for reference in evidence_references
            )
        ):
            return {
                "bucket": "invalid",
                "detail": base | {"reason": "evidence_references_invalid"},
            }
        if (
            not isinstance(decision["confidence"], (int, float))
            or not 0 <= decision["confidence"] <= 1
        ):
            return {"bucket": "invalid", "detail": base | {"reason": "confidence_invalid"}}
        target = self._target(connection, decision["target_type"], decision["target_id"])
        if target is None:
            return {"bucket": "invalid", "detail": base | {"reason": "target_unknown"}}
        if int(target["source_version_id"]) != int(export["source_version_id"]):
            return {"bucket": "invalid", "detail": base | {"reason": "target_wrong_version"}}
        if decision["target_id"] not in included_targets[decision["target_type"]]:
            return {
                "bucket": "invalid",
                "detail": base | {"reason": "target_not_in_export_scope"},
            }
        current = target["current_state"]
        if current != decision["expected_previous_state"]:
            return {
                "bucket": "stale",
                "detail": base | {"reason": "expected_state_changed", "current_state": current},
            }
        replacement = decision.get("replacement") or {}
        replacement_error = self._validate_replacement(connection, decision, replacement, export)
        if replacement_error:
            return {"bucket": "invalid", "detail": base | {"reason": replacement_error}}
        replacement_candidate = (
            replacement.get("candidate_id")
            or replacement.get("evidence_candidate_id")
            or replacement.get("parent_candidate_id")
        )
        if replacement_candidate and replacement_candidate not in included_targets["candidate"]:
            return {
                "bucket": "invalid",
                "detail": base | {"reason": "replacement_not_in_export_scope"},
            }
        resulting = self._resulting_state(decision["action"], current)
        if resulting == current and decision["action"] not in {
            "change_type",
            "change_parent",
            "adjust_printed_page",
            "classify_visual",
            "choose_primary",
            "resolve_topic_identity",
        }:
            return {"bucket": "no_effect", "detail": base | {"current_state": current}}
        return {
            "bucket": "applicable",
            "detail": base
            | {
                "target_type": decision["target_type"],
                "target_id": decision["target_id"],
                "action": decision["action"],
                "current_state": current,
                "resulting_state": resulting,
            },
        }

    def _package_scope(self, export: sqlite3.Row) -> tuple[dict[str, set[str]], set[str]]:
        relative = export["relative_path"]
        if not relative:
            raise ValueError("export path missing")
        path = (self.exports_root / relative).resolve()
        root = self.exports_root.resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError("export path invalid")
        if sha256_bytes(path.read_bytes()) != export["archive_sha256"]:
            raise ValueError("export hash mismatch")
        names = set()
        targets = {target_type: set() for target_type in TARGET_ACTIONS}
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            for target_type, filename in (
                ("candidate", "candidates.json"),
                ("topic", "topics.json"),
                ("relation", "exercise_solution_relations.json"),
                ("visual", "visual_pending.json"),
            ):
                values = json.loads(archive.read(filename))
                targets[target_type] = {str(value["id"]) for value in values}
        return targets, names

    def _apply_external_decision(
        self,
        connection: sqlite3.Connection,
        decision: dict[str, Any],
        ordinal: int,
        import_id: str,
        decision_set: dict[str, Any],
        decision_set_hash: str,
        now: str,
    ) -> tuple[str | None, str]:
        target = self._target(connection, decision["target_type"], decision["target_id"])
        if target is None or target["current_state"] != decision["expected_previous_state"]:
            raise LibraryContractError(f"La decisión {ordinal} se volvió stale durante apply.")
        action = decision["action"]
        replacement = decision.get("replacement") or {}
        index_only_resolution = (
            decision["target_type"] == "topic"
            and action == "resolve_topic_identity"
            and replacement.get("identity_resolution") == "index_only_no_direct_practice"
        )
        resulting = self._resulting_state(action, target["current_state"])
        candidate_id: str | None = None
        if decision["target_type"] == "candidate":
            candidate_id = decision["target_id"]
        elif decision["target_type"] == "topic" and action in {
            "choose_primary",
            "resolve_topic_identity",
        }:
            candidate_id = (
                replacement["evidence_candidate_id"]
                if index_only_resolution
                else replacement["candidate_id"]
            )
        candidate_decision_id = None
        if candidate_id:
            state_row = connection.execute(
                "SELECT * FROM document_candidate_review_state WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            candidate = connection.execute(
                "SELECT confidence FROM document_structure_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            previous = state_row["editorial_state"] if state_row else "proposed"
            confidence_before = (
                state_row["effective_confidence"] if state_row else candidate["confidence"]
            )
            candidate_result = (
                previous
                if index_only_resolution
                else resulting
                if decision["target_type"] == "candidate"
                else "confirmed"
            )
            candidate_decision_id = str(uuid4())
            connection.execute(
                "INSERT INTO document_candidate_decisions("
                "id,candidate_id,actor,method,rule,previous_state,new_state,confidence_before,"
                "confidence_after,evidence_json,comment,created_at) VALUES (?,?,?,"
                "'external_audit',?,?,?,?,?,?,?,?)",
                (
                    candidate_decision_id,
                    candidate_id,
                    decision_set["reviewer"],
                    f"external:{action}",
                    previous,
                    candidate_result,
                    confidence_before,
                    confidence_before if index_only_resolution else decision["confidence"],
                    json_dump(
                        {
                            "import_id": import_id,
                            "package_logical_hash": decision_set["package_logical_hash"],
                            "decision_set_hash": decision_set_hash,
                            "document_hash": decision_set["document_hash"],
                            "target_type": decision["target_type"],
                            "target_id": decision["target_id"],
                            "evidence_references": decision["evidence_references"],
                            "rationale": decision["rationale"],
                            "replacement": replacement,
                        }
                    ),
                    decision.get("note"),
                    now,
                ),
            )
            if index_only_resolution:
                connection.execute(
                    "UPDATE document_candidate_review_state SET latest_decision_id=?,updated_at=? "
                    "WHERE candidate_id=?",
                    (candidate_decision_id, now, candidate_id),
                )
            else:
                connection.execute(
                    "INSERT INTO document_candidate_review_state("
                    "candidate_id,editorial_state,effective_confidence,latest_decision_id,"
                    "priority,reason,updated_at) VALUES (?,?,?,?,NULL,?,?) "
                    "ON CONFLICT(candidate_id) DO UPDATE SET "
                    "editorial_state=excluded.editorial_state,"
                    "effective_confidence=excluded.effective_confidence,"
                    "latest_decision_id=excluded.latest_decision_id,priority=NULL,"
                    "reason=excluded.reason,updated_at=excluded.updated_at",
                    (
                        candidate_id,
                        candidate_result,
                        decision["confidence"],
                        candidate_decision_id,
                        f"external:{action}",
                        now,
                    ),
                )
        if index_only_resolution:
            topic = connection.execute(
                "SELECT evidence_json,issues_json FROM document_consolidated_topics WHERE id=?",
                (decision["target_id"],),
            ).fetchone()
            evidence = json_load(topic["evidence_json"], {})
            evidence.update(
                {
                    "topic_identity_resolved": True,
                    "identity_source": "index",
                    "principal_body_heading_required": False,
                    "direct_exercises_expected": False,
                    "direct_solutions_expected": False,
                    "identity_evidence_candidate_id": replacement["evidence_candidate_id"],
                    "review_method": "external_audit",
                    "package_logical_hash": decision_set["package_logical_hash"],
                    "document_hash": decision_set["document_hash"],
                    "reviewer": decision_set["reviewer"],
                    "rationale": decision["rationale"],
                }
            )
            issues = [
                issue
                for issue in json_load(topic["issues_json"], [])
                if issue != "principal_topic_evidence_insufficient"
            ]
            connection.execute(
                "UPDATE document_consolidated_topics SET state='confirmed',"
                "identity_resolution='index_only_no_direct_practice',"
                "identity_evidence_candidate_id=?,resolution_method='external_audit',"
                "primary_candidate_id=NULL,pdf_page_number=NULL,printed_page=NULL,confidence=?,"
                "evidence_json=?,issues_json=? WHERE id=?",
                (
                    replacement["evidence_candidate_id"],
                    decision["confidence"],
                    json_dump(evidence),
                    json_dump(issues),
                    decision["target_id"],
                ),
            )
            resulting = "confirmed"
        elif decision["target_type"] == "topic" and action in {
            "choose_primary",
            "resolve_topic_identity",
        }:
            candidate = connection.execute(
                "SELECT c.*,p.pdf_page_index+1 pdf_page_number FROM "
                "document_structure_candidates c JOIN document_pages p ON p.id=c.page_id "
                "WHERE c.id=?",
                (replacement["candidate_id"],),
            ).fetchone()
            connection.execute(
                "UPDATE document_consolidated_topics SET state='confirmed',primary_candidate_id=?,"
                "pdf_page_number=?,printed_page=?,confidence=?,"
                "identity_resolution='body_practice',identity_evidence_candidate_id=?,"
                "resolution_method='external_audit' WHERE id=?",
                (
                    candidate["id"],
                    candidate["pdf_page_number"],
                    candidate["proposed_printed_page"],
                    decision["confidence"],
                    candidate["id"],
                    decision["target_id"],
                ),
            )
            topic = connection.execute(
                "SELECT review_run_id,theme_number FROM document_consolidated_topics WHERE id=?",
                (decision["target_id"],),
            ).fetchone()
            connection.execute(
                "UPDATE document_consolidated_nodes SET source_candidate_id=?,pdf_page_number=?,"
                "state='confirmed' WHERE review_run_id=? AND theme_number=? "
                "AND node_type='topic_heading'",
                (
                    candidate["id"],
                    candidate["pdf_page_number"],
                    topic["review_run_id"],
                    topic["theme_number"],
                ),
            )
            resulting = "confirmed"
        elif decision["target_type"] == "topic":
            connection.execute(
                "UPDATE document_consolidated_topics SET state=? WHERE id=?",
                (resulting, decision["target_id"]),
            )
        elif decision["target_type"] == "relation":
            connection.execute(
                "UPDATE document_exercise_solution_relations SET state=? WHERE id=?",
                (resulting, decision["target_id"]),
            )
        elif decision["target_type"] == "visual":
            connection.execute(
                "UPDATE document_visual_page_classifications SET classification=?,state='confirmed',"
                "confidence=? WHERE id=?",
                (replacement["classification"], decision["confidence"], decision["target_id"]),
            )
            resulting = "confirmed"
        elif decision["target_type"] == "candidate":
            if action == "change_type":
                connection.execute(
                    "UPDATE document_consolidated_nodes SET node_type=? WHERE source_candidate_id=?",
                    (replacement["candidate_type"], decision["target_id"]),
                )
            elif action == "change_parent":
                parent = connection.execute(
                    "SELECT id FROM document_consolidated_nodes WHERE source_candidate_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (replacement["parent_candidate_id"],),
                ).fetchone()
                connection.execute(
                    "UPDATE document_consolidated_nodes SET parent_node_id=? WHERE source_candidate_id=?",
                    (parent["id"], decision["target_id"]),
                )
            elif action == "adjust_printed_page":
                connection.execute(
                    "UPDATE document_consolidated_topics SET printed_page=? "
                    "WHERE primary_candidate_id=?",
                    (replacement["printed_page"], decision["target_id"]),
                )
        return candidate_decision_id, resulting

    def _insert_readiness(
        self, connection: sqlite3.Connection, source_version_id: int, *, now: str
    ) -> dict[str, Any]:
        version = self._version(connection, source_version_id)
        run = connection.execute(
            "SELECT id FROM document_review_runs WHERE source_version_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (source_version_id,),
        ).fetchone()
        pages = connection.execute(
            "SELECT count(*) total,sum(coalesce(has_text,0)=1) text_pages,sum(issue_count>0) issues "
            "FROM document_pages WHERE source_version_id=?",
            (source_version_id,),
        ).fetchone()
        topic_counts = dict(
            connection.execute(
                "SELECT state,count(*) FROM document_consolidated_topics WHERE review_run_id=? "
                "GROUP BY state",
                (run["id"],),
            ).fetchall()
        )
        unresolved = int(
            connection.execute(
                "SELECT count(*) FROM document_consolidated_topics WHERE review_run_id=? "
                "AND (identity_resolution='unresolved' OR state='conflicted')",
                (run["id"],),
            ).fetchone()[0]
        )
        index_only_topics = int(
            connection.execute(
                "SELECT count(*) FROM document_consolidated_topics WHERE review_run_id=? "
                "AND identity_resolution='index_only_no_direct_practice'",
                (run["id"],),
            ).fetchone()[0]
        )
        queue = dict(
            connection.execute(
                "SELECT coalesce(s.priority,'P2'),count(*) FROM document_candidate_review_state s "
                "JOIN document_structure_candidates c ON c.id=s.candidate_id "
                "WHERE c.source_version_id=? AND s.editorial_state IN ('needs_review','conflicted') "
                "GROUP BY 1",
                (source_version_id,),
            ).fetchall()
        )
        visuals = int(
            connection.execute(
                "SELECT count(*) FROM document_visual_page_classifications WHERE review_run_id=? "
                "AND state='needs_review'",
                (run["id"],),
            ).fetchone()[0]
        )
        blockers = []
        if int(pages["total"] or 0) != int(version["page_count"] or 0):
            blockers.append("pages_not_fully_materialized")
        if unresolved:
            blockers.append(f"principal_topics_unresolved:{unresolved}")
        if queue.get("P0", 0):
            blockers.append(f"p0_review_items:{queue['P0']}")
        state = (
            "blocked"
            if blockers
            else "structurally_ready_with_issues"
            if sum(queue.values()) or visuals
            else "structurally_ready"
        )
        metrics = {
            "pages_expected": int(version["page_count"] or 0),
            "pages_materialized": int(pages["total"] or 0),
            "pages_with_text": int(pages["text_pages"] or 0),
            "pages_with_incident": int(pages["issues"] or 0),
            "topics_expected": 51,
            "topics_located": 51 - unresolved,
            "topics_auto_supported": topic_counts.get("auto_supported", 0),
            "topics_confirmed": topic_counts.get("confirmed", 0),
            "topics_conflicted": topic_counts.get("conflicted", 0),
            "topics_index_only_no_direct_practice": index_only_topics,
            "visual_pages_pending": visuals,
            "queue": {"P0": queue.get("P0", 0), "P1": queue.get("P1", 0), "P2": queue.get("P2", 0)},
        }
        snapshot_id = str(uuid4())
        rationale = {
            "recalculation": "incremental_audit_projection",
            "ocr": False,
            "llm": False,
            "chunking": False,
            "embeddings": False,
            "activation": False,
        }
        connection.execute(
            "INSERT INTO document_readiness_snapshots("
            "id,review_run_id,source_version_id,state,metrics_json,blockers_json,rationale_json,"
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                snapshot_id,
                run["id"],
                source_version_id,
                state,
                json_dump(metrics),
                json_dump(blockers),
                json_dump(rationale),
                now,
            ),
        )
        return {
            "id": snapshot_id,
            "review_run_id": run["id"],
            "source_version_id": source_version_id,
            "state": state,
            "metrics": metrics,
            "blockers": blockers,
            "rationale": rationale,
            "created_at": now,
        }

    @staticmethod
    def _target(
        connection: sqlite3.Connection, target_type: str, target_id: str
    ) -> dict[str, Any] | None:
        queries = {
            "candidate": (
                "SELECT c.source_version_id,s.editorial_state current_state FROM "
                "document_structure_candidates c LEFT JOIN document_candidate_review_state s "
                "ON s.candidate_id=c.id WHERE c.id=?"
            ),
            "topic": "SELECT source_version_id,state current_state FROM document_consolidated_topics WHERE id=?",
            "relation": "SELECT source_version_id,state current_state FROM document_exercise_solution_relations WHERE id=?",
            "visual": "SELECT source_version_id,state current_state FROM document_visual_page_classifications WHERE id=?",
        }
        if target_type not in queries:
            return None
        row = connection.execute(queries[target_type], (target_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _validate_replacement(
        connection: sqlite3.Connection,
        decision: dict[str, Any],
        replacement: dict[str, Any],
        export: sqlite3.Row,
    ) -> str | None:
        action = decision["action"]
        if action == "choose_primary":
            candidate_id = replacement.get("candidate_id")
            row = connection.execute(
                "SELECT source_version_id FROM document_structure_candidates WHERE id=?",
                (candidate_id,),
            ).fetchone()
            if row is None or row["source_version_id"] != export["source_version_id"]:
                return "replacement_candidate_invalid"
        elif action == "resolve_topic_identity":
            resolution = replacement.get("identity_resolution", "body_practice")
            if resolution == "body_practice":
                candidate_id = replacement.get("candidate_id")
                row = connection.execute(
                    "SELECT source_version_id FROM document_structure_candidates WHERE id=?",
                    (candidate_id,),
                ).fetchone()
                if row is None or row["source_version_id"] != export["source_version_id"]:
                    return "replacement_candidate_invalid"
            elif resolution == "index_only_no_direct_practice":
                candidate_id = replacement.get("evidence_candidate_id")
                row = connection.execute(
                    "SELECT c.source_version_id,c.candidate_type,c.canonical_topic_number,"
                    "t.theme_number,s.editorial_state,s.reason FROM document_structure_candidates c "
                    "JOIN document_consolidated_topics t ON t.id=? "
                    "LEFT JOIN document_candidate_review_state s ON s.candidate_id=c.id "
                    "WHERE c.id=?",
                    (decision["target_id"], candidate_id),
                ).fetchone()
                if row is None or row["source_version_id"] != export["source_version_id"]:
                    return "index_evidence_candidate_invalid"
                if row["candidate_type"] != "topic_heading":
                    return "index_evidence_must_be_topic_heading"
                if row["canonical_topic_number"] != row["theme_number"]:
                    return "index_evidence_topic_mismatch"
                if row["editorial_state"] != "rejected" or row["reason"] != (
                    "index_entry_not_principal_topic"
                ):
                    return "index_evidence_not_rejected_as_index_entry"
            else:
                return "identity_resolution_invalid"
        elif action == "change_type" and not replacement.get("candidate_type"):
            return "replacement_type_required"
        elif action == "change_parent":
            parent = connection.execute(
                "SELECT source_version_id FROM document_structure_candidates WHERE id=?",
                (replacement.get("parent_candidate_id"),),
            ).fetchone()
            if parent is None or parent["source_version_id"] != export["source_version_id"]:
                return "replacement_parent_invalid"
        elif action == "adjust_printed_page" and not isinstance(
            replacement.get("printed_page"), str
        ):
            return "printed_page_required"
        elif (
            action == "classify_visual" and replacement.get("classification") not in VISUAL_CLASSES
        ):
            return "visual_classification_invalid"
        return None

    @staticmethod
    def _resulting_state(action: str, current: str) -> str:
        return {
            "confirm": "confirmed",
            "reject": "rejected",
            "support": "auto_supported",
            "mark_conflicted": "conflicted",
            "confirm_relation": "confirmed",
            "reject_relation": "rejected",
            "classify_visual": "confirmed",
            "choose_primary": "confirmed",
            "resolve_topic_identity": "confirmed",
        }.get(action, current)

    @staticmethod
    def _ai_readiness(readiness: dict[str, Any]) -> str:
        state = readiness.get("state") or readiness.get("structural_readiness")
        metrics = readiness.get("metrics", {})
        blockers = readiness.get("blockers", [])
        if state == "blocked" or blockers:
            return "blocked_for_ai"
        if metrics.get("pages_materialized") != metrics.get("pages_expected"):
            return "not_ready_for_ai"
        if state == "structurally_ready":
            return "ready_for_ai"
        if state == "structurally_ready_with_issues":
            return "ready_for_ai_with_issues"
        return "not_ready_for_ai"

    def _version(self, connection: sqlite3.Connection, source_version_id: int) -> sqlite3.Row:
        row = connection.execute(
            "SELECT v.*,s.name,s.kind,s.format,coalesce(s.display_alias,s.canonical_title,s.name) "
            "title,(SELECT id FROM document_processing_runs r WHERE r.source_version_id=v.id "
            "ORDER BY created_at DESC LIMIT 1) latest_run_id FROM source_versions v "
            "JOIN sources s ON s.id=v.source_id WHERE v.id=?",
            (source_version_id,),
        ).fetchone()
        if row is None:
            raise LibraryNotFoundError("Versión documental no encontrada.")
        return row

    @staticmethod
    def _latest_readiness(connection: sqlite3.Connection, source_version_id: int) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM document_readiness_snapshots WHERE source_version_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (source_version_id,),
        ).fetchone()
        if row is None:
            raise LibraryContractError("La versión no tiene readiness estructural.")
        return row

    @staticmethod
    def _pipeline_versions(
        connection: sqlite3.Connection, source_version_id: int
    ) -> dict[str, Any]:
        runs = connection.execute(
            "SELECT pipeline_version,configuration_hash,target_hash FROM document_processing_runs "
            "WHERE source_version_id=? ORDER BY created_at",
            (source_version_id,),
        ).fetchall()
        review = connection.execute(
            "SELECT rule_version,configuration_hash,source_hash FROM document_review_runs "
            "WHERE source_version_id=? ORDER BY created_at DESC LIMIT 1",
            (source_version_id,),
        ).fetchone()
        return {
            "processing": [dict(row) for row in runs],
            "review": dict(review) if review else None,
            "audit_package": PACKAGE_SCHEMA,
        }

    @staticmethod
    def _normalize_selection(selection: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if selection.get("theme_number") is not None:
            number = int(selection["theme_number"])
            if not 1 <= number <= 51:
                raise LibraryContractError("theme_number debe estar entre 1 y 51.")
            result["theme_number"] = number
        for key in ("pages", "candidate_ids", "relation_ids", "review_item_ids"):
            if selection.get(key):
                values = selection[key]
                if not isinstance(values, list):
                    raise LibraryContractError(f"{key} debe ser una lista.")
                result[key] = sorted(set(values))
        if selection.get("include_p2"):
            result["include_p2"] = True
        return result

    @staticmethod
    def _selected_themes(selection: dict[str, Any]) -> set[int]:
        if not selection.get("theme_number"):
            return set()
        value = int(selection["theme_number"])
        return {number for number in (value - 1, value, value + 1) if 1 <= number <= 51}

    @staticmethod
    def _text_evidence(text: str | None, limit: int) -> dict[str, Any] | None:
        if text is None:
            return None
        encoded = text.encode("utf-8")
        return {
            "excerpt": text if len(text) <= limit else text[:limit],
            "sha256": sha256_bytes(encoded),
            "byte_length": len(encoded),
            "truncated": len(text) > limit,
            "lookup": "Use candidate/page ID, PDF page and bbox against the separately supplied PDF.",
        }

    @staticmethod
    def _decision_export(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["evidence"] = json_load(result.pop("evidence_json"), {})
        return result

    @staticmethod
    def _json_row(row: sqlite3.Row, json_fields: tuple[str, ...]) -> dict[str, Any]:
        result = dict(row)
        for field in json_fields:
            target = field.removesuffix("_json")
            result[target] = json_load(
                result.pop(field), [] if "ids" in field or "issues" in field else {}
            )
        return result

    @staticmethod
    def _encode_payload(package: dict[str, Any]) -> dict[str, bytes]:
        result = {}
        for name in PAYLOAD_FILES:
            value = package[name]
            result[name] = (
                value.encode("utf-8") if isinstance(value, str) else canonical_bytes(value)
            )
        return result

    @staticmethod
    def _write_zip(path: Path, payload: dict[str, bytes]) -> None:
        with zipfile.ZipFile(
            path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name in sorted(payload):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, payload[name])

    def _git_commit(self) -> str | None:
        root = self.database.path.parents[2]
        head = root / ".git" / "HEAD"
        if not head.is_file():
            return None
        value = head.read_text(encoding="utf-8").strip()
        if value.startswith("ref: "):
            ref = root / ".git" / value[5:]
            return ref.read_text(encoding="utf-8").strip() if ref.is_file() else None
        return value or None

    @staticmethod
    def _summary_markdown(
        source: dict[str, Any],
        version: dict[str, Any],
        mode: str,
        counts: dict[str, int],
        readiness: dict[str, Any],
        selection: dict[str, Any],
    ) -> str:
        return (
            "# LLC Document Audit Package\n\n"
            f"- Source: {source['title']}\n"
            f"- Source version: {version['source_version_id']} / v{version['version_number']}\n"
            f"- Document SHA-256: `{version['content_hash']}`\n"
            f"- Export mode: `{mode}`\n"
            f"- Selection: `{json_dump(selection)}`\n"
            f"- Structural readiness: `{readiness['state']}`\n"
            f"- Blockers: `{json_dump(readiness['blockers'])}`\n"
            f"- Counts: `{json_dump(counts)}`\n\n"
            "The PDF is intentionally excluded. SHA-256 proves integrity, not authenticity.\n"
        )

    @staticmethod
    def _readiness_read(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metrics"] = json_load(result.pop("metrics_json"), {})
        result["blockers"] = json_load(result.pop("blockers_json"), [])
        result["rationale"] = json_load(result.pop("rationale_json"), {})
        return result

    @staticmethod
    def _export_read(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["include_visuals"] = bool(result["include_visuals"])
        result["selection"] = json_load(result.pop("selection_json"), {})
        result["manifest"] = json_load(result.pop("manifest_json"), {})
        result["counts"] = json_load(result.pop("counts_json"), {})
        return result

    @staticmethod
    def _import_read(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["validation"] = json_load(result.pop("validation_json"), {})
        result["impact"] = json_load(result.pop("impact_json"), {})
        return result

    @staticmethod
    def _import_decision_read(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["replacement"] = json_load(result.pop("replacement_json"), {})
        result["evidence_references"] = json_load(result.pop("evidence_references_json"), [])
        return result

    @staticmethod
    def _snapshot_read(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for field, fallback in (
            ("coverage_json", {}),
            ("unresolved_json", []),
            ("blocking_json", []),
            ("pipeline_versions_json", {}),
            ("snapshot_payload_json", {}),
        ):
            result[field.removesuffix("_json")] = json_load(result.pop(field), fallback)
        return result
