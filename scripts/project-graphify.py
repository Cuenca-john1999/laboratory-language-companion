#!/usr/bin/env python3
"""Write a disposable Graphify-compatible projection from LLC canonical JSON."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from llc_api.core.config import get_settings  # noqa: E402
from llc_api.educational_library.cloud_knowledge.models import CanonicalContent  # noqa: E402
from llc_api.educational_library.graph.graphify_adapter import (  # noqa: E402
    build_graphify_graph,
)
from llc_api.educational_library.graph.projector import (  # noqa: E402
    project_canonical_to_graph,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Project LLC canonical content into a derived Graphify-compatible artifact."
    )
    parser.add_argument(
        "canonical", type=Path, help="llc.cloud-content.v1 canonical JSON"
    )
    parser.add_argument("--item-id", action="append", dest="item_ids")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--validate-with-graphify",
        action="store_true",
        help="also build the graph; requires LLC_GRAPH_ENABLED=true and apps/api[graph]",
    )
    return parser.parse_args()


def main() -> int:
    args = arguments()
    canonical_path = args.canonical.expanduser().resolve()
    canonical = CanonicalContent.model_validate_json(
        canonical_path.read_text(encoding="utf-8")
    )
    projection = project_canonical_to_graph(canonical, item_ids=args.item_ids)
    settings = get_settings()
    output = args.output or (
        settings.educational_library_runtime_dir
        / "graphify-out"
        / f"{canonical.provenance.run_id}.projection.json"
    )
    output = output.expanduser().resolve()
    if output == canonical_path:
        raise ValueError("projection output cannot overwrite canonical input")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            projection.to_graphify_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        delete=False,
    ) as temporary:
        temporary.write(payload)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, output)

    if args.validate_with_graphify:
        build_graphify_graph(projection, enabled=settings.graph_enabled)
    print(
        json.dumps(
            {
                "output": str(output),
                "nodes": len(projection.nodes),
                "edges": len(projection.edges),
                "graphify_validated": args.validate_with_graphify,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
