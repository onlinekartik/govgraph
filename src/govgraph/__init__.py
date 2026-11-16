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

__version__ = "0.1.0"

__all__ = [
    "AdapterError",
    "Edge",
    "EdgeRecord",
    "GovGraphError",
    "GraphReader",
    "GraphStore",
    "IngestError",
    "IngestReport",
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
    "UnsafeRoleError",
    "__version__",
    "edge_id",
    "ingest",
    "load_ontology",
    "node_id",
]
