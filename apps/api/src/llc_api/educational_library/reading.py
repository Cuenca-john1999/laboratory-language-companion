from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from llc_api.core.ai_coordination import ai_coordinator

from .database import LibraryDatabase
from .routing import LibraryModelRouter, ModelRole

PROMPT_VERSION = "pedagogical-reading.v1"
DEFAULT_MAX_PASSES = 3
DEFAULT_INPUT_CHARACTERS = 18_000
DEFAULT_OUTPUT_TOKENS = 2_048

CANDIDATE_TYPES = {
    "concept",
    "definition",
    "grammatical_rule",
    "condition",
    "constraint",
    "exception",
    "contrast",
    "terminology",
    "form_pattern",
    "paradigm",
    "example",
    "counterexample",
    "warning",
    "usage_note",
    "register_note",
    "common_error",
    "prerequisite",
    "dependency",
    "concept_relation",
    "cross_reference",
    "pedagogical_sequence",
    "table_interpretation",
    "unresolved_claim",
}


class ReadingError(RuntimeError):
    pass


class EvidenceRejected(ReadingError):
    pass


class ReadingCandidateState(StrEnum):
    PROPOSED = "proposed"
    CORROBORATED = "corroborated"
    NEEDS_REVIEW = "needs_review"
    CONFLICTED = "conflicted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str
    quote: str = Field(min_length=1)


class ReadingCandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_id: str = Field(min_length=1, max_length=120)
    candidate_type: str
    content: dict[str, Any]
    terminology_de: list[str] = Field(default_factory=list)
    terminology_es: list[str] = Field(default_factory=list)
    observed_level: str | None = None
    evidence: list[EvidenceReference] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    uncertainty: str | None = None


class ReadingRelationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_local_id: str
    target_local_id: str
    relation_type: str
    evidence_ids: list[str] = Field(default_factory=list)


class ReadingConflictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_local_ids: list[str] = Field(min_length=2)
    description: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class PedagogicalReadingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: int = Field(gt=0)
    evidence_scope: list[str]
    candidates: list[ReadingCandidateOutput]
    relations: list[ReadingRelationOutput] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    conflicts: list[ReadingConflictOutput] = Field(default_factory=list)


class ReadingRunCreate(BaseModel):
    source_version_id: int = Field(gt=0)
    language: str = Field(default="de", pattern=r"^[a-z]{2,3}$")
    model_role: Literal["teacher", "deep"] = "deep"
    max_passes: int = Field(default=DEFAULT_MAX_PASSES, ge=1, le=8)
    auto_continue: bool = False


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id() -> str:
    return str(uuid4())


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class PedagogicalReadingService:
    def __init__(self, database: LibraryDatabase, router: LibraryModelRouter | None = None):
        self.database = database
        self.router = router
        self.prompt = (
            Path(__file__).parents[1] / "prompts" / "pedagogical_reading_v1.md"
        ).read_text(encoding="utf-8")

    def readiness(self, source_version_id: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            version = connection.execute(
                "SELECT sv.*,s.canonical_title,s.name FROM source_versions sv "
                "JOIN sources s ON s.id=sv.source_id WHERE sv.id=?",
                (source_version_id,),
            ).fetchone()
            if version is None:
                raise ReadingError("source version not found")
            closure = connection.execute(
                "SELECT * FROM document_closure_snapshots WHERE source_version_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (source_version_id,),
            ).fetchone()
            topics = connection.execute(
                "SELECT count(DISTINCT theme_number) FROM document_consolidated_topics "
                "WHERE source_version_id=?",
                (source_version_id,),
            ).fetchone()[0]
        ready = bool(
            closure and closure["ai_readiness"] in {"ready_for_ai", "ready_for_ai_with_issues"}
        )
        return {
            "source_version_id": source_version_id,
            "source_id": version["source_id"],
            "title": version["canonical_title"] or version["name"],
            "document_hash": version["content_hash"],
            "activation_state": version["activation_state"],
            "is_active": bool(version["is_active"]),
            "ai_readiness": closure["ai_readiness"] if closure else "not_ready_for_ai",
            "topic_count": int(topics),
            "ready": ready,
        }

    def create_run(self, request: ReadingRunCreate, *, resolved_model: str) -> dict[str, Any]:
        ready = self.readiness(request.source_version_id)
        if not ready["ready"]:
            raise ReadingError("source version is not AI-ready")
        now = _now()
        run_id = _id()
        config = {
            "max_passes": request.max_passes,
            "input_character_budget": DEFAULT_INPUT_CHARACTERS,
            "output_token_budget": DEFAULT_OUTPUT_TOKENS,
        }
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO pedagogical_reading_runs(id,source_id,source_version_id,document_hash,"
                "language,pass_number,model_role,resolved_model,configuration_json,prompt_version,"
                "state,auto_continue,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    ready["source_id"],
                    request.source_version_id,
                    ready["document_hash"],
                    request.language,
                    request.model_role,
                    resolved_model,
                    _dump(config),
                    PROMPT_VERSION,
                    "planned",
                    int(request.auto_continue),
                    now,
                    now,
                ),
            )
            topics = connection.execute(
                "SELECT id,theme_number FROM document_consolidated_topics t "
                "WHERE source_version_id=? AND created_at=(SELECT max(created_at) "
                "FROM document_consolidated_topics latest WHERE latest.source_version_id=t.source_version_id "
                "AND latest.theme_number=t.theme_number) ORDER BY theme_number",
                (request.source_version_id,),
            ).fetchall()
            for topic in topics:
                context = self._build_context(
                    connection, request.source_version_id, int(topic["theme_number"]), config
                )
                connection.execute(
                    "INSERT INTO pedagogical_reading_topic_stages(id,run_id,topic_number,canonical_topic_id,"
                    "input_fingerprint,state,block_scope_json,budget_json,target_reasons_json,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        _id(),
                        run_id,
                        int(topic["theme_number"]),
                        topic["id"],
                        context["fingerprint"],
                        "pending",
                        _dump(context["block_ids"]),
                        _dump(context["budget"]),
                        _dump(["broad_pass_1"]),
                        now,
                        now,
                    ),
                )
        self._capture_coverage(run_id)
        return self.detail(run_id)

    def preview_plan(self, source_version_id: int) -> dict[str, Any]:
        """Build the deterministic plan without creating a run or calling a model."""
        ready = self.readiness(source_version_id)
        config = {
            "input_character_budget": DEFAULT_INPUT_CHARACTERS,
            "output_token_budget": DEFAULT_OUTPUT_TOKENS,
        }
        with self.database.connect() as connection:
            topics = connection.execute(
                "SELECT DISTINCT theme_number FROM document_consolidated_topics "
                "WHERE source_version_id=? ORDER BY theme_number",
                (source_version_id,),
            ).fetchall()
            plans = [
                {
                    "topic_number": int(row[0]),
                    **self._build_context(connection, source_version_id, int(row[0]), config),
                }
                for row in topics
            ]
        return {
            **ready,
            "planned_topics": len(plans),
            "topics": [
                {
                    "topic_number": plan["topic_number"],
                    "input_fingerprint": plan["fingerprint"],
                    "block_count": len(plan["block_ids"]),
                    "unit_count": len(plan["units"]),
                    "budget": plan["budget"],
                }
                for plan in plans
            ],
        }

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM pedagogical_reading_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self.detail(row["id"]) for row in rows]

    def detail(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            run = connection.execute(
                "SELECT * FROM pedagogical_reading_runs WHERE id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise ReadingError("reading run not found")
            stages = connection.execute(
                "SELECT * FROM pedagogical_reading_topic_stages WHERE run_id=? ORDER BY topic_number",
                (run_id,),
            ).fetchall()
            unresolved_count = connection.execute(
                "SELECT count(*) FROM pedagogical_reading_unresolved WHERE run_id=? AND state='open'",
                (run_id,),
            ).fetchone()[0]
            conflict_count = connection.execute(
                "SELECT count(*) FROM pedagogical_reading_conflicts WHERE run_id=? AND state='open'",
                (run_id,),
            ).fetchone()[0]
        result = self._run(run)
        result["stages"] = [self._stage(row) for row in stages]
        result["issues"] = sum(
            1
            for row in stages
            if row["state"] in {"completed_with_issues", "failed"}
            or row["rejected_output_count"]
            or row["warning_count"]
        )
        result["unresolved_count"] = int(unresolved_count)
        result["conflict_count"] = int(conflict_count)
        return result

    def stages(self, run_id: str) -> list[dict[str, Any]]:
        return self.detail(run_id)["stages"]

    def pause(self, run_id: str) -> dict[str, Any]:
        return self._control(run_id, "paused", "manual")

    def resume(self, run_id: str) -> dict[str, Any]:
        return self._control(run_id, "queued", None)

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self._control(run_id, "cancelled", None)

    def retry_failed(self, run_id: str, topics: list[int] | None = None) -> dict[str, Any]:
        now = _now()
        with self.database.transaction(immediate=True) as connection:
            query = "UPDATE pedagogical_reading_topic_stages SET state='pending',error_code=NULL,error_detail=NULL,updated_at=? WHERE run_id=? AND state='failed'"
            params: list[Any] = [now, run_id]
            if topics:
                query += f" AND topic_number IN ({','.join('?' for _ in topics)})"
                params.extend(topics)
            connection.execute(query, params)
            connection.execute(
                "UPDATE pedagogical_reading_runs SET state='queued',stop_reason=NULL,updated_at=? WHERE id=?",
                (now, run_id),
            )
        return self.detail(run_id)

    def create_next_pass(self, run_id: str) -> dict[str, Any]:
        previous = self.detail(run_id)
        max_passes = int(previous["configuration"]["max_passes"])
        if previous["pass_number"] >= max_passes:
            raise ReadingError("maximum reading passes reached")
        if previous["state"] not in {"completed", "completed_with_issues"}:
            raise ReadingError("next pass requires a completed reading run")
        targets = self._next_targets(run_id)
        if not targets:
            raise ReadingError("no material improvement target remains")
        now = _now()
        next_id = _id()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO pedagogical_reading_runs(id,source_id,source_version_id,document_hash,language,"
                "parent_run_id,base_run_id,pass_number,model_role,resolved_model,configuration_json,"
                "prompt_version,state,auto_continue,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    next_id,
                    previous["source_id"],
                    previous["source_version_id"],
                    previous["document_hash"],
                    previous["language"],
                    run_id,
                    previous["base_run_id"] or run_id,
                    previous["pass_number"] + 1,
                    previous["model_role"],
                    previous["resolved_model"],
                    _dump(previous["configuration"]),
                    PROMPT_VERSION,
                    "planned",
                    int(previous["auto_continue"]),
                    now,
                    now,
                ),
            )
            for topic, reasons in targets.items():
                context = self._build_context(
                    connection, previous["source_version_id"], topic, previous["configuration"]
                )
                canonical = connection.execute(
                    "SELECT id FROM document_consolidated_topics WHERE source_version_id=? "
                    "AND theme_number=? ORDER BY created_at DESC LIMIT 1",
                    (previous["source_version_id"], topic),
                ).fetchone()
                connection.execute(
                    "INSERT INTO pedagogical_reading_topic_stages(id,run_id,topic_number,canonical_topic_id,input_fingerprint,"
                    "state,block_scope_json,budget_json,target_reasons_json,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        _id(),
                        next_id,
                        topic,
                        canonical["id"] if canonical else None,
                        context["fingerprint"],
                        "pending",
                        _dump(context["block_ids"]),
                        _dump(context["budget"]),
                        _dump(sorted(reasons)),
                        now,
                        now,
                    ),
                )
        self._capture_coverage(next_id)
        return self.detail(next_id)

    async def execute(self, run_id: str) -> dict[str, Any]:
        if self.router is None:
            raise ReadingError("model router is required for execution")
        self._assert_current_document(run_id)
        self._control(run_id, "running", None)
        for stage in self.stages(run_id):
            current = self.detail(run_id)
            if current["state"] in {"paused", "cancelled"}:
                break
            if stage["state"] in {"completed", "completed_with_issues", "skipped"}:
                continue
            if ai_coordinator.interactive_active:
                self._control(run_id, "paused", "waiting_for_interactive_ai")
                await ai_coordinator.wait_for_interactive_idle()
                if self.detail(run_id)["pause_reason"] == "waiting_for_interactive_ai":
                    self._control(run_id, "running", None)
            try:
                await self.execute_topic(run_id, stage["topic_number"])
            except Exception:
                # Each topic is an atomic, durable unit; later topics remain independent.
                continue
        finished = self._finish(run_id)
        if finished["stop_reason"] == "automatic_next_pass_available":
            next_pass = self.create_next_pass(run_id)
            self._control(next_pass["id"], "queued", None)
            return await self.execute(next_pass["id"])
        return finished

    async def execute_topic(self, run_id: str, topic_number: int) -> dict[str, Any]:
        if self.router is None:
            raise ReadingError("model router is required for execution")
        started = time.monotonic()
        now = _now()
        with self.database.transaction(immediate=True) as connection:
            stage = connection.execute(
                "SELECT * FROM pedagogical_reading_topic_stages WHERE run_id=? AND topic_number=?",
                (run_id, topic_number),
            ).fetchone()
            run = connection.execute(
                "SELECT * FROM pedagogical_reading_runs WHERE id=?", (run_id,)
            ).fetchone()
            if stage is None or run is None:
                raise ReadingError("reading run or topic stage not found")
            connection.execute(
                "DELETE FROM pedagogical_reading_conflicts WHERE run_id=? AND topic_number=?",
                (run_id, topic_number),
            )
            connection.execute(
                "DELETE FROM pedagogical_reading_unresolved WHERE stage_id=?", (stage["id"],)
            )
            connection.execute(
                "DELETE FROM pedagogical_reading_candidates WHERE stage_id=?", (stage["id"],)
            )
            connection.execute(
                "UPDATE pedagogical_reading_topic_stages SET state='running',attempts=attempts+1,"
                "presented_block_ids_json='[]',rejected_output_count=0,warning_count=0,"
                "unresolved_count=0,started_at=?,updated_at=? WHERE id=?",
                (now, now, stage["id"]),
            )
            context = self._build_context(
                connection,
                run["source_version_id"],
                topic_number,
                json.loads(run["configuration_json"]),
            )
        try:
            if not context["units"]:
                raise ReadingError("topic has no eligible observed text blocks")
            role = ModelRole.DEEP if run["model_role"] == "deep" else ModelRole.TEACHER
            issues = 0
            for unit in context["units"]:
                self._mark_presented(stage["id"], [str(block["evidence_id"]) for block in unit])
                messages = [
                    {"role": "system", "content": self.prompt},
                    {
                        "role": "user",
                        "content": _dump(
                            {"contract": PROMPT_VERSION, "topic": topic_number, "evidence": unit}
                        ),
                    },
                ]
                output, model, _ = await self.router.structured_generate(
                    role, messages, PedagogicalReadingOutput, interactive=False
                )
                issues += self.persist_output(run_id, stage["id"], output, model=model)
            state = "completed_with_issues" if issues else "completed"
            self._finish_stage(stage["id"], state, int((time.monotonic() - started) * 1000), None)
        except Exception as exc:
            self._clear_stage_outputs(run_id, stage["id"], topic_number)
            self._finish_stage(
                stage["id"], "failed", int((time.monotonic() - started) * 1000), str(exc)
            )
            raise
        return self.detail(run_id)

    def persist_output(
        self, run_id: str, stage_id: str, output: PedagogicalReadingOutput, *, model: str
    ) -> int:
        now = _now()
        rejected = 0
        local_ids: dict[str, str] = {}
        with self.database.transaction(immediate=True) as connection:
            run = connection.execute(
                "SELECT * FROM pedagogical_reading_runs WHERE id=?", (run_id,)
            ).fetchone()
            stage = connection.execute(
                "SELECT * FROM pedagogical_reading_topic_stages WHERE id=?", (stage_id,)
            ).fetchone()
            if output.topic != stage["topic_number"]:
                raise EvidenceRejected("output topic is outside stage scope")
            allowed = set(json.loads(stage["block_scope_json"]))
            if not set(output.evidence_scope).issubset(allowed):
                raise EvidenceRejected("output evidence scope contains unknown blocks")
            for candidate in output.candidates:
                if candidate.candidate_type not in CANDIDATE_TYPES:
                    rejected += 1
                    continue
                try:
                    evidence = [
                        self._validate_evidence(connection, run, stage, item, allowed)
                        for item in candidate.evidence
                    ]
                except EvidenceRejected:
                    rejected += 1
                    continue
                content_hash = _hash(
                    _dump({"type": candidate.candidate_type, "content": candidate.content})
                )
                evidence_placeholders = ",".join("?" for _ in evidence)
                prior = connection.execute(
                    "SELECT c.id FROM pedagogical_reading_candidates c JOIN pedagogical_reading_evidence e ON e.candidate_id=c.id "
                    "WHERE c.source_version_id=? AND c.topic_number=? AND c.content_hash=? AND c.run_id<>? "
                    f"AND e.evidence_hash NOT IN ({evidence_placeholders}) LIMIT 1",
                    [
                        run["source_version_id"],
                        stage["topic_number"],
                        content_hash,
                        run_id,
                        *[item["hash"] for item in evidence],
                    ],
                ).fetchone()
                state = "corroborated" if prior else "proposed"
                existing = connection.execute(
                    "SELECT id FROM pedagogical_reading_candidates "
                    "WHERE run_id=? AND topic_number=? AND content_hash=?",
                    (run_id, stage["topic_number"], content_hash),
                ).fetchone()
                candidate_id = existing["id"] if existing else _id()
                local_ids[candidate.local_id] = candidate_id
                if existing is None:
                    connection.execute(
                        "INSERT INTO pedagogical_reading_candidates(id,run_id,stage_id,language,source_id,"
                        "source_version_id,topic_number,canonical_topic_id,candidate_type,content_json,terminology_de_json,"
                        "terminology_es_json,observed_level,model,model_role,pass_number,prompt_version,confidence,"
                        "uncertainty,state,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            candidate_id,
                            run_id,
                            stage_id,
                            run["language"],
                            run["source_id"],
                            run["source_version_id"],
                            stage["topic_number"],
                            stage["canonical_topic_id"],
                            candidate.candidate_type,
                            _dump(candidate.content),
                            _dump(candidate.terminology_de),
                            _dump(candidate.terminology_es),
                            candidate.observed_level,
                            model,
                            run["model_role"],
                            run["pass_number"],
                            PROMPT_VERSION,
                            candidate.confidence,
                            candidate.uncertainty,
                            state,
                            content_hash,
                            now,
                            now,
                        ),
                    )
                for item in evidence:
                    connection.execute(
                        "INSERT OR IGNORE INTO pedagogical_reading_evidence(id,candidate_id,source_version_id,page_id,block_id,quote,evidence_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (
                            _id(),
                            candidate_id,
                            run["source_version_id"],
                            item["page_id"],
                            item["block_id"],
                            item["quote"],
                            item["hash"],
                            now,
                        ),
                    )
            for relation in output.relations:
                if not set(relation.evidence_ids).issubset(allowed):
                    rejected += 1
                    continue
                source = local_ids.get(relation.source_local_id)
                target = local_ids.get(relation.target_local_id)
                if source and target:
                    connection.execute(
                        "INSERT OR IGNORE INTO pedagogical_reading_relations(id,run_id,source_candidate_id,target_candidate_id,relation_type,state,evidence_json,created_at) VALUES (?,?,?,?,?,'proposed',?,?)",
                        (
                            _id(),
                            run_id,
                            source,
                            target,
                            relation.relation_type,
                            _dump(relation.evidence_ids),
                            now,
                        ),
                    )
            for conflict in output.conflicts:
                if not set(conflict.evidence_ids).issubset(allowed):
                    rejected += 1
                    continue
                ids = [
                    local_ids[item] for item in conflict.candidate_local_ids if item in local_ids
                ]
                if len(ids) >= 2:
                    connection.execute(
                        "INSERT INTO pedagogical_reading_conflicts(id,run_id,topic_number,candidate_ids_json,description,evidence_json,state,created_at) VALUES (?,?,?,?,?,?,'open',?)",
                        (
                            _id(),
                            run_id,
                            stage["topic_number"],
                            _dump(ids),
                            conflict.description,
                            _dump(conflict.evidence_ids),
                            now,
                        ),
                    )
                    connection.execute(
                        f"UPDATE pedagogical_reading_candidates SET state='conflicted',updated_at=? WHERE id IN ({','.join('?' for _ in ids)})",
                        [now, *ids],
                    )
            for description in output.unresolved:
                connection.execute(
                    "INSERT INTO pedagogical_reading_unresolved(id,run_id,stage_id,topic_number,description,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (_id(), run_id, stage_id, stage["topic_number"], description, now),
                )
            connection.execute(
                "UPDATE pedagogical_reading_topic_stages SET rejected_output_count="
                "rejected_output_count+?,warning_count=warning_count+?,"
                "unresolved_count=unresolved_count+?,updated_at=? WHERE id=?",
                (rejected, len(output.warnings), len(output.unresolved), now, stage_id),
            )
        return rejected + len(output.unresolved) + len(output.warnings)

    def coverage(self, run_id: str) -> dict[str, Any]:
        metrics = self._coverage_metrics(run_id)
        with self.database.connect() as connection:
            snapshot = connection.execute(
                "SELECT * FROM pedagogical_reading_coverage WHERE run_id=? "
                "ORDER BY captured_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if snapshot:
            metrics["delta"] = json.loads(snapshot["delta_json"])
            metrics["material_improvement"] = bool(snapshot["material_improvement"])
            metrics["captured_at"] = snapshot["captured_at"]
        return metrics

    def candidates_summary(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT candidate_type,state,count(*) count FROM pedagogical_reading_candidates "
                "WHERE run_id=? GROUP BY candidate_type,state ORDER BY candidate_type,state",
                (run_id,),
            ).fetchall()
        return {"run_id": run_id, "counts": [dict(row) for row in rows]}

    def unresolved(self, run_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            candidates = connection.execute(
                "SELECT id,topic_number,candidate_type,content_json,uncertainty,state FROM "
                "pedagogical_reading_candidates WHERE run_id=? AND (candidate_type='unresolved_claim' "
                "OR state IN ('needs_review','conflicted')) ORDER BY topic_number,created_at",
                (run_id,),
            ).fetchall()
            observations = connection.execute(
                "SELECT id,topic_number,description,state,created_at FROM "
                "pedagogical_reading_unresolved WHERE run_id=? ORDER BY topic_number,created_at",
                (run_id,),
            ).fetchall()
        return [
            {**dict(row), "content": json.loads(row["content_json"]), "kind": "candidate"}
            for row in candidates
        ] + [{**dict(row), "kind": "observation"} for row in observations]

    def conflicts(self, run_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM pedagogical_reading_conflicts WHERE run_id=? ORDER BY topic_number,created_at",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def scheduler_state(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id,state,pause_reason,source_version_id,pass_number FROM pedagogical_reading_runs "
                "WHERE state IN ('queued','running','paused') ORDER BY updated_at DESC"
            ).fetchall()
        return {
            "interactive_ai_active": ai_coordinator.interactive_active,
            "active_runs": [dict(row) for row in rows],
        }

    def _build_context(
        self, connection, version_id: int, topic: int, config: dict[str, Any]
    ) -> dict[str, Any]:
        rows = connection.execute(
            "SELECT DISTINCT b.id block_id,b.page_id,p.pdf_page_index,b.raw_text,b.normalized_layout_text,b.block_type,b.reading_order "
            "FROM document_consolidated_nodes n JOIN document_structure_candidates c ON c.id=n.source_candidate_id "
            "JOIN document_page_blocks b ON b.id=c.block_id JOIN document_pages p ON p.id=b.page_id "
            "WHERE n.source_version_id=? AND n.theme_number=? ORDER BY p.pdf_page_index,b.reading_order",
            (version_id, topic),
        ).fetchall()
        blocks = [
            {
                "evidence_id": row["block_id"],
                "page_id": row["page_id"],
                "pdf_page": row["pdf_page_index"] + 1,
                "type": row["block_type"],
                "text": row["raw_text"] or row["normalized_layout_text"] or "",
            }
            for row in rows
            if row["raw_text"] or row["normalized_layout_text"]
        ]
        limit = int(config.get("input_character_budget", DEFAULT_INPUT_CHARACTERS))
        units: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        size = 0
        for block in blocks:
            block_size = len(block["text"])
            if current and size + block_size > limit:
                units.append(current)
                current, size = [], 0
            current.append(block)
            size += block_size
        if current:
            units.append(current)
        payload = _dump(blocks)
        return {
            "units": units,
            "block_ids": [item["evidence_id"] for item in blocks],
            "fingerprint": _hash(payload),
            "budget": {
                "estimated_input_characters": len(payload),
                "selected_blocks": len(blocks),
                "omitted_blocks": 0,
                "split_reason": "structural_block_boundary" if len(units) > 1 else None,
                "units": len(units),
                "output_tokens_reserved": int(
                    config.get("output_token_budget", DEFAULT_OUTPUT_TOKENS)
                ),
            },
        }

    def _validate_evidence(
        self, connection, run, stage, item: EvidenceReference, allowed: set[str]
    ) -> dict[str, Any]:
        if item.block_id not in allowed:
            raise EvidenceRejected("block is outside topic scope")
        row = connection.execute(
            "SELECT b.id,b.source_version_id,b.page_id,b.raw_text,b.normalized_layout_text,p.source_version_id page_version "
            "FROM document_page_blocks b JOIN document_pages p ON p.id=b.page_id WHERE b.id=?",
            (item.block_id,),
        ).fetchone()
        if (
            row is None
            or row["source_version_id"] != run["source_version_id"]
            or row["page_version"] != run["source_version_id"]
        ):
            raise EvidenceRejected("block/page/version mismatch")
        observed = _normalized(row["raw_text"] or row["normalized_layout_text"] or "")
        quote = _normalized(item.quote)
        if not quote or quote not in observed:
            raise EvidenceRejected("quote is not present in observed text")
        return {
            "page_id": row["page_id"],
            "block_id": row["id"],
            "quote": item.quote,
            "hash": _hash(f"{row['id']}:{quote}"),
        }

    def _next_targets(self, run_id: str) -> dict[int, set[str]]:
        targets: dict[int, set[str]] = {}
        with self.database.connect() as connection:
            for row in connection.execute(
                "SELECT topic_number,state,block_scope_json,presented_block_ids_json,"
                "rejected_output_count FROM pedagogical_reading_topic_stages WHERE run_id=?",
                (run_id,),
            ):
                if row["state"] == "failed":
                    targets.setdefault(row["topic_number"], set()).add("failed_topic")
                if row["state"] == "completed_with_issues":
                    targets.setdefault(row["topic_number"], set()).add("unresolved_output")
                if set(json.loads(row["block_scope_json"])) - set(
                    json.loads(row["presented_block_ids_json"])
                ) and row["state"] not in {"pending", "skipped"}:
                    targets.setdefault(row["topic_number"], set()).add("block_not_processed")
                if row["rejected_output_count"]:
                    targets.setdefault(row["topic_number"], set()).add("evidence_rejected")
            for row in connection.execute(
                "SELECT c.topic_number,c.candidate_type,c.state,c.confidence,count(e.id) evidence_count "
                "FROM pedagogical_reading_candidates c LEFT JOIN pedagogical_reading_evidence e "
                "ON e.candidate_id=c.id WHERE c.run_id=? GROUP BY c.id",
                (run_id,),
            ):
                if row["candidate_type"] == "unresolved_claim":
                    targets.setdefault(row["topic_number"], set()).add("unresolved_claim")
                if row["state"] == "conflicted":
                    targets.setdefault(row["topic_number"], set()).add("open_conflict")
                if row["state"] == "proposed" and (
                    row["confidence"] < 0.85 or row["evidence_count"] < 2
                ):
                    targets.setdefault(row["topic_number"], set()).add("low_evidence_or_review")
            for row in connection.execute(
                "SELECT topic_number FROM pedagogical_reading_conflicts WHERE run_id=? AND state='open'",
                (run_id,),
            ):
                targets.setdefault(row["topic_number"], set()).add("open_conflict")
            for row in connection.execute(
                "SELECT topic_number FROM pedagogical_reading_unresolved "
                "WHERE run_id=? AND state='open'",
                (run_id,),
            ):
                targets.setdefault(row["topic_number"], set()).add("unresolved_output")
            for row in connection.execute(
                "SELECT DISTINCT s.topic_number FROM pedagogical_reading_topic_stages s "
                "JOIN json_each(s.block_scope_json) scoped JOIN document_page_blocks b "
                "ON b.id=scoped.value WHERE s.run_id=? AND b.block_type='probable_table' "
                "AND NOT EXISTS (SELECT 1 FROM pedagogical_reading_candidates c "
                "WHERE c.run_id=s.run_id AND c.topic_number=s.topic_number "
                "AND c.candidate_type='table_interpretation')",
                (run_id,),
            ):
                targets.setdefault(row["topic_number"], set()).add("pending_table")
            run = connection.execute(
                "SELECT source_version_id FROM pedagogical_reading_runs WHERE id=?", (run_id,)
            ).fetchone()
            for row in connection.execute(
                "SELECT DISTINCT theme_number FROM document_consolidated_topics "
                "WHERE source_version_id=? AND (state IN ('pending','conflicted') "
                "OR issues_json NOT IN ('[]','{}','null'))",
                (run["source_version_id"],),
            ):
                targets.setdefault(row["theme_number"], set()).add("structural_issue")
        return targets

    def _control(self, run_id: str, state: str, pause_reason: str | None) -> dict[str, Any]:
        now = _now()
        with self.database.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT state FROM pedagogical_reading_runs WHERE id=?", (run_id,)
            ).fetchone()
            if current is None:
                raise ReadingError("reading run not found")
            allowed = {
                "queued": {"planned", "queued", "paused", "interrupted", "completed_with_issues"},
                "running": {"queued", "running", "paused"},
                "paused": {"queued", "running"},
                "cancelled": {"planned", "queued", "running", "paused", "interrupted"},
            }
            if state in allowed and current["state"] not in allowed[state]:
                raise ReadingError(f"cannot transition {current['state']} to {state}")
            changed = connection.execute(
                "UPDATE pedagogical_reading_runs SET state=?,pause_reason=?,started_at=CASE WHEN ?='running' THEN coalesce(started_at,?) ELSE started_at END,updated_at=? WHERE id=?",
                (state, pause_reason, state, now, now, run_id),
            ).rowcount
            if not changed:
                raise ReadingError("reading run not found")
        return self.detail(run_id)

    def _assert_current_document(self, run_id: str) -> None:
        stale = False
        with self.database.transaction(immediate=True) as connection:
            run = connection.execute(
                "SELECT * FROM pedagogical_reading_runs WHERE id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise ReadingError("reading run not found")
            version = connection.execute(
                "SELECT content_hash FROM source_versions WHERE id=?",
                (run["source_version_id"],),
            ).fetchone()
            if version is None or version["content_hash"] != run["document_hash"]:
                stale = True
                now = _now()
                connection.execute(
                    "UPDATE pedagogical_reading_runs SET state='stale',"
                    "stop_reason='document_hash_changed',updated_at=? WHERE id=?",
                    (now, run_id),
                )
                connection.execute(
                    "UPDATE pedagogical_reading_topic_stages SET state='stale',updated_at=? "
                    "WHERE run_id=? AND state='pending'",
                    (now, run_id),
                )
        if stale:
            raise ReadingError("reading run is stale because the document hash changed")

    def _finish_stage(self, stage_id: str, state: str, duration: int, error: str | None) -> None:
        now = _now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE pedagogical_reading_topic_stages SET state=?,duration_ms=?,error_code=?,"
                "error_detail=?,completed_at=?,updated_at=? WHERE id=?",
                (state, duration, "topic_failed" if error else None, error, now, now, stage_id),
            )

    def _mark_presented(self, stage_id: str, block_ids: list[str]) -> None:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT presented_block_ids_json FROM pedagogical_reading_topic_stages WHERE id=?",
                (stage_id,),
            ).fetchone()
            presented = set(json.loads(row["presented_block_ids_json"]))
            presented.update(block_ids)
            connection.execute(
                "UPDATE pedagogical_reading_topic_stages SET presented_block_ids_json=?,updated_at=? "
                "WHERE id=?",
                (_dump(sorted(presented)), _now(), stage_id),
            )

    def _clear_stage_outputs(self, run_id: str, stage_id: str, topic_number: int) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "DELETE FROM pedagogical_reading_conflicts WHERE run_id=? AND topic_number=?",
                (run_id, topic_number),
            )
            connection.execute(
                "DELETE FROM pedagogical_reading_unresolved WHERE stage_id=?", (stage_id,)
            )
            connection.execute(
                "DELETE FROM pedagogical_reading_candidates WHERE stage_id=?", (stage_id,)
            )

    def _finish(self, run_id: str) -> dict[str, Any]:
        detail = self.detail(run_id)
        if detail["state"] in {"paused", "cancelled"}:
            return detail
        states = Counter(stage["state"] for stage in detail["stages"])
        state = (
            "completed_with_issues"
            if states["failed"] or states["completed_with_issues"]
            else "completed"
        )
        self._capture_coverage(run_id)
        coverage = self.coverage(run_id)
        targets = self._next_targets(run_id)
        max_passes = int(detail["configuration"]["max_passes"])
        if detail["pass_number"] >= max_passes:
            stop_reason = "maximum_passes"
        elif detail["pass_number"] > 1 and not coverage["material_improvement"]:
            stop_reason = "no_material_improvement"
        elif not targets:
            stop_reason = "no_material_improvement_target"
        elif detail["auto_continue"]:
            stop_reason = "automatic_next_pass_available"
        else:
            stop_reason = "awaiting_manual_next_pass"
        now = _now()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE pedagogical_reading_runs SET state=?,stop_reason=?,completed_at=?,updated_at=? WHERE id=?",
                (state, stop_reason, now, now, run_id),
            )
        return self.detail(run_id)

    def _coverage_metrics(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            run = connection.execute(
                "SELECT * FROM pedagogical_reading_runs WHERE id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise ReadingError("reading run not found")
            stages = connection.execute(
                "SELECT state,block_scope_json,presented_block_ids_json,rejected_output_count "
                "FROM pedagogical_reading_topic_stages WHERE run_id=?",
                (run_id,),
            ).fetchall()
            candidates = connection.execute(
                "SELECT candidate_type,state,count(*) n FROM pedagogical_reading_candidates "
                "WHERE run_id=? GROUP BY candidate_type,state",
                (run_id,),
            ).fetchall()
            evidence = connection.execute(
                "SELECT count(*) FROM pedagogical_reading_evidence e "
                "JOIN pedagogical_reading_candidates c ON c.id=e.candidate_id WHERE c.run_id=?",
                (run_id,),
            ).fetchone()[0]
            relations = connection.execute(
                "SELECT count(*) FROM pedagogical_reading_relations WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            conflicts = connection.execute(
                "SELECT count(*) total,sum(CASE WHEN state='open' THEN 1 ELSE 0 END) open_count,"
                "sum(CASE WHEN state='resolved' THEN 1 ELSE 0 END) resolved_count "
                "FROM pedagogical_reading_conflicts WHERE run_id=?",
                (run_id,),
            ).fetchone()
            unresolved = connection.execute(
                "SELECT count(*) FROM pedagogical_reading_unresolved WHERE run_id=? AND state='open'",
                (run_id,),
            ).fetchone()[0]
            uncertainty = connection.execute(
                "SELECT count(*) FROM pedagogical_reading_candidates "
                "WHERE run_id=? AND uncertainty IS NOT NULL AND trim(uncertainty)<>''",
                (run_id,),
            ).fetchone()[0]
            scoped_ids = {
                block_id for row in stages for block_id in json.loads(row["block_scope_json"])
            }
            table_ids: set[str] = set()
            if scoped_ids:
                placeholders = ",".join("?" for _ in scoped_ids)
                table_ids = {
                    row["id"]
                    for row in connection.execute(
                        f"SELECT id FROM document_page_blocks WHERE id IN ({placeholders}) "
                        "AND block_type='probable_table'",
                        sorted(scoped_ids),
                    )
                }
        states = Counter(row["state"] for row in stages)
        candidate_states = Counter()
        for row in candidates:
            candidate_states[row["state"]] += int(row["n"])
        presented_by_stage = [set(json.loads(row["presented_block_ids_json"])) for row in stages]
        processed_ids = {
            block_id
            for row, presented in zip(stages, presented_by_stage, strict=True)
            if row["state"] in {"completed", "completed_with_issues"}
            for block_id in presented
        }
        return {
            "run_id": run_id,
            "pass_number": run["pass_number"],
            "eligible_blocks": sum(len(json.loads(row["block_scope_json"])) for row in stages),
            "presented_blocks": len(set().union(*presented_by_stage)) if stages else 0,
            "processed_blocks": len(processed_ids),
            "failed_blocks": sum(
                len(presented)
                for row, presented in zip(stages, presented_by_stage, strict=True)
                if row["state"] == "failed"
            ),
            "eligible_tables": len(table_ids),
            "processed_tables": len(table_ids & processed_ids),
            "failed_stages": states["failed"],
            "candidates": {
                f"{row['candidate_type']}:{row['state']}": row["n"] for row in candidates
            },
            "candidate_total": sum(row["n"] for row in candidates),
            "proposed": candidate_states["proposed"],
            "corroborated": candidate_states["corroborated"],
            "needs_review": candidate_states["needs_review"],
            "conflicted": candidate_states["conflicted"],
            "evidence_links": evidence,
            "relations": relations,
            "conflicts_created": int(conflicts["total"] or 0),
            "conflicts_resolved": int(conflicts["resolved_count"] or 0),
            "open_conflicts": int(conflicts["open_count"] or 0),
            "open_unresolved": unresolved,
            "rejected_unsupported_outputs": sum(row["rejected_output_count"] for row in stages),
            "new_uncertainty": uncertainty,
        }

    def _capture_coverage(self, run_id: str) -> None:
        metrics = self._coverage_metrics(run_id)
        with self.database.transaction(immediate=True) as connection:
            run = connection.execute(
                "SELECT parent_run_id,pass_number FROM pedagogical_reading_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if run["parent_run_id"]:
                previous = connection.execute(
                    "SELECT metrics_json FROM pedagogical_reading_coverage WHERE run_id=? "
                    "ORDER BY captured_at DESC LIMIT 1",
                    (run["parent_run_id"],),
                ).fetchone()
            else:
                previous = connection.execute(
                    "SELECT metrics_json FROM pedagogical_reading_coverage WHERE run_id=? "
                    "ORDER BY captured_at DESC LIMIT 1",
                    (run_id,),
                ).fetchone()
            baseline = json.loads(previous["metrics_json"]) if previous else {}
            keys = (
                "presented_blocks",
                "processed_blocks",
                "failed_blocks",
                "candidate_total",
                "corroborated",
                "evidence_links",
                "relations",
                "conflicts_created",
                "conflicts_resolved",
                "open_conflicts",
                "open_unresolved",
                "rejected_unsupported_outputs",
                "new_uncertainty",
            )
            delta = {key: int(metrics[key]) - int(baseline.get(key, 0)) for key in keys}
            material = any(
                delta[key] > 0
                for key in (
                    "processed_blocks",
                    "candidate_total",
                    "corroborated",
                    "evidence_links",
                    "relations",
                    "conflicts_created",
                    "conflicts_resolved",
                    "new_uncertainty",
                )
            )
            material = material or delta["open_unresolved"] < 0 or delta["failed_blocks"] < 0
            connection.execute(
                "INSERT INTO pedagogical_reading_coverage(id,run_id,pass_number,metrics_json,delta_json,"
                "material_improvement,captured_at) VALUES (?,?,?,?,?,?,?)",
                (
                    _id(),
                    run_id,
                    run["pass_number"],
                    _dump(metrics),
                    _dump(delta),
                    int(material),
                    _now(),
                ),
            )

    @staticmethod
    def _run(row) -> dict[str, Any]:
        result = dict(row)
        result["configuration"] = json.loads(result.pop("configuration_json"))
        result["auto_continue"] = bool(result["auto_continue"])
        return result

    @staticmethod
    def _stage(row) -> dict[str, Any]:
        result = dict(row)
        for source, target in (
            ("block_scope_json", "block_scope"),
            ("presented_block_ids_json", "presented_block_ids"),
            ("budget_json", "budget"),
            ("target_reasons_json", "target_reasons"),
        ):
            result[target] = json.loads(result.pop(source))
        return result
