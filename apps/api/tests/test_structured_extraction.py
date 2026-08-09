from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import httpx
import pytest

from deutschos_api.core.config import Settings
from deutschos_api.educational_library.database import LibraryDatabase
from deutschos_api.educational_library.dependencies import get_library_service
from deutschos_api.educational_library.schemas import DocumentPageRepeatRequest
from deutschos_api.educational_library.service import EducationalLibraryService
from deutschos_api.educational_library.structured_extraction import StructuredExtractionService
from deutschos_api.main import app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def structured_library(tmp_path: Path):
    materials = tmp_path / "materials"
    materials.mkdir()
    settings = Settings(
        database_url="sqlite://",
        educational_materials_dir=materials,
        educational_library_runtime_dir=tmp_path / "runtime",
        educational_library_scan_on_startup=False,
        educational_library_embedding_model="",
    )
    library = EducationalLibraryService(settings)
    source = FIXTURES / "page_comparisons" / "fixture_a_target.pdf"
    target = materials / "synthetic.pdf"
    shutil.copy2(source, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    now = "2026-08-09T12:00:00+00:00"
    with library.database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO sources(id,current_path,name,kind,format,size_bytes,mtime_ns,current_hash,"
            "status,processing_state,first_seen_at,last_seen_at,document_state) "
            "VALUES ('synthetic','synthetic.pdf','Synthetic','document','.pdf',?,?,?,"
            "'present','pending',?,?,'candidate')",
            (target.stat().st_size, target.stat().st_mtime_ns, digest, now, now),
        )
        cursor = connection.execute(
            "INSERT INTO source_versions(source_id,version_number,content_hash,size_bytes,mtime_ns,"
            "processing_state,created_at,observed_path,observed_name,detected_at,document_state,"
            "availability_state,extraction_state,chunk_state,embedding_state,activation_state,"
            "is_active,version_provenance) VALUES ('synthetic',1,?,?,?,'pending',?,"
            "'synthetic.pdf','synthetic.pdf',?,'candidate','present','pending','pending','pending',"
            "'candidate',0,'fixture')",
            (digest, target.stat().st_size, target.stat().st_mtime_ns, now, now),
        )
        version_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE sources SET latest_version_id=? WHERE id='synthetic'", (version_id,)
        )
    return library, settings, version_id


def test_schema_9_is_reversible_and_preserves_existing_counts(tmp_path: Path):
    database = LibraryDatabase(tmp_path / "library.sqlite3")
    assert database.migrate() == 9
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO sources(id,current_path,name,kind,format,size_bytes,mtime_ns,status,"
            "processing_state,first_seen_at,last_seen_at) VALUES "
            "('s','s.pdf','s','document','.pdf',1,1,'present','pending','now','now')"
        )
    assert database.rollback_version_9() == 8
    assert database.integrity() == ("ok", [])
    assert database.migrate() == 9
    assert database.integrity() == ("ok", [])
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 1


def test_full_fixture_pipeline_materializes_text_layout_and_candidates(structured_library):
    library, settings, version_id = structured_library
    service = StructuredExtractionService(library.database, settings.educational_materials_dir)
    run = service.create_run(version_id)
    assert [stage.version for stage in run.stages] == [
        "pdf-preflight.v1",
        "page-materialization.v1",
        "embedded-text-extraction.v1",
        "layout-analysis.v1",
        "structure-candidate-extraction.v1",
        "coverage-reconciliation.v1",
        "version-comparison.v1",
    ]
    service.preflight(run.id)
    service.materialize_pages(run.id)
    service.extract_embedded_text(run.id)
    service.analyze_layout(run.id)
    service.extract_candidates(run.id)
    detail = service.reconcile_coverage(run.id)
    assert detail.state == "running"
    with library.database.connect() as connection:
        counts = tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM document_pages),"
                "(SELECT count(*) FROM document_page_artifacts),"
                "(SELECT count(*) FROM document_page_blocks),"
                "(SELECT count(*) FROM document_structure_candidates),"
                "(SELECT count(*) FROM chunks),(SELECT count(*) FROM embeddings),"
                "(SELECT count(*) FROM knowledge_units)"
            ).fetchone()
        )
        assert counts[:4] == (4, 4, counts[2], counts[3])
        assert counts[2] > 0 and counts[3] > 0
        assert counts[4:] == (0, 0, 0)
        assert (
            connection.execute("SELECT count(*) FROM source_versions WHERE is_active=1").fetchone()[
                0
            ]
            == 0
        )
    assert service.blocks(run.id, 1)
    candidates = service.candidates(source_version_id=version_id, page_size=100)
    assert candidates.total > 0
    assert all(item.raw_text == item.raw_text for item in candidates.items)
    assert service.hierarchy(version_id)


def test_repeat_selected_page_does_not_duplicate_page_identity(structured_library):
    library, settings, version_id = structured_library
    service = StructuredExtractionService(library.database, settings.educational_materials_dir)
    parent = service.create_run(version_id)
    service.preflight(parent.id)
    service.materialize_pages(parent.id)
    service.runs.cancel(parent.id)
    child = service.repeat_pages(
        parent.id,
        DocumentPageRepeatRequest(pages=[2], reason="synthetic retry"),
    )
    service.preflight(child.id)
    service.materialize_pages(child.id)
    with library.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM document_pages").fetchone()[0] == 4
    assert child.selected_pages == [2]


def test_deterministic_rules_preserve_text_and_mark_visual_review(structured_library):
    library, settings, _ = structured_library
    service = StructuredExtractionService(library.database, settings.educational_materials_dir)
    cases = json.loads((FIXTURES / "structured_extraction" / "pages.json").read_text())
    by_case = {item["case"]: item["text"] for item in cases}
    assert service._candidate_kind(by_case["example"], "text", 10, 1, None)[0] == "example"
    assert service._candidate_kind(by_case["exercise"], "text", 10, 1, None)[0] == (
        "exercise_heading"
    )
    assert service._candidate_kind(by_case["solution"], "text", 10, 1, None)[0] == (
        "solution_heading"
    )
    visual = service._candidate_kind("", "image", 10, 1, "exercise")
    assert visual[0] == "diagram" and "visual_content_not_flattened" in visual[3]
    defective = by_case["defective_ocr"]
    assert service._candidate_kind(defective, "text", 10, 1, None)[0] == "paragraph"
    assert defective == "El modo impemtivo"


def test_get_readers_do_not_write(structured_library):
    library, settings, version_id = structured_library
    service = StructuredExtractionService(library.database, settings.educational_materials_dir)
    before = library.database.path.stat().st_mtime_ns
    assert service.candidates(source_version_id=version_id).total == 0
    assert service.hierarchy(version_id) == []
    after = library.database.path.stat().st_mtime_ns
    assert after == before


@pytest.mark.anyio
async def test_structured_api_is_paginated_filterable_and_explicit(structured_library):
    library, _, version_id = structured_library
    app.dependency_overrides[get_library_service] = lambda: library
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/library/laboratory/extraction/runs",
                params={"source_version_id": version_id},
            )
            assert created.status_code == 201
            run_id = created.json()["id"]
            for path in (
                "pdf-preflight",
                "pages/materialize",
                "text/extract-embedded",
                "layout/analyze",
                "candidates/extract",
                "structured-coverage/reconcile",
            ):
                response = await client.post(f"/api/library/laboratory/runs/{run_id}/{path}")
                assert response.status_code == 200, response.text
            pages = await client.get(
                f"/api/library/laboratory/runs/{run_id}/pages",
                params={"page": 1, "page_size": 10, "filter": "all"},
            )
            assert pages.json()["total"] == 4
            blocks = await client.get(f"/api/library/laboratory/runs/{run_id}/pages/1/blocks")
            assert blocks.status_code == 200 and blocks.json()
            candidates = await client.get(
                "/api/library/laboratory/extraction/candidates",
                params={
                    "source_version_id": version_id,
                    "candidate_type": "paragraph",
                    "page": 1,
                    "page_size": 2,
                },
            )
            assert candidates.status_code == 200
            assert candidates.json()["page_size"] == 2
            hierarchy = await client.get(
                f"/api/library/laboratory/extraction/versions/{version_id}/hierarchy"
            )
            assert hierarchy.status_code == 200
            thumbnail = await client.get(
                f"/api/library/laboratory/extraction/versions/{version_id}/pages/1/thumbnail"
            )
            assert thumbnail.status_code == 200
            assert thumbnail.headers["content-type"] == "image/png"
    finally:
        app.dependency_overrides.clear()
