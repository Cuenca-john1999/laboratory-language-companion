from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter, defaultdict
from typing import Any
from uuid import uuid4

from .database import LibraryDatabase
from .schemas import LibraryContractError, LibraryNotFoundError
from .service import json_dump, json_load, utc_text

RULE_VERSION = "document-review.v1"
EDITORIAL_STATES = {
    "proposed",
    "auto_supported",
    "needs_review",
    "confirmed",
    "rejected",
    "conflicted",
    "superseded",
}
DECISION_METHODS = {
    "deterministic_rule",
    "cross_evidence",
    "canonical_match",
    "visual_review",
    "manual_review",
    "batch_manual",
    "external_audit",
}
_TOPIC_TOKEN = re.compile(r"\btema\s*([1-9]|[1-4]\d|5[01])\b", re.IGNORECASE)
_BARE_TOPIC = re.compile(r"^\s*tema\s*([1-9]|[1-4]\d|5[01])\s*[.:;-]?\s*$", re.I)
_LEVEL_TOKEN = re.compile(r"^\s*(?:nivel\s+)?([GMO])\s*$", re.IGNORECASE)
_ITEM_TOKEN = re.compile(r"^\s*((?:\d{1,3}|[IVX]{1,6}|[A-ZÄÖÜ])[.)])\s*", re.I)
_EXERCISE_WORD = re.compile(r"\b(?:ejercicios?|übungen?|aufgaben?)\b", re.I)
_SOLUTION_WORD = re.compile(r"\b(?:soluciones?|lösungen?)\b", re.I)


class DocumentReviewService:
    """Auditable deterministic review. It never invokes OCR, models or indexing."""

    def __init__(self, database: LibraryDatabase):
        self.database = database

    def consolidate(self, source_version_id: int, *, actor: str = "system") -> dict[str, Any]:
        configuration = {
            "ocr": False,
            "llm": False,
            "vision_ai": False,
            "chunking": False,
            "embeddings": False,
            "activation": False,
            "topic_count": 51,
        }
        configuration_hash = hashlib.sha256(json_dump(configuration).encode()).hexdigest()
        now = utc_text()
        review_run_id = str(uuid4())
        with self.database.transaction(immediate=True) as connection:
            version = self._version(connection, source_version_id)
            candidates = self._candidate_rows(connection, source_version_id)
            if not candidates:
                raise LibraryContractError("La versión no tiene candidatos estructurales actuales.")
            before = self._base_metrics(candidates)
            connection.execute(
                "INSERT INTO document_review_runs("
                "id,source_id,source_version_id,source_hash,rule_version,configuration_json,"
                "configuration_hash,state,before_metrics_json,after_metrics_json,started_at,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,'running',?,'{}',?,?,?)",
                (
                    review_run_id,
                    version["source_id"],
                    source_version_id,
                    version["content_hash"],
                    RULE_VERSION,
                    json_dump(configuration),
                    configuration_hash,
                    json_dump(before),
                    now,
                    now,
                    now,
                ),
            )
            self._initialize_states(connection, candidates, now)
            deduplicated = self._deduplicate(
                connection, review_run_id, candidates, actor=actor, now=now
            )
            topic_result = self._review_topics(
                connection, review_run_id, candidates, actor=actor, now=now
            )
            rule_counts = self._review_other_candidates(
                connection, review_run_id, candidates, actor=actor, now=now
            )
            visual = self._classify_visual_pages(
                connection, review_run_id, source_version_id, now=now
            )
            self._build_topics(
                connection,
                review_run_id,
                source_version_id,
                candidates,
                topic_result,
                now=now,
            )
            hierarchy = self._build_hierarchy(
                connection, review_run_id, source_version_id, candidates, now=now
            )
            relations = self._build_relations(
                connection, review_run_id, source_version_id, candidates, now=now
            )
            batches = self._build_safe_batches(
                connection, review_run_id, source_version_id, now=now
            )
            readiness = self._calculate_readiness(
                connection, review_run_id, source_version_id, now=now
            )
            after = self._review_metrics(connection, source_version_id, review_run_id)
            after.update(
                {
                    "deduplicated": deduplicated,
                    "rule_decisions": rule_counts,
                    "visual_pages": visual,
                    "hierarchy_nodes": hierarchy,
                    "exercise_solution_relations": relations,
                    "batches": batches,
                    "readiness": readiness["state"],
                }
            )
            issue_count = after.get("needs_review", 0) + after.get("conflicted", 0)
            state = "completed_with_issues" if issue_count else "completed"
            connection.execute(
                "UPDATE document_review_runs SET state=?,after_metrics_json=?,completed_at=?,"
                "updated_at=? WHERE id=?",
                (state, json_dump(after), now, now, review_run_id),
            )
        return self.run(review_run_id)

    def summary(self, source_version_id: int | None = None) -> dict[str, Any]:
        with self.database.connect() as connection:
            versions = (
                [source_version_id]
                if source_version_id is not None
                else [
                    int(row["id"])
                    for row in connection.execute(
                        "SELECT id FROM source_versions WHERE activation_state='candidate' "
                        "ORDER BY id"
                    ).fetchall()
                ]
            )
            items = []
            for version_id in versions:
                version = self._version(connection, version_id)
                latest = connection.execute(
                    "SELECT * FROM document_review_runs WHERE source_version_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (version_id,),
                ).fetchone()
                readiness = connection.execute(
                    "SELECT * FROM document_readiness_snapshots WHERE source_version_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (version_id,),
                ).fetchone()
                metrics = self._review_metrics(
                    connection, version_id, latest["id"] if latest else None
                )
                queue = dict(
                    connection.execute(
                        "SELECT coalesce(priority,'P2') priority,count(*) count "
                        "FROM document_candidate_review_state s JOIN document_structure_candidates c "
                        "ON c.id=s.candidate_id WHERE c.source_version_id=? "
                        "AND s.editorial_state IN ('needs_review','conflicted') GROUP BY priority",
                        (version_id,),
                    ).fetchall()
                )
                items.append(
                    {
                        "source_id": version["source_id"],
                        "source_version_id": version_id,
                        "source_title": version["title"],
                        "version_number": version["version_number"],
                        "hash": version["content_hash"],
                        "activation_state": version["activation_state"],
                        "run_id": latest["id"] if latest else None,
                        "run_state": latest["state"] if latest else "not_started",
                        "readiness": readiness["state"] if readiness else "not_ready",
                        "metrics": metrics,
                        "queue": {
                            "P0": queue.get("P0", 0),
                            "P1": queue.get("P1", 0),
                            "P2": queue.get("P2", 0),
                        },
                        "blockers": json_load(readiness["blockers_json"], []) if readiness else [],
                    }
                )
        return {"items": items}

    def run(self, review_run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_review_runs WHERE id=?", (review_run_id,)
            ).fetchone()
            if row is None:
                raise LibraryNotFoundError("Ejecución de revisión no encontrada.")
            return self._run_read(row)

    def queue(
        self,
        *,
        source_version_id: int | None = None,
        priority: str | None = None,
        candidate_type: str | None = None,
        theme_number: int | None = None,
        pdf_page_number: int | None = None,
        reason: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        clauses = ["s.editorial_state IN ('needs_review','conflicted')", "c.superseded_at IS NULL"]
        values: list[object] = []
        for column, value in (
            ("c.source_version_id", source_version_id),
            ("s.priority", priority),
            ("c.candidate_type", candidate_type),
            ("c.canonical_topic_number", theme_number),
            ("p.pdf_page_index", pdf_page_number - 1 if pdf_page_number else None),
            ("s.reason", reason),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                values.append(value)
        where = " AND ".join(clauses)
        with self.database.connect() as connection:
            total = int(
                connection.execute(
                    "SELECT count(*) FROM document_candidate_review_state s "
                    "JOIN document_structure_candidates c ON c.id=s.candidate_id "
                    "JOIN document_pages p ON p.id=c.page_id WHERE " + where,
                    values,
                ).fetchone()[0]
            )
            rows = connection.execute(
                "SELECT c.id,c.source_id,c.source_version_id,c.candidate_type,c.raw_text,"
                "c.canonical_topic_number,c.observed_pedagogical_level,c.confidence,"
                "p.pdf_page_index+1 pdf_page_number,s.editorial_state,s.effective_confidence,"
                "coalesce(s.priority,'P2') priority,s.reason,s.primary_candidate_id,"
                "json_array_length(c.issues_json)>0 has_issue FROM document_candidate_review_state s "
                "JOIN document_structure_candidates c ON c.id=s.candidate_id "
                "JOIN document_pages p ON p.id=c.page_id WHERE "
                + where
                + " ORDER BY CASE s.priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END,"
                "c.source_version_id,p.pdf_page_index,c.reading_order LIMIT ? OFFSET ?",
                [*values, page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "items": [dict(row) | {"has_issue": bool(row["has_issue"])} for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size,
        }

    def item(self, candidate_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT c.*,p.pdf_page_index+1 pdf_page_number,s.editorial_state,"
                "s.effective_confidence,s.primary_candidate_id,s.priority,s.reason "
                "FROM document_structure_candidates c JOIN document_pages p ON p.id=c.page_id "
                "LEFT JOIN document_candidate_review_state s ON s.candidate_id=c.id WHERE c.id=?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise LibraryNotFoundError("Candidato de revisión no encontrado.")
            decisions = connection.execute(
                "SELECT * FROM document_candidate_decisions WHERE candidate_id=? ORDER BY created_at",
                (candidate_id,),
            ).fetchall()
            neighbors = connection.execute(
                "SELECT id,candidate_type,raw_text,reading_order FROM document_structure_candidates "
                "WHERE page_id=? AND superseded_at IS NULL AND reading_order BETWEEN ? AND ? "
                "ORDER BY reading_order",
                (
                    row["page_id"],
                    max(0, int(row["reading_order"]) - 2),
                    int(row["reading_order"]) + 2,
                ),
            ).fetchall()
            parent = (
                connection.execute(
                    "SELECT id,candidate_type,raw_text,reading_order FROM document_structure_candidates "
                    "WHERE id=?",
                    (row["parent_candidate_id"],),
                ).fetchone()
                if row["parent_candidate_id"]
                else None
            )
            children = connection.execute(
                "SELECT id,candidate_type,raw_text,reading_order FROM document_structure_candidates "
                "WHERE parent_candidate_id=? AND superseded_at IS NULL ORDER BY reading_order LIMIT 50",
                (candidate_id,),
            ).fetchall()
            result = dict(row)
            for source, target, fallback in (
                ("bbox_json", "bbox", None),
                ("evidence_json", "evidence", {}),
                ("issues_json", "issues", []),
                ("configuration_json", "configuration", {}),
            ):
                result[target] = json_load(result.pop(source), fallback)
            result["needs_review"] = bool(result["needs_review"])
            result["decisions"] = [self._decision_read(value) for value in decisions]
            result["neighbors"] = [dict(value) for value in neighbors]
            result["parent"] = dict(parent) if parent else None
            result["children"] = [dict(value) for value in children]
            result["page_context"] = {
                "previous_pdf_page": max(1, int(row["pdf_page_number"]) - 1),
                "current_pdf_page": int(row["pdf_page_number"]),
                "next_pdf_page": int(row["pdf_page_number"]) + 1,
            }
            return result

    def decide(
        self,
        candidate_id: str,
        *,
        action: str,
        actor: str,
        method: str,
        comment: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state_by_action = {
            "support": "auto_supported",
            "confirm": "confirmed",
            "reject": "rejected",
            "conflict": "conflicted",
            "supersede": "superseded",
        }
        if action not in state_by_action or method not in DECISION_METHODS:
            raise LibraryContractError("Acción o método editorial no permitido.")
        if method in {"deterministic_rule", "canonical_match", "cross_evidence"}:
            raise LibraryContractError(
                "Los métodos automáticos solo se aplican durante consolidación."
            )
        with self.database.transaction(immediate=True) as connection:
            candidate = connection.execute(
                "SELECT * FROM document_structure_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if candidate is None:
                raise LibraryNotFoundError("Candidato de revisión no encontrado.")
            decision_id = self._apply_state(
                connection,
                candidate,
                state_by_action[action],
                method=method,
                rule=f"explicit_{action}",
                actor=actor,
                confidence=float(candidate["confidence"]),
                priority="P0" if action == "conflict" else None,
                reason=f"explicit_{action}",
                evidence=evidence or {},
                comment=comment,
                now=utc_text(),
            )
            row = connection.execute(
                "SELECT * FROM document_candidate_decisions WHERE id=?", (decision_id,)
            ).fetchone()
        return self._decision_read(row)

    def revert(self, decision_id: str, *, actor: str, comment: str | None = None) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            decision = connection.execute(
                "SELECT * FROM document_candidate_decisions WHERE id=?", (decision_id,)
            ).fetchone()
            if decision is None:
                raise LibraryNotFoundError("Decisión no encontrada.")
            candidate = connection.execute(
                "SELECT * FROM document_structure_candidates WHERE id=?",
                (decision["candidate_id"],),
            ).fetchone()
            current = connection.execute(
                "SELECT editorial_state,effective_confidence FROM document_candidate_review_state "
                "WHERE candidate_id=?",
                (decision["candidate_id"],),
            ).fetchone()
            if current is None or current["editorial_state"] != decision["new_state"]:
                raise LibraryContractError(
                    "La decisión ya no es el estado vigente y no puede revertirse."
                )
            now = utc_text()
            inverse_id = str(uuid4())
            connection.execute(
                "INSERT INTO document_candidate_decisions("
                "id,candidate_id,actor,method,rule,previous_state,new_state,confidence_before,"
                "confidence_after,evidence_json,comment,reverts_decision_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    inverse_id,
                    candidate["id"],
                    actor,
                    "manual_review",
                    "logical_revert",
                    current["editorial_state"],
                    decision["previous_state"],
                    current["effective_confidence"],
                    decision["confidence_before"],
                    json_dump({"reverted_decision_id": decision_id}),
                    comment,
                    decision_id,
                    now,
                ),
            )
            connection.execute(
                "UPDATE document_candidate_review_state SET editorial_state=?,"
                "effective_confidence=?,latest_decision_id=?,priority=NULL,reason='logical_revert',"
                "updated_at=? WHERE candidate_id=?",
                (
                    decision["previous_state"],
                    decision["confidence_before"],
                    inverse_id,
                    now,
                    candidate["id"],
                ),
            )
            row = connection.execute(
                "SELECT * FROM document_candidate_decisions WHERE id=?", (inverse_id,)
            ).fetchone()
        return self._decision_read(row)

    def batches(self, source_version_id: int | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM document_review_batches "
                + ("WHERE source_version_id=? " if source_version_id else "")
                + "ORDER BY created_at DESC",
                (source_version_id,) if source_version_id else (),
            ).fetchall()
            return [self._batch_read(row) for row in rows]

    def create_batch(
        self,
        candidate_ids: list[str],
        *,
        action: str,
        minimum_confidence: float,
    ) -> dict[str, Any]:
        if len(set(candidate_ids)) < 2:
            raise LibraryContractError("Un lote requiere al menos dos candidatos distintos.")
        action_map = {
            "support": "auto_support",
            "confirm": "confirm",
            "reject": "reject",
            "conflict": "mark_conflict",
            "supersede": "supersede",
        }
        if action not in action_map:
            raise LibraryContractError("Acción batch no permitida.")
        with self.database.transaction(immediate=True) as connection:
            marks = ",".join("?" for _ in candidate_ids)
            rows = connection.execute(
                "SELECT c.id,c.source_version_id,c.candidate_type,c.confidence,p.pdf_page_index+1 page,"
                "s.reason FROM document_structure_candidates c JOIN document_pages p ON p.id=c.page_id "
                "JOIN document_candidate_review_state s ON s.candidate_id=c.id WHERE c.id IN ("
                + marks
                + ")",
                candidate_ids,
            ).fetchall()
            if len(rows) != len(set(candidate_ids)):
                raise LibraryContractError("El lote contiene candidatos inexistentes.")
            signature = {
                (row["source_version_id"], row["candidate_type"], row["reason"]) for row in rows
            }
            if len(signature) != 1 or any(
                float(row["confidence"]) < minimum_confidence for row in rows
            ):
                raise LibraryContractError(
                    "El lote es heterogéneo o no alcanza la confianza mínima."
                )
            version_id, candidate_type, reason = signature.pop()
            batch_id = str(uuid4())
            now = utc_text()
            pages = sorted({int(row["page"]) for row in rows})
            sample = [{"candidate_id": row["id"], "page": row["page"]} for row in rows[:5]]
            connection.execute(
                "INSERT INTO document_review_batches("
                "id,source_version_id,rule,candidate_type,proposed_action,minimum_confidence,"
                "member_count,pages_json,sample_json,effect_json,state,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,'proposed',?)",
                (
                    batch_id,
                    version_id,
                    reason or "homogeneous_manual_pattern",
                    candidate_type,
                    action_map[action],
                    minimum_confidence,
                    len(rows),
                    json_dump(pages),
                    json_dump(sample),
                    json_dump({"target_state": action_map[action]}),
                    now,
                ),
            )
            connection.executemany(
                "INSERT INTO document_review_batch_members(batch_id,candidate_id) VALUES (?,?)",
                [(batch_id, row["id"]) for row in rows],
            )
            row = connection.execute(
                "SELECT * FROM document_review_batches WHERE id=?", (batch_id,)
            ).fetchone()
        return self._batch_read(row)

    def apply_batch(
        self, batch_id: str, *, actor: str, comment: str | None = None
    ) -> dict[str, Any]:
        target = {
            "auto_support": "auto_supported",
            "confirm": "confirmed",
            "reject": "rejected",
            "mark_conflict": "conflicted",
            "supersede": "superseded",
        }
        with self.database.transaction(immediate=True) as connection:
            batch = connection.execute(
                "SELECT * FROM document_review_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise LibraryNotFoundError("Lote de revisión no encontrado.")
            if batch["state"] != "proposed":
                raise LibraryContractError("Solo puede aplicarse un lote propuesto.")
            members = connection.execute(
                "SELECT c.* FROM document_review_batch_members m "
                "JOIN document_structure_candidates c ON c.id=m.candidate_id WHERE m.batch_id=?",
                (batch_id,),
            ).fetchall()
            now = utc_text()
            for candidate in members:
                decision_id = self._apply_state(
                    connection,
                    candidate,
                    target[batch["proposed_action"]],
                    method="batch_manual",
                    rule=batch["rule"],
                    actor=actor,
                    confidence=max(
                        float(candidate["confidence"]), float(batch["minimum_confidence"])
                    ),
                    priority=None,
                    reason=f"batch:{batch['rule']}",
                    evidence={"batch_id": batch_id},
                    comment=comment,
                    batch_id=batch_id,
                    now=now,
                )
                connection.execute(
                    "UPDATE document_review_batch_members SET decision_id=? "
                    "WHERE batch_id=? AND candidate_id=?",
                    (decision_id, batch_id, candidate["id"]),
                )
            connection.execute(
                "UPDATE document_review_batches SET state='applied',applied_at=? WHERE id=?",
                (now, batch_id),
            )
            row = connection.execute(
                "SELECT * FROM document_review_batches WHERE id=?", (batch_id,)
            ).fetchone()
        return self._batch_read(row)

    def revert_batch(
        self, batch_id: str, *, actor: str, comment: str | None = None
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            batch = connection.execute(
                "SELECT * FROM document_review_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise LibraryNotFoundError("Lote de revisión no encontrado.")
            if batch["state"] != "applied":
                raise LibraryContractError("Solo puede revertirse un lote aplicado.")
            decisions = [
                row["decision_id"]
                for row in connection.execute(
                    "SELECT decision_id FROM document_review_batch_members WHERE batch_id=? "
                    "AND decision_id IS NOT NULL",
                    (batch_id,),
                ).fetchall()
            ]
        for decision_id in decisions:
            self.revert(decision_id, actor=actor, comment=comment)
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE document_review_batches SET state='reverted',reverted_at=? WHERE id=?",
                (now, batch_id),
            )
            row = connection.execute(
                "SELECT * FROM document_review_batches WHERE id=?", (batch_id,)
            ).fetchone()
        return self._batch_read(row)

    def topics(self, source_version_id: int) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            run = self._latest_run(connection, source_version_id)
            if run is None:
                return []
            rows = connection.execute(
                "SELECT * FROM document_consolidated_topics WHERE review_run_id=? "
                "ORDER BY theme_number",
                (run["id"],),
            ).fetchall()
            return [self._topic_read(row) for row in rows]

    def hierarchy(self, source_version_id: int) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            run = self._latest_run(connection, source_version_id)
            if run is None:
                return []
            rows = connection.execute(
                "SELECT n.*,c.raw_text FROM document_consolidated_nodes n "
                "LEFT JOIN document_structure_candidates c ON c.id=n.source_candidate_id "
                "WHERE n.review_run_id=? ORDER BY n.pdf_page_number,n.reading_order,n.id",
                (run["id"],),
            ).fetchall()
        nodes = {row["id"]: dict(row) | {"children": []} for row in rows}
        roots = []
        for node in nodes.values():
            node["evidence"] = json_load(node.pop("evidence_json"), {})
            node["issues"] = json_load(node.pop("issues_json"), [])
            parent_id = node["parent_node_id"]
            if parent_id and parent_id in nodes:
                nodes[parent_id]["children"].append(node)
            else:
                roots.append(node)
        return roots

    def relations(self, source_version_id: int) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            run = self._latest_run(connection, source_version_id)
            if run is None:
                return []
            rows = connection.execute(
                "SELECT * FROM document_exercise_solution_relations WHERE review_run_id=? "
                "ORDER BY created_at,id",
                (run["id"],),
            ).fetchall()
            return [self._relation_read(row) for row in rows]

    def readiness(self, source_version_id: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_readiness_snapshots WHERE source_version_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (source_version_id,),
            ).fetchone()
            if row is None:
                return {
                    "source_version_id": source_version_id,
                    "state": "not_ready",
                    "metrics": {},
                    "blockers": ["consolidation_not_run"],
                    "rationale": {},
                }
            return self._readiness_read(row)

    def events(self, source_version_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT d.* FROM document_candidate_decisions d "
                "JOIN document_structure_candidates c ON c.id=d.candidate_id "
                "WHERE c.source_version_id=? ORDER BY d.created_at DESC LIMIT ?",
                (source_version_id, limit),
            ).fetchall()
            return [self._decision_read(row) for row in rows]

    def _initialize_states(
        self, connection: sqlite3.Connection, candidates: list[dict[str, Any]], now: str
    ) -> None:
        rows = []
        for candidate in candidates:
            state = self._base_state(candidate["status"])
            rows.append((candidate["id"], state, candidate["confidence"], now))
        connection.executemany(
            "INSERT OR IGNORE INTO document_candidate_review_state("
            "candidate_id,editorial_state,effective_confidence,updated_at) VALUES (?,?,?,?)",
            rows,
        )

    def _deduplicate(
        self,
        connection: sqlite3.Connection,
        review_run_id: str,
        candidates: list[dict[str, Any]],
        *,
        actor: str,
        now: str,
    ) -> int:
        groups: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
        for candidate in candidates:
            normalized = " ".join((candidate["normalized_layout_text"] or "").casefold().split())
            if not normalized:
                continue
            key = (
                candidate["page_id"],
                candidate["candidate_type"],
                normalized,
                candidate["bbox_json"] or "",
                candidate["parent_candidate_id"] or "",
            )
            groups[key].append(candidate)
        count = 0
        for values in groups.values():
            if len(values) < 2:
                continue
            values.sort(
                key=lambda row: (-float(row["confidence"]), int(row["reading_order"]), row["id"])
            )
            primary = values[0]
            for duplicate in values[1:]:
                self._apply_state(
                    connection,
                    duplicate,
                    "superseded",
                    method="deterministic_rule",
                    rule="exact_observed_duplicate",
                    actor=actor,
                    confidence=float(primary["confidence"]),
                    priority=None,
                    reason="exact_observed_duplicate",
                    evidence={"primary_candidate_id": primary["id"]},
                    primary_candidate_id=primary["id"],
                    review_run_id=review_run_id,
                    now=now,
                )
                count += 1
        return count

    def _review_topics(
        self,
        connection: sqlite3.Connection,
        review_run_id: str,
        candidates: list[dict[str, Any]],
        *,
        actor: str,
        now: str,
    ) -> dict[int, dict[str, Any]]:
        topic_candidates = [row for row in candidates if row["candidate_type"] == "topic_heading"]
        page_topics: dict[int, set[int]] = defaultdict(set)
        for row in topic_candidates:
            if row["canonical_topic_number"]:
                page_topics[int(row["page_id"])].add(int(row["canonical_topic_number"]))
        index_pages = {page_id for page_id, topics in page_topics.items() if len(topics) >= 3}
        by_topic: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in topic_candidates:
            number = row["canonical_topic_number"]
            if number:
                by_topic[int(number)].append(row)
            else:
                self._apply_state(
                    connection,
                    row,
                    "needs_review" if _TOPIC_TOKEN.search(row["raw_text"] or "") else "rejected",
                    method="deterministic_rule",
                    rule="topic_identity_missing",
                    actor=actor,
                    confidence=min(float(row["confidence"]), 0.5),
                    priority="P0" if _TOPIC_TOKEN.search(row["raw_text"] or "") else None,
                    reason="topic_identity_missing",
                    evidence={"canonical_match_state": row["canonical_match_state"]},
                    review_run_id=review_run_id,
                    now=now,
                )
        result: dict[int, dict[str, Any]] = {}
        for number in range(1, 52):
            values = by_topic.get(number, [])
            scored: list[tuple[float, int, dict[str, Any]]] = []
            for row in values:
                raw = " ".join((row["raw_text"] or "").split())
                block_type = row["block_type"]
                if row["page_id"] in index_pages or "..." in raw:
                    continue
                score = float(row["confidence"])
                if _BARE_TOPIC.match(raw):
                    score += 0.2
                if block_type in {"text", "column"}:
                    score += 0.1
                if block_type in {"probable_header", "probable_footer"}:
                    score -= 0.3
                if row["canonical_match_state"] in {
                    "exact_match",
                    "normalized_match",
                    "probable_match",
                }:
                    score += 0.1
                if _EXERCISE_WORD.search(raw) or _SOLUTION_WORD.search(raw):
                    score -= 0.05
                scored.append((score, int(row["pdf_page_number"]), row))
            scored.sort(key=lambda value: (-value[0], value[1], value[2]["reading_order"]))
            primary = scored[0][2] if scored and scored[0][0] >= 0.9 else None
            conflict = bool(
                len(scored) > 1
                and primary is not None
                and abs(scored[0][0] - scored[1][0]) < 0.02
                and scored[0][1] == scored[1][1]
            )
            if conflict:
                primary = None
            alternatives: list[str] = []
            for row in values:
                raw = " ".join((row["raw_text"] or "").split())
                if primary is not None and row["id"] == primary["id"]:
                    self._apply_state(
                        connection,
                        row,
                        "auto_supported",
                        method="canonical_match",
                        rule="unique_principal_topic_heading",
                        actor=actor,
                        confidence=min(0.99, max(float(row["confidence"]), 0.95)),
                        priority=None,
                        reason="unique_principal_topic_heading",
                        evidence={
                            "theme_number": number,
                            "index_page_excluded": True,
                            "layout_type": row["block_type"],
                        },
                        review_run_id=review_run_id,
                        now=now,
                    )
                elif row["page_id"] in index_pages or "..." in raw:
                    self._apply_state(
                        connection,
                        row,
                        "rejected",
                        method="deterministic_rule",
                        rule="index_entry_not_principal_topic",
                        actor=actor,
                        confidence=0.98,
                        priority=None,
                        reason="index_entry_not_principal_topic",
                        evidence={
                            "theme_number": number,
                            "page_topic_count": len(page_topics[row["page_id"]]),
                        },
                        review_run_id=review_run_id,
                        now=now,
                    )
                elif row["block_type"] in {
                    "probable_header",
                    "probable_footer",
                } and _BARE_TOPIC.match(raw):
                    self._apply_state(
                        connection,
                        row,
                        "rejected",
                        method="deterministic_rule",
                        rule="repeated_running_header_not_topic",
                        actor=actor,
                        confidence=0.97,
                        priority=None,
                        reason="repeated_running_header_not_topic",
                        evidence={"theme_number": number, "layout_type": row["block_type"]},
                        review_run_id=review_run_id,
                        now=now,
                    )
                elif primary is not None and (
                    (_BARE_TOPIC.match(raw) and row["block_type"] in {"text", "column"})
                    or (
                        (_EXERCISE_WORD.search(raw) or _SOLUTION_WORD.search(raw))
                        and _TOPIC_TOKEN.search(raw)
                    )
                ):
                    alternatives.append(row["id"])
                    self._apply_state(
                        connection,
                        row,
                        "auto_supported",
                        method="cross_evidence",
                        rule="supported_secondary_topic_context",
                        actor=actor,
                        confidence=max(float(row["confidence"]), 0.93),
                        priority=None,
                        reason="supported_secondary_topic_context",
                        evidence={
                            "theme_number": number,
                            "primary_candidate_id": primary["id"],
                            "principal": False,
                            "explicit_structural_context": True,
                        },
                        primary_candidate_id=primary["id"],
                        review_run_id=review_run_id,
                        now=now,
                    )
                else:
                    alternatives.append(row["id"])
                    self._apply_state(
                        connection,
                        row,
                        "conflicted" if conflict else "needs_review",
                        method="cross_evidence",
                        rule="alternate_topic_instance",
                        actor=actor,
                        confidence=min(float(row["confidence"]), 0.82),
                        priority="P0" if conflict or primary is None else "P1",
                        reason="topic_identity_conflict"
                        if conflict or primary is None
                        else "alternate_topic_instance",
                        evidence={
                            "theme_number": number,
                            "primary_candidate_id": primary["id"] if primary else None,
                        },
                        primary_candidate_id=primary["id"] if primary else None,
                        review_run_id=review_run_id,
                        now=now,
                    )
            result[number] = {
                "primary": primary,
                "alternatives": alternatives,
                "conflict": conflict,
                "candidate_count": len(values),
            }
        return result

    def _review_other_candidates(
        self,
        connection: sqlite3.Connection,
        review_run_id: str,
        candidates: list[dict[str, Any]],
        *,
        actor: str,
        now: str,
    ) -> dict[str, int]:
        counts: Counter[str] = Counter()
        parent_by_id = {row["id"]: row for row in candidates}
        printed = [
            row
            for row in candidates
            if row["candidate_type"] == "printed_page_number"
            and (row["raw_text"] or "").strip().isdigit()
        ]
        printed.sort(key=lambda row: int(row["pdf_page_number"]))
        printed_supported: set[str] = set()
        for index, row in enumerate(printed):
            page = int(row["pdf_page_number"])
            value = int((row["raw_text"] or "").strip())
            neighbors = printed[max(0, index - 1) : index] + printed[index + 1 : index + 2]
            if any(
                int((other["raw_text"] or "0").strip()) - value
                == int(other["pdf_page_number"]) - page
                for other in neighbors
            ):
                printed_supported.add(row["id"])
        for row in candidates:
            kind = row["candidate_type"]
            if kind == "topic_heading":
                continue
            raw = " ".join((row["raw_text"] or "").split())
            target: str | None = None
            rule = ""
            confidence = float(row["confidence"])
            priority: str | None = None
            if kind == "diagram":
                if row["has_text"]:
                    target, rule, confidence = "rejected", "ocr_scan_background_not_diagram", 0.98
                else:
                    target, rule, priority = "needs_review", "visual_page_without_text", "P1"
            elif kind == "table":
                target, rule, priority = "needs_review", "probable_table_visual_review", "P1"
            elif kind == "document_title":
                target, rule, priority = "needs_review", "document_title_visual_review", "P1"
            elif kind == "level_marker":
                if _LEVEL_TOKEN.match(raw) and row["block_type"] in {
                    "text",
                    "column",
                    "probable_header",
                }:
                    target, rule, confidence = (
                        "auto_supported",
                        "isolated_repeated_level_marker",
                        max(confidence, 0.94),
                    )
                else:
                    target, rule, confidence = "rejected", "level_token_incompatible_context", 0.9
            elif kind in {"exercise_heading", "solution_heading"}:
                pattern = _EXERCISE_WORD if kind == "exercise_heading" else _SOLUTION_WORD
                if pattern.search(raw):
                    target, rule, confidence = (
                        "auto_supported",
                        "explicit_structural_heading",
                        max(confidence, 0.96),
                    )
                else:
                    target, rule, priority = "needs_review", "ambiguous_structural_heading", "P1"
            elif kind in {"exercise_item", "solution_item"}:
                parent = parent_by_id.get(row["parent_candidate_id"])
                if _ITEM_TOKEN.match(raw) and parent is not None:
                    target, rule, confidence = (
                        "auto_supported",
                        "numbered_item_under_structural_parent",
                        max(confidence, 0.9),
                    )
                else:
                    target, rule, priority = "needs_review", "item_context_incomplete", "P1"
            elif kind == "printed_page_number":
                if row["id"] in printed_supported and row["block_type"] in {
                    "probable_page_number",
                    "probable_header",
                    "probable_footer",
                    "text",
                }:
                    target, rule, confidence = (
                        "auto_supported",
                        "numeric_page_marker_in_sequence",
                        max(confidence, 0.96),
                    )
                else:
                    target, rule, priority = "needs_review", "printed_page_sequence_uncertain", "P1"
            elif row["status"] == "needs_review":
                target, rule, priority = "needs_review", "inherited_extraction_issue", "P2"
            if target is None:
                continue
            self._apply_state(
                connection,
                row,
                target,
                method="deterministic_rule",
                rule=rule,
                actor=actor,
                confidence=min(1.0, confidence),
                priority=priority,
                reason=rule,
                evidence={"candidate_type": kind, "layout_type": row["block_type"]},
                review_run_id=review_run_id,
                now=now,
            )
            counts[rule] += 1
        return dict(counts)

    def _classify_visual_pages(
        self,
        connection: sqlite3.Connection,
        review_run_id: str,
        source_version_id: int,
        *,
        now: str,
    ) -> int:
        rows = connection.execute(
            "SELECT p.*,a.visual_metrics_json,a.region_hashes_json FROM document_pages p "
            "LEFT JOIN document_page_artifacts a ON a.page_id=p.id "
            "WHERE p.source_version_id=? AND coalesce(p.has_text,0)=0 ORDER BY p.pdf_page_index",
            (source_version_id,),
        ).fetchall()
        for row in rows:
            visual = json_load(row["visual_metrics_json"], {})
            regions = json_load(row["region_hashes_json"], {})
            has_visual = (
                bool(visual or regions)
                or connection.execute(
                    "SELECT 1 FROM document_page_blocks WHERE page_id=? "
                    "AND block_type IN ('image','graphic_region') LIMIT 1",
                    (row["id"],),
                ).fetchone()
                is not None
            )
            graphic_exercise = (
                connection.execute(
                    "SELECT 1 FROM document_structure_candidates c "
                    "LEFT JOIN document_structure_candidates parent "
                    "ON parent.id=c.parent_candidate_id WHERE c.page_id=? "
                    "AND c.candidate_type='diagram' "
                    "AND lower(coalesce(parent.raw_text,'')) GLOB '*ejercicio*' LIMIT 1",
                    (row["id"],),
                ).fetchone()
                is not None
            )
            classification = (
                "graphic_exercise"
                if graphic_exercise
                else "image_only"
                if has_visual
                else "unknown"
            )
            confidence = 0.72 if has_visual else 0.3
            connection.execute(
                "INSERT INTO document_visual_page_classifications("
                "id,review_run_id,source_version_id,page_id,classification,state,confidence,"
                "evidence_json,created_at) VALUES (?,?,?,?,?,'needs_review',?,?,?)",
                (
                    str(uuid4()),
                    review_run_id,
                    source_version_id,
                    row["id"],
                    classification,
                    confidence,
                    json_dump(
                        {
                            "pdf_page_number": int(row["pdf_page_index"]) + 1,
                            "embedded_text_absent": True,
                            "render_available_on_demand": True,
                            "content_described": False,
                            "explicit_exercise_parent": graphic_exercise,
                        }
                    ),
                    now,
                ),
            )
        return len(rows)

    def _build_topics(
        self,
        connection: sqlite3.Connection,
        review_run_id: str,
        source_version_id: int,
        candidates: list[dict[str, Any]],
        result: dict[int, dict[str, Any]],
        *,
        now: str,
    ) -> None:
        by_id = {row["id"]: row for row in candidates}
        for number in range(1, 52):
            value = result[number]
            primary = value["primary"]
            alternatives = value["alternatives"]
            if value["conflict"]:
                state = "conflicted"
            elif primary is not None:
                state = "auto_supported"
            elif value["candidate_count"]:
                state = "pending"
            else:
                state = "pending"
            raw = primary["raw_text"] if primary else None
            title_es, title_de = self._split_titles(raw)
            issues = []
            if primary is None:
                issues.append("principal_topic_evidence_insufficient")
            connection.execute(
                "INSERT INTO document_consolidated_topics("
                "id,review_run_id,source_version_id,theme_number,state,primary_candidate_id,"
                "alternative_candidate_ids_json,pdf_page_number,printed_page,title_es_observed,"
                "title_de_observed,observed_level,confidence,evidence_json,issues_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    review_run_id,
                    source_version_id,
                    number,
                    state,
                    primary["id"] if primary else None,
                    json_dump(alternatives),
                    primary["pdf_page_number"] if primary else None,
                    primary["proposed_printed_page"] if primary else None,
                    title_es,
                    title_de,
                    primary["observed_pedagogical_level"] if primary else None,
                    0.95 if primary else 0.0,
                    json_dump(
                        {
                            "canonical_route_used_as_anchor_only": True,
                            "candidate_count": value["candidate_count"],
                            "primary_observed": bool(primary),
                            "alternative_count": len(alternatives),
                            "alternative_candidates_present": all(
                                item in by_id for item in alternatives
                            ),
                        }
                    ),
                    json_dump(issues),
                    now,
                ),
            )

    def _build_hierarchy(
        self,
        connection: sqlite3.Connection,
        review_run_id: str,
        source_version_id: int,
        candidates: list[dict[str, Any]],
        *,
        now: str,
    ) -> int:
        topics = connection.execute(
            "SELECT * FROM document_consolidated_topics WHERE review_run_id=? "
            "AND primary_candidate_id IS NOT NULL ORDER BY theme_number",
            (review_run_id,),
        ).fetchall()
        book_node_id = str(uuid4())
        connection.execute(
            "INSERT INTO document_consolidated_nodes("
            "id,review_run_id,source_version_id,source_candidate_id,parent_node_id,node_type,"
            "theme_number,observed_level,pdf_page_number,reading_order,state,evidence_json,"
            "issues_json,created_at) VALUES (?,?,?,NULL,NULL,'book',NULL,NULL,1,0,"
            "'auto_supported',?,'[]',?)",
            (
                book_node_id,
                review_run_id,
                source_version_id,
                json_dump({"source_version_identity": True}),
                now,
            ),
        )
        levels = sorted(
            {
                str(candidate["observed_pedagogical_level"] or "unspecified")
                for topic in topics
                for candidate in candidates
                if candidate["id"] == topic["primary_candidate_id"]
            }
        )
        level_nodes: dict[str, str] = {}
        for level in levels:
            level_node_id = str(uuid4())
            level_nodes[level] = level_node_id
            connection.execute(
                "INSERT INTO document_consolidated_nodes("
                "id,review_run_id,source_version_id,source_candidate_id,parent_node_id,node_type,"
                "theme_number,observed_level,pdf_page_number,reading_order,state,evidence_json,"
                "issues_json,created_at) VALUES (?,?,?,NULL,?,'editorial_level',NULL,?,1,0,"
                "'auto_supported',?,'[]',?)",
                (
                    level_node_id,
                    review_run_id,
                    source_version_id,
                    book_node_id,
                    level,
                    json_dump({"observed_grouping_only": True}),
                    now,
                ),
            )
        topic_nodes: dict[int, str] = {}
        for topic in topics:
            candidate = next(
                row for row in candidates if row["id"] == topic["primary_candidate_id"]
            )
            node_id = str(uuid4())
            topic_nodes[int(topic["theme_number"])] = node_id
            observed_level = str(candidate["observed_pedagogical_level"] or "unspecified")
            connection.execute(
                "INSERT INTO document_consolidated_nodes("
                "id,review_run_id,source_version_id,source_candidate_id,parent_node_id,node_type,"
                "theme_number,observed_level,pdf_page_number,reading_order,state,evidence_json,"
                "issues_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'{}','[]',?)",
                (
                    node_id,
                    review_run_id,
                    source_version_id,
                    candidate["id"],
                    level_nodes[observed_level],
                    "topic_heading",
                    topic["theme_number"],
                    candidate["observed_pedagogical_level"],
                    candidate["pdf_page_number"],
                    candidate["reading_order"],
                    topic["state"],
                    now,
                ),
            )
        supported = connection.execute(
            "SELECT c.id,s.editorial_state FROM document_structure_candidates c "
            "JOIN document_candidate_review_state s ON s.candidate_id=c.id "
            "WHERE c.source_version_id=? AND s.editorial_state IN ('auto_supported','confirmed') "
            "AND c.candidate_type<>'topic_heading' AND c.superseded_at IS NULL",
            (source_version_id,),
        ).fetchall()
        supported_states = {row["id"]: row["editorial_state"] for row in supported}
        by_id = {row["id"]: row for row in candidates}
        count = 1 + len(level_nodes) + len(topic_nodes)
        for candidate_id, state in supported_states.items():
            row = by_id[candidate_id]
            theme, level = self._context(row, by_id)
            parent = topic_nodes.get(theme) if theme else None
            if parent is None:
                if row["candidate_type"] == "document_title":
                    parent = book_node_id
                elif row["candidate_type"] == "level_marker":
                    parent = level_nodes.get(level or "unspecified", book_node_id)
                else:
                    continue
            connection.execute(
                "INSERT INTO document_consolidated_nodes("
                "id,review_run_id,source_version_id,source_candidate_id,parent_node_id,node_type,"
                "theme_number,observed_level,pdf_page_number,reading_order,state,evidence_json,"
                "issues_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    review_run_id,
                    source_version_id,
                    candidate_id,
                    parent,
                    row["candidate_type"],
                    theme,
                    level,
                    row["pdf_page_number"],
                    row["reading_order"],
                    state,
                    json_dump({"source_candidate_preserved": True}),
                    "[]",
                    now,
                ),
            )
            count += 1
        return count

    def _build_relations(
        self,
        connection: sqlite3.Connection,
        review_run_id: str,
        source_version_id: int,
        candidates: list[dict[str, Any]],
        *,
        now: str,
    ) -> int:
        by_id = {row["id"]: row for row in candidates}
        exercises: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
        solutions: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in candidates:
            if row["candidate_type"] not in {"exercise_item", "solution_item"}:
                continue
            theme, level = self._context(row, by_id)
            label = self._item_label(row["raw_text"] or "")
            if theme is None or label is None:
                continue
            target = exercises if row["candidate_type"] == "exercise_item" else solutions
            target[(theme, level or "unknown", label)].append(row)
        count = 0
        for key in sorted(set(exercises) | set(solutions)):
            left = exercises.get(key, [])
            right = solutions.get(key, [])
            if not right:
                relation_type = "exercise_without_solution"
            elif not left:
                relation_type = "solution_without_exercise"
            elif len(left) == len(right) == 1:
                relation_type = "one_to_one"
            elif len(left) == 1:
                relation_type = "one_to_many"
            else:
                relation_type = "many_to_one"
            unambiguous = relation_type == "one_to_one"
            state = "auto_supported" if unambiguous else "needs_review"
            confidence = 0.92 if unambiguous else 0.58
            issues = []
            if relation_type == "exercise_without_solution":
                issues.append("solution_not_identified")
            elif relation_type == "solution_without_exercise":
                issues.append("exercise_not_identified")
            elif not unambiguous:
                issues.append("multiple_structural_candidates")
            connection.execute(
                "INSERT INTO document_exercise_solution_relations("
                "id,review_run_id,source_version_id,exercise_candidate_ids_json,"
                "solution_candidate_ids_json,relation_type,state,confidence,evidence_json,"
                "issues_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    review_run_id,
                    source_version_id,
                    json_dump([row["id"] for row in left]),
                    json_dump([row["id"] for row in right]),
                    relation_type,
                    state,
                    confidence,
                    json_dump({"theme_number": key[0], "level": key[1], "item_label": key[2]}),
                    json_dump(issues),
                    now,
                ),
            )
            count += 1
        return count

    def _build_safe_batches(
        self,
        connection: sqlite3.Connection,
        review_run_id: str,
        source_version_id: int,
        *,
        now: str,
    ) -> int:
        groups = connection.execute(
            "SELECT c.candidate_type,s.reason,count(*) count,min(s.effective_confidence) min_conf "
            "FROM document_candidate_review_state s JOIN document_structure_candidates c "
            "ON c.id=s.candidate_id WHERE c.source_version_id=? "
            "AND s.editorial_state='needs_review' AND s.reason IS NOT NULL "
            "GROUP BY c.candidate_type,s.reason HAVING count(*)>=2",
            (source_version_id,),
        ).fetchall()
        count = 0
        for group in groups:
            members = connection.execute(
                "SELECT c.id,p.pdf_page_index+1 page,c.raw_text FROM document_candidate_review_state s "
                "JOIN document_structure_candidates c ON c.id=s.candidate_id "
                "JOIN document_pages p ON p.id=c.page_id WHERE c.source_version_id=? "
                "AND s.editorial_state='needs_review' AND c.candidate_type=? AND s.reason=? "
                "ORDER BY p.pdf_page_index,c.reading_order",
                (source_version_id, group["candidate_type"], group["reason"]),
            ).fetchall()
            action = "mark_conflict"
            batch_id = str(uuid4())
            pages = sorted({int(row["page"]) for row in members})
            connection.execute(
                "INSERT INTO document_review_batches("
                "id,review_run_id,source_version_id,rule,candidate_type,proposed_action,"
                "minimum_confidence,member_count,pages_json,sample_json,effect_json,state,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'proposed',?)",
                (
                    batch_id,
                    review_run_id,
                    source_version_id,
                    group["reason"],
                    group["candidate_type"],
                    action,
                    group["min_conf"],
                    len(members),
                    json_dump(pages),
                    json_dump(
                        [
                            {
                                "candidate_id": row["id"],
                                "page": row["page"],
                                "text": (row["raw_text"] or "")[:120],
                            }
                            for row in members[:5]
                        ]
                    ),
                    json_dump({"estimated_items": len(members), "automatic_application": False}),
                    now,
                ),
            )
            connection.executemany(
                "INSERT INTO document_review_batch_members(batch_id,candidate_id) VALUES (?,?)",
                [(batch_id, row["id"]) for row in members],
            )
            count += 1
        return count

    def _calculate_readiness(
        self,
        connection: sqlite3.Connection,
        review_run_id: str,
        source_version_id: int,
        *,
        now: str,
    ) -> dict[str, Any]:
        version = self._version(connection, source_version_id)
        pages = connection.execute(
            "SELECT count(*) total,sum(coalesce(has_text,0)=1) text_pages,"
            "sum(issue_count>0) issue_pages FROM document_pages WHERE source_version_id=?",
            (source_version_id,),
        ).fetchone()
        topic_counts = dict(
            connection.execute(
                "SELECT state,count(*) FROM document_consolidated_topics WHERE review_run_id=? GROUP BY state",
                (review_run_id,),
            ).fetchall()
        )
        queue_counts = dict(
            connection.execute(
                "SELECT coalesce(s.priority,'P2'),count(*) FROM document_candidate_review_state s "
                "JOIN document_structure_candidates c ON c.id=s.candidate_id "
                "WHERE c.source_version_id=? AND s.editorial_state IN ('needs_review','conflicted') "
                "GROUP BY coalesce(s.priority,'P2')",
                (source_version_id,),
            ).fetchall()
        )
        visual_pending = int(
            connection.execute(
                "SELECT count(*) FROM document_visual_page_classifications WHERE review_run_id=? "
                "AND state='needs_review'",
                (review_run_id,),
            ).fetchone()[0]
        )
        blockers = []
        if int(pages["total"] or 0) != int(version["page_count"] or 0):
            blockers.append("pages_not_fully_materialized")
        unresolved_topics = topic_counts.get("pending", 0) + topic_counts.get("conflicted", 0)
        if unresolved_topics:
            blockers.append(f"principal_topics_unresolved:{unresolved_topics}")
        if queue_counts.get("P0", 0):
            blockers.append(f"p0_review_items:{queue_counts['P0']}")
        if blockers:
            state = "blocked"
        elif sum(queue_counts.values()) or visual_pending:
            state = "structurally_ready_with_issues"
        else:
            state = "structurally_ready"
        metrics = {
            "pages_materialized": int(pages["total"] or 0),
            "pages_expected": int(version["page_count"] or 0),
            "pages_with_text": int(pages["text_pages"] or 0),
            "pages_with_incident": int(pages["issue_pages"] or 0),
            "visual_pages_pending": visual_pending,
            "topics_expected": 51,
            "topics_located": 51 - topic_counts.get("pending", 0),
            "topics_auto_supported": topic_counts.get("auto_supported", 0),
            "topics_confirmed": topic_counts.get("confirmed", 0),
            "topics_conflicted": topic_counts.get("conflicted", 0),
            "queue": {
                "P0": queue_counts.get("P0", 0),
                "P1": queue_counts.get("P1", 0),
                "P2": queue_counts.get("P2", 0),
            },
        }
        snapshot_id = str(uuid4())
        connection.execute(
            "INSERT INTO document_readiness_snapshots("
            "id,review_run_id,source_version_id,state,metrics_json,blockers_json,rationale_json,"
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                snapshot_id,
                review_run_id,
                source_version_id,
                state,
                json_dump(metrics),
                json_dump(blockers),
                json_dump(
                    {
                        "zero_incidents_required": False,
                        "p0_blocks": True,
                        "p2_allows_ready_with_issues": True,
                        "activation_changed": False,
                    }
                ),
                now,
            ),
        )
        return {"id": snapshot_id, "state": state, "metrics": metrics, "blockers": blockers}

    def _apply_state(
        self,
        connection: sqlite3.Connection,
        candidate: sqlite3.Row | dict[str, Any],
        new_state: str,
        *,
        method: str,
        rule: str,
        actor: str,
        confidence: float,
        priority: str | None,
        reason: str,
        evidence: dict[str, Any],
        comment: str | None = None,
        primary_candidate_id: str | None = None,
        review_run_id: str | None = None,
        batch_id: str | None = None,
        now: str,
    ) -> str:
        if new_state not in EDITORIAL_STATES or method not in DECISION_METHODS:
            raise LibraryContractError("Estado o método editorial inválido.")
        current = connection.execute(
            "SELECT * FROM document_candidate_review_state WHERE candidate_id=?",
            (candidate["id"],),
        ).fetchone()
        previous_state = (
            current["editorial_state"] if current else self._base_state(candidate["status"])
        )
        confidence_before = (
            float(current["effective_confidence"]) if current else float(candidate["confidence"])
        )
        existing = connection.execute(
            "SELECT id FROM document_candidate_decisions WHERE candidate_id=? AND rule=? "
            "AND new_state=? AND coalesce(review_run_id,'')=coalesce(?,'') ORDER BY created_at DESC LIMIT 1",
            (candidate["id"], rule, new_state, review_run_id),
        ).fetchone()
        if existing:
            return existing["id"]
        decision_id = str(uuid4())
        connection.execute(
            "INSERT INTO document_candidate_decisions("
            "id,candidate_id,review_run_id,actor,method,rule,previous_state,new_state,"
            "confidence_before,confidence_after,batch_id,evidence_json,comment,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision_id,
                candidate["id"],
                review_run_id,
                actor,
                method,
                rule,
                previous_state,
                new_state,
                confidence_before,
                max(0.0, min(1.0, confidence)),
                batch_id,
                json_dump(evidence),
                comment,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO document_candidate_review_state("
            "candidate_id,editorial_state,effective_confidence,primary_candidate_id,"
            "latest_decision_id,priority,reason,updated_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(candidate_id) DO UPDATE SET editorial_state=excluded.editorial_state,"
            "effective_confidence=excluded.effective_confidence,"
            "primary_candidate_id=excluded.primary_candidate_id,"
            "latest_decision_id=excluded.latest_decision_id,priority=excluded.priority,"
            "reason=excluded.reason,updated_at=excluded.updated_at",
            (
                candidate["id"],
                new_state,
                max(0.0, min(1.0, confidence)),
                primary_candidate_id,
                decision_id,
                priority,
                reason,
                now,
            ),
        )
        return decision_id

    def _candidate_rows(
        self, connection: sqlite3.Connection, source_version_id: int
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT c.*,p.pdf_page_index+1 pdf_page_number,p.has_text,"
            "json_extract(c.evidence_json,'$.block_type') block_type "
            "FROM document_structure_candidates c JOIN document_pages p ON p.id=c.page_id "
            "WHERE c.source_version_id=? AND c.superseded_at IS NULL "
            "ORDER BY p.pdf_page_index,c.reading_order,c.id",
            (source_version_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _base_state(status: str) -> str:
        return {
            "rejected_by_rule": "rejected",
            "superseded": "superseded",
        }.get(status, status if status in EDITORIAL_STATES else "proposed")

    @staticmethod
    def _base_metrics(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        states = Counter(DocumentReviewService._base_state(row["status"]) for row in candidates)
        types = Counter(row["candidate_type"] for row in candidates)
        return {"total": len(candidates), "states": dict(states), "types": dict(types)}

    def _review_metrics(
        self, connection: sqlite3.Connection, source_version_id: int, review_run_id: str | None
    ) -> dict[str, Any]:
        states = dict(
            connection.execute(
                "SELECT s.editorial_state,count(*) FROM document_candidate_review_state s "
                "JOIN document_structure_candidates c ON c.id=s.candidate_id "
                "WHERE c.source_version_id=? GROUP BY s.editorial_state",
                (source_version_id,),
            ).fetchall()
        )
        types = dict(
            connection.execute(
                "SELECT c.candidate_type,count(*) FROM document_structure_candidates c "
                "JOIN document_candidate_review_state s ON s.candidate_id=c.id "
                "WHERE c.source_version_id=? AND s.editorial_state NOT IN ('rejected','superseded') "
                "GROUP BY c.candidate_type",
                (source_version_id,),
            ).fetchall()
        )
        metrics: dict[str, Any] = {"total": sum(states.values()), "types_effective": types}
        metrics.update({state: states.get(state, 0) for state in EDITORIAL_STATES})
        if review_run_id:
            topic_states = dict(
                connection.execute(
                    "SELECT state,count(*) FROM document_consolidated_topics "
                    "WHERE review_run_id=? GROUP BY state",
                    (review_run_id,),
                ).fetchall()
            )
            relation_states = dict(
                connection.execute(
                    "SELECT state,count(*) FROM document_exercise_solution_relations "
                    "WHERE review_run_id=? GROUP BY state",
                    (review_run_id,),
                ).fetchall()
            )
            metrics.update({f"topics_{state}": count for state, count in topic_states.items()})
            metrics["exercise_solution_relations"] = relation_states
            metrics["visual_pages_pending"] = int(
                connection.execute(
                    "SELECT count(*) FROM document_visual_page_classifications "
                    "WHERE review_run_id=? AND state='needs_review'",
                    (review_run_id,),
                ).fetchone()[0]
            )
        return metrics

    @staticmethod
    def _context(
        candidate: dict[str, Any], by_id: dict[str, dict[str, Any]]
    ) -> tuple[int | None, str | None]:
        theme = candidate.get("canonical_topic_number")
        level = candidate.get("observed_pedagogical_level")
        current = candidate
        seen: set[str] = set()
        for _ in range(20):
            parent_id = current.get("parent_candidate_id")
            if not parent_id or parent_id in seen or parent_id not in by_id:
                break
            seen.add(parent_id)
            current = by_id[parent_id]
            theme = theme or current.get("canonical_topic_number")
            level = level or current.get("observed_pedagogical_level")
        return int(theme) if theme else None, str(level) if level else None

    @staticmethod
    def _item_label(raw: str) -> str | None:
        match = _ITEM_TOKEN.match(raw)
        return match.group(1).casefold() if match else None

    @staticmethod
    def _split_titles(raw: str | None) -> tuple[str | None, str | None]:
        if not raw:
            return None, None
        clean = _TOPIC_TOKEN.sub("", raw, count=1).lstrip(" .,:;-")
        if not clean.strip():
            return None, None
        if "/" in clean:
            left, right = clean.split("/", 1)
            return left.strip() or None, right.splitlines()[0].strip() or None
        return clean.splitlines()[0].strip() or None, None

    @staticmethod
    def _version(connection: sqlite3.Connection, source_version_id: int) -> sqlite3.Row:
        row = connection.execute(
            "SELECT v.*,coalesce(s.display_alias,s.canonical_title,s.name) title "
            "FROM source_versions v JOIN sources s ON s.id=v.source_id "
            "WHERE v.id=?",
            (source_version_id,),
        ).fetchone()
        if row is None:
            raise LibraryNotFoundError("Versión documental no encontrada.")
        return row

    @staticmethod
    def _latest_run(connection: sqlite3.Connection, source_version_id: int) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM document_review_runs WHERE source_version_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (source_version_id,),
        ).fetchone()

    @staticmethod
    def _run_read(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["configuration"] = json_load(result.pop("configuration_json"), {})
        result["before_metrics"] = json_load(result.pop("before_metrics_json"), {})
        result["after_metrics"] = json_load(result.pop("after_metrics_json"), {})
        return result

    @staticmethod
    def _decision_read(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["evidence"] = json_load(result.pop("evidence_json"), {})
        return result

    @staticmethod
    def _batch_read(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["pages"] = json_load(result.pop("pages_json"), [])
        result["sample"] = json_load(result.pop("sample_json"), [])
        result["effect"] = json_load(result.pop("effect_json"), {})
        return result

    @staticmethod
    def _topic_read(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["alternative_candidate_ids"] = json_load(
            result.pop("alternative_candidate_ids_json"), []
        )
        result["evidence"] = json_load(result.pop("evidence_json"), {})
        result["issues"] = json_load(result.pop("issues_json"), [])
        return result

    @staticmethod
    def _relation_read(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["exercise_candidate_ids"] = json_load(result.pop("exercise_candidate_ids_json"), [])
        result["solution_candidate_ids"] = json_load(result.pop("solution_candidate_ids_json"), [])
        result["evidence"] = json_load(result.pop("evidence_json"), {})
        result["issues"] = json_load(result.pop("issues_json"), [])
        return result

    @staticmethod
    def _readiness_read(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metrics"] = json_load(result.pop("metrics_json"), {})
        result["blockers"] = json_load(result.pop("blockers_json"), [])
        result["rationale"] = json_load(result.pop("rationale_json"), {})
        return result
