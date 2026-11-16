from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

import govgraph as gg
from govgraph.spec.model import snake_case

CORE_NODES = {
    "DataAsset",
    "BusinessTerm",
    "CriticalDataElement",
    "Regulation",
    "DataProduct",
    "Owner",
    "DQRule",
    "DQResult",
    "DQFailure",
    "LineageEdge",
}
REQUESTED_EDGES = {
    "DEFINES",
    "CLASSIFIED_AS",
    "GOVERNED_BY",
    "OWNED_BY",
    "VALIDATES",
    "PROTECTS",
    "DERIVES_FROM",
    "FAILED_ON",
    "IMPACTS",
}


def extension(**sections: Any) -> dict[str, Any]:
    return {"ontology": {"name": "ext", "version": "0.1.0"}, **sections}


def problems_of(*documents: Any, include_core: bool = True) -> tuple[str, ...]:
    with pytest.raises(gg.OntologyError) as caught:
        gg.load_ontology(documents, include_core=include_core)
    return caught.value.problems


def test_core_ontology_has_the_documented_types(ontology: gg.Ontology) -> None:
    assert ontology.name == "core"
    assert ontology.version == "1.0.0"
    assert set(ontology.node_types) == CORE_NODES
    assert set(ontology.edge_types) >= REQUESTED_EDGES
    assert set(ontology.edge_types) - REQUESTED_EDGES == {"RESULT_OF", "RAISED_FROM"}


def test_every_core_type_is_described_and_keyed(ontology: gg.Ontology) -> None:
    for node_type in ontology.node_types.values():
        assert node_type.description
        assert node_type.key
        assert node_type.display in node_type.properties
    for edge_type in ontology.edge_types.values():
        assert edge_type.description
        assert edge_type.phrase
        assert edge_type.endpoints


def test_ontology_is_immutable(ontology: gg.Ontology) -> None:
    with pytest.raises(TypeError):
        ontology.node_types["Extra"] = ontology.node_types["Owner"]  # type: ignore[index]
    with pytest.raises(AttributeError):
        ontology.version = "2.0.0"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("DataAsset", "data_asset"),
        ("DQRule", "dq_rule"),
        ("CriticalDataElement", "critical_data_element"),
        ("OWNED_BY", "owned_by"),
        ("Level2Check", "level2_check"),
    ],
)
def test_snake_case(name: str, expected: str) -> None:
    assert snake_case(name) == expected


def test_table_names_come_from_type_names(ontology: gg.Ontology) -> None:
    assert ontology.node_type("DQFailure").table == "node_dq_failure"
    assert ontology.edge_type("DERIVES_FROM").table == "edge_derives_from"


def test_unknown_types_raise(ontology: gg.Ontology) -> None:
    with pytest.raises(gg.OntologyError, match="unknown node type 'Nope'"):
        ontology.node_type("Nope")
    with pytest.raises(gg.OntologyError, match="unknown edge type 'NOPE'"):
        ontology.edge_type("NOPE")


def test_edge_types_from_and_to(ontology: gg.Ontology) -> None:
    outgoing = {e.name for e in ontology.edge_types_from("DQFailure")}
    incoming = {e.name for e in ontology.edge_types_to("Owner")}
    assert outgoing == {"FAILED_ON", "IMPACTS", "RAISED_FROM"}
    assert incoming == {"OWNED_BY"}


def test_extension_adds_types_properties_and_endpoints(tmp_path: Path) -> None:
    path = tmp_path / "extension.yaml"
    path.write_text(
        yaml.safe_dump(
            extension(
                node_types={
                    "Pipeline": {
                        "description": "A scheduled job.",
                        "key": ["name"],
                        "properties": {"name": {"type": "string", "required": True}},
                    },
                    "DataAsset": {"properties": {"row_count": {"type": "integer"}}},
                },
                edge_types={
                    "PRODUCED_BY": {
                        "phrase": "is produced by",
                        "propagates": "target_to_source",
                        "endpoints": [{"source": "DataAsset", "target": "Pipeline"}],
                    },
                    "OWNED_BY": {"endpoints": [{"source": "Pipeline", "target": "Owner"}]},
                },
            )
        )
    )
    ontology = gg.load_ontology([path])

    assert ontology.extensions == (("ext", "0.1.0"),)
    assert ontology.node_type("Pipeline").display == "name"
    assert ontology.node_type("DataAsset").properties["row_count"].type == "integer"
    assert ontology.node_type("DataAsset").key == ("qualified_name",)
    assert ontology.edge_type("PRODUCED_BY").propagates == "target_to_source"
    assert ontology.edge_type("OWNED_BY").allows("Pipeline", "Owner")
    assert ontology.edge_type("OWNED_BY").allows("DataAsset", "Owner")


def test_edge_phrase_defaults_to_the_type_name() -> None:
    ontology = gg.load_ontology(
        [extension(edge_types={"SENT_TO": {"endpoints": [{"source": "Owner", "target": "Owner"}]}})]
    )
    assert ontology.edge_type("SENT_TO").phrase == "sent to"
    assert ontology.edge_type("SENT_TO").propagates == "none"


def test_merged_ontology_round_trips_through_to_dict(ontology: gg.Ontology) -> None:
    reloaded = gg.load_ontology([ontology.to_dict()], include_core=False)
    assert reloaded.to_dict() == ontology.to_dict()


def test_repeating_an_identical_definition_is_allowed(ontology: gg.Ontology) -> None:
    document = copy.deepcopy(ontology.to_dict())
    assert gg.load_ontology([document]).to_dict() == ontology.to_dict()


def test_rejects_dangling_edge_type() -> None:
    problems = problems_of(
        extension(edge_types={"SENT_TO": {"endpoints": [{"source": "Owner", "target": "Ghost"}]}})
    )
    assert problems == ("SENT_TO: dangling edge type, target node type 'Ghost' is not defined",)


def test_rejects_edge_type_without_endpoints() -> None:
    problems = problems_of(extension(edge_types={"SENT_TO": {"phrase": "is sent to"}}))
    assert problems == ("SENT_TO: an edge type needs at least one endpoint",)


@pytest.mark.parametrize(
    ("document", "fragment"),
    [
        ({"node_types": {}}, "'ontology' is a required property"),
        (extension(node_types={"lower": {"key": ["a"]}}), "'lower' does not match"),
        (extension(edge_types={"Mixed_Case": {}}), "'Mixed_Case' does not match"),
        (extension(surprise=1), "Additional properties are not allowed"),
        (
            extension(node_types={"Thing": {"key": ["a"], "properties": {"a": {"type": "text"}}}}),
            "'text' is not one of",
        ),
        (
            extension(node_types={"Thing": {"key": ["a"], "properties": {"Bad Name": {}}}}),
            "'Bad Name' does not match",
        ),
        ({"ontology": {"name": "ext", "version": "1"}}, "'1' does not match"),
    ],
)
def test_rejects_documents_that_break_the_schema(document: Any, fragment: str) -> None:
    assert any(fragment in problem for problem in problems_of(document))


@pytest.mark.parametrize(
    ("node_type", "fragment"),
    [
        ({"properties": {"a": {"type": "string"}}}, "Thing: a node type needs a 'key'"),
        (
            {"key": ["missing"], "properties": {"a": {"type": "string", "required": True}}},
            "Thing: key field 'missing' is not a declared property",
        ),
        (
            {"key": ["a"], "properties": {"a": {"type": "string"}}},
            "Thing: key field 'a' must be required",
        ),
        (
            {"key": ["a"], "properties": {"a": {"type": "number", "required": True}}},
            "Thing: key field 'a' must be a string or an integer",
        ),
        (
            {
                "key": ["a"],
                "display": "missing",
                "properties": {"a": {"type": "string", "required": True}},
            },
            "Thing: display property 'missing' is not a declared property",
        ),
        (
            {
                "key": ["a"],
                "properties": {
                    "a": {"type": "string", "required": True},
                    "source_id": {"type": "string"},
                },
            },
            "Thing.source_id: 'source_id' is a reserved property name",
        ),
        (
            {
                "key": ["a"],
                "properties": {
                    "a": {"type": "string", "required": True},
                    "n": {"type": "integer", "enum": ["1"]},
                },
            },
            "Thing.n: 'enum' is only allowed on string properties",
        ),
    ],
)
def test_rejects_inconsistent_node_types(node_type: dict[str, Any], fragment: str) -> None:
    assert fragment in problems_of(extension(node_types={"Thing": node_type}))


@pytest.mark.parametrize(
    ("section", "fragment"),
    [
        (
            {"node_types": {"Owner": {"key": ["name"]}}},
            "Owner cannot change 'key' of an existing type",
        ),
        (
            {"node_types": {"Owner": {"display": "kind"}}},
            "Owner cannot change 'display' of an existing type",
        ),
        (
            {"node_types": {"Owner": {"properties": {"name": {"type": "integer"}}}}},
            "Owner.name is already defined differently",
        ),
        (
            {"edge_types": {"OWNED_BY": {"propagates": "none"}}},
            "OWNED_BY cannot change 'propagates' of an existing type",
        ),
        (
            {"edge_types": {"OWNED_BY": {"phrase": "belongs to"}}},
            "OWNED_BY cannot change 'phrase' of an existing type",
        ),
    ],
)
def test_extension_cannot_change_existing_definitions(
    section: dict[str, Any], fragment: str
) -> None:
    assert any(fragment in problem for problem in problems_of(extension(**section)))


def test_rejects_types_whose_tables_collide_or_are_too_long() -> None:
    keyed = {"key": ["a"], "properties": {"a": {"type": "string", "required": True}}}
    problems = problems_of(
        extension(node_types={"DQRule": keyed, "DqRule": keyed, "A" + "b" * 60: keyed}),
        include_core=False,
    )
    assert "DqRule: table name 'node_dq_rule' collides with DQRule" in problems
    assert any("is longer than 50 characters" in problem for problem in problems)


def test_reports_every_problem_at_once() -> None:
    error_documents = extension(
        node_types={"Thing": {"properties": {}}},
        edge_types={"SENT_TO": {"endpoints": [{"source": "Ghost", "target": "Thing"}]}},
    )
    problems = problems_of(error_documents)
    assert len(problems) == 2
    with pytest.raises(gg.OntologyError) as caught:
        gg.load_ontology([error_documents])
    assert str(caught.value).startswith("invalid ontology: ")


def test_long_problem_lists_are_summarised() -> None:
    bad: dict[str, Any] = {f"Thing{i}": {"properties": {}} for i in range(7)}
    with pytest.raises(gg.OntologyError, match=r"\(and 2 more\)"):
        gg.load_ontology([extension(node_types=bad)])


def test_rejects_unreadable_and_malformed_files(tmp_path: Path) -> None:
    with pytest.raises(gg.OntologyError, match="cannot read ontology file"):
        gg.load_ontology([tmp_path / "absent.yaml"])
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with pytest.raises(gg.OntologyError, match="None is not of type 'object'"):
        gg.load_ontology([empty])
    broken = tmp_path / "broken.yaml"
    broken.write_text("ontology: [unclosed")
    with pytest.raises(gg.OntologyError, match="is not valid YAML"):
        gg.load_ontology([broken])


def test_rejects_an_empty_document_list() -> None:
    with pytest.raises(gg.OntologyError, match="no ontology documents"):
        gg.load_ontology(include_core=False)
