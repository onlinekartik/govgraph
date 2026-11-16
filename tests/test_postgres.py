"""PostgreSQL-specific behaviour. Skipped unless GOVGRAPH_TEST_DSN points at a database."""

from __future__ import annotations

import threading
import time

import pytest

import govgraph as gg
from govgraph.samples import asset, online_store
from govgraph.store.postgres import PostgresReader
from govgraph.store.queries import QueryCatalog
from tests.conftest import PostgresSandbox

pytestmark = pytest.mark.postgres


@pytest.fixture
def store(pg: PostgresSandbox, ontology: gg.Ontology) -> gg.PostgresStore:
    store = gg.PostgresStore(ontology, pg.admin_dsn, schema=pg.schema)
    store.create_schema()
    return store


@pytest.fixture
def reader(store: gg.PostgresStore, pg: PostgresSandbox) -> PostgresReader:
    gg.ingest(online_store(), store)
    store.grant_reader(pg.reader_role)
    return store.reader(pg.reader_dsn)


def tables(pg: PostgresSandbox) -> set[str]:
    rows = pg.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (pg.schema,)
    )
    return {row[0] for row in rows}


def test_create_schema_makes_one_table_per_type(
    store: gg.PostgresStore, pg: PostgresSandbox, ontology: gg.Ontology
) -> None:
    expected = {n.table for n in ontology.node_types.values()}
    expected |= {e.table for e in ontology.edge_types.values()}
    assert tables(pg) == expected

    store.create_schema()  # safe to repeat
    assert tables(pg) == expected


def test_create_schema_adds_columns_and_tables_for_an_extension(
    store: gg.PostgresStore, pg: PostgresSandbox
) -> None:
    record = gg.NodeRecord("DataAsset", {"qualified_name": "shop.orders"}, {"kind": "table"})
    store.upsert([record])
    extended = gg.load_ontology(
        [
            {
                "ontology": {"name": "ext", "version": "0.1.0"},
                "node_types": {
                    "DataAsset": {"properties": {"row_count": {"type": "integer"}}},
                    "Pipeline": {
                        "key": ["name"],
                        "properties": {"name": {"type": "string", "required": True}},
                    },
                },
            }
        ]
    )
    bigger = gg.PostgresStore(extended, pg.admin_dsn, schema=pg.schema)
    bigger.create_schema()
    bigger.upsert([gg.NodeRecord("DataAsset", {"qualified_name": "shop.orders"}, {"row_count": 9})])
    bigger.grant_reader(pg.reader_role)

    assert "node_pipeline" in tables(pg)
    with bigger.reader(pg.reader_dsn) as reader:
        (node,) = reader.get_nodes([record.id])
    assert node.properties == {"qualified_name": "shop.orders", "kind": "table", "row_count": 9}


def test_rows_are_stored_under_deterministic_ids(
    store: gg.PostgresStore, pg: PostgresSandbox
) -> None:
    gg.ingest(online_store(), store)
    gg.ingest(online_store(), store)
    rows = pg.execute(
        f'SELECT "id", "source_type", "target_type" FROM "{pg.schema}"."edge_failed_on"'  # noqa: S608
    )
    assert len(rows) == 2
    assert {row[1:] for row in rows} == {("DQFailure", "DataAsset")}
    count = pg.execute(f'SELECT count(*) FROM "{pg.schema}"."node_data_asset"')  # noqa: S608
    assert count == [(14,)]


def test_reader_refuses_a_role_that_can_write(store: gg.PostgresStore) -> None:
    with pytest.raises(gg.UnsafeRoleError, match="able to modify the graph"):
        store.reader().get_nodes([asset("raw.payments").id])
    with pytest.raises(gg.UnsafeRoleError):
        store.reader().connect()


def test_unsafe_role_is_logged_for_the_operator_but_not_shown_to_the_agent(
    store: gg.PostgresStore, caplog: pytest.LogCaptureFixture
) -> None:
    kit = gg.Toolkit(store.reader())
    with caplog.at_level("WARNING", logger="govgraph.tools"), pytest.raises(gg.ToolError) as caught:
        kit.find_by_term("revenue")
    assert str(caught.value) == "store_error: the graph store could not answer"
    assert "able to modify the graph" in caplog.text


def test_reader_refuses_a_role_with_one_write_grant(
    store: gg.PostgresStore, pg: PostgresSandbox
) -> None:
    store.grant_reader(pg.reader_role)
    pg.execute(f'GRANT INSERT ON "{pg.schema}"."node_owner" TO "{pg.reader_role}"')
    with pytest.raises(gg.UnsafeRoleError):
        store.reader(pg.reader_dsn).get_nodes([asset("raw.payments").id])


@pytest.mark.parametrize(
    "grant",
    [
        'GRANT UPDATE ("name") ON "{schema}"."node_owner" TO "{role}"',
        'GRANT INSERT ("id", "name") ON "{schema}"."node_owner" TO "{role}"',
        'GRANT DELETE ON "{schema}"."edge_owned_by" TO "{role}"',
        'GRANT TRUNCATE ON "{schema}"."node_owner" TO "{role}"',
        'GRANT CREATE ON SCHEMA "{schema}" TO "{role}"',
    ],
)
def test_reader_refuses_every_kind_of_write_grant(
    store: gg.PostgresStore, pg: PostgresSandbox, grant: str
) -> None:
    store.grant_reader(pg.reader_role)
    store.reader(pg.reader_dsn).connect()  # read-only so far, so accepted
    pg.execute(grant.format(schema=pg.schema, role=pg.reader_role))
    with pytest.raises(gg.UnsafeRoleError):
        store.reader(pg.reader_dsn).connect()


def test_waiting_for_the_connection_counts_against_the_time_limit(
    reader: PostgresReader, pg: PostgresSandbox
) -> None:
    """Two callers share one reader, as they do under the MCP server."""
    import psycopg

    node_id = asset("raw.payments").id
    reader.connect()
    locked, release = threading.Event(), threading.Event()

    def hold_table_lock() -> None:
        with psycopg.connect(pg.admin_dsn) as connection:
            connection.execute(
                f'LOCK TABLE "{pg.schema}"."node_data_asset" IN ACCESS EXCLUSIVE MODE'
            )
            locked.set()
            release.wait(timeout=30)

    def slow_caller() -> None:
        # Blocks in the database until the table lock goes away, holding the reader.
        reader.get_nodes([node_id], timeout_s=20)

    blocker = threading.Thread(target=hold_table_lock)
    blocker.start()
    assert locked.wait(timeout=10)
    first = threading.Thread(target=slow_caller)
    first.start()
    time.sleep(0.3)  # let the first caller take the reader and block in the database
    started = time.monotonic()
    try:
        with pytest.raises(gg.StoreTimeoutError):
            reader.get_nodes([node_id], timeout_s=0.4)
        waited = time.monotonic() - started
    finally:
        release.set()
        blocker.join()
        first.join()
    assert waited < 1.0, "the second caller must give up at its own deadline"
    assert len(reader.get_nodes([node_id], timeout_s=5)) == 1


def test_write_capable_role_can_be_accepted_explicitly(store: gg.PostgresStore) -> None:
    gg.ingest(online_store(), store)
    with store.reader(allow_write_capable_role=True) as reader:
        assert len(reader.get_nodes([asset("raw.payments").id])) == 1


def test_reader_needs_the_schema_to_exist(pg: PostgresSandbox, ontology: gg.Ontology) -> None:
    reader = PostgresReader(ontology, pg.reader_dsn, schema=pg.schema)
    with pytest.raises(gg.StoreError, match="does not exist"):
        reader.get_nodes([asset("raw.payments").id])


def test_reader_role_cannot_write_even_outside_govgraph(
    reader: PostgresReader, pg: PostgresSandbox
) -> None:
    import psycopg

    assert reader.get_nodes([asset("raw.payments").id])
    with psycopg.connect(pg.reader_dsn) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                f'DELETE FROM "{pg.schema}"."node_owner"'  # noqa: S608
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(f'CREATE TABLE "{pg.schema}"."x" (id int)')


def test_reader_runs_in_read_only_transactions(store: gg.PostgresStore) -> None:
    # Even with a role that could write, the transaction itself forbids it.
    import psycopg

    with store.reader(allow_write_capable_role=True) as reader:
        reader.get_nodes([])
        connection = reader._open(time.monotonic() + 5)
        assert connection.read_only is True
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            connection.execute(
                f'DELETE FROM "{store._schema}"."node_owner"'  # noqa: S608
            )
        connection.rollback()


def test_reader_only_runs_allow_listed_statements(reader: PostgresReader) -> None:
    catalog = QueryCatalog(reader.ontology, reader._catalog.schema)
    for statement in (
        catalog.upsert_node("Owner"),
        catalog.upsert_edge("OWNED_BY"),
        catalog.existing_node_ids("Owner", []),
    ):
        with pytest.raises(gg.StoreError, match="is not an allow-listed read query"):
            reader._run(statement, time.monotonic() + 5)


def test_statement_timeout_is_enforced(reader: PostgresReader, pg: PostgresSandbox) -> None:
    import psycopg

    locked, release = threading.Event(), threading.Event()

    def hold_lock() -> None:
        with psycopg.connect(pg.admin_dsn) as connection:
            connection.execute(
                f'LOCK TABLE "{pg.schema}"."node_data_asset" IN ACCESS EXCLUSIVE MODE'
            )
            locked.set()
            release.wait(timeout=30)

    blocker = threading.Thread(target=hold_lock)
    blocker.start()
    try:
        assert locked.wait(timeout=10)
        with pytest.raises(gg.StoreTimeoutError, match="exceeded its time limit"):
            reader.get_nodes([asset("raw.payments").id], timeout_s=0.2)
    finally:
        release.set()
        blocker.join()
    # The connection is still usable after a timeout.
    assert len(reader.get_nodes([asset("raw.payments").id], timeout_s=5)) == 1


def test_reader_reconnects_after_close(reader: PostgresReader) -> None:
    node_id = asset("raw.payments").id
    assert reader.get_nodes([node_id])
    reader.close()
    reader.close()
    assert reader.get_nodes([node_id])


def test_query_failure_is_reported_without_sql(reader: PostgresReader, pg: PostgresSandbox) -> None:
    pg.execute(f'DROP TABLE "{pg.schema}"."node_owner"')
    with pytest.raises(gg.StoreError) as caught:
        reader.find_nodes("Owner", "name", "x", limit=5)
    assert str(caught.value) == "a read query failed"
    # A failed query does not poison later ones.
    assert reader.get_nodes([asset("raw.payments").id])


def test_write_failure_is_reported_without_sql(
    store: gg.PostgresStore, pg: PostgresSandbox
) -> None:
    pg.execute(f'DROP TABLE "{pg.schema}"."node_owner"')
    with pytest.raises(gg.StoreError) as caught:
        store.upsert([gg.NodeRecord("Owner", {"identifier": "a"})])
    assert str(caught.value) == "could not write to the graph"


def test_failed_write_rolls_back_the_whole_batch(
    store: gg.PostgresStore, pg: PostgresSandbox
) -> None:
    pg.execute(f'DROP TABLE "{pg.schema}"."edge_owned_by"')
    table = gg.NodeRecord("DataAsset", {"qualified_name": "shop.orders"})
    owner = gg.NodeRecord("Owner", {"identifier": "a"})
    with pytest.raises(gg.StoreError):
        store.upsert([table, owner], [gg.EdgeRecord("OWNED_BY", table.ref(), owner.ref())])
    assert store.existing_node_ids([table.id, owner.id]) == set()
