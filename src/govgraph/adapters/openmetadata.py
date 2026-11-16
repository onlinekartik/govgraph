"""Adapter for metadata exported from OpenMetadata.

Reads one JSON file. It never calls the OpenMetadata API; export the entities first and
put them in a document shaped like this, each list holding entities exactly as the API
returns them::

    {"tables": [...], "glossaryTerms": [...], "testCases": [...], "lineage": [...]}

``lineage`` holds entity-lineage responses (``entity``, ``nodes``, ``upstreamEdges``,
``downstreamEdges``). Every section is optional.

What becomes what:

==========================  ===========================================================
OpenMetadata                govgraph
==========================  ===========================================================
table                       ``DataAsset`` (table or view), named by fullyQualifiedName
column                      ``DataAsset`` (column) whose parent is the table
owner                       ``Owner`` and ``OWNED_BY``
glossary term               ``BusinessTerm``
glossary tag on an asset    ``DEFINES``
data product on a table     ``DataProduct`` and ``DERIVES_FROM``
lineage edge                ``DERIVES_FROM`` plus a ``LineageEdge``
column lineage              ``DERIVES_FROM`` between the column assets
test case                   ``DQRule`` and ``VALIDATES``
latest test result          ``DQResult`` and ``RESULT_OF``
failed test result          ``DQFailure``, ``RAISED_FROM`` and ``FAILED_ON``
==========================  ===========================================================
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from typing import Any

from govgraph.adapters.base import asset_ref, column_record, read_json
from govgraph.errors import AdapterError
from govgraph.records import EdgeRecord, NodeRecord, NodeRef, Record

_VIEW_TYPES = {"View", "SecureView", "MaterializedView", "SemanticView"}
_DIMENSIONS = {"completeness", "validity", "uniqueness", "timeliness", "consistency", "accuracy"}
_STATUSES = {"Success": "pass", "Failed": "fail", "Aborted": "error"}


def _entity_link(link: Any) -> tuple[str, str | None] | None:
    """Split ``<#E::table::<fqn>>`` or ``<#E::table::<fqn>::columns::<column>>``."""
    text = str(link or "")
    if not (text.startswith("<#E::") and text.endswith(">")):
        return None
    parts = text[5:-1].split("::")
    if len(parts) == 2 and parts[0] == "table" and parts[1]:
        return parts[1], None
    if len(parts) == 4 and parts[0] == "table" and parts[2] == "columns" and parts[1] and parts[3]:
        return parts[1], parts[3]
    return None


def _owner_records(owners: Any, owned: NodeRef) -> Iterator[Record]:
    for owner in owners or []:
        name = owner.get("name")
        if not name:
            continue
        record = NodeRecord(
            "Owner",
            {"identifier": f"{owner.get('type') or 'owner'}:{name}"},
            {
                "name": owner.get("displayName") or name,
                "kind": "person" if owner.get("type") == "user" else "team",
            },
        )
        yield record
        yield EdgeRecord("OWNED_BY", owned, record.ref())


def _term_edges(tags: Any, asset: NodeRef) -> Iterator[Record]:
    for tag in tags or []:
        if tag.get("source") == "Glossary" and tag.get("tagFQN"):
            term = NodeRecord("BusinessTerm", {"name": tag["tagFQN"]})
            yield term
            yield EdgeRecord("DEFINES", term.ref(), asset)


def _timestamp(millis: Any) -> datetime | None:
    if isinstance(millis, bool) or not isinstance(millis, (int, float)):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc)


class OpenMetadataAdapter:
    """Reads tables, glossary terms, test cases and lineage exported from OpenMetadata."""

    name = "openmetadata"

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = path

    def records(self) -> Iterator[Record]:
        document = read_json(self._path, "OpenMetadata export")
        if not isinstance(document, Mapping):
            raise AdapterError("the OpenMetadata export must be a JSON object")
        for table in document.get("tables") or []:
            yield from self._table(table)
        for term in document.get("glossaryTerms") or []:
            yield from self._glossary_term(term)
        for lineage in document.get("lineage") or []:
            yield from self._lineage(lineage)
        for test_case in document.get("testCases") or []:
            yield from self._test_case(test_case)

    def _table(self, table: Mapping[str, Any]) -> Iterator[Record]:
        qualified_name = table.get("fullyQualifiedName")
        if not qualified_name:
            raise AdapterError("a table in the OpenMetadata export has no fullyQualifiedName")
        asset = asset_ref(qualified_name)
        yield NodeRecord(
            "DataAsset",
            asset.key,
            {
                "name": table.get("name"),
                "kind": "view" if table.get("tableType") in _VIEW_TYPES else "table",
                "description": table.get("description") or None,
            },
        )
        yield from _owner_records(table.get("owners"), asset)
        yield from _term_edges(table.get("tags"), asset)
        for product in table.get("dataProducts") or []:
            name = product.get("fullyQualifiedName") or product.get("name")
            if name:
                record = NodeRecord(
                    "DataProduct", {"name": name}, {"description": product.get("description")}
                )
                yield record
                yield EdgeRecord("DERIVES_FROM", record.ref(), asset)
        yield from self._columns(table.get("columns"), qualified_name)

    def _columns(self, columns: Any, parent: str) -> Iterator[Record]:
        for column in columns or []:
            name = column.get("name")
            if not name:
                continue
            # Built from the parent rather than taken from the export, so that the
            # column's parent is always the asset it was listed under.
            qualified_name = f"{parent}.{name}"
            yield column_record(
                qualified_name,
                data_type=column.get("dataType"),
                description=column.get("description") or None,
            )
            yield from _term_edges(column.get("tags"), asset_ref(qualified_name))
            yield from self._columns(column.get("children"), qualified_name)

    @staticmethod
    def _glossary_term(term: Mapping[str, Any]) -> Iterator[Record]:
        name = term.get("fullyQualifiedName") or term.get("name")
        if not name:
            raise AdapterError("a glossary term in the OpenMetadata export has no name")
        record = NodeRecord(
            "BusinessTerm",
            {"name": name},
            {
                "definition": term.get("description") or None,
                "domain": (term.get("glossary") or {}).get("name"),
            },
        )
        yield record
        yield from _owner_records(term.get("owners"), record.ref())

    @staticmethod
    def _lineage(lineage: Mapping[str, Any]) -> Iterator[Record]:
        references = [lineage.get("entity") or {}, *(lineage.get("nodes") or [])]
        tables = {
            ref.get("id"): ref.get("fullyQualifiedName")
            for ref in references
            if ref.get("type") == "table" and ref.get("fullyQualifiedName")
        }
        edges = [*(lineage.get("upstreamEdges") or []), *(lineage.get("downstreamEdges") or [])]
        for edge in edges:
            upstream, downstream = (
                tables.get(edge.get("fromEntity")),
                tables.get(edge.get("toEntity")),
            )
            if not upstream or not downstream:
                continue
            details = edge.get("lineageDetails") or {}
            pipeline = details.get("pipeline") or {}
            record = NodeRecord(
                "LineageEdge",
                {
                    "source_qualified_name": upstream,
                    "target_qualified_name": downstream,
                    "process": pipeline.get("fullyQualifiedName")
                    or pipeline.get("name")
                    or details.get("source")
                    or "lineage",
                },
                {"transformation": details.get("description") or details.get("sqlQuery")},
            )
            # The assets may not be in this export, so make sure they exist.
            yield NodeRecord("DataAsset", {"qualified_name": upstream})
            yield NodeRecord("DataAsset", {"qualified_name": downstream})
            yield record
            yield EdgeRecord(
                "DERIVES_FROM",
                asset_ref(downstream),
                asset_ref(upstream),
                {"lineage_id": record.id},
            )
            for mapping in details.get("columnsLineage") or []:
                target = mapping.get("toColumn")
                for source in mapping.get("fromColumns") or []:
                    if not target or not source:
                        continue
                    yield column_record(source)
                    yield column_record(target)
                    yield EdgeRecord(
                        "DERIVES_FROM",
                        asset_ref(target),
                        asset_ref(source),
                        {"lineage_id": record.id},
                    )

    @staticmethod
    def _test_case(test_case: Mapping[str, Any]) -> Iterator[Record]:
        rule_id = test_case.get("fullyQualifiedName") or test_case.get("name")
        link = _entity_link(test_case.get("entityLink"))
        if not rule_id or link is None:
            raise AdapterError(
                "a test case in the OpenMetadata export has no name or no table entityLink"
            )
        table, column = link
        dimension = str(test_case.get("dataQualityDimension") or "").lower()
        rule = NodeRecord(
            "DQRule",
            {"rule_id": rule_id},
            {
                "name": test_case.get("displayName") or test_case.get("name"),
                "description": test_case.get("description") or None,
                "dimension": dimension if dimension in _DIMENSIONS else None,
                "expression": (test_case.get("testDefinition") or {}).get("name"),
            },
        )
        yield rule
        # A stub, so the rule can be loaded even when the table is not in this export.
        if column:
            checked = asset_ref(f"{table}.{column}")
            yield column_record(f"{table}.{column}")
        else:
            checked = asset_ref(table)
            yield NodeRecord("DataAsset", checked.key)
        yield EdgeRecord("VALIDATES", rule.ref(), checked)
        yield from _owner_records(test_case.get("owners"), rule.ref())

        outcome = test_case.get("testCaseResult") or {}
        status = _STATUSES.get(str(outcome.get("testCaseStatus")))
        when = _timestamp(outcome.get("timestamp"))
        if status is None or when is None:
            return
        failed, passed = outcome.get("failedRows"), outcome.get("passedRows")
        checked_rows = (
            failed + passed if isinstance(failed, int) and isinstance(passed, int) else None
        )
        result = NodeRecord(
            "DQResult",
            {"rule_id": rule_id, "run_id": str(outcome["timestamp"])},
            {
                "status": status,
                "executed_at": when,
                "rows_failed": failed if isinstance(failed, int) else None,
                "rows_checked": checked_rows,
            },
        )
        yield result
        yield EdgeRecord("RESULT_OF", result.ref(), rule.ref())
        if status != "fail":
            return
        failure = NodeRecord(
            "DQFailure",
            {"failure_id": f"{rule_id}@{outcome['timestamp']}"},
            {
                "summary": outcome.get("result"),
                "status": "open",
                "detected_at": when,
                "failed_count": failed if isinstance(failed, int) else None,
            },
        )
        yield failure
        yield EdgeRecord("RAISED_FROM", failure.ref(), result.ref())
        yield EdgeRecord("FAILED_ON", failure.ref(), checked)
