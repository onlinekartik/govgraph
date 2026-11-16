"""The registry and the plain-file adapters. Every input file is generated here."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import govgraph as gg
from govgraph import adapters
from govgraph.adapters import registry
from govgraph.adapters.csv_json import CsvAdapter, JsonAdapter

ORDERS = {"type": "DataAsset", "key": {"qualified_name": "shop.orders"}}
TEAM = {"type": "Owner", "key": {"identifier": "team-a"}}


def write_json(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document))
    return path


def write_csv(directory: Path, name: str, *lines: str) -> None:
    (directory / name).write_text("\n".join(lines) + "\n")


def load_into_memory(adapter: adapters.Adapter, ontology: gg.Ontology) -> gg.GraphReader:
    store = gg.MemoryStore(ontology)
    gg.ingest(adapter, store)
    return store.reader()


# ---- registry ----------------------------------------------------------------------


def test_bundled_adapters_are_registered() -> None:
    assert adapters.available() == ["csv", "datahub", "dbt", "json", "openmetadata"]


def test_load_passes_options_to_the_adapter(tmp_path: Path) -> None:
    path = write_json(tmp_path / "graph.json", {"nodes": [ORDERS]})
    adapter = adapters.load("json", path=path)
    assert adapter.name == "json"
    assert [r.id for r in adapter.records()] == [
        gg.node_id("DataAsset", {"qualified_name": "shop.orders"})
    ]


def test_load_explains_what_went_wrong() -> None:
    with pytest.raises(
        gg.AdapterError, match=r"no adapter named 'nope' is installed\. Available: csv"
    ):
        adapters.load("nope")
    with pytest.raises(gg.AdapterError, match="adapter 'json' rejected its options"):
        adapters.load("json", colour="red")


class _EntryPoint:
    def __init__(self, name: str, target: Any) -> None:
        self.name = name
        self._target = target

    def load(self) -> Any:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


def test_third_party_adapters_are_found_through_entry_points(
    monkeypatch: pytest.MonkeyPatch, ontology: gg.Ontology
) -> None:
    class Catalogue:
        name = "catalogue"

        def __init__(self, team: str) -> None:
            self._team = team

        def records(self) -> Iterator[gg.Record]:
            yield gg.NodeRecord("Owner", {"identifier": self._team})

    def fake_entry_points(group: str) -> list[_EntryPoint]:
        assert group == "govgraph.adapters"
        return [
            _EntryPoint("catalogue", Catalogue),
            _EntryPoint("broken", ImportError("missing dependency")),
            _EntryPoint("not_an_adapter", dict),
        ]

    monkeypatch.setattr(registry, "entry_points", fake_entry_points)

    assert adapters.available() == ["broken", "catalogue", "not_an_adapter"]
    reader = load_into_memory(adapters.load("catalogue", team="team-z"), ontology)
    assert reader.get_nodes([gg.node_id("Owner", {"identifier": "team-z"})])
    with pytest.raises(gg.AdapterError, match="could not be imported: missing dependency"):
        adapters.load("broken")
    with pytest.raises(gg.AdapterError, match=r"does not have a name and a records\(\) method"):
        adapters.load("not_an_adapter")


# ---- JSON --------------------------------------------------------------------------


def test_json_adapter_reads_nodes_and_edges(tmp_path: Path, ontology: gg.Ontology) -> None:
    path = write_json(
        tmp_path / "graph.json",
        {
            "nodes": [
                {**ORDERS, "properties": {"kind": "table"}},
                {**TEAM, "properties": {"name": "Team A", "kind": "team"}},
            ],
            "edges": [{"type": "OWNED_BY", "source": ORDERS, "target": TEAM}],
        },
    )
    reader = load_into_memory(JsonAdapter(path), ontology)

    result = gg.Toolkit(reader).get_asset_context(
        gg.node_id("DataAsset", {"qualified_name": "shop.orders"})
    )
    assert [c.statement for c in result.claims] == [
        "DataAsset 'shop.orders' is a table",
        "DataAsset 'shop.orders' is owned by Owner 'Team A'",
    ]


def test_json_adapter_accepts_an_empty_document(tmp_path: Path) -> None:
    assert list(JsonAdapter(write_json(tmp_path / "empty.json", {})).records()) == []


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "the document must be an object"),
        ({"nodes": ["x"]}, r"nodes\[0\] must be an object"),
        ({"nodes": [{"key": {}}]}, r"nodes\[0\] needs a 'type'"),
        ({"nodes": [{"type": "Owner"}]}, r"nodes\[0\]\.key must be an object"),
        ({"nodes": [{**TEAM, "properties": 3}]}, r"nodes\[0\]\.properties must be an object"),
        ({"edges": [{"source": ORDERS, "target": TEAM}]}, r"edges\[0\] needs a 'type'"),
        ({"edges": [{"type": "OWNED_BY", "target": TEAM}]}, r"edges\[0\]\.source must be"),
        (
            {"edges": [{"type": "OWNED_BY", "source": ORDERS, "target": {"key": {}}}]},
            r"edges\[0\]\.target needs a 'type'",
        ),
    ],
)
def test_json_adapter_rejects_malformed_documents(
    tmp_path: Path, document: Any, message: str
) -> None:
    with pytest.raises(gg.AdapterError, match=message):
        list(JsonAdapter(write_json(tmp_path / "bad.json", document)).records())


def test_json_adapter_reports_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(gg.AdapterError, match="cannot read JSON file"):
        list(JsonAdapter(tmp_path / "absent.json").records())
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(gg.AdapterError, match="is not valid JSON"):
        list(JsonAdapter(broken).records())


# ---- CSV ---------------------------------------------------------------------------


def test_csv_adapter_reads_a_directory(tmp_path: Path, ontology: gg.Ontology) -> None:
    write_csv(
        tmp_path,
        "DataAsset.csv",
        "qualified_name,name,kind,parent_qualified_name",
        "shop.orders,orders,table,",
        "shop.orders.total,total,column,shop.orders",
    )
    write_csv(tmp_path, "Owner.csv", "identifier,name,kind", "team-a,Team A,team")
    write_csv(tmp_path, "DQResult.csv", "rule_id,run_id,status,rows_failed", "r1,run-1,fail,12")
    write_csv(tmp_path, "DQRule.csv", "rule_id,name", '"r1","Total, not negative"')
    write_csv(
        tmp_path,
        "OWNED_BY.csv",
        "source_type,source.qualified_name,source.rule_id,target_type,target.identifier",
        "DataAsset,shop.orders,,Owner,team-a",
        "DQRule,,r1,Owner,team-a",
    )
    write_csv(
        tmp_path, "VALIDATES.csv", "source.rule_id,target.qualified_name", "r1,shop.orders.total"
    )
    write_csv(
        tmp_path,
        "RESULT_OF.csv",
        "source.rule_id,source.run_id,target.rule_id",
        "r1,run-1,r1",
    )

    adapter = CsvAdapter(tmp_path, ontology)
    records = list(adapter.records())
    reader = load_into_memory(adapter, ontology)

    kinds = [type(r).__name__ for r in records]
    assert kinds == sorted(kinds, reverse=True), "nodes come before edges"
    (result,) = reader.get_nodes([gg.node_id("DQResult", {"rule_id": "r1", "run_id": "run-1"})])
    assert result.properties["rows_failed"] == 12
    (rule,) = reader.get_nodes([gg.node_id("DQRule", {"rule_id": "r1"})])
    assert rule.properties["name"] == "Total, not negative"
    column = gg.node_id("DataAsset", {"qualified_name": "shop.orders.total"})
    said = [c.statement for c in gg.Toolkit(reader).get_asset_context(column).claims]
    assert "DQRule 'Total, not negative' validates DataAsset 'shop.orders.total'" in said
    assert "DataAsset 'shop.orders' is owned by Owner 'Team A'" in said
    owned = reader.get_edges(
        [gg.node_id("Owner", {"identifier": "team-a"})], "OWNED_BY", "in", limit=9
    )
    assert len(owned) == 2


def test_csv_adapter_converts_cells_to_declared_types(tmp_path: Path) -> None:
    ontology = gg.load_ontology(
        [
            {
                "ontology": {"name": "ext", "version": "0.1.0"},
                "node_types": {
                    "Owner": {
                        "properties": {
                            "headcount": {"type": "integer"},
                            "score": {"type": "number"},
                            "active": {"type": "boolean"},
                            "since": {"type": "date"},
                        }
                    }
                },
            }
        ]
    )
    write_csv(
        tmp_path,
        "Owner.csv",
        "identifier,headcount,score,active,since",
        "a,12,0.5,Yes,2024-03-01",
        "b,,,false,",
    )
    reader = load_into_memory(CsvAdapter(tmp_path, ontology), ontology)
    a, b = (gg.node_id("Owner", {"identifier": i}) for i in "ab")
    by_id = {n.id: n.to_dict()["properties"] for n in reader.get_nodes([a, b])}
    assert by_id[a] == {
        "identifier": "a",
        "headcount": 12,
        "score": 0.5,
        "active": True,
        "since": "2024-03-01",
    }
    assert by_id[b] == {"identifier": "b", "active": False}


def test_csv_adapter_uses_the_core_ontology_by_default(tmp_path: Path) -> None:
    write_csv(tmp_path, "Owner.csv", "\ufeffidentifier,name", "a,A")
    (record,) = CsvAdapter(tmp_path).records()
    assert record == gg.NodeRecord("Owner", {"identifier": "a"}, {"name": "A"})


@pytest.mark.parametrize(
    ("name", "lines", "message"),
    [
        ("Ghost.csv", ["a"], "no node or edge type is named after: Ghost.csv"),
        ("Owner.csv", ["identifier,colour", "a,red"], "Owner.csv line 2: unknown column 'colour'"),
        (
            "DQResult.csv",
            ["rule_id,run_id,rows_failed", "r,1,many"],
            "DQResult.csv line 2, column 'rows_failed': 'many' is not a valid integer",
        ),
        (
            "OWNED_BY.csv",
            ["source.qualified_name,target.identifier", "shop.orders,a"],
            "OWNED_BY.csv line 2: 'source_type' must name a node type",
        ),
        (
            "OWNED_BY.csv",
            ["source_type,target_type", "DataAsset,Ghost"],
            "OWNED_BY.csv line 2: 'target_type' must name a node type",
        ),
        (
            "VALIDATES.csv",
            ["source.rule_id,target.qualified_name,weight", "r,a,3"],
            "VALIDATES.csv line 2: unknown column 'weight'",
        ),
    ],
)
def test_csv_adapter_explains_bad_input(
    tmp_path: Path, name: str, lines: list[str], message: str
) -> None:
    write_csv(tmp_path, name, *lines)
    with pytest.raises(gg.AdapterError) as caught:
        list(CsvAdapter(tmp_path).records())
    assert message in str(caught.value)


def test_csv_adapter_rejects_bad_booleans_and_missing_directories(tmp_path: Path) -> None:
    ontology = gg.load_ontology(
        [
            {
                "ontology": {"name": "ext", "version": "0.1.0"},
                "node_types": {"Owner": {"properties": {"active": {"type": "boolean"}}}},
            }
        ]
    )
    write_csv(tmp_path, "Owner.csv", "identifier,active", "a,maybe")
    with pytest.raises(gg.AdapterError, match="'maybe' is not a valid boolean"):
        list(CsvAdapter(tmp_path, ontology).records())
    with pytest.raises(gg.AdapterError, match="is not a directory"):
        list(CsvAdapter(tmp_path / "absent").records())
