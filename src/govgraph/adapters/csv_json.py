"""Adapters for plain files: one JSON document, or a directory of CSV files.

JSON document::

    {"nodes": [{"type": "Owner", "key": {"identifier": "a"}, "properties": {"name": "A"}}],
     "edges": [{"type": "OWNED_BY",
                "source": {"type": "DataAsset", "key": {"qualified_name": "shop.orders"}},
                "target": {"type": "Owner", "key": {"identifier": "a"}}}]}

CSV directory: one file per type, named ``<Type>.csv``. A node file has one column per
property. An edge file has ``source.<key field>`` and ``target.<key field>`` columns, plus
``source_type`` and ``target_type`` when the edge type allows more than one pair of node
types, plus one column per edge property. Empty cells mean "not set".
"""

from __future__ import annotations

import csv
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from govgraph.adapters.base import read_json
from govgraph.errors import AdapterError
from govgraph.records import EdgeRecord, NodeRecord, NodeRef, Record
from govgraph.spec.loader import load_ontology
from govgraph.spec.model import EdgeType, NodeType, Ontology, Property

_TRUE, _FALSE = {"true", "t", "yes", "y", "1"}, {"false", "f", "no", "n", "0"}


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AdapterError(f"{where} must be an object")
    return value


def _ref(value: Any, where: str) -> NodeRef:
    ref = _mapping(value, where)
    if not isinstance(ref.get("type"), str):
        raise AdapterError(f"{where} needs a 'type'")
    return NodeRef(ref["type"], dict(_mapping(ref.get("key"), f"{where}.key")))


class JsonAdapter:
    """Reads nodes and edges from one JSON document."""

    name = "json"

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = path

    def records(self) -> Iterator[Record]:
        document = _mapping(read_json(self._path, "JSON file"), "the document")
        for index, raw in enumerate(document.get("nodes", [])):
            where = f"nodes[{index}]"
            node = _ref(raw, where)
            properties = _mapping(raw.get("properties", {}), f"{where}.properties")
            yield NodeRecord(node.type, node.key, dict(properties))
        for index, raw in enumerate(document.get("edges", [])):
            where = f"edges[{index}]"
            edge = _mapping(raw, where)
            if not isinstance(edge.get("type"), str):
                raise AdapterError(f"{where} needs a 'type'")
            yield EdgeRecord(
                edge["type"],
                _ref(edge.get("source"), f"{where}.source"),
                _ref(edge.get("target"), f"{where}.target"),
                dict(_mapping(edge.get("properties", {}), f"{where}.properties")),
            )


def _convert(prop: Property, text: str, where: str) -> Any:
    """Turn a CSV cell into the Python type the property declares."""
    try:
        if prop.type == "integer":
            return int(text)
        if prop.type == "number":
            return float(text)
    except ValueError:
        raise AdapterError(f"{where}: {text!r} is not a valid {prop.type}") from None
    if prop.type == "boolean":
        lowered = text.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise AdapterError(f"{where}: {text!r} is not a valid boolean")
    return text


class CsvAdapter:
    """Reads nodes and edges from a directory of ``<Type>.csv`` files."""

    name = "csv"

    def __init__(self, path: str | os.PathLike[str], ontology: Ontology | None = None) -> None:
        self._path = Path(path)
        self._ontology = ontology or load_ontology()

    def records(self) -> Iterator[Record]:
        if not self._path.is_dir():
            raise AdapterError(f"{self._path} is not a directory")
        files = sorted(self._path.glob("*.csv"))
        unknown = [
            file.name
            for file in files
            if file.stem not in self._ontology.node_types
            and file.stem not in self._ontology.edge_types
        ]
        if unknown:
            raise AdapterError(f"no node or edge type is named after: {', '.join(unknown)}")
        # Nodes first, so that edges in the same directory find their endpoints.
        for file in files:
            if file.stem in self._ontology.node_types:
                yield from self._nodes(file, self._ontology.node_types[file.stem])
        for file in files:
            if file.stem in self._ontology.edge_types:
                yield from self._edges(file, self._ontology.edge_types[file.stem])

    @staticmethod
    def _rows(file: Path) -> Iterator[tuple[str, dict[str, str]]]:
        with file.open(newline="", encoding="utf-8-sig") as handle:
            for number, row in enumerate(csv.DictReader(handle), start=2):
                cells = {k: v for k, v in row.items() if k is not None and v not in (None, "")}
                yield f"{file.name} line {number}", cells

    def _values(
        self, declared: Mapping[str, Property], cells: Mapping[str, str], where: str
    ) -> dict[str, Any]:
        values = {}
        for column, text in cells.items():
            prop = declared.get(column)
            if prop is None:
                raise AdapterError(f"{where}: unknown column '{column}'")
            values[column] = _convert(prop, text, f"{where}, column '{column}'")
        return values

    def _nodes(self, file: Path, node_type: NodeType) -> Iterator[Record]:
        for where, cells in self._rows(file):
            values = self._values(node_type.properties, cells, where)
            key = {name: values.pop(name) for name in node_type.key if name in values}
            yield NodeRecord(node_type.name, key, values)

    def _edges(self, file: Path, edge_type: EdgeType) -> Iterator[Record]:
        only = edge_type.endpoints[0] if len(edge_type.endpoints) == 1 else None
        for where, cells in self._rows(file):
            ends = []
            for role, default in (
                ("source", only and only.source),
                ("target", only and only.target),
            ):
                type_name = cells.pop(f"{role}_type", default)
                declared = self._ontology.node_types.get(type_name or "")
                if declared is None:
                    raise AdapterError(f"{where}: '{role}_type' must name a node type")
                prefix = f"{role}."
                raw = {c[len(prefix) :]: cells.pop(c) for c in list(cells) if c.startswith(prefix)}
                ends.append(NodeRef(declared.name, self._values(declared.properties, raw, where)))
            properties = self._values(edge_type.properties, cells, where)
            yield EdgeRecord(edge_type.name, ends[0], ends[1], properties)
