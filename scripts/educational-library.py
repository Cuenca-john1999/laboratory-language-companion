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

from deutschos_api.core.config import get_settings  # noqa: E402
from deutschos_api.educational_library.knowledge import (  # noqa: E402
    EducationalKnowledgeService,
)
from deutschos_api.educational_library.schemas import (  # noqa: E402
    GroundedGenerationRequest,
    KnowledgeGenerationRequest,
)
from deutschos_api.educational_library.search import (  # noqa: E402
    EducationalSearchService,
    OllamaEmbeddingProvider,
)
from deutschos_api.educational_library.service import (  # noqa: E402
    EducationalLibraryService,
)
from deutschos_api.providers.ollama import OllamaProvider  # noqa: E402


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
    return command


def components():
    settings = get_settings()
    service = EducationalLibraryService(settings)
    embedding_model = settings.educational_library_embedding_model.strip()
    embedding = (
        OllamaEmbeddingProvider(settings.ollama_base_url, embedding_model)
        if embedding_model
        else None
    )
    search = EducationalSearchService(service.database, embedding)
    provider = OllamaProvider(settings.ollama_base_url, timeout=300)
    knowledge = EducationalKnowledgeService(
        service.database,
        search,
        provider,
        default_model=settings.ollama_model,
    )
    return service, search, knowledge


async def run(args: argparse.Namespace) -> object:
    service, search, knowledge = components()
    if args.command == "status":
        return service.summary().model_dump(mode="json")
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
