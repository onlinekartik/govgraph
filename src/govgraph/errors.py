"""Exception hierarchy for govgraph."""

from __future__ import annotations

from collections.abc import Sequence


class GovGraphError(Exception):
    """Base class for every error raised by govgraph."""


class OntologyError(GovGraphError):
    """An ontology document is invalid, or a type is not defined in the ontology."""

    def __init__(self, message: str, problems: Sequence[str] = ()) -> None:
        self.problems: tuple[str, ...] = tuple(problems) or (message,)
        super().__init__(message)


class RecordError(GovGraphError):
    """A node or edge record does not conform to the ontology."""

    def __init__(self, message: str, problems: Sequence[str] = ()) -> None:
        self.problems: tuple[str, ...] = tuple(problems) or (message,)
        super().__init__(message)


class IngestError(GovGraphError):
    """An ingest run was rejected. Nothing was written to the store."""

    def __init__(self, message: str, problems: Sequence[str] = ()) -> None:
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__(message)


class AdapterError(GovGraphError):
    """An adapter could not be loaded or could not read its input."""


class StoreError(GovGraphError):
    """A store operation failed."""


class StoreTimeoutError(StoreError):
    """A store operation exceeded its time limit."""


class UnsafeRoleError(StoreError):
    """A read connection uses a database role that is able to modify the graph."""


class ToolError(GovGraphError):
    """A tool call was rejected or failed. The message is safe to show to an agent."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
