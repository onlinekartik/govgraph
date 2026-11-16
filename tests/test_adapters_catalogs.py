"""dbt, OpenMetadata and DataHub adapters, run on small exports generated here."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import govgraph as gg
from govgraph.adapters.datahub import DataHubAdapter, dataset_name, field_name
from govgraph.adapters.dbt import DbtAdapter
from govgraph.adapters.openmetadata import OpenMetadataAdapter


def asset(qualified_name: str) -> str:
    return gg.node_id("DataAsset", {"qualified_name": qualified_name})


def write(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document))
    return path


def load(adapter: Any, ontology: gg.Ontology) -> tuple[gg.GraphReader, gg.IngestReport]:
    """Ingest strictly: any record that breaks the ontology or dangles fails the test."""
    store = gg.MemoryStore(ontology)
    report = gg.ingest(adapter, store)
    return store.reader(), report


def said(result: gg.ToolResult) -> list[str]:
    return [claim.statement for claim in result.claims]


def node(reader: gg.GraphReader, node_type: str, **key: str) -> dict[str, Any]:
    (found,) = reader.get_nodes([gg.node_id(node_type, key)])
    return dict(found.properties)


# ---- dbt ---------------------------------------------------------------------------

NOT_NULL = "test.shop.not_null_order_facts_total.1a2b"
RELATIONSHIP = "test.shop.relationships_order_facts_stg_orders.3c4d"
CUSTOM = "test.shop.totals_add_up"


def dbt_manifest() -> dict[str, Any]:
    def model(name: str, schema: str, depends_on: list[str], **extra: Any) -> dict[str, Any]:
        return {
            "resource_type": "model",
            "unique_id": f"model.shop.{name}",
            "database": "analytics",
            "schema": schema,
            "name": name,
            "alias": name,
            "description": f"The {name} model.",
            "columns": {},
            "depends_on": {"nodes": depends_on, "macros": ["macro.shop.cents_to_units"]},
            "config": {"materialized": "table"},
            **extra,
        }

    def test(
        unique_id: str, kind: str | None, depends_on: list[str], **extra: Any
    ) -> dict[str, Any]:
        return {
            "resource_type": "test",
            "unique_id": unique_id,
            "name": unique_id.split(".")[2],
            "depends_on": {"nodes": depends_on},
            "config": {"severity": "ERROR"},
            **({"test_metadata": {"name": kind, "kwargs": {}}} if kind else {}),
            **extra,
        }

    return {
        "metadata": {"project_name": "shop"},
        "nodes": {
            "model.shop.stg_orders": model(
                "stg_orders",
                "staging",
                ["source.shop.raw.web_orders"],
                config={"materialized": "view", "meta": {"owner": "platform-team"}},
            ),
            "model.shop.order_facts": model(
                "order_facts",
                "marts",
                ["model.shop.stg_orders", "seed.shop.currencies"],
                columns={
                    "total": {
                        "name": "total",
                        "description": "Order total.",
                        "data_type": "numeric",
                    },
                    "order_id": {"name": "order_id"},
                },
                group="finance",
            ),
            "seed.shop.currencies": {
                "resource_type": "seed",
                "unique_id": "seed.shop.currencies",
                "database": "analytics",
                "schema": "seeds",
                "name": "currencies",
                "meta": {"owner": "platform-team"},
            },
            NOT_NULL: test(
                NOT_NULL,
                "not_null",
                ["model.shop.order_facts"],
                column_name="total",
                attached_node="model.shop.order_facts",
            ),
            RELATIONSHIP: test(
                RELATIONSHIP,
                "relationships",
                ["model.shop.stg_orders", "model.shop.order_facts"],
                column_name="customer_id",
                config={"severity": "warn"},
            ),
            CUSTOM: test(CUSTOM, None, ["model.shop.order_facts"], config={}),
            "operation.shop.hook": {
                "resource_type": "operation",
                "unique_id": "operation.shop.hook",
            },
        },
        "sources": {
            "source.shop.raw.web_orders": {
                "resource_type": "source",
                "unique_id": "source.shop.raw.web_orders",
                "database": "landing",
                "schema": "raw",
                "name": "web_orders",
                "identifier": "web_orders_v2",
                "description": "",
            }
        },
        "groups": {"group.shop.finance": {"name": "finance", "owner": {"name": "Finance Team"}}},
        "exposures": {
            "exposure.shop.revenue": {
                "name": "revenue",
                "label": "Revenue Report",
                "description": "Weekly revenue.",
                "owner": {"name": "Finance Team", "email": "not-copied@example.invalid"},
                "depends_on": {"nodes": ["model.shop.order_facts", "metric.shop.unknown"]},
            },
            "exposure.shop.bare": {"name": "bare", "depends_on": {}},
        },
    }


def dbt_run_results() -> dict[str, Any]:
    return {
        "metadata": {"invocation_id": "inv-7", "generated_at": "2024-03-02T04:00:00.123456Z"},
        "results": [
            {"unique_id": NOT_NULL, "status": "fail", "failures": 12, "message": "Got 12 results"},
            {"unique_id": RELATIONSHIP, "status": "pass", "failures": 0},
            {"unique_id": CUSTOM, "status": "skipped"},
            {"unique_id": "model.shop.order_facts", "status": "success"},
        ],
    }


def test_dbt_manifest_becomes_assets_lineage_rules_and_products(
    tmp_path: Path, ontology: gg.Ontology
) -> None:
    reader, report = load(DbtAdapter(write(tmp_path / "manifest.json", dbt_manifest())), ontology)
    kit = gg.Toolkit(reader)
    facts = "analytics.marts.order_facts"

    assert report.rejected == ()
    assert (
        node(reader, "DataAsset", qualified_name="analytics.staging.stg_orders")["kind"] == "view"
    )
    assert node(reader, "DataAsset", qualified_name="landing.raw.web_orders_v2") == {
        "qualified_name": "landing.raw.web_orders_v2",
        "name": "web_orders",
        "kind": "table",
    }
    assert node(reader, "DataAsset", qualified_name=f"{facts}.total") == {
        "qualified_name": f"{facts}.total",
        "name": "total",
        "kind": "column",
        "parent_qualified_name": facts,
        "data_type": "numeric",
        "description": "Order total.",
    }
    assert node(reader, "DQRule", rule_id=NOT_NULL) == {
        "rule_id": NOT_NULL,
        "name": "not_null_order_facts_total",
        "dimension": "completeness",
        "severity": "high",
        "expression": "not_null(total)",
    }
    assert node(reader, "DQRule", rule_id=RELATIONSHIP)["severity"] == "medium"
    assert node(reader, "DQRule", rule_id=CUSTOM) == {"rule_id": CUSTOM, "name": "totals_add_up"}

    context = said(kit.get_asset_context(asset(facts)))
    for expected in (
        f"DataAsset '{facts}' is owned by Owner 'Finance Team'",
        f"DQRule 'not_null_order_facts_total' validates DataAsset '{facts}'",
        f"DQRule 'relationships_order_facts_stg_orders' validates DataAsset '{facts}'",
        f"DQRule 'totals_add_up' validates DataAsset '{facts}'",
        f"DataAsset '{facts}' derives from DataAsset 'analytics.staging.stg_orders', "
        "as recorded by LineageEdge 'model.shop.order_facts'",
        f"DataAsset '{facts}' derives from DataAsset 'analytics.seeds.currencies', "
        "as recorded by LineageEdge 'model.shop.order_facts'",
        f"DataProduct 'Revenue Report' derives from DataAsset '{facts}'",
    ):
        assert expected in context
    column = said(kit.get_asset_context(asset(f"{facts}.total")))
    assert f"DQRule 'not_null_order_facts_total' validates DataAsset '{facts}.total'" in column
    # The relationship test names a column that is not documented, so only relations are linked.
    assert not reader.get_nodes([asset(f"{facts}.customer_id")])

    affected = said(kit.impact_of(asset("landing.raw.web_orders_v2"), depth=3))
    assert any("affects DataProduct 'Revenue Report' (3 steps" in s for s in affected)
    owners = reader.find_nodes("Owner", "name", "", match="contains", limit=9)
    assert sorted(o.properties["identifier"] for o in owners) == ["Finance Team", "platform-team"]
    assert "example.invalid" not in json.dumps([o.to_dict() for o in owners])


def test_dbt_run_results_become_results_and_failures(tmp_path: Path, ontology: gg.Ontology) -> None:
    adapter = DbtAdapter(
        write(tmp_path / "manifest.json", dbt_manifest()),
        write(tmp_path / "run_results.json", dbt_run_results()),
    )
    reader, _ = load(adapter, ontology)
    facts = "analytics.marts.order_facts"

    assert node(reader, "DQResult", rule_id=NOT_NULL, run_id="inv-7") == {
        "rule_id": NOT_NULL,
        "run_id": "inv-7",
        "status": "fail",
        "executed_at": datetime(2024, 3, 2, 4, 0, 0, 123456, tzinfo=timezone.utc),
        "rows_failed": 12,
    }
    assert node(reader, "DQResult", rule_id=RELATIONSHIP, run_id="inv-7")["status"] == "pass"
    assert not reader.get_nodes([gg.node_id("DQResult", {"rule_id": CUSTOM, "run_id": "inv-7"})])
    failures = reader.find_nodes("DQFailure", "failure_id", "inv-7", match="contains", limit=9)
    assert [f.properties["failure_id"] for f in failures] == [f"inv-7:{NOT_NULL}"]

    explanation = said(gg.Toolkit(reader).explain_failure(failures[0].id))
    assert explanation[0] == (
        f"DQFailure 'inv-7:{NOT_NULL}' (high, open, detected 2024-03-02T04:00:00.123456+00:00): "
        "Got 12 results"
    )
    assert f"DQFailure 'inv-7:{NOT_NULL}' failed on DataAsset '{facts}'" in explanation
    assert f"DQFailure 'inv-7:{NOT_NULL}' failed on DataAsset '{facts}.total'" in explanation
    assert f"DataAsset '{facts}' is owned by Owner 'Finance Team'" in explanation
    downstream = said(gg.Toolkit(reader).upstream_causes(asset(facts)))
    assert any(s.startswith("Possible cause") and "the asset itself" in s for s in downstream)


@pytest.mark.parametrize(
    ("manifest", "results", "message"),
    [
        ([], None, "the dbt manifest has no 'nodes' section"),
        ({"nodes": []}, None, "the dbt manifest has no 'nodes' section"),
        ({"nodes": {}}, [], "the dbt run results have no 'results' list"),
        ({"nodes": {}}, {"results": [], "metadata": {}}, "no metadata.invocation_id"),
    ],
)
def test_dbt_adapter_rejects_files_that_are_not_dbt_artifacts(
    tmp_path: Path, manifest: Any, results: Any, message: str
) -> None:
    run_results = None if results is None else write(tmp_path / "run_results.json", results)
    adapter = DbtAdapter(write(tmp_path / "manifest.json", manifest), run_results)
    with pytest.raises(gg.AdapterError, match=message):
        list(adapter.records())


# ---- OpenMetadata ------------------------------------------------------------------

OM_ORDERS = "warehouse.shop.public.orders"
OM_FACTS = "warehouse.shop.marts.order_facts"


def openmetadata_export() -> dict[str, Any]:
    glossary_tag = {"tagFQN": "Sales.Order Total", "source": "Glossary", "labelType": "Manual"}
    return {
        "tables": [
            {
                "id": "t-1",
                "name": "orders",
                "fullyQualifiedName": OM_ORDERS,
                "tableType": "Regular",
                "description": "Orders as placed.",
                "owners": [
                    {"id": "u-1", "type": "user", "name": "avery", "displayName": "Avery Example"},
                    {"id": "g-1", "type": "team", "name": "orders"},
                    {"id": "x", "type": "team"},
                ],
                "tags": [{"tagFQN": "Tier.Tier1", "source": "Classification"}],
                "columns": [
                    {"name": "total", "dataType": "DECIMAL", "tags": [glossary_tag]},
                    {
                        "name": "address",
                        "dataType": "STRUCT",
                        "children": [{"name": "city", "dataType": "VARCHAR"}, {"dataType": "INT"}],
                    },
                ],
            },
            {
                "id": "t-2",
                "name": "order_facts",
                "fullyQualifiedName": OM_FACTS,
                "tableType": "MaterializedView",
                "tags": [glossary_tag],
                "dataProducts": [
                    {
                        "id": "p-1",
                        "type": "dataProduct",
                        "name": "revenue",
                        "fullyQualifiedName": "Revenue",
                    },
                    {"id": "p-2", "type": "dataProduct"},
                ],
            },
        ],
        "glossaryTerms": [
            {
                "name": "Order Total",
                "fullyQualifiedName": "Sales.Order Total",
                "description": "What the customer paid.",
                "glossary": {"name": "Sales"},
                "owners": [{"type": "team", "name": "finance", "displayName": "Finance"}],
            }
        ],
        "lineage": [
            {
                "entity": {"id": "t-2", "type": "table", "fullyQualifiedName": OM_FACTS},
                "nodes": [
                    {"id": "t-1", "type": "table", "fullyQualifiedName": OM_ORDERS},
                    {
                        "id": "t-0",
                        "type": "table",
                        "fullyQualifiedName": "warehouse.shop.raw.events",
                    },
                    {"id": "d-1", "type": "dashboard", "fullyQualifiedName": "bi.sales"},
                ],
                "upstreamEdges": [
                    {
                        "fromEntity": "t-1",
                        "toEntity": "t-2",
                        "lineageDetails": {
                            "pipeline": {
                                "type": "pipeline",
                                "fullyQualifiedName": "scheduler.build_facts",
                            },
                            "sqlQuery": "INSERT INTO order_facts ...",
                            "columnsLineage": [
                                {
                                    "fromColumns": [f"{OM_ORDERS}.total"],
                                    "toColumn": f"{OM_FACTS}.order_total",
                                },
                                {"fromColumns": [], "toColumn": f"{OM_FACTS}.ignored"},
                            ],
                        },
                    },
                    {"fromEntity": "t-0", "toEntity": "t-1"},
                ],
                "downstreamEdges": [{"fromEntity": "t-2", "toEntity": "d-1"}],
            }
        ],
        "testCases": [
            {
                "name": "total_not_null",
                "displayName": "Total is present",
                "fullyQualifiedName": f"{OM_ORDERS}.total.total_not_null",
                "entityLink": f"<#E::table::{OM_ORDERS}::columns::total>",
                "testDefinition": {"name": "columnValuesToBeNotNull"},
                "dataQualityDimension": "Completeness",
                "owners": [{"type": "team", "name": "orders"}],
                "testCaseResult": {
                    "timestamp": 1709272800000,
                    "testCaseStatus": "Failed",
                    "result": "Found 7 null values",
                    "failedRows": 7,
                    "passedRows": 993,
                },
            },
            {
                "name": "row_count",
                "fullyQualifiedName": f"{OM_FACTS}.row_count",
                "entityLink": f"<#E::table::{OM_FACTS}>",
                "dataQualityDimension": "Integrity",
                "testCaseResult": {"timestamp": 1709272800000, "testCaseStatus": "Success"},
            },
            {
                "name": "never_run",
                "fullyQualifiedName": "warehouse.shop.raw.events.never_run",
                "entityLink": "<#E::table::warehouse.shop.raw.events>",
                "testCaseResult": {"testCaseStatus": "Queued", "timestamp": 1709272800000},
            },
        ],
    }


def test_openmetadata_export_becomes_a_graph(tmp_path: Path, ontology: gg.Ontology) -> None:
    reader, report = load(
        OpenMetadataAdapter(write(tmp_path / "export.json", openmetadata_export())), ontology
    )
    kit = gg.Toolkit(reader)

    assert report.rejected == ()
    assert node(reader, "DataAsset", qualified_name=OM_FACTS)["kind"] == "view"
    assert node(reader, "DataAsset", qualified_name=f"{OM_ORDERS}.address.city") == {
        "qualified_name": f"{OM_ORDERS}.address.city",
        "name": "city",
        "kind": "column",
        "parent_qualified_name": f"{OM_ORDERS}.address",
        "data_type": "VARCHAR",
    }
    assert node(reader, "Owner", identifier="user:avery") == {
        "identifier": "user:avery",
        "name": "Avery Example",
        "kind": "person",
    }
    assert node(reader, "BusinessTerm", name="Sales.Order Total") == {
        "name": "Sales.Order Total",
        "definition": "What the customer paid.",
        "domain": "Sales",
    }

    term = said(kit.find_by_term("order total"))
    assert f"BusinessTerm 'Sales.Order Total' defines DataAsset '{OM_ORDERS}.total'" in term
    assert f"BusinessTerm 'Sales.Order Total' defines DataAsset '{OM_FACTS}'" in term
    assert "BusinessTerm 'Sales.Order Total' is owned by Owner 'Finance'" in term

    context = said(kit.get_asset_context(asset(OM_FACTS)))
    assert f"DataProduct 'Revenue' derives from DataAsset '{OM_FACTS}'" in context
    assert (
        f"DataAsset '{OM_FACTS}' derives from DataAsset '{OM_ORDERS}', "
        "as recorded by LineageEdge 'scheduler.build_facts'"
    ) in context
    assert f"DQRule 'row_count' validates DataAsset '{OM_FACTS}'" in context
    lineage = node(
        reader,
        "LineageEdge",
        source_qualified_name=OM_ORDERS,
        target_qualified_name=OM_FACTS,
        process="scheduler.build_facts",
    )
    assert lineage["transformation"] == "INSERT INTO order_facts ..."
    # Lineage that leaves the tables in the export still loads, as stub assets.
    assert node(reader, "DataAsset", qualified_name="warehouse.shop.raw.events") == {
        "qualified_name": "warehouse.shop.raw.events"
    }
    assert not reader.get_nodes([asset("bi.sales")])


def test_openmetadata_test_results_become_failures(tmp_path: Path, ontology: gg.Ontology) -> None:
    reader, _ = load(
        OpenMetadataAdapter(write(tmp_path / "export.json", openmetadata_export())), ontology
    )
    rule_id = f"{OM_ORDERS}.total.total_not_null"

    assert node(reader, "DQRule", rule_id=rule_id) == {
        "rule_id": rule_id,
        "name": "Total is present",
        "dimension": "completeness",
        "expression": "columnValuesToBeNotNull",
    }
    assert "dimension" not in node(reader, "DQRule", rule_id=f"{OM_FACTS}.row_count")
    assert node(reader, "DQResult", rule_id=rule_id, run_id="1709272800000") == {
        "rule_id": rule_id,
        "run_id": "1709272800000",
        "status": "fail",
        "executed_at": datetime(2024, 3, 1, 6, 0, tzinfo=timezone.utc),
        "rows_failed": 7,
        "rows_checked": 1000,
    }
    passed = node(reader, "DQResult", rule_id=f"{OM_FACTS}.row_count", run_id="1709272800000")
    assert passed["status"] == "pass"
    assert "rows_checked" not in passed
    assert not reader.find_nodes("DQResult", "rule_id", "never_run", match="contains", limit=5)

    failure_id = gg.node_id("DQFailure", {"failure_id": f"{rule_id}@1709272800000"})
    explanation = said(gg.Toolkit(reader).explain_failure(failure_id))
    assert explanation[0].endswith(": Found 7 null values")
    assert f"DataAsset '{OM_ORDERS}' is owned by Owner 'Avery Example'" in explanation
    # The failed column feeds order_facts.order_total, so the failure shows up downstream.
    causes = said(gg.Toolkit(reader).upstream_causes(asset(f"{OM_FACTS}.order_total")))
    assert any(
        s.startswith("Possible cause") and f"failed on DataAsset '{OM_ORDERS}.total', 1 step" in s
        for s in causes
    )


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "must be a JSON object"),
        ({"tables": [{"name": "orders"}]}, "a table in the OpenMetadata export has no"),
        ({"glossaryTerms": [{"description": "x"}]}, "a glossary term in the OpenMetadata export"),
        ({"testCases": [{"name": "t"}]}, "has no name or no table entityLink"),
        ({"testCases": [{"name": "t", "entityLink": "<#E::dashboard::bi.sales>"}]}, "entityLink"),
        ({"testCases": [{"name": "t", "entityLink": "<#E::table::a::rows::1>"}]}, "entityLink"),
        ({"testCases": [{"entityLink": "<#E::table::a>"}]}, "has no name"),
    ],
)
def test_openmetadata_adapter_rejects_unusable_exports(
    tmp_path: Path, document: Any, message: str
) -> None:
    with pytest.raises(gg.AdapterError, match=message):
        list(OpenMetadataAdapter(write(tmp_path / "export.json", document)).records())


def test_openmetadata_adapter_accepts_an_empty_export(tmp_path: Path) -> None:
    assert list(OpenMetadataAdapter(write(tmp_path / "export.json", {})).records()) == []


# ---- DataHub -----------------------------------------------------------------------


def urn(name: str) -> str:
    return f"urn:li:dataset:(urn:li:dataPlatform:postgres,{name},PROD)"


def field(name: str, path: str) -> str:
    return f"urn:li:schemaField:({urn(name)},{path})"


def proposal(entity_urn: str, aspect_name: str, body: Any, raw: bool = False) -> dict[str, Any]:
    aspect = (
        {"value": json.dumps(body), "contentType": "application/json"} if raw else {"json": body}
    )
    return {
        "entityType": entity_urn.split(":")[2],
        "entityUrn": entity_urn,
        "changeType": "UPSERT",
        "aspectName": aspect_name,
        "aspect": aspect,
    }


TERM = "urn:li:glossaryTerm:9f1c"
PRODUCT = "urn:li:dataProduct:revenue"


def datahub_export() -> list[Any]:
    term = {"terms": [{"urn": TERM}, {"urn": "urn:li:tag:not-a-term"}]}
    return [
        proposal(urn("shop.orders"), "datasetProperties", {"description": "All orders."}),
        proposal(
            urn("shop.orders"),
            "schemaMetadata",
            {
                "schemaName": "orders",
                "fields": [
                    {"fieldPath": "total", "nativeDataType": "numeric", "glossaryTerms": term},
                    {"fieldPath": "id", "nativeDataType": "bigint", "description": "Key."},
                    {"nativeDataType": "text"},
                ],
            },
        ),
        proposal(
            urn("shop.orders"),
            "ownership",
            {
                "owners": [
                    {"owner": "urn:li:corpuser:avery", "type": "DATAOWNER"},
                    {"owner": "urn:li:corpGroup:orders", "type": "TECHNICAL_OWNER"},
                    {"owner": "not-a-urn", "type": "DATAOWNER"},
                ]
            },
        ),
        proposal(urn("shop.order_facts"), "glossaryTerms", term, raw=True),
        proposal(
            urn("shop.order_facts"),
            "upstreamLineage",
            {
                "upstreams": [
                    {"dataset": urn("shop.orders"), "type": "TRANSFORMED"},
                    {"dataset": "urn:li:chart:(bi,7)", "type": "VIEW"},
                ],
                "fineGrainedLineages": [
                    {
                        "upstreamType": "FIELD_SET",
                        "upstreams": [field("shop.orders", "total"), "urn:li:other:x"],
                        "downstreamType": "FIELD",
                        "downstreams": [field("shop.order_facts", "order_total")],
                    }
                ],
            },
        ),
        proposal(TERM, "glossaryTermInfo", {"name": "Order Total", "definition": "Amount paid."}),
        proposal(TERM, "ownership", {"owners": [{"owner": "urn:li:corpGroup:finance"}]}),
        proposal(
            PRODUCT,
            "dataProductProperties",
            {
                "name": "Revenue",
                "description": "Revenue reporting.",
                "assets": [{"destinationUrn": urn("shop.order_facts")}, {"destinationUrn": TERM}],
            },
        ),
        proposal(PRODUCT, "ownership", {"owners": [{"owner": "urn:li:corpGroup:finance"}]}),
        proposal("urn:li:chart:(bi,7)", "ownership", {"owners": []}),
        proposal(urn("shop.orders"), "status", {"removed": False}),
        proposal("urn:li:chart:(bi,7)", "datasetProperties", {"description": "ignored"}),
        proposal(TERM, "dataProductProperties", {"name": "ignored"}),
        {"proposedSnapshot": {"urn": urn("shop.legacy")}},
        "not an object",
    ]


def test_datahub_export_becomes_a_graph(tmp_path: Path, ontology: gg.Ontology) -> None:
    reader, report = load(DataHubAdapter(write(tmp_path / "mcps.json", datahub_export())), ontology)
    kit = gg.Toolkit(reader)

    assert report.rejected == ()
    assert node(reader, "DataAsset", qualified_name="shop.orders") == {
        "qualified_name": "shop.orders",
        "name": "orders",
        "kind": "table",
        "description": "All orders.",
    }
    assert node(reader, "DataAsset", qualified_name="shop.orders.id") == {
        "qualified_name": "shop.orders.id",
        "name": "id",
        "kind": "column",
        "parent_qualified_name": "shop.orders",
        "data_type": "bigint",
        "description": "Key.",
    }
    assert node(reader, "Owner", identifier="urn:li:corpuser:avery") == {
        "identifier": "urn:li:corpuser:avery",
        "name": "avery",
        "kind": "person",
    }
    assert node(reader, "Owner", identifier="urn:li:corpGroup:orders")["kind"] == "team"
    assert not reader.get_nodes([asset("shop.legacy")])

    term = said(kit.find_by_term("order total"))
    assert "BusinessTerm 'Order Total' matches the search text and means: Amount paid." in term
    assert "BusinessTerm 'Order Total' defines DataAsset 'shop.orders.total'" in term
    assert "BusinessTerm 'Order Total' defines DataAsset 'shop.order_facts'" in term
    assert "BusinessTerm 'Order Total' is owned by Owner 'finance'" in term

    context = said(kit.get_asset_context(asset("shop.order_facts")))
    assert (
        "DataAsset 'shop.order_facts' derives from DataAsset 'shop.orders', "
        "as recorded by LineageEdge 'transformed'"
    ) in context
    assert "DataProduct 'revenue' derives from DataAsset 'shop.order_facts'" in context
    assert node(reader, "DataProduct", name="revenue")["description"] == "Revenue reporting."

    affected = said(kit.impact_of(asset("shop.orders.total"), depth=1))
    assert affected == [
        "A problem at DataAsset 'shop.orders.total' affects "
        "DataAsset 'shop.order_facts.order_total' (1 step: "
        "DataAsset 'shop.order_facts.order_total' derives from DataAsset 'shop.orders.total')"
    ]
    owners = said(kit.impact_of(gg.node_id("DataProduct", {"name": "revenue"}), depth=1))
    assert owners == [
        "A problem at DataProduct 'revenue' affects Owner 'finance' "
        "(1 step: DataProduct 'revenue' is owned by Owner 'finance')"
    ]


def test_datahub_terms_without_info_are_named_by_their_urn(
    tmp_path: Path, ontology: gg.Ontology
) -> None:
    export = [proposal(urn("shop.orders"), "glossaryTerms", {"terms": [{"urn": TERM}]})]
    reader, _ = load(DataHubAdapter(write(tmp_path / "mcps.json", export)), ontology)
    assert node(reader, "BusinessTerm", name="9f1c") == {"name": "9f1c"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (urn("shop.orders"), "shop.orders"),
        ("urn:li:dataset:(urn:li:dataPlatform:files,a,b.csv,DEV)", "a,b.csv"),
        ("urn:li:dataset:(urn:li:dataPlatform:postgres,PROD)", None),
        ("urn:li:dataset:(urn:li:dataPlatform:postgres,,PROD)", None),
        ("urn:li:chart:(bi,7)", None),
        (None, None),
    ],
)
def test_dataset_name(value: Any, expected: str | None) -> None:
    assert dataset_name(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (field("shop.orders", "total"), "shop.orders.total"),
        (f"urn:li:schemaField:({urn('shop.orders')},)", None),
        ("urn:li:schemaField:(urn:li:chart:(bi,7),total)", None),
        (urn("shop.orders"), None),
        (7, None),
    ],
)
def test_field_name(value: Any, expected: str | None) -> None:
    assert field_name(value) == expected


def test_datahub_adapter_rejects_unusable_exports(tmp_path: Path) -> None:
    with pytest.raises(gg.AdapterError, match="must be a JSON list of change proposals"):
        list(DataHubAdapter(write(tmp_path / "mcps.json", {})).records())
    broken = {
        "entityUrn": urn("shop.orders"),
        "aspectName": "datasetProperties",
        "aspect": {"value": "{not json"},
    }
    with pytest.raises(gg.AdapterError, match="is not valid JSON"):
        list(DataHubAdapter(write(tmp_path / "mcps.json", [broken])).records())


def test_datahub_adapter_tolerates_empty_and_non_object_aspects(
    tmp_path: Path, ontology: gg.Ontology
) -> None:
    export = [
        {"entityUrn": urn("shop.a"), "aspectName": "datasetProperties"},
        {"entityUrn": urn("shop.b"), "aspectName": "datasetProperties", "aspect": {"value": "[1]"}},
    ]
    reader, _ = load(DataHubAdapter(write(tmp_path / "mcps.json", export)), ontology)
    assert node(reader, "DataAsset", qualified_name="shop.a")["name"] == "a"
    assert node(reader, "DataAsset", qualified_name="shop.b")["kind"] == "table"
