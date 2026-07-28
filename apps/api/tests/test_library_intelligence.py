from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from deutschos_api.core.config import Settings
from deutschos_api.educational_library.dependencies import (
    get_library_model_router,
    get_library_search,
    get_library_service,
)
from deutschos_api.educational_library.document_intelligence import (
    DocumentIntelligenceService,
)
from deutschos_api.educational_library.editorial import LibraryEditorialService
from deutschos_api.educational_library.routing import (
    LibraryModelRouter,
    ModelRole,
    ModelRoutingError,
    ModelRoutingPolicy,
)
from deutschos_api.educational_library.schemas import (
    CoreSourceAssignmentRequest,
    EditorialSectionLinkRequest,
    EditorialSectionUpdate,
    QueryAmbiguity,
    TeacherIntent,
    TeacherQueryPlan,
)
from deutschos_api.educational_library.search import (
    EMBEDDINGGEMMA_MODEL,
    EducationalSearchService,
    prepare_embedding_document,
    prepare_embedding_query,
)
from deutschos_api.educational_library.service import EducationalLibraryService
from deutschos_api.educational_library.teacher import EducationalTeacherService
from deutschos_api.main import app
from deutschos_api.providers.base import (
    ModelProvider,
    ProviderUnavailableError,
)
from deutschos_api.schemas.api import ModelInfo


class CountingEmbeddingProvider:
    provider_name = "test"
    model_name = "multilingual-test"
    model_version = "embed-api.v1"

    def __init__(self, *, digest: str = "digest-a"):
        self.digest = digest
        self.calls: list[list[str]] = []

    async def available(self) -> bool:
        return True

    async def metadata(self) -> tuple[str, str]:
        return self.model_version, self.digest

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [
            [
                float("artikel" in text.casefold() or "artículo" in text.casefold()),
                float("gruß" in text.casefold() or "saludo" in text.casefold()),
                1.0,
            ]
            for text in texts
        ]


class InconsistentEmbeddingProvider(CountingEmbeddingProvider):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0, 0.0], [1.0, 0.0, 0.0]][: len(texts)]


class RouterProvider(ModelProvider):
    def __init__(self, models: list[str], *, fail: set[str] | None = None):
        self.models = models
        self.fail = fail or set()
        self.calls: list[str] = []

    async def health_check(self) -> bool:
        return True

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name=name) for name in self.models]

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        del messages
        return model

    async def stream_chat(self, model: str, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        yield await self.chat(model, messages)

    async def structured_generate(
        self, model: str, messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> BaseModel:
        del messages
        self.calls.append(model)
        if model in self.fail:
            raise ProviderUnavailableError("offline")
        assert schema is TeacherQueryPlan
        return TeacherQueryPlan(
            intent=TeacherIntent.DEFINITION,
            language="de",
            target_expression="die",
            user_language="es",
            ambiguity=QueryAmbiguity.HIGH,
            search_queries=["die"],
        )


def test_embeddinggemma_retrieval_formats_queries_and_documents_centrally():
    assert prepare_embedding_query("  der   Akkusativ  ", EMBEDDINGGEMMA_MODEL) == (
        "task: search result | query: der Akkusativ"
    )
    assert prepare_embedding_document("  Der   Artikel  ", EMBEDDINGGEMMA_MODEL) == (
        "title: none | text: Der Artikel"
    )
    assert prepare_embedding_query("  texto  ", "another-model") == "texto"


@pytest.fixture
def intelligence_library(tmp_path: Path) -> EducationalLibraryService:
    materials = tmp_path / "materials"
    materials.mkdir()
    settings = Settings(
        database_url="sqlite://",
        educational_materials_dir=materials,
        educational_library_runtime_dir=tmp_path / "runtime",
        educational_library_scan_on_startup=False,
        educational_library_embedding_model="",
        lm_studio_model="test",
    )
    return EducationalLibraryService(settings)


def test_core_candidates_assignments_alias_relation_and_audit_are_safe(
    intelligence_library: EducationalLibraryService,
):
    root = intelligence_library.root
    theory_name = "01_CORE_Herder_Gramatica_Alemana_Hispanohablantes_A1-C2.md"
    workbook_name = "02_CORE_Herder_Gramatica_Alemana_Ejercicios_y_Soluciones_A1-C2.md"
    (root / theory_name).write_text("# TEMA 1 Verbos\n\nDer Artikel.", encoding="utf-8")
    (root / workbook_name).write_text("# Übungen\n\nErgänzen Sie.", encoding="utf-8")
    intelligence_library.scan()
    editorial = LibraryEditorialService(intelligence_library.database)
    candidates = editorial.candidates()
    assert [(item.suggested_role.value, item.unambiguous) for item in candidates] == [
        ("core_theory", True),
        ("core_workbook", True),
    ]
    by_role = {item.suggested_role.value: item.source for item in candidates}
    theory = by_role["core_theory"]
    workbook = by_role["core_workbook"]
    assigned_theory = editorial.assign_core(
        theory.id,
        CoreSourceAssignmentRequest(
            operation_id="assign-theory-001",
            pedagogical_role="core_theory",
            display_alias="Herder · Gramática",
            canonical_title="Gramática alemana para hispanohablantes",
            related_source_id=workbook.id,
        ),
    )
    assigned_workbook = editorial.assign_core(
        workbook.id,
        CoreSourceAssignmentRequest(
            operation_id="assign-workbook-001",
            pedagogical_role="core_workbook",
            display_alias="Herder · Ejercicios",
            related_source_id=theory.id,
        ),
    )
    assert assigned_theory.current_path == theory_name
    assert assigned_theory.display_alias == "Herder · Gramática"
    assert assigned_theory.related_source_id == workbook.id
    assert assigned_workbook.related_source_id == theory.id
    assert editorial.core_pair().ready
    assert (root / theory_name).is_file() and (root / workbook_name).is_file()
    repeated = editorial.assign_core(
        theory.id,
        CoreSourceAssignmentRequest(
            operation_id="assign-theory-001",
            pedagogical_role="core_theory",
            display_alias="Herder · Gramática",
            canonical_title="Gramática alemana para hispanohablantes",
            related_source_id=workbook.id,
        ),
    )
    assert repeated == assigned_theory
    with pytest.raises(Exception, match="otro contenido"):
        editorial.assign_core(
            theory.id,
            CoreSourceAssignmentRequest(
                operation_id="assign-theory-001",
                pedagogical_role="core_theory",
                display_alias="Alias conflictivo",
                related_source_id=workbook.id,
            ),
        )
    with intelligence_library.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM source_editorial_events").fetchone()[0] == 2

    renamed = root / "manual-renombrado.md"
    (root / theory_name).rename(renamed)
    intelligence_library.scan()
    preserved = editorial.get_source(theory.id)
    assert preserved.current_path == renamed.name
    assert preserved.pedagogical_role.value == "core_theory"
    assert preserved.display_alias == "Herder · Gramática"
    assert preserved.related_source_id == workbook.id


def test_ambiguous_core_candidates_are_never_auto_confirmed(
    intelligence_library: EducationalLibraryService,
):
    for suffix in ("A", "B"):
        (
            intelligence_library.root / f"Herder Gramatica Alemana Hispanohablantes {suffix}.md"
        ).write_text("# TEMA 1\n\nRegel.", encoding="utf-8")
    intelligence_library.scan()
    candidates = LibraryEditorialService(intelligence_library.database).candidates()
    assert len(candidates) == 2
    assert not any(item.unambiguous for item in candidates)
    assert not any(item.source.user_selected_core for item in candidates)


def test_revisable_sections_ranges_links_and_page_quality(
    intelligence_library: EducationalLibraryService,
):
    path = intelligence_library.root / "Herder Gramatica Alemana Hispanohablantes.md"
    path.write_text("# Kapitel\n\nTEMA 1 Verbos. Der Artikel.", encoding="utf-8")
    intelligence_library.scan()
    source = intelligence_library.list_sources()[0]
    with intelligence_library.database.transaction(immediate=True) as connection:
        document_id = connection.execute(
            "SELECT id FROM documents WHERE source_version_id=(SELECT current_version_id FROM sources WHERE id=?)",
            (source.id,),
        ).fetchone()[0]
        connection.execute("UPDATE documents SET page_count=3 WHERE id=?", (document_id,))
        section_id = connection.execute(
            "SELECT id FROM sections WHERE document_id=?", (document_id,)
        ).fetchone()[0]
        connection.execute(
            "UPDATE sections SET page_start=1,page_end=1,text='TEMA 1 Verbos. Der Artikel zeigt das Genus.' WHERE id=?",
            (section_id,),
        )
        connection.execute(
            "INSERT INTO sections(document_id,sequence,kind,title,hierarchy_json,text,page_start,page_end,content_role) "
            "VALUES (?,1,'page','Página 2','[]','TEMA 2 Artikel. Die Frau liest.',2,2,'theory')",
            (document_id,),
        )
    editorial = LibraryEditorialService(intelligence_library.database)
    sections = editorial.build_section_index(source.id)
    assert [(item.page_start, item.page_end) for item in sections] == [(1, 1), (2, 2)]
    updated = editorial.update_section(
        sections[0].id,
        EditorialSectionUpdate(topic="Verben", editorial_status="user_confirmed"),
    )
    assert updated.topic == "Verben"
    linked = editorial.link_section(
        sections[0].id,
        EditorialSectionLinkRequest(
            target_section_id=sections[1].id,
            relation="theory_to_practice",
        ),
    )
    assert linked.related_sections == [sections[1].id]

    intelligence = DocumentIntelligenceService(
        intelligence_library.database, intelligence_library.settings
    )
    quality = intelligence.analyze_source(source.id)
    assert len(quality) == 3
    assert quality[-1].quality == "unusable"
    assert quality[-1].warnings


@pytest.mark.anyio
async def test_embeddings_incremental_cached_multilingual_and_role_aware(
    intelligence_library: EducationalLibraryService,
):
    core = intelligence_library.root / "core.md"
    supplementary = intelligence_library.root / "supplement.md"
    solution = intelligence_library.root / "Lösungen.md"
    core.write_text("# Artikel\n\nDer Artikel zeigt das Genus.", encoding="utf-8")
    supplementary.write_text("# Artículo\n\nUn artículo acompaña al sustantivo.", encoding="utf-8")
    solution.write_text("# Lösungen\n\nDer Artikel.", encoding="utf-8")
    intelligence_library.scan()
    core_source = next(
        item for item in intelligence_library.list_sources() if item.name == "core.md"
    )
    with intelligence_library.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE sources SET pedagogical_role='core_theory',priority=80 WHERE id=?",
            (core_source.id,),
        )
    provider = CountingEmbeddingProvider()
    search = EducationalSearchService(intelligence_library.database, provider)
    assert await search.index_embeddings(batch_size=2) == 2
    assert await search.index_embeddings() == 0
    first = await search.search("artículo", mode="hybrid")
    second = await search.search("artículo", mode="hybrid")
    # Editorial priority is strong only among relevant candidates: exact
    # Spanish lexical evidence still beats a merely related core chunk.
    assert first.results[0].source_name == "supplement.md"
    german = await search.search("Artikel", mode="hybrid")
    assert german.results[0].source_id == core_source.id
    assert second.query_embedding_cache_hit
    assert first.core_results >= 1
    with intelligence_library.database.connect() as connection:
        rows = connection.execute(
            "SELECT status,text_hash,model_digest,dimension,normalization_version FROM embeddings"
        ).fetchall()
        assert len(rows) == 2
        assert all(row["status"] == "indexed" and row["text_hash"] for row in rows)
        assert {row["model_digest"] for row in rows} == {"digest-a"}
        assert {row["dimension"] for row in rows} == {3}
        assert (
            connection.execute(
                "SELECT embedding_status FROM chunks WHERE content_role='solution'"
            ).fetchone()[0]
            == "excluded"
        )

    core.write_text("# Artikel\n\nDer Artikel zeigt Genus und Kasus.", encoding="utf-8")
    intelligence_library.scan()
    assert await search.index_embeddings() == 1

    provider.digest = "digest-b"
    assert (await search.search("Artikel", mode="semantic")).results == []
    assert await search.index_embeddings() == 2
    with intelligence_library.database.connect() as connection:
        assert {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT e.model_digest FROM embeddings e "
                "JOIN chunks c ON c.id=e.chunk_id JOIN source_versions sv "
                "ON sv.id=c.source_version_id JOIN sources s ON s.id=sv.source_id "
                "WHERE sv.id=s.current_version_id"
            )
        } == {"digest-b"}


@pytest.mark.anyio
async def test_embedding_dimension_failure_is_persisted_and_retryable(
    intelligence_library: EducationalLibraryService,
):
    (intelligence_library.root / "one.md").write_text(
        "# Eins\n\nDer bestimmte Artikel zeigt Genus.", encoding="utf-8"
    )
    (intelligence_library.root / "two.md").write_text(
        "# Zwei\n\nDas Pronomen ersetzt ein Nomen.", encoding="utf-8"
    )
    intelligence_library.scan()
    provider = InconsistentEmbeddingProvider()
    search = EducationalSearchService(intelligence_library.database, provider)
    with pytest.raises(RuntimeError, match="dimensiones incompatibles"):
        await search.index_embeddings(batch_size=2)
    with intelligence_library.database.connect() as connection:
        rows = connection.execute(
            "SELECT status,error_code FROM embeddings ORDER BY chunk_id"
        ).fetchall()
    assert [(row["status"], row["error_code"]) for row in rows] == [
        ("failed", "EmbeddingDimensionError"),
        ("failed", "EmbeddingDimensionError"),
    ]
    valid = CountingEmbeddingProvider()
    recovered = EducationalSearchService(intelligence_library.database, valid)
    assert await recovered.index_embeddings(retry_failed=True) == 2


@pytest.mark.anyio
async def test_visual_reprocessing_is_selective_versioned_and_never_modifies_original(
    intelligence_library: EducationalLibraryService,
    monkeypatch: pytest.MonkeyPatch,
):
    original = b"%PDF-1.4\nfixture\n%%EOF"
    path = intelligence_library.root / "scan.pdf"
    path.write_bytes(original)
    intelligence_library.scan(process_documents=False)
    source = next(item for item in intelligence_library.list_sources() if item.name == path.name)

    def fake_run(command, **kwargs):
        del kwargs
        prefix = Path(command[-1])
        (prefix.parent / f"{prefix.name}-1.png").write_bytes(b"fixture-png")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "Tabelle: der Hund | den Hund\n"
                                "Akkusativ: Ich sehe den Hund."
                            )
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def post(self, *args, **kwargs):
            del args
            assert kwargs["json"]["model"] == "google/gemma-4-12b-qat"
            assert kwargs["json"]["temperature"] == 0
            assert kwargs["json"]["messages"][0]["content"][1]["type"] == "image_url"
            return FakeResponse()

    monkeypatch.setattr(
        "deutschos_api.educational_library.document_intelligence.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        "deutschos_api.educational_library.document_intelligence.subprocess.run",
        fake_run,
    )
    monkeypatch.setattr(
        "deutschos_api.educational_library.document_intelligence.httpx.AsyncClient",
        FakeClient,
    )
    intelligence = DocumentIntelligenceService(
        intelligence_library.database, intelligence_library.settings
    )
    variant = await intelligence.reprocess_vision(source.id, 1)
    repeated = await intelligence.reprocess_vision(source.id, 1)
    assert variant.id == repeated.id
    assert variant.method == "vision"
    assert variant.provenance["model"] == "google/gemma-4-12b-qat"
    assert variant.provenance["temporary_image_deleted"] is True
    assert "den Hund" in variant.text_preview
    assert path.read_bytes() == original
    with intelligence_library.database.connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM page_extraction_variants").fetchone()[0] == 1
        )


@pytest.mark.anyio
async def test_model_router_uses_installed_capabilities_and_bounded_fallback():
    provider = RouterProvider(
        ["google/gemma-4-12b-qat", "google/gemma-4-26b-a4b-qat", "google/gemma-4-12b-qat", "text-embedding-embeddinggemma-300m"],
        fail={"google/gemma-4-26b-a4b-qat"},
    )
    router = LibraryModelRouter(
        provider,
        ModelRoutingPolicy(
            planner="google/gemma-4-12b-qat",
            embedding="text-embedding-embeddinggemma-300m",
            teacher="google/gemma-4-26b-a4b-qat",
            fallback="google/gemma-4-12b-qat",
            vision="google/gemma-4-12b-qat",
            repair="google/gemma-4-12b-qat",
        ),
    )
    plan, selected, fallback = await router.structured_generate(
        ModelRole.TEACHER,
        [{"role": "user", "content": "die"}],
        TeacherQueryPlan,
    )
    assert plan.target_expression == "die"
    assert selected == "google/gemma-4-12b-qat"
    assert fallback
    assert router.candidates(ModelRole.TEACHER) == (
        "google/gemma-4-26b-a4b-qat",
        "google/gemma-4-12b-qat",
    )
    assert router.candidates(ModelRole.FALLBACK) == (
        "google/gemma-4-12b-qat",
        "google/gemma-4-26b-a4b-qat",
    )
    status = await router.status()
    assert next(item for item in status.roles if item.role == "vision").available
    missing = LibraryModelRouter(
        RouterProvider([]),
        router.policy,
    )
    with pytest.raises(ModelRoutingError):
        await missing.select(ModelRole.TEACHER)

    deep = TeacherQueryPlan(
        intent=TeacherIntent.GRAMMAR_EXPLANATION,
        language="de",
        target_expression="Konjunktiv II",
        ambiguity=QueryAmbiguity.LOW,
        search_queries=["Konjunktiv II"],
    )
    ordinary = deep.model_copy(update={"target_expression": "kein und nicht"})
    assert EducationalTeacherService._requires_deep_model(deep)
    assert not EducationalTeacherService._requires_deep_model(ordinary)


def test_schema_v5_contains_documental_memory_and_route_tables_without_main_alembic(
    intelligence_library: EducationalLibraryService,
):
    with intelligence_library.database.connect() as connection:
        assert connection.execute("SELECT max(version) FROM library_schema").fetchone()[0] == 5
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "source_editorial_events",
        "editorial_sections",
        "page_quality",
        "page_extraction_variants",
        "library_cache",
        "pedagogical_concepts",
        "document_page_mappings",
        "pedagogical_evidence_locations",
        "pedagogical_memory_audit",
        "canonical_route_imports",
        "canonical_topics",
        "canonical_outline_nodes",
        "canonical_legacy_mappings",
    } <= names
    assert intelligence_library.database_integrity() == ("ok", [])


@pytest.mark.anyio
async def test_intelligence_http_contracts_are_strict_and_operational(
    client,
    intelligence_library: EducationalLibraryService,
):
    theory_path = intelligence_library.root / "Herder Gramatica Alemana Hispanohablantes.md"
    workbook_path = intelligence_library.root / "Herder Gramatica Alemana Ejercicios.md"
    theory_path.write_text(
        "# Artikel\n\nTEMA 1 Artikel. Der Artikel zeigt Genus.", encoding="utf-8"
    )
    workbook_path.write_text(
        "# Übungen\n\nTEMA 1 Übungen. Ergänzen Sie den Artikel.", encoding="utf-8"
    )
    intelligence_library.scan()
    candidates = LibraryEditorialService(intelligence_library.database).candidates()
    by_role = {item.suggested_role.value: item.source for item in candidates}
    theory = by_role["core_theory"]
    with intelligence_library.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE sections SET text='TEMA 1 Artikel. Der Artikel zeigt Genus.',"
            "page_start=1,page_end=1 WHERE document_id=(SELECT d.id FROM documents d "
            "JOIN source_versions sv ON sv.id=d.source_version_id WHERE sv.source_id=?)",
            (theory.id,),
        )
        connection.execute(
            "UPDATE documents SET page_count=1 WHERE source_version_id="
            "(SELECT current_version_id FROM sources WHERE id=?)",
            (theory.id,),
        )
    embedding = CountingEmbeddingProvider()
    search = EducationalSearchService(intelligence_library.database, embedding)
    router = LibraryModelRouter(
        RouterProvider(["google/gemma-4-12b-qat", "google/gemma-4-12b-qat", "text-embedding-embeddinggemma-300m"]),
        ModelRoutingPolicy(
            planner="google/gemma-4-12b-qat",
            embedding="text-embedding-embeddinggemma-300m",
            teacher="google/gemma-4-12b-qat",
            fallback="google/gemma-4-26b-a4b-qat",
            vision="google/gemma-4-12b-qat",
            repair="google/gemma-4-12b-qat",
        ),
    )
    app.dependency_overrides[get_library_service] = lambda: intelligence_library
    app.dependency_overrides[get_library_search] = lambda: search
    app.dependency_overrides[get_library_model_router] = lambda: router
    try:
        core = await client.get("/api/library/core")
        assert core.status_code == 200
        assert not core.json()["ready"]
        assert core.json()["candidates"][0]["source"]["semantic_indexed_chunks"] == 0
        workbook = by_role["core_workbook"]
        assigned = await client.post(
            f"/api/library/sources/{theory.id}/core",
            json={
                "operation_id": "http-theory-001",
                "pedagogical_role": "core_theory",
                "display_alias": "Herder · Teoría",
                "related_source_id": workbook.id,
            },
        )
        assert assigned.status_code == 200
        assert assigned.json()["display_alias"] == "Herder · Teoría"
        assert theory_path.is_file()
        assert (
            await client.post(
                f"/api/library/sources/{workbook.id}/core",
                json={
                    "operation_id": "http-workbook-001",
                    "pedagogical_role": "core_workbook",
                    "display_alias": "Herder · Práctica",
                    "related_source_id": theory.id,
                },
            )
        ).status_code == 200
        assert (await client.get("/api/library/core")).json()["ready"]

        sections = await client.post(f"/api/library/sources/{theory.id}/sections/build")
        assert sections.status_code == 200
        assert sections.json()[0]["stable_key"] == "topic-001"
        section_id = sections.json()[0]["id"]
        updated = await client.put(
            f"/api/library/sections/{section_id}",
            json={"topic": "Artikel", "editorial_status": "user_confirmed"},
        )
        assert updated.status_code == 200
        assert updated.json()["topic"] == "Artikel"

        quality = await client.post(f"/api/library/sources/{theory.id}/pages/analyze")
        assert quality.status_code == 200
        assert quality.json()[0]["quality"] in {"acceptable", "good", "poor"}
        roles = await client.get("/api/library/models/roles")
        assert roles.status_code == 200
        assert (
            next(item for item in roles.json()["roles"] if item["role"] == "teacher")[
                "selected_model"
            ]
            == "google/gemma-4-12b-qat"
        )

        indexed = await client.post(
            "/api/library/semantic/index",
            json={"batch_size": 2, "core_first": True},
        )
        assert indexed.status_code == 202
        job = await client.get(f"/api/library/jobs/{indexed.json()['id']}")
        assert job.json()["state"] == "completed"
        hybrid = await client.get(
            "/api/library/search",
            params={"query": "artículo", "mode": "hybrid"},
        )
        assert hybrid.status_code == 200
        payload = hybrid.json()
        assert payload["semantic_available"]
        assert set(payload["timings"]) == {
            "fts_ms",
            "query_embedding_ms",
            "vector_ms",
            "ranking_ms",
            "total_ms",
        }
        assert "vector_json" not in str(payload)
    finally:
        app.dependency_overrides.pop(get_library_service, None)
        app.dependency_overrides.pop(get_library_search, None)
        app.dependency_overrides.pop(get_library_model_router, None)
