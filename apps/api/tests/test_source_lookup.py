from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from llc_api.core.config import Settings
from llc_api.educational_library.dependencies import (
    get_library_memory,
    get_library_teacher,
)
from llc_api.educational_library.memory import PedagogicalMemoryService
from llc_api.educational_library.schemas import (
    EvidenceLocationCreate,
    EvidenceRegion,
    LocationFeedbackRequest,
    MemoryReviewRequest,
    PageMappingUpdate,
    PedagogicalConceptCreate,
    PedagogicalMemoryStatus,
    QueryAmbiguity,
    TeacherAnswerDraft,
    TeacherAskRequest,
    TeacherClaim,
    TeacherIntent,
    TeacherQueryPlan,
)
from llc_api.educational_library.search import EducationalSearchService
from llc_api.educational_library.service import EducationalLibraryService
from llc_api.educational_library.source_lookup import (
    DeterministicSourceLookupService,
    SourceLookupMode,
    detect_source_lookup,
)
from llc_api.educational_library.teacher import EducationalTeacherService
from llc_api.main import app
from llc_api.providers.base import ModelProvider, ProviderUnavailableError
from llc_api.schemas.api import ModelInfo


class CountingProvider(ModelProvider):
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    async def health_check(self) -> bool:
        return not self.fail

    async def list_models(self) -> list[ModelInfo]:
        return [] if self.fail else [ModelInfo(name="teacher-test")]

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        del model, messages
        self.calls += 1
        if self.fail:
            raise ProviderUnavailableError("offline")
        raise AssertionError("source lookup must not call unstructured generation")

    async def stream_chat(self, model: str, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        yield await self.chat(model, messages)

    async def structured_generate(
        self, model: str, messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> BaseModel:
        del model, messages, schema
        self.calls += 1
        if self.fail:
            raise ProviderUnavailableError("offline")
        raise AssertionError("pure source lookup must not call structured generation")


class InvalidCitationProvider(CountingProvider):
    async def structured_generate(
        self, model: str, messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> BaseModel:
        del model, messages
        self.calls += 1
        if schema is TeacherQueryPlan:
            return TeacherQueryPlan(
                intent=TeacherIntent.GRAMMAR_EXPLANATION,
                language="de",
                target_expression="Akkusativ",
                user_language="es",
                ambiguity=QueryAmbiguity.LOW,
                search_queries=["Akkusativ"],
                required_evidence=["definición"],
            )
        if schema is TeacherAnswerDraft:
            return TeacherAnswerDraft(
                evidence_sufficient=True,
                direct_answer="El acusativo marca a menudo el objeto directo.",
                claims=[
                    TeacherClaim(
                        text="El acusativo puede marcar el objeto directo.",
                        source_chunk_ids=[999_999],
                    )
                ],
            )
        raise AssertionError(schema)


class RepairUnavailableProvider(InvalidCitationProvider):
    async def structured_generate(
        self, model: str, messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> BaseModel:
        if self.calls >= 2:
            self.calls += 1
            raise ProviderUnavailableError("repair offline")
        return await super().structured_generate(model, messages, schema)


@pytest.fixture
def lookup_library(tmp_path: Path) -> EducationalLibraryService:
    materials = tmp_path / "materials"
    materials.mkdir()
    (materials / "Herder-Grammatik.md").write_text(
        "# Akkusativ\n\nDer Akkusativ markiert oft das direkte Objekt.\n\n"
        "# Perfekt\n\nDas Perfekt besteht aus Hilfsverb und Partizip II.\n\n"
        "# Negation\n\nKein negiert ein Nomen; nicht negiert andere Satzteile.",
        encoding="utf-8",
    )
    library = EducationalLibraryService(
        Settings(
            database_url="sqlite://",
            educational_materials_dir=materials,
            educational_library_runtime_dir=tmp_path / "runtime",
            educational_library_scan_on_startup=False,
            educational_library_embedding_model="",
            lm_studio_model="teacher-test",
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
        chunks = connection.execute(
            "SELECT id,title FROM chunks WHERE source_version_id=? ORDER BY id",
            (source["current_version_id"],),
        ).fetchall()
        for index, chunk in enumerate(chunks):
            connection.execute(
                "UPDATE chunks SET page_start=?,page_end=? WHERE id=?",
                (89 + index, 89 + index, chunk["id"]),
            )
    return library


def _akkusativ_location(
    library: EducationalLibraryService,
    *,
    status: PedagogicalMemoryStatus = PedagogicalMemoryStatus.USER_CONFIRMED,
):
    memory = PedagogicalMemoryService(library.database)
    concept = memory.create_concept(
        PedagogicalConceptCreate(
            canonical_name="Akkusativ",
            display_name_es="acusativo",
            display_name_de="Akkusativ",
            language="de",
            category="grammar_case",
            status=status,
            origin="user_created",
        )
    )
    with library.database.connect() as connection:
        chunk = connection.execute(
            "SELECT c.id,c.source_version_id,c.text,c.title FROM chunks c "
            "WHERE c.title LIKE '%Akkusativ%' ORDER BY c.id LIMIT 1"
        ).fetchone()
    mapping = memory.upsert_page_mapping(
        chunk["source_version_id"],
        PageMappingUpdate(
            pdf_page_number=89,
            scan_layout="double_page",
            status=status,
            origin="test",
            operation_id="lookup-page-map-89",
        ),
    )
    assert mapping.printed_left_label is None
    location = memory.create_location(
        EvidenceLocationCreate(
            concept_id=concept.id,
            source_version_id=chunk["source_version_id"],
            chunk_id=chunk["id"],
            pdf_page_number=89,
            region=EvidenceRegion.UNKNOWN,
            heading=chunk["title"],
            evidence_snippet=chunk["text"],
            extraction_quality=1,
            status=status,
            origin="test_fixture",
        )
    )
    return memory, concept, location


def _teacher(
    library: EducationalLibraryService,
    provider: CountingProvider | None = None,
    *,
    default_model: str = "teacher-test",
) -> EducationalTeacherService:
    return EducationalTeacherService(
        library.database,
        EducationalSearchService(library.database),
        provider or CountingProvider(),
        default_model=default_model,
    )


@pytest.mark.parametrize(
    ("question", "target"),
    [
        ("¿Dónde aparece el acusativo?", "acusativo"),
        ("¿En qué página está el Perfekt?", "perfekt"),
        ("¿En qué libro está el acusativo?", "acusativo"),
        ("Muéstrame la sección de Akkusativ", "akkusativ"),
        ("Localiza el Perfekt", "perfekt"),
        ("Busca la tabla de pronombres", "pronombres"),
        ("¿En qué capítulo está la negación?", "negacion"),
        ("¿Dónde puedo leer sobre kein?", "kein"),
        ("Wo steht Akkusativ?", "akkusativ"),
        ("Auf welcher Seite steht Perfekt?", "perfekt"),
        ("In welchem Kapitel steht Negation?", "negation"),
    ],
)
def test_source_lookup_intent_detection_is_local(question: str, target: str):
    detected = detect_source_lookup(question)
    assert detected.mode == SourceLookupMode.PURE
    assert detected.target == target


def test_source_lookup_detection_separates_explanation_mixed_and_context():
    assert detect_source_lookup("¿Qué es el acusativo?").mode == SourceLookupMode.NONE
    mixed = detect_source_lookup("¿Qué es el acusativo y dónde aparece en Herder?")
    assert mixed.mode == SourceLookupMode.MIXED
    assert detect_source_lookup("¿Dónde aparece?").mode == SourceLookupMode.AMBIGUOUS
    contextual = detect_source_lookup("¿Dónde aparece?", parent_target="Akkusativ")
    assert contextual.mode == SourceLookupMode.PURE
    assert contextual.target == "Akkusativ"


@pytest.mark.anyio
async def test_confirmed_lookup_is_exact_fast_model_free_and_persisted(
    lookup_library: EducationalLibraryService,
):
    _, concept, location = _akkusativ_location(lookup_library)
    provider = CountingProvider()
    result = await _teacher(lookup_library, provider, default_model="").ask(
        TeacherAskRequest(question="¿En qué parte de Herder aparece el acusativo?")
    )

    assert result.status.value == "completed"
    assert result.answer_kind == "source_lookup"
    assert result.used_generation is False
    assert result.models == {}
    assert provider.calls == 0
    assert result.source_lookup is not None
    assert result.source_lookup.status.value == "verified_location"
    assert result.source_lookup.concept is not None
    assert result.source_lookup.concept.concept_id == concept.id
    assert [item.location_id for item in result.source_lookup.locations] == [location.id]
    public = result.source_lookup.locations[0]
    assert public.pdf_page == 89
    assert public.printed_page is None
    assert public.scan_layout.value == "double_page"
    assert public.region.value == "unknown"
    assert public.review_status.value == "user_confirmed"
    assert public.provenance == "memory"
    assert public.citation == "Herder · PDF p. 89 · página impresa sin identificar"
    assert result.answer.direct_answer == (
        "El acusativo aparece en el manual Herder en la página 89 del PDF. "
        "La página corresponde a un escaneo doble. La numeración impresa y la mitad "
        "concreta todavía no están identificadas. Esta ubicación fue confirmada por ti."
    )
    assert result.timings.generation_ms == 0
    with lookup_library.database.connect() as connection:
        plan = json.loads(
            connection.execute(
                "SELECT plan_json FROM teacher_queries WHERE id=?", (result.query_id,)
            ).fetchone()[0]
        )
        assert plan["intent"] == "source_lookup"
        assert connection.execute(
            "SELECT 1 FROM teacher_query_location_usage WHERE query_id=? AND location_id=?",
            (result.query_id, location.id),
        ).fetchone()


@pytest.mark.anyio
async def test_lookup_api_is_structured_sanitized_and_history_safe(
    client: httpx.AsyncClient,
    lookup_library: EducationalLibraryService,
):
    _akkusativ_location(lookup_library)
    provider = CountingProvider()
    teacher = _teacher(lookup_library, provider)
    app.dependency_overrides[get_library_teacher] = lambda: teacher
    try:
        response = await client.post(
            "/api/library/ask",
            json={"question": "¿Dónde aparece Akkusativ en Herder?"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["answer_kind"] == "source_lookup"
        assert payload["used_generation"] is False
        assert payload["source_lookup"]["status"] == "verified_location"
        assert payload["source_lookup"]["locations"][0]["pdf_page"] == 89
        serialised = json.dumps(payload)
        for private in (
            "current_path",
            "content_hash",
            "plan_json",
            "answer_json",
            "prompt_version",
            "/Volumes/",
        ):
            assert private not in serialised
        reopened = await client.get(f"/api/library/queries/{payload['query_id']}")
        assert reopened.json() == payload
        assert provider.calls == 0
    finally:
        app.dependency_overrides.pop(get_library_teacher, None)


@pytest.mark.anyio
async def test_lookup_cache_repairs_citations_and_invalidates_after_feedback(
    lookup_library: EducationalLibraryService,
):
    memory, _, location = _akkusativ_location(lookup_library)
    teacher = _teacher(lookup_library)
    question = "¿Dónde aparece el acusativo en Herder?"
    first = await teacher.ask(TeacherAskRequest(question=question))
    second = await teacher.ask(TeacherAskRequest(question=question))
    assert first.lookup_cache_hit is False
    assert second.lookup_cache_hit is True

    with lookup_library.database.transaction(immediate=True) as connection:
        cache_row = connection.execute(
            "SELECT cache_key,value_json FROM library_cache WHERE namespace='source_lookup'"
        ).fetchone()
        tampered = json.loads(cache_row["value_json"])
        tampered["result"]["locations"][0]["citation"] = "inventada · PDF p. 999"
        connection.execute(
            "UPDATE library_cache SET value_json=? WHERE namespace='source_lookup' AND cache_key=?",
            (json.dumps(tampered), cache_row["cache_key"]),
        )
    repaired = await teacher.ask(TeacherAskRequest(question=question))
    assert repaired.lookup_cache_hit is True
    assert "999" not in repaired.source_lookup.locations[0].citation

    region_feedback = memory.feedback_location(
        repaired.query_id,
        location.id,
        LocationFeedbackRequest(
            verdict="correct",
            region="right",
            scan_layout="double_page",
            operation_id="lookup-feedback-region-right",
        ),
    )
    region_updated = await teacher.ask(TeacherAskRequest(question=question))
    assert region_updated.lookup_cache_hit is False
    assert region_updated.source_lookup.locations[0].region.value == "right"
    assert region_updated.source_lookup.locations[0].printed_page is None

    memory.feedback_location(
        region_updated.query_id,
        location.id,
        LocationFeedbackRequest(
            verdict="unknown",
            operation_id="lookup-feedback-unknown",
        ),
    )
    feedback = memory.feedback_location(
        region_updated.query_id,
        location.id,
        LocationFeedbackRequest(
            verdict="correct",
            region="right",
            scan_layout="double_page",
            printed_right_label="175",
            operation_id="lookup-feedback-printed-page",
        ),
    )
    corrected = await teacher.ask(TeacherAskRequest(question=question))
    assert corrected.lookup_cache_hit is False
    assert corrected.source_lookup.locations[0].printed_page == "175"
    assert corrected.source_lookup.locations[0].citation == (
        "Herder · PDF p. 89 · libro p. 175 · mitad derecha"
    )

    memory.revert(feedback.review_id, "lookup-feedback-revert", "Deshacer calibración")
    reverted = await teacher.ask(TeacherAskRequest(question=question))
    assert reverted.lookup_cache_hit is False
    assert reverted.source_lookup.locations[0].region.value == "right"
    assert reverted.source_lookup.locations[0].printed_page is None
    assert reverted.source_lookup.locations[0].citation == (
        "Herder · PDF p. 89 · página impresa sin identificar · mitad derecha"
    )
    assert memory.get_location(location.id).status.value == "user_confirmed"
    assert region_feedback.resulting_status.value == "user_confirmed"
    audit = memory.audit(target_id=location.id)
    assert {item.action for item in audit} >= {"location_feedback", "review_reverted"}
    public_audit = json.dumps([item.model_dump(mode="json") for item in audit], ensure_ascii=False)
    assert "_page_mapping_snapshot" not in public_audit


@pytest.mark.anyio
async def test_hybrid_fallback_creates_candidate_but_unknown_topic_does_not(
    lookup_library: EducationalLibraryService,
):
    provider = CountingProvider()
    teacher = _teacher(lookup_library, provider)
    candidate = await teacher.ask(TeacherAskRequest(question="¿Dónde aparece el Perfekt?"))
    assert candidate.status.value == "completed"
    assert candidate.source_lookup.status.value == "candidate_locations"
    assert candidate.hybrid_fallback is True
    assert candidate.used_generation is False
    assert candidate.source_lookup.locations[0].provenance == "hybrid"
    assert candidate.source_lookup.locations[0].review_status.value == "candidate"
    assert provider.calls == 0

    missing = await teacher.ask(TeacherAskRequest(question="¿Dónde aparecen los verbos marcianos?"))
    assert missing.status.value == "insufficient"
    assert missing.failure_reason.value == "no_evidence"
    assert missing.source_lookup.status.value == "no_location"
    assert missing.source_lookup.locations == []
    assert provider.calls == 0


@pytest.mark.anyio
async def test_lookup_statuses_filter_sources_and_never_reuse_rejected(
    lookup_library: EducationalLibraryService,
):
    memory, concept, location = _akkusativ_location(
        lookup_library, status=PedagogicalMemoryStatus.SYSTEM_VERIFIED
    )
    resolver = DeterministicSourceLookupService(
        lookup_library.database,
        EducationalSearchService(lookup_library.database),
        memory=memory,
    )
    verified = await resolver.resolve("¿Dónde aparece el acusativo en Herder?")
    assert verified.result.status.value == "verified_location"

    with lookup_library.database.transaction(immediate=True) as connection:
        base = connection.execute(
            "SELECT source_version_id,chunk_id,evidence_snippet FROM "
            "pedagogical_evidence_locations WHERE id=?",
            (location.id,),
        ).fetchone()
        other_chunk = connection.execute(
            "SELECT id,text,title FROM chunks WHERE source_version_id=? AND id<>? ORDER BY id LIMIT 1",
            (base["source_version_id"], base["chunk_id"]),
        ).fetchone()
    second = memory.create_location(
        EvidenceLocationCreate(
            concept_id=concept.id,
            source_version_id=base["source_version_id"],
            chunk_id=other_chunk["id"],
            pdf_page_number=90,
            heading=other_chunk["title"],
            evidence_snippet=other_chunk["text"],
            extraction_quality=0.8,
            status="system_verified",
            origin="test_fixture",
        )
    )
    multiple = await resolver.resolve("¿Dónde aparece el acusativo en Herder?")
    assert multiple.result.status.value == "multiple_locations"
    assert {item.location_id for item in multiple.result.locations} == {location.id, second.id}

    with lookup_library.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE pedagogical_evidence_locations SET status='conflict' WHERE concept_id=?",
            (concept.id,),
        )
    conflict = await resolver.resolve("¿Dónde aparece el acusativo en Herder?")
    assert conflict.result.status.value == "conflict"

    with lookup_library.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE sources SET status='missing' WHERE id=(SELECT source_id FROM "
            "pedagogical_evidence_locations WHERE id=?)",
            (location.id,),
        )
    stale = await resolver.resolve("¿Dónde aparece el acusativo en Herder?")
    assert stale.result.status.value == "stale"
    assert all(item.review_status.value == "conflict" for item in stale.result.locations)

    with lookup_library.database.transaction(immediate=True) as connection:
        connection.execute("UPDATE sources SET status='present'")
        connection.execute(
            "UPDATE pedagogical_evidence_locations SET status='rejected' WHERE concept_id=?",
            (concept.id,),
        )
    rejected = await resolver.resolve("¿Dónde aparece el acusativo en Herder?")
    assert rejected.result.status.value == "no_location"
    assert rejected.result.locations == []


@pytest.mark.anyio
async def test_mixed_lookup_preserves_location_when_teacher_is_unavailable(
    lookup_library: EducationalLibraryService,
):
    _akkusativ_location(lookup_library)
    provider = CountingProvider(fail=True)
    result = await _teacher(lookup_library, provider).ask(
        TeacherAskRequest(question="¿Qué es el acusativo y dónde aparece en Herder?")
    )
    assert result.status.value == "completed"
    assert result.answer_kind == "teacher_answer_with_source_lookup"
    assert result.used_generation is False
    assert result.failure_reason.value == "generation_failure"
    assert result.source_lookup.status.value == "verified_location"
    assert result.source_lookup.locations[0].pdf_page == 89
    assert "ubicación documental sigue disponible" in result.answer.direct_answer
    assert provider.calls == 1


@pytest.mark.anyio
async def test_mixed_lookup_survives_citation_validation_failure(
    lookup_library: EducationalLibraryService,
):
    _akkusativ_location(lookup_library)
    provider = InvalidCitationProvider()
    result = await _teacher(lookup_library, provider).ask(
        TeacherAskRequest(question="¿Qué es el acusativo y dónde aparece en Herder?")
    )
    assert result.status.value == "completed"
    assert result.answer_kind == "teacher_answer_with_source_lookup"
    assert result.used_generation is False
    assert result.failure_reason.value == "citation_validation_failure"
    assert result.source_lookup.status.value == "verified_location"
    assert result.source_lookup.locations[0].citation.startswith("Herder · PDF p. 89")
    assert "ubicación documental sigue disponible" in result.answer.direct_answer
    assert provider.calls == 3


@pytest.mark.anyio
async def test_mixed_lookup_survives_repair_provider_failure(
    lookup_library: EducationalLibraryService,
):
    _akkusativ_location(lookup_library)
    provider = RepairUnavailableProvider()
    result = await _teacher(lookup_library, provider).ask(
        TeacherAskRequest(question="¿Qué es el acusativo y dónde aparece en Herder?")
    )
    assert result.status.value == "completed"
    assert result.failure_reason.value == "repair_failure"
    assert result.source_lookup.locations[0].pdf_page == 89
    assert result.used_generation is False


def test_review_queue_prioritizes_current_conflict_and_postpones_unknown(
    lookup_library: EducationalLibraryService,
):
    memory, concept, location = _akkusativ_location(
        lookup_library, status=PedagogicalMemoryStatus.CANDIDATE
    )
    other = memory.create_concept(
        PedagogicalConceptCreate(canonical_name="Perfekt", status="conflict")
    )
    query_id = _teacher_query_for_queue(lookup_library, concept.id, location.id)
    queue = memory.review_queue(current_query_id=query_id)
    assert queue[0].target_id == other.id
    assert next(item for item in queue if item.target_id == location.id).priority >= 1_000
    assert all(item.target_id != concept.id for item in memory.review_queue(target_type="location"))
    assert all(item.target_type == "concept" for item in memory.review_queue(target_type="concept"))
    assert any(
        item.target_id == concept.id
        for item in memory.review_queue(target_type="concept", herder_only=True)
    )
    assert memory.review_queue(status=PedagogicalMemoryStatus.USER_CONFIRMED) == []

    reviewed = memory.review_target(
        "concept",
        other.id,
        request=MemoryReviewRequest(
            action="unknown",
            operation_id="lookup-review-unknown",
        ),
    )
    assert reviewed.verdict == "unknown"
    assert all(item.target_id != other.id for item in memory.review_queue())
    memory.revert(reviewed.review_id, "lookup-review-revert", None)
    assert any(item.target_id == other.id for item in memory.review_queue())


@pytest.mark.anyio
async def test_review_queue_http_filters_and_sanitizes(
    client: httpx.AsyncClient,
    lookup_library: EducationalLibraryService,
):
    memory, concept, location = _akkusativ_location(
        lookup_library, status=PedagogicalMemoryStatus.CANDIDATE
    )
    query_id = _teacher_query_for_queue(lookup_library, concept.id, location.id)
    app.dependency_overrides[get_library_memory] = lambda: memory
    try:
        response = await client.get(
            "/api/library/memory/review-queue",
            params={
                "limit": 10,
                "target_type": "location",
                "status": "candidate",
                "herder_only": "true",
                "recently_used_only": "true",
                "current_query_id": query_id,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert [item["target_id"] for item in payload] == [location.id]
        assert payload[0]["source_role"] == "core_theory"
        assert payload[0]["priority"] >= 1_000
        serialised = json.dumps(payload)
        assert "current_path" not in serialised
        assert "/Volumes/" not in serialised
    finally:
        app.dependency_overrides.pop(get_library_memory, None)


def _teacher_query_for_queue(
    library: EducationalLibraryService,
    concept_id: str,
    location_id: str,
) -> str:
    teacher = _teacher(library)
    cancelled = teacher.persist_cancelled(TeacherAskRequest(question="Localiza el acusativo"))
    PedagogicalMemoryService(library.database).link_lookup_query(
        cancelled.query_id,
        [concept_id],
        [location_id],
    )
    return cancelled.query_id
