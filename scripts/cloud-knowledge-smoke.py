#!/usr/bin/env python3
"""Explicit, opt-in live smoke test for cloud knowledge extraction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from llc_api.core.config import get_settings  # noqa: E402
from llc_api.educational_library.cloud_knowledge.models import (  # noqa: E402
    CloudExtractionCreate,
    CloudExtractionMode,
)
from llc_api.educational_library.cloud_knowledge.service import (  # noqa: E402
    CloudKnowledgeExtractionService,
)
from llc_api.educational_library.dependencies import (  # noqa: E402
    get_cloud_provider_registry,
)
from llc_api.educational_library.service import EducationalLibraryService  # noqa: E402

CLI_PROVIDER_ALIASES = {"google": "google_gemini"}


def parse_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start < 1 or end < start:
                raise argparse.ArgumentTypeError("invalid page range")
            pages.update(range(start, end + 1))
        else:
            page = int(token)
            if page < 1:
                raise argparse.ArgumentTypeError("pages are one-based")
            pages.add(page)
    return sorted(pages)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one explicit live Gemini extraction against a registered source version."
    )
    parser.add_argument("--live", action="store_true", help="required safety acknowledgement")
    parser.add_argument("--source-version-id", type=int, required=True)
    parser.add_argument("--mode", choices=[item.value for item in CloudExtractionMode], required=True)
    parser.add_argument("--provider", default="google")
    parser.add_argument("--model")
    parser.add_argument("--pages", type=parse_pages, default=[])
    return parser.parse_args()


def configured_registry(settings):
    """Use the application's canonical cloud-provider composition point."""
    return get_cloud_provider_registry(settings)


def canonical_provider_id(registry, requested: str) -> str:
    canonical = CLI_PROVIDER_ALIASES.get(requested, requested)
    installed = {descriptor.provider_id for descriptor in registry.descriptors()}
    return canonical if canonical in installed else requested


def main() -> int:
    args = arguments()
    if not args.live:
        print("Refusing network access: pass --live for an explicit cloud call.", file=sys.stderr)
        return 2
    settings = get_settings()
    if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value().strip():
        print("GEMINI_API_KEY is not configured; no cloud call was made.", file=sys.stderr)
        return 2
    library = EducationalLibraryService(settings, recover_interrupted=False)
    registry = configured_registry(settings)
    service = CloudKnowledgeExtractionService(
        library.database,
        library.root,
        library.runtime,
        settings,
        registry,
    )
    run = service.create_run(
        CloudExtractionCreate(
            source_version_id=args.source_version_id,
            mode=args.mode,
            provider=canonical_provider_id(registry, args.provider),
            model=args.model,
            pages=args.pages,
            initiated_by="explicit-live-smoke",
            reason="manual cloud knowledge smoke test",
        )
    )
    result = service.execute(run.id)
    print(
        json.dumps(
            {
                "run_id": result.id,
                "state": result.state,
                "provider_status": result.provider_status,
                "validation_outcome": result.validation_outcome,
                "usage": result.usage.model_dump(mode="json"),
                "artifacts": [item.model_dump(mode="json") for item in result.artifacts],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0 if result.state in {"completed", "completed_with_issues"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
