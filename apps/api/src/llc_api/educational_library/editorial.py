from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass

from .database import LibraryDatabase
from .schemas import (
    CoreSourceAssignmentRequest,
    CoreSourceCandidate,
    CoreSourcePairRead,
    EditorialSectionLinkRequest,
    EditorialSectionRead,
    EditorialSectionUpdate,
    EditorialStatus,
    LibraryContractError,
    LibraryNotFoundError,
    MetadataOrigin,
    PedagogicalRole,
    SourceRead,
)
from .service import json_dump, json_load, utc_text

_CORE_ROLES = {
    PedagogicalRole.CORE_THEORY,
    PedagogicalRole.CORE_WORKBOOK,
    PedagogicalRole.CORE_ANSWER_KEY,
}
_THEORY_MARKERS = ("hispanohabl", "gramatica alemana", "gramática alemana")
_WORKBOOK_MARKERS = ("ejercicios", "übungen", "workbook")
_ANSWER_MARKERS = ("soluciones", "lösungen", "answer key", "respuestas")
_TOPIC_HEADING = re.compile(r"\b(?:TEMA|THEMA)\s*(\d{1,3})\b", re.IGNORECASE)


@dataclass(frozen=True)
class _Suggestion:
    role: PedagogicalRole | None
    confidence: float
    evidence: tuple[str, ...]


def _fold(value: str | None) -> str:
    return re.sub(r"[_\-.]+", " ", value or "").casefold()


def _suggest(row: sqlite3.Row) -> _Suggestion:
    haystack = " ".join(
        _fold(row[key])
        for key in ("name", "current_path", "canonical_title", "document_title")
        if key in row.keys()
    )
    evidence: list[str] = []
    if "herder" in haystack:
        evidence.append("El nombre o título identifica la editorial Herder.")
    if any(marker in haystack for marker in _THEORY_MARKERS):
        evidence.append("El título identifica una gramática para hispanohablantes.")
    workbook = any(marker in haystack for marker in _WORKBOOK_MARKERS)
    answers = any(marker in haystack for marker in _ANSWER_MARKERS)
    if workbook:
        evidence.append("El título declara ejercicios.")
    if answers:
        evidence.append("El título declara soluciones.")
    if "herder" not in haystack:
        return _Suggestion(None, 0.0, tuple(evidence))
    if workbook:
        return _Suggestion(PedagogicalRole.CORE_WORKBOOK, 0.98, tuple(evidence))
    if any(marker in haystack for marker in _THEORY_MARKERS):
        return _Suggestion(PedagogicalRole.CORE_THEORY, 0.98, tuple(evidence))
    if answers:
        return _Suggestion(PedagogicalRole.CORE_ANSWER_KEY, 0.85, tuple(evidence))
    return _Suggestion(None, 0.45, tuple(evidence))


class LibraryEditorialService:
    """Auditable editorial metadata; never renames or writes source files."""

    def __init__(self, database: LibraryDatabase):
        self.database = database

    def candidates(self) -> list[CoreSourceCandidate]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT s.*,coalesce(sv.version_number,0) current_version,d.title document_title,"
                "(SELECT count(*) FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
                "WHERE c.source_version_id=s.current_version_id AND e.status='indexed') "
                "semantic_indexed_chunks,"
                "(SELECT count(*) FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
                "WHERE c.source_version_id=s.current_version_id AND e.status='failed') "
                "semantic_failed_chunks "
                "FROM sources s LEFT JOIN source_versions sv ON sv.id=s.current_version_id "
                "LEFT JOIN documents d ON d.source_version_id=sv.id "
                "WHERE s.status='present' AND s.excluded=0 ORDER BY s.name COLLATE NOCASE"
            ).fetchall()
        suggested: list[tuple[sqlite3.Row, _Suggestion]] = []
        for row in rows:
            item = _suggest(row)
            if item.role is not None or "herder" in _fold(row["name"]):
                suggested.append((row, item))
        role_counts: dict[PedagogicalRole, int] = {}
        for _, item in suggested:
            if item.role:
                role_counts[item.role] = role_counts.get(item.role, 0) + 1
        return [
            CoreSourceCandidate(
                source=self._source_read(row),
                suggested_role=item.role,
                confidence=item.confidence,
                evidence=list(item.evidence),
                unambiguous=bool(
                    item.role and item.confidence >= 0.95 and role_counts.get(item.role) == 1
                ),
            )
            for row, item in suggested
        ]

    def core_pair(self) -> CoreSourcePairRead:
        candidates = self.candidates()
        by_role: dict[PedagogicalRole, SourceRead] = {}
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT s.*,coalesce(sv.version_number,0) current_version,"
                "(SELECT count(*) FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
                "WHERE c.source_version_id=s.current_version_id AND e.status='indexed') "
                "semantic_indexed_chunks,"
                "(SELECT count(*) FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
                "WHERE c.source_version_id=s.current_version_id AND e.status='failed') "
                "semantic_failed_chunks FROM sources s "
                "LEFT JOIN source_versions sv ON sv.id=s.current_version_id "
                "WHERE s.user_selected_core=1 AND s.editorial_status='user_confirmed' "
                "ORDER BY s.priority DESC,s.id"
            ).fetchall()
        for row in rows:
            role = PedagogicalRole(row["pedagogical_role"])
            by_role.setdefault(role, self._source_read(row))
        theory = by_role.get(PedagogicalRole.CORE_THEORY)
        workbook = by_role.get(PedagogicalRole.CORE_WORKBOOK)
        return CoreSourcePairRead(
            theory=theory,
            workbook=workbook,
            answer_key=by_role.get(PedagogicalRole.CORE_ANSWER_KEY),
            candidates=candidates,
            ready=bool(theory and workbook),
        )

    def assign_core(self, source_id: str, request: CoreSourceAssignmentRequest) -> SourceRead:
        role = PedagogicalRole(request.pedagogical_role)
        if role not in _CORE_ROLES:
            raise LibraryContractError("El rol solicitado no es un rol nuclear.")
        with self.database.transaction(immediate=True) as connection:
            row = self._source_row(connection, source_id)
            existing = connection.execute(
                "SELECT after_json FROM source_editorial_events "
                "WHERE source_id=? AND operation_id=?",
                (source_id, request.operation_id),
            ).fetchone()
            target = self._assignment_payload(row, request)
            if existing:
                if json_load(existing["after_json"], {}) != target:
                    raise LibraryContractError(
                        "El identificador editorial ya se usó con otro contenido."
                    )
                return self._source_read(self._source_row(connection, source_id))

            previous = connection.execute(
                "SELECT id FROM sources WHERE pedagogical_role=? AND user_selected_core=1 "
                "AND id!=?",
                (role.value, source_id),
            ).fetchall()
            for other in previous:
                connection.execute(
                    "UPDATE sources SET user_selected_core=0,pedagogical_role='supplementary',"
                    "editorial_status='user_confirmed' WHERE id=?",
                    (other["id"],),
                )
            before = self._editable_snapshot(row)
            connection.execute(
                "UPDATE sources SET pedagogical_role=?,user_selected_core=1,priority=?,"
                "editorial_status='user_confirmed',metadata_origin='user',metadata_confidence=1,"
                "display_alias=coalesce(?,display_alias),canonical_title=coalesce(?,canonical_title),"
                "related_source_id=?,editorial_notes=coalesce(?,editorial_notes) WHERE id=?",
                (
                    role.value,
                    max(80, int(row["priority"])),
                    request.display_alias,
                    request.canonical_title,
                    request.related_source_id,
                    request.editorial_notes,
                    source_id,
                ),
            )
            if request.related_source_id:
                related = self._source_row(connection, request.related_source_id)
                if related["id"] == source_id:
                    raise LibraryContractError("Una fuente no puede relacionarse consigo misma.")
                connection.execute(
                    "UPDATE sources SET related_source_id=? WHERE id=?",
                    (source_id, request.related_source_id),
                )
            after_row = self._source_row(connection, source_id)
            after = self._editable_snapshot(after_row)
            connection.execute(
                "INSERT INTO source_editorial_events(source_id,operation_id,action,before_json,"
                "after_json,created_at) VALUES (?,?,?,?,?,?)",
                (
                    source_id,
                    request.operation_id,
                    "assign_core",
                    json_dump(before),
                    json_dump(after),
                    utc_text(),
                ),
            )
        return self.get_source(source_id)

    def get_source(self, source_id: str) -> SourceRead:
        with self.database.connect() as connection:
            row = self._source_row(connection, source_id)
        return self._source_read(row)

    def build_section_index(self, source_id: str) -> list[EditorialSectionRead]:
        with self.database.transaction(immediate=True) as connection:
            source = self._source_row(connection, source_id)
            version_id = source["current_version_id"]
            if not version_id:
                raise LibraryContractError("La fuente todavía no tiene una versión procesada.")
            existing = connection.execute(
                "SELECT count(*) FROM editorial_sections WHERE source_version_id=?",
                (version_id,),
            ).fetchone()[0]
            if not existing:
                rows = connection.execute(
                    "SELECT se.* FROM sections se JOIN documents d ON d.id=se.document_id "
                    "WHERE d.source_version_id=? ORDER BY se.sequence",
                    (version_id,),
                ).fetchall()
                headings: list[tuple[int, int, str, str]] = []
                seen: set[str] = set()
                for row in rows:
                    match = _TOPIC_HEADING.search(row["text"][:1_000])
                    if not match or not row["page_start"]:
                        continue
                    stable_key = f"topic-{int(match.group(1)):03d}"
                    if stable_key in seen:
                        continue
                    seen.add(stable_key)
                    title_match = re.search(
                        r"(?:TEMA|THEMA)\s*\d{1,3}\s+([^\n]{3,160})",
                        row["text"][:700],
                        re.IGNORECASE,
                    )
                    title = (
                        re.sub(r"\s+", " ", title_match.group(1)).strip(" -")
                        if title_match
                        else f"Tema {int(match.group(1))}"
                    )
                    headings.append(
                        (int(row["page_start"]), int(row["sequence"]), stable_key, title)
                    )
                max_page = max((int(row["page_end"] or 0) for row in rows), default=0)
                for index, (page, _, stable_key, title) in enumerate(headings):
                    next_page = (
                        headings[index + 1][0] if index + 1 < len(headings) else max_page + 1
                    )
                    connection.execute(
                        "INSERT INTO editorial_sections(source_version_id,stable_key,title,page_start,"
                        "page_end,content_role,derivation_method,provenance_confidence,"
                        "editorial_status,created_at,updated_at) VALUES (?,?,?,?,?,'theory',"
                        "'heading-regex.v1',0.82,'system_suggested',?,?)",
                        (
                            version_id,
                            stable_key,
                            title,
                            page,
                            max(page, next_page - 1),
                            utc_text(),
                            utc_text(),
                        ),
                    )
        return self.list_sections(source_id)

    def list_sections(self, source_id: str) -> list[EditorialSectionRead]:
        with self.database.connect() as connection:
            source = self._source_row(connection, source_id)
            if not source["current_version_id"]:
                return []
            rows = connection.execute(
                "SELECT * FROM editorial_sections WHERE source_version_id=? ORDER BY page_start,id",
                (source["current_version_id"],),
            ).fetchall()
            links = connection.execute(
                "SELECT source_section_id,target_section_id FROM editorial_section_links "
                "WHERE source_section_id IN (SELECT id FROM editorial_sections "
                "WHERE source_version_id=?)",
                (source["current_version_id"],),
            ).fetchall()
        by_source: dict[int, list[int]] = {}
        for link in links:
            by_source.setdefault(int(link["source_section_id"]), []).append(
                int(link["target_section_id"])
            )
        return [self._section_read(row, by_source.get(int(row["id"]), [])) for row in rows]

    def update_section(
        self, section_id: int, update: EditorialSectionUpdate
    ) -> EditorialSectionRead:
        allowed = {
            "title",
            "page_start",
            "page_end",
            "cefr_min",
            "cefr_max",
            "topic",
            "content_role",
            "editorial_status",
            "notes",
        }
        assignments: list[str] = []
        values: list[object] = []
        for field in update.model_fields_set:
            if field not in allowed:
                continue
            value = getattr(update, field)
            assignments.append(f"{field}=?")
            values.append(value.value if isinstance(value, EditorialStatus) else value)
        with self.database.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT * FROM editorial_sections WHERE id=?", (section_id,)
            ).fetchone()
            if not current:
                raise LibraryNotFoundError("La sección editorial no existe.")
            page_start = update.page_start or current["page_start"]
            page_end = update.page_end or current["page_end"]
            if page_end < page_start:
                raise LibraryContractError("El rango de páginas no es válido.")
            values.extend([utc_text(), section_id])
            connection.execute(
                f"UPDATE editorial_sections SET {','.join(assignments)},updated_at=? WHERE id=?",
                values,
            )
            row = connection.execute(
                "SELECT * FROM editorial_sections WHERE id=?", (section_id,)
            ).fetchone()
        return self._section_read(row, [])

    def link_section(
        self, section_id: int, request: EditorialSectionLinkRequest
    ) -> EditorialSectionRead:
        with self.database.transaction(immediate=True) as connection:
            source = connection.execute(
                "SELECT * FROM editorial_sections WHERE id=?", (section_id,)
            ).fetchone()
            target = connection.execute(
                "SELECT * FROM editorial_sections WHERE id=?", (request.target_section_id,)
            ).fetchone()
            if not source or not target:
                raise LibraryNotFoundError("Una de las secciones editoriales no existe.")
            if section_id == request.target_section_id:
                raise LibraryContractError("Una sección no puede enlazarse consigo misma.")
            connection.execute(
                "INSERT INTO editorial_section_links(source_section_id,target_section_id,relation,"
                "editorial_status,created_at) VALUES (?,?,?,?,?) ON CONFLICT DO UPDATE SET "
                "editorial_status=excluded.editorial_status",
                (
                    section_id,
                    request.target_section_id,
                    request.relation,
                    request.editorial_status.value,
                    utc_text(),
                ),
            )
        return self._section_read(source, [request.target_section_id])

    @staticmethod
    def _source_row(connection: sqlite3.Connection, source_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT s.*,coalesce(sv.version_number,0) current_version,"
            "(SELECT count(*) FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
            "WHERE c.source_version_id=s.current_version_id AND e.status='indexed') "
            "semantic_indexed_chunks,"
            "(SELECT count(*) FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
            "WHERE c.source_version_id=s.current_version_id AND e.status='failed') "
            "semantic_failed_chunks FROM sources s "
            "LEFT JOIN source_versions sv ON sv.id=s.current_version_id WHERE s.id=?",
            (source_id,),
        ).fetchone()
        if not row:
            raise LibraryNotFoundError("La fuente no existe.")
        return row

    @staticmethod
    def _editable_snapshot(row: sqlite3.Row) -> dict[str, object]:
        return {
            key: row[key]
            for key in (
                "canonical_title",
                "display_alias",
                "pedagogical_role",
                "priority",
                "editorial_status",
                "user_selected_core",
                "metadata_origin",
                "metadata_confidence",
                "editorial_notes",
                "related_source_id",
            )
        }

    def _assignment_payload(
        self, row: sqlite3.Row, request: CoreSourceAssignmentRequest
    ) -> dict[str, object]:
        payload = self._editable_snapshot(row)
        payload.update(
            {
                "canonical_title": request.canonical_title or row["canonical_title"],
                "display_alias": request.display_alias or row["display_alias"],
                "pedagogical_role": request.pedagogical_role,
                "priority": max(80, int(row["priority"])),
                "editorial_status": EditorialStatus.USER_CONFIRMED.value,
                "user_selected_core": 1,
                "metadata_origin": MetadataOrigin.USER.value,
                "metadata_confidence": 1.0,
                "editorial_notes": request.editorial_notes or row["editorial_notes"],
                "related_source_id": request.related_source_id,
            }
        )
        return payload

    @staticmethod
    def _source_read(row: sqlite3.Row) -> SourceRead:
        return SourceRead(
            id=row["id"],
            current_path=row["current_path"],
            name=row["name"],
            kind=row["kind"],
            format=row["format"],
            size_bytes=row["size_bytes"],
            content_hash=row["current_hash"],
            language=row["language"],
            cefr_level=row["cefr_level"],
            topics=json_load(row["topics_json"], []),
            provenance=row["provenance"],
            rights=row["rights"],
            priority=row["priority"],
            editorial_confidence=row["editorial_confidence"],
            review_status=row["review_status"],
            status=row["status"],
            processing_state=row["processing_state"],
            duplicate_of_source_id=row["duplicate_of_source_id"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            current_version=row["current_version"],
            canonical_title=row["canonical_title"],
            display_alias=row["display_alias"],
            author=row["author"],
            publisher=row["publisher"],
            edition=row["edition"],
            cefr_min=row["cefr_min"],
            cefr_max=row["cefr_max"],
            pedagogical_role=row["pedagogical_role"],
            source_priority=row["priority"],
            editorial_status=row["editorial_status"],
            user_selected_core=bool(row["user_selected_core"]),
            metadata_origin=row["metadata_origin"],
            metadata_confidence=row["metadata_confidence"],
            editorial_notes=row["editorial_notes"],
            related_source_id=row["related_source_id"],
            semantic_indexed_chunks=row["semantic_indexed_chunks"],
            semantic_failed_chunks=row["semantic_failed_chunks"],
        )

    @staticmethod
    def _section_read(row: sqlite3.Row, related: list[int]) -> EditorialSectionRead:
        return EditorialSectionRead(
            id=row["id"],
            source_version_id=row["source_version_id"],
            stable_key=row["stable_key"],
            title=row["title"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            cefr_min=row["cefr_min"],
            cefr_max=row["cefr_max"],
            topic=row["topic"],
            content_role=row["content_role"],
            derivation_method=row["derivation_method"],
            provenance_confidence=row["provenance_confidence"],
            editorial_status=row["editorial_status"],
            notes=row["notes"],
            related_sections=sorted(set(related)),
        )


def stable_section_key(source_id: str, title: str, page_start: int) -> str:
    payload = f"{source_id}\0{title}\0{page_start}".encode()
    return "section-" + hashlib.sha256(payload).hexdigest()[:16]
