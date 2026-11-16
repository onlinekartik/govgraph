"""Expose a :class:`~govgraph.tools.Toolkit` as a Model Context Protocol server.

The server adds nothing to the tools. It lists them with their JSON-schema signatures
and passes calls to :meth:`Toolkit.call`, so every limit and check still applies.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from govgraph.errors import GovGraphError, ToolError
from govgraph.tools.toolkit import Toolkit

if TYPE_CHECKING:
    from mcp.server import Server

_LOG = logging.getLogger("govgraph.mcp")

INSTRUCTIONS = (
    "Read-only tools over a data-governance knowledge graph. Every statement a tool "
    "returns cites the IDs of the nodes that support it; use those IDs as arguments to "
    "other tools. A result marked truncated is incomplete: narrow the question or lower "
    "the depth rather than assuming nothing else exists."
)


def _require_mcp() -> None:
    try:
        import mcp  # noqa: F401
    except ImportError as exc:
        raise GovGraphError(
            "The MCP server needs the mcp package. Install it with: pip install 'govgraph[mcp]'"
        ) from exc


def build_server(kit: Toolkit, *, name: str = "govgraph") -> Server[Any]:
    """Return an MCP server that serves the tools of ``kit``."""
    _require_mcp()
    import anyio
    from mcp import types
    from mcp.server import Server

    from govgraph import __version__

    def failure(code: str, message: str) -> types.CallToolResult:
        payload = {"error": {"code": code, "message": message}}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload))],
            structured_content=payload,
            is_error=True,
        )

    async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=schema["name"],
                    description=schema["description"],
                    input_schema=schema["input_schema"],
                    annotations=types.ToolAnnotations(
                        read_only_hint=True,
                        destructive_hint=False,
                        idempotent_hint=True,
                        open_world_hint=False,
                    ),
                )
                for schema in kit.schemas()
            ]
        )

    async def call_tool(_context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            # Tools block on the store, so keep them off the event loop.
            result = await anyio.to_thread.run_sync(kit.call, params.name, params.arguments or {})
        except ToolError as error:
            return failure(error.code, error.message)
        except Exception:
            # Never let an unexpected exception carry internals back to the agent.
            _LOG.exception("tool %s failed unexpectedly", params.name)
            return failure("internal_error", "the tool failed unexpectedly")
        payload = result.to_dict()
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload))],
            structured_content=payload,
        )

    return Server(
        name,
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def serve_stdio(kit: Toolkit, *, name: str = "govgraph") -> None:
    """Serve the tools over standard input and output until the client disconnects."""
    _require_mcp()
    import anyio
    from mcp.server.stdio import stdio_server

    server = build_server(kit, name=name)

    async def run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(run)
