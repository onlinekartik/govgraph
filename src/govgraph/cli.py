"""Command-line interface.

Database connection strings are read from the environment, never from arguments, so they
do not end up in shell history or process listings:

``GOVGRAPH_DSN``           a role that may write, used by ``init``, ``grant-reader``, ``ingest``
``GOVGRAPH_READER_DSN``    a role that may only read, used by ``tool``, ``export``, ``mcp``
``GOVGRAPH_JDBC_PASSWORD`` the password ``puppygraph-schema --jdbc-uri`` puts in the catalog
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from govgraph import __version__, adapters
from govgraph.engines import JdbcConnection, puppygraph_schema
from govgraph.errors import GovGraphError
from govgraph.export import export_json
from govgraph.ids import node_id
from govgraph.ingest import ingest
from govgraph.samples import online_store
from govgraph.spec.docs import ontology_markdown
from govgraph.spec.loader import load_ontology
from govgraph.spec.model import Ontology
from govgraph.store.base import GraphReader
from govgraph.store.ddl import DEFAULT_SCHEMA, create_statements
from govgraph.store.memory import MemoryStore
from govgraph.store.postgres import PostgresReader, PostgresStore
from govgraph.tools import Limits, Toolkit
from govgraph.viewer import render_viewer


class UsageError(GovGraphError):
    """The command line asks for something that cannot be done as given."""


def _env(name: str, purpose: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise UsageError(f"set {name} to {purpose}")
    return value


def _ontology(args: argparse.Namespace) -> Ontology:
    return load_ontology(args.extension)


def _store(args: argparse.Namespace) -> PostgresStore:
    dsn = _env("GOVGRAPH_DSN", "the connection string of a role that may write to the graph")
    return PostgresStore(_ontology(args), dsn, schema=args.schema)


def _reader(args: argparse.Namespace) -> GraphReader:
    ontology = _ontology(args)
    if args.sample:
        store = MemoryStore(ontology)
        ingest(online_store(), store)
        return store.reader()
    dsn = _env(
        "GOVGRAPH_READER_DSN",
        "the connection string of a read-only role, or pass --sample to use the built-in "
        "sample graph",
    )
    reader = PostgresReader(ontology, dsn, schema=args.schema)
    reader.connect()
    return reader


def _toolkit(args: argparse.Namespace) -> Toolkit:
    return Toolkit(_reader(args), limits=Limits(max_rows=args.max_rows, timeout_s=args.timeout))


def _pairs(items: Sequence[str], what: str) -> dict[str, str]:
    pairs = {}
    for item in items:
        name, separator, value = item.partition("=")
        if not separator or not name:
            raise UsageError(f"{what} must look like name=value, got {item!r}")
        pairs[name] = value
    return pairs


def _write(text: str, out: str | None) -> None:
    if out is None:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    else:
        Path(out).write_text(text, encoding="utf-8")


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=False)


# ---- commands ----------------------------------------------------------------------


def _cmd_ontology(args: argparse.Namespace) -> None:
    ontology = _ontology(args)
    if args.action == "validate":
        _write(
            f"ok: {ontology.name} {ontology.version}, {len(ontology.node_types)} node types, "
            f"{len(ontology.edge_types)} edge types",
            None,
        )
    elif args.action == "show":
        _write(yaml.safe_dump(ontology.to_dict(), sort_keys=False), None)
    else:
        _write(ontology_markdown(ontology), None)


def _cmd_ddl(args: argparse.Namespace) -> None:
    _write(";\n".join(create_statements(_ontology(args), args.schema)) + ";", None)


def _cmd_puppygraph_schema(args: argparse.Namespace) -> None:
    jdbc = None
    if args.jdbc_uri:
        if not args.jdbc_user:
            raise UsageError("--jdbc-uri needs --jdbc-user")
        password = _env("GOVGRAPH_JDBC_PASSWORD", "the password of the JDBC user")
        jdbc = JdbcConnection(args.jdbc_uri, args.jdbc_user, password)
    document = puppygraph_schema(
        _ontology(args), catalog=args.catalog, schema=args.schema, jdbc=jdbc
    )
    _write(_json(document), args.out)


def _cmd_adapters(_args: argparse.Namespace) -> None:
    _write("\n".join(adapters.available()), None)


def _cmd_id(args: argparse.Namespace) -> None:
    ontology = _ontology(args)
    declared = ontology.node_type(args.type)
    key: dict[str, str | int] = {}
    for name, value in _pairs(args.key, "a key field").items():
        prop = declared.properties.get(name)
        key[name] = int(value) if prop is not None and prop.type == "integer" else value
    if set(key) != set(declared.key):
        raise UsageError(f"{args.type} is keyed by: {', '.join(declared.key)}")
    _write(node_id(args.type, key), None)


def _cmd_init(args: argparse.Namespace) -> None:
    _store(args).create_schema()
    _write(f"schema '{args.schema}' is ready", None)


def _cmd_grant_reader(args: argparse.Namespace) -> None:
    _store(args).grant_reader(args.role)
    _write(f"role '{args.role}' can now read schema '{args.schema}'", None)


def _cmd_ingest(args: argparse.Namespace) -> None:
    store = _store(args)
    options = _pairs(args.option, "an adapter option")
    adapter = adapters.load(args.adapter, ontology=store.ontology, **options)
    report = ingest(adapter, store, on_error=args.on_error)
    _write(
        _json(
            {
                "source": report.source,
                "nodes": report.nodes,
                "edges": report.edges,
                "rejected": list(report.rejected),
            }
        ),
        None,
    )


def _cmd_tools(args: argparse.Namespace) -> None:
    _write(_json(_toolkit(args).schemas()), None)


def _cmd_tool(args: argparse.Namespace) -> None:
    try:
        arguments = json.loads(args.args)
    except json.JSONDecodeError as exc:
        raise UsageError(f"--args is not valid JSON: {exc}") from exc
    _write(_json(_toolkit(args).call(args.name, arguments).to_dict()), args.out)


def _cmd_export(args: argparse.Namespace) -> None:
    limits = Limits(max_rows=args.max_rows, timeout_s=args.timeout)
    try:
        graph = export_json(_reader(args), args.root, depth=args.depth, limits=limits)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc
    _write(_json(graph), args.out)


def _cmd_viewer(args: argparse.Namespace) -> None:
    graph = None
    if args.data:
        try:
            graph = json.loads(Path(args.data).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UsageError(f"cannot read the export {args.data}: {exc}") from exc
        if not isinstance(graph, dict):
            raise UsageError(f"{args.data} is not a govgraph export")
    _write(render_viewer(graph), args.out)


def _cmd_mcp(args: argparse.Namespace) -> None:
    from govgraph.mcp import serve_stdio

    serve_stdio(_toolkit(args))


# ---- parser ------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--extension",
        action="append",
        default=[],
        metavar="FILE",
        help="ontology extension YAML to merge into the core ontology (repeatable)",
    )
    common.add_argument(
        "--schema", default=DEFAULT_SCHEMA, help="PostgreSQL schema (default: %(default)s)"
    )

    reading = argparse.ArgumentParser(add_help=False)
    reading.add_argument(
        "--sample",
        action="store_true",
        help="use the built-in sample graph held in memory instead of PostgreSQL",
    )
    reading.add_argument("--max-rows", type=int, default=Limits().max_rows, metavar="N")
    reading.add_argument("--timeout", type=float, default=Limits().timeout_s, metavar="SECONDS")

    parser = argparse.ArgumentParser(
        prog="govgraph",
        description="Data-governance metadata as a typed knowledge graph with bounded tools.",
        epilog=__doc__.split("\n", 2)[2] if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"govgraph {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, handler: Any, text: str, *parents: argparse.ArgumentParser) -> Any:
        command = commands.add_parser(name, help=text, description=text, parents=[common, *parents])
        command.set_defaults(handler=handler)
        return command

    ontology = add("ontology", _cmd_ontology, "validate, print or document the ontology")
    ontology.add_argument("action", choices=["validate", "show", "docs"])

    add("ddl", _cmd_ddl, "print the PostgreSQL DDL generated from the ontology")

    puppy = add(
        "puppygraph-schema", _cmd_puppygraph_schema, "print the PuppyGraph schema for the tables"
    )
    puppy.add_argument("--catalog", default="govgraph", help="PuppyGraph catalog name")
    puppy.add_argument("--jdbc-uri", help="also define the catalog, connecting to this JDBC URI")
    puppy.add_argument("--jdbc-user", help="database user for --jdbc-uri; use a read-only role")
    puppy.add_argument("--out", metavar="FILE", help="write to a file instead of standard output")

    add("adapters", _cmd_adapters, "list the installed adapters")

    identifier = add("id", _cmd_id, "print the ID of a node from its type and natural key")
    identifier.add_argument("type", help="node type, for example DataAsset")
    identifier.add_argument("key", nargs="+", metavar="FIELD=VALUE")

    add("init", _cmd_init, "create or extend the PostgreSQL schema for the ontology")

    grant = add("grant-reader", _cmd_grant_reader, "give an existing role read-only access")
    grant.add_argument("role")

    load = add("ingest", _cmd_ingest, "load metadata from an adapter into PostgreSQL")
    load.add_argument("adapter", help="adapter name; see 'govgraph adapters'")
    load.add_argument("-o", "--option", action="append", default=[], metavar="NAME=VALUE")
    load.add_argument("--on-error", choices=["raise", "skip"], default="raise")

    add("tools", _cmd_tools, "print the JSON-schema signatures of the agent tools", reading)

    tool = add("tool", _cmd_tool, "call one agent tool and print its result", reading)
    tool.add_argument("name")
    tool.add_argument("--args", default="{}", metavar="JSON", help="tool arguments")
    tool.add_argument("--out", metavar="FILE")

    export = add("export", _cmd_export, "export the graph around some nodes as JSON", reading)
    export.add_argument("root", nargs="+", metavar="NODE_ID")
    export.add_argument("--depth", type=int, default=2)
    export.add_argument("--out", metavar="FILE")

    viewer = add("viewer", _cmd_viewer, "write the standalone HTML viewer")
    viewer.add_argument("--data", metavar="FILE", help="a JSON export to embed in the page")
    viewer.add_argument("--out", metavar="FILE")

    add("mcp", _cmd_mcp, "serve the agent tools over MCP on standard input and output", reading)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line. Returns the process exit status."""
    args = _parser().parse_args(argv)
    try:
        args.handler(args)
    except (GovGraphError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
