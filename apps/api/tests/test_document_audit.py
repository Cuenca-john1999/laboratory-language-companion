from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_document_review import review_library as review_library_fixture  # noqa: E402

from llc_api.educational_library.audit import (  # noqa: E402
    DECISION_SCHEMA,
    DocumentAuditService,
)
from llc_api.educational_library.dependencies import get_document_audit  # noqa: E402
from llc_api.educational_library.review import DocumentReviewService  # noqa: E402
from llc_api.educational_library.schemas import LibraryContractError  # noqa: E402
from llc_api.main import app  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def audit_library(tmp_path: Path):
    database, version_id = review_library_fixture.__wrapped__(tmp_path)
    materials = tmp_path / "materials"
    materials.mkdir()
    shutil.copy2(
        FIXTURES / "page_comparisons" / "fixture_a_target.pdf",
        materials / "review.pdf",
    )
    DocumentReviewService(database).consolidate(version_id)
    return database, DocumentAuditService(database, materials), version_id


def decision_set(export: dict, *, target_id: str = "pending-a", expected: str = "needs_review"):
    return {
        "decision_schema": DECISION_SCHEMA,
        "package_logical_hash": export["logical_hash"],
        "source_id": export["source_id"],
        "source_version_id": export["source_version_id"],
        "document_hash": export["document_hash"],
        "reviewer": "external-fixture-reviewer",
        "reviewed_at": "2026-08-10T00:00:00+00:00",
        "decisions": [
            {
                "target_type": "candidate",
                "target_id": target_id,
                "expected_previous_state": expected,
                "action": "confirm",
                "evidence_references": ["candidates.json#pending-a"],
                "rationale": "Fixture evidence inspected.",
                "confidence": 0.97,
            }
        ],
    }


def candidate_state(database, candidate_id: str) -> str:
    with database.connect() as connection:
        return str(
            connection.execute(
                "SELECT editorial_state FROM document_candidate_review_state WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()[0]
        )


def index_only_decision_set(export: dict, topic_id: str, evidence_candidate_id: str):
    return {
        "decision_schema": DECISION_SCHEMA,
        "package_logical_hash": export["logical_hash"],
        "source_id": export["source_id"],
        "source_version_id": export["source_version_id"],
        "document_hash": export["document_hash"],
        "reviewer": "external-human-reviewer",
        "reviewed_at": "2026-08-10T01:00:00+00:00",
        "decisions": [
            {
                "target_type": "topic",
                "target_id": topic_id,
                "expected_previous_state": "pending",
                "action": "resolve_topic_identity",
                "replacement": {
                    "identity_resolution": "index_only_no_direct_practice",
                    "evidence_candidate_id": evidence_candidate_id,
                },
                "evidence_references": [
                    f"topics.json#{topic_id}",
                    f"candidates.json#{evidence_candidate_id}",
                ],
                "rationale": "The index explicitly marks this canonical topic with dashes "
                "for every exercise and solution column.",
                "confidence": 1.0,
            }
        ],
    }


def test_schema_12_is_reversible_without_removing_review_or_audit_data(audit_library):
    database, _, _ = audit_library
    with database.connect() as connection:
        review_count = connection.execute("SELECT count(*) FROM document_review_runs").fetchone()[0]
        candidate_count = connection.execute(
            "SELECT count(*) FROM document_structure_candidates"
        ).fetchone()[0]
    assert database.rollback_version_13() == 12
    assert database.rollback_version_12() == 11
    with database.connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM document_review_runs").fetchone()[0]
            == review_count
        )
        assert (
            connection.execute("SELECT count(*) FROM document_structure_candidates").fetchone()[0]
            == candidate_count
        )
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='document_audit_exports'"
        ).fetchone()
        topic_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_consolidated_topics)")
        }
        assert "identity_resolution" not in topic_columns
    assert database.migrate() == 13
    assert database.integrity() == ("ok", [])


def test_exports_are_version_bound_scoped_reproducible_and_hash_verified(audit_library):
    _, service, version_id = audit_library
    full = service.create_export(version_id, mode="full")
    repeated = service.create_export(version_id, mode="full")
    review = service.create_export(version_id, mode="review_only", selection={"include_p2": True})
    targeted = service.create_export(
        version_id,
        mode="targeted",
        selection={"theme_number": 1, "pages": [2]},
        include_visuals=False,
    )
    visual = service.create_export(
        version_id,
        mode="targeted",
        selection={"candidate_ids": ["topic-2"]},
        include_visuals=True,
    )
    assert full["logical_hash"] == repeated["logical_hash"]
    assert full["manifest"]["source"]["source_version_id"] == version_id
    assert full["manifest"]["source"]["document_sha256"] == full["document_hash"]
    assert set(full["manifest"]["files"]) >= {
        "source.json",
        "candidates.json",
        "readiness.json",
        "audit_summary.md",
    }
    assert targeted["counts"]["candidates"] < full["counts"]["candidates"]
    assert review["counts"]["review_queue"] > 0
    assert targeted["manifest"]["export_mode"] == "targeted"
    assert targeted["manifest"]["selection"]["theme_number"] == 1
    path = service.archive_path(visual["id"])
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert any(name.startswith("review/page-") for name in names)
        for name, expected in visual["manifest"]["files"].items():
            payload = archive.read(name)
            assert len(payload) == expected["size_bytes"]
            assert __import__("hashlib").sha256(payload).hexdigest() == expected["sha256"]
        review_queue = json.loads(archive.read("review_queue.json"))
        assert all(item["state"] in {"needs_review", "conflicted"} for item in review_queue)


def test_archive_tampering_is_detected(audit_library):
    _, service, version_id = audit_library
    export = service.create_export(version_id, mode="review_only")
    path = service.archive_path(export["id"])
    with path.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(LibraryContractError, match="integridad"):
        service.archive_path(export["id"])


def test_validator_rejects_wrong_identity_hash_target_and_stale_state(audit_library):
    database, service, version_id = audit_library
    export = service.create_export(version_id, mode="review_only", selection={"include_p2": True})
    valid = decision_set(export)
    assert service.validate_decision_set(valid)["valid"] is True
    for field, value, reason in (
        ("source_id", "wrong", "source_id_mismatch"),
        ("source_version_id", 999, "source_version_unknown"),
        ("document_hash", "0" * 64, "document_hash_mismatch"),
        ("package_logical_hash", "f" * 64, "package_logical_hash_unknown"),
    ):
        changed = valid | {field: value}
        result = service.validate_decision_set(changed)
        assert result["valid"] is False
        assert any(item["reason"] == reason for item in result["invalid"])
    unknown = decision_set(export, target_id="missing")
    assert service.validate_decision_set(unknown)["invalid"][0]["reason"] == "target_unknown"
    stale = decision_set(export, expected="proposed")
    assert service.validate_decision_set(stale)["stale"][0]["reason"] == "expected_state_changed"
    scoped_export = service.create_export(
        version_id, mode="targeted", selection={"candidate_ids": ["pending-a"]}
    )
    outside_scope = decision_set(scoped_export, target_id="topic-1")
    assert (
        service.validate_decision_set(outside_scope)["invalid"][0]["reason"]
        == "target_not_in_export_scope"
    )
    invalid_action = decision_set(export)
    invalid_action["decisions"][0]["action"] = "confirm_relation"
    assert (
        service.validate_decision_set(invalid_action)["invalid"][0]["reason"]
        == "action_not_allowed_for_target"
    )
    invalid_evidence = decision_set(export)
    invalid_evidence["decisions"][0]["evidence_references"] = ["missing.json#pending-a"]
    assert (
        service.validate_decision_set(invalid_evidence)["invalid"][0]["reason"]
        == "evidence_references_invalid"
    )
    before = database.path.stat().st_mtime_ns
    assert service.dry_run(valid)["valid"] is True
    assert database.path.stat().st_mtime_ns == before


def test_atomic_import_is_append_only_and_rolls_back_on_intra_set_stale(audit_library):
    database, service, version_id = audit_library
    export = service.create_export(version_id, mode="review_only", selection={"include_p2": True})
    value = decision_set(export)
    applied = service.apply_decision_set(value)
    assert applied["state"] == "applied" and applied["applied_count"] == 1
    assert candidate_state(database, "pending-a") == "confirmed"
    duplicate = service.validate_decision_set(value)
    assert duplicate["stale"][0]["reason"] == "decision_set_already_imported"
    with database.connect() as connection:
        row = connection.execute(
            "SELECT method,evidence_json FROM document_candidate_decisions "
            "WHERE candidate_id='pending-a' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert row["method"] == "external_audit"
        assert json.loads(row["evidence_json"])["package_logical_hash"] == export["logical_hash"]

    second_export = service.create_export(
        version_id, mode="review_only", selection={"include_p2": True}
    )
    duplicate_target = decision_set(second_export, target_id="pending-b")
    duplicate_target["decisions"].append(dict(duplicate_target["decisions"][0]))
    before_imports = len(service.imports())
    with pytest.raises(LibraryContractError, match="stale"):
        service.apply_decision_set(duplicate_target)
    assert candidate_state(database, "pending-b") == "needs_review"
    assert len(service.imports()) == before_imports


def test_closure_snapshots_are_immutable_stable_and_separate_ai_readiness(audit_library):
    database, service, version_id = audit_library
    first = service.create_snapshot(version_id)
    repeated = service.create_snapshot(version_id)
    assert first["snapshot_hash"] == repeated["snapshot_hash"]
    assert first["ai_readiness"] == "ready_for_ai_with_issues"
    with database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE document_closure_snapshots SET state='ready' WHERE id=?", (first["id"],)
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM document_closure_snapshots WHERE id=?", (first["id"],))
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE document_consolidated_topics SET state='pending',primary_candidate_id=NULL,"
            "identity_resolution='unresolved' "
            "WHERE source_version_id=? AND theme_number=51",
            (version_id,),
        )
    readiness = service.recalculate_readiness(version_id)
    assert readiness["state"] == "blocked"
    blocked = service.create_snapshot(version_id)
    assert blocked["ai_readiness"] == "blocked_for_ai"
    assert blocked["snapshot_hash"] != first["snapshot_hash"]


def test_genuinely_unresolved_topic_still_blocks_ai_readiness(audit_library):
    database, service, version_id = audit_library
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE document_consolidated_topics SET state='pending',primary_candidate_id=NULL,"
            "identity_resolution='unresolved',identity_evidence_candidate_id=NULL,"
            "resolution_method=NULL WHERE source_version_id=? AND theme_number=51",
            (version_id,),
        )
    readiness = service.recalculate_readiness(version_id)
    assert readiness["state"] == "blocked"
    assert readiness["blockers"] == ["principal_topics_unresolved:1"]
    assert service.closure_status(version_id)["ai_readiness"] == "blocked_for_ai"


def test_external_index_only_resolution_keeps_primary_null_and_changes_only_blocker(
    audit_library,
):
    database, service, version_id = audit_library
    with database.transaction(immediate=True) as connection:
        topic = connection.execute(
            "SELECT id FROM document_consolidated_topics WHERE source_version_id=? "
            "AND theme_number=1",
            (version_id,),
        ).fetchone()
        connection.execute(
            "DELETE FROM document_consolidated_nodes WHERE source_version_id=? "
            "AND theme_number=1 AND node_type='topic_heading'",
            (version_id,),
        )
        connection.execute(
            "UPDATE document_consolidated_topics SET state='pending',primary_candidate_id=NULL,"
            "pdf_page_number=NULL,printed_page=NULL,identity_resolution='unresolved',"
            "identity_evidence_candidate_id=NULL,resolution_method=NULL,"
            "issues_json='[\"principal_topic_evidence_insufficient\"]' WHERE id=?",
            (topic["id"],),
        )
    blocked = service.recalculate_readiness(version_id)
    old_snapshot = service.create_snapshot(version_id)
    export = service.create_export(
        version_id,
        mode="targeted",
        selection={"theme_number": 1},
    )
    value = index_only_decision_set(export, topic["id"], "index-1")
    assert service.validate_decision_set(value)["valid"] is True
    with database.connect() as connection:
        protected_before = tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM chunks),(SELECT count(*) FROM embeddings),"
                "(SELECT activation_state FROM source_versions WHERE id=?),"
                "(SELECT is_active FROM source_versions WHERE id=?)",
                (version_id, version_id),
            ).fetchone()
        )
        queue_before = dict(
            connection.execute(
                "SELECT coalesce(s.priority,'P2'),count(*) FROM "
                "document_candidate_review_state s JOIN document_structure_candidates c "
                "ON c.id=s.candidate_id WHERE c.source_version_id=? AND "
                "s.editorial_state IN ('needs_review','conflicted') GROUP BY 1",
                (version_id,),
            ).fetchall()
        )
        hierarchy_before = [
            tuple(row)
            for row in connection.execute(
                "SELECT id,source_candidate_id,parent_node_id,theme_number,state "
                "FROM document_consolidated_nodes WHERE source_version_id=? ORDER BY id",
                (version_id,),
            ).fetchall()
        ]
        children_before = connection.execute(
            "SELECT count(*) FROM document_consolidated_nodes WHERE source_version_id=? "
            "AND theme_number BETWEEN 2 AND 51",
            (version_id,),
        ).fetchone()[0]

    applied = service.apply_decision_set(value)
    new_snapshot = service.create_snapshot(version_id)
    with database.connect() as connection:
        resolved = connection.execute(
            "SELECT * FROM document_consolidated_topics WHERE id=?", (topic["id"],)
        ).fetchone()
        evidence = json.loads(resolved["evidence_json"])
        assert resolved["state"] == "confirmed"
        assert resolved["identity_resolution"] == "index_only_no_direct_practice"
        assert resolved["identity_evidence_candidate_id"] == "index-1"
        assert resolved["resolution_method"] == "external_audit"
        assert resolved["primary_candidate_id"] is None
        assert json.loads(resolved["issues_json"]) == []
        assert evidence["identity_source"] == "index"
        assert evidence["principal_body_heading_required"] is False
        assert evidence["direct_exercises_expected"] is False
        assert evidence["direct_solutions_expected"] is False
        index_state = connection.execute(
            "SELECT editorial_state,reason FROM document_candidate_review_state "
            "WHERE candidate_id='index-1'"
        ).fetchone()
        assert tuple(index_state) == ("rejected", "index_entry_not_principal_topic")
        decision = connection.execute(
            "SELECT method,previous_state,new_state,evidence_json FROM "
            "document_candidate_decisions WHERE candidate_id='index-1' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert tuple(decision)[:3] == ("external_audit", "rejected", "rejected")
        ledger_evidence = json.loads(decision["evidence_json"])
        assert ledger_evidence["target_id"] == topic["id"]
        assert ledger_evidence["package_logical_hash"] == export["logical_hash"]
        assert (
            connection.execute(
                "SELECT count(*) FROM document_consolidated_nodes WHERE "
                "source_candidate_id='index-1'"
            ).fetchone()[0]
            == 0
        )
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT id,source_candidate_id,parent_node_id,theme_number,state "
                "FROM document_consolidated_nodes WHERE source_version_id=? ORDER BY id",
                (version_id,),
            ).fetchall()
        ] == hierarchy_before
        assert (
            connection.execute(
                "SELECT count(*) FROM document_consolidated_nodes WHERE source_version_id=? "
                "AND theme_number BETWEEN 2 AND 51",
                (version_id,),
            ).fetchone()[0]
            == children_before
        )
        protected_after = tuple(
            connection.execute(
                "SELECT (SELECT count(*) FROM chunks),(SELECT count(*) FROM embeddings),"
                "(SELECT activation_state FROM source_versions WHERE id=?),"
                "(SELECT is_active FROM source_versions WHERE id=?)",
                (version_id, version_id),
            ).fetchone()
        )
        queue_after = dict(
            connection.execute(
                "SELECT coalesce(s.priority,'P2'),count(*) FROM "
                "document_candidate_review_state s JOIN document_structure_candidates c "
                "ON c.id=s.candidate_id WHERE c.source_version_id=? AND "
                "s.editorial_state IN ('needs_review','conflicted') GROUP BY 1",
                (version_id,),
            ).fetchall()
        )
    assert applied["state"] == "applied"
    assert blocked["blockers"] == ["principal_topics_unresolved:1"]
    assert applied["impact"]["readiness"] == "structurally_ready_with_issues"
    assert service.closure_status(version_id)["ai_readiness"] == "ready_for_ai_with_issues"
    assert protected_after == protected_before
    assert queue_after == queue_before
    assert old_snapshot["state"] == "blocked"
    assert service.snapshot(old_snapshot["id"])["state"] == "blocked"
    assert new_snapshot["id"] != old_snapshot["id"]
    assert new_snapshot["state"] == "ready_with_issues"
    assert new_snapshot["snapshot_hash"] != old_snapshot["snapshot_hash"]


def test_audit_operations_never_activate_or_create_ocr_chunks_or_embeddings(audit_library):
    database, service, version_id = audit_library

    def protected_state():
        with database.connect() as connection:
            version = connection.execute(
                "SELECT activation_state,is_active FROM source_versions WHERE id=?",
                (version_id,),
            ).fetchone()
            return {
                "activation": tuple(version),
                "chunks": connection.execute("SELECT count(*) FROM chunks").fetchone()[0],
                "embeddings": connection.execute("SELECT count(*) FROM embeddings").fetchone()[0],
                "runs": connection.execute(
                    "SELECT count(*) FROM document_processing_runs WHERE source_version_id=?",
                    (version_id,),
                ).fetchone()[0],
                "pages": connection.execute(
                    "SELECT count(*) FROM document_pages WHERE source_version_id=?",
                    (version_id,),
                ).fetchone()[0],
            }

    before = protected_state()
    export = service.create_export(version_id, mode="review_only", selection={"include_p2": True})
    value = decision_set(export)
    assert service.dry_run(value)["valid"] is True
    service.apply_decision_set(value)
    service.create_snapshot(version_id)
    assert protected_state() == before
    with zipfile.ZipFile(service.archive_path(export["id"])) as archive:
        provenance = json.loads(archive.read("provenance.json"))
    assert {
        key: provenance[key]
        for key in (
            "ocr_executed",
            "vision_ai_executed",
            "llm_executed",
            "chunks_created",
            "embeddings_created",
            "activation_changed",
        )
    } == {
        "ocr_executed": False,
        "vision_ai_executed": False,
        "llm_executed": False,
        "chunks_created": False,
        "embeddings_created": False,
        "activation_changed": False,
    }


@pytest.mark.anyio
async def test_audit_api_preview_export_dry_run_and_snapshots(audit_library):
    _, service, version_id = audit_library
    app.dependency_overrides[get_document_audit] = lambda: service
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            preview = await client.post(
                f"/api/library/laboratory/audit/versions/{version_id}/exports/preview",
                json={"mode": "review_only", "selection": {"include_p2": True}},
            )
            assert preview.status_code == 200, preview.text
            created = await client.post(
                f"/api/library/laboratory/audit/versions/{version_id}/exports",
                json={"mode": "review_only", "selection": {"include_p2": True}},
            )
            assert created.status_code == 201, created.text
            export = created.json()
            value = decision_set(export)
            validated = await client.post(
                "/api/library/laboratory/audit/imports/validate", json=value
            )
            dry_run = await client.post("/api/library/laboratory/audit/imports/dry-run", json=value)
            applied = await client.post("/api/library/laboratory/audit/imports/apply", json=value)
            recalculated = await client.post(
                f"/api/library/laboratory/audit/versions/{version_id}/readiness/recalculate"
            )
            assert validated.status_code == 200 and validated.json()["valid"] is True
            assert dry_run.status_code == 200 and dry_run.json()["valid"] is True
            assert applied.status_code == 201, applied.text
            assert recalculated.status_code == 200, recalculated.text
            status = await client.get(
                f"/api/library/laboratory/audit/versions/{version_id}/closure"
            )
            snapshot = await client.post(
                f"/api/library/laboratory/audit/versions/{version_id}/snapshots"
            )
            assert status.status_code == 200
            assert snapshot.status_code == 201, snapshot.text
            assert (await client.get("/api/library/laboratory/audit/exports")).status_code == 200
            assert (
                await client.get(f"/api/library/laboratory/audit/exports/{export['id']}")
            ).status_code == 200
            assert (
                await client.get(f"/api/library/laboratory/audit/exports/{export['id']}/manifest")
            ).status_code == 200
            assert (
                await client.get(f"/api/library/laboratory/audit/exports/{export['id']}/download")
            ).status_code == 200
            assert (await client.get("/api/library/laboratory/audit/imports")).status_code == 200
            assert (
                await client.get(f"/api/library/laboratory/audit/imports/{applied.json()['id']}")
            ).status_code == 200
            assert (await client.get("/api/library/laboratory/audit/snapshots")).status_code == 200
            assert (
                await client.get(f"/api/library/laboratory/audit/snapshots/{snapshot.json()['id']}")
            ).status_code == 200
    finally:
        app.dependency_overrides.pop(get_document_audit, None)


@pytest.mark.anyio
async def test_audit_get_endpoints_remain_read_only(audit_library):
    database, service, version_id = audit_library
    export = service.create_export(version_id, mode="review_only")
    snapshot = service.create_snapshot(version_id)
    app.dependency_overrides[get_document_audit] = lambda: service

    def mutation_counts():
        with database.connect() as connection:
            return tuple(
                connection.execute(
                    "SELECT (SELECT count(*) FROM document_audit_exports),"
                    "(SELECT count(*) FROM document_audit_imports),"
                    "(SELECT count(*) FROM document_candidate_decisions),"
                    "(SELECT count(*) FROM document_readiness_snapshots),"
                    "(SELECT count(*) FROM document_closure_snapshots)"
                ).fetchone()
            )

    before = mutation_counts()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = [
                await client.get("/api/library/laboratory/audit/exports"),
                await client.get(f"/api/library/laboratory/audit/exports/{export['id']}"),
                await client.get(f"/api/library/laboratory/audit/exports/{export['id']}/manifest"),
                await client.get("/api/library/laboratory/audit/imports"),
                await client.get(f"/api/library/laboratory/audit/versions/{version_id}/closure"),
                await client.get("/api/library/laboratory/audit/snapshots"),
                await client.get(f"/api/library/laboratory/audit/snapshots/{snapshot['id']}"),
            ]
            assert all(response.status_code == 200 for response in responses)
    finally:
        app.dependency_overrides.pop(get_document_audit, None)
    assert mutation_counts() == before
