#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api" / "src"
sys.path.insert(0, str(API_SRC))

from llc_api.core.config import get_settings  # noqa: E402
from llc_api.educational_library.knowledge import (  # noqa: E402
    EducationalKnowledgeService,
)
from llc_api.educational_library.canonical_route import (  # noqa: E402
    CanonicalRouteService,
)
from llc_api.educational_library.canonical_route_visual import (  # noqa: E402
    HerderIndexVisualExtractor,
    load_canonical_index,
    load_visual_bundle,
)
from llc_api.educational_library.document_intelligence import (  # noqa: E402
    DocumentIntelligenceService,
)
from llc_api.educational_library.memory import (  # noqa: E402
    PedagogicalMemoryService,
)
from llc_api.educational_library.editorial import (  # noqa: E402
    LibraryEditorialService,
)
from llc_api.educational_library.schemas import CoreSourceAssignmentRequest  # noqa: E402
from llc_api.educational_library.schemas import (  # noqa: E402
    GroundedGenerationRequest,
    KnowledgeGenerationRequest,
    PedagogicalMemoryImportRequest,
    TeacherAskRequest,
)
from llc_api.educational_library.search import (  # noqa: E402
    EducationalSearchService,
    LMStudioEmbeddingProvider,
)
from llc_api.educational_library.routing import (  # noqa: E402
    LibraryModelRouter,
    ModelRoutingPolicy,
)
from llc_api.educational_library.service import (  # noqa: E402
    EducationalLibraryService,
)
from llc_api.educational_library.teacher import (  # noqa: E402
    EducationalTeacherService,
    LearnerContext,
)
from llc_api.providers.lm_studio import LMStudioProvider  # noqa: E402
from llc_api.providers.base import (  # noqa: E402
    ProviderResponseError,
    ProviderUnavailableError,
)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Gestiona la biblioteca educativa local."
    )
    sub = command.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    scan = sub.add_parser("scan")
    scan.add_argument("--metadata-only", action="store_true")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument(
        "--mode", choices=("lexical", "semantic", "hybrid"), default="lexical"
    )
    search.add_argument("--limit", type=int, default=10)
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    derive = sub.add_parser("derive")
    derive.add_argument("query")
    derive.add_argument("--model")
    generate = sub.add_parser("generate")
    generate.add_argument("query")
    generate.add_argument(
        "--objective",
        choices=(
            "explanation",
            "micro_lesson",
            "exercises",
            "answer",
            "error_explanation",
        ),
        default="micro_lesson",
    )
    generate.add_argument("--level", default="A1")
    generate.add_argument("--model")
    sub.add_parser("integrity")
    sub.add_parser("core")
    assign = sub.add_parser("assign-core")
    assign.add_argument("--apply-unambiguous", action="store_true", required=True)
    sections = sub.add_parser("sections")
    sections.add_argument("source_id")
    sections.add_argument("--build", action="store_true")
    quality = sub.add_parser("page-quality")
    quality.add_argument("source_id")
    reprocess = sub.add_parser("reprocess-page")
    reprocess.add_argument("source_id")
    reprocess.add_argument("page_number", type=int)
    reprocess.add_argument("--method", choices=("pdftotext", "vision"), required=True)
    embeddings = sub.add_parser("index-semantic")
    embeddings.add_argument("--batch-size", type=int, default=16)
    embeddings.add_argument("--limit", type=int)
    embeddings.add_argument("--supplementary-first", action="store_true")
    sub.add_parser("memory-status")
    memory_import = sub.add_parser("memory-import")
    memory_import.add_argument("--operation-id", default="verified-memory-import-v1")
    memory_import.add_argument("--confirm-herder-akkusativ-pdf-89", action="store_true")
    route_import = sub.add_parser("import-herder-index")
    route_import.add_argument("reference", type=Path)
    route_import.add_argument("--bundle", type=Path)
    route_import.add_argument("--canonical-json", type=Path)
    route_import.add_argument("--source-id")
    sub.add_parser("canonical-route")
    route_mappings = sub.add_parser("canonical-route-mappings")
    route_mappings.add_argument(
        "--status", choices=("exact", "probable", "ambiguous", "unmatched", "rejected")
    )
    route_reconcile = sub.add_parser("canonical-route-reconcile")
    route_reconcile.add_argument(
        "--operation-id", default="canonical-route-reconcile-v1"
    )
    return command


def components():
    settings = get_settings()
    service = EducationalLibraryService(settings)
    embedding_model = settings.educational_library_embedding_model.strip()
    embedding = (
        LMStudioEmbeddingProvider(settings.lm_studio_base_url, embedding_model)
        if embedding_model
        else None
    )
    search = EducationalSearchService(service.database, embedding)
    provider = LMStudioProvider(settings.lm_studio_base_url, timeout=300)
    knowledge = EducationalKnowledgeService(
        service.database,
        search,
        provider,
        default_model=settings.lm_studio_model,
    )
    return service, search, knowledge


async def runtime_summary(service: EducationalLibraryService) -> dict[str, object]:
    settings = get_settings()
    provider = LMStudioProvider(settings.lm_studio_base_url, timeout=5)
    try:
        installed = [item.name for item in await provider.list_models()]
    except (ProviderUnavailableError, ProviderResponseError):
        installed = []
    return service.summary(
        lm_studio_available=bool(installed), installed_models=installed
    ).model_dump(mode="json")


async def run(args: argparse.Namespace) -> object:
    service, search, knowledge = components()
    if args.command == "status":
        return await runtime_summary(service)
    if args.command == "scan":
        return service.scan(process_documents=not args.metadata_only).model_dump(
            mode="json"
        )
    if args.command == "search":
        response = await search.search(args.query, mode=args.mode, limit=args.limit)
        payload = response.model_dump(mode="json")
        for result in payload["results"]:
            result.pop("text", None)
        return payload
    if args.command == "ask":
        settings = get_settings()
        provider = LMStudioProvider(
            settings.lm_studio_base_url,
            timeout=settings.educational_library_teacher_timeout_seconds,
        )
        router = LibraryModelRouter(
            provider,
            ModelRoutingPolicy(
                planner=settings.educational_library_planner_model,
                embedding=settings.educational_library_embedding_model,
                teacher=settings.educational_library_teacher_model,
                fallback=settings.educational_library_fallback_model,
                vision=settings.educational_library_vision_model,
                repair=settings.educational_library_repair_model,
                planner_timeout=settings.educational_library_planner_timeout_seconds,
                teacher_timeout=settings.educational_library_teacher_timeout_seconds,
            ),
        )
        teacher = EducationalTeacherService(
            service.database,
            search,
            provider,
            default_model=settings.lm_studio_model,
            model_router=router,
        )
        return (
            await teacher.ask(
                TeacherAskRequest(question=args.question),
                learner=LearnerContext(),
            )
        ).model_dump(mode="json")
    if args.command == "derive":
        return (
            await knowledge.generate_knowledge(
                KnowledgeGenerationRequest(query=args.query, model=args.model)
            )
        ).model_dump(mode="json")
    if args.command == "generate":
        return (
            await knowledge.grounded_generate(
                GroundedGenerationRequest(
                    query=args.query,
                    level=args.level,
                    objective=args.objective,
                    model=args.model,
                )
            )
        ).model_dump(mode="json")
    if args.command == "integrity":
        quick, foreign = service.database_integrity()
        return {"quick_check": quick, "foreign_key_check": foreign}
    if args.command == "core":
        return (
            LibraryEditorialService(service.database)
            .core_pair()
            .model_dump(mode="json")
        )
    if args.command == "assign-core":
        editorial = LibraryEditorialService(service.database)
        candidates = [item for item in editorial.candidates() if item.unambiguous]
        by_role = {
            item.suggested_role.value: item
            for item in candidates
            if item.suggested_role
        }
        theory = by_role.get("core_theory")
        workbook = by_role.get("core_workbook")
        if not theory or not workbook:
            raise RuntimeError("No existe una pareja Herder inequívoca para confirmar.")
        editorial.assign_core(
            theory.source.id,
            CoreSourceAssignmentRequest(
                operation_id="herder-core-theory-v1",
                pedagogical_role="core_theory",
                display_alias="Herder · Gramática alemana para hispanohablantes",
                canonical_title="Gramática alemana para hispanohablantes",
                related_source_id=workbook.source.id,
                editorial_notes="Fuente nuclear teórica confirmada por selección del usuario.",
            ),
        )
        editorial.assign_core(
            workbook.source.id,
            CoreSourceAssignmentRequest(
                operation_id="herder-core-workbook-v1",
                pedagogical_role="core_workbook",
                display_alias="Herder · Ejercicios y soluciones",
                canonical_title="Gramática alemana: ejercicios y soluciones",
                related_source_id=theory.source.id,
                editorial_notes="Cuaderno nuclear confirmado por selección del usuario.",
            ),
        )
        return editorial.core_pair().model_dump(mode="json")
    if args.command == "sections":
        editorial = LibraryEditorialService(service.database)
        result = (
            editorial.build_section_index(args.source_id)
            if args.build
            else editorial.list_sections(args.source_id)
        )
        return [item.model_dump(mode="json") for item in result]
    if args.command == "page-quality":
        result = DocumentIntelligenceService(
            service.database, get_settings()
        ).analyze_source(args.source_id)
        return [item.model_dump(mode="json") for item in result]
    if args.command == "reprocess-page":
        intelligence = DocumentIntelligenceService(service.database, get_settings())
        result = (
            await intelligence.reprocess_vision(args.source_id, args.page_number)
            if args.method == "vision"
            else intelligence.reprocess_pdftotext(args.source_id, args.page_number)
        )
        return result.model_dump(mode="json")
    if args.command == "index-semantic":
        count = await search.index_embeddings(
            batch_size=args.batch_size,
            limit=args.limit,
            core_first=not args.supplementary_first,
        )
        return {
            "indexed_now": count,
            "summary": await runtime_summary(service),
        }
    if args.command == "memory-status":
        return (
            PedagogicalMemoryService(service.database).summary().model_dump(mode="json")
        )
    if args.command == "memory-import":
        return (
            PedagogicalMemoryService(service.database)
            .import_existing(
                PedagogicalMemoryImportRequest(
                    operation_id=args.operation_id,
                    confirm_herder_akkusativ_pdf_89=args.confirm_herder_akkusativ_pdf_89,
                )
            )
            .model_dump(mode="json")
        )
    if args.command == "import-herder-index":
        settings = get_settings()
        route = CanonicalRouteService(service.database)
        if args.bundle and args.canonical_json:
            raise RuntimeError("Usa --bundle o --canonical-json, no ambos.")
        if args.canonical_json:
            pages = load_canonical_index(args.canonical_json.expanduser())
            model = "editorial-transcription+user-verified"
            parser_version = "herder-canonical-corrected.v1"
            visual_seconds = None
        elif args.bundle:
            pages = load_visual_bundle(args.bundle.expanduser())
            model = "qwen3-vl:8b+selective-visual-review"
            parser_version = "herder-reference-index.v1+curated-bundle.v1"
            visual_seconds = None
        else:
            extractor = HerderIndexVisualExtractor(
                settings.lm_studio_base_url,
                model=settings.educational_library_vision_model,
            )
            pages = await extractor.extract(args.reference.expanduser())
            model = extractor.model
            parser_version = extractor.parser_version
            visual_seconds = extractor.total_seconds
        return route.import_reference(
            args.reference.expanduser(),
            pages,
            model=model,
            parser_version=parser_version,
            source_id=args.source_id,
            visual_seconds=visual_seconds,
        ).model_dump(mode="json")
    if args.command == "canonical-route":
        route = CanonicalRouteService(service.database)
        payload = route.status().model_dump(mode="json")
        payload["topics"] = [
            topic.model_dump(mode="json") for topic in route.list_topics()
        ]
        return payload
    if args.command == "canonical-route-mappings":
        return [
            item.model_dump(mode="json")
            for item in CanonicalRouteService(service.database).mappings(args.status)
        ]
    if args.command == "canonical-route-reconcile":
        return [
            item.model_dump(mode="json")
            for item in CanonicalRouteService(service.database).reconcile_legacy(
                args.operation_id
            )
        ]
    raise AssertionError(args.command)


def main() -> int:
    args = parser().parse_args()
    try:
        payload = asyncio.run(run(args))
    except Exception as exc:
        print(
            json.dumps(
                {"error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False
            )
        )
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
