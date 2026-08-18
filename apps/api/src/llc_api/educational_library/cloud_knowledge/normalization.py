from __future__ import annotations

import re
from datetime import UTC, datetime

from .models import (
    CanonicalContent,
    CanonicalDocumentNode,
    CanonicalEvidence,
    CanonicalKnowledgeItem,
    CanonicalKnowledgeRelation,
    CanonicalProvenance,
    CanonicalStructure,
    CloudExtractionMode,
    CompactContentTransport,
    CompactEvidence,
    CompactStructureTransport,
)

TOPIC_NUMBER = re.compile(r"\bTema\s+(\d+)\.?\b", re.IGNORECASE)


def provenance(
    *,
    run_id: str,
    provider: str,
    model: str,
    mode: CloudExtractionMode,
    prompt_version: str,
    transport_version: str,
    source_id: str,
    source_version_id: int,
    pages: list[int],
    extracted_at: datetime | None = None,
) -> CanonicalProvenance:
    return CanonicalProvenance(
        run_id=run_id,
        provider=provider,
        model=model,
        extraction_mode=mode,
        prompt_version=prompt_version,
        transport_version=transport_version,
        source_id=source_id,
        source_version_id=source_version_id,
        pages=pages,
        extracted_at=extracted_at or datetime.now(UTC),
    )


def expand_structure(
    transport: CompactStructureTransport,
    metadata: CanonicalProvenance,
) -> CanonicalStructure:
    ids = {entry.o: f"node:{metadata.run_id}:{entry.o}" for entry in transport.e}
    nodes: list[CanonicalDocumentNode] = []
    for entry in transport.e:
        match = TOPIC_NUMBER.search(" ".join(value for value in (entry.n, entry.x, entry.t) if value))
        nodes.append(
            CanonicalDocumentNode(
                id=ids[entry.o],
                kind=entry.k,
                physical_pdf_page=entry.p,
                display_order=entry.o,
                raw_visible_text=entry.x,
                visible_number=entry.n,
                normalized_label=entry.t,
                printed_page=entry.pp,
                parent_id=(
                    ids.get(entry.po, f"missing-order:{entry.po}")
                    if entry.po is not None
                    else None
                ),
                depth=entry.d,
                topic_number=int(match.group(1)) if match else None,
                review_marker=entry.r,
            )
        )
    return CanonicalStructure(
        provenance=metadata,
        nodes=nodes,
        justified_missing_pages=sorted(set(transport.m)),
    )


def _evidence(
    item: CompactEvidence,
    metadata: CanonicalProvenance,
    node_ids: dict[int, str],
) -> CanonicalEvidence:
    return CanonicalEvidence(
        source_id=metadata.source_id,
        source_version_id=metadata.source_version_id,
        physical_pdf_page=item.p,
        printed_page=item.pp,
        document_node_id=node_ids.get(item.no) if item.no is not None else None,
        excerpt=item.q,
    )


def expand_content(
    transport: CompactContentTransport,
    metadata: CanonicalProvenance,
    *,
    document_node_ids: dict[int, str] | None = None,
) -> CanonicalContent:
    node_ids = document_node_ids or {}
    item_ids = {item.o: f"item:{metadata.run_id}:{item.o}" for item in transport.i}
    items = [
        CanonicalKnowledgeItem(
            id=item_ids[item.o],
            kind=item.k,
            raw_visible_text=item.x,
            normalized_label=item.nl,
            content=item.b,
            extractor_status=item.s,
            evidence=[_evidence(evidence, metadata, node_ids) for evidence in item.e],
        )
        for item in transport.i
    ]
    relations = [
        CanonicalKnowledgeRelation(
            id=f"relation:{metadata.run_id}:{relation.o}",
            source_item_id=item_ids.get(relation.f, f"missing:{relation.f}"),
            target_item_id=item_ids.get(relation.t, f"missing:{relation.t}"),
            kind=relation.k,
            extractor_status=relation.s,
            evidence=[_evidence(evidence, metadata, node_ids) for evidence in relation.e],
        )
        for relation in transport.r
    ]
    return CanonicalContent(provenance=metadata, items=items, relations=relations)
