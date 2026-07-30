from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from .database import LibraryDatabase
from .runs import stable_configuration_hash
from .schemas import (
    ComparisonEventRead,
    CorrespondenceAdjust,
    CorrespondenceDecision,
    CorrespondenceList,
    CorrespondenceRead,
    LibraryBusyError,
    LibraryContractError,
    LibraryNotFoundError,
    NoEquivalentRequest,
    TransferPlanRead,
    VersionComparisonCreate,
    VersionComparisonRead,
)
from .service import json_dump, json_load, utc_text

_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_HORIZONTAL_SPACE = re.compile(r"[^\S\n]+")
_LINE_END_SPACE = re.compile(r"[ \t]+\n")
_WORD = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
_CONFIDENCE_ORDER = {"very_high": 5, "high": 4, "medium": 3, "low": 2, "ambiguous": 1}


def normalize_text(text: str) -> str:
    """Conservative layout cleanup; never corrects, translates or folds German letters."""
    value = unicodedata.normalize("NFC", _INVISIBLE.sub("", text.replace("\r\n", "\n")))
    value = _LINE_END_SPACE.sub("\n", value)
    value = "\n".join(_HORIZONTAL_SPACE.sub(" ", line).strip() for line in value.split("\n"))
    return "\n".join(value.splitlines()).strip()


def text_fingerprint(text: str | None) -> dict[str, Any]:
    if text is None:
        return {
            "available": False,
            "normalized": None,
            "hash": None,
            "characters": 0,
            "words": 0,
            "tokens": [],
            "shingles": [],
            "first_lines": [],
            "last_lines": [],
        }
    normalized = normalize_text(text)
    words = _WORD.findall(normalized.casefold())
    shingles = [" ".join(words[index : index + 3]) for index in range(max(0, len(words) - 2))]
    lines = [line for line in normalized.splitlines() if line]
    return {
        "available": True,
        "normalized": normalized,
        "hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "characters": len(normalized),
        "words": len(words),
        "tokens": sorted(set(words)),
        "shingles": sorted(set(shingles)),
        "first_lines": lines[:3],
        "last_lines": lines[-3:],
        "replacement_characters": normalized.count("\ufffd"),
        "split_words": len(re.findall(r"\w-\n\w", normalized, re.UNICODE)),
        "suspicious_joins": len(re.findall(r"[a-zäöüß][A-ZÄÖÜ]", normalized)),
    }


def visual_fingerprint(
    render: bytes | None,
    *,
    width: int | None = None,
    height: int | None = None,
    regions: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Fingerprint pre-rendered local bytes. It performs no recognition or OCR."""
    if render is None:
        return {
            "available": False,
            "hash": None,
            "perceptual_hash": None,
            "region_hashes": {},
            "density": None,
        }
    digest = hashlib.sha256(render).hexdigest()
    sample = (
        render
        if len(render) <= 64
        else bytes(render[round(index * (len(render) - 1) / 63)] for index in range(64))
    )
    values = list(sample) + [255] * (64 - len(sample))
    average = sum(values) / len(values)
    bits = "".join("1" if value < average else "0" for value in values)
    perceptual = f"{int(bits, 2):016x}"
    region_hashes = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in sorted((regions or {}).items())
    }
    return {
        "available": True,
        "hash": digest,
        "perceptual_hash": perceptual,
        "region_hashes": region_hashes,
        "density": round(sum(1 for value in values if value < 224) / 64, 6),
        "width": width,
        "height": height,
    }


def _set_similarity(left: list[str], right: list[str]) -> float | None:
    if not left or not right:
        return None
    a, b = set(left), set(right)
    return len(a & b) / len(a | b)


def _hash_similarity(left: str | None, right: str | None) -> float | None:
    if not left or not right:
        return None
    if left == right:
        return 1.0
    try:
        distance = (int(left, 16) ^ int(right, 16)).bit_count()
        return max(0.0, 1 - distance / (len(left) * 4))
    except ValueError:
        return 0.0


def text_difference(base: str | None, target: str | None) -> dict[str, Any]:
    if base is None or target is None:
        return {
            "base_available": base is not None,
            "target_available": target is not None,
            "message": "Sin texto disponible",
            "recommendation": "insufficient_evidence",
        }
    base_words = _WORD.findall(normalize_text(base))
    target_words = _WORD.findall(normalize_text(target))
    added: list[str] = []
    removed: list[str] = []
    modified = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, base_words, target_words).get_opcodes():
        if tag in {"insert", "replace"}:
            added.extend(target_words[j1:j2])
        if tag in {"delete", "replace"}:
            removed.extend(base_words[i1:i2])
        if tag == "replace":
            modified += max(i2 - i1, j2 - j1)
    base_replacements = base.count("\ufffd")
    target_replacements = target.count("\ufffd")
    base_splits = len(re.findall(r"\w-\n\w", base, re.UNICODE))
    target_splits = len(re.findall(r"\w-\n\w", target, re.UNICODE))
    base_joins = len(re.findall(r"[a-zäöüß][A-ZÄÖÜ]", base))
    target_joins = len(re.findall(r"[a-zäöüß][A-ZÄÖÜ]", target))
    if target_replacements < base_replacements and target_splits <= base_splits:
        recommendation = "target_preferred"
    elif base_replacements < target_replacements and base_splits <= target_splits:
        recommendation = "base_preferred"
    elif base == target:
        recommendation = "mixed"
    else:
        recommendation = "manual_review"
    return {
        "base_available": True,
        "target_available": True,
        "base_characters": len(base),
        "target_characters": len(target),
        "base_words": len(base_words),
        "target_words": len(target_words),
        "identical": base == target,
        "added": added[:200],
        "removed": removed[:200],
        "modified_words": modified,
        "base_replacement_characters": base_replacements,
        "target_replacement_characters": target_replacements,
        "base_split_words": base_splits,
        "target_split_words": target_splits,
        "base_suspicious_joins": base_joins,
        "target_suspicious_joins": target_joins,
        "reading_order_may_differ": SequenceMatcher(None, base_words, target_words).ratio() < 0.65,
        "german_characters_preserved": all(char in target for char in set(base) & set("äöüÄÖÜß")),
        "recommendation": recommendation,
    }


class DocumentComparisonService:
    """Auditable page matching over existing local artifacts; never invokes OCR."""

    def __init__(self, database: LibraryDatabase):
        self.database = database

    def create(self, request: VersionComparisonCreate) -> VersionComparisonRead:
        now = utc_text()
        comparison_id = str(uuid4())
        config_hash = stable_configuration_hash(request.configuration)
        with self.database.transaction(immediate=True) as connection:
            versions = connection.execute(
                "SELECT id,source_id,content_hash FROM source_versions WHERE id IN (?,?)",
                (request.base_source_version_id, request.target_source_version_id),
            ).fetchall()
            if len(versions) != 2:
                raise LibraryNotFoundError("Una de las versiones documentales no existe.")
            by_id = {int(row["id"]): row for row in versions}
            base = by_id[request.base_source_version_id]
            target = by_id[request.target_source_version_id]
            if base["source_id"] != target["source_id"]:
                raise LibraryContractError("Las versiones deben pertenecer a la misma fuente.")
            existing = connection.execute(
                "SELECT id FROM document_version_comparisons WHERE "
                "base_source_version_id=? AND target_source_version_id=? AND algorithm_version=? "
                "AND configuration_hash=? AND state IN ('planned','running')",
                (
                    request.base_source_version_id,
                    request.target_source_version_id,
                    request.algorithm_version,
                    config_hash,
                ),
            ).fetchone()
            if existing:
                return self._get(connection, str(existing["id"]))
            incompatible = connection.execute(
                "SELECT id FROM document_version_comparisons WHERE "
                "base_source_version_id=? AND target_source_version_id=? "
                "AND state IN ('planned','running') LIMIT 1",
                (
                    request.base_source_version_id,
                    request.target_source_version_id,
                ),
            ).fetchone()
            if incompatible:
                raise LibraryBusyError("Ya existe una comparación activa con otra configuración.")
            revision = int(
                connection.execute(
                    "SELECT coalesce(max(revision),0)+1 FROM document_version_comparisons "
                    "WHERE base_source_version_id=? AND target_source_version_id=?",
                    (request.base_source_version_id, request.target_source_version_id),
                ).fetchone()[0]
            )
            connection.execute(
                "INSERT INTO document_version_comparisons("
                "id,source_id,base_source_version_id,target_source_version_id,base_hash,target_hash,"
                "algorithm_version,configuration_json,configuration_hash,revision,state,initiated_by,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'planned',?,?,?)",
                (
                    comparison_id,
                    base["source_id"],
                    request.base_source_version_id,
                    request.target_source_version_id,
                    base["content_hash"],
                    target["content_hash"],
                    request.algorithm_version,
                    json_dump(request.configuration),
                    config_hash,
                    revision,
                    request.initiated_by,
                    now,
                    now,
                ),
            )
            self._event(connection, comparison_id, None, "comparison_created", request.initiated_by)
            return self._get(connection, comparison_id)

    def list(
        self,
        *,
        state: str | None = None,
        source_id: str | None = None,
        limit: int = 50,
    ) -> list[VersionComparisonRead]:
        clauses: list[str] = []
        params: list[Any] = []
        if state:
            clauses.append("c.state=?")
            params.append(state)
        if source_id:
            clauses.append("c.source_id=?")
            params.append(source_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                self._comparison_select() + f" {where} ORDER BY c.created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._comparison_read(row) for row in rows]

    def get(self, comparison_id: str) -> VersionComparisonRead:
        with self.database.connect() as connection:
            return self._get(connection, comparison_id)

    def upsert_page_artifact(
        self,
        source_version_id: int,
        pdf_page_number: int,
        *,
        text: str | None,
        render: bytes | None,
        width_points: float,
        height_points: float,
        printed_page_number: str | None = None,
        regions: dict[str, bytes] | None = None,
    ) -> int:
        """Fixture/future renderer boundary. Callers supply bytes; this service never opens PDFs."""
        now = utc_text()
        textual = text_fingerprint(text)
        visual = visual_fingerprint(
            render,
            width=round(width_points),
            height=round(height_points),
            regions=regions,
        )
        geometry_hash = hashlib.sha256(
            f"{width_points:.3f}:{height_points:.3f}:0".encode()
        ).hexdigest()
        with self.database.transaction(immediate=True) as connection:
            page = connection.execute(
                "SELECT id FROM document_pages WHERE source_version_id=? AND pdf_page_index=?",
                (source_version_id, pdf_page_number - 1),
            ).fetchone()
            if page:
                page_id = int(page["id"])
                connection.execute(
                    "UPDATE document_pages SET printed_page_number=?,width_points=?,height_points=?,"
                    "rotation_degrees=0,has_text=?,character_count=?,updated_at=? WHERE id=?",
                    (
                        printed_page_number,
                        width_points,
                        height_points,
                        int(text is not None),
                        len(text or ""),
                        now,
                        page_id,
                    ),
                )
            else:
                cursor = connection.execute(
                    "INSERT INTO document_pages(source_version_id,pdf_page_index,"
                    "printed_page_number,width_points,height_points,rotation_degrees,has_text,"
                    "character_count,evidence_json,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,0,?,?,'{}',?,?)",
                    (
                        source_version_id,
                        pdf_page_number - 1,
                        printed_page_number,
                        width_points,
                        height_points,
                        int(text is not None),
                        len(text or ""),
                        now,
                        now,
                    ),
                )
                page_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO document_page_artifacts(page_id,artifact_version,text_content,"
                "normalized_text,text_hash,text_metrics_json,visual_hash,perceptual_hash,"
                "region_hashes_json,visual_metrics_json,geometry_hash,cache_key,created_at,updated_at)"
                " VALUES (?,'page-artifacts.v1',?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(page_id) DO UPDATE SET artifact_version=excluded.artifact_version,"
                "text_content=excluded.text_content,normalized_text=excluded.normalized_text,"
                "text_hash=excluded.text_hash,text_metrics_json=excluded.text_metrics_json,"
                "visual_hash=excluded.visual_hash,perceptual_hash=excluded.perceptual_hash,"
                "region_hashes_json=excluded.region_hashes_json,"
                "visual_metrics_json=excluded.visual_metrics_json,"
                "geometry_hash=excluded.geometry_hash,cache_key=excluded.cache_key,"
                "updated_at=excluded.updated_at",
                (
                    page_id,
                    text,
                    textual["normalized"],
                    textual["hash"],
                    json_dump(
                        {key: value for key, value in textual.items() if key != "normalized"}
                    ),
                    visual["hash"],
                    visual["perceptual_hash"],
                    json_dump(visual["region_hashes"]),
                    json_dump(
                        {
                            key: value
                            for key, value in visual.items()
                            if key not in {"hash", "perceptual_hash", "region_hashes"}
                        }
                    ),
                    geometry_hash,
                    visual["hash"],
                    now,
                    now,
                ),
            )
        return page_id

    def execute(self, comparison_id: str, *, recalculate: bool = False) -> VersionComparisonRead:
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            comparison = self._comparison_row(connection, comparison_id)
            allowed = {"completed", "completed_with_issues"} if recalculate else {"planned"}
            if comparison["state"] not in allowed:
                raise LibraryContractError("La comparación no puede ejecutarse desde este estado.")
            current = connection.execute(
                "SELECT id,content_hash FROM source_versions WHERE id IN (?,?)",
                (
                    comparison["base_source_version_id"],
                    comparison["target_source_version_id"],
                ),
            ).fetchall()
            hashes = {int(row["id"]): row["content_hash"] for row in current}
            if (
                hashes.get(comparison["base_source_version_id"]) != comparison["base_hash"]
                or hashes.get(comparison["target_source_version_id"]) != comparison["target_hash"]
            ):
                connection.execute(
                    "UPDATE document_version_comparisons SET state='stale',updated_at=? WHERE id=?",
                    (now, comparison_id),
                )
                self._event(connection, comparison_id, None, "hash_mismatch", None, "stale")
                return self._get(connection, comparison_id)
            connection.execute(
                "UPDATE document_version_comparisons SET state='running',started_at=?,"
                "updated_at=?,error_code=NULL,error_detail=NULL WHERE id=?",
                (now, now, comparison_id),
            )
            connection.execute(
                "UPDATE document_page_correspondences SET review_state='superseded',updated_at=? "
                "WHERE comparison_id=? AND review_state NOT IN ('confirmed','manually_adjusted')",
                (now, comparison_id),
            )
            base_pages = self._artifacts(connection, comparison["base_source_version_id"])
            target_pages = self._artifacts(connection, comparison["target_source_version_id"])
            if not base_pages and not target_pages:
                state, summary = (
                    "completed_with_issues",
                    {
                        "base_pages": 0,
                        "target_pages": 0,
                        "unresolved": 0,
                        "message": "No hay artefactos por página; no se abrió el PDF.",
                    },
                )
            else:
                confirmed = self._confirmed_page_ids(connection, comparison_id)
                proposals = self._propose(base_pages, target_pages, comparison, confirmed)
                for proposal in proposals:
                    self._insert_proposal(connection, comparison_id, proposal, now)
                summary = self._summary(
                    connection, comparison_id, len(base_pages), len(target_pages)
                )
                state = (
                    "completed_with_issues"
                    if summary["needs_review"] or summary["unresolved"]
                    else "completed"
                )
            connection.execute(
                "UPDATE document_version_comparisons SET state=?,summary_json=?,needs_review=?,"
                "completed_at=?,updated_at=? WHERE id=?",
                (
                    state,
                    json_dump(summary),
                    int(bool(summary.get("needs_review") or summary.get("unresolved"))),
                    now,
                    now,
                    comparison_id,
                ),
            )
            self._event(
                connection, comparison_id, None, "comparison_completed", None, state, summary
            )
            self._generate_transfer_plan(connection, comparison_id, now)
            return self._get(connection, comparison_id)

    def correspondences(
        self,
        comparison_id: str,
        *,
        confidence: str | None = None,
        review_state: str | None = None,
        relation_type: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> CorrespondenceList:
        with self.database.connect() as connection:
            self._comparison_row(connection, comparison_id)
            clauses = ["c.comparison_id=?", "c.review_state<>'superseded'"]
            params: list[Any] = [comparison_id]
            for column, value in (
                ("confidence", confidence),
                ("review_state", review_state),
                ("relation_type", relation_type),
            ):
                if value:
                    clauses.append(f"c.{column}=?")
                    params.append(value)
            where = " AND ".join(clauses)
            total = int(
                connection.execute(
                    f"SELECT count(*) FROM document_page_correspondences c WHERE {where}", params
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT c.* FROM document_page_correspondences c WHERE {where} "
                "ORDER BY c.created_at,c.id LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            return CorrespondenceList(
                items=[self._correspondence_read(connection, row) for row in rows],
                page=page,
                page_size=page_size,
                total=total,
                pages=math.ceil(total / page_size) if total else 0,
            )

    def correspondence(self, correspondence_id: str) -> CorrespondenceRead:
        with self.database.connect() as connection:
            row = self._correspondence_row(connection, correspondence_id)
            return self._correspondence_read(connection, row)

    def decide(
        self, correspondence_id: str, action: str, request: CorrespondenceDecision
    ) -> CorrespondenceRead:
        target = {"confirm": "confirmed", "reject": "rejected"}.get(action)
        if not target:
            raise LibraryContractError("Decisión de revisión no válida.")
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            row = self._correspondence_row(connection, correspondence_id)
            if target == "confirmed":
                self._assert_no_conflict(connection, row["comparison_id"], correspondence_id)
            connection.execute(
                "UPDATE document_page_correspondences SET review_state=?,review_note=?,updated_at=? "
                "WHERE id=?",
                (target, request.note, now, correspondence_id),
            )
            self._invalidate_plan(connection, row["comparison_id"], now)
            self._event(
                connection,
                row["comparison_id"],
                correspondence_id,
                action,
                request.actor,
                target,
                {"note": request.note},
                previous=row["review_state"],
            )
            return self._correspondence_read(
                connection, self._correspondence_row(connection, correspondence_id)
            )

    def adjust(self, correspondence_id: str, request: CorrespondenceAdjust) -> CorrespondenceRead:
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            old = self._correspondence_row(connection, correspondence_id)
            comparison = self._comparison_row(connection, old["comparison_id"])
            page_ids = self._resolve_page_numbers(
                connection, comparison, request.base_pages, request.target_pages
            )
            connection.execute(
                "UPDATE document_page_correspondences SET review_state='superseded',updated_at=? "
                "WHERE id=?",
                (now, correspondence_id),
            )
            new_id = str(uuid4())
            connection.execute(
                "INSERT INTO document_page_correspondences("
                "id,comparison_id,relation_type,review_state,confidence,aggregate_score,"
                "evidence_json,text_difference_json,recommendation,review_note,created_by,"
                "created_at,updated_at) VALUES (?,?,?,'manually_adjusted','high',1,?,?,"
                "'manual_review',?,?,?,?)",
                (
                    new_id,
                    old["comparison_id"],
                    request.relation_type,
                    json_dump({"manual": True, "replaces": correspondence_id}),
                    "{}",
                    request.note,
                    request.actor,
                    now,
                    now,
                ),
            )
            self._insert_page_links(connection, new_id, page_ids)
            self._assert_no_conflict(connection, old["comparison_id"], new_id)
            self._invalidate_plan(connection, old["comparison_id"], now)
            self._event(
                connection,
                old["comparison_id"],
                new_id,
                "manual_adjustment",
                request.actor,
                "manually_adjusted",
                {"superseded": correspondence_id},
                previous=old["review_state"],
            )
            return self._correspondence_read(
                connection, self._correspondence_row(connection, new_id)
            )

    def mark_no_equivalent(
        self, comparison_id: str, request: NoEquivalentRequest
    ) -> CorrespondenceRead:
        relation = "deleted" if request.side == "base" else "inserted"
        if request.reason not in {relation, "blank", "duplicate", "unresolved"}:
            raise LibraryContractError("El motivo no corresponde al lado de la página.")
        comparison = self.get(comparison_id)
        empty_id = str(uuid4())
        return self._create_manual(
            comparison_id,
            empty_id,
            CorrespondenceAdjust(
                base_pages=[request.page_number] if request.side == "base" else [],
                target_pages=[request.page_number] if request.side == "target" else [],
                relation_type=request.reason,
                actor=request.actor,
                note=request.note,
            ),
            comparison,
        )

    def cancel(self, comparison_id: str, actor: str | None = None) -> VersionComparisonRead:
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            row = self._comparison_row(connection, comparison_id)
            if row["state"] not in {"planned", "running"}:
                raise LibraryContractError("Solo se puede cancelar una comparación activa.")
            connection.execute(
                "UPDATE document_version_comparisons SET state='cancelled',completed_at=?,"
                "updated_at=? WHERE id=?",
                (now, now, comparison_id),
            )
            self._event(
                connection,
                comparison_id,
                None,
                "comparison_cancelled",
                actor,
                "cancelled",
                previous=row["state"],
            )
            return self._get(connection, comparison_id)

    def events(self, comparison_id: str) -> list[ComparisonEventRead]:
        with self.database.connect() as connection:
            self._comparison_row(connection, comparison_id)
            rows = connection.execute(
                "SELECT * FROM document_comparison_events WHERE comparison_id=? "
                "ORDER BY created_at,id",
                (comparison_id,),
            ).fetchall()
            return [self._event_read(row) for row in rows]

    def revert_event(
        self,
        comparison_id: str,
        event_id: str,
        request: CorrespondenceDecision,
    ) -> ComparisonEventRead:
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            event = connection.execute(
                "SELECT * FROM document_comparison_events WHERE id=? AND comparison_id=?",
                (event_id, comparison_id),
            ).fetchone()
            if not event:
                raise LibraryNotFoundError("El evento de revisión no existe.")
            if event["event_type"] not in {
                "confirm",
                "reject",
                "manual_adjustment",
                "manual_no_equivalent",
            }:
                raise LibraryContractError("Este evento no representa una decisión reversible.")
            if not event["correspondence_id"]:
                raise LibraryContractError("El evento no está ligado a una correspondencia.")
            relation = self._correspondence_row(connection, event["correspondence_id"])
            if event["event_type"] in {"confirm", "reject"}:
                if relation["review_state"] != event["new_state"]:
                    raise LibraryBusyError(
                        "La decisión ya cambió; revierte primero el evento más reciente."
                    )
                restored = event["previous_state"] or "needs_review"
                connection.execute(
                    "UPDATE document_page_correspondences SET review_state=?,review_note=?,"
                    "updated_at=? WHERE id=?",
                    (restored, request.note, now, relation["id"]),
                )
            else:
                if relation["review_state"] != "manually_adjusted":
                    raise LibraryBusyError("La decisión manual ya no es la versión vigente.")
                connection.execute(
                    "UPDATE document_page_correspondences SET review_state='superseded',"
                    "updated_at=? WHERE id=?",
                    (now, relation["id"]),
                )
                detail = json_load(event["detail_json"], {})
                replaced = detail.get("superseded")
                if replaced:
                    connection.execute(
                        "UPDATE document_page_correspondences SET review_state=?,updated_at=? "
                        "WHERE id=? AND comparison_id=?",
                        (
                            event["previous_state"] or "needs_review",
                            now,
                            replaced,
                            comparison_id,
                        ),
                    )
            self._invalidate_plan(connection, comparison_id, now)
            reverted_id = self._event(
                connection,
                comparison_id,
                relation["id"],
                "decision_reverted",
                request.actor,
                event["previous_state"],
                {"reverted_event_id": event_id, "note": request.note},
                previous=event["new_state"],
            )
            row = connection.execute(
                "SELECT * FROM document_comparison_events WHERE id=?", (reverted_id,)
            ).fetchone()
            return self._event_read(row)

    def transfer_plan(self, comparison_id: str) -> TransferPlanRead:
        with self.database.connect() as connection:
            self._comparison_row(connection, comparison_id)
            row = connection.execute(
                "SELECT * FROM document_transfer_plans WHERE comparison_id=? "
                "ORDER BY revision DESC LIMIT 1",
                (comparison_id,),
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("Aún no existe un plan de transferencia.")
            return TransferPlanRead(
                id=row["id"],
                comparison_id=row["comparison_id"],
                revision=row["revision"],
                state=row["state"],
                plan=json_load(row["plan_json"], {}),
                created_at=row["created_at"],
                invalidated_at=row["invalidated_at"],
            )

    def technical_thumbnail(self, correspondence_id: str, side: str, position: int) -> str:
        if side not in {"base", "target"} or position < 0:
            raise LibraryContractError("La miniatura solicitada no es válida.")
        with self.database.connect() as connection:
            self._correspondence_row(connection, correspondence_id)
            row = connection.execute(
                "SELECT p.pdf_page_index,p.has_text,a.visual_hash,a.visual_metrics_json "
                "FROM document_correspondence_pages cp "
                "JOIN document_pages p ON p.id=cp.page_id "
                "LEFT JOIN document_page_artifacts a ON a.page_id=p.id "
                "WHERE cp.correspondence_id=? AND cp.side=? AND cp.position=?",
                (correspondence_id, side, position),
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("La página de la miniatura no existe.")
        digest = (
            row["visual_hash"]
            or hashlib.sha256(f"{correspondence_id}:{side}:{position}".encode()).hexdigest()
        )
        safe_digest = re.sub(r"[^0-9a-f]", "a", digest.lower())[:12].ljust(12, "a")
        color = f"#{safe_digest[:6]}"
        page_number = int(row["pdf_page_index"]) + 1
        text_label = "texto existente" if row["has_text"] else "sin texto"
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="300" '
            'viewBox="0 0 220 300" role="img">'
            f'<rect width="220" height="300" fill="#111822"/>'
            f'<rect x="16" y="16" width="188" height="248" rx="5" fill="{color}" opacity=".32"/>'
            '<path d="M34 58h152M34 84h128M34 110h142M34 136h116M34 162h150" '
            'stroke="#d9e5f3" stroke-width="7" opacity=".7"/>'
            f'<text x="20" y="286" fill="#d9e5f3" font-family="system-ui" font-size="13">'
            f"PDF {page_number} · {text_label}</text></svg>"
        )

    def _propose(self, base, target, comparison, excluded: set[int]) -> list[dict[str, Any]]:
        window = int(json_load(comparison["configuration_json"], {}).get("window", 12))
        threshold = float(
            json_load(comparison["configuration_json"], {}).get("minimum_score", 0.55)
        )
        bases = [item for item in base if item["id"] not in excluded]
        targets = [item for item in target if item["id"] not in excluded]
        proposals: list[dict[str, Any]] = []
        used_base: set[int] = set()
        used_target: set[int] = set()

        # Double-page splits/combines are evaluated first so ordinal 1:1 cannot consume them.
        for page in bases:
            region = page["regions"]
            for index in range(len(targets) - 1):
                left, right = targets[index], targets[index + 1]
                if left["id"] in used_target or right["id"] in used_target:
                    continue
                score_left = float(
                    region.get("left") == left["visual_hash"] and bool(left["visual_hash"])
                )
                score_right = float(
                    region.get("right") == right["visual_hash"] and bool(right["visual_hash"])
                )
                split = (score_left + score_right) / 2
                if split >= 0.95:
                    proposals.append(
                        self._proposal("one_to_many", [page], [left, right], split=split)
                    )
                    used_base.add(page["id"])
                    used_target.update({left["id"], right["id"]})
                    break
        for page in targets:
            region = page["regions"]
            for index in range(len(bases) - 1):
                left, right = bases[index], bases[index + 1]
                if left["id"] in used_base or right["id"] in used_base:
                    continue
                score_left = float(
                    region.get("left") == left["visual_hash"] and bool(left["visual_hash"])
                )
                score_right = float(
                    region.get("right") == right["visual_hash"] and bool(right["visual_hash"])
                )
                split = (score_left + score_right) / 2
                if split >= 0.95:
                    proposals.append(
                        self._proposal("many_to_one", [left, right], [page], split=split)
                    )
                    used_base.update({left["id"], right["id"]})
                    used_target.add(page["id"])
                    break

        candidates: dict[
            int, list[tuple[float, dict[str, Any], dict[str, Any], dict[str, Any]]]
        ] = {}
        for base_page in bases:
            if base_page["id"] in used_base:
                continue
            expected = round(base_page["index"] * max(1, len(targets)) / max(1, len(bases)))
            pool = [
                target_page
                for target_page in targets
                if target_page["id"] not in used_target
                and (
                    abs(target_page["index"] - expected) <= window
                    or base_page["visual_hash"] == target_page["visual_hash"]
                    or base_page["text_hash"] == target_page["text_hash"]
                )
            ]
            scored = []
            for target_page in pool:
                scores = self._scores(base_page, target_page, len(bases), len(targets))
                if scores["aggregate"] >= threshold:
                    scored.append((scores["aggregate"], base_page, target_page, scores))
            candidates[base_page["id"]] = sorted(scored, key=lambda item: item[0], reverse=True)

        ranked = sorted(
            (item for values in candidates.values() for item in values[:1]),
            key=lambda item: item[0],
            reverse=True,
        )
        for _score, base_page, target_page, scores in ranked:
            if base_page["id"] in used_base or target_page["id"] in used_target:
                continue
            alternatives = candidates[base_page["id"]]
            same_competing_content = bool(
                len(alternatives) > 1
                and alternatives[0][2]["visual_hash"]
                and alternatives[0][2]["visual_hash"] == alternatives[1][2]["visual_hash"]
                and alternatives[0][2]["text_hash"] == alternatives[1][2]["text_hash"]
            )
            ambiguous = len(alternatives) > 1 and (
                alternatives[0][0] - alternatives[1][0] < 0.04 or same_competing_content
            )
            proposal = self._proposal(
                "ambiguous" if ambiguous else "one_to_one",
                [base_page],
                [target_page],
                scores=scores,
                ambiguous=ambiguous,
            )
            proposals.append(proposal)
            if not ambiguous:
                used_base.add(base_page["id"])
                used_target.add(target_page["id"])

        for page in bases:
            if page["id"] not in used_base:
                proposals.append(self._proposal("deleted", [page], [], ambiguous=False))
        for page in targets:
            if page["id"] not in used_target:
                proposals.append(self._proposal("inserted", [], [page], ambiguous=False))
        return proposals

    def _scores(self, base, target, base_count: int, target_count: int) -> dict[str, Any]:
        text = (
            1.0
            if base["text_hash"] and base["text_hash"] == target["text_hash"]
            else _set_similarity(base["shingles"], target["shingles"])
        )
        visual = (
            1.0
            if base["visual_hash"] and base["visual_hash"] == target["visual_hash"]
            else _hash_similarity(base["perceptual_hash"], target["perceptual_hash"])
        )
        ratio_base = base["width"] / base["height"] if base["width"] and base["height"] else None
        ratio_target = (
            target["width"] / target["height"] if target["width"] and target["height"] else None
        )
        geometry = (
            max(0.0, 1 - abs(ratio_base - ratio_target) / max(ratio_base, ratio_target))
            if ratio_base and ratio_target
            else None
        )
        ordinal = max(
            0.0,
            1
            - abs(
                base["index"] / max(1, base_count - 1) - target["index"] / max(1, target_count - 1)
            ),
        )
        printed = (
            float(base["printed"] == target["printed"])
            if base["printed"] and target["printed"]
            else None
        )
        available = [
            (text, 0.38),
            (visual, 0.38),
            (geometry, 0.12),
            (ordinal, 0.08),
            (printed, 0.04),
        ]
        weight = sum(item_weight for value, item_weight in available if value is not None)
        aggregate = sum(
            (value or 0) * item_weight for value, item_weight in available if value is not None
        )
        return {
            "text": text,
            "visual": visual,
            "geometry": geometry,
            "ordinal": ordinal,
            "printed": printed,
            "aggregate": aggregate / weight if weight else 0.0,
        }

    def _proposal(
        self,
        relation: str,
        base: list[dict[str, Any]],
        target: list[dict[str, Any]],
        *,
        scores: dict[str, Any] | None = None,
        split: float | None = None,
        ambiguous: bool = False,
    ) -> dict[str, Any]:
        scores = scores or {
            "text": None,
            "visual": None,
            "geometry": None,
            "ordinal": None,
            "printed": None,
            "aggregate": split or 0,
        }
        aggregate = float(scores["aggregate"] if split is None else split)
        exact = bool(
            len(base) == len(target) == 1
            and scores.get("text") == 1
            and scores.get("visual") == 1
            and (scores.get("geometry") or 0) >= 0.99
        )
        if ambiguous:
            confidence, review = "ambiguous", "needs_review"
        elif exact:
            confidence, review = "very_high", "auto_supported"
        elif aggregate >= 0.9:
            confidence, review = "high", "needs_review"
        elif aggregate >= 0.72:
            confidence, review = "medium", "needs_review"
        else:
            confidence, review = "low", "needs_review"
        base_text = "\n".join(item["text"] for item in base if item["text"] is not None) or None
        target_text = "\n".join(item["text"] for item in target if item["text"] is not None) or None
        difference = text_difference(base_text, target_text)
        evidence = {
            "signals": {
                "text_available": base_text is not None and target_text is not None,
                "image_comparable": all(item["visual_hash"] for item in [*base, *target]),
                "geometry_changed": scores.get("geometry") is not None
                and scores.get("geometry") < 0.98,
                "order_changed": scores.get("ordinal") is not None and scores.get("ordinal") < 0.98,
                "split_regions": split is not None,
            },
            "matched": [
                name
                for name in ("text", "visual", "geometry", "printed")
                if scores.get(name) is not None and scores[name] >= 0.9
            ],
            "disagreed": [
                name
                for name in ("text", "visual", "geometry", "printed")
                if scores.get(name) is not None and scores[name] < 0.6
            ],
            "requires_review": review == "needs_review",
            "explanation_code": (
                "double_page_regions_match"
                if split is not None
                else "ambiguous_competing_candidates"
                if ambiguous
                else "weighted_local_evidence"
            ),
        }
        return {
            "relation": relation,
            "base": base,
            "target": target,
            "scores": scores,
            "split": split,
            "aggregate": aggregate,
            "confidence": confidence,
            "review": review,
            "evidence": evidence,
            "difference": difference,
            "recommendation": difference["recommendation"],
        }

    def _insert_proposal(self, connection, comparison_id: str, proposal, now: str) -> None:
        correspondence_id = str(uuid4())
        scores = proposal["scores"]
        connection.execute(
            "INSERT INTO document_page_correspondences("
            "id,comparison_id,relation_type,review_state,confidence,text_similarity,"
            "visual_similarity,geometry_similarity,ordinal_similarity,printed_page_similarity,"
            "split_similarity,aggregate_score,evidence_json,text_difference_json,recommendation,"
            "created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'algorithm',?,?)",
            (
                correspondence_id,
                comparison_id,
                proposal["relation"],
                proposal["review"],
                proposal["confidence"],
                scores.get("text"),
                scores.get("visual"),
                scores.get("geometry"),
                scores.get("ordinal"),
                scores.get("printed"),
                proposal["split"],
                proposal["aggregate"],
                json_dump(proposal["evidence"]),
                json_dump(proposal["difference"]),
                proposal["recommendation"],
                now,
                now,
            ),
        )
        self._insert_page_links(
            connection,
            correspondence_id,
            {
                "base": [item["id"] for item in proposal["base"]],
                "target": [item["id"] for item in proposal["target"]],
            },
        )

    def _artifacts(self, connection, version_id: int) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT p.id,p.pdf_page_index,p.printed_page_number,p.width_points,p.height_points,"
            "a.text_content,a.text_hash,a.text_metrics_json,a.visual_hash,a.perceptual_hash,"
            "a.region_hashes_json FROM document_pages p "
            "LEFT JOIN document_page_artifacts a ON a.page_id=p.id "
            "WHERE p.source_version_id=? ORDER BY p.pdf_page_index",
            (version_id,),
        ).fetchall()
        result = []
        for row in rows:
            metrics = json_load(row["text_metrics_json"], {})
            result.append(
                {
                    "id": int(row["id"]),
                    "index": int(row["pdf_page_index"]),
                    "number": int(row["pdf_page_index"]) + 1,
                    "printed": row["printed_page_number"],
                    "width": row["width_points"],
                    "height": row["height_points"],
                    "text": row["text_content"],
                    "text_hash": row["text_hash"],
                    "shingles": metrics.get("shingles", []),
                    "visual_hash": row["visual_hash"],
                    "perceptual_hash": row["perceptual_hash"],
                    "regions": json_load(row["region_hashes_json"], {}),
                }
            )
        return result

    def _summary(self, connection, comparison_id: str, base_count: int, target_count: int):
        rows = connection.execute(
            "SELECT relation_type,review_state,count(*) count FROM document_page_correspondences "
            "WHERE comparison_id=? AND review_state<>'superseded' "
            "GROUP BY relation_type,review_state",
            (comparison_id,),
        ).fetchall()
        summary: dict[str, Any] = {
            "base_pages": base_count,
            "target_pages": target_count,
            "one_to_one": 0,
            "one_to_many": 0,
            "many_to_one": 0,
            "many_to_many": 0,
            "inserted": 0,
            "deleted": 0,
            "ambiguous": 0,
            "confirmed": 0,
            "pending": 0,
            "needs_review": 0,
            "unresolved": 0,
            "pages_without_text": 0,
        }
        for row in rows:
            relation, review, count = row["relation_type"], row["review_state"], int(row["count"])
            summary[relation] = summary.get(relation, 0) + count
            summary["confirmed" if review in {"confirmed", "manually_adjusted"} else "pending"] += (
                count
            )
            if review == "needs_review":
                summary["needs_review"] += count
            if relation in {"unresolved", "ambiguous"}:
                summary["unresolved"] += count
        summary["pages_without_text"] = int(
            connection.execute(
                "SELECT count(DISTINCT cp.page_id) FROM document_correspondence_pages cp "
                "JOIN document_page_correspondences c ON c.id=cp.correspondence_id "
                "JOIN document_pages p ON p.id=cp.page_id "
                "WHERE c.comparison_id=? AND c.review_state<>'superseded' AND p.has_text=0",
                (comparison_id,),
            ).fetchone()[0]
        )
        return summary

    def _generate_transfer_plan(self, connection, comparison_id: str, now: str) -> None:
        revision = int(
            connection.execute(
                "SELECT coalesce(max(revision),0)+1 FROM document_transfer_plans "
                "WHERE comparison_id=?",
                (comparison_id,),
            ).fetchone()[0]
        )
        relations = connection.execute(
            "SELECT relation_type,review_state,text_difference_json FROM "
            "document_page_correspondences WHERE comparison_id=? AND review_state<>'superseded'",
            (comparison_id,),
        ).fetchall()
        plan_items = []
        for relation in relations:
            diff = json_load(relation["text_difference_json"], {})
            geometry_stable = relation["relation_type"] == "one_to_one"
            text_stable = bool(diff.get("identical"))
            plan_items.append(
                {
                    "relation_type": relation["relation_type"],
                    "review_state": relation["review_state"],
                    "printed_page": "reuse_candidate" if geometry_stable else "redistribute",
                    "review_state_transfer": "traceable_proposal",
                    "issues": "review",
                    "sections": "propose_only",
                    "topics": "propose_only",
                    "evidence": "reuse_with_provenance" if geometry_stable else "review",
                    "concepts": "propose_only",
                    "chunks": "reuse_candidate"
                    if text_stable and geometry_stable
                    else "regenerate",
                    "embeddings": "reuse_candidate" if text_stable else "regenerate",
                    "coordinates": "reuse_candidate" if geometry_stable else "obsolete",
                    "canonical_mappings": "propose_only",
                }
            )
        plan = {
            "executable": False,
            "rules_version": "transfer-plan.v1",
            "warning": "Este plan es una propuesta y no modifica versiones ni artefactos.",
            "items": plan_items,
        }
        connection.execute(
            "INSERT INTO document_transfer_plans(id,comparison_id,revision,plan_json,created_at) "
            "VALUES (?,?,?,?,?)",
            (str(uuid4()), comparison_id, revision, json_dump(plan), now),
        )
        connection.execute(
            "UPDATE document_version_comparisons SET transfer_plan_revision=? WHERE id=?",
            (revision, comparison_id),
        )

    def _create_manual(self, comparison_id, fake_old_id, request, comparison):
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            row = self._comparison_row(connection, comparison_id)
            page_ids = self._resolve_page_numbers(
                connection, row, request.base_pages, request.target_pages
            )
            new_id = str(uuid4())
            connection.execute(
                "INSERT INTO document_page_correspondences(id,comparison_id,relation_type,"
                "review_state,confidence,aggregate_score,evidence_json,text_difference_json,"
                "recommendation,review_note,created_by,created_at,updated_at) "
                "VALUES (?,?,?,'manually_adjusted','high',1,?,'{}','manual_review',?,?,?,?)",
                (
                    new_id,
                    comparison_id,
                    request.relation_type,
                    json_dump({"manual": True}),
                    request.note,
                    request.actor,
                    now,
                    now,
                ),
            )
            self._insert_page_links(connection, new_id, page_ids)
            self._assert_no_conflict(connection, comparison_id, new_id)
            self._invalidate_plan(connection, comparison_id, now)
            self._event(
                connection,
                comparison_id,
                new_id,
                "manual_no_equivalent",
                request.actor,
                "manually_adjusted",
                {"side": "manual"},
            )
            return self._correspondence_read(
                connection, self._correspondence_row(connection, new_id)
            )

    def _resolve_page_numbers(self, connection, comparison, base, target):
        resolved: dict[str, list[int]] = {"base": [], "target": []}
        for side, version_id, numbers in (
            ("base", comparison["base_source_version_id"], base),
            ("target", comparison["target_source_version_id"], target),
        ):
            if not numbers:
                continue
            placeholders = ",".join("?" for _ in numbers)
            rows = connection.execute(
                f"SELECT id,pdf_page_index FROM document_pages WHERE source_version_id=? "
                f"AND pdf_page_index IN ({placeholders})",
                [version_id, *[number - 1 for number in numbers]],
            ).fetchall()
            by_number = {int(row["pdf_page_index"]) + 1: int(row["id"]) for row in rows}
            if any(number not in by_number for number in numbers):
                raise LibraryNotFoundError("Una página indicada no existe en la versión fijada.")
            resolved[side] = [by_number[number] for number in numbers]
        return resolved

    def _assert_no_conflict(self, connection, comparison_id: str, correspondence_id: str) -> None:
        conflict = connection.execute(
            "SELECT 1 FROM document_correspondence_pages candidate "
            "JOIN document_correspondence_pages existing ON existing.page_id=candidate.page_id "
            "JOIN document_page_correspondences relation ON relation.id=existing.correspondence_id "
            "WHERE candidate.correspondence_id=? AND existing.correspondence_id<>? "
            "AND relation.comparison_id=? "
            "AND relation.review_state IN ('confirmed','manually_adjusted') LIMIT 1",
            (correspondence_id, correspondence_id, comparison_id),
        ).fetchone()
        if conflict:
            raise LibraryBusyError("La página ya pertenece a una relación confirmada incompatible.")

    def _confirmed_page_ids(self, connection, comparison_id: str) -> set[int]:
        return {
            int(row[0])
            for row in connection.execute(
                "SELECT cp.page_id FROM document_correspondence_pages cp "
                "JOIN document_page_correspondences c ON c.id=cp.correspondence_id "
                "WHERE c.comparison_id=? AND c.review_state IN ('confirmed','manually_adjusted')",
                (comparison_id,),
            )
        }

    @staticmethod
    def _insert_page_links(connection, correspondence_id: str, page_ids) -> None:
        for side in ("base", "target"):
            for position, page_id in enumerate(page_ids[side]):
                connection.execute(
                    "INSERT INTO document_correspondence_pages("
                    "correspondence_id,side,page_id,position) VALUES (?,?,?,?)",
                    (correspondence_id, side, page_id, position),
                )

    @staticmethod
    def _invalidate_plan(connection, comparison_id: str, now: str) -> None:
        connection.execute(
            "UPDATE document_transfer_plans SET state='invalidated',invalidated_at=? "
            "WHERE comparison_id=? AND state='proposal'",
            (now, comparison_id),
        )

    def _comparison_row(self, connection, comparison_id: str):
        row = connection.execute(
            "SELECT * FROM document_version_comparisons WHERE id=?", (comparison_id,)
        ).fetchone()
        if not row:
            raise LibraryNotFoundError("La comparación documental no existe.")
        return row

    def _correspondence_row(self, connection, correspondence_id: str):
        row = connection.execute(
            "SELECT * FROM document_page_correspondences WHERE id=?", (correspondence_id,)
        ).fetchone()
        if not row:
            raise LibraryNotFoundError("La correspondencia no existe.")
        return row

    def _get(self, connection, comparison_id: str) -> VersionComparisonRead:
        row = connection.execute(
            self._comparison_select() + " WHERE c.id=?", (comparison_id,)
        ).fetchone()
        if not row:
            raise LibraryNotFoundError("La comparación documental no existe.")
        return self._comparison_read(row)

    @staticmethod
    def _comparison_select() -> str:
        return (
            "SELECT c.*,s.name source_title,b.version_number base_version_number,"
            "t.version_number target_version_number FROM document_version_comparisons c "
            "JOIN sources s ON s.id=c.source_id "
            "JOIN source_versions b ON b.id=c.base_source_version_id "
            "JOIN source_versions t ON t.id=c.target_source_version_id"
        )

    @staticmethod
    def _comparison_read(row) -> VersionComparisonRead:
        return VersionComparisonRead(
            id=row["id"],
            source_id=row["source_id"],
            source_title=row["source_title"],
            base_source_version_id=row["base_source_version_id"],
            base_version_number=row["base_version_number"],
            target_source_version_id=row["target_source_version_id"],
            target_version_number=row["target_version_number"],
            base_hash=row["base_hash"],
            target_hash=row["target_hash"],
            algorithm_version=row["algorithm_version"],
            configuration=json_load(row["configuration_json"], {}),
            configuration_hash=row["configuration_hash"],
            revision=row["revision"],
            supersedes_comparison_id=row["supersedes_comparison_id"],
            state=row["state"],
            initiated_by=row["initiated_by"],
            summary=json_load(row["summary_json"], {}),
            error_code=row["error_code"],
            error_detail=row["error_detail"],
            needs_review=bool(row["needs_review"]),
            transfer_plan_revision=row["transfer_plan_revision"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            updated_at=row["updated_at"],
        )

    def _correspondence_read(self, connection, row) -> CorrespondenceRead:
        pages = connection.execute(
            "SELECT cp.side,p.pdf_page_index FROM document_correspondence_pages cp "
            "JOIN document_pages p ON p.id=cp.page_id WHERE cp.correspondence_id=? "
            "ORDER BY cp.side,cp.position",
            (row["id"],),
        ).fetchall()
        return CorrespondenceRead(
            id=row["id"],
            comparison_id=row["comparison_id"],
            relation_type=row["relation_type"],
            review_state=row["review_state"],
            confidence=row["confidence"],
            base_pages=[
                int(page["pdf_page_index"]) + 1 for page in pages if page["side"] == "base"
            ],
            target_pages=[
                int(page["pdf_page_index"]) + 1 for page in pages if page["side"] == "target"
            ],
            text_similarity=row["text_similarity"],
            visual_similarity=row["visual_similarity"],
            geometry_similarity=row["geometry_similarity"],
            ordinal_similarity=row["ordinal_similarity"],
            printed_page_similarity=row["printed_page_similarity"],
            split_similarity=row["split_similarity"],
            aggregate_score=row["aggregate_score"],
            evidence=json_load(row["evidence_json"], {}),
            text_difference=json_load(row["text_difference_json"], {}),
            recommendation=row["recommendation"],
            review_note=row["review_note"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _event(
        connection,
        comparison_id: str,
        correspondence_id: str | None,
        event_type: str,
        actor: str | None,
        new: str | None = None,
        detail: dict[str, Any] | None = None,
        *,
        previous: str | None = None,
    ) -> str:
        event_id = str(uuid4())
        connection.execute(
            "INSERT INTO document_comparison_events("
            "id,comparison_id,correspondence_id,event_type,actor,previous_state,new_state,"
            "detail_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                comparison_id,
                correspondence_id,
                event_type,
                actor,
                previous,
                new,
                json_dump(detail or {}),
                utc_text(),
            ),
        )
        return event_id

    @staticmethod
    def _event_read(row) -> ComparisonEventRead:
        return ComparisonEventRead(
            id=row["id"],
            comparison_id=row["comparison_id"],
            correspondence_id=row["correspondence_id"],
            event_type=row["event_type"],
            actor=row["actor"],
            previous_state=row["previous_state"],
            new_state=row["new_state"],
            detail=json_load(row["detail_json"], {}),
            created_at=row["created_at"],
        )
