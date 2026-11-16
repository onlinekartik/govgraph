"""Records that adapters emit, and the nodes and edges that stores return."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from govgraph.ids import KeyValue, edge_id, node_id


@dataclass(frozen=True)
class NodeRef:
    """A pointer to a node by type and natural key."""

    type: str
    key: Mapping[str, KeyValue]

    @property
    def id(self) -> str:
        return node_id(self.type, self.key)


@dataclass(frozen=True)
class NodeRecord:
    """A node to write. ``key`` holds the natural-key properties, ``properties`` the rest."""

    type: str
    key: Mapping[str, KeyValue]
    properties: Mapping[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return node_id(self.type, self.key)

    def ref(self) -> NodeRef:
        return NodeRef(self.type, self.key)


@dataclass(frozen=True)
class EdgeRecord:
    """An edge to write between two nodes."""

    type: str
    source: NodeRef
    target: NodeRef
    properties: Mapping[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return edge_id(self.type, self.source.id, self.target.id)


Record = NodeRecord | EdgeRecord


def _plain(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


@dataclass(frozen=True)
class Node:
    """A stored node. ``properties`` includes the natural-key properties."""

    id: str
    type: str
    properties: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "properties": {k: _plain(v) for k, v in sorted(self.properties.items())},
        }


@dataclass(frozen=True)
class Edge:
    """A stored edge."""

    id: str
    type: str
    source_id: str
    target_id: str
    properties: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "properties": {k: _plain(v) for k, v in sorted(self.properties.items())},
        }
