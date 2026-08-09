from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from deutschos_api.educational_library.database import LibraryDatabase
from deutschos_api.educational_library.dependencies import get_document_review
from deutschos_api.educational_library.review import DocumentReviewService
from deutschos_api.educational_library.schemas import LibraryContractError
from deutschos_api.main import app


@pytest.fixture
def review_library(tmp_path: Path):
    database = LibraryDatabase(tmp_path / "library.sqlite3")
    assert database.migrate() == 10
    now = "2026-08-09T12:00:00+00:00"
    digest = hashlib.sha256(b"synthetic-review-document").hexdigest()
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO sources(id,current_path,name,kind,format,size_bytes,mtime_ns,current_hash,"
            "status,processing_state,first_seen_at,last_seen_at,document_state) VALUES "
            "('review','review.pdf','Synthetic review','document','.pdf',10,1,?,"
            "'present','completed',?,?,'candidate')",
            (digest, now, now),
        )
        cursor = connection.execute(
            "INSERT INTO source_versions(source_id,version_number,content_hash,size_bytes,mtime_ns,"
            "processing_state,created_at,observed_path,observed_name,detected_at,document_state,"
            "availability_state,extraction_state,chunk_state,embedding_state,activation_state,"
            "is_active,version_provenance,page_count) VALUES ('review',1,?,10,1,'completed',?,"
            "'review.pdf','review.pdf',?,'candidate','present','completed','pending','pending',"
            "'candidate',0,'fixture',53)",
            (digest, now, now),
        )
        version_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE sources SET latest_version_id=? WHERE id='review'", (version_id,)
        )
        connection.execute(
            "INSERT INTO document_processing_runs(id,source_id,source_version_id,target_hash,"
            "run_type,pipeline_version,configuration_json,configuration_hash,selection_strategy,"
            "state,created_at,updated_at) VALUES ('review-run','review',?,?,'full',"
            "'fixture.v1','{}',?,'all','completed',?,?)",
            (version_id, digest, digest, now, now),
        )
        page_ids: dict[int, int] = {}
        for page_number in range(1, 54):
            has_text = page_number != 53
            page = connection.execute(
                "INSERT INTO document_pages(source_version_id,pdf_page_index,width_points,"
                "height_points,has_text,character_count,text_quality,layout_state,structure_state,"
                "review_state,last_run_id,evidence_json,created_at,updated_at) VALUES "
                "(?,?,?,?,?,?,'good','completed','completed','pending','review-run','{}',?,?)",
                (
                    version_id,
                    page_number - 1,
                    595,
                    842,
                    int(has_text),
                    100 if has_text else 0,
                    now,
                    now,
                ),
            )
            page_ids[page_number] = int(page.lastrowid)
        connection.execute(
            "INSERT INTO document_page_artifacts(page_id,artifact_version,visual_metrics_json,"
            "region_hashes_json,created_at,updated_at) VALUES (?, 'fixture.v1',"
            "'{\"render\":true}','{}',?,?)",
            (page_ids[53], now, now),
        )

        orders: dict[int, int] = {}

        def add_candidate(
            candidate_id: str,
            page_number: int,
            candidate_type: str,
            raw: str,
            *,
            topic: int | None = None,
            block_type: str = "text",
            confidence: float = 0.82,
            status: str = "proposed",
            parent: str | None = None,
            bbox: str = "[10,10,500,40]",
        ) -> str:
            orders[page_number] = orders.get(page_number, 0) + 1
            connection.execute(
                "INSERT INTO document_structure_candidates("
                "id,source_id,source_version_id,page_id,run_id,stage,candidate_type,raw_text,"
                "normalized_layout_text,position_json,bbox_json,reading_order,parent_candidate_id,"
                "hierarchy_level,canonical_match_state,canonical_topic_number,confidence,status,"
                "evidence_json,extractor,extractor_version,configuration_json,issues_json,"
                "needs_review,created_at,updated_at) VALUES ("
                "?,'review',?,?,'review-run','structure',?,?,?,'{}',?,?,?,1,"
                "'exact_match',?,?,?,?,'fixture','fixture.v1','{}','[]',?,?,?)",
                (
                    candidate_id,
                    version_id,
                    page_ids[page_number],
                    candidate_type,
                    raw,
                    " ".join(raw.casefold().split()),
                    bbox,
                    orders[page_number],
                    parent,
                    topic,
                    confidence,
                    status,
                    f'{{"block_type":"{block_type}"}}',
                    int(status == "needs_review"),
                    now,
                    now,
                ),
            )
            return candidate_id

        for number in (1, 2, 3):
            add_candidate(
                f"index-{number}",
                1,
                "topic_heading",
                f"Tema {number} ........ {number + 10}",
                topic=number,
            )
        for number in range(1, 52):
            add_candidate(
                f"topic-{number}",
                number + 1,
                "topic_heading",
                f"Tema {number} Contenido observado",
                topic=number,
            )
        add_candidate(
            "running-header",
            2,
            "topic_heading",
            "Tema 1",
            topic=1,
            block_type="probable_header",
            confidence=0.95,
        )
        add_candidate("duplicate-primary", 2, "paragraph", "Texto repetido")
        add_candidate("duplicate-copy", 2, "paragraph", "Texto repetido")
        add_candidate("pending-a", 2, "unknown_block", "Fragmento A", status="needs_review")
        add_candidate("pending-b", 2, "unknown_block", "Fragmento B", status="needs_review")
        exercise = add_candidate(
            "exercise-heading", 2, "exercise_heading", "Ejercicios Tema 1", topic=1
        )
        solution = add_candidate(
            "solution-heading", 2, "solution_heading", "Soluciones Tema 1", topic=1
        )
        add_candidate("exercise-1", 2, "exercise_item", "1.) Aufgabe", parent=exercise)
        add_candidate("solution-1", 2, "solution_item", "1.) Lösung", parent=solution)
        add_candidate("exercise-2", 2, "exercise_item", "2.) Aufgabe", parent=exercise)
        add_candidate("solution-2a", 2, "solution_item", "2.) Lösung A", parent=solution)
        add_candidate("solution-2b", 2, "solution_item", "2.) Lösung B", parent=solution)
        add_candidate("solution-3", 2, "solution_item", "3.) Lösung", parent=solution)
        add_candidate("exercise-4", 2, "exercise_item", "4.) Aufgabe", parent=exercise)
        add_candidate(
            "topic-reference",
            2,
            "cross_reference",
            "Véase Tema 9",
            topic=9,
        )
        add_candidate(
            "graphic-page",
            53,
            "diagram",
            "",
            parent=exercise,
            block_type="image",
        )
    return database, version_id


def _state(database: LibraryDatabase, candidate_id: str) -> str:
    with database.connect() as connection:
        return str(
            connection.execute(
                "SELECT editorial_state FROM document_candidate_review_state WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()[0]
        )


def test_consolidation_is_deterministic_non_destructive_and_structurally_ready(review_library):
    database, version_id = review_library
    service = DocumentReviewService(database)
    with database.connect() as connection:
        immutable_before = tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM chunks),(SELECT count(*) FROM embeddings),"
                "(SELECT count(*) FROM source_versions WHERE is_active=1),"
                "(SELECT count(*) FROM document_structure_candidates)"
            ).fetchone()
        )
    run = service.consolidate(version_id)
    assert run["rule_version"] == "document-review.v1"
    assert run["configuration"] == {
        "activation": False,
        "chunking": False,
        "embeddings": False,
        "llm": False,
        "ocr": False,
        "topic_count": 51,
        "vision_ai": False,
    }
    topics = service.topics(version_id)
    assert len(topics) == 51
    assert {topic["state"] for topic in topics} == {"auto_supported"}
    assert len({topic["primary_candidate_id"] for topic in topics}) == 51
    assert service.readiness(version_id)["state"] == "structurally_ready_with_issues"
    assert _state(database, "index-1") == "rejected"
    assert _state(database, "running-header") == "rejected"
    assert _state(database, "duplicate-copy") == "superseded"
    assert _state(database, "topic-reference") == "proposed"
    assert topics[8]["primary_candidate_id"] == "topic-9"
    with database.connect() as connection:
        immutable_after = tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM chunks),(SELECT count(*) FROM embeddings),"
                "(SELECT count(*) FROM source_versions WHERE is_active=1),"
                "(SELECT count(*) FROM document_structure_candidates)"
            ).fetchone()
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert immutable_after == immutable_before
    assert database.integrity() == ("ok", [])


def test_hierarchy_relations_visual_queue_and_filters_are_auditable(review_library):
    database, version_id = review_library
    service = DocumentReviewService(database)
    service.consolidate(version_id)
    hierarchy = service.hierarchy(version_id)
    assert len(hierarchy) == 1
    assert hierarchy[0]["node_type"] == "book"
    assert hierarchy[0]["children"]
    node_ids: set[str] = set()

    def visit(nodes: list[dict]) -> None:
        for node in nodes:
            assert node["id"] not in node_ids
            node_ids.add(node["id"])
            visit(node["children"])

    visit(hierarchy)
    relations = service.relations(version_id)
    assert {relation["relation_type"] for relation in relations} == {
        "exercise_without_solution",
        "one_to_one",
        "one_to_many",
        "solution_without_exercise",
    }
    assert {relation["state"] for relation in relations} == {
        "auto_supported",
        "needs_review",
    }
    readiness = service.readiness(version_id)
    assert readiness["metrics"]["visual_pages_pending"] == 1
    with database.connect() as connection:
        visual = connection.execute(
            "SELECT classification,state FROM document_visual_page_classifications"
        ).fetchone()
        assert tuple(visual) == ("graphic_exercise", "needs_review")
    queue = service.queue(
        source_version_id=version_id,
        priority="P2",
        candidate_type="unknown_block",
        page=1,
        page_size=1,
    )
    assert queue["total"] == 2 and queue["pages"] == 2
    detail = service.item(queue["items"][0]["id"])
    assert detail["raw_text"] and detail["bbox"]
    assert detail["page_context"]["current_pdf_page"] == 2


def test_individual_decisions_and_logical_reverts_are_append_only(review_library):
    database, version_id = review_library
    service = DocumentReviewService(database)
    service.consolidate(version_id)
    decision = service.decide(
        "pending-a",
        action="confirm",
        actor="reviewer",
        method="manual_review",
        comment="Evidence inspected",
    )
    assert decision["previous_state"] == "needs_review"
    assert _state(database, "pending-a") == "confirmed"
    inverse = service.revert(decision["id"], actor="reviewer")
    assert inverse["reverts_decision_id"] == decision["id"]
    assert _state(database, "pending-a") == "needs_review"
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM document_candidate_decisions WHERE candidate_id='pending-a'"
            ).fetchone()[0]
            >= 3
        )
    with pytest.raises(LibraryContractError):
        service.decide(
            "pending-a",
            action="confirm",
            actor="reviewer",
            method="deterministic_rule",
        )


def test_batches_require_homogeneity_and_are_fully_reversible(review_library):
    database, version_id = review_library
    service = DocumentReviewService(database)
    service.consolidate(version_id)
    batch = service.create_batch(
        ["pending-a", "pending-b"], action="reject", minimum_confidence=0.5
    )
    assert batch["state"] == "proposed" and batch["member_count"] == 2
    assert _state(database, "pending-a") == "needs_review"
    applied = service.apply_batch(batch["id"], actor="reviewer")
    assert applied["state"] == "applied"
    assert {_state(database, item) for item in ("pending-a", "pending-b")} == {"rejected"}
    reverted = service.revert_batch(batch["id"], actor="reviewer")
    assert reverted["state"] == "reverted"
    assert {_state(database, item) for item in ("pending-a", "pending-b")} == {"needs_review"}
    with pytest.raises(LibraryContractError):
        service.create_batch(["pending-a", "topic-1"], action="confirm", minimum_confidence=0.5)


@pytest.mark.anyio
async def test_review_api_gets_are_read_only_and_mutations_are_explicit(review_library):
    database, version_id = review_library
    service = DocumentReviewService(database)
    app.dependency_overrides[get_document_review] = lambda: service
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/library/laboratory/review/versions/{version_id}/consolidate"
            )
            assert created.status_code == 201, created.text
            before = database.path.stat().st_mtime_ns
            for url in (
                "/api/library/laboratory/review/summary",
                f"/api/library/laboratory/review/versions/{version_id}/topics",
                f"/api/library/laboratory/review/versions/{version_id}/hierarchy",
                f"/api/library/laboratory/review/versions/{version_id}/exercise-solutions",
                f"/api/library/laboratory/review/versions/{version_id}/readiness",
                "/api/library/laboratory/review/queue?page=1&page_size=1",
            ):
                response = await client.get(url)
                assert response.status_code == 200, response.text
            assert database.path.stat().st_mtime_ns == before
            response = await client.post(
                "/api/library/laboratory/review/items/pending-a/decision",
                json={"action": "confirm", "actor": "api-reviewer"},
            )
            assert response.status_code == 200
            assert response.json()["new_state"] == "confirmed"
    finally:
        app.dependency_overrides.pop(get_document_review, None)


def test_schema_10_rollback_removes_only_review_layer(review_library):
    database, _ = review_library
    with database.connect() as connection:
        before = connection.execute(
            "SELECT count(*) FROM document_structure_candidates"
        ).fetchone()[0]
    assert database.rollback_version_10() == 9
    with database.connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM document_structure_candidates").fetchone()[0]
            == before
        )
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='document_review_runs'"
            ).fetchone()
            is None
        )
    assert database.migrate() == 10
    assert database.integrity() == ("ok", [])


def test_ambiguous_principal_topic_is_not_forced_and_p0_blocks(review_library):
    database, version_id = review_library
    now = "2026-08-09T12:00:00+00:00"
    with database.transaction(immediate=True) as connection:
        page_id = connection.execute(
            "SELECT id FROM document_pages WHERE source_version_id=? AND pdf_page_index=51",
            (version_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO document_structure_candidates("
            "id,source_id,source_version_id,page_id,run_id,stage,candidate_type,raw_text,"
            "normalized_layout_text,position_json,bbox_json,reading_order,hierarchy_level,"
            "canonical_match_state,canonical_topic_number,confidence,status,evidence_json,"
            "extractor,extractor_version,configuration_json,issues_json,needs_review,created_at,"
            "updated_at) VALUES ('topic-51-ambiguous','review',? ,?,'review-run','structure',"
            "'topic_heading','Tema 51 Alternativa','tema 51 alternativa','{}','[20,20,500,40]',"
            "2,1,'exact_match',51,0.82,'proposed','{\"block_type\":\"text\"}',"
            "'fixture','fixture.v1','{}','[]',0,?,?)",
            (version_id, page_id, now, now),
        )
    service = DocumentReviewService(database)
    service.consolidate(version_id)
    topic = service.topics(version_id)[50]
    assert topic["state"] == "conflicted"
    assert topic["primary_candidate_id"] is None
    assert service.queue(source_version_id=version_id, priority="P0")["total"] == 2
    assert service.readiness(version_id)["state"] == "blocked"
