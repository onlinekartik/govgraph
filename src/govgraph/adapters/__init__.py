"""Adapters turn metadata from a source system into normalized records."""

from govgraph.adapters.base import Adapter
from govgraph.adapters.registry import ENTRY_POINT_GROUP, available, load

__all__ = ["ENTRY_POINT_GROUP", "Adapter", "available", "load"]
