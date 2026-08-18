from __future__ import annotations

import builtins
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from llc_api.core.config import Settings
from llc_api.educational_library.cloud_knowledge.models import (
    AssertionOrigin,
    CanonicalContent,
    CanonicalEvidence,
    CanonicalKnowledgeItem,
    CanonicalKnowledgeRelation,
    CanonicalProvenance,
    CloudExtractionMode,
)
from llc_api.educational_library.graph.graphify_adapter import (
    GraphifyCompatibilityError,
    GraphifyDisabledError,
    GraphifyUnavailableError,
    build_graphify_graph,
)
from llc_api.educational_library.graph.projector import (
    GraphProjectionError,
    project_canonical_to_graph,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def canonical_content() -> CanonicalContent:
    provenance = CanonicalProvenance(
        run_id="herder-run-7",
        provider="fixture",
        model="fixture-model",
        extraction_mode=CloudExtractionMode.CONTENT,
        prompt_version="library-content.v1",
        transport_version="compact-content.v1",
        source_id="herder-theory",
        source_version_id=42,
        pages=[13, 12],
        extracted_at=datetime(2026, 8, 18, 10, 30, tzinfo=UTC),
    )
    page_12 = CanonicalEvidence(
        source_id="herder-theory",
        source_version_id=42,
        physical_pdf_page=12,
        printed_page="10",
        document_node_id="node:herder-run-7:3",
        excerpt="Der Akkusativ steht nach bestimmten Verben.",
    )
    page_13 = CanonicalEvidence(
        source_id="herder-theory",
        source_version_id=42,
        physical_pdf_page=13,
        printed_page="11",
        document_node_id="node:herder-run-7:4",
        excerpt="Ich sehe den Mann.",
    )
    items = [
        CanonicalKnowledgeItem(
            id="item:herder-run-7:1",
            kind="concept",
            raw_visible_text="Akkusativ",
            normalized_label="Akkusativ",
            content="Kasus für das direkte Objekt.",
            extractor_status=AssertionOrigin.EXTRACTED,
            review_status="verified",
            evidence=[page_13, page_12],
        ),
        CanonicalKnowledgeItem(
            id="item:herder-run-7:2",
            kind="grammatical_rule",
            raw_visible_text="Bestimmte Verben regieren den Akkusativ.",
            normalized_label="Akkusativ-Verben",
            content=None,
            extractor_status=AssertionOrigin.INFERRED,
            review_status="unverified",
            evidence=[page_12],
        ),
        CanonicalKnowledgeItem(
            id="item:herder-run-7:3",
            kind="example",
            raw_visible_text="Ich sehe den Mann.",
            normalized_label=None,
            content=None,
            extractor_status=AssertionOrigin.EXTRACTED,
            review_status="unverified",
            evidence=[page_13],
        ),
        CanonicalKnowledgeItem(
            id="item:herder-run-7:4",
            kind="concept",
            raw_visible_text="Dativ",
            normalized_label="Dativ",
            content=None,
            extractor_status=AssertionOrigin.EXTRACTED,
            review_status="rejected",
            evidence=[page_13],
        ),
    ]
    relations = [
        CanonicalKnowledgeRelation(
            id="relation:herder-run-7:1",
            source_item_id=items[0].id,
            target_item_id=items[1].id,
            kind="requires",
            extractor_status=AssertionOrigin.INFERRED,
            review_status="unverified",
            evidence=[page_12],
        ),
        CanonicalKnowledgeRelation(
            id="relation:herder-run-7:2",
            source_item_id=items[2].id,
            target_item_id=items[0].id,
            kind="example_of",
            extractor_status=AssertionOrigin.EXTRACTED,
            review_status="verified",
            evidence=[page_13],
        ),
        CanonicalKnowledgeRelation(
            id="relation:herder-run-7:3",
            source_item_id=items[3].id,
            target_item_id=items[0].id,
            kind="contrasts_with",
            extractor_status=AssertionOrigin.EXTRACTED,
            review_status="rejected",
            evidence=[page_13],
        ),
    ]
    return CanonicalContent(provenance=provenance, items=items, relations=relations)


def test_projection_is_deterministic_and_read_only(canonical_content: CanonicalContent):
    before = canonical_content.model_dump(mode="json")
    first = project_canonical_to_graph(canonical_content)
    reversed_evidence_item = canonical_content.items[0].model_copy(
        update={"evidence": list(reversed(canonical_content.items[0].evidence))}
    )
    shuffled = canonical_content.model_copy(
        update={
            "items": [*reversed(canonical_content.items[1:]), reversed_evidence_item],
            "relations": list(reversed(canonical_content.relations)),
        },
        deep=True,
    )
    second = project_canonical_to_graph(shuffled)

    assert first == second
    assert [node.id for node in first.nodes] == sorted(node.id for node in first.nodes)
    assert [edge.id for edge in first.edges] == sorted(edge.id for edge in first.edges)
    assert canonical_content.model_dump(mode="json") == before


def test_ids_relations_confidence_and_provenance_survive(canonical_content: CanonicalContent):
    projection = project_canonical_to_graph(canonical_content)
    nodes = {node.id: node for node in projection.nodes}
    edges = {edge.id: edge for edge in projection.edges}

    akk = nodes["item:herder-run-7:1"]
    assert akk.id == akk.llc_canonical_id == "item:herder-run-7:1"
    assert akk.llc_origin == "extracted"
    assert akk.llc_review_status == "verified"
    assert akk.llc_provenance["run_id"] == "herder-run-7"
    assert akk.llc_provenance["source_version_id"] == 42
    assert akk.llc_provenance["pages"] == [12, 13]
    assert akk.llc_evidence[0] == {
        "source_id": "herder-theory",
        "source_version_id": 42,
        "physical_pdf_page": 12,
        "printed_page": "10",
        "document_node_id": "node:herder-run-7:3",
        "excerpt": "Der Akkusativ steht nach bestimmten Verben.",
    }

    requires = edges["relation:herder-run-7:1"]
    assert requires.source == "item:herder-run-7:1"
    assert requires.target == "item:herder-run-7:2"
    assert requires.relation == "requires"
    assert requires.confidence == "INFERRED"
    assert requires.llc_origin == "inferred"
    assert requires.llc_review_status == "unverified"
    assert edges["relation:herder-run-7:2"].confidence == "EXTRACTED"
    assert edges["relation:herder-run-7:3"].llc_review_status == "rejected"


def test_subset_keeps_only_internal_edges(canonical_content: CanonicalContent):
    selected = {"item:herder-run-7:1", "item:herder-run-7:2"}
    projection = project_canonical_to_graph(canonical_content, item_ids=selected)

    assert {node.id for node in projection.nodes} == selected
    assert [edge.id for edge in projection.edges] == ["relation:herder-run-7:1"]


def test_projection_rejects_unknown_duplicate_and_dangling_ids(
    canonical_content: CanonicalContent,
):
    with pytest.raises(GraphProjectionError, match="Unknown canonical item IDs"):
        project_canonical_to_graph(canonical_content, item_ids={"missing"})

    duplicate = canonical_content.model_copy(
        update={"items": [*canonical_content.items, canonical_content.items[0]]}
    )
    with pytest.raises(GraphProjectionError, match="Duplicate canonical knowledge item ID"):
        project_canonical_to_graph(duplicate)

    dangling_relation = canonical_content.relations[0].model_copy(
        update={"target_item_id": "item:missing"}
    )
    dangling = canonical_content.model_copy(update={"relations": [dangling_relation]})
    with pytest.raises(GraphProjectionError, match="references an unknown item"):
        project_canonical_to_graph(dangling)


def test_graphify_is_disabled_by_default_and_import_is_lazy(
    canonical_content: CanonicalContent, monkeypatch: pytest.MonkeyPatch
):
    projection = project_canonical_to_graph(canonical_content)
    assert Settings(_env_file=None).graph_enabled is False
    monkeypatch.setenv("LLC_GRAPH_ENABLED", "true")
    assert Settings(_env_file=None).graph_enabled is True
    monkeypatch.delenv("LLC_GRAPH_ENABLED")
    with pytest.raises(GraphifyDisabledError):
        build_graphify_graph(projection, enabled=False)

    original_import = builtins.__import__

    def without_graphify(name, *args, **kwargs):
        if name == "graphify.build":
            raise ImportError("fixture: package unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_graphify)
    with pytest.raises(GraphifyUnavailableError):
        build_graphify_graph(projection, enabled=True)


def test_adapter_rejects_parallel_relations_before_graphify(
    canonical_content: CanonicalContent,
):
    duplicate_pair = canonical_content.relations[0].model_copy(
        update={"id": "relation:herder-run-7:parallel", "kind": "uses"}
    )
    canonical = canonical_content.model_copy(
        update={"relations": [*canonical_content.relations, duplicate_pair]}
    )
    projection = project_canonical_to_graph(canonical)

    with pytest.raises(GraphifyCompatibilityError, match="parallel LLC relationships"):
        build_graphify_graph(projection, enabled=True)


def test_real_graphify_build_preserves_ids_and_supports_focused_traversals(
    canonical_content: CanonicalContent,
):
    pytest.importorskip("graphify")
    projection = project_canonical_to_graph(canonical_content)
    before = projection.model_dump(mode="json")
    graph = build_graphify_graph(projection, enabled=True)

    assert graph.is_directed()
    assert set(graph.nodes) == {item.id for item in canonical_content.items}
    assert graph["item:herder-run-7:1"]["item:herder-run-7:2"]["relation"] == "requires"
    assert graph["item:herder-run-7:3"]["item:herder-run-7:1"]["relation"] == "example_of"
    assert graph["item:herder-run-7:4"]["item:herder-run-7:1"]["relation"] == "contrasts_with"
    assert graph.nodes["item:herder-run-7:1"]["llc_evidence"][0]["printed_page"] == "10"
    assert graph["item:herder-run-7:4"]["item:herder-run-7:1"]["llc_review_status"] == "rejected"
    assert projection.model_dump(mode="json") == before


def test_fastapi_import_does_not_require_graphify():
    code = """
import importlib.abc
import sys

class RejectGraphify(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "graphify" or fullname.startswith("graphify."):
            raise ImportError("Graphify deliberately unavailable")
        return None

sys.meta_path.insert(0, RejectGraphify())
from llc_api.main import app
assert app is not None
"""
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


def test_projection_cli_writes_reproducible_graphify_artifact(
    canonical_content: CanonicalContent, tmp_path: Path
):
    canonical_path = tmp_path / "canonical.json"
    output_path = tmp_path / "graphify-out" / "graph.json"
    canonical_path.write_text(canonical_content.model_dump_json(indent=2), encoding="utf-8")
    command = [
        sys.executable,
        str(ROOT / "scripts" / "project-graphify.py"),
        str(canonical_path),
        "--output",
        str(output_path),
        "--item-id",
        "item:herder-run-7:1",
        "--item-id",
        "item:herder-run-7:2",
    ]

    first = subprocess.run(command, check=True, capture_output=True, text=True)
    first_bytes = output_path.read_bytes()
    second = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert first_bytes == output_path.read_bytes()
    assert json.loads(first.stdout)["nodes"] == 2
    assert json.loads(second.stdout)["edges"] == 1
    assert [node["id"] for node in payload["nodes"]] == [
        "item:herder-run-7:1",
        "item:herder-run-7:2",
    ]
    assert payload["edges"][0]["relation"] == "requires"
