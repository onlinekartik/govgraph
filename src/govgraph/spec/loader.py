"""Load, merge and validate ontology documents."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import yaml
from jsonschema import Draft202012Validator

from govgraph.errors import OntologyError
from govgraph.spec.model import (
    KEY_PROPERTY_TYPES,
    RESERVED_PROPERTY_NAMES,
    EdgeType,
    Endpoint,
    NodeType,
    Ontology,
    Propagation,
    Property,
    PropertyType,
)

OntologySource = str | os.PathLike[str] | Mapping[str, Any]

CORE_ONTOLOGY_FILE = "core-1.0.yaml"
# PostgreSQL truncates identifiers at 63 characters; this leaves room for index suffixes.
_MAX_TABLE_NAME_LENGTH = 50


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    text = resources.files("govgraph.spec").joinpath("ontology.schema.json").read_text("utf-8")
    return Draft202012Validator(json.loads(text))


def _read(source: OntologySource) -> tuple[str, Any]:
    """Return (label, parsed document) for a path or an already-parsed mapping."""
    if isinstance(source, Mapping):
        return "<mapping>", dict(source)
    path = Path(source)
    try:
        text = path.read_text("utf-8")
    except OSError as exc:
        raise OntologyError(f"cannot read ontology file {path}: {exc.strerror}") from exc
    try:
        return str(path), yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OntologyError(f"{path} is not valid YAML: {exc}") from exc


def _read_core() -> tuple[str, Any]:
    text = (
        resources.files("govgraph.spec")
        .joinpath("ontologies")
        .joinpath(CORE_ONTOLOGY_FILE)
        .read_text("utf-8")
    )
    return CORE_ONTOLOGY_FILE, yaml.safe_load(text)


_NAME_RULES = {
    "node_types": (re.compile(r"[A-Z][A-Za-z0-9]*"), "node type"),
    "edge_types": (re.compile(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*"), "edge type"),
}
_PROPERTY_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")


def _name_problems(label: str, document: Any) -> list[str]:
    """Check every name in full.

    The JSON schema checks names too, but a regular expression search lets a trailing
    newline through, and a YAML key such as ``on`` is not a string at all. Names become
    table and column names, so nothing is left to chance here.
    """
    problems: list[str] = []
    if not isinstance(document, Mapping):
        return problems
    for section, (pattern, kind) in _NAME_RULES.items():
        types = document.get(section)
        for name, definition in types.items() if isinstance(types, Mapping) else ():
            if not isinstance(name, str) or not pattern.fullmatch(name):
                problems.append(f"{label}: {name!r} is not a valid {kind} name")
            properties = definition.get("properties") if isinstance(definition, Mapping) else None
            for prop in properties if isinstance(properties, Mapping) else ():
                if not isinstance(prop, str) or not _PROPERTY_NAME.fullmatch(prop):
                    problems.append(f"{label}: {name}: {prop!r} is not a valid property name")
    return problems


def _schema_problems(label: str, document: Any) -> list[str]:
    problems = _name_problems(label, document)
    if problems:
        return problems
    for error in sorted(_validator().iter_errors(document), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(part) for part in error.absolute_path) or "<root>"
        problems.append(f"{label}: {where}: {error.message}")
    return problems


def _merge_properties(
    label: str,
    owner: str,
    existing: dict[str, dict[str, Any]],
    incoming: Mapping[str, Mapping[str, Any]],
    problems: list[str],
) -> None:
    for name, definition in incoming.items():
        if name in existing and existing[name] != dict(definition):
            problems.append(f"{label}: {owner}.{name} is already defined differently")
        else:
            existing[name] = dict(definition)


def _merge_scalar(
    label: str,
    owner: str,
    field: str,
    existing: dict[str, Any],
    incoming: Mapping[str, Any],
    problems: list[str],
) -> None:
    if field not in incoming:
        return
    if field in existing and existing[field] != incoming[field]:
        problems.append(f"{label}: {owner} cannot change '{field}' of an existing type")
    else:
        existing[field] = incoming[field]


def _merge(documents: list[tuple[str, Any]], problems: list[str]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    for label, document in documents:
        for name, definition in (document.get("node_types") or {}).items():
            node = nodes.setdefault(name, {"properties": {}})
            _merge_scalar(label, name, "key", node, definition, problems)
            _merge_scalar(label, name, "display", node, definition, problems)
            node.setdefault("description", definition.get("description", ""))
            _merge_properties(
                label, name, node["properties"], definition.get("properties") or {}, problems
            )
        for name, definition in (document.get("edge_types") or {}).items():
            edge = edges.setdefault(name, {"properties": {}, "endpoints": []})
            _merge_scalar(label, name, "phrase", edge, definition, problems)
            _merge_scalar(label, name, "propagates", edge, definition, problems)
            edge.setdefault("description", definition.get("description", ""))
            for endpoint in definition.get("endpoints") or []:
                pair = (endpoint["source"], endpoint["target"])
                if pair not in edge["endpoints"]:
                    edge["endpoints"].append(pair)
            _merge_properties(
                label, name, edge["properties"], definition.get("properties") or {}, problems
            )
    return {"node_types": nodes, "edge_types": edges}


def _build_properties(
    owner: str, raw: Mapping[str, Mapping[str, Any]], problems: list[str]
) -> Mapping[str, Property]:
    properties: dict[str, Property] = {}
    for name, definition in raw.items():
        if name in RESERVED_PROPERTY_NAMES:
            problems.append(f"{owner}.{name}: '{name}' is a reserved property name")
        enum = tuple(definition.get("enum", ()))
        if enum and definition["type"] != "string":
            problems.append(f"{owner}.{name}: 'enum' is only allowed on string properties")
        properties[name] = Property(
            name=name,
            type=cast(PropertyType, definition["type"]),
            required=bool(definition.get("required", False)),
            enum=enum,
            description=definition.get("description", ""),
        )
    return MappingProxyType(properties)


def _build_node_type(name: str, raw: Mapping[str, Any], problems: list[str]) -> NodeType:
    properties = _build_properties(name, raw["properties"], problems)
    key = tuple(raw.get("key", ()))
    if not key:
        problems.append(f"{name}: a node type needs a 'key'")
    for field in key:
        prop = properties.get(field)
        if prop is None:
            problems.append(f"{name}: key field '{field}' is not a declared property")
        elif not prop.required:
            problems.append(f"{name}: key field '{field}' must be required")
        elif prop.type not in KEY_PROPERTY_TYPES:
            problems.append(f"{name}: key field '{field}' must be a string or an integer")
    display = raw.get("display") or ("name" if "name" in properties else None)
    if display is not None and display not in properties:
        problems.append(f"{name}: display property '{display}' is not a declared property")
    return NodeType(
        name=name,
        key=key,
        properties=properties,
        display=display,
        description=raw.get("description", ""),
    )


def _build_edge_type(
    name: str, raw: Mapping[str, Any], node_names: Iterable[str], problems: list[str]
) -> EdgeType:
    known = set(node_names)
    endpoints = tuple(Endpoint(source, target) for source, target in raw["endpoints"])
    if not endpoints:
        problems.append(f"{name}: an edge type needs at least one endpoint")
    for endpoint in endpoints:
        for role, node_name in (("source", endpoint.source), ("target", endpoint.target)):
            if node_name not in known:
                problems.append(
                    f"{name}: dangling edge type, {role} node type '{node_name}' is not defined"
                )
    return EdgeType(
        name=name,
        endpoints=endpoints,
        properties=_build_properties(name, raw["properties"], problems),
        phrase=raw.get("phrase") or name.lower().replace("_", " "),
        propagates=cast(Propagation, raw.get("propagates", "none")),
        description=raw.get("description", ""),
    )


def _table_problems(node_types: Iterable[NodeType], edge_types: Iterable[EdgeType]) -> list[str]:
    problems = []
    seen: dict[str, str] = {}
    named: list[tuple[str, str]] = [(n.name, n.table) for n in node_types]
    named += [(e.name, e.table) for e in edge_types]
    for type_name, table in named:
        if len(table) > _MAX_TABLE_NAME_LENGTH:
            problems.append(
                f"{type_name}: table name '{table}' is longer than "
                f"{_MAX_TABLE_NAME_LENGTH} characters"
            )
        if table in seen:
            problems.append(f"{type_name}: table name '{table}' collides with {seen[table]}")
        seen[table] = type_name
    return problems


def build_ontology(documents: list[tuple[str, Any]]) -> Ontology:
    """Validate and merge parsed documents. The first document is the base."""
    if not documents:
        raise OntologyError("no ontology documents were given")
    problems: list[str] = []
    for label, document in documents:
        problems.extend(_schema_problems(label, document))
    if problems:
        raise OntologyError(_summary(problems), problems)

    merged = _merge(documents, problems)
    node_types = {
        name: _build_node_type(name, raw, problems) for name, raw in merged["node_types"].items()
    }
    edge_types = {
        name: _build_edge_type(name, raw, node_types, problems)
        for name, raw in merged["edge_types"].items()
    }
    problems.extend(_table_problems(node_types.values(), edge_types.values()))
    if problems:
        raise OntologyError(_summary(problems), problems)

    base = documents[0][1]["ontology"]
    return Ontology(
        name=base["name"],
        version=base["version"],
        node_types=MappingProxyType(node_types),
        edge_types=MappingProxyType(edge_types),
        extensions=tuple(
            (doc["ontology"]["name"], doc["ontology"]["version"]) for _, doc in documents[1:]
        ),
    )


def _summary(problems: list[str]) -> str:
    head = "; ".join(problems[:5])
    more = f" (and {len(problems) - 5} more)" if len(problems) > 5 else ""
    return f"invalid ontology: {head}{more}"


def load_ontology(
    extensions: Iterable[OntologySource] = (), *, include_core: bool = True
) -> Ontology:
    """Load the bundled core ontology and merge ``extensions`` into it.

    Each extension is a path to a YAML file or an already-parsed mapping. An extension may
    add node types, edge types, properties and endpoints. It may not change what an
    existing type already declares. Raises :class:`OntologyError` listing every problem.
    """
    documents = [_read_core()] if include_core else []
    documents.extend(_read(source) for source in extensions)
    return build_ontology(documents)
