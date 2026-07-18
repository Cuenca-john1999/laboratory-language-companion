#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api" / "src"
sys.path.insert(0, str(API_SRC))

from deutschos_api.core.config import get_settings  # noqa: E402
from deutschos_api.educational_library.cache import LibraryCache  # noqa: E402
from deutschos_api.educational_library.routing import (  # noqa: E402
    LibraryModelRouter,
    ModelRoutingPolicy,
)
from deutschos_api.educational_library.schemas import TeacherAskRequest  # noqa: E402
from deutschos_api.educational_library.search import (  # noqa: E402
    EducationalSearchService,
    OllamaEmbeddingProvider,
)
from deutschos_api.educational_library.service import (  # noqa: E402
    EducationalLibraryService,
)
from deutschos_api.educational_library.teacher import (  # noqa: E402
    EducationalTeacherService,
    LearnerContext,
)
from deutschos_api.providers.ollama import OllamaProvider  # noqa: E402

QUERIES = (
    "¿Qué significa die?",
    "¿Cuál es la diferencia entre der, die y das?",
    "¿Cuál es la diferencia entre kein y nicht?",
    "¿Por qué se dice den Hund?",
    "Explícame los pronombres personales.",
    "Explícame Ich hätte gerne.",
    "¿Cómo funciona el Konjunktiv II?",
    "Explícame la declinación de los adjetivos.",
    "¿En qué sección de Herder aparece el acusativo?",
    "Explícame los verbos marcianos.",
)
MODELS = ("qwen3:14b", "qwen3.5:27b")


def _ollama_processes() -> str:
    result = subprocess.run(
        ["ollama", "ps"],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


async def benchmark(output: Path) -> dict[str, object]:
    settings = get_settings()
    service = EducationalLibraryService(settings)
    embedding = OllamaEmbeddingProvider(
        settings.ollama_base_url,
        settings.educational_library_embedding_model,
        timeout=settings.educational_library_embedding_timeout_seconds,
    )
    cache = LibraryCache(
        service.database,
        ttl_seconds=settings.educational_library_cache_ttl_seconds,
    )
    search = EducationalSearchService(service.database, embedding, cache=cache)
    provider = OllamaProvider(
        settings.ollama_base_url,
        timeout=max(300, settings.educational_library_teacher_timeout_seconds),
        keep_alive="5m",
    )
    installed = [item.name for item in await provider.list_models()]
    missing = sorted(
        {*MODELS, settings.educational_library_planner_model} - set(installed)
    )
    if missing:
        raise RuntimeError("Faltan modelos locales: " + ", ".join(missing))

    report: dict[str, object] = {
        "benchmark_version": "library-model-benchmark.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "queries": list(QUERIES),
        "installed_models": installed,
        "planner_model": settings.educational_library_planner_model,
        "embedding_model": settings.educational_library_embedding_model,
        "runs": [],
    }
    runs: list[dict[str, object]] = []
    for model in MODELS:
        router = LibraryModelRouter(
            provider,
            ModelRoutingPolicy(
                planner=settings.educational_library_planner_model,
                embedding=settings.educational_library_embedding_model,
                teacher=model,
                fallback=model,
                vision=settings.educational_library_vision_model,
                repair=settings.educational_library_repair_model,
                planner_timeout=settings.educational_library_planner_timeout_seconds,
                teacher_timeout=max(
                    300, settings.educational_library_teacher_timeout_seconds
                ),
            ),
        )
        teacher = EducationalTeacherService(
            service.database,
            search,
            provider,
            default_model=model,
            model_router=router,
            cache=cache,
        )
        for position, question in enumerate(QUERIES, start=1):
            provider.last_request_metrics = {}
            started = perf_counter()
            result = await teacher.ask(
                TeacherAskRequest(question=question),
                learner=LearnerContext(),
            )
            elapsed_ms = round((perf_counter() - started) * 1_000)
            run = {
                "model_under_test": model,
                "query_number": position,
                "question": question,
                "status": result.status.value,
                "failure_reason": result.failure_reason.value
                if result.failure_reason
                else None,
                "models": result.models,
                "timings": result.timings.model_dump(mode="json"),
                "wall_ms": elapsed_ms,
                "ollama_metrics": dict(provider.last_request_metrics),
                "confidence": result.confidence.value,
                "answer": result.answer.model_dump(mode="json"),
                "source_count": len(result.sources),
                "core_sources": sum(
                    source.evidence_origin == "core" for source in result.sources
                ),
                "supplementary_sources": sum(
                    source.evidence_origin == "supplementary"
                    for source in result.sources
                ),
                "citations": [source.citation for source in result.sources],
                "ollama_ps": _ollama_processes(),
            }
            runs.append(run)
            print(
                json.dumps(
                    {
                        "model": model,
                        "query": position,
                        "status": run["status"],
                        "wall_ms": elapsed_ms,
                        "teacher": result.models.get("teacher"),
                        "sources": len(result.sources),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    report["runs"] = runs
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    command = argparse.ArgumentParser(
        description="Compara modelos docentes locales sobre las diez consultas de #007L2."
    )
    command.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "var"
        / "educational-library"
        / "benchmarks"
        / "007L2-model-comparison.json",
    )
    args = command.parse_args()
    try:
        report = asyncio.run(benchmark(args.output))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "output": str(args.output),
                "runs": len(report["runs"]),
                "models": list(MODELS),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
