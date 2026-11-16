"""Check records against the ontology and convert them to stored form."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from govgraph.errors import OntologyError, RecordError
from govgraph.records import Edge, EdgeRecord, Node, NodeRecord
from govgraph.spec.model import Ontology, Property


def _parse_datetime(value: str) -> datetime:
    # datetime.fromisoformat only accepts a trailing "Z" from Python 3.11.
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    return datetime.fromisoformat(text)


def coerce(prop: Property, value: Any) -> Any:
    """Return ``value`` in the canonical Python type for ``prop``, or raise ``ValueError``."""
    kind = prop.type
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError("expected a string")
        if prop.enum and value not in prop.enum:
            raise ValueError(f"expected one of {', '.join(prop.enum)}")
        return value
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError("expected a boolean")
        return value
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("expected an integer")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("expected a number")
        return float(value)
    if kind == "date":
        if isinstance(value, datetime):
            raise ValueError("expected a date, not a date and time")
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return date.fromisoformat(value)
        raise ValueError("expected a date or an ISO 8601 date string")
    # datetime: stored as timezone-aware UTC; naive values are taken to be UTC.
    if isinstance(value, str):
        value = _parse_datetime(value)
    if not isinstance(value, datetime):
        raise ValueError("expected a datetime or an ISO 8601 string")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _coerce_properties(
    label: str,
    declared: Mapping[str, Property],
    given: Mapping[str, Any],
    problems: list[str],
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, value in given.items():
        prop = declared.get(name)
        if prop is None:
            problems.append(f"{label}: unknown property '{name}'")
            continue
        if value is None:
            continue
        try:
            values[name] = coerce(prop, value)
        except ValueError as exc:
            problems.append(f"{label}: property '{name}': {exc}")
    for name, prop in declared.items():
        if prop.required and name not in values:
            problems.append(f"{label}: required property '{name}' is missing")
    return values


def node_problems(ontology: Ontology, record: NodeRecord) -> tuple[Node | None, list[str]]:
    """Return the stored form of ``record``, or the reasons it is invalid."""
    label = f"node {record.type}"
    try:
        node_type = ontology.node_type(record.type)
    except OntologyError as exc:
        return None, [f"{label}: {exc}"]
    problems: list[str] = []
    if set(record.key) != set(node_type.key):
        problems.append(
            f"{label}: key must have exactly the fields {', '.join(node_type.key)}, "
            f"got {', '.join(sorted(record.key)) or 'none'}"
        )
    for name in record.key:
        if name in record.properties and record.properties[name] != record.key[name]:
            problems.append(f"{label}: property '{name}' disagrees with the key")
    values = _coerce_properties(
        label, node_type.properties, {**record.properties, **record.key}, problems
    )
    if problems:
        return None, problems
    try:
        return Node(id=record.id, type=record.type, properties=values), []
    except RecordError as exc:
        return None, [f"{label}: {exc}"]


def edge_problems(ontology: Ontology, record: EdgeRecord) -> tuple[Edge | None, list[str]]:
    """Return the stored form of ``record``, or the reasons it is invalid."""
    label = f"edge {record.type}"
    try:
        edge_type = ontology.edge_type(record.type)
    except OntologyError as exc:
        return None, [f"{label}: {exc}"]
    problems: list[str] = []
    if not edge_type.allows(record.source.type, record.target.type):
        problems.append(
            f"{label}: {record.source.type} -> {record.target.type} is not an allowed endpoint pair"
        )
    for role, ref in (("source", record.source), ("target", record.target)):
        node_type = ontology.node_types.get(ref.type)
        if node_type is not None and set(ref.key) != set(node_type.key):
            problems.append(
                f"{label}: {role} key must have exactly the fields {', '.join(node_type.key)}"
            )
    values = _coerce_properties(label, edge_type.properties, record.properties, problems)
    if problems:
        return None, problems
    try:
        return (
            Edge(
                id=record.id,
                type=record.type,
                source_id=record.source.id,
                target_id=record.target.id,
                properties=values,
            ),
            [],
        )
    except RecordError as exc:
        return None, [f"{label}: {exc}"]


def to_node(ontology: Ontology, record: NodeRecord) -> Node:
    node, problems = node_problems(ontology, record)
    if node is None:
        raise RecordError("; ".join(problems), problems)
    return node


def to_edge(ontology: Ontology, record: EdgeRecord) -> Edge:
    edge, problems = edge_problems(ontology, record)
    if edge is None:
        raise RecordError("; ".join(problems), problems)
    return edge
