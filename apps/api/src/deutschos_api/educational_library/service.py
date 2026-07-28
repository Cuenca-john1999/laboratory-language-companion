from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from deutschos_api.core.config import Settings, get_settings

from .chunking import CHUNKER_VERSION, chunk_sections
from .database import LIBRARY_SCHEMA_VERSION, LibraryDatabase
from .extractors import extract, supported_extensions
from .inventory import InventoryEntry, collect_inventory, sha256_file
from .schemas import (
    ChunkRead,
    InventoryReport,
    JobRead,
    JobState,
    LibraryBusyError,
    LibraryCapabilities,
    LibraryContractError,
    LibraryNotFoundError,
    LibrarySummary,
    ProcessingState,
    ScanSummary,
    SemanticIndexSummary,
    SourceRead,
    SourceStatus,
    SourceUpdateRequest,
    SourceVersionRead,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_text(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_load(value: str | None, fallback: object) -> object:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class EducationalLibraryService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.root = self.settings.educational_materials_dir
        self.runtime = self.settings.educational_library_runtime_dir
        self.database = LibraryDatabase(self.settings.educational_library_database_path)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.schema_version = self.database.migrate()
        self._recover_interrupted_jobs()

    def _recover_interrupted_jobs(self) -> None:
        lock_path = self.runtime / "scan.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE processing_jobs SET state='interrupted',updated_at=?,"
                    "error_code='process_restarted' WHERE state='running' AND kind='scan'",
                    (utc_text(),),
                )
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _ensure_source_root(self) -> Path:
        try:
            root = self.root.resolve(strict=True)
        except OSError as exc:
            raise LibraryContractError("La carpeta de materiales no está disponible.") from exc
        if not root.is_dir():
            raise LibraryContractError("La ruta de materiales no es un directorio.")
        runtime = self.runtime.resolve(strict=False)
        if root == runtime or root in runtime.parents or runtime in root.parents:
            raise LibraryContractError("La carpeta original y el runtime deben estar separados.")
        return root

    def _scan_lock(self) -> int:
        lock_path = self.runtime / "scan.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise LibraryBusyError("Ya existe un escaneo de biblioteca en curso.") from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"pid={os.getpid()}\nstarted={utc_text()}\n".encode())
        return descriptor

    def _unlock_scan(self, descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    def create_job(self, kind: str, *, payload: dict[str, object], priority: int = 0) -> JobRead:
        job_id = str(uuid4())
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            active = connection.execute(
                "SELECT id FROM processing_jobs WHERE kind=? AND state IN ('queued','running','paused')",
                (kind,),
            ).fetchone()
            if active:
                raise LibraryBusyError(f"Ya existe un trabajo {kind} activo.")
            connection.execute(
                "INSERT INTO processing_jobs(id, kind, state, priority, payload_json, created_at, "
                "updated_at) VALUES (?, ?, 'queued', ?, ?, ?, ?)",
                (job_id, kind, priority, json_dump(payload), now, now),
            )
            row = connection.execute(
                "SELECT * FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return self._job_read(row)

    def get_job(self, job_id: str) -> JobRead:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()
        if not row:
            raise LibraryNotFoundError("El trabajo de biblioteca no existe.")
        return self._job_read(row)

    def list_jobs(self, *, limit: int = 50) -> list[JobRead]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM processing_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._job_read(row) for row in rows]

    def cancel_job(self, job_id: str) -> JobRead:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("El trabajo de biblioteca no existe.")
            if row["state"] in {"completed", "failed", "cancelled"}:
                return self._job_read(row)
            connection.execute(
                "UPDATE processing_jobs SET cancel_requested=1, updated_at=? WHERE id=?",
                (utc_text(), job_id),
            )
            row = connection.execute(
                "SELECT * FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return self._job_read(row)

    def pause_job(self, job_id: str) -> JobRead:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("El trabajo de biblioteca no existe.")
            if row["state"] in {"queued", "running"}:
                connection.execute(
                    "UPDATE processing_jobs SET state='paused', cancel_requested=1, updated_at=? "
                    "WHERE id=?",
                    (utc_text(), job_id),
                )
            row = connection.execute(
                "SELECT * FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return self._job_read(row)

    def retry_job(self, job_id: str) -> JobRead:
        original = self.get_job(job_id)
        if original.state not in {JobState.FAILED, JobState.CANCELLED, JobState.INTERRUPTED}:
            raise LibraryContractError(
                "Solo se pueden reintentar trabajos interrumpidos o fallidos."
            )
        with self.database.connect() as connection:
            payload_row = connection.execute(
                "SELECT payload_json FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()
        payload = json_load(payload_row["payload_json"], {})
        if not isinstance(payload, dict):
            payload = {}
        return self.create_job(original.kind, payload=payload, priority=original.priority)

    def job_payload(self, job_id: str) -> dict[str, object]:
        self.get_job(job_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()
        payload = json_load(row["payload_json"], {})
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _job_read(row: sqlite3.Row) -> JobRead:
        return JobRead(
            id=row["id"],
            kind=row["kind"],
            state=row["state"],
            priority=row["priority"],
            progress_current=row["progress_current"],
            progress_total=row["progress_total"],
            attempts=row["attempts"],
            cursor=row["cursor"],
            error_code=row["error_code"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            cancel_requested=bool(row["cancel_requested"]),
        )

    def _start_job(self, job_id: str, total: int) -> None:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT state FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row:
                raise LibraryNotFoundError("El trabajo de biblioteca no existe.")
            if row["state"] not in {"queued", "interrupted"}:
                raise LibraryBusyError("El trabajo no está disponible para ejecución.")
            now = utc_text()
            connection.execute(
                "UPDATE processing_jobs SET state='running', attempts=attempts+1, "
                "progress_total=?, started_at=coalesce(started_at, ?), updated_at=?, "
                "cancel_requested=0, error_code=NULL, error_detail=NULL WHERE id=?",
                (total, now, now, job_id),
            )

    def _checkpoint_job(self, job_id: str, current: int, cursor: str) -> bool:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT cancel_requested, state FROM processing_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if not row:
                return True
            connection.execute(
                "UPDATE processing_jobs SET progress_current=?, cursor=?, updated_at=? WHERE id=?",
                (current, cursor, utc_text(), job_id),
            )
            return bool(row["cancel_requested"]) or row["state"] == "paused"

    def _finish_job(self, job_id: str, state: JobState, error_code: str | None = None) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE processing_jobs SET state=?, error_code=?, updated_at=?, completed_at=? "
                "WHERE id=?",
                (state.value, error_code, utc_text(), utc_text(), job_id),
            )

    def scan(self, *, process_documents: bool = True, job_id: str | None = None) -> ScanSummary:
        root = self._ensure_source_root()
        if job_id is None:
            job_id = self.create_job("scan", payload={"process_documents": process_documents}).id
        descriptor = self._scan_lock()
        counters: Counter[str] = Counter()
        try:
            snapshot = collect_inventory(root, confirm_duplicates=False)
            catalog_entries = [
                entry for entry in snapshot.entries if not entry.is_ignored and not entry.is_symlink
            ]
            self._start_job(job_id, len(catalog_entries))
            seen_paths: set[str] = set()
            for index, entry in enumerate(catalog_entries, start=1):
                if self._checkpoint_job(job_id, index - 1, entry.relative_path):
                    counters["cancelled"] = 1
                    break
                try:
                    outcome, processing_state = self._scan_entry(
                        entry, seen_paths, process_documents
                    )
                    counters[outcome] += 1
                    if processing_state == ProcessingState.ERROR:
                        counters["errors"] += 1
                    elif processing_state == ProcessingState.UNSUPPORTED:
                        counters["unsupported"] += 1
                    elif processing_state is not None:
                        counters["processed"] += 1
                except (OSError, ValueError, zipfile.BadZipFile) as exc:
                    self._record_file_error(entry, exc)
                    counters["errors"] += 1
                seen_paths.add(entry.relative_path)
                self._checkpoint_job(job_id, index, entry.relative_path)
            counters["missing"] = self._mark_missing(seen_paths)
            duplicate_groups = self._duplicate_groups()
            counters["duplicates"] = sum(max(0, len(group) - 1) for group in duplicate_groups)
            report = snapshot.report.model_copy(
                update={
                    "confirmed_duplicate_groups": len(duplicate_groups),
                    "confirmed_duplicate_files": sum(len(group) for group in duplicate_groups),
                    "duplicate_groups": duplicate_groups[:500],
                }
            )
            self._save_inventory(job_id, report)
            final_state = JobState.CANCELLED if counters["cancelled"] else JobState.COMPLETED
            self._finish_job(job_id, final_state)
            return ScanSummary(
                job_id=job_id,
                inventory=report,
                new=counters["new"],
                modified=counters["modified"],
                renamed=counters["renamed"],
                missing=counters["missing"],
                duplicates=counters["duplicates"],
                unchanged=counters["unchanged"],
                processed=counters["processed"],
                errors=counters["errors"],
                unsupported=counters["unsupported"],
                cancelled=bool(counters["cancelled"]),
            )
        except Exception as exc:
            self._finish_job(job_id, JobState.FAILED, type(exc).__name__)
            raise
        finally:
            self._unlock_scan(descriptor)

    def _scan_entry(
        self,
        entry: InventoryEntry,
        seen_paths: set[str],
        process_documents: bool,
    ) -> tuple[str, ProcessingState | None]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT s.*, sv.processing_state AS version_state FROM sources s "
                "LEFT JOIN source_versions sv ON sv.id=s.current_version_id "
                "WHERE s.current_path=?",
                (entry.relative_path,),
            ).fetchone()
        if (
            row
            and row["current_version_id"] is not None
            and row["current_hash"] is not None
            and row["size_bytes"] == entry.size_bytes
            and row["mtime_ns"] == entry.mtime_ns
        ):
            if row["status"] == SourceStatus.EXCLUDED.value:
                with self.database.transaction(immediate=True) as connection:
                    connection.execute(
                        "UPDATE sources SET last_seen_at=?,name=?,kind=?,format=? WHERE id=?",
                        (
                            utc_text(),
                            entry.name,
                            entry.kind.value,
                            entry.extension or "[none]",
                            row["id"],
                        ),
                    )
                return "unchanged", None
            with self.database.transaction(immediate=True) as connection:
                restored_status = (
                    SourceStatus.UNSUPPORTED.value
                    if row["version_state"] == ProcessingState.UNSUPPORTED.value
                    else SourceStatus.ERROR.value
                    if row["version_state"] == ProcessingState.ERROR.value
                    else SourceStatus.PRESENT.value
                )
                connection.execute(
                    "UPDATE sources SET last_seen_at=?,name=?,kind=?,format=?,status=?,"
                    "missing_since=NULL WHERE id=?",
                    (
                        utc_text(),
                        entry.name,
                        entry.kind.value,
                        entry.extension or "[none]",
                        restored_status,
                        row["id"],
                    ),
                )
            if process_documents and row["version_state"] in {"pending", "error"}:
                return "unchanged", self._process_version(
                    entry, row["id"], row["current_version_id"]
                )
            return "unchanged", None

        content_hash = sha256_file(entry.path)
        now = utc_text()
        if row:
            if row["current_hash"] == content_hash:
                with self.database.transaction(immediate=True) as connection:
                    connection.execute(
                        "UPDATE sources SET size_bytes=?, mtime_ns=?, last_seen_at=?, status='present', "
                        "missing_since=NULL WHERE id=?",
                        (entry.size_bytes, entry.mtime_ns, now, row["id"]),
                    )
                return "unchanged", None
            source_id = row["id"]
            version_id = self._create_version(
                source_id,
                content_hash,
                entry,
                previous_version_id=row["current_version_id"],
            )
            self._mark_knowledge_stale(row["current_version_id"])
            outcome = "modified"
        else:
            renamed = self._rename_candidate(content_hash, seen_paths, entry)
            if renamed:
                source_id, version_id = renamed
                del source_id, version_id
                return "renamed", None
            else:
                source_id = str(uuid4())
                duplicate_of = self._existing_duplicate(content_hash)
                with self.database.transaction(immediate=True) as connection:
                    connection.execute(
                        "INSERT INTO sources(id,current_path,name,kind,format,size_bytes,mtime_ns,"
                        "current_hash,status,processing_state,duplicate_of_source_id,first_seen_at,"
                        "last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            source_id,
                            entry.relative_path,
                            entry.name,
                            entry.kind.value,
                            entry.extension or "[none]",
                            entry.size_bytes,
                            entry.mtime_ns,
                            content_hash,
                            SourceStatus.PRESENT.value,
                            ProcessingState.PENDING.value,
                            duplicate_of,
                            now,
                            now,
                        ),
                    )
                version_id = self._create_version(source_id, content_hash, entry)
                outcome = "new"
        if process_documents:
            state = self._process_version(entry, source_id, version_id)
            return outcome, state
        return outcome, None

    def _create_version(
        self,
        source_id: str,
        content_hash: str,
        entry: InventoryEntry,
        *,
        previous_version_id: int | None = None,
    ) -> int:
        with self.database.transaction(immediate=True) as connection:
            version = connection.execute(
                "SELECT coalesce(max(version_number),0)+1 FROM source_versions WHERE source_id=?",
                (source_id,),
            ).fetchone()[0]
            cursor = connection.execute(
                "INSERT INTO source_versions(source_id,version_number,content_hash,size_bytes,"
                "mtime_ns,processing_state,previous_version_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    version,
                    content_hash,
                    entry.size_bytes,
                    entry.mtime_ns,
                    ProcessingState.PENDING.value,
                    previous_version_id,
                    utc_text(),
                ),
            )
            version_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE sources SET current_version_id=?, current_hash=?, size_bytes=?, mtime_ns=?, "
                "name=?, kind=?, format=?, status='present', processing_state='pending', "
                "last_seen_at=?, missing_since=NULL WHERE id=?",
                (
                    version_id,
                    content_hash,
                    entry.size_bytes,
                    entry.mtime_ns,
                    entry.name,
                    entry.kind.value,
                    entry.extension or "[none]",
                    utc_text(),
                    source_id,
                ),
            )
        return version_id

    def _rename_candidate(
        self, content_hash: str, seen_paths: set[str], entry: InventoryEntry
    ) -> tuple[str, int] | None:
        with self.database.connect() as connection:
            candidates = connection.execute(
                "SELECT id,current_path,current_version_id FROM sources WHERE current_hash=? "
                "AND status IN ('present','missing') ORDER BY first_seen_at",
                (content_hash,),
            ).fetchall()
        root = self.root.resolve(strict=True)
        for candidate in candidates:
            if candidate["current_path"] in seen_paths:
                continue
            old = (root / candidate["current_path"]).resolve(strict=False)
            try:
                old.relative_to(root)
            except ValueError:
                continue
            if old.exists():
                continue
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE sources SET current_path=?,name=?,size_bytes=?,mtime_ns=?,last_seen_at=?,"
                    "status='present',missing_since=NULL WHERE id=?",
                    (
                        entry.relative_path,
                        entry.name,
                        entry.size_bytes,
                        entry.mtime_ns,
                        utc_text(),
                        candidate["id"],
                    ),
                )
            return candidate["id"], candidate["current_version_id"]
        return None

    def _existing_duplicate(self, content_hash: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM sources WHERE current_hash=? AND status='present' "
                "ORDER BY first_seen_at LIMIT 1",
                (content_hash,),
            ).fetchone()
        return str(row["id"]) if row else None

    def _mark_knowledge_stale(self, version_id: int | None) -> None:
        if version_id is None:
            return
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE knowledge_units SET stale=1,status='stale',updated_at=? WHERE id IN ("
                "SELECT knowledge_unit_id FROM knowledge_unit_sources WHERE source_version_id=?)",
                (now, version_id),
            )
            connection.execute(
                "UPDATE pedagogical_evidence_locations SET status='stale',updated_at=? "
                "WHERE source_version_id=? AND status!='rejected'",
                (now, version_id),
            )
            connection.execute(
                "UPDATE document_page_mappings SET mapping_status='stale',updated_at=? "
                "WHERE source_version_id=? AND mapping_status!='rejected'",
                (now, version_id),
            )

    def _process_version(
        self, entry: InventoryEntry, source_id: str, version_id: int
    ) -> ProcessingState:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE source_versions SET processing_state='processing',error_code=NULL,error_detail=NULL "
                "WHERE id=?",
                (version_id,),
            )
            connection.execute(
                "UPDATE sources SET processing_state='processing' WHERE id=?", (source_id,)
            )
        try:
            result = extract(entry, self.settings)
            chunks = chunk_sections(result.sections)
            with self.database.transaction(immediate=True) as connection:
                existing_document = connection.execute(
                    "SELECT id FROM documents WHERE source_version_id=?", (version_id,)
                ).fetchone()
                if existing_document:
                    connection.execute(
                        "DELETE FROM documents WHERE id=?", (existing_document["id"],)
                    )
                cursor = connection.execute(
                    "INSERT INTO documents(source_version_id,title,author,language,structure_json,"
                    "extracted_text,page_count,duration_seconds,metadata_json,extraction_quality,"
                    "needs_ocr,needs_transcription,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        version_id,
                        result.title,
                        result.author,
                        result.language,
                        json_dump(
                            [
                                {
                                    "sequence": section.sequence,
                                    "kind": section.kind,
                                    "title": section.title,
                                    "page_start": section.page_start,
                                    "page_end": section.page_end,
                                    "start_seconds": section.start_seconds,
                                    "end_seconds": section.end_seconds,
                                    "content_role": section.content_role,
                                }
                                for section in result.sections
                            ]
                        ),
                        "\n\n".join(section.text for section in result.sections),
                        result.page_count,
                        result.duration_seconds,
                        json_dump(result.metadata),
                        result.extraction_quality,
                        int(result.needs_ocr),
                        int(result.needs_transcription),
                        utc_text(),
                    ),
                )
                document_id = int(cursor.lastrowid)
                section_ids: dict[int, int] = {}
                for section in result.sections:
                    section_cursor = connection.execute(
                        "INSERT INTO sections(document_id,sequence,kind,title,hierarchy_json,text,"
                        "page_start,page_end,start_seconds,end_seconds,content_role) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            document_id,
                            section.sequence,
                            section.kind,
                            section.title,
                            json_dump(section.hierarchy),
                            section.text,
                            section.page_start,
                            section.page_end,
                            section.start_seconds,
                            section.end_seconds,
                            section.content_role,
                        ),
                    )
                    section_ids[section.sequence] = int(section_cursor.lastrowid)
                for chunk in chunks:
                    connection.execute(
                        "INSERT INTO chunks(source_version_id,section_id,sequence,title,hierarchy_json,"
                        "text,character_start,character_end,page_start,page_end,start_seconds,end_seconds,"
                        "token_count,content_hash,language,cefr_level,topics_json,content_role,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            version_id,
                            section_ids.get(chunk.section_sequence),
                            chunk.sequence,
                            chunk.title,
                            json_dump(chunk.hierarchy),
                            chunk.text,
                            chunk.character_start,
                            chunk.character_end,
                            chunk.page_start,
                            chunk.page_end,
                            chunk.start_seconds,
                            chunk.end_seconds,
                            chunk.token_count,
                            chunk.content_hash,
                            chunk.language,
                            chunk.cefr_level,
                            json_dump(chunk.topics),
                            chunk.content_role,
                            utc_text(),
                        ),
                    )
                languages = Counter(
                    chunk.language for chunk in chunks if chunk.language != "unknown"
                )
                levels = Counter(
                    chunk.cefr_level for chunk in chunks if chunk.cefr_level != "unknown"
                )
                topics = sorted({topic for chunk in chunks for topic in chunk.topics})
                statistics = {
                    "sections": len(result.sections),
                    "chunks": len(chunks),
                    "characters": sum(len(section.text) for section in result.sections),
                    "page_count": result.page_count,
                    "duration_seconds": result.duration_seconds,
                    "needs_ocr": result.needs_ocr,
                    "needs_transcription": result.needs_transcription,
                    "chunker_version": CHUNKER_VERSION,
                    "classification": "heuristic.v1",
                }
                connection.execute(
                    "UPDATE source_versions SET extractor=?,extractor_version=?,processing_state=?,"
                    "statistics_json=?,processed_at=? WHERE id=?",
                    (
                        result.extractor,
                        result.extractor_version,
                        result.processing_state.value,
                        json_dump(statistics),
                        utc_text(),
                        version_id,
                    ),
                )
                connection.execute(
                    "UPDATE sources SET processing_state=?,language=coalesce(language,?),"
                    "cefr_level=coalesce(cefr_level,?),topics_json=CASE WHEN topics_json='[]' THEN ? "
                    "ELSE topics_json END,author=coalesce(author,?),status=? WHERE id=?",
                    (
                        result.processing_state.value,
                        languages.most_common(1)[0][0] if languages else result.language,
                        levels.most_common(1)[0][0] if levels else None,
                        json_dump(topics),
                        result.author,
                        SourceStatus.UNSUPPORTED.value
                        if result.processing_state == ProcessingState.UNSUPPORTED
                        else SourceStatus.PRESENT.value,
                        source_id,
                    ),
                )
            return result.processing_state
        except Exception as exc:
            self._record_processing_error(source_id, version_id, exc)
            return ProcessingState.ERROR

    def _record_processing_error(self, source_id: str, version_id: int, exc: Exception) -> None:
        error_code = type(exc).__name__
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE source_versions SET processing_state='error',error_code=?,error_detail=?,"
                "processed_at=? WHERE id=?",
                (error_code, str(exc)[:500], utc_text(), version_id),
            )
            connection.execute(
                "UPDATE sources SET processing_state='error',status='error' WHERE id=?",
                (source_id,),
            )

    def _record_file_error(self, entry: InventoryEntry, exc: Exception) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id,current_version_id FROM sources WHERE current_path=?",
                (entry.relative_path,),
            ).fetchone()
        if row and row["current_version_id"]:
            self._record_processing_error(row["id"], row["current_version_id"], exc)
        elif not row:
            now = utc_text()
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO sources(id,current_path,name,kind,format,size_bytes,mtime_ns,"
                    "status,processing_state,first_seen_at,last_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,'error','error',?,?)",
                    (
                        str(uuid4()),
                        entry.relative_path,
                        entry.name,
                        entry.kind.value,
                        entry.extension or "[none]",
                        entry.size_bytes,
                        entry.mtime_ns,
                        now,
                        now,
                    ),
                )

    def _mark_missing(self, seen_paths: set[str]) -> int:
        with self.database.transaction(immediate=True) as connection:
            rows = connection.execute(
                "SELECT id,current_path FROM sources WHERE status NOT IN ('missing','excluded')"
            ).fetchall()
            missing = [row for row in rows if row["current_path"] not in seen_paths]
            now = utc_text()
            for row in missing:
                connection.execute(
                    "UPDATE sources SET status='missing',missing_since=?,last_seen_at=last_seen_at "
                    "WHERE id=?",
                    (now, row["id"]),
                )
        return len(missing)

    def _duplicate_groups(self) -> list[list[str]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT current_hash,group_concat(current_path, char(10)) AS paths,count(*) AS total "
                "FROM sources WHERE status='present' AND current_hash IS NOT NULL "
                "GROUP BY current_hash HAVING count(*) > 1 ORDER BY min(current_path)"
            ).fetchall()
        return [sorted(row["paths"].split("\n"), key=str.casefold) for row in rows]

    def _save_inventory(self, job_id: str, report: InventoryReport) -> None:
        report_id = str(uuid4())
        payload = report.model_dump_json(indent=2)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO inventory_reports(id,job_id,generated_at,report_json) VALUES (?,?,?,?)",
                (report_id, job_id, report.generated_at.isoformat(), payload),
            )
        reports = self.runtime / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        temporary = reports / ".inventory-latest.json.partial"
        destination = reports / "inventory-latest.json"
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(destination)

    @staticmethod
    def _tesseract_languages() -> list[str]:
        executable = shutil.which("tesseract")
        if not executable:
            return []
        try:
            result = subprocess.run(
                [executable, "--list-langs"],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        return sorted(
            line.strip()
            for line in result.stdout.splitlines()[1:]
            if line.strip() and len(line.strip()) <= 20
        )

    def capabilities(
        self,
        *,
        lm_studio_available: bool = False,
        installed_models: list[str] | None = None,
    ) -> LibraryCapabilities:
        embedding_model = self.settings.educational_library_embedding_model.strip() or None
        installed_models = sorted(set(installed_models or []))
        vision_model = self.settings.educational_library_vision_model.strip()
        return LibraryCapabilities(
            fts5=True,
            pdftotext=shutil.which("pdftotext") is not None,
            ffprobe=shutil.which("ffprobe") is not None,
            transcription_backend=None,
            transcription_model=None,
            semantic_available=bool(
                embedding_model and lm_studio_available and embedding_model in installed_models
            ),
            embedding_provider="lm_studio" if embedding_model else None,
            embedding_model=embedding_model,
            lm_studio_available=lm_studio_available,
            pdftoppm=shutil.which("pdftoppm") is not None,
            tesseract=shutil.which("tesseract") is not None,
            ocrmypdf=shutil.which("ocrmypdf") is not None,
            tesseract_languages=self._tesseract_languages(),
            vision_available=bool(
                lm_studio_available and vision_model and vision_model in installed_models
            ),
            installed_models=installed_models,
        )

    def summary(
        self,
        *,
        lm_studio_available: bool = False,
        installed_models: list[str] | None = None,
    ) -> LibrarySummary:
        with self.database.connect() as connection:
            source = connection.execute(
                "SELECT count(*) total,coalesce(sum(size_bytes),0) bytes,"
                "sum(status='present') present,sum(status='missing') missing,"
                "sum(processing_state='processed') processed,"
                "sum(processing_state IN ('pending','processing')) pending,"
                "sum(processing_state='error') errors,sum(processing_state='unsupported') unsupported,"
                "sum(processing_state='needs_ocr') needs_ocr,"
                "sum(processing_state='awaiting_transcriber') needs_transcription FROM sources"
            ).fetchone()
            chunks = int(connection.execute("SELECT count(*) FROM chunks").fetchone()[0])
            embeddings = int(connection.execute("SELECT count(*) FROM embeddings").fetchone()[0])
            knowledge = int(
                connection.execute("SELECT count(*) FROM knowledge_units").fetchone()[0]
            )
            knowledge_rows = connection.execute(
                "SELECT status,count(*) total FROM knowledge_units GROUP BY status"
            ).fetchall()
            job_rows = connection.execute(
                "SELECT state,count(*) total FROM processing_jobs GROUP BY state"
            ).fetchall()
            inventory_row = connection.execute(
                "SELECT report_json FROM inventory_reports ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
            embedding_model = self.settings.educational_library_embedding_model.strip()
            semantic = connection.execute(
                "SELECT count(DISTINCT CASE WHEN e.status='indexed' "
                "AND e.source_version_id=c.source_version_id "
                "AND e.normalization_version='embeddinggemma-retrieval.v1' THEN c.id END) indexed_count,"
                "count(DISTINCT CASE WHEN e.status='failed' THEN c.id END) failed_count,"
                "count(DISTINCT CASE WHEN e.chunk_id IS NOT NULL AND (e.status='stale' "
                "OR coalesce(e.source_version_id,-1)!=c.source_version_id "
                "OR e.normalization_version!='embeddinggemma-retrieval.v1') THEN c.id END) stale_count,"
                "max(CASE WHEN e.status='indexed' THEN e.dimension END) dimension,"
                "max(CASE WHEN e.status='indexed' THEN e.model_digest END) model_digest "
                "FROM chunks c JOIN source_versions sv ON sv.id=c.source_version_id "
                "JOIN sources s ON s.id=sv.source_id LEFT JOIN embeddings e ON e.chunk_id=c.id "
                "AND e.provider='lm_studio' AND e.model=? WHERE sv.id=s.current_version_id "
                "AND s.status='present' AND s.excluded=0",
                (embedding_model,),
            ).fetchone()
            eligible = connection.execute(
                "SELECT count(*) FROM chunks c JOIN source_versions sv ON sv.id=c.source_version_id "
                "JOIN sources s ON s.id=sv.source_id WHERE sv.id=s.current_version_id "
                "AND s.status='present' AND s.excluded=0 AND length(trim(c.text))>=20 "
                "AND c.content_role NOT IN ('solution','index')"
            ).fetchone()[0]
            excluded = connection.execute(
                "SELECT count(*) FROM chunks c JOIN source_versions sv ON sv.id=c.source_version_id "
                "JOIN sources s ON s.id=sv.source_id WHERE sv.id=s.current_version_id "
                "AND s.status='present' AND s.excluded=0 AND (length(trim(c.text))<20 "
                "OR c.content_role IN ('solution','index'))"
            ).fetchone()[0]
        return LibrarySummary(
            materials_root=str(self.root),
            runtime_root=str(self.runtime),
            schema_version=LIBRARY_SCHEMA_VERSION,
            total_sources=int(source["total"] or 0),
            total_bytes=int(source["bytes"] or 0),
            sources_present=int(source["present"] or 0),
            sources_missing=int(source["missing"] or 0),
            processed=int(source["processed"] or 0),
            pending=int(source["pending"] or 0),
            errors=int(source["errors"] or 0),
            unsupported=int(source["unsupported"] or 0),
            needs_ocr=int(source["needs_ocr"] or 0),
            needs_transcription=int(source["needs_transcription"] or 0),
            chunks=chunks,
            embeddings=embeddings,
            knowledge_units=knowledge,
            knowledge_by_status={row["status"]: row["total"] for row in knowledge_rows},
            jobs_by_status={row["state"]: row["total"] for row in job_rows},
            capabilities=self.capabilities(
                lm_studio_available=lm_studio_available, installed_models=installed_models
            ),
            semantic_index=SemanticIndexSummary(
                model=embedding_model or None,
                model_digest=semantic["model_digest"],
                indexed=int(semantic["indexed_count"] or 0),
                pending=max(0, int(eligible or 0) - int(semantic["indexed_count"] or 0)),
                failed=int(semantic["failed_count"] or 0),
                stale=int(semantic["stale_count"] or 0),
                excluded=int(excluded or 0),
                dimension=semantic["dimension"],
                normalization_version="embeddinggemma-retrieval.v1",
            ),
            latest_inventory=InventoryReport.model_validate_json(inventory_row["report_json"])
            if inventory_row
            else None,
        )

    def list_sources(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SourceRead]:
        clauses: list[str] = []
        parameters: list[object] = []
        if status:
            clauses.append("s.status=?")
            parameters.append(status)
        if kind:
            clauses.append("s.kind=?")
            parameters.append(kind)
        if query:
            clauses.append("(s.name LIKE ? ESCAPE '\\' OR s.current_path LIKE ? ESCAPE '\\')")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters.extend([f"%{escaped}%", f"%{escaped}%"])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        embedding_model = self.settings.educational_library_embedding_model.strip()
        query_parameters = [embedding_model, embedding_model, *parameters, limit, offset]
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT s.*,coalesce(sv.version_number,0) current_version,"
                "(SELECT count(*) FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
                "WHERE c.source_version_id=s.current_version_id AND e.model=? "
                "AND e.status='indexed') semantic_indexed_chunks,"
                "(SELECT count(*) FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
                "WHERE c.source_version_id=s.current_version_id AND e.model=? "
                "AND e.status='failed') semantic_failed_chunks FROM sources s "
                "LEFT JOIN source_versions sv ON sv.id=s.current_version_id"
                + where
                + " ORDER BY s.priority DESC,s.name COLLATE NOCASE LIMIT ? OFFSET ?",
                query_parameters,
            ).fetchall()
        return [self._source_read(row) for row in rows]

    def get_source(self, source_id: str) -> SourceRead:
        embedding_model = self.settings.educational_library_embedding_model.strip()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT s.*,coalesce(sv.version_number,0) current_version,"
                "(SELECT count(*) FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
                "WHERE c.source_version_id=s.current_version_id AND e.model=? "
                "AND e.status='indexed') semantic_indexed_chunks,"
                "(SELECT count(*) FROM chunks c JOIN embeddings e ON e.chunk_id=c.id "
                "WHERE c.source_version_id=s.current_version_id AND e.model=? "
                "AND e.status='failed') semantic_failed_chunks FROM sources s "
                "LEFT JOIN source_versions sv ON sv.id=s.current_version_id WHERE s.id=?",
                (embedding_model, embedding_model, source_id),
            ).fetchone()
        if not row:
            raise LibraryNotFoundError("La fuente no existe.")
        return self._source_read(row)

    def source_versions(self, source_id: str) -> list[SourceVersionRead]:
        self.get_source(source_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_versions WHERE source_id=? ORDER BY version_number DESC",
                (source_id,),
            ).fetchall()
        return [
            SourceVersionRead(
                id=row["id"],
                source_id=row["source_id"],
                version_number=row["version_number"],
                content_hash=row["content_hash"],
                size_bytes=row["size_bytes"],
                extractor=row["extractor"],
                extractor_version=row["extractor_version"],
                processing_state=row["processing_state"],
                error_code=row["error_code"],
                statistics=json_load(row["statistics_json"], {}),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def source_chunks(self, source_id: str, *, limit: int = 50, offset: int = 0) -> list[ChunkRead]:
        source = self.get_source(source_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.*,sv.version_number FROM chunks c JOIN source_versions sv "
                "ON sv.id=c.source_version_id JOIN sources s ON s.id=sv.source_id "
                "WHERE s.id=? AND sv.id=s.current_version_id ORDER BY c.sequence LIMIT ? OFFSET ?",
                (source_id, limit, offset),
            ).fetchall()
        return [
            ChunkRead(
                id=row["id"],
                source_id=source_id,
                source_version_id=row["source_version_id"],
                source_name=source.name,
                source_path=source.current_path,
                text=row["text"],
                title=row["title"],
                page_start=row["page_start"],
                page_end=row["page_end"],
                start_seconds=row["start_seconds"],
                end_seconds=row["end_seconds"],
                level=row["cefr_level"],
                topics=json_load(row["topics_json"], []),
                review_status=source.review_status,
                rights=source.rights,
                source_version=row["version_number"],
                content_role=row["content_role"],
            )
            for row in rows
        ]

    def latest_inventory(self) -> InventoryReport:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT report_json FROM inventory_reports ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            raise LibraryNotFoundError("Todavía no existe un inventario de biblioteca.")
        return InventoryReport.model_validate_json(row["report_json"])

    def update_source(self, source_id: str, update: SourceUpdateRequest) -> SourceRead:
        allowed = {
            "rights": lambda value: value.value,
            "priority": lambda value: value,
            "editorial_confidence": lambda value: value,
            "review_status": lambda value: value,
            "language": lambda value: value,
            "cefr_level": lambda value: value,
            "topics": json_dump,
            "canonical_title": lambda value: value,
            "display_alias": lambda value: value,
            "author": lambda value: value,
            "publisher": lambda value: value,
            "edition": lambda value: value,
            "cefr_min": lambda value: value,
            "cefr_max": lambda value: value,
            "pedagogical_role": lambda value: value.value,
            "editorial_status": lambda value: value.value,
            "metadata_origin": lambda value: value.value,
            "metadata_confidence": lambda value: value,
            "editorial_notes": lambda value: value,
            "related_source_id": lambda value: value,
        }
        columns = {"topics": "topics_json"}
        assignments: list[str] = []
        values: list[object] = []
        for field in update.model_fields_set:
            value = getattr(update, field)
            if value is None:
                continue
            assignments.append(f"{columns.get(field, field)}=?")
            values.append(allowed[field](value))
        if not assignments:
            raise LibraryContractError("No hay cambios de fuente aplicables.")
        values.append(source_id)
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                f"UPDATE sources SET {','.join(assignments)} WHERE id=?", values
            )
            if cursor.rowcount != 1:
                raise LibraryNotFoundError("La fuente no existe.")
        return self.get_source(source_id)

    def exclude_source(self, source_id: str) -> SourceRead:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE sources SET excluded=1,status='excluded' WHERE id=?", (source_id,)
            )
            if cursor.rowcount != 1:
                raise LibraryNotFoundError("La fuente no existe.")
        return self.get_source(source_id)

    def reprocess_source(self, source_id: str) -> SourceRead:
        source = self.get_source(source_id)
        root = self._ensure_source_root()
        path = (root / source.current_path).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise LibraryContractError("La ruta catalogada está fuera de la raíz.") from exc
        if not path.is_file() or path.is_symlink():
            raise LibraryContractError("La fuente original ya no está disponible.")
        stat = path.stat()
        entry = InventoryEntry(
            path=path,
            relative_path=source.current_path,
            name=path.name,
            extension=path.suffix.lower(),
            kind=source.kind,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            is_hidden=False,
            is_ignored=False,
            is_symlink=False,
        )
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT current_version_id FROM sources WHERE id=?", (source_id,)
            ).fetchone()
        self._process_version(entry, source_id, row["current_version_id"])
        return self.get_source(source_id)

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

    def supported_formats(self) -> list[str]:
        return sorted(supported_extensions())

    def database_integrity(self) -> tuple[str, list[tuple[object, ...]]]:
        return self.database.integrity()
