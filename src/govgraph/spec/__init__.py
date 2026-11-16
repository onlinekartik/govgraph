"""The ontology spec: the single source of truth for node and edge types."""

from govgraph.spec.loader import load_ontology
from govgraph.spec.model import EdgeType, Endpoint, NodeType, Ontology, Property

__all__ = ["EdgeType", "Endpoint", "NodeType", "Ontology", "Property", "load_ontology"]
