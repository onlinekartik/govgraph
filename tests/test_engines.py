from __future__ import annotations

import json

import pytest

import govgraph as gg
from govgraph.engines import JdbcConnection, puppygraph_schema
from govgraph.engines.puppygraph import edge_label
from govgraph.spec.model import NodeType, Ontology
from govgraph.store.ddl import create_statements, edge_columns, node_columns

JDBC = JdbcConnection("jdbc:postgresql://db:5432/meta", "graph_reader", "placeholder-value")


def test_every_node_type_maps_to_its_table(ontology: gg.Ontology) -> None:
    document = puppygraph_schema(ontology, catalog="meta", schema="graph")

    assert list(document) == ["node", "edge"]
    assert [n["label"] for n in document["node"]] == list(ontology.node_types)
    for entry in document["node"]:
        declared = ontology.node_type(entry["label"])
        source = entry["dataSourceGroup"]["externalDataSource"]
        assert (source["catalog"], source["schema"], source["table"]) == (
            "meta",
            "graph",
            declared.table,
        )
        assert source["enabled"] is True
        mapped = [(f["sourceFieldName"], f["targetFieldName"]) for f in source["mappedField"]]
        assert mapped == [(c, c) for c in node_columns(declared)]
        assert entry["id"] == [{"name": "id", "type": "STRING"}]
        assert [a["name"] for a in entry["attribute"]] == list(node_columns(declared))


def test_property_types_map_to_puppygraph_types(ontology: gg.Ontology) -> None:
    document = puppygraph_schema(ontology)
    result = next(n for n in document["node"] if n["label"] == "DQResult")
    assert {a["name"]: a["type"] for a in result["attribute"]} == {
        "id": "STRING",
        "rule_id": "STRING",
        "run_id": "STRING",
        "status": "STRING",
        "executed_at": "DATETIME",
        "rows_checked": "LONG",
        "rows_failed": "LONG",
    }
    extended = gg.load_ontology(
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
    owner = next(n for n in puppygraph_schema(extended)["node"] if n["label"] == "Owner")
    types = {a["name"]: a["type"] for a in owner["attribute"]}
    assert (types["score"], types["active"], types["joined"]) == ("DOUBLE", "BOOLEAN", "DATE")


def test_every_endpoint_pair_becomes_one_edge_entry(ontology: gg.Ontology) -> None:
    document = puppygraph_schema(ontology)
    labels = [e["label"] for e in document["edge"]]
    node_labels = {n["label"] for n in document["node"]}

    assert len(labels) == sum(len(e.endpoints) for e in ontology.edge_types.values()) == 20
    assert len(set(labels)) == len(labels)
    for entry in document["edge"]:
        assert {entry["fromNodeLabel"], entry["toNodeLabel"]} <= node_labels
        assert entry["fromKey"] == [{"name": "source_id", "type": "STRING"}]
        assert entry["toKey"] == [{"name": "target_id", "type": "STRING"}]
        assert [a["name"] for a in entry["attribute"]][:3] == ["id", "source_id", "target_id"]


def test_single_endpoint_edge_keeps_its_name(ontology: gg.Ontology) -> None:
    document = puppygraph_schema(ontology)
    (entry,) = [e for e in document["edge"] if e["label"] == "FAILED_ON"]
    source = entry["dataSourceGroup"]["externalDataSource"]

    assert (entry["fromNodeLabel"], entry["toNodeLabel"]) == ("DQFailure", "DataAsset")
    assert "type" not in entry
    assert "whereClause" not in source
    assert source["table"] == "edge_failed_on"


def test_edge_with_several_endpoints_is_split_and_aliased(ontology: gg.Ontology) -> None:
    document = puppygraph_schema(ontology)
    entries = [e for e in document["edge"] if e.get("type") == ["DERIVES_FROM"]]
    declared = ontology.edge_type("DERIVES_FROM")

    assert [e["label"] for e in entries] == [
        "DERIVES_FROM_DataAsset_DataAsset",
        "DERIVES_FROM_DataProduct_DataAsset",
    ]
    assert [e["label"] for e in entries] == [edge_label(declared, p) for p in declared.endpoints]
    product = entries[1]["dataSourceGroup"]["externalDataSource"]
    assert product["table"] == "edge_derives_from"
    assert product["whereClause"] == "source_type = 'DataProduct' AND target_type = 'DataAsset'"
    mapped = {f["sourceFieldName"] for f in product["mappedField"]}
    assert mapped <= set(edge_columns(declared))
    assert "lineage_id" in mapped
    assert entries[1]["attribute"][-1] == {"name": "lineage_id", "type": "STRING"}


def test_catalog_is_only_included_when_a_connection_is_given(ontology: gg.Ontology) -> None:
    assert "catalog" not in puppygraph_schema(ontology)
    document = puppygraph_schema(ontology, catalog="meta", jdbc=JDBC)
    assert document["catalog"] == [
        {
            "name": "meta",
            "type": "postgresql",
            "jdbc": {
                "username": "graph_reader",
                "password": "placeholder-value",
                "jdbcUri": "jdbc:postgresql://db:5432/meta",
                "driverClass": "org.postgresql.Driver",
            },
        }
    ]
    json.dumps(document)


def test_connection_repr_hides_the_password() -> None:
    assert "placeholder-value" not in repr(JDBC)
    assert "graph_reader" in repr(JDBC)


def test_unsafe_schema_name_is_refused(ontology: gg.Ontology) -> None:
    with pytest.raises(gg.StoreError, match="unsafe SQL identifier"):
        puppygraph_schema(ontology, schema="a' OR '1'='1")


def test_hand_built_ontology_with_an_unsafe_type_name_is_refused(ontology: gg.Ontology) -> None:
    hostile = "X' OR '1'='1"
    owner = ontology.node_type("Owner")
    rogue = Ontology(
        name="rogue",
        version="0.0.1",
        node_types={hostile: NodeType(hostile, owner.key, owner.properties)},
        edge_types={},
    )
    with pytest.raises(gg.OntologyError, match="cannot be used in a PuppyGraph schema"):
        puppygraph_schema(rogue)
    with pytest.raises(gg.StoreError, match="unsafe SQL identifier"):
        create_statements(rogue)
