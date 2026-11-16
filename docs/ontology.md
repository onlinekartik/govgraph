# Ontology reference

The ontology says which node and edge types exist. It is the single source of truth:
PostgreSQL tables, the PuppyGraph schema, record validation and the agent tools are all
derived from it, so there is nowhere else to keep in step.

The built-in ontology is [`core-1.0.yaml`](../src/govgraph/spec/ontologies/core-1.0.yaml).
Its types are listed [below](#node-types).

## How nodes and edges are identified

Every node type names the properties that form its natural key. A node's ID is its type
name followed by a hash of the type and the key, for example
`DataAsset:3244387c6f96ca69e089da8246e525e2`. The same key gives the same ID on any
machine, which is what makes loading idempotent and lets two sources describe one node.
An edge's ID is a hash of its type and the IDs of its two ends, so there is at most one edge
of a type between two nodes.

```python
import govgraph as gg

gg.node_id("DataAsset", {"qualified_name": "shop.orders.total"})
```

Key values are matched exactly, including case. Normalise names in your adapter if your
sources disagree about them.

## Extending the ontology

Write a YAML file in the same shape as the core file and pass it when loading:

```yaml
ontology:
  name: pipelines
  version: "0.1.0"

node_types:
  Pipeline:
    description: A scheduled job that produces data.
    key: [name]
    properties:
      name: { type: string, required: true }
      schedule: { type: string }
  DataAsset:                       # add a property to an existing type
    properties:
      row_count: { type: integer }

edge_types:
  PRODUCED_BY:
    description: An asset is written by a pipeline.
    phrase: is produced by
    propagates: target_to_source   # a broken pipeline affects the asset
    endpoints:
      - { source: DataAsset, target: Pipeline }
  OWNED_BY:                        # allow an existing edge type between more types
    endpoints:
      - { source: Pipeline, target: Owner }
```

```python
ontology = gg.load_ontology(["pipelines.yaml"])
```

```console
govgraph ontology validate --extension pipelines.yaml
govgraph init --extension pipelines.yaml      # adds the new tables and columns
```

An extension may add node types, edge types, properties and endpoints. It may not change
what an existing type already declares: its key, its display property, the definition of
an existing property, or an edge's phrase and impact direction.

Loading fails, listing every problem, if a document does not match the
[JSON schema](../src/govgraph/spec/ontology.schema.json), or if:

- an edge type has no endpoints, or names a node type that is not defined (a dangling
  edge type);
- a key field is not a declared, required property of type string or integer;
- a property uses a reserved name (`id`, `source_id`, `source_type`, `target_id`,
  `target_type`);
- two types would get the same table name, or a table name longer than 50 characters.

### Fields

| Field | On | Meaning |
| --- | --- | --- |
| `key` | node type | Properties that identify a node. Required for a new type. |
| `display` | node type | Property shown to people. Defaults to `name` if there is one, otherwise the key. |
| `properties` | both | Name to `{type, required, enum, description}`. Types: `string`, `integer`, `number`, `boolean`, `date`, `datetime`. `enum` is for strings only. |
| `endpoints` | edge type | Allowed `{source, target}` pairs of node types. |
| `phrase` | edge type | Verb used to state the edge: "source *phrase* target". Defaults to the type name in lower case. |
| `propagates` | edge type | Which way a problem travels: `source_to_target`, `target_to_source` or `none` (the default). `impact_of` follows it. |

Names are checked: node types are `PascalCase`, edge types are `UPPER_SNAKE_CASE` and
properties are `snake_case`. They become table and column names, which is why nothing else
is accepted.

### What the tools do with extensions

`get_asset_context` reports every edge type that can touch a `DataAsset`, and `impact_of`
follows every edge type that declares a `propagates` direction, so both pick up your
additions without code. `explain_failure`, `upstream_causes` and `find_by_term` are written
against the core types.

## Two modelling notes

**Lineage.** `DERIVES_FROM` points from the derived asset to the asset it is built from,
so depth counts hops. The `LineageEdge` node type is the record of one transformation; a
`DERIVES_FROM` edge names it in `lineage_id`, and the tools cite it.

**Tables and columns.** Both are `DataAsset` nodes. A column names its table in
`parent_qualified_name` rather than through an edge. The tools use that to fall back to
the table's owner when a column has none, and to start a lineage search from the table as
well as the column.

## Core types

The rest of this page is generated from the ontology by `govgraph ontology docs`. A test
fails if it goes stale.

<!-- BEGIN GENERATED -->
Ontology `core` 1.0.0.

## Node types

### DataAsset

A physical data object such as a table, a view or a column.

Stored in table `node_data_asset`. Natural key: `qualified_name`.

| Property | Type | Required | Allowed values | Description |
| --- | --- | --- | --- | --- |
| `qualified_name` (key) | string | yes |  | Unique dotted name, for example warehouse.orders.total. |
| `name` | string |  |  | Short name. |
| `kind` | string |  | `table`, `view`, `column`, `file`, `other` | What sort of object this is. |
| `parent_qualified_name` | string |  |  | Qualified name of the containing asset, for example the table of a column. |
| `data_type` | string |  |  | Declared type of a column. |
| `description` | string |  |  |  |

### BusinessTerm

A glossary term that gives data a business meaning.

Stored in table `node_business_term`. Natural key: `name`.

| Property | Type | Required | Allowed values | Description |
| --- | --- | --- | --- | --- |
| `name` (key) | string | yes |  |  |
| `definition` | string |  |  |  |
| `domain` | string |  |  | Subject area the term belongs to. |
| `status` | string |  | `draft`, `approved`, `deprecated` |  |

### CriticalDataElement

A data element that the organisation has decided needs extra control.

Stored in table `node_critical_data_element`. Natural key: `name`.

| Property | Type | Required | Allowed values | Description |
| --- | --- | --- | --- | --- |
| `name` (key) | string | yes |  |  |
| `description` | string |  |  |  |
| `criticality` | string |  | `low`, `medium`, `high` |  |

### Regulation

An external rule or internal policy that data must comply with.

Stored in table `node_regulation`. Natural key: `code`.

| Property | Type | Required | Allowed values | Description |
| --- | --- | --- | --- | --- |
| `code` (key) | string | yes |  | Short unique identifier. |
| `name` | string |  |  |  |
| `description` | string |  |  |  |

### DataProduct

A curated set of data assets delivered to consumers as one unit.

Stored in table `node_data_product`. Natural key: `name`.

| Property | Type | Required | Allowed values | Description |
| --- | --- | --- | --- | --- |
| `name` (key) | string | yes |  |  |
| `description` | string |  |  |  |
| `domain` | string |  |  |  |
| `status` | string |  | `draft`, `active`, `retired` |  |

### Owner

A person, team or role that is accountable for something.

Stored in table `node_owner`. Natural key: `identifier`.

| Property | Type | Required | Allowed values | Description |
| --- | --- | --- | --- | --- |
| `identifier` (key) | string | yes |  | Stable unique handle. |
| `name` | string |  |  |  |
| `kind` | string |  | `person`, `team`, `role` |  |

### DQRule

A data-quality check that can be evaluated against data.

Stored in table `node_dq_rule`. Natural key: `rule_id`.

| Property | Type | Required | Allowed values | Description |
| --- | --- | --- | --- | --- |
| `rule_id` (key) | string | yes |  |  |
| `name` | string |  |  |  |
| `description` | string |  |  |  |
| `dimension` | string |  | `completeness`, `validity`, `uniqueness`, `timeliness`, `consistency`, `accuracy` |  |
| `severity` | string |  | `low`, `medium`, `high`, `critical` |  |
| `expression` | string |  |  | The check, in whatever language the rule engine uses. |

### DQResult

The outcome of evaluating one rule in one run.

Stored in table `node_dq_result`. Natural key: `rule_id`, `run_id`.

| Property | Type | Required | Allowed values | Description |
| --- | --- | --- | --- | --- |
| `rule_id` (key) | string | yes |  |  |
| `run_id` (key) | string | yes |  |  |
| `status` | string |  | `pass`, `warn`, `fail`, `error` |  |
| `executed_at` | datetime |  |  |  |
| `rows_checked` | integer |  |  |  |
| `rows_failed` | integer |  |  |  |

### DQFailure

A data-quality incident raised because a rule did not pass.

Stored in table `node_dq_failure`. Natural key: `failure_id`.

| Property | Type | Required | Allowed values | Description |
| --- | --- | --- | --- | --- |
| `failure_id` (key) | string | yes |  |  |
| `summary` | string |  |  |  |
| `severity` | string |  | `low`, `medium`, `high`, `critical` |  |
| `status` | string |  | `open`, `acknowledged`, `resolved` |  |
| `detected_at` | datetime |  |  |  |
| `failed_count` | integer |  |  |  |

### LineageEdge

The record of one transformation that moves data from one asset to another. A DERIVES_FROM edge points at it so that tools can cite where a lineage claim came from.

Stored in table `node_lineage_edge`. Natural key: `source_qualified_name`, `target_qualified_name`, `process`.

| Property | Type | Required | Allowed values | Description |
| --- | --- | --- | --- | --- |
| `source_qualified_name` (key) | string | yes |  | The upstream asset. |
| `target_qualified_name` (key) | string | yes |  | The downstream asset. |
| `process` (key) | string | yes |  | Job, model or query that does the work. |
| `transformation` | string |  |  | What the process does to the data. |

## Edge types

### DEFINES

A business term defines the meaning of an asset or a critical data element.

Stored in table `edge_defines`. Read as "source defines target". Impact: a problem at the source affects the target.

Allowed endpoints:

- `BusinessTerm` → `DataAsset`
- `BusinessTerm` → `CriticalDataElement`

### CLASSIFIED_AS

An asset holds a critical data element.

Stored in table `edge_classified_as`. Read as "source is classified as target". Impact: a problem at the source affects the target.

Allowed endpoints:

- `DataAsset` → `CriticalDataElement`

### GOVERNED_BY

Something is subject to a regulation.

Stored in table `edge_governed_by`. Read as "source is governed by target". Impact: a problem at the source affects the target.

Allowed endpoints:

- `CriticalDataElement` → `Regulation`
- `DataAsset` → `Regulation`
- `DataProduct` → `Regulation`

### OWNED_BY

Something is the responsibility of an owner.

Stored in table `edge_owned_by`. Read as "source is owned by target". Impact: a problem at the source affects the target.

Allowed endpoints:

- `DataAsset` → `Owner`
- `DataProduct` → `Owner`
- `BusinessTerm` → `Owner`
- `CriticalDataElement` → `Owner`
- `DQRule` → `Owner`

### VALIDATES

A rule checks an asset.

Stored in table `edge_validates`. Read as "source validates target". Impact: a problem at the source affects the target.

Allowed endpoints:

- `DQRule` → `DataAsset`

### PROTECTS

A rule exists to safeguard a critical data element.

Stored in table `edge_protects`. Read as "source protects target". Impact: a problem at the source affects the target.

Allowed endpoints:

- `DQRule` → `CriticalDataElement`

### DERIVES_FROM

The source is built from the target, so the target is upstream of the source. A problem at the target therefore flows to the source.

Stored in table `edge_derives_from`. Read as "source derives from target". Impact: a problem at the target affects the source.

Allowed endpoints:

- `DataAsset` → `DataAsset`
- `DataProduct` → `DataAsset`

| Property | Type | Required | Allowed values | Description |
| --- | --- | --- | --- | --- |
| `lineage_id` | string |  |  | Node ID of the LineageEdge record that documents this derivation. |

### FAILED_ON

A failure was observed on an asset.

Stored in table `edge_failed_on`. Read as "source failed on target". Impact: a problem at the source affects the target.

Allowed endpoints:

- `DQFailure` → `DataAsset`

### IMPACTS

A failure is known to affect a data product or a critical data element.

Stored in table `edge_impacts`. Read as "source impacts target". Impact: a problem at the source affects the target.

Allowed endpoints:

- `DQFailure` → `DataProduct`
- `DQFailure` → `CriticalDataElement`

### RESULT_OF

A result was produced by evaluating a rule.

Stored in table `edge_result_of`. Read as "source is a result of target". Impact: problems do not pass along this edge.

Allowed endpoints:

- `DQResult` → `DQRule`

### RAISED_FROM

A failure was raised because of a result.

Stored in table `edge_raised_from`. Read as "source was raised from target". Impact: problems do not pass along this edge.

Allowed endpoints:

- `DQFailure` → `DQResult`
<!-- END GENERATED -->
