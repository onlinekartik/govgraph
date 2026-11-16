"""govgraph: data-governance metadata as a typed knowledge graph with bounded agent tools."""

from govgraph.errors import (
    AdapterError,
    GovGraphError,
    IngestError,
    OntologyError,
    RecordError,
    StoreError,
    StoreTimeoutError,
    ToolError,
    UnsafeRoleError,
)
from govgraph.ids import edge_id, node_id
from govgraph.ingest import IngestReport, ingest
from govgraph.records import Edge, EdgeRecord, Node, NodeRecord, NodeRef, Record
from govgraph.spec import Ontology, load_ontology
from govgraph.store import GraphReader, GraphStore, MemoryStore, PostgresStore
from govgraph.tools import AuditEvent, Claim, Limits, Toolkit, ToolResult

__version__ = "0.1.0"

__all__ = [
    "AdapterError",
    "AuditEvent",
    "Claim",
    "Edge",
    "EdgeRecord",
    "GovGraphError",
    "GraphReader",
    "GraphStore",
    "IngestError",
    "IngestReport",
    "Limits",
    "MemoryStore",
    "Node",
    "NodeRecord",
    "NodeRef",
    "Ontology",
    "OntologyError",
    "PostgresStore",
    "Record",
    "RecordError",
    "StoreError",
    "StoreTimeoutError",
    "ToolError",
    "ToolResult",
    "Toolkit",
    "UnsafeRoleError",
    "__version__",
    "edge_id",
    "ingest",
    "load_ontology",
    "node_id",
]
