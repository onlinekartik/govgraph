"""Documentation that is generated, or that quotes code, must match the code."""

from __future__ import annotations

import re
from pathlib import Path

import govgraph as gg
from govgraph.spec.docs import ontology_markdown

ROOT = Path(__file__).resolve().parents[1]
BEGIN, END = "<!-- BEGIN GENERATED -->\n", "<!-- END GENERATED -->"


def test_ontology_reference_is_up_to_date(ontology: gg.Ontology) -> None:
    page = (ROOT / "docs" / "ontology.md").read_text(encoding="utf-8")
    generated = page[page.index(BEGIN) + len(BEGIN) : page.index(END)]
    assert generated == ontology_markdown(ontology), (
        "docs/ontology.md is stale: regenerate the section with 'govgraph ontology docs'"
    )


def test_reference_covers_every_type_and_property(ontology: gg.Ontology) -> None:
    text = ontology_markdown(ontology)
    for node_type in ontology.node_types.values():
        assert f"### {node_type.name}\n" in text
        assert f"`{node_type.table}`" in text
        for name in node_type.properties:
            assert f"| `{name}`" in text
    for edge_type in ontology.edge_types.values():
        assert f"### {edge_type.name}\n" in text
        for endpoint in edge_type.endpoints:
            assert f"- `{endpoint.source}` → `{endpoint.target}`" in text
    assert "| `qualified_name` (key) | string | yes |" in text
    assert "`open`, `acknowledged`, `resolved`" in text


def test_reference_names_extensions() -> None:
    extended = gg.load_ontology(
        [
            {
                "ontology": {"name": "ext", "version": "0.2.0"},
                "edge_types": {
                    "SENT_TO": {
                        "description": "A | B",
                        "endpoints": [{"source": "Owner", "target": "Owner"}],
                    }
                },
            }
        ]
    )
    text = ontology_markdown(extended)
    assert text.startswith("Ontology `core` 1.0.0 with `ext` 0.2.0.\n")
    assert "A \\| B" in text
    assert "problems do not pass along this edge" in text


def test_extension_example_in_the_docs_loads() -> None:
    import yaml

    page = (ROOT / "docs" / "ontology.md").read_text(encoding="utf-8")
    block = re.search(r"```yaml\n(ontology:\n  name: pipelines.*?)```", page, re.S)
    assert block is not None
    ontology = gg.load_ontology([yaml.safe_load(block.group(1))])
    assert ontology.edge_type("PRODUCED_BY").propagates == "target_to_source"
    assert ontology.edge_type("OWNED_BY").allows("Pipeline", "Owner")
    assert ontology.node_type("DataAsset").properties["row_count"].type == "integer"


def test_quickstart_in_the_readme_runs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    block = re.search(
        r"```python\n(import govgraph as gg\nfrom govgraph\.samples.*?)```", readme, re.S
    )
    assert block is not None
    namespace: dict[str, object] = {}
    exec(compile(block.group(1), "README quickstart", "exec"), namespace)  # noqa: S102
    result = namespace["result"]
    assert isinstance(result, gg.ToolResult)
    quoted = re.search(r"```text\n(.*?)```", readme, re.S)
    assert quoted is not None
    first, last = quoted.group(1).strip().splitlines()
    assert first == f"- {result.claims[0].statement}"
    assert last == f"- {result.claims[-1].statement}"
    assert f"It prints {len(result.claims)} statements." in readme


def test_no_markdown_link_is_broken() -> None:
    pages = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *sorted((ROOT / "docs").glob("*.md"))]
    for page in pages:
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", page.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("mailto:"):
                continue
            assert (page.parent / target).exists(), f"{page.name} links to missing {target}"
