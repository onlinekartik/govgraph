"""Export a bounded neighbourhood of the graph as JSON, for the viewer or anything else."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from govgraph.store.base import GraphReader
from govgraph.tools.context import BudgetExhaustedError, Step, ToolContext, walk
from govgraph.tools.limits import MAX_ROWS_CEILING, Limits

EXPORT_MAX_DEPTH = 5
DEFAULT_EXPORT_LIMITS = Limits(max_rows=MAX_ROWS_CEILING, timeout_s=10.0)


def export_json(
    reader: GraphReader,
    root_ids: Sequence[str],
    *,
    depth: int = 2,
    limits: Limits | None = None,
) -> dict[str, Any]:
    """Return the nodes and edges within ``depth`` steps of ``root_ids``, in any direction.

    The export runs under the same kind of limits as the tools. ``truncated`` in the
    result says whether a limit cut it short.
    """
    if not 0 <= depth <= EXPORT_MAX_DEPTH:
        raise ValueError(f"depth must be between 0 and {EXPORT_MAX_DEPTH}")
    limits = limits or DEFAULT_EXPORT_LIMITS
    ctx = ToolContext(reader, limits, time.monotonic)
    ontology = reader.ontology
    steps = [
        Step(edge_type, direction)
        for edge_type in ontology.edge_types
        for direction in ("out", "in")
    ]
    truncated_by = None
    try:
        roots = [ctx.require(root_id).id for root_id in root_ids]
        for _ in walk(ctx, roots, steps, depth):
            pass
    except BudgetExhaustedError as exhausted:
        truncated_by = exhausted.reason
    result = ctx.result.build("export", limits.to_dict(), 0.0, truncated_by)
    return {
        "ontology": {"name": ontology.name, "version": ontology.version},
        "roots": [root_id for root_id in root_ids if root_id in ctx.result.nodes],
        "depth": depth,
        "truncated": result.truncated,
        "nodes": [
            {**node.to_dict(), "label": ontology.node_type(node.type).label(node.properties)}
            for node in result.nodes
        ],
        "edges": [
            {
                "id": edge.id,
                "type": edge.type,
                "source": edge.source_id,
                "target": edge.target_id,
                "label": ontology.edge_type(edge.type).phrase,
                "properties": edge.to_dict()["properties"],
            }
            for edge in result.edges
        ],
    }
