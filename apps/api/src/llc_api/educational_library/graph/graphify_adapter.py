from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import GraphProjection


class GraphifyDisabledError(RuntimeError):
    """The optional Graphify integration was called while disabled."""


class GraphifyUnavailableError(RuntimeError):
    """Graphify was enabled but the optional package is unavailable."""


class GraphifyCompatibilityError(RuntimeError):
    """Graphify changed or cannot preserve the projected graph contract."""


def graphify_available() -> bool:
    """Return availability without importing Graphify during normal LLC startup."""
    from importlib.util import find_spec

    return find_spec("graphify") is not None


def build_graphify_graph(projection: GraphProjection, *, enabled: bool) -> Any:
    """Build Graphify's directed NetworkX graph and verify lossless identity.

    Import is deliberately lazy. Graphify currently uses a simple ``DiGraph``,
    so parallel relationships would overwrite one another and are rejected.
    """
    if not enabled:
        raise GraphifyDisabledError("Graphify is disabled; set LLC_GRAPH_ENABLED=true to use it")
    _reject_parallel_edges(projection)
    try:
        from graphify.build import build_from_json
    except ImportError as exc:
        raise GraphifyUnavailableError(
            "Graphify is not installed; install the apps/api 'graph' extra"
        ) from exc

    # Graphify normalizes input dictionaries in place. Keep our derived artifact
    # stable and, more importantly, never expose canonical models to that mutation.
    graph = build_from_json(deepcopy(projection.to_graphify_dict()), directed=True)
    expected_node_ids = {node.id for node in projection.nodes}
    actual_node_ids = set(graph.nodes)
    if actual_node_ids != expected_node_ids:
        raise GraphifyCompatibilityError(
            "Graphify changed canonical node identity: "
            f"expected {sorted(expected_node_ids)}, got {sorted(actual_node_ids)}"
        )

    expected_edge_ids = {edge.id for edge in projection.edges}
    actual_edge_ids = {data.get("llc_canonical_id") for _, _, data in graph.edges(data=True)}
    if actual_edge_ids != expected_edge_ids:
        rendered_actual_ids = sorted(
            "<missing>" if value is None else str(value) for value in actual_edge_ids
        )
        raise GraphifyCompatibilityError(
            "Graphify changed or dropped canonical relation identity: "
            f"expected {sorted(expected_edge_ids)}, got {rendered_actual_ids}"
        )
    return graph


def _reject_parallel_edges(projection: GraphProjection) -> None:
    endpoint_pairs: set[tuple[str, str]] = set()
    for edge in projection.edges:
        pair = (edge.source, edge.target)
        if pair in endpoint_pairs:
            raise GraphifyCompatibilityError(
                "Graphify's directed graph cannot preserve parallel LLC relationships "
                f"between {edge.source!r} and {edge.target!r}"
            )
        endpoint_pairs.add(pair)
