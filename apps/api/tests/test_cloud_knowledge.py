from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import shutil
import stat
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from llc_api.core.config import Settings
from llc_api.educational_library.cloud_knowledge.models import (
    AssertionOrigin,
    CapabilityRequirements,
    CloudExtractionCreate,
    CloudExtractionMode,
    CloudProviderDescriptor,
    CompactContentTransport,
    CompactStructureTransport,
    ProviderAvailability,
    ProviderCapabilities,
    ProviderExtractionRequest,
    ProviderRawResponse,
    ProviderUsage,
    ValidationOutcome,
)
from llc_api.educational_library.cloud_knowledge.normalization import (
    expand_content,
    expand_structure,
    provenance,
)
from llc_api.educational_library.cloud_knowledge.provider import (
    CloudKnowledgeProvider,
    CloudProviderError,
    CloudProviderRegistry,
)
from llc_api.educational_library.cloud_knowledge.providers import normalize_gemini_error
from llc_api.educational_library.cloud_knowledge.providers.google_gemini import (
    GoogleGeminiProvider,
    _pdf_for_upload,
)
from llc_api.educational_library.cloud_knowledge.service import CloudKnowledgeExtractionService
from llc_api.educational_library.cloud_knowledge.validation import (
    validate_content,
    validate_structure,
)
from llc_api.educational_library.database import LibraryDatabase
from llc_api.educational_library.dependencies import get_cloud_knowledge_extraction
from llc_api.educational_library.service import EducationalLibraryService
from llc_api.main import app

FIXTURES = Path(__file__).parent / "fixtures" / "page_comparisons"


class FakeCloudProvider(CloudKnowledgeProvider):
    def __init__(
        self,
        text: str,
        *,
        provider_id: str = "fake",
        capabilities: ProviderCapabilities | None = None,
        error: CloudProviderError | None = None,
    ):
        self.text = text
        self.calls = 0
        self.error = error
        self._descriptor = CloudProviderDescriptor(
            provider_id=provider_id,
            display_name="Offline fake",
            model="fake-model-v1",
            availability=ProviderAvailability.AVAILABLE,
            configured=True,
            capabilities=capabilities
            or ProviderCapabilities(
                supports_pdf=True,
                supports_images=True,
                supports_structured_output=True,
                supports_file_upload=True,
            ),
        )

    @property
    def descriptor(self) -> CloudProviderDescriptor:
        return self._descriptor

    def extract(self, request: ProviderExtractionRequest) -> ProviderRawResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert request.document_path.is_file()
        return ProviderRawResponse(
            text=self.text,
            raw={"response": "offline-fixture", "model": request.model},
            usage=ProviderUsage(input_tokens=10, output_tokens=20, total_tokens=30),
            provider_metadata={"network": False},
        )


@pytest.fixture
def cloud_library(tmp_path: Path):
    materials = tmp_path / "materials"
    materials.mkdir()
    source = FIXTURES / "fixture_a_target.pdf"
    target = materials / "fixture.pdf"
    shutil.copy2(source, target)
    settings = Settings(
        database_url="sqlite://",
        educational_materials_dir=materials,
        educational_library_runtime_dir=tmp_path / "runtime",
        educational_library_scan_on_startup=False,
        cloud_knowledge_provider="auto",
    )
    library = EducationalLibraryService(settings)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    now = "2026-08-18T12:00:00+00:00"
    with library.database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO sources(id,current_path,name,kind,format,size_bytes,mtime_ns,current_hash,"
            "status,processing_state,first_seen_at,last_seen_at,document_state) "
            "VALUES ('cloud-source','fixture.pdf','Fixture','document','.pdf',?,?,?,"
            "'present','pending',?,?,'candidate')",
            (target.stat().st_size, target.stat().st_mtime_ns, digest, now, now),
        )
        cursor = connection.execute(
            "INSERT INTO source_versions(source_id,version_number,content_hash,size_bytes,mtime_ns,"
            "processing_state,created_at,observed_path,observed_name,detected_at,document_state,"
            "availability_state,extraction_state,chunk_state,embedding_state,activation_state,"
            "is_active,version_provenance,page_count) VALUES ('cloud-source',1,?,?,?,'pending',?,"
            "'fixture.pdf','fixture.pdf',?,'candidate','present','pending','pending','pending',"
            "'candidate',0,'fixture',2)",
            (digest, target.stat().st_size, target.stat().st_mtime_ns, now, now),
        )
        version_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE sources SET latest_version_id=? WHERE id='cloud-source'", (version_id,)
        )
    return library, settings, version_id


def metadata(mode: CloudExtractionMode):
    return provenance(
        run_id="run-1",
        provider="fake",
        model="fake-model-v1",
        mode=mode,
        prompt_version="prompt.v1",
        transport_version="compact.v1",
        source_id="source-1",
        source_version_id=11,
        pages=[1, 2],
    )


def structure_text() -> str:
    return json.dumps(
        {
            "e": [
                {"k": "front_matter", "p": 1, "o": 1, "x": "Índice", "d": 0},
                {
                    "k": "topic",
                    "p": 1,
                    "o": 2,
                    "x": "Tema 1.",
                    "n": "Tema 1.",
                    "t": "Tema 1",
                    "d": 0,
                },
                {"k": "note", "p": 2, "o": 3, "x": "Nota", "d": 0},
                {"k": "back_matter", "p": 2, "o": 4, "x": "Apéndice", "d": 0},
            ],
            "m": [],
        },
        ensure_ascii=False,
    )


def test_provider_registry_manual_auto_and_capability_matching():
    compatible = FakeCloudProvider("{}", provider_id="compatible")
    incompatible = FakeCloudProvider(
        "{}",
        provider_id="no-vision",
        capabilities=ProviderCapabilities(supports_pdf=True, supports_structured_output=True),
    )
    registry = CloudProviderRegistry([compatible, incompatible])
    required = CapabilityRequirements(
        supports_pdf=True, supports_images=True, supports_structured_output=True
    )
    assert registry.resolve("compatible", required) is compatible
    assert registry.resolve("auto", required) is compatible
    with pytest.raises(CloudProviderError) as failure:
        registry.resolve("no-vision", required)
    assert failure.value.state == ProviderAvailability.INVALID_REQUEST
    assert failure.value.details["missing_capabilities"] == ["supports_images"]
    with pytest.raises(ValueError):
        registry.register(compatible)


def load_smoke_cli():
    script = Path(__file__).parents[3] / "scripts" / "cloud-knowledge-smoke.py"
    spec = importlib.util.spec_from_file_location("llc_cloud_knowledge_smoke", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cloud_extra_requires_current_interactions_sdk():
    pyproject = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert "google-genai>=2,<3" in pyproject["project"]["optional-dependencies"]["cloud"]
    assert version("google-genai").split(".", 1)[0] == "2"


def test_smoke_cli_uses_canonical_registry_and_resolves_google_without_network(tmp_path: Path):
    smoke = load_smoke_cli()
    settings = Settings(
        database_url="sqlite://",
        educational_materials_dir=tmp_path / "materials",
        educational_library_runtime_dir=tmp_path / "runtime",
        gemini_api_key="test-only-not-a-real-key",
    )
    registry = smoke.configured_registry(settings)
    provider_id = smoke.canonical_provider_id(registry, "google")
    provider = registry.resolve(
        provider_id,
        CapabilityRequirements(
            supports_pdf=True,
            supports_images=True,
            supports_structured_output=True,
            supports_file_upload=True,
        ),
    )
    assert provider_id == "google_gemini"
    assert provider.descriptor.configured is True
    assert provider.descriptor.provider_id == "google_gemini"


def test_smoke_cli_without_live_stops_before_settings_or_network(monkeypatch, capsys):
    smoke = load_smoke_cli()
    smoke.arguments = lambda: argparse.Namespace(live=False)

    def forbidden():
        raise AssertionError("settings/provider wiring must not run without --live")

    monkeypatch.setattr(smoke, "get_settings", forbidden)
    assert smoke.main() == 2
    assert "Refusing network access" in capsys.readouterr().err


def test_smoke_cli_main_maps_google_before_execute_without_exposing_key(
    tmp_path: Path, monkeypatch, capsys
):
    smoke = load_smoke_cli()
    secret = "test-cli-secret-must-not-appear"
    settings = Settings(
        database_url="sqlite://",
        educational_materials_dir=tmp_path / "materials",
        educational_library_runtime_dir=tmp_path / "runtime",
        gemini_api_key=secret,
    )
    monkeypatch.setattr(
        smoke,
        "arguments",
        lambda: argparse.Namespace(
            live=True,
            source_version_id=584,
            mode="structure",
            provider="google",
            model="gemini-3.6-flash",
            pages=[1],
        ),
    )
    monkeypatch.setattr(smoke, "get_settings", lambda: settings)
    monkeypatch.setattr(
        smoke,
        "EducationalLibraryService",
        lambda *_args, **_kwargs: SimpleNamespace(
            database=object(), root=tmp_path / "materials", runtime=tmp_path / "runtime"
        ),
    )

    class OfflineSmokeService:
        def __init__(self, _database, _root, _runtime, _settings, registry):
            self.registry = registry

        def create_run(self, request):
            assert request.provider == "google_gemini"
            assert {item.provider_id for item in self.registry.descriptors()} == {"google_gemini"}
            return SimpleNamespace(id="offline-run")

        def execute(self, _run_id):
            return SimpleNamespace(
                id="offline-run",
                state="completed",
                provider_status="available",
                validation_outcome="pass",
                usage=ProviderUsage(),
                artifacts=[],
            )

    monkeypatch.setattr(smoke, "CloudKnowledgeExtractionService", OfflineSmokeService)
    assert smoke.main() == 0
    output = capsys.readouterr().out
    assert "offline-run" in output
    assert secret not in output


def test_cloud_providers_are_not_imported_by_teacher_chat_or_study_runtime():
    source_root = Path(__file__).parents[1] / "src" / "llc_api"
    runtime_paths = [
        source_root / "api" / "routes.py",
        source_root / "services" / "chat.py",
        source_root / "educational_library" / "teacher.py",
        *sorted((source_root / "study").glob("*.py")),
    ]
    forbidden = ("cloud_knowledge", "google_gemini", "GoogleGeminiProvider")
    for path in runtime_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = [
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        ] + [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        assert not any(marker in imported for marker in forbidden for imported in imports), path


def test_selected_pdf_contains_only_requested_pages_and_is_deleted():
    pypdf = pytest.importorskip("pypdf")
    source = FIXTURES / "fixture_a_target.pdf"
    original = pypdf.PdfReader(source)
    with _pdf_for_upload(source, [2, 4]) as prepared:
        temporary = prepared.path
        assert temporary != source
        assert prepared.is_subset is True
        assert prepared.page_count == 2
        assert stat.S_IMODE(temporary.stat().st_mode) == 0o600
        selected = pypdf.PdfReader(temporary)
        assert len(selected.pages) == 2
        assert selected.pages[0].extract_text() == original.pages[1].extract_text()
        assert selected.pages[1].extract_text() == original.pages[3].extract_text()
    assert not temporary.exists()

    with _pdf_for_upload(source, [1, 2, 3, 4]) as full:
        assert full.path == source
        assert full.is_subset is False

    with pytest.raises(CloudProviderError) as failure:
        with _pdf_for_upload(source, [5]):
            pass
    assert failure.value.state == ProviderAvailability.INVALID_REQUEST
    assert failure.value.code == "pdf_page_outside_range"


def test_gemini_structure_uses_interactions_contract_and_cleans_up(monkeypatch, tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    calls: dict[str, object] = {}
    uploaded = SimpleNamespace(
        name="files/offline-test",
        uri="https://generativelanguage.googleapis.com/v1beta/files/offline-test",
        mime_type="application/pdf",
        state="ACTIVE",
    )

    class FakeFiles:
        def upload(self, *, file, config):
            path = Path(file)
            calls["upload_path"] = path
            calls["upload_page_count"] = len(pypdf.PdfReader(path).pages)
            calls["upload_mime_type"] = config.mime_type
            return uploaded

        def delete(self, *, name):
            calls["deleted"] = name

    class FakeInteractions:
        def create(self, **kwargs):
            calls["interaction"] = kwargs
            return SimpleNamespace(
                id="interaction-offline-test",
                status="completed",
                model="gemini-3.6-flash",
                output_text=structure_text(),
                outputs=[SimpleNamespace(text="legacy output must not be used")],
                usage=SimpleNamespace(
                    total_input_tokens=10,
                    total_output_tokens=20,
                    total_thought_tokens=3,
                    total_tokens=33,
                ),
            )

    class FakeModels:
        def generate_content(self, **_kwargs):
            raise AssertionError("Gemini 3.6 must use the Interactions API")

    class FakeClient:
        def __init__(self, **_kwargs):
            self.files = FakeFiles()
            self.interactions = FakeInteractions()
            self.models = FakeModels()

    from google import genai

    monkeypatch.setattr(genai, "Client", FakeClient)
    settings = Settings(
        database_url="sqlite://",
        educational_materials_dir=tmp_path / "materials",
        educational_library_runtime_dir=tmp_path / "runtime",
        gemini_api_key="offline-test-key",
        cloud_knowledge_max_output_tokens=65_536,
    )
    provider = GoogleGeminiProvider(settings)
    response = provider.extract(
        ProviderExtractionRequest(
            run_id="run-offline-test",
            source_id="source-offline-test",
            source_version_id=584,
            document_path=FIXTURES / "fixture_a_target.pdf",
            mime_type="application/pdf",
            mode=CloudExtractionMode.STRUCTURE,
            model="gemini-3.6-flash",
            pages=[1],
            prompt="Extract the structure.",
            response_schema={"type": "object", "properties": {"e": {"type": "array"}}},
        )
    )

    interaction = calls["interaction"]
    assert interaction == {
        "model": "gemini-3.6-flash",
        "input": [
            {
                "type": "document",
                "uri": uploaded.uri,
                "mime_type": "application/pdf",
            },
            {"type": "text", "text": "Extract the structure."},
        ],
        "generation_config": {
            "thinking_level": "minimal",
            "max_output_tokens": 65_536,
            "thinking_summaries": "none",
        },
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": {"type": "object", "properties": {"e": {"type": "array"}}},
        },
        "store": False,
    }
    assert calls["upload_page_count"] == 1
    assert calls["upload_mime_type"] == "application/pdf"
    assert calls["deleted"] == uploaded.name
    assert not Path(calls["upload_path"]).exists()
    assert response.text == structure_text()
    assert response.usage.total_tokens == 33
    assert response.provider_metadata["storage_mode"] == "interactions_store_false"


def test_schema_14_is_additive_reversible_and_backed_up(tmp_path: Path):
    database = LibraryDatabase(tmp_path / "library.sqlite3")
    assert database.migrate() == 14
    assert database.rollback_version_14() == 13
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='cloud_extraction_runs'"
            ).fetchone()
            is None
        )
    assert database.migrate() == 14
    backups = list((tmp_path / "backups").glob("library.sqlite3.schema13-*.bak"))
    assert len(backups) == 1
    with database.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='cloud_extraction_runs'"
        ).fetchone()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_model_config_and_api_key_are_separate_and_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaThisMustNeverAppearInSerialization12345")
    settings = Settings(
        database_url="sqlite://",
        cloud_knowledge_gemini_model="gemini-configurable-test",
    )
    assert settings.cloud_knowledge_gemini_model == "gemini-configurable-test"
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value().startswith("AIza")
    assert "AIzaThisMustNeverAppear" not in settings.model_dump_json()


class GeminiFailure(Exception):
    def __init__(self, code: int, message: str, *, details=None, status=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status = status


def test_gemini_error_and_quota_normalization_redacts_secrets():
    secret = "AIzaVerySecretCredentialThatMustBeRedacted"
    quota = normalize_gemini_error(
        GeminiFailure(429, f"RESOURCE_EXHAUSTED quota reached api_key={secret}"),
        api_key=secret,
    )
    assert quota.state == ProviderAvailability.QUOTA_EXHAUSTED
    assert secret not in quota.safe_message
    limited = normalize_gemini_error(GeminiFailure(429, "rate limited, retry later"))
    assert limited.state == ProviderAvailability.TEMPORARILY_LIMITED
    assert normalize_gemini_error(GeminiFailure(401, "bad credentials")).state == (
        ProviderAvailability.AUTHENTICATION_ERROR
    )
    assert normalize_gemini_error(GeminiFailure(503, "maintenance")).state == (
        ProviderAvailability.UNAVAILABLE
    )
    invalid = normalize_gemini_error(
        GeminiFailure(
            400,
            "Request contains an invalid argument.",
            status="INVALID_ARGUMENT",
            details={
                "error": {
                    "code": 400,
                    "message": "Invalid generation_config",
                    "details": [{"field": "thinking_budget"}],
                },
                "headers": {"authorization": secret},
                "api_key": secret,
            },
        ),
        api_key=secret,
    )
    assert invalid.details["provider_status"] == "INVALID_ARGUMENT"
    assert invalid.details["provider_details"] == {
        "error": {
            "code": 400,
            "message": "Invalid generation_config",
            "details": [{"field": "thinking_budget"}],
        }
    }
    assert secret not in json.dumps(invalid.details)


def test_compact_structure_expands_source_text_types_and_topic_number():
    transport = CompactStructureTransport.model_validate_json(structure_text())
    canonical = expand_structure(transport, metadata(CloudExtractionMode.STRUCTURE))
    assert [node.kind for node in canonical.nodes] == [
        "front_matter",
        "topic",
        "note",
        "back_matter",
    ]
    assert canonical.nodes[1].raw_visible_text == "Tema 1."
    assert canonical.nodes[1].normalized_label == "Tema 1"
    assert canonical.nodes[1].topic_number == 1
    assert validate_structure(canonical, [1, 2]).outcome == ValidationOutcome.PASS


def test_structure_referential_integrity_and_suspicious_hierarchy_warning():
    transport = CompactStructureTransport.model_validate(
        {
            "e": [
                {"k": "numbered_entry", "p": 1, "o": 1, "x": "5. Padre", "n": "5.", "d": 0},
                {"k": "numbered_entry", "p": 1, "o": 2, "x": "1. Uno", "n": "1.", "po": 1, "d": 1},
                {"k": "numbered_entry", "p": 1, "o": 3, "x": "2. Dos", "n": "2.", "po": 1, "d": 1},
                {"k": "numbered_entry", "p": 1, "o": 4, "x": "6. Seis", "n": "6.", "po": 1, "d": 1},
                {
                    "k": "numbered_entry",
                    "p": 1,
                    "o": 5,
                    "x": "7. Siete",
                    "n": "7.",
                    "po": 1,
                    "d": 1,
                },
            ]
        }
    )
    report = validate_structure(
        expand_structure(transport, metadata(CloudExtractionMode.STRUCTURE)), [1, 2]
    )
    assert report.outcome == ValidationOutcome.FAIL  # page 2 is missing
    assert any(issue.code == "suspicious_hierarchy" for issue in report.issues)
    bad_parent = CompactStructureTransport.model_validate(
        {"e": [{"k": "other", "p": 1, "o": 1, "x": "x", "po": 99, "d": 1}]}
    )
    bad_report = validate_structure(
        expand_structure(bad_parent, metadata(CloudExtractionMode.STRUCTURE)), [1]
    )
    assert any(issue.code == "missing_parent" for issue in bad_report.issues)


def test_content_evidence_relations_extracted_and_inferred_integrity():
    transport = CompactContentTransport.model_validate(
        {
            "i": [
                {
                    "o": 1,
                    "k": "definition",
                    "x": "Der Kasus ...",
                    "nl": "Kasus",
                    "s": "extracted",
                    "e": [{"p": 1, "q": "Der Kasus ..."}],
                },
                {"o": 2, "k": "concept", "x": "Deduced concept", "s": "inferred"},
            ],
            "r": [{"o": 1, "f": 2, "t": 1, "k": "requires", "s": "inferred"}],
        }
    )
    canonical = expand_content(transport, metadata(CloudExtractionMode.CONTENT))
    report = validate_content(canonical, [1, 2])
    assert report.outcome == ValidationOutcome.PASS
    assert canonical.items[0].extractor_status == AssertionOrigin.EXTRACTED
    assert canonical.items[1].extractor_status == AssertionOrigin.INFERRED

    extracted_relation = transport.model_copy(deep=True)
    extracted_relation.r[0].s = AssertionOrigin.EXTRACTED
    report = validate_content(
        expand_content(extracted_relation, metadata(CloudExtractionMode.CONTENT)), [1, 2]
    )
    assert report.outcome == ValidationOutcome.FAIL
    assert any(issue.code == "extracted_relation_without_evidence" for issue in report.issues)

    with pytest.raises(ValidationError):
        CompactContentTransport.model_validate(
            {"i": [{"o": 1, "k": "unknown", "x": "invented", "s": "extracted"}]}
        )


def make_service(cloud_library, provider: FakeCloudProvider):
    library, settings, version_id = cloud_library
    service = CloudKnowledgeExtractionService(
        library.database,
        library.root,
        library.runtime,
        settings,
        CloudProviderRegistry([provider]),
    )
    return service, version_id


def test_raw_artifact_is_saved_before_transport_validation(cloud_library):
    provider = FakeCloudProvider("not-json")
    service, version_id = make_service(cloud_library, provider)
    created = service.create_run(
        CloudExtractionCreate(
            source_version_id=version_id,
            mode=CloudExtractionMode.STRUCTURE,
            provider="fake",
        )
    )
    failed = service.execute(created.id)
    assert provider.calls == 1
    assert failed.state == "failed"
    assert failed.validation_outcome == ValidationOutcome.FAIL
    assert [artifact.kind for artifact in failed.artifacts] == ["raw"]
    raw_path = cloud_library[0].runtime / failed.artifacts[0].relative_path
    assert json.loads(raw_path.read_text())["provider_text"] == "not-json"


def test_quota_failure_is_preserved_as_run_state_not_document_corruption(cloud_library):
    provider = FakeCloudProvider(
        "",
        error=CloudProviderError(
            ProviderAvailability.QUOTA_EXHAUSTED,
            "Provider quota is exhausted.",
            code="429",
            details={"http_status": 429},
        ),
    )
    service, version_id = make_service(cloud_library, provider)
    created = service.create_run(
        CloudExtractionCreate(
            source_version_id=version_id,
            mode=CloudExtractionMode.CONTENT,
            provider="fake",
        )
    )
    failed = service.execute(created.id)
    assert failed.state == "failed"
    assert failed.provider_status == ProviderAvailability.QUOTA_EXHAUSTED
    assert failed.normalized_error == ProviderAvailability.QUOTA_EXHAUSTED
    assert failed.provider_error_code == "429"
    assert failed.artifacts == []
    with cloud_library[0].database.connect() as connection:
        state = connection.execute(
            "SELECT processing_state FROM source_versions WHERE id=?", (version_id,)
        ).fetchone()[0]
    assert state == "pending"


def test_fake_provider_full_pipeline_has_no_network_and_preserves_provenance(cloud_library):
    provider = FakeCloudProvider(structure_text())
    cloud_library[1].cloud_knowledge_provider = "fake"
    service, version_id = make_service(cloud_library, provider)
    created = service.create_run(
        CloudExtractionCreate(
            source_version_id=version_id,
            mode=CloudExtractionMode.STRUCTURE,
            provider="auto",
            model="manual-model-override",
            pages=[1, 2],
        )
    )
    completed = service.execute(created.id)
    assert completed.state == "completed"
    assert completed.model == "manual-model-override"
    assert completed.usage.total_tokens == 30
    assert completed.provider_metadata == {"network": False}
    assert completed.validation_outcome == ValidationOutcome.PASS
    assert [artifact.kind for artifact in completed.artifacts] == [
        "raw",
        "transport",
        "canonical",
        "validation",
    ]
    report = service.validation_report(completed.id)
    assert report.outcome == ValidationOutcome.PASS
    canonical_artifact = next(item for item in completed.artifacts if item.kind == "canonical")
    canonical = json.loads(
        (cloud_library[0].runtime / canonical_artifact.relative_path).read_text()
    )
    assert canonical["provenance"]["source_version_id"] == version_id
    assert canonical["provenance"]["provider"] == "fake"
    assert canonical["provenance"]["model"] == "manual-model-override"


@pytest.mark.anyio
async def test_cloud_api_lists_capabilities_and_requires_explicit_execute(cloud_library):
    provider = FakeCloudProvider(structure_text())
    service, version_id = make_service(cloud_library, provider)
    app.dependency_overrides[get_cloud_knowledge_extraction] = lambda: service
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            providers = await client.get("/api/library/cloud-knowledge/providers")
            assert providers.status_code == 200
            assert providers.json()[0]["capabilities"]["supports_pdf"] is True
            created = await client.post(
                "/api/library/cloud-knowledge/runs",
                json={"source_version_id": version_id, "mode": "structure", "provider": "fake"},
            )
            assert created.status_code == 201
            assert created.json()["state"] == "planned"
            assert provider.calls == 0
            executed = await client.post(
                f"/api/library/cloud-knowledge/runs/{created.json()['id']}/execute"
            )
            assert executed.status_code == 200
            assert executed.json()["state"] == "completed"
            validation = await client.get(
                f"/api/library/cloud-knowledge/runs/{created.json()['id']}/validation"
            )
            assert validation.json()["outcome"] == "pass"
    finally:
        app.dependency_overrides.clear()
