"""What a tool returns: claims, each backed by the nodes it cites."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from govgraph.records import Edge, Node

Truncation = Literal["rows", "timeout"]


@dataclass(frozen=True)
class Claim:
    """One statement about the graph, and the IDs of the nodes that support it."""

    statement: str
    citations: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.statement:
            raise ValueError("a claim needs a statement")
        if not self.citations:
            raise ValueError("a claim must cite at least one node")

    def to_dict(self) -> dict[str, Any]:
        return {"statement": self.statement, "citations": list(self.citations)}


@dataclass(frozen=True)
class ToolResult:
    """The outcome of one tool call.

    Every citation in ``claims`` is the ID of a node in ``nodes``. ``notes`` carry
    remarks that are not evidence, such as "nothing matched". ``truncated_by`` says which
    limit, if any, cut the call short; a truncated result is incomplete, never wrong.
    """

    tool: str
    claims: tuple[Claim, ...]
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    limits: Mapping[str, Any]
    elapsed_ms: float
    notes: tuple[str, ...] = ()
    truncated_by: Truncation | None = None

    def __post_init__(self) -> None:
        known = {node.id for node in self.nodes}
        for claim in self.claims:
            missing = [c for c in claim.citations if c not in known]
            if missing:
                raise ValueError(f"claim cites nodes that are not in the result: {missing}")

    @property
    def truncated(self) -> bool:
        return self.truncated_by is not None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable form of the result."""
        return {
            "tool": self.tool,
            "claims": [claim.to_dict() for claim in self.claims],
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "notes": list(self.notes),
            "truncated": self.truncated,
            "truncated_by": self.truncated_by,
            "limits": dict(self.limits),
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


@dataclass
class ResultBuilder:
    """Collects evidence while a tool runs, so a call that is cut short keeps what it has."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[str, Edge] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_nodes(self, nodes: list[Node]) -> None:
        for node in nodes:
            self.nodes.setdefault(node.id, node)

    def add_edges(self, edges: list[Edge]) -> None:
        for edge in edges:
            self.edges.setdefault(edge.id, edge)

    def claim(self, statement: str, *cited: Node) -> None:
        """Record a statement. The cited nodes become part of the result."""
        self.add_nodes(list(cited))
        claim = Claim(statement, tuple(dict.fromkeys(node.id for node in cited)))
        if claim not in self.claims:
            self.claims.append(claim)

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def build(
        self,
        tool: str,
        limits: Mapping[str, Any],
        elapsed_ms: float,
        truncated_by: Truncation | None,
    ) -> ToolResult:
        # An edge is only useful with both of its ends, which a truncated call may lack.
        edges = [
            edge
            for edge in self.edges.values()
            if edge.source_id in self.nodes and edge.target_id in self.nodes
        ]
        return ToolResult(
            tool=tool,
            claims=tuple(self.claims),
            nodes=tuple(self.nodes.values()),
            edges=tuple(edges),
            limits=dict(limits),
            elapsed_ms=elapsed_ms,
            notes=tuple(self.notes),
            truncated_by=truncated_by,
        )
