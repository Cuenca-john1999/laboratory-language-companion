from __future__ import annotations

import hashlib
import re
import sqlite3
import subprocess
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

from .database import LibraryDatabase
from .schemas import (
    CanonicalLegacyMappingRead,
    CanonicalOutlineNodeRead,
    CanonicalRouteAuditRead,
    CanonicalRouteImportRead,
    CanonicalRouteRevertRequest,
    CanonicalRouteStatusRead,
    CanonicalRouteValidationRead,
    CanonicalTopicRead,
    CanonicalTopicReviewRequest,
    LibraryContractError,
    LibraryNotFoundError,
    ReferenceIndexPage,
)
from .service import json_dump, json_load, utc_text

ROUTE_PARSER_VERSION = "herder-reference-index.v1"
ROUTE_ORIGIN = "user_reference_index"
_THEME = re.compile(r"\btema\s*(\d{1,2})\b", re.IGNORECASE)
_LEGACY_THEME_KEY = re.compile(r"^topic-(\d{3})$")
_OPERATION_ID = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
_PAGE = re.compile(r"^\s*(\d{1,4})\s*$")
_ANCHORS = {1: 27, 9: 80, 22: 161, 28: 214, 40: 319, 51: 369}
_EDITABLE_TOPIC_FIELDS = (
    "title_es",
    "title_de",
    "printed_start",
    "printed_end",
    "manual_pdf_start",
    "manual_pdf_end",
    "manual_scan_layout",
    "manual_region",
    "editorial_status",
)


def _fold(value: str | None) -> str:
    text = "".join(
        character
        for character in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def _page_number(label: str | None) -> int | None:
    match = _PAGE.fullmatch(label or "")
    return int(match.group(1)) if match else None


def _clean_theme_title(value: str | None, theme_number: int) -> str | None:
    if not value:
        return None
    cleaned = re.sub(
        rf"^\s*tema\s*{theme_number}\s*[.:\-]?\s*",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned or None


class CanonicalRouteService:
    """Versioned editorial route derived from a user-supplied reference index."""

    def __init__(self, database: LibraryDatabase):
        self.database = database

    @staticmethod
    def inspect_reference(path: Path) -> tuple[str, int, int]:
        if not path.is_file():
            raise LibraryNotFoundError("El PDF de índice de referencia no existe.")
        size = path.stat().st_size
        if size <= 0 or size > 100 * 1024 * 1024:
            raise LibraryContractError("El PDF de índice tiene un tamaño no permitido.")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            result = subprocess.run(
                ["pdfinfo", str(path)],
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LibraryContractError("No se pudo inspeccionar el PDF de referencia.") from exc
        match = re.search(r"^Pages:\s+(\d+)$", result.stdout, re.MULTILINE)
        if result.returncode != 0 or not match:
            raise LibraryContractError("El archivo de referencia no es un PDF legible.")
        return digest, size, int(match.group(1))

    @staticmethod
    def validate_pages(pages: list[ReferenceIndexPage]) -> CanonicalRouteValidationRead:
        physical = [
            entry
            for page in sorted(pages, key=lambda item: item.reference_pdf_page)
            for entry in page.entries
            if entry.hierarchy_level == "top_level_theme" and entry.theme_number is not None
        ]
        numbers = [entry.theme_number for entry in physical if entry.theme_number is not None]
        counts = Counter(numbers)
        duplicates = sorted(number for number, count in counts.items() if count > 1)
        missing = sorted(set(range(1, 52)) - set(numbers))
        by_number = {
            entry.theme_number: entry for entry in physical if counts[entry.theme_number] == 1
        }
        ordered = [by_number[number] for number in sorted(by_number)]
        starts = [_page_number(entry.printed_page_label) for entry in ordered]
        pages_valid = all(page is not None for page in starts)
        monotonic_pages = pages_valid and starts == sorted(starts)
        physical_sequence = numbers == sorted(numbers)
        checks = {
            f"theme_{number}_page_{page}": bool(
                number in by_number and _page_number(by_number[number].printed_page_label) == page
            )
            for number, page in _ANCHORS.items()
        }
        accusative = any(
            (
                entry.theme_number == 22
                or 22
                in [
                    int(match.group(1))
                    for part in entry.parent_path
                    if (match := _THEME.search(part))
                ]
            )
            and ("acusativo" in _fold(entry.title_es) or "akkusativ" in _fold(entry.title_de))
            and _page_number(entry.printed_page_label) == 162
            for page in pages
            for entry in page.entries
        )
        checks["theme_22_accusative_page_162"] = accusative
        theme_20_controls = {
            "theme_20_neuter_page_149": ("son neutros", 149),
            "theme_20_feminine_page_151": ("son femeninos", 151),
            "theme_20_other_observations_page_153": ("otras observaciones generales", 153),
        }
        for name, (title, printed_page) in theme_20_controls.items():
            checks[name] = any(
                entry.theme_number == 20
                and title in _fold(entry.title_es)
                and _page_number(entry.printed_page_label) == printed_page
                for page in pages
                for entry in page.entries
            )
        errors: list[str] = []
        warnings: list[str] = []
        if missing:
            errors.append(f"Faltan temas: {', '.join(map(str, missing))}.")
        if duplicates:
            errors.append(f"Temas duplicados: {', '.join(map(str, duplicates))}.")
        if not pages_valid:
            errors.append("Algún tema no tiene una página impresa entera y legible.")
        if pages_valid and not monotonic_pages:
            errors.append("Las páginas impresas iniciales no son crecientes.")
        failed_checks = [name for name, passed in checks.items() if not passed]
        if failed_checks:
            errors.append("Fallan checks canónicos: " + ", ".join(failed_checks) + ".")
        unreadable = [
            entry.theme_number
            for entry in physical
            if not _clean_theme_title(entry.title_es, entry.theme_number or 0)
        ]
        if unreadable:
            errors.append("Hay títulos principales ilegibles.")
        if not physical_sequence and not duplicates:
            warnings.append(
                "Las fotos no siguen el orden editorial; la ruta se reconstruirá por Tema y página."
            )
        if not any(
            entry.hierarchy_level == "front_matter" for page in pages for entry in page.entries
        ):
            warnings.append("No se identificó front matter en la extracción.")
        if not any(
            entry.hierarchy_level == "back_matter" for page in pages for entry in page.entries
        ):
            warnings.append("No se identificó back matter en la extracción.")
        if any(entry.parse_status != "verified" for page in pages for entry in page.entries):
            warnings.append("La extracción conserva entradas inciertas o recortadas para revisión.")
        valid = not errors and len(numbers) == 51
        return CanonicalRouteValidationRead(
            valid=valid,
            theme_count=len(numbers),
            missing_theme_numbers=missing,
            duplicate_theme_numbers=duplicates,
            sequence_monotonic=physical_sequence,
            printed_pages_monotonic=bool(monotonic_pages),
            canonical_checks=checks,
            errors=errors,
            warnings=warnings,
        )

    def import_reference(
        self,
        reference_path: Path,
        pages: list[ReferenceIndexPage],
        *,
        model: str,
        parser_version: str = ROUTE_PARSER_VERSION,
        source_id: str | None = None,
        visual_seconds: float | None = None,
    ) -> CanonicalRouteImportRead:
        digest, size, page_count = self.inspect_reference(reference_path)
        if len(pages) != page_count:
            raise LibraryContractError(
                "La extracción no contiene exactamente una salida por página de referencia."
            )
        page_numbers = sorted(page.reference_pdf_page for page in pages)
        if page_numbers != list(range(1, page_count + 1)):
            raise LibraryContractError("Las páginas extraídas no forman una secuencia completa.")
        validation = self.validate_pages(pages)
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            source = self._source(connection, source_id)
            existing = connection.execute(
                "SELECT * FROM canonical_route_imports WHERE source_version_id=? "
                "AND reference_sha256=?",
                (source["current_version_id"], digest),
            ).fetchone()
            if existing:
                return self._import_read(existing, idempotent_replay=True)
            route_version = int(
                connection.execute(
                    "SELECT coalesce(max(route_version),0)+1 FROM canonical_route_imports "
                    "WHERE source_version_id=?",
                    (source["current_version_id"],),
                ).fetchone()[0]
            )
            import_id = str(uuid4())
            status = "validated" if validation.valid else "candidate"
            editorial_status = "system_verified" if validation.valid else "candidate"
            statistics: dict[str, object] = {
                "reference_pages": page_count,
                "themes": validation.theme_count,
                "outline_nodes": 0,
                "visual_seconds": visual_seconds,
            }
            connection.execute(
                "INSERT INTO canonical_route_imports(id,source_id,source_version_id,"
                "route_version,reference_name,reference_sha256,reference_size_bytes,"
                "reference_page_count,model,parser_version,status,editorial_status,origin,active,"
                "validation_json,warnings_json,statistics_json,imported_at,validated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    source["id"],
                    source["current_version_id"],
                    route_version,
                    reference_path.name,
                    digest,
                    size,
                    page_count,
                    model,
                    parser_version,
                    status,
                    editorial_status,
                    ROUTE_ORIGIN,
                    0,
                    json_dump(validation.model_dump(mode="json")),
                    json_dump(validation.warnings),
                    json_dump(statistics),
                    now,
                    now if validation.valid else None,
                ),
            )
            for page in pages:
                payload = page.model_dump(mode="json")
                encoded = json_dump(payload)
                connection.execute(
                    "INSERT INTO canonical_route_pages(import_id,reference_pdf_page,"
                    "physical_index_page,extraction_json,extraction_hash,parse_status,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        import_id,
                        page.reference_pdf_page,
                        page.physical_index_page,
                        encoded,
                        hashlib.sha256(encoded.encode()).hexdigest(),
                        self._page_status(page),
                        now,
                    ),
                )
                confidence = min((entry.confidence for entry in page.entries), default=0.0)
                connection.execute(
                    "INSERT INTO canonical_route_visual_variants(import_id,source_version_id,"
                    "reference_pdf_page,region,model,prompt_version,output_json,confidence,"
                    "activated,review_state,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        import_id,
                        source["current_version_id"],
                        page.reference_pdf_page,
                        "full",
                        model,
                        parser_version,
                        encoded,
                        confidence,
                        int(validation.valid),
                        "system_verified" if validation.valid else "candidate",
                        now,
                    ),
                )
            outline_count = self._store_route(
                connection, import_id, source, pages, editorial_status, now
            )
            self._apply_verified_akkusativ_anchor(
                connection, import_id, int(source["current_version_id"]), now
            )
            mapping_counts = self._map_legacy(connection, import_id, source, now)
            statistics.update({"outline_nodes": outline_count, "legacy_mappings": mapping_counts})
            if validation.valid:
                connection.execute(
                    "UPDATE canonical_route_imports SET active=0,status='superseded',"
                    "superseded_at=? WHERE source_version_id=? AND active=1",
                    (now, source["current_version_id"]),
                )
                connection.execute(
                    "UPDATE canonical_route_imports SET active=1,statistics_json=? WHERE id=?",
                    (json_dump(statistics), import_id),
                )
            else:
                connection.execute(
                    "UPDATE canonical_route_imports SET statistics_json=? WHERE id=?",
                    (json_dump(statistics), import_id),
                )
            row = connection.execute(
                "SELECT * FROM canonical_route_imports WHERE id=?", (import_id,)
            ).fetchone()
        return self._import_read(row)

    def status(self) -> CanonicalRouteStatusRead:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM canonical_route_imports WHERE active=1 ORDER BY imported_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return CanonicalRouteStatusRead(
                    available=False,
                    active_import=None,
                    topic_count=0,
                    outline_count=0,
                    mapping_counts={},
                    pending_reviews=0,
                )
            topic_count = int(
                connection.execute(
                    "SELECT count(*) FROM canonical_topics WHERE import_id=?", (row["id"],)
                ).fetchone()[0]
            )
            outline_count = int(
                connection.execute(
                    "SELECT count(*) FROM canonical_outline_nodes n JOIN canonical_topics t "
                    "ON t.id=n.canonical_topic_id WHERE t.import_id=?",
                    (row["id"],),
                ).fetchone()[0]
            )
            mapping_counts = {
                item["mapping_status"]: int(item["count"])
                for item in connection.execute(
                    "SELECT mapping_status,count(*) count FROM canonical_legacy_mappings "
                    "WHERE import_id=? GROUP BY mapping_status",
                    (row["id"],),
                )
            }
            pending = int(
                connection.execute(
                    "SELECT count(*) FROM canonical_topics WHERE import_id=? "
                    "AND editorial_status IN ('candidate','conflict')",
                    (row["id"],),
                ).fetchone()[0]
            )
        return CanonicalRouteStatusRead(
            available=True,
            active_import=self._import_read(row),
            topic_count=topic_count,
            outline_count=outline_count,
            mapping_counts=mapping_counts,
            pending_reviews=pending,
        )

    def list_topics(self, query: str | None = None) -> list[CanonicalTopicRead]:
        with self.database.connect() as connection:
            active = self._active_import(connection)
            rows = connection.execute(
                "SELECT * FROM canonical_topics WHERE import_id=? ORDER BY theme_number",
                (active["id"],),
            ).fetchall()
            topics = [self._topic_read(connection, row) for row in rows]
            search_needles = self._search_needles(connection, query) if query else set()
        if not query:
            return topics
        needle = _fold(query)
        page_match = re.search(r"\b(?:pagina|página|page|p)\s*\.?\s*(\d+)\b", needle)
        theme_match = re.search(r"\btema\s*(\d+)\b", needle)
        return [
            topic
            for topic in topics
            if any(
                candidate in _fold(f"{topic.title_es} {topic.title_de or ''}")
                for candidate in search_needles
            )
            or (theme_match and topic.theme_number == int(theme_match.group(1)))
            or (
                page_match
                and topic.printed_start
                <= int(page_match.group(1))
                <= (topic.printed_end or topic.printed_start)
            )
            or any(
                any(
                    candidate in _fold(f"{node.title_es or ''} {node.title_de or ''}")
                    for candidate in search_needles
                )
                for node in topic.outline
            )
        ]

    @staticmethod
    def _search_needles(connection: sqlite3.Connection, query: str) -> set[str]:
        needle = _fold(query)
        values = {needle}
        rows = connection.execute(
            "SELECT c.id,c.canonical_name,c.display_name_es,c.display_name_de "
            "FROM pedagogical_concepts c WHERE c.editorial_status "
            "IN ('system_verified','user_confirmed') AND (c.normalized_name=? OR EXISTS ("
            "SELECT 1 FROM pedagogical_concept_aliases a WHERE a.concept_id=c.id "
            "AND a.normalized_text=? AND a.status NOT IN ('rejected','stale')))",
            (needle, needle),
        ).fetchall()
        for row in rows:
            values.update(
                _fold(row[field])
                for field in ("canonical_name", "display_name_es", "display_name_de")
                if row[field]
            )
            values.update(
                _fold(alias["text"])
                for alias in connection.execute(
                    "SELECT text FROM pedagogical_concept_aliases WHERE concept_id=? "
                    "AND status NOT IN ('rejected','stale')",
                    (row["id"],),
                )
            )
        return {value for value in values if value}

    def topic(self, theme_number: int) -> CanonicalTopicRead:
        with self.database.connect() as connection:
            active = self._active_import(connection)
            row = connection.execute(
                "SELECT * FROM canonical_topics WHERE import_id=? AND theme_number=?",
                (active["id"], theme_number),
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("El tema canónico no existe.")
            return self._topic_read(connection, row)

    def mappings(self, status: str | None = None) -> list[CanonicalLegacyMappingRead]:
        with self.database.connect() as connection:
            active = self._active_import(connection)
            query = (
                "SELECT m.*,e.stable_key legacy_stable_key,e.title legacy_title,"
                "t.theme_number FROM canonical_legacy_mappings m "
                "JOIN editorial_sections e ON e.id=m.legacy_section_id "
                "LEFT JOIN canonical_topics t ON t.id=m.canonical_topic_id WHERE m.import_id=?"
            )
            params: list[object] = [active["id"]]
            if status:
                query += " AND m.mapping_status=?"
                params.append(status)
            query += " ORDER BY e.page_start,e.id"
            rows = connection.execute(query, params).fetchall()
        return [self._mapping_read(row) for row in rows]

    def reconcile_legacy(self, operation_id: str) -> list[CanonicalLegacyMappingRead]:
        """Rebuild derived mappings without changing or deleting legacy sections."""
        if not _OPERATION_ID.fullmatch(operation_id):
            raise LibraryContractError("El identificador de reconciliación no es válido.")
        with self.database.transaction(immediate=True) as connection:
            active = self._active_import(connection)
            existing = connection.execute(
                "SELECT action,target_type FROM canonical_route_audits WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if existing:
                if existing["action"] != "reconcile" or existing["target_type"] != "import":
                    raise LibraryContractError(
                        "El identificador editorial ya se usó para otra operación."
                    )
                return self._mappings_read(connection, active["id"])
            before = self._mapping_snapshots(connection, active["id"])
            source = connection.execute(
                "SELECT * FROM sources WHERE id=?", (active["source_id"],)
            ).fetchone()
            connection.execute(
                "DELETE FROM canonical_legacy_mappings WHERE import_id=?", (active["id"],)
            )
            counts = self._map_legacy(connection, active["id"], source, utc_text())
            statistics = json_load(active["statistics_json"], {})
            statistics["legacy_mappings"] = counts
            connection.execute(
                "UPDATE canonical_route_imports SET statistics_json=? WHERE id=?",
                (json_dump(statistics), active["id"]),
            )
            after = self._mapping_snapshots(connection, active["id"])
            self._audit(
                connection,
                active["id"],
                operation_id,
                "reconcile",
                "import",
                active["id"],
                {"mappings": before},
                {"mappings": after},
                "Reconciliación derivada; las 53 secciones legacy permanecen intactas.",
            )
            return self._mappings_read(connection, active["id"])

    def resolve_legacy_exact(self, legacy_section_id: int | None) -> CanonicalTopicRead | None:
        if legacy_section_id is None:
            return None
        with self.database.connect() as connection:
            active = connection.execute(
                "SELECT id FROM canonical_route_imports WHERE active=1 ORDER BY imported_at DESC LIMIT 1"
            ).fetchone()
            if not active:
                return None
            row = connection.execute(
                "SELECT t.* FROM canonical_legacy_mappings m JOIN canonical_topics t "
                "ON t.id=m.canonical_topic_id WHERE m.import_id=? AND m.legacy_section_id=? "
                "AND m.mapping_status='exact'",
                (active["id"], legacy_section_id),
            ).fetchone()
            return self._topic_read(connection, row) if row else None

    def legacy_exact_aliases(self) -> dict[str, str]:
        """Return only reviewed-safe aliases used to surface existing personal data."""
        with self.database.connect() as connection:
            active = connection.execute(
                "SELECT id FROM canonical_route_imports WHERE active=1 "
                "ORDER BY imported_at DESC LIMIT 1"
            ).fetchone()
            if not active:
                return {}
            rows = connection.execute(
                "SELECT e.stable_key legacy_stable_key,t.stable_key canonical_stable_key "
                "FROM canonical_legacy_mappings m JOIN editorial_sections e "
                "ON e.id=m.legacy_section_id JOIN canonical_topics t "
                "ON t.id=m.canonical_topic_id WHERE m.import_id=? "
                "AND m.mapping_status='exact'",
                (active["id"],),
            ).fetchall()
        return {row["legacy_stable_key"]: row["canonical_stable_key"] for row in rows}

    def review_topic(
        self, theme_number: int, request: CanonicalTopicReviewRequest
    ) -> CanonicalTopicRead:
        with self.database.transaction(immediate=True) as connection:
            active = self._active_import(connection)
            row = connection.execute(
                "SELECT * FROM canonical_topics WHERE import_id=? AND theme_number=?",
                (active["id"], theme_number),
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("El tema canónico no existe.")
            existing = connection.execute(
                "SELECT * FROM canonical_route_audits WHERE operation_id=?",
                (request.operation_id,),
            ).fetchone()
            before = self._topic_snapshot(row)
            if existing:
                recorded_after = json_load(existing["after_json"], {})
                if (
                    existing["target_type"] != "topic"
                    or existing["target_id"] != str(row["id"])
                    or recorded_after != before
                    or not self._review_request_matches(existing, request, recorded_after)
                ):
                    raise LibraryContractError(
                        "El identificador editorial ya se usó con otro contenido."
                    )
                return self._topic_read(connection, row)
            changes: dict[str, object] = {}
            if request.action == "confirm":
                changes["editorial_status"] = "user_confirmed"
            elif request.action == "incorrect":
                changes["editorial_status"] = "rejected"
            elif request.action == "unknown":
                changes["editorial_status"] = "candidate"
            else:
                for field in _EDITABLE_TOPIC_FIELDS:
                    value = getattr(request, field, None)
                    if value is not None:
                        changes[field] = value
                changes["editorial_status"] = "user_confirmed"
            changes["updated_at"] = utc_text()
            assignments = ",".join(f"{key}=?" for key in changes)
            connection.execute(
                f"UPDATE canonical_topics SET {assignments} WHERE id=?",  # noqa: S608
                (*changes.values(), row["id"]),
            )
            updated = connection.execute(
                "SELECT * FROM canonical_topics WHERE id=?", (row["id"],)
            ).fetchone()
            after = self._topic_snapshot(updated)
            self._audit(
                connection,
                active["id"],
                request.operation_id,
                request.action,
                "topic",
                str(row["id"]),
                before,
                after,
                request.comment,
            )
            return self._topic_read(connection, updated)

    def revert(self, request: CanonicalRouteRevertRequest) -> CanonicalRouteAuditRead:
        with self.database.transaction(immediate=True) as connection:
            active = self._active_import(connection)
            existing = connection.execute(
                "SELECT * FROM canonical_route_audits WHERE operation_id=?",
                (request.operation_id,),
            ).fetchone()
            if existing:
                if (
                    existing["action"] != "revert"
                    or existing["reverts_audit_id"] != request.audit_id
                    or existing["comment"] != request.comment
                ):
                    raise LibraryContractError(
                        "El identificador editorial ya se usó con otro contenido."
                    )
                return self._audit_read(existing)
            original = connection.execute(
                "SELECT * FROM canonical_route_audits WHERE id=? AND import_id=?",
                (request.audit_id, active["id"]),
            ).fetchone()
            if not original or original["target_type"] != "topic":
                raise LibraryNotFoundError("La revisión canónica no existe o no es reversible.")
            target_id = int(original["target_id"])
            row = connection.execute(
                "SELECT * FROM canonical_topics WHERE id=?", (target_id,)
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("El tema revisado ya no existe.")
            before = self._topic_snapshot(row)
            desired = json_load(original["before_json"], {})
            values = [desired[field] for field in _EDITABLE_TOPIC_FIELDS]
            connection.execute(
                "UPDATE canonical_topics SET "
                + ",".join(f"{field}=?" for field in _EDITABLE_TOPIC_FIELDS)
                + ",updated_at=? WHERE id=?",
                (*values, utc_text(), target_id),
            )
            after_row = connection.execute(
                "SELECT * FROM canonical_topics WHERE id=?", (target_id,)
            ).fetchone()
            after = self._topic_snapshot(after_row)
            audit_id = self._audit(
                connection,
                active["id"],
                request.operation_id,
                "revert",
                "topic",
                str(target_id),
                before,
                after,
                request.comment,
                reverts_audit_id=original["id"],
            )
            audit = connection.execute(
                "SELECT * FROM canonical_route_audits WHERE id=?", (audit_id,)
            ).fetchone()
        return self._audit_read(audit)

    def audits(self, limit: int = 100) -> list[CanonicalRouteAuditRead]:
        with self.database.connect() as connection:
            active = self._active_import(connection)
            rows = connection.execute(
                "SELECT * FROM canonical_route_audits WHERE import_id=? ORDER BY id DESC LIMIT ?",
                (active["id"], limit),
            ).fetchall()
        return [self._audit_read(row) for row in rows]

    @staticmethod
    def _page_status(page: ReferenceIndexPage) -> str:
        statuses = {entry.parse_status for entry in page.entries}
        if "cropped" in statuses:
            return "cropped"
        if "uncertain" in statuses:
            return "uncertain"
        return "verified"

    @staticmethod
    def _source(connection: sqlite3.Connection, source_id: str | None) -> sqlite3.Row:
        if source_id:
            row = connection.execute(
                "SELECT * FROM sources WHERE id=? AND current_version_id IS NOT NULL", (source_id,)
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM sources WHERE pedagogical_role='core_theory' "
                "AND user_selected_core=1 AND editorial_status='user_confirmed' "
                "AND current_version_id IS NOT NULL ORDER BY priority DESC,id LIMIT 1"
            ).fetchone()
        if not row:
            raise LibraryNotFoundError("El manual Herder principal no está configurado.")
        return row

    @staticmethod
    def _active_import(connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM canonical_route_imports WHERE active=1 ORDER BY imported_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            raise LibraryNotFoundError("La ruta canónica Herder todavía no está disponible.")
        return row

    def _store_route(
        self,
        connection: sqlite3.Connection,
        import_id: str,
        source: sqlite3.Row,
        pages: list[ReferenceIndexPage],
        editorial_status: str,
        now: str,
    ) -> int:
        top_entries = sorted(
            (
                (page.reference_pdf_page, entry)
                for page in pages
                for entry in page.entries
                if entry.hierarchy_level == "top_level_theme" and entry.theme_number
            ),
            key=lambda item: item[1].theme_number or 0,
        )
        first_back_page = min(
            (
                _page_number(entry.printed_page_label)
                for page in pages
                for entry in page.entries
                if entry.hierarchy_level == "back_matter"
                and _page_number(entry.printed_page_label) is not None
            ),
            default=None,
        )
        topic_ids: dict[int, int] = {}
        for index, (reference_page, entry) in enumerate(top_entries):
            number = int(entry.theme_number or 0)
            printed_start = _page_number(entry.printed_page_label)
            if printed_start is None:
                continue
            next_start = (
                _page_number(top_entries[index + 1][1].printed_page_label)
                if index + 1 < len(top_entries)
                else first_back_page
            )
            printed_end = next_start - 1 if next_start and next_start > printed_start else None
            title_es = _clean_theme_title(entry.title_es, number) or "Título pendiente"
            title_de = _clean_theme_title(entry.title_de, number)
            stable_key = f"herder:{source['id']}:v{source['current_version_id']}:theme:{number:02d}"
            cursor = connection.execute(
                "INSERT INTO canonical_topics(import_id,source_id,source_version_id,stable_key,"
                "theme_number,title_es,title_de,printed_start,printed_end,printed_end_origin,"
                "reference_pdf_page,reference_visual_region,editorial_status,origin,confidence,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    source["id"],
                    source["current_version_id"],
                    stable_key,
                    number,
                    title_es,
                    title_de,
                    printed_start,
                    printed_end,
                    "calculated" if printed_end is not None else "unknown",
                    reference_page,
                    entry.visual_region,
                    (
                        "user_confirmed"
                        if entry.editorial_status == "user_confirmed"
                        else editorial_status
                    ),
                    ROUTE_ORIGIN,
                    entry.confidence,
                    now,
                    now,
                ),
            )
            topic_ids[number] = int(cursor.lastrowid)

        outline_count = 0
        current_theme: int | None = None
        sort_keys: Counter[int] = Counter()
        stacks: dict[int, list[tuple[str, int]]] = {}
        level_rank = {"section": 1, "subsection": 2, "item": 3}
        for page in sorted(pages, key=lambda item: item.reference_pdf_page):
            for entry in page.entries:
                if entry.hierarchy_level == "top_level_theme" and entry.theme_number:
                    current_theme = entry.theme_number
                    continue
                if entry.hierarchy_level in {"front_matter", "back_matter"}:
                    continue
                theme = entry.theme_number or current_theme
                if not theme or theme not in topic_ids:
                    continue
                rank = level_rank.get(entry.hierarchy_level, 1)
                stack = stacks.setdefault(theme, [])
                while stack and level_rank.get(stack[-1][0], 1) >= rank:
                    stack.pop()
                parent_id = stack[-1][1] if stack else None
                sort_keys[theme] += 1
                cursor = connection.execute(
                    "INSERT INTO canonical_outline_nodes(canonical_topic_id,parent_id,sort_key,"
                    "hierarchy_level,local_number,title_es,title_de,printed_page,"
                    "reference_pdf_page,visual_region,parse_status,editorial_status,origin,confidence,"
                    "raw_visible_text,notes,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        topic_ids[theme],
                        parent_id,
                        sort_keys[theme],
                        entry.hierarchy_level,
                        entry.local_number,
                        entry.title_es,
                        entry.title_de,
                        _page_number(entry.printed_page_label),
                        page.reference_pdf_page,
                        entry.visual_region,
                        entry.parse_status,
                        (
                            "user_confirmed"
                            if entry.editorial_status == "user_confirmed"
                            else editorial_status
                        ),
                        ROUTE_ORIGIN,
                        entry.confidence,
                        entry.raw_visible_text,
                        entry.notes,
                        now,
                        now,
                    ),
                )
                stack.append((entry.hierarchy_level, int(cursor.lastrowid)))
                outline_count += 1
        return outline_count

    @staticmethod
    def _apply_verified_akkusativ_anchor(
        connection: sqlite3.Connection, import_id: str, source_version_id: int, now: str
    ) -> None:
        location = connection.execute(
            "SELECT l.pdf_page_number,l.region_kind,m.scan_layout FROM "
            "pedagogical_evidence_locations l JOIN pedagogical_concepts c ON c.id=l.concept_id "
            "LEFT JOIN document_page_mappings m ON m.id=l.page_mapping_id "
            "WHERE l.source_version_id=? AND l.status='user_confirmed' "
            "AND c.normalized_name='akkusativ' AND l.pdf_page_number IS NOT NULL "
            "ORDER BY l.reviewed_at DESC,l.created_at DESC LIMIT 1",
            (source_version_id,),
        ).fetchone()
        if not location:
            return
        topic = connection.execute(
            "SELECT id FROM canonical_topics WHERE import_id=? AND theme_number=22",
            (import_id,),
        ).fetchone()
        if not topic:
            return
        nodes = connection.execute(
            "SELECT id,title_es,title_de,printed_page FROM canonical_outline_nodes "
            "WHERE canonical_topic_id=?",
            (topic["id"],),
        ).fetchall()
        target = next(
            (
                node
                for node in nodes
                if node["printed_page"] == 162
                and (
                    "acusativo" in _fold(node["title_es"]) or "akkusativ" in _fold(node["title_de"])
                )
            ),
            None,
        )
        if target:
            connection.execute(
                "UPDATE canonical_outline_nodes SET manual_pdf_page=?,manual_scan_layout=?,"
                "manual_region='unknown',editorial_status='user_confirmed',updated_at=? WHERE id=?",
                (
                    location["pdf_page_number"],
                    location["scan_layout"] or "unknown",
                    now,
                    target["id"],
                ),
            )

    def _map_legacy(
        self,
        connection: sqlite3.Connection,
        import_id: str,
        source: sqlite3.Row,
        now: str,
    ) -> dict[str, int]:
        topics = connection.execute(
            "SELECT * FROM canonical_topics WHERE import_id=? ORDER BY theme_number", (import_id,)
        ).fetchall()
        sections = connection.execute(
            "SELECT * FROM editorial_sections WHERE source_version_id=? ORDER BY page_start,id",
            (source["current_version_id"],),
        ).fetchall()
        claims = [self._legacy_theme_claim(section) for section in sections]
        content_start = self._legacy_content_start(claims)
        valid_positions = [
            (index, claim)
            for index, claim in enumerate(claims)
            if index >= content_start and claim is not None and 1 <= claim <= 51
        ]
        previous_claim: dict[int, int | None] = {}
        next_claim: dict[int, int | None] = {}
        for position, (section_index, _claim) in enumerate(valid_positions):
            previous_claim[section_index] = (
                valid_positions[position - 1][1] if position > 0 else None
            )
            next_claim[section_index] = (
                valid_positions[position + 1][1] if position + 1 < len(valid_positions) else None
            )
        counts: Counter[str] = Counter()
        for section_index, section in enumerate(sections):
            title = str(section["title"])
            visible_match = _THEME.search(title)
            claimed = claims[section_index]
            similarities = [
                (
                    topic,
                    max(
                        SequenceMatcher(None, _fold(title), _fold(topic["title_es"])).ratio(),
                        SequenceMatcher(None, _fold(title), _fold(topic["title_de"])).ratio(),
                    ),
                )
                for topic in topics
            ]
            similarities.sort(key=lambda item: (-item[1], item[0]["theme_number"]))
            best, similarity = similarities[0]
            evidence: list[str] = []
            target: sqlite3.Row | None = None
            if section_index < content_start:
                mapping_status, score = "rejected", 0.99
                reason = (
                    "Encabezado detectado en el bloque inicial del índice legacy, antes del "
                    "reinicio monotónico de temas."
                )
                evidence.extend(
                    ["legacy_initial_index_cluster", f"legacy_pdf_page:{section['page_start']}"]
                )
            elif claimed is not None and not 1 <= claimed <= 51:
                if similarity >= 0.65:
                    target = best
                    mapping_status, score = "probable", min(0.88, similarity)
                    evidence.extend(
                        [f"invalid_legacy_theme:{claimed}", f"title_similarity:{similarity:.3f}"]
                    )
                    reason = (
                        "La clave legacy no representa un Tema 1-51; el título aporta una "
                        "coincidencia probable que requiere revisión."
                    )
                else:
                    mapping_status, score = "rejected", 0.98
                    reason = "Falso encabezado legacy fuera del rango editorial Tema 1-51."
                    evidence.extend(
                        [
                            f"invalid_legacy_theme:{claimed}",
                            f"best_title_similarity:{similarity:.3f}",
                        ]
                    )
            elif claimed is not None:
                numbered = next(
                    (topic for topic in topics if topic["theme_number"] == claimed), None
                )
                if numbered is None:
                    mapping_status, score = "unmatched", 0.0
                    reason = "El tema declarado no existe en esta importación candidata."
                    evidence.append(f"missing_claimed_theme:{claimed}")
                    connection.execute(
                        "INSERT INTO canonical_legacy_mappings(import_id,legacy_section_id,"
                        "canonical_topic_id,mapping_status,score,evidence_json,reason,created_at,"
                        "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            import_id,
                            section["id"],
                            None,
                            mapping_status,
                            score,
                            json_dump(evidence),
                            reason,
                            now,
                            now,
                        ),
                    )
                    counts[mapping_status] += 1
                    continue
                same_similarity = max(
                    SequenceMatcher(None, _fold(title), _fold(numbered["title_es"])).ratio(),
                    SequenceMatcher(None, _fold(title), _fold(numbered["title_de"])).ratio(),
                )
                previous = previous_claim.get(section_index)
                following = next_claim.get(section_index)
                sequence_consistent = (previous is None or previous < claimed) and (
                    following is None or claimed < following
                )
                claim_source = "visible_theme_number" if visible_match else "legacy_stable_key"
                evidence.extend(
                    [
                        f"{claim_source}:{claimed}",
                        f"title_similarity:{same_similarity:.3f}",
                        f"monotonic_neighbors:{str(sequence_consistent).lower()}",
                        f"legacy_pdf_page:{section['page_start']}",
                    ]
                )
                target = numbered
                if same_similarity >= 0.65 or (
                    visible_match is not None and same_similarity >= 0.45
                ):
                    mapping_status, score = "exact", min(0.99, 0.72 + same_similarity * 0.27)
                    reason = "Coinciden número editorial y título normalizado."
                elif sequence_consistent:
                    mapping_status, score = "probable", min(0.89, 0.62 + same_similarity * 0.25)
                    reason = (
                        "La clave temática y el orden de páginas coinciden; el título OCR es "
                        "demasiado parcial para una reasignación exacta."
                    )
                else:
                    mapping_status, score = "ambiguous", 0.52
                    reason = (
                        "La clave temática no mantiene el orden esperado y no basta para "
                        "reasignar la sección automáticamente."
                    )
            elif similarity >= 0.65:
                target = best
                mapping_status, score = "probable", min(0.88, similarity)
                evidence.append(f"title_similarity:{similarity:.3f}")
                reason = "El título bilingüe normalizado coincide, pero falta el número editorial."
            else:
                mapping_status, score = "unmatched", min(0.49, similarity)
                reason = "No hay dos señales independientes para un mapping seguro."
                evidence.append(f"best_title_similarity:{similarity:.3f}")
            connection.execute(
                "INSERT INTO canonical_legacy_mappings(import_id,legacy_section_id,"
                "canonical_topic_id,mapping_status,score,evidence_json,reason,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    section["id"],
                    target["id"] if target is not None else None,
                    mapping_status,
                    score,
                    json_dump(evidence),
                    reason,
                    now,
                    now,
                ),
            )
            counts[mapping_status] += 1
        return dict(counts)

    @staticmethod
    def _legacy_theme_claim(section: sqlite3.Row) -> int | None:
        visible = _THEME.search(str(section["title"]))
        if visible:
            return int(visible.group(1))
        stable = _LEGACY_THEME_KEY.fullmatch(str(section["stable_key"]))
        return int(stable.group(1)) if stable else None

    @staticmethod
    def _legacy_content_start(claims: list[int | None]) -> int:
        """Locate the post-index topic sequence by its first editorial-number restart."""
        greatest = 0
        for index, claim in enumerate(claims):
            if claim is None or not 1 <= claim <= 51:
                continue
            if greatest and claim < greatest:
                return index
            greatest = max(greatest, claim)
        return 0

    def _mappings_read(
        self, connection: sqlite3.Connection, import_id: str
    ) -> list[CanonicalLegacyMappingRead]:
        rows = connection.execute(
            "SELECT m.*,e.stable_key legacy_stable_key,e.title legacy_title,"
            "t.theme_number FROM canonical_legacy_mappings m "
            "JOIN editorial_sections e ON e.id=m.legacy_section_id "
            "LEFT JOIN canonical_topics t ON t.id=m.canonical_topic_id "
            "WHERE m.import_id=? ORDER BY e.page_start,e.id",
            (import_id,),
        ).fetchall()
        return [self._mapping_read(row) for row in rows]

    @staticmethod
    def _mapping_snapshots(
        connection: sqlite3.Connection, import_id: str
    ) -> list[dict[str, object]]:
        rows = connection.execute(
            "SELECT m.legacy_section_id,t.theme_number,m.mapping_status,m.score,m.evidence_json,"
            "m.reason FROM canonical_legacy_mappings m LEFT JOIN canonical_topics t "
            "ON t.id=m.canonical_topic_id WHERE m.import_id=? ORDER BY m.legacy_section_id",
            (import_id,),
        ).fetchall()
        return [
            {
                "legacy_section_id": row["legacy_section_id"],
                "theme_number": row["theme_number"],
                "mapping_status": row["mapping_status"],
                "score": row["score"],
                "evidence": json_load(row["evidence_json"], []),
                "reason": row["reason"],
            }
            for row in rows
        ]

    @staticmethod
    def _review_request_matches(
        audit: sqlite3.Row,
        request: CanonicalTopicReviewRequest,
        recorded_after: dict[str, object],
    ) -> bool:
        if audit["action"] != request.action or audit["comment"] != request.comment:
            return False
        expected_status = {
            "confirm": "user_confirmed",
            "incorrect": "rejected",
            "unknown": "candidate",
            "correct": "user_confirmed",
        }[request.action]
        if recorded_after.get("editorial_status") != expected_status:
            return False
        if request.action != "correct":
            return True
        return all(
            value is None or recorded_after.get(field) == value
            for field in _EDITABLE_TOPIC_FIELDS
            if (value := getattr(request, field, None)) is not None
        )

    def _topic_read(self, connection: sqlite3.Connection, row: sqlite3.Row) -> CanonicalTopicRead:
        nodes = connection.execute(
            "SELECT * FROM canonical_outline_nodes WHERE canonical_topic_id=? ORDER BY sort_key",
            (row["id"],),
        ).fetchall()
        return CanonicalTopicRead(
            **{key: row[key] for key in CanonicalTopicRead.model_fields if key != "outline"},
            outline=[
                CanonicalOutlineNodeRead.model_validate(
                    {key: node[key] for key in CanonicalOutlineNodeRead.model_fields}
                )
                for node in nodes
            ],
        )

    @staticmethod
    def _mapping_read(row: sqlite3.Row) -> CanonicalLegacyMappingRead:
        return CanonicalLegacyMappingRead(
            id=row["id"],
            legacy_section_id=row["legacy_section_id"],
            legacy_stable_key=row["legacy_stable_key"],
            legacy_title=row["legacy_title"],
            canonical_topic_id=row["canonical_topic_id"],
            theme_number=row["theme_number"],
            mapping_status=row["mapping_status"],
            score=row["score"],
            evidence=json_load(row["evidence_json"], []),
            reason=row["reason"],
            reviewed_at=row["reviewed_at"],
        )

    @staticmethod
    def _import_read(
        row: sqlite3.Row, *, idempotent_replay: bool = False
    ) -> CanonicalRouteImportRead:
        return CanonicalRouteImportRead(
            id=row["id"],
            source_id=row["source_id"],
            source_version_id=row["source_version_id"],
            route_version=row["route_version"],
            reference_name=row["reference_name"],
            reference_sha256_prefix=str(row["reference_sha256"])[:12],
            reference_size_bytes=row["reference_size_bytes"],
            reference_page_count=row["reference_page_count"],
            model=row["model"],
            parser_version=row["parser_version"],
            status=row["status"],
            editorial_status=row["editorial_status"],
            origin=row["origin"],
            active=bool(row["active"]),
            validation=CanonicalRouteValidationRead.model_validate(
                json_load(row["validation_json"], {})
            ),
            warnings=json_load(row["warnings_json"], []),
            statistics=json_load(row["statistics_json"], {}),
            imported_at=row["imported_at"],
            validated_at=row["validated_at"],
            idempotent_replay=idempotent_replay,
        )

    @staticmethod
    def _topic_snapshot(row: sqlite3.Row) -> dict[str, object]:
        return {field: row[field] for field in _EDITABLE_TOPIC_FIELDS}

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        import_id: str,
        operation_id: str,
        action: str,
        target_type: str,
        target_id: str,
        before: dict[str, object],
        after: dict[str, object],
        comment: str | None,
        *,
        reverts_audit_id: int | None = None,
    ) -> int:
        cursor = connection.execute(
            "INSERT INTO canonical_route_audits(import_id,operation_id,actor,action,target_type,"
            "target_id,before_json,after_json,comment,reverts_audit_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                import_id,
                operation_id,
                "user",
                action,
                target_type,
                target_id,
                json_dump(before),
                json_dump(after),
                comment,
                reverts_audit_id,
                utc_text(),
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _audit_read(row: sqlite3.Row) -> CanonicalRouteAuditRead:
        return CanonicalRouteAuditRead(
            id=row["id"],
            operation_id=row["operation_id"],
            action=row["action"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            before=json_load(row["before_json"], {}),
            after=json_load(row["after_json"], {}),
            comment=row["comment"],
            reverts_audit_id=row["reverts_audit_id"],
            created_at=row["created_at"],
        )
