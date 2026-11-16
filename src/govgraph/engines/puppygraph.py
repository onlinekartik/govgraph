"""Generate a PuppyGraph schema from the ontology.

PuppyGraph reads the PostgreSQL tables in place, so Cypher and Gremlin queries run over
the same rows the store writes, with no copy. The schema is derived from the ontology in
the same way the tables are, which keeps the two in step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from govgraph.errors import OntologyError
from govgraph.spec.model import EdgeType, Endpoint, Ontology, Property, PropertyType
from govgraph.store.ddl import DEFAULT_SCHEMA, quote_ident

PUPPYGRAPH_TYPES: dict[PropertyType, str] = {
    "string": "STRING",
    "integer": "LONG",
    "number": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "DATETIME",
}

_ID_FIELD = {"name": "id", "type": "STRING"}
_PLAIN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class JdbcConnection:
    """How PuppyGraph should connect to PostgreSQL. Use a role that can only read."""

    uri: str
    username: str
    password: str = field(repr=False)


def _attributes(properties: list[Property]) -> list[dict[str, str]]:
    return [{"name": p.name, "type": PUPPYGRAPH_TYPES[p.type]} for p in properties]


def _source(
    catalog: str, schema: str, table: str, fields: list[str], where: str | None = None
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "enabled": True,
        "catalog": catalog,
        "schema": schema,
        "table": table,
        "mappedField": [{"sourceFieldName": f, "targetFieldName": f} for f in fields],
    }
    if where is not None:
        source["whereClause"] = where
    return {"externalDataSource": source}


def edge_label(edge_type: EdgeType, endpoint: Endpoint) -> str:
    """The PuppyGraph label for one endpoint pair of an edge type.

    PuppyGraph needs a label to have a single source and target node type. An edge type
    with one endpoint pair keeps its name. One with several gets a label per pair, and
    its own name becomes an alias that matches all of them.
    """
    if len(edge_type.endpoints) == 1:
        return edge_type.name
    return f"{edge_type.name}_{endpoint.source}_{endpoint.target}"


def puppygraph_schema(
    ontology: Ontology,
    *,
    catalog: str = "govgraph",
    schema: str = DEFAULT_SCHEMA,
    jdbc: JdbcConnection | None = None,
) -> dict[str, Any]:
    """Return a PuppyGraph schema document for the tables of ``ontology``.

    With ``jdbc`` the document also defines the catalog, and so contains the password;
    treat it as a secret. Without it, the catalog named ``catalog`` must already exist
    in PuppyGraph.
    """
    quote_ident(schema)
    # Type names become labels and are written into filter expressions, so insist on
    # plain names even for an ontology that was not built by the loader.
    names = [*ontology.node_types, *ontology.edge_types]
    for edge_type in ontology.edge_types.values():
        names += [name for e in edge_type.endpoints for name in (e.source, e.target)]
    for name in names:
        if not isinstance(name, str) or not _PLAIN_NAME.fullmatch(name):
            raise OntologyError(f"type name {name!r} cannot be used in a PuppyGraph schema")
    document: dict[str, Any] = {}
    if jdbc is not None:
        document["catalog"] = [
            {
                "name": catalog,
                "type": "postgresql",
                "jdbc": {
                    "username": jdbc.username,
                    "password": jdbc.password,
                    "jdbcUri": jdbc.uri,
                    "driverClass": "org.postgresql.Driver",
                },
            }
        ]

    document["node"] = [
        {
            "label": node_type.name,
            "dataSourceGroup": _source(
                catalog, schema, node_type.table, ["id", *node_type.properties]
            ),
            "id": [_ID_FIELD],
            # Listing the ID as an attribute too makes n.id return the ID the tools cite.
            "attribute": [_ID_FIELD, *_attributes(list(node_type.properties.values()))],
        }
        for node_type in ontology.node_types.values()
    ]

    edges = []
    for edge_type in ontology.edge_types.values():
        properties = list(edge_type.properties.values())
        fields = ["id", "source_id", "target_id", *edge_type.properties]
        for endpoint in edge_type.endpoints:
            label = edge_label(edge_type, endpoint)
            where = None
            entry: dict[str, Any] = {"label": label}
            if label != edge_type.name:
                entry["type"] = [edge_type.name]
                where = f"source_type = '{endpoint.source}' AND target_type = '{endpoint.target}'"
            entry.update(
                {
                    "fromNodeLabel": endpoint.source,
                    "toNodeLabel": endpoint.target,
                    "dataSourceGroup": _source(catalog, schema, edge_type.table, fields, where),
                    "id": [_ID_FIELD],
                    "fromKey": [{"name": "source_id", "type": "STRING"}],
                    "toKey": [{"name": "target_id", "type": "STRING"}],
                    "attribute": [
                        _ID_FIELD,
                        {"name": "source_id", "type": "STRING"},
                        {"name": "target_id", "type": "STRING"},
                        *_attributes(properties),
                    ],
                }
            )
            edges.append(entry)
    document["edge"] = edges
    return document
