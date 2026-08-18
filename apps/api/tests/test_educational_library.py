from __future__ import annotations

import fcntl
import json
import shutil
import zipfile
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel

from llc_api.core.config import Settings
from llc_api.educational_library.chunking import chunk_sections
from llc_api.educational_library.dependencies import get_library_service
from llc_api.educational_library.extractors import extract
from llc_api.educational_library.inventory import InventoryEntry, collect_inventory
from llc_api.educational_library.knowledge import EducationalKnowledgeService
from llc_api.educational_library.schemas import (
    ExtractedSection,
    GroundedDraftPayload,
    GroundedGenerationRequest,
    JobState,
    KnowledgeCitation,
    KnowledgeDraft,
    KnowledgeGenerationRequest,
    KnowledgeKind,
    KnowledgeReviewRequest,
    LibraryBusyError,
    LibraryContractError,
    ProcessingState,
    SourceKind,
)
from llc_api.educational_library.search import EducationalSearchService
from llc_api.educational_library.service import EducationalLibraryService, utc_text
from llc_api.educational_library.transcription import UnavailableTranscriptionProvider
from llc_api.main import app
from llc_api.providers.base import ModelProvider
from llc_api.providers.dependencies import get_model_provider
from llc_api.schemas.api import ModelInfo


@pytest.fixture
def library_settings(tmp_path: Path) -> Settings:
    materials = tmp_path / "materials"
    materials.mkdir()
    return Settings(
        database_url="sqlite://",
        educational_materials_dir=materials,
        educational_library_runtime_dir=tmp_path / "runtime",
        educational_library_scan_on_startup=False,
        educational_library_embedding_model="",
        lm_studio_model="test-qwen",
    )


@pytest.fixture
def library(library_settings: Settings) -> EducationalLibraryService:
    return EducationalLibraryService(library_settings)


def _entry(path: Path) -> InventoryEntry:
    stat = path.stat()
    suffix = path.suffix.lower()
    kind = SourceKind.DOCUMENT
    if suffix in {".srt", ".vtt"}:
        kind = SourceKind.SUBTITLE
    return InventoryEntry(
        path=path,
        relative_path=path.name,
        name=path.name,
        extension=suffix,
        kind=kind,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        is_hidden=False,
        is_ignored=False,
        is_symlink=False,
    )


def _minimal_pdf(path: Path, text: str | None) -> None:
    stream = "" if text is None else f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n{body}\nendobj\n".encode())
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(payload)


def _docx(path: Path) -> None:
    document = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Artikel</w:t></w:r></w:p>
    <w:p><w:r><w:t>Der Artikel zeigt das Genus.</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>die Frau</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    </w:body></w:document>"""
    core = """<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>DOCX Test</dc:title><dc:creator>LLC</dc:creator></cp:coreProperties>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("docProps/core.xml", core)


def _epub(path: Path) -> None:
    container = """<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>"""
    opf = """<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>EPUB Test</dc:title><dc:creator>LLC</dc:creator><dc:language>de</dc:language></metadata><manifest><item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/></spine></package>"""
    chapter = "<html><body><nav>Menü</nav><h1>Begrüßung</h1><p>Guten Morgen ist ein Gruß.</p><script>secret()</script></body></html>"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/chapter.xhtml", chapter)


class FakeEmbeddingProvider:
    provider_name = "test"
    model_name = "deterministic-test-only"
    model_version = "v1"

    async def available(self) -> bool:
        return True

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [
            [float(text.casefold().count("artikel")), float(text.casefold().count("gruß") + 1)]
            for text in texts
        ]


class UnavailableEmbeddingProvider(FakeEmbeddingProvider):
    model_name = "text-embedding-embeddinggemma-300m"

    async def available(self) -> bool:
        return False


class FakeModelProvider(ModelProvider):
    async def health_check(self) -> bool:
        return True

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="test-qwen")]

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        del model, messages
        return "unused"

    async def stream_chat(self, model: str, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        yield await self.chat(model, messages)

    async def structured_generate(
        self, model: str, messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> BaseModel:
        del model
        context = messages[-1]["content"]
        chunk_match = next(
            (int(value) for value in __import__("re").findall(r'"chunk_id":\s*(\d+)', context)),
            1,
        )
        if schema is KnowledgeDraft:
            quote_match = __import__("re").search(r'"text":\s*"([^"]+)', context)
            quote = (quote_match.group(1) if quote_match else "Der Artikel").replace("\\n", " ")[
                :60
            ]
            return KnowledgeDraft(
                evidence_sufficient=True,
                kind=KnowledgeKind.GRAMMAR_RULE,
                title="Artículos básicos",
                content_es="Los artículos alemanes acompañan al sustantivo según la fuente.",
                german_examples=["der Artikel"],
                translations=["el artículo"],
                cefr_level="A1",
                topics=["grammar"],
                keywords=["Artikel"],
                warnings=[],
                citations=[KnowledgeCitation(chunk_id=chunk_match, quote=quote)],
                confidence=0.75,
            )
        if schema is GroundedDraftPayload:
            return GroundedDraftPayload(
                title="Microlección de artículos",
                explanation="La fuente relaciona Artikel con el género gramatical.",
                examples=["Der Artikel ist wichtig."],
                exercises=["Completa: ___ Artikel."],
                claims=[
                    {
                        "text": "Los artículos aportan información gramatical.",
                        "source_chunk_ids": [chunk_match],
                    }
                ],
                warnings=[],
                confidence=0.7,
            )
        raise AssertionError(schema)


def test_inventory_is_safe_and_reports_real_categories(library_settings: Settings):
    root = library_settings.educational_materials_dir
    (root / "lesson.txt").write_text("Hallo", encoding="utf-8")
    (root / "empty").write_bytes(b"")
    (root / ".DS_Store").write_text("ignored", encoding="utf-8")
    (root / "audio.mp3").write_bytes(b"fake")
    (root / "movie.mp4").write_bytes(b"fake")
    (root / "captions.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
    (root / "image.png").write_bytes(b"fake")
    (root / "archive.zip").write_bytes(b"fake")
    (root / "unknown.xyz").write_bytes(b"fake")
    (root / "duplicate-a.txt").write_text("same", encoding="utf-8")
    (root / "duplicate-b.txt").write_text("same", encoding="utf-8")
    symlink = root / "outside-link"
    try:
        symlink.symlink_to(root.parent)
    except OSError:
        pytest.skip("filesystem does not permit symlinks")

    snapshot = collect_inventory(root)
    report = snapshot.report

    assert report.files == 11
    assert report.hidden_files == 1
    assert report.empty_files == 1
    assert report.without_extension == 2
    assert report.audio == 1
    assert report.video == 1
    assert report.subtitles == 1
    assert report.images == 1
    assert report.archives == 1
    assert report.unknown_formats >= 2
    assert report.symlinks == 1
    assert report.confirmed_duplicate_groups == 2
    assert any("duplicate-a.txt" in group for group in report.duplicate_groups)


def test_incremental_scan_versions_rename_missing_duplicate_and_restart(
    library: EducationalLibraryService,
    library_settings: Settings,
):
    root = library_settings.educational_materials_dir
    lesson = root / "lesson.md"
    lesson.write_text("# Artikel\n\nDer Artikel zeigt das Genus.", encoding="utf-8")

    first = library.scan()
    assert first.new == 1
    assert first.processed == 1
    source = library.list_sources()[0]
    assert source.current_version == 1
    assert library.summary().chunks == 1

    second = library.scan()
    assert second.unchanged == 1
    assert second.processed == 0
    assert library.source_versions(source.id)[0].version_number == 1

    lesson.write_text("# Artikel\n\nDer Artikel zeigt Genus und Kasus.", encoding="utf-8")
    modified = library.scan()
    assert modified.modified == 1
    assert library.source_versions(source.id)[0].version_number == 2
    assert len(library.source_versions(source.id)) == 2

    renamed = root / "renamed.md"
    lesson.rename(renamed)
    rename_scan = library.scan()
    assert rename_scan.renamed == 1
    assert library.get_source(source.id).current_path == "renamed.md"
    assert len(library.source_versions(source.id)) == 2

    duplicate = root / "copy.md"
    shutil.copyfile(renamed, duplicate)
    duplicate_scan = library.scan()
    assert duplicate_scan.new == 1
    assert duplicate_scan.duplicates == 1
    copy_source = next(item for item in library.list_sources() if item.name == "copy.md")
    assert copy_source.duplicate_of_source_id == source.id

    renamed.unlink()
    missing_scan = library.scan()
    assert missing_scan.missing == 1
    assert library.get_source(source.id).status.value == "missing"

    with library.database.transaction(immediate=True) as connection:
        job_id = str(uuid4())
        connection.execute(
            "INSERT INTO processing_jobs(id,kind,state,priority,payload_json,created_at,updated_at) "
            "VALUES (?,'scan','running',0,'{}',?,?)",
            (job_id, utc_text(), utc_text()),
        )
    restarted = EducationalLibraryService(library_settings)
    assert restarted.get_job(job_id).state.value == "interrupted"


def test_scan_lock_path_traversal_and_per_file_error(
    library: EducationalLibraryService,
    library_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    root = library_settings.educational_materials_dir
    good = root / "good.txt"
    bad = root / "bad.txt"
    good.write_text("Guten Morgen", encoding="utf-8")
    bad.write_text("nicht lesbar", encoding="utf-8")
    original = __import__(
        "llc_api.educational_library.service", fromlist=["sha256_file"]
    ).sha256_file

    def fail_one(path: Path) -> str:
        if path.name == "bad.txt":
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr("llc_api.educational_library.service.sha256_file", fail_one)
    result = library.scan()
    assert result.new == 1
    assert result.errors == 1
    assert any(item.name == "good.txt" for item in library.list_sources())

    lock = library.runtime / "scan.lock"
    lock.write_text("stale", encoding="utf-8")
    assert library.scan().unchanged == 1
    descriptor = lock.open("r+")
    fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(LibraryBusyError):
            library.scan()
    finally:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)
        descriptor.close()

    source = next(item for item in library.list_sources() if item.name == "good.txt")
    with library.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE sources SET current_path='../outside.txt' WHERE id=?", (source.id,)
        )
    with pytest.raises(LibraryContractError):
        library.reprocess_source(source.id)

    with library.database.transaction(immediate=True) as connection:
        active_job_id = str(uuid4())
        connection.execute(
            "INSERT INTO processing_jobs(id,kind,state,priority,payload_json,created_at,updated_at) "
            "VALUES (?,'scan','running',0,'{}',?,?)",
            (active_job_id, utc_text(), utc_text()),
        )
    active_descriptor = library._scan_lock()
    try:
        concurrent_context = EducationalLibraryService(library_settings)
        assert concurrent_context.get_job(active_job_id).state == JobState.RUNNING
    finally:
        library._unlock_scan(active_descriptor)
    restarted_context = EducationalLibraryService(library_settings)
    assert restarted_context.get_job(active_job_id).state == JobState.INTERRUPTED


def test_extractors_for_text_html_docx_epub_subtitles_and_pdf(
    library_settings: Settings,
):
    root = library_settings.educational_materials_dir
    files: list[Path] = []
    txt = root / "lesson.txt"
    txt.write_text("GRAMMATIK\nDer Artikel ist wichtig.", encoding="utf-8")
    files.append(txt)
    markdown = root / "lesson.md"
    markdown.write_text("# Gruß\n\nGuten Morgen!", encoding="utf-8")
    files.append(markdown)
    html = root / "lesson.html"
    html.write_text(
        "<nav>skip</nav><h1>Lesen</h1><p>Lena liest.</p><script>skip</script>",
        encoding="utf-8",
    )
    files.append(html)
    docx = root / "lesson.docx"
    _docx(docx)
    files.append(docx)
    epub = root / "lesson.epub"
    _epub(epub)
    files.append(epub)
    srt = root / "lesson.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,500\nGuten Morgen!\n", encoding="utf-8")
    files.append(srt)
    vtt = root / "lesson.vtt"
    vtt.write_text("WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nHallo!\n", encoding="utf-8")
    files.append(vtt)

    results = {path.suffix: extract(_entry(path), library_settings) for path in files}
    assert results[".txt"].sections[0].title == "GRAMMATIK"
    assert results[".md"].sections[0].title == "Gruß"
    assert "skip" not in " ".join(section.text for section in results[".html"].sections)
    assert results[".docx"].title == "DOCX Test"
    assert any("die Frau" in section.text for section in results[".docx"].sections)
    assert results[".epub"].title == "EPUB Test"
    assert results[".srt"].sections[0].start_seconds == 1
    assert results[".vtt"].sections[0].end_seconds == 4

    if shutil.which("pdftotext"):
        pdf = root / "text.pdf"
        _minimal_pdf(pdf, "Der Artikel ist wichtig.")
        textual = extract(_entry(pdf), library_settings)
        assert textual.page_count == 1
        assert textual.sections[0].page_start == 1
        blank = root / "blank.pdf"
        _minimal_pdf(blank, None)
        scanned = extract(_entry(blank), library_settings)
        assert scanned.needs_ocr
        assert scanned.processing_state == ProcessingState.NEEDS_OCR

    unsupported = root / "archive.zip"
    unsupported.write_bytes(b"not extracted")
    result = extract(_entry(unsupported), library_settings)
    assert result.processing_state == ProcessingState.UNSUPPORTED


def test_structural_chunking_stable_separates_solutions_and_timestamps():
    sections = [
        ExtractedSection(
            sequence=0,
            kind="section",
            title="Regel",
            hierarchy=["Kapitel 1", "Regel"],
            text="\n\n".join(
                f"Regel {index}: Der Artikel zeigt das Genus." for index in range(100)
            ),
            page_start=4,
            page_end=4,
            content_role="theory",
        ),
        ExtractedSection(
            sequence=1,
            kind="section",
            title="Lösungen",
            text="1. der 2. die",
            page_start=5,
            page_end=5,
            content_role="solution",
        ),
        ExtractedSection(
            sequence=2,
            kind="subtitle",
            text="Bitte sprechen Sie langsamer.",
            start_seconds=10,
            end_seconds=12,
            content_role="transcript",
        ),
    ]
    first = chunk_sections(sections)
    second = chunk_sections(sections)
    assert [item.content_hash for item in first] == [item.content_hash for item in second]
    assert len(first) > 3
    assert all(item.page_start == 4 for item in first if item.content_role == "theory")
    assert [item.content_role for item in first].count("solution") == 1
    transcript = next(item for item in first if item.content_role == "transcript")
    assert transcript.start_seconds == 10
    assert transcript.end_seconds == 12


@pytest.mark.anyio
async def test_fts_semantic_hybrid_filters_and_fallback(
    library: EducationalLibraryService,
    library_settings: Settings,
):
    root = library_settings.educational_materials_dir
    (root / "grammar.md").write_text(
        "# Artikel\n\nDer Artikel zeigt das Genus.\n\n# Lösungen\n\nDer.",
        encoding="utf-8",
    )
    (root / "greeting.md").write_text("# Gruß\n\nGuten Morgen ist ein Gruß.", encoding="utf-8")
    library.scan()
    lexical_service = EducationalSearchService(library.database)

    lexical = await lexical_service.search("Artikel", mode="lexical")
    assert lexical.effective_mode == "lexical"
    assert lexical.results[0].source_name == "grammar.md"
    assert all(result.content_role != "solution" for result in lexical.results)
    fallback = await lexical_service.search("Artikel", mode="hybrid")
    assert fallback.effective_mode == "lexical"
    assert fallback.warning
    unavailable = await EducationalSearchService(
        library.database, UnavailableEmbeddingProvider()
    ).search("Artikel", mode="semantic")
    assert unavailable.effective_mode == "lexical"
    assert "text-embedding-embeddinggemma-300m" in (unavailable.warning or "")
    assert "LM Studio" in (unavailable.warning or "")

    semantic_service = EducationalSearchService(library.database, FakeEmbeddingProvider())
    indexed = await semantic_service.index_embeddings()
    # Solution chunks remain searchable only when explicitly requested and are
    # deliberately excluded from the semantic index.
    assert indexed == 2
    with library.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM embeddings").fetchone()[0] == 2
    semantic = await semantic_service.search("Artikel", mode="semantic")
    hybrid = await semantic_service.search("Artikel", mode="hybrid")
    assert semantic.effective_mode == "semantic"
    assert hybrid.effective_mode == "hybrid"
    assert hybrid.results[0].source_name == "grammar.md"


@pytest.mark.anyio
async def test_knowledge_provenance_review_conflict_stale_and_grounded_generation(
    library: EducationalLibraryService,
    library_settings: Settings,
):
    path = library_settings.educational_materials_dir / "grammar.md"
    path.write_text("# Artikel\n\nDer Artikel zeigt das Genus.", encoding="utf-8")
    library.scan()
    search = EducationalSearchService(library.database)
    knowledge = EducationalKnowledgeService(
        library.database, search, FakeModelProvider(), default_model="test-qwen"
    )

    unit = await knowledge.generate_knowledge(KnowledgeGenerationRequest(query="Artikel"))
    assert unit.status.value == "candidate"
    assert unit.citations
    duplicate_citation_draft = KnowledgeDraft(
        evidence_sufficient=True,
        kind=KnowledgeKind.SUMMARY,
        title="Resumen con dos citas del mismo fragmento",
        content_es="La fuente contiene una explicación breve.",
        german_examples=[],
        translations=[],
        cefr_level="A1",
        topics=["grammar"],
        keywords=["Artikel"],
        warnings=[],
        citations=[unit.citations[0], unit.citations[0]],
        confidence=0.6,
    )
    deduplicated = knowledge._persist_knowledge(duplicate_citation_draft, "test-qwen")
    assert len(deduplicated.citations) == 1
    approved = knowledge.review(unit.id, KnowledgeReviewRequest(action="approve"))
    assert approved.status.value == "approved"

    grounded = await knowledge.grounded_generate(
        GroundedGenerationRequest(query="Artikel", level="A1", objective="micro_lesson")
    )
    assert grounded.sources
    assert grounded.payload.claims[0].source_chunk_ids[0] == grounded.sources[0].chunk_id
    assert grounded.payload.confidence == 0.7
    assert any("revisión editorial" in warning for warning in grounded.payload.warnings)
    assert grounded.review_status == "draft"

    with library.database.transaction(immediate=True) as connection:
        chunk_id = unit.citations[0].chunk_id
        version_id = connection.execute(
            "SELECT source_version_id FROM chunks WHERE id=?", (chunk_id,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO knowledge_units(id,kind,title,content_es,german_examples_json,"
            "translations_json,cefr_level,topics_json,keywords_json,warnings_json,confidence,"
            "status,model,prompt_version,content_hash,stale,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                "grammar_rule",
                "Artículos básicos",
                "Una explicación contradictoria.",
                "[]",
                "[]",
                "A1",
                "[]",
                "[]",
                "[]",
                0.4,
                "conflict",
                "test-qwen",
                "library-knowledge.v1",
                "different",
                0,
                utc_text(),
                utc_text(),
            ),
        )
        assert version_id

    path.write_text("# Artikel\n\nDer Artikel kann Genus und Kasus zeigen.", encoding="utf-8")
    library.scan()
    still_active = knowledge.get_knowledge(unit.id)
    assert still_active.status.value == "approved"
    source = library.list_sources()[0]
    assert source.current_version == 1
    assert len(library.source_versions(source.id)) == 2
    assert not still_active.stale


@pytest.mark.anyio
async def test_api_library_vertical_and_main_progress_is_untouched(
    client: httpx.AsyncClient,
    library: EducationalLibraryService,
    library_settings: Settings,
    db_session_factory,
):
    path = library_settings.educational_materials_dir / "grammar.md"
    path.write_text("# Artikel\n\nDer Artikel zeigt das Genus.", encoding="utf-8")

    def service_override() -> EducationalLibraryService:
        return library

    fake_provider = FakeModelProvider()
    app.dependency_overrides[get_library_service] = service_override
    app.dependency_overrides[get_model_provider] = lambda: fake_provider
    try:
        with db_session_factory() as db:
            before_skills = db.execute(
                __import__("sqlalchemy").text("SELECT count(*) FROM student_skills")
            ).scalar_one()
            before_evidence = db.execute(
                __import__("sqlalchemy").text("SELECT count(*) FROM skill_evidence")
            ).scalar_one()

        response = await client.post("/api/library/scan", json={"process_documents": True})
        assert response.status_code == 202
        status_response = await client.get("/api/library/status")
        assert status_response.status_code == 200
        assert status_response.json()["total_sources"] == 1
        assert status_response.json()["embeddings"] == 0
        assert (await client.get("/api/library/jobs")).json()[0]["state"] == "completed"
        sources = (await client.get("/api/library/sources")).json()
        assert len(sources) == 1
        source_id = sources[0]["id"]
        assert (await client.get(f"/api/library/sources/{source_id}/versions")).status_code == 200
        chunks = (await client.get(f"/api/library/sources/{source_id}/chunks")).json()
        assert chunks and len(chunks[0]["text"]) < 2_000

        search = await client.get("/api/library/search", params={"query": "Artikel"})
        assert search.status_code == 200
        assert search.json()["results"][0]["source_id"] == source_id
        assert "text" not in search.json()["results"][0]
        assert (await client.get("/api/library/search", params={"query": "??"})).status_code == 422
        assert (
            await client.get("/api/library/sources", params={"status": "invalid"})
        ).status_code == 422
        traversal = await client.get("/api/library/sources/../../outside")
        assert traversal.status_code in {404, 422}
        assert (await client.get("/api/library/sources/not-found")).status_code == 404

        learned = await client.post(
            "/api/library/knowledge/generate",
            json={"query": "Artikel", "max_chunks": 2},
        )
        assert learned.status_code == 200
        assert learned.json()["status"] == "candidate"
        approved = await client.post(
            f"/api/library/knowledge/{learned.json()['id']}/review",
            json={"action": "approve", "comment": "fixture review"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        generated = await client.post(
            "/api/library/grounded/generate",
            json={
                "query": "Artikel",
                "level": "A1",
                "objective": "micro_lesson",
                "explanation_language": "es",
                "max_sources": 3,
            },
        )
        assert generated.status_code == 200
        payload = generated.json()
        assert payload["sources"]
        assert "extracted_text" not in json.dumps(payload)

        with db_session_factory() as db:
            assert (
                db.execute(
                    __import__("sqlalchemy").text("SELECT count(*) FROM student_skills")
                ).scalar_one()
                == before_skills
            )
            assert (
                db.execute(
                    __import__("sqlalchemy").text("SELECT count(*) FROM skill_evidence")
                ).scalar_one()
                == before_evidence
            )
    finally:
        app.dependency_overrides.pop(get_library_service, None)
        app.dependency_overrides.pop(get_model_provider, None)


def test_library_database_integrity_and_no_main_schema_migration(
    library: EducationalLibraryService,
):
    quick, foreign = library.database_integrity()
    assert quick == "ok"
    assert foreign == []
    with library.database.connect() as connection:
        assert connection.execute("SELECT max(version) FROM library_schema").fetchone()[0] == 14
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='chunk_fts'").fetchone()


@pytest.mark.anyio
async def test_transcription_fallback_never_fabricates_content(tmp_path: Path):
    provider = UnavailableTranscriptionProvider()
    assert not await provider.available()
    with pytest.raises(RuntimeError, match="transcriptor local"):
        await provider.transcribe(tmp_path / "audio.mp3")
