"""The online-store example, end to end.

With no arguments it expects the services from docker-compose.yml: it loads a synthetic
dataset into PostgreSQL, runs every agent tool through a read-only role, then asks
PuppyGraph the same question in Cypher over the same tables and checks the answers agree.

    python run_demo.py --memory

runs only the tools, against an in-memory graph, and needs no services at all.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import govgraph as gg
from govgraph.engines import JdbcConnection, puppygraph_schema
from govgraph.export import export_json
from govgraph.samples import asset, online_store
from govgraph.viewer import render_viewer

SCHEMA = "govgraph"
READER_ROLE = "govgraph_reader"
FAILURE = gg.node_id("DQFailure", {"failure_id": "F-1002"})
ORDER_TOTAL = asset("marts.order_facts.order_total").id


def heading(text: str) -> None:
    print(f"\n=== {text} ===")


def short(node_id: str) -> str:
    """An ID shortened for display. Real callers pass the whole ID to the next tool."""
    node_type, _, digest = node_id.partition(":")
    return f"{node_type}:{digest[:8]}"


def show(result: gg.ToolResult) -> None:
    for claim in result.claims:
        print(f"  - {claim.statement}")
        print(f"      cites: {', '.join(short(node_id) for node_id in claim.citations)}")
    for note in result.notes:
        print(f"  note: {note}")
    limit = f", cut short by the {result.truncated_by} limit" if result.truncated else ""
    print(
        f"  [{len(result.nodes)} nodes, {len(result.edges)} edges, "
        f"{result.elapsed_ms:.1f} ms{limit}]"
    )


def run_tools(kit: gg.Toolkit) -> gg.ToolResult:
    heading("find_by_term('revenue')")
    show(kit.find_by_term("revenue"))
    heading("get_asset_context(marts.order_facts.order_total)")
    show(kit.get_asset_context(ORDER_TOTAL))
    heading("explain_failure(F-1002)")
    explanation = kit.explain_failure(FAILURE)
    show(explanation)
    heading("upstream_causes(reporting.daily_revenue.revenue, depth=5)")
    show(kit.upstream_causes(asset("reporting.daily_revenue.revenue").id, depth=5))
    heading("impact_of(raw.payments, depth=3)")
    show(kit.impact_of(asset("raw.payments").id, depth=3))
    heading("A tool refuses to go past its depth limit")
    try:
        kit.impact_of(asset("raw.payments").id, depth=10)
    except gg.ToolError as error:
        print(f"  refused: {error}")
    return explanation


def write_viewer(reader: gg.GraphReader, directory: Path) -> None:
    heading("Viewer")
    graph = export_json(reader, [FAILURE], depth=3)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "graph.json").write_text(json.dumps(graph, indent=2), encoding="utf-8")
        (directory / "graph.html").write_text(render_viewer(graph), encoding="utf-8")
    except OSError as error:
        print(f"  could not write to {directory}: {error.strerror}")
        return
    print(f"  wrote {len(graph['nodes'])} nodes and {len(graph['edges'])} edges")
    print(f"  open {directory / 'graph.html'} in a browser")


def secret(name: str) -> str:
    return (Path(os.environ["DEMO_SECRETS_DIR"]) / name).read_text(encoding="utf-8").strip()


def prepare_reader_role(admin_dsn: str, password: str) -> None:
    """Create the read-only login role, or reset its password if it already exists."""
    import psycopg
    from psycopg import sql

    with psycopg.connect(admin_dsn) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (READER_ROLE,)
        ).fetchone()
        statement = sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
            sql.SQL("ALTER" if exists else "CREATE"),
            sql.Identifier(READER_ROLE),
            sql.Literal(password),
        )
        connection.execute(statement)


def puppygraph(path: str, body: Any, password: str) -> Any:
    """POST a JSON body to PuppyGraph and return the decoded reply."""
    token = base64.b64encode(f"puppygraph:{password}".encode()).decode()
    request = urllib.request.Request(  # noqa: S310 - the URL comes from our own compose file
        os.environ["DEMO_PUPPYGRAPH_URL"] + path,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "authorization": f"Basic {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as reply:  # noqa: S310
            return json.loads(reply.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        raise SystemExit(f"PuppyGraph answered {error.code} for {path}: {detail}") from error


def cypher(query: str, password: str) -> list[list[Any]]:
    rows = puppygraph("/submitCypher", {"query": query}, password)
    return [row["Values"] for row in rows]


def run_puppygraph(ontology: gg.Ontology, explanation: gg.ToolResult) -> None:
    heading("PuppyGraph: Cypher over the same PostgreSQL tables, no copy")
    password = secret("puppygraph_password")
    jdbc = JdbcConnection(
        f"jdbc:postgresql://{os.environ['DEMO_POSTGRES_HOST']}:5432/postgres",
        READER_ROLE,
        secret("reader_password"),
    )
    schema = puppygraph_schema(ontology, schema=SCHEMA, jdbc=jdbc)
    puppygraph("/schema?allowCatalogUpdate=true", schema, password)
    print(
        f"  uploaded a schema with {len(schema['node'])} node labels and "
        f"{len(schema['edge'])} edge labels, generated from the ontology"
    )

    (nodes,) = cypher("MATCH (n) RETURN count(n)", password)
    (edges,) = cypher("MATCH ()-[r]->() RETURN count(r)", password)
    print(f"  PuppyGraph sees {nodes[0]} nodes and {edges[0]} edges")

    query = (
        "MATCH (f:DQFailure)-[:FAILED_ON]->(:DataAsset)-[:DERIVES_FROM*1..3]->(:DataAsset)"
        "<-[:FAILED_ON]-(cause:DQFailure) "
        "WHERE f.failure_id = 'F-1002' RETURN DISTINCT cause.id"
    )
    print(f"  {query}")
    from_cypher = {row[0] for row in cypher(query, password)}
    from_tool = {
        claim.citations[-1]
        for claim in explanation.claims
        if claim.statement.startswith("Possible cause")
    }
    print(f"  Cypher found upstream failures:           {sorted(from_cypher)}")
    print(f"  explain_failure found upstream failures:  {sorted(from_tool)}")
    if not from_cypher or from_cypher != from_tool:
        raise SystemExit("PuppyGraph and the tools disagree")
    print("  They agree.")


def run_with_services() -> None:
    from psycopg.conninfo import make_conninfo

    host = os.environ["DEMO_POSTGRES_HOST"]
    admin_dsn = make_conninfo(
        host=host, dbname="postgres", user="postgres", password=secret("postgres_password")
    )
    reader_dsn = make_conninfo(
        host=host, dbname="postgres", user=READER_ROLE, password=secret("reader_password")
    )
    ontology = gg.load_ontology()

    heading("Load the synthetic online-store metadata into PostgreSQL")
    store = gg.PostgresStore(ontology, admin_dsn, schema=SCHEMA)
    store.create_schema()
    report = gg.ingest(online_store(), store)
    print(f"  wrote {report.nodes} nodes and {report.edges} edges (safe to repeat)")

    heading("Give the tools a role that can only read")
    prepare_reader_role(admin_dsn, secret("reader_password"))
    store.grant_reader(READER_ROLE)
    try:
        store.reader().connect()
    except gg.UnsafeRoleError:
        print("  the writing role is refused as a reader, as it should be")
    else:
        raise SystemExit("the writing role was accepted as a reader")

    with store.reader(reader_dsn) as reader:
        reader.connect()
        print(f"  connected as {READER_ROLE}")
        explanation = run_tools(gg.Toolkit(reader))
        write_viewer(reader, Path(os.environ.get("DEMO_OUTPUT_DIR", "out")))
    run_puppygraph(ontology, explanation)
    heading("Done")
    print("  PuppyGraph UI: http://localhost:8081 (user: puppygraph)")
    print("  Its generated password:")
    print("    docker compose exec puppygraph cat /run/gg-secrets/puppygraph_password")
    print("  Remove everything, including the generated passwords: docker compose down -v")


def run_in_memory() -> None:
    heading("Load the synthetic online-store metadata into memory")
    store = gg.MemoryStore(gg.load_ontology())
    report = gg.ingest(online_store(), store)
    print(f"  wrote {report.nodes} nodes and {report.edges} edges")
    reader = store.reader()
    run_tools(gg.Toolkit(reader))
    write_viewer(reader, Path(os.environ.get("DEMO_OUTPUT_DIR", "out")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--memory", action="store_true", help="use an in-memory graph only")
    args = parser.parse_args(argv)
    if args.memory:
        run_in_memory()
    else:
        run_with_services()
    return 0


if __name__ == "__main__":
    sys.exit(main())
