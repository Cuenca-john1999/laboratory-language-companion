from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from .database import LibraryDatabase
from .schemas import (
    ConceptAliasCreate,
    ConceptAliasOrigin,
    ConceptAliasRead,
    ConceptRelationCreate,
    ConceptRelationRead,
    EvidenceLocationCreate,
    EvidenceLocationRead,
    EvidenceRegion,
    LibraryContractError,
    LibraryNotFoundError,
    LocationFeedbackRequest,
    MemoryAuditRead,
    MemoryFeedbackRead,
    MemoryFeedbackRequest,
    MemoryFeedbackVerdict,
    MemoryReviewAction,
    MemoryReviewQueueItem,
    MemoryReviewRequest,
    NormalizedBBox,
    PageMappingRead,
    PageMappingUpdate,
    PedagogicalConceptCreate,
    PedagogicalConceptRead,
    PedagogicalConceptSummary,
    PedagogicalMemoryImportRead,
    PedagogicalMemoryImportRequest,
    PedagogicalMemoryStatus,
    PedagogicalMemorySummary,
    QueryMemoryRead,
)

_SPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\wäöüßáéíóúñ]+", re.UNICODE)
_QUERY_WORD = re.compile(r"[A-Za-zÄÖÜäöüßÁÉÍÓÚáéíóúÑñ][\wÄÖÜäöüßÁÉÍÓÚáéíóúÑñ-]*")
_QUERY_TARGET_STOPWORDS = {
    "cual",
    "das",
    "de",
    "der",
    "die",
    "el",
    "en",
    "es",
    "ist",
    "la",
    "man",
    "por",
    "que",
    "und",
    "was",
    "wie",
}
_PRIVATE_AUDIT_KEYS = {
    "answer_json",
    "content_hash",
    "current_path",
    "evidence_hash",
    "plan_json",
    "prompt",
    "prompt_json",
}
_STATUS_RANK = {
    PedagogicalMemoryStatus.USER_CONFIRMED.value: 0,
    PedagogicalMemoryStatus.SYSTEM_VERIFIED.value: 1,
    PedagogicalMemoryStatus.CANDIDATE.value: 2,
    PedagogicalMemoryStatus.CONFLICT.value: 3,
    PedagogicalMemoryStatus.REJECTED.value: 4,
    PedagogicalMemoryStatus.STALE.value: 5,
}


def utc_text() -> str:
    return datetime.now(UTC).isoformat()


def json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


def public_audit_snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: public_audit_snapshot(item)
            for key, item in value.items()
            if key not in _PRIVATE_AUDIT_KEYS
            and not key.endswith("_hash")
            and not key.endswith("_path")
            and "prompt" not in key
        }
    if isinstance(value, list):
        return [public_audit_snapshot(item) for item in value]
    return value


def normalize_concept(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return _SPACE.sub(" ", _NON_WORD.sub(" ", value)).strip()


def stable_concept_id(language: str, canonical_name: str) -> str:
    normalized = normalize_concept(canonical_name)
    return str(uuid5(NAMESPACE_URL, f"deutschos:pedagogical-concept:{language}:{normalized}"))


def query_target(question: str, plan: dict[str, Any]) -> str:
    explicit = str(plan.get("target_expression") or "").strip()
    if explicit:
        return explicit
    searches = [str(value) for value in plan.get("search_queries", []) if str(value).strip()]
    frequency: Counter[str] = Counter()
    for search in searches:
        frequency.update(set(normalize_concept(search).split()))
    candidates: list[tuple[int, int, str, str]] = []
    for original in _QUERY_WORD.findall(question):
        normalized = normalize_concept(original)
        if (
            len(normalized) >= 3
            and normalized not in _QUERY_TARGET_STOPWORDS
            and frequency[normalized] >= 2
        ):
            candidates.append((frequency[normalized], len(normalized), normalized, original))
    if not candidates:
        return ""
    return sorted(candidates, key=lambda value: (-value[0], -value[1], value[2]))[0][3]


def _evidence_hash(snippet: str, heading: str | None) -> str:
    value = f"{normalize_concept(heading or '')}\n{unicodedata.normalize('NFC', snippet).strip()}"
    return hashlib.sha256(value.encode()).hexdigest()


def _region_label(region: str) -> str | None:
    return {
        "left": "mitad izquierda",
        "right": "mitad derecha",
        "both": "ambas mitades",
        "full": "página completa",
        "custom": "región marcada",
    }.get(region)


def _printed_label(row: sqlite3.Row) -> str | None:
    region = row["region_kind"]
    if region == "left":
        return row["printed_left_label"]
    if region == "right":
        return row["printed_right_label"]
    if region == "both" and row["printed_left_label"] and row["printed_right_label"]:
        return f"{row['printed_left_label']}–{row['printed_right_label']}"
    return row["printed_full_label"] or (
        f"{row['printed_left_label']}–{row['printed_right_label']}"
        if row["printed_left_label"] and row["printed_right_label"]
        else row["printed_left_label"] or row["printed_right_label"]
    )


def format_public_citation(row: sqlite3.Row) -> str:
    parts = [str(row["source_name"])]
    if row["pdf_page_number"]:
        parts.append(f"PDF p. {row['pdf_page_number']}")
        printed = _printed_label(row)
        parts.append(f"libro p. {printed}" if printed else "página impresa sin identificar")
    elif row["chunk_id"]:
        parts.append("ubicación documental")
    region = _region_label(row["region_kind"])
    if region and row["region_kind"] not in {"full", "unknown"}:
        parts.append(region)
    return " · ".join(parts)


class PedagogicalMemoryService:
    """Evidence-backed memory layered over the existing library database."""

    def __init__(self, database: LibraryDatabase):
        self.database = database

    def summary(self) -> PedagogicalMemorySummary:
        with self.database.connect() as connection:
            counts = {
                "concepts": connection.execute(
                    "SELECT count(*) FROM pedagogical_concepts"
                ).fetchone()[0],
                "aliases": connection.execute(
                    "SELECT count(*) FROM pedagogical_concept_aliases"
                ).fetchone()[0],
                "locations": connection.execute(
                    "SELECT count(*) FROM pedagogical_evidence_locations"
                ).fetchone()[0],
                "relations": connection.execute(
                    "SELECT count(*) FROM pedagogical_concept_relations"
                ).fetchone()[0],
            }
            by_status = Counter(
                {
                    row["status"]: int(row["total"])
                    for row in connection.execute(
                        "SELECT status,count(*) total FROM pedagogical_evidence_locations "
                        "GROUP BY status"
                    )
                }
            )
            for row in connection.execute(
                "SELECT editorial_status status,count(*) total FROM pedagogical_concepts "
                "GROUP BY editorial_status"
            ):
                by_status[row["status"]] += int(row["total"])
        return PedagogicalMemorySummary(
            **counts,
            by_status=dict(sorted(by_status.items())),
            pending_review=by_status["candidate"] + by_status["conflict"],
        )

    def create_concept(self, request: PedagogicalConceptCreate) -> PedagogicalConceptRead:
        with self.database.transaction(immediate=True) as connection:
            concept_id, _ = self._create_concept(connection, request)
        return self.get_concept(concept_id)

    def _create_concept(
        self,
        connection: sqlite3.Connection,
        request: PedagogicalConceptCreate,
    ) -> tuple[str, bool]:
        normalized = normalize_concept(request.canonical_name)
        if not normalized:
            raise LibraryContractError("El nombre del concepto no contiene texto útil.")
        existing = connection.execute(
            "SELECT * FROM pedagogical_concepts WHERE normalized_name=? AND language=?",
            (normalized, request.language),
        ).fetchone()
        if existing:
            concept_id = str(existing["id"])
            status = str(existing["editorial_status"])
            can_promote = status not in {"rejected", "conflict"} or (
                request.status == PedagogicalMemoryStatus.USER_CONFIRMED
            )
            if can_promote and _STATUS_RANK[request.status.value] < _STATUS_RANK[status]:
                status = request.status.value
            now = utc_text()
            connection.execute(
                "UPDATE pedagogical_concepts SET display_name_es=coalesce(?,display_name_es),"
                "display_name_de=coalesce(?,display_name_de),category=CASE WHEN category='other' "
                "THEN ? ELSE category END,description=coalesce(?,description),editorial_status=?,"
                "updated_at=? WHERE id=?",
                (
                    request.display_name_es,
                    request.display_name_de,
                    request.category,
                    request.description,
                    status,
                    now,
                    concept_id,
                ),
            )
            for alias, language in (
                (request.display_name_es, "es"),
                (request.display_name_de, "de"),
            ):
                if alias:
                    self._add_alias(
                        connection,
                        concept_id,
                        ConceptAliasCreate(
                            text=alias,
                            language=language,
                            origin=request.origin,
                            status=request.status,
                            confidence=(
                                "high"
                                if request.status == PedagogicalMemoryStatus.USER_CONFIRMED
                                else "moderate"
                            ),
                        ),
                    )
            return concept_id, False
        concept_id = stable_concept_id(request.language, request.canonical_name)
        now = utc_text()
        connection.execute(
            "INSERT INTO pedagogical_concepts(id,canonical_name,normalized_name,language,"
            "display_name_es,display_name_de,category,description,editorial_status,created_at,"
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                concept_id,
                request.canonical_name.strip(),
                normalized,
                request.language,
                request.display_name_es,
                request.display_name_de,
                request.category,
                request.description,
                request.status.value,
                now,
                now,
            ),
        )
        self._add_alias(
            connection,
            concept_id,
            ConceptAliasCreate(
                text=request.canonical_name,
                language=request.language,
                origin=request.origin,
                status=request.status,
                confidence="high"
                if request.status == PedagogicalMemoryStatus.USER_CONFIRMED
                else "moderate",
            ),
        )
        display_aliases = (
            (request.display_name_es, "es"),
            (request.display_name_de, "de"),
        )
        for alias, language in display_aliases:
            if alias and normalize_concept(alias) != normalized:
                self._add_alias(
                    connection,
                    concept_id,
                    ConceptAliasCreate(
                        text=alias,
                        language=language,
                        origin=request.origin,
                        status=request.status,
                        confidence=(
                            "high"
                            if request.status == PedagogicalMemoryStatus.USER_CONFIRMED
                            else "moderate"
                        ),
                    ),
                )
        return concept_id, True

    def add_alias(self, concept_id: str, request: ConceptAliasCreate) -> PedagogicalConceptRead:
        with self.database.transaction(immediate=True) as connection:
            self._concept_row(connection, concept_id)
            self._add_alias(connection, concept_id, request)
        return self.get_concept(concept_id)

    def _add_alias(
        self,
        connection: sqlite3.Connection,
        concept_id: str,
        request: ConceptAliasCreate,
    ) -> bool:
        normalized = normalize_concept(request.text)
        if not normalized:
            raise LibraryContractError("El alias no contiene texto útil.")
        now = utc_text()
        cursor = connection.execute(
            "INSERT OR IGNORE INTO pedagogical_concept_aliases(concept_id,text,language,"
            "normalized_text,origin,status,confidence,user_confirmed,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                concept_id,
                request.text.strip(),
                request.language,
                normalized,
                request.origin.value,
                request.status.value,
                request.confidence,
                int(request.origin == ConceptAliasOrigin.USER_CONFIRMED),
                now,
                now,
            ),
        )
        collision = connection.execute(
            "SELECT DISTINCT concept_id FROM pedagogical_concept_aliases WHERE normalized_text=? "
            "AND language=? AND concept_id!=? AND status NOT IN ('rejected','stale')",
            (normalized, request.language, concept_id),
        ).fetchall()
        if collision:
            ids = [concept_id, *(str(row["concept_id"]) for row in collision)]
            connection.execute(
                f"UPDATE pedagogical_concepts SET editorial_status='conflict',updated_at=? "
                f"WHERE id IN ({','.join('?' for _ in ids)})",
                (now, *ids),
            )
        return bool(cursor.rowcount)

    def add_relation(
        self, concept_id: str, request: ConceptRelationCreate
    ) -> PedagogicalConceptRead:
        with self.database.transaction(immediate=True) as connection:
            self._concept_row(connection, concept_id)
            self._concept_row(connection, request.target_concept_id)
            now = utc_text()
            connection.execute(
                "INSERT INTO pedagogical_concept_relations(source_concept_id,target_concept_id,"
                "relation_type,status,origin,created_at,updated_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(source_concept_id,target_concept_id,relation_type) DO NOTHING",
                (
                    concept_id,
                    request.target_concept_id,
                    request.relation_type.value,
                    request.status.value,
                    request.origin,
                    now,
                    now,
                ),
            )
        return self.get_concept(concept_id)

    def list_concepts(
        self,
        *,
        query: str | None = None,
        status: PedagogicalMemoryStatus | None = None,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PedagogicalConceptSummary]:
        clauses: list[str] = []
        values: list[object] = []
        if query:
            clauses.append(
                "(pc.normalized_name LIKE ? OR EXISTS (SELECT 1 FROM "
                "pedagogical_concept_aliases pca WHERE pca.concept_id=pc.id "
                "AND pca.normalized_text LIKE ?))"
            )
            needle = f"%{normalize_concept(query)}%"
            values.extend([needle, needle])
        if status:
            clauses.append("pc.editorial_status=?")
            values.append(status.value)
        if category:
            clauses.append("pc.category=?")
            values.append(category)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT pc.* FROM pedagogical_concepts pc "
                f"{where} ORDER BY pc.normalized_name,pc.id LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
            return [self._concept_summary(connection, row) for row in rows]

    def get_concept(self, concept_id: str) -> PedagogicalConceptRead:
        with self.database.connect() as connection:
            row = self._concept_row(connection, concept_id)
            base = self._concept_summary(connection, row)
            relations = connection.execute(
                "SELECT pcr.*,pc.canonical_name target_name FROM pedagogical_concept_relations pcr "
                "JOIN pedagogical_concepts pc ON pc.id=pcr.target_concept_id "
                "WHERE pcr.source_concept_id=? ORDER BY pcr.relation_type,pc.normalized_name",
                (concept_id,),
            ).fetchall()
            query_ids = [
                str(item["query_id"])
                for item in connection.execute(
                    "SELECT query_id FROM teacher_query_concepts WHERE concept_id=? "
                    "ORDER BY created_at DESC LIMIT 50",
                    (concept_id,),
                )
            ]
        return PedagogicalConceptRead(
            **base.model_dump(),
            relations=[self._relation_read(item) for item in relations],
            locations=self.list_locations(concept_id=concept_id, limit=200),
            query_ids=query_ids,
        )

    def upsert_page_mapping(
        self, source_version_id: int, request: PageMappingUpdate
    ) -> PageMappingRead:
        after = request.model_dump(mode="json")
        with self.database.transaction(immediate=True) as connection:
            replay = self._operation_replay(connection, request.operation_id, after)
            if replay:
                row = connection.execute(
                    "SELECT * FROM document_page_mappings WHERE id=?",
                    (int(replay["target_id"]),),
                ).fetchone()
                return self._mapping_read(row)
            self._source_version_row(connection, source_version_id)
            before_row = connection.execute(
                "SELECT * FROM document_page_mappings WHERE source_version_id=? "
                "AND pdf_page_number=?",
                (source_version_id, request.pdf_page_number),
            ).fetchone()
            before = dict(before_row) if before_row else {}
            now = utc_text()
            connection.execute(
                "INSERT INTO document_page_mappings(source_version_id,pdf_page_index,"
                "pdf_page_number,scan_layout,printed_left_label,printed_right_label,"
                "printed_full_label,rotation,mapping_status,mapping_origin,reviewed_at,created_at,"
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_version_id,"
                "pdf_page_number) DO UPDATE SET scan_layout=excluded.scan_layout,"
                "printed_left_label=excluded.printed_left_label,"
                "printed_right_label=excluded.printed_right_label,"
                "printed_full_label=excluded.printed_full_label,rotation=excluded.rotation,"
                "mapping_status=excluded.mapping_status,mapping_origin=excluded.mapping_origin,"
                "mapping_version=document_page_mappings.mapping_version+1,"
                "reviewed_at=excluded.reviewed_at,updated_at=excluded.updated_at",
                (
                    source_version_id,
                    request.pdf_page_number - 1,
                    request.pdf_page_number,
                    request.scan_layout.value,
                    request.printed_left_label,
                    request.printed_right_label,
                    request.printed_full_label,
                    request.rotation,
                    request.status.value,
                    request.origin,
                    now if request.status == PedagogicalMemoryStatus.USER_CONFIRMED else None,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM document_page_mappings WHERE source_version_id=? "
                "AND pdf_page_number=?",
                (source_version_id, request.pdf_page_number),
            ).fetchone()
            self._audit(
                connection,
                request.operation_id,
                "user",
                "page_mapping_upserted",
                "page_mapping",
                str(row["id"]),
                before,
                after,
            )
        return self._mapping_read(row)

    def get_page_mapping(self, source_version_id: int, pdf_page_number: int) -> PageMappingRead:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_page_mappings WHERE source_version_id=? "
                "AND pdf_page_number=?",
                (source_version_id, pdf_page_number),
            ).fetchone()
        if not row:
            raise LibraryNotFoundError("El mapa de página no existe.")
        return self._mapping_read(row)

    def create_location(self, request: EvidenceLocationCreate) -> EvidenceLocationRead:
        with self.database.transaction(immediate=True) as connection:
            location_id, _ = self._create_location(connection, request)
        return self.get_location(location_id)

    def _create_location(
        self,
        connection: sqlite3.Connection,
        request: EvidenceLocationCreate,
    ) -> tuple[str, bool]:
        self._concept_row(connection, request.concept_id)
        version = self._source_version_row(connection, request.source_version_id)
        if request.chunk_id is not None:
            chunk = connection.execute(
                "SELECT * FROM chunks WHERE id=? AND source_version_id=?",
                (request.chunk_id, request.source_version_id),
            ).fetchone()
            if not chunk:
                raise LibraryContractError("El fragmento no pertenece a la versión indicada.")
        if request.editorial_section_id is not None:
            section = connection.execute(
                "SELECT id FROM editorial_sections WHERE id=? AND source_version_id=?",
                (request.editorial_section_id, request.source_version_id),
            ).fetchone()
            if not section:
                raise LibraryContractError("La sección no pertenece a la versión indicada.")
        mapping_id = None
        if request.pdf_page_number:
            mapping = connection.execute(
                "SELECT id FROM document_page_mappings WHERE source_version_id=? "
                "AND pdf_page_number=?",
                (request.source_version_id, request.pdf_page_number),
            ).fetchone()
            mapping_id = mapping["id"] if mapping else None
        evidence_hash = _evidence_hash(request.evidence_snippet, request.heading)
        existing = connection.execute(
            "SELECT * FROM pedagogical_evidence_locations WHERE concept_id=? "
            "AND source_version_id=? AND ifnull(chunk_id,-1)=ifnull(?,-1) "
            "AND ifnull(pdf_page_number,-1)=ifnull(?,-1) AND region_kind=? "
            "ORDER BY updated_at DESC LIMIT 1",
            (
                request.concept_id,
                request.source_version_id,
                request.chunk_id,
                request.pdf_page_number,
                request.region.value,
            ),
        ).fetchone()
        if existing:
            status = str(existing["status"])
            can_promote = status not in {"rejected", "conflict"} or (
                request.status == PedagogicalMemoryStatus.USER_CONFIRMED
            )
            if can_promote and _STATUS_RANK[request.status.value] < _STATUS_RANK[status]:
                status = request.status.value
                now = utc_text()
                connection.execute(
                    "UPDATE pedagogical_evidence_locations SET status=?,page_mapping_id=coalesce(?,"
                    "page_mapping_id),heading=coalesce(?,heading),evidence_snippet=?,evidence_hash=?,"
                    "extraction_quality=max(extraction_quality,?),reviewed_at=?,updated_at=? WHERE id=?",
                    (
                        status,
                        mapping_id,
                        request.heading,
                        request.evidence_snippet.strip(),
                        evidence_hash,
                        request.extraction_quality,
                        now if status == "user_confirmed" else existing["reviewed_at"],
                        now,
                        existing["id"],
                    ),
                )
            return str(existing["id"]), False
        location_id = str(uuid4())
        now = utc_text()
        connection.execute(
            "INSERT INTO pedagogical_evidence_locations(id,concept_id,source_id,"
            "source_version_id,chunk_id,editorial_section_id,page_mapping_id,pdf_page_index,"
            "pdf_page_number,region_kind,custom_bbox_json,heading,evidence_snippet,evidence_hash,"
            "extraction_quality,status,origin,reviewed_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                location_id,
                request.concept_id,
                version["source_id"],
                request.source_version_id,
                request.chunk_id,
                request.editorial_section_id,
                mapping_id,
                request.pdf_page_number - 1 if request.pdf_page_number else None,
                request.pdf_page_number,
                request.region.value,
                request.custom_bbox.model_dump_json() if request.custom_bbox else None,
                request.heading,
                request.evidence_snippet.strip(),
                evidence_hash,
                request.extraction_quality,
                request.status.value,
                request.origin,
                now if request.status == PedagogicalMemoryStatus.USER_CONFIRMED else None,
                now,
                now,
            ),
        )
        return location_id, True

    def list_locations(
        self,
        *,
        concept_id: str | None = None,
        status: PedagogicalMemoryStatus | None = None,
        source_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EvidenceLocationRead]:
        clauses: list[str] = []
        values: list[object] = []
        if concept_id:
            clauses.append("pel.concept_id=?")
            values.append(concept_id)
        if status:
            clauses.append("pel.status=?")
            values.append(status.value)
        if source_id:
            clauses.append("pel.source_id=?")
            values.append(source_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                self._location_select()
                + f" {where} ORDER BY CASE pel.status WHEN 'user_confirmed' THEN 0 "
                "WHEN 'system_verified' THEN 1 WHEN 'candidate' THEN 2 WHEN 'conflict' THEN 3 "
                "ELSE 4 END,pel.updated_at DESC LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
        return [self._location_read(row) for row in rows]

    def get_location(self, location_id: str) -> EvidenceLocationRead:
        with self.database.connect() as connection:
            row = connection.execute(
                self._location_select() + " WHERE pel.id=?", (location_id,)
            ).fetchone()
        if not row:
            raise LibraryNotFoundError("La ubicación pedagógica no existe.")
        return self._location_read(row)

    def feedback_response(
        self, query_id: str, request: MemoryFeedbackRequest
    ) -> MemoryFeedbackRead:
        after = {"verdict": request.verdict.value, "comment": request.comment}
        with self.database.transaction(immediate=True) as connection:
            self._teacher_query_row(connection, query_id)
            replay = self._operation_replay(connection, request.operation_id, after)
            if replay:
                return self._feedback_from_audit(connection, replay)
            row = connection.execute(
                "SELECT * FROM teacher_response_feedback WHERE query_id=?", (query_id,)
            ).fetchone()
            before = dict(row) if row else {}
            now = utc_text()
            connection.execute(
                "INSERT INTO teacher_response_feedback(query_id,verdict,comment,updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(query_id) DO UPDATE SET verdict=excluded.verdict,"
                "comment=excluded.comment,updated_at=excluded.updated_at",
                (query_id, request.verdict.value, request.comment, now),
            )
            review_id = self._review(
                connection,
                "response",
                query_id,
                query_id,
                request.verdict.value,
                before,
                after,
                request.comment,
            )
            self._audit(
                connection,
                request.operation_id,
                "user",
                "response_feedback",
                "response",
                query_id,
                before,
                {**after, "review_id": review_id},
                request.comment,
                query_id,
            )
        return MemoryFeedbackRead(
            review_id=review_id,
            target_type="response",
            target_id=query_id,
            verdict=request.verdict.value,
            resulting_status=None,
            created_at=now,
        )

    def feedback_location(
        self,
        query_id: str,
        location_id: str,
        request: LocationFeedbackRequest,
    ) -> MemoryFeedbackRead:
        after_request = request.model_dump(mode="json")
        with self.database.transaction(immediate=True) as connection:
            self._teacher_query_row(connection, query_id)
            row = connection.execute(
                "SELECT * FROM pedagogical_evidence_locations WHERE id=?", (location_id,)
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("La ubicación pedagógica no existe.")
            replay = self._operation_replay(connection, request.operation_id, after_request)
            if replay:
                return self._feedback_from_audit(connection, replay)
            before = dict(row)
            status = row["status"]
            if request.verdict == MemoryFeedbackVerdict.CORRECT:
                status = PedagogicalMemoryStatus.USER_CONFIRMED.value
            elif request.verdict == MemoryFeedbackVerdict.INCORRECT:
                status = PedagogicalMemoryStatus.REJECTED.value
            now = utc_text()
            connection.execute(
                "UPDATE pedagogical_evidence_locations SET status=?,reviewed_at=?,updated_at=? "
                "WHERE id=?",
                (
                    status,
                    now if request.verdict != MemoryFeedbackVerdict.UNKNOWN else row["reviewed_at"],
                    now,
                    location_id,
                ),
            )
            if request.scan_layout is not None and row["pdf_page_number"]:
                self._upsert_mapping_from_feedback(connection, row, request, now)
            if request.suggested_concept_name:
                self._create_concept(
                    connection,
                    PedagogicalConceptCreate(
                        canonical_name=request.suggested_concept_name,
                        language="de",
                        status=PedagogicalMemoryStatus.CANDIDATE,
                        origin=ConceptAliasOrigin.USER_CREATED,
                    ),
                )
            after = {**after_request, "status": status}
            review_id = self._review(
                connection,
                "location",
                location_id,
                query_id,
                request.verdict.value,
                before,
                after,
                request.comment,
            )
            self._audit(
                connection,
                request.operation_id,
                "user",
                "location_feedback",
                "location",
                location_id,
                before,
                {**after, "review_id": review_id},
                request.comment,
                query_id,
            )
        return MemoryFeedbackRead(
            review_id=review_id,
            target_type="location",
            target_id=location_id,
            verdict=request.verdict.value,
            resulting_status=status,
            created_at=now,
        )

    def review_target(
        self,
        target_type: str,
        target_id: str,
        request: MemoryReviewRequest,
    ) -> MemoryFeedbackRead:
        tables = {
            "concept": ("pedagogical_concepts", "editorial_status"),
            "location": ("pedagogical_evidence_locations", "status"),
            "relation": ("pedagogical_concept_relations", "status"),
        }
        if target_type not in tables:
            raise LibraryContractError("El tipo de memoria no admite revisión.")
        table, column = tables[target_type]
        after_request = request.model_dump(mode="json")
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (target_id,)).fetchone()
            if not row:
                raise LibraryNotFoundError("El elemento de memoria no existe.")
            replay = self._operation_replay(connection, request.operation_id, after_request)
            if replay:
                return self._feedback_from_audit(connection, replay)
            before = dict(row)
            status = row[column]
            if request.action == MemoryReviewAction.CONFIRM:
                status = PedagogicalMemoryStatus.USER_CONFIRMED.value
            elif request.action == MemoryReviewAction.REJECT:
                status = PedagogicalMemoryStatus.REJECTED.value
            now = utc_text()
            if request.action not in {MemoryReviewAction.UNKNOWN, MemoryReviewAction.POSTPONE}:
                connection.execute(
                    f"UPDATE {table} SET {column}=?,updated_at=? WHERE id=?",
                    (status, now, target_id),
                )
            after = {**after_request, "status": status}
            review_id = self._review(
                connection,
                target_type,
                target_id,
                None,
                request.action.value,
                before,
                after,
                request.comment,
            )
            self._audit(
                connection,
                request.operation_id,
                "user",
                f"{target_type}_review",
                target_type,
                target_id,
                before,
                {**after, "review_id": review_id},
                request.comment,
            )
        return MemoryFeedbackRead(
            review_id=review_id,
            target_type=target_type,
            target_id=target_id,
            verdict=request.action.value,
            resulting_status=status,
            created_at=now,
        )

    def revert(self, review_id: str, operation_id: str, comment: str | None) -> MemoryFeedbackRead:
        with self.database.transaction(immediate=True) as connection:
            review = connection.execute(
                "SELECT * FROM pedagogical_memory_reviews WHERE id=?", (review_id,)
            ).fetchone()
            if not review:
                raise LibraryNotFoundError("La revisión no existe.")
            replay = self._operation_replay(
                connection, operation_id, {"review_id": review_id, "comment": comment}
            )
            if replay:
                return self._feedback_from_audit(connection, replay)
            before_snapshot = json_load(review["before_json"], {})
            current: dict[str, Any] = {}
            target_type = str(review["target_type"])
            target_id = str(review["target_id"])
            resulting: str | None = None
            if target_type == "response":
                current_row = connection.execute(
                    "SELECT * FROM teacher_response_feedback WHERE query_id=?", (target_id,)
                ).fetchone()
                current = dict(current_row) if current_row else {}
                if before_snapshot:
                    connection.execute(
                        "INSERT INTO teacher_response_feedback(query_id,verdict,comment,updated_at) "
                        "VALUES (?,?,?,?) ON CONFLICT(query_id) DO UPDATE SET "
                        "verdict=excluded.verdict,comment=excluded.comment,updated_at=excluded.updated_at",
                        (
                            target_id,
                            before_snapshot["verdict"],
                            before_snapshot.get("comment"),
                            utc_text(),
                        ),
                    )
                else:
                    connection.execute(
                        "DELETE FROM teacher_response_feedback WHERE query_id=?", (target_id,)
                    )
            else:
                tables = {
                    "concept": ("pedagogical_concepts", "editorial_status"),
                    "location": ("pedagogical_evidence_locations", "status"),
                    "relation": ("pedagogical_concept_relations", "status"),
                }
                if target_type not in tables:
                    raise LibraryContractError("La revisión no se puede revertir automáticamente.")
                table, column = tables[target_type]
                row = connection.execute(
                    f"SELECT * FROM {table} WHERE id=?", (target_id,)
                ).fetchone()
                current = dict(row) if row else {}
                previous = before_snapshot.get(column)
                if previous is not None:
                    connection.execute(
                        f"UPDATE {table} SET {column}=?,updated_at=? WHERE id=?",
                        (previous, utc_text(), target_id),
                    )
                    resulting = str(previous)
            now = utc_text()
            reverted_review = self._review(
                connection,
                target_type,
                target_id,
                review["query_id"],
                "revert",
                current,
                before_snapshot,
                comment,
                reverts_review_id=review_id,
            )
            self._audit(
                connection,
                operation_id,
                "user",
                "review_reverted",
                target_type,
                target_id,
                current,
                {"review_id": review_id, "comment": comment, "reverted_review": reverted_review},
                comment,
                review["query_id"],
            )
        return MemoryFeedbackRead(
            review_id=reverted_review,
            target_type=target_type,
            target_id=target_id,
            verdict="revert",
            resulting_status=resulting,
            created_at=now,
        )

    def audit(
        self, *, target_type: str | None = None, target_id: str | None = None, limit: int = 100
    ) -> list[MemoryAuditRead]:
        clauses: list[str] = []
        values: list[object] = []
        if target_type:
            clauses.append("target_type=?")
            values.append(target_type)
        if target_id:
            clauses.append("target_id=?")
            values.append(target_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM pedagogical_memory_audit {where} ORDER BY id DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [
            MemoryAuditRead(
                id=row["id"],
                operation_id=row["operation_id"],
                actor=row["actor"],
                action=row["action"],
                target_type=row["target_type"],
                target_id=row["target_id"],
                query_id=row["query_id"],
                before=public_audit_snapshot(json_load(row["before_json"], {})),
                after=public_audit_snapshot(json_load(row["after_json"], {})),
                comment=row["comment"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def review_queue(self, limit: int = 30) -> list[MemoryReviewQueueItem]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT 'location' target_type,pel.id target_id,pc.canonical_name title,"
                "coalesce(s.display_alias,s.name) subtitle,pel.status,"
                "CASE WHEN pel.status='conflict' THEN 100 ELSE 50 END + "
                "CASE WHEN s.pedagogical_role LIKE 'core_%' THEN 30 ELSE 0 END + "
                "count(tqlu.query_id) priority,count(tqlu.query_id) used_by_queries,pel.updated_at "
                "FROM pedagogical_evidence_locations pel JOIN pedagogical_concepts pc "
                "ON pc.id=pel.concept_id JOIN sources s ON s.id=pel.source_id "
                "LEFT JOIN teacher_query_location_usage tqlu ON tqlu.location_id=pel.id "
                "WHERE pel.status IN ('candidate','conflict') GROUP BY pel.id "
                "UNION ALL SELECT 'concept',pc.id,pc.canonical_name,pc.category,"
                "pc.editorial_status,CASE WHEN pc.editorial_status='conflict' THEN 90 ELSE 40 END + "
                "count(tqc.query_id),count(tqc.query_id),pc.updated_at FROM pedagogical_concepts pc "
                "LEFT JOIN teacher_query_concepts tqc ON tqc.concept_id=pc.id "
                "WHERE pc.editorial_status IN ('candidate','conflict') GROUP BY pc.id "
                "ORDER BY priority DESC,updated_at DESC,target_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            MemoryReviewQueueItem(
                target_type=row["target_type"],
                target_id=str(row["target_id"]),
                title=row["title"],
                subtitle=row["subtitle"],
                status=row["status"],
                priority=row["priority"],
                used_by_queries=row["used_by_queries"],
            )
            for row in rows
        ]

    def import_existing(
        self, request: PedagogicalMemoryImportRequest
    ) -> PedagogicalMemoryImportRead:
        requested = request.model_dump(mode="json")
        with self.database.transaction(immediate=True) as connection:
            replay = self._operation_replay(connection, request.operation_id, requested)
            if replay:
                payload = json_load(replay["after_json"], {})
                result_fields = PedagogicalMemoryImportRead.model_fields
                return PedagogicalMemoryImportRead(
                    **{
                        key: value
                        for key, value in payload.items()
                        if key in result_fields and key != "idempotent_replay"
                    },
                    idempotent_replay=True,
                )
            before_counts = self._import_table_counts(connection)
            counts = Counter()
            if request.include_sections:
                self._import_sections(connection, counts)
            if request.include_knowledge:
                self._import_knowledge(connection, counts)
            if request.include_teacher_history:
                self._import_teacher_history(connection, counts)
            if request.confirm_herder_akkusativ_pdf_89:
                self._import_confirmed_akkusativ(connection, counts)
            after_counts = self._import_table_counts(connection)
            result = PedagogicalMemoryImportRead(
                concepts_created=after_counts["concepts"] - before_counts["concepts"],
                aliases_created=after_counts["aliases"] - before_counts["aliases"],
                locations_created=after_counts["locations"] - before_counts["locations"],
                query_links_created=after_counts["query_links"] - before_counts["query_links"],
                mappings_created=after_counts["mappings"] - before_counts["mappings"],
                skipped=counts["skipped"],
            )
            self._audit(
                connection,
                request.operation_id,
                "migration",
                "existing_memory_imported",
                "library",
                "schema-v4",
                {},
                {**requested, **result.model_dump(mode="json")},
                "Importación prudente desde secciones, conocimiento e historial existentes.",
            )
        return result

    @staticmethod
    def _import_table_counts(connection: sqlite3.Connection) -> dict[str, int]:
        return {
            "concepts": int(
                connection.execute("SELECT count(*) FROM pedagogical_concepts").fetchone()[0]
            ),
            "aliases": int(
                connection.execute("SELECT count(*) FROM pedagogical_concept_aliases").fetchone()[0]
            ),
            "locations": int(
                connection.execute(
                    "SELECT count(*) FROM pedagogical_evidence_locations"
                ).fetchone()[0]
            ),
            "query_links": int(
                connection.execute("SELECT count(*) FROM teacher_query_concepts").fetchone()[0]
            ),
            "mappings": int(
                connection.execute("SELECT count(*) FROM document_page_mappings").fetchone()[0]
            ),
        }

    def memory_matches(self, text: str) -> dict[str, Any]:
        normalized = f" {normalize_concept(text)} "
        with self.database.connect() as connection:
            alias_rows = connection.execute(
                "SELECT pca.*,pc.canonical_name,pc.editorial_status FROM "
                "pedagogical_concept_aliases pca JOIN pedagogical_concepts pc "
                "ON pc.id=pca.concept_id WHERE pca.status NOT IN ('rejected','stale') "
                "AND pc.editorial_status NOT IN ('rejected','stale') ORDER BY length(pca.normalized_text) DESC"
            ).fetchall()
            concept_ids: list[str] = []
            for row in alias_rows:
                alias = str(row["normalized_text"])
                if (
                    len(alias) >= 3
                    and f" {alias} " in normalized
                    and row["concept_id"] not in concept_ids
                ):
                    concept_ids.append(str(row["concept_id"]))
            if not concept_ids:
                return {"concept_ids": [], "locations": [], "boosts": {}, "rejected_chunks": set()}
            placeholders = ",".join("?" for _ in concept_ids)
            rows = connection.execute(
                self._location_select()
                + f" WHERE pel.concept_id IN ({placeholders}) AND pel.status IN "
                "('user_confirmed','system_verified','candidate','conflict') "
                "AND sv.id=s.current_version_id AND s.status='present' AND s.excluded=0",
                concept_ids,
            ).fetchall()
            rejected = connection.execute(
                f"SELECT chunk_id FROM pedagogical_evidence_locations pel JOIN sources s "
                f"ON s.id=pel.source_id WHERE pel.concept_id IN ({placeholders}) "
                "AND pel.status='rejected' AND pel.source_version_id=s.current_version_id "
                "AND chunk_id IS NOT NULL",
                concept_ids,
            ).fetchall()
        boosts: dict[int, float] = {}
        for row in rows:
            if row["chunk_id"] is None:
                continue
            value = {
                "user_confirmed": 2.0,
                "system_verified": 1.2,
                "candidate": 0.30,
                "conflict": 0.05,
            }[row["status"]]
            boosts[int(row["chunk_id"])] = max(boosts.get(int(row["chunk_id"]), 0), value)
        rows = sorted(rows, key=lambda row: (_STATUS_RANK[row["status"]], row["id"]))
        return {
            "concept_ids": concept_ids,
            "locations": [self._location_read(row) for row in rows],
            "boosts": boosts,
            "rejected_chunks": {int(row["chunk_id"]) for row in rejected},
        }

    def remember_teacher_query(self, query_id: str) -> QueryMemoryRead:
        with self.database.transaction(immediate=True) as connection:
            query = self._teacher_query_row(connection, query_id)
            plan = json_load(query["plan_json"], {})
            self._record_persisted_memory_usage(connection, query_id)
            if query["status"] != "completed":
                return self._query_memory(connection, query_id)
            target = query_target(str(query["question"]), plan)
            search_text = " ".join(
                [str(query["question"]), target, *[str(v) for v in plan.get("search_queries", [])]]
            )
            matched = self._match_concepts(connection, search_text)
            if target and len(normalize_concept(target)) >= 3 and not matched:
                language = str(plan.get("language") or "de")
                if language not in {"de", "es"}:
                    language = "de"
                concept_id, _ = self._create_concept(
                    connection,
                    PedagogicalConceptCreate(
                        canonical_name=target,
                        language=language,
                        display_name_de=target if language == "de" else None,
                        category="other",
                        status=PedagogicalMemoryStatus.CANDIDATE,
                        origin=ConceptAliasOrigin.QUERY_DETECTED,
                    ),
                )
                matched = [concept_id]
            now = utc_text()
            for concept_id in matched:
                connection.execute(
                    "INSERT OR IGNORE INTO teacher_query_concepts(query_id,concept_id,origin,"
                    "is_primary,status,created_at) VALUES (?,?,?,?,'candidate',?)",
                    (
                        query_id,
                        concept_id,
                        "query_detected",
                        int(
                            bool(target)
                            and normalize_concept(target) in normalize_concept(search_text)
                        ),
                        now,
                    ),
                )
            source_rows = connection.execute(
                "SELECT tqs.*,c.title,c.text,c.page_start,coalesce(d.extraction_quality,0) quality "
                "FROM teacher_query_sources tqs JOIN chunks c ON c.id=tqs.chunk_id "
                "LEFT JOIN documents d ON d.source_version_id=tqs.source_version_id "
                "WHERE tqs.query_id=? ORDER BY tqs.sequence",
                (query_id,),
            ).fetchall()
            sequence = 0
            for concept_id in matched[:3]:
                for source in source_rows:
                    existing = connection.execute(
                        "SELECT id FROM pedagogical_evidence_locations WHERE concept_id=? "
                        "AND chunk_id=? AND source_version_id=? AND status NOT IN ('rejected','stale') "
                        "ORDER BY CASE status WHEN 'user_confirmed' THEN 0 "
                        "WHEN 'system_verified' THEN 1 WHEN 'candidate' THEN 2 ELSE 3 END LIMIT 1",
                        (concept_id, source["chunk_id"], source["source_version_id"]),
                    ).fetchone()
                    if existing:
                        location_id = str(existing["id"])
                    else:
                        location_id, _ = self._create_location(
                            connection,
                            EvidenceLocationCreate(
                                concept_id=concept_id,
                                source_version_id=source["source_version_id"],
                                chunk_id=source["chunk_id"],
                                pdf_page_number=source["page_start"],
                                region=EvidenceRegion.UNKNOWN,
                                heading=source["title"],
                                evidence_snippet=source["snippet"][:600],
                                extraction_quality=source["quality"],
                                status=PedagogicalMemoryStatus.CANDIDATE,
                                origin="query_detected",
                            ),
                        )
                    connection.execute(
                        "INSERT OR IGNORE INTO teacher_query_location_usage(query_id,location_id,"
                        "sequence,evidence_role,created_at) VALUES (?,?,?,?,?)",
                        (query_id, location_id, sequence, "cited", now),
                    )
                    sequence += 1
        return self.query_memory(query_id)

    def _record_persisted_memory_usage(self, connection: sqlite3.Connection, query_id: str) -> None:
        rows = connection.execute(
            "SELECT * FROM teacher_query_sources WHERE query_id=? ORDER BY sequence",
            (query_id,),
        ).fetchall()
        now = utc_text()
        for row in rows:
            matched = json_load(row["matched_queries_json"], [])
            if not any(str(value).startswith("memory:") for value in matched):
                continue
            location = connection.execute(
                "SELECT pel.* FROM pedagogical_evidence_locations pel JOIN sources s "
                "ON s.id=pel.source_id WHERE pel.chunk_id=? AND pel.source_version_id=? "
                "AND pel.status IN ('user_confirmed','system_verified','candidate') "
                "AND s.current_version_id=pel.source_version_id ORDER BY CASE pel.status "
                "WHEN 'user_confirmed' THEN 0 WHEN 'system_verified' THEN 1 ELSE 2 END LIMIT 1",
                (row["chunk_id"], row["source_version_id"]),
            ).fetchone()
            if not location:
                continue
            connection.execute(
                "INSERT OR IGNORE INTO teacher_query_concepts(query_id,concept_id,origin,"
                "is_primary,status,created_at) VALUES (?,?,?,1,?,?)",
                (
                    query_id,
                    location["concept_id"],
                    "retrieved_memory",
                    location["status"],
                    now,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO teacher_query_location_usage(query_id,location_id,"
                "sequence,evidence_role,created_at) VALUES (?,?,?,?,?)",
                (query_id, location["id"], row["sequence"], "memory", now),
            )

    def query_memory(self, query_id: str) -> QueryMemoryRead:
        with self.database.connect() as connection:
            return self._query_memory(connection, query_id)

    def _query_memory(self, connection: sqlite3.Connection, query_id: str) -> QueryMemoryRead:
        self._teacher_query_row(connection, query_id)
        concept_rows = connection.execute(
            "SELECT pc.* FROM teacher_query_concepts tqc JOIN pedagogical_concepts pc "
            "ON pc.id=tqc.concept_id WHERE tqc.query_id=? ORDER BY tqc.is_primary DESC,"
            "pc.normalized_name",
            (query_id,),
        ).fetchall()
        location_rows = connection.execute(
            self._location_select()
            + " JOIN teacher_query_location_usage tqlu ON tqlu.location_id=pel.id "
            "WHERE tqlu.query_id=? ORDER BY tqlu.sequence",
            (query_id,),
        ).fetchall()
        feedback = connection.execute(
            "SELECT verdict FROM teacher_response_feedback WHERE query_id=?", (query_id,)
        ).fetchone()
        return QueryMemoryRead(
            query_id=query_id,
            concepts=[self._concept_summary(connection, row) for row in concept_rows],
            locations=[self._location_read(row) for row in location_rows],
            response_feedback=feedback["verdict"] if feedback else None,
            memory_used=any(
                "memory:" in value
                for row in connection.execute(
                    "SELECT matched_queries_json FROM teacher_query_sources WHERE query_id=?",
                    (query_id,),
                )
                for value in json_load(row["matched_queries_json"], [])
            ),
        )

    def mark_version_stale(self, connection: sqlite3.Connection, source_version_id: int) -> None:
        now = utc_text()
        connection.execute(
            "UPDATE pedagogical_evidence_locations SET status='stale',updated_at=? "
            "WHERE source_version_id=? AND status!='rejected'",
            (now, source_version_id),
        )
        connection.execute(
            "UPDATE document_page_mappings SET mapping_status='stale',updated_at=? "
            "WHERE source_version_id=? AND mapping_status!='rejected'",
            (now, source_version_id),
        )

    def _import_sections(self, connection: sqlite3.Connection, counts: Counter[str]) -> None:
        rows = connection.execute(
            "SELECT es.*,sv.source_id,coalesce(d.extraction_quality,0) quality FROM "
            "editorial_sections es JOIN source_versions sv ON sv.id=es.source_version_id "
            "JOIN sources s ON s.current_version_id=sv.id LEFT JOIN documents d "
            "ON d.source_version_id=sv.id WHERE s.pedagogical_role='core_theory' "
            "ORDER BY es.page_start,es.id"
        ).fetchall()
        for row in rows:
            title = str(row["title"]).strip()
            if not 2 <= len(title) <= 200:
                counts["skipped"] += 1
                continue
            concept_id, created = self._create_concept(
                connection,
                PedagogicalConceptCreate(
                    canonical_name=title,
                    language="de",
                    display_name_de=title,
                    category="other",
                    status=PedagogicalMemoryStatus.CANDIDATE,
                    origin=ConceptAliasOrigin.IMPORTED_SECTION,
                ),
            )
            counts["concepts"] += int(created)
            counts["aliases"] += int(created)
            _, location_created = self._create_location(
                connection,
                EvidenceLocationCreate(
                    concept_id=concept_id,
                    source_version_id=row["source_version_id"],
                    editorial_section_id=row["id"],
                    pdf_page_number=row["page_start"],
                    region=EvidenceRegion.UNKNOWN,
                    heading=title,
                    evidence_snippet=title,
                    extraction_quality=row["quality"],
                    status=PedagogicalMemoryStatus.CANDIDATE,
                    origin="imported_section",
                ),
            )
            counts["locations"] += int(location_created)

    def _import_knowledge(self, connection: sqlite3.Connection, counts: Counter[str]) -> None:
        units = connection.execute(
            "SELECT * FROM knowledge_units WHERE stale=0 AND status IN "
            "('approved','candidate','needs_review') ORDER BY created_at,id"
        ).fetchall()
        for unit in units:
            status = PedagogicalMemoryStatus.CANDIDATE
            sources = connection.execute(
                "SELECT kus.*,c.text,c.title,c.page_start,c.source_version_id,"
                "coalesce(d.extraction_quality,0) quality,pq.quality page_quality,s.current_version_id "
                "FROM knowledge_unit_sources kus JOIN chunks c ON c.id=kus.chunk_id "
                "JOIN source_versions sv ON sv.id=kus.source_version_id JOIN sources s "
                "ON s.id=sv.source_id LEFT JOIN documents d ON d.source_version_id=sv.id "
                "LEFT JOIN page_quality pq ON pq.source_version_id=sv.id "
                "AND pq.page_number=c.page_start WHERE kus.knowledge_unit_id=?",
                (unit["id"],),
            ).fetchall()
            verified = bool(sources) and all(
                source["source_version_id"] == source["current_version_id"]
                and source["page_quality"] != "unusable"
                and normalize_concept(source["quote"]) in normalize_concept(source["text"])
                for source in sources
            )
            if unit["status"] == "approved" and verified:
                status = PedagogicalMemoryStatus.SYSTEM_VERIFIED
            concept_id, created = self._create_concept(
                connection,
                PedagogicalConceptCreate(
                    canonical_name=unit["title"],
                    language="de",
                    category="other",
                    status=status,
                    origin=ConceptAliasOrigin.SYSTEM_SUGGESTED,
                ),
            )
            counts["concepts"] += int(created)
            counts["aliases"] += int(created)
            for source in sources:
                _, location_created = self._create_location(
                    connection,
                    EvidenceLocationCreate(
                        concept_id=concept_id,
                        source_version_id=source["source_version_id"],
                        chunk_id=source["chunk_id"],
                        pdf_page_number=source["page_start"],
                        region=EvidenceRegion.UNKNOWN,
                        heading=source["title"],
                        evidence_snippet=source["quote"],
                        extraction_quality=source["quality"],
                        status=status,
                        origin="knowledge_unit",
                    ),
                )
                counts["locations"] += int(location_created)

    def _import_teacher_history(self, connection: sqlite3.Connection, counts: Counter[str]) -> None:
        rows = connection.execute(
            "SELECT id,question,plan_json FROM teacher_queries WHERE status='completed' "
            "ORDER BY created_at,id"
        ).fetchall()
        for query in rows:
            plan = json_load(query["plan_json"], {})
            target = str(plan.get("target_expression") or "").strip()
            if not target or len(normalize_concept(target)) < 3:
                counts["skipped"] += 1
                continue
            concept_id, created = self._create_concept(
                connection,
                PedagogicalConceptCreate(
                    canonical_name=target,
                    language=str(plan.get("language") or "de"),
                    category="other",
                    status=PedagogicalMemoryStatus.CANDIDATE,
                    origin=ConceptAliasOrigin.QUERY_DETECTED,
                ),
            )
            counts["concepts"] += int(created)
            counts["aliases"] += int(created)
            cursor = connection.execute(
                "INSERT OR IGNORE INTO teacher_query_concepts(query_id,concept_id,origin,"
                "is_primary,status,created_at) VALUES (?,?,?,1,'candidate',?)",
                (query["id"], concept_id, "query_detected", utc_text()),
            )
            counts["query_links"] += int(cursor.rowcount > 0)
            source_rows = connection.execute(
                "SELECT tqs.*,c.title,c.page_start,coalesce(d.extraction_quality,0) quality,"
                "s.current_version_id FROM teacher_query_sources tqs JOIN chunks c "
                "ON c.id=tqs.chunk_id JOIN source_versions sv ON sv.id=tqs.source_version_id "
                "JOIN sources s ON s.id=sv.source_id LEFT JOIN documents d "
                "ON d.source_version_id=sv.id WHERE tqs.query_id=? ORDER BY tqs.sequence",
                (query["id"],),
            ).fetchall()
            for sequence, source in enumerate(source_rows):
                location_status = (
                    PedagogicalMemoryStatus.CANDIDATE
                    if source["source_version_id"] == source["current_version_id"]
                    else PedagogicalMemoryStatus.STALE
                )
                location_id, _ = self._create_location(
                    connection,
                    EvidenceLocationCreate(
                        concept_id=concept_id,
                        source_version_id=source["source_version_id"],
                        chunk_id=source["chunk_id"],
                        pdf_page_number=source["page_start"],
                        region=EvidenceRegion.UNKNOWN,
                        heading=source["title"],
                        evidence_snippet=source["snippet"][:600],
                        extraction_quality=source["quality"],
                        status=location_status,
                        origin="teacher_history",
                    ),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO teacher_query_location_usage(query_id,location_id,"
                    "sequence,evidence_role,created_at) VALUES (?,?,?,?,?)",
                    (query["id"], location_id, sequence, "cited", utc_text()),
                )

    def _import_confirmed_akkusativ(
        self, connection: sqlite3.Connection, counts: Counter[str]
    ) -> None:
        source = connection.execute(
            "SELECT s.id source_id,s.current_version_id FROM sources s WHERE "
            "s.pedagogical_role='core_theory' AND s.user_selected_core=1 "
            "AND s.status='present' ORDER BY s.priority DESC,s.id LIMIT 1"
        ).fetchone()
        if not source:
            raise LibraryContractError("No existe un manual Herder teórico confirmado.")
        concept_id, created = self._create_concept(
            connection,
            PedagogicalConceptCreate(
                canonical_name="Akkusativ",
                language="de",
                display_name_es="Acusativo",
                display_name_de="Akkusativ",
                category="grammatical_case",
                description="Caso gramatical acusativo.",
                status=PedagogicalMemoryStatus.USER_CONFIRMED,
                origin=ConceptAliasOrigin.USER_CONFIRMED,
            ),
        )
        counts["concepts"] += int(created)
        counts["aliases"] += int(created)
        for alias in ("acusativo", "caso acusativo", "vierter Fall"):
            counts["aliases"] += int(
                self._add_alias(
                    connection,
                    concept_id,
                    ConceptAliasCreate(
                        text=alias,
                        language="es" if "acusativo" in alias else "de",
                        origin=ConceptAliasOrigin.USER_CONFIRMED,
                        status=PedagogicalMemoryStatus.USER_CONFIRMED,
                        confidence="high",
                    ),
                )
            )
        now = utc_text()
        mapping = connection.execute(
            "SELECT id FROM document_page_mappings WHERE source_version_id=? AND pdf_page_number=89",
            (source["current_version_id"],),
        ).fetchone()
        if not mapping:
            connection.execute(
                "INSERT INTO document_page_mappings(source_version_id,pdf_page_index,"
                "pdf_page_number,scan_layout,rotation,mapping_status,mapping_origin,reviewed_at,"
                "created_at,updated_at) VALUES (?,88,89,'double_page',0,'user_confirmed',"
                "'user_report',?,?,?)",
                (source["current_version_id"], now, now, now),
            )
            counts["mappings"] += 1
        chunk = connection.execute(
            "SELECT c.*,coalesce(d.extraction_quality,0) quality FROM chunks c "
            "LEFT JOIN documents d ON d.source_version_id=c.source_version_id "
            "WHERE c.source_version_id=? AND c.page_start<=89 AND coalesce(c.page_end,c.page_start)>=89 "
            "ORDER BY CASE WHEN lower(c.text) LIKE '%akkusativ%' THEN 0 ELSE 1 END,c.sequence LIMIT 1",
            (source["current_version_id"],),
        ).fetchone()
        snippet = (
            str(chunk["text"])[:600]
            if chunk
            else "Ubicación del concepto confirmada por el usuario en el original PDF."
        )
        _, location_created = self._create_location(
            connection,
            EvidenceLocationCreate(
                concept_id=concept_id,
                source_version_id=source["current_version_id"],
                chunk_id=chunk["id"] if chunk else None,
                pdf_page_number=89,
                region=EvidenceRegion.UNKNOWN,
                heading=chunk["title"] if chunk else "Akkusativ",
                evidence_snippet=snippet,
                extraction_quality=chunk["quality"] if chunk else 0,
                status=PedagogicalMemoryStatus.USER_CONFIRMED,
                origin="user_report",
            ),
        )
        counts["locations"] += int(location_created)

    def _match_concepts(self, connection: sqlite3.Connection, text: str) -> list[str]:
        normalized = f" {normalize_concept(text)} "
        rows = connection.execute(
            "SELECT concept_id,normalized_text FROM pedagogical_concept_aliases "
            "WHERE status NOT IN ('rejected','stale') ORDER BY length(normalized_text) DESC"
        ).fetchall()
        result: list[str] = []
        for row in rows:
            alias = str(row["normalized_text"])
            if len(alias) >= 3 and f" {alias} " in normalized and row["concept_id"] not in result:
                result.append(str(row["concept_id"]))
        return result

    @staticmethod
    def _location_select() -> str:
        return (
            "SELECT pel.*,pc.canonical_name concept_name,sv.version_number,"
            "coalesce(s.display_alias,s.name) source_name,dpm.scan_layout,dpm.printed_left_label,"
            "dpm.printed_right_label,dpm.printed_full_label,dpm.mapping_status "
            "FROM pedagogical_evidence_locations pel JOIN pedagogical_concepts pc "
            "ON pc.id=pel.concept_id JOIN source_versions sv ON sv.id=pel.source_version_id "
            "JOIN sources s ON s.id=pel.source_id LEFT JOIN document_page_mappings dpm "
            "ON dpm.id=pel.page_mapping_id"
        )

    def _location_read(self, row: sqlite3.Row) -> EvidenceLocationRead:
        bbox = json_load(row["custom_bbox_json"], None)
        return EvidenceLocationRead(
            id=row["id"],
            concept_id=row["concept_id"],
            concept_name=row["concept_name"],
            source_id=row["source_id"],
            source_name=row["source_name"],
            source_version=row["version_number"],
            chunk_id=row["chunk_id"],
            pdf_page_number=row["pdf_page_number"],
            printed_page_label=_printed_label(row),
            scan_layout=row["scan_layout"] or "unknown",
            region=row["region_kind"],
            custom_bbox=NormalizedBBox.model_validate(bbox) if bbox else None,
            heading=row["heading"],
            evidence_snippet=row["evidence_snippet"][:600],
            extraction_quality=row["extraction_quality"],
            status=row["status"],
            origin=row["origin"],
            public_citation=format_public_citation(row),
            reviewed_at=row["reviewed_at"],
        )

    def _concept_summary(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> PedagogicalConceptSummary:
        aliases = connection.execute(
            "SELECT * FROM pedagogical_concept_aliases WHERE concept_id=? "
            "ORDER BY user_confirmed DESC,normalized_text,id",
            (row["id"],),
        ).fetchall()
        counts = {
            item["status"]: int(item["total"])
            for item in connection.execute(
                "SELECT status,count(*) total FROM pedagogical_evidence_locations "
                "WHERE concept_id=? GROUP BY status",
                (row["id"],),
            )
        }
        return PedagogicalConceptSummary(
            id=row["id"],
            canonical_name=row["canonical_name"],
            language=row["language"],
            display_name_es=row["display_name_es"],
            display_name_de=row["display_name_de"],
            category=row["category"],
            description=row["description"],
            status=row["editorial_status"],
            aliases=[self._alias_read(item) for item in aliases],
            location_counts=counts,
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _alias_read(row: sqlite3.Row) -> ConceptAliasRead:
        return ConceptAliasRead(
            id=row["id"],
            text=row["text"],
            language=row["language"],
            normalized_text=row["normalized_text"],
            origin=row["origin"],
            status=row["status"],
            confidence=row["confidence"],
            user_confirmed=bool(row["user_confirmed"]),
        )

    @staticmethod
    def _relation_read(row: sqlite3.Row) -> ConceptRelationRead:
        return ConceptRelationRead(
            id=row["id"],
            source_concept_id=row["source_concept_id"],
            target_concept_id=row["target_concept_id"],
            target_name=row["target_name"],
            relation_type=row["relation_type"],
            status=row["status"],
            origin=row["origin"],
        )

    @staticmethod
    def _mapping_read(row: sqlite3.Row) -> PageMappingRead:
        if not row:
            raise LibraryNotFoundError("El mapa de página no existe.")
        return PageMappingRead(
            id=row["id"],
            source_version_id=row["source_version_id"],
            pdf_page_index=row["pdf_page_index"],
            pdf_page_number=row["pdf_page_number"],
            scan_layout=row["scan_layout"],
            printed_left_label=row["printed_left_label"],
            printed_right_label=row["printed_right_label"],
            printed_full_label=row["printed_full_label"],
            rotation=row["rotation"],
            status=row["mapping_status"],
            origin=row["mapping_origin"],
            mapping_version=row["mapping_version"],
            reviewed_at=row["reviewed_at"],
        )

    @staticmethod
    def _concept_row(connection: sqlite3.Connection, concept_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM pedagogical_concepts WHERE id=?", (concept_id,)
        ).fetchone()
        if not row:
            raise LibraryNotFoundError("El concepto pedagógico no existe.")
        return row

    @staticmethod
    def _source_version_row(connection: sqlite3.Connection, version_id: int) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM source_versions WHERE id=?", (version_id,)
        ).fetchone()
        if not row:
            raise LibraryNotFoundError("La versión documental no existe.")
        return row

    @staticmethod
    def _teacher_query_row(connection: sqlite3.Connection, query_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM teacher_queries WHERE id=?", (query_id,)).fetchone()
        if not row:
            raise LibraryNotFoundError("La consulta educativa no existe.")
        return row

    @staticmethod
    def _review(
        connection: sqlite3.Connection,
        target_type: str,
        target_id: str,
        query_id: str | None,
        verdict: str,
        before: dict[str, Any],
        after: dict[str, Any],
        comment: str | None,
        *,
        reverts_review_id: str | None = None,
    ) -> str:
        review_id = str(uuid4())
        connection.execute(
            "INSERT INTO pedagogical_memory_reviews(id,target_type,target_id,query_id,verdict,"
            "actor,comment,before_json,after_json,reverts_review_id,created_at) "
            "VALUES (?,?,?,?,?,'user',?,?,?,?,?)",
            (
                review_id,
                target_type,
                target_id,
                query_id,
                verdict,
                comment,
                json_dump(before),
                json_dump(after),
                reverts_review_id,
                utc_text(),
            ),
        )
        return review_id

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        operation_id: str,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        before: dict[str, Any],
        after: dict[str, Any],
        comment: str | None = None,
        query_id: str | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO pedagogical_memory_audit(operation_id,actor,action,target_type,"
            "target_id,query_id,before_json,after_json,comment,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                operation_id,
                actor,
                action,
                target_type,
                target_id,
                query_id,
                json_dump(before),
                json_dump(after),
                comment,
                utc_text(),
            ),
        )

    @staticmethod
    def _operation_replay(
        connection: sqlite3.Connection, operation_id: str, payload: dict[str, Any]
    ) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT * FROM pedagogical_memory_audit WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if not row:
            return None
        stored = json_load(row["after_json"], {})
        comparable = {key: stored.get(key) for key in payload}
        if comparable != payload:
            raise LibraryContractError(
                "El identificador de operación ya se usó con otro contenido."
            )
        return row

    @staticmethod
    def _feedback_from_audit(
        connection: sqlite3.Connection, audit: sqlite3.Row
    ) -> MemoryFeedbackRead:
        after = json_load(audit["after_json"], {})
        review_id = after.get("review_id")
        if not review_id:
            review = connection.execute(
                "SELECT id FROM pedagogical_memory_reviews WHERE target_type=? AND target_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (audit["target_type"], audit["target_id"]),
            ).fetchone()
            review_id = review["id"] if review else str(uuid4())
        return MemoryFeedbackRead(
            review_id=review_id,
            target_type=audit["target_type"],
            target_id=audit["target_id"],
            verdict=after.get("verdict") or after.get("action") or audit["action"],
            resulting_status=after.get("status"),
            created_at=audit["created_at"],
        )

    @staticmethod
    def _upsert_mapping_from_feedback(
        connection: sqlite3.Connection,
        location: sqlite3.Row,
        request: LocationFeedbackRequest,
        now: str,
    ) -> None:
        mapping = connection.execute(
            "SELECT * FROM document_page_mappings WHERE source_version_id=? AND pdf_page_number=?",
            (location["source_version_id"], location["pdf_page_number"]),
        ).fetchone()
        status = (
            "user_confirmed" if request.verdict == MemoryFeedbackVerdict.CORRECT else "candidate"
        )
        connection.execute(
            "INSERT INTO document_page_mappings(source_version_id,pdf_page_index,pdf_page_number,"
            "scan_layout,printed_left_label,printed_right_label,printed_full_label,rotation,"
            "mapping_status,mapping_origin,reviewed_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,0,?,'user_feedback',?,?,?) ON CONFLICT(source_version_id,"
            "pdf_page_number) DO UPDATE SET scan_layout=excluded.scan_layout,"
            "printed_left_label=coalesce(excluded.printed_left_label,document_page_mappings.printed_left_label),"
            "printed_right_label=coalesce(excluded.printed_right_label,document_page_mappings.printed_right_label),"
            "printed_full_label=coalesce(excluded.printed_full_label,document_page_mappings.printed_full_label),"
            "mapping_status=excluded.mapping_status,mapping_origin='user_feedback',"
            "mapping_version=document_page_mappings.mapping_version+1,reviewed_at=excluded.reviewed_at,"
            "updated_at=excluded.updated_at",
            (
                location["source_version_id"],
                location["pdf_page_number"] - 1,
                location["pdf_page_number"],
                request.scan_layout.value,
                request.printed_left_label,
                request.printed_right_label,
                request.printed_full_label,
                status,
                now if status == "user_confirmed" else mapping["reviewed_at"] if mapping else None,
                now,
                now,
            ),
        )
        fresh = connection.execute(
            "SELECT id FROM document_page_mappings WHERE source_version_id=? AND pdf_page_number=?",
            (location["source_version_id"], location["pdf_page_number"]),
        ).fetchone()
        connection.execute(
            "UPDATE pedagogical_evidence_locations SET page_mapping_id=?,region_kind=coalesce(?,"
            "region_kind),updated_at=? WHERE id=?",
            (
                fresh["id"],
                request.region.value if request.region else None,
                now,
                location["id"],
            ),
        )
