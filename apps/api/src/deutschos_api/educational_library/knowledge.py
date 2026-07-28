from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

from deutschos_api.providers.base import (
    MalformedStructuredOutputError,
    ModelProvider,
    ProviderResponseError,
    ProviderUnavailableError,
)

from .database import LibraryDatabase
from .schemas import (
    GroundedDraftPayload,
    GroundedGenerationRead,
    GroundedGenerationRequest,
    GroundedSource,
    KnowledgeCitation,
    KnowledgeDraft,
    KnowledgeGenerationRequest,
    KnowledgeReviewRequest,
    KnowledgeStatus,
    KnowledgeUnitRead,
    LibraryContractError,
    LibraryNotFoundError,
    LibraryProviderUnavailableError,
)
from .search import EducationalSearchService
from .service import json_dump, json_load, utc_text

PROMPT_VERSION_KNOWLEDGE = "library-knowledge.v2"
PROMPT_VERSION_GROUNDED = "library-grounded.v1"
PROMPT_ROOT = Path(__file__).resolve().parents[1] / "prompts"
UNVERIFIABLE_CITATION_WARNING = (
    "Se descartaron citas del modelo que no coincidían literalmente; requiere revisión humana."
)


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _content_hash(draft: KnowledgeDraft) -> str:
    payload = {
        "kind": draft.kind.value,
        "title": _normalise(draft.title),
        "content_es": _normalise(draft.content_es),
        "examples": sorted(_normalise(value) for value in draft.german_examples),
        "sources": sorted(citation.chunk_id for citation in draft.citations),
    }
    return hashlib.sha256(json_dump(payload).encode()).hexdigest()


class EducationalKnowledgeService:
    def __init__(
        self,
        database: LibraryDatabase,
        search: EducationalSearchService,
        model_provider: ModelProvider,
        *,
        default_model: str,
    ):
        self.database = database
        self.search = search
        self.model_provider = model_provider
        self.default_model = default_model

    async def generate_knowledge(self, request: KnowledgeGenerationRequest) -> KnowledgeUnitRead:
        model = self.default_model
        if not model:
            raise LibraryProviderUnavailableError("No hay un modelo local configurado.")
        search = await self.search.search(
            request.query,
            mode="hybrid",
            limit=request.max_chunks,
            include_solutions=False,
        )
        if not search.results:
            raise LibraryContractError("No existe evidencia suficiente para derivar conocimiento.")
        chunks = {result.id: result for result in search.results}
        context = [
            {
                "chunk_id": result.id,
                "source": result.source_name,
                "page_start": result.page_start,
                "page_end": result.page_end,
                "start_seconds": result.start_seconds,
                "end_seconds": result.end_seconds,
                "content_role": result.content_role,
                "text": result.text[:2_500],
            }
            for result in search.results
        ]
        system = (PROMPT_ROOT / "library_knowledge_v2.md").read_text(encoding="utf-8")
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"CONSULTA: {request.query}\n\nCONTEXTO:\n"
                    + json.dumps(context, ensure_ascii=False)
                ),
            },
        ]
        try:
            draft = await self.model_provider.structured_generate(model, messages, KnowledgeDraft)
        except (
            ProviderUnavailableError,
            ProviderResponseError,
            MalformedStructuredOutputError,
        ) as exc:
            raise LibraryProviderUnavailableError(
                "La generación pedagógica local no está disponible."
            ) from exc
        if not draft.evidence_sufficient:
            raise LibraryContractError(
                "La evidencia recuperada no permite derivar una unidad pedagógica fiable."
            )
        valid_citations, invalid_citations = self._partition_citations(draft, chunks)
        if invalid_citations:
            repair_messages = [
                *messages,
                {"role": "assistant", "content": draft.model_dump_json()},
                {
                    "role": "user",
                    "content": (
                        "Corrige únicamente la fundamentación. Cada quote debe ser una subcadena "
                        "literal breve, copiada carácter por carácter del text del mismo chunk_id. "
                        "No uses saltos de línea, flechas ni reconstrucciones. Conserva solo "
                        "afirmaciones sostenidas directamente y devuelve el JSON completo."
                    ),
                },
            ]
            try:
                draft = await self.model_provider.structured_generate(
                    model, repair_messages, KnowledgeDraft
                )
            except (
                ProviderUnavailableError,
                ProviderResponseError,
                MalformedStructuredOutputError,
            ) as exc:
                raise LibraryProviderUnavailableError(
                    "La generación pedagógica local no está disponible."
                ) from exc
            if not draft.evidence_sufficient:
                raise LibraryContractError(
                    "La evidencia recuperada no permite derivar una unidad pedagógica fiable."
                ) from None
            valid_citations, invalid_citations = self._partition_citations(draft, chunks)
        if not valid_citations:
            raise LibraryContractError("La unidad generada no contiene ninguna cita verificable.")
        if invalid_citations:
            draft = draft.model_copy(
                update={
                    "citations": valid_citations,
                    "warnings": [*draft.warnings, UNVERIFIABLE_CITATION_WARNING],
                    "confidence": min(draft.confidence, 0.65),
                }
            )
        return self._persist_knowledge(draft, model)

    def _partition_citations(
        self, draft: KnowledgeDraft, chunks: dict[int, object]
    ) -> tuple[list[KnowledgeCitation], list[KnowledgeCitation]]:
        valid: list[KnowledgeCitation] = []
        invalid: list[KnowledgeCitation] = []
        for citation in draft.citations:
            result = chunks.get(citation.chunk_id)
            if result is None:
                invalid.append(citation)
                continue
            source_text = result.text
            if _normalise(citation.quote) not in _normalise(source_text):
                invalid.append(citation)
                continue
            valid.append(citation)
        return valid, invalid

    def _validate_citations(self, draft: KnowledgeDraft, chunks: dict[int, object]) -> None:
        valid, invalid = self._partition_citations(draft, chunks)
        if invalid or len(valid) != len(draft.citations):
            raise LibraryContractError("La unidad generada contiene una cita no verificable.")

    def _persist_knowledge(self, draft: KnowledgeDraft, model: str) -> KnowledgeUnitRead:
        content_hash = _content_hash(draft)
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT id FROM knowledge_units WHERE content_hash=? AND prompt_version=?",
                (content_hash, PROMPT_VERSION_KNOWLEDGE),
            ).fetchone()
            if existing:
                unit_id = existing["id"]
            else:
                unit_id = str(uuid4())
                conflict_rows = connection.execute(
                    "SELECT id FROM knowledge_units WHERE kind=? AND lower(title)=lower(?) "
                    "AND content_hash!=? AND status!='rejected'",
                    (draft.kind.value, draft.title, content_hash),
                ).fetchall()
                status = (
                    KnowledgeStatus.CONFLICT
                    if conflict_rows
                    else KnowledgeStatus.NEEDS_REVIEW
                    if UNVERIFIABLE_CITATION_WARNING in draft.warnings
                    else KnowledgeStatus.CANDIDATE
                )
                connection.execute(
                    "INSERT INTO knowledge_units(id,kind,title,content_es,german_examples_json,"
                    "translations_json,cefr_level,topics_json,keywords_json,warnings_json,confidence,"
                    "status,model,prompt_version,content_hash,stale,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        unit_id,
                        draft.kind.value,
                        draft.title,
                        draft.content_es,
                        json_dump(draft.german_examples),
                        json_dump(draft.translations),
                        draft.cefr_level,
                        json_dump(draft.topics),
                        json_dump(draft.keywords),
                        json_dump(draft.warnings),
                        draft.confidence,
                        status.value,
                        model,
                        PROMPT_VERSION_KNOWLEDGE,
                        content_hash,
                        0,
                        now,
                        now,
                    ),
                )
                for conflict in conflict_rows:
                    connection.execute(
                        "UPDATE knowledge_units SET status='conflict',updated_at=? WHERE id=?",
                        (now, conflict["id"]),
                    )
                seen_chunk_ids: set[int] = set()
                for citation in draft.citations:
                    if citation.chunk_id in seen_chunk_ids:
                        continue
                    seen_chunk_ids.add(citation.chunk_id)
                    version = connection.execute(
                        "SELECT source_version_id FROM chunks WHERE id=?", (citation.chunk_id,)
                    ).fetchone()
                    if not version:
                        raise LibraryContractError(
                            "La evidencia desapareció durante la generación."
                        )
                    connection.execute(
                        "INSERT INTO knowledge_unit_sources(knowledge_unit_id,chunk_id,"
                        "source_version_id,quote) VALUES (?,?,?,?)",
                        (unit_id, citation.chunk_id, version["source_version_id"], citation.quote),
                    )
        return self.get_knowledge(unit_id)

    def list_knowledge(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[KnowledgeUnitRead]:
        clauses: list[str] = []
        parameters: list[object] = []
        if status:
            clauses.append("status=?")
            parameters.append(status)
        if kind:
            clauses.append("kind=?")
            parameters.append(kind)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.extend([limit, offset])
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM knowledge_units" + where + " ORDER BY "
                "CASE status WHEN 'approved' THEN 0 WHEN 'candidate' THEN 1 WHEN 'needs_review' "
                "THEN 2 ELSE 3 END,confidence DESC,created_at DESC LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        return [self._knowledge_read(row) for row in rows]

    def get_knowledge(self, unit_id: str) -> KnowledgeUnitRead:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_units WHERE id=?", (unit_id,)
            ).fetchone()
        if not row:
            raise LibraryNotFoundError("La unidad pedagógica no existe.")
        return self._knowledge_read(row)

    def review(self, unit_id: str, request: KnowledgeReviewRequest) -> KnowledgeUnitRead:
        status_map = {
            "approve": KnowledgeStatus.APPROVED.value,
            "reject": KnowledgeStatus.REJECTED.value,
            "conflict": KnowledgeStatus.CONFLICT.value,
            "needs_review": KnowledgeStatus.NEEDS_REVIEW.value,
            "incorrect": KnowledgeStatus.REJECTED.value,
        }
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT id,stale FROM knowledge_units WHERE id=?", (unit_id,)
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("La unidad pedagógica no existe.")
            if request.action == "approve" and row["stale"]:
                raise LibraryContractError(
                    "Una unidad obsoleta no puede aprobarse sin regeneración."
                )
            if request.action in status_map:
                connection.execute(
                    "UPDATE knowledge_units SET status=?,updated_at=? WHERE id=?",
                    (status_map[request.action], now, unit_id),
                )
            connection.execute(
                "INSERT INTO knowledge_feedback(knowledge_unit_id,action,comment,created_at) "
                "VALUES (?,?,?,?)",
                (unit_id, request.action, request.comment, now),
            )
        return self.get_knowledge(unit_id)

    async def grounded_generate(self, request: GroundedGenerationRequest) -> GroundedGenerationRead:
        model = self.default_model
        if not model:
            raise LibraryProviderUnavailableError("No hay un modelo local configurado.")
        search = await self.search.search(
            request.query,
            mode="hybrid",
            limit=request.max_sources,
            include_solutions=False,
        )
        if not search.results:
            raise LibraryContractError("No hay evidencia local suficiente para responder.")
        chunk_ids = {result.id for result in search.results}
        knowledge = self._relevant_knowledge(request.query, limit=6)
        context = [
            {
                "chunk_id": result.id,
                "source": result.source_name,
                "page_start": result.page_start,
                "page_end": result.page_end,
                "start_seconds": result.start_seconds,
                "end_seconds": result.end_seconds,
                "text": result.text[:2_500],
            }
            for result in search.results
        ]
        system = (PROMPT_ROOT / "library_grounded_v1.md").read_text(encoding="utf-8")
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "query": request.query,
                        "level": request.level,
                        "objective": request.objective,
                        "explanation_language": request.explanation_language,
                        "knowledge": knowledge,
                        "context": context,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            payload = await self.model_provider.structured_generate(
                model, messages, GroundedDraftPayload
            )
        except (
            ProviderUnavailableError,
            ProviderResponseError,
            MalformedStructuredOutputError,
        ) as exc:
            raise LibraryProviderUnavailableError(
                "La generación fundamentada local no está disponible."
            ) from exc
        for claim in payload.claims:
            if not set(claim.source_chunk_ids).issubset(chunk_ids):
                raise LibraryContractError(
                    "La respuesta generada contiene una referencia inexistente."
                )
        if any(result.review_status != "approved" for result in search.results):
            payload = payload.model_copy(
                update={
                    "confidence": min(payload.confidence, 0.75),
                    "warnings": [
                        *payload.warnings,
                        "Las fuentes recuperadas todavía no tienen revisión editorial aprobada.",
                    ],
                }
            )
        draft_id = str(uuid4())
        evidence_sufficient = payload.confidence >= 0.45 and not any(
            "insuficiente" in warning.casefold() for warning in payload.warnings
        )
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO grounded_drafts(id,query,objective,level,explanation_language,"
                "payload_json,source_chunk_ids_json,model,prompt_version,review_status,"
                "evidence_sufficient,created_at) VALUES (?,?,?,?,?,?,?,?,?,'draft',?,?)",
                (
                    draft_id,
                    request.query,
                    request.objective,
                    request.level,
                    request.explanation_language,
                    payload.model_dump_json(),
                    json_dump(sorted(chunk_ids)),
                    model,
                    PROMPT_VERSION_GROUNDED,
                    int(evidence_sufficient),
                    utc_text(),
                ),
            )
        return self.get_grounded_draft(draft_id)

    def get_grounded_draft(self, draft_id: str) -> GroundedGenerationRead:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM grounded_drafts WHERE id=?", (draft_id,)
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("El borrador fundamentado no existe.")
            chunk_ids = json_load(row["source_chunk_ids_json"], [])
            placeholders = ",".join("?" for _ in chunk_ids)
            source_rows = (
                connection.execute(
                    "SELECT c.id chunk_id,s.id source_id,s.name source_name,c.page_start,c.page_end,"
                    "c.start_seconds,c.end_seconds,sv.version_number,s.review_status "
                    "FROM chunks c JOIN source_versions sv ON sv.id=c.source_version_id "
                    "JOIN sources s ON s.id=sv.source_id WHERE c.id IN (" + placeholders + ")",
                    chunk_ids,
                ).fetchall()
                if chunk_ids
                else []
            )
        source_map = {item["chunk_id"]: item for item in source_rows}
        return GroundedGenerationRead(
            id=row["id"],
            query=row["query"],
            objective=row["objective"],
            payload=GroundedDraftPayload.model_validate_json(row["payload_json"]),
            sources=[
                GroundedSource(
                    chunk_id=chunk_id,
                    source_id=source_map[chunk_id]["source_id"],
                    source_name=source_map[chunk_id]["source_name"],
                    page_start=source_map[chunk_id]["page_start"],
                    page_end=source_map[chunk_id]["page_end"],
                    start_seconds=source_map[chunk_id]["start_seconds"],
                    end_seconds=source_map[chunk_id]["end_seconds"],
                    source_version=source_map[chunk_id]["version_number"],
                    review_status=source_map[chunk_id]["review_status"],
                )
                for chunk_id in chunk_ids
                if chunk_id in source_map
            ],
            model=row["model"],
            prompt_version=row["prompt_version"],
            review_status="draft",
            evidence_sufficient=bool(row["evidence_sufficient"]),
            created_at=row["created_at"],
        )

    def _relevant_knowledge(self, query: str, *, limit: int) -> list[dict[str, object]]:
        tokens = [token.casefold() for token in re.findall(r"\w+", query) if len(token) > 2]
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id,title,content_es,status,confidence FROM knowledge_units "
                "WHERE stale=0 AND status IN ('approved','candidate','needs_review') ORDER BY "
                "CASE status WHEN 'approved' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END,"
                "confidence DESC LIMIT 100"
            ).fetchall()
        scored = []
        for row in rows:
            haystack = f"{row['title']} {row['content_es']}".casefold()
            overlap = sum(token in haystack for token in tokens)
            scored.append((overlap, row))
        scored.sort(key=lambda item: (-item[0], -item[1]["confidence"], item[1]["id"]))
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "content_es": row["content_es"][:2_000],
                "status": row["status"],
                "confidence": row["confidence"],
            }
            for _, row in scored[:limit]
        ]

    def _knowledge_read(self, row: sqlite3.Row) -> KnowledgeUnitRead:
        with self.database.connect() as connection:
            citations = connection.execute(
                "SELECT chunk_id,quote FROM knowledge_unit_sources WHERE knowledge_unit_id=? "
                "ORDER BY chunk_id",
                (row["id"],),
            ).fetchall()
        return KnowledgeUnitRead(
            id=row["id"],
            evidence_sufficient=True,
            kind=row["kind"],
            title=row["title"],
            content_es=row["content_es"],
            german_examples=json_load(row["german_examples_json"], []),
            translations=json_load(row["translations_json"], []),
            cefr_level=row["cefr_level"],
            topics=json_load(row["topics_json"], []),
            keywords=json_load(row["keywords_json"], []),
            warnings=json_load(row["warnings_json"], []),
            citations=[
                {"chunk_id": item["chunk_id"], "quote": item["quote"]} for item in citations
            ],
            confidence=row["confidence"],
            status=row["status"],
            model=row["model"],
            prompt_version=row["prompt_version"],
            stale=bool(row["stale"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
