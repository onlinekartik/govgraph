# govgraph

govgraph turns data-governance metadata into a typed knowledge graph, and gives AI agents
a small set of bounded, auditable tools for reading it.

The metadata is the kind a data catalog holds: tables and columns, business terms, critical
data elements, regulations, owners, data-quality rules and failures, and lineage. An agent
can ask "why did this check fail?" or "what breaks if this table is wrong?" and get an
answer in which every statement cites the graph nodes that support it.

- **Typed.** Node and edge types live in one versioned YAML ontology. Tables, validation,
  graph-engine mappings and tools are all derived from it. You extend it with your own YAML.
- **Bounded.** Every tool call has hard limits on depth, rows and runtime.
- **Read-only.** Tools get a reader that cannot write, run only allow-listed, parameterized
  queries, and never return SQL.
- **Vendor-neutral.** Runs in memory with no server, or on PostgreSQL. Adapters are plugins.

Status: alpha (0.1.0). The API may change before 1.0.

## Quickstart

You need Python 3.10 or later. govgraph is not on a package index yet, so install it from a
checkout:

```console
git clone <this repository> govgraph
cd govgraph
python -m venv .venv && source .venv/bin/activate
pip install .
```

Then run this. It needs no database and no network.

```python
import govgraph as gg
from govgraph.samples import online_store

ontology = gg.load_ontology()  # the built-in core ontology
store = gg.MemoryStore(ontology)  # in-memory graph, no server needed
gg.ingest(online_store(), store)  # a synthetic dataset that ships with the package

tools = gg.Toolkit(store.reader())  # the tools only ever get a read-only reader
failure = gg.node_id("DQFailure", {"failure_id": "F-1002"})

result = tools.explain_failure(failure)
for claim in result.claims:
    print("-", claim.statement)
```

It prints 17 statements. The first and last are:

```text
- DQFailure 'F-1002' (critical, open, detected 2024-03-01T06:20:00+00:00): 310 orders have a missing or negative total.
- Possible cause: DQFailure 'F-1001' (high, open, detected 2024-03-01T06:05:00+00:00) failed on DataAsset 'raw.payments.amount', 2 steps upstream of DataAsset 'marts.order_facts.order_total'
```

Each claim also carries `citations`, the IDs of the nodes it rests on, and
`result.to_dict()` gives the whole answer as JSON. The same thing from the command line:

```console
govgraph tool explain_failure --sample \
  --args "{\"failure_id\": \"$(govgraph id DQFailure failure_id=F-1002)\"}"
```

## How it fits together

| Layer | What it does | Where |
| --- | --- | --- |
| Spec | One declarative ontology is the single source of truth for node and edge types. | [`spec/`](src/govgraph/spec), [docs/ontology.md](docs/ontology.md) |
| Store | PostgreSQL, one table per node and edge type, generated from the spec. Upserts are idempotent and IDs are a hash of the natural key. An in-memory NetworkX store works without a server. | [`store/`](src/govgraph/store) |
| Graph engine | A PuppyGraph schema generated from the same spec, so Cypher and Gremlin run over the SQL tables with no copy. | [`engines/`](src/govgraph/engines) |
| Adapters | Plugins that turn a source into normalized records. They never touch a store. | [`adapters/`](src/govgraph/adapters), [docs/adapters.md](docs/adapters.md) |
| Tools | Five read-only functions with JSON-schema signatures, plus an MCP server. | [`tools/`](src/govgraph/tools), [`mcp/`](src/govgraph/mcp) |
| Viewer | One HTML file that draws a JSON export as an SVG graph. No dependencies. | [`viewer/`](src/govgraph/viewer) |

## The tools

| Tool | Answers | Limit |
| --- | --- | --- |
| `get_asset_context(asset_id)` | What is this asset, who owns it, what governs and checks it, and what is next to it in lineage? | one step, plus regulations |
| `explain_failure(failure_id)` | Where did this failure happen, what raised it, who owns the data, what does it affect, and what upstream may have caused it? | 3 steps upstream |
| `impact_of(entity_id, depth=2)` | What is affected if this node has a problem? | depth at most 3 |
| `upstream_causes(asset_id, depth=3)` | Which failures are recorded on this asset, on its table or columns, and on the assets it is built from? | depth at most 5 |
| `find_by_term(term)` | Which business terms match this text, and what do they define? | text match on term names |

Every call also runs under `Limits(max_rows=200, timeout_s=5.0)`. You can change these up
to fixed ceilings of 1000 rows and 30 seconds. A call that hits a limit returns what it has
found with `truncated` set, and never states a conclusion that needs the missing data. A
depth above a tool's cap is rejected.

To use the tools from an agent framework, register them from their schemas and dispatch
through `call`, which validates the arguments first:

```python
for schema in tools.schemas():  # name, description, input_schema (JSON Schema)
    register_with_your_framework(schema)

result = tools.call("impact_of", {"entity_id": some_id, "depth": 2})  # raises gg.ToolError
```

Every call produces an audit event. By default it is logged as one JSON line to the
`govgraph.audit` logger at INFO level, which your application has to route somewhere to
keep. Pass `Toolkit(reader, audit=your_function)` to handle the events yourself. The
`govgraph tool` and `govgraph mcp` commands write them to standard error, or to a file with
`--audit-log`.

### MCP

```console
pip install ".[mcp]"
govgraph mcp --sample                      # the sample graph, to try a client against
GOVGRAPH_READER_DSN=... govgraph mcp       # your graph in PostgreSQL
```

This serves the same five tools over standard input and output.

## Your own metadata

Adapters read a source and yield records. Bundled ones:

| Adapter | Reads |
| --- | --- |
| `csv` | a directory of `<Type>.csv` files |
| `json` | one JSON document of nodes and edges |
| `dbt` | `manifest.json` and, optionally, `run_results.json` |
| `openmetadata` | a JSON export of tables, glossary terms, test cases and lineage |
| `datahub` | a JSON file of metadata change proposals |

The catalog adapters read exported files. They do not call any API. See
[docs/adapters.md](docs/adapters.md) for the formats and for writing your own.

```python
from govgraph import adapters

adapter = adapters.load("dbt", manifest_path="target/manifest.json")
report = gg.ingest(adapter, store)  # validates every record, then writes
```

`ingest` is the only path from an adapter to a store. By default one invalid record
rejects the whole batch and nothing is written.

## PostgreSQL

```console
pip install ".[postgres]"
export GOVGRAPH_DSN="postgresql://..."          # a role that may create tables and write
govgraph init                                   # creates the schema from the ontology
govgraph ingest dbt -o manifest_path=target/manifest.json
govgraph grant-reader agent_reader              # SELECT only, for an existing role
export GOVGRAPH_READER_DSN="postgresql://..."   # that read-only role
govgraph tool find_by_term --args '{"term": "revenue"}'
```

Connection strings are read from the environment only, never from arguments. A reader
refuses to work with a role that is able to change the graph, so pointing
`GOVGRAPH_READER_DSN` at the writing role fails with an explanation. In Python:

```python
store = gg.PostgresStore(ontology, writer_dsn)
store.create_schema()
gg.ingest(adapter, store)
store.grant_reader("agent_reader")
tools = gg.Toolkit(store.reader(reader_dsn))
```

## PuppyGraph

```console
govgraph puppygraph-schema --out schema.json
```

writes a PuppyGraph schema for the same tables, which you upload to PuppyGraph. Cypher and
Gremlin then query PostgreSQL in place. Node and edge `id` values are the same IDs the tools
cite. An edge type that connects more than one pair of node types becomes one PuppyGraph
label per pair, and its own name works as an alias for all of them, so
`MATCH ()-[:OWNED_BY]->()` matches every kind of ownership.

## Viewer

```console
govgraph export "$(govgraph id DQFailure failure_id=F-1002)" --sample --depth 3 --out graph.json
govgraph viewer --data graph.json --out graph.html
```

`graph.html` is one self-contained file. Open it in a browser to pan, zoom, drag nodes,
filter by type and inspect properties. It loads nothing from the network.

## Example: an online store

[`examples/online_store`](examples/online_store) starts PostgreSQL and PuppyGraph, loads
the synthetic dataset, runs every tool through a read-only role, and checks that a Cypher
query through PuppyGraph gives the same answer as `explain_failure`.

```console
cd examples/online_store
docker compose up --build
```

The PuppyGraph image is about 3.4 GB. No password is stored in the repository: the first
start generates random ones into a Docker volume. To run only the tools, with no services:

```console
python examples/online_store/run_demo.py --memory
```

## Documentation

- [Ontology reference](docs/ontology.md): every node and edge type, and how to extend them.
- [Writing an adapter](docs/adapters.md)
- [Security notes](docs/security.md): what the safety properties are and how they are enforced.
- [Changelog](CHANGELOG.md)

## Development

```console
pip install -e ".[dev]"
ruff check . && ruff format --check .
mypy
pytest
```

Tests use only synthetic data generated in code. The PostgreSQL tests are skipped unless
`GOVGRAPH_TEST_DSN` points at a disposable database; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

[Apache-2.0](LICENSE). Versions follow [Semantic Versioning](https://semver.org/).
