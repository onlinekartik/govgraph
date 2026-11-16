# Adapters

An adapter turns metadata from one source into normalized records. It only yields
records. It is never given a store, so it cannot write to one, and
[`govgraph.ingest`](../src/govgraph/ingest.py) is the single path from an adapter to a
store.

## Bundled adapters

| Name | Options | Input |
| --- | --- | --- |
| `json` | `path` | One JSON document: `{"nodes": [...], "edges": [...]}`. |
| `csv` | `path` | A directory with one `<Type>.csv` per node or edge type. |
| `dbt` | `manifest_path`, `run_results_path` (optional) | dbt's `manifest.json` and `run_results.json`. |
| `openmetadata` | `path` | A JSON export: `{"tables": [...], "glossaryTerms": [...], "testCases": [...], "lineage": [...]}`. |
| `datahub` | `path` | A JSON list of metadata change proposals, as DataHub's file sink writes. |

```console
govgraph adapters
govgraph ingest csv -o path=./metadata
```

```python
from govgraph import adapters

adapter = adapters.load(
    "dbt", manifest_path="target/manifest.json", run_results_path="target/run_results.json"
)
```

The exact mapping from each source to node and edge types is in the docstring at the top of
each module in [`src/govgraph/adapters`](../src/govgraph/adapters).

The `dbt`, `openmetadata` and `datahub` adapters read files that you export. They make no
network calls and need no client library. They cover the parts of each format listed in
their docstrings and skip the rest. They are tested against small exports written for the
tests, not against a live installation of those tools, so check the result on your own
export before relying on it.

### JSON

```json
{
  "nodes": [
    {"type": "DataAsset", "key": {"qualified_name": "shop.orders"}, "properties": {"kind": "table"}},
    {"type": "Owner", "key": {"identifier": "team-a"}, "properties": {"name": "Team A"}}
  ],
  "edges": [
    {"type": "OWNED_BY",
     "source": {"type": "DataAsset", "key": {"qualified_name": "shop.orders"}},
     "target": {"type": "Owner", "key": {"identifier": "team-a"}}}
  ]
}
```

### CSV

A node file has one column per property:

```csv
qualified_name,name,kind
shop.orders,orders,table
```

An edge file names each end by its key, as `source.<key field>` and `target.<key field>`.
If the edge type allows more than one pair of node types, add `source_type` and
`target_type`. Any other column is an edge property. Empty cells mean "not set".

```csv
source_type,source.qualified_name,target_type,target.identifier
DataAsset,shop.orders,Owner,team-a
```

Cells are converted to the type the ontology declares, so `12` in an integer column is
loaded as a number.

## Records

```python
import govgraph as gg

table = gg.NodeRecord("DataAsset", {"qualified_name": "shop.orders"}, {"kind": "table"})
team = gg.NodeRecord("Owner", {"identifier": "team-a"}, {"name": "Team A"})
owns = gg.EdgeRecord("OWNED_BY", table.ref(), team.ref())
```

- `NodeRecord(type, key, properties)`: `key` holds the natural-key properties, `properties`
  the rest. A property set to `None` is left out.
- `EdgeRecord(type, source, target, properties)`: `source` and `target` are `NodeRef(type,
  key)`. `record.ref()` gives you one.
- IDs are derived from the type and key. You never assign them.

## What ingest does with your records

1. It checks every record against the ontology: the type exists, the key has exactly the
   key fields, every property is declared and has the right type, enum values are allowed,
   and an edge joins an allowed pair of node types.
2. It checks that both ends of every edge exist, either in the same batch or already in
   the store.
3. If anything was rejected it raises `IngestError`, listing the problems, and writes
   nothing. With `on_error="skip"` it writes the valid records and returns the rejections
   in the report.

Writing is an idempotent merge. Loading the same records again changes nothing. A property
that a record does not supply keeps the value it already has, so two adapters can each
contribute part of the same node. The other side of that is that an adapter cannot clear a
value by leaving it out.

Because of the merge, an adapter may emit a node with only its key when it needs the node to
exist. The bundled catalog adapters do this for assets that lineage refers to but the export
does not describe.

## Writing an adapter

An adapter is any object with a `name` and a `records()` method that yields records. There
is no base class to inherit from.

```python
from collections.abc import Iterator

import govgraph as gg


class SpreadsheetAdapter:
    """Reads the ownership spreadsheet the data office keeps."""

    name = "spreadsheet"

    def __init__(self, path: str) -> None:
        self._path = path

    def records(self) -> Iterator[gg.Record]:
        for qualified_name, team in self._rows():
            table = gg.NodeRecord("DataAsset", {"qualified_name": qualified_name})
            owner = gg.NodeRecord("Owner", {"identifier": team}, {"name": team, "kind": "team"})
            yield table
            yield owner
            yield gg.EdgeRecord("OWNED_BY", table.ref(), owner.ref())

    def _rows(self) -> Iterator[tuple[str, str]]: ...  # read self._path
```

Use it directly:

```python
gg.ingest(SpreadsheetAdapter("owners.xlsx"), store)
```

To make it available by name, to `adapters.load` and to `govgraph ingest`, register it as an
entry point in the `govgraph.adapters` group of your own package:

```toml
[project.entry-points."govgraph.adapters"]
spreadsheet = "my_package.adapters:SpreadsheetAdapter"
```

Options given as `-o name=value` on the command line arrive as string keyword arguments to
the constructor. If the constructor has a parameter called `ontology`, it is passed the
ontology in use, including extensions.

### Guidelines

- **Yield, do not write.** Do not open a connection to the graph store. If you find you
  need to read the graph, that logic belongs in ingest or in a tool.
- **Make keys stable.** IDs come from keys, so a key that changes between runs creates a
  second node. Prefer the fully qualified name a system already uses.
- **Be lazy.** Read the source inside `records()`, not in the constructor or at import
  time. Importing an adapter must not touch the network or the disk.
- **Leave personal data behind** unless you need it. The dbt adapter, for example, keeps an
  owner's name and drops the email address.
- **Raise `gg.AdapterError`** with a message that says which file and which entry is wrong
  when the input is not what you expect.
- **Test with synthetic input** that the test itself generates, and load it with
  `gg.ingest(adapter, gg.MemoryStore(ontology))`, which rejects any record that does not
  fit the ontology.
