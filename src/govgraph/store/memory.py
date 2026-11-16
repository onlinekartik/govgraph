"""In-memory store backed by NetworkX. Needs no server, so it is the default for trying
things out and for tests."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence

import networkx as nx

from govgraph.records import Edge, EdgeRecord, Node, NodeRecord
from govgraph.spec.model import Ontology
from govgraph.store.base import Direction, Match, check_edge_query, check_find_query
from govgraph.validation import to_edge, to_node


class MemoryReader:
    """Read-only view of a :class:`MemoryStore`."""

    def __init__(self, ontology: Ontology, graph: nx.MultiDiGraph[str]) -> None:
        self._ontology = ontology
        self._graph = graph

    @property
    def ontology(self) -> Ontology:
        return self._ontology

    def _node(self, node_id: str) -> Node | None:
        if node_id not in self._graph:
            return None
        node: Node | None = self._graph.nodes[node_id].get("node")
        return node

    def get_nodes(self, node_ids: Sequence[str], *, timeout_s: float | None = None) -> list[Node]:
        found = (self._node(node_id) for node_id in set(node_ids))
        return sorted((node for node in found if node is not None), key=lambda n: n.id)

    def get_edges(
        self,
        node_ids: Sequence[str],
        edge_type: str,
        direction: Direction,
        *,
        limit: int,
        timeout_s: float | None = None,
    ) -> list[Edge]:
        check_edge_query(self._ontology, edge_type, direction, limit)
        found: dict[str, Edge] = {}
        for node_id in set(node_ids):
            if node_id not in self._graph:
                continue
            incident = (
                self._graph.out_edges(node_id, data=True)
                if direction == "out"
                else self._graph.in_edges(node_id, data=True)
            )
            for _, _, data in incident:
                edge: Edge = data["edge"]
                if edge.type == edge_type:
                    found[edge.id] = edge
        return sorted(found.values(), key=lambda e: e.id)[:limit]

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
        check_find_query(self._ontology, node_type, prop, match, limit)
        needle = value.lower()
        found: list[Node] = []
        for _, data in self._graph.nodes(data=True):
            node: Node | None = data.get("node")
            if node is None or node.type != node_type:
                continue
            text = node.properties.get(prop)
            if text is None:
                continue
            haystack = text.lower()
            matched = haystack == needle if match == "exact" else needle in haystack
            if matched:
                found.append(node)
        return sorted(found, key=lambda n: n.id)[:limit]


class MemoryStore:
    """A graph held in memory."""

    def __init__(self, ontology: Ontology) -> None:
        self._ontology = ontology
        self._graph: nx.MultiDiGraph[str] = nx.MultiDiGraph()

    @property
    def ontology(self) -> Ontology:
        return self._ontology

    def create_schema(self) -> None:
        """Nothing to create for an in-memory graph."""

    def upsert(
        self, nodes: Iterable[NodeRecord] = (), edges: Iterable[EdgeRecord] = ()
    ) -> tuple[int, int]:
        # Validate everything first so that a bad record leaves the graph untouched.
        new_nodes = [to_node(self._ontology, record) for record in nodes]
        new_edges = [to_edge(self._ontology, record) for record in edges]
        for node in new_nodes:
            current: Node | None = (
                self._graph.nodes[node.id].get("node") if node.id in self._graph else None
            )
            merged = {**current.properties, **node.properties} if current else node.properties
            self._graph.add_node(node.id, node=Node(node.id, node.type, merged))
        for edge in new_edges:
            data = self._graph.get_edge_data(edge.source_id, edge.target_id, key=edge.id)
            merged = {**data["edge"].properties, **edge.properties} if data else edge.properties
            self._graph.add_edge(
                edge.source_id,
                edge.target_id,
                key=edge.id,
                edge=Edge(edge.id, edge.type, edge.source_id, edge.target_id, merged),
            )
        return len(new_nodes), len(new_edges)

    def existing_node_ids(self, node_ids: Collection[str]) -> set[str]:
        return {
            node_id
            for node_id in node_ids
            if node_id in self._graph and "node" in self._graph.nodes[node_id]
        }

    def reader(self) -> MemoryReader:
        return MemoryReader(self._ontology, self._graph)
