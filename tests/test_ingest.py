from __future__ import annotations

from collections.abc import Iterator

import pytest

import govgraph as gg
from govgraph.adapters import Adapter
from govgraph.samples import asset, online_store
from tests.conftest import Backend

TABLE = gg.NodeRecord("DataAsset", {"qualified_name": "shop.orders"}, {"kind": "table"})
OWNER = gg.NodeRecord("Owner", {"identifier": "team-a"}, {"name": "Team A"})
OWNS = gg.EdgeRecord("OWNED_BY", TABLE.ref(), OWNER.ref())


class Fixed:
    name = "fixed"

    def __init__(self, *records: object) -> None:
        self._records = records

    def records(self) -> Iterator[gg.Record]:
        return iter(self._records)  # type: ignore[arg-type]


def test_sample_adapter_satisfies_the_protocol() -> None:
    assert isinstance(online_store(), Adapter)
    assert isinstance(Fixed(), Adapter)
    assert not isinstance([TABLE], Adapter)


def test_ingest_writes_an_adapter_and_reports_counts(backend: Backend) -> None:
    report = gg.ingest(online_store(), backend.store)

    assert report.source == "online_store_sample"
    assert report.rejected == ()
    assert report.nodes == 48
    assert report.edges == 61
    reader = backend.reader()
    assert (
        len(reader.find_nodes("DataAsset", "qualified_name", ".", match="contains", limit=99)) == 14
    )


def test_ingest_twice_changes_nothing(backend: Backend) -> None:
    first = gg.ingest(online_store(), backend.store)
    reader = backend.reader()
    before = reader.find_nodes("DataAsset", "qualified_name", ".", match="contains", limit=99)
    second = gg.ingest(online_store(), backend.store)
    after = reader.find_nodes("DataAsset", "qualified_name", ".", match="contains", limit=99)
    assert (first.nodes, first.edges) == (second.nodes, second.edges)
    assert before == after


def test_ingest_accepts_plain_iterables(backend: Backend) -> None:
    report = gg.ingest([TABLE, OWNER, OWNS], backend.store)
    assert (report.source, report.nodes, report.edges) == ("records", 2, 1)


def test_ingest_rejects_everything_if_anything_is_invalid(backend: Backend) -> None:
    bad = gg.NodeRecord("Owner", {"identifier": "b"}, {"kind": "robot"})
    with pytest.raises(gg.IngestError) as caught:
        gg.ingest(Fixed(TABLE, bad, "not a record"), backend.store)

    assert "fixed: 2 record(s) rejected" in str(caught.value)
    assert len(caught.value.problems) == 2
    assert caught.value.problems[1] == "not a record: str"
    assert backend.store.existing_node_ids([TABLE.id]) == set()


def test_ingest_can_skip_invalid_records(backend: Backend) -> None:
    bad_node = gg.NodeRecord("Owner", {"identifier": "b"}, {"kind": "robot"})
    bad_edge = gg.EdgeRecord("OWNED_BY", OWNER.ref(), TABLE.ref())
    report = gg.ingest([TABLE, OWNER, OWNS, bad_node, bad_edge], backend.store, on_error="skip")

    assert (report.nodes, report.edges) == (2, 1)
    assert len(report.rejected) == 2
    assert backend.store.existing_node_ids([TABLE.id, OWNER.id, bad_node.id]) == {
        TABLE.id,
        OWNER.id,
    }


def test_ingest_rejects_edges_to_nodes_that_do_not_exist(backend: Backend) -> None:
    with pytest.raises(gg.IngestError, match=r"edge OWNED_BY: target Owner .* not found"):
        gg.ingest([TABLE, OWNS], backend.store)

    report = gg.ingest([TABLE, OWNS], backend.store, on_error="skip")
    assert (report.nodes, report.edges) == (1, 0)
    assert report.rejected == ("edge OWNED_BY: target Owner {'identifier': 'team-a'} not found",)


def test_ingest_names_both_missing_endpoints(backend: Backend) -> None:
    with pytest.raises(gg.IngestError, match=r"source DataAsset .* and target Owner .* not found"):
        gg.ingest([OWNS], backend.store)


def test_ingest_accepts_edges_to_nodes_already_in_the_store(backend: Backend) -> None:
    gg.ingest([TABLE, OWNER], backend.store)
    report = gg.ingest([OWNS], backend.store)
    assert (report.nodes, report.edges) == (0, 1)
    edges = backend.reader().get_edges([TABLE.id], "OWNED_BY", "out", limit=5)
    assert [e.target_id for e in edges] == [OWNER.id]


def test_ingest_merges_repeated_nodes_within_a_batch(backend: Backend) -> None:
    again = gg.NodeRecord(
        "DataAsset", {"qualified_name": "shop.orders"}, {"name": "orders", "kind": None}
    )
    report = gg.ingest([TABLE, again], backend.store)
    assert report.nodes == 1
    (node,) = backend.reader().get_nodes([TABLE.id])
    assert node.properties == {"qualified_name": "shop.orders", "kind": "table", "name": "orders"}


def test_long_rejection_lists_are_summarised(backend: Backend) -> None:
    bad = [gg.NodeRecord("Ghost", {"name": str(i)}) for i in range(8)]
    with pytest.raises(gg.IngestError, match=r"8 record\(s\) rejected: .* \(and 3 more\)"):
        gg.ingest(bad, backend.store)


def test_sample_dataset_is_internally_consistent(ontology: gg.Ontology) -> None:
    records = list(online_store().records())
    nodes = {r.id for r in records if isinstance(r, gg.NodeRecord)}
    edges = [r for r in records if isinstance(r, gg.EdgeRecord)]
    assert all(e.source.id in nodes and e.target.id in nodes for e in edges)
    assert asset("marts.order_facts.order_total").id in nodes
    assert {e.type for e in edges} == set(ontology.edge_types)
    assert {r.type for r in records if isinstance(r, gg.NodeRecord)} == set(ontology.node_types)
