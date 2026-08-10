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

from llc_api.core.config import Settings, get_settings

from .chunking import CHUNKER_VERSION, chunk_sections
from .database import LIBRARY_SCHEMA_VERSION, LibraryDatabase
from .extractors import extract, supported_extensions
from .inventory import InventoryEntry, collect_inventory, sha256_file
from .schemas import (
    ChunkRead,
    DocumentInventoryResult,
    DocumentVersionRead,
    InventoryChangeRead,
    InventoryReport,
    JobRead,
    JobState,
    LaboratorySourceDetail,
    LaboratorySourceRead,
    LaboratorySummary,
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
    def __init__(self, settings: Settings | None = None, *, recover_interrupted: bool = True):
        self.settings = settings or get_settings()
        self.root = self.settings.educational_materials_dir
        self.runtime = self.settings.educational_library_runtime_dir
        self.database = LibraryDatabase(self.settings.educational_library_database_path)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.schema_version = self.database.migrate()
        if recover_interrupted:
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
                now = utc_text()
                connection.execute(
                    "UPDATE processing_jobs SET state='interrupted',updated_at=?,"
                    "error_code='process_restarted' WHERE state='running' AND kind='scan'",
                    (now,),
                )
                has_runs = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='document_processing_runs'"
                ).fetchone()
                if has_runs:
                    connection.execute(
                        "UPDATE processing_jobs SET state='interrupted',updated_at=?,"
                        "error_code='process_restarted' WHERE state='running' "
                        "AND document_run_id IS NOT NULL",
                        (now,),
                    )
                    connection.execute(
                        "UPDATE document_run_stages SET state='failed',updated_at=?,"
                        "completed_at=?,error_code='process_restarted',"
                        "error_detail='La etapa se interrumpió al reiniciar el proceso.' "
                        "WHERE state='running'",
                        (now, now),
                    )
                    interrupted = connection.execute(
                        "SELECT id,state FROM document_processing_runs WHERE state='running'"
                    ).fetchall()
                    for run in interrupted:
                        connection.execute(
                            "UPDATE document_processing_runs SET state='paused',resumable=1,"
                            "updated_at=?,error_code='process_restarted',"
                            "error_detail='La ejecución quedó pausada tras reiniciar el proceso.' "
                            "WHERE id=?",
                            (now, run["id"]),
                        )
                        connection.execute(
                            "INSERT INTO document_run_events("
                            "id,run_id,event_type,from_state,to_state,detail_json,created_at"
                            ") VALUES (?,?,?,'running','paused','{}',?)",
                            (
                                str(uuid4()),
                                run["id"],
                                "process_restart_recovery",
                                now,
                            ),
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
        self._recover_interrupted_jobs()
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
                "SELECT s.*, av.processing_state AS version_state,"
                "lv.processing_state AS latest_version_state FROM sources s "
                "LEFT JOIN source_versions av ON av.id=s.current_version_id "
                "LEFT JOIN source_versions lv ON lv.id=s.latest_version_id "
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
                if (
                    process_documents
                    and row["latest_version_id"] is not None
                    and row["latest_version_state"] in {"pending", "error"}
                ):
                    state = self._process_version(entry, row["id"], row["latest_version_id"])
                    self._activate_initial_version(row["id"], row["latest_version_id"], state)
                    return "unchanged", state
                return "unchanged", None
            source_id = row["id"]
            version_id = self._create_version(
                source_id,
                content_hash,
                entry,
                previous_version_id=row["latest_version_id"] or row["current_version_id"],
            )
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
            self._activate_initial_version(source_id, version_id, state)
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
        now = utc_text()
        with self.database.transaction(immediate=True) as connection:
            version = connection.execute(
                "SELECT coalesce(max(version_number),0)+1 FROM source_versions WHERE source_id=?",
                (source_id,),
            ).fetchone()[0]
            cursor = connection.execute(
                "INSERT INTO source_versions(source_id,version_number,content_hash,size_bytes,"
                "mtime_ns,processing_state,previous_version_id,created_at,observed_path,observed_name,"
                "detected_at,document_state,availability_state,extraction_state,chunk_state,"
                "embedding_state,activation_state,is_active,version_provenance,change_reason) "
                "VALUES (?,?,?,?,?,?,?, ?,?,?,?,'candidate','present','pending','pending','pending',"
                "'candidate',0,'scanner',?)",
                (
                    source_id,
                    version,
                    content_hash,
                    entry.size_bytes,
                    entry.mtime_ns,
                    ProcessingState.PENDING.value,
                    previous_version_id,
                    now,
                    entry.relative_path,
                    entry.name,
                    now,
                    (
                        "initial_file_detected"
                        if previous_version_id is None
                        else "content_hash_changed_at_same_path"
                    ),
                ),
            )
            version_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE sources SET latest_version_id=?, current_hash=?, size_bytes=?, mtime_ns=?, "
                "name=?, kind=?, format=?, status='present', processing_state='pending', "
                "document_state='candidate',last_seen_at=?, missing_since=NULL WHERE id=?",
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

    def _activate_initial_version(
        self, source_id: str, version_id: int, state: ProcessingState
    ) -> None:
        if state not in {ProcessingState.PROCESSED, ProcessingState.PARTIAL}:
            return
        with self.database.transaction(immediate=True) as connection:
            source = connection.execute(
                "SELECT current_version_id FROM sources WHERE id=?", (source_id,)
            ).fetchone()
            if source is None or source["current_version_id"] is not None:
                return
            connection.execute(
                "UPDATE source_versions SET is_active=1,activation_state='active',"
                "document_state='active' WHERE id=? AND source_id=?",
                (version_id, source_id),
            )
            connection.execute(
                "UPDATE sources SET current_version_id=?,document_state='active',"
                "processing_state=? WHERE id=?",
                (version_id, state.value, source_id),
            )

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

    def detect_document_changes(self) -> DocumentInventoryResult:
        """Inventory files and hashes without extracting or changing the active corpus."""
        root = self._ensure_source_root()
        snapshot = collect_inventory(root, confirm_duplicates=False)
        entries = [
            entry for entry in snapshot.entries if not entry.is_ignored and not entry.is_symlink
        ]
        observed = [(entry, sha256_file(entry.path)) for entry in entries]
        job_id = str(uuid4())
        generated_at = utc_text()
        changes: list[InventoryChangeRead] = []
        counters: Counter[str] = Counter()
        seen_source_ids: set[str] = set()

        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO processing_jobs(id,kind,state,priority,payload_json,progress_current,"
                    "progress_total,attempts,created_at,started_at,updated_at) "
                    "VALUES (?, 'document_inventory', 'running', 0, '{}', 0, ?, 1, ?, ?, ?)",
                    (job_id, len(observed), generated_at, generated_at, generated_at),
                )
                for index, (entry, content_hash) in enumerate(observed, start=1):
                    change = self._apply_inventory_entry(
                        connection, entry, content_hash, seen_source_ids, generated_at
                    )
                    counters[change.outcome] += 1
                    if change.outcome in {"modified", "new", "duplicate", "manual_review"}:
                        counters["candidate_versions_created"] += 1
                    if change.outcome != "unchanged":
                        changes.append(change)
                    connection.execute(
                        "UPDATE processing_jobs SET progress_current=?,cursor=?,updated_at=? WHERE id=?",
                        (index, entry.relative_path, utc_text(), job_id),
                    )

                rows = connection.execute(
                    "SELECT id,current_path,name,current_hash,current_version_id,latest_version_id "
                    "FROM sources WHERE excluded=0 AND status!='excluded'"
                ).fetchall()
                for row in rows:
                    if row["id"] in seen_source_ids:
                        continue
                    connection.execute(
                        "UPDATE sources SET status='missing',document_state='missing',"
                        "missing_since=coalesce(missing_since,?),last_inventory_at=? WHERE id=?",
                        (generated_at, generated_at, row["id"]),
                    )
                    if row["latest_version_id"] is not None:
                        connection.execute(
                            "UPDATE source_versions SET availability_state='missing',"
                            "document_state=CASE WHEN is_active=1 THEN document_state ELSE 'missing' END "
                            "WHERE id=?",
                            (row["latest_version_id"],),
                        )
                    counters["missing"] += 1
                    changes.append(
                        InventoryChangeRead(
                            outcome="missing",
                            source_id=row["id"],
                            relative_path=row["current_path"],
                            title=row["name"],
                            previous_hash=row["current_hash"],
                            current_hash=None,
                            active_version_id=row["current_version_id"],
                            candidate_version_id=(
                                row["latest_version_id"]
                                if row["latest_version_id"] != row["current_version_id"]
                                else None
                            ),
                            message="El archivo no está disponible; se conserva todo su historial.",
                        )
                    )

                result = DocumentInventoryResult(
                    job_id=job_id,
                    generated_at=generated_at,
                    files_scanned=len(observed),
                    unchanged=counters["unchanged"],
                    modified=counters["modified"],
                    new=counters["new"],
                    renamed=counters["renamed"],
                    missing=counters["missing"],
                    duplicates=counters["duplicate"],
                    manual_review=counters["manual_review"],
                    candidate_versions_created=counters["candidate_versions_created"],
                    changes=changes,
                )
                connection.execute(
                    "UPDATE processing_jobs SET state='completed',progress_current=?,updated_at=?,"
                    "completed_at=? WHERE id=?",
                    (len(observed), generated_at, generated_at, job_id),
                )
                connection.execute(
                    "INSERT INTO document_inventory_runs(id,job_id,generated_at,result_json) "
                    "VALUES (?,?,?,?)",
                    (str(uuid4()), job_id, generated_at, result.model_dump_json()),
                )
            return result
        except Exception as exc:
            failed_at = utc_text()
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO processing_jobs(id,kind,state,priority,payload_json,progress_current,"
                    "progress_total,attempts,error_code,error_detail,created_at,started_at,updated_at,"
                    "completed_at) VALUES (?, 'document_inventory', 'failed', 0, '{}', 0, ?, 1, ?, ?,"
                    "?,?,?,?)",
                    (
                        job_id,
                        len(observed),
                        type(exc).__name__,
                        str(exc)[:1_000],
                        generated_at,
                        generated_at,
                        failed_at,
                        failed_at,
                    ),
                )
            raise

    def _apply_inventory_entry(
        self,
        connection: sqlite3.Connection,
        entry: InventoryEntry,
        content_hash: str,
        seen_source_ids: set[str],
        detected_at: str,
    ) -> InventoryChangeRead:
        row = connection.execute(
            "SELECT * FROM sources WHERE current_path=?", (entry.relative_path,)
        ).fetchone()
        if row is not None:
            seen_source_ids.add(str(row["id"]))
            existing = connection.execute(
                "SELECT * FROM source_versions WHERE source_id=? AND content_hash=?",
                (row["id"], content_hash),
            ).fetchone()
            if existing is not None:
                active = existing["id"] == row["current_version_id"]
                if not active and existing["activation_state"] == "historical":
                    connection.execute(
                        "UPDATE source_versions SET activation_state='candidate',"
                        "document_state='candidate',availability_state='present' WHERE id=?",
                        (existing["id"],),
                    )
                else:
                    connection.execute(
                        "UPDATE source_versions SET availability_state='present' WHERE id=?",
                        (existing["id"],),
                    )
                connection.execute(
                    "UPDATE sources SET current_hash=?,size_bytes=?,mtime_ns=?,name=?,kind=?,format=?,"
                    "latest_version_id=?,status='present',missing_since=NULL,last_seen_at=?,"
                    "last_inventory_at=?,document_state=? WHERE id=?",
                    (
                        content_hash,
                        entry.size_bytes,
                        entry.mtime_ns,
                        entry.name,
                        entry.kind.value,
                        entry.extension or "[none]",
                        existing["id"],
                        detected_at,
                        detected_at,
                        "failed"
                        if existing["processing_state"] == "error"
                        else "needs_ocr"
                        if existing["processing_state"] == "needs_ocr"
                        else "active"
                        if active
                        else existing["document_state"],
                        row["id"],
                    ),
                )
                return InventoryChangeRead(
                    outcome="unchanged",
                    source_id=row["id"],
                    relative_path=entry.relative_path,
                    title=row["name"],
                    previous_hash=row["current_hash"],
                    current_hash=content_hash,
                    active_version_id=row["current_version_id"],
                    candidate_version_id=None if active else existing["id"],
                    message="El hash ya estaba registrado; no se creó otra versión.",
                )

            version_id = self._insert_candidate_version(
                connection,
                source_id=row["id"],
                entry=entry,
                content_hash=content_hash,
                previous_version_id=row["latest_version_id"] or row["current_version_id"],
                detected_at=detected_at,
                change_reason="content_hash_changed_at_same_path",
            )
            previous_candidate = connection.execute(
                "SELECT id FROM source_versions WHERE source_id=? AND id<>? "
                "AND activation_state='candidate' AND is_active=0 ORDER BY version_number DESC LIMIT 1",
                (row["id"], version_id),
            ).fetchone()
            if previous_candidate is not None:
                connection.execute(
                    "UPDATE source_versions SET activation_state='superseded',"
                    "document_state='historical',superseded_at=?,superseded_by_version_id=? "
                    "WHERE id=?",
                    (detected_at, version_id, previous_candidate["id"]),
                )
            connection.execute(
                "UPDATE sources SET current_hash=?,size_bytes=?,mtime_ns=?,name=?,kind=?,format=?,"
                "latest_version_id=?,status='present',processing_state='pending',"
                "document_state='candidate',missing_since=NULL,last_seen_at=?,last_inventory_at=? "
                "WHERE id=?",
                (
                    content_hash,
                    entry.size_bytes,
                    entry.mtime_ns,
                    entry.name,
                    entry.kind.value,
                    entry.extension or "[none]",
                    version_id,
                    detected_at,
                    detected_at,
                    row["id"],
                ),
            )
            return InventoryChangeRead(
                outcome="modified",
                source_id=row["id"],
                relative_path=entry.relative_path,
                title=row["name"],
                previous_hash=row["current_hash"],
                current_hash=content_hash,
                active_version_id=row["current_version_id"],
                candidate_version_id=version_id,
                message="Nueva versión candidata; la versión activa no cambió.",
            )

        candidates = connection.execute(
            "SELECT s.* FROM sources s JOIN source_versions sv "
            "ON sv.id=s.latest_version_id WHERE sv.content_hash=? AND s.excluded=0",
            (content_hash,),
        ).fetchall()
        rename_candidates = []
        for candidate in candidates:
            if candidate["id"] in seen_source_ids:
                continue
            previous_path = (self.root / candidate["current_path"]).resolve(strict=False)
            try:
                previous_path.relative_to(self.root.resolve(strict=True))
            except ValueError:
                continue
            if not previous_path.exists():
                rename_candidates.append(candidate)
        if len(rename_candidates) == 1:
            candidate = rename_candidates[0]
            seen_source_ids.add(str(candidate["id"]))
            connection.execute(
                "UPDATE sources SET current_path=?,name=?,kind=?,format=?,size_bytes=?,mtime_ns=?,"
                "current_hash=?,status='present',missing_since=NULL,last_seen_at=?,last_inventory_at=? "
                "WHERE id=?",
                (
                    entry.relative_path,
                    entry.name,
                    entry.kind.value,
                    entry.extension or "[none]",
                    entry.size_bytes,
                    entry.mtime_ns,
                    content_hash,
                    detected_at,
                    detected_at,
                    candidate["id"],
                ),
            )
            connection.execute(
                "UPDATE source_versions SET availability_state='present' WHERE id=?",
                (candidate["latest_version_id"],),
            )
            return InventoryChangeRead(
                outcome="renamed",
                source_id=candidate["id"],
                relative_path=entry.relative_path,
                title=candidate["name"],
                previous_hash=content_hash,
                current_hash=content_hash,
                active_version_id=candidate["current_version_id"],
                candidate_version_id=(
                    candidate["latest_version_id"]
                    if candidate["latest_version_id"] != candidate["current_version_id"]
                    else None
                ),
                message="Se registró la ubicación nueva conservando la identidad y el historial.",
            )

        ambiguous = len(rename_candidates) > 1
        duplicate_of = candidates[0]["id"] if candidates and not ambiguous else None
        source_id = str(uuid4())
        connection.execute(
            "INSERT INTO sources(id,current_path,name,kind,format,size_bytes,mtime_ns,current_hash,"
            "status,processing_state,duplicate_of_source_id,first_seen_at,last_seen_at,"
            "document_state,needs_manual_review,last_inventory_at) "
            "VALUES (?,?,?,?,?,?,?,?, 'present','pending',?,?,?,?,?,?)",
            (
                source_id,
                entry.relative_path,
                entry.name,
                entry.kind.value,
                entry.extension or "[none]",
                entry.size_bytes,
                entry.mtime_ns,
                content_hash,
                duplicate_of,
                detected_at,
                detected_at,
                "manual_review" if ambiguous else "candidate",
                int(ambiguous),
                detected_at,
            ),
        )
        version_id = self._insert_candidate_version(
            connection,
            source_id=source_id,
            entry=entry,
            content_hash=content_hash,
            previous_version_id=None,
            detected_at=detected_at,
            change_reason="new_file_detected",
            document_state="manual_review" if ambiguous else "candidate",
        )
        connection.execute(
            "UPDATE sources SET latest_version_id=? WHERE id=?", (version_id, source_id)
        )
        seen_source_ids.add(source_id)
        outcome = "manual_review" if ambiguous else "duplicate" if duplicate_of else "new"
        return InventoryChangeRead(
            outcome=outcome,
            source_id=source_id,
            relative_path=entry.relative_path,
            title=entry.name,
            previous_hash=None,
            current_hash=content_hash,
            active_version_id=None,
            candidate_version_id=version_id,
            message=(
                "La coincidencia es ambigua; se creó un caso separado para revisión manual."
                if ambiguous
                else "Se detectó un duplicado físico sin fusionar identidades."
                if duplicate_of
                else "Nueva fuente con una versión candidata pendiente de procesamiento."
            ),
        )

    @staticmethod
    def _insert_candidate_version(
        connection: sqlite3.Connection,
        *,
        source_id: str,
        entry: InventoryEntry,
        content_hash: str,
        previous_version_id: int | None,
        detected_at: str,
        change_reason: str,
        document_state: str = "candidate",
    ) -> int:
        version_number = int(
            connection.execute(
                "SELECT coalesce(max(version_number),0)+1 FROM source_versions WHERE source_id=?",
                (source_id,),
            ).fetchone()[0]
        )
        cursor = connection.execute(
            "INSERT INTO source_versions(source_id,version_number,content_hash,size_bytes,mtime_ns,"
            "processing_state,previous_version_id,created_at,observed_path,observed_name,detected_at,"
            "document_state,availability_state,extraction_state,chunk_state,embedding_state,"
            "activation_state,is_active,version_provenance,change_reason) "
            "VALUES (?,?,?,?,?,'pending',?,?,?,?,?,?,'present','pending','pending','pending',"
            "'candidate',0,'inventory',?)",
            (
                source_id,
                version_number,
                content_hash,
                entry.size_bytes,
                entry.mtime_ns,
                previous_version_id,
                detected_at,
                entry.relative_path,
                entry.name,
                detected_at,
                document_state,
                change_reason,
            ),
        )
        return int(cursor.lastrowid)

    def latest_document_inventory(self) -> DocumentInventoryResult:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM document_inventory_runs ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise LibraryNotFoundError("Todavía no existe un inventario documental.")
        return DocumentInventoryResult.model_validate_json(row["result_json"])

    def laboratory_summary(self) -> LaboratorySummary:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT count(*) catalogued_sources,"
                "sum(CASE WHEN current_version_id IS NOT NULL AND EXISTS("
                "SELECT 1 FROM chunks c WHERE c.source_version_id=sources.current_version_id"
                ") THEN 1 ELSE 0 END) recoverable_sources,"
                "sum(CASE WHEN current_version_id IS NULL OR NOT EXISTS("
                "SELECT 1 FROM chunks c WHERE c.source_version_id=sources.current_version_id"
                ") THEN 1 ELSE 0 END) sources_without_content,"
                "(SELECT count(*) FROM source_versions WHERE activation_state='candidate') "
                "candidate_versions,"
                "sum(CASE WHEN EXISTS(SELECT 1 FROM source_versions sv WHERE sv.source_id=sources.id "
                "AND sv.extraction_state='needs_ocr' AND sv.id=sources.latest_version_id) "
                "THEN 1 ELSE 0 END) needs_ocr_sources,"
                "sum(CASE WHEN document_state='failed' THEN 1 ELSE 0 END) error_sources,"
                "sum(CASE WHEN status='missing' THEN 1 ELSE 0 END) missing_files "
                "FROM sources"
            ).fetchone()
            pending_jobs = connection.execute(
                "SELECT count(*) FROM processing_jobs WHERE state IN ('queued','running','paused')"
            ).fetchone()[0]
        return LaboratorySummary(
            catalogued_sources=int(row["catalogued_sources"] or 0),
            recoverable_sources=int(row["recoverable_sources"] or 0),
            sources_without_content=int(row["sources_without_content"] or 0),
            candidate_versions=int(row["candidate_versions"] or 0),
            needs_ocr_sources=int(row["needs_ocr_sources"] or 0),
            error_sources=int(row["error_sources"] or 0),
            missing_files=int(row["missing_files"] or 0),
            pending_jobs=int(pending_jobs or 0),
        )

    def laboratory_sources(self, *, filter_name: str = "all") -> list[LaboratorySourceRead]:
        clauses = {
            "all": "",
            "active": " AND s.current_version_id IS NOT NULL",
            "candidates": " AND s.latest_version_id IS NOT s.current_version_id",
            "needs_ocr": " AND lv.extraction_state='needs_ocr'",
            "errors": " AND s.document_state='failed'",
            "without_content": " AND (s.current_version_id IS NULL OR ac.active_chunks=0)",
            "missing": " AND s.status='missing'",
        }
        if filter_name not in clauses:
            raise LibraryContractError("El filtro documental no es válido.")
        with self.database.connect() as connection:
            rows = connection.execute(
                self._laboratory_source_query()
                + clauses[filter_name]
                + " ORDER BY s.priority DESC,s.name COLLATE NOCASE"
            ).fetchall()
        return [self._laboratory_source_read(row) for row in rows]

    def laboratory_source(self, source_id: str) -> LaboratorySourceDetail:
        with self.database.connect() as connection:
            row = connection.execute(
                self._laboratory_source_query() + " AND s.id=?", (source_id,)
            ).fetchone()
            if row is None:
                raise LibraryNotFoundError("La fuente no existe.")
            versions = self._document_versions(connection, source_id)
        base = self._laboratory_source_read(row)
        return LaboratorySourceDetail(
            **base.model_dump(),
            canonical_title=row["canonical_title"],
            display_alias=row["display_alias"],
            author=row["author"],
            publisher=row["publisher"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            versions=versions,
        )

    def document_versions(self, source_id: str) -> list[DocumentVersionRead]:
        with self.database.connect() as connection:
            exists = connection.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone()
            if exists is None:
                raise LibraryNotFoundError("La fuente no existe.")
            return self._document_versions(connection, source_id)

    def document_version(self, version_id: int) -> DocumentVersionRead:
        with self.database.connect() as connection:
            row = connection.execute(
                self._document_version_query() + " WHERE sv.id=?", (version_id,)
            ).fetchone()
        if row is None:
            raise LibraryNotFoundError("La versión documental no existe.")
        return self._document_version_read(row)

    def _document_versions(
        self, connection: sqlite3.Connection, source_id: str
    ) -> list[DocumentVersionRead]:
        rows = connection.execute(
            self._document_version_query()
            + " WHERE sv.source_id=? ORDER BY sv.version_number DESC",
            (source_id,),
        ).fetchall()
        return [self._document_version_read(row) for row in rows]

    @staticmethod
    def _document_version_query() -> str:
        return (
            "SELECT sv.*,(SELECT count(*) FROM chunks c WHERE c.source_version_id=sv.id) chunks,"
            "(SELECT count(*) FROM embeddings e WHERE e.source_version_id=sv.id) embeddings "
            "FROM source_versions sv"
        )

    @staticmethod
    def _document_version_read(row: sqlite3.Row) -> DocumentVersionRead:
        return DocumentVersionRead(
            id=row["id"],
            source_id=row["source_id"],
            version_number=row["version_number"],
            content_hash=row["content_hash"],
            size_bytes=row["size_bytes"],
            mtime_ns=row["mtime_ns"],
            observed_path=row["observed_path"],
            observed_name=row["observed_name"],
            detected_at=row["detected_at"] or row["created_at"],
            page_count=row["page_count"],
            document_state=row["document_state"],
            availability_state=row["availability_state"],
            extraction_state=row["extraction_state"],
            chunk_state=row["chunk_state"],
            embedding_state=row["embedding_state"],
            activation_state=row["activation_state"],
            is_active=bool(row["is_active"]),
            extractor=row["extractor"],
            extractor_version=row["extractor_version"],
            extraction_tool=row["extraction_tool"],
            extraction_tool_version=row["extraction_tool_version"],
            ocr_tool=row["ocr_tool"],
            ocr_tool_version=row["ocr_tool_version"],
            ocr_languages=json_load(row["ocr_languages_json"], []),
            technical_metadata=json_load(row["technical_metadata_json"], {}),
            provenance=row["version_provenance"],
            change_reason=row["change_reason"],
            previous_version_id=row["previous_version_id"],
            error_code=row["error_code"],
            error_detail=row["error_detail"],
            statistics=json_load(row["statistics_json"], {}),
            processed_at=row["processed_at"],
            chunks=int(row["chunks"] or 0),
            embeddings=int(row["embeddings"] or 0),
        )

    @staticmethod
    def _laboratory_source_query() -> str:
        return (
            "SELECT s.*,av.version_number active_version_number,"
            "lv.version_number latest_version_number,lv.page_count,"
            "lv.extraction_state latest_extraction_state,lv.error_code,"
            "coalesce(ac.active_chunks,0) active_chunks,"
            "coalesce(ae.active_embeddings,0) active_embeddings "
            "FROM sources s "
            "LEFT JOIN source_versions av ON av.id=s.current_version_id "
            "LEFT JOIN source_versions lv ON lv.id=s.latest_version_id "
            "LEFT JOIN (SELECT source_version_id,count(*) active_chunks FROM chunks "
            "GROUP BY source_version_id) ac ON ac.source_version_id=s.current_version_id "
            "LEFT JOIN (SELECT source_version_id,count(*) active_embeddings FROM embeddings "
            "GROUP BY source_version_id) ae ON ae.source_version_id=s.current_version_id "
            "WHERE 1=1"
        )

    @staticmethod
    def _laboratory_source_read(row: sqlite3.Row) -> LaboratorySourceRead:
        path = Path(row["current_path"])
        collection = path.parts[0] if len(path.parts) > 1 else None
        return LaboratorySourceRead(
            id=row["id"],
            title=row["display_alias"] or row["canonical_title"] or row["name"],
            collection=collection,
            format=row["format"],
            current_path=row["current_path"],
            source_status=row["status"],
            document_state=row["document_state"],
            needs_manual_review=bool(row["needs_manual_review"]),
            active_version_id=row["current_version_id"],
            active_version_number=row["active_version_number"],
            latest_version_id=row["latest_version_id"],
            latest_version_number=row["latest_version_number"],
            page_count=row["page_count"],
            active_chunks=int(row["active_chunks"] or 0),
            active_embeddings=int(row["active_embeddings"] or 0),
            needs_ocr=row["latest_extraction_state"] == "needs_ocr",
            error_code=row["error_code"],
            change_pending=(
                row["latest_version_id"] is not None
                and row["latest_version_id"] != row["current_version_id"]
            ),
        )

    def reconcile_latest_as_candidate(self, source_id: str) -> LaboratorySourceDetail:
        """Repair a legacy silent replacement without deleting extracted artifacts."""
        with self.database.transaction(immediate=True) as connection:
            source = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if source is None or source["latest_version_id"] is None:
                raise LibraryNotFoundError("La fuente o su última versión no existe.")
            latest_id = int(source["latest_version_id"])
            latest = connection.execute(
                "SELECT * FROM source_versions WHERE id=?", (latest_id,)
            ).fetchone()
            previous = connection.execute(
                "SELECT sv.id FROM source_versions sv WHERE sv.source_id=? AND sv.id!=? "
                "AND EXISTS(SELECT 1 FROM chunks c WHERE c.source_version_id=sv.id) "
                "ORDER BY sv.version_number DESC LIMIT 1",
                (source_id, latest_id),
            ).fetchone()
            active_id = int(previous["id"]) if previous else None
            connection.execute(
                "UPDATE source_versions SET is_active=0,"
                "activation_state=CASE WHEN id=? THEN 'candidate' ELSE 'historical' END,"
                "document_state=CASE WHEN id=? THEN ? ELSE 'historical' END "
                "WHERE source_id=?",
                (
                    latest_id,
                    latest_id,
                    "pending_validation"
                    if latest["processing_state"] in {"processed", "partial"}
                    else "candidate",
                    source_id,
                ),
            )
            if active_id is not None:
                connection.execute(
                    "UPDATE source_versions SET is_active=1,activation_state='active',"
                    "document_state='active' WHERE id=?",
                    (active_id,),
                )
            connection.execute(
                "UPDATE sources SET current_version_id=?,document_state=?,processing_state=? "
                "WHERE id=?",
                (
                    active_id,
                    "pending_validation"
                    if latest["processing_state"] in {"processed", "partial"}
                    else "candidate",
                    latest["processing_state"],
                    source_id,
                ),
            )
        return self.laboratory_source(source_id)

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
                "UPDATE source_versions SET processing_state='processing',document_state='processing',"
                "extraction_state='processing',error_code=NULL,error_detail=NULL WHERE id=?",
                (version_id,),
            )
            connection.execute(
                "UPDATE sources SET processing_state='processing',"
                "document_state=CASE WHEN latest_version_id=? THEN 'processing' "
                "ELSE document_state END WHERE id=?",
                (version_id, source_id),
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
                    "statistics_json=?,processed_at=?,page_count=?,document_state=?,"
                    "extraction_state=?,chunk_state=?,extraction_tool=?,"
                    "extraction_tool_version=?,technical_metadata_json=? WHERE id=?",
                    (
                        result.extractor,
                        result.extractor_version,
                        result.processing_state.value,
                        json_dump(statistics),
                        utc_text(),
                        result.page_count,
                        (
                            "needs_ocr"
                            if result.processing_state == ProcessingState.NEEDS_OCR
                            else "failed"
                            if result.processing_state == ProcessingState.ERROR
                            else "pending_validation"
                        ),
                        (
                            "needs_ocr"
                            if result.processing_state == ProcessingState.NEEDS_OCR
                            else "failed"
                            if result.processing_state == ProcessingState.ERROR
                            else "unsupported"
                            if result.processing_state == ProcessingState.UNSUPPORTED
                            else "extracted"
                        ),
                        "available" if chunks else "pending",
                        result.extractor,
                        result.extractor_version,
                        json_dump(result.metadata),
                        version_id,
                    ),
                )
                connection.execute(
                    "UPDATE sources SET processing_state=?,language=coalesce(language,?),"
                    "cefr_level=coalesce(cefr_level,?),topics_json=CASE WHEN topics_json='[]' THEN ? "
                    "ELSE topics_json END,author=coalesce(author,?),status=?,"
                    "document_state=CASE WHEN latest_version_id=? THEN ? ELSE document_state END "
                    "WHERE id=?",
                    (
                        result.processing_state.value,
                        languages.most_common(1)[0][0] if languages else result.language,
                        levels.most_common(1)[0][0] if levels else None,
                        json_dump(topics),
                        result.author,
                        SourceStatus.UNSUPPORTED.value
                        if result.processing_state == ProcessingState.UNSUPPORTED
                        else SourceStatus.PRESENT.value,
                        version_id,
                        (
                            "needs_ocr"
                            if result.processing_state == ProcessingState.NEEDS_OCR
                            else "failed"
                            if result.processing_state == ProcessingState.ERROR
                            else "pending_validation"
                        ),
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
                "processed_at=?,document_state='failed',extraction_state='failed' WHERE id=?",
                (error_code, str(exc)[:500], utc_text(), version_id),
            )
            connection.execute(
                "UPDATE sources SET processing_state='error',status='error',"
                "document_state=CASE WHEN latest_version_id=? THEN 'failed' "
                "ELSE document_state END WHERE id=?",
                (version_id, source_id),
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
