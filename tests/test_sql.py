"""The SQL govgraph generates, checked without a database."""

from __future__ import annotations

import pytest

import govgraph as gg
from govgraph.store.ddl import create_statements, quote_ident, reader_grant_statements
from govgraph.store.queries import READ_QUERIES, QueryCatalog, QueryName, escape_like

A, B = "DataAsset:" + "a" * 32, "DataAsset:" + "b" * 32


@pytest.fixture
def catalog(ontology: gg.Ontology) -> QueryCatalog:
    return QueryCatalog(ontology, "graph")


@pytest.mark.parametrize("name", ["govgraph", "node_data_asset", "_x", "a1"])
def test_quote_ident_accepts_plain_names(name: str) -> None:
    assert quote_ident(name) == f'"{name}"'


@pytest.mark.parametrize(
    "name",
    ["", "Upper", "1abc", "a-b", "a b", 'a"b', "a;b", "x" * 64, "tab\t", "a.b", "é"],
)
def test_quote_ident_refuses_anything_else(name: str) -> None:
    with pytest.raises(gg.StoreError, match="unsafe SQL identifier"):
        quote_ident(name)


def test_ddl_has_one_table_per_node_and_edge_type(ontology: gg.Ontology) -> None:
    statements = create_statements(ontology, "graph")
    created = [s for s in statements if s.startswith("CREATE TABLE")]

    assert statements[0] == 'CREATE SCHEMA IF NOT EXISTS "graph"'
    assert len(created) == len(ontology.node_types) + len(ontology.edge_types)
    names = [n.table for n in ontology.node_types.values()]
    names += [e.table for e in ontology.edge_types.values()]
    for name in names:
        assert any(f'"graph"."{name}" (' in s for s in created)
    assert all("IF NOT EXISTS" in s for s in statements)


def test_ddl_maps_property_types_to_columns(ontology: gg.Ontology) -> None:
    statements = create_statements(ontology)
    result = next(
        s for s in statements if 'CREATE TABLE IF NOT EXISTS "govgraph"."node_dq_result"' in s
    )
    assert result == (
        'CREATE TABLE IF NOT EXISTS "govgraph"."node_dq_result" ("id" TEXT PRIMARY KEY, '
        '"rule_id" TEXT, "run_id" TEXT, "status" TEXT, "executed_at" TIMESTAMPTZ, '
        '"rows_checked" BIGINT, "rows_failed" BIGINT)'
    )
    derives = next(
        s for s in statements if 'CREATE TABLE IF NOT EXISTS "govgraph"."edge_derives_from"' in s
    )
    assert derives == (
        'CREATE TABLE IF NOT EXISTS "govgraph"."edge_derives_from" ("id" TEXT PRIMARY KEY, '
        '"source_id" TEXT NOT NULL, "source_type" TEXT NOT NULL, "target_id" TEXT NOT NULL, '
        '"target_type" TEXT NOT NULL, "lineage_id" TEXT)'
    )
    assert (
        'CREATE INDEX IF NOT EXISTS "edge_derives_from_target_idx" '
        'ON "govgraph"."edge_derives_from" ("target_id")'
    ) in statements
    assert (
        'ALTER TABLE "govgraph"."node_dq_result" ADD COLUMN IF NOT EXISTS "rows_failed" BIGINT'
    ) in statements


def test_ddl_covers_every_property_type() -> None:
    ontology = gg.load_ontology(
        [
            {
                "ontology": {"name": "ext", "version": "0.1.0"},
                "node_types": {
                    "Owner": {
                        "properties": {
                            "score": {"type": "number"},
                            "active": {"type": "boolean"},
                            "joined": {"type": "date"},
                        }
                    }
                },
            }
        ]
    )
    owner = next(s for s in create_statements(ontology) if '"node_owner" (' in s)
    assert '"score" DOUBLE PRECISION, "active" BOOLEAN, "joined" DATE' in owner


def test_ddl_and_grants_refuse_unsafe_names(ontology: gg.Ontology) -> None:
    with pytest.raises(gg.StoreError):
        create_statements(ontology, 'x"; DROP SCHEMA public; --')
    with pytest.raises(gg.StoreError):
        reader_grant_statements("reader; DROP ROLE admin")
    with pytest.raises(gg.StoreError):
        QueryCatalog(ontology, "Bad Schema")


def test_reader_grants_are_select_only() -> None:
    statements = reader_grant_statements("agent_reader", "graph")
    assert statements == [
        'GRANT USAGE ON SCHEMA "graph" TO "agent_reader"',
        'GRANT SELECT ON ALL TABLES IN SCHEMA "graph" TO "agent_reader"',
        'ALTER DEFAULT PRIVILEGES IN SCHEMA "graph" GRANT SELECT ON TABLES TO "agent_reader"',
    ]


def test_read_allow_list_contains_no_writes() -> None:
    assert QueryName.UPSERT_NODE not in READ_QUERIES
    assert QueryName.UPSERT_EDGE not in READ_QUERIES
    assert QueryName.EXISTING_NODE_IDS not in READ_QUERIES
    assert len(READ_QUERIES) == 7


def test_read_statements_are_selects_with_bound_values(catalog: QueryCatalog) -> None:
    hostile = "x'; DROP TABLE node_owner; --"
    statements = [
        catalog.set_timeout(1.5),
        catalog.role_privileges(),
        catalog.nodes_by_id("DataAsset", [A, hostile]),
        catalog.edges("OWNED_BY", "out", [A, hostile], 5),
        catalog.edges("OWNED_BY", "in", [B], 5),
        catalog.nodes_by_text("Owner", "name", hostile, "exact", 5),
        catalog.nodes_by_text("Owner", "name", hostile, "contains", 5),
    ]
    assert {s.name for s in statements} == READ_QUERIES
    for statement in statements:
        assert statement.text.startswith("SELECT ")
        assert hostile not in statement.text
        assert "DROP" not in statement.text
        assert ";" not in statement.text
        assert statement.text.count("%s") == len(statement.params)


def test_edge_statements(catalog: QueryCatalog) -> None:
    out = catalog.edges("DERIVES_FROM", "out", [A], 7)
    assert out.name is QueryName.EDGES_BY_SOURCE
    assert out.text == (
        'SELECT "id", "source_id", "source_type", "target_id", "target_type", "lineage_id" '
        'FROM "graph"."edge_derives_from" WHERE "source_id" = ANY(%s) ORDER BY "id" LIMIT %s'
    )
    assert out.params == ([A], 7)
    incoming = catalog.edges("DERIVES_FROM", "in", [A], 7)
    assert incoming.name is QueryName.EDGES_BY_TARGET
    assert 'WHERE "target_id" = ANY(%s)' in incoming.text


def test_text_search_statements(catalog: QueryCatalog) -> None:
    exact = catalog.nodes_by_text("BusinessTerm", "name", "Order Total", "exact", 3)
    assert exact.text.endswith('WHERE lower("name") = lower(%s) ORDER BY "id" LIMIT %s')
    assert exact.params == ("Order Total", 3)
    contains = catalog.nodes_by_text("BusinessTerm", "name", "50%_off\\", "contains", 3)
    assert contains.text.endswith('WHERE "name" ILIKE %s ESCAPE \'\\\' ORDER BY "id" LIMIT %s')
    assert contains.params == ("%50\\%\\_off\\\\%", 3)


def test_escape_like() -> None:
    assert escape_like("plain") == "plain"
    assert escape_like("a%b_c\\d") == "a\\%b\\_c\\\\d"


def test_set_timeout_is_in_whole_milliseconds(catalog: QueryCatalog) -> None:
    assert catalog.set_timeout(2.5).params == ("2500",)
    assert catalog.set_timeout(0.0001).params == ("1",)


def test_upsert_statements_merge_on_conflict(catalog: QueryCatalog) -> None:
    node = catalog.upsert_node("Owner")
    assert node.name is QueryName.UPSERT_NODE
    assert node.text == (
        'INSERT INTO "graph"."node_owner" AS t ("id", "identifier", "name", "kind") '
        'VALUES (%s, %s, %s, %s) ON CONFLICT ("id") DO UPDATE SET '
        '"identifier" = COALESCE(EXCLUDED."identifier", t."identifier"), '
        '"name" = COALESCE(EXCLUDED."name", t."name"), '
        '"kind" = COALESCE(EXCLUDED."kind", t."kind")'
    )
    edge = catalog.upsert_edge("OWNED_BY")
    assert edge.name is QueryName.UPSERT_EDGE
    assert edge.text.startswith('INSERT INTO "graph"."edge_owned_by" AS t ("id", "source_id", ')
    assert catalog.existing_node_ids("Owner", [A]).text == (
        'SELECT "id" FROM "graph"."node_owner" WHERE "id" = ANY(%s)'
    )


def test_catalog_refuses_names_that_are_not_in_the_ontology(catalog: QueryCatalog) -> None:
    with pytest.raises(gg.OntologyError):
        catalog.nodes_by_id('Owner" --', [A])
    with pytest.raises(gg.OntologyError):
        catalog.edges("OWNED_BY; DROP", "out", [A], 5)
    with pytest.raises(gg.OntologyError):
        catalog.nodes_by_text("Owner", "name) = '' OR (1=1", "x", "exact", 5)
    with pytest.raises(gg.OntologyError):
        catalog.upsert_node("Ghost")
    with pytest.raises(gg.OntologyError):
        catalog.upsert_edge("GHOST")
    with pytest.raises(gg.OntologyError):
        catalog.existing_node_ids("Ghost", [A])
    assert catalog.schema == "graph"
