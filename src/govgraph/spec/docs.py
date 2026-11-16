"""Render an ontology as a Markdown reference."""

from __future__ import annotations

from collections.abc import Mapping

from govgraph.spec.model import Ontology, Property

_PROPAGATION = {
    "source_to_target": "a problem at the source affects the target",
    "target_to_source": "a problem at the target affects the source",
    "none": "problems do not pass along this edge",
}


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _property_table(properties: Mapping[str, Property], key: tuple[str, ...] = ()) -> list[str]:
    if not properties:
        return ["No properties.", ""]
    lines = [
        "| Property | Type | Required | Allowed values | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    for prop in properties.values():
        name = f"`{prop.name}`" + (" (key)" if prop.name in key else "")
        allowed = ", ".join(f"`{value}`" for value in prop.enum)
        required = "yes" if prop.required else ""
        lines.append(
            f"| {name} | {prop.type} | {required} | {allowed} | {_cell(prop.description)} |"
        )
    return [*lines, ""]


def ontology_markdown(ontology: Ontology) -> str:
    """Return a Markdown description of every node and edge type in ``ontology``."""
    title = f"`{ontology.name}` {ontology.version}"
    if ontology.extensions:
        title += " with " + ", ".join(f"`{n}` {v}" for n, v in ontology.extensions)
    lines = [f"Ontology {title}.", "", "## Node types", ""]
    for node_type in ontology.node_types.values():
        lines += [
            f"### {node_type.name}",
            "",
            _cell(node_type.description),
            "",
            f"Stored in table `{node_type.table}`. "
            f"Natural key: {', '.join(f'`{field}`' for field in node_type.key)}.",
            "",
            *_property_table(node_type.properties, node_type.key),
        ]
    lines += ["## Edge types", ""]
    for edge_type in ontology.edge_types.values():
        lines += [
            f"### {edge_type.name}",
            "",
            _cell(edge_type.description),
            "",
            f'Stored in table `{edge_type.table}`. Read as "source {edge_type.phrase} target". '
            f"Impact: {_PROPAGATION[edge_type.propagates]}.",
            "",
            "Allowed endpoints:",
            "",
            *[f"- `{e.source}` → `{e.target}`" for e in edge_type.endpoints],
            "",
        ]
        if edge_type.properties:
            lines += _property_table(edge_type.properties)
    return "\n".join(lines).rstrip() + "\n"
