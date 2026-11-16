"""Names, descriptions and JSON-schema signatures of the agent tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from govgraph.ids import ID_PATTERN
from govgraph.tools.limits import IMPACT_MAX_DEPTH, UPSTREAM_MAX_DEPTH

MAX_TERM_LENGTH = 200


@dataclass(frozen=True)
class ToolSpec:
    """How a tool is presented to an agent framework."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def _id(description: str) -> dict[str, Any]:
    return {"type": "string", "pattern": ID_PATTERN, "description": description}


def _depth(default: int, maximum: int) -> dict[str, Any]:
    return {
        "type": "integer",
        "minimum": 1,
        "maximum": maximum,
        "default": default,
        "description": f"How many steps to follow. At most {maximum}.",
    }


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


_CITES = " Read-only. Every statement in the answer cites the IDs of the nodes that support it."

TOOL_SPECS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "get_asset_context",
            "Describe one data asset: what it means, who owns it, which critical data "
            "elements and regulations apply to it, which rules check it, what it is built "
            "from, what is built from it, and any failures recorded on it." + _CITES,
            _object({"asset_id": _id("ID of a DataAsset node.")}, ["asset_id"]),
        ),
        ToolSpec(
            "explain_failure",
            "Explain one data-quality failure: where it happened, which rule and result "
            "raised it, who owns the affected data, what it impacts, and which failures "
            "further upstream may have caused it." + _CITES,
            _object({"failure_id": _id("ID of a DQFailure node.")}, ["failure_id"]),
        ),
        ToolSpec(
            "impact_of",
            "List what would be affected if the given node had a problem, following the "
            "direction in which each kind of relationship passes problems on." + _CITES,
            _object(
                {
                    "entity_id": _id("ID of any node."),
                    "depth": _depth(2, IMPACT_MAX_DEPTH),
                },
                ["entity_id"],
            ),
        ),
        ToolSpec(
            "upstream_causes",
            "Follow lineage upstream from a data asset and list the failures recorded on "
            "the assets it is built from." + _CITES,
            _object(
                {
                    "asset_id": _id("ID of a DataAsset node."),
                    "depth": _depth(3, UPSTREAM_MAX_DEPTH),
                },
                ["asset_id"],
            ),
        ),
        ToolSpec(
            "find_by_term",
            "Find business terms whose name contains the given text, ignoring case, and "
            "list the data assets and critical data elements each one defines." + _CITES,
            _object(
                {
                    "term": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_TERM_LENGTH,
                        "description": "Text to look for in business term names.",
                    }
                },
                ["term"],
            ),
        ),
    )
}
