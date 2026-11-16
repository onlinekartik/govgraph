"""A small synthetic dataset: the governance metadata of a fictional online store.

Everything here is invented and generated in code. It exists so that the quickstart, the
example and the tests have something realistic to run against without any real data.

The store sells things online. Orders, payments and customers land in ``raw`` tables, are
cleaned into ``staging``, combined into ``marts`` and summarised in ``reporting``. A
data-quality run has just found payments without an amount, and order totals that are
wrong as a result.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from govgraph.records import EdgeRecord, NodeRecord, NodeRef, Record

RUN_ID = "run-0042"

_TABLES: dict[str, tuple[str, str]] = {
    "raw.web_orders": ("Orders as submitted by the web shop.", "team-platform"),
    "raw.payments": ("Payment events from the payment provider feed.", "team-platform"),
    "raw.customers": ("Customer sign-up records.", "team-platform"),
    "staging.orders": ("Cleaned orders, one row per order.", "team-platform"),
    "staging.payments": ("Cleaned payments, one row per payment.", "team-platform"),
    "staging.customers": ("Cleaned customers, one row per customer.", "team-platform"),
    "marts.order_facts": ("Orders joined to their payments.", "team-orders"),
    "marts.customer_profile": ("One row per customer with order history.", "team-customers"),
    "reporting.daily_revenue": ("Revenue per day.", "role-finance-steward"),
}

_COLUMNS: dict[str, tuple[str, str]] = {
    "raw.payments.amount": ("numeric", "Amount paid, in the order currency."),
    "staging.payments.amount": ("numeric", "Amount paid, in the order currency."),
    "marts.order_facts.order_total": ("numeric", "Sum of the payments of an order."),
    "marts.customer_profile.email": ("text", "Contact email address of the customer."),
    "reporting.daily_revenue.revenue": ("numeric", "Sum of order totals for the day."),
}

# (downstream, upstream, process, what the process does)
_LINEAGE: tuple[tuple[str, str, str, str], ...] = (
    ("staging.orders", "raw.web_orders", "load_orders", "Deduplicate and type-cast."),
    ("staging.payments", "raw.payments", "load_payments", "Deduplicate and type-cast."),
    ("staging.customers", "raw.customers", "load_customers", "Deduplicate and type-cast."),
    ("marts.order_facts", "staging.orders", "build_order_facts", "Join orders to payments."),
    ("marts.order_facts", "staging.payments", "build_order_facts", "Join orders to payments."),
    (
        "marts.customer_profile",
        "staging.customers",
        "build_customer_profile",
        "Join customers to their orders.",
    ),
    (
        "marts.customer_profile",
        "staging.orders",
        "build_customer_profile",
        "Join customers to their orders.",
    ),
    (
        "reporting.daily_revenue",
        "marts.order_facts",
        "aggregate_daily_revenue",
        "Sum order totals per day.",
    ),
    ("staging.payments.amount", "raw.payments.amount", "load_payments", "Copied unchanged."),
    (
        "marts.order_facts.order_total",
        "staging.payments.amount",
        "build_order_facts",
        "Sum of payment amounts per order.",
    ),
    (
        "reporting.daily_revenue.revenue",
        "marts.order_facts.order_total",
        "aggregate_daily_revenue",
        "Sum of order totals per day.",
    ),
)

_OWNERS: dict[str, tuple[str, str]] = {
    "team-platform": ("Data Platform Team", "team"),
    "team-orders": ("Orders Team", "team"),
    "team-customers": ("Customer Team", "team"),
    "role-finance-steward": ("Finance Data Steward", "role"),
}

# name: (definition, domain, assets it defines, critical data element it defines)
_TERMS: dict[str, tuple[str, str, tuple[str, ...], str | None]] = {
    "Order Total": (
        "The full amount a customer paid for one order.",
        "Sales",
        ("marts.order_facts.order_total",),
        "Recognised Revenue",
    ),
    "Daily Revenue": (
        "The sum of all order totals for one calendar day.",
        "Finance",
        ("reporting.daily_revenue.revenue",),
        "Recognised Revenue",
    ),
    "Customer Email": (
        "The address the store uses to contact a customer.",
        "Customer",
        ("marts.customer_profile.email",),
        "Customer Contact Detail",
    ),
}

# name: (description, regulation code, assets classified as this element)
_CRITICAL_ELEMENTS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "Recognised Revenue": (
        "Revenue figures that feed financial statements.",
        "FIN-01",
        ("marts.order_facts.order_total", "reporting.daily_revenue.revenue"),
    ),
    "Customer Contact Detail": (
        "Anything that can be used to contact an individual customer.",
        "PRIV-01",
        ("marts.customer_profile.email",),
    ),
}

_REGULATIONS: dict[str, tuple[str, str]] = {
    "FIN-01": ("Financial Reporting Policy", "Reported figures must be complete and accurate."),
    "PRIV-01": ("Personal Data Policy", "Personal data must be correct and access-controlled."),
}

# name: (description, asset it is built from, owner, regulation code or None)
_PRODUCTS: dict[str, tuple[str, str, str, str | None]] = {
    "Revenue Dashboard": (
        "Daily revenue for the finance team.",
        "reporting.daily_revenue",
        "role-finance-steward",
        None,
    ),
    "Customer Overview": (
        "A profile of every customer for the support team.",
        "marts.customer_profile",
        "team-customers",
        "PRIV-01",
    ),
}

# rule_id: (name, dimension, severity, expression, asset, critical element, status, failed)
_RULES: dict[str, tuple[str, str, str, str, str, str | None, str, int]] = {
    "payments_amount_present": (
        "Payment amount is present",
        "completeness",
        "high",
        "amount IS NOT NULL",
        "raw.payments.amount",
        None,
        "fail",
        310,
    ),
    "order_total_not_negative": (
        "Order total is not negative",
        "validity",
        "critical",
        "order_total >= 0",
        "marts.order_facts.order_total",
        "Recognised Revenue",
        "fail",
        310,
    ),
    "customer_email_format": (
        "Customer email looks like an email address",
        "validity",
        "medium",
        "email LIKE '%_@_%'",
        "marts.customer_profile.email",
        "Customer Contact Detail",
        "pass",
        0,
    ),
    "daily_revenue_fresh": (
        "Daily revenue was refreshed today",
        "timeliness",
        "medium",
        "max(day) = current_date",
        "reporting.daily_revenue",
        None,
        "pass",
        0,
    ),
}

# failure_id: (summary, severity, detected_at, rule, asset, impacted product, impacted element)
_FAILURES: dict[str, tuple[str, str, str, str, str, str | None, str | None]] = {
    "F-1001": (
        "310 payments arrived without an amount.",
        "high",
        "2024-03-01T06:05:00+00:00",
        "payments_amount_present",
        "raw.payments.amount",
        None,
        None,
    ),
    "F-1002": (
        "310 orders have a missing or negative total.",
        "critical",
        "2024-03-01T06:20:00+00:00",
        "order_total_not_negative",
        "marts.order_facts.order_total",
        "Revenue Dashboard",
        "Recognised Revenue",
    ),
}


def asset(qualified_name: str) -> NodeRef:
    return NodeRef("DataAsset", {"qualified_name": qualified_name})


def _ref(node_type: str, **key: str) -> NodeRef:
    return NodeRef(node_type, key)


def _node(ref: NodeRef, **properties: Any) -> NodeRecord:
    return NodeRecord(ref.type, ref.key, properties)


def _records() -> Iterator[Record]:
    for identifier, (name, kind) in _OWNERS.items():
        yield _node(_ref("Owner", identifier=identifier), name=name, kind=kind)

    for qualified_name, (description, owner) in _TABLES.items():
        yield _node(
            asset(qualified_name),
            name=qualified_name.rsplit(".", 1)[1],
            kind="table",
            description=description,
        )
        yield EdgeRecord("OWNED_BY", asset(qualified_name), _ref("Owner", identifier=owner))

    for qualified_name, (data_type, description) in _COLUMNS.items():
        parent, name = qualified_name.rsplit(".", 1)
        yield _node(
            asset(qualified_name),
            name=name,
            kind="column",
            parent_qualified_name=parent,
            data_type=data_type,
            description=description,
        )

    for downstream, upstream, process, transformation in _LINEAGE:
        lineage = _ref(
            "LineageEdge",
            source_qualified_name=upstream,
            target_qualified_name=downstream,
            process=process,
        )
        yield _node(lineage, transformation=transformation)
        yield EdgeRecord(
            "DERIVES_FROM", asset(downstream), asset(upstream), {"lineage_id": lineage.id}
        )

    for code, (name, description) in _REGULATIONS.items():
        yield _node(_ref("Regulation", code=code), name=name, description=description)

    steward = _ref("Owner", identifier="role-finance-steward")
    for name, (description, regulation, assets) in _CRITICAL_ELEMENTS.items():
        element = _ref("CriticalDataElement", name=name)
        yield _node(element, description=description, criticality="high")
        yield EdgeRecord("GOVERNED_BY", element, _ref("Regulation", code=regulation))
        yield EdgeRecord("OWNED_BY", element, steward)
        for qualified_name in assets:
            yield EdgeRecord("CLASSIFIED_AS", asset(qualified_name), element)

    for name, (definition, domain, assets, element_name) in _TERMS.items():
        term = _ref("BusinessTerm", name=name)
        yield _node(term, definition=definition, domain=domain, status="approved")
        yield EdgeRecord("OWNED_BY", term, steward)
        for qualified_name in assets:
            yield EdgeRecord("DEFINES", term, asset(qualified_name))
        if element_name:
            yield EdgeRecord("DEFINES", term, _ref("CriticalDataElement", name=element_name))

    for name, (description, source, owner, regulation_code) in _PRODUCTS.items():
        product = _ref("DataProduct", name=name)
        yield _node(product, description=description, status="active")
        yield EdgeRecord("DERIVES_FROM", product, asset(source))
        yield EdgeRecord("OWNED_BY", product, _ref("Owner", identifier=owner))
        if regulation_code:
            yield EdgeRecord("GOVERNED_BY", product, _ref("Regulation", code=regulation_code))

    for rule_id, rule_fields in _RULES.items():
        name, dimension, severity, expression, target, element_name, status, failed = rule_fields
        rule = _ref("DQRule", rule_id=rule_id)
        yield _node(rule, name=name, dimension=dimension, severity=severity, expression=expression)
        yield EdgeRecord("VALIDATES", rule, asset(target))
        yield EdgeRecord("OWNED_BY", rule, _ref("Owner", identifier="team-orders"))
        if element_name:
            yield EdgeRecord("PROTECTS", rule, _ref("CriticalDataElement", name=element_name))
        result = _ref("DQResult", rule_id=rule_id, run_id=RUN_ID)
        yield _node(
            result,
            status=status,
            executed_at="2024-03-01T06:00:00+00:00",
            rows_checked=12000,
            rows_failed=failed,
        )
        yield EdgeRecord("RESULT_OF", result, rule)

    for failure_id, failure_fields in _FAILURES.items():
        summary, severity, detected_at, rule_id, target, product_name, element_name = failure_fields
        failure = _ref("DQFailure", failure_id=failure_id)
        yield _node(
            failure,
            summary=summary,
            severity=severity,
            status="open",
            detected_at=detected_at,
            failed_count=310,
        )
        yield EdgeRecord("FAILED_ON", failure, asset(target))
        yield EdgeRecord("RAISED_FROM", failure, _ref("DQResult", rule_id=rule_id, run_id=RUN_ID))
        if product_name:
            yield EdgeRecord("IMPACTS", failure, _ref("DataProduct", name=product_name))
        if element_name:
            yield EdgeRecord("IMPACTS", failure, _ref("CriticalDataElement", name=element_name))


class OnlineStoreSample:
    """Adapter that yields the synthetic online-store dataset."""

    name = "online_store_sample"

    def records(self) -> Iterator[Record]:
        return _records()


def online_store() -> OnlineStoreSample:
    """Return an adapter for the synthetic online-store dataset."""
    return OnlineStoreSample()
