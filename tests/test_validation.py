from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

import govgraph as gg
from govgraph.spec.model import Property
from govgraph.validation import coerce, edge_problems, node_problems, to_edge, to_node

UTC = timezone.utc


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("string", "text", "text"),
        ("boolean", True, True),
        ("integer", 7, 7),
        ("number", 7, 7.0),
        ("number", 1.5, 1.5),
        ("date", date(2024, 3, 1), date(2024, 3, 1)),
        ("date", "2024-03-01", date(2024, 3, 1)),
        ("datetime", "2024-03-01T06:00:00Z", datetime(2024, 3, 1, 6, tzinfo=UTC)),
        ("datetime", "2024-03-01T08:00:00+02:00", datetime(2024, 3, 1, 6, tzinfo=UTC)),
        ("datetime", "2024-03-01T06:00:00", datetime(2024, 3, 1, 6, tzinfo=UTC)),
        ("datetime", datetime(2024, 3, 1, 6), datetime(2024, 3, 1, 6, tzinfo=UTC)),
        (
            "datetime",
            datetime(2024, 3, 1, 8, tzinfo=timezone(timedelta(hours=2))),
            datetime(2024, 3, 1, 6, tzinfo=UTC),
        ),
    ],
)
def test_coerce_accepts_and_normalises(kind: Any, value: Any, expected: Any) -> None:
    result = coerce(Property("p", kind), value)
    assert result == expected
    assert type(result) is type(expected)
    if isinstance(result, datetime):
        assert result.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("string", 1),
        ("boolean", 1),
        ("boolean", "true"),
        ("integer", True),
        ("integer", 1.0),
        ("integer", "1"),
        ("number", True),
        ("number", "1.5"),
        ("date", datetime(2024, 3, 1)),
        ("date", "01/03/2024"),
        ("date", 20240301),
        ("datetime", "yesterday"),
        ("datetime", 1709272800),
        ("datetime", date(2024, 3, 1)),
    ],
)
def test_coerce_rejects_wrong_types(kind: Any, value: Any) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        coerce(Property("p", kind), value)


def test_coerce_enforces_enums() -> None:
    prop = Property("status", "string", enum=("open", "closed"))
    assert coerce(prop, "open") == "open"
    with pytest.raises(ValueError, match="expected one of open, closed"):
        coerce(prop, "ajar")


def test_valid_node_becomes_stored_form(ontology: gg.Ontology) -> None:
    record = gg.NodeRecord(
        "DQFailure",
        {"failure_id": "F-1"},
        {"severity": "high", "detected_at": "2024-03-01T06:05:00Z", "summary": None},
    )
    node = to_node(ontology, record)
    assert node.id == record.id
    assert node.properties == {
        "failure_id": "F-1",
        "severity": "high",
        "detected_at": datetime(2024, 3, 1, 6, 5, tzinfo=UTC),
    }


@pytest.mark.parametrize(
    ("record", "fragment"),
    [
        (gg.NodeRecord("Ghost", {"name": "x"}), "unknown node type 'Ghost'"),
        (gg.NodeRecord("Owner", {}), "key must have exactly the fields identifier, got none"),
        (gg.NodeRecord("Owner", {"name": "x"}), "key must have exactly the fields identifier"),
        (gg.NodeRecord("Owner", {"identifier": "x"}, {"age": 3}), "unknown property 'age'"),
        (gg.NodeRecord("Owner", {"identifier": "x"}, {"kind": "robot"}), "expected one of"),
        (gg.NodeRecord("Owner", {"identifier": 5}), "property 'identifier': expected a string"),
        (gg.NodeRecord("Owner", {"identifier": ""}), "key field 'identifier' cannot be empty"),
        (
            gg.NodeRecord("Owner", {"identifier": "x"}, {"identifier": "y"}),
            "property 'identifier' disagrees with the key",
        ),
        (
            gg.NodeRecord("DQResult", {"rule_id": "r"}),
            "key must have exactly the fields rule_id, run_id, got rule_id",
        ),
    ],
)
def test_invalid_nodes_are_explained(
    ontology: gg.Ontology, record: gg.NodeRecord, fragment: str
) -> None:
    node, problems = node_problems(ontology, record)
    assert node is None
    assert any(fragment in problem for problem in problems)
    with pytest.raises(gg.RecordError) as caught:
        to_node(ontology, record)
    assert caught.value.problems == tuple(problems)


def test_valid_edge_becomes_stored_form(ontology: gg.Ontology) -> None:
    source = gg.NodeRef("DataAsset", {"qualified_name": "a"})
    target = gg.NodeRef("DataAsset", {"qualified_name": "b"})
    edge = to_edge(ontology, gg.EdgeRecord("DERIVES_FROM", source, target, {"lineage_id": "x"}))
    assert (edge.source_id, edge.target_id) == (source.id, target.id)
    assert edge.id == gg.edge_id("DERIVES_FROM", source.id, target.id)
    assert edge.properties == {"lineage_id": "x"}


@pytest.mark.parametrize(
    ("record", "fragment"),
    [
        (
            gg.EdgeRecord(
                "GHOST",
                gg.NodeRef("Owner", {"identifier": "a"}),
                gg.NodeRef("Owner", {"identifier": "b"}),
            ),
            "unknown edge type 'GHOST'",
        ),
        (
            gg.EdgeRecord(
                "OWNED_BY",
                gg.NodeRef("Owner", {"identifier": "a"}),
                gg.NodeRef("DataAsset", {"qualified_name": "b"}),
            ),
            "Owner -> DataAsset is not an allowed endpoint pair",
        ),
        (
            gg.EdgeRecord(
                "OWNED_BY",
                gg.NodeRef("DataAsset", {"name": "a"}),
                gg.NodeRef("Owner", {"identifier": "b"}),
            ),
            "source key must have exactly the fields qualified_name",
        ),
        (
            gg.EdgeRecord(
                "OWNED_BY",
                gg.NodeRef("DataAsset", {"qualified_name": "a"}),
                gg.NodeRef("Owner", {"identifier": "b"}),
                {"since": "2024"},
            ),
            "unknown property 'since'",
        ),
        (
            gg.EdgeRecord(
                "OWNED_BY",
                gg.NodeRef("DataAsset", {"qualified_name": ""}),
                gg.NodeRef("Owner", {"identifier": "b"}),
            ),
            "key field 'qualified_name' cannot be empty",
        ),
    ],
)
def test_invalid_edges_are_explained(
    ontology: gg.Ontology, record: gg.EdgeRecord, fragment: str
) -> None:
    edge, problems = edge_problems(ontology, record)
    assert edge is None
    assert any(fragment in problem for problem in problems)
    with pytest.raises(gg.RecordError):
        to_edge(ontology, record)


def test_required_properties_must_be_present() -> None:
    ontology = gg.load_ontology(
        [
            {
                "ontology": {"name": "ext", "version": "0.1.0"},
                "node_types": {
                    "Owner": {"properties": {"cost_centre": {"type": "string", "required": True}}}
                },
            }
        ]
    )
    _, problems = node_problems(ontology, gg.NodeRecord("Owner", {"identifier": "x"}))
    assert problems == ["node Owner: required property 'cost_centre' is missing"]
