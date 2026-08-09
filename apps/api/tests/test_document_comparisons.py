from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from deutschos_api.core.config import Settings
from deutschos_api.educational_library.comparisons import (
    DocumentComparisonService,
    normalize_text,
    text_difference,
    text_fingerprint,
    visual_fingerprint,
)
from deutschos_api.educational_library.dependencies import (
    get_document_comparisons,
    get_library_service,
)
from deutschos_api.educational_library.schemas import (
    CorrespondenceAdjust,
    LibraryBusyError,
    LibraryContractError,
    VersionComparisonCreate,
)
from deutschos_api.educational_library.service import EducationalLibraryService
from deutschos_api.main import app

PDF_FIXTURES = Path(__file__).parent / "fixtures" / "page_comparisons"


@pytest.fixture
def comparison_library(tmp_path: Path):
    settings = Settings(
        database_url="sqlite://",
        educational_materials_dir=tmp_path / "materials",
        educational_library_runtime_dir=tmp_path / "runtime",
        educational_library_scan_on_startup=False,
        educational_library_embedding_model="",
    )
    settings.educational_materials_dir.mkdir()
    return EducationalLibraryService(settings)


def seed_versions(library, *, different_source: bool = False) -> tuple[int, int]:
    now = "2026-07-30T12:00:00+00:00"
    with library.database.transaction(immediate=True) as connection:
        for source_id in ["fixture-source", *(["other-source"] if different_source else [])]:
            connection.execute(
                "INSERT INTO sources(id,current_path,name,kind,format,size_bytes,mtime_ns,"
                "current_hash,status,processing_state,first_seen_at,last_seen_at,document_state) "
                "VALUES (?,?,?,?,?,10,1,?,'present','pending',?,?,'candidate')",
                (source_id, f"{source_id}.pdf", source_id, "document", ".pdf", "a" * 64, now, now),
            )
        ids = []
        for number, digest in [(1, "a" * 64), (2, "b" * 64)]:
            source_id = "other-source" if different_source and number == 2 else "fixture-source"
            cursor = connection.execute(
                "INSERT INTO source_versions(source_id,version_number,content_hash,size_bytes,"
                "mtime_ns,processing_state,created_at,detected_at,page_count,document_state,"
                "availability_state,extraction_state,chunk_state,embedding_state,activation_state,"
                "version_provenance) VALUES (?,?,?,10,1,'pending',?,?,10,'candidate','present',"
                "'pending','pending','pending','candidate','fixture')",
                (source_id, number, digest, now, now),
            )
            ids.append(int(cursor.lastrowid))
    return ids[0], ids[1]


def request(base: int, target: int, configuration=None):
    return VersionComparisonCreate(
        base_source_version_id=base,
        target_source_version_id=target,
        algorithm_version="page-match.v1",
        configuration=configuration or {"window": 12, "minimum_score": 0.5},
        initiated_by="pytest",
    )


def add_page(service, version, number, label, *, text=True, regions=None, width=595, height=842):
    render = label.encode()
    return service.upsert_page_artifact(
        version,
        number,
        text=f"Text {label} Grüße ß" if text else None,
        render=render,
        width_points=width,
        height_points=height,
        printed_page_number=str(number),
        regions={key: value.encode() for key, value in (regions or {}).items()},
    )


def test_model_identity_hash_idempotency_stale_and_rollback(comparison_library):
    base, target = seed_versions(comparison_library)
    service = DocumentComparisonService(comparison_library.database)
    first = service.create(request(base, target))
    reused = service.create(request(base, target))
    assert reused.id == first.id
    with pytest.raises(LibraryBusyError):
        service.create(request(base, target, {"window": 4}))
    assert first.base_hash == "a" * 64
    assert first.target_hash == "b" * 64
    assert (
        first.configuration_hash == hashlib.sha256(b'{"minimum_score":0.5,"window":12}').hexdigest()
    )

    with comparison_library.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE source_versions SET content_hash=? WHERE id=?", ("c" * 64, target)
        )
    assert service.execute(first.id).state == "stale"

    database = comparison_library.database
    assert database.rollback_version_12() == 11
    assert database.rollback_version_11() == 10
    assert database.rollback_version_10() == 9
    assert database.rollback_version_9() == 8
    assert database.rollback_version_8() == 7
    assert database.migrate() == 12
    assert database.integrity() == ("ok", [])


def test_completed_comparison_is_historical_and_recalculation_is_explicit(
    comparison_library,
):
    base, target = seed_versions(comparison_library)
    service = DocumentComparisonService(comparison_library.database)
    add_page(service, base, 1, "same")
    add_page(service, target, 1, "same")
    comparison = service.create(request(base, target))
    service.execute(comparison.id)
    with pytest.raises(LibraryContractError):
        service.execute(comparison.id)
    recalculated = service.execute(comparison.id, recalculate=True)
    assert recalculated.id == comparison.id
    newer = service.create(request(base, target))
    assert newer.id != comparison.id
    assert newer.revision == 2


def test_rejects_same_version_cross_source_and_ocr_configuration(comparison_library):
    base, target = seed_versions(comparison_library, different_source=True)
    service = DocumentComparisonService(comparison_library.database)
    with pytest.raises(LibraryContractError):
        service.create(request(base, target))
    with pytest.raises(ValueError):
        request(base, base)
    with pytest.raises(ValueError):
        request(base, target, {"ocr": True})


def test_one_to_one_reorder_text_diff_and_no_text(comparison_library):
    base, target = seed_versions(comparison_library)
    service = DocumentComparisonService(comparison_library.database)
    for number, label in enumerate(["a", "b", "c", "d"], 1):
        add_page(service, base, number, label, text=number != 4)
    for number, label in enumerate(["a", "c", "b", "d"], 1):
        add_page(service, target, number, label, text=number != 4)
    comparison = service.create(request(base, target))
    completed = service.execute(comparison.id)
    relations = service.correspondences(comparison.id, page_size=100).items
    one_to_one = [item for item in relations if item.relation_type == "one_to_one"]
    assert len(one_to_one) == 4
    reordered = [item for item in one_to_one if item.evidence["signals"]["order_changed"]]
    assert len(reordered) == 2
    no_text = next(item for item in one_to_one if item.base_pages == [4])
    assert no_text.text_difference["message"] == "Sin texto disponible"
    assert no_text.relation_type != "blank"
    assert completed.summary["pages_without_text"] == 2


def test_double_page_split_and_combine(comparison_library):
    base, target = seed_versions(comparison_library)
    service = DocumentComparisonService(comparison_library.database)
    add_page(service, base, 1, "cover")
    add_page(service, base, 2, "spread", regions={"left": "p1", "right": "p2"}, width=1190)
    add_page(service, base, 3, "back")
    for number, label in enumerate(["cover", "p1", "p2", "back"], 1):
        add_page(service, target, number, label)
    comparison = service.create(request(base, target))
    service.execute(comparison.id)
    relations = service.correspondences(comparison.id, page_size=100).items
    split = next(item for item in relations if item.relation_type == "one_to_many")
    assert split.base_pages == [2]
    assert split.target_pages == [2, 3]
    assert split.split_similarity == 1
    assert split.review_state == "needs_review"

    inverse = service.create(request(target, base, {"window": 8, "minimum_score": 0.5}))
    service.execute(inverse.id)
    assert any(
        item.relation_type == "many_to_one"
        for item in service.correspondences(inverse.id, page_size=100).items
    )


def test_insert_delete_ambiguity_and_unforced_coverage(comparison_library):
    base, target = seed_versions(comparison_library)
    service = DocumentComparisonService(comparison_library.database)
    add_page(service, base, 1, "stable")
    add_page(service, base, 2, "removed")
    add_page(service, base, 3, "new")
    add_page(service, target, 1, "stable")
    add_page(service, target, 2, "new")
    add_page(service, target, 3, "new")
    comparison = service.create(request(base, target, {"window": 4, "minimum_score": 0.8}))
    service.execute(comparison.id)
    relations = service.correspondences(comparison.id, page_size=100).items
    assert any(item.relation_type == "deleted" for item in relations)
    assert any(item.relation_type == "inserted" for item in relations)
    assert any(
        item.relation_type == "ambiguous" and item.review_state == "needs_review"
        for item in relations
    )


def test_manual_nm_audit_conflicts_recalculation_and_transfer_plan(comparison_library):
    base, target = seed_versions(comparison_library)
    service = DocumentComparisonService(comparison_library.database)
    for version in (base, target):
        for number in range(1, 5):
            add_page(service, version, number, f"{version}-{number}")
    comparison = service.create(request(base, target, {"minimum_score": 0.99}))
    service.execute(comparison.id)
    original = service.correspondences(comparison.id, page_size=100).items[0]
    adjusted = service.adjust(
        original.id,
        CorrespondenceAdjust(
            base_pages=[1, 2],
            target_pages=[2, 3],
            relation_type="many_to_many",
            actor="reviewer",
            note="Bloque reorganizado",
        ),
    )
    assert adjusted.review_state == "manually_adjusted"
    assert adjusted.base_pages == [1, 2]
    conflicting_source = next(
        item
        for item in service.correspondences(comparison.id, page_size=100).items
        if item.id != adjusted.id
    )
    with pytest.raises(LibraryBusyError):
        service.adjust(
            conflicting_source.id,
            CorrespondenceAdjust(
                base_pages=[1],
                target_pages=[4],
                relation_type="one_to_one",
                actor="reviewer",
            ),
        )
    service.execute(comparison.id, recalculate=True)
    assert service.correspondence(adjusted.id).review_state == "manually_adjusted"
    events = service.events(comparison.id)
    assert any(event.event_type == "manual_adjustment" for event in events)
    plan = service.transfer_plan(comparison.id)
    assert plan.plan["executable"] is False
    assert any(item["chunks"] == "regenerate" for item in plan.plan["items"])


def test_fingerprints_and_text_anomalies_are_deterministic_and_conservative():
    assert normalize_text("  Grüße  \n  weiß  ") == "Grüße\nweiß"
    assert "ß" in normalize_text("groß")
    assert "ä" in normalize_text("spät")
    assert normalize_text("feler") == "feler"
    assert normalize_text("links\nrechts") == "links\nrechts"
    assert text_fingerprint("Grüße")["hash"] == text_fingerprint("Grüße")["hash"]
    assert text_fingerprint("Grüße")["hash"] != text_fingerprint("Grusse")["hash"]
    first = visual_fingerprint(b"render-a")
    assert first["hash"] == visual_fingerprint(b"render-a")["hash"]
    assert first["hash"] != visual_fingerprint(b"render-b")["hash"]
    diff = text_difference("Gr\ufffdße Wör-\nter", "Grüße Wörter und")
    assert diff["target_replacement_characters"] < diff["base_replacement_characters"]
    assert diff["target_split_words"] < diff["base_split_words"]
    assert diff["recommendation"] == "target_preferred"
    assert text_difference("eins", "eins zwei")["recommendation"] != "target_preferred"


def test_versioned_pdf_fixtures_are_small_synthetic_and_complete():
    fixtures = sorted(PDF_FIXTURES.glob("fixture_*.pdf"))
    assert len(fixtures) == 14
    assert {path.stem.rsplit("_", 1)[0] for path in fixtures} == {
        f"fixture_{letter}" for letter in "abcdefg"
    }
    for fixture in fixtures:
        content = fixture.read_bytes()
        assert content.startswith(b"%PDF-")
        assert len(content) < 10_000
        assert b"Herder" not in content


@pytest.mark.anyio
async def test_api_gets_are_read_only_filters_pagination_and_review(comparison_library):
    base, target = seed_versions(comparison_library)
    service = DocumentComparisonService(comparison_library.database)
    add_page(service, base, 1, "same")
    add_page(service, target, 1, "same")
    app.dependency_overrides[get_document_comparisons] = lambda: service
    app.dependency_overrides[get_library_service] = lambda: comparison_library
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/library/laboratory/comparisons",
            json=request(base, target).model_dump(mode="json"),
        )
        assert created.status_code == 201
        comparison_id = created.json()["id"]
        executed = await client.post(f"/api/library/laboratory/comparisons/{comparison_id}/execute")
        assert executed.status_code == 200
        before = len(service.events(comparison_id))
        listed = await client.get(
            f"/api/library/laboratory/comparisons/{comparison_id}/correspondences",
            params={"confidence": "very_high", "page_size": 1},
        )
        assert listed.status_code == 200
        assert listed.json()["page_size"] == 1
        relation_id = listed.json()["items"][0]["id"]
        assert len(service.events(comparison_id)) == before
        confirmed = await client.post(
            f"/api/library/laboratory/comparisons/{comparison_id}/correspondences/"
            f"{relation_id}/confirm",
            json={"actor": "api-reviewer", "note": "fixture"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["review_state"] == "confirmed"
        assert "/Volumes/" not in confirmed.text
        events = await client.get(f"/api/library/laboratory/comparisons/{comparison_id}/events")
        confirm_event = next(event for event in events.json() if event["event_type"] == "confirm")
        reverted = await client.post(
            f"/api/library/laboratory/comparisons/{comparison_id}/events/"
            f"{confirm_event['id']}/revert",
            json={"actor": "api-reviewer", "note": "undo fixture"},
        )
        assert reverted.status_code == 200
        assert reverted.json()["event_type"] == "decision_reverted"
        assert service.correspondence(relation_id).review_state == "auto_supported"
        thumbnail = await client.get(
            f"/api/library/laboratory/comparisons/{comparison_id}/correspondences/"
            f"{relation_id}/thumbnail/base/0"
        )
        assert thumbnail.status_code == 200
        assert thumbnail.headers["content-type"].startswith("image/svg+xml")
        assert "/Volumes/" not in thumbnail.text
    app.dependency_overrides.clear()
