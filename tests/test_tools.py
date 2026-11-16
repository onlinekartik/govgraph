from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

import pytest
from jsonschema import Draft202012Validator

import govgraph as gg
from govgraph.records import Edge, Node
from govgraph.samples import asset, online_store
from govgraph.store.base import Direction, GraphReader, Match
from govgraph.tools import IMPACT_MAX_DEPTH, TOOL_SPECS, UPSTREAM_MAX_DEPTH
from tests.conftest import Backend

F1001 = gg.node_id("DQFailure", {"failure_id": "F-1001"})
F1002 = gg.node_id("DQFailure", {"failure_id": "F-1002"})
ORDER_TOTAL = asset("marts.order_facts.order_total").id
REVENUE = asset("reporting.daily_revenue.revenue").id
RAW_AMOUNT = asset("raw.payments.amount").id
ORDERS_TEAM = gg.node_id("Owner", {"identifier": "team-orders"})

CALLS: list[tuple[str, dict[str, Any]]] = [
    ("get_asset_context", {"asset_id": ORDER_TOTAL}),
    ("explain_failure", {"failure_id": F1002}),
    ("impact_of", {"entity_id": RAW_AMOUNT, "depth": 3}),
    ("upstream_causes", {"asset_id": REVENUE, "depth": 5}),
    ("find_by_term", {"term": "revenue"}),
]


def statements(result: gg.ToolResult) -> list[str]:
    return [claim.statement for claim in result.claims]


def comparable(result: gg.ToolResult) -> dict[str, Any]:
    return {k: v for k, v in result.to_dict().items() if k != "elapsed_ms"}


def assert_well_formed(result: gg.ToolResult) -> None:
    node_ids = {node.id for node in result.nodes}
    for claim in result.claims:
        assert claim.citations
        assert set(claim.citations) <= node_ids
    for edge in result.edges:
        assert {edge.source_id, edge.target_id} <= node_ids
    json.dumps(result.to_dict())


@pytest.fixture
def kit(memory_store: gg.MemoryStore) -> gg.Toolkit:
    return gg.Toolkit(memory_store.reader())


class SpyReader:
    """Wraps a reader, records what was asked of it and can be told to misbehave."""

    def __init__(self, inner: GraphReader, fail_with: Exception | None = None) -> None:
        self._inner = inner
        self.fail_with = fail_with
        self.calls: list[str] = []
        self.rows_returned = 0
        self.timeouts: list[float | None] = []

    @property
    def ontology(self) -> gg.Ontology:
        return self._inner.ontology

    def _before(self, name: str, timeout_s: float | None) -> None:
        self.calls.append(name)
        self.timeouts.append(timeout_s)
        if self.fail_with is not None:
            raise self.fail_with

    def get_nodes(self, node_ids: Sequence[str], *, timeout_s: float | None = None) -> list[Node]:
        self._before("get_nodes", timeout_s)
        rows = self._inner.get_nodes(node_ids, timeout_s=timeout_s)
        self.rows_returned += len(rows)
        return rows

    def get_edges(
        self,
        node_ids: Sequence[str],
        edge_type: str,
        direction: Direction,
        *,
        limit: int,
        timeout_s: float | None = None,
    ) -> list[Edge]:
        self._before("get_edges", timeout_s)
        rows = self._inner.get_edges(
            node_ids, edge_type, direction, limit=limit, timeout_s=timeout_s
        )
        assert len(rows) <= limit
        self.rows_returned += len(rows)
        return rows

    def find_nodes(
        self,
        node_type: str,
        prop: str,
        value: str,
        *,
        match: Match = "exact",
        limit: int,
        timeout_s: float | None = None,
    ) -> list[Node]:
        self._before("find_nodes", timeout_s)
        rows = self._inner.find_nodes(
            node_type, prop, value, match=match, limit=limit, timeout_s=timeout_s
        )
        self.rows_returned += len(rows)
        return rows


# ---- what each tool says -----------------------------------------------------------


def test_get_asset_context(loaded: Backend) -> None:
    result = gg.Toolkit(loaded.reader()).get_asset_context(ORDER_TOTAL)

    assert_well_formed(result)
    assert result.tool == "get_asset_context"
    assert not result.truncated
    assert statements(result) == [
        "DataAsset 'marts.order_facts.order_total' is a column",
        "BusinessTerm 'Order Total' defines DataAsset 'marts.order_facts.order_total'",
        "DataAsset 'marts.order_facts.order_total' is classified as "
        "CriticalDataElement 'Recognised Revenue'",
        "DQRule 'Order total is not negative' validates DataAsset 'marts.order_facts.order_total'",
        "DataAsset 'marts.order_facts.order_total' derives from "
        "DataAsset 'staging.payments.amount', as recorded by LineageEdge 'build_order_facts'",
        "DataAsset 'reporting.daily_revenue.revenue' derives from "
        "DataAsset 'marts.order_facts.order_total', as recorded by "
        "LineageEdge 'aggregate_daily_revenue'",
        "DQFailure 'F-1002' failed on DataAsset 'marts.order_facts.order_total'",
        "CriticalDataElement 'Recognised Revenue' is governed by "
        "Regulation 'Financial Reporting Policy'",
        "DataAsset 'marts.order_facts.order_total' is part of DataAsset 'marts.order_facts'",
        "DataAsset 'marts.order_facts' is owned by Owner 'Orders Team'",
    ]
    assert result.claims[0].citations == (ORDER_TOTAL,)
    assert result.claims[-1].citations == (asset("marts.order_facts").id, ORDERS_TEAM)
    assert result.nodes[0].id == ORDER_TOTAL


def test_get_asset_context_of_a_table_uses_its_own_owner(kit: gg.Toolkit) -> None:
    result = kit.get_asset_context(asset("marts.order_facts").id)
    said = statements(result)
    assert "DataAsset 'marts.order_facts' is a table" in said
    assert "DataAsset 'marts.order_facts' is owned by Owner 'Orders Team'" in said
    assert not any("is part of" in s for s in said)


def test_explain_failure(loaded: Backend) -> None:
    result = gg.Toolkit(loaded.reader()).explain_failure(F1002)

    assert_well_formed(result)
    said = statements(result)
    assert said[0] == (
        "DQFailure 'F-1002' (critical, open, detected 2024-03-01T06:20:00+00:00): "
        "310 orders have a missing or negative total."
    )
    for expected in (
        "DQFailure 'F-1002' failed on DataAsset 'marts.order_facts.order_total'",
        "DQFailure 'F-1002' was raised from DQResult 'order_total_not_negative/run-0042'",
        "DQResult 'order_total_not_negative/run-0042' is a result of "
        "DQRule 'Order total is not negative'",
        "DQFailure 'F-1002' impacts DataProduct 'Revenue Dashboard'",
        "DQFailure 'F-1002' impacts CriticalDataElement 'Recognised Revenue'",
        "DataAsset 'marts.order_facts' is owned by Owner 'Orders Team'",
        "CriticalDataElement 'Recognised Revenue' is governed by "
        "Regulation 'Financial Reporting Policy'",
    ):
        assert expected in said
    cause = result.claims[-1]
    assert cause.statement == (
        "Possible cause: DQFailure 'F-1001' (high, open, detected 2024-03-01T06:05:00+00:00) "
        "failed on DataAsset 'raw.payments.amount', 2 steps upstream of "
        "DataAsset 'marts.order_facts.order_total'"
    )
    assert cause.citations == (
        ORDER_TOTAL,
        asset("staging.payments.amount").id,
        RAW_AMOUNT,
        F1001,
    )
    assert result.limits == {"max_rows": 200, "timeout_s": 5.0, "upstream_depth": 3}


def test_explain_failure_says_when_nothing_upstream_failed(kit: gg.Toolkit) -> None:
    result = kit.explain_failure(F1001)
    assert_well_formed(result)
    assert not any(s.startswith("Possible cause") for s in statements(result))
    assert result.claims[-1].statement == (
        "No other failure is recorded on DataAsset 'raw.payments.amount' or within 3 steps upstream"
    )
    assert result.claims[-1].citations == (RAW_AMOUNT,)


def test_explain_failure_without_an_asset(ontology: gg.Ontology) -> None:
    store = gg.MemoryStore(ontology)
    failure = gg.NodeRecord("DQFailure", {"failure_id": "F-9"}, {"status": "open"})
    store.upsert([failure])
    result = gg.Toolkit(store.reader()).explain_failure(failure.id)
    assert statements(result) == ["DQFailure 'F-9' (open)"]
    assert result.notes == (
        "The failure is not linked to a data asset, so lineage was not searched.",
    )


def test_upstream_causes(loaded: Backend) -> None:
    result = gg.Toolkit(loaded.reader()).upstream_causes(REVENUE, depth=5)

    assert_well_formed(result)
    causes = [c for c in result.claims if c.statement.startswith("Possible cause")]
    assert [c.statement for c in causes] == [
        "Possible cause: DQFailure 'F-1002' (critical, open, detected 2024-03-01T06:20:00+00:00) "
        "failed on DataAsset 'marts.order_facts.order_total', 1 step upstream of "
        "DataAsset 'reporting.daily_revenue.revenue'",
        "Possible cause: DQFailure 'F-1001' (high, open, detected 2024-03-01T06:05:00+00:00) "
        "failed on DataAsset 'raw.payments.amount', 3 steps upstream of "
        "DataAsset 'reporting.daily_revenue.revenue'",
    ]
    assert causes[1].citations == (
        REVENUE,
        ORDER_TOTAL,
        asset("staging.payments.amount").id,
        RAW_AMOUNT,
        F1001,
    )
    assert result.limits["depth"] == 5
    assert result.limits["max_depth"] == UPSTREAM_MAX_DEPTH


def test_upstream_causes_respects_depth(kit: gg.Toolkit) -> None:
    near = kit.upstream_causes(REVENUE, depth=1)
    causes = [s for s in statements(near) if s.startswith("Possible cause")]
    assert len(causes) == 1
    assert "F-1002" in causes[0]
    assert near.limits["depth"] == 1
    assert kit.upstream_causes(REVENUE).limits["depth"] == 3


def test_upstream_causes_includes_failures_on_the_asset_itself(kit: gg.Toolkit) -> None:
    result = kit.upstream_causes(RAW_AMOUNT)
    assert statements(result)[-1] == (
        "Possible cause: DQFailure 'F-1001' (high, open, detected 2024-03-01T06:05:00+00:00) "
        "failed on DataAsset 'raw.payments.amount', the asset itself"
    )


def test_upstream_causes_says_when_there_are_none(kit: gg.Toolkit) -> None:
    result = kit.upstream_causes(asset("marts.customer_profile").id, depth=2)
    assert_well_formed(result)
    assert statements(result)[-1] == (
        "No failure is recorded on DataAsset 'marts.customer_profile' "
        "or within 2 steps upstream of it"
    )


def test_impact_of(loaded: Backend) -> None:
    result = gg.Toolkit(loaded.reader()).impact_of(asset("raw.payments").id, depth=3)

    assert_well_formed(result)
    affected = [s.split(" affects ")[1].split(" (")[0] for s in statements(result)]
    assert affected == [
        "Owner 'Data Platform Team'",
        "DataAsset 'staging.payments'",
        "DataAsset 'marts.order_facts'",
        "Owner 'Orders Team'",
        "DataAsset 'reporting.daily_revenue'",
    ]
    last = result.claims[-1]
    assert last.statement == (
        "A problem at DataAsset 'raw.payments' affects DataAsset 'reporting.daily_revenue' "
        "(3 steps: DataAsset 'staging.payments' derives from DataAsset 'raw.payments', "
        "as recorded by LineageEdge 'load_payments'; "
        "DataAsset 'marts.order_facts' derives from DataAsset 'staging.payments', "
        "as recorded by LineageEdge 'build_order_facts'; "
        "DataAsset 'reporting.daily_revenue' derives from DataAsset 'marts.order_facts', "
        "as recorded by LineageEdge 'aggregate_daily_revenue')"
    )
    assert len(last.citations) == 7
    assert last.citations[0] == asset("raw.payments").id
    assert result.limits["max_depth"] == IMPACT_MAX_DEPTH


def test_impact_of_defaults_to_two_steps_and_accepts_any_node_type(kit: gg.Toolkit) -> None:
    result = kit.impact_of(F1002)
    assert result.limits["depth"] == 2
    affected = {s.split(" affects ")[1].split(" (")[0] for s in statements(result)}
    assert affected == {
        "DataAsset 'marts.order_facts.order_total'",
        "DataProduct 'Revenue Dashboard'",
        "CriticalDataElement 'Recognised Revenue'",
        "DataAsset 'reporting.daily_revenue.revenue'",
        "Regulation 'Financial Reporting Policy'",
        "Owner 'Finance Data Steward'",
    }


def test_impact_of_says_when_nothing_is_affected(kit: gg.Toolkit) -> None:
    result = kit.impact_of(ORDERS_TEAM, depth=3)
    assert statements(result) == [
        "Nothing in the graph is affected by Owner 'Orders Team' within 3 steps"
    ]
    assert result.claims[0].citations == (ORDERS_TEAM,)


def test_find_by_term(loaded: Backend) -> None:
    result = gg.Toolkit(loaded.reader()).find_by_term("ORDER tot")

    assert_well_formed(result)
    assert sorted(statements(result)) == sorted(
        [
            "BusinessTerm 'Order Total' matches the search text and means: "
            "The full amount a customer paid for one order.",
            "BusinessTerm 'Order Total' defines DataAsset 'marts.order_facts.order_total'",
            "BusinessTerm 'Order Total' defines CriticalDataElement 'Recognised Revenue'",
            "BusinessTerm 'Order Total' is owned by Owner 'Finance Data Steward'",
        ]
    )


def test_find_by_term_with_no_match(kit: gg.Toolkit) -> None:
    result = kit.find_by_term("%")
    assert result.claims == ()
    assert result.nodes == ()
    assert result.notes == ("No business term has a name containing the search text.",)
    assert not result.truncated


# ---- one implementation, two stores ------------------------------------------------


@pytest.mark.postgres
def test_tools_give_the_same_answer_on_both_stores(
    loaded: Backend, memory_store: gg.MemoryStore
) -> None:
    if loaded.name != "postgres":
        pytest.skip("compares PostgreSQL with memory")
    memory, postgres = gg.Toolkit(memory_store.reader()), gg.Toolkit(loaded.reader())
    for name, arguments in CALLS:
        assert comparable(postgres.call(name, arguments)) == comparable(
            memory.call(name, arguments)
        )


@pytest.mark.parametrize(("name", "arguments"), CALLS)
def test_results_are_reproducible_and_contain_no_sql(
    loaded: Backend, name: str, arguments: dict[str, Any]
) -> None:
    kit = gg.Toolkit(loaded.reader())
    first, second = kit.call(name, arguments), kit.call(name, arguments)
    assert comparable(first) == comparable(second)
    assert_well_formed(first)
    assert first.claims
    text = json.dumps(first.to_dict())
    for fragment in ("SELECT", "ANY(", "ILIKE", "node_data_asset", "edge_", "govgraph."):
        assert fragment not in text


# ---- limits ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_rows": 0},
        {"max_rows": 1001},
        {"max_rows": 2.5},
        {"max_rows": True},
        {"timeout_s": 0},
        {"timeout_s": -1},
        {"timeout_s": 30.5},
    ],
)
def test_limits_have_hard_ceilings(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="must be"):
        gg.Limits(**kwargs)


def test_limits_defaults_and_bounds() -> None:
    assert gg.Limits().to_dict() == {"max_rows": 200, "timeout_s": 5.0}
    assert gg.Limits(max_rows=1000, timeout_s=30).max_rows == 1000


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("impact_of", {"entity_id": RAW_AMOUNT, "depth": IMPACT_MAX_DEPTH + 1}),
        ("upstream_causes", {"asset_id": REVENUE, "depth": UPSTREAM_MAX_DEPTH + 1}),
        ("impact_of", {"entity_id": RAW_AMOUNT, "depth": 0}),
        ("impact_of", {"entity_id": RAW_AMOUNT, "depth": True}),
        ("impact_of", {"entity_id": RAW_AMOUNT, "depth": "2"}),
        ("upstream_causes", {"asset_id": REVENUE, "depth": 2.5}),
    ],
)
def test_depth_cannot_exceed_the_cap(kit: gg.Toolkit, name: str, arguments: dict[str, Any]) -> None:
    with pytest.raises(gg.ToolError) as caught:
        kit.call(name, arguments)
    assert caught.value.code == "invalid_input"
    assert "depth must be" in caught.value.message


@pytest.mark.parametrize("max_rows", [1, 2, 3, 5, 8, 13, 21])
@pytest.mark.parametrize(("name", "arguments"), CALLS)
def test_row_limit_truncates_but_never_lies(
    loaded: Backend, name: str, arguments: dict[str, Any], max_rows: int
) -> None:
    full = gg.Toolkit(loaded.reader()).call(name, arguments)
    small = gg.Toolkit(loaded.reader(), limits=gg.Limits(max_rows=max_rows)).call(name, arguments)

    assert_well_formed(small)
    assert len(small.nodes) + len(small.edges) <= max_rows
    assert small.limits["max_rows"] == max_rows
    if len(full.nodes) + len(full.edges) > max_rows:
        assert small.truncated_by == "rows"
    # Whatever a truncated call does say, the full call says too.
    assert set(statements(small)) <= set(statements(full))
    assert {n.id for n in small.nodes} <= {n.id for n in full.nodes}


def test_row_limit_is_enforced_at_the_store(memory_store: gg.MemoryStore) -> None:
    spy = SpyReader(memory_store.reader())
    result = gg.Toolkit(spy, limits=gg.Limits(max_rows=10)).impact_of(F1002, depth=3)
    assert result.truncated_by == "rows"
    # One extra row per query is allowed, to tell "exactly full" from "there was more".
    assert spy.rows_returned <= 10 + len(spy.calls)
    assert set(spy.calls) <= {"get_nodes", "get_edges", "find_nodes"}


def test_exactly_enough_rows_is_not_truncated(ontology: gg.Ontology) -> None:
    store = gg.MemoryStore(ontology)
    term = gg.NodeRecord("BusinessTerm", {"name": "Basket"})
    store.upsert([term])
    result = gg.Toolkit(store.reader(), limits=gg.Limits(max_rows=1)).find_by_term("basket")
    assert not result.truncated
    assert [n.id for n in result.nodes] == [term.id]


def test_time_limit_truncates(memory_store: gg.MemoryStore) -> None:
    ticks = iter(range(1000))
    spy = SpyReader(memory_store.reader())
    kit = gg.Toolkit(spy, limits=gg.Limits(timeout_s=4.5), clock=lambda: float(next(ticks)))

    result = kit.explain_failure(F1002)

    assert result.truncated_by == "timeout"
    assert_well_formed(result)
    assert statements(result)[0].startswith("DQFailure 'F-1002'")
    assert not any(s.startswith("No other failure") for s in statements(result))
    assert 0 < len(spy.calls) <= 4
    # Each query is given only the time that is left.
    assert spy.timeouts == sorted(spy.timeouts, reverse=True)  # type: ignore[type-var]
    assert all(t is not None and 0 < t <= 4.5 for t in spy.timeouts)
    assert result.elapsed_ms > 4500


def test_store_timeout_truncates(memory_store: gg.MemoryStore) -> None:
    spy = SpyReader(memory_store.reader(), fail_with=gg.StoreTimeoutError("slow"))
    for name, arguments in CALLS:
        result = gg.Toolkit(spy).call(name, arguments)
        assert result.truncated_by == "timeout"
        assert result.claims == ()
        assert result.nodes == ()


def test_store_failure_is_reported_without_details(memory_store: gg.MemoryStore) -> None:
    internal = 'SELECT * FROM "govgraph"."node_owner" -- INTERNAL-DETAIL'
    spy = SpyReader(memory_store.reader(), fail_with=gg.StoreError(internal))
    with pytest.raises(gg.ToolError) as caught:
        gg.Toolkit(spy).find_by_term("revenue")
    assert caught.value.code == "store_error"
    assert caught.value.message == "the graph store could not answer"
    assert "SELECT" not in str(caught.value)
    assert "INTERNAL-DETAIL" not in str(caught.value)


# ---- input checking ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "code", "message"),
    [
        ("drop_tables", {}, "unknown_tool", "there is no tool with that name"),
        ("get_asset_context", {}, "invalid_input", "missing required argument: asset_id"),
        ("get_asset_context", {"asset_id": 7}, "invalid_input", "asset_id must be of type string"),
        (
            "get_asset_context",
            {"asset_id": "'; DROP TABLE x; --"},
            "invalid_input",
            "asset_id is not a well-formed ID",
        ),
        (
            "get_asset_context",
            {"asset_id": ORDER_TOTAL, "sql": "SELECT 1"},
            "invalid_input",
            "unknown argument",
        ),
        (
            "get_asset_context",
            {"asset_id": F1002},
            "invalid_input",
            "the ID must be the ID of a DataAsset",
        ),
        (
            "explain_failure",
            {"failure_id": ORDER_TOTAL},
            "invalid_input",
            "the ID must be the ID of a DQFailure",
        ),
        (
            "impact_of",
            {"entity_id": "Ghost:" + "0" * 32},
            "invalid_input",
            "the ID does not refer to a node type in the ontology",
        ),
        (
            "impact_of",
            {"entity_id": "OWNED_BY:" + "0" * 32},
            "invalid_input",
            "the ID does not refer to a node type in the ontology",
        ),
        (
            "get_asset_context",
            {"asset_id": asset("no.such.asset").id},
            "not_found",
            "no DataAsset has that ID",
        ),
        ("find_by_term", {"term": ""}, "invalid_input", "term must be at least 1 character long"),
        (
            "find_by_term",
            {"term": "x" * 201},
            "invalid_input",
            "term must be at most 200 characters long",
        ),
        ("find_by_term", {"term": None}, "invalid_input", "term must be of type string"),
    ],
)
def test_bad_calls_are_rejected(
    kit: gg.Toolkit, name: str, arguments: dict[str, Any], code: str, message: str
) -> None:
    with pytest.raises(gg.ToolError) as caught:
        kit.call(name, arguments)
    assert (caught.value.code, caught.value.message) == (code, message)
    assert str(caught.value) == f"{code}: {message}"


def test_arguments_must_be_an_object(kit: gg.Toolkit) -> None:
    with pytest.raises(gg.ToolError, match="arguments must be an object"):
        kit.call("find_by_term", ["revenue"])  # type: ignore[arg-type]
    with pytest.raises(gg.ToolError, match="unknown_tool"):
        kit.call(None, {})  # type: ignore[arg-type]


def test_rejected_calls_never_reach_the_store(memory_store: gg.MemoryStore) -> None:
    spy = SpyReader(memory_store.reader())
    kit = gg.Toolkit(spy)
    rejected: list[tuple[str, dict[str, Any]]] = [
        ("nope", {}),
        ("get_asset_context", {"asset_id": "bad"}),
        ("impact_of", {"entity_id": RAW_AMOUNT, "depth": 99}),
        ("explain_failure", {"failure_id": ORDER_TOTAL}),
    ]
    for name, arguments in rejected:
        with pytest.raises(gg.ToolError):
            kit.call(name, arguments)
    assert spy.calls == []


# ---- schemas -----------------------------------------------------------------------


def test_schemas_describe_every_tool(kit: gg.Toolkit) -> None:
    schemas = kit.schemas()
    assert [s["name"] for s in schemas] == [
        "get_asset_context",
        "explain_failure",
        "impact_of",
        "upstream_causes",
        "find_by_term",
    ]
    for schema in schemas:
        Draft202012Validator.check_schema(schema["input_schema"])
        assert schema["input_schema"]["additionalProperties"] is False
        assert "cites" in schema["description"]
        assert callable(getattr(kit, schema["name"]))
    by_name = {s["name"]: s["input_schema"]["properties"] for s in schemas}
    assert by_name["impact_of"]["depth"]["maximum"] == 3
    assert by_name["upstream_causes"]["depth"]["maximum"] == 5
    json.dumps(schemas)


def test_schemas_are_copies(kit: gg.Toolkit) -> None:
    kit.schemas()[2]["input_schema"]["properties"]["depth"]["maximum"] = 99
    assert kit.schemas()[2]["input_schema"]["properties"]["depth"]["maximum"] == 3
    assert TOOL_SPECS["impact_of"].input_schema["properties"]["depth"]["maximum"] == 3


# ---- audit -------------------------------------------------------------------------


def test_every_call_is_audited(memory_store: gg.MemoryStore) -> None:
    events: list[gg.AuditEvent] = []
    kit = gg.Toolkit(memory_store.reader(), limits=gg.Limits(max_rows=3), audit=events.append)

    kit.find_by_term("zzz")
    kit.impact_of(F1002)
    with pytest.raises(gg.ToolError):
        kit.get_asset_context(asset("no.such.asset").id)
    with pytest.raises(gg.ToolError):
        kit.call("nope", {"x": 1})

    assert [(e.tool, e.outcome, e.error_code) for e in events] == [
        ("find_by_term", "ok", None),
        ("impact_of", "truncated", None),
        ("get_asset_context", "error", "not_found"),
        ("nope", "error", "unknown_tool"),
    ]
    assert events[0].arguments == {"term": "zzz"}
    assert events[1].rows == 3
    assert all(e.elapsed_ms >= 0 for e in events)
    assert events[2].to_dict()["error_code"] == "not_found"


def test_audit_events_are_logged_by_default(
    kit: gg.Toolkit, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="govgraph.audit"):
        kit.find_by_term("revenue")
    (record,) = caplog.records
    event = json.loads(record.getMessage())
    assert event["tool"] == "find_by_term"
    assert event["arguments"] == {"term": "revenue"}
    assert event["outcome"] == "ok"
    assert event["rows"] == 7


# ---- ontology awareness ------------------------------------------------------------


def test_toolkit_needs_the_core_types() -> None:
    bare = gg.load_ontology(
        [
            {
                "ontology": {"name": "bare", "version": "0.1.0"},
                "node_types": {
                    "DataAsset": {
                        "key": ["qualified_name"],
                        "properties": {"qualified_name": {"type": "string", "required": True}},
                    }
                },
            }
        ],
        include_core=False,
    )
    with pytest.raises(gg.OntologyError, match="need these core ontology types: BusinessTerm"):
        gg.Toolkit(gg.MemoryStore(bare).reader())


def test_tools_follow_edge_types_added_by_an_extension() -> None:
    ontology = gg.load_ontology(
        [
            {
                "ontology": {"name": "ext", "version": "0.1.0"},
                "node_types": {
                    "Pipeline": {
                        "key": ["name"],
                        "properties": {"name": {"type": "string", "required": True}},
                    }
                },
                "edge_types": {
                    "PRODUCED_BY": {
                        "phrase": "is produced by",
                        "propagates": "target_to_source",
                        "endpoints": [{"source": "DataAsset", "target": "Pipeline"}],
                    }
                },
            }
        ]
    )
    store = gg.MemoryStore(ontology)
    table = gg.NodeRecord("DataAsset", {"qualified_name": "shop.orders"}, {"kind": "table"})
    pipeline = gg.NodeRecord("Pipeline", {"name": "nightly"})
    store.upsert([table, pipeline], [gg.EdgeRecord("PRODUCED_BY", table.ref(), pipeline.ref())])
    kit = gg.Toolkit(store.reader())

    assert "DataAsset 'shop.orders' is produced by Pipeline 'nightly'" in statements(
        kit.get_asset_context(table.id)
    )
    assert statements(kit.impact_of(pipeline.id)) == [
        "A problem at Pipeline 'nightly' affects DataAsset 'shop.orders' "
        "(1 step: DataAsset 'shop.orders' is produced by Pipeline 'nightly')"
    ]


def test_lineage_cycles_terminate(ontology: gg.Ontology) -> None:
    store = gg.MemoryStore(ontology)
    names = ["a", "b", "c"]
    tables = [gg.NodeRecord("DataAsset", {"qualified_name": n}) for n in names]
    loop = [
        gg.EdgeRecord("DERIVES_FROM", tables[i].ref(), tables[(i + 1) % 3].ref()) for i in range(3)
    ]
    store.upsert(tables, loop)
    kit = gg.Toolkit(store.reader())

    upstream = kit.upstream_causes(tables[0].id, depth=5)
    impact = kit.impact_of(tables[0].id, depth=3)

    assert not upstream.truncated
    assert len(upstream.nodes) == 3
    assert [s.split(" affects ")[1].split(" (")[0] for s in statements(impact)] == [
        "DataAsset 'c'",
        "DataAsset 'b'",
    ]


def test_dangling_edges_are_not_reported(ontology: gg.Ontology) -> None:
    store = gg.MemoryStore(ontology)
    table = gg.NodeRecord("DataAsset", {"qualified_name": "shop.orders"})
    ghost = gg.NodeRef("Owner", {"identifier": "gone"})
    store.upsert([table], [gg.EdgeRecord("OWNED_BY", table.ref(), ghost)])
    result = gg.Toolkit(store.reader()).get_asset_context(table.id)
    assert statements(result) == ["DataAsset 'shop.orders' exists"]
    assert result.edges == ()
    assert_well_formed(result)


# ---- result invariants -------------------------------------------------------------


def test_a_claim_must_cite_something() -> None:
    with pytest.raises(ValueError, match="must cite at least one node"):
        gg.Claim("The sky is blue", ())
    with pytest.raises(ValueError, match="needs a statement"):
        gg.Claim("", ("DataAsset:" + "0" * 32,))


def test_a_result_cannot_cite_nodes_it_does_not_contain() -> None:
    with pytest.raises(ValueError, match="cites nodes that are not in the result"):
        gg.ToolResult(
            tool="t",
            claims=(gg.Claim("x", ("DataAsset:" + "0" * 32,)),),
            nodes=(),
            edges=(),
            limits={},
            elapsed_ms=0.0,
        )


def test_sample_is_what_the_tests_assume() -> None:
    ids = {r.id for r in online_store().records() if isinstance(r, gg.NodeRecord)}
    assert {F1001, F1002, ORDER_TOTAL, REVENUE, RAW_AMOUNT, ORDERS_TEAM} <= ids
