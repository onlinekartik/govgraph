"""Hard limits that every tool call runs under."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_ROWS_CEILING = 1000
MAX_TIMEOUT_S = 30.0

# Depth caps are fixed. They are part of each tool's contract and cannot be raised.
IMPACT_MAX_DEPTH = 3
UPSTREAM_MAX_DEPTH = 5
EXPLAIN_UPSTREAM_DEPTH = 3


@dataclass(frozen=True)
class Limits:
    """Per-call budget.

    ``max_rows`` caps the nodes and edges a call may read from the store.
    ``timeout_s`` caps how long a call may run. A call that hits either returns what it
    has found so far, marked as truncated.
    """

    max_rows: int = 200
    timeout_s: float = 5.0

    def __post_init__(self) -> None:
        if isinstance(self.max_rows, bool) or not isinstance(self.max_rows, int):
            raise ValueError("max_rows must be an integer")
        if not 1 <= self.max_rows <= MAX_ROWS_CEILING:
            raise ValueError(f"max_rows must be between 1 and {MAX_ROWS_CEILING}")
        if not 0 < self.timeout_s <= MAX_TIMEOUT_S:
            raise ValueError(f"timeout_s must be greater than 0 and at most {MAX_TIMEOUT_S:g}")

    def to_dict(self) -> dict[str, Any]:
        return {"max_rows": self.max_rows, "timeout_s": self.timeout_s}
