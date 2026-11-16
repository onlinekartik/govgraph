"""The agent tools.

Each tool is a plain method that takes JSON-friendly arguments and returns a
:class:`ToolResult`, so it can be registered with any agent framework. The tools only
ever see a read-only :class:`GraphReader`.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from govgraph.errors import OntologyError, StoreError, ToolError
from govgraph.ids import is_id, node_id
from govgraph.records import Edge, Node
from govgraph.spec.model import Ontology
from govgraph.store.base import Direction, GraphReader
from govgraph.tools.context import BudgetExhaustedError, Step, ToolContext, walk
from govgraph.tools.limits import EXPLAIN_UPSTREAM_DEPTH, Limits
from govgraph.tools.results import ToolResult, Truncation
from govgraph.tools.schemas import TOOL_SPECS

_AUDIT_LOG = logging.getLogger("govgraph.audit")
_LOG = logging.getLogger("govgraph.tools")

# The core ontology types the tools are written against.
DATA_ASSET = "DataAsset"
BUSINESS_TERM = "BusinessTerm"
DQ_FAILURE = "DQFailure"
LINEAGE_EDGE = "LineageEdge"
_REQUIRED_NODE_TYPES = (DATA_ASSET, BUSINESS_TERM, DQ_FAILURE, LINEAGE_EDGE)
_REQUIRED_EDGE_TYPES = (
    "DEFINES",
    "CLASSIFIED_AS",
    "GOVERNED_BY",
    "OWNED_BY",
    "DERIVES_FROM",
    "FAILED_ON",
    "IMPACTS",
    "RESULT_OF",
    "RAISED_FROM",
)


@dataclass(frozen=True)
class AuditEvent:
    """A record of one tool call, emitted whether the call succeeded or not."""

    tool: str
    arguments: Mapping[str, Any]
    outcome: str
    rows: int
    elapsed_ms: float
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "outcome": self.outcome,
            "rows": self.rows,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "error_code": self.error_code,
        }


Relation = Literal["self", "contains", "part"]

# How many levels of containment are followed: column, nested column, table.
CONTAINMENT_LEVELS = 3


@dataclass(frozen=True)
class _Trail:
    """How an asset was reached while looking upstream.

    ``anchor`` is the asset that lineage led to. ``node`` is that asset, an asset that
    contains it or an asset that is part of it, as ``relation`` says. ``path`` runs from
    the asset first asked about to ``node`` and becomes the citations.
    """

    node: Node
    anchor: Node
    relation: Relation
    path: tuple[Node, ...]
    distance: int


def node_id_of_asset(qualified_name: str) -> str:
    return node_id(DATA_ASSET, {"qualified_name": qualified_name})


def _log_audit_event(event: AuditEvent) -> None:
    _AUDIT_LOG.info(json.dumps(event.to_dict(), sort_keys=True))


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _argument_problem(error: ValidationError) -> str:
    """Describe a schema violation without echoing the offending value back."""
    name = ".".join(str(part) for part in error.absolute_path)
    rule = error.validator
    expected: Any = error.validator_value
    given: Any = error.instance
    if rule == "required":
        missing = [key for key in expected if key not in given]
        return f"missing required argument: {', '.join(missing)}"
    if rule == "additionalProperties":
        return "unknown argument"
    if rule == "type":
        return f"{name} must be of type {expected}"
    if rule == "pattern":
        return f"{name} is not a well-formed ID"
    if rule in ("minimum", "maximum"):
        return f"{name} must be at {'least' if rule == 'minimum' else 'most'} {expected}"
    if rule in ("minLength", "maxLength"):
        bound = "at least" if rule == "minLength" else "at most"
        return f"{name} must be {bound} {_plural(expected, 'character')} long"
    return f"{name or 'arguments'} is not valid"


class Toolkit:
    """The bounded, read-only tools an agent uses to traverse the graph."""

    def __init__(
        self,
        reader: GraphReader,
        *,
        limits: Limits | None = None,
        audit: Callable[[AuditEvent], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        ontology = reader.ontology
        missing = [n for n in _REQUIRED_NODE_TYPES if n not in ontology.node_types]
        missing += [e for e in _REQUIRED_EDGE_TYPES if e not in ontology.edge_types]
        if missing:
            raise OntologyError(f"the tools need these core ontology types: {', '.join(missing)}")
        self._reader = reader
        self._ontology: Ontology = ontology
        self._limits = limits or Limits()
        self._audit = audit or _log_audit_event
        self._clock = clock
        self._validators = {
            name: Draft202012Validator(spec.input_schema) for name, spec in TOOL_SPECS.items()
        }
        self._handlers: dict[str, Callable[..., None]] = {
            "get_asset_context": self._get_asset_context,
            "explain_failure": self._explain_failure,
            "impact_of": self._impact_of,
            "upstream_causes": self._upstream_causes,
            "find_by_term": self._find_by_term,
        }

    @property
    def limits(self) -> Limits:
        return self._limits

    # ---- public tools ---------------------------------------------------------------

    def get_asset_context(self, asset_id: str) -> ToolResult:
        """Describe a data asset and everything directly attached to it."""
        return self.call("get_asset_context", {"asset_id": asset_id})

    def explain_failure(self, failure_id: str) -> ToolResult:
        """Explain a data-quality failure, with evidence and possible upstream causes."""
        return self.call("explain_failure", {"failure_id": failure_id})

    def impact_of(self, entity_id: str, depth: int = 2) -> ToolResult:
        """List what a problem at ``entity_id`` would affect, up to 3 steps away."""
        return self.call("impact_of", {"entity_id": entity_id, "depth": depth})

    def upstream_causes(self, asset_id: str, depth: int = 3) -> ToolResult:
        """List failures on the assets upstream of ``asset_id``, up to 5 steps away."""
        return self.call("upstream_causes", {"asset_id": asset_id, "depth": depth})

    def find_by_term(self, term: str) -> ToolResult:
        """Find business terms by name and list what they define."""
        return self.call("find_by_term", {"term": term})

    def schemas(self) -> list[dict[str, Any]]:
        """Return the name, description and JSON-schema input signature of every tool."""
        return [copy.deepcopy(spec.to_dict()) for spec in TOOL_SPECS.values()]

    def call(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        """Validate ``arguments`` against the tool's schema and run the tool.

        Raises :class:`ToolError` if the tool is unknown, the arguments are invalid, the
        requested node does not exist or the store fails. Its message never contains SQL
        or other store internals.
        """
        started = self._clock()
        ctx = ToolContext(self._reader, self._limits, self._clock)
        try:
            limits, truncated_by = self._run(ctx, name, arguments)
        except ToolError as error:
            self._emit(name, arguments, "error", ctx, started, error.code)
            raise
        except Exception as error:
            # A bug in a tool must still be audited, and must not carry internals out.
            _LOG.exception("tool %s failed unexpectedly", name)
            self._emit(name, arguments, "error", ctx, started, "internal_error")
            raise ToolError("internal_error", "the tool failed unexpectedly") from error
        elapsed_ms = (self._clock() - started) * 1000
        result = ctx.result.build(name, limits, elapsed_ms, truncated_by)
        self._emit(name, arguments, "truncated" if result.truncated else "ok", ctx, started)
        return result

    # ---- plumbing -------------------------------------------------------------------

    def _run(
        self, ctx: ToolContext, name: str, arguments: Mapping[str, Any]
    ) -> tuple[dict[str, Any], Truncation | None]:
        handler = self._handlers.get(name) if isinstance(name, str) else None
        if handler is None:
            raise ToolError("unknown_tool", "there is no tool with that name")
        if not isinstance(arguments, Mapping):
            raise ToolError("invalid_input", "arguments must be an object")
        error = next(iter(self._validators[name].iter_errors(dict(arguments))), None)
        if error is not None:
            raise ToolError("invalid_input", _argument_problem(error))
        declared = TOOL_SPECS[name].input_schema["properties"]
        for key, value in arguments.items():
            # JSON Schema counts 2.0 as an integer. Python does not, so be strict here.
            if declared[key]["type"] == "integer" and type(value) is not int:
                raise ToolError("invalid_input", f"{key} must be of type integer")
        limits = {**self._limits.to_dict(), **self._depth_limits(name, arguments)}
        try:
            handler(ctx, **arguments)
        except BudgetExhaustedError as exhausted:
            return limits, exhausted.reason
        except StoreError as error:
            # The agent gets a deliberately vague message, because store errors can carry
            # connection or query details. The operator gets the reason in the log.
            _LOG.warning("tool %s could not read from the store: %s", name, error)
            raise ToolError("store_error", "the graph store could not answer") from error
        return limits, None

    @staticmethod
    def _depth_limits(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """The depth a call runs with and the cap on it, for the tools that walk."""
        if name == "explain_failure":
            return {"upstream_depth": EXPLAIN_UPSTREAM_DEPTH}
        depth = TOOL_SPECS[name].input_schema["properties"].get("depth")
        if depth is None:
            return {}
        return {"depth": arguments.get("depth", depth["default"]), "max_depth": depth["maximum"]}

    def _emit(
        self,
        name: str,
        arguments: Mapping[str, Any],
        outcome: str,
        ctx: ToolContext,
        started: float,
        error_code: str | None = None,
    ) -> None:
        safe_arguments = dict(arguments) if isinstance(arguments, Mapping) else {}
        self._audit(
            AuditEvent(
                tool=str(name),
                arguments=safe_arguments,
                outcome=outcome,
                rows=len(ctx.result.nodes) + len(ctx.result.edges),
                elapsed_ms=(self._clock() - started) * 1000,
                error_code=error_code,
            )
        )

    # ---- wording --------------------------------------------------------------------

    def _describe(self, node: Node) -> str:
        return f"{node.type} '{self._ontology.node_type(node.type).label(node.properties)}'"

    def _edge_sentence(self, ctx: ToolContext, edge: Edge) -> tuple[str, list[Node]] | None:
        """State an edge as a sentence, with the nodes that back it."""
        source, target = ctx.known(edge.source_id), ctx.known(edge.target_id)
        if source is None or target is None:
            return None
        phrase = self._ontology.edge_type(edge.type).phrase
        sentence = f"{self._describe(source)} {phrase} {self._describe(target)}"
        cited = [source, target]
        record = ctx.known(str(edge.properties.get("lineage_id", "")))
        if record is not None:
            sentence += f", as recorded by {self._describe(record)}"
            cited.append(record)
        return sentence, cited

    def _load_ends(self, ctx: ToolContext, edges: Sequence[Edge]) -> None:
        """Read the nodes at both ends of ``edges``, and any lineage records they name."""
        wanted = [end for edge in edges for end in (edge.source_id, edge.target_id)]
        for edge in edges:
            record = edge.properties.get("lineage_id")
            if isinstance(record, str) and is_id(record):
                wanted.append(record)
        ctx.nodes(wanted)

    def _expand(
        self, ctx: ToolContext, nodes: Sequence[Node], edge_type: str, direction: Direction
    ) -> list[Node]:
        """Follow one edge type from ``nodes``, state every edge found, return the far ends."""
        edges = ctx.edges([node.id for node in nodes], edge_type, direction)
        self._load_ends(ctx, edges)
        reached: dict[str, Node] = {}
        for edge in edges:
            stated = self._edge_sentence(ctx, edge)
            if stated is None:
                continue
            ctx.result.claim(stated[0], *stated[1])
            far = ctx.known(edge.target_id if direction == "out" else edge.source_id)
            if far is not None:
                reached[far.id] = far
        return list(reached.values())

    def _parent(self, ctx: ToolContext, asset: Node, announce: bool = True) -> Node | None:
        """The asset that contains ``asset``, for example the table of a column."""
        parent_name = asset.properties.get("parent_qualified_name")
        if not parent_name:
            return None
        found = ctx.nodes([node_id_of_asset(parent_name)])
        if not found:
            return None
        parent = found[0]
        if announce:
            ctx.result.claim(
                f"{self._describe(asset)} is part of {self._describe(parent)}", asset, parent
            )
        return parent

    def _owners(self, ctx: ToolContext, assets: Sequence[Node]) -> None:
        """State who owns each asset, falling back to the owner of its parent."""
        for asset in assets:
            if self._expand(ctx, [asset], "OWNED_BY", "out"):
                continue
            parent = self._parent(ctx, asset)
            if parent is not None:
                self._expand(ctx, [parent], "OWNED_BY", "out")

    def _failure_sentence(self, failure: Node) -> str:
        details = [
            str(failure.properties[name])
            for name in ("severity", "status")
            if name in failure.properties
        ]
        detected = failure.properties.get("detected_at")
        if isinstance(detected, datetime):
            details.append(f"detected {detected.isoformat()}")
        suffix = f" ({', '.join(details)})" if details else ""
        return f"{self._describe(failure)}{suffix}"

    # ---- tools ----------------------------------------------------------------------

    def _get_asset_context(self, ctx: ToolContext, asset_id: str) -> None:
        asset = ctx.require(asset_id, DATA_ASSET)
        kind = asset.properties.get("kind")
        ctx.result.claim(
            f"{self._describe(asset)} is a {kind}" if kind else f"{self._describe(asset)} exists",
            asset,
        )
        # Every edge type the ontology allows on a data asset, extensions included.
        reached: dict[tuple[str, Direction], list[Node]] = {}
        for edge_type in self._ontology.edge_types.values():
            if any(e.source == DATA_ASSET for e in edge_type.endpoints):
                reached[edge_type.name, "out"] = self._expand(ctx, [asset], edge_type.name, "out")
            if any(e.target == DATA_ASSET for e in edge_type.endpoints):
                reached[edge_type.name, "in"] = self._expand(ctx, [asset], edge_type.name, "in")
        self._expand(ctx, reached.get(("CLASSIFIED_AS", "out"), []), "GOVERNED_BY", "out")
        parent = self._parent(ctx, asset)
        if parent is not None and not reached.get(("OWNED_BY", "out")):
            self._expand(ctx, [parent], "OWNED_BY", "out")

    def _explain_failure(self, ctx: ToolContext, failure_id: str) -> None:
        failure = ctx.require(failure_id, DQ_FAILURE)
        summary = failure.properties.get("summary")
        ctx.result.claim(
            self._failure_sentence(failure) + (f": {summary}" if summary else ""), failure
        )
        assets = self._expand(ctx, [failure], "FAILED_ON", "out")
        results = self._expand(ctx, [failure], "RAISED_FROM", "out")
        self._expand(ctx, results, "RESULT_OF", "out")
        self._expand(ctx, [failure], "IMPACTS", "out")
        self._owners(ctx, assets)
        elements = self._expand(ctx, assets, "CLASSIFIED_AS", "out")
        self._expand(ctx, elements, "GOVERNED_BY", "out")

        if not assets:
            ctx.result.note(
                "The failure is not linked to a data asset, so lineage was not searched."
            )
            return
        causes = self._upstream_failures(ctx, assets, EXPLAIN_UPSTREAM_DEPTH, {failure.id})
        if not causes:
            ctx.result.claim(
                "No other failure is recorded on "
                + " or ".join(self._describe(a) for a in assets)
                + ", on the assets that contain it or are part of it, or within "
                + f"{_plural(EXPLAIN_UPSTREAM_DEPTH, 'step')} upstream of them",
                *assets,
            )

    def _upstream_causes(self, ctx: ToolContext, asset_id: str, depth: int = 3) -> None:
        asset = ctx.require(asset_id, DATA_ASSET)
        causes = self._upstream_failures(ctx, [asset], depth, set())
        if not causes:
            ctx.result.claim(
                f"No failure is recorded on {self._describe(asset)}, on the assets that "
                f"contain it or are part of it, or within {_plural(depth, 'step')} "
                "upstream of them",
                asset,
            )

    def _relatives(
        self, ctx: ToolContext, asset: Node, announce: bool = False
    ) -> list[tuple[Node, Relation]]:
        """The assets that contain ``asset`` and the assets that are part of it.

        A table's columns are part of it, and a column's table contains it. Siblings are
        deliberately left out: another column of the same table is neither.
        """
        seen = {asset.id}
        relatives: list[tuple[Node, Relation]] = []
        inner = asset
        for _ in range(CONTAINMENT_LEVELS):
            outer = self._parent(ctx, inner, announce=announce)
            if outer is None or outer.id in seen:
                break
            seen.add(outer.id)
            relatives.append((outer, "contains"))
            inner = outer
        holders = [asset]
        for _ in range(CONTAINMENT_LEVELS):
            parts: list[Node] = []
            for holder in holders:
                name = holder.properties.get("qualified_name")
                if not name:
                    continue
                # The store matches text without regard to case; containment is exact.
                for part in ctx.find(
                    DATA_ASSET, "parent_qualified_name", name, "exact", keep=False
                ):
                    if part.properties.get("parent_qualified_name") == name and part.id not in seen:
                        seen.add(part.id)
                        parts.append(part)
            relatives.extend((part, "part") for part in parts)
            holders = parts
        return relatives

    def _upstream_failures(
        self, ctx: ToolContext, assets: Sequence[Node], depth: int, ignore: set[str]
    ) -> int:
        """State the lineage upstream of ``assets`` and every failure found along it.

        Failures are looked for on each asset reached, on the assets that contain it and
        on the assets that are part of it, because lineage is often recorded between
        tables while failures are recorded on columns, or the other way round.
        """
        trails: dict[str, _Trail] = {}

        def admit(anchors: Sequence[tuple[Node, tuple[Node, ...]]], distance: int) -> list[_Trail]:
            # Assets reached directly come first, so a direct route wins over one through
            # a containing asset.
            level = []
            for anchor, path in anchors:
                if anchor.id not in trails:
                    trails[anchor.id] = _Trail(anchor, anchor, "self", path, distance)
                    level.append(trails[anchor.id])
            for trail in list(level):
                for relative, relation in self._relatives(ctx, trail.node, announce=distance == 0):
                    if relative.id not in trails:
                        route = (*trail.path, relative)
                        trails[relative.id] = _Trail(
                            relative, trail.node, relation, route, distance
                        )
                        level.append(trails[relative.id])
            return level

        level = admit([(asset, (asset,)) for asset in assets], 0)
        found = self._failures_on(ctx, level, ignore)
        for distance in range(1, depth + 1):
            edges = ctx.edges([trail.node.id for trail in level], "DERIVES_FROM", "out")
            self._load_ends(ctx, edges)
            reached: list[tuple[Node, tuple[Node, ...]]] = []
            for edge in edges:
                stated = self._edge_sentence(ctx, edge)
                target = ctx.known(edge.target_id)
                if stated is None or target is None:
                    continue
                ctx.result.claim(stated[0], *stated[1])
                reached.append((target, (*trails[edge.source_id].path, target)))
            level = admit(reached, distance)
            if not level:
                break
            found += self._failures_on(ctx, level, ignore)
        return found

    def _failures_on(self, ctx: ToolContext, level: Sequence[_Trail], ignore: set[str]) -> int:
        by_asset = {trail.node.id: trail for trail in level}
        edges = ctx.edges(list(by_asset), "FAILED_ON", "in")
        ctx.nodes([edge.source_id for edge in edges])
        found = 0
        for edge in edges:
            failure, trail = ctx.known(edge.source_id), by_asset.get(edge.target_id)
            if failure is None or trail is None or failure.id in ignore:
                continue
            ctx.result.claim(
                f"Possible cause: {self._failure_sentence(failure)} failed on "
                f"{self._describe(trail.node)}, {self._where(trail)}",
                *trail.path,
                failure,
            )
            found += 1
        return found

    def _where(self, trail: _Trail) -> str:
        """Say where a failed asset sits relative to the asset that was asked about."""
        if trail.distance == 0:
            return {
                "self": "the asset itself",
                "contains": "the asset that contains it",
                "part": "a part of the asset",
            }[trail.relation]
        upstream = f"{_plural(trail.distance, 'step')} upstream of {self._describe(trail.path[0])}"
        if trail.relation == "self":
            return upstream
        link = "which contains" if trail.relation == "contains" else "a part of"
        return f"{link} {self._describe(trail.anchor)}, {upstream}"

    def _containers(self, ctx: ToolContext, node_ids: Sequence[str]) -> dict[str, str]:
        """Map the ID of each containing asset to the ID of the asset it contains."""
        containers: dict[str, str] = {}
        for part_id in node_ids:
            node = ctx.known(part_id)
            parent_name = node.properties.get("parent_qualified_name") if node else None
            if node is not None and node.type == DATA_ASSET and parent_name:
                containers.setdefault(node_id_of_asset(parent_name), part_id)
        return containers

    def _hop_sentence(
        self, ctx: ToolContext, reached: str, came_from: str, edge: Edge | None
    ) -> tuple[str, list[Node]] | None:
        if edge is not None:
            return self._edge_sentence(ctx, edge)
        part, whole = ctx.known(came_from), ctx.known(reached)
        if part is None or whole is None:
            return None
        return f"{self._describe(part)} is part of {self._describe(whole)}", [part, whole]

    def _impact_of(self, ctx: ToolContext, entity_id: str, depth: int = 2) -> None:
        entity = ctx.require(entity_id)
        steps = [
            Step(edge.name, "out" if edge.propagates == "source_to_target" else "in")
            for edge in self._ontology.edge_types.values()
            if edge.propagates != "none"
        ]
        affected = 0
        # A problem in part of an asset is a problem in the asset, so containment counts
        # as a step too: from a column to its table.
        for state, level in walk(
            ctx, [entity.id], steps, depth, also=lambda ids: self._containers(ctx, ids)
        ):
            for node in level:
                hops = state.hops(node.id)
                self._load_ends(ctx, [edge for _, _, edge in hops if edge is not None])
                route = [self._hop_sentence(ctx, there, here, edge) for there, here, edge in hops]
                stated = [step for step in route if step is not None]
                ctx.result.claim(
                    f"A problem at {self._describe(entity)} affects {self._describe(node)} "
                    f"({_plural(len(hops), 'step')}: {'; '.join(s for s, _ in stated)})",
                    entity,
                    *(cited for _, nodes in stated for cited in nodes),
                )
                affected += 1
        if not affected:
            ctx.result.claim(
                f"Nothing in the graph is affected by {self._describe(entity)} "
                f"within {_plural(depth, 'step')}",
                entity,
            )

    def _find_by_term(self, ctx: ToolContext, term: str) -> None:
        terms = ctx.find(BUSINESS_TERM, "name", term, "contains")
        if not terms:
            ctx.result.note("No business term has a name containing the search text.")
            return
        for found in terms:
            definition = found.properties.get("definition")
            ctx.result.claim(
                f"{self._describe(found)} matches the search text"
                + (f" and means: {definition}" if definition else ""),
                found,
            )
        self._expand(ctx, terms, "DEFINES", "out")
        self._expand(ctx, terms, "OWNED_BY", "out")
