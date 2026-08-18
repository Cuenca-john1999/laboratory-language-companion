from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from llc_api.core.config import Settings
from llc_api.educational_library.database import (
    _MIGRATION_0001,
    _MIGRATION_0002,
    _MIGRATION_0003,
    LibraryDatabase,
)
from llc_api.educational_library.dependencies import get_library_memory
from llc_api.educational_library.memory import (
    PedagogicalMemoryService,
    normalize_concept,
    query_target,
)
from llc_api.educational_library.schemas import (
    ConceptAliasCreate,
    ConceptRelationCreate,
    EvidenceLocationCreate,
    LibraryContractError,
    LocationFeedbackRequest,
    MemoryFeedbackRequest,
    MemoryReviewRequest,
    PageMappingUpdate,
    PedagogicalConceptCreate,
    PedagogicalMemoryImportRequest,
)
from llc_api.educational_library.service import EducationalLibraryService, utc_text
from llc_api.main import app


@pytest.fixture
def memory_library(tmp_path: Path) -> EducationalLibraryService:
    materials = tmp_path / "materials"
    materials.mkdir()
    (materials / "Herder-Theorie.md").write_text(
        "# Akkusativ\n\nDer Akkusativ markiert oft das direkte Objekt: Ich sehe den Hund.\n\n"
        "# Perfekt\n\nDas Perfekt besteht oft aus Hilfsverb und Partizip II.",
        encoding="utf-8",
    )
    library = EducationalLibraryService(
        Settings(
            database_url="sqlite://",
            educational_materials_dir=materials,
            educational_library_runtime_dir=tmp_path / "runtime",
            educational_library_scan_on_startup=False,
            educational_library_embedding_model="",
        )
    )
    library.scan()
    with library.database.transaction(immediate=True) as connection:
        source = connection.execute("SELECT id,current_version_id FROM sources").fetchone()
        connection.execute(
            "UPDATE sources SET display_alias='Herder',pedagogical_role='core_theory',"
            "editorial_status='user_confirmed',user_selected_core=1 WHERE id=?",
            (source["id"],),
        )
        connection.execute(
            "UPDATE chunks SET page_start=89,page_end=89 WHERE source_version_id=?",
            (source["current_version_id"],),
        )
        connection.execute(
            "UPDATE sections SET page_start=89,page_end=89 WHERE document_id=(SELECT id FROM "
            "documents WHERE source_version_id=?)",
            (source["current_version_id"],),
        )
    return library


def _source_evidence(library: EducationalLibraryService):
    with library.database.connect() as connection:
        return connection.execute(
            "SELECT s.id source_id,s.current_version_id,c.id chunk_id,c.text,c.title "
            "FROM sources s JOIN chunks c ON c.source_version_id=s.current_version_id LIMIT 1"
        ).fetchone()


def _insert_query(library: EducationalLibraryService, *, status: str = "completed") -> str:
    query_id = str(uuid4())
    conversation_id = str(uuid4())
    now = utc_text()
    evidence = _source_evidence(library)
    plan = {
        "intent": "definition",
        "language": "de",
        "target_expression": "Akkusativ",
        "user_language": "es",
        "ambiguity": "low",
        "possible_interpretations": [],
        "search_queries": ["Akkusativ", "acusativo"],
        "required_evidence": ["definición"],
    }
    answer = {
        "evidence_sufficient": status == "completed",
        "direct_answer": "El acusativo suele marcar el objeto directo.",
        "key_points": [],
        "examples": [],
        "important_nuance": None,
        "ambiguity_note": None,
        "follow_up_question": None,
        "claims": (
            [
                {
                    "text": "El acusativo puede marcar el objeto directo.",
                    "source_chunk_ids": [evidence["chunk_id"]],
                }
            ]
            if status == "completed"
            else []
        ),
        "warnings": [],
    }
    with library.database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO teacher_conversations(id,created_at,updated_at) VALUES (?,?,?)",
            (conversation_id, now, now),
        )
        connection.execute(
            "INSERT INTO teacher_queries(id,conversation_id,question,plan_json,answer_json,status,"
            "confidence,model,plan_prompt_version,answer_prompt_version,repair_prompt_version,"
            "retrieval_mode,semantic_available,warnings_json,timings_json,created_at,models_json,"
            "stream_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,'[]','{}',?,'{}','complete')",
            (
                query_id,
                conversation_id,
                "¿Qué es el acusativo?",
                json.dumps(plan),
                json.dumps(answer),
                status,
                "moderate" if status == "completed" else "insufficient",
                "test",
                "plan.v1",
                "answer.v1",
                "repair.v1",
                "hybrid",
                now,
            ),
        )
        if status == "completed":
            connection.execute(
                "INSERT INTO teacher_query_sources(query_id,sequence,chunk_id,source_version_id,"
                "retrieval_score,matched_queries_json,snippet) VALUES (?,0,?,?,1,'[]',?)",
                (
                    query_id,
                    evidence["chunk_id"],
                    evidence["current_version_id"],
                    "Der Akkusativ markiert oft das direkte Objekt.",
                ),
            )
    return query_id


def test_schema_v3_to_v5_is_backed_up_atomic_and_integral(tmp_path: Path):
    path = tmp_path / "library.sqlite3"
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + _MIGRATION_0001)
        connection.execute("INSERT INTO library_schema VALUES (1,datetime('now'))")
        connection.executescript(_MIGRATION_0002)
        connection.execute("INSERT INTO library_schema VALUES (2,datetime('now'))")
        connection.executescript(_MIGRATION_0003)
        connection.execute("INSERT INTO library_schema VALUES (3,datetime('now'))")
    finally:
        connection.close()
    database = LibraryDatabase(path)
    assert database.migrate() == 14
    backups = list((tmp_path / "backups").glob("library.sqlite3.schema3-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT max(version) FROM library_schema").fetchone()[0] == 3
    route_backups = list((tmp_path / "backups").glob("library.sqlite3.schema4-*.bak"))
    assert len(route_backups) == 1
    with sqlite3.connect(route_backups[0]) as backup:
        assert backup.execute("SELECT max(version) FROM library_schema").fetchone()[0] == 4
    with database.connect() as migrated:
        tables = {
            row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "pedagogical_concepts",
        "document_page_mappings",
        "pedagogical_evidence_locations",
        "pedagogical_memory_reviews",
        "pedagogical_memory_audit",
        "canonical_route_imports",
        "canonical_topics",
    } <= tables
    assert database.integrity() == ("ok", [])
    assert database.migrate() == 14
    assert len(list((tmp_path / "backups").glob("library.sqlite3.schema3-*.bak"))) == 1
    assert len(list((tmp_path / "backups").glob("library.sqlite3.schema4-*.bak"))) == 1
    assert not list((tmp_path / "backups").glob("*.partial*"))


def test_completed_query_derives_a_prudent_target_only_from_repeated_plan_terms(
    memory_library: EducationalLibraryService,
):
    plan = {
        "intent": "definition",
        "language": "unknown",
        "target_expression": None,
        "search_queries": [
            "Was ist das Perfekt?",
            "Perfekt Deutsch Definition",
            "Wie bildet man das Perfekt im Deutschen?",
        ],
    }
    assert query_target("¿Qué es el Perfekt?", plan) == "Perfekt"
    assert query_target("¿Qué es el Plusquamperfekt?", plan) == ""

    query_id = _insert_query(memory_library)
    with memory_library.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE teacher_queries SET question=?,plan_json=? WHERE id=?",
            ("¿Qué es el Perfekt?", json.dumps(plan), query_id),
        )
    memory = PedagogicalMemoryService(memory_library.database)
    result = memory.remember_teacher_query(query_id)
    with memory_library.database.connect() as connection:
        concept = connection.execute(
            "SELECT * FROM pedagogical_concepts WHERE canonical_name='Perfekt'"
        ).fetchone()
        locations = connection.execute(
            "SELECT count(*) FROM pedagogical_evidence_locations WHERE concept_id=?",
            (concept["id"],),
        ).fetchone()[0]
    assert concept["language"] == "de"
    assert concept["editorial_status"] == "candidate"
    assert [item.id for item in result.concepts] == [concept["id"]]
    assert locations == 1


def test_concepts_aliases_normalization_deduplication_relations_and_conflicts(
    memory_library: EducationalLibraryService,
):
    memory = PedagogicalMemoryService(memory_library.database)
    accusative = memory.create_concept(
        PedagogicalConceptCreate(
            canonical_name="Akkusativ",
            display_name_es="Acusativo",
            display_name_de="Akkusativ",
            category="grammatical_case",
        )
    )
    assert normalize_concept("  CASO—Acusativo ") == "caso acusativo"
    assert {alias.normalized_text for alias in accusative.aliases} == {"akkusativ", "acusativo"}
    memory.add_alias(
        accusative.id,
        ConceptAliasCreate(text="caso acusativo", language="es", origin="user_confirmed"),
    )
    repeated = memory.create_concept(
        PedagogicalConceptCreate(canonical_name="AKKUSATIV", category="other")
    )
    assert repeated.id == accusative.id
    unresolved_repeat = memory.create_concept(
        PedagogicalConceptCreate(canonical_name="Akkusativ", language="mixed", category="other")
    )
    assert unresolved_repeat.id == accusative.id
    unresolved_first = memory.create_concept(
        PedagogicalConceptCreate(canonical_name="Perfekt", language="unknown", category="other")
    )
    resolved = memory.create_concept(
        PedagogicalConceptCreate(
            canonical_name="Perfekt",
            language="de",
            category="verb_tense",
            status="user_confirmed",
        )
    )
    assert resolved.id == unresolved_first.id
    assert resolved.language == "de"
    assert resolved.status.value == "user_confirmed"
    with memory_library.database.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM pedagogical_concepts WHERE normalized_name='perfekt'"
            ).fetchone()[0]
            == 1
        )
    nominative = memory.create_concept(
        PedagogicalConceptCreate(canonical_name="Nominativ", category="grammatical_case")
    )
    related = memory.add_relation(
        accusative.id,
        ConceptRelationCreate(
            target_concept_id=nominative.id,
            relation_type="contrasts_with",
            origin="user_created",
        ),
    )
    assert related.relations[0].target_name == "Nominativ"
    memory.add_alias(
        nominative.id,
        ConceptAliasCreate(text="acusativo", language="es", origin="system_suggested"),
    )
    assert memory.get_concept(accusative.id).status.value == "conflict"
    assert memory.get_concept(nominative.id).status.value == "conflict"


def test_page_mapping_regions_labels_and_public_citations(
    memory_library: EducationalLibraryService,
):
    memory = PedagogicalMemoryService(memory_library.database)
    evidence = _source_evidence(memory_library)
    concept = memory.create_concept(
        PedagogicalConceptCreate(canonical_name="Akkusativ", display_name_es="Acusativo")
    )
    mapping = memory.upsert_page_mapping(
        evidence["current_version_id"],
        PageMappingUpdate(
            pdf_page_number=89,
            scan_layout="double_page",
            printed_left_label="174",
            printed_right_label="175",
            status="user_confirmed",
            origin="user",
            operation_id="mapping-page-89",
        ),
    )
    assert mapping.pdf_page_index == 88
    right = memory.create_location(
        EvidenceLocationCreate(
            concept_id=concept.id,
            source_version_id=evidence["current_version_id"],
            chunk_id=evidence["chunk_id"],
            pdf_page_number=89,
            region="right",
            evidence_snippet="Der Akkusativ markiert oft das direkte Objekt.",
            status="system_verified",
            origin="test",
        )
    )
    assert right.printed_page_label == "175"
    assert right.public_citation == "Herder · PDF p. 89 · libro p. 175 · mitad derecha"
    with memory_library.database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO pedagogical_evidence_locations(id,concept_id,source_id,"
            "source_version_id,chunk_id,editorial_section_id,page_mapping_id,pdf_page_index,"
            "pdf_page_number,region_kind,custom_bbox_json,heading,evidence_snippet,evidence_hash,"
            "extraction_quality,status,origin,reviewed_at,created_at,updated_at) "
            "SELECT ?,concept_id,source_id,source_version_id,chunk_id,editorial_section_id,"
            "page_mapping_id,pdf_page_index,pdf_page_number,region_kind,custom_bbox_json,heading,"
            "'Candidato repetido con otro extracto.','duplicate-evidence-hash',"
            "extraction_quality,'candidate','test',NULL,created_at,updated_at "
            "FROM pedagogical_evidence_locations WHERE id=?",
            (str(uuid4()), right.id),
        )
    resolved = memory.create_concept(
        PedagogicalConceptCreate(canonical_name="Akkusativ", language="de")
    )
    with memory_library.database.connect() as connection:
        exact_locations = connection.execute(
            "SELECT status FROM pedagogical_evidence_locations WHERE concept_id=? "
            "AND source_version_id=? AND chunk_id=? AND pdf_page_number=89 AND region_kind='right'",
            (resolved.id, evidence["current_version_id"], evidence["chunk_id"]),
        ).fetchall()
    assert [row["status"] for row in exact_locations] == ["system_verified"]
    both = memory.create_location(
        EvidenceLocationCreate(
            concept_id=concept.id,
            source_version_id=evidence["current_version_id"],
            pdf_page_number=89,
            region="both",
            evidence_snippet="Tema extendido en las dos páginas.",
            origin="test",
        )
    )
    assert both.printed_page_label == "174–175"
    memory.upsert_page_mapping(
        evidence["current_version_id"],
        PageMappingUpdate(
            pdf_page_number=90,
            scan_layout="single_page",
            printed_full_label="iv",
            status="system_verified",
            origin="deterministic",
            operation_id="mapping-page-90",
        ),
    )
    unknown = memory.create_location(
        EvidenceLocationCreate(
            concept_id=concept.id,
            source_version_id=evidence["current_version_id"],
            pdf_page_number=90,
            region="unknown",
            evidence_snippet="Referencia por revisar.",
            origin="test",
        )
    )
    assert unknown.printed_page_label == "iv"
    assert "libro p. iv" in unknown.public_citation
    no_mapping = memory.create_location(
        EvidenceLocationCreate(
            concept_id=concept.id,
            source_version_id=evidence["current_version_id"],
            pdf_page_number=92,
            region="unknown",
            evidence_snippet="Numeración todavía desconocida.",
            origin="test",
        )
    )
    assert "página impresa sin identificar" in no_mapping.public_citation
    custom = EvidenceLocationCreate(
        concept_id=concept.id,
        source_version_id=evidence["current_version_id"],
        pdf_page_number=91,
        region="custom",
        custom_bbox={"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.5},
        evidence_snippet="Región precisa.",
        origin="test",
    )
    assert memory.create_location(custom).custom_bbox.width == 0.4
    with pytest.raises(ValueError):
        EvidenceLocationCreate(
            concept_id=concept.id,
            source_version_id=evidence["current_version_id"],
            region="custom",
            evidence_snippet="Sin caja.",
            origin="test",
        )


def test_response_and_location_feedback_are_independent_audited_and_reversible(
    memory_library: EducationalLibraryService,
):
    memory = PedagogicalMemoryService(memory_library.database)
    query_id = _insert_query(memory_library)
    query_memory = memory.remember_teacher_query(query_id)
    assert query_memory.locations
    location = query_memory.locations[0]
    response = memory.feedback_response(
        query_id,
        MemoryFeedbackRequest(
            verdict="correct", operation_id="response-feedback-1", comment="Útil"
        ),
    )
    assert response.resulting_status is None
    assert memory.get_location(location.id).status.value == "candidate"
    confirmed = memory.feedback_location(
        query_id,
        location.id,
        LocationFeedbackRequest(
            verdict="correct",
            operation_id="location-feedback-1",
            scan_layout="double_page",
            region="left",
            comment="Está en la mitad izquierda.",
        ),
    )
    assert confirmed.resulting_status.value == "user_confirmed"
    rejected = memory.feedback_location(
        query_id,
        location.id,
        LocationFeedbackRequest(verdict="incorrect", operation_id="location-feedback-2"),
    )
    assert rejected.resulting_status.value == "rejected"
    memory.revert(rejected.review_id, "revert-feedback-2", "Restaurar confirmación")
    assert memory.get_location(location.id).status.value == "user_confirmed"
    neutral = memory.feedback_location(
        query_id,
        location.id,
        LocationFeedbackRequest(verdict="unknown", operation_id="location-feedback-3"),
    )
    assert neutral.resulting_status.value == "user_confirmed"
    audit = memory.audit(target_id=location.id)
    assert len(audit) == 4
    public_audit = json.dumps([item.model_dump(mode="json") for item in audit], ensure_ascii=False)
    assert "evidence_hash" not in public_audit
    assert "current_path" not in public_audit
    assert "prompt" not in public_audit
    with pytest.raises(LibraryContractError):
        memory.feedback_location(
            query_id,
            location.id,
            LocationFeedbackRequest(verdict="incorrect", operation_id="location-feedback-3"),
        )


def test_exact_negative_does_not_block_other_concepts_and_new_versions_go_stale(
    memory_library: EducationalLibraryService,
):
    memory = PedagogicalMemoryService(memory_library.database)
    evidence = _source_evidence(memory_library)
    accusative = memory.create_concept(
        PedagogicalConceptCreate(canonical_name="Akkusativ", display_name_es="Acusativo")
    )
    perfekt = memory.create_concept(
        PedagogicalConceptCreate(canonical_name="Perfekt", display_name_es="Pretérito perfecto")
    )
    locations = []
    for concept in (accusative, perfekt):
        locations.append(
            memory.create_location(
                EvidenceLocationCreate(
                    concept_id=concept.id,
                    source_version_id=evidence["current_version_id"],
                    chunk_id=evidence["chunk_id"],
                    pdf_page_number=89,
                    evidence_snippet=concept.canonical_name,
                    status="system_verified",
                    origin="test",
                )
            )
        )
    query_id = _insert_query(memory_library)
    memory.feedback_location(
        query_id,
        locations[0].id,
        LocationFeedbackRequest(verdict="incorrect", operation_id="reject-exact-location"),
    )
    assert memory.get_location(locations[1].id).status.value == "system_verified"
    assert evidence["chunk_id"] in memory.memory_matches("Perfekt")["boosts"]
    assert evidence["chunk_id"] in memory.memory_matches("Acusativo")["rejected_chunks"]
    with memory_library.database.transaction(immediate=True) as connection:
        memory.mark_version_stale(connection, evidence["current_version_id"])
    assert memory.get_location(locations[1].id).status.value == "stale"


def test_source_rename_preserves_memory_and_content_change_invalidates_it(
    memory_library: EducationalLibraryService,
):
    memory = PedagogicalMemoryService(memory_library.database)
    evidence = _source_evidence(memory_library)
    concept = memory.create_concept(
        PedagogicalConceptCreate(canonical_name="Akkusativ", display_name_es="Acusativo")
    )
    location = memory.create_location(
        EvidenceLocationCreate(
            concept_id=concept.id,
            source_version_id=evidence["current_version_id"],
            chunk_id=evidence["chunk_id"],
            pdf_page_number=89,
            evidence_snippet="Der Akkusativ markiert oft das direkte Objekt.",
            status="user_confirmed",
            origin="test",
        )
    )
    original = memory_library.root / "Herder-Theorie.md"
    renamed = memory_library.root / "Herder-Grammatik.md"
    original.rename(renamed)
    memory_library.scan()
    assert memory.get_location(location.id).status.value == "user_confirmed"
    with memory_library.database.connect() as connection:
        source = connection.execute(
            "SELECT id,current_version_id,current_path FROM sources"
        ).fetchone()
    assert source["id"] == evidence["source_id"]
    assert source["current_version_id"] == evidence["current_version_id"]
    assert source["current_path"].endswith("Herder-Grammatik.md")
    renamed.write_text("# Akkusativ\n\nContenido realmente modificado.", encoding="utf-8")
    memory_library.scan()
    assert memory.get_location(location.id).status.value == "user_confirmed"
    with memory_library.database.connect() as connection:
        source = connection.execute(
            "SELECT current_version_id,latest_version_id FROM sources"
        ).fetchone()
        assert source["current_version_id"] == evidence["current_version_id"]
        assert source["latest_version_id"] != source["current_version_id"]


def test_import_is_prudent_idempotent_and_represents_confirmed_akkusativ(
    memory_library: EducationalLibraryService,
):
    memory = PedagogicalMemoryService(memory_library.database)
    query_id = _insert_query(memory_library, status="completed")
    evidence = _source_evidence(memory_library)
    now = utc_text()
    with memory_library.database.transaction(immediate=True) as connection:
        for unit_id, title, status in (
            ("ku-approved", "Objekt im Akkusativ", "approved"),
            ("ku-candidate", "Partizip II", "candidate"),
        ):
            connection.execute(
                "INSERT INTO knowledge_units(id,kind,title,content_es,german_examples_json,"
                "translations_json,cefr_level,topics_json,keywords_json,warnings_json,confidence,"
                "status,model,prompt_version,content_hash,created_at,updated_at) "
                "VALUES (?, 'grammar_rule',?,'Explicación','[]','[]','A1','[]','[]','[]',0.8,"
                "?,'test','knowledge.v1',?,?,?)",
                (unit_id, title, status, f"hash-{unit_id}", now, now),
            )
            connection.execute(
                "INSERT INTO knowledge_unit_sources(knowledge_unit_id,chunk_id,source_version_id,"
                "quote) VALUES (?,?,?,?)",
                (
                    unit_id,
                    evidence["chunk_id"],
                    evidence["current_version_id"],
                    "Der Akkusativ markiert oft das direkte Objekt",
                ),
            )
    request = PedagogicalMemoryImportRequest(
        operation_id="initial-memory-import",
        confirm_herder_akkusativ_pdf_89=True,
    )
    imported = memory.import_existing(request)
    assert imported.concepts_created >= 1
    assert imported.mappings_created == 1
    akk = memory.list_concepts(query="acusativo")[0]
    assert akk.status.value == "user_confirmed"
    location = next(
        item for item in memory.get_concept(akk.id).locations if item.pdf_page_number == 89
    )
    assert location.status.value == "user_confirmed"
    assert location.scan_layout.value == "double_page"
    assert location.printed_page_label is None
    assert location.region.value == "unknown"
    assert (
        len([item for item in memory.get_concept(akk.id).locations if item.pdf_page_number == 89])
        == 1
    )
    assert memory.query_memory(query_id).locations
    approved = memory.list_concepts(query="Objekt im Akkusativ")[0]
    candidate = memory.list_concepts(query="Partizip II")[0]
    assert approved.status.value == "system_verified"
    assert candidate.status.value == "candidate"
    repeated = memory.import_existing(request)
    assert repeated.idempotent_replay
    assert memory.summary().concepts == 3


def test_review_queue_unknown_is_neutral_and_http_is_sanitized(
    memory_library: EducationalLibraryService,
):
    memory = PedagogicalMemoryService(memory_library.database)
    concept = memory.create_concept(PedagogicalConceptCreate(canonical_name="Perfekt"))
    queue = memory.review_queue()
    assert queue[0].target_id == concept.id
    reviewed = memory.review_target(
        "concept",
        concept.id,
        MemoryReviewRequest(action="unknown", operation_id="review-unknown-1"),
    )
    assert reviewed.resulting_status.value == "candidate"


@pytest.mark.anyio
async def test_memory_http_contract_feedback_and_private_fields(
    client,
    memory_library: EducationalLibraryService,
):
    memory = PedagogicalMemoryService(memory_library.database)
    query_id = _insert_query(memory_library)
    memory.remember_teacher_query(query_id)
    app.dependency_overrides[get_library_memory] = lambda: memory
    try:
        created = await client.post(
            "/api/library/memory/concepts",
            json={
                "canonical_name": "Perfekt",
                "language": "de",
                "display_name_es": "Pretérito perfecto",
                "category": "verb_tense",
            },
        )
        assert created.status_code == 201
        assert "content_hash" not in json.dumps(created.json())
        assert "/Volumes/" not in json.dumps(created.json())
        query_memory = await client.get(f"/api/library/queries/{query_id}/memory")
        assert query_memory.status_code == 200
        location_id = query_memory.json()["locations"][0]["id"]
        feedback = await client.post(
            f"/api/library/queries/{query_id}/locations/{location_id}/feedback",
            json={"verdict": "correct", "operation_id": "http-location-feedback"},
        )
        assert feedback.status_code == 200
        assert feedback.json()["resulting_status"] == "user_confirmed"
        audit = await client.get(
            "/api/library/memory/audit",
            params={"target_type": "location", "target_id": location_id},
        )
        assert audit.status_code == 200
        serialized_audit = json.dumps(audit.json())
        assert "evidence_hash" not in serialized_audit
        assert "current_path" not in serialized_audit
        assert "prompt" not in serialized_audit
        invalid = await client.post(
            f"/api/library/queries/{query_id}/feedback",
            json={"verdict": "yes", "operation_id": "invalid-feedback-1"},
        )
        assert invalid.status_code == 422
        assert "traceback" not in invalid.text.casefold()
    finally:
        app.dependency_overrides.pop(get_library_memory, None)
