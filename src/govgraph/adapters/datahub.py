"""Adapter for metadata exported from DataHub.

Reads a JSON file holding a list of metadata change proposals, the format DataHub's file
sink writes. It never calls the DataHub API. Each entry names an entity and one aspect::

    {"entityType": "dataset",
     "entityUrn": "urn:li:dataset:(urn:li:dataPlatform:postgres,shop.orders,PROD)",
     "aspectName": "datasetProperties",
     "aspect": {"json": {"description": "All orders."}}}

Aspects that are read:

==========================  ===========================================================
DataHub aspect              govgraph
==========================  ===========================================================
datasetProperties           ``DataAsset`` (table), named by the name part of the URN
schemaMetadata              ``DataAsset`` (column) per field
ownership                   ``Owner`` and ``OWNED_BY``
glossaryTermInfo            ``BusinessTerm``
glossaryTerms               ``DEFINES`` (on a dataset or on a schema field)
upstreamLineage             ``DERIVES_FROM`` plus a ``LineageEdge``; fine-grained lineage
                            becomes ``DERIVES_FROM`` between column assets
dataProductProperties       ``DataProduct`` and ``DERIVES_FROM``
==========================  ===========================================================

Other aspects, and entries in the older snapshot format, are skipped.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from typing import Any

from govgraph.adapters.base import asset_ref, column_record, read_json
from govgraph.errors import AdapterError
from govgraph.records import EdgeRecord, NodeRecord, NodeRef, Record

_DATASET, _FIELD = "urn:li:dataset:(", "urn:li:schemaField:("
_TERM, _PRODUCT = "urn:li:glossaryTerm:", "urn:li:dataProduct:"
_OWNER_KINDS = {"corpuser": "person", "corpGroup": "team"}


def dataset_name(urn: Any) -> str | None:
    """The name inside ``urn:li:dataset:(<platform urn>,<name>,<environment>)``."""
    if not isinstance(urn, str) or not (urn.startswith(_DATASET) and urn.endswith(")")):
        return None
    parts = urn[len(_DATASET) : -1].split(",")
    return ",".join(parts[1:-1]) or None if len(parts) >= 3 else None


def field_name(urn: Any) -> str | None:
    """``<dataset name>.<field path>`` for ``urn:li:schemaField:(<dataset urn>,<path>)``."""
    if not isinstance(urn, str) or not (urn.startswith(_FIELD) and urn.endswith(")")):
        return None
    dataset_urn, _, path = urn[len(_FIELD) : -1].rpartition(",")
    dataset = dataset_name(dataset_urn)
    return f"{dataset}.{path}" if dataset and path else None


def _aspect(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    aspect = entry.get("aspect") or {}
    if isinstance(aspect.get("json"), Mapping):
        body: Mapping[str, Any] = aspect["json"]
        return body
    if isinstance(aspect.get("value"), str):
        try:
            decoded = json.loads(aspect["value"])
        except json.JSONDecodeError as exc:
            raise AdapterError(f"aspect of {entry.get('entityUrn')} is not valid JSON") from exc
        return decoded if isinstance(decoded, Mapping) else {}
    return {}


class DataHubAdapter:
    """Reads metadata change proposals exported from DataHub."""

    name = "datahub"

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = path

    def records(self) -> Iterator[Record]:
        entries = read_json(self._path, "DataHub export")
        if not isinstance(entries, list):
            raise AdapterError("the DataHub export must be a JSON list of change proposals")
        proposals = [
            (entry["entityUrn"], entry["aspectName"], _aspect(entry))
            for entry in entries
            if isinstance(entry, Mapping) and entry.get("entityUrn") and entry.get("aspectName")
        ]
        # Terms are referred to by URN, so learn their names before anything points at them.
        self._term_names = {
            urn: aspect.get("name") or urn[len(_TERM) :]
            for urn, name, aspect in proposals
            if name == "glossaryTermInfo" and urn.startswith(_TERM)
        }
        handlers = {
            "datasetProperties": self._dataset_properties,
            "schemaMetadata": self._schema_metadata,
            "ownership": self._ownership,
            "glossaryTermInfo": self._glossary_term_info,
            "glossaryTerms": self._glossary_terms,
            "upstreamLineage": self._upstream_lineage,
            "dataProductProperties": self._data_product,
        }
        for urn, name, aspect in proposals:
            handler = handlers.get(name)
            if handler is not None:
                yield from handler(urn, aspect)

    def _term(self, urn: Any) -> NodeRecord | None:
        if not isinstance(urn, str) or not urn.startswith(_TERM):
            return None
        return NodeRecord("BusinessTerm", {"name": self._term_names.get(urn) or urn[len(_TERM) :]})

    def _owned(self, urn: str) -> NodeRef | None:
        """The node an ownership aspect is attached to, for the entity types we map."""
        dataset = dataset_name(urn)
        if dataset:
            return asset_ref(dataset)
        term = self._term(urn)
        if term is not None:
            return term.ref()
        if urn.startswith(_PRODUCT):
            return NodeRef("DataProduct", {"name": urn[len(_PRODUCT) :]})
        return None

    @staticmethod
    def _dataset_properties(urn: str, aspect: Mapping[str, Any]) -> Iterator[Record]:
        dataset = dataset_name(urn)
        if dataset:
            yield NodeRecord(
                "DataAsset",
                {"qualified_name": dataset},
                {
                    "name": aspect.get("name") or dataset.rsplit(".", 1)[-1],
                    "kind": "table",
                    "description": aspect.get("description") or None,
                },
            )

    def _schema_metadata(self, urn: str, aspect: Mapping[str, Any]) -> Iterator[Record]:
        dataset = dataset_name(urn)
        if not dataset:
            return
        yield NodeRecord("DataAsset", {"qualified_name": dataset}, {"kind": "table"})
        for field in aspect.get("fields") or []:
            path = field.get("fieldPath")
            if not path:
                continue
            qualified_name = f"{dataset}.{path}"
            yield NodeRecord(
                "DataAsset",
                {"qualified_name": qualified_name},
                {
                    "name": path,
                    "kind": "column",
                    "parent_qualified_name": dataset,
                    "data_type": field.get("nativeDataType"),
                    "description": field.get("description") or None,
                },
            )
            yield from self._defines(field.get("glossaryTerms"), asset_ref(qualified_name))

    def _defines(self, terms: Any, asset: NodeRef) -> Iterator[Record]:
        for association in (terms or {}).get("terms") or []:
            term = self._term(association.get("urn"))
            if term is not None:
                yield term
                yield EdgeRecord("DEFINES", term.ref(), asset)

    def _glossary_terms(self, urn: str, aspect: Mapping[str, Any]) -> Iterator[Record]:
        dataset = dataset_name(urn)
        if dataset:
            yield NodeRecord("DataAsset", {"qualified_name": dataset})
            yield from self._defines(aspect, asset_ref(dataset))

    def _glossary_term_info(self, urn: str, aspect: Mapping[str, Any]) -> Iterator[Record]:
        term = self._term(urn)
        if term is not None:
            yield NodeRecord(
                "BusinessTerm", term.key, {"definition": aspect.get("definition") or None}
            )

    def _ownership(self, urn: str, aspect: Mapping[str, Any]) -> Iterator[Record]:
        owned = self._owned(urn)
        if owned is None:
            return
        yield NodeRecord(owned.type, owned.key)
        for entry in aspect.get("owners") or []:
            owner_urn = str(entry.get("owner") or "")
            parts = owner_urn.split(":", 3)
            if len(parts) != 4 or parts[:2] != ["urn", "li"] or not parts[3]:
                continue
            owner = NodeRecord(
                "Owner",
                {"identifier": owner_urn},
                {"name": parts[3], "kind": _OWNER_KINDS.get(parts[2])},
            )
            yield owner
            yield EdgeRecord("OWNED_BY", owned, owner.ref())

    @staticmethod
    def _upstream_lineage(urn: str, aspect: Mapping[str, Any]) -> Iterator[Record]:
        downstream = dataset_name(urn)
        if not downstream:
            return
        yield NodeRecord("DataAsset", {"qualified_name": downstream})
        for entry in aspect.get("upstreams") or []:
            upstream = dataset_name(entry.get("dataset"))
            if not upstream:
                continue
            record = NodeRecord(
                "LineageEdge",
                {
                    "source_qualified_name": upstream,
                    "target_qualified_name": downstream,
                    "process": str(entry.get("query") or entry.get("type") or "lineage").lower(),
                },
            )
            yield NodeRecord("DataAsset", {"qualified_name": upstream})
            yield record
            yield EdgeRecord(
                "DERIVES_FROM",
                asset_ref(downstream),
                asset_ref(upstream),
                {"lineage_id": record.id},
            )
        for fine in aspect.get("fineGrainedLineages") or []:
            sources = [field_name(u) for u in fine.get("upstreams") or []]
            targets = [field_name(u) for u in fine.get("downstreams") or []]
            for target in filter(None, targets):
                for source in filter(None, sources):
                    yield column_record(source)
                    yield column_record(target)
                    yield EdgeRecord("DERIVES_FROM", asset_ref(target), asset_ref(source))

    @staticmethod
    def _data_product(urn: str, aspect: Mapping[str, Any]) -> Iterator[Record]:
        if not urn.startswith(_PRODUCT):
            return
        # Keyed by the URN's ID so that other aspects of the product find the same node.
        product = NodeRecord(
            "DataProduct",
            {"name": urn[len(_PRODUCT) :]},
            {"description": aspect.get("description") or aspect.get("name")},
        )
        yield product
        for asset in aspect.get("assets") or []:
            dataset = dataset_name(asset.get("destinationUrn"))
            if dataset:
                yield NodeRecord("DataAsset", {"qualified_name": dataset})
                yield EdgeRecord("DERIVES_FROM", product.ref(), asset_ref(dataset))
