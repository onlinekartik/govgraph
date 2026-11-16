from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import govgraph as gg
from govgraph.ids import ID_PATTERN, id_type, is_id


def test_node_id_is_deterministic_and_typed() -> None:
    first = gg.node_id("DataAsset", {"qualified_name": "shop.orders"})
    assert first == gg.node_id("DataAsset", {"qualified_name": "shop.orders"})
    assert first.startswith("DataAsset:")
    assert is_id(first)
    assert id_type(first) == "DataAsset"
    assert first != gg.node_id("DataAsset", {"qualified_name": "shop.order"})
    assert (
        first.split(":")[1] != gg.node_id("Owner", {"qualified_name": "shop.orders"}).split(":")[1]
    )


def test_node_id_is_stable_across_releases() -> None:
    # A change here means every stored ID changes. Do not update this value casually.
    assert gg.node_id("DataAsset", {"qualified_name": "shop.orders.total"}) == (
        "DataAsset:3244387c6f96ca69e089da8246e525e2"
    )


def test_node_id_ignores_key_order_but_not_value_types() -> None:
    forward = gg.node_id("DQResult", {"rule_id": "r", "run_id": "1"})
    backward = gg.node_id("DQResult", {"run_id": "1", "rule_id": "r"})
    assert forward == backward
    assert forward != gg.node_id("DQResult", {"rule_id": "r", "run_id": 1})


@pytest.mark.parametrize(
    "key",
    [{}, {"name": ""}, {"name": None}, {"name": 1.5}, {"name": True}, {"name": ["a"]}],
)
def test_node_id_rejects_unusable_keys(key: dict[str, object]) -> None:
    with pytest.raises(gg.RecordError):
        gg.node_id("Owner", key)  # type: ignore[arg-type]


def test_edge_id_depends_on_type_and_direction() -> None:
    a = gg.node_id("Owner", {"identifier": "a"})
    b = gg.node_id("Owner", {"identifier": "b"})
    assert gg.edge_id("OWNED_BY", a, b) == gg.edge_id("OWNED_BY", a, b)
    assert gg.edge_id("OWNED_BY", a, b) != gg.edge_id("OWNED_BY", b, a)
    assert gg.edge_id("OWNED_BY", a, b) != gg.edge_id("DEFINES", a, b)
    assert id_type(gg.edge_id("OWNED_BY", a, b)) == "OWNED_BY"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "DataAsset",
        "DataAsset:xyz",
        "DataAsset:" + "A" * 32,
        "DataAsset:" + "0" * 32 + "\n",
        "'; DROP TABLE x;--",
        7,
        None,
    ],
)
def test_is_id_rejects_malformed_values(value: object) -> None:
    assert not is_id(value)
    if isinstance(value, str):
        with pytest.raises(ValueError, match="not a govgraph ID"):
            id_type(value)


def test_id_pattern_matches_generated_ids() -> None:
    import re

    assert re.match(ID_PATTERN, gg.node_id("Owner", {"identifier": "a"}))


def test_records_derive_ids_and_refs() -> None:
    record = gg.NodeRecord("Owner", {"identifier": "a"}, {"name": "A"})
    assert record.id == gg.node_id("Owner", {"identifier": "a"})
    assert record.ref() == gg.NodeRef("Owner", {"identifier": "a"})
    assert record.ref().id == record.id
    edge = gg.EdgeRecord("OWNED_BY", gg.NodeRef("DataAsset", {"qualified_name": "t"}), record.ref())
    assert edge.id == gg.edge_id("OWNED_BY", edge.source.id, record.id)


def test_nodes_and_edges_serialise_to_plain_json_values() -> None:
    node = gg.Node(
        "DQFailure:" + "0" * 32,
        "DQFailure",
        {"detected_at": datetime(2024, 3, 1, 6, 5, tzinfo=timezone.utc), "day": date(2024, 3, 1)},
    )
    assert node.to_dict() == {
        "id": node.id,
        "type": "DQFailure",
        "properties": {"day": "2024-03-01", "detected_at": "2024-03-01T06:05:00+00:00"},
    }
    edge = gg.Edge("OWNED_BY:" + "1" * 32, "OWNED_BY", "a", "b")
    assert edge.to_dict() == {
        "id": edge.id,
        "type": "OWNED_BY",
        "source_id": "a",
        "target_id": "b",
        "properties": {},
    }
