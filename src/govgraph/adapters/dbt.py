"""Adapter for dbt artifacts.

Reads ``manifest.json`` and, optionally, ``run_results.json`` from a dbt project's
``target`` directory. Nothing is executed and no warehouse is contacted.

What becomes what:

==========================  ===========================================================
dbt                         govgraph
==========================  ===========================================================
model, seed, snapshot,      ``DataAsset`` (table or view), named ``database.schema.name``
source
documented column           ``DataAsset`` (column) whose parent is the relation
``depends_on``              ``DERIVES_FROM`` plus a ``LineageEdge`` naming the model
``meta.owner``, group owner ``Owner`` and ``OWNED_BY``
data test                   ``DQRule`` and ``VALIDATES``
exposure                    ``DataProduct``, ``DERIVES_FROM`` and ``OWNED_BY``
test result                 ``DQResult`` and ``RESULT_OF``
failed test result          ``DQFailure``, ``RAISED_FROM`` and ``FAILED_ON``
==========================  ===========================================================
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from typing import Any

from govgraph.adapters.base import asset_ref, column_record, read_json
from govgraph.errors import AdapterError
from govgraph.records import EdgeRecord, NodeRecord, NodeRef, Record

_RELATION_TYPES = {"model", "seed", "snapshot", "source"}
_DIMENSIONS = {
    "not_null": "completeness",
    "unique": "uniqueness",
    "accepted_values": "validity",
    "relationships": "consistency",
}
_SEVERITIES = {"error": "high", "warn": "medium"}
_STATUSES = {"pass": "pass", "fail": "fail", "warn": "warn", "error": "error"}


def _relation_name(node: Mapping[str, Any]) -> str:
    name = node.get("alias") or node.get("identifier") or node.get("name")
    parts = [node.get("database"), node.get("schema"), name]
    return ".".join(str(part) for part in parts if part)


def _owner(identifier: str) -> NodeRecord:
    return NodeRecord("Owner", {"identifier": identifier}, {"name": identifier})


class DbtAdapter:
    """Reads a dbt ``manifest.json`` and, optionally, a ``run_results.json``."""

    name = "dbt"

    def __init__(
        self,
        manifest_path: str | os.PathLike[str],
        run_results_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self._manifest_path = manifest_path
        self._run_results_path = run_results_path

    def records(self) -> Iterator[Record]:
        manifest = read_json(self._manifest_path, "dbt manifest")
        if not isinstance(manifest, Mapping) or not isinstance(manifest.get("nodes"), Mapping):
            raise AdapterError("the dbt manifest has no 'nodes' section")
        nodes: dict[str, Mapping[str, Any]] = {**manifest["nodes"], **manifest.get("sources", {})}
        relations = {
            unique_id: _relation_name(node)
            for unique_id, node in nodes.items()
            if node.get("resource_type") in _RELATION_TYPES
        }
        groups = {
            group.get("name"): (group.get("owner") or {}).get("name")
            for group in (manifest.get("groups") or {}).values()
        }

        for unique_id, qualified_name in relations.items():
            yield from self._relation(nodes[unique_id], qualified_name, relations, groups)

        tests = {
            unique_id: node
            for unique_id, node in nodes.items()
            if node.get("resource_type") == "test"
        }
        targets = {
            unique_id: self._test_targets(node, relations) for unique_id, node in tests.items()
        }
        documented = {
            f"{relations[unique_id]}.{column}"
            for unique_id in relations
            for column in nodes[unique_id].get("columns") or {}
        }
        for unique_id, node in tests.items():
            yield from self._test(unique_id, node, targets[unique_id], documented)

        for exposure in (manifest.get("exposures") or {}).values():
            yield from self._exposure(exposure, relations)

        if self._run_results_path is not None:
            yield from self._results(self._run_results_path, tests, targets, documented)

    def _relation(
        self,
        node: Mapping[str, Any],
        qualified_name: str,
        relations: Mapping[str, str],
        groups: Mapping[Any, Any],
    ) -> Iterator[Record]:
        materialized = (node.get("config") or {}).get("materialized")
        yield NodeRecord(
            "DataAsset",
            {"qualified_name": qualified_name},
            {
                "name": node.get("name"),
                "kind": "view" if materialized == "view" else "table",
                "description": node.get("description") or None,
            },
        )
        for column, details in (node.get("columns") or {}).items():
            yield column_record(
                f"{qualified_name}.{column}",
                data_type=details.get("data_type"),
                description=details.get("description") or None,
            )

        meta = {**((node.get("config") or {}).get("meta") or {}), **(node.get("meta") or {})}
        owner = meta.get("owner") or groups.get(node.get("group"))
        if isinstance(owner, str) and owner:
            yield _owner(owner)
            yield EdgeRecord("OWNED_BY", asset_ref(qualified_name), _owner(owner).ref())

        for upstream_id in (node.get("depends_on") or {}).get("nodes") or []:
            upstream = relations.get(upstream_id)
            if upstream is None:
                continue
            lineage = NodeRecord(
                "LineageEdge",
                {
                    "source_qualified_name": upstream,
                    "target_qualified_name": qualified_name,
                    "process": node.get("unique_id") or qualified_name,
                },
                {"transformation": f"dbt {node.get('resource_type')}"},
            )
            yield lineage
            yield EdgeRecord(
                "DERIVES_FROM",
                asset_ref(qualified_name),
                asset_ref(upstream),
                {"lineage_id": lineage.id},
            )

    @staticmethod
    def _test_targets(node: Mapping[str, Any], relations: Mapping[str, str]) -> list[str]:
        """Qualified names of the relations a test checks."""
        attached = node.get("attached_node")
        if isinstance(attached, str) and attached in relations:
            return [relations[attached]]
        depends_on = (node.get("depends_on") or {}).get("nodes") or []
        return [relations[unique_id] for unique_id in depends_on if unique_id in relations]

    @staticmethod
    def _checked_assets(
        node: Mapping[str, Any], targets: list[str], documented: set[str]
    ) -> list[NodeRef]:
        """The relation under test and, when the test names one, its documented column."""
        assets = [asset_ref(target) for target in targets]
        column = node.get("column_name")
        if column and targets and f"{targets[0]}.{column}" in documented:
            assets.append(asset_ref(f"{targets[0]}.{column}"))
        return assets

    def _test(
        self, unique_id: str, node: Mapping[str, Any], targets: list[str], documented: set[str]
    ) -> Iterator[Record]:
        kind = (node.get("test_metadata") or {}).get("name")
        column = node.get("column_name")
        severity = str((node.get("config") or {}).get("severity") or "").lower()
        rule = NodeRecord(
            "DQRule",
            {"rule_id": unique_id},
            {
                "name": node.get("name"),
                "description": node.get("description") or None,
                "dimension": _DIMENSIONS.get(str(kind)),
                "severity": _SEVERITIES.get(severity),
                "expression": f"{kind}({column})" if kind and column else kind,
            },
        )
        yield rule
        for checked in self._checked_assets(node, targets, documented):
            yield EdgeRecord("VALIDATES", rule.ref(), checked)

    @staticmethod
    def _exposure(exposure: Mapping[str, Any], relations: Mapping[str, str]) -> Iterator[Record]:
        product = NodeRecord(
            "DataProduct",
            {"name": exposure.get("label") or exposure["name"]},
            {"description": exposure.get("description") or None},
        )
        yield product
        for upstream_id in (exposure.get("depends_on") or {}).get("nodes") or []:
            if upstream_id in relations:
                yield EdgeRecord("DERIVES_FROM", product.ref(), asset_ref(relations[upstream_id]))
        # Only the owner's name is used; contact details are left behind.
        owner = (exposure.get("owner") or {}).get("name")
        if owner:
            yield _owner(owner)
            yield EdgeRecord("OWNED_BY", product.ref(), _owner(owner).ref())

    def _results(
        self,
        path: str | os.PathLike[str],
        tests: Mapping[str, Mapping[str, Any]],
        targets: Mapping[str, list[str]],
        documented: set[str],
    ) -> Iterator[Record]:
        run = read_json(path, "dbt run results")
        if not isinstance(run, Mapping) or not isinstance(run.get("results"), list):
            raise AdapterError("the dbt run results have no 'results' list")
        metadata = run.get("metadata") or {}
        run_id, finished_at = metadata.get("invocation_id"), metadata.get("generated_at")
        if not run_id:
            raise AdapterError("the dbt run results have no metadata.invocation_id")
        for outcome in run["results"]:
            unique_id = outcome.get("unique_id")
            status = _STATUSES.get(str(outcome.get("status")))
            if unique_id not in tests or status is None:
                continue
            failures = outcome.get("failures")
            result = NodeRecord(
                "DQResult",
                {"rule_id": unique_id, "run_id": run_id},
                {
                    "status": status,
                    "executed_at": finished_at,
                    "rows_failed": failures if isinstance(failures, int) else None,
                },
            )
            yield result
            yield EdgeRecord("RESULT_OF", result.ref(), NodeRef("DQRule", {"rule_id": unique_id}))
            if status != "fail":
                continue
            severity = str((tests[unique_id].get("config") or {}).get("severity") or "").lower()
            failure = NodeRecord(
                "DQFailure",
                {"failure_id": f"{run_id}:{unique_id}"},
                {
                    "summary": outcome.get("message"),
                    "severity": _SEVERITIES.get(severity),
                    "status": "open",
                    "detected_at": finished_at,
                    "failed_count": failures if isinstance(failures, int) else None,
                },
            )
            yield failure
            yield EdgeRecord("RAISED_FROM", failure.ref(), result.ref())
            for checked in self._checked_assets(tests[unique_id], targets[unique_id], documented):
                yield EdgeRecord("FAILED_ON", failure.ref(), checked)
