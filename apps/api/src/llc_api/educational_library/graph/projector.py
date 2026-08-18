from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

from llc_api.educational_library.cloud_knowledge.models import (
    AssertionOrigin,
    CanonicalContent,
    CanonicalEvidence,
)

from .models import GraphEdge, GraphifyConfidence, GraphNode, GraphProjection


class GraphProjectionError(ValueError):
    """Canonical input cannot be projected without ambiguity or data loss."""


def project_canonical_to_graph(
    canonical: CanonicalContent,
    *,
    item_ids: Collection[str] | None = None,
) -> GraphProjection:
    """Project canonical document knowledge into a deterministic Graphify shape.

    LLC canonical IDs are used verbatim as Graphify IDs. Selecting ``item_ids``
    keeps only relationships whose two endpoints are selected.
    """
    items_by_id = _unique_by_id(canonical.items, record_type="knowledge item")
    _unique_by_id(canonical.relations, record_type="knowledge relation")
    for relation in canonical.relations:
        if relation.source_item_id not in items_by_id or relation.target_item_id not in items_by_id:
            raise GraphProjectionError(
                f"Canonical relation {relation.id!r} references an unknown item"
            )

    selected_ids = set(items_by_id) if item_ids is None else set(item_ids)
    missing_ids = selected_ids - set(items_by_id)
    if missing_ids:
        missing = ", ".join(sorted(missing_ids))
        raise GraphProjectionError(f"Unknown canonical item IDs: {missing}")

    provenance = canonical.provenance.model_dump(mode="json")
    provenance["pages"] = sorted(provenance["pages"])
    source_file = _source_file(
        canonical.provenance.source_id,
        canonical.provenance.source_version_id,
    )
    nodes = [
        GraphNode(
            id=item.id,
            label=item.normalized_label or item.raw_visible_text,
            file_type=item.kind,
            source_file=source_file,
            llc_canonical_id=item.id,
            llc_schema_version=canonical.schema_version,
            llc_origin=item.extractor_status,
            llc_review_status=item.review_status,
            llc_raw_visible_text=item.raw_visible_text,
            llc_normalized_label=item.normalized_label,
            llc_content=item.content,
            llc_provenance=provenance,
            llc_evidence=_sorted_evidence(item.evidence),
        )
        for item in sorted(
            (items_by_id[item_id] for item_id in selected_ids),
            key=lambda value: value.id,
        )
    ]

    relations = [
        relation
        for relation in canonical.relations
        if relation.source_item_id in selected_ids and relation.target_item_id in selected_ids
    ]
    edges = [
        GraphEdge(
            id=relation.id,
            source=relation.source_item_id,
            target=relation.target_item_id,
            relation=relation.kind,
            confidence=_graphify_confidence(relation.extractor_status),
            source_file=source_file,
            llc_canonical_id=relation.id,
            llc_schema_version=canonical.schema_version,
            llc_origin=relation.extractor_status,
            llc_review_status=relation.review_status,
            llc_provenance=provenance,
            llc_evidence=_sorted_evidence(relation.evidence),
        )
        for relation in sorted(relations, key=lambda value: value.id)
    ]
    return GraphProjection(nodes=nodes, edges=edges)


def _unique_by_id(records: Iterable[Any], *, record_type: str) -> dict[str, Any]:
    by_id: dict[str, Any] = {}
    for record in records:
        if record.id in by_id:
            raise GraphProjectionError(f"Duplicate canonical {record_type} ID: {record.id}")
        by_id[record.id] = record
    return by_id


def _graphify_confidence(origin: AssertionOrigin) -> GraphifyConfidence:
    # Review status is intentionally not folded into Graphify confidence.
    return "EXTRACTED" if origin == AssertionOrigin.EXTRACTED else "INFERRED"


def _source_file(source_id: str, source_version_id: int) -> str:
    return f"llc-source/{source_id}/version/{source_version_id}"


def _sorted_evidence(evidence: Iterable[CanonicalEvidence]) -> list[dict[str, Any]]:
    records = [item.model_dump(mode="json") for item in evidence]
    return sorted(
        records,
        key=lambda item: (
            item["source_id"],
            item["source_version_id"],
            item["physical_pdf_page"],
            item["printed_page"] or "",
            item["document_node_id"] or "",
            item["excerpt"],
        ),
    )
