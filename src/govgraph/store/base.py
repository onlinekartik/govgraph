"""Interfaces that every graph store implements.

A :class:`GraphStore` can write. A :class:`GraphReader` cannot: it has no method that
changes data, and it is the only thing the agent tools are given.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from typing import Literal, Protocol, runtime_checkable

from govgraph.errors import OntologyError
from govgraph.records import Edge, EdgeRecord, Node, NodeRecord
from govgraph.spec.model import Ontology

Direction = Literal["out", "in"]
Match = Literal["exact", "contains"]

DEFAULT_TIMEOUT_S = 30.0


@runtime_checkable
class GraphReader(Protocol):
    """Read-only access to a graph. Results are ordered by ID so they are reproducible."""

    @property
    def ontology(self) -> Ontology: ...

    def get_nodes(self, node_ids: Sequence[str], *, timeout_s: float | None = None) -> list[Node]:
        """Return the nodes that exist among ``node_ids``."""
        ...

    def get_edges(
        self,
        node_ids: Sequence[str],
        edge_type: str,
        direction: Direction,
        *,
        limit: int,
        timeout_s: float | None = None,
    ) -> list[Edge]:
        """Return up to ``limit`` edges of ``edge_type`` that leave ("out") or enter ("in")
        any of ``node_ids``."""
        ...

    def find_nodes(
        self,
        node_type: str,
        prop: str,
        value: str,
        *,
        match: Match = "exact",
        limit: int,
        timeout_s: float | None = None,
    ) -> list[Node]:
        """Return up to ``limit`` nodes whose string property matches, ignoring case."""
        ...


class GraphStore(Protocol):
    """A graph that can be written to and that hands out read-only readers."""

    @property
    def ontology(self) -> Ontology: ...

    def create_schema(self) -> None:
        """Create or extend the storage structures for the ontology. Safe to repeat."""
        ...

    def upsert(
        self, nodes: Iterable[NodeRecord] = (), edges: Iterable[EdgeRecord] = ()
    ) -> tuple[int, int]:
        """Validate and write records, returning (nodes written, edges written).

        Writing is idempotent. A property that a record does not supply keeps the value
        it already has, so several sources can each contribute part of a node.
        """
        ...

    def existing_node_ids(self, node_ids: Collection[str]) -> set[str]:
        """Return the subset of ``node_ids`` that is present in the store."""
        ...

    def reader(self) -> GraphReader: ...


def check_edge_query(ontology: Ontology, edge_type: str, direction: str, limit: int) -> None:
    ontology.edge_type(edge_type)
    if direction not in ("out", "in"):
        raise ValueError("direction must be 'out' or 'in'")
    if limit < 1:
        raise ValueError("limit must be at least 1")


def check_find_query(ontology: Ontology, node_type: str, prop: str, match: str, limit: int) -> None:
    declared = ontology.node_type(node_type).properties.get(prop)
    if declared is None:
        raise OntologyError(f"{node_type} has no property {prop!r}")
    if declared.type != "string":
        raise OntologyError(f"{node_type}.{prop} is not a string property")
    if match not in ("exact", "contains"):
        raise ValueError("match must be 'exact' or 'contains'")
    if limit < 1:
        raise ValueError("limit must be at least 1")
