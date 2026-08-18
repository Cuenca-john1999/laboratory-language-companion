from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from llc_api.schemas.base import APIModel

GraphifyConfidence = Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"]


class GraphNode(APIModel):
    """Graphify node plus lossless LLC projection metadata."""

    id: str
    label: str
    file_type: str
    source_file: str
    llc_canonical_id: str
    llc_schema_version: str
    llc_origin: Literal["extracted", "inferred"]
    llc_review_status: Literal["unverified", "verified", "rejected"]
    llc_raw_visible_text: str
    llc_normalized_label: str | None = None
    llc_content: str | None = None
    llc_provenance: dict[str, JsonValue]
    llc_evidence: list[dict[str, JsonValue]] = Field(default_factory=list)


class GraphEdge(APIModel):
    """Graphify edge whose canonical relationship identity remains explicit."""

    id: str
    source: str
    target: str
    relation: str
    confidence: GraphifyConfidence
    source_file: str
    llc_canonical_id: str
    llc_schema_version: str
    llc_origin: Literal["extracted", "inferred"]
    llc_review_status: Literal["unverified", "verified", "rejected"]
    llc_provenance: dict[str, JsonValue]
    llc_evidence: list[dict[str, JsonValue]] = Field(default_factory=list)


class GraphProjection(APIModel):
    """Deterministic derived graph; never an LLC source of truth."""

    schema_version: Literal["llc.graph-projection.v1"] = "llc.graph-projection.v1"
    directed: Literal[True] = True
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    def to_graphify_dict(self) -> dict[str, list[dict[str, JsonValue]]]:
        """Return Graphify's extraction-dict boundary without projection headers."""
        return {
            "nodes": [node.model_dump(mode="json") for node in self.nodes],
            "edges": [edge.model_dump(mode="json") for edge in self.edges],
            "hyperedges": [],
        }
