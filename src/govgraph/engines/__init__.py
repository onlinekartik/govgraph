"""Mappings from the ontology to graph query engines."""

from govgraph.engines.puppygraph import JdbcConnection, puppygraph_schema

__all__ = ["JdbcConnection", "puppygraph_schema"]
