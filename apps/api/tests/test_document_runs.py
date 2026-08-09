from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from deutschos_api.core.config import Settings
from deutschos_api.educational_library.database import LibraryDatabase
from deutschos_api.educational_library.dependencies import get_library_service
from deutschos_api.educational_library.runs import (
    DocumentRunService,
    stable_configuration_hash,
)
from deutschos_api.educational_library.schemas import (
    DocumentRunCreate,
    DocumentRunPassCreate,
    LibraryBusyError,
    LibraryContractError,
)
from deutschos_api.educational_library.service import EducationalLibraryService
from deutschos_api.main import app


@pytest.fixture
def run_settings(tmp_path: Path) -> Settings:
    materials = tmp_path / "materials"
    materials.mkdir()
    return Settings(
        database_url="sqlite://",
        educational_materials_dir=materials,
        educational_library_runtime_dir=tmp_path / "runtime",
        educational_library_scan_on_startup=False,
        educational_library_embedding_model="",
    )


@pytest.fixture
def run_library(run_settings: Settings) -> EducationalLibraryService:
    return EducationalLibraryService(run_settings)


def seed_version(
    library: EducationalLibraryService,
    settings: Settings,
    *,
    name: str = "fixture.pdf",
    page_count: int | None = 3,
    content: bytes = b"%PDF-1.4\nsynthetic fixture\n%%EOF",
    source_id: str = "fixture-source",
    version_number: int = 1,
) -> tuple[str, int]:
    path = settings.educational_materials_dir / name
    path.write_bytes(content)
    import hashlib

    content_hash = hashlib.sha256(content).hexdigest()
    now = "2026-07-30T12:00:00+00:00"
    with library.database.transaction(immediate=True) as connection:
        existing = connection.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone()
        if not existing:
            connection.execute(
                "INSERT INTO sources("
                "id,current_path,name,kind,format,size_bytes,mtime_ns,current_hash,status,"
                "processing_state,first_seen_at,last_seen_at,document_state"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    name,
                    name,
                    "document",
                    ".pdf",
                    len(content),
                    path.stat().st_mtime_ns,
                    content_hash,
                    "present",
                    "pending",
                    now,
                    now,
                    "candidate",
                ),
            )
        cursor = connection.execute(
            "INSERT INTO source_versions("
            "source_id,version_number,content_hash,size_bytes,mtime_ns,processing_state,"
            "created_at,observed_path,observed_name,detected_at,page_count,document_state,"
            "availability_state,extraction_state,chunk_state,embedding_state,activation_state,"
            "version_provenance"
            ") VALUES (?,?,?,?,?,'pending',?,?,?,?,?,'candidate','present','pending','pending',"
            "'pending','candidate','fixture')",
            (
                source_id,
                version_number,
                content_hash,
                len(content),
                path.stat().st_mtime_ns,
                now,
                name,
                name,
                now,
                page_count,
            ),
        )
        version_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE sources SET latest_version_id=? WHERE id=?",
            (version_id, source_id),
        )
    return source_id, version_id


def request_for(version_id: int, **overrides) -> DocumentRunCreate:
    payload = {
        "source_version_id": version_id,
        "run_type": "document_analysis",
        "pipeline_version": "document-pipeline.v1",
        "configuration": {"language": "de", "safe_only": True},
        "selection_strategy": "all_pages",
        "reason": "synthetic fixture",
    }
    payload.update(overrides)
    return DocumentRunCreate(**payload)


def test_run_pins_version_hash_lineage_and_configuration(
    run_library: EducationalLibraryService, run_settings: Settings
):
    source_id, version_id = seed_version(run_library, run_settings)
    service = DocumentRunService(run_library.database, run_settings.educational_materials_dir)
    first = service.create_run(request_for(version_id))
    _, second_version = seed_version(
        run_library,
        run_settings,
        name="fixture-v2.pdf",
        content=b"%PDF-1.4\nsecond version\n%%EOF",
        source_id=source_id,
        version_number=2,
    )

    unchanged = service.get_run(first.id)
    assert unchanged.source_version_id == version_id
    assert unchanged.source_version_id != second_version
    assert unchanged.target_hash == first.target_hash
    assert unchanged.configuration_hash == stable_configuration_hash(
        {"language": "de", "safe_only": True}
    )
    assert stable_configuration_hash({"a": 1}) == stable_configuration_hash({"a": 1})
    assert stable_configuration_hash({"a": 1}) != stable_configuration_hash({"a": 2})

    with run_library.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE document_page_stage_results SET state='pending' WHERE run_id=?",
            (first.id,),
        )
    service.run_preflight(first.id)
    service.reconcile_coverage(first.id)
    child = service.create_pass(
        first.id, DocumentRunPassCreate(reason="retry pending fixture pages")
    )
    assert child.parent_run_id == first.id
    assert child.base_run_id == first.id
    assert child.target_hash == first.target_hash
    assert service.get_run(first.id).id == first.id


def test_state_machine_pause_resume_cancel_and_terminal_guards(
    run_library: EducationalLibraryService, run_settings: Settings
):
    _, version_id = seed_version(run_library, run_settings)
    service = DocumentRunService(run_library.database, run_settings.educational_materials_dir)
    run = service.create_run(request_for(version_id))
    completed_preflight = service.run_preflight(run.id)
    assert completed_preflight.state == "running"
    paused = service.pause(run.id)
    assert paused.state == "paused"
    resumed = service.resume(run.id)
    assert resumed.state == "queued"
    cancelled = service.cancel(run.id)
    assert cancelled.state == "cancelled"
    assert next(stage for stage in cancelled.stages if stage.name == "preflight").state == (
        "completed"
    )
    with pytest.raises(LibraryContractError):
        service.resume(run.id)

    completed_run = service.create_run(request_for(version_id, exclusive=False))
    service.run_preflight(completed_run.id)
    completed = service.reconcile_coverage(completed_run.id)
    assert completed.state in {"completed", "completed_with_issues"}
    assert completed.resumable is False
    with pytest.raises(LibraryContractError):
        service.resume(completed_run.id)


def test_incompatible_concurrency_is_blocked(
    run_library: EducationalLibraryService, run_settings: Settings
):
    _, version_id = seed_version(run_library, run_settings)
    service = DocumentRunService(run_library.database, run_settings.educational_materials_dir)
    first = service.create_run(request_for(version_id))
    second = service.create_run(request_for(version_id))
    service.run_preflight(first.id)

    with pytest.raises(LibraryBusyError):
        service.run_preflight(second.id)


def test_preflight_is_safe_and_hash_mismatch_becomes_stale(
    run_library: EducationalLibraryService,
    run_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    _, version_id = seed_version(run_library, run_settings)
    service = DocumentRunService(run_library.database, run_settings.educational_materials_dir)
    run = service.create_run(request_for(version_id))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("forbidden processing capability called")

    monkeypatch.setattr("deutschos_api.educational_library.service.extract", forbidden)
    detail = service.run_preflight(run.id)
    assert next(stage for stage in detail.stages if stage.name == "preflight").state == (
        "completed"
    )
    with run_library.database.connect() as connection:
        assert tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM chunks),(SELECT count(*) FROM embeddings)"
            ).fetchone()
        ) == (0, 0)

    stale_run = service.create_run(request_for(version_id, exclusive=False))
    (run_settings.educational_materials_dir / "fixture.pdf").write_bytes(b"changed")
    stale = service.run_preflight(stale_run.id)
    assert stale.state == "stale"
    assert stale.error_code == "target_hash_mismatch"
    assert "materials" not in (stale.error_detail or "")


def test_coverage_pages_are_version_scoped_idempotent_and_unknown_is_not_zero(
    run_library: EducationalLibraryService, run_settings: Settings
):
    source_id, version_id = seed_version(run_library, run_settings, page_count=3)
    _, second_version = seed_version(
        run_library,
        run_settings,
        name="fixture-v2.pdf",
        page_count=2,
        content=b"%PDF-1.4\nv2\n%%EOF",
        source_id=source_id,
        version_number=2,
    )
    with run_library.database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO page_quality("
            "source_version_id,page_number,extraction_method,character_count,detected_language,"
            "quality,warnings_json,review_status,created_at,updated_at"
            ") VALUES (?,1,'fixture',120,'de','good','[]','user_confirmed','now','now'),"
            "(?,2,'fixture',0,'de','unusable','[\"no_text\"]','unreviewed','now','now')",
            (version_id, version_id),
        )
    service = DocumentRunService(run_library.database, run_settings.educational_materials_dir)
    run = service.create_run(request_for(version_id))
    service.run_preflight(run.id)
    first = service.reconcile_coverage(run.id)
    snapshot_count = len(first.coverage)
    second = service.reconcile_coverage(run.id)
    assert len(second.coverage) == snapshot_count

    pages = service.pages(run.id, page=1, page_size=10, filter_name="all")
    assert pages.total == 3
    assert pages.items[0].source_version_id == version_id
    assert pages.items[0].stage_states["text_extraction"] == "completed"
    assert pages.items[1].stage_states["review"] == "needs_review"
    assert service.pages(run.id, page=1, page_size=10, filter_name="issues").total == 1

    with run_library.database.connect() as connection:
        connection.execute(
            "INSERT INTO document_pages("
            "source_version_id,pdf_page_index,created_at,updated_at"
            ") VALUES (?,0,'now','now')",
            (second_version,),
        )
        identities = connection.execute(
            "SELECT id,source_version_id,pdf_page_index FROM document_pages "
            "WHERE pdf_page_index=0 ORDER BY source_version_id"
        ).fetchall()
    assert len(identities) == 2
    assert identities[0]["id"] != identities[1]["id"]

    canonical = next(
        snapshot for snapshot in second.coverage if snapshot.dimension == "canonical_mapping"
    )
    assert canonical.execution_status == "not_executed"
    assert canonical.completed is None
    assert not hasattr(canonical, "understanding_percentage")
    for snapshot in second.coverage:
        if snapshot.execution_status == "executed" and snapshot.denominator is not None:
            assert (snapshot.completed or 0) + (snapshot.pending or 0) + (snapshot.failed or 0) + (
                snapshot.not_applicable or 0
            ) == snapshot.denominator


def test_failure_keeps_prior_snapshot_and_retry_increments_attempt(
    run_library: EducationalLibraryService,
    run_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    _, version_id = seed_version(run_library, run_settings)
    service = DocumentRunService(run_library.database, run_settings.educational_materials_dir)
    run = service.create_run(request_for(version_id))
    service.run_preflight(run.id)

    original = service._reconcile_in_transaction

    def fail(_run, _attempt):
        raise sqlite3.OperationalError("synthetic transactional failure")

    monkeypatch.setattr(service, "_reconcile_in_transaction", fail)
    failed = service.reconcile_coverage(run.id)
    assert failed.state == "failed"
    assert len(failed.coverage) == 1
    retried = service.retry_stage(run.id, "coverage_reconciliation", failed_pages_only=True)
    attempts = [
        stage.attempt for stage in retried.stages if stage.name == "coverage_reconciliation"
    ]
    assert attempts == [1, 2]
    monkeypatch.setattr(service, "_reconcile_in_transaction", original)
    completed = service.reconcile_coverage(run.id)
    assert completed.state in {"completed", "completed_with_issues"}
    assert len(completed.coverage) > 1


def test_failed_pages_only_retry_pins_page_selection(
    run_library: EducationalLibraryService, run_settings: Settings
):
    _, version_id = seed_version(run_library, run_settings, page_count=2)
    service = DocumentRunService(run_library.database, run_settings.educational_materials_dir)
    run = service.create_run(request_for(version_id))
    service.run_preflight(run.id)
    service.reconcile_coverage(run.id)
    with run_library.database.transaction(immediate=True) as connection:
        pages = connection.execute(
            "SELECT id,pdf_page_index FROM document_pages WHERE source_version_id=? "
            "ORDER BY pdf_page_index",
            (version_id,),
        ).fetchall()
        connection.execute(
            "UPDATE document_processing_runs SET state='failed',resumable=1,"
            "completed_at=NULL WHERE id=?",
            (run.id,),
        )
        connection.execute(
            "UPDATE document_run_stages SET state='failed' WHERE run_id=? "
            "AND name='coverage_reconciliation'",
            (run.id,),
        )
        connection.execute(
            "UPDATE document_page_stage_results SET state='completed' WHERE run_id=? AND page_id=?",
            (run.id, pages[0]["id"]),
        )
        connection.execute(
            "UPDATE document_page_stage_results SET state='failed' WHERE run_id=? AND page_id=?",
            (run.id, pages[1]["id"]),
        )

    retried = service.retry_stage(run.id, "coverage_reconciliation", failed_pages_only=True)
    retry = max(
        (stage for stage in retried.stages if stage.name == "coverage_reconciliation"),
        key=lambda stage: stage.attempt,
    )
    assert retry.configuration["failed_pages_only"] is True
    assert retry.configuration["selected_pages"] == [2]


def test_restart_recovery_pauses_runs_without_false_success(
    run_library: EducationalLibraryService, run_settings: Settings
):
    _, version_id = seed_version(run_library, run_settings)
    service = DocumentRunService(run_library.database, run_settings.educational_materials_dir)
    run = service.create_run(request_for(version_id))
    service.run_preflight(run.id)
    with run_library.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE document_run_stages SET state='running' "
            "WHERE run_id=? AND name='coverage_reconciliation'",
            (run.id,),
        )
        connection.execute(
            "UPDATE processing_jobs SET state='running' WHERE document_run_id=?",
            (run.id,),
        )
    EducationalLibraryService(run_settings)
    recovered = service.get_run(run.id)
    assert recovered.state == "paused"
    assert all(stage.state != "running" for stage in recovered.stages)
    assert all(
        stage.state != "completed"
        for stage in recovered.stages
        if stage.name == "coverage_reconciliation"
    )


def test_schema_11_migrate_rollback_migrate_preserves_legacy_jobs(tmp_path: Path):
    path = tmp_path / "library.sqlite3"
    database = LibraryDatabase(path)
    assert database.migrate() == 11
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO processing_jobs(id,kind,state,created_at,updated_at) "
            "VALUES ('legacy-job','scan','interrupted','now','now')"
        )
    assert database.rollback_version_11() == 10
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM processing_jobs WHERE id='legacy-job'"
            ).fetchone()[0]
            == 1
        )
    assert database.migrate() == 11
    with database.connect() as connection:
        job = connection.execute(
            "SELECT document_run_id,legacy FROM processing_jobs WHERE id='legacy-job'"
        ).fetchone()
        assert tuple(job) == (None, 1)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.anyio
async def test_run_api_requires_exact_version_gets_are_read_only_and_pages_paginate(
    run_library: EducationalLibraryService, run_settings: Settings
):
    _, version_id = seed_version(run_library, run_settings)
    app.dependency_overrides[get_library_service] = lambda: run_library
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.post(
                "/api/library/laboratory/runs",
                json={
                    "run_type": "document_analysis",
                    "pipeline_version": "v1",
                    "configuration": {},
                    "selection_strategy": "all_pages",
                },
            )
            assert missing.status_code == 422
            created = await client.post(
                "/api/library/laboratory/runs",
                json=request_for(version_id).model_dump(),
            )
            assert created.status_code == 201
            run_id = created.json()["id"]
            with run_library.database.connect() as connection:
                before = tuple(
                    connection.execute(
                        "SELECT "
                        "(SELECT count(*) FROM document_processing_runs),"
                        "(SELECT count(*) FROM document_run_stages),"
                        "(SELECT count(*) FROM document_coverage_snapshots),"
                        "(SELECT count(*) FROM document_pages),"
                        "(SELECT count(*) FROM document_run_events),"
                        "(SELECT max(updated_at) FROM document_processing_runs)"
                    ).fetchone()
                )
            listing = await client.get(
                f"/api/library/laboratory/runs?source_version_id={version_id}&state=planned"
            )
            detail = await client.get(f"/api/library/laboratory/runs/{run_id}")
            stages = await client.get(f"/api/library/laboratory/runs/{run_id}/stages")
            coverage = await client.get(f"/api/library/laboratory/runs/{run_id}/coverage")
            issues = await client.get(f"/api/library/laboratory/runs/{run_id}/issues")
            events = await client.get(f"/api/library/laboratory/runs/{run_id}/events")
            pages = await client.get(
                f"/api/library/laboratory/runs/{run_id}/pages?page=1&page_size=10"
            )
            with run_library.database.connect() as connection:
                after = tuple(
                    connection.execute(
                        "SELECT "
                        "(SELECT count(*) FROM document_processing_runs),"
                        "(SELECT count(*) FROM document_run_stages),"
                        "(SELECT count(*) FROM document_coverage_snapshots),"
                        "(SELECT count(*) FROM document_pages),"
                        "(SELECT count(*) FROM document_run_events),"
                        "(SELECT max(updated_at) FROM document_processing_runs)"
                    ).fetchone()
                )
            preflight = await client.post(f"/api/library/laboratory/runs/{run_id}/preflight")
            paused = await client.post(f"/api/library/laboratory/runs/{run_id}/pause")
            resumed = await client.post(f"/api/library/laboratory/runs/{run_id}/resume")
            reconciled = await client.post(
                f"/api/library/laboratory/runs/{run_id}/coverage/reconcile"
            )
            child = await client.post(
                f"/api/library/laboratory/runs/{run_id}/passes",
                json={"reason": "API pending-only pass"},
            )
            cancelled = await client.post(
                f"/api/library/laboratory/runs/{child.json()['id']}/cancel"
            )
            invalid = await client.post(f"/api/library/laboratory/runs/{child.json()['id']}/resume")
    finally:
        app.dependency_overrides.clear()
    assert listing.status_code == detail.status_code == 200
    assert stages.status_code == coverage.status_code == 200
    assert issues.status_code == events.status_code == pages.status_code == 200
    assert detail.json()["source_version_id"] == version_id
    assert pages.json()["page_size"] == 10
    assert before == after
    assert preflight.status_code == paused.status_code == resumed.status_code == 200
    assert reconciled.status_code == 200
    assert reconciled.json()["state"] == "completed"
    assert child.status_code == 201
    assert child.json()["parent_run_id"] == run_id
    assert cancelled.json()["state"] == "cancelled"
    assert invalid.status_code == 422
