"""Behaviour every store must share. Runs against memory and, when available, PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import govgraph as gg
from govgraph.samples import asset
from tests.conftest import Backend


def owner(identifier: str, /, **properties: object) -> gg.NodeRecord:
    return gg.NodeRecord("Owner", {"identifier": identifier}, properties)


def table(qualified_name: str, /, **properties: object) -> gg.NodeRecord:
    return gg.NodeRecord("DataAsset", {"qualified_name": qualified_name}, properties)


def owned_by(asset_name: str, identifier: str) -> gg.EdgeRecord:
    return gg.EdgeRecord("OWNED_BY", asset(asset_name), owner(identifier).ref())


def test_upsert_then_read_back(backend: Backend) -> None:
    written = backend.store.upsert(
        [table("shop.orders", kind="table"), owner("team-a", name="Team A", kind="team")],
        [owned_by("shop.orders", "team-a")],
    )
    assert written == (2, 1)

    reader = backend.reader()
    nodes = reader.get_nodes([asset("shop.orders").id, owner("team-a").id])
    assert {n.type: dict(n.properties) for n in nodes} == {
        "DataAsset": {"qualified_name": "shop.orders", "kind": "table"},
        "Owner": {"identifier": "team-a", "name": "Team A", "kind": "team"},
    }
    assert [n.id for n in nodes] == sorted(n.id for n in nodes)


def test_upsert_is_idempotent(backend: Backend) -> None:
    records = [table("shop.orders", kind="table"), owner("team-a", name="Team A")]
    edges = [owned_by("shop.orders", "team-a")]
    backend.store.upsert(records, edges)
    reader = backend.reader()
    before_nodes = reader.get_nodes([r.id for r in records])
    before_edges = reader.get_edges([records[0].id], "OWNED_BY", "out", limit=10)

    backend.store.upsert(records, edges)

    assert reader.get_nodes([r.id for r in records]) == before_nodes
    assert reader.get_edges([records[0].id], "OWNED_BY", "out", limit=10) == before_edges
    assert len(before_edges) == 1


def test_upsert_merges_instead_of_erasing(backend: Backend) -> None:
    backend.store.upsert([table("shop.orders", kind="table", description="Orders.")])
    backend.store.upsert([table("shop.orders", name="orders", description="All orders.")])

    (node,) = backend.reader().get_nodes([asset("shop.orders").id])
    assert node.properties == {
        "qualified_name": "shop.orders",
        "kind": "table",
        "name": "orders",
        "description": "All orders.",
    }


def test_edge_properties_merge_too(backend: Backend) -> None:
    backend.store.upsert([table("a"), table("b")])
    derives = gg.EdgeRecord("DERIVES_FROM", asset("a"), asset("b"))
    backend.store.upsert(edges=[derives])
    backend.store.upsert(
        edges=[gg.EdgeRecord("DERIVES_FROM", asset("a"), asset("b"), {"lineage_id": "L"})]
    )
    backend.store.upsert(edges=[derives])

    (edge,) = backend.reader().get_edges([asset("a").id], "DERIVES_FROM", "out", limit=5)
    assert edge.properties == {"lineage_id": "L"}


def test_invalid_record_leaves_the_store_untouched(backend: Backend) -> None:
    with pytest.raises(gg.RecordError, match="unknown property 'colour'"):
        backend.store.upsert([table("shop.orders"), table("shop.items", colour="red")])
    assert backend.store.existing_node_ids([asset("shop.orders").id]) == set()


def test_upsert_of_nothing_writes_nothing(backend: Backend) -> None:
    assert backend.store.upsert() == (0, 0)


def test_datetimes_round_trip_as_utc(backend: Backend) -> None:
    record = gg.NodeRecord(
        "DQFailure", {"failure_id": "F-1"}, {"detected_at": "2024-03-01T08:05:00+02:00"}
    )
    backend.store.upsert([record])
    (node,) = backend.reader().get_nodes([record.id])
    assert node.properties["detected_at"] == datetime(2024, 3, 1, 6, 5, tzinfo=timezone.utc)
    assert node.to_dict()["properties"]["detected_at"] == "2024-03-01T06:05:00+00:00"


def test_get_nodes_ignores_unknown_and_malformed_ids(loaded: Backend) -> None:
    reader = loaded.reader()
    known = asset("raw.payments").id
    nodes = reader.get_nodes(
        [
            known,
            known,
            gg.node_id("DataAsset", {"qualified_name": "nope"}),
            "garbage",
            "Ghost:" + "0" * 32,
        ]
    )
    assert [n.id for n in nodes] == [known]
    assert reader.get_nodes([]) == []


def test_get_edges_follows_direction(loaded: Backend) -> None:
    reader = loaded.reader()
    facts = asset("marts.order_facts").id

    upstream = reader.get_edges([facts], "DERIVES_FROM", "out", limit=10)
    downstream = reader.get_edges([facts], "DERIVES_FROM", "in", limit=10)

    assert {e.target_id for e in upstream} == {
        asset("staging.orders").id,
        asset("staging.payments").id,
    }
    assert {e.source_id for e in downstream} == {asset("reporting.daily_revenue").id}
    assert all(e.source_id == facts and e.type == "DERIVES_FROM" for e in upstream)
    assert all(e.properties["lineage_id"].startswith("LineageEdge:") for e in upstream)


def test_get_edges_accepts_many_nodes_and_honours_the_limit(loaded: Backend) -> None:
    reader = loaded.reader()
    tables = [asset(name).id for name in ("raw.payments", "raw.customers", "raw.web_orders")]

    everything = reader.get_edges(tables, "OWNED_BY", "out", limit=10)
    limited = reader.get_edges(tables, "OWNED_BY", "out", limit=2)

    assert len(everything) == 3
    assert [e.id for e in everything] == sorted(e.id for e in everything)
    assert limited == everything[:2]
    assert reader.get_edges([], "OWNED_BY", "out", limit=5) == []
    assert reader.get_edges(tables, "VALIDATES", "out", limit=5) == []


def test_get_edges_rejects_bad_arguments(loaded: Backend) -> None:
    reader = loaded.reader()
    node = asset("raw.payments").id
    with pytest.raises(gg.OntologyError, match="unknown edge type"):
        reader.get_edges([node], "NOT_AN_EDGE", "out", limit=5)
    with pytest.raises(ValueError, match="direction"):
        reader.get_edges([node], "OWNED_BY", "sideways", limit=5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="limit"):
        reader.get_edges([node], "OWNED_BY", "out", limit=0)


def test_find_nodes_matches_text_ignoring_case(loaded: Backend) -> None:
    reader = loaded.reader()

    exact = reader.find_nodes("BusinessTerm", "name", "order total", limit=5)
    partial = reader.find_nodes("BusinessTerm", "name", "REVENUE", match="contains", limit=5)
    several = reader.find_nodes("DataAsset", "qualified_name", "raw.", match="contains", limit=50)

    assert [n.properties["name"] for n in exact] == ["Order Total"]
    assert [n.properties["name"] for n in partial] == ["Daily Revenue"]
    assert {n.properties["qualified_name"] for n in several} == {
        "raw.web_orders",
        "raw.payments",
        "raw.customers",
        "raw.payments.amount",
    }
    assert reader.find_nodes("BusinessTerm", "name", "order", limit=5) == []
    assert (
        len(reader.find_nodes("DataAsset", "qualified_name", ".", match="contains", limit=3)) == 3
    )


@pytest.mark.parametrize("text", ["%", "_", "\\", "100%_\\"])
def test_find_nodes_treats_wildcards_as_plain_text(backend: Backend, text: str) -> None:
    backend.store.upsert([owner("plain", name="plain name"), owner("odd", name=f"a{text}b")])
    reader = backend.reader()

    found = reader.find_nodes("Owner", "name", text, match="contains", limit=5)

    assert [n.properties["identifier"] for n in found] == ["odd"]


def test_find_nodes_rejects_bad_arguments(loaded: Backend) -> None:
    reader = loaded.reader()
    with pytest.raises(gg.OntologyError, match="unknown node type"):
        reader.find_nodes("Ghost", "name", "x", limit=5)
    with pytest.raises(gg.OntologyError, match="has no property"):
        reader.find_nodes("Owner", 'name" OR 1=1 --', "x", limit=5)
    with pytest.raises(gg.OntologyError, match="is not a string property"):
        reader.find_nodes("DQResult", "rows_failed", "3", limit=5)
    with pytest.raises(ValueError, match="match"):
        reader.find_nodes("Owner", "name", "x", match="regex", limit=5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="limit"):
        reader.find_nodes("Owner", "name", "x", limit=0)


def test_existing_node_ids(loaded: Backend) -> None:
    present = asset("raw.payments").id
    absent = asset("raw.nothing").id
    assert loaded.store.existing_node_ids([present, absent, "garbage"]) == {present}
    assert loaded.store.existing_node_ids([]) == set()


def test_edges_to_missing_nodes_do_not_create_nodes(backend: Backend) -> None:
    backend.store.upsert([table("shop.orders")], [owned_by("shop.orders", "nobody")])
    reader = backend.reader()
    assert reader.get_nodes([owner("nobody").id]) == []
    assert backend.store.existing_node_ids([owner("nobody").id]) == set()
    assert reader.find_nodes("Owner", "name", "", match="contains", limit=5) == []


def test_reader_exposes_no_write_methods(backend: Backend) -> None:
    reader = backend.reader()
    assert reader.ontology is backend.store.ontology
    public = {name for name in dir(reader) if not name.startswith("_")}
    assert public <= {"ontology", "get_nodes", "get_edges", "find_nodes", "connect", "close"}
