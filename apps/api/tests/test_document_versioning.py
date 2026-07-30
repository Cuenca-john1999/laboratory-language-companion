from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from deutschos_api.core.config import Settings
from deutschos_api.educational_library.database import LibraryDatabase
from deutschos_api.educational_library.dependencies import get_library_service
from deutschos_api.educational_library.service import EducationalLibraryService
from deutschos_api.main import app


@pytest.fixture
def document_settings(tmp_path: Path) -> Settings:
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
def document_library(document_settings: Settings) -> EducationalLibraryService:
    return EducationalLibraryService(document_settings)


def _seed_active_markdown(
    service: EducationalLibraryService, settings: Settings
) -> tuple[str, int, int]:
    path = settings.educational_materials_dir / "grammar.md"
    path.write_text("# Kasus\nDer Akkusativ markiert das direkte Objekt.", encoding="utf-8")
    service.scan(process_documents=True)
    with service.database.transaction(immediate=True) as connection:
        source = connection.execute(
            "SELECT id,current_version_id FROM sources WHERE current_path='grammar.md'"
        ).fetchone()
        version_id = int(source["current_version_id"])
        connection.execute(
            "UPDATE source_versions SET is_active=1,activation_state='active',"
            "document_state='active',availability_state='present',extraction_state='extracted',"
            "chunk_state='available',observed_path='grammar.md',observed_name='grammar.md',"
            "detected_at=created_at WHERE id=?",
            (version_id,),
        )
        connection.execute(
            "UPDATE sources SET latest_version_id=current_version_id,document_state='active' "
            "WHERE id=?",
            (source["id"],),
        )
        chunks = connection.execute(
            "SELECT count(*) FROM chunks WHERE source_version_id=?", (version_id,)
        ).fetchone()[0]
    return str(source["id"]), version_id, int(chunks)


def test_same_hash_is_idempotent_and_reads_do_not_write(
    document_library: EducationalLibraryService, document_settings: Settings
):
    source_id, active_id, _ = _seed_active_markdown(document_library, document_settings)

    first = document_library.detect_document_changes()
    before = document_library.database.path.stat().st_mtime_ns
    summary = document_library.laboratory_summary()
    sources = document_library.laboratory_sources()
    detail = document_library.laboratory_source(source_id)
    after = document_library.database.path.stat().st_mtime_ns
    second = document_library.detect_document_changes()

    assert first.candidate_versions_created == 0
    assert second.candidate_versions_created == 0
    assert first.unchanged == second.unchanged == 1
    assert len(detail.versions) == 1
    assert detail.active_version_id == active_id
    assert summary.catalogued_sources == len(sources) == 1
    assert before == after


def test_changed_hash_creates_candidate_for_same_source_and_preserves_active_corpus(
    document_library: EducationalLibraryService, document_settings: Settings
):
    source_id, active_id, active_chunks = _seed_active_markdown(document_library, document_settings)
    old_chunk_ids = [
        row[0]
        for row in document_library.database.connect()
        .execute("SELECT id FROM chunks ORDER BY id")
        .fetchall()
    ]
    (document_settings.educational_materials_dir / "grammar.md").write_text(
        "# Kasus\nNeue OCR-Schicht mit mehr Text.", encoding="utf-8"
    )

    result = document_library.detect_document_changes()
    detail = document_library.laboratory_source(source_id)

    assert result.modified == 1
    assert result.candidate_versions_created == 1
    assert len(document_library.laboratory_sources()) == 1
    assert detail.active_version_id == active_id
    assert detail.latest_version_id != active_id
    assert detail.versions[0].activation_state == "candidate"
    assert detail.versions[0].extraction_state == "pending"
    assert detail.active_chunks == active_chunks
    assert [chunk.id for chunk in document_library.source_chunks(source_id)] == old_chunk_ids
    assert document_library.detect_document_changes().candidate_versions_created == 0
    assert len(document_library.document_versions(source_id)) == 2


def test_missing_file_keeps_versions_chunks_and_active_pointer(
    document_library: EducationalLibraryService, document_settings: Settings
):
    source_id, active_id, active_chunks = _seed_active_markdown(document_library, document_settings)
    (document_settings.educational_materials_dir / "grammar.md").unlink()

    result = document_library.detect_document_changes()
    detail = document_library.laboratory_source(source_id)

    assert result.missing == 1
    assert detail.source_status == "missing"
    assert detail.active_version_id == active_id
    assert detail.active_chunks == active_chunks
    assert len(detail.versions) == 1


def test_renamed_same_hash_keeps_identity_and_history(
    document_library: EducationalLibraryService, document_settings: Settings
):
    source_id, active_id, _ = _seed_active_markdown(document_library, document_settings)
    source = document_settings.educational_materials_dir / "grammar.md"
    source.rename(document_settings.educational_materials_dir / "renamed.md")

    result = document_library.detect_document_changes()
    detail = document_library.laboratory_source(source_id)

    assert result.renamed == 1
    assert detail.id == source_id
    assert detail.current_path == "renamed.md"
    assert detail.active_version_id == active_id
    assert len(detail.versions) == 1
    assert detail.versions[0].observed_path == "grammar.md"


def test_ambiguous_hash_match_is_not_merged(
    document_library: EducationalLibraryService, document_settings: Settings
):
    path = document_settings.educational_materials_dir / "first.md"
    path.write_text("identical", encoding="utf-8")
    document_library.detect_document_changes()
    path.unlink()
    with document_library.database.transaction(immediate=True) as connection:
        original = connection.execute("SELECT * FROM sources").fetchone()
        version = connection.execute("SELECT * FROM source_versions").fetchone()
        connection.execute(
            "INSERT INTO sources(id,current_path,name,kind,format,size_bytes,mtime_ns,current_hash,"
            "status,processing_state,first_seen_at,last_seen_at,latest_version_id,document_state) "
            "VALUES ('ambiguous-copy','second.md','second.md','document','.md',?,?,?,?,?,?,?,"
            "?, 'candidate')",
            (
                original["size_bytes"],
                original["mtime_ns"],
                original["current_hash"],
                "missing",
                "pending",
                original["first_seen_at"],
                original["last_seen_at"],
                None,
            ),
        )
        cursor = connection.execute(
            "INSERT INTO source_versions(source_id,version_number,content_hash,size_bytes,mtime_ns,"
            "processing_state,created_at,observed_path,observed_name,detected_at,document_state,"
            "availability_state,extraction_state,chunk_state,embedding_state,activation_state,"
            "version_provenance) VALUES ('ambiguous-copy',1,?,?,?,'pending',?,'second.md',"
            "'second.md',?,'candidate','missing','pending','pending','pending','candidate','test')",
            (
                version["content_hash"],
                version["size_bytes"],
                version["mtime_ns"],
                version["created_at"],
                version["created_at"],
            ),
        )
        connection.execute(
            "UPDATE sources SET latest_version_id=? WHERE id='ambiguous-copy'",
            (cursor.lastrowid,),
        )
    (document_settings.educational_materials_dir / "third.md").write_text(
        "identical", encoding="utf-8"
    )

    result = document_library.detect_document_changes()

    assert result.manual_review == 1
    assert len(document_library.laboratory_sources()) == 3
    reviewed = [item for item in document_library.laboratory_sources() if item.needs_manual_review]
    assert len(reviewed) == 1
    assert reviewed[0].current_path == "third.md"


def test_inventory_failure_rolls_back_document_changes(
    document_library: EducationalLibraryService,
    document_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    (document_settings.educational_materials_dir / "one.md").write_text("eins", encoding="utf-8")
    (document_settings.educational_materials_dir / "two.md").write_text("zwei", encoding="utf-8")
    original = document_library._apply_inventory_entry
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected inventory failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(document_library, "_apply_inventory_entry", fail_second)

    with pytest.raises(RuntimeError, match="injected"):
        document_library.detect_document_changes()

    with document_library.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
        job = connection.execute(
            "SELECT state,error_code FROM processing_jobs WHERE kind='document_inventory'"
        ).fetchone()
    assert tuple(job) == ("failed", "RuntimeError")


def test_inventory_does_not_call_extraction_ocr_models_or_create_artifacts(
    document_library: EducationalLibraryService,
    document_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    (document_settings.educational_materials_dir / "candidate.md").write_text(
        "Nur Inventar", encoding="utf-8"
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("processing capability was initialized")

    monkeypatch.setattr("deutschos_api.educational_library.service.extract", forbidden)
    result = document_library.detect_document_changes()

    with document_library.database.connect() as connection:
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM documents),(SELECT count(*) FROM chunks),"
            "(SELECT count(*) FROM embeddings)"
        ).fetchone()
    assert result.new == 1
    assert tuple(counts) == (0, 0, 0)


@pytest.mark.anyio
async def test_laboratory_api_exposes_summary_history_versions_and_inventory(
    document_library: EducationalLibraryService, document_settings: Settings
):
    source_id, _, _ = _seed_active_markdown(document_library, document_settings)
    app.dependency_overrides[get_library_service] = lambda: document_library
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            summary = await client.get("/api/library/laboratory/summary")
            sources = await client.get("/api/library/laboratory/sources?filter=active")
            detail = await client.get(f"/api/library/laboratory/sources/{source_id}")
            versions = await client.get(f"/api/library/laboratory/sources/{source_id}/versions")
            version = await client.get(
                f"/api/library/laboratory/versions/{versions.json()[0]['id']}"
            )
            inventory = await client.post("/api/library/laboratory/inventory")
            latest = await client.get("/api/library/laboratory/inventory/latest")
    finally:
        app.dependency_overrides.clear()

    assert summary.status_code == 200
    assert summary.json()["catalogued_sources"] == 1
    assert len(sources.json()) == 1
    assert detail.json()["versions"][0]["is_active"] is True
    assert version.json()["provenance"]
    assert inventory.status_code == 200
    assert latest.json()["job_id"] == inventory.json()["job_id"]


def test_schema_6_and_7_preserve_legacy_rows_and_are_reversible(tmp_path: Path):
    path = tmp_path / "library.sqlite3"
    database = LibraryDatabase(path)
    assert database.migrate() == 7
    assert database.rollback_version_7() == 6
    assert database.rollback_version_6() == 5
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO sources(id,current_path,name,kind,format,size_bytes,mtime_ns,current_hash,"
            "status,first_seen_at,last_seen_at) VALUES "
            "('legacy','legacy.md','legacy.md','document','.md',1,1,'hash','present','now','now')"
        )
        cursor = connection.execute(
            "INSERT INTO source_versions(source_id,version_number,content_hash,size_bytes,mtime_ns,"
            "created_at) VALUES ('legacy',1,'hash',1,1,'now')"
        )
        connection.execute(
            "UPDATE sources SET current_version_id=? WHERE id='legacy'", (cursor.lastrowid,)
        )

    assert database.migrate() == 7
    with database.connect() as connection:
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM sources),(SELECT count(*) FROM source_versions),"
            "(SELECT count(*) FROM source_versions WHERE is_active=1)"
        ).fetchone()
        assert tuple(counts) == (1, 1, 1)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    assert database.rollback_version_7() == 6
    assert database.rollback_version_6() == 5
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
