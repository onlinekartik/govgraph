"""Immutable in-memory model of an ontology.

Everything else in govgraph (tables, graph-engine mappings, validation, tools) is derived
from these objects, so they are the single source of truth for node and edge types.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from govgraph.errors import OntologyError

PropertyType = Literal["string", "integer", "number", "boolean", "date", "datetime"]
Propagation = Literal["source_to_target", "target_to_source", "none"]

PROPERTY_TYPES: tuple[PropertyType, ...] = (
    "string",
    "integer",
    "number",
    "boolean",
    "date",
    "datetime",
)
KEY_PROPERTY_TYPES: tuple[PropertyType, ...] = ("string", "integer")

# Column names that every node or edge table uses for itself.
RESERVED_PROPERTY_NAMES = frozenset({"id", "source_id", "source_type", "target_id", "target_type"})

_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def snake_case(name: str) -> str:
    """Convert a PascalCase or UPPER_SNAKE type name to snake_case."""
    return _WORD_BOUNDARY.sub("_", name).lower()


@dataclass(frozen=True)
class Property:
    """A typed property of a node or edge type."""

    name: str
    type: PropertyType
    required: bool = False
    enum: tuple[str, ...] = ()
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.required:
            out["required"] = True
        if self.enum:
            out["enum"] = list(self.enum)
        if self.description:
            out["description"] = self.description
        return out


@dataclass(frozen=True)
class NodeType:
    """A node type.

    ``key`` names the properties that form the natural key. ``display`` names the
    property to show people; without one, the key is shown.
    """

    name: str
    key: tuple[str, ...]
    properties: Mapping[str, Property]
    display: str | None = None
    description: str = ""

    @property
    def table(self) -> str:
        return f"node_{snake_case(self.name)}"

    def label(self, properties: Mapping[str, Any]) -> str:
        """A short human-readable name for a node with these properties."""
        if self.display is not None and properties.get(self.display) is not None:
            return str(properties[self.display])
        return "/".join(str(properties.get(field, "?")) for field in self.key)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"description": self.description, "key": list(self.key)}
        if self.display is not None:
            out["display"] = self.display
        out["properties"] = {name: prop.to_dict() for name, prop in self.properties.items()}
        return out


@dataclass(frozen=True)
class Endpoint:
    """One allowed (source node type, target node type) pair of an edge type."""

    source: str
    target: str


@dataclass(frozen=True)
class EdgeType:
    """An edge type.

    ``phrase`` is the verb used to state the edge in a sentence. ``propagates`` says in
    which direction a problem at one end affects the other end.
    """

    name: str
    endpoints: tuple[Endpoint, ...]
    properties: Mapping[str, Property]
    phrase: str
    propagates: Propagation = "none"
    description: str = ""

    @property
    def table(self) -> str:
        return f"edge_{snake_case(self.name)}"

    def allows(self, source: str, target: str) -> bool:
        return Endpoint(source, target) in self.endpoints

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "phrase": self.phrase,
            "propagates": self.propagates,
            "endpoints": [{"source": e.source, "target": e.target} for e in self.endpoints],
            "properties": {name: prop.to_dict() for name, prop in self.properties.items()},
        }


@dataclass(frozen=True)
class Ontology:
    """A validated set of node and edge types."""

    name: str
    version: str
    node_types: Mapping[str, NodeType]
    edge_types: Mapping[str, EdgeType]
    extensions: tuple[tuple[str, str], ...] = ()

    def node_type(self, name: str) -> NodeType:
        try:
            return self.node_types[name]
        except KeyError:
            raise OntologyError(f"unknown node type {name!r}") from None

    def edge_type(self, name: str) -> EdgeType:
        try:
            return self.edge_types[name]
        except KeyError:
            raise OntologyError(f"unknown edge type {name!r}") from None

    def edge_types_from(self, node_type: str) -> tuple[EdgeType, ...]:
        """Edge types that may start at ``node_type``."""
        return tuple(
            edge
            for edge in self.edge_types.values()
            if any(e.source == node_type for e in edge.endpoints)
        )

    def edge_types_to(self, node_type: str) -> tuple[EdgeType, ...]:
        """Edge types that may end at ``node_type``."""
        return tuple(
            edge
            for edge in self.edge_types.values()
            if any(e.target == node_type for e in edge.endpoints)
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the merged ontology as one document that :func:`load_ontology` accepts."""
        return {
            "ontology": {"name": self.name, "version": self.version},
            "node_types": {name: nt.to_dict() for name, nt in self.node_types.items()},
            "edge_types": {name: et.to_dict() for name, et in self.edge_types.items()},
        }
