"""Budgeted, read-only access to the graph for the duration of one tool call."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TypeVar

from govgraph.errors import StoreTimeoutError, ToolError
from govgraph.ids import id_type, is_id
from govgraph.records import Edge, Node
from govgraph.spec.model import Ontology
from govgraph.store.base import Direction, GraphReader, Match
from govgraph.tools.limits import Limits
from govgraph.tools.results import ResultBuilder, Truncation

_Row = TypeVar("_Row", Node, Edge)


class BudgetExhaustedError(Exception):
    """Raised inside a tool when its row or time budget runs out."""

    def __init__(self, reason: Truncation) -> None:
        self.reason: Truncation = reason
        super().__init__(reason)


class ToolContext:
    """Wraps a reader so that every read is counted against the call's limits.

    Everything read is added to the result being built, which is what lets a call that
    runs out of budget still return the evidence it has gathered. A read that would go
    over budget keeps what fits and then raises, so a tool can never carry on, and draw
    conclusions, from data it knows to be incomplete.
    """

    def __init__(self, reader: GraphReader, limits: Limits, clock: Callable[[], float]) -> None:
        self.ontology: Ontology = reader.ontology
        self.result = ResultBuilder()
        self._reader = reader
        self._clock = clock
        self._deadline = clock() + limits.timeout_s
        self._rows_left = limits.max_rows
        self._seen: dict[str, Node | None] = {}

    def _time_left(self) -> float:
        left = self._deadline - self._clock()
        if left <= 0:
            raise BudgetExhaustedError("timeout")
        return left

    def _take(self, rows: list[_Row]) -> tuple[list[_Row], bool]:
        """Keep as many rows as the budget allows; also say whether some were dropped."""
        kept = rows[: self._rows_left]
        self._rows_left -= len(kept)
        return kept, len(kept) < len(rows)

    def known(self, node_id: str) -> Node | None:
        """A node this call has already read, without touching the store."""
        return self._seen.get(node_id)

    def nodes(self, node_ids: Iterable[str]) -> list[Node]:
        """Return the nodes that exist among ``node_ids``, in the order given."""
        wanted = list(dict.fromkeys(node_ids))
        unread = [node_id for node_id in wanted if node_id not in self._seen]
        if unread:
            timeout = self._time_left()
            # Never ask for more nodes than the budget has room for.
            affordable = unread[: self._rows_left]
            try:
                found = self._reader.get_nodes(affordable, timeout_s=timeout) if affordable else []
            except StoreTimeoutError:
                raise BudgetExhaustedError("timeout") from None
            self._rows_left -= len(found)
            self._seen.update(dict.fromkeys(affordable))
            self._seen.update({node.id: node for node in found})
            self.result.add_nodes(found)
            if len(affordable) < len(unread):
                raise BudgetExhaustedError("rows")
        return [node for node in (self._seen.get(node_id) for node_id in wanted) if node]

    def edges(self, node_ids: Sequence[str], edge_type: str, direction: Direction) -> list[Edge]:
        """Return the edges of one type that leave or enter ``node_ids``."""
        if not node_ids:
            return []
        timeout = self._time_left()
        try:
            rows = self._reader.get_edges(
                node_ids, edge_type, direction, limit=self._rows_left + 1, timeout_s=timeout
            )
        except StoreTimeoutError:
            raise BudgetExhaustedError("timeout") from None
        edges, dropped = self._take(rows)
        self.result.add_edges(edges)
        if dropped:
            raise BudgetExhaustedError("rows")
        return edges

    def find(self, node_type: str, prop: str, value: str, match: Match) -> list[Node]:
        """Return the nodes of one type whose text property matches ``value``."""
        timeout = self._time_left()
        try:
            rows = self._reader.find_nodes(
                node_type, prop, value, match=match, limit=self._rows_left + 1, timeout_s=timeout
            )
        except StoreTimeoutError:
            raise BudgetExhaustedError("timeout") from None
        nodes, dropped = self._take(rows)
        self._seen.update({node.id: node for node in nodes})
        self.result.add_nodes(nodes)
        if dropped:
            raise BudgetExhaustedError("rows")
        return nodes

    def require(self, node_id: str, node_type: str | None = None) -> Node:
        """Return the node with ``node_id`` or raise a :class:`ToolError`."""
        if not is_id(node_id) or id_type(node_id) not in self.ontology.node_types:
            raise ToolError("invalid_input", "the ID does not refer to a node type in the ontology")
        if node_type is not None and id_type(node_id) != node_type:
            raise ToolError("invalid_input", f"the ID must be the ID of a {node_type}")
        found = self.nodes([node_id])
        if not found:
            raise ToolError("not_found", f"no {id_type(node_id)} has that ID")
        return found[0]


@dataclass(frozen=True)
class Step:
    """One edge type to follow, and which way: "out" along the edge, "in" against it."""

    edge_type: str
    direction: Direction


@dataclass
class Walk:
    """The state of a breadth-first walk."""

    depth: dict[str, int] = field(default_factory=dict)
    via: dict[str, tuple[str, Edge]] = field(default_factory=dict)

    def path(self, node_id: str) -> list[str]:
        """IDs from a start node to ``node_id``, inclusive."""
        path = [node_id]
        while path[-1] in self.via:
            path.append(self.via[path[-1]][0])
        return path[::-1]

    def hops(self, node_id: str) -> list[tuple[str, Edge]]:
        """The (node reached, edge taken) pairs from a start node to ``node_id``."""
        path = self.path(node_id)
        return [(step, self.via[step][1]) for step in path[1:]]


def walk(
    ctx: ToolContext, start_ids: Sequence[str], steps: Sequence[Step], max_depth: int
) -> Iterator[tuple[Walk, list[Node]]]:
    """Walk outwards from ``start_ids`` one level at a time.

    Yields the walk state and the nodes first reached at each depth, nearest first, so a
    caller can record what it learns level by level and keep it if the budget runs out.
    """
    state = Walk(depth=dict.fromkeys(start_ids, 0))
    frontier = list(start_ids)
    for depth in range(1, max_depth + 1):
        candidates: dict[str, tuple[str, Edge]] = {}
        for step in steps:
            declared = ctx.ontology.edge_type(step.edge_type)
            near = {e.source if step.direction == "out" else e.target for e in declared.endpoints}
            sources = [node_id for node_id in frontier if id_type(node_id) in near]
            for edge in ctx.edges(sources, step.edge_type, step.direction):
                here, there = (
                    (edge.source_id, edge.target_id)
                    if step.direction == "out"
                    else (edge.target_id, edge.source_id)
                )
                if there not in state.depth:
                    candidates.setdefault(there, (here, edge))
        reached = ctx.nodes(candidates)
        for node in reached:
            state.depth[node.id] = depth
            state.via[node.id] = candidates[node.id]
        if not reached:
            return
        yield state, reached
        frontier = [node.id for node in reached]
