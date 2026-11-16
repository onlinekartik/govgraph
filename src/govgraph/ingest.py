"""The only path from adapters to a store."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from govgraph.adapters.base import Adapter
from govgraph.errors import IngestError
from govgraph.records import EdgeRecord, NodeRecord, Record
from govgraph.store.base import GraphStore
from govgraph.validation import edge_problems, node_problems


@dataclass(frozen=True)
class IngestReport:
    """What an ingest run wrote, and what it left out and why."""

    source: str
    nodes: int
    edges: int
    rejected: tuple[str, ...] = ()


def ingest(
    source: Adapter | Iterable[Record],
    store: GraphStore,
    *,
    on_error: Literal["raise", "skip"] = "raise",
) -> IngestReport:
    """Validate every record from ``source`` against the ontology, then write them.

    A record is rejected if it does not conform to the ontology, or if it is an edge whose
    source or target node is neither in this batch nor already in the store. With
    ``on_error="raise"`` (the default) any rejection raises :class:`IngestError` and
    nothing is written. With ``on_error="skip"`` the valid records are written and the
    rejections are listed in the report.
    """
    name = source.name if isinstance(source, Adapter) else "records"
    ontology = store.ontology
    nodes: dict[str, NodeRecord] = {}
    edges: list[EdgeRecord] = []
    rejected: list[str] = []
    for record in _objects(source):
        if isinstance(record, NodeRecord):
            node, problems = node_problems(ontology, record)
            if node is None:
                rejected.extend(problems)
            else:
                earlier = nodes.get(node.id)
                nodes[node.id] = record if earlier is None else _merge(earlier, record)
        elif isinstance(record, EdgeRecord):
            stored, problems = edge_problems(ontology, record)
            if stored is None:
                rejected.extend(problems)
            else:
                edges.append(record)
        else:
            rejected.append(f"not a record: {type(record).__name__}")

    endpoint_ids = {ref.id for edge in edges for ref in (edge.source, edge.target)}
    missing = endpoint_ids - nodes.keys()
    missing -= store.existing_node_ids(missing)
    kept: list[EdgeRecord] = []
    for edge in edges:
        absent = [
            f"{role} {ref.type} {dict(ref.key)}"
            for role, ref in (("source", edge.source), ("target", edge.target))
            if ref.id in missing
        ]
        if absent:
            rejected.append(f"edge {edge.type}: {' and '.join(absent)} not found")
        else:
            kept.append(edge)

    if rejected and on_error == "raise":
        shown = "; ".join(rejected[:5])
        more = f" (and {len(rejected) - 5} more)" if len(rejected) > 5 else ""
        raise IngestError(f"{name}: {len(rejected)} record(s) rejected: {shown}{more}", rejected)

    node_count, edge_count = store.upsert(nodes.values(), kept)
    return IngestReport(name, node_count, edge_count, tuple(rejected))


def _objects(source: Adapter | Iterable[Record]) -> Iterable[object]:
    # Typed as objects because an adapter is third-party code and may yield anything.
    return source.records() if isinstance(source, Adapter) else source


def _merge(first: NodeRecord, second: NodeRecord) -> NodeRecord:
    """Combine two records for the same node; values from the later record win."""
    properties = {**first.properties}
    properties.update({k: v for k, v in second.properties.items() if v is not None})
    return NodeRecord(first.type, first.key, properties)
