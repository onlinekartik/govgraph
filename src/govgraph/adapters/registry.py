"""Discover adapters through the ``govgraph.adapters`` entry-point group."""

from __future__ import annotations

import inspect
from importlib.metadata import entry_points
from typing import Any

from govgraph.adapters.base import Adapter
from govgraph.errors import AdapterError
from govgraph.spec.model import Ontology

ENTRY_POINT_GROUP = "govgraph.adapters"


def available() -> list[str]:
    """Names of the adapters that are installed."""
    return sorted(entry.name for entry in entry_points(group=ENTRY_POINT_GROUP))


def _accepts_ontology(factory: Any) -> bool:
    try:
        return "ontology" in inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return False


def load(name: str, /, *, ontology: Ontology | None = None, **options: Any) -> Adapter:
    """Create the adapter registered as ``name``, passing ``options`` to its constructor.

    ``ontology`` is passed on only to adapters whose constructor has a parameter of that
    name, which is how an adapter learns about types added by an extension.
    """
    matches = [entry for entry in entry_points(group=ENTRY_POINT_GROUP) if entry.name == name]
    if not matches:
        raise AdapterError(
            f"no adapter named {name!r} is installed. Available: {', '.join(available())}"
        )
    try:
        factory = matches[0].load()
    except Exception as exc:
        raise AdapterError(f"adapter {name!r} could not be imported: {exc}") from exc
    if ontology is not None and _accepts_ontology(factory):
        options["ontology"] = ontology
    try:
        adapter = factory(**options)
    except TypeError as exc:
        raise AdapterError(f"adapter {name!r} rejected its options: {exc}") from exc
    if not isinstance(adapter, Adapter):
        raise AdapterError(f"adapter {name!r} does not have a name and a records() method")
    return adapter
