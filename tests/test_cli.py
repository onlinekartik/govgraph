from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
import yaml
from mcp import Client, StdioServerParameters

import govgraph as gg
from govgraph.cli import main
from tests.conftest import PostgresSandbox

F1002 = gg.node_id("DQFailure", {"failure_id": "F-1002"})


@pytest.fixture(autouse=True)
def no_database_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GOVGRAPH_DSN", "GOVGRAPH_READER_DSN", "GOVGRAPH_JDBC_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    status = main(list(argv))
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def ok(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    status, out, err = run(capsys, *argv)
    assert (status, err) == (0, "")
    return out


def fails(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    status, out, err = run(capsys, *argv)
    assert (status, out) == (1, "")
    assert err.startswith("error: ")
    return err


def extension_file(tmp_path: Path) -> str:
    path = tmp_path / "extension.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "ontology": {"name": "ext", "version": "0.1.0"},
                "node_types": {
                    "Pipeline": {
                        "key": ["run"],
                        "properties": {"run": {"type": "integer", "required": True}},
                    }
                },
            }
        )
    )
    return str(path)


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out == f"govgraph {gg.__version__}\n"


def test_a_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2


def test_ontology_validate_show_and_docs(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert ok(capsys, "ontology", "validate") == "ok: core 1.0.0, 10 node types, 11 edge types\n"
    extended = ok(capsys, "ontology", "validate", "--extension", extension_file(tmp_path))
    assert "11 node types" in extended
    shown = yaml.safe_load(ok(capsys, "ontology", "show"))
    assert shown == gg.load_ontology().to_dict()
    docs = ok(capsys, "ontology", "docs")
    assert "### DataAsset" in docs
    assert "| `qualified_name` (key) | string | yes |" in docs


def test_ontology_problems_are_reported(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "ontology: {name: ext, version: '0.1.0'}\nedge_types: {LINKS: {endpoints: "
        "[{source: Owner, target: Ghost}]}}\n"
    )
    err = fails(capsys, "ontology", "validate", "--extension", str(bad))
    assert "dangling edge type" in err


def test_ddl_and_puppygraph_schema(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ddl = ok(capsys, "ddl", "--schema", "graph")
    assert ddl.startswith('CREATE SCHEMA IF NOT EXISTS "graph";\n')
    assert ddl.rstrip().endswith(";")

    plain = json.loads(ok(capsys, "puppygraph-schema", "--schema", "graph"))
    assert list(plain) == ["node", "edge"]
    assert plain["node"][0]["dataSourceGroup"]["externalDataSource"]["schema"] == "graph"

    assert "needs --jdbc-user" in fails(capsys, "puppygraph-schema", "--jdbc-uri", "jdbc:x")
    jdbc = ("puppygraph-schema", "--jdbc-uri", "jdbc:postgresql://db/meta", "--jdbc-user", "r")
    assert "set GOVGRAPH_JDBC_PASSWORD" in fails(capsys, *jdbc)
    monkeypatch.setenv("GOVGRAPH_JDBC_PASSWORD", "placeholder-value")
    out = tmp_path / "schema.json"
    assert ok(capsys, *jdbc, "--catalog", "meta", "--out", str(out)) == ""
    catalog = json.loads(out.read_text())["catalog"][0]
    assert (catalog["name"], catalog["jdbc"]["username"]) == ("meta", "r")
    assert catalog["jdbc"] == {
        "username": "r",
        "password": "placeholder-value",
        "jdbcUri": "jdbc:postgresql://db/meta",
        "driverClass": "org.postgresql.Driver",
    }


def test_adapters_and_ids(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert ok(capsys, "adapters").split() == ["csv", "datahub", "dbt", "json", "openmetadata"]
    assert ok(capsys, "id", "DQFailure", "failure_id=F-1002").strip() == F1002
    integer_key = ok(capsys, "id", "Pipeline", "run=7", "--extension", extension_file(tmp_path))
    assert integer_key.strip() == gg.node_id("Pipeline", {"run": 7})
    assert "DataAsset is keyed by: qualified_name" in fails(capsys, "id", "DataAsset", "name=x")
    assert "must look like name=value" in fails(capsys, "id", "DataAsset", "x")
    assert "unknown node type" in fails(capsys, "id", "Ghost", "a=b")


def test_tools_run_on_the_sample_graph(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    schemas = json.loads(ok(capsys, "tools", "--sample"))
    assert schemas[0]["name"] == "get_asset_context"

    result = json.loads(
        ok(capsys, "tool", "find_by_term", "--sample", "--args", '{"term": "revenue"}')
    )
    assert result["claims"][0]["statement"].startswith("BusinessTerm 'Daily Revenue' matches")

    out = tmp_path / "result.json"
    arguments = json.dumps({"failure_id": F1002})
    assert (
        ok(
            capsys,
            "tool",
            "explain_failure",
            "--sample",
            "--args",
            arguments,
            "--max-rows",
            "4",
            "--out",
            str(out),
        )
        == ""
    )
    limited = json.loads(out.read_text())
    assert limited["truncated_by"] == "rows"
    assert limited["limits"]["max_rows"] == 4


def test_tool_errors_are_reported(capsys: pytest.CaptureFixture[str]) -> None:
    assert "unknown_tool" in fails(capsys, "tool", "nope", "--sample")
    assert "--args is not valid JSON" in fails(
        capsys, "tool", "find_by_term", "--sample", "--args", "{"
    )
    assert "missing required argument: term" in fails(capsys, "tool", "find_by_term", "--sample")
    assert "max_rows must be between" in fails(
        capsys, "tool", "find_by_term", "--sample", "--max-rows", "0"
    )
    assert "set GOVGRAPH_READER_DSN" in fails(capsys, "tool", "find_by_term")


def test_export_and_viewer(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    graph = json.loads(ok(capsys, "export", F1002, "--sample", "--depth", "1"))
    assert graph["roots"] == [F1002]
    assert len(graph["nodes"]) == 5

    export, page = tmp_path / "graph.json", tmp_path / "graph.html"
    assert ok(capsys, "export", F1002, "--sample", "--out", str(export)) == ""
    assert ok(capsys, "viewer", "--data", str(export), "--out", str(page)) == ""
    assert '"roots": ["' + F1002 + '"]' in page.read_text()
    assert "const EMBEDDED_GRAPH = null;" in ok(capsys, "viewer")

    assert "depth must be between 0 and 5" in fails(
        capsys, "export", F1002, "--sample", "--depth", "9"
    )
    assert "cannot read the export" in fails(
        capsys, "viewer", "--data", str(tmp_path / "absent.json")
    )
    export.write_text("[]")
    assert "is not a govgraph export" in fails(capsys, "viewer", "--data", str(export))


def test_writing_commands_need_a_connection(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["init"], ["grant-reader", "reader"], ["ingest", "json", "-o", "path=x"]):
        assert "set GOVGRAPH_DSN" in fails(capsys, *argv)


def test_mcp_server_speaks_over_stdio() -> None:
    """Starts the real command as a subprocess and talks to it as an MCP client would."""
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "govgraph", "mcp", "--sample", "--max-rows", "50"]
    )

    async def session() -> tuple[list[str], Any, Any]:
        async with Client(server) as client:
            tools = await client.list_tools()
            found = await client.call_tool("find_by_term", {"term": "customer"})
            refused = await client.call_tool("impact_of", {"entity_id": F1002, "depth": 9})
            return [tool.name for tool in tools.tools], found, refused

    names, found, refused = anyio.run(session)

    assert names == [
        "get_asset_context",
        "explain_failure",
        "impact_of",
        "upstream_causes",
        "find_by_term",
    ]
    assert found.is_error is not True
    assert found.structured_content["limits"]["max_rows"] == 50
    assert found.structured_content["claims"][0]["statement"].startswith(
        "BusinessTerm 'Customer Email' matches"
    )
    assert refused.is_error is True
    assert refused.structured_content["error"]["code"] == "invalid_input"


@pytest.mark.postgres
def test_full_workflow_against_postgresql(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    pg: PostgresSandbox,
    tmp_path: Path,
) -> None:
    schema = ("--schema", pg.schema)
    monkeypatch.setenv("GOVGRAPH_DSN", pg.admin_dsn)
    assert ok(capsys, "init", *schema) == f"schema '{pg.schema}' is ready\n"

    orders = {"type": "DataAsset", "key": {"qualified_name": "shop.orders"}}
    team = {"type": "Owner", "key": {"identifier": "team-a"}}
    document = {
        "nodes": [
            {**orders, "properties": {"kind": "table"}},
            {**team, "properties": {"name": "A"}},
        ],
        "edges": [{"type": "OWNED_BY", "source": orders, "target": team}],
    }
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(document))
    report = json.loads(ok(capsys, "ingest", "json", "-o", f"path={path}", *schema))
    assert report == {"source": "json", "nodes": 2, "edges": 1, "rejected": []}

    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    (csv_dir / "Owner.csv").write_text("identifier,name\nteam-b,B\n")
    (csv_dir / "OWNED_BY.csv").write_text(
        "source_type,source.qualified_name,target_type,target.identifier\n"
        "DataAsset,shop.missing,Owner,team-b\n"
    )
    assert "1 record(s) rejected" in fails(
        capsys, "ingest", "csv", "-o", f"path={csv_dir}", *schema
    )
    skipped = json.loads(
        ok(capsys, "ingest", "csv", "-o", f"path={csv_dir}", "--on-error", "skip", *schema)
    )
    assert (skipped["nodes"], skipped["edges"], len(skipped["rejected"])) == (1, 0, 1)

    asset_id = gg.node_id("DataAsset", {"qualified_name": "shop.orders"})
    arguments = json.dumps({"asset_id": asset_id})
    monkeypatch.setenv("GOVGRAPH_READER_DSN", pg.admin_dsn)
    refused = fails(capsys, "tool", "get_asset_context", "--args", arguments, *schema)
    assert "able to modify the graph" in refused

    assert ok(capsys, "grant-reader", pg.reader_role, *schema) == (
        f"role '{pg.reader_role}' can now read schema '{pg.schema}'\n"
    )
    monkeypatch.setenv("GOVGRAPH_READER_DSN", pg.reader_dsn)
    result = json.loads(ok(capsys, "tool", "get_asset_context", "--args", arguments, *schema))
    assert [c["statement"] for c in result["claims"]] == [
        "DataAsset 'shop.orders' is a table",
        "DataAsset 'shop.orders' is owned by Owner 'A'",
    ]
    exported = json.loads(ok(capsys, "export", asset_id, "--depth", "1", *schema))
    assert len(exported["nodes"]) == 2
