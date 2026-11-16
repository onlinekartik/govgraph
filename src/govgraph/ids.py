"""Deterministic identifiers.

A node ID is ``<NodeType>:<digest>`` where the digest is a hash of the node type and its
natural key. The same key therefore always produces the same ID, on any machine, which is
what makes ingestion idempotent and lets independent adapters refer to the same node.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from govgraph.errors import RecordError

KeyValue = str | int

# Bump only if the hashing recipe changes; doing so changes every ID.
_ID_SCHEME = "govgraph-id-v1"
_DIGEST_LENGTH = 32

ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*:[0-9a-f]{32}$"
_ID_RE = re.compile(ID_PATTERN)


def _digest(payload: object) -> str:
    text = json.dumps([_ID_SCHEME, payload], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]


def node_id(node_type: str, key: Mapping[str, KeyValue]) -> str:
    """Return the ID of the node of ``node_type`` identified by ``key``."""
    if not key:
        raise RecordError(f"{node_type}: a natural key cannot be empty")
    for name, value in key.items():
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise RecordError(
                f"{node_type}: key field '{name}' must be a string or an integer, "
                f"not {type(value).__name__}"
            )
        if value == "":
            raise RecordError(f"{node_type}: key field '{name}' cannot be empty")
    return f"{node_type}:{_digest([node_type, sorted(key.items())])}"


def edge_id(edge_type: str, source_id: str, target_id: str) -> str:
    """Return the ID of the ``edge_type`` edge between two nodes.

    There is at most one edge of a given type between a given pair of nodes.
    """
    return f"{edge_type}:{_digest([edge_type, source_id, target_id])}"


def is_id(value: object) -> bool:
    return isinstance(value, str) and _ID_RE.match(value) is not None


def id_type(identifier: str) -> str:
    """Return the node or edge type encoded in an ID."""
    if not is_id(identifier):
        raise ValueError("not a govgraph ID")
    return identifier.split(":", 1)[0]
