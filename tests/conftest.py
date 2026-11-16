"""Shared fixtures. All test data is synthetic and generated in code."""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

import govgraph as gg
from govgraph.samples import online_store
from govgraph.store.base import GraphReader, GraphStore

TEST_DSN = os.environ.get("GOVGRAPH_TEST_DSN")


@pytest.fixture(scope="session")
def ontology() -> gg.Ontology:
    return gg.load_ontology()


@dataclass
class PostgresSandbox:
    """A throwaway schema plus a read-only role, removed when the test ends."""

    admin_dsn: str = field(repr=False)
    reader_dsn: str = field(repr=False)
    schema: str
    reader_role: str

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        import psycopg

        with psycopg.connect(self.admin_dsn) as connection:
            cursor = connection.execute(sql, params)
            return cursor.fetchall() if cursor.description else []


@pytest.fixture
def pg() -> Iterator[PostgresSandbox]:
    if not TEST_DSN:
        pytest.skip("set GOVGRAPH_TEST_DSN to run PostgreSQL tests")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    suffix = secrets.token_hex(4)
    schema, role, password = f"gg_test_{suffix}", f"gg_reader_{suffix}", secrets.token_hex(16)
    with psycopg.connect(TEST_DSN) as connection:
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
    sandbox = PostgresSandbox(
        admin_dsn=TEST_DSN,
        reader_dsn=make_conninfo(TEST_DSN, user=role, password=password),
        schema=schema,
        reader_role=role,
    )
    try:
        yield sandbox
    finally:
        with psycopg.connect(TEST_DSN) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )
            connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@dataclass
class Backend:
    """A store together with a way to get a read-only reader for it."""

    name: str
    store: GraphStore
    sandbox: PostgresSandbox | None = None

    def reader(self) -> GraphReader:
        if self.sandbox is None:
            return self.store.reader()
        assert isinstance(self.store, gg.PostgresStore)
        self.store.grant_reader(self.sandbox.reader_role)
        return self.store.reader(self.sandbox.reader_dsn)


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
def backend(request: pytest.FixtureRequest, ontology: gg.Ontology) -> Backend:
    """An empty store of each kind; the PostgreSQL one is skipped without a database."""
    if request.param == "memory":
        return Backend("memory", gg.MemoryStore(ontology))
    sandbox: PostgresSandbox = request.getfixturevalue("pg")
    store = gg.PostgresStore(ontology, sandbox.admin_dsn, schema=sandbox.schema)
    store.create_schema()
    return Backend("postgres", store, sandbox)


@pytest.fixture
def loaded(backend: Backend) -> Backend:
    """A store of each kind holding the synthetic online-store dataset."""
    gg.ingest(online_store(), backend.store)
    return backend


@pytest.fixture
def memory_store(ontology: gg.Ontology) -> gg.MemoryStore:
    store = gg.MemoryStore(ontology)
    gg.ingest(online_store(), store)
    return store
