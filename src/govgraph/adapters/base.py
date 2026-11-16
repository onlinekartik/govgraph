"""The adapter interface."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable

from govgraph.errors import AdapterError
from govgraph.records import NodeRecord, NodeRef, Record


@runtime_checkable
class Adapter(Protocol):
    """Turns metadata from one source into normalized records.

    An adapter only yields records. It is never given a store, so it cannot write to
    one; :func:`govgraph.ingest` is the single path from an adapter to a store.
    """

    name: str

    def records(self) -> Iterator[Record]: ...


def read_json(path: str | os.PathLike[str], what: str) -> Any:
    """Parse a JSON file, raising :class:`AdapterError` with a readable message."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise AdapterError(f"cannot read {what} {os.fspath(path)}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise AdapterError(f"{what} {os.fspath(path)} is not valid JSON: {exc}") from exc


def asset_ref(qualified_name: str) -> NodeRef:
    return NodeRef("DataAsset", {"qualified_name": qualified_name})


def column_record(qualified_name: str, **properties: Any) -> NodeRecord:
    """A column asset whose parent is everything before the last dot of its name."""
    parent, _, name = qualified_name.rpartition(".")
    return NodeRecord(
        "DataAsset",
        {"qualified_name": qualified_name},
        {"name": name, "kind": "column", "parent_qualified_name": parent or None, **properties},
    )
