"""Model Context Protocol wrapper around the tools. Requires the ``mcp`` extra."""

from govgraph.mcp.server import build_server, serve_stdio

__all__ = ["build_server", "serve_stdio"]
