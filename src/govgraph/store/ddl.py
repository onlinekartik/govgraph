"""Generate PostgreSQL DDL from the ontology: one table per node type and per edge type."""

from __future__ import annotations

import re

from govgraph.errors import StoreError
from govgraph.spec.model import EdgeType, NodeType, Ontology, PropertyType

DEFAULT_SCHEMA = "govgraph"

PG_TYPES: dict[PropertyType, str] = {
    "string": "TEXT",
    "integer": "BIGINT",
    "number": "DOUBLE PRECISION",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMPTZ",
}

EDGE_BASE_COLUMNS: tuple[str, ...] = ("id", "source_id", "source_type", "target_id", "target_type")

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def quote_ident(name: str) -> str:
    """Quote a SQL identifier, refusing anything but lower-case letters, digits and ``_``.

    Every identifier govgraph puts into SQL goes through here. Identifiers come only
    from the validated ontology or from operator configuration, never from tool input.
    """
    if not _IDENTIFIER.match(name):
        raise StoreError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def qualified(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def node_columns(node_type: NodeType) -> tuple[str, ...]:
    return ("id", *node_type.properties)


def edge_columns(edge_type: EdgeType) -> tuple[str, ...]:
    return (*EDGE_BASE_COLUMNS, *edge_type.properties)


def create_statements(ontology: Ontology, schema: str = DEFAULT_SCHEMA) -> list[str]:
    """Return idempotent DDL that creates the schema, or adds what is missing from it."""
    statements = [f"CREATE SCHEMA IF NOT EXISTS {quote_ident(schema)}"]
    for node_type in ontology.node_types.values():
        table = qualified(schema, node_type.table)
        columns = ['"id" TEXT PRIMARY KEY']
        columns += [
            f"{quote_ident(p.name)} {PG_TYPES[p.type]}" for p in node_type.properties.values()
        ]
        statements.append(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(columns)})")
        statements += _add_columns(table, node_type)
    for edge_type in ontology.edge_types.values():
        table = qualified(schema, edge_type.table)
        columns = ['"id" TEXT PRIMARY KEY']
        columns += [f"{quote_ident(name)} TEXT NOT NULL" for name in EDGE_BASE_COLUMNS[1:]]
        columns += [
            f"{quote_ident(p.name)} {PG_TYPES[p.type]}" for p in edge_type.properties.values()
        ]
        statements.append(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(columns)})")
        statements += _add_columns(table, edge_type)
        for end in ("source", "target"):
            index = quote_ident(f"{edge_type.table}_{end}_idx")
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {index} ON {table} ({quote_ident(end + '_id')})"
            )
    return statements


def _add_columns(table: str, owner: NodeType | EdgeType) -> list[str]:
    # Lets an extension add properties to a table that already exists.
    return [
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {quote_ident(p.name)} {PG_TYPES[p.type]}"
        for p in owner.properties.values()
    ]


def reader_grant_statements(role: str, schema: str = DEFAULT_SCHEMA) -> list[str]:
    """Return statements that give an existing role read-only access to the graph."""
    quoted_role, quoted_schema = quote_ident(role), quote_ident(schema)
    return [
        f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {quoted_schema} TO {quoted_role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted_schema} "
        f"GRANT SELECT ON TABLES TO {quoted_role}",
    ]
