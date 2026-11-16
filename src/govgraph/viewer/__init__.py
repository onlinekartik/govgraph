"""A dependency-free graph viewer: one HTML file that draws a JSON export as SVG."""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib import resources
from typing import Any

_DATA_MARK = "/*__GOVGRAPH_DATA__*/null"
_SCRIPT_MARK = "/*__GOVGRAPH_VIEWER_JS__*/"


def _embeddable(graph: Mapping[str, Any]) -> str:
    """Serialise ``graph`` so that no value can end the surrounding ``<script>`` element."""
    text = json.dumps(graph, ensure_ascii=True, sort_keys=True)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_viewer(graph: Mapping[str, Any] | None = None) -> str:
    """Return a self-contained HTML page.

    With ``graph`` (the output of :func:`govgraph.export.export_json`) the page shows that
    graph when opened. Without it, the page asks for a JSON export file to load.
    """
    files = resources.files("govgraph.viewer")
    page = files.joinpath("viewer.html").read_text("utf-8")
    script = files.joinpath("viewer.js").read_text("utf-8")
    if _DATA_MARK not in page or _SCRIPT_MARK not in page:
        raise RuntimeError("the viewer template is missing its placeholders")
    page = page.replace(_SCRIPT_MARK, script)
    return page.replace(_DATA_MARK, "null" if graph is None else _embeddable(graph))
