"""PostgreSQL store. Requires the ``postgres`` extra (psycopg 3)."""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Collection, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from types import TracebackType
from typing import TYPE_CHECKING, Any

from govgraph.errors import StoreError, StoreTimeoutError, UnsafeRoleError
from govgraph.ids import id_type, is_id
from govgraph.records import Edge, EdgeRecord, Node, NodeRecord
from govgraph.spec.model import Ontology
from govgraph.store.base import DEFAULT_TIMEOUT_S, Direction, Match
from govgraph.store.ddl import (
    DEFAULT_SCHEMA,
    EDGE_BASE_COLUMNS,
    create_statements,
    edge_columns,
    node_columns,
    reader_grant_statements,
)
from govgraph.store.queries import READ_QUERIES, QueryCatalog, Statement
from govgraph.validation import to_edge, to_node

if TYPE_CHECKING:
    import psycopg


def _driver() -> Any:
    try:
        import psycopg
    except ImportError as exc:
        raise StoreError(
            "PostgreSQL support needs psycopg. Install it with: pip install 'govgraph[postgres]'"
        ) from exc
    return psycopg


def _connect(dsn: str) -> psycopg.Connection[tuple[Any, ...]]:
    driver = _driver()
    try:
        connection: psycopg.Connection[tuple[Any, ...]] = driver.connect(dsn)
    except driver.Error as exc:
        raise StoreError("could not connect to the database") from exc
    return connection


def _utc(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value


def _properties(columns: Sequence[str], row: Sequence[Any]) -> dict[str, Any]:
    return {
        name: _utc(value) for name, value in zip(columns, row, strict=True) if value is not None
    }


class PostgresReader:
    """Read-only access to a PostgreSQL graph.

    Every query runs in a READ ONLY transaction with a statement timeout, and only
    statements on the allow-list are executed. By default the reader also refuses to
    work with a database role that would be able to change the graph.
    """

    def __init__(
        self,
        ontology: Ontology,
        dsn: str,
        *,
        schema: str = DEFAULT_SCHEMA,
        allow_write_capable_role: bool = False,
    ) -> None:
        self._ontology = ontology
        self._dsn = dsn
        self._catalog = QueryCatalog(ontology, schema)
        self._allow_write_capable_role = allow_write_capable_role
        self._connection: psycopg.Connection[tuple[Any, ...]] | None = None
        self._lock = threading.Lock()

    @property
    def ontology(self) -> Ontology:
        return self._ontology

    def __enter__(self) -> PostgresReader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def connect(self) -> None:
        """Connect now and check the database role, instead of on the first query.

        Call this at start-up so that a misconfigured role is reported to the operator
        straight away rather than to an agent in the middle of a tool call.
        """
        with self._lock:
            self._open()

    def close(self) -> None:
        with self._lock:
            self._drop_connection()

    def _drop_connection(self) -> None:
        if self._connection is not None:
            connection, self._connection = self._connection, None
            connection.close()

    def _open(self) -> psycopg.Connection[tuple[Any, ...]]:
        if self._connection is not None and not self._connection.closed:
            return self._connection
        connection = _connect(self._dsn)
        try:
            connection.read_only = True
            self._check_role(connection)
        except BaseException:
            connection.close()
            raise
        self._connection = connection
        return connection

    def _check_role(self, connection: psycopg.Connection[tuple[Any, ...]]) -> None:
        statement = self._catalog.role_privileges()
        try:
            with connection.transaction():
                row = connection.execute(statement.text, statement.params).fetchone()
        except _driver().Error as exc:
            raise StoreError("could not check the privileges of the database role") from exc
        schema_exists, is_superuser, can_create, can_modify = row or (False, False, False, False)
        if not schema_exists:
            raise StoreError(
                f"schema '{self._catalog.schema}' does not exist. Create it with create_schema()"
            )
        if (is_superuser or can_create or can_modify) and not self._allow_write_capable_role:
            raise UnsafeRoleError(
                "the database role used for reading is able to modify the graph. Connect with "
                "a role that only has SELECT (see reader_grant_statements), or pass "
                "allow_write_capable_role=True to accept the risk"
            )

    def _run(self, statement: Statement, timeout_s: float | None) -> list[tuple[Any, ...]]:
        if statement.name not in READ_QUERIES:
            raise StoreError(f"'{statement.name.value}' is not an allow-listed read query")
        driver = _driver()
        timeout = self._catalog.set_timeout(DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s)
        with self._lock:
            connection = self._open()
            try:
                with connection.transaction():
                    connection.execute(timeout.text, timeout.params)
                    return connection.execute(statement.text, statement.params).fetchall()
            except driver.errors.QueryCanceled as exc:
                raise StoreTimeoutError("the query exceeded its time limit") from exc
            except driver.Error as exc:
                self._drop_connection()
                raise StoreError("a read query failed") from exc

    def get_nodes(self, node_ids: Sequence[str], *, timeout_s: float | None = None) -> list[Node]:
        by_type: dict[str, list[str]] = defaultdict(list)
        for node_id in set(node_ids):
            if is_id(node_id) and id_type(node_id) in self._ontology.node_types:
                by_type[id_type(node_id)].append(node_id)
        nodes: list[Node] = []
        for node_type, ids in sorted(by_type.items()):
            columns = node_columns(self._ontology.node_type(node_type))
            for row in self._run(self._catalog.nodes_by_id(node_type, sorted(ids)), timeout_s):
                nodes.append(Node(row[0], node_type, _properties(columns[1:], row[1:])))
        return sorted(nodes, key=lambda n: n.id)

    def get_edges(
        self,
        node_ids: Sequence[str],
        edge_type: str,
        direction: Direction,
        *,
        limit: int,
        timeout_s: float | None = None,
    ) -> list[Edge]:
        statement = self._catalog.edges(edge_type, direction, sorted(set(node_ids)), limit)
        columns = edge_columns(self._ontology.edge_type(edge_type))
        base = len(EDGE_BASE_COLUMNS)
        return [
            Edge(
                id=row[0],
                type=edge_type,
                source_id=row[1],
                target_id=row[3],
                properties=_properties(columns[base:], row[base:]),
            )
            for row in self._run(statement, timeout_s)
        ]

    def find_nodes(
        self,
        node_type: str,
        prop: str,
        value: str,
        *,
        match: Match = "exact",
        limit: int,
        timeout_s: float | None = None,
    ) -> list[Node]:
        statement = self._catalog.nodes_by_text(node_type, prop, value, match, limit)
        columns = node_columns(self._ontology.node_type(node_type))
        return [
            Node(row[0], node_type, _properties(columns[1:], row[1:]))
            for row in self._run(statement, timeout_s)
        ]


class PostgresStore:
    """A graph stored in PostgreSQL, one table per node type and per edge type."""

    def __init__(self, ontology: Ontology, dsn: str, *, schema: str = DEFAULT_SCHEMA) -> None:
        self._ontology = ontology
        self._dsn = dsn
        self._schema = schema
        self._catalog = QueryCatalog(ontology, schema)

    @property
    def ontology(self) -> Ontology:
        return self._ontology

    @contextmanager
    def _transaction(self, action: str) -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
        driver = _driver()
        connection = _connect(self._dsn)
        try:
            with connection:
                yield connection
        except driver.Error as exc:
            raise StoreError(f"could not {action}") from exc

    def _execute_all(self, action: str, statements: Iterable[str]) -> None:
        with self._transaction(action) as connection:
            for text in statements:
                connection.execute(text)

    def create_schema(self) -> None:
        self._execute_all("create the schema", create_statements(self._ontology, self._schema))

    def grant_reader(self, role: str) -> None:
        """Give an existing database role read-only access to the graph."""
        self._execute_all("grant read access", reader_grant_statements(role, self._schema))

    def upsert(
        self, nodes: Iterable[NodeRecord] = (), edges: Iterable[EdgeRecord] = ()
    ) -> tuple[int, int]:
        node_rows: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
        edge_rows: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
        node_count = edge_count = 0
        for record in nodes:
            node = to_node(self._ontology, record)
            columns = node_columns(self._ontology.node_type(node.type))
            node_rows[node.type].append(
                (node.id, *(node.properties.get(name) for name in columns[1:]))
            )
            node_count += 1
        for edge_record in edges:
            edge = to_edge(self._ontology, edge_record)
            columns = edge_columns(self._ontology.edge_type(edge.type))
            edge_rows[edge.type].append(
                (
                    edge.id,
                    edge.source_id,
                    id_type(edge.source_id),
                    edge.target_id,
                    id_type(edge.target_id),
                    *(edge.properties.get(name) for name in columns[len(EDGE_BASE_COLUMNS) :]),
                )
            )
            edge_count += 1
        if not node_rows and not edge_rows:
            return 0, 0
        with self._transaction("write to the graph") as connection, connection.cursor() as cursor:
            for node_type, rows in node_rows.items():
                cursor.executemany(self._catalog.upsert_node(node_type).text, rows)
            for edge_type, rows in edge_rows.items():
                cursor.executemany(self._catalog.upsert_edge(edge_type).text, rows)
        return node_count, edge_count

    def existing_node_ids(self, node_ids: Collection[str]) -> set[str]:
        by_type: dict[str, list[str]] = defaultdict(list)
        for node_id in node_ids:
            if is_id(node_id) and id_type(node_id) in self._ontology.node_types:
                by_type[id_type(node_id)].append(node_id)
        found: set[str] = set()
        if not by_type:
            return found
        with self._transaction("read from the graph") as connection:
            for node_type, ids in by_type.items():
                statement = self._catalog.existing_node_ids(node_type, ids)
                rows = connection.execute(statement.text, statement.params).fetchall()
                found.update(row[0] for row in rows)
        return found

    def reader(
        self, dsn: str | None = None, *, allow_write_capable_role: bool = False
    ) -> PostgresReader:
        """Return a read-only reader.

        Pass the ``dsn`` of a role that only has SELECT. Without it the reader would use
        the store's own, write-capable role, which it refuses unless
        ``allow_write_capable_role`` is set.
        """
        return PostgresReader(
            self._ontology,
            dsn or self._dsn,
            schema=self._schema,
            allow_write_capable_role=allow_write_capable_role,
        )
