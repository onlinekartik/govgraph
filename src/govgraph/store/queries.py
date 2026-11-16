"""The complete list of SQL statements govgraph will run against PostgreSQL.

Nothing else in the package builds SQL. Each statement has a fixed shape. Table and
column names are taken from the validated ontology and quoted by :func:`quote_ident`;
every value is a bound parameter. A reader refuses to run anything that is not in
:data:`READ_QUERIES`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from govgraph.spec.model import Ontology
from govgraph.store.base import Direction, Match, check_edge_query, check_find_query
from govgraph.store.ddl import DEFAULT_SCHEMA, edge_columns, node_columns, qualified, quote_ident


class QueryName(str, Enum):
    SET_TIMEOUT = "set_timeout"
    ROLE_PRIVILEGES = "role_privileges"
    NODES_BY_ID = "nodes_by_id"
    EDGES_BY_SOURCE = "edges_by_source"
    EDGES_BY_TARGET = "edges_by_target"
    NODES_BY_TEXT_EXACT = "nodes_by_text_exact"
    NODES_BY_TEXT_CONTAINS = "nodes_by_text_contains"
    EXISTING_NODE_IDS = "existing_node_ids"
    UPSERT_NODE = "upsert_node"
    UPSERT_EDGE = "upsert_edge"


READ_QUERIES = frozenset(
    {
        QueryName.SET_TIMEOUT,
        QueryName.ROLE_PRIVILEGES,
        QueryName.NODES_BY_ID,
        QueryName.EDGES_BY_SOURCE,
        QueryName.EDGES_BY_TARGET,
        QueryName.NODES_BY_TEXT_EXACT,
        QueryName.NODES_BY_TEXT_CONTAINS,
    }
)


@dataclass(frozen=True)
class Statement:
    """One allow-listed statement, ready to execute."""

    name: QueryName
    text: str
    params: tuple[Any, ...] = ()


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so that ``value`` only ever matches itself."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class QueryCatalog:
    """Builds the allow-listed statements for one ontology and schema."""

    def __init__(self, ontology: Ontology, schema: str = DEFAULT_SCHEMA) -> None:
        quote_ident(schema)
        self._ontology = ontology
        self._schema = schema

    @property
    def schema(self) -> str:
        return self._schema

    def _column_list(self, columns: tuple[str, ...]) -> str:
        return ", ".join(quote_ident(column) for column in columns)

    def set_timeout(self, timeout_s: float) -> Statement:
        millis = max(1, int(timeout_s * 1000))
        return Statement(
            QueryName.SET_TIMEOUT,
            "SELECT set_config('statement_timeout', %s, true)",
            (str(millis),),
        )

    def role_privileges(self) -> Statement:
        """Whether the schema exists and whether the current role could change the graph."""
        text = (
            "SELECT n.oid IS NOT NULL, "
            "r.rolsuper, "
            "COALESCE(has_schema_privilege(n.oid, 'CREATE'), false), "
            "EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = n.oid "
            "AND c.relkind IN ('r', 'p') "
            "AND (has_table_privilege(c.oid, 'DELETE, TRUNCATE') "
            # Also true for a grant on individual columns, which the table check misses.
            "OR has_any_column_privilege(c.oid, 'INSERT, UPDATE'))) "
            "FROM pg_roles r LEFT JOIN pg_namespace n ON n.nspname = %s "
            "WHERE r.rolname = current_user"
        )
        return Statement(QueryName.ROLE_PRIVILEGES, text, (self._schema,))

    def nodes_by_id(self, node_type: str, node_ids: list[str]) -> Statement:
        declared = self._ontology.node_type(node_type)
        text = (
            f"SELECT {self._column_list(node_columns(declared))} "  # noqa: S608
            f"FROM {qualified(self._schema, declared.table)} "
            'WHERE "id" = ANY(%s) ORDER BY "id"'
        )
        return Statement(QueryName.NODES_BY_ID, text, (node_ids,))

    def edges(
        self, edge_type: str, direction: Direction, node_ids: list[str], limit: int
    ) -> Statement:
        check_edge_query(self._ontology, edge_type, direction, limit)
        declared = self._ontology.edge_type(edge_type)
        name, column = (
            (QueryName.EDGES_BY_SOURCE, "source_id")
            if direction == "out"
            else (QueryName.EDGES_BY_TARGET, "target_id")
        )
        text = (
            f"SELECT {self._column_list(edge_columns(declared))} "  # noqa: S608
            f"FROM {qualified(self._schema, declared.table)} "
            f'WHERE {quote_ident(column)} = ANY(%s) ORDER BY "id" LIMIT %s'
        )
        return Statement(name, text, (node_ids, limit))

    def nodes_by_text(
        self, node_type: str, prop: str, value: str, match: Match, limit: int
    ) -> Statement:
        check_find_query(self._ontology, node_type, prop, match, limit)
        declared = self._ontology.node_type(node_type)
        select = (
            f"SELECT {self._column_list(node_columns(declared))} "  # noqa: S608
            f"FROM {qualified(self._schema, declared.table)} "
        )
        if match == "exact":
            text = f'{select}WHERE lower({quote_ident(prop)}) = lower(%s) ORDER BY "id" LIMIT %s'
            return Statement(QueryName.NODES_BY_TEXT_EXACT, text, (value, limit))
        text = f"{select}WHERE {quote_ident(prop)} ILIKE %s ESCAPE '\\' ORDER BY \"id\" LIMIT %s"
        return Statement(QueryName.NODES_BY_TEXT_CONTAINS, text, (f"%{escape_like(value)}%", limit))

    def existing_node_ids(self, node_type: str, node_ids: list[str]) -> Statement:
        declared = self._ontology.node_type(node_type)
        text = (
            f'SELECT "id" FROM {qualified(self._schema, declared.table)} '  # noqa: S608
            'WHERE "id" = ANY(%s)'
        )
        return Statement(QueryName.EXISTING_NODE_IDS, text, (node_ids,))

    def upsert_node(self, node_type: str) -> Statement:
        declared = self._ontology.node_type(node_type)
        return self._upsert(QueryName.UPSERT_NODE, declared.table, node_columns(declared))

    def upsert_edge(self, edge_type: str) -> Statement:
        declared = self._ontology.edge_type(edge_type)
        return self._upsert(QueryName.UPSERT_EDGE, declared.table, edge_columns(declared))

    def _upsert(self, name: QueryName, table: str, columns: tuple[str, ...]) -> Statement:
        target = qualified(self._schema, table)
        placeholders = ", ".join(["%s"] * len(columns))
        # A value that the new row leaves NULL keeps what is already stored.
        updates = ", ".join(
            f"{quote_ident(c)} = COALESCE(EXCLUDED.{quote_ident(c)}, t.{quote_ident(c)})"
            for c in columns[1:]
        )
        text = (
            f"INSERT INTO {target} AS t ({self._column_list(columns)}) "  # noqa: S608
            f'VALUES ({placeholders}) ON CONFLICT ("id") DO UPDATE SET {updates}'
        )
        return Statement(name, text)
