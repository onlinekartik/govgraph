"""The adapter interface."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from govgraph.records import Record


@runtime_checkable
class Adapter(Protocol):
    """Turns metadata from one source into normalized records.

    An adapter only yields records. It is never given a store, so it cannot write to
    one; :func:`govgraph.ingest` is the single path from an adapter to a store.
    """

    name: str

    def records(self) -> Iterator[Record]: ...
